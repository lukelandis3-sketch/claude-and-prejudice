"""Import a local plain-text file."""

import os


def load(path):
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    meta = {
        "title": os.path.splitext(os.path.basename(path))[0],
        "author": None,
        "kind": "book",
        "source": path,
    }
    return meta, text
