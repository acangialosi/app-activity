from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

from app_activity.dashboard import HTML


class ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._in_script = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "script":
            self._in_script = True
            self.scripts.append("")

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.scripts[-1] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False


class ScriptParserTests(unittest.TestCase):
    def test_extracts_case_variations_and_script_attributes(self) -> None:
        for opening, closing in (
            ("<script>", "</script>"),
            ("<SCRIPT>", "</SCRIPT>"),
            ('<ScRiPt type="text/javascript" data-label="a > b">', "</sCrIpT >"),
        ):
            with self.subTest(opening=opening):
                parser = ScriptParser()
                parser.feed(f"{opening}const value = 42;{closing}")
                parser.close()
                self.assertEqual(parser.scripts, ["const value = 42;"])

    def test_preserves_script_contents_and_document_order(self) -> None:
        first = 'if (1 < 2) { const text = "&amp;"; }\n'
        second = 'const text = "<div>not HTML</div>";'
        parser = ScriptParser()
        parser.feed(f"<script>{first}</script><p>Not JavaScript</p>")
        parser.feed(f"<script>{second}</script>")
        parser.close()
        self.assertEqual(parser.scripts, [first, second])

    def test_ignores_commented_script_tags(self) -> None:
        parser = ScriptParser()
        parser.feed("<!-- <script>not executable</script> --><p>Text</p>")
        parser.close()
        self.assertEqual(parser.scripts, [])


class DashboardTests(unittest.TestCase):
    def test_desktop_layout_scales_to_viewport_with_720_pixel_minimum(self) -> None:
        self.assertIn("@media(min-width:751px)", HTML)
        self.assertIn("height:max(720px,100dvh)", HTML)
        self.assertIn("grid-template-rows:auto auto auto minmax(0,1fr)", HTML)
        self.assertIn('class="card applications-card"', HTML)
        self.assertIn("#apps{flex:1;min-height:0;overflow-y:auto", HTML)
        self.assertIn("scrollbar-gutter:stable", HTML)
        self.assertIn("@media(max-width:750px)", HTML)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JavaScript tests")
    def test_application_filter_behavior(self) -> None:
        parser = ScriptParser()
        parser.feed(HTML)
        parser.close()
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("dashboard_filter_test.js"))],
            input=json.dumps(parser.scripts),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_application_list_controls_chart_filter(self) -> None:
        self.assertNotIn("applicationSelect", HTML)
        self.assertIn('class="app-select" type="button"', HTML)
        self.assertIn('aria-controls="timeline"', HTML)
        self.assertIn('aria-pressed="${x.name===selectedApplication}"', HTML)
        self.assertIn('id="showAllApplications"', HTML)
        self.assertIn('<details class="app-details"><summary>', HTML)

    def test_current_period_button_resets_offset(self) -> None:
        self.assertIn('id="currentPeriod"', HTML)
        self.assertIn(">Now</button>", HTML)
        self.assertIn("currentPeriod.disabled=periodOffset===0", HTML)
        self.assertIn(
            "currentPeriod.onclick=()=>{if(periodOffset!==0)"
            "{periodOffset=0;load()}}",
            HTML,
        )

    def test_chart_has_mode_aware_y_axis(self) -> None:
        self.assertIn('class="y-axis" id="yAxis"', HTML)
        self.assertIn("function timeAxis(dataMax,capacity)", HTML)
        self.assertIn("max<=capacity", HTML)
        self.assertIn("x.seconds/axis.max*100", HTML)
        self.assertIn('"--grid-step"', HTML)
        self.assertIn("function fixedTimeAxis(capacity,intervals)", HTML)
        self.assertIn('d.period==="hour"?5:d.period==="day"?4:0', HTML)

    def test_truncated_application_names_receive_tooltips(self) -> None:
        self.assertIn('class="app-name" data-app-index="${i}"', HTML)
        self.assertIn("label.scrollWidth>label.clientWidth?app.name", HTML)
        self.assertIn('addEventListener("resize",updateAppTooltips)', HTML)

    def test_theme_can_follow_system_or_use_light_and_dark_modes(self) -> None:
        self.assertIn('id="themeSelect"', HTML)
        self.assertIn('<option value="system">System</option>', HTML)
        self.assertIn('<option value="light">Light</option>', HTML)
        self.assertIn('<option value="dark">Dark</option>', HTML)
        self.assertIn("@media(prefers-color-scheme:dark)", HTML)
        self.assertIn('localStorage.getItem("appActivityTheme")', HTML)
        self.assertIn('localStorage.setItem("appActivityTheme",theme)', HTML)
        self.assertIn("delete document.documentElement.dataset.theme", HTML)


if __name__ == "__main__":
    unittest.main()
