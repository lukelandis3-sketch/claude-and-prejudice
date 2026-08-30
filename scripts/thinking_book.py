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
MAX_NEW_ITEMS_PER_FEED = 3
HOOK_COMMANDS = {"sync", "advance", "restore", "refresh-feeds"}
PATH_COMMANDS = {"add", "load", "libby", "clippings", "readwise"}


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


def statusline_live_path():
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or "global"
    if not re.match(r"^[A-Za-z0-9_-]+$", session_id):
        session_id = "global"
    return tbstate.path("statusline.live." + session_id)


def current_line():
    return tbstate.stream_line(tbstate.read_pos())


def sync_spinner(config=None):
    """Point spinnerVerbs at the line we are currently on."""
    config = config or tbstate.load_config()
    if not config["surfaces"]["spinner"]:
        return
    line = current_line()
    prefix = config.get("prefix") or ""
    tbsettings.set_spinner_line((prefix + line) if line else "")


def advance_by(steps):
    """Move the bookmark, clamped to the stream. Returns the new position."""
    total = tbstate.stream_count()
    position = tbstate.read_pos() + steps
    position = max(1, min(position, total if total else 1))
    tbstate.write_pos(position)
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
    with tbstate.locked():
        logical = tbstate.capture_position()
        queue = tbstate.load_queue()
        old_items = list(queue["items"])
        queue_changed = False
        for item_id, meta, fragments in prepared:
            tbstate.save_item(item_id, meta, fragments)
            if item_id not in queue["items"]:
                queue["items"].append(item_id)
                queue_changed = True
        if queue_changed:
            tbstate.save_queue(queue)
        tbstate.rebuild_stream()
        tbstate.restore_position(logical, old_items=old_items)
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


def cmd_load(args):
    if not args:
        raise SystemExit("usage: /book load <path.epub|path.txt>")
    path = os.path.abspath(os.path.expanduser(args[0]))
    if not os.path.exists(path):
        raise SystemExit("no such file: %s" % path)

    if os.path.basename(path).casefold() == "my clippings.txt":
        import clippings
        _install_many("clippings", clippings.load(path))
        after_interactive_import()
        return

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
    after_interactive_import()


def cmd_gutenberg(args):
    if not args:
        raise SystemExit("usage: /book gutenberg <search terms|id>")
    import gutenberg
    meta, text = gutenberg.load(" ".join(args))
    _install(_slug("gutenberg", meta.get("gutenberg_id") or meta.get("source")), meta, text)
    after_interactive_import()


def cmd_libby(args):
    if not args:
        raise SystemExit("usage: /book libby <reading-journey-export.json>")
    import libby
    path = os.path.abspath(os.path.expanduser(args[0]))
    meta, text = libby.load(path)
    _install(_slug("libby", path), meta, text)
    after_interactive_import()


def cmd_clippings(args):
    if not args:
        raise SystemExit("usage: /book clippings <My Clippings.txt>")
    import clippings
    path = os.path.abspath(os.path.expanduser(args[0]))
    _install_many("clippings", clippings.load(path))
    after_interactive_import()


def cmd_readwise(args):
    if not args:
        raise SystemExit("usage: /book readwise <export.csv|export.json>")
    import readwise
    path = os.path.abspath(os.path.expanduser(args[0]))
    _install_many("readwise", readwise.load(path))
    after_interactive_import()


def cmd_read(args):
    if not args:
        raise SystemExit("usage: /book read <url>")
    import article
    url = args[0]
    meta, text = article.load(url)
    _install(_slug("article", url), meta, text)
    after_interactive_import()


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


