"""Safe, surgical edits to ~/.claude/settings.json.

Claude Code watches this file with chokidar and invalidates its settings cache on any
external write, which is what lets a hook change the spinner text mid-session. That also
means every write here is observed, so writes must be atomic and must never lose a key
the user put there themselves.
"""

import json
import os
import time

import tbstate

SPINNER_KEY = "spinnerVerbs"
STATUSLINE_KEY = "statusLine"
_MISSING = object()


class SettingsError(ValueError):
    """The live settings file is unsafe to edit."""


def backup_path():
    return tbstate.path("settings.backup.json")


def raw_backup_path():
    return tbstate.path("settings.backup.raw")


def origins_path():
    return tbstate.path("settings.origins.json")


def written_path():
    return tbstate.path("settings.written.json")


def migration_path():
    return tbstate.path("settings.legacy-pending.json")


def ensure_settings_file():
    """Create settings.json if absent.

    Claude Code only watches directories that already contained a settings file when the
    session started, so an absent file means our later writes would go unnoticed.
    """
    target = tbstate.settings_path()
    if os.path.exists(target):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tbstate.atomic_write(target, "{}\n")
    return True


def read_settings():
    target = tbstate.settings_path()
    try:
        with open(target, "rb") as fh:
            raw = fh.read()
    except OSError:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SettingsError(
            "%s is not valid JSON; thinking-book left it unchanged. Repair that file "
            "and try again." % target
        ) from exc
    if not isinstance(value, dict):
        raise SettingsError(
            "%s must contain a JSON object; thinking-book left it unchanged." % target
        )
    return value


def _backup_once(settings):
    target = backup_path()
    if os.path.exists(target):
        return
    tbstate.ensure_home()
    tbstate.write_json(target, settings)


def _raw_backup_once(raw):
    target = raw_backup_path()
    if os.path.exists(target):
        return
    tbstate.ensure_home()
    tbstate.atomic_write_bytes(target, raw)
    tbstate.write_json(tbstate.path("settings.backup.meta.json"), {
        "source": tbstate.settings_path(),
        "taken_at": int(time.time()),
        "note": "Exact bytes at the first settings mutation by thinking-book v0.4+.",
    })


def _legacy_original(key):
    if not os.path.exists(backup_path()):
        return _MISSING
    backup = tbstate.read_json(backup_path(), {})
    if isinstance(backup, dict) and key in backup:
        return backup[key]
    return _MISSING


def _legacy_pending():
    if not os.path.exists(migration_path()):
        _initialize_legacy_pending()
    data = tbstate.read_json(migration_path(), {"keys": []})
    keys = data.get("keys") if isinstance(data, dict) else []
    return list(keys) if isinstance(keys, list) else []


def _initialize_legacy_pending():
    if os.path.exists(migration_path()):
        return
    # A JSON backup without a v0.4 raw backup is the pre-v0.4 state. Preserve the legacy
    # original independently for each key; the keys may first be touched on different turns.
    keys = ([SPINNER_KEY, STATUSLINE_KEY]
            if os.path.exists(backup_path()) and not os.path.exists(raw_backup_path()) else [])
    tbstate.write_json(migration_path(), {"keys": keys})


def _consume_legacy(key):
    pending = _legacy_pending()
    if key in pending:
        pending = [item for item in pending if item != key]
        tbstate.write_json(migration_path(), {"keys": pending})


def _remember_origins(settings, keys):
    origins = tbstate.read_json(origins_path(), {})
    pending = _legacy_pending()
    changed = False
    for key in keys:
        if key in origins:
            continue
        legacy = _legacy_original(key)
        if key in pending:
            present, value = legacy is not _MISSING, legacy
            _consume_legacy(key)
        else:
            present, value = key in settings, settings.get(key)
        origins[key] = {"present": present}
        if present:
            origins[key]["value"] = value
        changed = True
    if changed:
        tbstate.write_json(origins_path(), origins)


def _retire(keys):
    """End one enable/restore cycle so the next activation snapshots fresh values."""
    origins = tbstate.read_json(origins_path(), {})
    written = tbstate.read_json(written_path(), {})
    origins_changed = written_changed = False
    for key in keys:
        if isinstance(origins, dict) and key in origins:
            origins.pop(key)
            origins_changed = True
        if isinstance(written, dict) and key in written:
            written.pop(key)
            written_changed = True
        _consume_legacy(key)
    # Keep the files (even empty) to distinguish a completed migration cycle from a
    # pre-v0.4 install that has only settings.backup.json.
    if origins_changed or not os.path.exists(origins_path()):
        tbstate.write_json(origins_path(), origins if isinstance(origins, dict) else {})
    if written_changed or not os.path.exists(written_path()):
        tbstate.write_json(written_path(), written if isinstance(written, dict) else {})


def _original(key, fallback=_MISSING):
    origins = tbstate.read_json(origins_path(), {})
    record = origins.get(key) if isinstance(origins, dict) else None
    if isinstance(record, dict) and "present" in record:
        return record.get("value") if record["present"] else _MISSING
    if key in _legacy_pending():
        return _legacy_original(key)
    return fallback


def _record_written(key, value):
    written = tbstate.read_json(written_path(), {})
    written[key] = value
    tbstate.write_json(written_path(), written)


def _last_written(key):
    written = tbstate.read_json(written_path(), {})
    return written.get(key, _MISSING) if isinstance(written, dict) else _MISSING


def _owns_key(key):
    origins = tbstate.read_json(origins_path(), {})
    return (
        _last_written(key) is not _MISSING or
        (isinstance(origins, dict) and key in origins) or
        key in _legacy_pending()
    )


