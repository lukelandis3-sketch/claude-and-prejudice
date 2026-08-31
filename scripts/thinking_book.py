#!/usr/bin/env python3
"""thinking-book -- read a book in the margins of Claude Code.

Command line behind the /book and /n slash commands and the plugin's hooks. Every
hook-facing subcommand exits 0 no matter what goes wrong: a bad book must never be able
to block a turn or break a status line.
"""

import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (HERE, os.path.join(HERE, "sources")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import settings as tbsettings
import tbstate

SCRIPT_NAME = "statusline.sh"
FEED_REFRESH_SECONDS = 3600
LIVE_MARKER_MAX_AGE = 30 * 24 * 60 * 60
MAX_NEW_ITEMS_PER_FEED = 3
HOOK_COMMANDS = {"sync", "advance", "restore", "refresh-feeds"}
PATH_COMMANDS = {"add", "start", "load", "libby", "clippings", "readwise"}


def book_command(suffix=""):
    """The command this invocation can actually run, with an optional argument tail."""
    launcher = os.environ.get("THINKING_BOOK_COMMAND")
    base = launcher if launcher in ("book", "tb") else "/thinking-book:book"
    return base + ((" " + suffix) if suffix else "")


# ------------------------------------------------------------------ small helpers

def _slug(prefix, value):
    import hashlib
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (prefix, digest)


def plugin_root():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)
    # ${CLAUDE_PLUGIN_ROOT} often carries a trailing slash; normalise so paths read cleanly.
    return os.path.normpath(root)


