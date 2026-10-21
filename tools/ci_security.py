from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "app-activity-ci-security"
BETTERLEAKS_VERSION = "1.9.0"
BETTERLEAKS_HASHES = {
    "arm64": "3e5b9b6cdbfe8d486ae2dd2fa137473dc9ab42bf50317f051b73561121861816",
    "x64": "b765daec9fda475b031b5a5159798cbe4294a3f2a50683cbc0471226e376d3da",
}
MINGIT_VERSION = "2.56.0.windows.2"
MINGIT_HASHES = {
    "arm64": "38b33dc6024026e3315cf88ab2cfea65205bbd7bb3a8e824bd21c8ad4fe609a7",
    "x64": "da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be",
}
PIN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?"
    r"==[A-Za-z0-9][A-Za-z0-9.!+_-]*(?:\s*;\s*.+)?"
)


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
            "Check the security workflow's tool-installation step."
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
        if not key.upper().startswith(("PIP_", "BETTERLEAKS_", "GITLEAKS_", "PYTHON"))
    }
    environment.update({
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_INDEX_URL": "https://pypi.org/simple",
        "PIP_ONLY_BINARY": ":all:",
        "PIP_NO_INPUT": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    if local_git().exists():
        environment["PATH"] = str(local_git().parent) + os.pathsep + environment.get("PATH", "")
    return environment


def local_git() -> Path:
    return TOOLS / f"git-{MINGIT_VERSION}" / "cmd" / "git.exe"


def git_supports_isolation(environment: dict[str, str], executable: Path | None = None) -> bool:
    environment = {
        **environment, "GIT_CONFIG_GLOBAL": "NUL", "GIT_CONFIG_SYSTEM": "NUL",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1",
    }
    command = str(executable or (local_git() if local_git().exists() else "git"))
    result = run(
        [command, "rev-parse", "--is-inside-work-tree"], cwd=ROOT,
        env=environment, timeout=30,
    )
    return result.returncode == 0 and not result.stderr.strip()


def download_release(url: str, digest: str, tool: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            archive = response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise SecurityError(f"{tool} download failed; check network access to GitHub.") from error
    if hashlib.sha256(archive).hexdigest() != digest:
        raise SecurityError(f"{tool} download checksum mismatch; refusing to install.")
    return archive


def mingit_asset_name(version: str, architecture: str) -> str:
    match = re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+)\.windows\.([1-9][0-9]*)", version)
    if not match or architecture not in {"arm64", "x64"}:
        raise SecurityError("Unsupported MinGit version or Windows architecture.")
    archive_version, revision = match.groups()
    if revision != "1":
        archive_version += "." + revision
    suffix = {"arm64": "arm64", "x64": "64-bit"}[architecture]
    return f"MinGit-{archive_version}-{suffix}.zip"


def setup_git(architecture: str) -> None:
    environment = clean_environment()
    if local_git().exists():
        check_version([str(local_git()), "--version"], MINGIT_VERSION)
    if git_supports_isolation(environment):
        return
    destination = local_git().parent.parent
    if destination.exists():
        raise SecurityError("Runner Git is incomplete or incompatible; rerun the security job.")
    url = (
        f"https://github.com/git-for-windows/git/releases/download/v{MINGIT_VERSION}/"
        f"{mingit_asset_name(MINGIT_VERSION, architecture)}"
    )
    archive = download_release(url, MINGIT_HASHES[architecture], "MinGit")
    with tempfile.TemporaryDirectory(prefix="mingit-setup-", dir=TOOLS) as directory:
        staging = Path(directory) / "git"
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in zipped.namelist()):
                raise SecurityError("MinGit archive contains an unsafe path; refusing to install.")
            zipped.extractall(staging)
        executable = staging / "cmd" / "git.exe"
        check_version([str(executable), "--version"], MINGIT_VERSION)
        environment["PATH"] = str(executable.parent) + os.pathsep + environment.get("PATH", "")
        if not git_supports_isolation(environment, executable):
            raise SecurityError("Downloaded MinGit failed its isolation compatibility check.")
        staging.rename(destination)
    print(f"Installed isolated runner Git {MINGIT_VERSION}; system Git and settings are unchanged.")


def audit_python() -> Path:
    return TOOLS / "venv" / "Scripts" / "python.exe"


def pip_audit_version(requirements: str | None = None) -> str:
    if requirements is None:
        requirements = (ROOT / "requirements-security.txt").read_text(encoding="utf-8")
    pins = [
        match.group(1)
        for line in requirements.splitlines()
        if (match := re.fullmatch(r"pip-audit==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", line.strip()))
    ]
    if len(pins) != 1:
        raise SecurityError("requirements-security.txt must contain exactly one unconditional pip-audit pin.")
    return pins[0]


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
            "Rerun the security workflow with a fresh runner."
        )


def check_version(command: list[str], expected: str) -> None:
    result = run(command, cwd=ROOT, env=clean_environment(), timeout=30)
    if result.returncode or expected not in result.stdout.strip().split():
        raise SecurityError(
            f"Expected {Path(command[0]).name} version {expected}; "
            "check the security workflow's tool-installation step."
        )


