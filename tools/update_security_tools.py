from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from tools import ci_security as security


NATIVE_TOOLS = {
    "BETTERLEAKS": "betterleaks/betterleaks",
    "MINGIT": "git-for-windows/git",
}
BRANCH_PREFIX = "automation/security-tools-"
SOURCE_PATH = "tools/ci_security.py"
REQUIREMENTS_PATH = "requirements-security.txt"


def github(resource: str, data: dict | None = None) -> dict | list:
    command = ["gh", "api", resource]
    if data is not None:
        command += ["--method", "POST", "--input", "-"]
    result = security.run(
        command, cwd=security.ROOT,
        input=json.dumps(data) if data is not None else None,
    )
    if result.returncode:
        raise security.SecurityError(
            f"GitHub request failed for {resource}; check permissions and connectivity."
        )
    try:
        document = json.loads(result.stdout)
    except ValueError as error:
        raise security.SecurityError("GitHub returned invalid JSON; no successful update was accepted.") from error
    if not isinstance(document, (dict, list)):
        raise security.SecurityError("GitHub returned an unexpected response.")
    return document


def version_key(version: str, tool: str) -> tuple[int, ...]:
    pattern = r"[0-9]+\.[0-9]+\.[0-9]+"
    if tool == "MINGIT":
        pattern += r"\.windows\.[0-9]+"
    if not isinstance(version, str) or not re.fullmatch(pattern, version):
        raise security.SecurityError(f"{tool} needs a stable, supported version; refusing {version!r}.")
    return tuple(int(part) for part in version.replace(".windows.", ".").split("."))


def assignment(source: str, name: str) -> ast.Assign:
    matches = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
    ]
    if len(matches) != 1:
        raise security.SecurityError(f"Expected exactly one {name} assignment; review the pin format.")
    return matches[0]


def replace_constant(source: str, name: str, value: str | dict) -> str:
    node = assignment(source, name)
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [f"{name} = {json.dumps(value, indent=4)}\n"]
    return "".join(lines)


def pin_files(repo: Path) -> dict[str, str]:
    return {path: (repo / path).read_text(encoding="utf-8") for path in (SOURCE_PATH, REQUIREMENTS_PATH)}


def parse_pins(files: dict[str, str]) -> dict:
    source = files[SOURCE_PATH]
    pins = {
        tool: {
            "version": ast.literal_eval(assignment(source, f"{tool}_VERSION").value),
            "hashes": ast.literal_eval(assignment(source, f"{tool}_HASHES").value),
        }
        for tool in NATIVE_TOOLS
    }
    pins["PIP_AUDIT"] = {
        "version": security.pip_audit_version(files[REQUIREMENTS_PATH]),
    }
    return pins


def current_pins(repo: Path) -> dict:
    return parse_pins(pin_files(repo))


def validate_pin(tool: str, pin: dict) -> None:
    keys = {"version", "hashes"} if tool in NATIVE_TOOLS else {"version"}
    if not isinstance(pin, dict) or set(pin) != keys:
        raise security.SecurityError(f"{tool} has an invalid pin schema.")
    version_key(pin["version"], tool)
    if tool in NATIVE_TOOLS:
        hashes = pin["hashes"]
        if not isinstance(hashes, dict) or set(hashes) != {"arm64", "x64"} or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in hashes.values()
        ):
            raise security.SecurityError(f"{tool} needs SHA-256 pins for both Windows architectures.")


def render_pin_files(original_files: dict[str, str], updates: dict) -> dict[str, str]:
    current = parse_pins(original_files)
    if not isinstance(updates, dict) or set(updates) - set(current):
        raise security.SecurityError("Update plan contains unsupported tools.")
    source = original = original_files[SOURCE_PATH]
    files = {}
    for tool, pin in updates.items():
        validate_pin(tool, pin)
        validate_pin(tool, current[tool])
        if version_key(pin["version"], tool) < version_key(current[tool]["version"], tool):
            raise security.SecurityError(f"{tool} would be downgraded; refusing the update.")
        if pin["version"] == current[tool]["version"]:
            if pin != current[tool]:
                raise security.SecurityError(f"{tool} release checksums changed without a new version.")
            continue
        if tool in NATIVE_TOOLS:
            source = replace_constant(source, f"{tool}_VERSION", pin["version"])
            source = replace_constant(source, f"{tool}_HASHES", pin["hashes"])
        else:
            requirements = original_files[REQUIREMENTS_PATH]
            updated, count = re.subn(
                r"(?m)^[ \t]*pip-audit==[^\s]+[ \t]*$",
                f"pip-audit=={pin['version']}", requirements,
            )
            if count != 1:
                raise security.SecurityError("Cannot update the authoritative pip-audit requirement.")
            files[REQUIREMENTS_PATH] = updated
    if source != original:
        files[SOURCE_PATH] = source
    return files


