import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from support import IsolatedStateCase, SCRIPTS


class SettingsTest(IsolatedStateCase):
    def write_settings(self, data):
        path = self.tbstate.settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)

    def read_settings(self):
        with open(self.tbstate.settings_path()) as fh:
            return json.load(fh)

    def test_creates_settings_file_when_absent(self):
        # Claude Code only watches directories that held a settings file at startup.
        self.assertFalse(os.path.exists(self.tbstate.settings_path()))
        self.assertTrue(self.tbsettings.ensure_settings_file())
        self.assertEqual(self.read_settings(), {})

    def test_sets_single_verb_in_replace_mode(self):
        # Claude Code samples the verb list at random; one element makes it deterministic.
        self.tbsettings.set_spinner_line("Call me Ishmael.")
        self.assertEqual(
            self.read_settings()["spinnerVerbs"],
            {"mode": "replace", "verbs": ["Call me Ishmael."]},
        )

    def test_preserves_unrelated_keys(self):
        self.write_settings({"model": "opus", "env": {"FOO": "bar"}, "permissions": {"allow": ["Bash"]}})
        self.tbsettings.set_spinner_line("A line.")
        settings = self.read_settings()
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["env"], {"FOO": "bar"})
        self.assertEqual(settings["permissions"], {"allow": ["Bash"]})

    def test_blank_line_clears_rather_than_writing_empty_verbs(self):
        # An empty verbs array silently falls back to the stock gerunds.
        self.tbsettings.set_spinner_line("Something.")
        self.tbsettings.set_spinner_line("   ")
        self.assertNotIn("spinnerVerbs", self.read_settings())

    def test_backup_is_taken_once_before_first_edit(self):
        self.write_settings({"model": "opus"})
        self.tbsettings.set_spinner_line("First.")
        self.tbsettings.set_spinner_line("Second.")
        with open(self.tbsettings.backup_path()) as fh:
            backup = json.load(fh)
        self.assertEqual(backup, {"model": "opus"})
        self.assertNotIn("spinnerVerbs", backup)

    def test_only_our_keys_differ_from_the_backup(self):
        self.write_settings({"model": "opus", "statusLine": {"type": "command", "command": "mine"}})
        self.tbsettings.set_spinner_line("A line.")
        self.tbsettings.set_statusline("ours")
        self.assertEqual(set(self.tbsettings.diff_against_backup()), {"spinnerVerbs", "statusLine"})

    def test_restores_a_users_own_spinner_verbs_rather_than_deleting_them(self):
        # Someone may already have had custom verbs; /book off must not destroy them.
        original = {"mode": "append", "verbs": ["Yarring", "Splicing"]}
        self.write_settings({"spinnerVerbs": original})
        self.tbsettings.set_spinner_line("Call me Ishmael.")
        self.tbsettings.clear_spinner()
        self.assertEqual(self.read_settings()["spinnerVerbs"], original)
        self.assertEqual(self.tbsettings.diff_against_backup(), {})

    def test_clear_removes_the_key_when_the_user_had_none(self):
        self.write_settings({"model": "opus"})
        self.tbsettings.set_spinner_line("A line.")
        self.tbsettings.clear_spinner()
        self.assertNotIn("spinnerVerbs", self.read_settings())

    def test_padding_survives_a_later_statusline_write(self):
        self.tbsettings.set_statusline("ours", padding=2)
        self.tbsettings.set_statusline("ours", refresh_interval=10)
        entry = self.read_settings()["statusLine"]
        self.assertEqual(entry["padding"], 2)
        self.assertEqual(entry["refreshInterval"], 10)

    def test_restores_wrapped_statusline_verbatim(self):
        original = {"type": "command", "command": "my-prompt --fancy", "padding": 1}
        self.write_settings({"statusLine": original})
        self.tbsettings.set_statusline("ours", padding=1)
        self.tbsettings.restore_statusline(original)
        self.assertEqual(self.read_settings()["statusLine"], original)

    def test_restore_without_original_removes_the_key(self):
        self.tbsettings.set_statusline("ours")
        self.tbsettings.restore_statusline(None)
        self.assertNotIn("statusLine", self.read_settings())

    def test_corrupt_settings_file_is_preserved_and_refused(self):
        os.makedirs(os.path.dirname(self.tbstate.settings_path()), exist_ok=True)
        original = b"{ this is not json\n"
        with open(self.tbstate.settings_path(), "w") as fh:
            fh.write(original.decode())
        with self.assertRaises(self.tbsettings.SettingsError):
            self.tbsettings.set_spinner_line("Recovered.")
        with open(self.tbstate.settings_path(), "rb") as fh:
            self.assertEqual(fh.read(), original)

    def test_non_object_settings_are_preserved_and_refused(self):
        original = b"[1, 2, 3]\n"
        os.makedirs(os.path.dirname(self.tbstate.settings_path()), exist_ok=True)
        with open(self.tbstate.settings_path(), "wb") as fh:
            fh.write(original)
        with self.assertRaises(self.tbsettings.SettingsError):
            self.tbsettings.set_spinner_line("No overwrite.")
        with open(self.tbstate.settings_path(), "rb") as fh:
            self.assertEqual(fh.read(), original)

    def test_raw_backup_preserves_exact_original_bytes(self):
        original = b'{\n  "model" : "opus",\n  "env": {"A": 1}\n}\n'
        os.makedirs(os.path.dirname(self.tbstate.settings_path()), exist_ok=True)
        with open(self.tbstate.settings_path(), "wb") as fh:
            fh.write(original)
        self.tbsettings.set_spinner_line("A line.")
        with open(self.tbsettings.raw_backup_path(), "rb") as fh:
            self.assertEqual(fh.read(), original)

    def test_writing_the_same_spinner_value_is_a_noop(self):
        self.tbsettings.set_spinner_line("Hold this line.")
        path = self.tbstate.settings_path()
        before = os.stat(path)
        with open(path, "rb") as fh:
            contents = fh.read()
        self.tbsettings.set_spinner_line("Hold this line.")
        after = os.stat(path)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), contents)
        self.assertEqual(after.st_ino, before.st_ino)

    def test_identical_user_spinner_is_not_claimed_or_removed(self):
        custom = {"mode": "replace", "verbs": ["Hold this line."]}
        self.write_settings({"spinnerVerbs": custom, "theme": "dark"})
        self.tbsettings.set_spinner_line("Hold this line.")
        self.tbsettings.clear_spinner()
        self.assertEqual(self.read_settings()["spinnerVerbs"], custom)
        self.assertFalse(os.path.exists(self.tbsettings.backup_path()))

    def test_legacy_backup_is_used_per_key_not_per_first_v04_write(self):
        original_status = {"type": "command", "command": "npx ccstatusline"}
        self.write_settings({
            "spinnerVerbs": {"mode": "replace", "verbs": ["old plugin line"]},
            "statusLine": {"type": "command", "command": "ours-v03"},
        })
        self.tbstate.write_json(self.tbsettings.backup_path(), {
            "spinnerVerbs": {"mode": "append", "verbs": ["Yarr"]},
            "statusLine": original_status,
        })
        self.tbsettings.set_spinner_line("A v0.4 line.")
        self.tbsettings.set_statusline("ours-v04")
        self.tbsettings.restore_statusline(None)
        self.assertEqual(self.read_settings()["statusLine"], original_status)

    def test_restore_leaves_a_statusline_we_never_wrote_alone(self):
        self.tbsettings.set_statusline("ours")
        self.tbsettings.restore_statusline(None)
        mine = {"type": "command", "command": "npx ccstatusline"}
        settings = self.read_settings()
        settings["statusLine"] = mine
        self.write_settings(settings)
        self.tbsettings.restore_statusline(None)
        self.assertEqual(self.read_settings()["statusLine"], mine)

    def test_clear_leaves_one_element_user_spinner_we_never_wrote(self):
        self.tbsettings.set_spinner_line("ours")
        self.tbsettings.clear_spinner()
        mine = {"mode": "replace", "verbs": ["My own single verb"]}
        settings = self.read_settings()
        settings["spinnerVerbs"] = mine
        self.write_settings(settings)
        self.tbsettings.clear_spinner()
        self.assertEqual(self.read_settings()["spinnerVerbs"], mine)

    def test_clear_preserves_spinner_verbs_changed_after_our_last_write(self):
        self.tbsettings.set_spinner_line("Our line.")
        custom = {"mode": "append", "verbs": ["User edit"]}
        settings = self.read_settings()
        settings["spinnerVerbs"] = custom
        self.write_settings(settings)
        self.tbsettings.clear_spinner()
        self.assertEqual(self.read_settings()["spinnerVerbs"], custom)

        # The preserved edit becomes the baseline for the next enable/disable cycle.
        self.tbsettings.set_spinner_line("Our next line.")
        self.tbsettings.clear_spinner()
        self.assertEqual(self.read_settings()["spinnerVerbs"], custom)

    def test_one_session_ending_cannot_clear_another_sessions_spinner(self):
        original = {"mode": "append", "verbs": ["Pondering"]}
        self.write_settings({"spinnerVerbs": original})
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session-a"}):
            self.tbsettings.set_spinner_line("Session A line")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session-b"}):
            self.tbsettings.set_spinner_line("Session B line")

        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session-a"}):
            self.tbsettings.clear_spinner(session_only=True)
        self.assertEqual(
            self.read_settings()["spinnerVerbs"]["verbs"], ["Session B line"])

        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session-b"}):
            self.tbsettings.clear_spinner(session_only=True)
        self.assertEqual(self.read_settings()["spinnerVerbs"], original)

    def test_restore_preserves_statusline_changed_after_our_last_write(self):
        original = {"type": "command", "command": "before"}
        self.write_settings({"statusLine": original})
        self.tbsettings.set_statusline("ours")
        newer = {"type": "command", "command": "after"}
        self.write_settings({"statusLine": newer})
        self.tbsettings.restore_statusline(original)
        self.assertEqual(self.read_settings()["statusLine"], newer)

    def test_present_null_and_falsy_values_restore_exactly(self):
        for key, setter, clearer in (
            ("spinnerVerbs", lambda: self.tbsettings.set_spinner_line("ours"),
             self.tbsettings.clear_spinner),
            ("statusLine", lambda: self.tbsettings.set_statusline("ours"),
             lambda: self.tbsettings.restore_statusline(None)),
        ):
            for original in (None, {}, ""):
                with self.subTest(key=key, original=original):
                    for name in ("settings.backup.json", "settings.backup.raw",
                                 "settings.backup.meta.json", "settings.origins.json",
                                 "settings.written.json"):
                        try:
                            os.unlink(self.tbstate.path(name))
                        except OSError:
                            pass
                    self.write_settings({key: original})
                    setter()
                    clearer()
                    self.assertIn(key, self.read_settings())
                    self.assertEqual(self.read_settings()[key], original)

    def test_concurrent_writers_leave_valid_json(self):
        self.write_settings({"model": "opus"})
        program = (
            "import sys;"
            "sys.path[:0]=[%r];"
            "import settings as s;"
            "s.set_spinner_line('line %%s' %% sys.argv[1])" % SCRIPTS
        )
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.config_dir)
        workers = [
            subprocess.Popen([sys.executable, "-c", program, str(n)], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for n in range(8)
        ]
        for worker in workers:
            _out, err = worker.communicate(timeout=60)
            self.assertEqual(worker.returncode, 0, err.decode())

        settings = self.read_settings()
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(len(settings["spinnerVerbs"]["verbs"]), 1)


if __name__ == "__main__":
    unittest.main()
