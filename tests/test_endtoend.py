"""Full round trip: import a real EPUB, read through it, then turn the plugin off."""

import json
import os
import unittest

import support
from support import IsolatedStateCase, make_epub


class RoundTripTest(IsolatedStateCase):
    def settings(self):
        path = self.tbstate.settings_path()
        if not os.path.exists(path):
            return {}
        with open(path) as fh:
            return json.load(fh)

    def setUp(self):
        super().setUp()
        self.book = make_epub(os.path.join(self.config_dir, "voyage.epub"))

    def test_import_read_and_off_leaves_settings_as_they_were(self):
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"model": "opus", "permissions": {"allow": ["Bash"]}}, fh, indent=2)

        loaded = self.run_cli("load", self.book)
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        self.assertIn("The Test Voyage", loaded.stdout)

        self.run_cli("mode", "turn")
        self.run_cli("sync")
        first = self.settings()["spinnerVerbs"]["verbs"][0]
        # The chapter heading is its own fragment now -- it is a separate block, and
        # merging it into the prose that follows was the bug.
        self.assertEqual(first, "Chapter One")

        seen = [first]
        for _ in range(3):
            self.run_cli("advance")
            seen.append(self.settings()["spinnerVerbs"]["verbs"][0])
        self.assertEqual(len(set(seen)), len(seen), "the same line repeated: %r" % seen)
        self.assertTrue(any("Ishmael" in line for line in seen),
                        "expected the prose to follow the heading: %r" % seen)

        off = self.run_cli("off")
        self.assertEqual(off.returncode, 0, off.stderr)

        final = self.settings()
        self.assertNotIn("spinnerVerbs", final)
        self.assertNotIn("statusLine", final)
        self.assertEqual(final["model"], "opus")
        self.assertEqual(final["permissions"], {"allow": ["Bash"]})
        self.assertEqual(self.tbsettings.diff_against_backup(), {})

    def test_spinner_verbs_are_always_a_single_element_replace_list(self):
        # Claude Code samples the list at random -- more than one element loses the order.
        self.run_cli("load", self.book)
        self.run_cli("mode", "turn")
        for _ in range(5):
            self.run_cli("advance")
            verbs = self.settings()["spinnerVerbs"]
            self.assertEqual(verbs["mode"], "replace")
            self.assertEqual(len(verbs["verbs"]), 1)
            self.assertTrue(verbs["verbs"][0].strip())

    def test_status_reports_position_and_progress(self):
        self.run_cli("load", self.book)
        status = self.run_cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("The Test Voyage", status.stdout)
        self.assertIn("A. Fixture", status.stdout)
        self.assertIn("Position: line 1 of", status.stdout)

    def test_pane_on_wraps_an_existing_status_line_and_off_restores_it(self):
        original = {"type": "command", "command": "my-own-prompt", "padding": 1}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": original}, fh, indent=2)

        self.run_cli("load", self.book)
        self.run_cli("pane", "on")
        live = self.settings()["statusLine"]["command"]
        self.assertIn("statusline.sh", live)
        with open(self.tbstate.path("wrapped.cmd")) as fh:
            self.assertEqual(fh.read().strip(), "my-own-prompt")

        self.run_cli("pane", "off")
        self.assertEqual(self.settings()["statusLine"], original)
        self.assertFalse(os.path.exists(self.tbstate.path("wrapped.cmd")))

    def test_pane_on_from_two_plugin_roots_does_not_nest(self):
        """The exact sequence that broke a real install: clone first, installed plugin second."""
        import shutil
        second_root = os.path.join(self.config_dir, "installed-copy")
        shutil.copytree(os.path.dirname(os.path.dirname(support.CLI)), second_root)

        self.run_cli("load", self.book)
        first = self.run_cli("pane", "on")
        self.assertEqual(first.returncode, 0, first.stderr)

        second = self.run_cli("pane", "on", env={"CLAUDE_PLUGIN_ROOT": second_root})
        self.assertEqual(second.returncode, 0, second.stderr)

        # Neither run may leave a wrapped command pointing at a thinking-book script.
        self.assertFalse(
            os.path.exists(self.tbstate.path("wrapped.cmd")),
            "pane on wrapped thinking-book in itself",
        )
        self.assertIsNone(self.tbstate.load_config()["wrapped_statusline"])

    def test_repair_unwinds_a_self_wrapped_status_line(self):
        self.run_cli("load", self.book)
        self.tbstate.atomic_write(
            self.tbstate.path("wrapped.cmd"), 'sh "%s"\n' % support.STATUSLINE
        )
        config = self.tbstate.load_config()
        config["wrapped_statusline"] = {"type": "command", "command": support.STATUSLINE}
        self.tbstate.save_config(config)

        result = self.run_cli("repair")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Repaired:", result.stdout)
        self.assertFalse(os.path.exists(self.tbstate.path("wrapped.cmd")))
        self.assertIsNone(self.tbstate.load_config()["wrapped_statusline"])

    def test_repair_leaves_a_genuine_third_party_status_line_alone(self):
        original = {"type": "command", "command": "my-own-prompt --fancy"}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": original}, fh, indent=2)
        self.run_cli("load", self.book)
        self.run_cli("pane", "on")

        result = self.run_cli("repair")
        self.assertIn("Nothing to repair", result.stdout)
        with open(self.tbstate.path("wrapped.cmd")) as fh:
            self.assertEqual(fh.read().strip(), "my-own-prompt --fancy")

    def test_refresh_interval_is_written_only_when_asked_for(self):
        self.run_cli("load", self.book)
        self.run_cli("pane", "on")
        self.assertNotIn("refreshInterval", self.settings()["statusLine"])

        self.run_cli("refresh", "10")
        self.assertEqual(self.settings()["statusLine"]["refreshInterval"], 10)

        self.run_cli("refresh", "off")
        self.assertNotIn("refreshInterval", self.settings()["statusLine"])

    def test_queue_holds_several_items_and_reads_them_in_order(self):
        second = os.path.join(self.config_dir, "second.txt")
        with open(second, "w") as fh:
            fh.write("A second book entirely. With its own sentences.")

        self.run_cli("load", self.book)
        self.run_cli("load", second)
        queue = self.run_cli("queue")
        self.assertIn("The Test Voyage", queue.stdout)
        self.assertIn("second", queue.stdout)

        rows = self.tbstate.load_index()
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0][0], rows[1][0])

    def test_reimporting_the_same_book_does_not_duplicate_it(self):
        self.run_cli("load", self.book)
        first_total = self.tbstate.stream_count()
        self.run_cli("load", self.book)
        self.assertEqual(self.tbstate.stream_count(), first_total)
        self.assertEqual(len(self.tbstate.load_queue()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
