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


if __name__ == "__main__":
    unittest.main()
