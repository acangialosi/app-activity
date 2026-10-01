from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app_activity.windows_activity import _application_metadata, _application_name


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


if __name__ == "__main__":
    unittest.main()
