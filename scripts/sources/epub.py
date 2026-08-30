"""Import a DRM-free EPUB by walking its spine in reading order.

Encrypted EPUBs (Adobe DRM, Kindle KFX) are rejected outright -- this plugin does not
circumvent DRM. Buy DRM-free, borrow highlights, or read public domain.
"""

import os
import posixpath
import zipfile
from xml.etree import ElementTree

import htmltext

CONTAINER = "META-INF/container.xml"
ENCRYPTION = "META-INF/encryption.xml"


class NotAnEpub(Exception):
    pass


class DrmProtected(Exception):
    pass


def _findall(root, tag):
    """Namespace-agnostic descendant search -- EPUB namespaces vary by producer."""
    return [el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == tag]


def _opf_path(archive):
    try:
        root = ElementTree.fromstring(archive.read(CONTAINER))
    except (KeyError, ElementTree.ParseError) as exc:
        raise NotAnEpub("missing or invalid %s" % CONTAINER) from exc
    for rootfile in _findall(root, "rootfile"):
        full_path = rootfile.get("full-path")
        if full_path:
            return full_path
    raise NotAnEpub("no rootfile in container.xml")


def _metadata(opf_root):
    meta = {"title": None, "author": None}
    for element in _findall(opf_root, "title"):
        if element.text and element.text.strip():
            meta["title"] = element.text.strip()
            break
    for element in _findall(opf_root, "creator"):
        if element.text and element.text.strip():
            meta["author"] = element.text.strip()
            break
    return meta


def _spine_documents(opf_root):
    manifest = {}
    for item in _findall(opf_root, "item"):
        item_id, href = item.get("id"), item.get("href")
        media = (item.get("media-type") or "").lower()
        properties = (item.get("properties") or "")
        if not item_id or not href:
            continue
        # The nav document is a table of contents, not prose.
        if "nav" in properties.split():
            continue
        if "html" in media or media == "":
            manifest[item_id] = href

    ordered = []
    for itemref in _findall(opf_root, "itemref"):
        idref = itemref.get("idref")
        if idref and idref in manifest:
            ordered.append(manifest[idref])
    return ordered


def extract_text(path):
    """Return (meta, full_text) for an EPUB on disk."""
    if not zipfile.is_zipfile(path):
        raise NotAnEpub("%s is not a zip archive" % path)

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if ENCRYPTION in names:
            raise DrmProtected(
                "%s contains %s -- this looks DRM-protected. thinking-book does not "
                "strip DRM; use a DRM-free copy." % (os.path.basename(path), ENCRYPTION)
            )

        opf_name = _opf_path(archive)
        try:
            opf_root = ElementTree.fromstring(archive.read(opf_name))
        except (KeyError, ElementTree.ParseError) as exc:
            raise NotAnEpub("unreadable OPF at %s" % opf_name) from exc

        meta = _metadata(opf_root)
        base = posixpath.dirname(opf_name)
        texts = []
        for href in _spine_documents(opf_root):
            member = posixpath.normpath(posixpath.join(base, href)) if base else href
            if member not in names:
                continue
            try:
                raw = archive.read(member).decode("utf-8", errors="replace")
            except KeyError:
                continue
            texts.append(htmltext.to_text(raw))

    if not meta["title"]:
        meta["title"] = os.path.splitext(os.path.basename(path))[0]
    return meta, "\n\n".join(t for t in texts if t)


def load(path):
    path = os.path.abspath(os.path.expanduser(path))
    meta, text = extract_text(path)
    meta.update({"kind": "book", "source": path})
    return meta, text