def cmd_add(args):
    """One front door: URL, supported local file/export, or Gutenberg search."""
    if not args:
        raise SystemExit("usage: /book add <title|url|file>")
    target = " ".join(args).strip()
    if re.match(r"^https?://", target, re.I):
        return cmd_read([target])

    path = os.path.abspath(os.path.expanduser(target))
    suffix = os.path.splitext(path)[1].lower()
    path_shaped = (target.startswith(("/", "./", "../", "~")) or suffix in {
        ".epub", ".txt", ".csv", ".json", ".mobi", ".azw", ".azw3", ".kfx",
    })
    if os.path.exists(path):
        if suffix == ".csv":
            return cmd_readwise([path])
        if suffix == ".json":
            kind = _json_export_kind(path)
            if kind == "readwise":
                return cmd_readwise([path])
            if kind == "libby":
                return cmd_libby([path])
            raise SystemExit("unrecognized JSON export: %s" % path)
        return cmd_load([path])
    if path_shaped:
        raise SystemExit("no such file: %s" % path)
    return cmd_gutenberg([target])


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
        raise SystemExit("usage: /book feed add|rm|list [url]")
    action = args[0]
    data = load_feeds()

    if action == "list":
        if not data["feeds"]:
            print("No feeds subscribed.")
        for entry in data["feeds"]:
            print("%s  %s" % (entry.get("title") or "(untitled)", entry["url"]))
        return

    if len(args) < 2:
        raise SystemExit("usage: /book feed %s <url>" % action)
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
    if "thinking-book" in command or "thinking_book" in command:
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
        print("thinking-book is off; run /book on when you want to start reading.")
        return

    sync_spinner(config)
    if not config["surfaces"]["statusline"]:
        return
    enabled, reason = enable_statusline(auto=True)
    if enabled and reason == "enabled":
        print("Reading surface enabled; restart Claude Code once if the status line is not visible yet.")
    elif not enabled and reason:
        print("A status line is already configured; /book pane on will add the book alongside it.")


def cmd_pane(args):
    action = (args[0] if args else "on").lower()

    if action == "on":
        enable_statusline(auto=False)
        print("Status line reading surface enabled.")
    elif action == "off":
        _disable_statusline()
        print("Status line reading surface disabled. The original or newer user status line is in place.")
    else:
        raise SystemExit("usage: /book pane on|off")


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
                refresh_interval=config.get("statusline_refresh_interval"))
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
        raise SystemExit("usage: /book refresh <seconds|off>")
    if args[0] == "off":
        interval = None
        message = "Status line refresh interval cleared."
    elif args[0].isdigit():
        interval = max(1, int(args[0]))
        message = ("Status line will refresh every %ss where supported -- older Claude Code "
                   "versions ignore this key." % interval)
    else:
        raise SystemExit("usage: /book refresh <seconds|off>")
    config = tbstate.update_config(
        lambda live: live.update({"statusline_refresh_interval": interval})
    )
    live = as_statusline_entry(tbsettings.current_statusline())
    if config["surfaces"]["statusline"] and live and is_our_statusline(live):
        tbsettings.set_statusline(
            statusline_command(),
            refresh_interval=config.get("statusline_refresh_interval"))
    elif config["surfaces"]["statusline"]:
        message += " Run /book pane on first to apply it without replacing your status line."
    print(message)


def _set_hud(enabled):
    if enabled and tbstate.stream_count():
        generation_dir = tbstate.stream_generation_dir()
        if not generation_dir or not os.path.isfile(os.path.join(generation_dir, "0.hud")):
            with tbstate.locked():
                logical = tbstate.capture_position()
                old_items = list(tbstate.load_queue()["items"])
                tbstate.rebuild_stream(include_hud=True)
                tbstate.restore_position(logical, old_items=old_items)
    return tbstate.update_config(lambda live: live.update({"hud": enabled}))


def cmd_hud(args):
    if not args or args[0] not in ("on", "off"):
        raise SystemExit("usage: /book hud on|off")
    enabled = args[0] == "on"
    config = _set_hud(enabled)
    if enabled:
        message = "Graphical reading HUD enabled. It will appear above the book line."
        if not config["surfaces"]["statusline"]:
            message += " The status-line surface is off; run /book pane on to show it."
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
        raise SystemExit("usage: /book display hud|line|spinner|off")
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
    """Resolve a stable id, exact/partial title, or the number shown by `/book queue`."""
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
        raise SystemExit("no queued item matches %r; run /book queue for numbers and titles."
                         % query)
    if len(matches) > 1:
        choices = ", ".join("%d: %s (%s)" % (
                            entry["number"], entry["title"], entry["id"])
                            for entry in matches)
        raise SystemExit("%r is ambiguous: %s" % (query, choices))
    return matches[0]


