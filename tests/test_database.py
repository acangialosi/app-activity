from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app_activity.database import ActivityDatabase, ActivityState


PST = timezone(timedelta(hours=-8), "PST")
PDT = timezone(timedelta(hours=-7), "PDT")


def pacific_time(moment: datetime) -> datetime:
    """Mimic datetime.astimezone() on a US Pacific machine in 2026."""
    if moment.tzinfo is None:
        # Skipped and repeated times use the offset before the change.
        daylight = datetime(2026, 3, 8, 3) <= moment < datetime(2026, 11, 1, 2)
        moment = moment.replace(tzinfo=PDT if daylight else PST)
    daylight = (
        datetime(2026, 3, 8, 10, tzinfo=timezone.utc)
        <= moment
        < datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    )
    return moment.astimezone(PDT if daylight else PST)


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
        # No time zone changes its clocks on this date, so it has 24 hours.
        now = datetime(2026, 1, 14, 12).astimezone()
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

    def test_reports_periods_before_1970(self) -> None:
        # Windows cannot convert local times before 1970, so these periods
        # fall back to the reference UTC offset.
        now = datetime(2026, 1, 14, 12).astimezone()
        days = (datetime(1969, 1, 14) - datetime(2026, 1, 14)).days
        for period, offset in (
            ("hour", days * 24),
            ("day", days),
            ("year", -57),
        ):
            with self.subTest(period=period):
                report = self.database.report(period, now, offset=offset)

                self.assertTrue(report["start"].startswith("1969-01-"))
                self.assertIn("1969", report["label"])

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
        now = datetime.now().replace(
            hour=12, minute=30, second=0, microsecond=0
        ).astimezone()
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
        now = datetime.now().replace(
            hour=12, minute=30, second=0, microsecond=0
        ).astimezone()
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


class ReadOnlyDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "activity.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def record(
        self, database: ActivityDatabase, app_name: str, start: float
    ) -> None:
        state = ActivityState(app_name, False)
        segment_id = database.record_sample(None, None, state, start)
        database.record_sample(segment_id, state, state, start + 60)

    def recorded_apps(self, database: ActivityDatabase) -> list[str]:
        return [row[2] for row in database.export_rows()]

    def test_reads_uncheckpointed_wal_while_the_writer_is_open(self) -> None:
        writer = ActivityDatabase(self.path)
        reader = ActivityDatabase(self.path, read_only=True)
        try:
            writer._connection().execute("PRAGMA wal_autocheckpoint=0")
            self.record(writer, "Code", 100)
            wal = self.path.with_name(f"{self.path.name}-wal")
            self.assertGreater(wal.stat().st_size, 0)
            database_bytes = self.path.read_bytes()

            self.assertEqual(["Code"], self.recorded_apps(reader))
            self.record(writer, "Edge", 200)
            self.assertEqual(["Code", "Edge"], self.recorded_apps(reader))
            self.assertEqual(database_bytes, self.path.read_bytes())
        finally:
            reader.close()
            writer.close()

    def test_reads_a_cleanly_closed_database_without_changing_it(self) -> None:
        writer = ActivityDatabase(self.path)
        self.record(writer, "Code", 100)
        writer.close()
        self.assertFalse(self.path.with_name(f"{self.path.name}-wal").exists())
        database_bytes = self.path.read_bytes()

        reader = ActivityDatabase(self.path, read_only=True)
        try:
            self.assertEqual(["Code"], self.recorded_apps(reader))
        finally:
            reader.close()

        self.assertEqual(database_bytes, self.path.read_bytes())

    def test_rejects_writes(self) -> None:
        writer = ActivityDatabase(self.path)
        self.record(writer, "Code", 100)
        writer.close()

        reader = ActivityDatabase(self.path, read_only=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.record(reader, "Edge", 200)
            self.assertEqual(["Code"], self.recorded_apps(reader))
        finally:
            reader.close()

    def test_never_creates_a_missing_database_or_directory(self) -> None:
        root = Path(self.temp_dir.name)
        for path in (self.path, root / "AppActivity" / "activity.db"):
            with self.subTest(path=path):
                reader = ActivityDatabase(path, read_only=True)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        reader.export_rows()
                finally:
                    reader.close()

        self.assertEqual([], list(root.iterdir()))

    def test_opens_paths_with_uri_special_characters(self) -> None:
        path = Path(self.temp_dir.name) / "App Activity #1 %20 ü" / "a.db"
        writer = ActivityDatabase(path)
        self.record(writer, "Code", 100)
        writer.close()

        reader = ActivityDatabase(path, read_only=True)
        try:
            self.assertEqual(["Code"], self.recorded_apps(reader))
        finally:
            reader.close()


class DaylightSavingTimeReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = ActivityDatabase(Path(self.temp_dir.name) / "test.db")
        local_time = mock.patch(
            "app_activity.database._local_time", pacific_time
        )
        local_time.start()
        self.addCleanup(local_time.stop)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def record(self, start: datetime, end: datetime) -> None:
        state = ActivityState("Code", False)
        segment_id = self.database.record_sample(
            None, None, state, start.timestamp()
        )
        self.database.record_sample(
            segment_id, state, state, end.timestamp()
        )

    def test_day_has_25_hours_when_clocks_fall_back(self) -> None:
        self.record(
            datetime(2026, 11, 1, 0, 15, tzinfo=PDT),
            datetime(2026, 11, 1, 0, 45, tzinfo=PDT),
        )
        self.record(
            datetime(2026, 11, 1, 1, 15, tzinfo=PST),
            datetime(2026, 11, 1, 1, 45, tzinfo=PST),
        )

        report = self.database.report(
            "day", datetime(2026, 11, 1, 12, tzinfo=PST)
        )

        self.assertEqual(
            ("2026-11-01T00:00:00-07:00", "2026-11-02T00:00:00-08:00"),
            (report["start"], report["end"]),
        )
        self.assertEqual(3600, report["active_seconds"])
        timeline = report["timeline"]
        self.assertEqual(
            [3600, 7200, 3600],
            [bucket["capacity_seconds"] for bucket in timeline[:3]],
        )
        self.assertEqual(
            25 * 3600,
            sum(bucket["capacity_seconds"] for bucket in timeline),
        )
        self.assertEqual(
            [1800, 1800], [bucket["seconds"] for bucket in timeline[:2]]
        )

    def test_day_has_23_hours_when_clocks_spring_forward(self) -> None:
        self.record(
            datetime(2026, 3, 7, 23, 15, tzinfo=PST),
            datetime(2026, 3, 7, 23, 45, tzinfo=PST),
        )
        now = datetime(2026, 3, 8, 12, tzinfo=PDT)

        report = self.database.report("day", now)
        previous = self.database.report("day", now, offset=-1)

        self.assertEqual(
            ("2026-03-08T00:00:00-08:00", "2026-03-09T00:00:00-07:00"),
            (report["start"], report["end"]),
        )
        self.assertEqual(
            (0, 1800),
            (report["active_seconds"], previous["active_seconds"]),
        )
        skipped = report["timeline"][2]
        self.assertEqual(
            ("02", "2026-03-08T02:00:00-08:00", 0),
            (skipped["label"], skipped["start"], skipped["capacity_seconds"]),
        )
        self.assertEqual(
            23 * 3600,
            sum(bucket["capacity_seconds"] for bucket in report["timeline"]),
        )

    def test_calendar_periods_use_each_boundary_offset(self) -> None:
        for now in (
            datetime(2026, 11, 1, 0, 30, tzinfo=PDT),
            datetime(2026, 11, 1, 12, tzinfo=PST),
        ):
            with self.subTest(now=now.isoformat()):
                week = self.database.report("week", now)
                month = self.database.report("month", now)

                self.assertEqual(
                    (
                        "2026-10-26T00:00:00-07:00",
                        "2026-11-02T00:00:00-08:00",
                        "Oct 26 - Nov 01, 2026",
                    ),
                    (week["start"], week["end"], week["label"]),
                )
                self.assertEqual(
                    ("2026-11-01T00:00:00-07:00", "2026-12-01T00:00:00-08:00"),
                    (month["start"], month["end"]),
                )

        year = self.database.report(
            "year", datetime(2026, 6, 1, 12, tzinfo=PDT)
        )

        self.assertEqual(
            ["-08:00"] * 3 + ["-07:00"] * 8 + ["-08:00"],
            [bucket["start"][-6:] for bucket in year["timeline"]],
        )
        self.assertEqual(
            365 * 86400,
            sum(bucket["capacity_seconds"] for bucket in year["timeline"]),
        )

    def test_hour_navigation_keeps_repeated_hours_distinct(self) -> None:
        self.record(
            datetime(2026, 11, 1, 1, 10, tzinfo=PDT),
            datetime(2026, 11, 1, 1, 20, tzinfo=PDT),
        )
        now = datetime(2026, 11, 1, 2, 30, tzinfo=PST)

        reports = [
            self.database.report("hour", now, offset=offset)
            for offset in (-1, -2, -3)
        ]

        self.assertEqual(
            [
                "2026-11-01T01:00:00-08:00",
                "2026-11-01T01:00:00-07:00",
                "2026-11-01T00:00:00-07:00",
            ],
            [report["start"] for report in reports],
        )
        self.assertEqual(
            [0, 600, 0], [report["active_seconds"] for report in reports]
        )
        self.assertIn("1:00 AM - 2:00 AM", reports[1]["label"])
        self.assertEqual("01:00", reports[1]["timeline"][0]["label"])


if __name__ == "__main__":
    unittest.main()
