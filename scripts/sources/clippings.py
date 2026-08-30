"""Import highlights from Kindle's de-facto `My Clippings.txt` format.

This is a user-owned plain-text export, not Kindle book data and not a DRM workaround.
Record metadata is localized, so the parser deliberately ignores it and reads only the
title line plus text after the first blank line.
"""

import os
import re

SEPARATOR = "=========="
_TITLE_AUTHOR = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def _title_author(line):
    line = line.strip()
    match = _TITLE_AUTHOR.match(line)
    if not match:
        return line or "Kindle highlights", None
    return match.group(1).strip() or "Kindle highlights", match.group(2).strip() or None


def parse(raw, source=None):
    """Return ordered `[(meta, text)]` groups, one per book."""
    raw = (raw or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    groups = {}
    for record in raw.split(SEPARATOR):
        lines = record.strip("\n ").split("\n")
        if not lines or not lines[0].strip():
            continue
        try:
            blank = next(index for index, line in enumerate(lines[1:], 1) if not line.strip())
        except StopIteration:
            continue
        text = "\n".join(lines[blank + 1:]).strip()
        if not text:
            continue
        title, author = _title_author(lines[0])
        key = (title.casefold(), (author or "").casefold())
        group = groups.setdefault(key, {
            "meta": {"title": title, "author": author, "kind": "highlights", "source": source},
            "seen": set(), "highlights": [],
        })
        normalized = " ".join(text.split())
        if normalized and normalized not in group["seen"]:
            group["seen"].add(normalized)
            group["highlights"].append(text)

    return [(group["meta"], "\n\n".join(group["highlights"]))
            for group in groups.values() if group["highlights"]]


def load(path):
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        groups = parse(fh.read(), source=path)
    if not groups:
        raise LookupError("no Kindle highlights found in %s" % os.path.basename(path))
    return groups
