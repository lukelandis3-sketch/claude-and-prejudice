"""Full round trip: import a real EPUB, read through it, then turn the plugin off."""

import json
import os
from pathlib import Path
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

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

    def test_first_import_auto_enables_an_empty_statusline(self):
        loaded = self.run_cli("load", self.book)
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        self.assertIn("Reading surface enabled", loaded.stdout)
        self.assertIn("statusline.sh", self.settings()["statusLine"]["command"])

    def test_first_import_does_not_auto_wrap_a_third_party_statusline(self):
        original = {"type": "command", "command": "my-own-prompt", "padding": 1}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": original}, fh)
        loaded = self.run_cli("load", self.book)
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        self.assertIn("already configured", loaded.stdout)
        self.assertEqual(self.settings()["statusLine"], original)

    def test_on_is_the_inverse_of_off(self):
        self.run_cli("load", self.book)
        self.run_cli("off")
        self.assertFalse(self.tbstate.load_config()["surfaces"]["spinner"])

        enabled = self.run_cli("on")
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        config = self.tbstate.load_config()
        self.assertFalse(config["paused"])
        self.assertEqual(config["surfaces"], {"statusline": True, "spinner": True})
        self.assertIn("spinnerVerbs", self.settings())
        self.assertIn("statusline.sh", self.settings()["statusLine"]["command"])

        self.run_cli("off")
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
        self.assertIn("1/", status.stdout)
        self.assertIn("Surfaces:", status.stdout)

    def test_bare_dashboard_shows_current_book_progress_and_controls(self):
        self.run_cli("load", self.book)
        dashboard = self.run_cli("", env={"PATH": ""})
        self.assertEqual(dashboard.returncode, 0, dashboard.stderr)
        self.assertIn("📖 The Test Voyage", dashboard.stdout)
        self.assertIn("1/", dashboard.stdout)
        self.assertIn("Next: !", dashboard.stdout)
        self.assertIn("install-cli", dashboard.stdout)
        self.assertIn(os.path.join(support.REPO, "bin", "tb"), dashboard.stdout)
        self.assertIn("More: /book help", dashboard.stdout)
        self.assertNotIn("All commands:", dashboard.stdout)
        self.assertNotIn("thinking-book 0.", dashboard.stdout)

    def test_dashboard_ignores_an_unrelated_tb_executable(self):
        fake_bin = os.path.join(self.config_dir, "fake-bin")
        os.makedirs(fake_bin)
        fake_tb = os.path.join(fake_bin, "tb")
        with open(fake_tb, "w") as fh:
            fh.write("#!/bin/sh\necho unrelated\n")
        os.chmod(fake_tb, 0o755)
        self.run_cli("load", self.book)

        dashboard = self.run_cli("", env={"PATH": fake_bin})
        self.assertIn(os.path.join(support.REPO, "bin", "tb"), dashboard.stdout)
        self.assertNotIn("Next: !tb n", dashboard.stdout)

    def test_add_accepts_an_unquoted_file_path_with_spaces(self):
        directory = os.path.join(self.config_dir, "My Books")
        os.makedirs(directory)
        path = os.path.join(directory, "short story.txt")
        with open(path, "w") as fh:
            fh.write("A readable sentence from the unified add command.")
        result = self.run_cli("add " + path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("short story", result.stdout)

    def test_add_accepts_a_percent_encoded_file_url(self):
        path = os.path.join(self.config_dir, "a book.txt")
        with open(path, "w") as fh:
            fh.write("A readable sentence loaded from a pasted file URL.")
        result = self.run_cli("add", Path(path).as_uri())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a book", result.stdout)

    def test_add_rejects_a_directory_with_a_useful_error(self):
        result = self.run_cli("add", self.config_dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a readable file", result.stderr)

    def test_bare_title_matching_a_directory_still_searches_gutenberg(self):
        import thinking_book
        directory = os.path.join(self.config_dir, "Dracula")
        os.makedirs(directory)
        called = []
        original = thinking_book.cmd_gutenberg
        previous = os.getcwd()
        thinking_book.cmd_gutenberg = lambda args: called.append(args)
        try:
            os.chdir(self.config_dir)
            thinking_book.cmd_add(["Dracula"])
        finally:
            os.chdir(previous)
            thinking_book.cmd_gutenberg = original
        self.assertEqual(called, [["Dracula"]])

    def test_add_auto_detects_readwise_csv_and_libby_json(self):
        csv_path = os.path.join(self.config_dir, "readwise.csv")
        with open(csv_path, "w") as fh:
            fh.write("Book Title,Author,Highlight\nOne,A,First highlight.\n")
        csv_result = self.run_cli("add", csv_path)
        self.assertEqual(csv_result.returncode, 0, csv_result.stderr)
        self.assertIn("highlight book", csv_result.stdout)

        json_path = os.path.join(self.config_dir, "journey.json")
        with open(json_path, "w") as fh:
            json.dump({"title": "Library Book", "highlights": [{"text": "Quote."}]}, fh)
        json_result = self.run_cli("add", json_path)
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        self.assertIn("Library Book", json_result.stdout)

    def test_add_detects_readwise_json_without_an_author_field(self):
        path = os.path.join(self.config_dir, "readwise.json")
        with open(path, "w") as fh:
            json.dump({"highlights": [
                {"title": "One", "text": "First quote."},
                {"title": "Two", "text": "Second quote."},
            ]}, fh)
        result = self.run_cli("add", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 highlight book(s)", result.stdout)

    def test_start_imports_a_chosen_book_and_applies_all_defaults_once(self):
        result = self.run_cli("start", self.book)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.splitlines()), 2, result.stdout)
        self.assertIn("The Test Voyage", result.stdout)
        self.assertIn("250 WPM", result.stdout)
        config = self.tbstate.load_config()
        self.assertEqual(config["mode"], "timer")
        self.assertEqual(config["words_per_minute"], 250)
        self.assertTrue(config["hud"])
        self.assertEqual(config["surfaces"], {"statusline": True, "spinner": True})
        self.assertIn("spinnerVerbs", self.settings())

    def test_start_without_a_new_book_keeps_the_current_book(self):
        self.run_cli("load", self.book)
        self.run_cli("mode", "manual")
        self.run_cli("pace", "333")
        self.run_cli("display", "spinner")
        before = list(self.tbstate.load_queue()["items"])
        result = self.run_cli("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tbstate.load_queue()["items"], before)
        self.assertIn("The Test Voyage", result.stdout)
        config = self.tbstate.load_config()
        self.assertEqual(config["mode"], "manual")
        self.assertEqual(config["words_per_minute"], 333)
        self.assertEqual(config["surfaces"], {"statusline": False, "spinner": True})
        self.assertNotIn("status line is in use", result.stdout)

    def test_start_switches_to_the_newly_chosen_book(self):
        self.run_cli("start", self.book)
        other = os.path.join(self.config_dir, "second.txt")
        with open(other, "w") as fh:
            fh.write("This is the second book, selected by the reader.")
        result = self.run_cli("start", other)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("second", result.stdout.lower())
        self.assertNotIn("The Test Voyage", result.stdout)
        active = self.tbstate.item_at(self.tbstate.read_pos())
        self.assertEqual(active[3].lower(), "second")

    def test_start_reimport_selects_an_existing_inactive_book_without_duplication(self):
        self.run_cli("start", self.book)
        other = os.path.join(self.config_dir, "second.txt")
        with open(other, "w") as fh:
            fh.write("This is the second book, selected by the reader.")
        self.run_cli("start", other)
        before = list(self.tbstate.load_queue()["items"])

        result = self.run_cli("start", self.book)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tbstate.load_queue()["items"], before)
        self.assertIn("The Test Voyage", result.stdout)
        self.assertEqual(self.tbstate.item_at(self.tbstate.read_pos())[3], "The Test Voyage")

    def test_start_multi_book_export_uses_import_order_and_reports_the_rest(self):
        path = os.path.join(self.config_dir, "readwise.csv")
        with open(path, "w") as fh:
            fh.write(
                "Book Title,Author,Highlight\n"
                "First Choice,A,First highlight.\n"
                "Second Choice,B,Second highlight.\n"
            )
        result = self.run_cli("start", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("First Choice", result.stdout)
        self.assertIn("(+1 more)", result.stdout)
        self.assertEqual(self.tbstate.item_at(self.tbstate.read_pos())[3], "First Choice")

        reversed_path = os.path.join(self.config_dir, "readwise-reversed.csv")
        with open(reversed_path, "w") as fh:
            fh.write(
                "Book Title,Author,Highlight\n"
                "Second Choice,B,Updated second highlight.\n"
                "First Choice,A,Updated first highlight.\n"
            )
        reversed_result = self.run_cli("start", reversed_path)
        self.assertEqual(reversed_result.returncode, 0, reversed_result.stderr)
        self.assertIn("Second Choice", reversed_result.stdout)
        self.assertEqual(self.tbstate.item_at(self.tbstate.read_pos())[3], "Second Choice")

    def test_start_without_any_book_asks_for_one_concisely(self):
        result = self.run_cli("start")
        self.assertEqual(result.returncode, 1)
        self.assertIn("title, URL, or file path", result.stderr)
        self.assertNotIn("/thinking-book", result.stderr)
        self.assertLessEqual(len(result.stderr.splitlines()), 1)

    def test_start_does_not_replace_a_third_party_statusline(self):
        original = {"type": "command", "command": "my-own-prompt", "padding": 1}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": original}, fh)
        result = self.run_cli("start", self.book)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.settings()["statusLine"], original)
        self.assertEqual(
            self.tbstate.load_config()["surfaces"],
            {"statusline": False, "spinner": True},
        )
        self.assertIn("/thinking-book:book pane on", result.stdout)
        self.assertLessEqual(len(result.stdout.splitlines()), 2, result.stdout)
        generation = self.tbstate.stream_generation_dir()
        self.assertFalse(os.path.exists(os.path.join(generation, "0.hud")))

        self.run_cli("pane", "on")
        self.assertTrue(os.path.exists(os.path.join(
            self.tbstate.stream_generation_dir(), "0.hud")))

    def test_start_reports_partial_success_if_settings_are_broken(self):
        self.run_cli("start", self.book)
        other = os.path.join(self.config_dir, "second.txt")
        with open(other, "w") as fh:
            fh.write("This is the second book, safely queued before setup fails.")
        settings_path = self.tbstate.settings_path()
        broken = b"{ not valid json\n"
        with open(settings_path, "wb") as fh:
            fh.write(broken)

        result = self.run_cli("start", other)

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Ready", result.stdout + result.stderr)
        self.assertIn("Queued second, but setup did not finish", result.stderr)
        with open(settings_path, "rb") as fh:
            self.assertEqual(fh.read(), broken)
        self.assertEqual(len(self.tbstate.load_queue()["items"]), 2)

    def test_start_import_failure_preserves_current_book_and_config(self):
        self.run_cli("start", self.book)
        active = self.tbstate.read_pos()
        config = self.tbstate.load_config()
        result = self.run_cli("start", os.path.join(self.config_dir, "missing.epub"))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Ready", result.stdout + result.stderr)
        self.assertEqual(self.tbstate.read_pos(), active)
        self.assertEqual(self.tbstate.load_config(), config)

    def test_start_title_failure_is_attempted_once(self):
        import thinking_book
        calls = []
        original = thinking_book.cmd_gutenberg

        def fail(args, activate=True):
            calls.append((args, activate))
            raise TimeoutError("The read operation timed out")

        thinking_book.cmd_gutenberg = fail
        try:
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                thinking_book.cmd_start(["A title"])
        finally:
            thinking_book.cmd_gutenberg = original
        self.assertEqual(calls, [(["A title"], False)])

    def test_concurrent_starts_leave_complete_config_and_valid_settings(self):
        paths = []
        for number in (1, 2):
            path = os.path.join(self.config_dir, "book-%d.txt" % number)
            with open(path, "w") as fh:
                fh.write("Readable content for concurrent book %d." % number)
            paths.append(path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda path: self.run_cli("start", path), paths))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        config = self.tbstate.load_config()
        self.assertEqual(config["mode"], "timer")
        self.assertEqual(config["words_per_minute"], 250)
        self.assertTrue(config["hud"])
        self.assertTrue(config["surfaces"]["spinner"])
        self.assertEqual(len(self.tbstate.load_queue()["items"]), 2)
        self.settings()  # Parses successfully.

    def test_display_modes_are_single_commands_and_off_restores_settings(self):
        original = {"type": "command", "command": "my-status"}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": original, "theme": "dark"}, fh)
        self.run_cli("load", self.book)

        hud = self.run_cli("display", "hud")
        self.assertEqual(hud.returncode, 0, hud.stderr)
        self.assertTrue(self.tbstate.load_config()["hud"])
        spinner = self.run_cli("display", "spinner")
        self.assertEqual(spinner.returncode, 0, spinner.stderr)
        self.assertEqual(self.tbstate.load_config()["surfaces"],
                         {"statusline": False, "spinner": True})
        self.assertEqual(self.settings()["statusLine"], original)

        self.run_cli("display", "off")
        self.assertEqual(self.settings()["statusLine"], original)
        self.assertEqual(self.settings()["theme"], "dark")

    def test_pace_switches_timer_to_words_per_minute(self):
        self.run_cli("mode", "manual")
        result = self.run_cli("pace", "180")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tbstate.load_config()["words_per_minute"], 180)
        self.assertIn("180 words per minute", result.stdout)
        self.assertIn("/book mode timer", result.stdout)

        self.run_cli("dwell", "7")
        config = self.tbstate.load_config()
        self.assertIsNone(config["words_per_minute"])
        self.assertEqual(config["dwell_seconds"], 7)

    def test_enabling_pace_upgrades_legacy_stream_shards(self):
        self.run_cli("load", self.book)
        marker = os.path.join(self.tbstate.stream_generation_dir(), "format")
        if os.path.exists(marker):
            os.unlink(marker)
        result = self.run_cli("pace", "250")
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.tbstate.stream_generation_dir(), "format")) as fh:
            self.assertEqual(fh.read().strip(), "2")

    def test_invalid_pace_has_a_stable_usage_error(self):
        for value in ("fast", "0", "9" * 5000):
            result = self.run_cli("pace", value)
            self.assertEqual(result.returncode, 1)
            self.assertIn("usage: /book pace", result.stderr)

    def test_excessive_dwell_has_a_stable_usage_error(self):
        result = self.run_cli("dwell", "9" * 10000)
        self.assertEqual(result.returncode, 1)
        self.assertIn("usage: /book dwell", result.stderr)

    def test_manual_turn_reports_book_changes_and_stream_boundaries(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(1)

        crossed = self.run_cli("next")
        self.assertIn("📖 Beta", crossed.stdout)
        self.assertIn("b1", crossed.stdout)
        end = self.run_cli("next")
        self.assertIn("End of Beta", end.stdout)
        self.assertEqual(self.tbstate.read_pos(), 2)
        self.tbstate.write_pos(1)
        beginning = self.run_cli("back")
        self.assertIn("Beginning of Alpha", beginning.stdout)

    def test_invalid_page_counts_do_not_move_the_bookmark(self):
        self.run_cli("load", self.book)
        self.tbstate.write_pos(2)
        for command, value in (("next", "later"), ("next", "-2"),
                               ("back", "0"), ("back", "2 extra")):
            result = self.run_cli(command, value)
            self.assertEqual(result.returncode, 1, (command, value, result.stderr))
            self.assertIn("positive line count", result.stderr)
            self.assertEqual(self.tbstate.read_pos(), 2)

    def test_dashboard_off_state_points_to_on_instead_of_pause(self):
        self.run_cli("load", self.book)
        self.run_cli("off")
        dashboard = self.run_cli("")
        self.assertIn("off — /book on", dashboard.stdout)
        self.assertNotIn("Pause: /book pause", dashboard.stdout)

    def test_dashboard_reports_damaged_queue_instead_of_calling_it_empty(self):
        self.tbstate.save_queue({"items": ["missing-fragments"]})
        self.tbstate.rebuild_stream()
        dashboard = self.run_cli("")
        self.assertIn("unavailable", dashboard.stdout)
        self.assertIn("/book queue", dashboard.stdout)
        self.assertNotIn("No book is queued", dashboard.stdout)

    def test_hud_command_enables_and_disables_the_graphical_status(self):
        self.run_cli("load", self.book)
        enabled = self.run_cli("hud", "on")
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertTrue(self.tbstate.load_config()["hud"])
        self.assertIn("Graphical reading HUD enabled", enabled.stdout)
        self.assertTrue(os.path.exists(os.path.join(
            self.tbstate.stream_generation_dir(), "0.hud")))

        disabled = self.run_cli("hud", "off")
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertFalse(self.tbstate.load_config()["hud"])

    def test_enabling_hud_preserves_logical_bookmark_when_a_prior_item_is_damaged(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1", "b2"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(4)
        os.unlink(self.tbstate.item_fragments_path("a"))

        enabled = self.run_cli("hud", "on")
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), "b2")

    def test_status_reports_progress_in_the_current_book_not_the_whole_library(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1", "b2", "b3"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(4)

        status = self.run_cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("📖 Beta", status.stdout)
        self.assertIn("2/3 (66%)", status.stdout)
        self.assertIn("Library: book 2 of 2", status.stdout)
        self.assertNotIn("line 4 of 5", status.stdout)

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

    def test_each_pane_cycle_snapshots_the_users_current_statusline(self):
        first = {"type": "command", "command": "first"}
        second = {"type": "command", "command": "second"}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": first}, fh)
        self.run_cli("pane", "on")
        self.run_cli("pane", "off")
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": second}, fh)
        self.run_cli("pane", "on")
        self.run_cli("pane", "off")
        self.assertEqual(self.settings()["statusLine"], second)

    def test_each_on_off_cycle_snapshots_current_spinner_verbs(self):
        first = {"mode": "append", "verbs": ["First"]}
        second = {"mode": "append", "verbs": ["Second"]}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"spinnerVerbs": first}, fh)
        self.run_cli("load", self.book)
        self.run_cli("off")
        settings = self.settings()
        settings["spinnerVerbs"] = second
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump(settings, fh)
        self.run_cli("on")
        self.run_cli("off")
        self.assertEqual(self.settings()["spinnerVerbs"], second)

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

    def test_pane_on_survives_a_string_statusline(self):
        # is_our_statusline accepts a plain string, but the callers used to call .get()
        # on it and raise AttributeError after config had already been saved.
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": "my-own-prompt"}, fh, indent=2)
        self.run_cli("load", self.book)

        result = self.run_cli("pane", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("AttributeError", result.stderr)
        self.assertIn("statusline.sh", self.settings()["statusLine"]["command"])
        with open(self.tbstate.path("wrapped.cmd")) as fh:
            self.assertEqual(fh.read().strip(), "my-own-prompt")

    def test_repair_survives_a_string_statusline(self):
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": "my-own-prompt"}, fh, indent=2)
        result = self.run_cli("repair")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("AttributeError", result.stderr)

    def test_refresh_interval_is_written_only_when_asked_for(self):
        self.run_cli("load", self.book)
        self.run_cli("pane", "on")
        self.assertNotIn("refreshInterval", self.settings()["statusLine"])

        self.run_cli("refresh", "10")
        self.assertEqual(self.settings()["statusLine"]["refreshInterval"], 10)

        self.run_cli("refresh", "off")
        self.assertNotIn("refreshInterval", self.settings()["statusLine"])

    def test_refresh_does_not_take_over_a_third_party_statusline(self):
        mine = {"type": "command", "command": "my-own-prompt"}
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": mine}, fh)
        self.run_cli("load", self.book)
        result = self.run_cli("refresh", "10")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.settings()["statusLine"], mine)
        self.assertFalse(os.path.exists(self.tbstate.path("wrapped.cmd")))

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

    def test_queue_is_numbered_and_marks_the_current_book(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "article"}, ["b1", "b2"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(4)

        result = self.run_cli("queue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1.    Alpha [1/2] (book)", result.stdout)
        self.assertIn("2. -> Beta [2/2] (article)", result.stdout)

    def test_open_accepts_the_number_shown_by_queue(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()

        result = self.run_cli("open", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Opened Beta", result.stdout)
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), "b1")

    def test_displayed_number_wins_over_another_books_numeric_title(self):
        for item, title in (("a", "Alpha"), ("b", "Beta"), ("c", "2")):
            self.tbstate.save_item(item, {"title": title, "kind": "book"}, [item + "1"])
        self.tbstate.save_queue({"items": ["a", "b", "c"]})
        self.tbstate.rebuild_stream()

        result = self.run_cli("open", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Opened Beta", result.stdout)
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), "b1")

    def test_reimporting_the_same_book_does_not_duplicate_it(self):
        self.run_cli("load", self.book)
        first_total = self.tbstate.stream_count()
        self.run_cli("load", self.book)
        self.assertEqual(self.tbstate.stream_count(), first_total)
        self.assertEqual(len(self.tbstate.load_queue()["items"]), 1)

    def test_removing_an_earlier_item_preserves_current_fragment(self):
        self.tbstate.save_item("a", {"title": "Book A", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Book B", "kind": "book"}, ["b1", "b2"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(4)

        result = self.run_cli("queue", "rm", "a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), "b2")

    def test_removing_current_item_opens_the_next_item(self):
        for item in ("a", "b", "c"):
            self.tbstate.save_item(item, {"title": item.upper(), "kind": "book"}, [item + "1"])
        self.tbstate.save_queue({"items": ["a", "b", "c"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(2)
        self.run_cli("queue", "rm", "b")
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), "c1")

    def test_queue_remove_accepts_a_number_or_title_and_rejects_no_match(self):
        for item, title in (("a", "Alpha One"), ("b", "Beta Two"), ("c", "Gamma Three")):
            self.tbstate.save_item(item, {"title": title, "kind": "book"}, [item + "1"])
        self.tbstate.save_queue({"items": ["a", "b", "c"]})
        self.tbstate.rebuild_stream()

        by_number = self.run_cli("queue", "rm", "2")
        self.assertEqual(by_number.returncode, 0, by_number.stderr)
        self.assertIn("Removed Beta Two", by_number.stdout)
        by_title = self.run_cli("queue", "rm", "Gamma", "Three")
        self.assertEqual(by_title.returncode, 0, by_title.stderr)
        self.assertEqual(self.tbstate.load_queue()["items"], ["a"])

        missing = self.run_cli("queue", "rm", "Nobody")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no queued item matches", missing.stderr)
        self.assertEqual(self.tbstate.load_queue()["items"], ["a"])

    def test_blank_title_uses_the_item_id_in_queue_and_remove_confirmation(self):
        self.tbstate.save_item("untitled", {"title": "  ", "kind": "book"}, ["line"])
        self.tbstate.save_queue({"items": ["untitled"]})
        self.tbstate.rebuild_stream()

        self.assertIn("untitled", self.run_cli("queue").stdout)
        removed = self.run_cli("queue", "rm", "1")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("Removed untitled", removed.stdout)
        self.assertIn("Queue is empty", removed.stdout)

    def test_blank_title_from_an_old_stream_index_is_repaired_when_displayed(self):
        self.tbstate.save_item("untitled", {"title": "  ", "kind": "book"}, ["line"])
        self.tbstate.save_queue({"items": ["untitled"]})
        self.tbstate.rebuild_stream()
        self.tbstate.atomic_write(self.tbstate.path("stream.idx"), "1\tuntitled\tbook\t\n")

        self.assertIn("untitled", self.run_cli("queue").stdout)
        removed = self.run_cli("queue", "rm", "1")
        self.assertIn("Removed untitled", removed.stdout)

    def test_queue_remove_by_id_recovers_an_item_missing_from_the_stream_index(self):
        self.tbstate.save_item("healthy", {"title": "Healthy", "kind": "book"}, ["line"])
        self.tbstate.save_queue({"items": ["healthy", "missing-fragments"]})
        self.tbstate.rebuild_stream()
        listing = self.run_cli("queue").stdout
        self.assertIn("missing-fragments", listing)
        self.assertIn("unavailable", listing)

        removed = self.run_cli("queue", "rm", "missing-fragments")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("Removed missing-fragments", removed.stdout)
        self.assertEqual(self.tbstate.load_queue()["items"], ["healthy"])

    def test_removing_last_book_falls_back_to_previous_and_names_it(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(2)

        removed = self.run_cli("queue", "rm", "2")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("Now reading Alpha", removed.stdout)
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), "a1")

    def test_removing_an_inactive_book_says_reading_continues(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()

        removed = self.run_cli("queue", "rm", "2")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("Still reading Alpha", removed.stdout)

    def test_reimport_preserves_position_in_a_later_item(self):
        first = os.path.join(self.config_dir, "first.txt")
        second = os.path.join(self.config_dir, "second.txt")
        with open(first, "w") as fh:
            fh.write("First one. First two. First three.")
        with open(second, "w") as fh:
            fh.write("Second one. Second two. Second three.")
        self.run_cli("load", first)
        self.run_cli("load", second)
        second_start = self.tbstate.load_index()[1][0]
        self.tbstate.write_pos(second_start + 1)
        expected = self.tbstate.stream_line(self.tbstate.read_pos())

        with open(first, "w") as fh:
            fh.write("Short now.")
        self.run_cli("load", first)
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), expected)

    def test_open_preserves_a_bookmark_for_each_item(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1", "a2", "a3"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1", "b2", "b3"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(2)

        self.assertIn("b1", self.run_cli("open", "Beta").stdout)
        self.run_cli("next")
        self.assertIn("a2", self.run_cli("open", "Alpha").stdout)
        reopened = self.run_cli("open", "Beta")
        self.assertIn("b2", reopened.stdout)
        self.assertLess(abs(self.tbstate.read_last_advance() - time.time()), 3)

    def test_open_reports_ambiguous_titles(self):
        self.tbstate.save_item("a", {"title": "The Sea", "kind": "book"}, ["a1"])
        self.tbstate.save_item("b", {"title": "Beyond the Sea", "kind": "book"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        result = self.run_cli("open", "Sea")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ambiguous", result.stderr)
        self.assertIn("(a)", result.stderr)
        self.assertIn("(b)", result.stderr)

    def test_open_prefers_an_exact_title_and_clamps_stale_bookmark(self):
        self.tbstate.save_item("a", {"title": "Sea", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Deep Sea", "kind": "book"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(3)
        self.tbstate.write_json(self.tbstate.path("bookmarks.json"), {"a": 500})
        result = self.run_cli("open", "Sea")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("line 2", result.stdout)
        self.assertEqual(self.tbstate.load_bookmarks()["a"], 2)

    def test_import_after_off_stays_off(self):
        self.run_cli("load", self.book)
        self.run_cli("off")
        another = os.path.join(self.config_dir, "another.txt")
        with open(another, "w") as fh:
            fh.write("Another readable sentence.")
        result = self.run_cli("load", another)
        self.assertIn("run /book on", result.stdout)
        self.assertTrue(self.tbstate.load_config()["paused"])

    def test_clippings_batch_reimport_is_stable_across_file_paths(self):
        content = ("Book One (Author A)\n- Your Highlight\n\nFirst highlight.\n==========\n"
                   "Book Two (Author B)\n- Your Highlight\n\nSecond highlight.\n==========")
        first = os.path.join(self.config_dir, "My Clippings.txt")
        second = os.path.join(self.config_dir, "Copy.txt")
        for path in (first, second):
            with open(path, "w") as fh:
                fh.write(content)
        result = self.run_cli("clippings", first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 highlight book", result.stdout)
        ids = list(self.tbstate.load_queue()["items"])
        self.run_cli("clippings", second)
        self.assertEqual(self.tbstate.load_queue()["items"], ids)

    def test_load_recognizes_my_clippings_by_its_standard_filename(self):
        path = os.path.join(self.config_dir, "My Clippings.txt")
        with open(path, "w") as fh:
            fh.write("Book One (Author A)\n- Your Highlight\n\nA saved highlight.\n==========")
        result = self.run_cli("load", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("highlight book", result.stdout)
        item = self.tbstate.load_queue()["items"][0]
        self.assertEqual(self.tbstate.item_meta(item)["kind"], "highlights")

    def test_readwise_csv_imports_in_one_batch(self):
        path = os.path.join(self.config_dir, "readwise.csv")
        with open(path, "w") as fh:
            fh.write("Book Title,Author,Highlight\nOne,A,First highlight.\nTwo,B,Second highlight.\n")
        result = self.run_cli("readwise", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 highlight book", result.stdout)
        self.assertEqual(len(self.tbstate.load_queue()["items"]), 2)

    def test_load_routes_a_misnamed_epub_by_content(self):
        renamed = make_epub(os.path.join(self.config_dir, "voyage.bin"))
        result = self.run_cli("load", renamed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("The Test Voyage", result.stdout)


if __name__ == "__main__":
    unittest.main()
