from __future__ import annotations

import ctypes
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app_activity.tray import (
    POWERBROADCAST_SETTING,
    TrayApplication,
    _color,
)


class TrayApplicationTests(unittest.TestCase):
    def test_open_report_opens_local_dashboard(self) -> None:
        tray = TrayApplication(Path("stop.requested"), "http://127.0.0.1:8765")

        with patch("app_activity.tray.webbrowser.open") as open_browser:
            tray.open_report()

        open_browser.assert_called_once_with("http://127.0.0.1:8765")

    def test_color_builds_windows_color_reference(self) -> None:
        self.assertEqual(0x030201, _color(1, 2, 3))

    def test_display_power_notification_updates_idle_signal(self) -> None:
        display_off = threading.Event()
        tray = TrayApplication(
            Path("stop.requested"),
            "http://127.0.0.1:8765",
            display_off,
        )
        buffer = ctypes.create_string_buffer(
            POWERBROADCAST_SETTING.Data.offset
            + ctypes.sizeof(ctypes.c_ulong)
        )
        setting = ctypes.cast(
            buffer, ctypes.POINTER(POWERBROADCAST_SETTING)
        ).contents
        setting.DataLength = ctypes.sizeof(ctypes.c_ulong)
        state = ctypes.cast(
            ctypes.addressof(buffer) + POWERBROADCAST_SETTING.Data.offset,
            ctypes.POINTER(ctypes.c_ulong),
        )

        state.contents.value = 0
        tray._update_display_state(ctypes.addressof(buffer))
        self.assertTrue(display_off.is_set())

        state.contents.value = 1
        tray._update_display_state(ctypes.addressof(buffer))
        self.assertFalse(display_off.is_set())


if __name__ == "__main__":
    unittest.main()
