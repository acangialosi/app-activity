from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import security_check as security


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

    def push_line(self, head: str, base: str | None = None) -> str:
        return f"refs/heads/local {head} refs/heads/remote {base or '0' * 40}\n"


class RepositoryTests(RepositoryFixture):
    def test_multiple_new_existing_and_deleted_refs(self) -> None:
        head = self.save("file.txt", "clean\n")
        deleted = f"(delete) {'0' * 40} refs/heads/old {self.base}\n"
        stdin = self.push_line(head, self.base) + self.push_line(head) + deleted
        self.assertEqual(security.outgoing(self.repo, stdin), [
            (f"{self.base}..{head}", head), (head, head),
        ])
        self.assertEqual(security.outgoing(self.repo, deleted), [])
        self.assertEqual(security.outgoing(self.repo, ""), [])

    def test_bad_input_and_unknown_remote_base_fail(self) -> None:
        for stdin in ("malformed", self.push_line("invalid"), self.push_line(self.base, "f" * 40)):
            with self.subTest(stdin=stdin), self.assertRaises(security.SecurityError):
                security.outgoing(self.repo, stdin)

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

    def test_custom_hooks_path_is_not_modified(self) -> None:
        directory = Path(self.temporary.name) / "existing-hooks"
        directory.mkdir()
        (directory / "pre-push").write_text("existing hook")
        self.git("config", "core.hooksPath", str(directory))
        with self.assertRaises(security.SecurityError):
            security.install_hook(self.repo)
        self.assertEqual((directory / "pre-push").read_text(), "existing hook")
        self.assertEqual(self.git("config", "core.hooksPath"), str(directory))

    def test_hook_install_failure_restores_existing_hook(self) -> None:
        original = self.repo / ".git" / "hooks" / "pre-push"
        original.write_text("#!/bin/sh\nexit 0\n")
        before = original.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                security.install_hook(self.repo)
        self.assertEqual(original.read_bytes(), before)
        self.assertFalse(original.with_name("pre-push.before-security").exists())

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

    def test_hook_preserves_arguments_stdin_and_original_failure(self) -> None:
        hooks = self.repo / ".git" / "hooks"
        original = hooks / "pre-push"
        original.write_text(
            '#!/bin/sh\ncat > original-stdin\nprintf "%s\\n" "$@" > original-args\nexit 7\n',
            newline="\n",
        )
        original.chmod(0o755)
        helper = Path(self.temporary.name) / "helper.py"
        helper.write_text(
            "import pathlib,sys\n"
            "pathlib.Path('security-stdin').write_text(sys.stdin.read())\n"
            "pathlib.Path('security-args').write_text('\\n'.join(sys.argv[2:])+'\\n')\n"
        )
        with patch.object(security, "__file__", str(helper)):
            security.install_hook(self.repo)
            security.install_hook(self.repo)
        remote = Path(self.temporary.name) / "remote.git"
        self.git("init", "--bare", "-q", str(remote))
        self.git("remote", "add", "fixture", str(remote))
        result = subprocess.run(
            ["git", "push", "fixture", "HEAD:refs/heads/test"],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            (self.repo / "security-stdin").read_text(),
            (self.repo / "original-stdin").read_text(),
        )
        self.assertEqual(
            (self.repo / "security-args").read_text(),
            (self.repo / "original-args").read_text(),
        )
        self.assertTrue((hooks / "pre-push.before-security").exists())
        helper.write_text("raise SystemExit(9)\n")
        (self.repo / "original-stdin").unlink()
        result = subprocess.run(
            ["git", "push", "fixture", "HEAD:refs/heads/test"],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "original-stdin").exists())


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
        }):
            environment = security.clean_environment()
        self.assertEqual(environment["PIP_INDEX_URL"], "https://pypi.org/simple")
        self.assertNotIn("PIP_AUDIT_OUTPUT", environment)
        self.assertNotIn("GITLEAKS_CONFIG_TOML", environment)
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
                security.setup()
            self.assertFalse((Path(directory) / "gitleaks.exe").exists())


@unittest.skipUnless((security.TOOLS / "gitleaks.exe").exists(), "Run security_check.py setup for real scanner tests")
class GitleaksTests(RepositoryFixture):
    def token(self) -> str:
        return "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"

    def assert_secret_blocked(self, revisions: list[str]) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(security.SecurityError, "Secret detected"):
                security.scan_secrets(self.repo, revisions)
        self.assertNotIn(self.token(), output.getvalue())
        self.assertNotIn("Finding", output.getvalue())
        self.assertIn("RuleID", output.getvalue())

    def test_clean_history_passes(self) -> None:
        security.scan_secrets(self.repo, ["--all HEAD"])

    def test_secret_added_then_deleted_is_blocked_for_new_and_existing_ref(self) -> None:
        self.save("credential.txt", 'token = "' + self.token() + '"\n')
        self.git("rm", "-q", "credential.txt")
        self.git("commit", "-q", "-m", "Remove fixture")
        head = self.git("rev-parse", "HEAD")
        for stdin in (self.push_line(head, self.base), self.push_line(head)):
            with self.subTest(stdin=stdin):
                targets = security.outgoing(self.repo, stdin)
                self.assert_secret_blocked([revision for revision, _ in targets])

    def test_non_checked_out_ref_and_multiple_refs_are_scanned(self) -> None:
        secret_head = self.save("credential.txt", 'token = "' + self.token() + '"\n')
        self.git("checkout", "-q", "--detach", self.base)
        clean_head = self.save("clean.txt", "safe\n")
        targets = security.outgoing(
            self.repo, self.push_line(clean_head, self.base) + self.push_line(secret_head, self.base),
        )
        self.assert_secret_blocked([revision for revision, _ in targets])

    def test_commit_message_secret_is_blocked(self) -> None:
        self.git("commit", "--allow-empty", "-q", "-m", "token = " + self.token())
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
        with patch.object(security, "ensure_gitleaks", return_value=security.TOOLS / "gitleaks.exe"), patch.object(
            security, "run", return_value=subprocess.CompletedProcess([], 2, "", self.token())
        ), patch.object(security, "git", return_value="false"):
            with self.assertRaisesRegex(security.SecurityError, "incomplete") as raised:
                security.scan_secrets(self.repo, ["--all HEAD"])
        self.assertNotIn(self.token(), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