def cmd_dashboard(args):
    """A compact control panel for a bare `/book`, without making the model improvise."""
    config = tbstate.load_config()
    entries = _queue_entries()
    active = next((entry for entry in entries if entry["active"]), None)
    queue_ids = tbstate.load_queue()["items"]
    indexed_ids = {entry["id"] for entry in entries}
    unavailable = [item_id for item_id in queue_ids if item_id not in indexed_ids]
    print("thinking-book %s" % version())
    if not active:
        if unavailable:
            print("\n%d queued item%s unavailable; run /book queue to inspect or remove %s."
                  % (len(unavailable), " is" if len(unavailable) == 1 else "s are",
                     "it" if len(unavailable) == 1 else "them"))
            print("Help: /book help · Guided setup: /thinking-book:setup")
            return
        print("\nNo book is queued.")
        print("Start: /book add <title|url|file>")
        print("Guided setup: /thinking-book:setup · Help: /book help")
        return

    meta = tbstate.item_meta(active["id"])
    author = meta.get("author") if isinstance(meta, dict) else None
    heading = "📖 %s%s" % (active["title"], " — %s" % author if author else "")
    percent = (active["offset"] * 100) // max(1, active["length"])
    surfaces = config["surfaces"]
    if not (surfaces["statusline"] or surfaces["spinner"]):
        state = "off — /book on"
    elif config["paused"]:
        state = "paused"
    elif not surfaces["statusline"]:
        state = "reading (spinner only)"
    else:
        state = "reading"
    pace = "timer %ss" % config["dwell_seconds"] if config["mode"] == "timer" else config["mode"]
    print("\n%s" % heading)
    print("%s %d/%d (%d%%) · %s · %s" % (
        tbstate.progress_bar(active["offset"], active["length"]),
        active["offset"], active["length"], percent, pace, state))
    if len(entries) > 1:
        print("Library: book %d of %d" % (active["number"], len(entries)))
    print("Current: %s" % (current_line() or "(blank)"))
    import shlex
    import shutil
    if shutil.which("tb"):
        controls = "Next: !tb n · Back: !tb b"
    else:
        tb_command = shlex.quote(os.path.join(plugin_root(), "bin", "tb"))
        controls = ("Next: !%s n · Back: !%s b · Shorter: /book install-cli"
                    % (tb_command, tb_command))
    if not (surfaces["statusline"] or surfaces["spinner"]):
        controls += " · Enable: /book on"
    elif config["paused"]:
        controls += " · Resume: /book resume"
    else:
        controls += " · Pause: /book pause"
    print("\n%s" % controls)
    print("Switch: /book queue · Display: /book display · Setup: /thinking-book:setup")
    if "--details" in args:
        print("Surfaces: statusline=%s spinner=%s hud=%s" % (
            "on" if surfaces["statusline"] else "off",
            "on" if surfaces["spinner"] else "off",
            "on" if config["hud"] else "off",
        ))
        if len(entries) > 1:
            print("Queue:")
            for entry in entries:
                marker = "->" if entry["active"] else "  "
                print("  %d. %s %s" % (entry["number"], marker, entry["title"]))
    print("All commands: /book help")


def _turn(steps):
    if not tbstate.stream_count():
        print("Nothing queued -- try /book add <title|url|file>.")
        return
    before = tbstate.read_pos()
    previous = tbstate.item_at(before)
    position = advance_by(steps)
    sync_spinner()
    line = current_line()
    current = tbstate.item_at(position)
    if steps and position == before:
        title = _display_title(current[1], current[3]) if current else "the library"
        print("%s of %s. Use /book queue to switch books." %
              ("End" if steps > 0 else "Beginning", title))
    elif previous and current and previous[1] != current[1]:
        print("📖 %s\n%s" % (_display_title(current[1], current[3]), line))
    else:
        print(line or "Nothing queued -- try /book add <title|url|file>.")


