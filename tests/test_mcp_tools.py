from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from app_activity.database import ActivityDatabase
from app_activity.mcp_errors import ToolError, UnknownToolError
from app_activity.mcp_tools import TOOLS, call_tool


NOW = datetime(2026, 1, 14, 15, 30).astimezone()
DAY = {"start": "2026-01-14", "end": "2026-01-14"}
SEGMENTS = [
    ("2026-01-14", "09:00", "09:30", "Microsoft Edge", "Inbox - Outlook"),
    ("2026-01-14", "09:30", "09:45", "Microsoft Edge", "News - Example"),
    ("2026-01-14", "09:45", "10:45", "Code", "notes.py - Code"),
    ("2026-01-14", "10:45", "11:00", "Idle", ""),
    ("2026-01-13", "14:00", "16:00", "Code", "main.py - Code"),
    ("2026-01-05", "10:00", "10:20", "Slack", "general"),
    ("2026-02-03", "08:00", "09:00", "Excel", "budget.xlsx"),
    ("2025-12-31", "12:00", "12:10", "Word", "letter.docx"),
]
SAMPLE_VALUES: dict[str, Any] = {
    "period": "week",
    "date": "2026-01-14T09:10",
    "include_timeline": True,
    "include_window_titles": True,
    "max_applications": 5,
    "max_windows_per_application": 2,
    "start": "2026-01-14",
    "end": "2026-01-14",
    "application": "Code",
    "include_idle": False,
    "limit": 10,
    "offset": 1,
}


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone()


def iso(text: str) -> str:
    return local(text).isoformat()


def app_seconds(result: dict[str, Any]) -> dict[str, int]:
    return {
        app["name"]: app["active_seconds"] for app in result["applications"]
    }


def names(result: dict[str, Any]) -> list[str]:
    return [segment["application"] for segment in result["segments"]]


class McpToolsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "AppActivity" / "activity.db"
        # The writer stays open, like the tracking service, while tools read.
        self.database = ActivityDatabase(self.database_path)
        for day, start, end, app_name, title in SEGMENTS:
            self.add(f"{day}T{start}", f"{day}T{end}", app_name, title)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def add(
        self, start: str, end: str, app_name: str, title: str = ""
    ) -> None:
        connection = self.database._connection()
        connection.execute(
            """
            INSERT INTO activity_segments(
                start_ts, end_ts, app_name, is_idle, window_title
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                local(start).timestamp(),
                local(end).timestamp(),
                app_name,
                int(app_name == "Idle"),
                title,
            ),
        )
        connection.commit()

    def call(
        self, name: Any, arguments: Any = None, **options: Any
    ) -> dict[str, Any]:
        options = {"database_path": self.database_path, "now": NOW, **options}
        with mock.patch("app_activity.cli.is_running", return_value=False):
            result = call_tool(name, arguments, **options)
        json.dumps(result)
        return result

    def assert_tool_error(
        self, name: str, arguments: Any, message: str, **options: Any
    ) -> None:
        with self.assertRaises(ToolError) as caught:
            self.call(name, arguments, **options)
        self.assertIn(message, str(caught.exception))


class SummaryToolTests(McpToolsTestCase):
    def test_summarizes_the_day_containing_now_by_default(self) -> None:
        result = self.call("get_activity_summary")

        self.assertEqual("day", result["period"])
        self.assertEqual("Wednesday, January 14, 2026", result["label"])
        self.assertEqual(iso("2026-01-14T00:00"), result["start"])
        self.assertEqual(iso("2026-01-15T00:00"), result["end"])
        self.assertEqual(
            (6300, 900, 7200, "1h 45m", "15m 00s", "2h 00m"),
            (
                result["active_seconds"],
                result["idle_seconds"],
                result["tracked_seconds"],
                result["active_duration"],
                result["idle_duration"],
                result["tracked_duration"],
            ),
        )
        self.assertEqual(2, result["application_count"])
        self.assertFalse(result["applications_truncated"])
        self.assertEqual(
            [
                {
                    "name": "Code",
                    "active_seconds": 3600,
                    "active_duration": "1h 00m",
                    "percent_of_active_time": 57.1,
                },
                {
                    "name": "Microsoft Edge",
                    "active_seconds": 2700,
                    "active_duration": "45m 00s",
                    "percent_of_active_time": 42.9,
                },
            ],
            result["applications"],
        )
        self.assertNotIn("timeline", result)

    def test_date_selects_the_period_that_contains_it(self) -> None:
        week = {"Code": 10800, "Microsoft Edge": 2700}
        slack = {"Slack": 1200}
        january = {**week, **slack}
        february = {"Excel": 3600}
        cases = [
            ("day", "2026-01-13", "2026-01-13", "2026-01-14", {"Code": 7200}),
            ("week", "2026-01-13", "2026-01-12", "2026-01-19", week),
            # Weeks start on Monday, so a Sunday ends the previous week.
            ("week", "2026-01-11", "2026-01-05", "2026-01-12", slack),
            ("month", "2026-01-31", "2026-01-01", "2026-02-01", january),
            ("month", "2026-02-03", "2026-02-01", "2026-03-01", february),
            (
                "year",
                "2026-02-10",
                "2026-01-01",
                "2027-01-01",
                {**january, **february},
            ),
            ("year", "2025-12-31", "2025-01-01", "2026-01-01", {"Word": 600}),
        ]
        for period, date, start, end, expected in cases:
            with self.subTest(period=period, date=date):
                result = self.call(
                    "get_activity_summary", {"period": period, "date": date}
                )

                self.assertEqual(period, result["period"])
                self.assertEqual(iso(f"{start}T00:00"), result["start"])
                self.assertEqual(iso(f"{end}T00:00"), result["end"])
                self.assertEqual(expected, app_seconds(result))
                self.assertEqual(
                    sum(expected.values()), result["active_seconds"]
                )

    def test_hour_needs_a_date_time(self) -> None:
        result = self.call(
            "get_activity_summary",
            {"period": "hour", "date": "2026-01-14T09:10"},
        )

        self.assertEqual(iso("2026-01-14T09:00"), result["start"])
        self.assertEqual(iso("2026-01-14T10:00"), result["end"])
        self.assertEqual(
            {"Microsoft Edge": 2700, "Code": 900}, app_seconds(result)
        )
        self.assert_tool_error(
            "get_activity_summary",
            {"period": "hour", "date": "2026-01-14"},
            "period 'hour' needs a date-time",
        )

    def test_aware_dates_are_converted_to_local_time(self) -> None:
        moment = local("2026-01-14T09:10")
        india = timezone(timedelta(hours=5, minutes=30))
        for value in (
            moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            moment.astimezone(india).isoformat(),
        ):
            with self.subTest(value=value):
                result = self.call(
                    "get_activity_summary", {"period": "hour", "date": value}
                )

                self.assertEqual(iso("2026-01-14T09:00"), result["start"])
                self.assertEqual(3600, result["active_seconds"])

    def test_window_titles_are_opt_in(self) -> None:
        result = self.call(
            "get_activity_summary", {"include_window_titles": True}
        )

        code, edge = result["applications"]
        self.assertEqual(
            [
                {
                    "title": "Inbox - Outlook",
                    "active_seconds": 1800,
                    "active_duration": "30m 00s",
                    "percent_of_application_time": 66.7,
                },
                {
                    "title": "News - Example",
                    "active_seconds": 900,
                    "active_duration": "15m 00s",
                    "percent_of_application_time": 33.3,
                },
            ],
            edge["windows"],
        )
        self.assertFalse(edge["windows_truncated"])
        self.assertEqual(
            ["notes.py - Code"],
            [window["title"] for window in code["windows"]],
        )

    def test_limits_applications_and_windows(self) -> None:
        windows = self.call(
            "get_activity_summary",
            {"include_window_titles": True, "max_windows_per_application": 1},
        )
        applications = self.call(
            "get_activity_summary", {"max_applications": 1}
        )

        code, edge = windows["applications"]
        self.assertEqual(
            ["Inbox - Outlook"], [w["title"] for w in edge["windows"]]
        )
        self.assertTrue(edge["windows_truncated"])
        self.assertFalse(code["windows_truncated"])
        self.assertEqual(2, applications["application_count"])
        self.assertTrue(applications["applications_truncated"])
        self.assertEqual(
            [
                {
                    "name": "Code",
                    "active_seconds": 3600,
                    "active_duration": "1h 00m",
                    "percent_of_active_time": 57.1,
                }
            ],
            applications["applications"],
        )

    def test_timeline_is_opt_in_with_bucket_bounds(self) -> None:
        result = self.call("get_activity_summary", {"include_timeline": True})
        limited = self.call(
            "get_activity_summary",
            {"include_timeline": True, "max_applications": 1},
        )

        self.assertEqual(24, len(result["timeline"]))
        self.assertEqual(
            {
                "label": "09",
                "start": iso("2026-01-14T09:00"),
                "end": iso("2026-01-14T10:00"),
                "active_seconds": 3600,
                "applications": [
                    {"name": "Microsoft Edge", "active_seconds": 2700},
                    {"name": "Code", "active_seconds": 900},
                ],
            },
            result["timeline"][9],
        )
        self.assertEqual(
            [{"name": "Code", "active_seconds": 2700}],
            result["timeline"][10]["applications"],
        )
        self.assertEqual(
            [{"name": "Microsoft Edge", "active_seconds": 2700}],
            limited["timeline"][9]["applications"],
        )

    def test_timeline_buckets_follow_the_period(self) -> None:
        cases = [
            ("hour", "2026-01-14T09:10", 12, timedelta(minutes=5)),
            ("day", "2026-01-14", 24, timedelta(hours=1)),
            ("week", "2026-01-14", 7, timedelta(days=1)),
            ("month", "2026-01-14", 31, timedelta(days=1)),
            ("year", "2026-01-14", 12, timedelta(days=31)),
        ]
        for period, date, count, first_bucket in cases:
            with self.subTest(period=period):
                result = self.call(
                    "get_activity_summary",
                    {"period": period, "date": date, "include_timeline": True},
                )

                timeline = result["timeline"]
                self.assertEqual(count, len(timeline))
                self.assertEqual(result["start"], timeline[0]["start"])
                self.assertEqual(result["end"], timeline[-1]["end"])
                self.assertEqual(
                    first_bucket,
                    datetime.fromisoformat(timeline[0]["end"])
                    - datetime.fromisoformat(timeline[0]["start"]),
                )


class SegmentsToolTests(McpToolsTestCase):
    def test_lists_segments_for_an_inclusive_date_range(self) -> None:
        result = self.call("get_activity_segments", DAY)

        self.assertEqual(iso("2026-01-14T00:00"), result["start"])
        self.assertEqual(iso("2026-01-15T00:00"), result["end"])
        self.assertEqual(
            (4, 0, 4, None),
            (
                result["total"],
                result["offset"],
                result["returned"],
                result["next_offset"],
            ),
        )
        self.assertEqual(
            {
                "start": iso("2026-01-14T09:00"),
                "end": iso("2026-01-14T09:30"),
                "duration_seconds": 1800,
                "application": "Microsoft Edge",
                "idle": False,
            },
            result["segments"][0],
        )
        self.assertEqual(
            ["Microsoft Edge", "Microsoft Edge", "Code", "Idle"],
            names(result),
        )
        self.assertEqual(
            [False, False, False, True],
            [segment["idle"] for segment in result["segments"]],
        )

    def test_date_only_end_includes_that_whole_day(self) -> None:
        result = self.call(
            "get_activity_segments",
            {"start": "2026-01-13", "end": "2026-01-13"},
        )

        self.assertEqual(iso("2026-01-14T00:00"), result["end"])
        self.assertEqual(["Code"], names(result))
        self.assertEqual(7200, result["segments"][0]["duration_seconds"])

    def test_clips_segments_to_a_date_time_range(self) -> None:
        result = self.call(
            "get_activity_segments",
            {"start": "2026-01-14T09:15", "end": "2026-01-14T10:00"},
        )

        self.assertEqual(
            [
                (iso("2026-01-14T09:15"), iso("2026-01-14T09:30"), 900),
                (iso("2026-01-14T09:30"), iso("2026-01-14T09:45"), 900),
                (iso("2026-01-14T09:45"), iso("2026-01-14T10:00"), 900),
            ],
            [
                (s["start"], s["end"], s["duration_seconds"])
                for s in result["segments"]
            ],
        )

    def test_excludes_segments_without_a_whole_second_in_range(self) -> None:
        self.add("2026-01-14T09:35", "2026-01-14T09:35", "Notepad")
        self.add("2026-01-14T09:29:50", "2026-01-14T09:30:00.400", "Paint")
        self.add("2026-01-14T09:44:59.400", "2026-01-14T09:50", "Teams")

        result = self.call(
            "get_activity_segments",
            {"start": "2026-01-14T09:30", "end": "2026-01-14T09:45"},
        )

        self.assertEqual(2, result["total"])
        self.assertEqual(["Microsoft Edge", "Teams"], names(result))
        self.assertEqual(
            [
                (iso("2026-01-14T09:30"), iso("2026-01-14T09:45"), 900),
                (iso("2026-01-14T09:44:59"), iso("2026-01-14T09:45"), 1),
            ],
            [
                (s["start"], s["end"], s["duration_seconds"])
                for s in result["segments"]
            ],
        )

    def test_can_exclude_idle_segments(self) -> None:
        result = self.call(
            "get_activity_segments", {**DAY, "include_idle": False}
        )

        self.assertEqual(3, result["total"])
        self.assertNotIn("Idle", names(result))

    def test_application_filter_is_a_case_insensitive_exact_match(
        self,
    ) -> None:
        self.add("2026-01-14T12:00", "2026-01-14T12:30", "Éditeur")
        cases = {
            "ÉDITEUR": ["Éditeur"],
            "microsoft EDGE": ["Microsoft Edge", "Microsoft Edge"],
            "idle": ["Idle"],
            "Microsoft": [],
        }
        for application, expected in cases.items():
            with self.subTest(application=application):
                result = self.call(
                    "get_activity_segments",
                    {**DAY, "application": application},
                )

                self.assertEqual(expected, names(result))
                self.assertEqual(len(expected), result["total"])

        no_idle = self.call(
            "get_activity_segments",
            {**DAY, "application": "Idle", "include_idle": False},
        )
        self.assertEqual(0, no_idle["total"])

    def test_paginates_with_limit_and_offset(self) -> None:
        pages = [
            self.call("get_activity_segments", {**DAY, "limit": 3}),
            self.call(
                "get_activity_segments", {**DAY, "limit": 3, "offset": 3}
            ),
            self.call(
                "get_activity_segments", {**DAY, "limit": 3, "offset": 10}
            ),
        ]

        self.assertEqual(
            [(4, 0, 3, 3), (4, 3, 1, None), (4, 10, 0, None)],
            [
                (
                    page["total"],
                    page["offset"],
                    page["returned"],
                    page["next_offset"],
                )
                for page in pages
            ],
        )
        self.assertEqual(["Idle"], names(pages[1]))

    def test_window_titles_are_opt_in(self) -> None:
        default = self.call("get_activity_segments", DAY)
        titled = self.call(
            "get_activity_segments", {**DAY, "include_window_titles": True}
        )

        self.assertFalse(
            any("window_title" in segment for segment in default["segments"])
        )
        self.assertEqual(
            ["Inbox - Outlook", "News - Example", "notes.py - Code", ""],
            [segment["window_title"] for segment in titled["segments"]],
        )

    def test_range_can_cover_at_most_31_days(self) -> None:
        for start, end in (
            ("2026-01-01", "2026-01-31"),
            ("2026-01-01T00:00", "2026-02-01T00:00"),
        ):
            with self.subTest(start=start, end=end):
                result = self.call(
                    "get_activity_segments", {"start": start, "end": end}
                )
                self.assertEqual(6, result["total"])

        for start, end in (
            ("2026-01-01", "2026-02-01"),
            ("2026-01-01T00:00", "2026-02-01T00:01"),
        ):
            with self.subTest(start=start, end=end):
                self.assert_tool_error(
                    "get_activity_segments",
                    {"start": start, "end": end},
                    "at most 31 days",
                )

    def test_end_must_be_after_start(self) -> None:
        for start, end in (
            ("2026-01-14T10:00", "2026-01-14T10:00"),
            ("2026-01-14T10:00", "2026-01-14T09:00"),
            ("2026-01-15", "2026-01-14"),
        ):
            with self.subTest(start=start, end=end):
                self.assert_tool_error(
                    "get_activity_segments",
                    {"start": start, "end": end},
                    "end must be after start",
                )


class StatusToolTests(McpToolsTestCase):
    def status(self, *, running: bool, **options: Any) -> dict[str, Any]:
        options = {"database_path": self.database_path, "now": NOW, **options}
        with mock.patch("app_activity.cli.is_running", return_value=running):
            result = call_tool("get_tracking_status", None, **options)
        json.dumps(result)
        return result

    def test_reports_recording_state_and_recorded_range(self) -> None:
        offset = NOW.strftime("%z")

        self.assertTrue(self.status(running=True)["recording"])
        self.assertEqual(
            {
                "recording": False,
                "history_available": True,
                "first_recorded_at": iso("2025-12-31T12:00"),
                "last_recorded_at": iso("2026-02-03T09:00"),
                "current_time": NOW.isoformat(timespec="seconds"),
                "utc_offset": f"{offset[:3]}:{offset[3:]}",
                "timezone": NOW.tzname(),
                "sample_interval_seconds": 5.0,
                "idle_threshold_seconds": 300,
            },
            self.status(running=False),
        )

    def test_missing_history_is_not_an_error(self) -> None:
        missing = self.root / "missing" / "activity.db"

        result = self.status(running=False, database_path=missing)

        self.assertFalse(result["history_available"])
        self.assertIsNone(result["first_recorded_at"])
        self.assertIsNone(result["last_recorded_at"])
        self.assertFalse(missing.parent.exists())

    def test_history_without_segments_is_not_available(self) -> None:
        empty = self.root / "empty.db"
        ActivityDatabase(empty).close()
        uninitialized = self.root / "uninitialized.db"
        uninitialized.touch()

        for path in (empty, uninitialized):
            with self.subTest(path=path.name):
                result = self.status(running=True, database_path=path)

                self.assertFalse(result["history_available"])
                self.assertIsNone(result["first_recorded_at"])
        self.assertEqual(0, uninitialized.stat().st_size)

    def test_defaults_to_the_current_local_time(self) -> None:
        before = datetime.now().astimezone().replace(microsecond=0)
        result = self.status(running=False, now=None)
        after = datetime.now().astimezone()

        current = datetime.fromisoformat(result["current_time"])
        self.assertLessEqual(before, current)
        self.assertLessEqual(current, after)


class HistoryAccessTests(McpToolsTestCase):
    def test_missing_history_is_an_error_for_activity_tools(self) -> None:
        missing = self.root / "missing" / "activity.db"

        for name, arguments in (
            ("get_activity_summary", None),
            ("get_activity_segments", DAY),
        ):
            with self.subTest(name=name):
                self.assert_tool_error(
                    name,
                    arguments,
                    "Start App Activity to begin recording",
                    database_path=missing,
                )
        self.assertFalse(missing.parent.exists())

    def test_uninitialized_history_is_reported_as_missing(self) -> None:
        uninitialized = self.root / "uninitialized.db"
        uninitialized.touch()

        self.assert_tool_error(
            "get_activity_summary",
            None,
            "Start App Activity to begin recording",
            database_path=uninitialized,
        )

    def test_tools_never_change_the_history_file(self) -> None:
        self.database.close()
        before = self.database_path.read_bytes()

        self.call(
            "get_activity_summary",
            {"include_timeline": True, "include_window_titles": True},
        )
        self.call(
            "get_activity_segments", {**DAY, "application": "code"}
        )
        self.call("get_tracking_status")

        self.assertEqual(before, self.database_path.read_bytes())

    def test_defaults_to_the_configured_database_path(self) -> None:
        with mock.patch(
            "app_activity.paths.DATABASE_PATH", self.database_path
        ):
            result = call_tool("get_activity_summary", None, now=NOW)

        self.assertEqual(6300, result["active_seconds"])

    def test_reports_an_older_history_format(self) -> None:
        legacy = self.root / "legacy.db"
        connection = sqlite3.connect(legacy)
        try:
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
            connection.execute(
                """
                INSERT INTO activity_segments(
                    start_ts, end_ts, app_name, is_idle
                )
                VALUES (?, ?, 'Code', 0)
                """,
                (
                    local("2026-01-14T09:00").timestamp(),
                    local("2026-01-14T10:00").timestamp(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        for name, arguments in (
            ("get_activity_summary", None),
            ("get_activity_segments", DAY),
        ):
            with self.subTest(name=name):
                self.assert_tool_error(
                    name,
                    arguments,
                    "older format. Start App Activity once to upgrade it",
                    database_path=legacy,
                )
        status = self.call("get_tracking_status", database_path=legacy)
        self.assertTrue(status["history_available"])

    def test_reports_unreadable_history(self) -> None:
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not a database " * 512)

        self.assert_tool_error(
            "get_activity_summary",
            None,
            "The App Activity history could not be read",
            database_path=corrupt,
        )

    def test_reports_a_busy_history(self) -> None:
        with mock.patch.object(
            ActivityDatabase,
            "report",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            self.assert_tool_error(
                "get_activity_summary", None, "history is busy"
            )


class ValidationTests(McpToolsTestCase):
    def test_rejects_invalid_arguments(self) -> None:
        summary = "get_activity_summary"
        segments = "get_activity_segments"
        cases = [
            (summary, [], "Arguments must be a JSON object"),
            (summary, "day", "Arguments must be a JSON object"),
            (summary, {"periods": "day"}, "Unknown argument(s): periods"),
            ("get_tracking_status", {"verbose": True}, "Unknown argument(s)"),
            (summary, {"period": "Day"}, "period must be one of"),
            (summary, {"period": "fortnight"}, "period must be one of"),
            (summary, {"period": 1}, "period must be a string"),
            (summary, {"max_applications": True}, "must be an integer"),
            (summary, {"max_applications": "5"}, "must be an integer"),
            (summary, {"max_applications": 2.5}, "must be an integer"),
            (summary, {"max_applications": 0}, "between 1 and 100"),
            (summary, {"max_applications": 101}, "between 1 and 100"),
            (summary, {"max_windows_per_application": 51}, "between 1 and 50"),
            (summary, {"include_timeline": 1}, "must be true or false"),
            (summary, {"include_window_titles": "true"}, "true or false"),
            (summary, {"date": "2026-13-01"}, "date must be a local date"),
            (summary, {"date": "yesterday"}, "date must be a local date"),
            (summary, {"date": ""}, "date must be a local date"),
            (summary, {"date": "2026-01-14T25:00"}, "must be a local date"),
            (
                summary,
                {"period": "year", "date": "9999-12-31"},
                "outside the supported date range",
            ),
            (segments, {"start": "2026-01-14"}, "Missing required argument"),
            (
                segments,
                {"start": None, "end": "2026-01-14"},
                "Missing required argument: start",
            ),
            (segments, {**DAY, "limit": 0}, "between 1 and 1000"),
            (segments, {**DAY, "limit": 1001}, "between 1 and 1000"),
            (segments, {**DAY, "offset": -1}, "offset must be at least 0"),
            (segments, {**DAY, "include_idle": 0}, "must be true or false"),
            (segments, {**DAY, "application": " "}, "must not be empty"),
            (segments, {**DAY, "application": 5}, "must be a string"),
            (segments, {**DAY, "end": "soon"}, "end must be a local date"),
            (
                segments,
                {"start": "9999-12-31", "end": "9999-12-31"},
                "outside the supported date range",
            ),
        ]
        for name, arguments, message in cases:
            with self.subTest(name=name, arguments=arguments):
                self.assert_tool_error(name, arguments, message)

    def test_accepts_null_optionals_and_integral_numbers(self) -> None:
        summary = self.call(
            "get_activity_summary",
            {"period": None, "date": None, "max_applications": 1.0},
        )
        segments = self.call(
            "get_activity_segments",
            {**DAY, "application": None, "limit": 2.0},
        )

        self.assertEqual("day", summary["period"])
        self.assertEqual(["Code"], list(app_seconds(summary)))
        self.assertEqual(2, segments["returned"])

    def test_unknown_tools_raise_unknown_tool_error(self) -> None:
        for name in ("get_activity", "GET_TRACKING_STATUS", "", None):
            with self.subTest(name=name):
                with self.assertRaises(UnknownToolError):
                    self.call(name, [])


class ToolDefinitionTests(McpToolsTestCase):
    def test_tool_definitions_follow_the_contract(self) -> None:
        self.assertEqual(
            [
                "get_activity_summary",
                "get_activity_segments",
                "get_tracking_status",
            ],
            [tool["name"] for tool in TOOLS],
        )
        for tool in TOOLS:
            with self.subTest(tool=tool["name"]):
                schema = tool["inputSchema"]

                self.assertEqual(
                    {
                        "name",
                        "title",
                        "description",
                        "inputSchema",
                        "annotations",
                    },
                    set(tool),
                )
                self.assertTrue(tool["title"])
                self.assertTrue(tool["description"])
                self.assertEqual(
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": False,
                    },
                    tool["annotations"],
                )
                self.assertEqual("object", schema["type"])
                self.assertIs(False, schema["additionalProperties"])
                self.assertLessEqual(
                    set(schema.get("required", [])), set(schema["properties"])
                )
                for rules in schema["properties"].values():
                    self.assertIn(
                        rules["type"], {"string", "integer", "boolean"}
                    )
                    self.assertTrue(rules["description"])
                json.dumps(tool)
        self.assertEqual(
            ["start", "end"], TOOLS[1]["inputSchema"]["required"]
        )

    def test_every_declared_property_is_accepted(self) -> None:
        for tool in TOOLS:
            schema = tool["inputSchema"]
            required = {
                name: SAMPLE_VALUES[name]
                for name in schema.get("required", [])
            }
            for name, rules in schema["properties"].items():
                values = [SAMPLE_VALUES[name], *rules.get("enum", [])]
                if "default" in rules:
                    values.append(rules["default"])
                for value in values:
                    with self.subTest(tool=tool["name"], name=name, v=value):
                        self.call(tool["name"], {**required, name: value})


if __name__ == "__main__":
    unittest.main()
