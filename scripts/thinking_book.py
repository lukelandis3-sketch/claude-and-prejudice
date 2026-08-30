#!/usr/bin/env python3
"""thinking-book -- read a book in the margins of Claude Code.

Command line behind the /book and /n slash commands and the plugin's hooks. Every
hook-facing subcommand exits 0 no matter what goes wrong: a bad book must never be able
to block a turn or break a status line.
"""

import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (HERE, os.path.join(HERE, "sources")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import chunker
import settings as tbsettings
import tbstate

SCRIPT_NAME = "statusline.sh"
FEED_REFRESH_SECONDS = 3600
MAX_NEW_ITEMS_PER_FEED = 3
HOOK_COMMANDS = {"sync", "advance", "restore", "refresh-feeds"}


# ------------------------------------------------------------------ small helpers

def _slug(prefix, value):
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (prefix, digest)


def plugin_root():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)
    # ${CLAUDE_PLUGIN_ROOT} often carries a trailing slash; normalise so paths read cleanly.
    return os.path.normpath(root)


def version():
    """Read the version from plugin.json -- the manifest is the single source of truth."""
    manifest = os.path.join(plugin_root(), ".claude-plugin", "plugin.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            return json.load(fh).get("version") or "unknown"
    except (OSError, ValueError):
        return "unknown"


def statusline_command():
    return 'sh "%s"' % os.path.join(plugin_root(), "scripts", "statusline.sh")


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

def _install(item_id, meta, text, announce=True):
    fragments = chunker.to_fragments(text)
    if not fragments:
        raise LookupError("nothing readable found in %r" % meta.get("title"))

    with tbstate.locked():
        tbstate.save_item(item_id, meta, fragments)
        queue = tbstate.load_queue()
        if item_id not in queue["items"]:
            queue["items"].append(item_id)
            tbstate.save_queue(queue)
        tbstate.rebuild_stream()

    if announce:
        label = meta.get("title") or item_id
        author = meta.get("author")
        print("Queued %s%s -- %d fragments." % (label, (" by %s" % author) if author else "", len(fragments)))
    return item_id, len(fragments)


def cmd_load(args):
    if not args:
        raise SystemExit("usage: /book load <path.epub|path.txt>")
    path = os.path.abspath(os.path.expanduser(args[0]))
    if not os.path.exists(path):
        raise SystemExit("no such file: %s" % path)

    if path.lower().endswith(".epub"):
        import epub
        meta, text = epub.load(path)
        item_id = _slug("epub", path)
    else:
        import plaintext
        meta, text = plaintext.load(path)
        item_id = _slug("text", path)
    _install(item_id, meta, text)


def cmd_gutenberg(args):
    if not args:
        raise SystemExit("usage: /book gutenberg <search terms|id>")
    import gutenberg
    meta, text = gutenberg.load(" ".join(args))
    _install(_slug("gutenberg", meta.get("gutenberg_id") or meta.get("source")), meta, text)


def cmd_libby(args):
    if not args:
        raise SystemExit("usage: /book libby <reading-journey-export.json>")
    import libby
    path = os.path.abspath(os.path.expanduser(args[0]))
    meta, text = libby.load(path)
    _install(_slug("libby", path), meta, text)


def cmd_read(args):
    if not args:
        raise SystemExit("usage: /book read <url>")
    import article
    url = args[0]
    meta, text = article.load(url)
    _install(_slug("article", url), meta, text)


# --------------------------------------------------------------------------- feeds

def _feeds_file():
    return tbstate.path("feeds.json")


def load_feeds():
    data = tbstate.read_json(_feeds_file(), {"feeds": []})
    return data if isinstance(data.get("feeds"), list) else {"feeds": []}


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
        if any(entry["url"] == url for entry in data["feeds"]):
            print("Already subscribed to %s" % url)
            return
        data["feeds"].append({"url": url, "title": meta.get("title"), "last_checked": 0, "seen": []})
        save_feeds(data)
        print("Subscribed to %s (%d entries available)." % (meta.get("title") or url, len(entries)))
        refresh_feeds(force=True)
    elif action == "rm":
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
    added = 0
    for entry in data["feeds"]:
        if not force and now - float(entry.get("last_checked") or 0) < FEED_REFRESH_SECONDS:
            continue
        entry["last_checked"] = now
        try:
            meta, items = feedmod.load(entry["url"])
        except Exception:
            continue
        entry.setdefault("title", meta.get("title"))
        seen = set(entry.get("seen") or [])
        fresh = [item for item in items if item["link"] not in seen][:MAX_NEW_ITEMS_PER_FEED]
        for item in fresh:
            try:
                article_meta, text = article.load(item["link"])
            except Exception:
                seen.add(item["link"])
                continue
            article_meta["title"] = item.get("title") or article_meta.get("title")
            try:
                _install(_slug("article", item["link"]), article_meta, text, announce=False)
                added += 1
            except Exception:
                pass
            seen.add(item["link"])
        entry["seen"] = list(seen)[-500:]
    save_feeds(data)
    return added


# ------------------------------------------------------------------------- surfaces

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
        command = entry or ""
    if not command or SCRIPT_NAME not in command:
        return False
    if "thinking-book" in command or "thinking_book" in command:
        return True
    # Installed under some other directory name: check the script sits beside our CLI.
    for token in re.findall(r'[^\s"\']*' + re.escape(SCRIPT_NAME), command):
        if os.path.exists(os.path.join(os.path.dirname(token), "thinking_book.py")):
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


def cmd_pane(args):
    action = (args[0] if args else "on").lower()
    config = tbstate.load_config()

    if action == "on":
        existing = tbsettings.current_statusline()
        ours = statusline_command()
        if existing and not is_our_statusline(existing):
            # Preserve whatever the user already had; statusline.sh will run it too.
            config["wrapped_statusline"] = existing
        # If `existing` is already ours, keep whatever we stored the first time and do
        # not nest. This makes `pane on` idempotent across plugin roots.
        config["surfaces"]["statusline"] = True
        tbstate.save_config(config)
        _write_wrapped(config.get("wrapped_statusline"))
        padding = existing.get("padding") if existing and not is_our_statusline(existing) else None
        tbsettings.set_statusline(ours, padding=padding,
                                  refresh_interval=config.get("statusline_refresh_interval"))
        print("Status line reading surface enabled.")
    elif action == "off":
        config["surfaces"]["statusline"] = False
        wrapped = config.get("wrapped_statusline")
        if is_our_statusline(wrapped):
            wrapped = None
        config["wrapped_statusline"] = None
        tbstate.save_config(config)
        _write_wrapped(None)
        tbsettings.restore_statusline(wrapped)
        print("Status line reading surface disabled." + (" Your own status line is back." if wrapped else ""))
    else:
        raise SystemExit("usage: /book pane on|off")


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
        config["wrapped_statusline"] = None
        tbstate.save_config(config)
        findings.append("cleared a stored status line that was thinking-book's own")

    live = tbsettings.current_statusline()
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
    config = tbstate.load_config()
    if args[0] == "off":
        config["statusline_refresh_interval"] = None
        message = "Status line refresh interval cleared."
    elif args[0].isdigit():
        config["statusline_refresh_interval"] = max(1, int(args[0]))
        message = ("Status line will refresh every %ss where supported -- older Claude Code "
                   "versions ignore this key." % config["statusline_refresh_interval"])
    else:
        raise SystemExit("usage: /book refresh <seconds|off>")
    tbstate.save_config(config)
    if config["surfaces"]["statusline"]:
        tbsettings.set_statusline(
            statusline_command(),
            refresh_interval=config.get("statusline_refresh_interval"))
    print(message)


# -------------------------------------------------------------------------- reading

def cmd_next(args):
    steps = int(args[0]) if args and args[0].lstrip("-").isdigit() else 1
    advance_by(steps)
    sync_spinner()
    line = current_line()
    print(line if line else "Nothing queued -- try /book gutenberg <title>.")


def cmd_back(args):
    steps = int(args[0]) if args and args[0].lstrip("-").isdigit() else 1
    advance_by(-abs(steps))
    sync_spinner()
    print(current_line() or "Nothing queued.")


def cmd_status(_args):
    config = tbstate.load_config()
    total = tbstate.stream_count()
    position = tbstate.read_pos()
    if not total:
        print("Nothing queued. Start with /book gutenberg <title> or /book load <file.epub>.")
        return

    current = tbstate.item_at(position)
    percent = (position / total) * 100
    print("Reading:  %s" % (current[3] if current else "(unknown)"))
    meta = tbstate.item_meta(current[1]) if current else {}
    if meta.get("author"):
        print("Author:   %s" % meta["author"])
    print("Position: line %d of %d  (%.1f%%)" % (position, total, percent))
    print("Mode:     %s%s (dwell %ss)" % (config["mode"], " [paused]" if config["paused"] else "", config["dwell_seconds"]))
    print("Surfaces: statusline=%s spinner=%s" % (
        "on" if config["surfaces"]["statusline"] else "off",
        "on" if config["surfaces"]["spinner"] else "off",
    ))
    print("Current:  %s" % (current_line() or "(blank)"))

    queue = tbstate.load_queue()
    if len(queue["items"]) > 1:
        print("\nQueue:")
        for start, item_id, kind, title in tbstate.load_index():
            marker = "->" if current and item_id == current[1] else "  "
            print("  %s %-9s %s" % (marker, kind, title))


def cmd_queue(args):
    action = args[0] if args else "list"
    if action == "list":
        rows = tbstate.load_index()
        if not rows:
            print("Queue is empty.")
        for start, item_id, kind, title in rows:
            print("%-24s %-9s %s" % (item_id, kind, title))
        return

    with tbstate.locked():
        queue = tbstate.load_queue()
        if action == "clear":
            queue["items"] = []
        elif action == "rm" and len(args) > 1:
            queue["items"] = [i for i in queue["items"] if i != args[1]]
        else:
            raise SystemExit("usage: /book queue [list|rm <id>|clear]")
        tbstate.save_queue(queue)
        tbstate.rebuild_stream()
    tbstate.write_pos(min(tbstate.read_pos(), max(1, tbstate.stream_count())))
    sync_spinner()
    print("Queue updated.")


def cmd_mode(args):
    if not args or args[0] not in tbstate.VALID_MODES:
        raise SystemExit("usage: /book mode %s" % "|".join(tbstate.VALID_MODES))
    config = tbstate.load_config()
    config["mode"] = args[0]
    tbstate.save_config(config)
    print("Advance mode: %s" % args[0])


def cmd_dwell(args):
    if not args or not args[0].isdigit():
        raise SystemExit("usage: /book dwell <seconds>")
    config = tbstate.load_config()
    config["dwell_seconds"] = max(1, int(args[0]))
    tbstate.save_config(config)
    print("Timer mode will turn the page every %d seconds." % config["dwell_seconds"])


def cmd_pause(_args):
    config = tbstate.load_config()
    config["paused"] = True
    tbstate.save_config(config)
    print("Paused on: %s" % (current_line() or "(nothing queued)"))


def cmd_resume(_args):
    config = tbstate.load_config()
    config["paused"] = False
    tbstate.save_config(config)
    tbstate.write_last_advance()
    print("Resumed.")


def cmd_off(_args):
    config = tbstate.load_config()
    wrapped = config.get("wrapped_statusline")
    if is_our_statusline(wrapped):
        wrapped = None
    config["paused"] = True
    config["surfaces"] = {"statusline": False, "spinner": False}
    config["wrapped_statusline"] = None
    tbstate.save_config(config)
    _write_wrapped(None)
    tbsettings.clear_spinner()
    tbsettings.restore_statusline(wrapped)
    print("thinking-book is off. Stock spinner verbs restored." + (" Your status line is back." if wrapped else ""))


def cmd_line(_args):
    line = current_line()
    if line:
        print(line)


def cmd_version(_args):
    """Which copy is actually running -- the answer when docs and behaviour disagree."""
    print("thinking-book %s" % version())
    print("running from %s" % plugin_root())


# ---------------------------------------------------------------------------- hooks

def _feeds_due():
    now = time.time()
    for entry in load_feeds()["feeds"]:
        if now - float(entry.get("last_checked") or 0) >= FEED_REFRESH_SECONDS:
            return True
    return False


def _spawn_feed_refresh():
    """Refresh feeds out of band -- SessionStart must not wait on the network."""
    import subprocess
    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "refresh-feeds"],
            stdout=devnull, stderr=devnull, stdin=devnull,
            start_new_session=True,
        )


