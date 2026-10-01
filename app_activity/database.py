from __future__ import annotations

import calendar
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActivityState:
    app_name: str
    is_idle: bool
    process_name: str | None = None


class ActivityDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._migrated_aliases: set[tuple[str, str]] = set()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
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
                    is_idle INTEGER NOT NULL CHECK (is_idle IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS idx_segments_time
                    ON activity_segments(start_ts, end_ts);
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
        if previous_segment_id is not None and previous_state == state:
            connection.execute(
                "UPDATE activity_segments SET end_ts = ? WHERE id = ?",
                (sample_ts, previous_segment_id),
            )
            connection.commit()
            return previous_segment_id

        cursor = connection.execute(
            """
            INSERT INTO activity_segments(start_ts, end_ts, app_name, is_idle)
            VALUES (?, ?, ?, ?)
            """,
            (sample_ts, sample_ts, state.app_name, int(state.is_idle)),
        )
        connection.commit()
        return int(cursor.lastrowid)

    def _migrate_process_name(
        self, connection: sqlite3.Connection, state: ActivityState
    ) -> None:
        process_name = state.process_name
        alias = (process_name, state.app_name) if process_name else None
        if (
            alias is None
            or process_name == state.app_name
            or alias in self._migrated_aliases
        ):
            return
        connection.execute(
            "UPDATE activity_segments SET app_name = ? WHERE app_name = ?",
            (state.app_name, process_name),
        )
        self._migrated_aliases.add(alias)

    def report(self, period: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        start, end, buckets = _period_bounds(period, now)
        rows = self._connection().execute(
            """
            SELECT start_ts, end_ts, app_name, is_idle
            FROM activity_segments
            WHERE end_ts >= ? AND start_ts < ?
            ORDER BY start_ts
            """,
            (start.timestamp(), end.timestamp()),
        ).fetchall()

        app_seconds: dict[str, float] = defaultdict(float)
        bucket_active = [0.0] * len(buckets)
        active_seconds = 0.0
        idle_seconds = 0.0

        for start_ts, end_ts, app_name, is_idle in rows:
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
                for index, bucket in enumerate(buckets):
                    overlap = max(
                        0.0,
                        min(segment_end, bucket["end"].timestamp())
                        - max(segment_start, bucket["start"].timestamp()),
                    )
                    bucket_active[index] += overlap

        apps = [
            {
                "name": name,
                "seconds": round(seconds),
                "percent": round(seconds * 100 / active_seconds, 1)
                if active_seconds
                else 0,
            }
            for name, seconds in sorted(
                app_seconds.items(), key=lambda item: item[1], reverse=True
            )
        ]
        return {
            "period": period,
            "label": _period_label(period, start, end),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "active_seconds": round(active_seconds),
            "idle_seconds": round(idle_seconds),
            "tracked_seconds": round(active_seconds + idle_seconds),
            "apps": apps,
            "timeline": [
                {"label": bucket["label"], "seconds": round(seconds)}
                for bucket, seconds in zip(buckets, bucket_active)
            ],
        }


def _period_bounds(
    period: str, now: datetime
) -> tuple[datetime, datetime, list[dict[str, Any]]]:
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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
        start = day_start - timedelta(days=day_start.weekday())
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
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
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
    return start, end, buckets


def _period_label(period: str, start: datetime, end: datetime) -> str:
    if period == "day":
        return start.strftime("%A, %B %d, %Y")
    if period == "week":
        return f"{start.strftime('%b %d')} - {(end - timedelta(days=1)).strftime('%b %d, %Y')}"
    if period == "month":
        return start.strftime("%B %Y")
    return str(start.year)
