import json
import os
import subprocess
import sys
import unittest

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

    def test_corrupt_settings_file_is_backed_up_not_propagated(self):
        os.makedirs(os.path.dirname(self.tbstate.settings_path()), exist_ok=True)
        with open(self.tbstate.settings_path(), "w") as fh:
            fh.write("{ this is not json")
        self.tbsettings.set_spinner_line("Recovered.")
        self.assertEqual(self.read_settings()["spinnerVerbs"]["verbs"], ["Recovered."])

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
