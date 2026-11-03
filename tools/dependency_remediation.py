from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import zipfile
import zlib
from collections.abc import Iterator

from tools import ci_security as security


SECURITY_WORKFLOW = ".github/workflows/security.yml"
INSTALLER_WORKFLOW = ".github/workflows/windows-installer.yml"
WORKFLOW_NAMES = {SECURITY_WORKFLOW: "Security", INSTALLER_WORKFLOW: "Windows installer"}
EXPECTED_PYTHONS = ("3.11", "3.12", "3.13", "3.14")
ARTIFACT_PREFIX = "dependency-findings-"
REPORT_NAME = "findings.json"
REPORT_KEYS = frozenset(("schema", "python", "revision", "status", "findings"))
FINDING_KEYS = frozenset(("package", "version", "advisory", "fix_versions", "aliases"))
LABEL = "dependency-remediation"
ISSUE_AUTHOR = "github-actions[bot]"
COPILOT_ASSIGNEE = "copilot-swe-agent[bot]"
COPILOT_AUTHOR = "Copilot"
COPILOT_LOGINS = ("Copilot", "copilot-swe-agent[bot]", "copilot-swe-agent")
DEPENDABOT = "dependabot[bot]"
AUDIT_EVENTS = ("push", "schedule", "workflow_dispatch")
AUDIT_CONCLUSIONS = ("success", "failure")
PASSED = ("success", "neutral", "skipped")
FAILED = ("failure", "timed_out", "startup_failure")
JOB_FAILED = ("failure", "timed_out")
OPT_OUT = ("not_planned", "duplicate")
TOKEN_VARIABLES = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "COPILOT_REMEDIATION_TOKEN",
)
MAX_ATTEMPTS = 3
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_PAGES = 20
MAX_ROWS = 50
MAX_ENTRIES = 10
MAX_REFERENCES = 5
MAX_FAILURES = 20
SERVER = "https://github.com"
ISSUE_MARKER = "<!-- app-activity:dependency-remediation fingerprint={} -->"
ISSUE_PATTERN = re.compile(r"<!-- app-activity:dependency-remediation fingerprint=([0-9a-f]{16}) -->")
CI_MARKER = "<!-- app-activity:dependency-remediation-ci sha={} -->"
CI_PATTERN = re.compile(r"<!-- app-activity:dependency-remediation-ci sha=([0-9a-f]{40}) -->")
LIMIT_MARKER = "<!-- app-activity:dependency-remediation-ci limit -->"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,63}")
ADVISORY = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Za-z0-9]+)+")
SHA = re.compile(r"[0-9a-f]{40}")
CLOSING = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+#(\d{1,10})\b")
HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\)")
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()+-]")


class RemediationError(security.SecurityError):
    pass


class GitHubError(RemediationError):
    def __init__(self, message: str, status: int | None) -> None:
        super().__init__(message)
        self.status = status


def matches(pattern: re.Pattern[str], value: object, limit: int = 200) -> bool:
    return isinstance(value, str) and len(value) <= limit and pattern.fullmatch(value) is not None


def valid_repository(value: object) -> bool:
    return matches(REPOSITORY, value) and all(part not in (".", "..") for part in value.split("/"))


def valid_branch(value: object) -> bool:
    return (
        matches(BRANCH, value) and ".." not in value and "//" not in value
        and not value.endswith(("/", ".", ".lock"))
    )


def one_of(value: object, options: tuple[str, ...]) -> bool:
    # Tuples avoid TypeError when GitHub returns an unhashable value where a string is expected.
    return isinstance(value, str) and value in options


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def order(value: str) -> tuple:
    parts = tuple(
        (0, int(part), "") if "0" <= part[0] <= "9" else (1, 0, part)
        for part in re.findall(r"[0-9]+|[^0-9]+", value)
    )
    return parts, value


