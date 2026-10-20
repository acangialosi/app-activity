from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser

from app_activity.dashboard import HTML


class FilterStatusParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[dict[str, str | None]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id") == "filterStatus":
            self.statuses.append(attributes)


class DashboardFilterStatusTests(unittest.TestCase):
    def test_filter_status_remains_available_to_assistive_technology(self) -> None:
        parser = FilterStatusParser()
        parser.feed(HTML)
        parser.close()
        self.assertEqual(len(parser.statuses), 1)
        status = parser.statuses[0]
        self.assertEqual(status["role"], "status")
        self.assertIn("filter-status", status["class"].split())
        self.assertNotIn("hidden", status)
        self.assertNotEqual(status.get("aria-hidden"), "true")

    def test_filter_status_is_always_visually_hidden_without_taking_layout_space(self) -> None:
        stylesheet = HTML.split("<style>", 1)[1].split("</style>", 1)[0]
        rules = re.findall(r"([^{}]+)\{([^{}]*)\}", stylesheet)
        status_rules = [
            (selector.strip(), declarations)
            for selector, declarations in rules
            if ".filter-status" in selector
        ]
        self.assertEqual(len(status_rules), 1)
        selector, declarations = status_rules[0]
        self.assertEqual(selector, ".filter-status")
        styles = dict(
            declaration.split(":", 1)
            for declaration in declarations.split(";")
            if declaration
        )
        for name, value in {
            "position": "absolute",
            "width": "1px",
            "height": "1px",
            "padding": "0",
            "margin": "-1px",
            "overflow": "hidden",
            "clip-path": "inset(50%)",
            "white-space": "nowrap",
            "border": "0",
        }.items():
            with self.subTest(property=name):
                self.assertEqual(styles.get(name), value)
        self.assertNotIn("display", styles)
        self.assertNotIn("visibility", styles)
