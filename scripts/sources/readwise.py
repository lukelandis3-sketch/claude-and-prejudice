"""Import user-exported Readwise highlights from CSV or JSON, offline."""

import csv
import json
import os

ALIASES = {
    "text": ("highlight", "text"),
    "title": ("booktitle", "title"),
    "author": ("author",),
    "note": ("note",),
}


def _key(value):
    return "".join(ch for ch in str(value).casefold() if ch.isalnum())


def _value(row, field):
    normalized = {_key(key): value for key, value in row.items()}
    for alias in ALIASES[field]:
        value = normalized.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _json_rows(payload):
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("highlights", "results", "data"):
        if isinstance(payload.get(key), list):
            return [row for row in payload[key] if isinstance(row, dict)]
    rows = []
    for book in payload.get("books", []) if isinstance(payload.get("books"), list) else []:
        if not isinstance(book, dict):
            continue
        for highlight in book.get("highlights", []) if isinstance(book.get("highlights"), list) else []:
            if isinstance(highlight, dict):
                row = dict(book)
                row.pop("highlights", None)
                row.update(highlight)
                rows.append(row)
    return rows


def parse_rows(rows, source=None):
    groups = {}
    for row in rows:
        text = _value(row, "text")
        if not text:
            continue
        note = _value(row, "note")
        if note and note != text:
            text = "%s\n\nNote: %s" % (text, note)
        title = _value(row, "title") or "Readwise highlights"
        author = _value(row, "author")
        key = (title.casefold(), (author or "").casefold())
        group = groups.setdefault(key, {
            "meta": {"title": title, "author": author, "kind": "highlights", "source": source},
            "seen": set(), "highlights": [],
        })
        normalized = " ".join(text.split())
        if normalized not in group["seen"]:
            group["seen"].add(normalized)
            group["highlights"].append(text)
    return [(group["meta"], "\n\n".join(group["highlights"]))
            for group in groups.values() if group["highlights"]]


def load(path):
    path = os.path.abspath(os.path.expanduser(path))
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8-sig") as fh:
            rows = _json_rows(json.load(fh))
    else:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    groups = parse_rows(rows, source=path)
    if not groups:
        raise LookupError("no Readwise highlights found in %s" % os.path.basename(path))
    return groups
