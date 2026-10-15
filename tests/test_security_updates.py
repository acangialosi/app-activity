from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import security_check as security
from tools import update_gitleaks as updater


class GitleaksInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tools = Path(temporary.name)
        self.executable = self.tools / "gitleaks.exe"
        self.executable.write_bytes(b"old scanner")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("gitleaks.exe", b"new scanner")
        self.archive = buffer.getvalue()
        for mock in (
            patch.object(security, "TOOLS", self.tools),
            patch.object(security.sys, "platform", "win32"),
            patch.object(security.platform, "machine", return_value="AMD64"),
            patch.object(security, "GITLEAKS_HASHES", {"x64": hashlib.sha256(self.archive).hexdigest()}),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def result(self, version: str, code: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], code, version, "")

    def test_current_version_never_downloads(self) -> None:
        with patch.object(security, "run", return_value=self.result(security.GITLEAKS_VERSION)), patch(
            "urllib.request.urlopen"
        ) as download:
            self.assertEqual(security.ensure_gitleaks(), self.executable)
        download.assert_not_called()

    def test_outdated_and_missing_versions_install_atomically(self) -> None:
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.executable.unlink()
                with patch.object(security, "run", return_value=self.result("0.0.1")), patch(
                    "urllib.request.urlopen", return_value=io.BytesIO(self.archive)
                ) as download, patch.object(security, "check_version") as verify:
                    self.assertEqual(security.ensure_gitleaks(), self.executable)
                self.assertEqual(self.executable.read_bytes(), b"new scanner")
                self.assertEqual(list(self.tools.iterdir()), [self.executable])
                self.assertIn(f"/v{security.GITLEAKS_VERSION}/", download.call_args.args[0])
                self.assertNotEqual(Path(verify.call_args.args[0][0]), self.executable)
                self.assertEqual(verify.call_args.args[1], security.GITLEAKS_VERSION)

    def test_network_and_checksum_failures_keep_old_binary_but_fail(self) -> None:
        for failure in ("network", "checksum"):
            download = (
                patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline"))
                if failure == "network" else
                patch("urllib.request.urlopen", return_value=io.BytesIO(b"invalid archive"))
            )
            with self.subTest(failure=failure), download, patch.object(
                security, "run", return_value=self.result("0.0.1")
            ):
                with self.assertRaises(security.SecurityError):
                    security.ensure_gitleaks()
            self.assertEqual(self.executable.read_bytes(), b"old scanner")

    def test_wrong_downloaded_version_does_not_replace_existing_binary(self) -> None:
        with patch.object(security, "run", return_value=self.result("0.0.1")), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(self.archive)
        ), patch.object(security, "check_version", side_effect=security.SecurityError("wrong version")):
            with self.assertRaisesRegex(security.SecurityError, "wrong version"):
                security.ensure_gitleaks()
        self.assertEqual(self.executable.read_bytes(), b"old scanner")
        self.assertEqual(list(self.tools.iterdir()), [self.executable])

    def test_locked_binary_is_preserved_and_fails(self) -> None:
        with patch.object(security, "run", return_value=self.result("0.0.1")), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(self.archive)
        ), patch.object(security, "check_version"), patch.object(Path, "replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                security.ensure_gitleaks()
        self.assertEqual(self.executable.read_bytes(), b"old scanner")

    def test_unreadable_installed_version_is_not_treated_as_current(self) -> None:
        with patch.object(security, "run", return_value=self.result("", 1)):
            with self.assertRaisesRegex(security.SecurityError, "Cannot read"):
                security.ensure_gitleaks()

    def test_refresh_failure_prevents_scanner_execution(self) -> None:
        with patch.object(security, "git", return_value="false"), patch.object(
            security, "ensure_gitleaks", side_effect=security.SecurityError("refresh failed")
        ), patch.object(security, "run") as scanner:
            with self.assertRaisesRegex(security.SecurityError, "refresh failed"):
                security.scan_secrets(self.tools, ["HEAD"])
        scanner.assert_not_called()


class ReleaseUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "security_check.py"
        self.original = (
            'GITLEAKS_VERSION = "8.30.0"\n'
            'GITLEAKS_HASHES = {"arm64": "old", "x64": "old"}\n'
            'OTHER_PIN = "unchanged"\n'
        )
        self.path.write_text(self.original, encoding="utf-8")
        self.version = "8.30.1"
        self.archives = {"arm64": b"arm release fixture", "x64": b"x64 release fixture"}
        self.hashes = {arch: hashlib.sha256(data).hexdigest() for arch, data in self.archives.items()}
        self.release = {
            "tag_name": "v" + self.version, "draft": False, "prerelease": False,
            "assets": [{"name": name} for name in [
                f"gitleaks_{self.version}_checksums.txt",
                *(f"gitleaks_{self.version}_windows_{arch}.zip" for arch in self.archives),
            ]],
        }
        self.checksums = "".join(
            f"{digest}  gitleaks_{self.version}_windows_{arch}.zip\n"
            for arch, digest in self.hashes.items()
        )

    def download(self, url: str) -> bytes:
        if url == "https://api.github.com/repos/gitleaks/gitleaks/releases/latest":
            return json.dumps(self.release).encode()
        prefix = f"{updater.RELEASES}/v{self.version}/gitleaks_{self.version}_"
        self.assertTrue(url.startswith(prefix))
        name = url.removeprefix(prefix)
        if name == "checksums.txt":
            return self.checksums.encode()
        arch = name.removeprefix("windows_").removesuffix(".zip")
        return self.archives[arch]

    def test_verified_release_updates_only_pins_and_is_idempotent(self) -> None:
        with patch.object(updater, "download", side_effect=self.download) as download:
            self.assertEqual(updater.update(self.path), self.version)
            source = self.path.read_text()
            nodes = updater.assignments(source)
            self.assertEqual(ast.literal_eval(nodes["GITLEAKS_VERSION"].value), self.version)
            self.assertEqual(ast.literal_eval(nodes["GITLEAKS_HASHES"].value), self.hashes)
            self.assertTrue(source.endswith('OTHER_PIN = "unchanged"\n'))
            self.assertEqual(download.call_count, 4)
            self.assertIsNone(updater.update(self.path))
            self.assertEqual(download.call_count, 5)
            self.assertEqual(self.path.read_text(), source)

    def test_malformed_prerelease_and_downgrade_versions_fail(self) -> None:
        cases = [
            {"tag_name": "v8.29.0"}, {"tag_name": "v8.31.0-rc1"},
            {"tag_name": "v8.31.0\nmalformed"}, {"tag_name": "v08.31.0"},
            {"draft": True}, {"prerelease": True}, {"prerelease": None},
        ]
        for changes in cases:
            with self.subTest(changes=changes), patch.object(updater, "download", return_value=json.dumps({
                **self.release, **changes,
            }).encode()):
                with self.assertRaises(updater.UpdateError):
                    updater.update(self.path)
            self.assertEqual(self.path.read_text(), self.original)

    def test_missing_asset_and_ambiguous_checksum_do_not_change_pins(self) -> None:
        for failure in ("asset", "checksum", "duplicate"):
            original_assets = self.release["assets"]
            original_checksums = self.checksums
            if failure == "asset":
                self.release["assets"] = original_assets[:-1]
            elif failure == "checksum":
                self.checksums = "malformed"
            else:
                self.checksums += original_checksums
            with self.subTest(failure=failure), patch.object(updater, "download", side_effect=self.download):
                with self.assertRaises(updater.UpdateError):
                    updater.update(self.path)
            self.assertEqual(self.path.read_text(), self.original)
            self.release["assets"] = original_assets
            self.checksums = original_checksums

    def test_bad_archive_does_not_change_pins(self) -> None:
        self.archives["x64"] = b"corrupted"
        with patch.object(updater, "download", side_effect=self.download):
            with self.assertRaisesRegex(updater.UpdateError, "checksum mismatch"):
                updater.update(self.path)
        self.assertEqual(self.path.read_text(), self.original)

    def test_release_supplied_download_urls_are_not_used(self) -> None:
        for asset in self.release["assets"]:
            asset["browser_download_url"] = "https://example.invalid/untrusted"
        with patch.object(updater, "download", side_effect=self.download):
            updater.update(self.path)

    def test_changed_pin_layout_fails_before_network_access(self) -> None:
        self.path.write_text('GITLEAKS_VERSION = "8.30.0"\n')
        with patch.object(updater, "download") as download:
            with self.assertRaisesRegex(updater.UpdateError, "layout"):
                updater.update(self.path)
        download.assert_not_called()

    def test_network_failure_is_explicit(self) -> None:
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(updater.UpdateError, "download failed"):
                updater.update(self.path)
        self.assertEqual(self.path.read_text(), self.original)


if __name__ == "__main__":
    unittest.main()
