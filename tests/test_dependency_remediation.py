from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import dependency_remediation as remediation


REPO = "owner/app"
HEAD = "a" * 40
OLD = "b" * 40
PR_SHA = "c" * 40
RUN_ID = 101
ADVISORY = "GHSA-9wx4-h78v-vm56"
PYTHONS = remediation.EXPECTED_PYTHONS
ISSUES = f"repos/{REPO}/issues?" + urllib.parse.urlencode({"labels": remediation.LABEL, "state": "all"})
PULLS = f"repos/{REPO}/pulls?state=open&base=main"


def finding(package: str = "Requests", fix_versions: tuple[str, ...] = ("2.32.0",), **values: object) -> dict:
    document = {
        "package": package, "version": "2.31.0", "advisory": ADVISORY,
        "fix_versions": list(fix_versions), "aliases": ["CVE-2024-35195"],
    }
    document.update(values)
    return document


def report(python: str, status: str = "vulnerable", findings: list | None = None, revision: str | None = HEAD) -> dict:
    if findings is None:
        findings = [finding()] if status == "vulnerable" else []
    return {"schema": 1, "python": python, "revision": revision, "status": status, "findings": findings}


def archive(content: dict | bytes, *names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name in names or (remediation.REPORT_NAME,):
            package.writestr(name, content if isinstance(content, bytes) else json.dumps(content))
    return buffer.getvalue()


def security_run(**values: object) -> dict:
    run = {
        "id": RUN_ID, "path": remediation.SECURITY_WORKFLOW, "event": "push", "head_branch": "main",
        "head_sha": HEAD, "status": "completed", "conclusion": "failure", "head_repository": {"full_name": REPO},
    }
    run.update(values)
    return run


def tracked_issue(
    number: int = 5, digest: str = "0" * 16, state: str = "open", reason: str | None = None,
    assignees: tuple[str, ...] = ("Copilot",), packages: str = "requests",
) -> dict:
    markers = (remediation.ISSUE_MARKER.format(digest), remediation.PACKAGES_MARKER.format(packages))
    return {
        "number": number, "user": {"login": remediation.ISSUE_AUTHOR}, "state": state, "state_reason": reason,
        "labels": [{"name": remediation.LABEL}], "body": "\n".join((*markers, "Findings")),
        "assignees": [{"login": login} for login in assignees],
    }


def current_digest(fix_versions: tuple[str, ...] = ("2.32.0",)) -> str:
    return remediation.fingerprint(
        [{"package": "requests", "version": "2.31.0", "advisory": ADVISORY, "fix_versions": list(fix_versions)}]
    )


class FakeGitHub(remediation.GitHub):
    def __init__(self, routes: dict, agent_token: str | None = "agent-token") -> None:
        super().__init__("token", agent_token)
        self.routes = routes
        self.calls: list[tuple[str, str, object, bool]] = []

    def request(self, method: str, resource: str, data: object = None, *, agent: bool = False) -> bytes:
        path, _, query = resource.partition("?")
        kept = [part for part in query.split("&") if part and not part.startswith(("per_page=", "page="))]
        resource = path + ("?" + "&".join(kept) if kept else "")
        self.calls.append((method, resource, data, agent))
        if (method, resource) not in self.routes:
            raise AssertionError(f"Unexpected request: {method} {resource}")
        response = self.routes[(method, resource)]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, bytes) else json.dumps(response).encode("utf-8")

    def sent(self, method: str) -> list[tuple[str, object, bool]]:
        return [(resource, data, agent) for verb, resource, data, agent in self.calls if verb == method]


class WorkflowFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.summary = self.directory / "summary.md"
        self.use_event("workflow_run")

    def use_event(self, event: str) -> None:
        payload = self.directory / "event.json"
        payload.write_text(json.dumps({"workflow_run": {"id": RUN_ID}}), encoding="utf-8")
        values = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": event, "GITHUB_REPOSITORY": REPO,
            "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_PATH": str(payload), "GITHUB_STEP_SUMMARY": str(self.summary),
        }
        patcher = patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_command(self, command, github: FakeGitHub) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(command(github), 0)
        return output.getvalue()


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_parse_report_normalizes_findings(self) -> None:
        releases = tuple(f"2.{minor}.0" for minor in range(12))
        document = report("3.12", findings=[
            finding("Requests_Toolbelt", ("10.0", "2.0.0", "2.0.0"), aliases=[ADVISORY, "CVE-1", "CVE-1"]),
            finding("requests", releases),
        ])
        parsed = remediation.parse_report(json.dumps(document).encode("utf-8"), "3.12", HEAD)
        self.assertEqual(parsed, {"status": "vulnerable", "findings": [{
            "package": "requests-toolbelt", "version": "2.31.0", "advisory": ADVISORY,
            "fix_versions": ["2.0.0", "10.0"], "aliases": ["CVE-1"],
        }, {
            "package": "requests", "version": "2.31.0", "advisory": ADVISORY,
            "fix_versions": list(releases[2:]), "aliases": ["CVE-2024-35195"],
        }]})

    def test_parse_report_rejects_inconsistent_documents(self) -> None:
        cases = {
            "different commit": report("3.11", revision=OLD),
            "vulnerable without findings": report("3.11", findings=[]),
            "clean with findings": report("3.11", "clean", [finding()]),
            "wrong Python": report("3.12"),
            "boolean schema": {**report("3.11"), "schema": True},
            "extra key": {**report("3.11"), "extra": 1},
            "malformed package": report("3.11", findings=[finding(), finding("bad package!")]),
            "malformed version": report("3.11", findings=[finding(version="bad version")]),
            "malformed advisory": report("3.11", findings=[finding(advisory="not-an-advisory")]),
            "malformed fix version": report("3.11", findings=[finding(fix_versions=("2.32.0", "bad version"))]),
            "non-string alias": report("3.11", findings=[finding(aliases=["CVE-1", 7])]),
            "malformed alias": report("3.11", findings=[finding(aliases=["cve 1"])]),
            "finding without aliases list": report("3.11", findings=[finding(aliases="CVE-1")]),
        }
        for name, document in cases.items():
            with self.subTest(name), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(remediation.RemediationError):
                    remediation.parse_report(json.dumps(document).encode("utf-8"), "3.11", HEAD)
        parsed = remediation.parse_report(json.dumps(report("3.11", "error", revision=None)).encode(), "3.11", HEAD)
        self.assertEqual(parsed, {"status": "error", "findings": []})

    def test_read_artifact_requires_one_bounded_report(self) -> None:
        self.assertEqual(remediation.read_artifact(archive(b"{}"), "3.11"), b"{}")
        invalid = {
            "extra member": archive(b"{}", remediation.REPORT_NAME, "other.txt"),
            "wrong name": archive(b"{}", "nested/findings.json"),
            "not a zip": b"not a zip",
        }
        for name, data in invalid.items():
            with self.subTest(name), self.assertRaises(remediation.RemediationError):
                remediation.read_artifact(data, "3.11")
        with patch.object(remediation, "MAX_REPORT_BYTES", 4), self.assertRaises(remediation.RemediationError):
            remediation.read_artifact(archive(b"12345"), "3.11")

    def test_aggregate_merges_pythons_and_reports_incomplete_audits(self) -> None:
        reports = {
            "3.11": {"status": "vulnerable", "findings": [{**finding("requests"), "aliases": ["CVE-2"]}]},
            "3.12": {"status": "vulnerable", "findings": [finding("requests", ("2.32.1",))]},
            "3.13": None,
            "3.14": {"status": "error", "findings": []},
        }
        findings, missing = remediation.aggregate(reports)
        self.assertEqual(missing, ["3.13", "3.14"])
        self.assertEqual(findings, [{
            "package": "requests", "version": "2.31.0", "advisory": ADVISORY, "fix_versions": ["2.32.0", "2.32.1"],
            "aliases": ["CVE-2", "CVE-2024-35195"], "pythons": ["3.11", "3.12"],
        }])


class ClientTests(unittest.TestCase):
    def test_request_uses_only_the_selected_token_and_withholds_error_details(self) -> None:
        failed = subprocess.CompletedProcess([], 1, b"", b"gh: Not Found private-detail (HTTP 404)")
        succeeded = subprocess.CompletedProcess([], 0, b'{"ok": true}', b"")
        ambient = {"GITHUB_TOKEN": "ambient", "COPILOT_REMEDIATION_TOKEN": "ambient-agent", "GH_TOKEN": "ambient-gh"}
        github = remediation.GitHub("primary", "agent")
        with patch.dict(os.environ, ambient), patch.object(subprocess, "run", side_effect=[failed, succeeded]) as run:
            with self.assertRaises(remediation.GitHubError) as raised:
                github.request("GET", "repos/owner/app/issues?state=all", agent=True)
            self.assertEqual(github.api("POST", "repos/owner/app/labels", {"name": "x"}), {"ok": True})
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(str(raised.exception), "GitHub request failed: GET repos/owner/app/issues (HTTP 404).")
        first, second = run.call_args_list
        self.assertEqual(first.args[0], ["gh", "api", "repos/owner/app/issues?state=all", "--method", "GET"])
        self.assertIsNone(first.kwargs["input"])
        environment = first.kwargs["env"]
        self.assertEqual(environment["GH_TOKEN"], "agent")
        self.assertEqual(environment["GH_PROMPT_DISABLED"], "1")
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("COPILOT_REMEDIATION_TOKEN", environment)
        self.assertEqual(second.args[0][-2:], ["--input", "-"])
        self.assertEqual(second.kwargs["env"]["GH_TOKEN"], "primary")
        self.assertEqual(json.loads(second.kwargs["input"]), {"name": "x"})

    def test_pages_stop_on_a_short_page_and_reject_runaway_pagination(self) -> None:
        github = remediation.GitHub("token")
        pages = [[{"id": index} for index in range(100)], [{"id": 100}]]
        with patch.object(github, "api", side_effect=pages) as api:
            self.assertEqual(len(list(github.pages("repos/owner/app/pulls?state=open"))), 101)
        self.assertEqual(api.call_args_list[1].args[1], "repos/owner/app/pulls?state=open&per_page=100&page=2")
        with patch.object(remediation, "MAX_PAGES", 2), patch.object(github, "api", return_value=pages[0]):
            with self.assertRaisesRegex(remediation.RemediationError, "more than 2 pages"):
                list(github.pages("repos/owner/app/pulls"))

    def test_main_reports_failures_without_a_traceback(self) -> None:
        error = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), contextlib.redirect_stderr(error):
            self.assertEqual(remediation.main(["remediate"]), 1)
        self.assertIn("DEPENDENCY REMEDIATION FAILED: This helper runs in GitHub Actions", error.getvalue())


