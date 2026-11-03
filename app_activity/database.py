from __future__ import annotations

import calendar
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .app_classification import uses_title_details


_SEGMENT_NAME = "CASE WHEN is_idle THEN 'Idle' ELSE app_name END"


@dataclass(frozen=True)
class ActivityState:
    app_name: str
    is_idle: bool
    process_name: str | None = None
    window_title: str = ""
    detail_key: str = ""
    legacy_names: tuple[str, ...] = ()


class ActivityDatabase:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only
        self._local = threading.local()
        self._migrated_aliases: set[tuple[str, str]] = set()
        self._migrated_detail_apps: set[str] = set()
        if not read_only:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            # mode=ro never creates the database and opens it read-only, so
            # neither queries nor WAL checkpoints can change it. SQLite may
            # still create the -wal and -shm files it needs to read WAL data.
            connection = sqlite3.connect(
                _read_only_uri(self.path), uri=True, timeout=10
            )
            connection.execute("PRAGMA query_only=ON")
            return connection
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._connect()
            self._local.connection = connection
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS activity_segments (
                    id INTEGER PRIMARY KEY,
                    start_ts REAL NOT NULL,
                    end_ts REAL NOT NULL,
                    app_name TEXT NOT NULL,
                    is_idle INTEGER NOT NULL CHECK (is_idle IN (0, 1)),
                    window_title TEXT NOT NULL DEFAULT '',
                    detail_key TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_segments_time
                    ON activity_segments(start_ts, end_ts);
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(activity_segments)"
                )
            }
            if "window_title" not in columns:
                connection.execute(
                    """
                    ALTER TABLE activity_segments
                    ADD COLUMN window_title TEXT NOT NULL DEFAULT ''
                    """
                )
            if "detail_key" not in columns:
                connection.execute(
                    """
                    ALTER TABLE activity_segments
                    ADD COLUMN detail_key TEXT NOT NULL DEFAULT ''
                    """
                )
            connection.execute(
                """
                UPDATE activity_segments
                SET app_name = 'Idle',
                    is_idle = 1,
                    window_title = '',
                    detail_key = ''
                WHERE lower(app_name) = 'windows default lock screen'
                   OR lower(window_title) = 'windows default lock screen'
                   OR lower(app_name) = 'program manager'
                   OR lower(window_title) = 'program manager'
                   OR lower(app_name) = 'desktop'
                   OR lower(window_title) = 'desktop'
                   OR lower(app_name) = 'task switching'
                   OR lower(window_title) = 'task switching'
                   OR lower(app_name) = 'windows logon user interface host'
                   OR lower(window_title) = 'windows logon user interface host'
                   OR lower(app_name) = 'credential ui broker'
                   OR lower(window_title) = 'credential ui broker'
                """
            )
            connection.execute(
                """
                UPDATE activity_segments
                SET app_name = CASE
                    WHEN instr(window_title, char(92)) > 0
                    THEN substr(
                        window_title,
                        1,
                        instr(window_title, char(92)) - 1
                    )
                    ELSE window_title
                END
                WHERE window_title != ''
                  AND lower(app_name)
                      LIKE 'microsoft%windows%operating system'
                """
            )
            connection.commit()
        finally:
            connection.close()

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def record_sample(
        self,
        previous_segment_id: int | None,
        previous_state: ActivityState | None,
        state: ActivityState,
        sample_ts: float,
    ) -> int:
        connection = self._connection()
        self._migrate_process_name(connection, state)
        self._migrate_legacy_details(connection, state)
        if previous_segment_id is not None and previous_state == state:
            connection.execute(
                "UPDATE activity_segments SET end_ts = ? WHERE id = ?",
                (sample_ts, previous_segment_id),
            )
            connection.commit()
            return previous_segment_id

        cursor = connection.execute(
            """
            INSERT INTO activity_segments(
                start_ts, end_ts, app_name, is_idle, window_title
                , detail_key
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                sample_ts,
                sample_ts,
                state.app_name,
                int(state.is_idle),
                state.window_title,
                state.detail_key,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)

    def _migrate_process_name(
        self, connection: sqlite3.Connection, state: ActivityState
    ) -> None:
        aliases = (state.process_name, *state.legacy_names)
        for old_name in aliases:
            alias = (old_name, state.app_name) if old_name else None
            if (
                alias is None
                or old_name == state.app_name
                or alias in self._migrated_aliases
            ):
                continue
            connection.execute(
                "UPDATE activity_segments SET app_name = ? WHERE app_name = ?",
                (state.app_name, old_name),
            )
            self._migrated_aliases.add(alias)

    def _migrate_legacy_details(
        self, connection: sqlite3.Connection, state: ActivityState
    ) -> None:
        if (
            not state.detail_key
            or uses_title_details(state.app_name)
            or state.app_name in self._migrated_detail_apps
        ):
            return
        connection.execute(
            """
            UPDATE activity_segments
            SET detail_key = ?
            WHERE app_name = ? AND detail_key = '' AND window_title != ''
            """,
            (state.detail_key, state.app_name),
        )
        self._migrated_detail_apps.add(state.app_name)

    def report(
        self,
        period: str,
        now: datetime | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        start, end, buckets = _period_bounds(period, now, offset)
        rows = self._connection().execute(
            """
            SELECT
                start_ts, end_ts, app_name, is_idle, window_title, detail_key
            FROM activity_segments
            WHERE end_ts >= ? AND start_ts < ?
            ORDER BY start_ts
            """,
            (start.timestamp(), end.timestamp()),
        ).fetchall()

        app_seconds: dict[str, float] = defaultdict(float)
        detail_seconds: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        detail_labels: dict[str, dict[str, str]] = defaultdict(dict)
        bucket_active = [0.0] * len(buckets)
        bucket_apps: list[dict[str, float]] = [
            defaultdict(float) for _ in buckets
        ]
        active_seconds = 0.0
        idle_seconds = 0.0

        for (
            start_ts,
            end_ts,
            app_name,
            is_idle,
            window_title,
            detail_key,
        ) in rows:
            segment_start = max(float(start_ts), start.timestamp())
            segment_end = min(float(end_ts), end.timestamp())
            duration = max(0.0, segment_end - segment_start)
            if not duration:
                continue
            if is_idle:
                idle_seconds += duration
            else:
                active_seconds += duration
                app_seconds[str(app_name)] += duration
                if window_title:
                    key = _report_detail_key(
                        str(app_name),
                        str(window_title),
                        str(detail_key),
                    )
                    detail_seconds[str(app_name)][key] += duration
                    detail_labels[str(app_name)][key] = str(window_title)
                for index, bucket in enumerate(buckets):
                    overlap = max(
                        0.0,
                        min(segment_end, bucket["end"].timestamp())
                        - max(segment_start, bucket["start"].timestamp()),
                    )
                    bucket_active[index] += overlap
                    bucket_apps[index][str(app_name)] += overlap

        apps = [
            {
                "name": name,
                "seconds": round(seconds),
                "percent": round(seconds * 100 / active_seconds, 1)
                if active_seconds
                else 0,
                "windows": [
                    {
                        "title": title,
                        "seconds": round(title_seconds),
                        "percent": round(title_seconds * 100 / seconds, 1),
                    }
                    for key, title_seconds in sorted(
                        detail_seconds[name].items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                    for title in [detail_labels[name][key]]
                ],
            }
            for name, seconds in sorted(
                app_seconds.items(), key=lambda item: item[1], reverse=True
            )
        ]
        return {
            "period": period,
            "offset": offset,
            "label": _period_label(period, start, end),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "active_seconds": round(active_seconds),
            "idle_seconds": round(idle_seconds),
            "tracked_seconds": round(active_seconds + idle_seconds),
            "apps": apps,
            "timeline": [
                {
                    "label": bucket["label"],
                    "start": bucket["start"].isoformat(),
                    "end": bucket["end"].isoformat(),
                    "capacity_seconds": round(
                        (
                            bucket["end"] - bucket["start"]
                        ).total_seconds()
                    ),
                    "seconds": round(seconds),
                    "apps": [
                        {"name": name, "seconds": round(app_seconds)}
                        for name, app_seconds in sorted(
                            apps.items(),
                            key=lambda item: item[1],
                            reverse=True,
                        )
                        if app_seconds > 0
                    ],
                }
                for bucket, seconds, apps in zip(
                    buckets, bucket_active, bucket_apps
                )
            ],
        }

    def export_rows(
        self, start_ts: float | None = None, end_ts: float | None = None
    ) -> list[tuple[float, float, str, bool, str]]:
        conditions = ["end_ts > start_ts"]
        parameters: list[float] = []
        if start_ts is not None:
            conditions.append("end_ts > ?")
            parameters.append(start_ts)
        if end_ts is not None:
            conditions.append("start_ts < ?")
            parameters.append(end_ts)
        rows = self._connection().execute(
            f"""
            SELECT start_ts, end_ts, app_name, is_idle, window_title
            FROM activity_segments
            WHERE {" AND ".join(conditions)}
            ORDER BY start_ts
            """,
            parameters,
        ).fetchall()
        return [
            (
                max(float(start), start_ts) if start_ts is not None else float(start),
                min(float(end), end_ts) if end_ts is not None else float(end),
                str(app_name),
                bool(is_idle),
                str(window_title),
            )
            for start, end, app_name, is_idle, window_title in rows
        ]

    def segments(
        self,
        start_ts: float,
        end_ts: float,
        *,
        application: str | None = None,
        include_idle: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[int, list[tuple[int, int, str, bool, str]]]:
        """Return the number of matching segments and one page of them.

        Rows are clipped to the range like export_rows, then rounded to whole
        seconds; segments that round to zero length are excluded. Idle
        segments are named "Idle". The application filter is a
        case-insensitive exact match.
        """
        # CAST truncates, so adding 0.5 rounds these positive timestamps.
        rounded_start = "CAST(MAX(start_ts, ?) + 0.5 AS INTEGER)"
        rounded_end = "CAST(MIN(end_ts, ?) + 0.5 AS INTEGER)"
        conditions = [
            "end_ts > ?",
            "start_ts < ?",
            f"{rounded_end} > {rounded_start}",
        ]
        parameters: list[Any] = [start_ts, end_ts, end_ts, start_ts]
        if not include_idle:
            conditions.append("is_idle = 0")
        connection = self._connection()
        # One read transaction keeps the count and the page consistent while
        # the service is recording.
        began = not connection.in_transaction
        if began:
            connection.execute("BEGIN")
        try:
            if application is not None:
                # SQLite only folds ASCII case, so match names in Python.
                wanted = application.strip().casefold()
                range_filter = " AND ".join(conditions)
                names = [
                    str(name)
                    for (name,) in connection.execute(
                        f"""
                        SELECT DISTINCT {_SEGMENT_NAME}
                        FROM activity_segments
                        WHERE {range_filter}
                        """,
                        parameters,
                    )
                    if str(name).strip().casefold() == wanted
                ]
                if not names:
                    return 0, []
                placeholders = ", ".join("?" for _ in names)
                conditions.append(f"{_SEGMENT_NAME} IN ({placeholders})")
                parameters.extend(names)
            where = " AND ".join(conditions)
            (total,) = connection.execute(
                f"SELECT COUNT(*) FROM activity_segments WHERE {where}",
                parameters,
            ).fetchone()
            rows = connection.execute(
                f"""
                SELECT {rounded_start}, {rounded_end}, {_SEGMENT_NAME},
                    is_idle, window_title
                FROM activity_segments
                WHERE {where}
                ORDER BY start_ts, id
                LIMIT ? OFFSET ?
                """,
                [
                    start_ts,
                    end_ts,
                    *parameters,
                    -1 if limit is None else limit,
                    offset,
                ],
            ).fetchall()
        finally:
            if began:
                connection.rollback()
        return int(total), [
            (
                int(start),
                int(end),
                str(app_name),
                bool(is_idle),
                str(window_title),
            )
            for start, end, app_name, is_idle, window_title in rows
        ]

    def recorded_range(self) -> tuple[float, float] | None:
        first, last = self._connection().execute(
            "SELECT MIN(start_ts), MAX(end_ts) FROM activity_segments"
        ).fetchone()
        if first is None:
            return None
        return float(first), float(last)


def _read_only_uri(path: Path) -> str:
    # Path.as_uri() writes UNC paths as file://server/share, which SQLite
    # rejects, so always use an empty authority.
    location = quote(path.resolve().as_posix(), safe="/:")
    if not location.startswith("/"):
        location = f"/{location}"
    return f"file://{location}?mode=ro"


def _legacy_detail_key(app_name: str, window_title: str) -> str:
    if uses_title_details(app_name):
        return f"legacy-title:{window_title}"
    return "legacy-window"


def _report_detail_key(
    app_name: str, window_title: str, detail_key: str
) -> str:
    if uses_title_details(app_name):
        return f"title:{window_title}"
    return detail_key or _legacy_detail_key(app_name, window_title)


def _local_time(moment: datetime) -> datetime:
    # Naive values are local wall-clock times. Python resolves skipped and
    # repeated times with the UTC offset in effect before the change.
    return moment.astimezone()


def _localize(moment: datetime, fallback: tzinfo | None) -> datetime:
    try:
        local = _local_time(moment)
    except (OverflowError, OSError):
        # Windows cannot convert times before 1970. Nothing was recorded
        # then, so the reference UTC offset is close enough.
        if moment.tzinfo is not None:
            return moment
        return moment.replace(tzinfo=fallback)
    if moment.tzinfo is not None or local.replace(tzinfo=None) == moment:
        return local
    # Like zoneinfo, a skipped wall-clock time keeps its wall clock with the
    # UTC offset in effect before the change.
    utc = local.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.replace(tzinfo=timezone(moment - utc))


def _period_bounds(
    period: str, now: datetime, offset: int = 0
) -> tuple[datetime, datetime, list[dict[str, Any]]]:
    now = _local_time(now)
    reference = now.tzinfo
    if period == "hour":
        # Step in absolute time so repeated and skipped clock hours stay
        # distinct, then show each boundary with its own UTC offset.
        start = _localize(
            now.replace(minute=0, second=0, microsecond=0)
            + timedelta(hours=offset),
            reference,
        )
        buckets = []
        for minute in range(0, 60, 5):
            bucket_start = _localize(
                start + timedelta(minutes=minute), reference
            )
            buckets.append(
                {
                    "start": bucket_start,
                    "end": _localize(
                        start + timedelta(minutes=minute + 5), reference
                    ),
                    "label": bucket_start.strftime("%H:%M"),
                }
            )
        return start, _localize(start + timedelta(hours=1), reference), buckets
    # Other periods follow local wall-clock dates, and each boundary gets its
    # own UTC offset, so a day lasts 23 or 25 hours when DST starts or ends.
    now = now.replace(tzinfo=None)
    if period == "day":
        start = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=offset)
        end = start + timedelta(days=1)
        buckets = [
            {
                "start": start + timedelta(hours=hour),
                "end": start + timedelta(hours=hour + 1),
                "label": f"{hour:02d}",
            }
            for hour in range(24)
        ]
    elif period == "week":
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = (
            day_start
            - timedelta(days=day_start.weekday())
            + timedelta(weeks=offset)
        )
        end = start + timedelta(days=7)
        buckets = [
            {
                "start": start + timedelta(days=day),
                "end": start + timedelta(days=day + 1),
                "label": (start + timedelta(days=day)).strftime("%a"),
            }
            for day in range(7)
        ]
    elif period == "month":
        month_index = now.year * 12 + now.month - 1 + offset
        year, zero_based_month = divmod(month_index, 12)
        start = now.replace(
            year=year,
            month=zero_based_month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        _, days = calendar.monthrange(start.year, start.month)
        end = start + timedelta(days=days)
        buckets = [
            {
                "start": start + timedelta(days=day),
                "end": start + timedelta(days=day + 1),
                "label": str(day + 1),
            }
            for day in range(days)
        ]
    elif period == "year":
        start = now.replace(
            year=now.year + offset,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        end = start.replace(year=start.year + 1)
        buckets = []
        for month in range(1, 13):
            bucket_start = start.replace(month=month)
            bucket_end = (
                bucket_start.replace(month=month + 1)
                if month < 12
                else end
            )
            buckets.append(
                {
                    "start": bucket_start,
                    "end": bucket_end,
                    "label": bucket_start.strftime("%b"),
                }
            )
    else:
        raise ValueError(f"Unsupported period: {period}")
    return (
        _localize(start, reference),
        _localize(end, reference),
        [
            {
                **bucket,
                "start": _localize(bucket["start"], reference),
                "end": _localize(bucket["end"], reference),
            }
            for bucket in buckets
        ],
    )


def _period_label(period: str, start: datetime, end: datetime) -> str:
    if period == "hour":
        start_time = start.strftime("%I:%M %p").lstrip("0")
        end_time = end.strftime("%I:%M %p").lstrip("0")
        return (
            f"{start.strftime('%A, %B %d, %Y')} · "
            f"{start_time} - {end_time}"
        )
    if period == "day":
        return start.strftime("%A, %B %d, %Y")
    if period == "week":
        return f"{start.strftime('%b %d')} - {(end - timedelta(days=1)).strftime('%b %d, %Y')}"
    if period == "month":
        return start.strftime("%B %Y")
    return str(start.year)
