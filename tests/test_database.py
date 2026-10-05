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
        active = ActivityState("Code", False, window_title="project.py - Code")
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
        self.assertEqual(
            "project.py - Code", report["apps"][0]["windows"][0]["title"]
        )
        self.assertEqual(1800, report["apps"][0]["windows"][0]["seconds"])
        self.assertEqual(
            [3600] * 24,
            [bucket["capacity_seconds"] for bucket in report["timeline"]],
        )
        timeline_app_seconds = sum(
            app["seconds"]
            for bucket in report["timeline"]
            for app in bucket["apps"]
            if app["name"] == "Code"
        )
        self.assertEqual(1800, timeline_app_seconds)

    def test_rejects_unknown_period(self) -> None:
        with self.assertRaises(ValueError):
            self.database.report("decade")

    def test_resolved_name_migrates_historical_process_name(self) -> None:
        legacy = ActivityState("msedge", False)
        legacy_id = self.database.record_sample(None, None, legacy, 100)
        self.database.record_sample(legacy_id, legacy, legacy, 130)
        resolved = ActivityState(
            "Microsoft Edge",
            False,
            "msedge",
            "App Activity - Microsoft Edge",
        )

        self.database.record_sample(None, None, resolved, 140)

        names = self.database._connection().execute(
            "SELECT DISTINCT app_name FROM activity_segments"
        ).fetchall()
        self.assertEqual([("Microsoft Edge",)], names)

    def test_migrates_existing_database_to_window_titles(self) -> None:
        self.database.close()
        connection = __import__("sqlite3").connect(self.database.path)
        connection.execute("DROP TABLE activity_segments")
        connection.execute(
            """
            CREATE TABLE activity_segments (
                id INTEGER PRIMARY KEY,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                app_name TEXT NOT NULL,
                is_idle INTEGER NOT NULL
            )
            """
        )
        connection.commit()
        connection.close()

        self.database = ActivityDatabase(self.database.path)

        columns = {
            row[1]
            for row in self.database._connection().execute(
                "PRAGMA table_info(activity_segments)"
            )
        }
        self.assertIn("window_title", columns)
        self.assertIn("detail_key", columns)

    def test_migrates_existing_lock_screen_segments_to_idle(self) -> None:
        connection = self.database._connection()
        connection.execute(
            """
            INSERT INTO activity_segments(
                start_ts, end_ts, app_name, is_idle, window_title, detail_key
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                100,
                130,
                "Microsoft Windows Operating System",
                0,
                "Windows Default Lock Screen",
                "window:100",
            ),
        )
        connection.commit()
        self.database.close()

        self.database = ActivityDatabase(self.database.path)

        row = self.database._connection().execute(
            """
            SELECT app_name, is_idle, window_title, detail_key
            FROM activity_segments
            """
        ).fetchone()
        self.assertEqual(("Idle", 1, "", ""), row)

    def test_migrates_generic_windows_segments_from_window_titles(self) -> None:
        connection = self.database._connection()
        connection.executemany(
            """
            INSERT INTO activity_segments(
                start_ts, end_ts, app_name, is_idle, window_title, detail_key
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    100,
                    130,
                    "Microsoft® Windows® Operating System",
                    0,
                    "Calculator",
                    "window:100",
                ),
                (
                    130,
                    160,
                    "Microsoft® Windows® Operating System",
                    0,
                    "Task Manager",
                    "window:200",
                ),
                (
                    160,
                    190,
                    "Microsoft® Windows® Operating System",
                    0,
                    "Control Panel\\Programs\\Programs and Features",
                    "window:300",
                ),
                (
                    190,
                    220,
                    "Microsoft® Windows® Operating System",
                    0,
                    "",
                    "window:400",
                ),
            ],
        )
        connection.commit()
        self.database.close()

        self.database = ActivityDatabase(self.database.path)

        row = self.database._connection().execute(
            """
            SELECT app_name, is_idle, window_title, detail_key
            FROM activity_segments
            ORDER BY start_ts
            """
        ).fetchall()
        self.assertEqual(
            [
                ("Calculator", 0, "Calculator", "window:100"),
                ("Task Manager", 0, "Task Manager", "window:200"),
                (
                    "Control Panel",
                    0,
                    "Control Panel\\Programs\\Programs and Features",
                    "window:300",
                ),
                (
                    "Microsoft® Windows® Operating System",
                    0,
                    "",
                    "window:400",
                ),
            ],
            row,
        )

    def test_migrates_existing_system_surfaces_to_idle(
        self,
    ) -> None:
        connection = self.database._connection()
        connection.executemany(
            """
            INSERT INTO activity_segments(
                start_ts, end_ts, app_name, is_idle, window_title, detail_key
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    100,
                    130,
                    "Windows Explorer",
                    0,
                    "Program Manager",
                    "window:100",
                ),
                (
                    130,
                    160,
                    "Windows Logon User Interface Host",
                    0,
                    "",
                    "window:200",
                ),
                (
                    160,
                    190,
                    "Credential UI Broker",
                    0,
                    "",
                    "window:300",
                ),
                (
                    190,
                    220,
                    "Microsoft Windows Operating System",
                    0,
                    "Task Switching",
                    "window:400",
                ),
            ],
        )
        connection.commit()
        self.database.close()

        self.database = ActivityDatabase(self.database.path)

        rows = self.database._connection().execute(
            """
            SELECT app_name, is_idle, window_title, detail_key
            FROM activity_segments
            ORDER BY start_ts
            """
        ).fetchall()
        self.assertEqual([("Idle", 1, "", "")] * 4, rows)

    def test_report_groups_multiple_titles_under_one_app(self) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        first = ActivityState(
            "Microsoft Edge", False, "msedge", "First page - Microsoft Edge"
        )
        second = ActivityState(
            "Microsoft Edge", False, "msedge", "Second page - Microsoft Edge"
        )
        first_id = self.database.record_sample(
            None, None, first, now.timestamp()
        )
        self.database.record_sample(
            first_id, first, first, now.timestamp() + 600
        )
        second_id = self.database.record_sample(
            None, None, second, now.timestamp() + 600
        )
        self.database.record_sample(
            second_id, second, second, now.timestamp() + 900
        )

        app = self.database.report("day", now)["apps"][0]

        self.assertEqual("Microsoft Edge", app["name"])
        self.assertEqual(900, app["seconds"])
        self.assertEqual(
            ["First page - Microsoft Edge", "Second page - Microsoft Edge"],
            [window["title"] for window in app["windows"]],
        )

    def test_report_can_move_to_previous_day(self) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        yesterday = now.timestamp() - 86400
        state = ActivityState("Notepad", False)
        segment_id = self.database.record_sample(
            None, None, state, yesterday
        )
        self.database.record_sample(
            segment_id, state, state, yesterday + 900
        )

        current_report = self.database.report("day", now)
        previous_report = self.database.report("day", now, offset=-1)

        self.assertEqual(0, current_report["active_seconds"])
        self.assertEqual(900, previous_report["active_seconds"])
        self.assertEqual(-1, previous_report["offset"])

    def test_hour_report_uses_five_minute_buckets(self) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=30, second=0, microsecond=0
        )
        state = ActivityState("Code", False)
        segment_id = self.database.record_sample(
            None, None, state, now.replace(minute=5).timestamp()
        )
        self.database.record_sample(
            segment_id,
            state,
            state,
            now.replace(minute=15).timestamp(),
        )

        report = self.database.report("hour", now)

        self.assertEqual(600, report["active_seconds"])
        self.assertEqual(12, len(report["timeline"]))
        self.assertEqual(
            [300] * 12,
            [bucket["capacity_seconds"] for bucket in report["timeline"]],
        )
        self.assertEqual(
            ["12:00", "12:05", "12:10"],
            [bucket["label"] for bucket in report["timeline"][:3]],
        )
        self.assertEqual(
            600,
            sum(bucket["seconds"] for bucket in report["timeline"]),
        )

    def test_hour_report_can_move_to_previous_hour(self) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=30, second=0, microsecond=0
        )
        previous_hour = now.replace(
            hour=11, minute=10
        ).timestamp()
        state = ActivityState("Code", False)
        segment_id = self.database.record_sample(
            None, None, state, previous_hour
        )
        self.database.record_sample(
            segment_id, state, state, previous_hour + 300
        )

        current_report = self.database.report("hour", now)
        previous_report = self.database.report("hour", now, offset=-1)

        self.assertEqual(0, current_report["active_seconds"])
        self.assertEqual(300, previous_report["active_seconds"])
        self.assertEqual(-1, previous_report["offset"])
        self.assertIn("11:00 AM - 12:00 PM", previous_report["label"])

    def test_terminal_title_changes_remain_one_window(self) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        first = ActivityState(
            "Windows Terminal",
            False,
            "WindowsTerminal",
            "Copilot - Planning",
            "window:100",
        )
        second = ActivityState(
            "Windows Terminal",
            False,
            "WindowsTerminal",
            "Copilot - Implementing",
            "window:100",
        )
        first_id = self.database.record_sample(
            None, None, first, now.timestamp()
        )
        self.database.record_sample(
            first_id, first, first, now.timestamp() + 300
        )
        second_id = self.database.record_sample(
            None, None, second, now.timestamp() + 300
        )
        self.database.record_sample(
            second_id, second, second, now.timestamp() + 600
        )

        windows = self.database.report("day", now)["apps"][0]["windows"]

        self.assertEqual(1, len(windows))
        self.assertEqual("Copilot - Implementing", windows[0]["title"])
        self.assertEqual(600, windows[0]["seconds"])

    def test_edge_title_changes_remain_distinct_tabs(self) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        first = ActivityState(
            "Microsoft Edge",
            False,
            "msedge",
            "First page - Microsoft Edge",
            "tab:200:First page - Microsoft Edge",
        )
        second = ActivityState(
            "Microsoft Edge",
            False,
            "msedge",
            "Second page - Microsoft Edge",
            "tab:200:Second page - Microsoft Edge",
        )
        first_id = self.database.record_sample(
            None, None, first, now.timestamp()
        )
        self.database.record_sample(
            first_id, first, first, now.timestamp() + 300
        )
        second_id = self.database.record_sample(
            None, None, second, now.timestamp() + 300
        )
        self.database.record_sample(
            second_id, second, second, now.timestamp() + 600
        )

        windows = self.database.report("day", now)["apps"][0]["windows"]

        self.assertEqual(2, len(windows))

    def test_edge_legacy_and_current_records_with_same_title_are_merged(
        self,
    ) -> None:
        now = datetime.now().astimezone().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        title = "App Activity - Microsoft Edge"
        legacy = ActivityState(
            "Microsoft Edge", False, "msedge", title
        )
        current = ActivityState(
            "Microsoft Edge",
            False,
            "msedge",
            title,
            f"tab:200:{title}",
        )
        legacy_id = self.database.record_sample(
            None, None, legacy, now.timestamp()
        )
        self.database.record_sample(
            legacy_id, legacy, legacy, now.timestamp() + 300
        )
        current_id = self.database.record_sample(
            None, None, current, now.timestamp() + 300
        )
        self.database.record_sample(
            current_id, current, current, now.timestamp() + 600
        )

        windows = self.database.report("day", now)["apps"][0]["windows"]

        self.assertEqual(1, len(windows))
        self.assertEqual(title, windows[0]["title"])
        self.assertEqual(600, windows[0]["seconds"])


if __name__ == "__main__":
    unittest.main()
