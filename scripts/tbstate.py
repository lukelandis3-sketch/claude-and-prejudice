"""Paths, state files, and safe writes for thinking-book.

Two kinds of state live side by side, deliberately:

  * JSON files (config.json, queue.json) are the human-readable record. Python owns them.
  * Flat single-value files (pos, last) are the hot path. statusline.sh reads and writes
    them on every assistant message, so they must be parseable with a single `cat`.

Nothing is duplicated between the two -- each value has exactly one home.
"""

import errno
import fcntl
import json
import os
import re
import time
from contextlib import contextmanager

DEFAULT_CONFIG = {
    "mode": "timer",            # timer | turn | manual
    "dwell_seconds": 8,
    "words_per_minute": 250,     # None preserves the legacy fixed-second timer
    "paused": False,
    "surfaces": {"statusline": True, "spinner": True},
    "wrapped_statusline": None,  # the user's own statusLine command, if we wrapped one
    "statusline_refresh_interval": None,  # written only where the CLI version supports it
    "prefix": "",
    "hud": False,
}

VALID_MODES = ("timer", "turn", "manual")
STREAM_SHARD_LINES = 256
STREAM_FORMAT = "2"  # word-count prefix + tab + prose


def config_dir():
    """Claude Code's config directory, honouring CLAUDE_CONFIG_DIR."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), ".claude")


def home():
    return os.path.join(config_dir(), "thinking-book")


def path(*parts):
    return os.path.join(home(), *parts)


def settings_path():
    return os.path.join(config_dir(), "settings.json")


def ensure_home():
    os.makedirs(path("library"), exist_ok=True)
    return home()


# --------------------------------------------------------------------------- writes

def atomic_write(target, text):
    """Write via a temp file in the same directory, then rename. Never a partial file."""
    import tempfile
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".tb-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_bytes(target, data):
    """Binary twin of atomic_write, used when byte-for-byte preservation matters."""
    import tempfile
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".tb-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def locked(name="tb.lock"):
    """Advisory lock so concurrent Claude Code sessions cannot interleave writes."""
    ensure_home()
    lock_file = path(name)
    fh = open(lock_file, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _read(target, default=""):
    try:
        with open(target, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ENOTDIR):
            raise
        return default


def read_json(target, default):
    raw = _read(target, "")
    if not raw.strip():
        return json.loads(json.dumps(default))
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(json.dumps(default))


def write_json(target, data):
    # One key per line: statusline.sh greps this file rather than parsing JSON.
    atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# -------------------------------------------------------------------------- config

def load_config():
    config = read_json(path("config.json"), DEFAULT_CONFIG)
    legacy_fixed = (isinstance(config, dict) and "dwell_seconds" in config
                    and "words_per_minute" not in config)
    if not isinstance(config, dict):
        config = {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})
    if merged.get("mode") not in VALID_MODES:
        merged["mode"] = DEFAULT_CONFIG["mode"]
    try:
        merged["dwell_seconds"] = min(
            86400, max(1, int(merged.get("dwell_seconds", 8))))
    except (TypeError, ValueError):
        merged["dwell_seconds"] = DEFAULT_CONFIG["dwell_seconds"]
    if legacy_fixed:
        merged["words_per_minute"] = None
    elif merged.get("words_per_minute") is not None:
        try:
            merged["words_per_minute"] = max(
                30, min(1000, int(merged["words_per_minute"])))
        except (TypeError, ValueError):
            merged["words_per_minute"] = DEFAULT_CONFIG["words_per_minute"]
    surfaces = merged.get("surfaces")
    surfaces = surfaces if isinstance(surfaces, dict) else {}
    merged["surfaces"] = {
        "statusline": bool(surfaces.get("statusline", True)),
        "spinner": bool(surfaces.get("spinner", True)),
    }
    hud = merged.get("hud")
    merged["hud"] = hud if isinstance(hud, bool) else DEFAULT_CONFIG["hud"]
    return merged


def _shell_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def write_hot_env(config):
    """Mirror the values statusline.sh needs into a shell-sourceable file.

    The hot path runs on every assistant message, so it must not parse JSON. Python is
    the only writer of this file and config.json is the source of truth; this is a
    derived cache, refreshed on every save_config.
    """
    lines = [
        "TB_MODE=%s" % _shell_quote(config["mode"]),
        "TB_DWELL=%s" % _shell_quote(int(config["dwell_seconds"])),
        "TB_WPM=%s" % _shell_quote(int(config.get("words_per_minute") or 0)),
        "TB_PAUSED=%s" % ("1" if config["paused"] else "0"),
        "TB_STATUSLINE=%s" % ("1" if config["surfaces"]["statusline"] else "0"),
        "TB_PREFIX=%s" % _shell_quote(config.get("prefix") or ""),
        "TB_HUD=%s" % ("1" if config.get("hud") else "0"),
    ]
    atomic_write(path("hot.env"), "\n".join(lines) + "\n")
    # The Stop hook needs only four enum/boolean fields. A fixed, non-executable record
    # lets POSIX sh read them with one builtin instead of sourcing a cache or starting
    # Python on every assistant response.
    atomic_write(path("stop.control"), "%s %d %d %d\n" % (
        config["mode"],
        1 if config["paused"] else 0,
        1 if config["surfaces"]["statusline"] else 0,
        1 if config["surfaces"]["spinner"] else 0,
    ))


def save_config(config):
    # Until the new derived state and spinner line have both been published, force the
    # Stop dispatcher through the full correctness path.
    try:
        os.unlink(path("spinner.cursor"))
    except OSError:
        pass
    write_json(path("config.json"), config)
    write_hot_env(config)


def update_config(mutator):
    """Locked read-modify-write for commands shared by concurrent sessions."""
    with locked("config.lock"):
        config = load_config()
        before = json.loads(json.dumps(config))
        mutator(config)
        if (config != before or not os.path.exists(path("config.json"))
                or not os.path.exists(path("hot.env"))):
            save_config(config)
        return config


# ------------------------------------------------------------------------ position

def read_pos():
    raw = _read(path("pos"), "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def write_pos(index):
    atomic_write(path("pos"), "%d\n" % max(1, int(index)))


def read_last_advance():
    raw = _read(path("last"), "0").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def write_last_advance(when=None):
    atomic_write(path("last"), "%d\n" % int(when if when is not None else time.time()))


# -------------------------------------------------------------------------- stream

def stream_path():
    return path("stream.txt")


def stream_generation():
    generation = _read(path("stream.gen"), "").strip()
    return generation if re.fullmatch(r"[0-9a-f-]+", generation) else ""


def stream_generation_dir(generation=None):
    generation = generation or stream_generation()
    return path("stream-generations", generation) if generation else ""


def stream_has_word_counts(generation=None):
    directory = stream_generation_dir(generation)
    return bool(directory and _read(os.path.join(directory, "format"), "").strip()
                == STREAM_FORMAT)


def stream_has_index(generation=None):
    directory = stream_generation_dir(generation)
    return bool(directory and os.path.isfile(os.path.join(directory, "index")))


def _decode_stream_record(record):
    prefix, separator, prose = record.partition("\t")
    if separator and prefix.isdigit():
        return min(1000, max(1, int(prefix))), prose
    return min(1000, max(1, len(record.split()))), record


def stream_count():
    """Line count of the reading stream, from the cache written by rebuild_stream."""
    generation_dir = stream_generation_dir()
    raw = _read(os.path.join(generation_dir, "count"), "").strip() if generation_dir else ""
    if raw.isdigit():
        return int(raw)
    # Migration fallback for streams created before generation directories.
    raw = _read(path("count"), "").strip()
    if raw.isdigit():
        return int(raw)
    try:
        with open(stream_path(), "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def stream_record(index):
    """Return (word count, prose) for a 1-based stream position."""
    if index < 1:
        return 0, ""
    generation = stream_generation()
    if generation:
        shard = (index - 1) // STREAM_SHARD_LINES
        row = (index - 1) % STREAM_SHARD_LINES + 1
        target = os.path.join(stream_generation_dir(generation), "%d.txt" % shard)
        try:
            with open(target, encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if number == row:
                        return _decode_stream_record(line.rstrip("\n"))
        except OSError:
            return 0, ""
        return 0, ""
    # Migration fallback: SessionStart rebuilds old streams into a generation.
    try:
        with open(stream_path(), encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if number == index:
                    return _decode_stream_record(line.rstrip("\n"))
    except OSError:
        return 0, ""
    return 0, ""


def stream_line(index):
    """1-based prose lookup. Empty string when out of range."""
    return stream_record(index)[1]


def stream_window(start, end):
    """Return a bounded inclusive prose window without scanning the whole stream."""
    total = stream_count()
    start = max(1, int(start))
    end = min(total, int(end))
    if end < start:
        return []

    generation = stream_generation()
    if not generation:
        rows = []
        try:
            with open(stream_path(), encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if number > end:
                        break
                    if number >= start:
                        rows.append(_decode_stream_record(line.rstrip("\n"))[1])
        except OSError:
            return []
        return rows

    directory = stream_generation_dir(generation)
    rows = []
    first_shard = (start - 1) // STREAM_SHARD_LINES
    last_shard = (end - 1) // STREAM_SHARD_LINES
    for shard in range(first_shard, last_shard + 1):
        shard_start = shard * STREAM_SHARD_LINES + 1
        target = os.path.join(directory, "%d.txt" % shard)
        try:
            with open(target, encoding="utf-8") as fh:
                for offset, line in enumerate(fh):
                    position = shard_start + offset
                    if position > end:
                        break
                    if position >= start:
                        rows.append(_decode_stream_record(line.rstrip("\n"))[1])
        except OSError:
            return []
    return rows


def load_queue():
    queue = read_json(path("queue.json"), {"items": []})
    items = queue.get("items")
    if not isinstance(items, list):
        return {"items": []}
    valid = (item for item in items if isinstance(item, str) and item)
    return {"items": list(dict.fromkeys(valid))}


def save_queue(queue):
    write_json(path("queue.json"), queue)


def item_meta(item_id):
    return read_json(path("library", item_id + ".json"), {})


def item_fragments_path(item_id):
    return path("library", item_id + ".txt")


def save_item(item_id, meta, fragments):
    ensure_home()
    atomic_write(item_fragments_path(item_id), "\n".join(fragments) + "\n")
    meta = dict(meta)
    meta["fragments"] = len(fragments)
    write_json(path("library", item_id + ".json"), meta)


def progress_bar(offset, total, width=10):
    """A fixed-width reading bar safe for both Python output and precomputed HUD rows."""
    total = max(1, int(total))
    offset = max(1, min(int(offset), total))
    filled = max(1, min(width, (offset * width) // total))
    return "█" * filled + "░" * (width - filled)


def timer_interval(words, config):
    """Seconds for this fragment: word-proportional WPM, bounded for readable UI."""
    wpm = config.get("words_per_minute")
    if not wpm:
        return max(1, int(config.get("dwell_seconds") or 1))
    words = max(1, int(words or 1))
    seconds = (words * 60 + int(wpm) - 1) // int(wpm)
    return max(2, min(30, seconds))


def _hud_title(item_id, title, limit=38):
    title = re.sub(r"[\x00-\x1f\x7f]+", " ", str(title or ""))
    title = " ".join(title.split()) or item_id
    return title if len(title) <= limit else title[:limit - 1].rstrip() + "…"


def hud_line(item_id, title, offset, total):
    percent = (max(1, min(offset, total)) * 100) // max(1, total)
    return "%s · %s %d/%d (%d%%)" % (
        _hud_title(item_id, title), progress_bar(offset, total), offset, total, percent)


def rebuild_stream(include_hud=None):
    """Flatten every queued item into one stream file plus an index of item offsets.

    Doing this at import time is what keeps statusline.sh to a single `awk` lookup.
    """
    ensure_home()
    include_hud = load_config()["hud"] if include_hud is None else bool(include_hud)
    queue = load_queue()
    chunks, index_rows, line_no = [], [], 1
    hud_rows = [] if include_hud else None
    for item_id in queue["items"]:
        raw = _read(item_fragments_path(item_id), "")
        lines = [ln for ln in raw.split("\n") if ln.strip()]
        if not lines:
            continue
        meta = item_meta(item_id)
        meta = meta if isinstance(meta, dict) else {}
        title = _hud_title(item_id, meta.get("title"), limit=10_000)
        kind = meta.get("kind", "text")
        index_rows.append("%d\t%s\t%s\t%s" % (line_no, item_id, kind, title))
        chunks.extend(lines)
        if hud_rows is not None:
            hud_rows.extend(hud_line(item_id, title, offset, len(lines))
                            for offset in range(1, len(lines) + 1))
        line_no += len(lines)
    _publish_stream_generation(chunks, hud_rows, index_rows)
    # These pre-generation caches duplicate the complete book. Keep read fallbacks above
    # for upgrades, but retire them after a successful self-contained publication.
    for legacy in (stream_path(), path("stream.idx"), path("count")):
        try:
            os.unlink(legacy)
        except OSError:
            pass
    return len(chunks)


def _publish_stream_generation(lines, hud_rows, index_rows):
    """Publish immutable bounded-size lookup shards through one atomic pointer."""
    root = path("stream-generations")
    os.makedirs(root, exist_ok=True)
    generation = "%x-%x" % (time.time_ns(), os.getpid())
    target = os.path.join(root, generation)
    os.makedirs(target)
    atomic_write(os.path.join(target, "format"), STREAM_FORMAT + "\n")
    atomic_write(
        os.path.join(target, "index"),
        ("\n".join(index_rows) + "\n") if index_rows else "",
    )
    for start in range(0, len(lines), STREAM_SHARD_LINES):
        shard = lines[start:start + STREAM_SHARD_LINES]
        records = ["%d\t%s" % (min(1000, max(1, len(line.split()))), line)
                   for line in shard]
        atomic_write(
            os.path.join(target, "%d.txt" % (start // STREAM_SHARD_LINES)),
            "\n".join(records) + "\n",
        )
        if hud_rows is not None:
            atomic_write(
                os.path.join(target, "%d.hud" % (start // STREAM_SHARD_LINES)),
                "\n".join(hud_rows[start:start + STREAM_SHARD_LINES]) + "\n",
            )
    atomic_write(os.path.join(target, "count"), "%d\n" % len(lines))
    atomic_write(path("stream.gen"), generation + "\n")

    # The previous generation covers a reader that observed the old pointer immediately
    # before publication. Anything older can no longer be referenced.
    generations = []
    for name in os.listdir(root):
        candidate = os.path.join(root, name)
        if os.path.isdir(candidate):
            try:
                generations.append((os.stat(candidate).st_mtime_ns, candidate))
            except OSError:
                pass
    for _mtime, obsolete in sorted(generations, reverse=True)[2:]:
        try:
            import shutil
            shutil.rmtree(obsolete)
        except OSError:
            pass


def _parse_index(raw):
    rows = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            rows.append((int(parts[0]), parts[1], parts[2], parts[3]))
        except ValueError:
            continue
    return rows


def ensure_hud_shards():
    """Add missing HUD siblings to the live generation without republishing prose."""
    with locked():
        generation = stream_generation()
        directory = stream_generation_dir(generation) if generation else ""
        if not directory or not os.path.isdir(directory):
            return False
        raw_count = _read(os.path.join(directory, "count"), "").strip()
        if not re.fullmatch(r"[0-9]{1,12}", raw_count):
            return False
        total = int(raw_count)
        raw_index = _read(os.path.join(directory, "index"), "")
        index_lines = [line for line in raw_index.split("\n") if line.strip()]
        rows = _parse_index(raw_index)
        if len(rows) != len(index_lines):
            return False
        if (total and not rows) or (rows and rows[0][0] != 1):
            return False

        text_shards = []
        for name in os.listdir(directory):
            match = re.fullmatch(r"([0-9]+)\.txt", name)
            if match:
                text_shards.append(int(match.group(1)))
        expected_shards = (total + STREAM_SHARD_LINES - 1) // STREAM_SHARD_LINES
        if (len(text_shards) != expected_shards
                or (text_shards and (min(text_shards) != 0
                                     or max(text_shards) != expected_shards - 1))):
            return False
        missing = [start for start in range(0, total, STREAM_SHARD_LINES)
                   if not os.path.isfile(os.path.join(
                       directory, "%d.hud" % (start // STREAM_SHARD_LINES)))]
        if not missing:
            return True

        hud_rows = []
        for offset, row in enumerate(rows):
            end = rows[offset + 1][0] - 1 if offset + 1 < len(rows) else total
            if row[0] < 1 or end < row[0] or end > total:
                return False
            length = end - row[0] + 1
            hud_rows.extend(hud_line(row[1], row[3], line, length)
                            for line in range(1, length + 1))
        if len(hud_rows) != total:
            return False

        for start in missing:
            target = os.path.join(directory, "%d.hud" % (start // STREAM_SHARD_LINES))
            atomic_write(
                target,
                "\n".join(hud_rows[start:start + STREAM_SHARD_LINES]) + "\n",
            )
        return True


def load_index():
    """[(start_line, item_id, kind, title)] for every item in the stream."""
    generation_dir = stream_generation_dir()
    target = os.path.join(generation_dir, "index") if generation_dir else ""
    if not target or not os.path.isfile(target):
        target = path("stream.idx")
    return _parse_index(_read(target, ""))


def item_at(index, rows=None):
    """Which queued item does stream line `index` belong to?"""
    current = None
    for row in (rows if rows is not None else load_index()):
        if row[0] <= index:
            current = row
        else:
            break
    return current


def item_bounds(item_id, rows=None, total=None):
    """Return inclusive (start, end) bounds for an indexed item."""
    rows = rows if rows is not None else load_index()
    total = stream_count() if total is None else total
    for offset, row in enumerate(rows):
        if row[1] != item_id:
            continue
        end = rows[offset + 1][0] - 1 if offset + 1 < len(rows) else total
        return row[0], max(row[0], end)
    return None


def locate_position(index, rows=None, total=None):
    """Map a global cursor to (item_id, one-based relative offset)."""
    rows = rows if rows is not None else load_index()
    total = stream_count() if total is None else total
    current = None
    for row in rows:
        if row[0] <= index:
            current = row
        else:
            break
    if not current:
        return None
    bounds = item_bounds(current[1], rows=rows, total=total)
    relative = max(1, min(index, bounds[1]) - bounds[0] + 1)
    return current[1], relative


def resolve_position(item_id, offset=1, rows=None, total=None):
    """Map an item bookmark back to a clamped global cursor."""
    bounds = item_bounds(item_id, rows=rows, total=total)
    if not bounds:
        return None
    try:
        offset = max(1, int(offset))
    except (TypeError, ValueError):
        offset = 1
    return min(bounds[0] + offset - 1, bounds[1])


def load_bookmarks():
    data = read_json(path("bookmarks.json"), {})
    return data if isinstance(data, dict) else {}


def save_bookmark(item_id, offset):
    if not item_id:
        return
    bookmarks = load_bookmarks()
    bookmarks[item_id] = max(1, int(offset))
    write_json(path("bookmarks.json"), bookmarks)


def capture_position(rows=None):
    """Persist and return the active item's logical bookmark."""
    position = read_pos()
    rows = load_index() if rows is None else rows
    current = item_at(position, rows=rows)
    # Derive the relative offset from the index start rather than the cached stream
    # count. A damaged count file should not move the reader during a repair rebuild;
    # restore_position clamps a deliberately unbounded stored offset against the new
    # complete stream.
    logical = (current[1], max(1, position - current[0] + 1)) if current else None
    if logical:
        save_bookmark(*logical)
    return logical


def restore_position(logical, old_items=None):
    """Restore a logical cursor after rebuilding, with a deterministic removal fallback."""
    queue = load_queue()["items"]
    target = logical[0] if logical else None
    offset = logical[1] if logical else 1
    position = resolve_position(target, offset) if target in queue else None

    if position is None and target and old_items and target in old_items:
        removed_at = old_items.index(target)
        later = [item for item in old_items[removed_at + 1:] if item in queue]
        earlier = [item for item in old_items[:removed_at] if item in queue]
        fallback = later[0] if later else earlier[-1] if earlier else None
        if fallback:
            position = resolve_position(fallback, load_bookmarks().get(fallback, 1))
    if position is None:
        position = 1
    write_pos(position)
    write_last_advance()
    current = locate_position(position)
    if current:
        save_bookmark(*current)
    return position


@contextmanager
def rebuilding_stream(include_hud=None):
    """Lock, snapshot the logical bookmark, then rebuild and restore it safely."""
    with locked():
        logical = capture_position()
        old_items = list(load_queue()["items"])
        yield logical, old_items
        rebuild_stream(include_hud=include_hud)
        restore_position(logical, old_items=old_items)
