from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app_activity.tray import TrayApplication, _color


class TrayApplicationTests(unittest.TestCase):
    def test_open_report_opens_local_dashboard(self) -> None:
        tray = TrayApplication(Path("stop.requested"), "http://127.0.0.1:8765")

        with patch("app_activity.tray.webbrowser.open") as open_browser:
            tray.open_report()

        open_browser.assert_called_once_with("http://127.0.0.1:8765")

    def test_color_builds_windows_color_reference(self) -> None:
        self.assertEqual(0x030201, _color(1, 2, 3))


if __name__ == "__main__":
    unittest.main()
