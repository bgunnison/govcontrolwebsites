from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import site_lib
from build import build_site as builder
from update import update_site


class PostPathTests(unittest.TestCase):
    def test_ordinary_titles_keep_their_existing_url_format(self):
        self.assertEqual(site_lib.unique_slug("Public Policy", set()), "public-policy")
        self.assertEqual(site_lib.unique_slug("Public Policy", {"public-policy"}), "public-policy-2")
        self.assertEqual(site_lib.unique_slug("公共", set()), "item")

    def test_long_titles_with_same_prefix_have_distinct_stable_slugs(self):
        prefix = "Detailed regulation about public policy and institutional oversight " * 8
        first = site_lib.unique_slug(prefix + "first", set())
        second = site_lib.unique_slug(prefix + "second", set())
        self.assertNotEqual(first, second)
        self.assertEqual(first, site_lib.unique_slug(prefix + "first", set()))
        for slug in (first, second):
            self.assertLessEqual(len(slug.encode("ascii")), site_lib.MAX_POST_SLUG_LENGTH)
            self.assertRegex(slug, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    def test_boundary_lengths_and_collision_suffixes_stay_bounded(self):
        for length in (119, 120, 121, 500):
            existing = set()
            for _ in range(12):
                slug = site_lib.unique_slug("a" * length, existing)
                self.assertNotIn(slug, existing)
                self.assertLessEqual(len(slug), site_lib.MAX_POST_SLUG_LENGTH)
                existing.add(slug)

    def test_long_title_can_be_saved_and_built_without_changing_the_title(self):
        title = ("Commission implementing regulation establishing detailed institutional rules " * 8).strip()
        slug = site_lib.unique_slug(title, set())
        post = {"slug": slug, "title": title, "published_at": "2026-11-11", "summary": "A policy update."}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(update_site.SITE_SETTINGS, {"posts_dir": root / "posts"}):
                saved = update_site.persist_post(post)
            self.assertEqual(json.loads(saved.read_text(encoding="utf-8")), post)
            self.assertLessEqual(len(saved.name.encode("utf-8")), 255)
            url = builder.post_url(slug)
            relative = builder.relative_dir_from_url(url)
            builder.write_page(root / "dist", relative, "<h1>Policy</h1>")
            self.assertTrue((root / "dist" / relative / "index.html").is_file())
            feed = ET.fromstring(builder.build_rss([post]))
            self.assertEqual(feed.findtext("channel/item/title"), title)
            self.assertTrue(feed.findtext("channel/item/link").endswith(url))


def verify_post_paths() -> None:
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PostPathTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Post path regression tests failed.")


if __name__ == "__main__":
    verify_post_paths()
