from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / ".security-tools"
GITLEAKS_VERSION = "8.30.1"
PIP_AUDIT_VERSION = "2.10.1"
GITLEAKS_HASHES = {
    "arm64": "b95f5e4f5c425cedca7ee203d9afd29597e692c4924a12ed42f970537c72cc0f",
    "x64": "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e",
}
HOOK_MARKER = "# App Activity security pre-push hook"
PIN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?"
    r"==[A-Za-z0-9][A-Za-z0-9.!+_-]*(?:\s*;\s*.+)?"
)
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class SecurityError(Exception):
    pass


def run(
    command: list[str], *, cwd: Path, input: str | None = None,
    env: dict[str, str] | None = None, timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, cwd=cwd, input=input, env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError as error:
        raise SecurityError(
            f"Required tool is missing: {Path(command[0]).name}. "
            "Run python tools\\security_check.py setup."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise SecurityError(
            f"{Path(command[0]).name} timed out; the check is incomplete."
        ) from error


def git(repo: Path, *arguments: str) -> str:
    result = run(["git", *arguments], cwd=repo)
    if result.returncode:
        raise SecurityError(
            "Git could not read the requested history. Fetch the remote refs "
            "and retry; raw output is withheld to avoid exposing credentials."
        )
    return result.stdout.strip()


def repository() -> Path:
    return Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))


