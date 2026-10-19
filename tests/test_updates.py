from __future__ import annotations

import io
import json
import threading
import unittest
from http.client import IncompleteRead
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app_activity import updates
from app_activity.dashboard import create_server


def metadata(
    built_at: str = "2026-10-15T12:00:00+00:00", channel: str = "nightly"
) -> dict[str, object]:
    return {"schema": 1, "version": "0.1.0", "channel": channel, "built_at": built_at}


class UpdateTests(unittest.TestCase):
    def checker(self, channel: str = "nightly") -> updates.UpdateChecker:
        with (
            patch.object(updates.sys, "frozen", True, create=True),
            patch.object(updates.Path, "read_text", return_value=json.dumps(metadata(channel=channel))),
        ):
            return updates.UpdateChecker()

    def test_source_checkout_does_not_contact_github(self) -> None:
        with (
            patch.object(updates.sys, "frozen", False, create=True),
            patch.object(updates, "urlopen") as download,
        ):
            checker = updates.UpdateChecker()
            status = checker.status(force=True)
            self.assertEqual(checker.status(), status)
        self.assertEqual(status["status"], "source")
        self.assertEqual(status["release_url"], updates.RELEASES_URL)
        download.assert_not_called()

    def test_missing_or_invalid_bundled_metadata_is_visible(self) -> None:
        for error in (FileNotFoundError(), ValueError()):
            with (
                self.subTest(error=error),
                patch.object(updates.sys, "frozen", True, create=True),
                patch.object(updates.Path, "read_text", side_effect=error),
                patch.object(updates, "urlopen") as download,
            ):
                checker = updates.UpdateChecker()
                status = checker.status(force=True)
                self.assertEqual(checker.status(), status)
                self.assertEqual(status["status"], "error")
                self.assertIn("Build information", status["message"])
                download.assert_not_called()

    def test_timestamp_comparison_includes_nightly_with_unchanged_version(self) -> None:
        for timestamp, expected in (
            ("2026-10-15T12:00:01Z", "available"),
            ("2026-10-15T12:00:00+00:00", "current"),
            ("2026-10-14T12:00:00+00:00", "current"),
            ("2026-10-15T05:00:00-07:00", "current"),
            ("2026-10-15T06:00:00-07:00", "available"),
        ):
            with self.subTest(timestamp=timestamp):
                checker = self.checker()
                raw = json.dumps(metadata(timestamp)).encode("utf-8-sig")
                with (
                    patch.object(updates, "urlopen", return_value=io.BytesIO(raw)) as download,
                    patch.object(updates.time, "monotonic", return_value=100),
                ):
                    checker._check()
                    status = checker.status()
                self.assertEqual(status["status"], expected)
                self.assertEqual(status["version"], "0.1.0")
                self.assertEqual(checker._next_check, 100 + updates.CHECK_INTERVAL)
                self.assertEqual(download.call_count, 1)
                request = download.call_args.args[0]
                self.assertEqual(
                    request.full_url,
                    f"{updates.RELEASES_URL}/download/nightly/{updates.MANIFEST_NAME}",
                )
                self.assertEqual(download.call_args.kwargs, {"timeout": 5})
                self.assertIsNone(request.data)
                self.assertNotIn("Authorization", request.headers)
                self.assertEqual(status["release_url"], f"{updates.RELEASES_URL}/tag/nightly")
                if timestamp.endswith("-07:00") and expected == "available":
                    self.assertIn("13:00 UTC", status["message"])

    def test_stable_build_follows_latest_stable_release(self) -> None:
        checker = self.checker("stable")
        with patch.object(
            updates, "urlopen",
            return_value=io.BytesIO(json.dumps(metadata("2026-10-16T12:00:00Z", "stable")).encode()),
        ) as download:
            checker._check()
        self.assertEqual(checker.status()["status"], "available")
        self.assertEqual(checker.status()["release_url"], f"{updates.RELEASES_URL}/latest")
        self.assertEqual(
            download.call_args.args[0].full_url,
            f"{updates.RELEASES_URL}/latest/download/{updates.MANIFEST_NAME}",
        )

    def test_invalid_metadata_and_network_failures_are_not_reported_as_current(self) -> None:
        invalid = [
            b"not json", b"\xff", b"[]", b"{}",
            b"x" * (updates.MAX_MANIFEST_BYTES + 1),
            json.dumps(metadata(channel="stable")).encode(),
            json.dumps(metadata(built_at="not a date")).encode(),
            json.dumps(metadata(built_at="2026-10-15T12:00:00")).encode(),
            json.dumps({**metadata(), "schema": 2}).encode(),
            json.dumps({**metadata(), "version": []}).encode(),
            json.dumps({**metadata(), "channel": []}).encode(),
        ]
        failures = [
            TimeoutError(), URLError("offline"),
            HTTPError("https://github.com", 404, "Not found", {}, None),
            HTTPError("https://github.com", 429, "Rate limited", {}, None),
            IncompleteRead(b""),
        ]
        for value in invalid + failures:
            with self.subTest(value=repr(value)[:100]):
                checker = self.checker()
                kwargs = (
                    {"side_effect": value} if isinstance(value, Exception)
                    else {"return_value": io.BytesIO(value)}
                )
                with (
                    patch.object(updates, "urlopen", **kwargs),
                    patch.object(updates.time, "monotonic", return_value=100),
                ):
                    checker._check()
                    self.assertEqual(checker.status()["status"], "error")
                    self.assertIn("retrying later", checker.status()["message"])
                self.assertEqual(checker._next_check, 100 + updates.RETRY_INTERVAL)

    def test_check_is_single_flight_and_cached_until_expiry(self) -> None:
        checker = self.checker()
        with (
            patch.object(updates.threading, "Thread") as thread,
            patch.object(updates.time, "monotonic", return_value=100),
        ):
            self.assertEqual(checker.status()["status"], "pending")
            for _ in range(10):
                checker.status()
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            with patch.object(
                updates, "urlopen",
                return_value=io.BytesIO(json.dumps(metadata()).encode()),
            ):
                checker._check()
            checker.status()
            thread.assert_called_once()
        with (
            patch.object(updates.threading, "Thread") as thread,
            patch.object(updates.time, "monotonic", return_value=100 + updates.CHECK_INTERVAL),
        ):
            checker.status()
            thread.return_value.start.assert_called_once()

    def test_force_bypasses_cached_results_but_reuses_inflight_check(self) -> None:
        for cached_status in ("current", "available", "error"):
            with self.subTest(cached_status=cached_status):
                checker = self.checker()
                checker._status.update(status=cached_status, message="Cached result")
                checker._next_check = 100 + (
                    updates.RETRY_INTERVAL if cached_status == "error"
                    else updates.CHECK_INTERVAL
                )
                with (
                    patch.object(updates.threading, "Thread") as thread,
                    patch.object(updates.time, "monotonic", return_value=100),
                ):
                    self.assertEqual(checker.status()["status"], cached_status)
                    thread.assert_not_called()
                    status = checker.status(force=True)
                    self.assertEqual(status["status"], "pending")
                    self.assertEqual(status["message"], "Checking for updates...")
                    self.assertEqual(status["channel"], "nightly")
                    for _ in range(10):
                        self.assertEqual(checker.status(force=True), status)
                        self.assertEqual(checker.status(), status)
                    thread.return_value.start.assert_called_once()
                    with patch.object(
                        updates, "urlopen",
                        return_value=io.BytesIO(json.dumps(metadata()).encode()),
                    ):
                        checker._check()
                    self.assertEqual(checker.status()["status"], "current")
                    self.assertEqual(checker._next_check, 100 + updates.CHECK_INTERVAL)
                    checker.status(force=True)
                    self.assertEqual(thread.return_value.start.call_count, 2)

    def test_update_endpoint_does_not_wait_for_github_or_block_reports(self) -> None:
        checker = self.checker()
        checker._status.update(status="current", message="You're up to date.")
        checker._next_check = updates.time.monotonic() + updates.CHECK_INTERVAL
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def check() -> None:
            entered.set()
            try:
                release.wait(5)
            finally:
                finished.set()

        database = Mock()
        database.report.return_value = {"label": "Today"}
        with (
            patch.object(checker, "_check", side_effect=check) as background_check,
            patch("app_activity.dashboard.UpdateChecker", return_value=checker),
        ):
            server = create_server(database, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/update", timeout=2) as response:
                    self.assertEqual(json.load(response)["status"], "current")
                background_check.assert_not_called()
                with urlopen(Request(f"{base}/api/update", method="POST"), timeout=2) as response:
                    self.assertEqual(json.load(response)["status"], "pending")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertTrue(entered.wait(2))
                for method in ("POST", "GET"):
                    with urlopen(Request(f"{base}/api/update", method=method), timeout=2) as response:
                        self.assertEqual(json.load(response)["status"], "pending")
                background_check.assert_called_once()
                with urlopen(f"{base}/api/report?period=day", timeout=2) as response:
                    self.assertEqual(json.load(response), {"label": "Today"})
                with urlopen(f"{base}/api/status", timeout=2) as response:
                    self.assertEqual(json.load(response)["status"], "running")
            finally:
                release.set()
                server.shutdown()
                thread.join(5)
                server.server_close()
                self.assertTrue(finished.wait(5))


if __name__ == "__main__":
    unittest.main()