def update(mutator, touched=(), record_key=None, retire=()):
    """Read-modify-write settings.json under a lock, backing it up before the first edit.

    `mutator` receives the settings dict and mutates it in place.
    """
    with tbstate.locked("settings.lock"):
        ensure_settings_file()
        _initialize_legacy_pending()
        settings = read_settings()
        before = json.loads(json.dumps(settings))
        mutator(settings)
        if settings == before:
            if retire:
                _retire(retire)
            return settings, False
        with open(tbstate.settings_path(), "rb") as fh:
            raw = fh.read()
        _raw_backup_once(raw)
        _backup_once(before)
        _remember_origins(before, touched)
        tbstate.write_json(tbstate.settings_path(), settings)
        if record_key is not None:
            _record_written(record_key, settings[record_key])
        if retire:
            _retire(retire)
        return settings, True


def set_spinner_line(line):
    """Point spinnerVerbs at exactly one verb.

    Claude Code samples the verb list at random, so a single-element list is the only way
    to make the choice deterministic and read a book in order.
    """
    line = (line or "").strip()
    if not line:
        return clear_spinner()

    def mutate(settings):
        settings[SPINNER_KEY] = {"mode": "replace", "verbs": [line]}

    settings, _changed = update(
        mutate, touched=(SPINNER_KEY,), record_key=SPINNER_KEY
    )
    return settings


def clear_spinner():
    """Put back whatever spinnerVerbs the user had before we first touched settings.

    Someone may already have had their own custom verbs; deleting the key outright would
    destroy them, which is not what "/book off restores every key we touched" promises.
    """
    original = _original(SPINNER_KEY)
    last_written = _last_written(SPINNER_KEY)
    owned = _owns_key(SPINNER_KEY)

    def mutate(settings):
        live = settings.get(SPINNER_KEY, _MISSING)
        if not owned:
            return
        if live is _MISSING or (last_written is not _MISSING and live != last_written):
            return
        if original is not _MISSING:
            settings[SPINNER_KEY] = original
        else:
            settings.pop(SPINNER_KEY, None)

    settings, _changed = update(mutate, retire=(SPINNER_KEY,))
    return settings


def current_statusline():
    return read_settings().get(STATUSLINE_KEY)


def set_statusline(command, padding=None, refresh_interval=None):
    """Install our status line command.

    `refresh_interval` is written only when set. It is not in every Claude Code version's
    settings schema; versions that do not know it ignore it, and it is what gives timer
    mode a real wall clock on versions that do.
    """

    def mutate(settings):
        entry = {"type": "command", "command": command}
        # Carry forward padding the user set; replacing the entry wholesale would drop it
        # on every `refresh` and on any second `pane on`.
        existing = settings.get(STATUSLINE_KEY)
        if padding is None and isinstance(existing, dict) and "padding" in existing:
            entry["padding"] = existing["padding"]
        if padding is not None:
            entry["padding"] = padding
        if refresh_interval is not None:
            entry["refreshInterval"] = refresh_interval
        settings[STATUSLINE_KEY] = entry

    settings, _changed = update(
        mutate, touched=(STATUSLINE_KEY,), record_key=STATUSLINE_KEY
    )
    return settings


def install_statusline(command, is_ours, auto=False, refresh_interval=None):
    """Atomically decide whether to install/wrap a status line.

    Returns (enabled, original_entry, settings). Automatic callers never replace a
    third-party entry; explicit callers return it so the hot path can wrap it.
    """
    outcome = {"enabled": False, "original": None}

    def mutate(settings):
        raw = settings.get(STATUSLINE_KEY, _MISSING)
        existing = ({"type": "command", "command": raw}
                    if isinstance(raw, str) else raw if isinstance(raw, dict) else None)
        if auto and existing and not is_ours(existing):
            outcome["original"] = existing
            return
        if existing and not is_ours(existing):
            outcome["original"] = existing

        entry = {"type": "command", "command": command}
        if existing and "padding" in existing:
            entry["padding"] = existing["padding"]
        if refresh_interval is not None:
            entry["refreshInterval"] = refresh_interval
        settings[STATUSLINE_KEY] = entry
        outcome["enabled"] = True

    settings, changed = update(
        mutate, touched=(STATUSLINE_KEY,),
        record_key=STATUSLINE_KEY,
    )
    return outcome["enabled"], outcome["original"], settings, changed


def restore_statusline(original):
    """Put back whatever the user had before we wrapped it (or remove ours)."""

    saved = _original(STATUSLINE_KEY, fallback=original if original is not None else _MISSING)
    last_written = _last_written(STATUSLINE_KEY)
    owned = _owns_key(STATUSLINE_KEY) or original is not None

    def mutate(settings):
        if not owned:
            return
        live = settings.get(STATUSLINE_KEY, _MISSING)
        if last_written is not _MISSING and live != last_written:
            return
        if saved is not _MISSING:
            settings[STATUSLINE_KEY] = saved
        else:
            settings.pop(STATUSLINE_KEY, None)

    settings, _changed = update(mutate, retire=(STATUSLINE_KEY,))
    return settings


def diff_against_backup():
    """Keys that differ between the backup and the live file -- used by the tests."""
    if not os.path.exists(backup_path()):
        return {}
    origins = tbstate.read_json(origins_path(), {})
    before = tbstate.read_json(backup_path(), {})
    if isinstance(origins, dict):
        for key, record in origins.items():
            if not isinstance(record, dict) or "present" not in record:
                continue
            if record["present"]:
                before[key] = record.get("value")
            else:
                before.pop(key, None)
    after = read_settings()
    changed = {}
    for key in set(before) | set(after):
        if json.dumps(before.get(key), sort_keys=True) != json.dumps(after.get(key), sort_keys=True):
            changed[key] = (before.get(key), after.get(key))
    return changed
