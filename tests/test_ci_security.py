from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tools import ci_security as security


class RepositoryFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="security-test-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Security test")
        self.git("config", "user.email", "security-test@example.invalid")
        self.base = self.save("requirements-build.txt", "pyinstaller==6.22.3\n")

    def git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments], cwd=self.repo, capture_output=True, text=True,
            encoding="utf-8", check=True,
        )
        return result.stdout.strip()

    def save(self, name: str, content: str) -> str:
        (self.repo / name).write_text(content, encoding="utf-8")
        self.git("add", "--", name)
        self.git("commit", "-q", "-m", "Fixture update")
        return self.git("rev-parse", "HEAD")


class RepositoryTests(RepositoryFixture):
    def test_dependency_snapshot_ignores_uncommitted_and_other_branch_changes(self) -> None:
        head = self.save("requirements-build.txt", "pyinstaller==6.22.2\n")
        (self.repo / "requirements-build.txt").write_text("pyinstaller==0.0.0\n")
        self.assertEqual(
            security.dependency_manifests(self.repo, self.base),
            {"requirements-build.txt": "pyinstaller==6.22.3"},
        )
        self.assertEqual(
            security.dependency_manifests(self.repo, head),
            {"requirements-build.txt": "pyinstaller==6.22.2"},
        )

    def test_unsupported_requirements_do_not_execute(self) -> None:
        for content in ("-r somewhere.txt", "project @ https://example.invalid/package", "--extra-index-url https://example.invalid"):
            head = self.save("requirements-build.txt", content)
            with self.subTest(content=content), self.assertRaises(security.SecurityError):
                security.dependency_manifests(self.repo, head)

    def test_unhandled_manifest_fails_instead_of_skipping(self) -> None:
        head = self.save("package.json", "{}")
        with self.assertRaises(security.SecurityError):
            security.dependency_manifests(self.repo, head)

    def test_audit_uses_committed_manifests_and_deduplicates_snapshots(self) -> None:
        clean = subprocess.CompletedProcess(
            [], 0, json.dumps({"dependencies": [{"name": "fixture", "vulns": []}]}), "",
        )
        manifests = security.dependency_manifests(self.repo, self.base)
        with patch.object(security, "verify_audit_interpreter"), patch.object(
            security, "check_version"
        ), patch.object(
            security, "dependency_manifests", return_value=manifests
        ), patch.object(security, "run", return_value=clean) as runner:
            security.audit_dependencies(self.repo, [self.base, self.base])
        runner.assert_called_once()
        arguments = runner.call_args.args[0]
        self.assertIn("--strict", arguments)
        self.assertIn("--cache-dir", arguments)
        self.assertIn("-r", arguments)
        self.assertNotIn("--fix", arguments)
        self.assertNotIn("--no-deps", arguments)
        self.assertEqual(runner.call_args.kwargs["env"]["PIP_ONLY_BINARY"], ":all:")
        self.assertNotEqual(runner.call_args.kwargs["cwd"], self.repo)


