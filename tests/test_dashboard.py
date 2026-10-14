from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from app_activity.dashboard import HTML


class DashboardTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JavaScript tests")
    def test_application_filter_behavior(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("dashboard_filter_test.js"))],
            input=HTML,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_chart_has_labeled_application_filter(self) -> None:
        self.assertIn('for="applicationSelect"', HTML)
        self.assertIn('id="applicationSelect" aria-describedby="chartHelp"', HTML)
        self.assertIn('<option value="">All applications</option>', HTML)

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
