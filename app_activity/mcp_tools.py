"""Read-only MCP tools that expose the App Activity history."""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .database import ActivityDatabase
from .mcp_errors import ToolError, UnknownToolError


_MAX_RANGE_DAYS = 31
_NO_HISTORY = (
    "No App Activity history was found. Start App Activity to begin recording."
)
_LOCAL_MOMENT = (
    "a local date (YYYY-MM-DD) or date-time (YYYY-MM-DDTHH:MM[:SS]). A "
    "date-time may end with a UTC offset or Z; without one it is local time"
)
_TITLE_PRIVACY = (
    "Window titles can reveal private details such as document names, web "
    "page titles, or message subjects, so request them only when the user "
    "needs that detail."
)


def _annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


class _MissingHistory(ToolError):
    """The history database is missing or has not been initialized yet."""


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_activity_summary",
        "title": "Activity summary",
        "description": (
            "Summarize how the user spent time on this Windows computer "
            "during one local calendar hour, day, week (Monday to Sunday), "
            "month, or year: active, idle, and total tracked time, plus "
            "applications ranked by active (foreground, non-idle) time. Use "
            "it for questions such as 'How much time did I spend in Outlook "
            "this week?' or 'What did I use most yesterday?'. It can add a "
            "timeline breakdown and, only on request, window titles. All "
            "times are in the user's local time zone."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["hour", "day", "week", "month", "year"],
                    "default": "day",
                    "description": (
                        "Local calendar period to summarize. Weeks start on "
                        "Monday."
                    ),
                },
                "date": {
                    "type": "string",
                    "description": (
                        "Any moment inside the period to summarize, as "
                        f"{_LOCAL_MOMENT}. Defaults to now. Period 'hour' "
                        "needs a date-time."
                    ),
                },
                "include_timeline": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Add a per-bucket breakdown: 5-minute buckets for an "
                        "hour, hours for a day, days for a week or month, and "
                        "months for a year."
                    ),
                },
                "include_window_titles": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Include each application's top window titles. "
                        f"{_TITLE_PRIVACY}"
                    ),
                },
                "max_applications": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                    "description": (
                        "Maximum number of applications to return, ranked by "
                        "active time. Also limits each timeline bucket."
                    ),
                },
                "max_windows_per_application": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": (
                        "Maximum number of window titles per application. "
                        "Only used when include_window_titles is true."
                    ),
                },
            },
            "additionalProperties": False,
        },
        "annotations": _annotations(),
    },
    {
        "name": "get_activity_segments",
        "title": "Activity segments",
        "description": (
            "List the chronological foreground-application segments (which "
            "application was in the foreground, from when to when) that "
            "overlap a local time range of at most 31 days, clipped to the "
            "range. Use it for questions such as 'What was I doing around "
            "3 PM?' or 'When did I switch to Teams this morning?'. Idle time "
            "appears as the application 'Idle'. Results are paginated: pass "
            "next_offset as offset to get the next page. For total time per "
            "application, use get_activity_summary instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": (
                        f"Start of the range, as {_LOCAL_MOMENT}. A date "
                        "means the start of that day."
                    ),
                },
                "end": {
                    "type": "string",
                    "description": (
                        f"End of the range, as {_LOCAL_MOMENT}. A date "
                        "includes that whole day; a date-time is exclusive. "
                        "Must be after start and at most 31 days later."
                    ),
                },
                "application": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Only return segments for this application name, "
                        "matched exactly but case-insensitively (for example "
                        "'Microsoft Edge'). Use 'Idle' for idle time."
                    ),
                },
                "include_idle": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Include idle segments (application 'Idle')."
                    ),
                },
                "include_window_titles": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Include each segment's window title. "
                        f"{_TITLE_PRIVACY}"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 200,
                    "description": "Maximum number of segments to return.",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": (
                        "Number of matching segments to skip, for pagination."
                    ),
                },
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        "annotations": _annotations(),
    },
    {
        "name": "get_tracking_status",
        "title": "Tracking status",
        "description": (
            "Report whether App Activity is recording right now, which time "
            "span the recorded history covers, the current local time and "
            "time zone, and the sampling settings. Use it to check whether "
            "the data is fresh or to resolve relative dates such as "
            "'yesterday' before calling the other tools."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": _annotations(),
    },
]


def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    database_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    handler = _HANDLERS.get(name) if isinstance(name, str) else None
    if handler is None:
        raise UnknownToolError(f"Unknown tool: {_shorten(str(name))}")
    values = _validate(_TOOLS_BY_NAME[name]["inputSchema"], arguments)
    if database_path is None:
        from . import paths

        database_path = paths.DATABASE_PATH
    now = (now or datetime.now()).astimezone()
    return handler(values, Path(database_path), now)