def render_updates(repo: Path, updates: dict) -> dict[str, str]:
    return render_pin_files(pin_files(repo), updates)


def asset_name(tool: str, version: str, architecture: str) -> str:
    if tool == "BETTERLEAKS":
        return f"betterleaks_{version}_windows_{architecture}.zip"
    return security.mingit_asset_name(version, architecture)


def asset_url(tool: str, version: str, architecture: str) -> str:
    return (
        f"https://github.com/{NATIVE_TOOLS[tool]}/releases/download/v{version}/"
        f"{asset_name(tool, version, architecture)}"
    )


def discover(repo: Path) -> dict:
    current = current_pins(repo)
    candidates = {}
    for tool, upstream in NATIVE_TOOLS.items():
        release = github(f"repos/{upstream}/releases/latest")
        if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
            raise security.SecurityError(f"{tool} did not return a stable published release.")
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise security.SecurityError(f"{tool} has an unsupported release tag.")
        version = tag[1:]
        version_key(version, tool)
        assets = release.get("assets")
        if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
            raise security.SecurityError(f"{tool} release assets are missing or malformed.")
        hashes = {}
        for architecture in ("arm64", "x64"):
            matches = [asset for asset in assets if asset.get("name") == asset_name(tool, version, architecture)]
            if len(matches) != 1:
                raise security.SecurityError(f"{tool} is missing a unique Windows {architecture} archive.")
            asset = matches[0]
            digest = asset.get("digest")
            if (
                not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                or asset.get("browser_download_url") != asset_url(tool, version, architecture)
            ):
                raise security.SecurityError(f"{tool} archive metadata has no trusted SHA-256/URL.")
            hashes[architecture] = digest.removeprefix("sha256:")
        candidates[tool] = {"version": version, "hashes": hashes}
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/pip-audit/json", timeout=60) as response:
            package = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise security.SecurityError("Could not read pip-audit release metadata from PyPI.") from error
    if (
        not isinstance(package, dict) or not isinstance(package.get("info"), dict)
        or package["info"].get("name") != "pip-audit"
        or not isinstance(package.get("urls"), list)
        or not any(isinstance(file, dict) and file.get("packagetype") == "bdist_wheel"
                   and file.get("yanked") is False for file in package["urls"])
    ):
        raise security.SecurityError("pip-audit needs a published, non-yanked wheel.")
    candidates["PIP_AUDIT"] = {"version": package["info"].get("version")}
    render_updates(repo, candidates)
    updates = {tool: pin for tool, pin in candidates.items() if pin != current[tool]}
    for tool, pin in updates.items():
        if tool in NATIVE_TOOLS:
            for architecture, digest in pin["hashes"].items():
                security.download_release(asset_url(tool, pin["version"], architecture), digest, tool)
    return updates


def github_pages(resource: str) -> Iterator[dict]:
    separator = "&" if "?" in resource else "?"
    page = 1
    while True:
        items = github(f"{resource}{separator}per_page=100&page={page}")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise security.SecurityError(f"Could not inspect paginated GitHub data for {resource}.")
        yield from items
        if len(items) < 100:
            return
        page += 1


def original_pin_files(resource: str, revision: str) -> dict[str, str]:
    files = {}
    for path in (SOURCE_PATH, REQUIREMENTS_PATH):
        blob = github(f"{resource}/contents/{path}?ref={revision}")
        if not isinstance(blob, dict) or blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
            raise security.SecurityError("Cannot verify the original update branch's pin files.")
        try:
            content = base64.b64decode("".join(blob["content"].split()), validate=True).decode("utf-8")
            files[path] = content.replace("\r\n", "\n").replace("\r", "\n")
        except ValueError as error:
            raise security.SecurityError("The original pin files have an invalid encoding.") from error
    return files


