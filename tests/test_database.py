from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app_activity.database import ActivityDatabase, ActivityState


class ActivityDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = ActivityDatabase(Path(self.temp_dir.name) / "test.db")

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_record_sample_extends_matching_segment(self) -> None:
        state = ActivityState("Code", False)
        segment_id = self.database.record_sample(None, None, state, 100)
        same_id = self.database.record_sample(segment_id, state, state, 130)

        self.assertEqual(segment_id, same_id)
        row = self.database._connection().execute(
            "SELECT start_ts, end_ts FROM activity_segments"
        ).fetchone()
        self.assertEqual((100.0, 130.0), row)

    def test_day_report_separates_active_and_idle_time(self) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        active = ActivityState("Code", False)
        idle = ActivityState("Idle", True)
        active_id = self.database.record_sample(None, None, active, now.timestamp())
        self.database.record_sample(
            active_id, active, active, now.timestamp() + 1800
        )
        idle_id = self.database.record_sample(None, None, idle, now.timestamp() + 1800)
        self.database.record_sample(idle_id, idle, idle, now.timestamp() + 2400)

        report = self.database.report("day", now)

        self.assertEqual(1800, report["active_seconds"])
        self.assertEqual(600, report["idle_seconds"])
        self.assertEqual("Code", report["apps"][0]["name"])
        self.assertEqual(100.0, report["apps"][0]["percent"])

    def test_rejects_unknown_period(self) -> None:
        with self.assertRaises(ValueError):
            self.database.report("decade")

    def test_resolved_name_migrates_historical_process_name(self) -> None:
        legacy = ActivityState("msedge", False)
        legacy_id = self.database.record_sample(None, None, legacy, 100)
        self.database.record_sample(legacy_id, legacy, legacy, 130)
        resolved = ActivityState("Microsoft Edge", False, "msedge")

        self.database.record_sample(None, None, resolved, 140)

        names = self.database._connection().execute(
            "SELECT DISTINCT app_name FROM activity_segments"
        ).fetchall()
        self.assertEqual([("Microsoft Edge",)], names)


if __name__ == "__main__":
    unittest.main()