def _report(args, message):
    """Hooks pass --quiet and stay silent; a person running the same command gets a line."""
    if "--quiet" not in args:
        print(message)


def cmd_refresh_feeds(args):
    added = refresh_feeds()
    sync_spinner()
    _report(args, "Feeds refreshed; %d new item(s) queued." % added)


def cmd_sync(args):
    """SessionStart: make sure the plumbing exists, then show where we left off."""
    tbstate.ensure_home()
    tbsettings.ensure_settings_file()
    config = tbstate.load_config()
    tbstate.write_hot_env(config)
    if tbstate.stream_count() == 0:
        tbstate.rebuild_stream()
    sync_spinner(config)
    if not config["paused"] and _feeds_due():
        try:
            _spawn_feed_refresh()
        except Exception:
            pass
    _report(args, current_line() or "Nothing queued -- try /book gutenberg <title>.")


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
    elif mode == "timer" and not config["surfaces"]["statusline"]:
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
    "load": cmd_load, "gutenberg": cmd_gutenberg, "libby": cmd_libby, "read": cmd_read,
    "feed": cmd_feed, "queue": cmd_queue, "status": cmd_status, "mode": cmd_mode,
    "dwell": cmd_dwell, "pause": cmd_pause, "resume": cmd_resume, "pane": cmd_pane,
    "off": cmd_off, "next": cmd_next, "back": cmd_back, "line": cmd_line,
    "repair": cmd_repair, "refresh": cmd_refresh, "version": cmd_version,
    "sync": cmd_sync, "advance": cmd_advance, "restore": cmd_restore,
    "refresh-feeds": cmd_refresh_feeds,
}


def main(argv):
    if not argv:
        print(__doc__.strip())
        print("\nCommands: %s" % ", ".join(sorted(COMMANDS)))
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
        handler(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    except Exception as exc:
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
