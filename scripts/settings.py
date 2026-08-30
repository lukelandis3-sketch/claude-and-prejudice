"""Safe, surgical edits to ~/.claude/settings.json.

Claude Code watches this file with chokidar and invalidates its settings cache on any
external write, which is what lets a hook change the spinner text mid-session. That also
means every write here is observed, so writes must be atomic and must never lose a key
the user put there themselves.
"""

import json
import os

import tbstate

SPINNER_KEY = "spinnerVerbs"
STATUSLINE_KEY = "statusLine"


def backup_path():
    return tbstate.path("settings.backup.json")


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
    return tbstate.read_json(tbstate.settings_path(), {})


def _backup_once(settings):
    target = backup_path()
    if os.path.exists(target):
        return
    tbstate.ensure_home()
    tbstate.write_json(target, settings)


def update(mutator):
    """Read-modify-write settings.json under a lock, backing it up before the first edit.

    `mutator` receives the settings dict and mutates it in place.
    """
    with tbstate.locked("settings.lock"):
        ensure_settings_file()
        settings = read_settings()
        _backup_once(settings)
        mutator(settings)
        tbstate.write_json(tbstate.settings_path(), settings)
        return settings


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

    return update(mutate)


def clear_spinner():
    """Put back whatever spinnerVerbs the user had before we first touched settings.

    Someone may already have had their own custom verbs; deleting the key outright would
    destroy them, which is not what "/book off restores every key we touched" promises.
    """
    original = None
    if os.path.exists(backup_path()):
        original = tbstate.read_json(backup_path(), {}).get(SPINNER_KEY)

    def mutate(settings):
        if original is not None:
            settings[SPINNER_KEY] = original
        else:
            settings.pop(SPINNER_KEY, None)

    return update(mutate)


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

    return update(mutate)


def restore_statusline(original):
    """Put back whatever the user had before we wrapped it (or remove ours)."""

    def mutate(settings):
        if original:
            settings[STATUSLINE_KEY] = original
        else:
            settings.pop(STATUSLINE_KEY, None)

    return update(mutate)


def diff_against_backup():
    """Keys that differ between the backup and the live file -- used by the tests."""
    if not os.path.exists(backup_path()):
        return {}
    before = tbstate.read_json(backup_path(), {})
    after = read_settings()
    changed = {}
    for key in set(before) | set(after):
        if json.dumps(before.get(key), sort_keys=True) != json.dumps(after.get(key), sort_keys=True):
            changed[key] = (before.get(key), after.get(key))
    return changed
