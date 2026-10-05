from __future__ import annotations

import unittest

from app_activity.dashboard import HTML


class DashboardTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