class AuditTests(unittest.TestCase):
    def report(self, packages: list[dict], code: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], code, json.dumps({"dependencies": packages}), "")

    def test_clean_and_vulnerable_dependency_results(self) -> None:
        clean = {"name": "fixture", "version": "1.0", "vulns": []}
        self.assertEqual(security.inspect_audit(self.report([clean])), 1)
        vulnerable = {**clean, "vulns": [{"id": "TEST-ADVISORY", "fix_versions": ["2.0"]}]}
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(security.SecurityError):
                security.inspect_audit(self.report([vulnerable], 1))
        self.assertIn("TEST-ADVISORY", output.getvalue())

    def test_missing_skipped_empty_and_failed_reports_fail(self) -> None:
        cases = [
            subprocess.CompletedProcess([], 1, "", "network failure"),
            subprocess.CompletedProcess([], 0, "not json", ""),
            self.report([]),
            self.report([{"name": "fixture", "skip_reason": "not available"}]),
            self.report([{"name": "fixture", "vulns": []}], 1),
        ]
        for result in cases:
            with self.subTest(result=result), self.assertRaises(security.SecurityError):
                security.inspect_audit(result)

    def test_tool_errors_are_not_success_and_do_not_echo_output(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(security.SecurityError, "missing"):
                security.run(["missing"], cwd=Path.cwd())
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("scanner", 1)):
            with self.assertRaisesRegex(security.SecurityError, "timed out"):
                security.run(["scanner"], cwd=Path.cwd())

    def test_environment_cannot_redirect_audit_or_scanner_output(self) -> None:
        with patch.dict(os.environ, {
            "PIP_INDEX_URL": "https://example.invalid",
            "PIP_AUDIT_OUTPUT": "unexpected.json",
            "GITLEAKS_CONFIG_TOML": "unexpected",
            "BETTERLEAKS_CONFIG_TOML": "unexpected",
            "BETTERLEAKS_VALIDATION": "true",
        }):
            environment = security.clean_environment()
        self.assertEqual(environment["PIP_INDEX_URL"], "https://pypi.org/simple")
        self.assertNotIn("PIP_AUDIT_OUTPUT", environment)
        self.assertNotIn("GITLEAKS_CONFIG_TOML", environment)
        self.assertNotIn("BETTERLEAKS_CONFIG_TOML", environment)
        self.assertNotIn("BETTERLEAKS_VALIDATION", environment)
        self.assertEqual(environment["PIP_ONLY_BINARY"], ":all:")

    def test_different_audit_interpreter_fails(self) -> None:
        result = subprocess.CompletedProcess([], 0, '[[3, 10], "unknown"]', "")
        with patch.object(security, "run", return_value=result):
            with self.assertRaisesRegex(security.SecurityError, "different Python"):
                security.verify_audit_interpreter()

    def test_download_checksum_mismatch_does_not_install_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            security, "TOOLS", Path(directory)
        ), patch("urllib.request.urlopen", return_value=io.BytesIO(b"not a trusted release")):
            with self.assertRaisesRegex(security.SecurityError, "checksum mismatch"):
                security.setup_secrets()
            self.assertFalse((Path(directory) / "betterleaks.exe").exists())


