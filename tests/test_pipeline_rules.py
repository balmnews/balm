"""Unit tests for pipeline.py's pure editorial rules. No network, no API calls.

Run with:  python -m pytest tests/   or   python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from pipeline import (  # noqa: E402
    attach_sources,
    edition_review_stats,
    is_top_story_eligible,
    outlet_family,
    top_story_cap,
)


def _raw(source, url, title="t"):
    return {"source": source, "url": url, "title": title}


def _attached(*raw_articles):
    """Run attach_sources on one cluster and return the resulting article."""
    article = {"cluster_id": 1}
    attach_sources([article], [list(raw_articles)])
    return article


class TopStoryCap(unittest.TestCase):
    def test_cap(self):
        for visible, cap in [(0, 0), (5, 1), (7, 1), (8, 2), (11, 2),
                             (12, 3), (16, 4), (30, 4)]:
            with self.subTest(visible=visible):
                self.assertEqual(top_story_cap(visible), cap)


class OutletFamily(unittest.TestCase):
    def test_siblings_collapse(self):
        for name in ("Fox News", "Fox News World", "Fox News Politics"):
            self.assertEqual(outlet_family(name), "Fox News")
        for name in ("BBC News", "BBC World", "BBC"):
            self.assertEqual(outlet_family(name), "BBC")
        for name in ("The Guardian", "Guardian Environment"):
            self.assertEqual(outlet_family(name), "The Guardian")

    def test_unrelated_name_maps_to_itself(self):
        self.assertEqual(outlet_family("NPR News"), "NPR News")


class SourceDedupe(unittest.TestCase):
    def test_same_url_under_two_source_names(self):
        a = _attached(_raw("BBC News", "https://bbc.co.uk/news/x"),
                      _raw("BBC World", "https://bbc.co.uk/news/x"))
        self.assertEqual(len(a["sources"]), 1)
        self.assertEqual(a["sources"][0]["source"], "BBC News")  # first kept

    def test_same_url_different_query_strings(self):
        a = _attached(_raw("NPR News", "https://npr.org/a?utm_source=rss"),
                      _raw("NPR News", "https://npr.org/a?utm_source=feed&x=1"),
                      _raw("PBS NewsHour", "https://pbs.org/b"))
        self.assertEqual([s["url"] for s in a["sources"]],
                         ["https://npr.org/a?utm_source=rss", "https://pbs.org/b"])


class Eligibility(unittest.TestCase):
    def test_single_outlet(self):
        a = _attached(_raw("NPR News", "https://npr.org/a"))
        self.assertEqual(a["outlet_count"], 1)
        self.assertFalse(is_top_story_eligible(a))

    def test_two_sibling_feeds_count_once(self):
        a = _attached(_raw("Fox News", "https://fox.com/a"),
                      _raw("Fox News Politics", "https://fox.com/b"))
        self.assertEqual(a["outlet_count"], 1)
        self.assertFalse(is_top_story_eligible(a))

    def test_two_distinct_outlets(self):
        a = _attached(_raw("Fox News", "https://fox.com/a"),
                      _raw("The Guardian", "https://theguardian.com/b"))
        self.assertEqual(a["outlet_count"], 2)
        self.assertTrue(is_top_story_eligible(a))

    def test_single_official_source(self):
        a = _attached(_raw("Federal Reserve", "https://federalreserve.gov/a"))
        self.assertTrue(a["has_official_source"])
        self.assertTrue(is_top_story_eligible(a))

    def test_difficult_news_never_eligible(self):
        a = _attached(_raw("Fox News", "https://fox.com/a"),
                      _raw("NPR News", "https://npr.org/b"))
        a["isDifficult"] = True
        self.assertFalse(is_top_story_eligible(a))


class TopStoriesSkipsCall(unittest.TestCase):
    def test_small_edition_hides_panel_without_calling_claude(self):
        articles = [{"story_id": f"story_{i}", "outlet_count": 3} for i in range(7)]
        with mock.patch.object(pipeline, "Anthropic") as client:
            self.assertEqual(pipeline.select_top_stories(articles, "key"), [])
            client.assert_not_called()

    def test_answer_truncated_to_cap_and_filtered_to_eligible(self):
        # 12 visible → cap 3. story_0 is single-outlet, so ineligible.
        articles = [{"story_id": f"story_{i}", "outlet_count": 1 if i == 0 else 2}
                    for i in range(12)]
        reply = ('{"top_stories": [{"story_id": "story_0"}, {"story_id": "story_1"},'
                 ' {"story_id": "story_1"}, {"story_id": "story_2"},'
                 ' {"story_id": "story_3"}, {"story_id": "story_4"}]}')
        with mock.patch.object(pipeline, "Anthropic") as client:
            client.return_value.messages.create.return_value.content = [mock.Mock(text=reply)]
            top = pipeline.select_top_stories(articles, "key")
            prompt = client.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual([t["story_id"] for t in top], ["story_1", "story_2", "story_3"])
        self.assertIn("between 2 and 3", prompt)
        self.assertNotIn("story_0 ", prompt)


class EditionReview(unittest.TestCase):
    def test_stats(self):
        articles = [
            {"story_id": "story_0", "category": "ECONOMY", "outlet_count": 1,
             "brief_summary": "word " * 41},
            {"story_id": "story_1", "category": "ECONOMY", "outlet_count": 2,
             "brief_summary": "short"},
            {"story_id": "story_2", "category": "DIFFICULT NEWS", "outlet_count": 1,
             "isDifficult": True, "brief_summary": "short"},
        ]
        stats = edition_review_stats(articles, [{"story_id": "story_1"}])
        self.assertEqual(stats["story_count"], 3)
        self.assertEqual(stats["visible_count"], 2)
        self.assertEqual(stats["difficult_count"], 1)
        self.assertEqual(stats["top_story_count"], 1)
        self.assertEqual(stats["top_story_cap"], 0)
        self.assertEqual(stats["single_outlet_count"], 2)
        self.assertEqual(stats["long_briefs"], {"story_0": 41})
        self.assertEqual(stats["single_story_categories"], ["DIFFICULT NEWS"])

    def test_review_failure_never_raises(self):
        with mock.patch.object(pipeline, "review_edition", side_effect=RuntimeError("boom")):
            pipeline.run_edition_review([], [], None, "2026-10-07", "am", "key")


if __name__ == "__main__":
    unittest.main()
