import json
import os
import tempfile
import unittest
from unittest import mock

import support

import article
import clippings
import epub
import feed
import gutenberg
import libby
import plaintext
import readwise


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

    def test_percent_encoded_hrefs_and_fragments_resolve(self):
        # Regression: "ch%201.xhtml" never matched the member "ch 1.xhtml", so the
        # chapter vanished while the import still reported success.
        encoded = support.make_epub_with_encoded_href(
            os.path.join(self._tmp.name, "encoded.epub")
        )
        _meta, text = epub.load(encoded)
        self.assertIn("Ishmael", text)
        self.assertIn("driving off the spleen", text)

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

    def test_detects_an_epub_with_the_wrong_extension(self):
        renamed = support.make_epub(os.path.join(self._tmp.name, "book.bin"))
        self.assertTrue(epub.is_epub(renamed))


class GutenbergTest(unittest.TestCase):
    def test_extracts_ids_from_public_book_and_text_urls(self):
        self.assertEqual(gutenberg.extract_id(
            "https://www.gutenberg.org/ebooks/2701"), "2701")
        self.assertEqual(gutenberg.extract_id(
            "https://gutenberg.org/files/2701/2701-0.txt"), "2701")
        self.assertEqual(gutenberg.extract_id(
            "https://gutenberg.org/cache/epub/2701/pg2701.txt"), "2701")
        self.assertIsNone(gutenberg.extract_id(
            "https://gutenberg.org.evil.test/ebooks/2701"))

    def test_search_prefers_an_exact_normalized_title_over_result_order(self):
        payload = {"results": [
            {
                "id": 1, "title": "A Reader's Guide to Moby Dick", "authors": [],
                "formats": {"text/plain": "https://texts.test/wrong.txt"},
            },
            {
                "id": 2701, "title": "Moby-Dick", "authors": [{"name": "Melville"}],
                "formats": {"text/plain": "https://texts.test/right.txt"},
            },
        ]}

        with mock.patch.object(gutenberg.fetch, "get") as get:
            get.side_effect = [json.dumps(payload), "Call me Ishmael."]
            meta, text = gutenberg.load("Moby Dick")

        self.assertEqual(meta["gutenberg_id"], 2701)
        self.assertEqual(text, "Call me Ishmael.")
        self.assertEqual(get.call_args_list[1].args[0], "https://texts.test/right.txt")

    def test_exact_title_without_plain_text_does_not_hide_a_readable_result(self):
        payload = {"results": [
            {
                "id": 1, "title": "Related readable result", "authors": [],
                "formats": {"text/plain": "https://texts.test/readable.txt"},
            },
            {
                "id": 2, "title": "Wanted", "authors": [],
                "formats": {"application/epub+zip": "https://texts.test/wanted.epub"},
            },
        ]}

        with mock.patch.object(gutenberg.fetch, "get") as get:
            get.side_effect = [json.dumps(payload), "Readable prose."]
            meta, _text = gutenberg.load("Wanted")

        self.assertEqual(meta["gutenberg_id"], 1)

    def test_search_page_is_not_mistaken_for_a_book_url(self):
        self.assertIsNone(gutenberg.extract_id(
            "https://www.gutenberg.org/ebooks/search/?query=moby"))

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

    GUTENBERG_TEXT = """MOBY-DICK; or, THE WHALE.

By Herman Melville

CONTENTS

CHAPTER 1. Loomings.

CHAPTER 2. The Carpet-Bag.

CHAPTER 14. Nantucket.

CHAPTER 134. The Chase-Second Day.

CHAPTER 135. The Chase.-Third Day.

ETYMOLOGY.

The pale Usher-threadbare in coat, heart, body, and brain; I see him now.

CHAPTER 1. Loomings.

Call me Ishmael. Some years ago I thought I would sail about a little.
"""

    def test_front_matter_strip_opens_on_the_first_chapter(self):
        # Regression: a real install spent its opening fragments reading the contents list.
        stripped = gutenberg.strip_front_matter(self.GUTENBERG_TEXT)
        self.assertTrue(stripped.startswith("CHAPTER 1. Loomings."))
        self.assertIn("Call me Ishmael.", stripped)
        self.assertNotIn("CHAPTER 135.", stripped)

    def test_front_matter_strip_leaves_text_without_a_contents_alone(self):
        prose = "Call me Ishmael.\n\nSome years ago I thought I would sail."
        self.assertEqual(gutenberg.strip_front_matter(prose), prose)

    def test_body_chapter_headings_are_not_mistaken_for_a_contents_list(self):
        # In the body each heading is followed by prose, so runs are never long.
        body = "\n\n".join(
            "CHAPTER %d. Title.\n\nSome prose for chapter %d that runs on a while." % (n, n)
            for n in range(1, 12)
        )
        self.assertIn("chapter 11", gutenberg.strip_front_matter(body))

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

    def test_handles_highlights_stored_in_an_id_keyed_object(self):
        path = self._write({
            "title": "Mapped Highlights",
            "highlights": {
                "highlight-1": {"text": "A dictionary-mapped highlight."},
            },
        })
        meta, text = libby.load(path)
        self.assertEqual(meta["title"], "Mapped Highlights")
        self.assertEqual(text, "A dictionary-mapped highlight.")

    def test_deduplicates_repeated_highlights(self):
        path = self._write({"highlights": [{"text": "Same."}, {"text": "Same."}]})
        _meta, text = libby.load(path)
        self.assertEqual(text.count("Same."), 1)

    def test_raises_when_no_highlights(self):
        path = self._write({"title": "Empty", "highlights": []})
        with self.assertRaises(LookupError):
            libby.load(path)

    def test_accepts_a_utf8_bom(self):
        path = os.path.join(self._tmp.name, "bom.json")
        with open(path, "w", encoding="utf-8-sig") as fh:
            json.dump({"title": "Book", "highlights": [{"text": "Quote."}]}, fh)
        meta, text = libby.load(path)
        self.assertEqual(meta["title"], "Book")
        self.assertEqual(text, "Quote.")

    def test_title_text_metadata_is_not_imported_as_a_fake_highlight(self):
        path = self._write({
            "title": {"text": "Piranesi"},
            "highlights": [{"quote": "Actual quotation."}],
        })
        meta, text = libby.load(path)
        self.assertEqual(meta["highlight_count"], 1)
        self.assertEqual(text, "Actual quotation.")

    def test_nested_reading_journey_metadata_is_preserved(self):
        path = self._write({"readingJourney": {
            "bookTitle": "Another Book",
            "firstCreatorName": "Writer",
            "annotations": [{"text": "A nested highlight."}],
        }})
        meta, text = libby.load(path)
        self.assertEqual(meta["title"], "Another Book")
        self.assertEqual(meta["author"], "Writer")
        self.assertEqual(text, "A nested highlight.")


