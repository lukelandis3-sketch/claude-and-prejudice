"""Shared test helpers: an isolated config dir, a real EPUB fixture, and a CLI runner."""

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
SOURCES = os.path.join(SCRIPTS, "sources")
CLI = os.path.join(SCRIPTS, "thinking_book.py")
STATUSLINE = os.path.join(SCRIPTS, "statusline.sh")

for candidate in (SCRIPTS, SOURCES):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

CONTENT_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Test Voyage</dc:title>
    <dc:creator>A. Fixture</dc:creator>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>"""

CHAPTER_ONE = """<html><body>
<h1>Chapter One</h1>
<p>Call me Ishmael. Some years ago, never mind how long precisely, I thought I would
sail about a little and see the watery part of the world.</p>
<script>var tracking = 1;</script>
</body></html>"""

CHAPTER_TWO = """<html><body>
<p>It is a way I have of driving off the spleen. Whenever it is a damp November in my
soul, I account it high time to get to sea as soon as I can.</p>
</body></html>"""

NAV_XHTML = """<html><body><nav><ol><li>Chapter One</li><li>Chapter Two</li></ol></nav></body></html>"""


def make_epub(path, encrypted=False):
    """Write a small but structurally real EPUB to `path`."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", CONTENT_OPF)
        archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        archive.writestr("OEBPS/ch1.xhtml", CHAPTER_ONE)
        archive.writestr("OEBPS/ch2.xhtml", CHAPTER_TWO)
        if encrypted:
            archive.writestr("META-INF/encryption.xml", "<encryption/>")
    return path


def make_epub_with_encoded_href(path):
    """An EPUB whose spine href is percent-encoded and whose member name has a space.

    Entirely legal per the OPF spec, and the shape that silently lost a chapter.
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr(
            "OEBPS/content.opf",
            CONTENT_OPF.replace('href="ch1.xhtml"', 'href="ch%201.xhtml#start"'),
        )
        archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        archive.writestr("OEBPS/ch 1.xhtml", CHAPTER_ONE)
        archive.writestr("OEBPS/ch2.xhtml", CHAPTER_TWO)
    return path


class IsolatedStateCase(unittest.TestCase):
    """Point CLAUDE_CONFIG_DIR at a scratch directory so no real settings are touched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = self._tmp.name
        self._previous = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.config_dir
        self._previous_session = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

        import importlib
        import tbstate
        import settings as tbsettings
        importlib.reload(tbstate)
        importlib.reload(tbsettings)
        self.tbstate = tbstate
        self.tbsettings = tbsettings
        tbstate.ensure_home()

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._previous
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if self._previous_session is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._previous_session
        self._tmp.cleanup()

    def run_cli(self, *args, **kwargs):
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self.config_dir
        env["CLAUDE_PLUGIN_ROOT"] = REPO
        env.update(kwargs.pop("env", {}))
        return subprocess.run(
            [sys.executable, CLI] + list(args),
            capture_output=True, text=True, env=env, timeout=60,
        )

    def run_statusline(self, stdin_json="{}", env=None):
        environment = dict(os.environ)
        environment["CLAUDE_CONFIG_DIR"] = self.config_dir
        environment.update(env or {})
        return subprocess.run(
            ["sh", STATUSLINE],
            input=stdin_json, capture_output=True, text=True,
            env=environment, timeout=30,
        )

    def seed_stream(self, lines, mode="timer", dwell=8, paused=False, statusline=True,
                    wpm=None):
        """Install a synthetic reading stream without going through an importer."""
        self.tbstate.save_item("test-item", {"title": "Test Item", "kind": "book"}, lines)
        self.tbstate.save_queue({"items": ["test-item"]})
        self.tbstate.rebuild_stream()
        config = self.tbstate.load_config()
        config.update({
            "mode": mode, "dwell_seconds": dwell, "paused": paused,
            "words_per_minute": wpm,
        })
        config["surfaces"]["statusline"] = statusline
        self.tbstate.save_config(config)
        self.tbstate.write_pos(1)
        self.tbstate.write_last_advance(0)
