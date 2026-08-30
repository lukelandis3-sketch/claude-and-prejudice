"""Minimal HTML-to-text extraction on top of the standard library.

No lxml, no readability, no pip install -- the plugin has to stay dependency-free.
"""

import re
from html.parser import HTMLParser

SKIP_TAGS = {
    "script", "style", "nav", "aside", "footer", "header", "noscript",
    "form", "svg", "figure", "figcaption", "template", "iframe", "button",
}

BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "td", "th", "hr", "dd", "dt",
}


class TextExtractor(HTMLParser):
    """Collect visible text, dropping chrome and inserting breaks at block boundaries."""

    def __init__(self, skip_tags=None):
        super().__init__(convert_charrefs=True)
        self.skip_tags = SKIP_TAGS if skip_tags is None else skip_tags
        self._skip_depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def text(self):
        joined = "".join(self._parts)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        joined = re.sub(r"\n\s*\n+", "\n", joined)
        return joined.strip()


def to_text(html, skip_tags=None):
    parser = TextExtractor(skip_tags=skip_tags)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # A malformed document should yield whatever was parsed, not an exception.
        pass
    return parser.text()


def strip_tags(fragment):
    """Plain tag strip for short attribute-ish values (titles, feed entries)."""
    return re.sub(r"<[^>]+>", " ", fragment or "")
