from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import site_lib
from build import build_site as builder
from update import update_site


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 14, 12, 0, tzinfo=tz)


def source(day: str, name: str = "announcement") -> dict[str, str]:
    return {
        "title": name,
        "url": f"https://example.com/{name}",
        "description": "An announcement about an upcoming event.",
        "published_at": day,
    }


class PublicationDateTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(patch.object(update_site, "datetime", FixedDateTime))
        self.stack.enter_context(patch.object(site_lib, "datetime", FixedDateTime))
        self.stack.enter_context(patch.object(
            update_site.requests.sessions.Session, "request",
            side_effect=AssertionError("Date regression tests must not use the network"),
        ))

    def test_date_window_includes_today_but_excludes_future_old_and_unknown(self):
        for day, expected in (
            ("2026-09-14", True), ("2026-09-13", True),
            ("09/14/2026", True), ("2026-09-14T10:00:00Z", True),
            ("2026-09-30", False), ("2026-09-15", False),
            ("2026-09-01", False), ("2026-08-31", False),
            ("", False), ("unknown", False), ("2026-02-30", False),
            ("Event 2026-09-13", False),
        ):
            with self.subTest(day=day):
                self.assertEqual(update_site.is_strictly_after(day, "09/01/2026"), expected)
        self.assertFalse(update_site.is_strictly_after("2026-09-14", "invalid"))
        self.assertEqual(update_site.normalize_source_date("2024-02-29"), "2024-02-29")

    def test_prompt_distinguishes_publication_and_event_dates(self):
        request = update_site.build_source_curation_request("Test", "09/01/2026", "Research.", 5)
        self.assertIn("on or before 2026-09-14", request)
        self.assertIn("Never use an event/meeting date", request)
        self.assertIn("Do not guess a publication date", request)

    def test_rejects_bad_research_dates_before_fetching_pages(self):
        sources = [source(day, str(index)) for index, day in enumerate(
            ("2026-09-30", "", "invalid", "2026-02-30")
        )]
        with patch.object(update_site, "http_ok") as fetch:
            self.assertEqual(update_site.validate_item_sources(sources, 8), [])
            fetch.assert_not_called()

    def test_summary_cannot_replace_approved_dates_or_titles(self):
        approved = source("2026-09-13")
        changed = {**approved, "published_at": "2026-09-30", "title": "Wrong title"}
        self.assertEqual(
            update_site.validate_item_sources([changed], 4, {approved["url"]: approved}),
            [approved],
        )
        undated = {**approved, "published_at": ""}
        self.assertEqual(update_site.validate_item_sources([changed], 4, {approved["url"]: undated}), [])
        self.assertEqual(update_site.validate_item_sources([source("2026-09-13", "unapproved")], 4,
                                                        {approved["url"]: approved}), [])

    def test_post_metadata_and_persistence_fail_closed(self):
        site_lib.validate_post_dates([{"published_at": "2026-09-14", "updated_at": "2026-09-14"}])
        for bad_post in (
            {"published_at": "2026-09-30"}, {"published_at": ""},
            {"published_at": "2026-02-30"}, {"published_at": "09/14/2026"},
            {"published_at": "2026-09-14", "updated_at": "2026-09-15"},
            {"published_at": "2026-09-14", "created_at": "2026-09-15"},
            {"published_at": "2026-09-14", "sources": [source("2026-09-30")]},
        ):
            with self.subTest(post=bad_post), patch.object(update_site, "write_json") as write:
                with self.assertRaises(ValueError):
                    update_site.persist_post({**bad_post, "slug": "test-post"})
                write.assert_not_called()

    def test_build_rejects_future_post_before_cleaning_dist(self):
        with (
            patch.object(builder, "load_categories", return_value=[]),
            patch.object(builder, "load_posts", return_value=[
                {"slug": "future", "published_at": "2026-09-30"}
            ]),
            patch.object(builder.shutil, "rmtree") as cleanup,
        ):
            with self.assertRaisesRegex(ValueError, "future published_at"):
                builder.build_site()
            cleanup.assert_not_called()

    def generate_fixture(self, candidates, *, resumed):
        temp_dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        posts_dir = temp_dir / "posts"
        log_path = temp_dir / "logs" / "test.jsonl"
        checkpoint = log_path.parent / "pending" / "test.json"
        config = {
            "api_key": "test-key", "log_path": log_path,
            "items_per_topic": 16, "source_target": 1,
        }
        prompts = {
            "prompt_guide": "test",
            "topics": [{"Topic": "Test", "LastDate": "09/01/2026"}],
        }
        for name, value in (
            ("load_prompt_config", prompts), ("load_categories", []), ("load_posts", []),
            ("expand_topic_prompt", "test"), ("today_prompt_date", "09/14/2026"),
            ("http_ok", True), ("fetch_title_desc", ("", "")),
        ):
            self.stack.enter_context(patch.object(update_site, name, return_value=value))
        self.stack.enter_context(patch.dict(update_site.SITE_SETTINGS, {"posts_dir": posts_dir}))
        self.stack.enter_context(patch.object(update_site, "append_log"))
        self.stack.enter_context(patch.object(update_site, "mark_topic_used"))
        research = self.stack.enter_context(patch.object(
            update_site, "call_openai_curated_sources", return_value={"sources": candidates}
        ))
        # Simulate the summarizer substituting the event date for every source date.
        summaries = self.stack.enter_context(patch.object(
            update_site, "call_openai_topic_batch", return_value={"items": [
                {
                    "title": item["title"], "summary": "An event is scheduled for September 30, 2026.",
                    "sources": [{**item, "published_at": "2026-09-30"}],
                }
                for item in candidates
            ]}
        ))
        if resumed:
            update_site.save_research_checkpoint(checkpoint, "Test", "09/01/2026", candidates)
        paths = update_site.generate_posts_with_settings(config)
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths], research, summaries, checkpoint

    def test_fresh_and_resumed_research_exclude_future_dates(self):
        for resumed in (False, True):
            with self.subTest(resumed=resumed), ExitStack() as local_stack:
                # Keep fixture patches scoped to this iteration.
                outer_stack, self.stack = self.stack, local_stack
                try:
                    posts, research, summaries, checkpoint = self.generate_fixture(
                        [source("2026-09-30", "future"), source("2026-09-13", "valid")],
                        resumed=resumed,
                    )
                    self.assertEqual(len(posts), 1)
                    self.assertEqual(posts[0]["title"], "valid")
                    self.assertEqual(posts[0]["published_at"], "2026-09-13")
                    self.assertEqual(posts[0]["sources"][0]["published_at"], "2026-09-13")
                    self.assertEqual(posts[0]["created_at"], "2026-09-14")
                    self.assertIn("September 30, 2026", posts[0]["summary"])
                    self.assertEqual(research.call_count, 0 if resumed else 1)
                    summaries.assert_called_once()
                    self.assertFalse(checkpoint.exists())
                finally:
                    self.stack = outer_stack

    def test_future_only_checkpoint_does_not_trigger_paid_summary(self):
        posts, research, summaries, checkpoint = self.generate_fixture(
            [source("2026-09-30")], resumed=True
        )
        self.assertEqual(posts, [])
        research.assert_not_called()
        summaries.assert_not_called()
        self.assertFalse(checkpoint.exists())


def verify_publication_dates() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PublicationDateTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Publication-date regression tests failed.")


if __name__ == "__main__":
    verify_publication_dates()
