"""RSS / Atom subscriptions.

Feeds are refreshed at SessionStart. Only entry links are collected here; the article
importer does the actual text extraction when an entry reaches the queue.
"""

import re
from xml.etree import ElementTree

import fetch
import htmltext


def _tag(element):
    return element.tag.rsplit("}", 1)[-1].lower()


def _text(element):
    if element is None or element.text is None:
        return None
    value = re.sub(r"\s+", " ", htmltext.strip_tags(element.text)).strip()
    return value or None


def _entry_link(entry):
    # RSS puts the URL in <link>text</link>; Atom puts it in <link href="...">.
    for child in entry:
        if _tag(child) != "link":
            continue
        href = child.get("href")
        if href:
            rel = (child.get("rel") or "alternate").lower()
            if rel == "alternate":
                return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    for child in entry:
        if _tag(child) in ("guid", "id") and child.text and child.text.strip().startswith("http"):
            return child.text.strip()
    return None


def parse(xml_text):
    """Return (feed_title, [{'title':..., 'link':...}]) from RSS or Atom markup."""
    root = ElementTree.fromstring(xml_text)
    feed_title, entries = None, []

    channels = [el for el in root.iter() if _tag(el) == "channel"] or [root]
    for element in channels[0]:
        if _tag(element) == "title":
            feed_title = _text(element)
            break

    for element in root.iter():
        if _tag(element) not in ("item", "entry"):
            continue
        title = None
        for child in element:
            if _tag(child) == "title":
                title = _text(child)
                break
        link = _entry_link(element)
        if link:
            entries.append({"title": title or link, "link": link})
    return feed_title, entries


def load(url):
    feed_title, entries = parse(fetch.get(url, accept="application/rss+xml, application/atom+xml, application/xml"))
    if not entries:
        raise LookupError("no entries found in feed %s" % url)
    return {"title": feed_title or url, "kind": "feed", "source": url}, entries
