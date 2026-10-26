from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build import build_site as builder


class FeedTextTests(unittest.TestCase):
    def feed(self, title: str, summary: str):
        return ET.fromstring(builder.build_rss([{
            "title": title,
            "summary": summary,
            "slug": "test-post",
            "published_at": "2026-10-20",
        }]))

    def test_scraped_control_characters_do_not_break_rss(self):
        invalid = "\x00\x01\x08\x0b\x0c\x0e\x1f\ud800\udfff\ufffe\uffff"
        branding = {"site_name": "Pub\x00lic news", "description": "News\x0b & policy"}
        with patch.dict(builder.SITE_COPY, {"branding": branding}):
            feed = self.feed("Agen\x00cy & public <oversight>", "Saved" + invalid + " report")
        self.assertEqual(feed.findtext("channel/title"), "Public news")
        self.assertEqual(feed.findtext("channel/description"), "News & policy")
        self.assertEqual(feed.findtext("channel/item/title"), "Agency & public <oversight>")
        self.assertEqual(feed.findtext("channel/item/description"), "Saved report")

    def test_feed_preserves_unicode_whitespace_and_literal_entities(self):
        title = 'Café — 公共政策 🏛️ & "oversight" <today>'
        summary = "First\tline\nSecond\r\nline with literal &amp; and &#0;"
        feed = self.feed(title, summary)
        self.assertEqual(feed.findtext("channel/item/title"), title)
        self.assertEqual(feed.findtext("channel/item/description"), summary.replace("\r\n", "\n"))


def verify_feed_text() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FeedTextTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Feed text regression tests failed.")


if __name__ == "__main__":
    verify_feed_text()
