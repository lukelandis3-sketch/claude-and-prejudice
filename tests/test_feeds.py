"""Feed bookkeeping, with the network stubbed out."""

import os
import sys
import types
import unittest

from support import IsolatedStateCase


class FeedSeenTest(IsolatedStateCase):
    """Regression: `seen` was sliced as a set, so an arbitrary 500 links survived."""

    def setUp(self):
        super().setUp()
        import importlib
        import thinking_book
        importlib.reload(thinking_book)
        self.tb = thinking_book

        # Stub the two modules refresh_feeds imports lazily.
        self.entries = [{"title": "e%d" % n, "link": "https://x.test/%d" % n}
                        for n in range(3)]
        feed_stub = types.ModuleType("feed")
        feed_stub.load = lambda url: ({"title": "Stub Feed"}, self.entries)
        article_stub = types.ModuleType("article")
        article_stub.load = lambda url: (
            {"title": url, "kind": "article", "source": url},
            "A sentence of prose long enough to survive chunking.",
        )
        self._saved = {name: sys.modules.get(name) for name in ("feed", "article")}
        sys.modules["feed"] = feed_stub
        sys.modules["article"] = article_stub

    def tearDown(self):
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        super().tearDown()

    def _write_feed(self, seen):
        self.tb.save_feeds({"feeds": [
            {"url": "https://x.test/feed", "title": "Stub", "last_checked": 0, "seen": seen}
        ]})

    def test_truncation_keeps_the_most_recent_links(self):
        old = ["https://x.test/old%d" % n for n in range(600)]
        self._write_feed(old)
        self.tb.refresh_feeds(force=True)

        seen = self.tb.load_feeds()["feeds"][0]["seen"]
        self.assertEqual(len(seen), 500)
        # The newly fetched links are the most recent, so they must survive.
        for entry in self.entries:
            self.assertIn(entry["link"], seen)
        # And the oldest must be the ones dropped.
        self.assertNotIn("https://x.test/old0", seen)
        self.assertIn("https://x.test/old599", seen)

    def test_already_seen_links_are_not_refetched(self):
        self._write_feed([e["link"] for e in self.entries])
        added = self.tb.refresh_feeds(force=True)
        self.assertEqual(added, 0)

    def test_seen_has_no_duplicates_after_a_refresh(self):
        self._write_feed([self.entries[0]["link"]])
        self.tb.refresh_feeds(force=True)
        seen = self.tb.load_feeds()["feeds"][0]["seen"]
        self.assertEqual(len(seen), len(set(seen)))

    def test_multiple_new_articles_publish_one_stream_generation(self):
        self._write_feed([])
        original = self.tb.tbstate.rebuild_stream
        calls = []

        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        self.tb.tbstate.rebuild_stream = counted
        try:
            added = self.tb.refresh_feeds(force=True)
        finally:
            self.tb.tbstate.rebuild_stream = original
        self.assertEqual(added, 3)
        self.assertEqual(len(calls), 1)

    def test_duplicate_article_links_are_staged_once(self):
        self.entries = [self.entries[0], dict(self.entries[0])]
        self._write_feed([])
        added = self.tb.refresh_feeds(force=True)
        self.assertEqual(added, 1)
        self.assertEqual(self.tb.load_feeds()["feeds"][0]["seen"],
                         [self.entries[0]["link"]])

    def test_failed_install_is_not_marked_seen_so_it_can_retry(self):
        self._write_feed([])
        original = self.tb._install_prepared
        self.tb._install_prepared = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))
        try:
            self.tb.refresh_feeds(force=True)
        finally:
            self.tb._install_prepared = original
        seen = self.tb.load_feeds()["feeds"][0]["seen"]
        self.assertNotIn(self.entries[0]["link"], seen)

    def test_transient_article_failure_is_retried_on_the_next_refresh(self):
        self.entries = [self.entries[0]]
        self._write_feed([])
        article = sys.modules["article"]
        succeeds = article.load
        calls = {"count": 0}

        def flaky(url):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("temporary timeout")
            return succeeds(url)

        article.load = flaky

        self.assertEqual(self.tb.refresh_feeds(force=True), 0)
        self.assertNotIn(
            self.entries[0]["link"], self.tb.load_feeds()["feeds"][0]["seen"])
        self.assertEqual(self.tb.refresh_feeds(force=True), 1)
        self.assertIn(
            self.entries[0]["link"], self.tb.load_feeds()["feeds"][0]["seen"])

    def test_one_failed_article_does_not_consume_the_success_budget(self):
        self.entries = [
            {"title": "e%d" % n, "link": "https://x.test/%d" % n}
            for n in range(4)
        ]
        self._write_feed([])
        article = sys.modules["article"]
        succeeds = article.load

        def first_is_broken(url):
            if url == self.entries[0]["link"]:
                raise OSError("permanently unavailable")
            return succeeds(url)

        article.load = first_is_broken

        self.assertEqual(self.tb.refresh_feeds(force=True), 3)
        seen = self.tb.load_feeds()["feeds"][0]["seen"]
        self.assertNotIn(self.entries[0]["link"], seen)
        self.assertEqual(seen, [entry["link"] for entry in self.entries[1:]])

    def test_retry_window_eventually_reaches_entries_after_many_failures(self):
        self.entries = [
            {"title": "e%d" % n, "link": "https://x.test/%d" % n}
            for n in range(13)
        ]
        self._write_feed([])
        article = sys.modules["article"]
        succeeds = article.load

        def first_twelve_are_broken(url):
            if url != self.entries[12]["link"]:
                raise OSError("unavailable")
            return succeeds(url)

        article.load = first_twelve_are_broken

        self.assertEqual(self.tb.refresh_feeds(force=True), 0)
        self.assertEqual(self.tb.refresh_feeds(force=True), 1)
        self.assertIn(
            self.entries[12]["link"], self.tb.load_feeds()["feeds"][0]["seen"])

    def test_valid_non_object_feeds_file_is_treated_as_empty(self):
        self.tb.tbstate.write_json(self.tb._feeds_file(), ["not", "an", "object"])
        self.assertEqual(self.tb.load_feeds(), {"feeds": []})

    def test_subscription_added_during_refresh_survives_commit(self):
        self._write_feed([])
        original = self.tb._install_prepared
        injected = {"done": False}

        def install_and_add(*args, **kwargs):
            if not injected["done"]:
                injected["done"] = True
                current = self.tb.load_feeds()
                current["feeds"].append({
                    "url": "https://new.test/feed", "title": "New",
                    "last_checked": 0, "seen": [],
                })
                self.tb.save_feeds(current)
            return original(*args, **kwargs)

        self.tb._install_prepared = install_and_add
        try:
            self.tb.refresh_feeds(force=True)
        finally:
            self.tb._install_prepared = original
        urls = [feed["url"] for feed in self.tb.load_feeds()["feeds"]]
        self.assertIn("https://new.test/feed", urls)


class FeedAddTest(IsolatedStateCase):
    def test_feed_add_does_not_block_on_the_network(self):
        # `feed add` used to call refresh_feeds(force=True) inline: every subscription
        # plus three articles each, at 20s a request, inside a slash command.
        import inspect

        import thinking_book
        source = inspect.getsource(thinking_book.cmd_feed)
        self.assertIn("_spawn_feed_refresh", source)
        self.assertNotIn("refresh_feeds(force=True)", source)

    def test_refresh_feeds_command_accepts_force(self):
        import inspect

        import thinking_book
        self.assertIn('force="--force" in args',
                      inspect.getsource(thinking_book.cmd_refresh_feeds))


if __name__ == "__main__":
    unittest.main()
