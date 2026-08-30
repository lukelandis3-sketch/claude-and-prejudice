"""Import a public-domain book from Project Gutenberg via the Gutendex API."""

import json
import re
import urllib.parse

import fetch

API = "https://gutendex.com/books"

_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)
_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)

PREFERRED_FORMATS = (
    "text/plain; charset=utf-8",
    "text/plain; charset=us-ascii",
    "text/plain",
)


def search(query, limit=5):
    """Return [(id, title, author)] for a free-text query."""
    url = "%s?search=%s" % (API, urllib.parse.quote(query))
    payload = json.loads(fetch.get(url, accept="application/json"))
    results = []
    for book in payload.get("results", [])[:limit]:
        authors = book.get("authors") or []
        author = authors[0].get("name") if authors else None
        results.append((book.get("id"), book.get("title"), author))
    return results


def _text_url(formats):
    for key in PREFERRED_FORMATS:
        for available, url in formats.items():
            if available.lower() == key and not url.endswith(".zip"):
                return url
    for available, url in formats.items():
        if available.lower().startswith("text/plain") and not url.endswith(".zip"):
            return url
    return None


def strip_boilerplate(text):
    """Drop the Project Gutenberg licence header and footer."""
    start = _START.search(text)
    if start:
        text = text[start.end():]
    end = _END.search(text)
    if end:
        text = text[: end.start()]
    return text.strip()


def load(argument):
    """Accept a Gutenberg id or a free-text search string."""
    argument = str(argument).strip()
    if argument.isdigit():
        book = json.loads(fetch.get("%s/%s" % (API, argument), accept="application/json"))
    else:
        payload = json.loads(
            fetch.get("%s?search=%s" % (API, urllib.parse.quote(argument)), accept="application/json")
        )
        results = payload.get("results") or []
        if not results:
            raise LookupError("no Project Gutenberg match for %r" % argument)
        book = results[0]

    url = _text_url(book.get("formats") or {})
    if not url:
        raise LookupError("no plain-text format available for %r" % book.get("title"))

    authors = book.get("authors") or []
    meta = {
        "title": book.get("title"),
        "author": authors[0].get("name") if authors else None,
        "kind": "book",
        "source": url,
        "gutenberg_id": book.get("id"),
    }
    return meta, strip_boilerplate(fetch.get(url, accept="text/plain"))