class ClippingsTest(unittest.TestCase):
    def test_groups_books_handles_bom_crlf_localized_metadata_and_dedupes(self):
        raw = ("\ufeffBook One (Author A)\r\n"
               "- Votre surlignement à la page 1\r\n\r\n"
               "First highlight.\r\n==========\r\n"
               "Book One (Author A)\r\n- Your Highlight\r\n\r\n"
               "First   highlight.\r\n==========\r\n"
               "Book Two (Author B)\r\n- Your Highlight\r\n\r\n"
               "Second highlight.\r\n==========\r\n"
               "Book Two (Author B)\r\n- Your Bookmark\r\n\r\n==========")
        groups = clippings.parse(raw, source="fixture")
        self.assertEqual([meta["title"] for meta, _text in groups], ["Book One", "Book Two"])
        self.assertEqual(groups[0][1], "First highlight.")
        self.assertEqual(groups[1][1], "Second highlight.")
        self.assertTrue(all(meta["kind"] == "highlights" for meta, _text in groups))

    def test_no_highlights_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "My Clippings.txt")
            with open(path, "w") as fh:
                fh.write("Book (A)\n- Your Bookmark\n\n==========")
            with self.assertRaises(LookupError):
                clippings.load(path)


class ReadwiseTest(unittest.TestCase):
    def test_csv_accepts_named_column_aliases_and_groups_books(self):
        rows = [
            {"Book Title": "One", "Author": "A", "Highlight": "First.", "Note": "n"},
            {"Book Title": "Two", "Author": "B", "Highlight": "Second.", "Location": "2"},
        ]
        groups = readwise.parse_rows(rows, source="fixture.csv")
        self.assertEqual([meta["title"] for meta, _text in groups], ["One", "Two"])
        self.assertEqual(groups[0][1], "First.\n\nNote: n")
        self.assertEqual(groups[1][1], "Second.")

    def test_json_accepts_lowercase_aliases_and_nested_books(self):
        payload = {"books": [{
            "title": "Nested", "author": "Writer",
            "highlights": [{"text": "A nested highlight."}],
        }]}
        rows = readwise._json_rows(payload)
        groups = readwise.parse_rows(rows)
        self.assertEqual(groups[0][0]["title"], "Nested")
        self.assertEqual(groups[0][1], "A nested highlight.")

    def test_nested_highlight_title_does_not_replace_its_parent_book(self):
        payload = {"books": [{
            "title": "The Book", "author": "Writer",
            "highlights": [{"title": "Chapter 1", "text": "Quote."}],
        }]}
        groups = readwise.parse_rows(readwise._json_rows(payload))
        self.assertEqual(groups[0][0]["title"], "The Book")
        self.assertEqual(groups[0][0]["author"], "Writer")
        self.assertEqual(groups[0][1], "Quote.")

    def test_exact_duplicates_are_removed(self):
        groups = readwise.parse_rows([
            {"title": "One", "text": "Same."},
            {"title": "One", "text": " Same. "},
        ])
        self.assertEqual(groups[0][1], "Same.")


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

    def test_bounded_gzip_rejects_expansion_over_the_limit(self):
        import gzip as gzip_module
        import fetch
        raw = gzip_module.compress(b"x" * 100)
        with self.assertRaises(fetch.FetchError):
            fetch._bounded_gunzip(raw, limit=20)

    def test_wire_response_over_the_limit_is_rejected_not_truncated(self):
        import fetch

        class Headers(dict):
            def get_content_charset(self):
                return "utf-8"

        class Response:
            headers = Headers()
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, amount):
                return b"x" * amount

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(fetch.FetchError):
                fetch.get("https://example.test/large")

    def test_get_decodes_case_insensitive_gzip_encoding_lists(self):
        import gzip as gzip_module
        import fetch

        class Headers(dict):
            def get_content_charset(self):
                return "utf-8"

        class Response:
            headers = Headers({"Content-Encoding": "GZIP, identity"})
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _amount):
                return gzip_module.compress(b"Readable response.")

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            self.assertEqual(fetch.get("https://example.test/gzip"), "Readable response.")


