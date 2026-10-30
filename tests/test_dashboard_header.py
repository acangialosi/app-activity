from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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


def chromium_browser() -> str | None:
    for name in ("msedge", "chromium", "chromium-browser", "google-chrome"):
        if browser := shutil.which(name):
            return browser
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        if directory := os.environ.get(variable):
            browser = Path(directory) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if browser.is_file():
                return str(browser)
    return None


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
    @unittest.skipUnless(chromium_browser(), "A Chromium browser is required for layout tests")
    def test_navigation_width_does_not_depend_on_report_label(self) -> None:
        labels = {}
        for period in ("hour", "day", "week", "month", "year"):
            labels[period] = []
            for now in (
                datetime(2026, 10, 22, 11, tzinfo=timezone.utc),
                datetime(2026, 10, 23, 12, tzinfo=timezone.utc),
                datetime(2026, 9, 30, 23, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 0, tzinfo=timezone.utc),
            ):
                start, end, _ = _period_bounds(period, now, 0)
                labels[period].append(_period_label(period, start, end))
        script = r"""
const page = PAGE, labels = LABELS;
const results = [];
async function check() {
    for (const width of [1280, 900, 751, 750, 375, 320]) {
        const frame = document.createElement("iframe");
        frame.style.cssText = `width:${width}px;height:900px;border:0`;
        frame.setAttribute("sandbox", "allow-same-origin");
        const ready = new Promise(resolve => frame.onload = resolve);
        frame.srcdoc = page;
        document.body.appendChild(frame);
        await ready;
        const doc = frame.contentDocument;
        const periods = doc.getElementById("periods");
        for (const name of Object.keys(labels)) {
            const button = doc.createElement("button");
            button.textContent = name[0].toUpperCase() + name.slice(1);
            periods.appendChild(button);
        }
        for (const [period, values] of Object.entries(labels)) {
            doc.getElementById("currentPeriod").textContent =
                period === "day" ? "Today" : `This ${period}`;
            let baseline;
            for (const value of ["Loading...", ...values]) {
                doc.getElementById("label").textContent = value;
                const bounds = [...doc.querySelectorAll(
                    ".header-controls,.report-nav,.report-label,#previousPeriod,#nextPeriod,#currentPeriod,#settingsSummary,#periods,#periods button"
                )].map(element => {
                    const rect = element.getBoundingClientRect();
                    return [rect.x, rect.width];
                });
                if (!baseline) baseline = bounds;
                if (JSON.stringify(bounds) !== JSON.stringify(baseline))
                    throw new Error(`${width}px ${period}: controls shifted for ${value}: ${JSON.stringify(bounds)} != ${JSON.stringify(baseline)}`);
                if (doc.documentElement.scrollWidth > width)
                    throw new Error(`${width}px ${period}: horizontal overflow`);
            }
        }
        frame.remove();
        results.push(width);
    }
}
check().then(() => document.getElementById("result").textContent = JSON.stringify(results))
    .catch(error => document.getElementById("result").textContent = error.message);
"""
        page = json.dumps(HTML).replace("<", r"\u003c")
        script = script.replace("PAGE", page).replace("LABELS", json.dumps(labels))
        with tempfile.TemporaryDirectory(prefix="app-activity-layout-") as directory:
            fixture = Path(directory) / "layout.html"
            fixture.write_text(
                '<!doctype html><pre id="result">pending</pre><script>' + script + "</script>",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    chromium_browser(), "--headless", "--disable-gpu",
                    "--no-first-run", "--disable-extensions",
                    f"--user-data-dir={Path(directory) / 'profile'}",
                    "--dump-dom", "--virtual-time-budget=10000", fixture.as_uri(),
                ],
                capture_output=True, text=True, encoding="utf-8", timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            match = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.DOTALL)
            self.assertIsNotNone(match, result.stderr)
            self.assertEqual(match[1], "[1280,900,751,750,375,320]")

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