def _validate(schema: dict[str, Any], arguments: object) -> dict[str, Any]:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolError("Arguments must be a JSON object.")
    properties: dict[str, dict[str, Any]] = schema["properties"]
    unknown = sorted(
        _shorten(str(key)) for key in arguments if key not in properties
    )
    if unknown:
        allowed = ", ".join(properties) or "none"
        raise ToolError(
            f"Unknown argument(s): {', '.join(unknown)}. "
            f"Allowed arguments: {allowed}."
        )
    values: dict[str, Any] = {}
    for name, rules in properties.items():
        value = arguments.get(name)
        if value is not None:
            values[name] = _check(name, rules, value)
        elif name in schema.get("required", ()):
            raise ToolError(f"Missing required argument: {name}.")
        else:
            values[name] = rules.get("default")
    return values


def _check(name: str, rules: dict[str, Any], value: object) -> Any:
    kind = rules["type"]
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ToolError(f"{name} must be true or false.")
        return value
    if kind == "integer":
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        # bool is a subclass of int, but true is not a valid count.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolError(f"{name} must be an integer.")
        low = rules["minimum"]
        high = rules.get("maximum")
        if value < low or (high is not None and value > high):
            bounds = (
                f"at least {low}"
                if high is None
                else f"between {low} and {high}"
            )
            raise ToolError(f"{name} must be {bounds}.")
        return value
    if not isinstance(value, str):
        raise ToolError(f"{name} must be a string.")
    choices = rules.get("enum")
    if choices is not None and value not in choices:
        options = ", ".join(choices)
        raise ToolError(
            f"{name} must be one of: {options}; got {_shorten(value)!r}."
        )
    if rules.get("minLength") and not value.strip():
        raise ToolError(f"{name} must not be empty.")
    return value


def _summary(
    values: dict[str, Any], database_path: Path, now: datetime
) -> dict[str, Any]:
    period = values["period"]
    reference = now
    if values["date"] is not None:
        moment = _parse_moment("date", values["date"])
        if isinstance(moment, datetime):
            reference = moment
        elif period == "hour":
            raise ToolError(
                "period 'hour' needs a date-time for date, such as "
                f"{moment.isoformat()}T09:00."
            )
        else:
            # Noon is safely inside the day even when DST shifts midnight.
            reference = _local("date", datetime.combine(moment, time(12)))
    with _read_history(database_path) as database:
        try:
            report = database.report(period, now=reference)
        except (OverflowError, OSError, ValueError):
            raise ToolError(
                "date is outside the supported date range."
            ) from None

    max_applications = values["max_applications"]
    max_windows = values["max_windows_per_application"]
    applications = []
    for app in report["apps"][:max_applications]:
        application: dict[str, Any] = {
            "name": app["name"],
            "active_seconds": app["seconds"],
            "active_duration": _duration(app["seconds"]),
            "percent_of_active_time": app["percent"],
        }
        if values["include_window_titles"]:
            application["windows"] = [
                {
                    "title": window["title"],
                    "active_seconds": window["seconds"],
                    "active_duration": _duration(window["seconds"]),
                    "percent_of_application_time": window["percent"],
                }
                for window in app["windows"][:max_windows]
            ]
            application["windows_truncated"] = (
                len(app["windows"]) > max_windows
            )
        applications.append(application)

    result: dict[str, Any] = {
        "period": report["period"],
        "label": report["label"],
        "start": report["start"],
        "end": report["end"],
        "active_seconds": report["active_seconds"],
        "idle_seconds": report["idle_seconds"],
        "tracked_seconds": report["tracked_seconds"],
        "active_duration": _duration(report["active_seconds"]),
        "idle_duration": _duration(report["idle_seconds"]),
        "tracked_duration": _duration(report["tracked_seconds"]),
        "application_count": len(report["apps"]),
        "applications_truncated": len(report["apps"]) > max_applications,
        "applications": applications,
    }
    if values["include_timeline"]:
        result["timeline"] = [
            {
                "label": bucket["label"],
                "start": bucket["start"],
                "end": bucket["end"],
                "active_seconds": bucket["seconds"],
                "applications": [
                    {"name": app["name"], "active_seconds": app["seconds"]}
                    for app in bucket["apps"][:max_applications]
                ],
            }
            for bucket in report["timeline"]
        ]
    return result


