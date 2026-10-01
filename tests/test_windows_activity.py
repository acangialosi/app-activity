from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app_activity.windows_activity import _application_name


class ApplicationNameTests(unittest.TestCase):
    def setUp(self) -> None:
        _application_name.cache_clear()

    def tearDown(self) -> None:
        _application_name.cache_clear()

    def test_prefers_file_description(self) -> None:
        with patch(
            "app_activity.windows_activity._version_string",
            side_effect=["Microsoft Teams", "Unused product"],
        ):
            self.assertEqual(
                "Microsoft Teams", _application_name(Path("ms-teams.exe"))
            )

    def test_uses_product_name_when_description_is_missing(self) -> None:
        with patch(
            "app_activity.windows_activity._version_string",
            side_effect=[None, "Contoso Editor"],
        ):
            self.assertEqual(
                "Contoso Editor", _application_name(Path("editor.exe"))
            )

    def test_falls_back_to_process_name_without_metadata(self) -> None:
        with patch(
            "app_activity.windows_activity._version_string", return_value=None
        ):
            self.assertEqual("custom-tool", _application_name(Path("custom-tool.exe")))


if __name__ == "__main__":
    unittest.main()
