from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from app_activity.collector import ActivityCollector
from app_activity.database import ActivityState


class ActivityCollectorTests(unittest.TestCase):
    def test_long_sampling_gap_is_recorded_as_idle(self) -> None:
        database = MagicMock()
        database.record_sample.side_effect = [1, 2, 2, 3]
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, False, True]
        state = ActivityState("Code", False)
        collector = ActivityCollector(
            database,
            stop_event,
            sample_interval_seconds=5,
            state_provider=lambda _: state,
        )

        with patch(
            "app_activity.collector.time.time",
            side_effect=[100.0, 200.0],
        ):
            collector.run()

        idle = ActivityState("Idle", True)
        self.assertEqual(
            [
                call(None, None, state, 100.0),
                call(None, None, idle, 100.0),
                call(2, idle, idle, 200.0),
                call(None, None, state, 200.0),
            ],
            database.record_sample.call_args_list,
        )

    def test_normal_sampling_does_not_insert_idle_gap(self) -> None:
        database = MagicMock()
        database.record_sample.side_effect = [1, 1]
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, False, True]
        state = ActivityState("Code", False)
        collector = ActivityCollector(
            database,
            stop_event,
            sample_interval_seconds=5,
            state_provider=lambda _: state,
        )

        with patch(
            "app_activity.collector.time.time",
            side_effect=[100.0, 105.0],
        ):
            collector.run()

        self.assertEqual(
            [
                call(None, None, state, 100.0),
                call(1, state, state, 105.0),
            ],
            database.record_sample.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
