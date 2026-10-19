from __future__ import annotations

import unittest
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from app_activity.dashboard import HTML


class FeedbackParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_settings = False
        self.in_feedback = False
        self.links: list[dict[str, str | None]] = []
        self.labels: list[str] = []
        self.in_link = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "details" and attributes.get("id") == "settings":
            self.in_settings = True
        if self.in_settings and tag == "section":
            self.in_feedback = attributes.get("aria-labelledby") == "feedbackHeading"
        if self.in_feedback and tag == "a":
            self.links.append(attributes)
            self.labels.append("")
            self.in_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "details":
            self.in_settings = False
        if tag == "section":
            self.in_feedback = False
        if tag == "a":
            self.in_link = False

    def handle_data(self, data: str) -> None:
        if self.in_link:
            self.labels[-1] += data


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
                self.assertNotIn("aria-describedby", link)
                self.assertNotIn("onclick", link)

    def test_feedback_is_in_settings_without_obsolete_footer_or_warning(self) -> None:
        self.assertIn('<h2 id="feedbackHeading">Feedback</h2>', HTML)
        self.assertNotIn("<footer", HTML)
        self.assertNotIn("feedback-footer", HTML)
        self.assertNotIn("feedbackHelp", HTML)
        self.assertNotIn("Opens GitHub in a new tab. No activity data is attached.", HTML)
        self.assertIn(
            ".feedback-links{display:flex;flex-direction:column;", HTML
        )
        self.assertIn(
            "grid-template-rows:auto auto auto minmax(0,1fr);", HTML
        )
        self.assertIn(".settings-panel :focus-visible{outline:2px solid", HTML)


if __name__ == "__main__":
    unittest.main()