def base_routes() -> dict:
    return {
        ("GET", f"repos/{REPO}"): {"default_branch": "main"},
        ("GET", f"repos/{REPO}/git/ref/heads/main"): {"object": {"sha": HEAD}},
    }


def audit_routes(documents: dict | None = None, run: dict | None = None, issues: list | None = None) -> dict:
    routes = base_routes()
    routes[("GET", f"repos/{REPO}/actions/runs/{RUN_ID}")] = run or security_run()
    routes[("GET", ISSUES)] = issues or []
    artifacts = []
    for index, (python, document) in enumerate((documents or {p: report(p) for p in PYTHONS}).items()):
        if document is None:
            continue
        data = archive(document)
        identifier = 200 + index
        artifacts.append({
            "id": identifier, "name": remediation.ARTIFACT_PREFIX + python, "expired": False,
            "size_in_bytes": len(data),
        })
        routes[("GET", f"repos/{REPO}/actions/artifacts/{identifier}/zip")] = data
    routes[("GET", f"repos/{REPO}/actions/runs/{RUN_ID}/artifacts")] = {"artifacts": artifacts}
    return routes


def changes(github: FakeGitHub) -> list[tuple[str, str, object, bool]]:
    return [call for call in github.calls if call[0] != "GET"]


DEPENDABOT_BRANCH = "dependabot/pip/requests-2.32.0"


def dependabot_pull(number: int = 12, sha: str = PR_SHA, ref: str = DEPENDABOT_BRANCH) -> dict:
    return {
        "number": number, "user": {"login": remediation.DEPENDABOT}, "body": "Bumps requests.",
        "head": {"ref": ref, "sha": sha, "repo": {"full_name": REPO}}, "base": {"ref": "main"},
    }


def copilot_pull_request(number: int = 9, body: str = "Fixes #5", ref: str = "copilot/fix-dependencies") -> dict:
    return {
        "number": number, "user": {"login": remediation.COPILOT_AUTHOR}, "body": body,
        "head": {"ref": ref, "sha": PR_SHA, "repo": {"full_name": REPO}}, "base": {"ref": "main"},
    }


