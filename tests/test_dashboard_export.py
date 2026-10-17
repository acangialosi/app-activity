from __future__ import annotations

import csv
import io
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import urlopen

from app_activity.dashboard import create_server
from app_activity.database import ActivityDatabase


class DashboardExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.database = ActivityDatabase(Path(self.temp_dir.name) / "activity.db")
        start = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.day = start.date().isoformat()
        self.database._connection().executemany(
            """
            INSERT INTO activity_segments(
                start_ts, end_ts, app_name, is_idle, window_title
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    (start + timedelta(hours=23, minutes=30)).timestamp(),
                    (start + timedelta(days=1, minutes=30)).timestamp(),
                    "=1+1",
                    0,
                    "=2+2",
                ),
                (
                    (start + timedelta(days=1, minutes=30)).timestamp(),
                    (start + timedelta(days=1, minutes=40)).timestamp(),
                    "Browser",
                    1,
                    "Idle title",
                ),
            ],
        )
        self.database._connection().commit()
        self.server = create_server(self.database, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.database.close()
        self.temp_dir.cleanup()

    def test_date_range_clips_segments_and_exports_csv_safely(self) -> None:
        with urlopen(
            f"{self.base_url}/api/export?start={self.day}&end={self.day}",
            timeout=2,
        ) as response:
            self.assertEqual(response.headers["Content-Type"], "text/csv; charset=utf-8")
            self.assertIn("attachment;", response.headers["Content-Disposition"])
            rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))

        self.assertEqual(1, len(rows))
        self.assertEqual("'=1+1", rows[0]["application"])
        self.assertEqual("'=2+2", rows[0]["window_title"])
        self.assertEqual(1800, float(rows[0]["duration_seconds"]))
        self.assertEqual("False", rows[0]["idle"])

    def test_all_exports_full_segments_and_invalid_ranges_are_rejected(self) -> None:
        with urlopen(f"{self.base_url}/api/export?all=1", timeout=2) as response:
            rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
        self.assertEqual(2, len(rows))
        self.assertEqual(600, float(rows[1]["duration_seconds"]))

        for query in ("start=bad&end=2026-01-01", "start=2026-01-02&end=2026-01-01"):
            with self.subTest(query=query), self.assertRaises(HTTPError) as error:
                urlopen(f"{self.base_url}/api/export?{query}", timeout=2)
            self.assertEqual(400, error.exception.code)


if __name__ == "__main__":
    unittest.main()
