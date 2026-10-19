from __future__ import annotations

import unittest
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from app_activity.dashboard import HTML


class FeedbackParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_footer = False
        self.links: list[dict[str, str | None]] = []
        self.labels: list[str] = []
        self.in_link = False
        self.help_text = ""
        self.in_help = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "footer" and attributes.get("class") == "feedback-footer":
            self.in_footer = True
        if self.in_footer and tag == "a":
            self.links.append(attributes)
            self.labels.append("")
            self.in_link = True
        if self.in_footer and attributes.get("id") == "feedbackHelp":
            self.in_help = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "footer":
            self.in_footer = False
        if tag == "a":
            self.in_link = False
        if tag == "p":
            self.in_help = False

    def handle_data(self, data: str) -> None:
        if self.in_link:
            self.labels[-1] += data
        if self.in_help:
            self.help_text += data


class DashboardFeedbackTests(unittest.TestCase):
    def test_feedback_links_prefill_only_static_issue_fields(self) -> None:
        parser = FeedbackParser()
        parser.feed(HTML)
        parser.close()
        self.assertEqual(parser.labels, ["Report a problem", "Suggest a feature"])
        expected = [
            (
                "[Problem] Briefly describe the problem",
                "## What happened?\nDescribe the problem.\n\n"
                "## Steps to reproduce\n1. \n\n"
                "## Expected behavior\nWhat did you expect?\n\n"
                "## Environment\n"
                "App Activity version and Windows version (if known):\n\n"
                "Please omit private information, window titles, and activity history.\n",
            ),
            (
                "[Feature] Briefly describe the idea",
                "## Feature idea\nWhat would you like App Activity to do?\n\n"
                "## Why would this help?\nDescribe your use case.\n\n"
                "## Alternatives considered\nOptional: describe other approaches.\n\n"
                "Please omit private information, window titles, and activity history.\n",
            ),
        ]
        self.assertEqual(len(parser.links), len(expected))
        for link, (title, body) in zip(parser.links, expected):
            with self.subTest(title=title):
                href = link["href"]
                self.assertIsInstance(href, str)
                url = urlsplit(href)
                self.assertEqual(url.scheme, "https")
                self.assertEqual(url.netloc, "github.com")
                self.assertEqual(url.path, "/acangialosi/app-activity/issues/new")
                self.assertEqual(url.fragment, "")
                self.assertEqual(
                    parse_qs(url.query, strict_parsing=True),
                    {"title": [title], "body": [body]},
                )
                self.assertEqual(
                    url.query, urlencode({"title": title, "body": body}, quote_via=quote)
                )
                self.assertEqual(link["target"], "_blank")
                self.assertEqual(set(link["rel"].split()), {"noopener", "noreferrer"})
                self.assertEqual(link["aria-describedby"], "feedbackHelp")
                self.assertNotIn("onclick", link)
        self.assertEqual(
            parser.help_text,
            "Opens GitHub in a new tab. No activity data is attached.",
        )

    def test_feedback_footer_wraps_and_has_its_own_desktop_grid_row(self) -> None:
        self.assertGreater(HTML.index("<footer "), HTML.index('<section class="grid">'))
        self.assertLess(HTML.index("</footer>"), HTML.index("</main>"))
        self.assertIn(
            ".feedback-footer{display:flex;align-items:center;flex-wrap:wrap;", HTML
        )
        self.assertIn(
            "grid-template-rows:auto auto auto auto minmax(0,1fr) auto;", HTML
        )
        self.assertIn(".feedback-footer a:focus-visible{outline:2px solid", HTML)


if __name__ == "__main__":
    unittest.main()
