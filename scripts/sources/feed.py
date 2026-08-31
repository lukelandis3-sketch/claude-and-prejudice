"""RSS / Atom subscriptions.

Feeds are refreshed at SessionStart. Only entry links are collected here; the article
importer does the actual text extraction when an entry reaches the queue.
"""

import re
import urllib.parse
from xml.etree import ElementTree

import fetch
import htmltext

XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


def _tag(element):
    return element.tag.rsplit("}", 1)[-1].lower()


def _text(element):
    if element is None or element.text is None:
        return None
    value = re.sub(r"\s+", " ", htmltext.strip_tags(element.text)).strip()
    return value or None


def _entry_link(entry, base_url=""):
    # RSS puts the URL in <link>text</link>; Atom puts it in <link href="...">.
    entry_base = urllib.parse.urljoin(base_url, entry.get(XML_BASE) or "")
    for child in entry:
        if _tag(child) != "link":
            continue
        link_base = urllib.parse.urljoin(entry_base, child.get(XML_BASE) or "")
        href = (child.get("href") or "").strip()
        if href:
            rel = (child.get("rel") or "alternate").lower()
            if rel == "alternate":
                return urllib.parse.urljoin(link_base, href)
        if child.text and child.text.strip():
            return urllib.parse.urljoin(link_base, child.text.strip())
    for child in entry:
        if _tag(child) in ("guid", "id") and child.text and child.text.strip().startswith("http"):
            return child.text.strip()
    return None


def _entries_with_parent_base(element, parent_base):
    """Yield feed entries with the xml:base inherited from their parent."""
    pending = [(element, parent_base)]
    while pending:
        current, inherited_base = pending.pop()
        if _tag(current) in ("item", "entry"):
            yield current, inherited_base
        current_base = urllib.parse.urljoin(
            inherited_base, current.get(XML_BASE) or "")
        pending.extend((child, current_base) for child in reversed(current))


def parse(xml_text, base_url=""):
    """Return (feed_title, [{'title':..., 'link':...}]) from RSS or Atom markup."""
    root = ElementTree.fromstring(xml_text)
    feed_title, entries = None, []

    channels = [el for el in root.iter() if _tag(el) == "channel"] or [root]
    for element in channels[0]:
        if _tag(element) == "title":
            feed_title = _text(element)
            break

    for element, parent_base in _entries_with_parent_base(root, base_url):
        title = None
        for child in element:
            if _tag(child) == "title":
                title = _text(child)
                break
        link = _entry_link(element, parent_base)
        if link:
            entries.append({"title": title or link, "link": link})
    return feed_title, entries


def load(url):
    feed_title, entries = parse(
        fetch.get(url, accept="application/rss+xml, application/atom+xml, application/xml"),
        base_url=url,
    )
    if not entries:
        raise LookupError("no entries found in feed %s" % url)
    return {"title": feed_title or url, "kind": "feed", "source": url}, entries
