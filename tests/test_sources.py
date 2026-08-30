import json
import os
import tempfile
import unittest

import support

import article
import epub
import feed
import gutenberg
import libby


class EpubTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = support.make_epub(os.path.join(self._tmp.name, "voyage.epub"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_metadata_and_spine_order(self):
        meta, text = epub.load(self.path)
        self.assertEqual(meta["title"], "The Test Voyage")
        self.assertEqual(meta["author"], "A. Fixture")
        self.assertLess(text.index("Call me Ishmael"), text.index("driving off the spleen"))

    def test_skips_nav_document_and_scripts(self):
        _meta, text = epub.load(self.path)
        self.assertNotIn("var tracking", text)
        # The nav document lists chapter names; prose should not be polluted by the TOC.
        self.assertEqual(text.count("Chapter One"), 1)

    def test_rejects_encrypted_epub_rather_than_stripping_drm(self):
        encrypted = support.make_epub(
            os.path.join(self._tmp.name, "drm.epub"), encrypted=True
        )
        with self.assertRaises(epub.DrmProtected):
            epub.load(encrypted)

    def test_rejects_non_epub(self):
        plain = os.path.join(self._tmp.name, "not.epub")
        with open(plain, "w") as fh:
            fh.write("just text")
        with self.assertRaises(epub.NotAnEpub):
            epub.load(plain)


class GutenbergTest(unittest.TestCase):
    def test_strips_licence_header_and_footer(self):
        raw = (
            "The Project Gutenberg eBook of Whatever\n"
            "*** START OF THE PROJECT GUTENBERG EBOOK MOBY DICK ***\n"
            "Call me Ishmael.\n"
            "*** END OF THE PROJECT GUTENBERG EBOOK MOBY DICK ***\n"
            "Terms of use follow, at length.\n"
        )
        self.assertEqual(gutenberg.strip_boilerplate(raw), "Call me Ishmael.")

    def test_leaves_text_without_markers_alone(self):
        self.assertEqual(gutenberg.strip_boilerplate("Just prose."), "Just prose.")

    def test_prefers_utf8_plain_text_and_skips_zips(self):
        formats = {
            "application/epub+zip": "https://x/book.epub",
            "text/plain; charset=utf-8": "https://x/book.txt",
            "text/plain": "https://x/book.zip",
        }
        self.assertEqual(gutenberg._text_url(formats), "https://x/book.txt")

    def test_returns_none_when_no_text_format(self):
        self.assertIsNone(gutenberg._text_url({"application/epub+zip": "https://x/b.epub"}))


class LibbyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, payload):
        path = os.path.join(self._tmp.name, "journey.json")
        with open(path, "w") as fh:
            json.dump(payload, fh)
        return path

    def test_reads_highlights_in_order(self):
        path = self._write({
            "title": "Piranesi",
            "author": "Susanna Clarke",
            "highlights": [
                {"quote": "The Beauty of the House is immeasurable.", "chapter": "Part 1"},
                {"quote": "Its Kindness infinite.", "chapter": "Part 1"},
            ],
        })
        meta, text = libby.load(path)
        self.assertEqual(meta["title"], "Piranesi")
        self.assertEqual(meta["author"], "Susanna Clarke")
        self.assertEqual(meta["kind"], "highlights")
        self.assertLess(text.index("immeasurable"), text.index("Kindness"))

    def test_handles_alternative_schema_and_nesting(self):
        path = self._write({
            "readingJourney": {
                "bookTitle": "Another Book",
                "annotations": [{"text": "A nested highlight."}],
            }
        })
        _meta, text = libby.load(path)
        self.assertIn("A nested highlight.", text)

    def test_deduplicates_repeated_highlights(self):
        path = self._write({"highlights": [{"text": "Same."}, {"text": "Same."}]})
        _meta, text = libby.load(path)
        self.assertEqual(text.count("Same."), 1)

    def test_raises_when_no_highlights(self):
        path = self._write({"title": "Empty", "highlights": []})
        with self.assertRaises(LookupError):
            libby.load(path)


class ArticleTest(unittest.TestCase):
    HTML = """<html><head>
      <title>Site Name | Real Title</title>
      <meta property="og:title" content="The Real Title"/>
    </head><body>
      <nav>Home About Contact Subscribe Login</nav>
      <article>
        <p>This is a genuine paragraph of prose, long enough to be treated as content.</p>
        <p>Short.</p>
        <p>Another substantial paragraph that comfortably clears the length threshold.</p>
      </article>
      <footer>Copyright 2026 Some Publisher. All rights reserved.</footer>
    </body></html>"""

    def test_prefers_og_title(self):
        meta, _text = article.extract(self.HTML, url="https://x.test/post")
        self.assertEqual(meta["title"], "The Real Title")

    def test_drops_navigation_and_short_lines(self):
        _meta, text = article.extract(self.HTML, url="https://x.test/post")
        self.assertNotIn("Subscribe", text)
        self.assertNotIn("Short.", text)
        self.assertIn("genuine paragraph", text)

    def test_falls_back_when_no_long_paragraphs(self):
        _meta, text = article.extract("<html><body><p>Tiny.</p></body></html>")
        self.assertIn("Tiny.", text)

    def test_prefers_article_region_over_page_chrome(self):
        html = """<html><body>
          <div>Sidebar text that is quite long and would otherwise be mistaken for prose.</div>
          <main><p>The actual body of the piece, which is what we want to read here.</p></main>
        </body></html>"""
        _meta, text = article.extract(html)
        self.assertIn("actual body", text)
        self.assertNotIn("Sidebar", text)


class FetchTest(unittest.TestCase):
    def test_rejects_non_http_schemes(self):
        import fetch
        for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                fetch.get(url)


class FeedTest(unittest.TestCase):
    def test_parses_rss(self):
        rss = """<?xml version="1.0"?><rss version="2.0"><channel>
          <title>Test Feed</title>
          <item><title>First</title><link>https://example.com/1</link></item>
          <item><title>Second</title><link>https://example.com/2</link></item>
        </channel></rss>"""
        title, entries = feed.parse(rss)
        self.assertEqual(title, "Test Feed")
        self.assertEqual([e["link"] for e in entries],
                         ["https://example.com/1", "https://example.com/2"])

    def test_parses_atom_link_href(self):
        atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
          <title>Atom Feed</title>
          <entry><title>Entry</title>
            <link rel="alternate" href="https://example.com/a"/>
            <link rel="edit" href="https://example.com/edit"/>
          </entry></feed>"""
        title, entries = feed.parse(atom)
        self.assertEqual(title, "Atom Feed")
        self.assertEqual(entries[0]["link"], "https://example.com/a")

    def test_ignores_entries_without_links(self):
        rss = """<?xml version="1.0"?><rss><channel><title>T</title>
          <item><title>No link here</title></item>
        </channel></rss>"""
        _title, entries = feed.parse(rss)
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
