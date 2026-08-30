"""Full round trip: import a real EPUB, read through it, then turn the plugin off."""

import json
import os
import time
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
        self.assertIn("Position: line 1 of", status.stdout)

    def test_status_reports_progress_in_the_current_book_not_the_whole_library(self):
        self.tbstate.save_item("a", {"title": "Alpha", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Beta", "kind": "book"}, ["b1", "b2", "b3"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.tbstate.write_pos(4)

        status = self.run_cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Reading:  Beta", status.stdout)
        self.assertIn("Position: line 2 of 3  (66.7%)", status.stdout)
        self.assertIn("Library:  book 2 of 2", status.stdout)
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