class PlainTextTest(unittest.TestCase):
    def test_rejects_pdf_zip_mobi_and_binary_data(self):
        cases = (
            (b"%PDF-1.7 data", "PDF"),
            (b"PK\x03\x04 zip data", "ZIP"),
            (b"x" * 60 + b"BOOKMOBI", "Kindle"),
            (b"plain\x00binary", "binary"),
        )
        for raw, label in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(plaintext.NotPlainText, label):
                    plaintext.decode(raw, "fixture")

    def test_tolerates_a_small_amount_of_bad_utf8(self):
        text = plaintext.decode(b"A mostly valid sentence with one odd byte: \xff end.")
        self.assertIn("mostly valid", text)


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

    def test_load_resolves_relative_rss_and_atom_links_against_the_feed(self):
        fixtures = (
            (
                """<rss><channel><title>RSS</title>
                <item><title>Entry</title><link>/posts/1</link></item>
                </channel></rss>""",
                "https://example.com/posts/1",
            ),
            (
                """<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
                <entry><title>Entry</title><link href="posts/2"/></entry>
                </feed>""",
                "https://example.com/feeds/posts/2",
            ),
        )
        for xml, expected in fixtures:
            with self.subTest(expected=expected), mock.patch.object(
                    feed.fetch, "get", return_value=xml):
                _meta, entries = feed.load("https://example.com/feeds/latest.xml")
                self.assertEqual(entries[0]["link"], expected)

    def test_atom_links_inherit_xml_base(self):
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"
                    xml:base="https://cdn.example/articles/">
          <title>Atom</title><entry><title>One</title><link href="post-1"/></entry>
        </feed>"""
        with mock.patch.object(feed.fetch, "get", return_value=atom):
            _meta, entries = feed.load("https://origin.example/feed.xml")
        self.assertEqual(entries[0]["link"], "https://cdn.example/articles/post-1")

    def test_rss_items_inherit_channel_xml_base(self):
        rss = """<rss><channel xml:base="https://cdn.example/articles/">
          <title>RSS</title><item><title>One</title><link>post-1</link></item>
        </channel></rss>"""
        with mock.patch.object(feed.fetch, "get", return_value=rss):
            _meta, entries = feed.load("https://origin.example/feed.xml")
        self.assertEqual(entries[0]["link"], "https://cdn.example/articles/post-1")

    def test_ignores_entries_without_links(self):
        rss = """<?xml version="1.0"?><rss><channel><title>T</title>
          <item><title>No link here</title></item>
        </channel></rss>"""
        _title, entries = feed.parse(rss)
        self.assertEqual(entries, [])

    def test_ignores_whitespace_only_atom_href(self):
        atom = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>No link</title><link href="   "/></entry>
        </feed>"""
        _title, entries = feed.parse(atom, base_url="https://example.com/feed.xml")
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
