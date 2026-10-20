from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

from app_activity.dashboard import HTML
from test_dashboard import ScriptParser


class HeaderParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, dict[str, str | None]]] = []
        self.controls: dict[str, tuple[str, dict[str, str | None], list[str | None]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if "id" in attributes:
            self.controls[attributes["id"]] = (
                tag, attributes, [attrs.get("class") or attrs.get("id") or tag for tag, attrs in self.stack]
            )
        if tag not in {"path", "rect", "circle", "input", "meta", "link"}:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class HeaderTests(unittest.TestCase):
    def test_navigation_and_segmented_periods_share_right_header_group(self) -> None:
        parser = HeaderParser()
        parser.feed(HTML)
        for control in ("previousPeriod", "label", "nextPeriod", "currentPeriod", "settings", "periods"):
            with self.subTest(control=control):
                self.assertIn("header-controls", parser.controls[control][2])
                self.assertIn("header", parser.controls[control][2])
        self.assertEqual(parser.controls["periods"][1]["aria-label"], "Report period")
        for control in ("previousPeriod", "nextPeriod"):
            self.assertEqual(parser.controls[control][0], "button")
            self.assertEqual(parser.controls[control][1]["type"], "button")
            self.assertIn("aria-label", parser.controls[control][1])
        self.assertEqual(parser.controls["label"][0], "span")
        self.assertEqual(parser.controls["label"][1]["role"], "status")
        self.assertIn("disabled", parser.controls["nextPeriod"][1])
        self.assertIn("disabled", parser.controls["currentPeriod"][1])

    def test_header_layout_supports_segments_wrapping_and_keyboard_focus(self) -> None:
        self.assertIn(".header-controls{display:flex;flex-direction:column;align-items:flex-end;", HTML)
        self.assertIn("#periods{display:flex;width:440px;max-width:100%;", HTML)
        self.assertIn("#periods button{flex:1;min-width:0;min-height:44px;", HTML)
        self.assertIn(".header-controls button:focus-visible", HTML)
        self.assertIn(".report-nav{display:flex;justify-content:flex-end;", HTML)
        mobile = HTML.split("@media(max-width:750px)", 1)[1].split("</style>", 1)[0]
        self.assertIn(".header-controls{width:100%}", mobile)
        self.assertIn(".report-label{flex:1 1 calc(100% - 104px);", mobile)
        self.assertIn(".settings[open]{width:100%}", mobile)
        self.assertIn(".settings-panel{position:static;width:100%;", mobile)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JavaScript tests")
    def test_period_navigation_behavior(self) -> None:
        parser = ScriptParser()
        parser.feed(HTML)
        parser.close()
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("dashboard_header_test.js"))],
            input=json.dumps(parser.scripts), text=True, encoding="utf-8",
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