def cmd_next(args):
    steps = int(args[0]) if args and args[0].lstrip("-").isdigit() else 1
    _turn(steps)


def cmd_back(args):
    steps = int(args[0]) if args and args[0].lstrip("-").isdigit() else 1
    _turn(-abs(steps))


def cmd_status(_args):
    cmd_dashboard(["--details"])


def cmd_queue(args):
    action = args[0] if args else "list"
    if action == "list":
        entries = _queue_entries()
        queue_ids = tbstate.load_queue()["items"]
        indexed_ids = {entry["id"] for entry in entries}
        unavailable = [item_id for item_id in queue_ids if item_id not in indexed_ids]
        if not entries and not unavailable:
            print("Queue is empty.")
        for entry in entries:
            marker = "->" if entry["active"] else "  "
            print("%d. %s %s [%d/%d] (%s)" % (
                entry["number"], marker, entry["title"], entry["offset"],
                entry["length"], entry["kind"]))
        for item_id in unavailable:
            print("! %s (unavailable; remove with /book queue rm %s)" % (item_id, item_id))
        return

    if action not in ("clear", "rm") or (action == "rm" and len(args) < 2):
        raise SystemExit("usage: /book queue [list|rm <number-or-title>|clear]")

    removed_title = None
    removed_count = 0
    removed_active = False
    with tbstate.locked():
        queue = tbstate.load_queue()
        old_items = list(queue["items"])
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
        logical = tbstate.capture_position()
        if action == "clear":
            queue["items"] = []
        else:
            removed_active = bool(logical and logical[0] == selected["id"])
            queue["items"] = [i for i in queue["items"] if i != selected["id"]]
        tbstate.save_queue(queue)
        tbstate.rebuild_stream()
        tbstate.restore_position(logical, old_items=old_items)
    sync_spinner()
    if action == "rm":
        now = tbstate.item_at(tbstate.read_pos())
        if not now:
            suffix = " Queue is empty."
        else:
            verb = "Now" if removed_active else "Still"
            suffix = " %s reading %s." % (verb, _display_title(now[1], now[3]))
        print("Removed %s.%s" % (removed_title, suffix))
    else:
        print("Removed %d queued item%s." %
              (removed_count, "" if removed_count == 1 else "s"))


def cmd_open(args):
    if not args:
        raise SystemExit("usage: /book open <number-or-title>")
    query = " ".join(args).strip()
    with tbstate.locked():
        rows = tbstate.load_index()
        selected = _resolve_queue_item(query, rows=rows)
        tbstate.capture_position()
        item_id, title = selected["id"], selected["title"]
        offset = tbstate.load_bookmarks().get(item_id, 1)
        position = tbstate.resolve_position(item_id, offset)
        if position is None:
            raise SystemExit("%s is no longer queued; run /book queue and try again." % title)
        tbstate.write_pos(position)
        tbstate.write_last_advance()
        resolved = tbstate.locate_position(position)
        offset = resolved[1] if resolved else 1
        tbstate.save_bookmark(item_id, offset)
    sync_spinner()
    print("Opened %s at line %d: %s" % (title, offset, current_line() or "(blank)"))


def cmd_mode(args):
    if not args or args[0] not in tbstate.VALID_MODES:
        raise SystemExit("usage: /book mode %s" % "|".join(tbstate.VALID_MODES))
    tbstate.update_config(lambda config: config.update({"mode": args[0]}))
    print("Advance mode: %s" % args[0])


def cmd_dwell(args):
    if not args or not args[0].isdigit():
        raise SystemExit("usage: /book dwell <seconds>")
    config = tbstate.update_config(
        lambda live: live.update({"dwell_seconds": max(1, int(args[0]))})
    )
    print("Timer mode will turn the page every %d seconds." % config["dwell_seconds"])


