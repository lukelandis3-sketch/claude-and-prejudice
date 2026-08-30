"""Import a Libby / OverDrive "Reading Journey" export.

Libby has no public API, but it does let you export a title's reading journey -- notes,
highlights, chapter and position -- as JSON, and that export keeps working after you have
returned the book. This gives highlights, not the full text: library ebooks are DRM'd and
this plugin does not touch that.

Export shapes have drifted between Libby versions, so this walks the document defensively
rather than assuming one schema.
"""

import json
import os

HIGHLIGHT_KEYS = ("highlights", "bookmarks", "annotations", "notes", "entries")
TEXT_KEYS = ("quote", "text", "highlight", "excerpt", "content", "note")


def _first_string(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _collect(node, found):
    """Walk the export and collect anything that looks like a highlight."""
    if isinstance(node, dict):
        text = _first_string(node, TEXT_KEYS)
        if text:
            found.append({
                "text": text,
                "chapter": _first_string(node, ("chapter", "chapterTitle", "title")),
                "percent": node.get("percent") or node.get("percentComplete"),
            })
            return
        for key in HIGHLIGHT_KEYS:
            if isinstance(node.get(key), list):
                _collect(node[key], found)
        for value in node.values():
            if isinstance(value, (dict, list)) and not isinstance(value, str):
                _collect(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect(item, found)


def _title_and_author(payload):
    title = author = None
    if isinstance(payload, dict):
        for key in ("title", "bookTitle", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                title = value.strip()
                break
            if isinstance(value, dict):
                nested = value.get("text") or value.get("main")
                if isinstance(nested, str) and nested.strip():
                    title = nested.strip()
                    break
        for key in ("author", "creator", "firstCreatorName"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                author = value.strip()
                break
    return title, author


def load(path):
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, encoding="utf-8", errors="replace") as fh:
        payload = json.load(fh)

    found = []
    _collect(payload, found)
    seen, highlights = set(), []
    for item in found:
        text = item["text"]
        if text in seen:
            continue
        seen.add(text)
        highlights.append(item)

    if not highlights:
        raise LookupError("no highlights found in %s" % os.path.basename(path))

    title, author = _title_and_author(payload)
    meta = {
        "title": title or os.path.splitext(os.path.basename(path))[0],
        "author": author,
        "kind": "highlights",
        "source": path,
        "highlight_count": len(highlights),
    }
    # Highlights are already discrete passages; keep them as their own paragraphs so the
    # chunker never runs two unrelated quotes together.
    return meta, "\n".join(item["text"] for item in highlights)