def setup_secrets() -> None:
    if sys.platform != "win32":
        raise SecurityError("Tool setup supports Windows x64 and ARM64.")
    architecture = {"amd64": "x64", "arm64": "arm64"}.get(platform.machine().lower())
    if architecture is None:
        raise SecurityError("Unsupported Windows architecture; no scan was run.")
    TOOLS.mkdir(exist_ok=True)
    executable = TOOLS / "betterleaks.exe"
    asset = f"betterleaks_{BETTERLEAKS_VERSION}_windows_{architecture}.zip"
    url = f"https://github.com/betterleaks/betterleaks/releases/download/v{BETTERLEAKS_VERSION}/{asset}"
    archive = download_release(url, BETTERLEAKS_HASHES[architecture], "Betterleaks")
    with tempfile.TemporaryDirectory(prefix="betterleaks-setup-", dir=TOOLS) as directory:
        replacement = Path(directory) / "betterleaks.exe"
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            try:
                replacement.write_bytes(zipped.read("betterleaks.exe"))
            except KeyError as error:
                raise SecurityError("Betterleaks release archive is missing its executable.") from error
        check_version([str(replacement), "version"], BETTERLEAKS_VERSION)
        replacement.replace(executable)
    setup_git(architecture)
    print("Pinned Betterleaks installed in runner temporary storage.")


def setup_dependencies() -> None:
    TOOLS.mkdir(exist_ok=True)
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
    check_version([str(audit_python()), "-I", "-m", "pip_audit", "--version"], pip_audit_version())
    print("Pinned pip-audit installed in runner temporary storage.")


def scan_secrets(repo: Path, revisions: list[str]) -> None:
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise SecurityError("Shallow history cannot be scanned completely; fetch --unshallow first.")
    executable = TOOLS / "betterleaks.exe"
    check_version([str(executable), "version"], BETTERLEAKS_VERSION)
    if sys.platform == "win32" and not git_supports_isolation(clean_environment()):
        raise SecurityError(
            "Git for Windows cannot read the scanner's isolated configuration; scan incomplete. "
            "Check the workflow's scanner setup step for the corrected isolated runner Git."
        )
    # Native redaction can leave secrets in commit metadata; never persist raw reports.
    common = [
        "--config", str(ROOT / ".betterleaks.toml"),
        "--gitleaks-ignore-path", str(repo / ".betterleaksignore"),
        "--ignore-gitleaks-allow", "--redact=100", "--no-banner", "--no-color",
        "--log-level", "error", "--report-format", "json", "--report-path", "-",
        "--validation=false", "--max-archive-depth=0", "--max-decode-depth=0",
    ]
    for revision in revisions:
        options = f"--full-history -m {revision}"
        for mode in ("git", "stdin"):
            arguments = [str(executable), mode, *common]
            text = None
            if mode == "git":
                arguments += ["--git-workers=0", "--log-opts=" + options, str(repo)]
            else:
                text = git(repo, "log", "--format=%B", *revision.split())
            result = run(arguments, cwd=repo, input=text, env=clean_environment())
            if result.returncode not in (0, 1) or result.stderr.strip():
                raise SecurityError(
                    f"Betterleaks failed or reported an error (exit {result.returncode}); scan incomplete. "
                    "Raw scanner output is withheld to protect secrets."
                )
            try:
                findings = json.loads(result.stdout)
            except ValueError as error:
                raise SecurityError("Betterleaks produced an unreadable report; scan incomplete.") from error
            if not isinstance(findings, list) or any(not isinstance(finding, dict) for finding in findings):
                raise SecurityError("Betterleaks report has an unexpected format.")
            if findings:
                for finding in findings:
                    print(json.dumps({
                        key: finding.get(key)
                        for key in ("RuleID", "File", "StartLine", "Commit", "Fingerprint")
                    }))
                raise SecurityError("Secret detected. Remove it from outgoing history and rotate real credentials.")
            if result.returncode:
                raise SecurityError("Betterleaks returned an error without findings; scan incomplete.")
    print("Secret scan passed (commit patches, merge changes, and commit messages).")


def dependency_manifests(repo: Path, revision: str) -> dict[str, str]:
    files = git(repo, "ls-tree", "-r", "--name-only", revision).splitlines()
    unsupported = {
        "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock",
        "poetry.lock", "uv.lock", "package.json",
    }
    if any(Path(name).name in unsupported for name in files):
        raise SecurityError("A new dependency manifest format needs audit support before merging.")
    manifests = {}
    for name in files:
        path = Path(name)
        if path.name.startswith("requirements") and path.suffix == ".txt":
            if len(path.parts) != 1:
                raise SecurityError("Nested requirements need explicit audit support before merging.")
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
    check_version([str(python), "-I", "-m", "pip_audit", "--version"], pip_audit_version())
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Security checks for GitHub Actions runners.")
    parser.add_argument(
        "command", choices=("setup-secrets", "setup-dependencies", "secrets", "dependencies"),
    )
    args = parser.parse_args()
    try:
        if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("RUNNER_TEMP"):
            raise SecurityError("This helper runs in GitHub Actions; no local security setup is required.")
        if args.command == "setup-secrets":
            setup_secrets()
            return 0
        if args.command == "setup-dependencies":
            setup_dependencies()
            return 0
        repo = repository()
        if args.command == "secrets":
            scan_secrets(repo, ["--all HEAD"])
        else:
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