class ToolSetupTests(unittest.TestCase):
    def test_auditor_version_has_one_authoritative_requirement_pin(self) -> None:
        self.assertEqual(security.pip_audit_version("# tooling\npip-audit==9.8.7\n"), "9.8.7")
        for content in ("", "pip-audit>=9.8.7", "pip-audit==1.0.0\npip-audit==2.0.0",
                        'pip-audit==1.0.0; python_version >= "3.11"'):
            with self.subTest(content=content), self.assertRaises(security.SecurityError):
                security.pip_audit_version(content)

    def test_auditor_setup_verifies_the_version_changed_by_dependabot(self) -> None:
        executable = self.tools / "python.exe"
        executable.write_bytes(b"fixture")
        (self.tools / "requirements-security.txt").write_text("pip-audit==9.8.7\n", encoding="utf-8")
        with patch.object(security, "ROOT", self.tools), patch.object(
            security, "audit_python", return_value=executable
        ), patch.object(security, "verify_audit_interpreter"), patch.object(
            security, "run", return_value=subprocess.CompletedProcess([], 0, "", "")
        ), patch.object(security, "check_version") as version:
            security.setup_dependencies()
        self.assertEqual(version.call_args.args[1], "9.8.7")

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tools = Path(temporary.name)
        mock = patch.object(security, "TOOLS", self.tools)
        mock.start()
        self.addCleanup(mock.stop)

    def archive(self, name: str) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(name, b"verified fixture")
        return output.getvalue()

    def test_compatible_system_git_does_not_download(self) -> None:
        with patch.object(security, "git_supports_isolation", return_value=True), patch.object(
            security, "download_release"
        ) as download:
            security.setup_git("x64")
        download.assert_not_called()
        self.assertFalse(security.local_git().exists())

    def test_mingit_uses_published_revision_one_and_later_archive_names(self) -> None:
        for version, architecture, expected in (
            ("2.56.0.windows.1", "arm64", "MinGit-2.56.0-arm64.zip"),
            ("2.56.0.windows.1", "x64", "MinGit-2.56.0-64-bit.zip"),
            ("2.56.0.windows.2", "arm64", "MinGit-2.56.0.2-arm64.zip"),
            ("2.56.0.windows.2", "x64", "MinGit-2.56.0.2-64-bit.zip"),
        ):
            with self.subTest(version=version, architecture=architecture):
                self.assertEqual(security.mingit_asset_name(version, architecture), expected)

    def test_local_git_is_selected_only_for_security_subprocesses(self) -> None:
        executable = security.local_git()
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        before = dict(os.environ)
        environment = security.clean_environment()
        self.assertTrue(environment["PATH"].startswith(str(executable.parent) + os.pathsep))
        with patch.object(
            security, "run", return_value=subprocess.CompletedProcess([], 0, "true", "")
        ) as run:
            self.assertTrue(security.git_supports_isolation(environment))
        self.assertEqual(run.call_args.args[0][0], str(executable))
        self.assertEqual(run.call_args.kwargs["env"]["GIT_CONFIG_GLOBAL"], "NUL")
        self.assertEqual(run.call_args.kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(dict(os.environ), before)
        self.assertNotIn("GIT_CONFIG_GLOBAL", environment.keys() - before.keys())

    def test_git_fallback_is_verified_before_becoming_active(self) -> None:
        archive = self.archive("cmd/git.exe")
        with patch.object(security, "git_supports_isolation", side_effect=[False, True]) as probe, patch.object(
            security, "download_release", return_value=archive
        ) as download, patch.object(security, "check_version") as version:
            security.setup_git("x64")
        self.assertEqual(security.local_git().read_bytes(), b"verified fixture")
        self.assertIn(security.mingit_asset_name(security.MINGIT_VERSION, "x64"), download.call_args.args[0])
        self.assertEqual(download.call_args.args[1], security.MINGIT_HASHES["x64"])
        self.assertEqual(version.call_args.args[1], security.MINGIT_VERSION)
        self.assertEqual(probe.call_args.args[1], Path(version.call_args.args[0][0]))
        self.assertNotEqual(probe.call_args.args[1], security.local_git())
        with patch.object(security, "git_supports_isolation", return_value=True), patch.object(
            security, "check_version"
        ), patch.object(security, "download_release") as download:
            security.setup_git("x64")
        download.assert_not_called()

    def test_failed_git_fallback_is_not_installed(self) -> None:
        archive = self.archive("cmd/git.exe")
        with patch.object(security, "git_supports_isolation", return_value=False), patch.object(
            security, "download_release", return_value=archive
        ), patch.object(security, "check_version"):
            with self.assertRaisesRegex(security.SecurityError, "compatibility"):
                security.setup_git("x64")
        self.assertEqual(list(self.tools.iterdir()), [])

    def test_scanner_setup_failure_preserves_existing_binary(self) -> None:
        executable = self.tools / "betterleaks.exe"
        executable.write_bytes(b"previous scanner")
        for failure in ("checksum", "version"):
            archive = self.archive("betterleaks.exe")
            digest = "0" * 64 if failure == "checksum" else hashlib.sha256(archive).hexdigest()
            with self.subTest(failure=failure), patch.object(
                security.platform, "machine", return_value="AMD64"
            ), patch.object(security, "BETTERLEAKS_HASHES", {"x64": digest}), patch(
                "urllib.request.urlopen", return_value=io.BytesIO(archive)
            ), patch.object(security, "check_version", side_effect=security.SecurityError("version mismatch")):
                with self.assertRaises(security.SecurityError):
                    security.setup_secrets()
            self.assertEqual(executable.read_bytes(), b"previous scanner")
            self.assertEqual(list(self.tools.iterdir()), [executable])


class CICommandTests(unittest.TestCase):
    def test_no_local_command_installs_tools_or_scans(self) -> None:
        for command in ("setup-secrets", "setup-dependencies", "secrets", "dependencies"):
            with self.subTest(command=command), patch.dict(os.environ, {}, clear=True), patch(
                "sys.argv", ["ci_security.py", command]
            ), patch.object(security, "setup_secrets") as secrets, patch.object(
                security, "setup_dependencies"
            ) as dependencies, patch.object(security, "repository") as repository, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(security.main(), 1)
            secrets.assert_not_called()
            dependencies.assert_not_called()
            repository.assert_not_called()

    def test_ci_setup_installs_only_requested_tool(self) -> None:
        for command in ("setup-secrets", "setup-dependencies"):
            with self.subTest(command=command), patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "RUNNER_TEMP": tempfile.gettempdir(),
            }), patch("sys.argv", ["ci_security.py", command]), patch.object(
                security, "setup_secrets"
            ) as secrets, patch.object(security, "setup_dependencies") as dependencies:
                self.assertEqual(security.main(), 0)
                self.assertEqual(secrets.call_count, int(command == "setup-secrets"))
                self.assertEqual(dependencies.call_count, int(command == "setup-dependencies"))

    def test_tools_live_outside_repository(self) -> None:
        self.assertFalse(security.TOOLS.is_relative_to(security.ROOT))
        self.assertFalse(hasattr(security, "install_hook"))
        self.assertFalse(hasattr(security, "outgoing"))


