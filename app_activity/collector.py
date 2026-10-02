from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .database import ActivityDatabase, ActivityState
from .windows_activity import get_activity_state


class ActivityCollector:
    def __init__(
        self,
        database: ActivityDatabase,
        stop_event: threading.Event,
        sample_interval_seconds: float = 5.0,
        idle_threshold_seconds: int = 300,
        state_provider: Callable[[int], ActivityState] = get_activity_state,
    ) -> None:
        self.database = database
        self.stop_event = stop_event
        self.sample_interval_seconds = sample_interval_seconds
        self.idle_threshold_seconds = idle_threshold_seconds
        self.state_provider = state_provider

    def run(self) -> None:
        segment_id: int | None = None
        previous_state: ActivityState | None = None
        previous_sample_ts: float | None = None
        gap_threshold = max(30.0, self.sample_interval_seconds * 3)
        while not self.stop_event.is_set():
            sample_ts = time.time()
            state = self.state_provider(self.idle_threshold_seconds)
            if (
                previous_sample_ts is not None
                and sample_ts - previous_sample_ts > gap_threshold
            ):
                idle = ActivityState(app_name="Idle", is_idle=True)
                idle_id = self.database.record_sample(
                    None, None, idle, previous_sample_ts
                )
                self.database.record_sample(
                    idle_id, idle, idle, sample_ts
                )
                segment_id = None
                previous_state = None
            segment_id = self.database.record_sample(
                segment_id, previous_state, state, sample_ts
            )
            previous_state = state
            previous_sample_ts = sample_ts
            self.stop_event.wait(self.sample_interval_seconds)