def _segments(
    values: dict[str, Any], database_path: Path, now: datetime
) -> dict[str, Any]:
    start = _range_bound("start", values["start"], end_of_day=False)
    end = _range_bound("end", values["end"], end_of_day=True)
    if end <= start:
        raise ToolError("end must be after start.")
    # Compare wall-clock times so a DST change cannot push a range of whole
    # days over the limit.
    span = end.replace(tzinfo=None) - start.replace(tzinfo=None)
    if span > timedelta(days=_MAX_RANGE_DAYS):
        raise ToolError(
            f"The range can cover at most {_MAX_RANGE_DAYS} days. Split it "
            "into smaller ranges, or use get_activity_summary for totals."
        )
    offset = values["offset"]
    with _read_history(database_path) as database:
        total, rows = database.segments(
            start.timestamp(),
            end.timestamp(),
            application=values["application"],
            include_idle=values["include_idle"],
            limit=values["limit"],
            offset=offset,
        )
    segments = []
    for first, last, application, idle, title in rows:
        segment: dict[str, Any] = {
            "start": _iso(first),
            "end": _iso(last),
            "duration_seconds": last - first,
            "application": application,
            "idle": idle,
        }
        if values["include_window_titles"]:
            segment["window_title"] = title
        segments.append(segment)
    next_offset = offset + len(segments)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": total,
        "offset": offset,
        "returned": len(segments),
        "next_offset": next_offset if next_offset < total else None,
        "segments": segments,
    }


def _status(
    values: dict[str, Any], database_path: Path, now: datetime
) -> dict[str, Any]:
    from . import cli
    from .collector import ActivityCollector

    recorded = None
    with suppress(_MissingHistory):
        with _read_history(database_path) as database:
            recorded = database.recorded_range()
    settings = inspect.signature(ActivityCollector).parameters
    return {
        "recording": bool(cli.is_running()),
        "history_available": recorded is not None,
        "first_recorded_at": None if recorded is None else _iso(recorded[0]),
        "last_recorded_at": None if recorded is None else _iso(recorded[1]),
        "current_time": now.isoformat(timespec="seconds"),
        "utc_offset": _utc_offset(now),
        "timezone": now.tzname(),
        "sample_interval_seconds": settings["sample_interval_seconds"].default,
        "idle_threshold_seconds": settings["idle_threshold_seconds"].default,
    }


@contextmanager
def _read_history(database_path: Path) -> Iterator[ActivityDatabase]:
    if not database_path.is_file():
        raise _MissingHistory(_NO_HISTORY)
    database = ActivityDatabase(database_path, read_only=True)
    try:
        yield database
    except sqlite3.Error as error:
        raise _database_error(error) from error
    finally:
        database.close()


def _database_error(error: sqlite3.Error) -> ToolError:
    message = str(error)
    lowered = message.lower()
    if "locked" in lowered or "busy" in lowered:
        return ToolError(
            "The App Activity history is busy. Try again in a moment."
        )
    if "no such table" in lowered:
        return _MissingHistory(_NO_HISTORY)
    if "no such column" in lowered:
        return ToolError(
            "The App Activity history uses an older format. Start App "
            "Activity once to upgrade it, then try again."
        )
    return ToolError(
        f"The App Activity history could not be read ({message})."
    )


def _range_bound(name: str, text: str, *, end_of_day: bool) -> datetime:
    moment = _parse_moment(name, text)
    if isinstance(moment, datetime):
        return moment
    if end_of_day:
        try:
            moment += timedelta(days=1)
        except OverflowError:
            raise ToolError(
                f"{name} is outside the supported date range."
            ) from None
    return _local(name, datetime.combine(moment, time.min))


def _parse_moment(name: str, text: str) -> date | datetime:
    text = text.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ToolError(
            f"{name} must be a local date (YYYY-MM-DD) or date-time "
            f"(YYYY-MM-DDTHH:MM[:SS]); got {_shorten(text)!r}."
        ) from None
    return _local(name, moment)


def _local(name: str, moment: datetime) -> datetime:
    try:
        return moment.astimezone()
    except (OverflowError, OSError, ValueError):
        raise ToolError(
            f"{name} is outside the supported date range."
        ) from None


def _iso(timestamp: float) -> str:
    moment = datetime.fromtimestamp(round(timestamp), timezone.utc)
    return moment.astimezone().isoformat()


def _utc_offset(moment: datetime) -> str:
    offset = moment.utcoffset() or timedelta()
    minutes = round(offset.total_seconds() / 60)
    sign = "-" if minutes < 0 else "+"
    hours, minutes = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _duration(seconds: int) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _shorten(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


_HANDLERS: dict[
    str, Callable[[dict[str, Any], Path, datetime], dict[str, Any]]
] = {
    "get_activity_summary": _summary,
    "get_activity_segments": _segments,
    "get_tracking_status": _status,
}
_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}