@unittest.skipUnless((security.TOOLS / "betterleaks.exe").exists(), "Real scanner fixtures run in the security CI job")
class BetterleaksTests(RepositoryFixture):
    def token(self) -> str:
        return "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"

    def assert_secret_blocked(self, revisions: list[str]) -> None:
        run = security.run
        scans = []

        def inspect_report(command, **kwargs):
            result = run(command, **kwargs)
            if "--report-path" in command:
                self.assertEqual(command[command.index("--report-path") + 1], "-")
                self.assertIn("--redact=100", command)
                self.assertIn("--validation=false", command)
                scans.append(command)
            return result

        with contextlib.redirect_stdout(io.StringIO()) as output, patch.object(
            security, "run", side_effect=inspect_report
        ):
            with self.assertRaisesRegex(security.SecurityError, "Secret detected"):
                security.scan_secrets(self.repo, revisions)
        self.assertNotIn(self.token(), output.getvalue())
        self.assertNotIn("Finding", output.getvalue())
        self.assertIn("RuleID", output.getvalue())
        self.assertTrue(scans)
        self.assertNotIn("Message", output.getvalue())
        self.assertNotIn("Attributes", output.getvalue())

    def test_clean_history_passes(self) -> None:
        security.scan_secrets(self.repo, ["--all HEAD"])

    def test_secret_added_then_deleted_is_blocked_in_full_history(self) -> None:
        self.save("credential.txt", 'token = "' + self.token() + '"\n')
        self.git("rm", "-q", "credential.txt")
        self.git("commit", "-q", "-m", "Remove fixture")
        self.assert_secret_blocked(["--all HEAD"])

    def test_non_checked_out_ref_and_multiple_refs_are_scanned(self) -> None:
        self.save("credential.txt", 'token = "' + self.token() + '"\n')
        self.git("checkout", "-q", "--detach", self.base)
        self.save("clean.txt", "safe\n")
        self.assert_secret_blocked(["--all HEAD"])

    def test_commit_message_secret_is_blocked(self) -> None:
        self.git("commit", "--allow-empty", "-q", "-m", "token = " + self.token())
        self.assert_secret_blocked([self.base + "..HEAD"])

    def test_commit_metadata_is_not_written_or_echoed(self) -> None:
        (self.repo / "credential.txt").write_text('token = "' + self.token() + '"\n')
        self.git("add", "credential.txt")
        self.git("commit", "-q", "-m", "token = " + self.token())
        self.assert_secret_blocked([self.base + "..HEAD"])

    def test_secret_in_initial_commit_is_blocked(self) -> None:
        self.git("checkout", "-q", "--orphan", "new-root")
        self.git("rm", "-q", "-rf", ".")
        head = self.save("credential.txt", 'token = "' + self.token() + '"\n')
        self.assert_secret_blocked([head])

    def test_secret_added_only_by_merge_commit_is_blocked(self) -> None:
        original_branch = self.git("branch", "--show-current")
        self.git("checkout", "-q", "-b", "side")
        self.save("side.txt", "safe\n")
        self.git("checkout", "-q", original_branch)
        self.save("main.txt", "safe\n")
        self.git("merge", "--no-commit", "--no-ff", "side")
        self.save("credential.txt", 'token = "' + self.token() + '"\n')
        self.assert_secret_blocked([self.base + "..HEAD"])

    def test_shallow_history_is_rejected(self) -> None:
        clone = Path(self.temporary.name) / "shallow"
        self.git("clone", "-q", "--depth", "1", self.repo.as_uri(), str(clone))
        with self.assertRaisesRegex(security.SecurityError, "Shallow"):
            security.scan_secrets(clone, ["--all HEAD"])

    def test_scanner_failure_is_blocking_without_echoing_sensitive_diagnostics(self) -> None:
        with patch.object(security, "check_version"), patch.object(
            security, "git_supports_isolation", return_value=True
        ), patch.object(
            security, "run", return_value=subprocess.CompletedProcess([], 2, "", self.token())
        ), patch.object(security, "git", return_value="false"):
            with self.assertRaisesRegex(security.SecurityError, "incomplete") as raised:
                security.scan_secrets(self.repo, ["--all HEAD"])
        self.assertNotIn(self.token(), str(raised.exception))

    def test_error_diagnostics_block_even_with_zero_exit_code(self) -> None:
        with patch.object(security, "check_version"), patch.object(
            security, "git_supports_isolation", return_value=True
        ), patch.object(
            security, "run", return_value=subprocess.CompletedProcess([], 0, "", self.token())
        ), patch.object(security, "git", return_value="false"):
            with self.assertRaisesRegex(security.SecurityError, "incomplete") as raised:
                security.scan_secrets(self.repo, ["--all HEAD"])
        self.assertNotIn(self.token(), str(raised.exception))

    def test_missing_malformed_and_failed_reports_block(self) -> None:
        for code, report in ((0, ""), (0, "{}"), (0, "null"), (0, "[null]"), (1, "[]")):
            with self.subTest(code=code, report=report), patch.object(
                security, "check_version"
            ), patch.object(
                security, "git_supports_isolation", return_value=True
            ), patch.object(
                security, "git", return_value="false"
            ), patch.object(
                security, "run", return_value=subprocess.CompletedProcess([], code, report, "")
            ):
                with self.assertRaises(security.SecurityError):
                    security.scan_secrets(self.repo, ["HEAD"])

    def test_inline_allow_comments_do_not_bypass_detection(self) -> None:
        for marker in ("gitleaks:allow", "betterleaks:allow"):
            with self.subTest(marker=marker):
                head = self.save("credential.txt", 'token = "' + self.token() + '" # ' + marker + "\n")
                self.assert_secret_blocked([self.base + ".." + head])

    def test_invalid_history_is_not_a_successful_scan(self) -> None:
        with self.assertRaises(security.SecurityError):
            security.scan_secrets(self.repo, ["does-not-exist"])

    def test_provider_validation_stays_disabled(self) -> None:
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = (
                "[[rules]]\nid = 'fixture'\nregex = 'ghp_[A-Za-z0-9]{36}'\n"
                "validate = '''\n"
                f'let r = http.get("http://127.0.0.1:{server.server_port}/validate");\n'
                'r.status == 200 ? {"result": "valid"} : {"result": "invalid"}\n'
                "'''\n"
            )
            (self.repo / ".betterleaks.toml").write_text(config, encoding="utf-8")
            self.save("credential.txt", 'token = "' + self.token() + '"\n')
            with patch.object(security, "ROOT", self.repo), patch.dict(os.environ, {
                "BETTERLEAKS_VALIDATION": "true",
                "BETTERLEAKS_CONFIG": "nonexistent-config.toml",
                "GITLEAKS_CONFIG_TOML": "invalid override",
            }):
                self.assert_secret_blocked(["--all HEAD"])
            self.assertEqual(requests, [])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