class RemediateTests(WorkflowFixture):
    def test_new_findings_open_an_issue_and_assign_copilot(self) -> None:
        routes = audit_routes()
        # An older artifact for the same Python must be ignored; its download route is deliberately missing.
        routes[("GET", f"repos/{REPO}/actions/runs/{RUN_ID}/artifacts")]["artifacts"].append(
            {"id": 150, "name": "dependency-findings-3.11", "expired": False, "size_in_bytes": 10}
        )
        routes.update({
            ("GET", f"repos/{REPO}/labels/{remediation.LABEL}"): remediation.GitHubError("missing", 404),
            ("POST", f"repos/{REPO}/labels"): {},
            ("POST", f"repos/{REPO}/issues"): {"number": 7, "assignees": []},
            ("GET", PULLS): [],
            ("POST", f"repos/{REPO}/issues/7/assignees"): {"assignees": [{"login": "Copilot"}]},
        })
        github = FakeGitHub(routes)
        output = self.run_command(remediation.remediate, github)
        created = next(data for resource, data, _ in github.sent("POST") if resource == f"repos/{REPO}/issues")
        self.assertEqual(created["title"], "Fix vulnerable dependency: requests")
        self.assertEqual(created["labels"], [remediation.LABEL])
        body = created["body"]
        self.assertTrue(body.startswith(remediation.ISSUE_MARKER.format(current_digest())))
        self.assertEqual(body.splitlines()[1], remediation.PACKAGES_MARKER.format("requests"))
        self.assertIn(
            f"| `requests` | `2.31.0` | [{ADVISORY}](https://osv.dev/vulnerability/{ADVISORY}) | CVE-2024-35195 "
            "| `2.32.0` | 3.11, 3.12, 3.13, 3.14 |",
            body,
        )
        self.assertIsNone(re.search(r"#\d", body))
        resource, payload, agent = github.sent("POST")[-1]
        self.assertEqual((resource, agent), (f"repos/{REPO}/issues/7/assignees", True))
        self.assertEqual(payload["assignees"], [remediation.COPILOT_ASSIGNEE])
        assignment = payload["agent_assignment"]
        self.assertEqual((assignment["target_repo"], assignment["base_branch"]), (REPO, "main"))
        self.assertIn("Resolve issue #7", assignment["custom_instructions"])
        self.assertEqual([call[1] for call in github.calls if call[3]], [f"repos/{REPO}/issues/7/assignees"])
        self.assertIn("Assigned Copilot to #7.", output)
        self.assertIn("- Opened #7 for the vulnerable dependencies.", self.summary.read_text(encoding="utf-8"))

    def test_unchanged_findings_leave_the_tracked_issue_alone(self) -> None:
        routes = audit_routes(issues=[tracked_issue(digest=current_digest())])
        routes[("GET", PULLS)] = []
        github = FakeGitHub(routes)
        output = self.run_command(remediation.remediate, github)
        self.assertEqual(changes(github), [])
        self.assertIn("#5 already tracks the current dependency findings.", output)
        self.assertIn("Copilot is already assigned to #5.", output)

    def test_changed_findings_update_the_issue_and_its_copilot_pull_request(self) -> None:
        routes = audit_routes(issues=[tracked_issue(digest="0" * 16)])
        routes.update({
            ("PATCH", f"repos/{REPO}/issues/5"): {},
            ("GET", f"repos/{REPO}/issues/5/comments"): [],
            ("POST", f"repos/{REPO}/issues/5/comments"): {},
            ("GET", PULLS): [copilot_pull_request(9), copilot_pull_request(10, "Fixes #6"), dependabot_pull()],
            ("GET", "user"): {"login": "maintainer"},
            ("GET", f"repos/{REPO}/issues/9/comments"): [],
            ("POST", f"repos/{REPO}/issues/9/comments"): {},
            # The Dependabot update of a listed package is reviewed too; its checks are still running.
            ("GET", f"repos/{REPO}/actions/runs?head_sha={PR_SHA}&event=pull_request"): {"workflow_runs": []},
        })
        github = FakeGitHub(routes)
        output = self.run_command(remediation.remediate, github)
        digest = current_digest()
        (_, pending, _), (_, final, _) = github.sent("PATCH")
        self.assertEqual(pending["title"], "Fix vulnerable dependency: requests")
        self.assertTrue(pending["body"].startswith(remediation.ISSUE_MARKER.format(digest)))
        self.assertIn(remediation.PENDING_MARKER, pending["body"])
        self.assertEqual(final, {"body": pending["body"].replace(remediation.PENDING_MARKER + "\n", "")})
        posts = github.sent("POST")
        self.assertEqual(
            [(resource, agent) for resource, _, agent in posts],
            [(f"repos/{REPO}/issues/5/comments", False), (f"repos/{REPO}/issues/9/comments", True)],
        )
        self.assertTrue(posts[1][1]["body"].startswith("@copilot The dependency findings in #5 changed"))
        for _, data, _ in posts:
            self.assertTrue(data["body"].endswith(remediation.FINDINGS_MARKER.format(digest)))
        self.assertIn("Waiting for the Security workflow to finish on #12", output)

    def test_failed_notices_are_retried_without_duplicates(self) -> None:
        routes = audit_routes(issues=[tracked_issue(digest="0" * 16)])
        routes.update({
            ("PATCH", f"repos/{REPO}/issues/5"): {},
            ("POST", f"repos/{REPO}/issues/5/comments"): {},
            ("GET", PULLS): [copilot_pull_request(9), copilot_pull_request(10)],
            ("GET", "user"): {"login": "maintainer"},
            ("POST", f"repos/{REPO}/issues/9/comments"): {},
            ("POST", f"repos/{REPO}/issues/10/comments"): remediation.GitHubError("failed", 502),
        })
        for number in (5, 9, 10):
            routes[("GET", f"repos/{REPO}/issues/{number}/comments")] = []
        failed = FakeGitHub(routes)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(remediation.GitHubError):
            remediation.remediate(failed)
        [(_, pending, _)] = failed.sent("PATCH")
        self.assertIn(remediation.PENDING_MARKER, pending["body"])
        # The retry finds the pending issue and the notices that were posted, and posts only the missing one.
        notices = {resource: data["body"] for resource, data, _ in failed.sent("POST")}
        routes.update({
            ("GET", ISSUES): [{**tracked_issue(), "body": pending["body"]}],
            ("GET", f"repos/{REPO}/issues/5/comments"): [
                comment(remediation.ISSUE_AUTHOR, notices[f"repos/{REPO}/issues/5/comments"]),
            ],
            ("GET", f"repos/{REPO}/issues/9/comments"): [
                comment("maintainer", notices[f"repos/{REPO}/issues/9/comments"]),
            ],
            ("POST", f"repos/{REPO}/issues/10/comments"): {},
        })
        retry = FakeGitHub(routes)
        output = self.run_command(remediation.remediate, retry)
        self.assertEqual([resource for resource, _, _ in retry.sent("POST")], [f"repos/{REPO}/issues/10/comments"])
        self.assertNotIn(remediation.PENDING_MARKER, retry.sent("PATCH")[-1][1]["body"])
        self.assertIn("Copilot was already asked to update #9 for the current findings.", output)

    def test_only_the_newest_notice_from_the_author_counts(self) -> None:
        notice = remediation.FINDINGS_MARKER.format
        digest = current_digest()
        cases = {
            "newest notice matches": ([comment("maintainer", notice("1" * 16)), comment("maintainer", notice(digest))],
                                      True),
            "findings changed back": ([comment("maintainer", notice(digest)), comment("maintainer", notice("1" * 16))],
                                      False),
            "other authors ignored": ([comment("intruder", notice(digest))], False),
        }
        for name, (comments, expected) in cases.items():
            with self.subTest(name):
                github = FakeGitHub({("GET", f"repos/{REPO}/issues/9/comments"): comments})
                self.assertEqual(remediation.announced(github, REPO, 9, "maintainer", digest), expected)

    def test_new_issues_review_failed_checks_on_covering_dependabot_updates(self) -> None:
        routes = audit_routes()
        routes.update({
            ("GET", f"repos/{REPO}/labels/{remediation.LABEL}"): {},
            ("POST", f"repos/{REPO}/issues"): {"number": 7, "assignees": []},
            # Only the update of a listed package is reviewed; the flask update has no routes for its checks.
            ("GET", PULLS): [dependabot_pull(), dependabot_pull(13, sha=OLD, ref="dependabot/pip/flask-3.0.0")],
            ("GET", f"repos/{REPO}/actions/runs?head_sha={PR_SHA}&event=pull_request"): {"workflow_runs": [
                pr_run(identifier=300), pr_run(remediation.SECURITY_WORKFLOW, "success", 301),
            ]},
            ("GET", f"repos/{REPO}/actions/runs/300/jobs?filter=latest"): job_list(),
            ("GET", "user"): {"login": "maintainer"},
            ("GET", f"repos/{REPO}/issues/12/comments"): [],
            ("POST", f"repos/{REPO}/issues/12/comments"): {},
        })
        github = FakeGitHub(routes)
        output = self.run_command(remediation.remediate, github)
        self.assertIn("already update requests to fixed versions; Copilot was not assigned to #7.", output)
        resource, data, agent = github.sent("POST")[-1]
        self.assertEqual((resource, agent), (f"repos/{REPO}/issues/12/comments", True))
        self.assertTrue(data["body"].startswith(f"@copilot Checks failed at `{PR_SHA[:12]}` on this pull request:"))
        self.assertIn("Keep the updated dependency version", data["body"])
        self.assertIn("(attempt 1 of 3)", output)
        github = FakeGitHub(routes, agent_token=None)
        output = self.run_command(remediation.remediate, github)
        self.assertNotIn(("GET", PULLS), [call[:2] for call in github.calls])
        self.assertIn("::warning::Add the COPILOT_REMEDIATION_TOKEN secret", output)

    def test_clean_audit_closes_open_issues(self) -> None:
        clean = {python: report(python, "clean") for python in PYTHONS}
        routes = audit_routes(clean, issues=[tracked_issue(5), tracked_issue(4, state="closed", reason="completed")])
        routes.update({("POST", f"repos/{REPO}/issues/5/comments"): {}, ("PATCH", f"repos/{REPO}/issues/5"): {}})
        github = FakeGitHub(routes)
        self.run_command(remediation.remediate, github)
        closed = {"state": "closed", "state_reason": "completed"}
        self.assertEqual(github.sent("PATCH"), [(f"repos/{REPO}/issues/5", closed, False)])
        self.assertIn("found no known vulnerabilities", github.sent("POST")[0][1]["body"])

    def test_incomplete_clean_audit_leaves_issues_open(self) -> None:
        documents = {
            "3.11": report("3.11", "clean"), "3.12": report("3.12", "clean"),
            "3.13": report("3.13", "error", revision=None), "3.14": None,
        }
        github = FakeGitHub(audit_routes(documents, issues=[tracked_issue()]))
        output = self.run_command(remediation.remediate, github)
        self.assertEqual(changes(github), [])
        self.assertIn("::warning::The Python 3.14 dependency findings report is missing or expired.", output)
        self.assertIn("::warning::The dependency audit is incomplete for Python 3.13, 3.14;", output)

    def test_findings_closed_as_not_planned_or_duplicate_are_not_tracked_again(self) -> None:
        for reason, label in (("not_planned", "not planned"), ("duplicate", "a duplicate")):
            with self.subTest(reason=reason):
                declined = tracked_issue(digest=current_digest(), state="closed", reason=reason)
                # The opt-out also keeps another open remediation issue from moving to the declined findings.
                github = FakeGitHub(audit_routes(issues=[declined, tracked_issue(6, digest="1" * 16)]))
                output = self.run_command(remediation.remediate, github)
                self.assertEqual(changes(github), [])
                self.assertIn(
                    f"::warning::#5 was closed as {label} for these exact findings; remediation issues were left "
                    "unchanged.",
                    output,
                )

    def test_runs_that_did_not_audit_the_current_head_change_nothing(self) -> None:
        cases = {
            "older commit": (security_run(head_sha=OLD), f"audited an older commit `{OLD[:12]}`"),
            "pull request": (security_run(event="pull_request"), "did not audit `main`"),
            "cancelled": (security_run(conclusion="cancelled"), "did not finish its audit"),
        }
        expected = [f"repos/{REPO}", f"repos/{REPO}/git/ref/heads/main", f"repos/{REPO}/actions/runs/{RUN_ID}"]
        for name, (run, message) in cases.items():
            with self.subTest(name):
                github = FakeGitHub(audit_routes(run=run))
                self.assertIn(message, self.run_command(remediation.remediate, github))
                self.assertEqual([call[1] for call in github.calls], expected)
        github = FakeGitHub(audit_routes(run=security_run(path=remediation.INSTALLER_WORKFLOW)))
        with self.assertRaisesRegex(remediation.RemediationError, "not this repository's Security workflow"):
            remediation.remediate(github)

    def test_manual_run_uses_the_latest_completed_audit_of_the_head(self) -> None:
        self.use_event("workflow_dispatch")
        query = urllib.parse.urlencode({"branch": "main", "head_sha": HEAD, "status": "completed"})
        lookup = ("GET", f"repos/{REPO}/actions/workflows/security.yml/runs?{query}")
        routes = audit_routes({python: report(python, "clean") for python in PYTHONS})
        routes[lookup] = {"workflow_runs": [
            security_run(id=RUN_ID - 1), security_run(id=RUN_ID + 1, event="pull_request"),
            security_run(id=RUN_ID + 2, conclusion="cancelled"), security_run(event="workflow_dispatch"),
        ]}
        self.assertIn("is clean on Python 3.11-3.14", self.run_command(remediation.remediate, FakeGitHub(routes)))
        routes[lookup] = {"workflow_runs": []}
        output = self.run_command(remediation.remediate, FakeGitHub(routes))
        self.assertIn("No completed Security audit was found", output)


