from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from app_activity.dashboard import HTML
from app_activity.database import _period_bounds, _period_label
from test_dashboard import ScriptParser


def css_rule(selector: str, source: str = HTML) -> dict[str, str]:
    match = re.search(re.escape(selector) + r"\{([^{}]*)\}", source)
    if match is None:
        raise AssertionError(f"Missing CSS rule: {selector}")
    return dict(declaration.split(":", 1) for declaration in match[1].split(";") if declaration)


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
        for control in ("previousPeriod", "label", "nextPeriod", "currentPeriod"):
            self.assertIn("report-nav", parser.controls[control][2])
        for control in ("settings", "periods"):
            self.assertEqual(parser.controls[control][2][-1], "header-controls")
            self.assertNotIn("report-nav", parser.controls[control][2])

    def test_navigation_and_scope_stretch_in_the_same_shrinkable_grid_column(self) -> None:
        controls = css_rule(".header-controls")
        self.assertEqual(controls["display"], "grid")
        self.assertEqual(controls["grid-template-columns"], "minmax(0,1fr) auto")
        self.assertEqual(controls["grid-template-areas"], '"navigation settings" "periods ."')
        self.assertEqual(controls["min-width"], "0")
        for selector, area in ((".report-nav", "navigation"), ("#periods", "periods")):
            with self.subTest(selector=selector):
                rule = css_rule(selector)
                self.assertEqual(rule["grid-area"], area)
                self.assertEqual(rule["min-width"], "0")
                self.assertNotIn("width", rule, "Both rows must size from the shared grid track")
        self.assertEqual(css_rule(".settings")["grid-area"], "settings")
        self.assertEqual(css_rule(".report-label")["flex"], "1")
        self.assertEqual(css_rule(".report-label")["min-width"], "0")
        self.assertEqual(css_rule("#label")["overflow-wrap"], "anywhere")
        self.assertEqual(css_rule(".report-nav button")["flex"], "none")
        self.assertEqual(css_rule(".report-nav button")["min-width"], "44px")
        self.assertEqual(css_rule(".report-nav button")["min-height"], "44px")
        self.assertEqual(css_rule("#periods button")["flex"], "1")
        self.assertEqual(css_rule("#periods button")["min-width"], "0")
        self.assertEqual(css_rule("#periods button")["min-height"], "44px")
        self.assertIn(".header-controls button:focus-visible", HTML)

    def test_mobile_header_wraps_long_labels_and_expands_settings_inline(self) -> None:
        mobile = HTML.split("@media(max-width:750px)", 1)[1].split("</style>", 1)[0]
        controls = css_rule(".header-controls", mobile)
        self.assertEqual(controls["width"], "100%")
        self.assertEqual(controls["grid-template-columns"], "minmax(0,1fr)")
        self.assertEqual(controls["grid-template-areas"], '"navigation" "settings" "periods"')
        self.assertEqual(css_rule(".report-nav", mobile)["width"], "100%")
        self.assertEqual(css_rule(".report-nav", mobile)["flex-wrap"], "wrap")
        self.assertIn(".report-label{flex:1 1 calc(100% - 104px);", mobile)
        self.assertIn(".settings[open]{width:100%}", mobile)
        self.assertIn(".settings-panel{position:static;width:100%;", mobile)
        self.assertEqual(css_rule("#periods button", mobile)["padding"], "9px 4px")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JavaScript tests")
    def test_period_navigation_behavior(self) -> None:
        parser = ScriptParser()
        parser.feed(HTML)
        parser.close()
        reports = {}
        now = datetime(2026, 9, 30, 11, tzinfo=timezone.utc)
        for period in ("hour", "day", "week", "month", "year"):
            reports[period] = {}
            for offset in (0, -1, -2):
                start, end, _ = _period_bounds(period, now, offset)
                reports[period][offset] = {
                    "period": period, "label": _period_label(period, start, end),
                    "start": start.isoformat(), "end": end.isoformat(),
                    "active_seconds": 0, "idle_seconds": 0, "tracked_seconds": 0,
                    "apps": [], "timeline": [],
                }
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("dashboard_header_test.js"))],
            input=json.dumps({"scripts": parser.scripts, "reports": reports}),
            text=True, encoding="utf-8",
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
