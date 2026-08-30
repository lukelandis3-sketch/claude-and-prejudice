"""Import a web article.

A stdlib heuristic, not a readability port: prefer an <article> or <main> region when the
page has one, otherwise take the body minus obvious chrome, then keep only lines long
enough to be prose. Navigation and footers are short; paragraphs are not.
"""

import re

import fetch
import htmltext

MIN_PARAGRAPH = 40

_REGION = re.compile(r"<(article|main)\b[^>]*>(.*?)</\1>", re.I | re.S)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_OG_TITLE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.I | re.S
)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)


def _best_region(html):
    """The longest <article>/<main> block, or the whole document if there is none."""
    best = ""
    for match in _REGION.finditer(html):
        body = match.group(2)
        if len(body) > len(best):
            best = body
    return best or html


def extract_title(html):
    for pattern in (_OG_TITLE, _H1, _TITLE):
        match = pattern.search(html)
        if match:
            title = htmltext.strip_tags(match.group(1))
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                return title
    return None


def prose_lines(text, min_paragraph=MIN_PARAGRAPH):
    """Keep lines long enough to be prose; nav links and bylines fall away."""
    return [line.strip() for line in text.split("\n") if len(line.strip()) >= min_paragraph]


def extract(html, url=None):
    title = extract_title(html)
    text = htmltext.to_text(_best_region(html))
    lines = prose_lines(text)
    if not lines:
        # A page with no long paragraphs: fall back to everything rather than nothing.
        lines = [line.strip() for line in text.split("\n") if line.strip()]
    meta = {"title": title or (url or "article"), "author": None, "kind": "article", "source": url}
    return meta, "\n".join(lines)


def load(url):
    html = fetch.get(url, accept="text/html,application/xhtml+xml")
    return extract(html, url=url)
