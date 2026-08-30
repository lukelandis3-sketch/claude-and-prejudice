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
import tempfile
import time
import shutil
from contextlib import contextmanager

DEFAULT_CONFIG = {
    "mode": "timer",            # timer | turn | manual
    "dwell_seconds": 8,
    "paused": False,
    "surfaces": {"statusline": True, "spinner": True},
    "wrapped_statusline": None,  # the user's own statusLine command, if we wrapped one
    "statusline_refresh_interval": None,  # written only where the CLI version supports it
    "prefix": "",
}

VALID_MODES = ("timer", "turn", "manual")
STREAM_SHARD_LINES = 256


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
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})
    if merged.get("mode") not in VALID_MODES:
        merged["mode"] = DEFAULT_CONFIG["mode"]
    try:
        merged["dwell_seconds"] = max(1, int(merged.get("dwell_seconds", 8)))
    except (TypeError, ValueError):
        merged["dwell_seconds"] = DEFAULT_CONFIG["dwell_seconds"]
    surfaces = merged.get("surfaces") or {}
    merged["surfaces"] = {
        "statusline": bool(surfaces.get("statusline", True)),
        "spinner": bool(surfaces.get("spinner", True)),
    }
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
        "TB_PAUSED=%s" % ("1" if config["paused"] else "0"),
        "TB_STATUSLINE=%s" % ("1" if config["surfaces"]["statusline"] else "0"),
        "TB_PREFIX=%s" % _shell_quote(config.get("prefix") or ""),
    ]
    atomic_write(path("hot.env"), "\n".join(lines) + "\n")


def save_config(config):
    write_json(path("config.json"), config)
    write_hot_env(config)


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
    return _read(path("stream.gen"), "").strip()


def stream_generation_dir(generation=None):
    generation = generation or stream_generation()
    return path("stream-generations", generation) if generation else ""


def stream_count():
    """Line count of the reading stream, from the cache written by rebuild_stream."""
    raw = _read(path("count"), "").strip()
    if raw.isdigit():
        return int(raw)
    try:
        with open(stream_path(), "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def stream_line(index):
    """1-based lookup into the reading stream. Empty string when out of range."""
    if index < 1:
        return ""
    generation = stream_generation()
    if generation:
        shard = (index - 1) // STREAM_SHARD_LINES
        row = (index - 1) % STREAM_SHARD_LINES + 1
        target = os.path.join(stream_generation_dir(generation), "%d.txt" % shard)
        try:
            with open(target, encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if number == row:
                        return line.rstrip("\n")
        except OSError:
            return ""
        return ""
    # Migration fallback: SessionStart rebuilds old streams into a generation.
    try:
        with open(stream_path(), encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if number == index:
                    return line.rstrip("\n")
    except OSError:
        return ""
    return ""


def load_queue():
    queue = read_json(path("queue.json"), {"items": []})
    items = queue.get("items")
    return {"items": list(items) if isinstance(items, list) else []}


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


def rebuild_stream():
    """Flatten every queued item into one stream file plus an index of item offsets.

    Doing this at import time is what keeps statusline.sh to a single `awk` lookup.
    """
    ensure_home()
    queue = load_queue()
    chunks, index_rows, line_no = [], [], 1
    for item_id in queue["items"]:
        raw = _read(item_fragments_path(item_id), "")
        lines = [ln for ln in raw.split("\n") if ln.strip()]
        if not lines:
            continue
        meta = item_meta(item_id)
        title = (meta.get("title") or item_id).replace("\t", " ")
        kind = meta.get("kind", "text")
        index_rows.append("%d\t%s\t%s\t%s" % (line_no, item_id, kind, title))
        chunks.extend(lines)
        line_no += len(lines)
    atomic_write(stream_path(), ("\n".join(chunks) + "\n") if chunks else "")
    atomic_write(path("stream.idx"), ("\n".join(index_rows) + "\n") if index_rows else "")
    # Cached so the hot path never has to count lines in a whole novel.
    atomic_write(path("count"), "%d\n" % len(chunks))
    _publish_stream_generation(chunks)
    return len(chunks)


def _publish_stream_generation(lines):
    """Publish immutable bounded-size lookup shards through one atomic pointer."""
    root = path("stream-generations")
    os.makedirs(root, exist_ok=True)
    generation = "%x-%x" % (time.time_ns(), os.getpid())
    target = os.path.join(root, generation)
    os.makedirs(target)
    for start in range(0, len(lines), STREAM_SHARD_LINES):
        shard = lines[start:start + STREAM_SHARD_LINES]
        atomic_write(
            os.path.join(target, "%d.txt" % (start // STREAM_SHARD_LINES)),
            "\n".join(shard) + "\n",
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
            shutil.rmtree(obsolete)
        except OSError:
            pass


def load_index():
    """[(start_line, item_id, kind, title)] for every item in the stream."""
    rows = []
    for line in _read(path("stream.idx"), "").split("\n"):
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


def item_at(index):
    """Which queued item does stream line `index` belong to?"""
    current = None
    for row in load_index():
        if row[0] <= index:
            current = row
        else:
            break
    return current
