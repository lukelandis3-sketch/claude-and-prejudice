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


def _collect(node, found, candidate=False):
    """Walk the export and collect anything that looks like a highlight."""
    if isinstance(node, dict):
        if candidate:
            text = _first_string(node, TEXT_KEYS)
            if text:
                found.append({
                    "text": text,
                    "chapter": _first_string(node, ("chapter", "chapterTitle", "title")),
                    "percent": node.get("percent") or node.get("percentComplete"),
                })
                return
        for key in HIGHLIGHT_KEYS:
            if isinstance(node.get(key), (dict, list)):
                _collect(node[key], found, candidate=True)
        for key, value in node.items():
            if key in HIGHLIGHT_KEYS:
                continue
            if isinstance(value, (dict, list)) and not isinstance(value, str):
                # Some exports key highlights by an opaque annotation id instead of
                # storing them in a list. Once inside a known highlight container,
                # its mapping values remain highlight candidates.
                _collect(value, found, candidate=candidate)
    elif isinstance(node, list):
        for item in node:
            _collect(item, found, candidate=candidate)


def _title_and_author(payload):
    title = author = None
    candidates = [payload] if isinstance(payload, dict) else []
    for key in ("readingJourney", "book", "media", "publication"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        for key in ("title", "bookTitle", "name"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                title = title or value.strip()
                break
            if isinstance(value, dict):
                nested = value.get("text") or value.get("main")
                if isinstance(nested, str) and nested.strip():
                    title = title or nested.strip()
                    break
        for key in ("author", "creator", "firstCreatorName"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                author = author or value.strip()
                break
    return title, author


def load(path):
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
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
    return meta, "\n\n".join(item["text"] for item in highlights)
