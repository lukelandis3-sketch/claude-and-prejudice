"""Import a public-domain book from Project Gutenberg via the Gutendex API."""

import json
import re
import urllib.parse

import fetch

API = "https://gutendex.com/books"

_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)
_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)

# A chapter heading and a table-of-contents entry look identical in a Gutenberg text.
# What separates them is context: in the contents they appear back to back, while in the
# body each is followed by prose. So a long run of consecutive heading-shaped blocks is
# the contents listing.
_HEADING_SHAPE = re.compile(r'^(CHAPTER|BOOK|PART|VOLUME)\s+[\dIVXLCDM]+\b', re.I)
_FIRST_CHAPTER = re.compile(r'^(CHAPTER|BOOK|PART)\s+(1|I)\b', re.I)
_BLOCK_SPLIT = re.compile(r'\n\s*\n+')
MAX_HEADING_LENGTH = 80
MIN_CONTENTS_RUN = 5

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


def _looks_like_heading(block):
    block = block.strip()
    return len(block) <= MAX_HEADING_LENGTH and bool(_HEADING_SHAPE.match(block))


def strip_front_matter(text):
    """Drop the title page and table of contents so reading opens on actual prose.

    Without this the reader spends its first few hundred fragments on a chapter list --
    which is exactly what a real install did, opening on "CHAPTER 14." over and over.
    """
    blocks = _BLOCK_SPLIT.split(text)

    contents_end, index = None, 0
    while index < len(blocks):
        if not _looks_like_heading(blocks[index]):
            index += 1
            continue
        run_end = index
        while run_end < len(blocks) and _looks_like_heading(blocks[run_end]):
            run_end += 1
        if run_end - index >= MIN_CONTENTS_RUN:
            contents_end = run_end
            break
        index = run_end

    if contents_end is None:
        return text.strip()

    remainder = blocks[contents_end:]
    # Prefer to open on the first real chapter rather than on front apparatus.
    for offset, block in enumerate(remainder):
        if _FIRST_CHAPTER.match(block.strip()):
            remainder = remainder[offset:]
            break
    return "\n\n".join(remainder).strip()


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
    return meta, strip_front_matter(strip_boilerplate(fetch.get(url, accept="text/plain")))