def cmd_pause(_args):
    tbstate.update_config(lambda config: config.update({"paused": True}))
    print("Paused on: %s" % (current_line() or "(nothing queued)"))


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
    """Resume both reading surfaces -- the explicit inverse of `/book off`."""
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


def cmd_reader(_args):
    """A companion pane where one keypress turns the page, outside the conversation."""
    import reader

    def state():
        config = tbstate.load_config()
        position, total = tbstate.read_pos(), tbstate.stream_count()
        current = tbstate.item_at(position)
        title = _display_title(current[1], current[3]) if current else None
        return (current_line(), title, position, total, config["mode"])

    def advance(step):
        advance_by(step)
        sync_spinner()

    return reader.run(state, lambda: advance(1), lambda: advance(-1))


def cmd_install_cli(args):
    """Symlink bin/tb somewhere on PATH so page turns are `tb n`, not a python3 invocation."""
    target_dir = os.path.abspath(os.path.expanduser(
        args[0] if args else os.path.join("~", ".local", "bin")))
    source = os.path.join(plugin_root(), "bin", "tb")
    if not os.path.exists(source):
        raise SystemExit("cannot find %s" % source)

    os.makedirs(target_dir, exist_ok=True)
    link = os.path.join(target_dir, "tb")

    if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(source):
        print("Already installed: %s" % link)
    else:
        if os.path.islink(link) or os.path.exists(link):
            raise SystemExit("%s already exists -- remove it first, or pass another "
                             "directory." % link)
        os.symlink(source, link)
        print("Installed %s -> %s" % (link, source))

    path_entries = [os.path.abspath(os.path.expanduser(p))
                    for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if target_dir not in path_entries:
        print("Note: %s is not on your PATH. Add it, or symlink tb somewhere that is."
              % target_dir)
    else:
        print("Turn the page with `tb n` in any terminal, or `!tb n` inside Claude Code "
              "(no model turn).")


def cmd_version(_args):
    """Which copy is actually running -- the answer when docs and behaviour disagree."""
    print("thinking-book %s" % version())
    print("running from %s" % plugin_root())


def print_help():
    print("Read something now: /book add <title|url|file>")
    print("Then: /book status · /book queue · /book open <number-or-title> · /book pause")
    print("Display: /book display hud|line|spinner|off · guided setup: /thinking-book:setup")
    print("Library: /book queue rm <number-or-title> · /book queue clear")
    print("Sources: read <url> · clippings <file> · readwise <export> · libby <export> · feed")
    print("All commands: %s" % ", ".join(sorted(COMMANDS)))


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


def cmd_sync(args):
    """SessionStart: make sure the plumbing exists, then show where we left off."""
    tbstate.ensure_home()
    tbsettings.ensure_settings_file()
    try:
        os.unlink(statusline_live_path())
    except OSError:
        pass
    config = tbstate.load_config()
    tbstate.write_hot_env(config)
    generation_dir = tbstate.stream_generation_dir()
    if (tbstate.stream_count() == 0 or not tbstate.stream_generation()
            or not generation_dir or not os.path.isdir(generation_dir)):
        with tbstate.locked():
            tbstate.rebuild_stream()
    sync_spinner(config)
    if not config["paused"] and _feeds_due():
        try:
            _spawn_feed_refresh()
        except Exception:
            pass
    _report(args, current_line() or "Nothing queued -- try /book add <title|url|file>.")


def cmd_advance(args):
    """Stop: apply the turn-based half of the advance policy, then sync the spinner.

    In timer mode the status line is normally the thing that turns pages, since it runs
    far more often. If that surface is off, this is the only clock we have, so it applies
    the dwell check itself.
    """
    config = tbstate.load_config()
    if config["paused"] or tbstate.stream_count() == 0:
        sync_spinner(config)
        _report(args, current_line() or "Nothing queued.")
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
        elif time.time() - last >= config["dwell_seconds"]:
            advance_by(1)
    sync_spinner(config)
    _report(args, current_line() or "Nothing queued.")


def cmd_restore(args):
    """SessionEnd: do not leave a stale line in settings for non-plugin sessions."""
    tbsettings.clear_spinner()
    _report(args, "Spinner override removed.")


COMMANDS = {
    "add": cmd_add, "load": cmd_load, "gutenberg": cmd_gutenberg, "libby": cmd_libby,
    "clippings": cmd_clippings, "readwise": cmd_readwise, "read": cmd_read,
    "feed": cmd_feed, "queue": cmd_queue, "open": cmd_open, "status": cmd_status, "mode": cmd_mode,
    "dwell": cmd_dwell, "pause": cmd_pause, "resume": cmd_resume, "pane": cmd_pane,
    "on": cmd_on, "off": cmd_off, "next": cmd_next, "back": cmd_back, "line": cmd_line,
    "repair": cmd_repair, "refresh": cmd_refresh, "hud": cmd_hud, "display": cmd_display,
    "version": cmd_version, "help": cmd_help,
    "reader": cmd_reader, "install-cli": cmd_install_cli,
    "sync": cmd_sync, "advance": cmd_advance, "restore": cmd_restore,
    "refresh-feeds": cmd_refresh_feeds,
}

# Brevity is the whole point of `tb`: `tb n` must work, not just `tb next`.
COMMANDS["n"] = cmd_next
COMMANDS["b"] = cmd_back


def _normalise_argv(argv):
    """Slash commands hand us one quoted blob; a shell hands us real argv.

    The quoting matters: an unquoted $ARGUMENTS let a pasted newline reach the shell,
    which then tried to execute the next line as a program.
    """
    import shlex
    if len(argv) == 1 and any(ch.isspace() for ch in argv[0]):
        blob = argv[0].strip()
        name, separator, remainder = blob.partition(" ")
        if separator and name in PATH_COMMANDS:
            lines = remainder.splitlines()
            raw_path = lines[0].strip()
            try:
                parsed = shlex.split(raw_path)
            except ValueError:
                parsed = []
            path_arg = parsed[0] if len(parsed) == 1 else raw_path
            return [name, path_arg] + [line.strip() for line in lines[1:] if line.strip()]
        try:
            argv = shlex.split(argv[0])
        except ValueError:
            argv = argv[0].split()
    # With no arguments, a quoted "$ARGUMENTS" still delivers one empty string; passing it
    # through turns a bare /book into `unknown command ''`.
    return [argument for argument in argv if argument.strip()]


def _looks_like_a_slash_command(argument):
    return argument.startswith("/") and ":" in argument and not os.path.exists(argument)


def main(argv):
    argv = _normalise_argv(argv)
    checked = argv[2:] if argv and argv[0] in PATH_COMMANDS else argv[1:]
    stray = [a for a in checked if _looks_like_a_slash_command(a)]
    if stray:
        print("ignoring what looks like a second slash command (%s) -- send one command "
              "per message." % stray[0], file=sys.stderr)
        keep = 2 if argv[0] in PATH_COMMANDS else 1
        argv = argv[:keep] + [a for a in argv[keep:] if not _looks_like_a_slash_command(a)]

    if not argv:
        cmd_dashboard([])
        return 0

    name, args = argv[0], argv[1:]
    handler = COMMANDS.get(name)
    if not handler:
        # A directory-source install runs straight out of a git checkout, so it goes stale
        # silently. Say which copy is running rather than only that the command is unknown.
        print("unknown command %r -- thinking-book %s running from %s"
              % (name, version(), plugin_root()), file=sys.stderr)
        print("known commands: %s" % ", ".join(sorted(COMMANDS)), file=sys.stderr)
        print("if you expected this command, that checkout may be behind: "
              "git pull in the directory above, then restart Claude Code.", file=sys.stderr)
        return 2

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