def clean_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("PIP_", "GITLEAKS_", "PYTHON"))
    }
    environment.update({
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_INDEX_URL": "https://pypi.org/simple",
        "PIP_ONLY_BINARY": ":all:",
        "PIP_NO_INPUT": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    return environment


def audit_python() -> Path:
    return TOOLS / "venv" / "Scripts" / "python.exe"


def verify_audit_interpreter() -> None:
    result = run(
        [str(audit_python()), "-I", "-c",
         "import json,platform,sys; print(json.dumps([list(sys.version_info[:2]),platform.machine().lower()]))"],
        cwd=ROOT, env=clean_environment(), timeout=30,
    )
    try:
        actual = json.loads(result.stdout)
    except ValueError as error:
        raise SecurityError("Cannot verify the isolated audit interpreter; run setup.") from error
    expected = [list(sys.version_info[:2]), platform.machine().lower()]
    if result.returncode or actual != expected:
        raise SecurityError(
            "The security-tool environment uses a different Python version or architecture. "
            "Use its original interpreter or move .security-tools aside and rerun setup."
        )


def check_version(command: list[str], expected: str) -> None:
    result = run(command, cwd=ROOT, env=clean_environment(), timeout=30)
    if result.returncode or expected not in result.stdout.strip().split():
        raise SecurityError(
            f"Expected {Path(command[0]).name} version {expected}; "
            "run python tools\\security_check.py setup."
        )


def setup() -> None:
    if sys.platform != "win32":
        raise SecurityError("Tool setup supports Windows x64 and ARM64.")
    architecture = {"amd64": "x64", "arm64": "arm64"}.get(platform.machine().lower())
    if architecture is None:
        raise SecurityError("Unsupported Windows architecture; no scan was run.")
    TOOLS.mkdir(exist_ok=True)
    executable = TOOLS / "gitleaks.exe"
    if not executable.exists():
        asset = f"gitleaks_{GITLEAKS_VERSION}_windows_{architecture}.zip"
        url = f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}/{asset}"
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                archive = response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise SecurityError("Gitleaks download failed; check network access to GitHub.") from error
        if hashlib.sha256(archive).hexdigest() != GITLEAKS_HASHES[architecture]:
            raise SecurityError("Gitleaks download checksum mismatch; refusing to install.")
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            executable.write_bytes(zipped.read("gitleaks.exe"))
    check_version([str(executable), "version"], GITLEAKS_VERSION)
    if not audit_python().exists():
        venv.EnvBuilder(with_pip=True).create(TOOLS / "venv")
    verify_audit_interpreter()
    result = run(
        [str(audit_python()), "-I", "-m", "pip", "install",
         "--index-url", "https://pypi.org/simple", "--only-binary=:all:",
         "-r", str(ROOT / "requirements-security.txt")],
        cwd=ROOT, env=clean_environment(),
    )
    if result.returncode:
        if "SSL" in result.stderr or "TLS" in result.stderr:
            raise SecurityError(
                "Security-tool installation failed during TLS negotiation with PyPI's "
                "package service. Check network/proxy certificates; certificate verification was not disabled."
            )
        raise SecurityError(
            "Security-tool installation failed. Verify PyPI connectivity and "
            "wheel availability for this Python/Windows version; no global packages were changed."
        )
    check_version([str(audit_python()), "-I", "-m", "pip_audit", "--version"], PIP_AUDIT_VERSION)
    print("Pinned Gitleaks and pip-audit installed in .security-tools.")


def commit(repo: Path, value: str) -> str:
    if not OID.fullmatch(value):
        raise SecurityError("Invalid object ID in pre-push input.")
    return git(repo, "rev-parse", "--verify", value + "^{commit}")


def outgoing(repo: Path, stdin: str) -> list[tuple[str, str]]:
    targets = []
    for line in stdin.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise SecurityError("Malformed pre-push ref input; refusing an incomplete scan.")
        _, local, _, remote = fields
        if not OID.fullmatch(local) or not OID.fullmatch(remote):
            raise SecurityError("Invalid pre-push object ID.")
        if set(local) == {"0"}:
            continue
        head = commit(repo, local)
        base = None if set(remote) == {"0"} else commit(repo, remote)
        target = (f"{base}..{head}" if base else head, head)
        if target not in targets:
            targets.append(target)
    return targets


def scan_secrets(repo: Path, revisions: list[str]) -> None:
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise SecurityError("Shallow history cannot be scanned completely; fetch --unshallow first.")
    executable = TOOLS / "gitleaks.exe"
    check_version([str(executable), "version"], GITLEAKS_VERSION)
    with tempfile.TemporaryDirectory(prefix="app-activity-secrets-") as directory:
        report = Path(directory) / "findings.json"
        common = [
            "--config", str(ROOT / ".gitleaks.toml"),
            "--gitleaks-ignore-path", str(repo / ".gitleaksignore"),
            "--ignore-gitleaks-allow", "--redact=100", "--no-banner", "--no-color",
            "--log-level", "error", "--report-format", "json", "--report-path", str(report),
        ]
        for revision in revisions:
            options = f"--full-history -m {revision}"
            for mode in ("git", "stdin"):
                arguments = [str(executable), mode, *common]
                text = None
                if mode == "git":
                    arguments += ["--log-opts=" + options, str(repo)]
                else:
                    text = git(repo, "log", "--format=%B", *revision.split())
                result = run(arguments, cwd=repo, input=text, env=clean_environment())
                if result.returncode not in (0, 1) or not report.exists():
                    raise SecurityError(
                        f"Gitleaks failed (exit {result.returncode}); scan incomplete. "
                        "Raw scanner output is withheld to protect secrets."
                    )
                try:
                    findings = json.loads(report.read_text(encoding="utf-8"))
                except (ValueError, OSError) as error:
                    raise SecurityError("Gitleaks produced an unreadable report; scan incomplete.") from error
                if not isinstance(findings, list):
                    raise SecurityError("Gitleaks report has an unexpected format.")
                if findings:
                    for finding in findings:
                        print(json.dumps({
                            key: finding.get(key)
                            for key in ("RuleID", "File", "StartLine", "Commit", "Fingerprint")
                        }))
                    raise SecurityError("Secret detected. Remove it from outgoing history and rotate real credentials.")
                if result.returncode:
                    raise SecurityError("Gitleaks returned an error without findings; scan incomplete.")
                report.unlink()
    print("Secret scan passed (commit patches, merge changes, and commit messages).")


def dependency_manifests(repo: Path, revision: str) -> dict[str, str]:
    files = git(repo, "ls-tree", "-r", "--name-only", revision).splitlines()
    unsupported = {
        "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock",
        "poetry.lock", "uv.lock", "package.json",
    }
    if any(Path(name).name in unsupported for name in files):
        raise SecurityError("A new dependency manifest format needs audit support before pushing.")
    manifests = {}
    for name in files:
        path = Path(name)
        if path.name.startswith("requirements") and path.suffix == ".txt":
            if len(path.parts) != 1:
                raise SecurityError("Nested requirements need explicit audit support before pushing.")
            content = git(repo, "show", f"{revision}:{name}")
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not PIN.fullmatch(line):
                    raise SecurityError(
                        f"{name} contains unsupported requirements. Only exact package pins "
                        "(with optional extras/markers) are supported; URLs, includes, and pip options are not executed."
                    )
            manifests[name] = content
    if not manifests:
        raise SecurityError("No supported dependency manifests found; audit coverage must be reviewed.")
    return manifests


def inspect_audit(result: subprocess.CompletedProcess[str]) -> int:
    try:
        document = json.loads(result.stdout)
    except ValueError as error:
        raise SecurityError(
            f"Dependency audit failed (exit {result.returncode}); check network access, "
            "package resolution and wheel availability. No complete advisory report was returned."
        ) from error
    if not isinstance(document, dict) or not isinstance(document.get("dependencies"), list):
        raise SecurityError("Dependency audit report has an unexpected format.")
    dependencies = document["dependencies"]
    if not dependencies:
        raise SecurityError("Dependency audit returned no packages; coverage is incomplete.")
    found = False
    for package in dependencies:
        if package.get("skip_reason") or not isinstance(package.get("vulns"), list):
            raise SecurityError("A dependency could not be audited; coverage is incomplete.")
        for vulnerability in package["vulns"]:
            found = True
            print(json.dumps({
                "package": package.get("name"), "version": package.get("version"),
                "advisory": vulnerability.get("id"),
                "fix_versions": vulnerability.get("fix_versions"),
            }))
    if found:
        raise SecurityError("Known dependency vulnerabilities found; review and update affected pins explicitly.")
    if result.returncode:
        raise SecurityError(f"Dependency audit failed (exit {result.returncode}); no successful result was accepted.")
    return len(dependencies)


def audit_dependencies(repo: Path, revisions: list[str]) -> None:
    if sys.platform != "win32" or not (3, 11) <= sys.version_info[:2] <= (3, 14):
        raise SecurityError("Audit coverage requires Windows Python 3.11-3.14; use the Windows CI matrix.")
    python = audit_python()
    verify_audit_interpreter()
    check_version([str(python), "-I", "-m", "pip_audit", "--version"], PIP_AUDIT_VERSION)
    seen = set()
    for revision in revisions:
        manifests = dependency_manifests(repo, revision)
        snapshot = tuple(sorted(manifests.items()))
        if snapshot in seen:
            continue
        seen.add(snapshot)
        with tempfile.TemporaryDirectory(prefix="app-activity-audit-") as directory:
            root = Path(directory)
            arguments = [
                str(python), "-I", "-m", "pip_audit",
                "--strict", "--format", "json", "--progress-spinner", "off",
                "--timeout", "30", "--index-url", "https://pypi.org/simple",
                "--cache-dir", str(root / "advisory-cache"),
            ]
            for name, content in manifests.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                arguments += ["-r", str(path)]
            result = run(arguments, cwd=root, env=clean_environment())
            count = inspect_audit(result)
            print(
                f"Dependency audit passed: {count} resolved packages from {len(manifests)} manifests "
                f"at {revision[:12]} (Windows Python {sys.version_info.major}.{sys.version_info.minor})."
            )


def install_hook(repo: Path) -> None:
    configured = run(["git", "config", "--get", "core.hooksPath"], cwd=repo)
    if configured.returncode != 1:
        raise SecurityError(
            "An existing core.hooksPath configuration was left unchanged. "
            "Integrate this command into your existing pre-push hook manually."
        )
    hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    hooks.mkdir(exist_ok=True)
    hook, previous = hooks / "pre-push", hooks / "pre-push.before-security"
    if hook.is_symlink() or previous.is_symlink():
        raise SecurityError("Existing hook symlink was left unchanged; integrate the check manually.")
    preserve = hook.exists() and HOOK_MARKER not in hook.read_text(encoding="utf-8", errors="replace")
    if preserve:
        if previous.exists():
            raise SecurityError("Hook backup already exists; refusing to overwrite either existing hook.")
    quote = lambda path: shlex.quote(Path(path).as_posix())
    content = (
        f"#!/bin/sh\n{HOOK_MARKER}\n"
        'input=$(mktemp) || exit 1\n'
        'trap \'rm -f "$input"\' EXIT HUP INT TERM\n'
        'cat > "$input" || exit 1\n'
        f'{quote(sys.executable)} {quote(Path(__file__).resolve())} pre-push "$@" '
        '< "$input" || exit $?\n'
    )
    if preserve or previous.exists():
        content += f'{quote(previous)} "$@" < "$input"\n'
    with tempfile.NamedTemporaryFile(
        mode="w", dir=hooks, prefix="pre-push-security-", delete=False,
        encoding="utf-8", newline="\n",
    ) as temporary:
        replacement = Path(temporary.name)
        temporary.write(content)
    try:
        replacement.chmod(replacement.stat().st_mode | 0o111)
        if preserve:
            hook.rename(previous)
        try:
            replacement.replace(hook)
        except OSError:
            if preserve:
                previous.rename(hook)
            raise
    finally:
        replacement.unlink(missing_ok=True)
    print("Installed repository-local pre-push hook; any previous hook is preserved and chained.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local, fail-closed repository security checks.")
    parser.add_argument(
        "command", choices=("setup", "install-hook", "check", "secrets", "dependencies", "pre-push"),
    )
    parser.add_argument("remote", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("remote_url", nargs="?", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
            return 0
        repo = repository()
        if args.command == "install-hook":
            install_hook(repo)
            return 0
        if args.command == "pre-push":
            targets = outgoing(repo, sys.stdin.read())
            if not targets:
                print("No outgoing commits (empty push or ref deletions only).")
                return 0
            scan_secrets(repo, [revision for revision, _ in targets])
            audit_dependencies(repo, [head for _, head in targets])
        else:
            if args.command in ("check", "secrets"):
                scan_secrets(repo, ["--all HEAD"])
            if args.command in ("check", "dependencies"):
                audit_dependencies(repo, [git(repo, "rev-parse", "HEAD")])
    except (SecurityError, OSError, zipfile.BadZipFile) as error:
        if isinstance(error, SecurityError):
            print(f"SECURITY CHECK FAILED: {error}", file=sys.stderr)
        else:
            print("SECURITY CHECK FAILED: local tool or file operation failed; check setup and permissions.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