def field(document: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def clean(name: object) -> str:
    text = UNSAFE_NAME.sub("", name)[:100].strip() if isinstance(name, str) else ""
    return text or "unnamed"


def entries(values: list, pattern: re.Pattern[str], exclude: str = "") -> list[str]:
    unique = {value for value in values if matches(pattern, value, 64) and value != exclude}
    return sorted(unique, key=order)[:MAX_ENTRIES]


def short(sha: str) -> str:
    return sha[:12]


def run_url(repo: str, run_id: int) -> str:
    return f"{SERVER}/{repo}/actions/runs/{run_id}"


class GitHub:
    def __init__(self, token: str, agent_token: str | None = None) -> None:
        self.token = token
        self.agent_token = agent_token

    def request(self, method: str, resource: str, data: object = None, *, agent: bool = False) -> bytes:
        token = self.agent_token if agent else self.token
        path = resource.split("?", 1)[0]
        if not token:
            raise RemediationError(f"No token is available for {method} {path}.")
        command = ["gh", "api", resource, "--method", method]
        if data is not None:
            command += ["--input", "-"]
        environment = {key: value for key, value in os.environ.items() if key.upper() not in TOKEN_VARIABLES}
        environment.update(GH_TOKEN=token, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
        try:
            result = subprocess.run(
                command, cwd=security.ROOT, env=environment, capture_output=True, timeout=120,
                input=None if data is None else json.dumps(data).encode("utf-8"),
            )
        except FileNotFoundError as error:
            raise RemediationError("GitHub CLI (gh) is missing on this runner.") from error
        except subprocess.TimeoutExpired as error:
            raise RemediationError(f"GitHub request timed out: {method} {path}.") from error
        if result.returncode:
            # gh's stderr can echo response details, so only the HTTP status is surfaced.
            found = HTTP_STATUS.search(result.stderr.decode("utf-8", "replace"))
            status = int(found.group(1)) if found else None
            detail = f"HTTP {status}" if status else "no HTTP status"
            raise GitHubError(f"GitHub request failed: {method} {path} ({detail}).", status)
        return result.stdout

    def api(self, method: str, resource: str, data: object = None, *, agent: bool = False) -> dict | list:
        body = self.request(method, resource, data, agent=agent)
        path = resource.split("?", 1)[0]
        if not body.strip():
            return {}
        try:
            document = json.loads(body)
        except (ValueError, RecursionError) as error:
            raise RemediationError(f"GitHub returned invalid JSON for {method} {path}.") from error
        if not isinstance(document, (dict, list)):
            raise RemediationError(f"GitHub returned an unexpected response for {method} {path}.")
        return document

    def get(self, resource: str, *, agent: bool = False) -> dict:
        document = self.api("GET", resource, agent=agent)
        if not isinstance(document, dict):
            path = resource.split("?", 1)[0]
            raise RemediationError(f"GitHub returned an unexpected response for GET {path}.")
        return document

    def pages(self, resource: str, key: str | None = None, *, agent: bool = False) -> Iterator[dict]:
        separator = "&" if "?" in resource else "?"
        path = resource.split("?", 1)[0]
        for page in range(1, MAX_PAGES + 1):
            document = self.api("GET", f"{resource}{separator}per_page=100&page={page}", agent=agent)
            items = document if key is None else field(document, key)
            if not isinstance(items, list):
                raise RemediationError(f"GitHub returned an unexpected page for {path}.")
            yield from (item for item in items if isinstance(item, dict))
            if len(items) < 100:
                return
        raise RemediationError(f"GitHub returned more than {MAX_PAGES} pages for {path}.")


def note(message: str, warning: bool = False) -> None:
    if warning:
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning::{escaped}")
    else:
        print(message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"- {message}\n")
        except OSError:
            pass


def context(github: GitHub, events: tuple[str, ...]) -> tuple[str, str]:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RemediationError("This helper runs in GitHub Actions; no local setup is required.")
    if not one_of(os.environ.get("GITHUB_EVENT_NAME"), events):
        raise RemediationError("This command does not support the triggering event.")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not valid_repository(repo):
        raise RemediationError("GITHUB_REPOSITORY is missing or invalid.")
    branch = github.get(f"repos/{repo}").get("default_branch")
    if not valid_branch(branch):
        raise RemediationError("The repository's default branch name is not supported.")
    if os.environ.get("GITHUB_REF") != f"refs/heads/{branch}":
        raise RemediationError("Run this workflow from the default branch.")
    return repo, branch


def branch_head(github: GitHub, repo: str, branch: str) -> str:
    sha = field(github.get(f"repos/{repo}/git/ref/heads/{branch}"), "object", "sha")
    if not matches(SHA, sha):
        raise RemediationError("Could not read the default branch head commit.")
    return sha


def triggering_run() -> int:
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            event = json.load(handle)
    except (KeyError, OSError, ValueError, RecursionError) as error:
        raise RemediationError("Could not read the workflow_run event payload.") from error
    run_id = field(event, "workflow_run", "id")
    if type(run_id) is not int or run_id <= 0:
        raise RemediationError("The workflow_run event payload has no valid run id.")
    return run_id


def remediate(github: GitHub) -> int:
    repo, branch = context(github, ("workflow_run", "workflow_dispatch"))
    head = branch_head(github, repo, branch)
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_run":
        run_id = triggering_run()
        run = github.get(f"repos/{repo}/actions/runs/{run_id}")
    else:
        run = latest_audit(github, repo, branch, head)
        if run is None:
            note(
                f"No completed Security audit was found for `{short(head)}` on `{branch}`; "
                "run this workflow again after it finishes."
            )
            return 0
        run_id = run["id"]
    if not verify_run(run, repo, branch, head, run_id):
        return 0
    findings, missing = aggregate(load_reports(github, repo, run_id, head))
    issues = remediation_issues(github, repo)
    if findings:
        track(github, repo, branch, run_id, head, findings, missing, issues)
    elif not missing:
        close_resolved(github, repo, run_id, head, issues)
    else:
        versions = ", ".join(missing)
        note(
            f"The dependency audit is incomplete for Python {versions}; remediation issues were left unchanged.",
            warning=True,
        )
    return 0


def latest_audit(github: GitHub, repo: str, branch: str, head: str) -> dict | None:
    query = urllib.parse.urlencode({"branch": branch, "head_sha": head, "status": "completed"})
    runs = [
        run for run in github.pages(f"repos/{repo}/actions/workflows/security.yml/runs?{query}", "workflow_runs")
        if one_of(run.get("event"), AUDIT_EVENTS) and one_of(run.get("conclusion"), AUDIT_CONCLUSIONS)
        and type(run.get("id")) is int
    ]
    return max(runs, key=lambda run: run["id"], default=None)


def verify_run(run: dict, repo: str, branch: str, head: str, run_id: int) -> bool:
    if type(run.get("id")) is not int or run["id"] != run_id:
        raise RemediationError("GitHub returned a different workflow run than requested.")
    if run.get("path") != SECURITY_WORKFLOW or field(run, "head_repository", "full_name") != repo:
        raise RemediationError("The audited run is not this repository's Security workflow.")
    if not one_of(run.get("event"), AUDIT_EVENTS) or run.get("head_branch") != branch:
        note(f"Security run {run_id} did not audit `{branch}`; nothing to do.")
        return False
    if run.get("status") != "completed" or not one_of(run.get("conclusion"), AUDIT_CONCLUSIONS):
        note(f"Security run {run_id} did not finish its audit; remediation issues were left unchanged.")
        return False
    sha = run.get("head_sha")
    if sha != head:
        audited = f" `{short(sha)}`" if matches(SHA, sha) else ""
        note(
            f"Security run {run_id} audited an older commit{audited}; the audit of `{short(head)}` "
            "will update remediation."
        )
        return False
    return True


def load_reports(github: GitHub, repo: str, run_id: int, head: str) -> dict[str, dict | None]:
    latest: dict[str, dict] = {}
    for artifact in github.pages(f"repos/{repo}/actions/runs/{run_id}/artifacts", "artifacts"):
        name = artifact.get("name")
        identifier = artifact.get("id")
        if not isinstance(name, str) or not name.startswith(ARTIFACT_PREFIX) or type(identifier) is not int:
            continue
        python = name[len(ARTIFACT_PREFIX):]
        if python in EXPECTED_PYTHONS and (python not in latest or identifier > latest[python]["id"]):
            latest[python] = artifact
    reports: dict[str, dict | None] = {}
    for python in EXPECTED_PYTHONS:
        artifact = latest.get(python)
        if artifact is None or artifact.get("expired") is not False:
            note(f"The Python {python} dependency findings report is missing or expired.", warning=True)
            reports[python] = None
            continue
        size = artifact.get("size_in_bytes")
        if type(size) is not int or not 0 < size <= MAX_ARTIFACT_BYTES:
            raise RemediationError(f"The Python {python} findings artifact has an unexpected size.")
        data = github.request("GET", f"repos/{repo}/actions/artifacts/{artifact['id']}/zip")
        reports[python] = parse_report(read_artifact(data, python), python, head)
    return reports


def read_artifact(data: bytes, python: str) -> bytes:
    failure = f"The Python {python} findings artifact could not be read."
    if len(data) > MAX_ARTIFACT_BYTES:
        raise RemediationError(failure)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if [member.filename for member in members] != [REPORT_NAME] or members[0].file_size > MAX_REPORT_BYTES:
                raise RemediationError(failure)
            with archive.open(members[0]) as handle:
                content = handle.read(MAX_REPORT_BYTES + 1)
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, ValueError) as error:
        raise RemediationError(failure) from error
    if len(content) > MAX_REPORT_BYTES:
        raise RemediationError(failure)
    return content


def parse_report(content: bytes, python: str, head: str) -> dict:
    failure = f"The Python {python} findings report is invalid."
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise RemediationError(failure) from error
    if not isinstance(document, dict) or set(document) != REPORT_KEYS:
        raise RemediationError(failure)
    status = document["status"]
    findings = document["findings"]
    if (
        type(document["schema"]) is not int or document["schema"] != 1 or document["python"] != python
        or not one_of(status, ("clean", "vulnerable", "error")) or not isinstance(findings, list)
        or bool(findings) != (status == "vulnerable")
    ):
        raise RemediationError(failure)
    if document["revision"] != head and not (status == "error" and document["revision"] is None):
        raise RemediationError(f"The Python {python} findings report audited a different commit.")
    parsed = []
    for finding in findings:
        if (
            not isinstance(finding, dict) or set(finding) != FINDING_KEYS
            or not isinstance(finding["fix_versions"], list) or not isinstance(finding["aliases"], list)
        ):
            raise RemediationError(failure)
        package, version, advisory = finding["package"], finding["version"], finding["advisory"]
        if not (matches(PACKAGE, package, 100) and matches(VERSION, version, 64) and matches(ADVISORY, advisory, 64)):
            note(f"Ignored a malformed finding in the Python {python} report.", warning=True)
            continue
        parsed.append({
            "package": canonical(package),
            "version": version,
            "advisory": advisory,
            "fix_versions": entries(finding["fix_versions"], VERSION),
            "aliases": entries(finding["aliases"], ADVISORY, exclude=advisory),
        })
    if status == "vulnerable" and not parsed:
        raise RemediationError(failure)
    return {"status": status, "findings": parsed}


def aggregate(reports: dict[str, dict | None]) -> tuple[list[dict], list[str]]:
    missing = [python for python, report in reports.items() if report is None or report["status"] == "error"]
    combined: dict[tuple[str, str, str], dict] = {}
    for python, report in reports.items():
        for finding in report["findings"] if report is not None else []:
            key = (finding["package"], finding["version"], finding["advisory"])
            entry = combined.setdefault(key, {
                "package": key[0], "version": key[1], "advisory": key[2],
                "fix_versions": [], "aliases": [], "pythons": [],
            })
            for name in ("fix_versions", "aliases"):
                entry[name] = sorted(set(entry[name]) | set(finding[name]), key=order)[:MAX_ENTRIES]
            entry["pythons"].append(python)
    findings = sorted(
        combined.values(), key=lambda entry: (entry["package"], order(entry["version"]), entry["advisory"]),
    )
    return findings, missing


def fingerprint(findings: list[dict]) -> str:
    rows = sorted(
        [finding["package"], finding["version"], finding["advisory"], finding["fix_versions"]]
        for finding in findings
    )
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def tracked(issue: dict) -> bool:
    labels = issue.get("labels")
    body = issue.get("body")
    number = issue.get("number")
    return (
        "pull_request" not in issue and field(issue, "user", "login") == ISSUE_AUTHOR
        and type(number) is int and number > 0
        and isinstance(body, str) and ISSUE_PATTERN.search(body) is not None
        and isinstance(labels, list) and any(field(label, "name") == LABEL for label in labels)
        and one_of(issue.get("state"), ("open", "closed"))
    )


def marker(issue: dict) -> str:
    return ISSUE_PATTERN.search(issue["body"]).group(1)


def remediation_issues(github: GitHub, repo: str) -> list[dict]:
    query = urllib.parse.urlencode({"labels": LABEL, "state": "all"})
    issues = [issue for issue in github.pages(f"repos/{repo}/issues?{query}") if tracked(issue)]
    return sorted(issues, key=lambda issue: issue["number"])


def track(
    github: GitHub, repo: str, branch: str, run_id: int, head: str,
    findings: list[dict], missing: list[str], issues: list[dict],
) -> None:
    digest = fingerprint(findings)
    title = issue_title(findings)
    body = issue_body(digest, repo, branch, run_id, head, findings, missing)
    url = run_url(repo, run_id)
    current = [issue for issue in issues if issue["state"] == "open"]
    if current:
        issue = current[0]
        number = issue["number"]
        if marker(issue) != digest:
            github.api("PATCH", f"repos/{repo}/issues/{number}", {"title": title, "body": body})
            comment = (
                f"The dependency findings changed at `{short(head)}` ([Security run]({url})). "
                "The description lists the current findings."
            )
            github.api("POST", f"repos/{repo}/issues/{number}/comments", {"body": comment})
            note(f"Updated #{number} with the current dependency findings.")
            if github.agent_token:
                notify_pulls(github, repo, branch, number, head, url)
        else:
            note(f"#{number} already tracks the current dependency findings.")
        assign(github, repo, branch, issue, findings)
        return
    declined = [
        issue for issue in issues if marker(issue) == digest and one_of(issue.get("state_reason"), OPT_OUT)
    ]
    if declined:
        closed = declined[-1]
        reason = "a duplicate" if closed["state_reason"] == "duplicate" else "not planned"
        note(f"#{closed['number']} was closed as {reason} for these exact findings; no new issue was opened.",
             warning=True)
        return
    ensure_label(github, repo)
    created = github.api("POST", f"repos/{repo}/issues", {"title": title, "body": body, "labels": [LABEL]})
    number = field(created, "number")
    if type(number) is not int or number <= 0:
        raise RemediationError("GitHub did not return the new remediation issue number.")
    note(f"Opened #{number} for the vulnerable dependencies.")
    assign(github, repo, branch, created, findings)


def ensure_label(github: GitHub, repo: str) -> None:
    try:
        github.get(f"repos/{repo}/labels/{LABEL}")
        return
    except GitHubError as lookup:
        if lookup.status != 404:
            raise
    label = {"name": LABEL, "color": "d73a4a", "description": "Automatic dependency vulnerability remediation"}
    try:
        github.api("POST", f"repos/{repo}/labels", label)
    except GitHubError as creation:
        # 422 means another run created the label first.
        if creation.status != 422:
            raise


def issue_title(findings: list[dict]) -> str:
    packages = list(dict.fromkeys(finding["package"] for finding in findings))
    noun = "dependency" if len(packages) == 1 else "dependencies"
    listed = ", ".join(packages[:3])
    title = f"Fix vulnerable {noun}: {listed}"
    if len(packages) > 3:
        title += f" and {len(packages) - 3} more"
    if len(title) > 120:
        title = f"Fix {len(packages)} vulnerable {noun}"
    return title


def issue_body(
    digest: str, repo: str, branch: str, run_id: int, head: str, findings: list[dict], missing: list[str],
) -> str:
    url = run_url(repo, run_id)
    lines = [
        ISSUE_MARKER.format(digest),
        f"## Vulnerable dependencies on `{branch}`",
        "",
        f"The [Security workflow run]({url}) audited `{short(head)}` and found these known vulnerabilities:",
        "",
        "| Package | Installed | Advisory | Aliases | Fixed in | Python |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for finding in findings[:MAX_ROWS]:
        package, version, advisory = finding["package"], finding["version"], finding["advisory"]
        aliases = ", ".join(finding["aliases"]) or "none"
        fixes = ", ".join(f"`{fix}`" for fix in finding["fix_versions"]) or "No fixed release yet"
        pythons = ", ".join(finding["pythons"])
        lines.append(
            f"| `{package}` | `{version}` | [{advisory}](https://osv.dev/vulnerability/{advisory}) "
            f"| {aliases} | {fixes} | {pythons} |"
        )
    if len(findings) > MAX_ROWS:
        omitted = len(findings) - MAX_ROWS
        lines += ["", f"{omitted} more findings are omitted; see the run's `{ARTIFACT_PREFIX}*` artifacts."]
    if missing:
        versions = ", ".join(missing)
        lines += ["", "> [!NOTE]", f"> The audit was incomplete for Python {versions}, so this list may be partial."]
    lines += [
        "",
        "### Instructions for Copilot",
        "",
        "1. Upgrade each vulnerable package to the lowest fixed version that publishes Windows wheels for "
        "Python 3.11 through 3.14. Check each candidate with `pip download --only-binary=:all: --platform win_amd64 "
        "--python-version 3.11 --no-deps --dest <temporary-directory> <package>==<version>`, then repeat for "
        "3.12, 3.13, and 3.14.",
        "2. Keep exact `==` pins in the root `requirements-*.txt` files. When the vulnerable package is a transitive "
        "dependency, pin its fixed version explicitly in the requirements file that installs it.",
        "3. Fix any code, tests, packaging, or documentation that the upgrade breaks, and add regression tests for "
        "changed behavior.",
        "4. Never suppress or ignore advisories, and never skip, weaken, or delete tests or security gates.",
        "5. Leave findings without a fixed release unchanged, do not invent versions, and list those findings in the "
        "pull request description.",
        "6. The Windows CI checks are authoritative: `test`, `build`, and `security-dependencies (3.11)` through "
        "`security-dependencies (3.14)` must pass. A local pip-audit run on Linux is only a quick check.",
        "7. Write `Fixes #` followed by this issue's number in the pull request description so that merging closes "
        "this issue. A maintainer reviews and merges the pull request.",
        "",
        "---",
        "This issue is managed by the Dependency remediation workflow. To opt out of these exact findings, close it "
        "as not planned. To retry, unassign Copilot; the next audit or a manual run of the workflow assigns it "
        f"again. Keep the `{LABEL}` label and the hidden marker comment so the workflow can track this issue.",
    ]
    return "\n".join(lines) + "\n"


def close_resolved(github: GitHub, repo: str, run_id: int, head: str, issues: list[dict]) -> None:
    supported = f"{EXPECTED_PYTHONS[0]}-{EXPECTED_PYTHONS[-1]}"
    current = [issue for issue in issues if issue["state"] == "open"]
    if not current:
        note(f"The dependency audit of `{short(head)}` is clean on Python {supported}.")
        return
    url = run_url(repo, run_id)
    comment = (
        f"The [Security run]({url}) found no known vulnerabilities at `{short(head)}` on Python {supported}. "
        "Closing as resolved."
    )
    for issue in current:
        number = issue["number"]
        github.api("POST", f"repos/{repo}/issues/{number}/comments", {"body": comment})
        github.api("PATCH", f"repos/{repo}/issues/{number}", {"state": "closed", "state_reason": "completed"})
        note(f"Closed #{number}; the dependency audit is clean.")


def copilot_assigned(document: object) -> bool:
    assignees = field(document, "assignees")
    return isinstance(assignees, list) and any(
        one_of(field(assignee, "login"), COPILOT_LOGINS) for assignee in assignees
    )


def assign(github: GitHub, repo: str, branch: str, issue: dict, findings: list[dict]) -> None:
    number = issue["number"]
    if copilot_assigned(issue):
        note(f"Copilot is already assigned to #{number}.")
        return
    packages = sorted({finding["package"] for finding in findings})
    fixable = sorted({finding["package"] for finding in findings if finding["fix_versions"]})
    unfixable = [package for package in packages if package not in fixable]
    if not fixable:
        note(f"No fixed releases are available yet for the findings in #{number}; Copilot was not assigned.")
        return
    if not github.agent_token:
        note(
            f"Add the COPILOT_REMEDIATION_TOKEN secret, or assign Copilot to #{number} manually, "
            "to start automatic remediation.",
            warning=True,
        )
        return
    covered = dependabot_coverage(github, repo, branch, fixable)
    if len(covered) == len(fixable):
        updated = ", ".join(covered)
        note(f"Open Dependabot pull requests already update {updated}; Copilot was not assigned to #{number}.")
        return
    instructions = [
        f"Resolve issue #{number} by following the instructions in its description.",
        "Keep exact version pins, and pin vulnerable transitive dependencies explicitly.",
        "Fix any code, tests, packaging, or documentation that the upgrade breaks, and add regression tests.",
        "Never suppress advisories, and never skip, weaken, or delete tests or security gates.",
        "The Windows CI checks are authoritative.",
        f"Include Fixes #{number} in the pull request description.",
    ]
    if covered:
        updated = ", ".join(covered)
        instructions.append(f"Open Dependabot pull requests already update {updated}; leave those packages unchanged.")
    if unfixable:
        pending = ", ".join(unfixable)
        instructions.append(
            f"No fixed release exists yet for {pending}; leave those packages unchanged and mention them in the "
            "pull request."
        )
    payload = {
        "assignees": [COPILOT_ASSIGNEE],
        "agent_assignment": {
            "target_repo": repo,
            "base_branch": branch,
            "custom_instructions": " ".join(instructions),
            "custom_agent": "",
            "model": "",
        },
    }
    response = github.api("POST", f"repos/{repo}/issues/{number}/assignees", payload, agent=True)
    # GitHub can silently drop an assignee it cannot assign, so confirm the assignment took effect.
    if not copilot_assigned(response) and not copilot_assigned(
        github.get(f"repos/{repo}/issues/{number}", agent=True)
    ):
        raise RemediationError(
            f"Copilot was not assigned to #{number}; check that Copilot cloud agent is enabled and that the "
            "token can assign it."
        )
    note(f"Assigned Copilot to #{number}.")


def dependabot_coverage(github: GitHub, repo: str, branch: str, packages: list[str]) -> list[str]:
    query = urllib.parse.urlencode({"state": "open", "base": branch})
    refs = []
    for pull in github.pages(f"repos/{repo}/pulls?{query}"):
        ref = field(pull, "head", "ref")
        if (
            field(pull, "user", "login") == DEPENDABOT and isinstance(ref, str) and ref.startswith("dependabot/")
            and field(pull, "head", "repo", "full_name") == repo
        ):
            refs.append(canonical(ref.rsplit("/", 1)[-1]))
    return [package for package in packages if any(re.match(re.escape(package) + "-[0-9]", ref) for ref in refs)]


def closing_references(pull: dict) -> list[int]:
    body = pull.get("body")
    if not isinstance(body, str):
        return []
    return list(dict.fromkeys(int(number) for number in CLOSING.findall(body)))[:MAX_REFERENCES]


def copilot_pull(pull: dict, repo: str, branch: str) -> bool:
    ref = field(pull, "head", "ref")
    return (
        field(pull, "user", "login") == COPILOT_AUTHOR and isinstance(ref, str) and ref.startswith("copilot/")
        and field(pull, "head", "repo", "full_name") == repo and field(pull, "base", "ref") == branch
        and type(pull.get("number")) is int
    )


def notify_pulls(github: GitHub, repo: str, branch: str, number: int, sha: str, url: str) -> None:
    query = urllib.parse.urlencode({"state": "open", "base": branch})
    comment = (
        f"@copilot The dependency findings in #{number} changed at `{short(sha)}` ([Security run]({url})). "
        "Re-read the issue and update this pull request to resolve the current findings, following its instructions."
    )
    for pull in github.pages(f"repos/{repo}/pulls?{query}"):
        if copilot_pull(pull, repo, branch) and number in closing_references(pull):
            github.api("POST", f"repos/{repo}/issues/{pull['number']}/comments", {"body": comment}, agent=True)
            note(f"Asked Copilot to update #{pull['number']} for the changed findings.")


def feedback(github: GitHub) -> int:
    repo, branch = context(github, ("workflow_run",))
    run_id = triggering_run()
    run = github.get(f"repos/{repo}/actions/runs/{run_id}")
    sha = run.get("head_sha")
    head_branch = run.get("head_branch")
    if not (
        type(run.get("id")) is int and run["id"] == run_id and run.get("event") == "pull_request"
        and one_of(run.get("path"), tuple(WORKFLOW_NAMES)) and field(run, "head_repository", "full_name") == repo
        and matches(SHA, sha) and valid_branch(head_branch) and run.get("status") == "completed"
    ):
        note(f"Run {run_id} is not a completed pull request check from this repository; nothing to do.")
        return 0
    pull = find_pull(github, repo, branch, head_branch, sha)
    if pull is None:
        return 0
    kind = eligible(github, repo, branch, pull)
    if kind is None:
        return 0
    runs = latest_runs(github, repo, head_branch, sha)
    if runs is None:
        return 0
    failures = failed_jobs(github, repo, runs)
    number = pull["number"]
    if not failures:
        note(f"All checks pass for #{number} at `{short(sha)}`.")
        return 0
    if not github.agent_token:
        note(
            f"Checks failed on #{number}; add the COPILOT_REMEDIATION_TOKEN secret to ask Copilot to fix them "
            "automatically.",
            warning=True,
        )
        return 0
    request_fix(github, repo, pull, sha, kind, failures)
    return 0


def find_pull(github: GitHub, repo: str, branch: str, head_branch: str, sha: str) -> dict | None:
    owner = repo.split("/", 1)[0]
    query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{head_branch}", "base": branch})
    for pull in github.pages(f"repos/{repo}/pulls?{query}"):
        if (
            field(pull, "head", "ref") == head_branch and field(pull, "head", "repo", "full_name") == repo
            and field(pull, "base", "ref") == branch and type(pull.get("number")) is int
        ):
            if field(pull, "head", "sha") != sha:
                note(f"#{pull['number']} has commits newer than `{short(sha)}`; their checks decide the next step.")
                return None
            return pull
    note(f"No open pull request into `{branch}` uses `{head_branch}`; nothing to do.")
    return None


def eligible(github: GitHub, repo: str, branch: str, pull: dict) -> str | None:
    ref = field(pull, "head", "ref")
    if field(pull, "user", "login") == DEPENDABOT and isinstance(ref, str) and ref.startswith("dependabot/"):
        return "dependabot"
    if copilot_pull(pull, repo, branch):
        for reference in closing_references(pull):
            try:
                issue = github.get(f"repos/{repo}/issues/{reference}")
            except GitHubError as error:
                if error.status in (404, 410):
                    continue
                raise
            if tracked(issue) and issue["state"] == "open":
                return "copilot"
    note(f"#{pull['number']} is not a Dependabot or dependency remediation pull request; nothing to do.")
    return None


def latest_runs(github: GitHub, repo: str, head_branch: str, sha: str) -> dict[str, dict] | None:
    query = urllib.parse.urlencode({"head_sha": sha, "event": "pull_request"})
    latest: dict[str, dict] = {}
    for run in github.pages(f"repos/{repo}/actions/runs?{query}", "workflow_runs"):
        path = run.get("path")
        identifier = run.get("id")
        if (
            one_of(path, tuple(WORKFLOW_NAMES)) and type(identifier) is int and run.get("head_branch") == head_branch
            and run.get("head_sha") == sha and field(run, "head_repository", "full_name") == repo
            and (path not in latest or identifier > latest[path]["id"])
        ):
            latest[path] = run
    for path, name in WORKFLOW_NAMES.items():
        run = latest.get(path)
        if run is None or run.get("status") != "completed" or run.get("conclusion") == "action_required":
            note(f"Waiting for the {name} workflow to finish at `{short(sha)}`.")
            return None
        if not one_of(run.get("conclusion"), PASSED + FAILED):
            note(f"The {name} workflow did not finish at `{short(sha)}`; nothing to do.")
            return None
    return latest


def failed_jobs(github: GitHub, repo: str, runs: dict[str, dict]) -> list[dict]:
    failures = []
    for path, run in runs.items():
        if not one_of(run.get("conclusion"), FAILED):
            continue
        workflow = WORKFLOW_NAMES[path]
        run_id = run["id"]
        found = []
        for job in github.pages(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest", "jobs"):
            job_id = job.get("id")
            if not one_of(job.get("conclusion"), JOB_FAILED) or type(job_id) is not int:
                continue
            steps = job.get("steps") if isinstance(job.get("steps"), list) else []
            failed_steps = [
                clean(step.get("name")) for step in steps
                if isinstance(step, dict) and one_of(step.get("conclusion"), JOB_FAILED)
            ]
            found.append({
                "workflow": workflow,
                "job": clean(job.get("name")),
                "steps": failed_steps[:MAX_ENTRIES],
                "url": f"{SERVER}/{repo}/actions/runs/{run_id}/job/{job_id}",
            })
        failures += found or [{"workflow": workflow, "job": "", "steps": [], "url": run_url(repo, run_id)}]
    return failures[:MAX_FAILURES]


def failure_list(failures: list[dict]) -> list[str]:
    lines = []
    for failure in failures:
        workflow, url = failure["workflow"], failure["url"]
        if failure["job"]:
            steps = ", ".join(failure["steps"]) or "no failed step reported"
            lines.append(f"- {workflow} / {failure['job']} (failed steps: {steps}): [logs]({url})")
        else:
            lines.append(f"- {workflow}: the workflow failed without a failed job: [run]({url})")
    return lines


def request_fix(github: GitHub, repo: str, pull: dict, sha: str, kind: str, failures: list[dict]) -> None:
    number = pull["number"]
    login = github.get("user", agent=True).get("login")
    if not isinstance(login, str) or not login:
        raise RemediationError("Could not identify the user behind COPILOT_REMEDIATION_TOKEN.")
    bodies = [
        comment["body"] for comment in github.pages(f"repos/{repo}/issues/{number}/comments")
        if field(comment, "user", "login") == login and isinstance(comment.get("body"), str)
    ]
    if any(LIMIT_MARKER in body for body in bodies):
        note(f"#{number} already reached the limit of automatic fix requests; a maintainer needs to take over.",
             warning=True)
        return
    attempted = list(dict.fromkeys(found for body in bodies for found in CI_PATTERN.findall(body)))
    if sha in attempted:
        note(f"Copilot was already asked to fix the checks on #{number} at `{short(sha)}`.")
        return
    listing = failure_list(failures)
    if len(attempted) >= MAX_ATTEMPTS:
        body = "\n".join([
            f"Automatic fix requests reached the limit of {MAX_ATTEMPTS} attempts, and checks still fail at "
            f"`{short(sha)}`:",
            "",
            *listing,
            "",
            "A maintainer needs to take over: fix the failures or close this pull request.",
            LIMIT_MARKER,
        ])
        github.api("POST", f"repos/{repo}/issues/{number}/comments", {"body": body}, agent=True)
        note(f"#{number} reached the limit of {MAX_ATTEMPTS} automatic fix requests; a maintainer needs to take over.",
             warning=True)
        return
    if kind == "dependabot":
        guidance = (
            "Keep the updated dependency version, and adapt the code, tests, packaging, or documentation to it "
            "instead of reverting the update."
        )
    else:
        guidance = (
            "Keep resolving the linked dependency remediation issue, following the instructions in its description."
        )
    body = "\n".join([
        f"@copilot Checks failed at `{short(sha)}` on this pull request:",
        "",
        *listing,
        "",
        "Read the failed job logs with your GitHub tools, then fix the root cause. Never weaken, skip, or delete "
        "tests or security gates, and never suppress advisories. The Windows CI checks are authoritative.",
        guidance,
        CI_MARKER.format(sha),
    ])
    github.api("POST", f"repos/{repo}/issues/{number}/comments", {"body": body}, agent=True)
    attempt = len(attempted) + 1
    note(
        f"Asked Copilot to fix the failing checks on #{number} at `{short(sha)}` "
        f"(attempt {attempt} of {MAX_ATTEMPTS})."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Automate dependency vulnerability remediation in GitHub Actions.")
    parser.add_argument("command", choices=("remediate", "feedback"))
    args = parser.parse_args(argv)
    github = GitHub(
        os.environ.get("GH_TOKEN", "").strip(), os.environ.get("COPILOT_REMEDIATION_TOKEN", "").strip() or None,
    )
    try:
        return remediate(github) if args.command == "remediate" else feedback(github)
    except (security.SecurityError, OSError) as error:
        print(f"DEPENDENCY REMEDIATION FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