class AssignmentTests(WorkflowFixture):
    def assign(self, github: FakeGitHub, findings: list[dict], pulls: list[dict] | None = None) -> str:
        updates = remediation.dependabot_pulls(pulls or [], REPO, "main")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            remediation.assign(github, REPO, "main", {"number": 7, "assignees": []}, findings, updates)
        return output.getvalue()

    def test_assignment_waits_for_a_token_and_a_fixed_release(self) -> None:
        output = self.assign(FakeGitHub({}, agent_token=None), [finding("requests")])
        self.assertIn("::warning::Add the COPILOT_REMEDIATION_TOKEN secret, or assign Copilot to #7 manually", output)
        output = self.assign(FakeGitHub({}), [finding("requests", ())])
        self.assertIn("No fixed releases are available yet for the findings in #7", output)

    def test_dependabot_pull_requests_narrow_or_replace_the_assignment(self) -> None:
        human = {**dependabot_pull(4, ref="dependabot/pip/urllib3-2.2.2"), "user": {"login": "octocat"}}
        pulls = [dependabot_pull(3, ref="dependabot/pip/Requests-2.32.0"), human]
        github = FakeGitHub({})
        output = self.assign(github, [finding("requests")], pulls)
        self.assertIn(
            "Open Dependabot pull requests already update requests to fixed versions; Copilot was not assigned to #7.",
            output,
        )
        self.assertEqual(changes(github), [])
        assigned = {"assignees": [{"login": remediation.COPILOT_ASSIGNEE}]}
        github = FakeGitHub({("POST", f"repos/{REPO}/issues/7/assignees"): assigned})
        self.assign(github, [finding("requests"), finding("urllib3", ("2.2.2",)), finding("zipp", ())], pulls)
        instructions = github.sent("POST")[0][1]["agent_assignment"]["custom_instructions"]
        self.assertIn("already update requests to fixed versions; leave those packages unchanged.", instructions)
        self.assertIn("No fixed release exists yet for zipp;", instructions)

    def test_dependabot_coverage_requires_a_fixed_version_for_every_advisory(self) -> None:
        second = finding("requests", ("2.32.4",), advisory="GHSA-9hjg-9r4m-mvj7")
        cases = {
            "stale version": ("dependabot/pip/requests-2.31.1", [finding("requests")], []),
            "newer version": ("dependabot/pip/requests-2.33.0", [finding("requests")], ["requests"]),
            "pre-release of the fix": ("dependabot/pip/requests-2.32.0rc1", [finding("requests")], []),
            "listed pre-release fix": ("dependabot/pip/requests-2.32.0rc1", [finding("requests", ("2.32.0rc1",))],
                                       ["requests"]),
            "one of two advisories": ("dependabot/pip/requests-2.32.0", [finding("requests"), second], []),
            "both advisories": ("dependabot/pip/requests-2.32.4", [finding("requests"), second], ["requests"]),
            "digit in package name": ("dependabot/pip/django-2fa-1.2.0", [finding("django-2fa", ("1.2.0",))],
                                      ["django-2fa"]),
            "other ecosystem": ("dependabot/github_actions/requests-2.33.0", [finding("requests")], []),
        }
        for name, (ref, findings, covered) in cases.items():
            with self.subTest(name):
                updates = remediation.dependabot_pulls([dependabot_pull(3, ref=ref)], REPO, "main")
                self.assertEqual(remediation.dependabot_coverage(updates, findings), covered)

    def test_only_valid_dependabot_pull_requests_into_the_branch_count(self) -> None:
        valid = dependabot_pull()
        invalid = [
            {**valid, "base": {"ref": "release"}},
            {**valid, "head": {**valid["head"], "sha": "not a commit"}},
            {**valid, "number": "12"},
        ]
        self.assertEqual(remediation.dependabot_pulls([valid, *invalid], REPO, "main"), [valid])

    def test_silently_ignored_assignment_fails_the_run(self) -> None:
        github = FakeGitHub({
            ("POST", f"repos/{REPO}/issues/7/assignees"): {"assignees": []},
            ("GET", f"repos/{REPO}/issues/7"): {"assignees": []},
        })
        with self.assertRaisesRegex(remediation.RemediationError, "Copilot was not assigned to #7"):
            self.assign(github, [finding("requests")])
        self.assertEqual([call[3] for call in github.calls], [True, True])