def publish(repo: Path, updates: dict, base: str) -> str | None:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or any(part in {".", ".."} for part in repository.split("/"))
        or not re.fullmatch(r"[0-9a-f]{40}", base)
    ):
        raise security.SecurityError("Publishing requires a scheduled/manual Actions run and a verified base.")
    resource = f"repos/{repository}"
    default = github(resource)["default_branch"]
    if os.environ.get("GITHUB_REF") != f"refs/heads/{default}":
        raise security.SecurityError("Only the default-branch workflow may publish update PRs.")
    if security.git(repo, "status", "--porcelain") or security.git(repo, "rev-parse", "HEAD") != base:
        raise security.SecurityError("Publishing requires a clean checkout of the discovery commit.")
    if github(f"{resource}/git/ref/heads/{default}")["object"]["sha"] != base:
        raise security.SecurityError("The default branch changed after discovery; rerun instead of publishing stale pins.")
    files = render_updates(repo, updates)
    if not files:
        print("No security-tool upgrades to publish.")
        return None
    for pull in github_pages(f"{resource}/pulls?state=open"):
        if pull["head"]["ref"].startswith(BRANCH_PREFIX):
            print(f"Existing scanner update PR: {pull['html_url']}; leaving its work unchanged.")
            return None
        if any(file["filename"] in files for file in github_pages(f"{resource}/pulls/{pull['number']}/files")):
            print(f"Existing PR changes the same pin files: {pull['html_url']}; deferring this proposal.")
            return None
    fingerprint = hashlib.sha256(json.dumps(updates, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    branch = BRANCH_PREFIX + fingerprint
    query = urllib.parse.urlencode({"state": "all", "head": f"{repository.split('/')[0]}:{branch}"})
    previous = github(f"{resource}/pulls?{query}")
    if not isinstance(previous, list):
        raise security.SecurityError("Could not inspect earlier upgrade proposals.")
    if previous:
        print(f"These upgrades were already proposed: {previous[0]['html_url']}; not duplicating the PR.")
        return None
    refs = github(f"{resource}/git/matching-refs/heads/{branch}")
    if not isinstance(refs, list):
        raise security.SecurityError("Could not inspect the update branch.")
    matches = [ref for ref in refs if ref["ref"] == f"refs/heads/{branch}"]
    original_base = base
    existing = None
    if matches:
        existing = github(f"{resource}/git/commits/{matches[0]['object']['sha']}")
        if len(existing["parents"]) != 1:
            raise security.SecurityError("An unexpected update branch already exists; refusing to overwrite it.")
        original_base = existing["parents"][0]["sha"]
        if original_base != base:
            comparison = github(f"{resource}/compare/{original_base}...{base}")
            if comparison["merge_base_commit"]["sha"] != original_base or comparison["status"] != "ahead":
                raise security.SecurityError("The orphan update branch is not based on earlier main; refusing to reuse it.")
            files = render_pin_files(original_pin_files(resource, original_base), updates)
            if not files:
                raise security.SecurityError("The orphan branch has no matching upgrade changes; refusing to reuse it.")
    base_commit = github(f"{resource}/git/commits/{original_base}")
    tree = github(f"{resource}/git/trees", {
        "base_tree": base_commit["tree"]["sha"],
        "tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in sorted(files.items())
        ],
    })
    if existing:
        if existing["tree"]["sha"] != tree["sha"]:
            raise security.SecurityError("An unexpected update branch already exists; refusing to overwrite it.")
    else:
        commit = github(f"{resource}/git/commits", {
            "message": "Update pinned security tools",
            "tree": tree["sha"], "parents": [base],
        })
        github(f"{resource}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    changes = "\n".join(f"- {tool}: `{pin['version']}`" for tool, pin in sorted(updates.items()))
    pull = github(f"{resource}/pulls", {
        "title": "Update pinned security tools", "head": branch, "base": default, "draft": True,
        "body": (
            "## Pinned scanner upgrades\n\n" + changes
            + "\n\nWindows archive SHA-256 pins were obtained from the official GitHub releases and "
            "verified against the downloaded archives. pip-audit remains exactly pinned in "
            "`requirements-security.txt`. No scanner binaries were installed by the updater.\n\n"
            "## Required acceptance\n\nApprove the workflow runs if GitHub requests it. "
            "Keep this PR in draft until required Windows tests/build/security checks pass and "
            "the latest commit has completed Copilot review with all valid findings addressed. "
            "Review release notes and any major-version compatibility changes before merging.\n\n"
            "A recovered orphan branch may predate main. Incorporate updated main and rerun all "
            "checks/review if required by the normal strict up-to-date protection.\n\n"
            "This workflow never approves, merges, force-pushes, or changes repository protections. "
            "It leaves an existing upgrade PR untouched so contributor fixes are preserved."
        ),
    })
    print(f"Created draft security-tool upgrade PR: {pull['html_url']}")
    return pull["html_url"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose reviewed upgrades for pinned CI security tools.")
    parser.add_argument("command", choices=("check", "publish"))
    args = parser.parse_args()
    try:
        if args.command == "check":
            updates = discover(security.ROOT)
            base = security.git(security.ROOT, "rev-parse", "HEAD")
            print(json.dumps(updates, indent=2) if updates else "Security tools already match the latest stable releases.")
            if output := os.environ.get("GITHUB_OUTPUT"):
                with Path(output).open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(f"base={base}\nupdates={json.dumps(updates)}\n")
        else:
            document = os.environ.get("SECURITY_TOOL_UPDATES")
            if not document:
                raise security.SecurityError("Missing verified security-tool update plan.")
            try:
                updates = json.loads(document)
            except ValueError as error:
                raise security.SecurityError("Invalid security-tool update plan JSON.") from error
            publish(security.ROOT, updates, os.environ.get("SECURITY_TOOL_BASE", ""))
    except (security.SecurityError, OSError) as error:
        print(f"SECURITY TOOL UPDATE FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
