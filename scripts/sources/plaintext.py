"""Import a local plain-text file."""

import os


class NotPlainText(ValueError):
    pass


def decode(raw, name="file"):
    """Decode plausible text and reject common binary containers with useful errors."""
    if raw.startswith(b"%PDF-"):
        raise NotPlainText("%s is a PDF, not plain text; export it to .txt first." % name)
    if b"BOOKMOBI" in raw[:128]:
        raise NotPlainText(
            "%s is a Kindle/MOBI book. thinking-book does not decrypt Kindle books; "
            "import My Clippings.txt for your highlights instead." % name
        )
    if raw.startswith(b"PK\x03\x04"):
        raise NotPlainText(
            "%s is a ZIP/Office container, not plain text. DRM-free EPUBs are supported "
            "directly." % name
        )
    if b"\x00" in raw[:4096]:
        raise NotPlainText("%s looks binary, not like a plain-text book." % name)
    text = raw.decode("utf-8", errors="replace")
    if text and text.count("\ufffd") / len(text) > 0.10:
        raise NotPlainText("%s contains too much undecodable data to be plain text." % name)
    return text


def load(path):
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, "rb") as fh:
        raw = fh.read()
    text = decode(raw, os.path.basename(path))
    meta = {
        "title": os.path.splitext(os.path.basename(path))[0],
        "author": None,
        "kind": "book",
        "source": path,
    }
    return meta, text
