from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app_activity.app_classification import uses_title_details
from app_activity.windows_activity import (
    _application_metadata,
    _application_name,
    _detail_key,
    _is_lock_screen,
)


class ApplicationNameTests(unittest.TestCase):
    def setUp(self) -> None:
        _application_name.cache_clear()
        _application_metadata.cache_clear()

    def tearDown(self) -> None:
        _application_name.cache_clear()
        _application_metadata.cache_clear()

    def test_prefers_product_name(self) -> None:
        with patch(
            "app_activity.windows_activity._version_string",
            side_effect=["Windows Terminal", "Windows Terminal Host"],
        ):
            self.assertEqual(
                "Windows Terminal", _application_name(Path("WindowsTerminal.exe"))
            )

    def test_uses_file_description_when_product_name_is_missing(self) -> None:
        with patch(
            "app_activity.windows_activity._version_string",
            side_effect=[None, "Contoso Editor"],
        ):
            self.assertEqual(
                "Contoso Editor", _application_name(Path("editor.exe"))
            )

    def test_file_description_is_retained_as_a_legacy_alias(self) -> None:
        with patch(
            "app_activity.windows_activity._version_string",
            side_effect=["Windows Terminal", "Windows Terminal Host"],
        ):
            name, aliases = _application_metadata(Path("WindowsTerminal.exe"))

        self.assertEqual("Windows Terminal", name)
        self.assertEqual(("Windows Terminal Host",), aliases)

    def test_falls_back_to_process_name_without_metadata(self) -> None:
        with patch(
            "app_activity.windows_activity._version_string", return_value=None
        ):
            self.assertEqual("custom-tool", _application_name(Path("custom-tool.exe")))

    def test_terminal_detail_uses_stable_window_identity(self) -> None:
        first = _detail_key("Windows Terminal", 100, "Copilot - Planning")
        second = _detail_key("Windows Terminal", 100, "Copilot - Implementing")

        self.assertEqual(first, second)

    def test_edge_detail_uses_window_and_title(self) -> None:
        first = _detail_key("Microsoft Edge", 200, "First page")
        second = _detail_key("Microsoft Edge", 200, "Second page")

        self.assertNotEqual(first, second)

    def test_browser_classification_does_not_match_partial_product_names(
        self,
    ) -> None:
        self.assertTrue(uses_title_details("Microsoft Edge"))
        self.assertFalse(uses_title_details("ArcGIS Pro"))

    def test_lock_screen_is_identified_from_product_name_or_title(self) -> None:
        self.assertTrue(_is_lock_screen("Windows Default Lock Screen", ""))
        self.assertTrue(
            _is_lock_screen(
                "Microsoft Windows Operating System",
                "Windows Default Lock Screen",
            )
        )
        self.assertFalse(
            _is_lock_screen(
                "Microsoft Windows Operating System",
                "Task Switching",
            )
        )


if __name__ == "__main__":
    unittest.main()