def job_list() -> dict:
    return {"jobs": [
        {"id": 900, "name": "test", "conclusion": "failure", "steps": [
            {"name": "Run tests", "conclusion": "failure"}, {"name": "Checkout", "conclusion": "success"},
        ]},
        {"id": 901, "name": "build", "conclusion": "skipped", "steps": []},
    ]}


def pr_run(
    path: str = remediation.INSTALLER_WORKFLOW, conclusion: str | None = "failure", identifier: int = RUN_ID,
    branch: str = DEPENDABOT_BRANCH, **values: object,
) -> dict:
    run = {
        "id": identifier, "path": path, "event": "pull_request", "head_branch": branch, "head_sha": PR_SHA,
        "status": "completed", "conclusion": conclusion, "head_repository": {"full_name": REPO},
    }
    run.update(values)
    return run


def feedback_routes(pull: dict, runs: list[dict] | None = None, comments: list[dict] | None = None) -> dict:
    branch = pull["head"]["ref"]
    query = urllib.parse.urlencode({"state": "open", "head": f"owner:{branch}", "base": "main"})
    if runs is None:
        runs = [
            pr_run(branch=branch), pr_run(remediation.SECURITY_WORKFLOW, "success", 102, branch),
            pr_run(conclusion="success", identifier=50, branch=branch),
        ]
    routes = base_routes()
    routes.update({
        ("GET", f"repos/{REPO}/actions/runs/{RUN_ID}"): pr_run(branch=branch),
        ("GET", f"repos/{REPO}/pulls?{query}"): [pull],
        ("GET", ISSUES): [tracked_issue()],
        ("GET", f"repos/{REPO}/actions/runs?head_sha={PR_SHA}&event=pull_request"): {"workflow_runs": runs},
        ("GET", f"repos/{REPO}/actions/runs/{RUN_ID}/jobs?filter=latest"): job_list(),
        ("GET", "user"): {"login": "maintainer"},
        ("GET", f"repos/{REPO}/issues/{pull['number']}/comments"): comments or [],
        ("POST", f"repos/{REPO}/issues/{pull['number']}/comments"): {},
    })
    return routes