def version():
    """Read the version from plugin.json -- the manifest is the single source of truth."""
    import json
    manifest = os.path.join(plugin_root(), ".claude-plugin", "plugin.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            return json.load(fh).get("version") or "unknown"
    except (OSError, ValueError):
        return "unknown"


def statusline_command():
    return 'sh "%s"' % os.path.join(plugin_root(), "scripts", "statusline.sh")


def _session_id():
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or "global"
    if len(session_id) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        session_id = "global"
    return session_id


def statusline_live_path():
    return tbstate.path("statusline.live." + _session_id())


def stop_suppression_path():
    return tbstate.path("stop.skip." + _session_id())


def _mark_following_stop_consumed():
    """An explicit page command already used this response's turn-mode advance."""
    if not os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return
    config = tbstate.load_config()
    if config["mode"] != "turn" or config["paused"]:
        return
    tbstate.atomic_write(stop_suppression_path(), "1\n")


def _consume_stop_suppression():
    marker = stop_suppression_path()
    try:
        os.unlink(marker)
        return True
    except OSError:
        return False


def _clear_stop_suppression():
    try:
        os.unlink(stop_suppression_path())
    except OSError:
        pass


def prune_statusline_markers(now=None):
    """Remove abandoned liveness markers without disturbing recent sessions."""
    cutoff = (time.time() if now is None else now) - LIVE_MARKER_MAX_AGE
    try:
        entries = os.scandir(tbstate.home())
    except OSError:
        return
    with entries:
        for entry in entries:
            if not entry.name.startswith("statusline.live."):
                continue
            try:
                if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                    os.unlink(entry.path)
            except OSError:
                pass


def current_line():
    return tbstate.stream_line(tbstate.read_pos())


def sync_spinner(config=None):
    """Point spinnerVerbs at the line we are currently on."""
    config = config or tbstate.load_config()
    cursor = tbstate.path("spinner.cursor")
    try:
        os.unlink(cursor)
    except OSError:
        pass
    generation = tbstate.stream_generation() or "none"
    position = tbstate.read_pos()
    line = tbstate.stream_line(position)
    if config["surfaces"]["spinner"]:
        prefix = config.get("prefix") or ""
        tbsettings.set_spinner_line((prefix + line) if line else "")
        # Only certify the cheap Stop path if the immutable stream pointer and cursor
        # stayed stable while settings.json was updated.
        if ((tbstate.stream_generation() or "none") == generation
                and tbstate.read_pos() == position):
            tbstate.atomic_write(cursor, "%s %d %d\n" % (
                generation, position, tbstate.stream_count()))
    return line


def advance_by(steps):
    """Move the bookmark, clamped to the stream. Returns the new position."""
    total = tbstate.stream_count()
    before = tbstate.read_pos()
    requested = before + steps
    position = max(1, min(requested, total if total else 1))
    if position != before or (steps < 0 and tbstate.is_finished()):
        tbstate.write_pos(position)
    if total and steps > 0 and requested > total:
        tbstate.mark_finished()
    tbstate.write_last_advance()
    return position


# ----------------------------------------------------------------------- importing

def _prepare_item(item_id, meta, text):
    import chunker
    fragments = chunker.to_fragments(text)
    if not fragments:
        raise LookupError("nothing readable found in %r" % meta.get("title"))
    return item_id, meta, fragments


def _install_prepared(prepared):
    """Save any number of already-chunked items with one lock and one stream publish."""
    if not prepared:
        return []
    with tbstate.rebuilding_stream():
        queue = tbstate.load_queue()
        queue_changed = False
        for item_id, meta, fragments in prepared:
            tbstate.save_item(item_id, meta, fragments)
            if item_id not in queue["items"]:
                queue["items"].append(item_id)
                queue_changed = True
        if queue_changed:
            tbstate.save_queue(queue)
    return [item_id for item_id, _meta, _fragments in prepared]


def _install(item_id, meta, text, announce=True):
    prepared = _prepare_item(item_id, meta, text)
    _install_prepared([prepared])

    if announce:
        label = meta.get("title") or item_id
        author = meta.get("author")
        print("Queued %s%s -- %d fragments." % (
            label, (" by %s" % author) if author else "", len(prepared[2])))
    return item_id, len(prepared[2])


def _install_many(kind, items):
    """Install a multi-book export with one lock, rebuild, and summary."""
    prepared = []
    for meta, text in items:
        identity = "%s\0%s" % (
            (meta.get("title") or "").strip().casefold(),
            (meta.get("author") or "").strip().casefold(),
        )
        try:
            prepared.append(_prepare_item(_slug(kind, identity), meta, text))
        except LookupError:
            continue
    if not prepared:
        raise LookupError("nothing readable found in the export")
    _install_prepared(prepared)

    total = sum(len(row[2]) for row in prepared)
    print("Queued %d highlight book(s) -- %d fragments." % (len(prepared), total))
    return [row[0] for row in prepared]


def cmd_load(args, activate=True):
    if not args:
        raise SystemExit("usage: %s" % book_command("load <path.epub|path.txt>"))
    path = os.path.abspath(os.path.expanduser(args[0]))
    if not os.path.exists(path):
        raise SystemExit("no such file: %s" % path)

    if os.path.basename(path).casefold() == "my clippings.txt":
        import clippings
        item_ids = _install_many("clippings", clippings.load(path))
        if activate:
            after_interactive_import()
        return None if activate else item_ids

    import epub
    suffix = os.path.splitext(path)[1].lower()
    if suffix in (".mobi", ".azw", ".azw3", ".kfx"):
        raise SystemExit(
            "%s is a Kindle format. thinking-book does not decrypt DRM; use a DRM-free "
            "EPUB or import My Clippings.txt highlights." % path
        )
    if path.lower().endswith(".epub") or epub.is_epub(path):
        meta, text = epub.load(path)
        item_id = _slug("epub", path)
    else:
        import plaintext
        meta, text = plaintext.load(path)
        item_id = _slug("text", path)
    _install(item_id, meta, text)
    if activate:
        after_interactive_import()
    return None if activate else [item_id]


def cmd_gutenberg(args, activate=True):
    if not args:
        raise SystemExit("usage: %s" % book_command("gutenberg <search terms|id>"))
    import gutenberg
    try:
        meta, text = gutenberg.load(" ".join(args))
    except gutenberg.fetch.FetchError:
        raise SystemExit(
            "Could not fetch that book from Project Gutenberg. Retry, or use a book "
            "URL or local file."
        )
    item_id = _slug("gutenberg", meta.get("gutenberg_id") or meta.get("source"))
    _install(item_id, meta, text)
    if activate:
        after_interactive_import()
    return None if activate else [item_id]


def cmd_libby(args, activate=True):
    if not args:
        raise SystemExit("usage: %s" % book_command("libby <reading-journey-export.json>"))
    import libby
    path = os.path.abspath(os.path.expanduser(args[0]))
    meta, text = libby.load(path)
    item_id = _slug("libby", path)
    _install(item_id, meta, text)
    if activate:
        after_interactive_import()
    return None if activate else [item_id]


def cmd_clippings(args, activate=True):
    if not args:
        raise SystemExit("usage: %s" % book_command("clippings <My Clippings.txt>"))
    import clippings
    path = os.path.abspath(os.path.expanduser(args[0]))
    item_ids = _install_many("clippings", clippings.load(path))
    if activate:
        after_interactive_import()
    return None if activate else item_ids


def cmd_readwise(args, activate=True):
    if not args:
        raise SystemExit("usage: %s" % book_command("readwise <export.csv|export.json>"))
    import readwise
    path = os.path.abspath(os.path.expanduser(args[0]))
    item_ids = _install_many("readwise", readwise.load(path))
    if activate:
        after_interactive_import()
    return None if activate else item_ids


def cmd_read(args, activate=True):
    if not args:
        raise SystemExit("usage: %s" % book_command("read <url>"))
    import article
    url = args[0]
    meta, text = article.load(url)
    item_id = _slug("article", url)
    _install(item_id, meta, text)
    if activate:
        after_interactive_import()
    return None if activate else [item_id]


def _json_export_kind(path):
    """Distinguish the two supported JSON exports without accepting arbitrary JSON."""
    import json
    try:
        with open(path, encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(payload, list):
        return "readwise"
    if not isinstance(payload, dict):
        return None
    if any(isinstance(payload.get(key), list) for key in ("books", "results", "data")):
        return "readwise"
    highlights = payload.get("highlights")
    if isinstance(highlights, list):
        if any(isinstance(row, dict) and any(
                key in row for key in (
                    "title", "Title", "bookTitle", "Book Title", "author", "Author"))
                for row in highlights):
            return "readwise"
        return "libby"
    return "libby" if "readingJourney" in payload else None


def _run_import(handler, args, activate):
    return handler(args) if activate else handler(args, activate=False)


def cmd_add(args, activate=True):
    """One front door: URL, supported local file/export, or Gutenberg search."""
    if not args:
        raise SystemExit("usage: %s" % book_command("add <title|url|file>"))
    target = " ".join(args).strip()
    if re.match(r"^https?://", target, re.I):
        import gutenberg
        gutenberg_id = gutenberg.extract_id(target)
        if gutenberg_id:
            return _run_import(cmd_gutenberg, [gutenberg_id], activate)
        return _run_import(cmd_read, [target], activate)
    if target.casefold().startswith("file://"):
        from urllib.parse import unquote, urlsplit
        parsed = urlsplit(target)
        if parsed.netloc not in ("", "localhost"):
            raise SystemExit("remote file URL is not supported: %s" % target)
        target = unquote(parsed.path)

    path = os.path.abspath(os.path.expanduser(target))
    suffix = os.path.splitext(path)[1].lower()
    path_shaped = (target.startswith(("/", "./", "../", "~")) or suffix in {
        ".epub", ".txt", ".csv", ".json", ".mobi", ".azw", ".azw3", ".kfx",
    })
    if os.path.isfile(path):
        if suffix == ".csv":
            return _run_import(cmd_readwise, [path], activate)
        if suffix == ".json":
            kind = _json_export_kind(path)
            if kind == "readwise":
                return _run_import(cmd_readwise, [path], activate)
            if kind == "libby":
                return _run_import(cmd_libby, [path], activate)
            raise SystemExit("unrecognized JSON export: %s" % path)
        return _run_import(cmd_load, [path], activate)
    if os.path.exists(path) and path_shaped:
        raise SystemExit("not a readable file: %s" % path)
    if path_shaped:
        raise SystemExit("no such file: %s" % path)
    return _run_import(cmd_gutenberg, [target], activate)


def _quiet_add(args):
    """Import through the unified router while replacing source-specific chatter."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return cmd_add(args, activate=False)


def cmd_start(args):
    """Import an optional book and apply the recommended setup in one quiet command."""
    written = []
    if args:
        # Source-specific commands are useful interactively but noisy in onboarding.
        # Preserve their errors while replacing success chatter with one stable result.
        written = _quiet_add(args)
        if written:
            offset = tbstate.load_bookmarks().get(written[0], 1)
            position = tbstate.resolve_position(written[0], offset)
            if position is not None:
                tbstate.write_pos(position)
    elif not tbstate.load_queue()["items"]:
        raise SystemExit("Choose a book: %s" % book_command("<title|url|file>"))

    current = tbstate.item_at(tbstate.read_pos())
    title = _display_title(current[1], current[3]) if current else "your book"
    meta = tbstate.item_meta(current[1]) if current else {}
    author = meta.get("author") if isinstance(meta, dict) else None
    label = "%s by %s" % (title, author) if author else title

    statusline_reason = ""
    try:
        if args:
            enabled, statusline_reason = enable_statusline(auto=True)
            config = tbstate.update_config(lambda live: live.update({
                "mode": "timer",
                "words_per_minute": 250,
                "paused": False,
                "hud": True,
                "surfaces": {"statusline": enabled, "spinner": True},
            }))
            if enabled and not tbstate.ensure_hud_shards():
                with tbstate.rebuilding_stream(include_hud=True):
                    pass
        else:
            config = tbstate.update_config(lambda live: live.update({"paused": False}))
        tbstate.write_last_advance()
        sync_spinner(config)
    except Exception as exc:
        message = ("Queued %s, but setup did not finish" if args
                   else "Could not resume %s") % label
        raise SystemExit("%s: %s" % (message, exc))

    surfaces = config["surfaces"]
    pace = "250 WPM" if args else _pace_label(config)
    extra = " (+%d more)" % (len(written) - 1) if len(written) > 1 else ""
    print("Ready — %s%s · %s" % (label, extra, pace))
    if surfaces["statusline"]:
        location = "📖 Read below the input box"
        if statusline_reason == "enabled":
            location += " (restart Claude once if missing)"
    elif surfaces["spinner"]:
        location = "Read on the live spinner"
        if args:
            location += " · Add 📖 line: %s" % book_command("display hud")
    else:
        location = "Reading display is off"
    print("%s · %s" % (location, _controls_hint(config)))


def cmd_source(args):
    """Read a title, URL, or file without making the user name an import command."""
    queued = tbstate.load_queue()["items"]
    if not queued and not tbstate.load_index():
        return cmd_start(args)

    # A valid stream index can recover a crash-truncated queue without treating an
    # established reader as a first run and resetting their preferences.
    if not queued:
        with tbstate.locked():
            queued = list(dict.fromkeys(row[1] for row in tbstate.load_index()))
            if queued and not tbstate.load_queue()["items"]:
                tbstate.save_queue({"items": queued})

    written = _quiet_add(args)
    if not written:
        raise SystemExit("No readable books were imported.")
    cmd_open([written[0]])
    config = tbstate.load_config()
    if config["paused"] and not any(config["surfaces"].values()):
        print("thinking-book is off; run %s when you want to start reading."
              % book_command("on"))
    if len(written) > 1:
        print("Queued %d more book%s." % (
            len(written) - 1, "" if len(written) == 2 else "s"))


# --------------------------------------------------------------------------- feeds

def _feeds_file():
    return tbstate.path("feeds.json")


def load_feeds():
    data = tbstate.read_json(_feeds_file(), {"feeds": []})
    return data if isinstance(data, dict) and isinstance(data.get("feeds"), list) else {"feeds": []}


def save_feeds(data):
    tbstate.write_json(_feeds_file(), data)


def cmd_feed(args):
    if not args:
        raise SystemExit("usage: %s" % book_command("feed add|rm|list [url]"))
    action = args[0]
    data = load_feeds()

    if action == "list":
        if not data["feeds"]:
            print("No feeds subscribed.")
        for entry in data["feeds"]:
            print("%s  %s" % (entry.get("title") or "(untitled)", entry["url"]))
        return

    if len(args) < 2:
        raise SystemExit("usage: %s" % book_command("feed %s <url>" % action))
    url = args[1]

    if action == "add":
        import feed as feedmod
        meta, entries = feedmod.load(url)
        with tbstate.locked("feeds.lock"):
            data = load_feeds()
            if any(entry["url"] == url for entry in data["feeds"]):
                print("Already subscribed to %s" % url)
                return
            data["feeds"].append({
                "url": url, "title": meta.get("title"), "last_checked": 0, "seen": [],
            })
            save_feeds(data)
        print("Subscribed to %s (%d entries available); fetching in the background."
              % (meta.get("title") or url, len(entries)))
        # Never fetch every subscription synchronously inside a slash command -- that is
        # the foreground-network hazard cmd_sync deliberately avoids.
        try:
            _spawn_feed_refresh(force=True)
        except Exception:
            pass
    elif action == "rm":
        with tbstate.locked("feeds.lock"):
            data = load_feeds()
            before = len(data["feeds"])
            data["feeds"] = [entry for entry in data["feeds"] if entry["url"] != url]
            save_feeds(data)
        print("Removed %s" % url if len(data["feeds"]) < before else "Not subscribed to %s" % url)
    else:
        raise SystemExit("unknown feed action %r" % action)


def refresh_feeds(force=False):
    """Pull new entries into the queue. Best effort -- never raises."""
    data = load_feeds()
    if not data["feeds"]:
        return 0

    import article
    import feed as feedmod

    now = time.time()
    staged = []
    staged_links = set()
    staged_seen = []
    for entry in data["feeds"]:
        if not force and now - float(entry.get("last_checked") or 0) < FEED_REFRESH_SECONDS:
            continue
        entry["last_checked"] = now
        try:
            meta, items = feedmod.load(entry["url"])
        except Exception:
            continue
        entry.setdefault("title", meta.get("title"))
        # Order matters: this list is truncated below, and slicing an unordered set kept
        # an arbitrary 500 links rather than the most recent, so old articles came back.
        seen = list(dict.fromkeys(entry.get("seen") or []))
        seen_set = set(seen)
        fresh = [item for item in items if item["link"] not in seen_set][:MAX_NEW_ITEMS_PER_FEED]
        for item in fresh:
            if item["link"] in staged_links:
                staged_seen.append((entry, item["link"]))
                continue
            try:
                article_meta, text = article.load(item["link"])
            except Exception:
                seen.append(item["link"])
                continue
            article_meta["title"] = item.get("title") or article_meta.get("title")
            try:
                prepared = _prepare_item(_slug("article", item["link"]), article_meta, text)
            except Exception:
                continue
            staged.append(prepared)
            staged_links.add(item["link"])
            staged_seen.append((entry, item["link"]))
        entry["seen"] = seen[-500:]
    added = 0
    if staged:
        try:
            _install_prepared(staged)
        except Exception:
            staged = []
        else:
            added = len(staged)
            for entry, link in staged_seen:
                seen = list(dict.fromkeys(entry.get("seen") or []))
                if link not in seen:
                    seen.append(link)
                entry["seen"] = seen[-500:]
    # Network work happened without a lock. Merge its results into the latest subscription
    # set so a concurrent add survives and a concurrently removed feed stays removed.
    updates = {entry.get("url"): entry for entry in data["feeds"] if entry.get("url")}
    with tbstate.locked("feeds.lock"):
        latest = load_feeds()
        for live in latest["feeds"]:
            refreshed = updates.get(live.get("url"))
            if not refreshed:
                continue
            live["title"] = live.get("title") or refreshed.get("title")
            live["last_checked"] = max(
                float(live.get("last_checked") or 0),
                float(refreshed.get("last_checked") or 0),
            )
            combined = list(dict.fromkeys(
                list(live.get("seen") or []) + list(refreshed.get("seen") or [])
            ))
            live["seen"] = combined[-500:]
        save_feeds(latest)
    return added


# ------------------------------------------------------------------------- surfaces

def _path_candidates(command):
    """Paths a shell command might be referring to.

    Quoted segments come first: quoting is how a path containing a space survives a shell
    command at all, and a whitespace-split token would cut it in half.
    """
    candidates = []
    for match in re.finditer(r'"([^"]*)"|\'([^\']*)\'', command):
        candidates.append(match.group(1) if match.group(1) is not None else match.group(2))
    candidates.extend(command.split())
    candidates.append(command)
    return [c.strip().strip('"\'') for c in candidates if c and c.strip()]


def as_statusline_entry(entry):
    """Normalise a statusLine value to a dict, so callers can always use .get()."""
    if isinstance(entry, str):
        return {"type": "command", "command": entry}
    return entry if isinstance(entry, dict) else None


def is_our_statusline(entry):
    """Is this status line command one of ours, whatever path it was installed from?

    Identity must not depend on the plugin root: running `pane on` from a git clone and
    again from the installed plugin yields two different paths to the same script. Taking
    the second for a third-party status line is what made statusline.sh wrap -- and then
    invoke -- itself, recursing until Claude Code's timeout killed it.
    """
    if isinstance(entry, dict):
        command = entry.get("command") or ""
    else:
        command = entry if isinstance(entry, str) else ""
    if not command or SCRIPT_NAME not in command:
        return False
    if any(name in command for name in (
            "thinking-book", "thinking_book", "claude-and-prejudice")):
        return True
    # Installed under some other directory name: check the script sits beside our CLI.
    for candidate in _path_candidates(command):
        if os.path.basename(candidate) != SCRIPT_NAME:
            continue
        if os.path.exists(os.path.join(os.path.dirname(candidate), "thinking_book.py")):
            return True
    return False


def _write_wrapped(entry):
    """Mirror the user's own status line command into a flat file for the hot path."""
    target = tbstate.path("wrapped.cmd")
    command = (entry or {}).get("command") if isinstance(entry, dict) else None
    if command and not is_our_statusline(command):
        tbstate.atomic_write(target, command + "\n")
    elif os.path.exists(target):
        os.unlink(target)


def enable_statusline(auto=False):
    """Enable the status-line surface through the one self-wrap-safe path.

    Automatic activation is deliberately conservative: it fills an empty statusLine but
    never takes over a third-party one without the explicit `pane on` or `on` command.
    Returns (enabled, reason) for the interactive caller's concise notice.
    """
    config = tbstate.load_config()
    enabled, original, _settings, changed = tbsettings.install_statusline(
        statusline_command(), is_our_statusline, auto=auto,
        refresh_interval=config.get("statusline_refresh_interval"),
    )
    if not enabled:
        return False, "another status line is already configured"
    def mutate(config):
        if original:
            config["wrapped_statusline"] = original
        config["surfaces"]["statusline"] = True

    config = tbstate.update_config(mutate)
    _write_wrapped(config.get("wrapped_statusline"))
    return True, "enabled" if changed else "already enabled"


def after_interactive_import():
    """Sync reading surfaces after a person explicitly imports something."""
    config = tbstate.load_config()
    if config["paused"] and not any(config["surfaces"].values()):
        print("thinking-book is off; run %s when you want to start reading."
              % book_command("on"))
        return

    sync_spinner(config)
    if not config["surfaces"]["statusline"]:
        return
    enabled, reason = enable_statusline(auto=True)
    if enabled and reason == "enabled":
        print("Reading surface enabled; restart Claude Code once if the status line is not visible yet.")
    elif not enabled and reason:
        print("A status line is already configured; %s will add the book alongside it."
              % book_command("display hud"))


def cmd_pane(args):
    action = (args[0] if args else "on").lower()

    if action == "on":
        enable_statusline(auto=False)
        config = tbstate.load_config()
        if config.get("hud"):
            config = _set_hud(True)
        message = "Status line reading surface enabled."
        if config["paused"]:
            message += " Reading remains paused; %s when ready." % book_command("resume")
        print(message)
    elif action == "off":
        _disable_statusline()
        print("Status line reading surface disabled. The original or newer user status line is in place.")
    else:
        raise SystemExit("usage: %s" % book_command("pane on|off"))


def _disable_statusline():
    holder = {"wrapped": None}
    def mutate(config):
        config["surfaces"]["statusline"] = False
        wrapped = config.get("wrapped_statusline")
        holder["wrapped"] = None if is_our_statusline(wrapped) else wrapped
        config["wrapped_statusline"] = None
    tbstate.update_config(mutate)
    _write_wrapped(None)
    tbsettings.restore_statusline(holder["wrapped"])


def cmd_repair(_args):
    """Undo a self-wrapped status line and report what was unwound.

    Needed for machines already in the broken state, where `pane off` would otherwise
    restore an inner thinking-book script rather than the user's own status line.
    """
    findings = []
    config = tbstate.load_config()

    wrapped_file = tbstate.path("wrapped.cmd")
    if os.path.exists(wrapped_file):
        with open(wrapped_file, encoding="utf-8") as fh:
            command = fh.read().strip()
        if is_our_statusline(command):
            os.unlink(wrapped_file)
            findings.append("removed a wrapped.cmd that pointed back at thinking-book "
                            "(this is what caused the repeated lines)")

    if is_our_statusline(config.get("wrapped_statusline")):
        tbstate.update_config(lambda live: live.update({"wrapped_statusline": None}))
        config["wrapped_statusline"] = None
        findings.append("cleared a stored status line that was thinking-book's own")

    live = as_statusline_entry(tbsettings.current_statusline())
    if live and is_our_statusline(live):
        expected = statusline_command()
        if live.get("command") != expected:
            tbsettings.set_statusline(
                expected, padding=live.get("padding"),
                refresh_interval=config.get("statusline_refresh_interval"),
                origin_record=_statusline_origin_record(config))
            findings.append("repointed the status line at this install's script")

    if findings:
        print("Repaired:")
        for finding in findings:
            print("  - %s" % finding)
    else:
        print("Nothing to repair -- no self-wrapping detected.")


def cmd_refresh(args):
    """Set statusLine.refreshInterval, where the running Claude Code supports it."""
    if not args:
        raise SystemExit("usage: %s" % book_command("refresh <seconds|off>"))
    if args[0] == "off":
        interval = None
        message = "Status line refresh interval cleared."
    elif args[0].isdigit():
        interval = max(1, int(args[0]))
        message = ("Status line will refresh every %ss where supported -- older Claude Code "
                   "versions ignore this key." % interval)
    else:
        raise SystemExit("usage: %s" % book_command("refresh <seconds|off>"))
    config = tbstate.update_config(
        lambda live: live.update({"statusline_refresh_interval": interval})
    )
    live = as_statusline_entry(tbsettings.current_statusline())
    if config["surfaces"]["statusline"] and live and is_our_statusline(live):
        tbsettings.set_statusline(
            statusline_command(),
            refresh_interval=config.get("statusline_refresh_interval"))
    elif config["surfaces"]["statusline"]:
        message += " Run %s first to apply it without replacing your status line." \
                   % book_command("display hud")
    print(message)


def _set_hud(enabled):
    config = tbstate.update_config(lambda live: live.update({"hud": enabled}))
    if enabled and tbstate.load_queue()["items"]:
        if not tbstate.ensure_hud_shards():
            with tbstate.rebuilding_stream(include_hud=True):
                pass
    return config


def cmd_hud(args):
    if not args or args[0] not in ("on", "off"):
        raise SystemExit("usage: %s" % book_command("hud on|off"))
    enabled = args[0] == "on"
    config = _set_hud(enabled)
    if enabled:
        message = "Graphical reading HUD enabled. It will appear above the book line."
        if not config["surfaces"]["statusline"]:
            message += " The status-line surface is off; run %s to show it." \
                       % book_command("display hud")
        print(message)
    else:
        print("Graphical reading HUD disabled. The compact book line remains.")


def cmd_display(args):
    choices = ("hud", "line", "spinner", "off")
    if not args:
        config = tbstate.load_config()
        surfaces = config["surfaces"]
        if not any(surfaces.values()):
            choice = "off"
        elif surfaces["statusline"]:
            choice = "hud" if config.get("hud") else "line"
        else:
            choice = "spinner"
        print("Display: %s" % choice)
        return
    choice = args[0].lower()
    if choice not in choices:
        raise SystemExit("usage: %s" % book_command("display hud|line|spinner|off"))
    if choice == "off":
        return cmd_off([])
    if choice == "spinner":
        _set_hud(False)
        _disable_statusline()
        tbstate.update_config(lambda config: config.update({
            "paused": False,
            "surfaces": {"statusline": False, "spinner": True},
        }))
    else:
        enable_statusline(auto=False)
        _set_hud(choice == "hud")
        tbstate.update_config(lambda config: config.update({
            "paused": False,
            "surfaces": {"statusline": True, "spinner": True},
        }))
    tbstate.write_last_advance()
    sync_spinner(tbstate.load_config())
    print("Display: %s." % choice)


# -------------------------------------------------------------------------- reading

def _display_title(item_id, title):
    """Never let missing, whitespace-only, or legacy index titles leak into the UI."""
    return " ".join(str(title or "").split()) or item_id


def _pace_label(config):
    if config["mode"] != "timer":
        return config["mode"]
    return ("%s wpm" % config["words_per_minute"] if config.get("words_per_minute")
            else "timer %ss" % config["dwell_seconds"])


def _queue_entries(rows=None):
    """Human-facing queue state, derived once for status, listing, and selection."""
    rows = tbstate.load_index() if rows is None else rows
    total = tbstate.stream_count()
    current = tbstate.locate_position(tbstate.read_pos(), rows=rows, total=total)
    bookmarks = tbstate.load_bookmarks()
    entries = []
    for offset_in_rows, row in enumerate(rows):
        number = offset_in_rows + 1
        _start, item_id, kind, title = row
        end = rows[offset_in_rows + 1][0] - 1 if number < len(rows) else total
        length = max(1, end - row[0] + 1)
        active = bool(current and current[0] == item_id)
        offset = current[1] if active else bookmarks.get(item_id, 1)
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            offset = 1
        offset = max(1, min(offset, length))
        entries.append({
            "number": number, "id": item_id, "kind": kind,
            "title": _display_title(item_id, title),
            "offset": offset, "length": length, "active": active,
        })
    return entries


def _resolve_queue_item(query, rows=None):
    """Resolve a stable id, exact/partial title, or the number shown by the library."""
    query = query.strip()
    entries = _queue_entries(rows)
    folded = query.casefold()
    matches = [entry for entry in entries if entry["id"] == query]
    if not matches and query.isdecimal():
        matches = [entry for entry in entries if entry["number"] == int(query)]
    if not matches:
        matches = [entry for entry in entries if entry["title"].casefold() == folded]
    if not matches:
        matches = [entry for entry in entries if folded in entry["title"].casefold()]
    if not matches:
        raise SystemExit("no library item matches %r; run %s for numbers and titles."
                         % (query, book_command("library")))
    if len(matches) > 1:
        choices = ", ".join("%d: %s (%s)" % (
                            entry["number"], entry["title"], entry["id"])
                            for entry in matches)
        raise SystemExit("%r is ambiguous: %s" % (query, choices))
    return matches[0]


def _controls_hint(config):
    """Describe page turns without steering users into a noisy Claude transcript."""
    if config["mode"] == "timer":
        return "Timer paused" if config["paused"] else "Pages turn automatically"
    if config["mode"] == "turn":
        return "Page turns after each response"

    launcher = os.environ.get("THINKING_BOOK_COMMAND")
    if launcher in ("book", "tb"):
        return "Manual controls: %s next · %s back" % (launcher, launcher)
    import shutil
    short_book = shutil.which("book")
    bundled_book = os.path.join(plugin_root(), "bin", "book")
    if short_book and os.path.realpath(short_book) == os.path.realpath(bundled_book):
        return "Manual controls in another terminal: book next · book back"
    return ("Next: /thinking-book:n · Back: /thinking-book:b · External controls: %s"
            % book_command("install-cli"))


def cmd_dashboard(args):
    """A compact control panel for a bare `/book`, without making the model improvise."""
    details = "--details" in args
    config = tbstate.load_config()
    entries = _queue_entries()
    active = next((entry for entry in entries if entry["active"]), None)
    queue_ids = tbstate.load_queue()["items"]
    indexed_ids = {entry["id"] for entry in entries}
    unavailable = [item_id for item_id in queue_ids if item_id not in indexed_ids]
    if details:
        print("thinking-book %s" % version())
    if not active:
        if unavailable:
            print("%d library item%s unavailable."
                  % (len(unavailable), " is" if len(unavailable) == 1 else "s are"))
            print("Fix: %s · Help: %s" % (
                book_command("library"), book_command("help")))
            return
        print("No book yet.")
        print("Start: %s · Help: %s" % (
            book_command("<title|url|file>"), book_command("help")))
        return

    meta = tbstate.item_meta(active["id"])
    author = meta.get("author") if isinstance(meta, dict) else None
    heading = "Book: %s%s" % (active["title"], " — %s" % author if author else "")
    percent = (active["offset"] * 100) // max(1, active["length"])
    surfaces = config["surfaces"]
    finished = active["number"] == len(entries) and tbstate.is_finished()
    if not (surfaces["statusline"] or surfaces["spinner"]):
        state = "off — %s" % book_command("on")
    elif finished:
        state = "finished"
    elif config["paused"]:
        state = "paused"
    elif not surfaces["statusline"]:
        state = "reading (spinner only)"
    else:
        state = "reading"
    pace = _pace_label(config)
    library = " · book %d/%d" % (active["number"], len(entries)) if len(entries) > 1 else ""
    print(heading)
    print("%s %d/%d (%d%%)%s · %s · %s" % (
        tbstate.progress_bar(active["offset"], active["length"]),
        active["offset"], active["length"], percent, library, pace, state))
    if surfaces["statusline"]:
        print("Read below the input box.")
    elif surfaces["spinner"]:
        print("Read on the live spinner while Claude works.")
    else:
        print("Reading surface is off.")
    controls = _controls_hint(config)
    if not (surfaces["statusline"] or surfaces["spinner"]):
        controls += " · Enable: %s" % book_command("on")
    elif config["paused"]:
        controls += " · Resume: %s" % book_command("resume")
    else:
        controls += " · Pause: %s" % book_command("pause")
    print("%s · Library: %s · Help: %s" % (
        controls, book_command("library"), book_command("help")))
    if details:
        print("Surfaces: statusline=%s spinner=%s hud=%s" % (
            "on" if surfaces["statusline"] else "off",
            "on" if surfaces["spinner"] else "off",
            "on" if config["hud"] else "off",
        ))
        if len(entries) > 1:
            print("Library:")
            for entry in entries:
                marker = "->" if entry["active"] else "  "
                print("  %d. %s %s" % (entry["number"], marker, entry["title"]))


def _turn(steps):
    if not tbstate.stream_count():
        print("Nothing queued — try %s." % book_command("<title|url|file>"))
        return
    before = tbstate.read_pos()
    rows = tbstate.load_index()
    previous = tbstate.item_at(before, rows=rows)
    position = advance_by(steps)
    line = sync_spinner()
    current = tbstate.item_at(position, rows=rows)
    if steps and position == before:
        title = _display_title(current[1], current[3]) if current else "the library"
        print("%s of %s. Use %s to switch books." %
              ("End" if steps > 0 else "Beginning", title, book_command("library")))
    elif previous and current and previous[1] != current[1]:
        print("Book: %s\n%s" % (_display_title(current[1], current[3]), line))
    else:
        print(line or "Nothing queued — try %s." % book_command("<title|url|file>"))


def _integer_arg(args, usage, minimum=1, maximum=None):
    try:
        value = int(args[0]) if len(args) == 1 and args[0].isdigit() else minimum - 1
    except ValueError:
        value = minimum - 1
    if value < minimum or (maximum is not None and value > maximum):
        raise SystemExit(usage)
    return value


def _page_count(args, command):
    return 1 if not args else _integer_arg(
        args, "usage: %s" % book_command("%s [positive line count]" % command))


def cmd_next(args):
    _turn(_page_count(args, "next"))
    if tbstate.stream_count():
        _mark_following_stop_consumed()


def cmd_back(args):
    _turn(-_page_count(args, "back"))
    if tbstate.stream_count():
        _mark_following_stop_consumed()


def cmd_status(_args):
    cmd_dashboard(["--details"])


def cmd_queue(args):
    action = args[0] if args else "list"
    if action == "remove":
        action = "rm"
    if action == "list":
        entries = _queue_entries()
        queue_ids = tbstate.load_queue()["items"]
        indexed_ids = {entry["id"] for entry in entries}
        unavailable = [item_id for item_id in queue_ids if item_id not in indexed_ids]
        if not entries and not unavailable:
            print("Library is empty.")
            print("Add: %s" % book_command("<title|url|file>"))
        for entry in entries:
            marker = "📖" if entry["active"] else "  "
            percent = (entry["offset"] * 100) // max(1, entry["length"])
            print("%d. %s %s — %d/%d (%d%%)" % (
                entry["number"], marker, entry["title"], entry["offset"],
                entry["length"], percent))
        for item_id in unavailable:
            print("! %s (unavailable; remove with %s)" % (
                item_id, book_command("library remove %s" % item_id)))
        if entries:
            print("Open: %s · Remove: %s" % (
                book_command("open 1"), book_command("library remove 1")))
        return

    if action not in ("clear", "rm") or (action == "rm" and len(args) < 2):
        raise SystemExit("usage: %s" % book_command(
            "library [remove <number-or-title>|clear]"))

    removed_title = None
    removed_count = 0
    removed_active = False
    with tbstate.rebuilding_stream() as (logical, _old_items):
        queue = tbstate.load_queue()
        if action == "clear":
            removed_count = len(queue["items"])
            selected = None
        else:
            query = " ".join(args[1:]).strip()
            if query in queue["items"]:
                meta = tbstate.item_meta(query)
                meta = meta if isinstance(meta, dict) else {}
                selected = {"id": query, "title": _display_title(query, meta.get("title"))}
            else:
                selected = _resolve_queue_item(query)
            removed_title = selected["title"]
        if action == "clear":
            queue["items"] = []
        else:
            removed_active = bool(logical and logical[0] == selected["id"])
            queue["items"] = [i for i in queue["items"] if i != selected["id"]]
        tbstate.save_queue(queue)
    sync_spinner()
    if action == "rm":
        now = tbstate.item_at(tbstate.read_pos())
        if not now:
            suffix = " Library is empty."
        else:
            verb = "Now" if removed_active else "Still"
            suffix = " %s reading %s." % (verb, _display_title(now[1], now[3]))
        print("Removed %s.%s" % (removed_title, suffix))
    else:
        print("Removed %d library item%s." %
              (removed_count, "" if removed_count == 1 else "s"))


def cmd_open(args):
    if not args:
        raise SystemExit("usage: %s" % book_command("open <number-or-title>"))
    query = " ".join(args).strip()
    with tbstate.locked():
        rows = tbstate.load_index()
        total = tbstate.stream_count()
        selected = _resolve_queue_item(query, rows=rows)
        tbstate.capture_position(rows=rows)
        item_id, title = selected["id"], selected["title"]
        offset = tbstate.load_bookmarks().get(item_id, 1)
        position = tbstate.resolve_position(item_id, offset, rows=rows, total=total)
        if position is None:
            raise SystemExit("%s is no longer in your library; run %s and try again."
                             % (title, book_command("library")))
        tbstate.write_pos(position)
        tbstate.write_last_advance()
        resolved = tbstate.locate_position(position, rows=rows, total=total)
        offset = resolved[1] if resolved else 1
        tbstate.save_bookmark(item_id, offset)
    config = tbstate.load_config()
    line = sync_spinner(config)
    percent = (offset * 100) // max(1, selected["length"])
    if os.environ.get("THINKING_BOOK_COMMAND") in ("book", "tb"):
        print("Opened %s at %d/%d (%d%%): %s" % (
            title, offset, selected["length"], percent, line or "(blank)"))
        return
    surfaces = config["surfaces"]
    if surfaces["statusline"]:
        message = "Read at 📖 below the input box."
    elif surfaces["spinner"]:
        message = "%s (live spinner)." % (line or "(blank)")
    else:
        message = "Reading is off — %s." % book_command("on")
    print("Opened %s · %d/%d (%d%%). %s" % (
        title, offset, selected["length"], percent, message))


def cmd_mode(args):
    if not args or args[0] not in tbstate.VALID_MODES:
        raise SystemExit("usage: %s" % book_command(
            "mode %s" % "|".join(tbstate.VALID_MODES)))
    _clear_stop_suppression()
    tbstate.update_config(lambda config: config.update({"mode": args[0]}))
    if args[0] == "timer":
        tbstate.write_last_advance()
    print("Advance mode: %s" % args[0])


def cmd_pace(args):
    wpm = _integer_arg(
        args, "usage: %s" % book_command("pace <30-1000 words-per-minute>"), 30, 1000)
    if tbstate.stream_count() and not tbstate.stream_has_word_counts():
        with tbstate.rebuilding_stream():
            pass
    config = tbstate.update_config(lambda live: live.update({"words_per_minute": wpm}))
    if config["mode"] == "timer":
        tbstate.write_last_advance()
    message = "Timer pace: %d words per minute." % wpm
    if config["mode"] != "timer":
        message += " Timer mode is off -- run %s to use it." % book_command("mode timer")
    print(message)


def cmd_dwell(args):
    seconds = _integer_arg(
        args, "usage: %s" % book_command("dwell <1-86400 seconds>"), 1, 86400)
    config = tbstate.update_config(lambda live: live.update({
        "dwell_seconds": seconds, "words_per_minute": None,
    }))
    if config["mode"] == "timer":
        tbstate.write_last_advance()
    message = "Timer interval: %d seconds." % config["dwell_seconds"]
    if config["mode"] != "timer":
        message += " Timer mode is off -- run %s to use it." % book_command("mode timer")
    print(message)


def cmd_pause(_args):
    _clear_stop_suppression()
    config = tbstate.update_config(lambda live: live.update({"paused": True}))
    line = sync_spinner(config)
    print("Paused on: %s" % (line or "(nothing queued)"))


def cmd_resume(_args):
    tbstate.update_config(lambda config: config.update({"paused": False}))
    tbstate.write_last_advance()
    print("Resumed.")


def cmd_off(_args):
    holder = {"wrapped": None}
    def mutate(config):
        wrapped = config.get("wrapped_statusline")
        holder["wrapped"] = None if is_our_statusline(wrapped) else wrapped
        config["paused"] = True
        config["surfaces"] = {"statusline": False, "spinner": False}
        config["wrapped_statusline"] = None
    tbstate.update_config(mutate)
    wrapped = holder["wrapped"]
    _write_wrapped(None)
    tbsettings.clear_spinner()
    tbsettings.restore_statusline(wrapped)
    print("thinking-book is off. Plugin overrides were removed; newer user edits were left untouched.")


def cmd_on(_args):
    """Resume both reading surfaces -- the explicit inverse of the off command."""
    tbstate.update_config(lambda config: config.update({
        "paused": False,
        "surfaces": {"statusline": True, "spinner": True},
    }))
    enable_statusline(auto=False)
    tbstate.write_last_advance()
    sync_spinner(tbstate.load_config())
    print("thinking-book is on. Reading surfaces enabled.")


def cmd_line(_args):
    line = current_line()
    if line:
        print(line)


def cmd_recap(args):
    """Print recent context ending at the current passage."""
    count = 5 if not args else _integer_arg(
        args, "usage: %s" % book_command("recap [1-50]"), 1, 50)
    position = tbstate.read_pos()
    rows = tbstate.load_index()
    current = tbstate.item_at(position, rows=rows)
    bounds = tbstate.item_bounds(current[1], rows=rows) if current else None
    first = bounds[0] if bounds else 1
    passages = tbstate.stream_window(max(first, position - count + 1), position)
    if not passages:
        print("Nothing queued — try %s." % book_command("<title|url|file>"))
        return
    for offset, passage in enumerate(passages):
        marker = "📖 " if offset == len(passages) - 1 else "  "
        print(marker + passage)


def _reader_snapshot(cache, context_radius=40):
    """Reader-pane state with per-book progress and a bounded context window."""
    generation = tbstate.stream_generation()
    if cache.get("generation") != generation:
        cache.clear()
        cache.update({
            "generation": generation,
            "rows": tbstate.load_index(),
            "total": tbstate.stream_count(),
        })
    rows, total = cache["rows"], cache["total"]
    position = max(1, min(tbstate.read_pos(), total if total else 1))
    current = tbstate.item_at(position, rows=rows)
    if not current:
        return ([], None, 1, 0, _pace_label(tbstate.load_config()))
    bounds = tbstate.item_bounds(current[1], rows=rows, total=total)
    start, end = bounds
    window_start = max(start, position - context_radius)
    window_end = min(end, position + context_radius)
    passages = tbstate.stream_window(window_start, window_end)
    context = [
        (passage, window_start + offset == position)
        for offset, passage in enumerate(passages)
    ]
    config = tbstate.load_config()
    pace = _pace_label(config) + (" · paused" if config["paused"] else "")
    return (
        context, _display_title(current[1], current[3]),
        position - start + 1, end - start + 1, pace,
    )


def cmd_reader(_args):
    """A companion pane where one keypress turns the page, outside the conversation."""
    import reader

    if not tbstate.stream_count():
        print("No book yet. Start: book <title|url|file>")
        return 1

    cache = {}

    def state():
        return _reader_snapshot(cache)

    def advance(step):
        advance_by(step)
        sync_spinner()

    def pause():
        config = tbstate.update_config(
            lambda live: live.update({"paused": not live["paused"]}))
        if not config["paused"]:
            tbstate.write_last_advance()
        sync_spinner(config)

    def pace(delta):
        def mutate(config):
            current = config.get("words_per_minute") or 250
            config.update({
                "mode": "timer", "paused": False,
                "words_per_minute": max(30, min(1000, current + delta)),
            })
        config = tbstate.update_config(mutate)
        tbstate.write_last_advance()
        sync_spinner(config)

    return reader.run(
        state, lambda: advance(1), lambda: advance(-1), pause,
        lambda: pace(25), lambda: pace(-25))


def _is_stale_plugin_launcher(link):
    """Whether a symlink is recognisably one of our launchers from an older root."""
    if not os.path.islink(link):
        return False
    try:
        target = os.readlink(link)
    except OSError:
        return False
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(link), target)
    target = os.path.normpath(target)
    if os.path.basename(target) != "book" or os.path.basename(os.path.dirname(target)) != "bin":
        return False
    root = os.path.dirname(os.path.dirname(target))
    if os.path.isfile(os.path.join(root, "scripts", "thinking_book.py")):
        return True
    normal = target.replace(os.sep, "/")
    return "/thinking-book/" in normal or "/claude-and-prejudice/" in normal


def _replace_symlink(link, source):
    """Atomically publish a launcher symlink without a broken-link window."""
    temporary = "%s.tmp.%d" % (link, os.getpid())
    try:
        os.symlink(source, temporary)
        os.replace(temporary, link)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def cmd_install_cli(args):
    """Symlink the readable `book` launcher somewhere on PATH."""
    target_dir = os.path.abspath(os.path.expanduser(
        args[0] if args else os.path.join("~", ".local", "bin")))
    source = os.path.join(plugin_root(), "bin", "book")
    if not os.path.exists(source):
        raise SystemExit("cannot find %s" % source)

    os.makedirs(target_dir, exist_ok=True)
    link = os.path.join(target_dir, "book")

    if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(source):
        print("Already installed: %s" % link)
    elif _is_stale_plugin_launcher(link):
        _replace_symlink(link, source)
        print("Updated %s -> %s" % (link, source))
    else:
        if os.path.lexists(link):
            raise SystemExit("%s already exists -- remove it first, or pass another "
                             "directory." % link)
        os.symlink(source, link)
        print("Installed %s -> %s" % (link, source))

    path_entries = [os.path.abspath(os.path.expanduser(p))
                    for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if target_dir not in path_entries:
        print("Use `%s next` now, or add %s to PATH." % (link, target_dir))
    else:
        print("Use `book next`, `book back`, or `book reader` in another terminal.")


def cmd_version(_args):
    """Which copy is actually running -- the answer when docs and behaviour disagree."""
    print("thinking-book %s" % version())
    print("running from %s" % plugin_root())


def print_help():
    print("Read: %s · next|back [n] · recap [n] · pause|resume · status"
          % book_command("<title|url|file>"))
    print("Pace: pace <wpm> · mode timer|turn|manual · dwell <seconds>")
    print("Display: display hud|line|spinner|off · surfaces: on|off")
    print("Library: library · open <number|title> · add <title|url|file>")
    print("More: reader · feed · install-cli · repair · version")


def cmd_help(_args):
    print_help()


# ---------------------------------------------------------------------------- hooks

def _feeds_due():
    now = time.time()
    for entry in load_feeds()["feeds"]:
        if now - float(entry.get("last_checked") or 0) >= FEED_REFRESH_SECONDS:
            return True
    return False


def _spawn_feed_refresh(force=False):
    """Refresh feeds out of band -- nothing interactive should wait on the network."""
    import subprocess
    command = [sys.executable, os.path.abspath(__file__), "refresh-feeds"]
    if force:
        command.append("--force")
    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(
            command,
            stdout=devnull, stderr=devnull, stdin=devnull,
            start_new_session=True,
        )


def _report(args, message):
    """Hooks pass --quiet and stay silent; a person running the same command gets a line."""
    if "--quiet" not in args:
        print(message)


def cmd_refresh_feeds(args):
    added = refresh_feeds(force="--force" in args)
    sync_spinner()
    _report(args, "Feeds refreshed; %d new item(s) queued." % added)


def _statusline_origin_record(config):
    """The setting an older install wrapped, or absence if it wrapped nothing."""
    wrapped = config.get("wrapped_statusline")
    if wrapped is not None and not is_our_statusline(wrapped):
        return {"present": True, "value": wrapped}
    return {"present": False}


def _repair_moved_statusline(config):
    """Repoint our missing absolute script path after a plugin cache upgrade."""
    if not config["surfaces"]["statusline"]:
        return False
    live = as_statusline_entry(tbsettings.current_statusline())
    if not live or not is_our_statusline(live):
        return False
    command = live.get("command") or ""
    expected = statusline_command()
    if command == expected:
        return False
    old_script = next((candidate for candidate in _path_candidates(command)
                       if os.path.basename(candidate) == SCRIPT_NAME), None)
    if not old_script or os.path.exists(old_script):
        return False
    tbsettings.set_statusline(
        expected, padding=live.get("padding"),
        refresh_interval=config.get("statusline_refresh_interval"),
        origin_record=_statusline_origin_record(config))
    return True


def cmd_sync(args):
    """SessionStart: make sure the plumbing exists, then show where we left off."""
    tbstate.ensure_home()
    tbsettings.ensure_settings_file()
    try:
        os.unlink(statusline_live_path())
    except OSError:
        pass
    try:
        os.unlink(stop_suppression_path())
    except OSError:
        pass
    prune_statusline_markers()
    config = tbstate.load_config()
    try:
        _repair_moved_statusline(config)
    except (OSError, tbsettings.SettingsError):
        # Repair is opportunistic; damaged settings must not prevent local stream recovery.
        pass
    tbstate.write_hot_env(config)
    generation_dir = tbstate.stream_generation_dir()
    queue_items = tbstate.load_queue()["items"]
    if queue_items and (tbstate.stream_count() == 0 or not tbstate.stream_generation()
                        or not generation_dir or not os.path.isdir(generation_dir)
                        or not tbstate.stream_has_index()
                        or (config.get("words_per_minute")
                            and not tbstate.stream_has_word_counts())):
        with tbstate.rebuilding_stream():
            pass
    line = sync_spinner(config)
    if not config["paused"] and _feeds_due():
        try:
            _spawn_feed_refresh()
        except Exception:
            pass
    _report(args, line or "Nothing queued — try %s."
            % book_command("<title|url|file>"))


def cmd_advance(args):
    """Stop: apply the turn-based half of the advance policy, then sync the spinner.

    In timer mode the status line is normally the thing that turns pages, since it runs
    far more often. If that surface is off, this is the only clock we have, so it applies
    the dwell check itself.
    """
    config = tbstate.load_config()
    if _consume_stop_suppression():
        line = sync_spinner(config)
        _report(args, line or "Nothing queued.")
        return
    if config["paused"] or tbstate.stream_count() == 0:
        line = sync_spinner(config)
        _report(args, line or "Nothing queued.")
        return

    mode = config["mode"]
    if mode == "turn":
        advance_by(1)
    elif mode == "timer" and not (
            config["surfaces"]["statusline"] and os.path.exists(statusline_live_path())):
        last = tbstate.read_last_advance()
        if not last:
            # Cold start: show this line and start the clock rather than skipping it.
            tbstate.write_last_advance()
        elif time.time() - last >= tbstate.timer_interval(
                tbstate.stream_record(tbstate.read_pos())[0], config):
            advance_by(1)
    line = sync_spinner(config)
    _report(args, line or "Nothing queued.")


def cmd_restore(args):
    """SessionEnd: do not leave a stale line in settings for non-plugin sessions."""
    try:
        os.unlink(stop_suppression_path())
    except OSError:
        pass
    tbsettings.clear_spinner(session_only=True)
    _report(args, "Spinner override removed.")


COMMANDS = {
    "add": cmd_add, "start": cmd_start, "load": cmd_load,
    "gutenberg": cmd_gutenberg, "libby": cmd_libby,
    "clippings": cmd_clippings, "readwise": cmd_readwise, "read": cmd_read,
    "feed": cmd_feed, "library": cmd_queue, "queue": cmd_queue,
    "open": cmd_open, "status": cmd_status, "mode": cmd_mode,
    "pace": cmd_pace, "dwell": cmd_dwell, "pause": cmd_pause, "resume": cmd_resume, "pane": cmd_pane,
    "on": cmd_on, "off": cmd_off, "next": cmd_next, "back": cmd_back,
    "line": cmd_line, "recap": cmd_recap,
    "repair": cmd_repair, "refresh": cmd_refresh, "hud": cmd_hud, "display": cmd_display,
    "version": cmd_version, "help": cmd_help,
    "reader": cmd_reader, "install-cli": cmd_install_cli,
    "sync": cmd_sync, "advance": cmd_advance, "restore": cmd_restore,
    "refresh-feeds": cmd_refresh_feeds,
}

# Keep the legacy one-letter actions working for existing `tb n` hotkeys.
COMMANDS["n"] = cmd_next
COMMANDS["b"] = cmd_back


def _normalise_argv(argv):
    """Slash commands hand us one quoted blob; a shell hands us real argv.

    The quoting matters: an unquoted $ARGUMENTS let a pasted newline reach the shell,
    which then tried to execute the next line as a program.
    """
    if len(argv) == 1 and any(ch.isspace() for ch in argv[0]):
        blob = argv[0].strip()
        lines = blob.splitlines()
        first = lines[0].strip() if lines else ""
        words = first.split(None, 1)
        name = words[0] if words else ""
        remainder = words[1] if len(words) > 1 else ""
        if remainder and name in PATH_COMMANDS:
            path_arg = _one_argument(remainder)
            return [name, path_arg] + [line.strip() for line in lines[1:] if line.strip()]
        # An implicit title or path is one semantic argument. Keep its spaces intact;
        # shlex would otherwise turn `Moby Dick` into an unknown command plus an arg.
        if name not in COMMANDS:
            return ([_one_argument(first)] if first else []) + [
                line.strip() for line in lines[1:] if line.strip()]
        try:
            import shlex
            argv = shlex.split(argv[0])
        except ValueError:
            argv = argv[0].split()
    # With no arguments, a quoted "$ARGUMENTS" still delivers one empty string; passing it
    # through turns a bare /book into `unknown command ''`.
    return [argument for argument in argv if argument.strip()]


def _one_argument(text):
    """Unquote one title or path without splitting unquoted multiword input."""
    import shlex
    try:
        parsed = shlex.split(text)
    except ValueError:
        return text
    return parsed[0] if len(parsed) == 1 else text


def _looks_like_a_slash_command(argument):
    return argument.startswith("/") and ":" in argument and not os.path.exists(argument)


def _command_suggestion(argv):
    """Return a likely command for an ambiguous single-word source, if any."""
    if len(argv) != 1:
        return None
    token = argv[0].strip().casefold()
    if (len(token) < 3 or any(ch in token for ch in "/\\.~:")
            or any(ch.isspace() for ch in token)):
        return None
    import difflib
    public = sorted(set(COMMANDS) - HOOK_COMMANDS - {"n", "b"})
    matches = difflib.get_close_matches(token, public, n=1, cutoff=0.8)
    return matches[0] if matches else None


def _print_unknown(name):
    """Keep the stale-checkout diagnostic for inputs that cannot be book sources."""
    print("Unknown command %r. Try %s." % (name, book_command("help")), file=sys.stderr)
    print("thinking-book %s · %s · update: git pull, then restart Claude Code"
          % (version(), plugin_root()), file=sys.stderr)


def main(argv):
    argv = _normalise_argv(argv)
    checked = argv[2:] if argv and argv[0] in PATH_COMMANDS else argv[1:]
    stray = [a for a in checked if _looks_like_a_slash_command(a)]
    if stray:
        print("ignoring what looks like a second slash command (%s) -- send one command "
              "per message." % stray[0], file=sys.stderr)
        argv = argv[:argv.index(stray[0])]

    if not argv:
        cmd_dashboard([])
        return 0

    name, args = argv[0], argv[1:]
    handler = COMMANDS.get(name)
    if not handler:
        command_shaped_hyphen = (len(argv) == 1 and "-" in name
                                  and not any(ch in name for ch in "/\\.~:")
                                  and not any(ch.isspace() for ch in name))
        if (name.startswith("-") or command_shaped_hyphen
                or _looks_like_a_slash_command(name)):
            _print_unknown(name)
            return 2
        suggestion = _command_suggestion(argv)
        if suggestion:
            if tbstate.load_queue()["items"]:
                escape = "%s, then %s" % (
                    book_command("add %s" % name), book_command("open %s" % name))
            else:
                escape = book_command("start %s" % name)
            print("Did you mean %r? To read a book called %s: %s"
                  % (suggestion, name, escape), file=sys.stderr)
            return 2
        handler, args = cmd_source, [" ".join(argv)]

    # Hooks swallow everything; interactive commands report their errors.
    if name in HOOK_COMMANDS:
        try:
            handler(args)
        except Exception:
            pass
        return 0

    try:
        return handler(args) or 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(str(exc) or type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