def comment(login: str, body: str) -> dict:
    return {"user": {"login": login}, "body": body}


class FeedbackTests(WorkflowFixture):
    def test_failed_dependabot_checks_ask_copilot_to_fix_them(self) -> None:
        github = FakeGitHub(feedback_routes(dependabot_pull()))
        output = self.run_command(remediation.feedback, github)
        self.assertEqual(len(changes(github)), 1)
        resource, data, agent = github.sent("POST")[0]
        self.assertEqual((resource, agent), (f"repos/{REPO}/issues/12/comments", True))
        body = data["body"]
        self.assertTrue(body.startswith(f"@copilot Checks failed at `{PR_SHA[:12]}` on this pull request:"))
        self.assertEqual(
            [line for line in body.splitlines() if line.startswith("- ")],
            [f"- Windows installer / test (failed steps: Run tests): [logs]({remediation.SERVER}/{REPO}/actions/runs/"
             f"{RUN_ID}/job/900)"],
        )
        self.assertIn("Keep the updated dependency version", body)
        self.assertTrue(body.endswith(remediation.CI_MARKER.format(PR_SHA)))
        self.assertIn(("GET", "user", None, True), github.calls)
        self.assertIn("(attempt 1 of 3)", output)

    def test_fix_requests_are_deduplicated_and_capped(self) -> None:
        marker = remediation.CI_MARKER.format
        previous = [comment("maintainer", marker(digit * 40)) for digit in "def"]
        spoofed = [comment("intruder", marker(PR_SHA)), comment("intruder", remediation.LIMIT_MARKER)]
        cases = {
            "already requested": ([comment("maintainer", marker(PR_SHA))], "already asked to fix the checks on #12", None),
            "limit recorded": ([comment("maintainer", remediation.LIMIT_MARKER)], "already reached the limit", None),
            "attempts exhausted": (previous, "#12 reached the limit of 3 automatic fix requests", remediation.LIMIT_MARKER),
            "other authors ignored": (spoofed, "(attempt 1 of 3)", marker(PR_SHA)),
        }
        for name, (comments, message, ending) in cases.items():
            with self.subTest(name):
                github = FakeGitHub(feedback_routes(dependabot_pull(), comments=comments))
                self.assertIn(message, self.run_command(remediation.feedback, github))
                posts = github.sent("POST")
                if ending is None:
                    self.assertEqual(posts, [])
                else:
                    self.assertEqual(len(posts), 1)
                    self.assertTrue(posts[0][1]["body"].endswith(ending))

    def test_feedback_waits_for_final_results_on_the_latest_commit(self) -> None:
        pull = dependabot_pull()
        installer = pr_run()
        security = pr_run(remediation.SECURITY_WORKFLOW, "success", 102)
        waiting = "Waiting for the Security workflow to finish"
        cases = {
            "security missing": (feedback_routes(pull, [installer]), waiting),
            "security running": (
                feedback_routes(pull, [installer, {**security, "status": "in_progress", "conclusion": None}]), waiting,
            ),
            "approval required": (feedback_routes(pull, [installer, {**security, "conclusion": "action_required"}]),
                                  waiting),
            "newer commit": (feedback_routes(dependabot_pull(sha=OLD)), f"#12 has commits newer than `{PR_SHA[:12]}`"),
            "all passing": (feedback_routes(pull, [{**installer, "conclusion": "success"}, security]),
                            "All checks pass for #12"),
        }
        for name, (routes, message) in cases.items():
            with self.subTest(name):
                github = FakeGitHub(routes)
                self.assertIn(message, self.run_command(remediation.feedback, github))
                self.assertEqual(changes(github), [])
                self.assertNotIn("user", [call[1] for call in github.calls])
        github = FakeGitHub(feedback_routes(pull), agent_token=None)
        output = self.run_command(remediation.feedback, github)
        self.assertIn("::warning::Checks failed on #12; add the COPILOT_REMEDIATION_TOKEN secret", output)
        self.assertEqual(changes(github), [])

    def test_feedback_only_handles_dependabot_and_remediation_pull_requests(self) -> None:
        human = feedback_routes({**dependabot_pull(), "user": {"login": "octocat"}})
        untracked = feedback_routes(copilot_pull_request(body="Fixes #40"))
        untracked[("GET", f"repos/{REPO}/issues/40")] = {"number": 40, "user": {"login": "octocat"}, "state": "open"}
        pushed = feedback_routes(dependabot_pull())
        pushed[("GET", f"repos/{REPO}/actions/runs/{RUN_ID}")] = pr_run(event="push")
        resolved = feedback_routes(dependabot_pull())
        resolved[("GET", ISSUES)] = [tracked_issue(state="closed", reason="completed")]
        ignored = "is not a Dependabot pip update or Copilot pull request for an open dependency remediation issue"
        cases = {
            "human pull request": (human, ignored),
            "untracked issue": (untracked, ignored),
            "other ecosystem": (feedback_routes(dependabot_pull(ref="dependabot/github_actions/actions/checkout-4.2.0")),
                                ignored),
            "unlisted package": (feedback_routes(dependabot_pull(ref="dependabot/pip/flask-3.0.0")), ignored),
            "closed remediation issue": (resolved, ignored),
            "push run": (pushed, "is not a completed pull request check from this repository"),
        }
        for name, (routes, message) in cases.items():
            with self.subTest(name):
                github = FakeGitHub(routes)
                self.assertIn(message, self.run_command(remediation.feedback, github))
                self.assertEqual(changes(github), [])
        linked = feedback_routes(copilot_pull_request(body="Fixes #41\nResolves #5"))
        linked[("GET", f"repos/{REPO}/issues/41")] = remediation.GitHubError("missing", 404)
        linked[("GET", f"repos/{REPO}/issues/5")] = tracked_issue()
        github = FakeGitHub(linked)
        self.run_command(remediation.feedback, github)
        self.assertIn("Keep resolving the linked dependency remediation issue", github.sent("POST")[0][1]["body"])


if __name__ == "__main__":
    unittest.main()
