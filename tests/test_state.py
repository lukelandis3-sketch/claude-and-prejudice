import os
import threading
import time
import unittest
from unittest import mock

from support import IsolatedStateCase


class StreamTest(IsolatedStateCase):
    def test_rebuild_sanitizes_terminal_controls_in_legacy_fragments(self):
        self.tbstate.save_item(
            "legacy", {"title": "Legacy", "kind": "book"},
            ["Safe \x9b31mred\x9b0m invoice\u202egpj.exe."],
        )
        self.tbstate.save_queue({"items": ["legacy"]})

        self.tbstate.rebuild_stream()

        line = self.tbstate.stream_line(1)
        self.assertNotIn("\x9b", line)
        self.assertNotIn("\u202e", line)
        self.assertIn("Safe", line)

    def test_compact_default_does_not_write_unused_hud_shards(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.assertFalse(os.path.exists(os.path.join(
            self.tbstate.stream_generation_dir(), "0.hud")))

    def test_hud_title_is_single_line_bounded_and_control_free(self):
        hostile = "\x1b[31mBad\tTitle\u2028" + "x" * 200
        line = self.tbstate.hud_line("fallback", hostile, 1, 2)
        self.assertNotIn("\x1b", line)
        self.assertNotIn("\n", line)
        title = line.split(" · ", 1)[0].removeprefix("📖 ")
        self.assertLessEqual(len(title), 38)

    def test_load_queue_filters_invalid_and_duplicate_item_ids(self):
        self.tbstate.write_json(
            self.tbstate.path("queue.json"),
            {"items": ["a", "a", None, 7, "", "b", "a"]},
        )
        self.assertEqual(self.tbstate.load_queue(), {"items": ["a", "b"]})

    def test_rebuild_concatenates_items_in_queue_order(self):
        self.tbstate.save_item("a", {"title": "Book A", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Book B", "kind": "article"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.assertEqual(self.tbstate.rebuild_stream(), 3)
        self.assertEqual(self.tbstate.stream_line(1), "a1")
        self.assertEqual(self.tbstate.stream_line(3), "b1")

    def test_generation_shards_precompute_word_counts_but_api_returns_only_prose(self):
        self.tbstate.save_item("a", {"title": "A"}, ["one two three"])
        self.tbstate.save_queue({"items": ["a"]})
        self.tbstate.rebuild_stream()
        generation = self.tbstate.stream_generation_dir()
        with open(os.path.join(generation, "0.txt")) as fh:
            self.assertEqual(fh.readline(), "3\tone two three\n")
        with open(os.path.join(generation, "format")) as fh:
            self.assertEqual(fh.read().strip(), "2")
        self.assertEqual(self.tbstate.stream_record(1), (3, "one two three"))
        self.assertEqual(self.tbstate.stream_line(1), "one two three")

    def test_generation_is_self_contained_and_legacy_stream_is_not_duplicated(self):
        self.seed_stream(["one", "two", "three"])
        self.assertEqual(self.tbstate.stream_count(), 3)
        with open(os.path.join(self.tbstate.stream_generation_dir(), "count")) as fh:
            self.assertEqual(fh.read().strip(), "3")
        self.assertTrue(os.path.exists(os.path.join(
            self.tbstate.stream_generation_dir(), "index")))
        for legacy in ("count", "stream.txt", "stream.idx"):
            self.assertFalse(os.path.exists(self.tbstate.path(legacy)))

    def test_legacy_stream_remains_readable_until_first_rebuild(self):
        self.tbstate.atomic_write(self.tbstate.path("stream.txt"), "old one\nold two\n")
        self.tbstate.atomic_write(self.tbstate.path("count"), "2\n")
        self.tbstate.atomic_write(
            self.tbstate.path("stream.idx"), "1\told\tbook\tOld Book\n")
        self.assertEqual(self.tbstate.stream_count(), 2)
        self.assertEqual(self.tbstate.stream_line(2), "old two")
        self.assertEqual(self.tbstate.load_index(), [(1, "old", "book", "Old Book")])

    def test_v06_generation_uses_legacy_index_until_sync_migrates_it(self):
        self.seed_stream(["one", "two"], mode="manual")
        generation_index = os.path.join(self.tbstate.stream_generation_dir(), "index")
        with open(generation_index) as fh:
            contents = fh.read()
        self.tbstate.atomic_write(self.tbstate.path("stream.idx"), contents)
        os.unlink(generation_index)
        self.assertEqual(self.tbstate.item_at(2)[1], "test-item")

    def test_two_publications_retain_two_self_contained_generations(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.tbstate.rebuild_stream()
        root = self.tbstate.path("stream-generations")
        generations = [os.path.join(root, name) for name in os.listdir(root)]
        self.assertEqual(len(generations), 2)
        for generation in generations:
            self.assertTrue(os.path.isfile(os.path.join(generation, "index")))

    def test_stream_line_out_of_range_is_empty(self):
        self.seed_stream(["only"])
        self.assertEqual(self.tbstate.stream_line(0), "")
        self.assertEqual(self.tbstate.stream_line(99), "")

    def test_item_at_maps_position_to_its_item(self):
        self.tbstate.save_item("a", {"title": "Book A", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Book B", "kind": "article"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.assertEqual(self.tbstate.item_at(1)[1], "a")
        self.assertEqual(self.tbstate.item_at(2)[1], "a")
        self.assertEqual(self.tbstate.item_at(3)[1], "b")

    def test_empty_items_are_skipped_without_breaking_the_index(self):
        self.tbstate.save_item("empty", {"title": "Nothing", "kind": "book"}, [])
        self.tbstate.save_item(
            "controls", {"title": "Controls", "kind": "book"}, ["\x1b[31m"],
        )
        self.tbstate.save_item("real", {"title": "Something", "kind": "book"}, ["x1"])
        self.tbstate.save_queue({"items": ["empty", "controls", "real"]})
        self.assertEqual(self.tbstate.rebuild_stream(), 1)
        self.assertEqual(self.tbstate.item_at(1)[1], "real")
        self.assertTrue(self.tbstate.stream_is_healthy(["empty", "controls", "real"]))

    def test_metadata_newlines_cannot_corrupt_the_stream_index(self):
        self.tbstate.save_item("a", {"title": "Bad\nTitle", "kind": "book"}, ["a1"])
        self.tbstate.save_queue({"items": ["a"]})
        self.tbstate.rebuild_stream()
        self.assertEqual(self.tbstate.item_at(1)[3], "Bad Title")

    def test_corrupt_generation_cannot_traverse_outside_stream_directory(self):
        outside = os.path.join(self.config_dir, "outside")
        os.makedirs(outside)
        os.makedirs(self.tbstate.path("stream-generations"))
        self.tbstate.atomic_write(os.path.join(outside, "0.txt"), "not book data\n")
        self.tbstate.atomic_write(self.tbstate.path("stream.gen"), "../../outside\n")
        self.assertEqual(self.tbstate.stream_line(1), "")

    def test_stream_generation_handles_shard_boundaries(self):
        lines = ["line-%d" % n for n in range(1, 515)]
        self.seed_stream(lines, mode="manual")
        self.assertTrue(self.tbstate.stream_generation())
        for position in (1, 256, 257, 512, 513, 514):
            with self.subTest(position=position):
                self.assertEqual(self.tbstate.stream_line(position), lines[position - 1])

    def test_stream_window_reads_only_the_requested_rows_across_shards(self):
        lines = ["line-%d" % n for n in range(1, 515)]
        self.seed_stream(lines, mode="manual")
        self.assertEqual(
            self.tbstate.stream_window(255, 258),
            ["line-255", "line-256", "line-257", "line-258"],
        )

    def test_stream_window_clamps_to_the_book_stream(self):
        self.seed_stream(["one", "two", "three"], mode="manual")
        self.assertEqual(self.tbstate.stream_window(-20, 2), ["one", "two"])
        self.assertEqual(self.tbstate.stream_window(3, 99), ["three"])
        self.assertEqual(self.tbstate.stream_window(4, 2), [])

    def test_locate_and_resolve_use_item_relative_offsets(self):
        self.tbstate.save_item("a", {"title": "A", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "B", "kind": "book"}, ["b1", "b2", "b3"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.assertEqual(self.tbstate.locate_position(4), ("b", 2))
        self.assertEqual(self.tbstate.resolve_position("b", 2), 4)
        self.assertEqual(self.tbstate.resolve_position("a", 99), 2)


class ConfigTest(IsolatedStateCase):
    def test_terminal_labels_sanitize_fallback_text_too(self):
        self.assertEqual(
            self.tbstate.terminal_label("", fallback="\x1b[31mFallback\x1b[0m\nName"),
            "Fallback Name",
        )

    def test_invisible_terminal_label_uses_its_fallback(self):
        self.assertEqual(
            self.tbstate.terminal_label("\u00ad\u200b\ufeff", fallback="book-id"),
            "book-id",
        )

    def test_nonblocking_lock_skips_duplicate_background_work(self):
        with self.tbstate.try_locked("refresh.lock") as first:
            with self.tbstate.try_locked("refresh.lock") as second:
                self.assertTrue(first)
                self.assertFalse(second)

    def test_turn_guard_is_reentrant_within_one_command(self):
        with self.tbstate.turn_guard():
            with self.tbstate.turn_guard():
                self.assertTrue(os.path.isdir(self.tbstate.path("turn.lock.d")))
        self.assertFalse(os.path.exists(self.tbstate.path("turn.lock.d")))

    def test_turn_guard_quarantines_a_stale_regular_file_lock(self):
        lock = self.tbstate.path("turn.lock.d")
        with open(lock, "w") as fh:
            fh.write("corrupt")
        os.utime(lock, (1, 1))

        with self.tbstate.turn_guard(timeout=0.1):
            self.assertTrue(os.path.isdir(lock))

        self.assertFalse(os.path.exists(lock))
        self.assertTrue(any(name.startswith("turn.lock.stale.")
                            for name in os.listdir(self.tbstate.home())))

    def test_turn_guard_quarantines_a_stale_lock_with_junk(self):
        lock = self.tbstate.path("turn.lock.d")
        os.mkdir(lock)
        with open(os.path.join(lock, "junk"), "w") as fh:
            fh.write("left by a killed process")
        os.utime(lock, (1, 1))

        with self.tbstate.turn_guard(timeout=0.1):
            self.assertTrue(os.path.isdir(lock))

        self.assertFalse(os.path.exists(lock))

    def test_turn_guard_treats_an_impossible_owner_pid_as_corrupt(self):
        lock = self.tbstate.path("turn.lock.d")
        os.mkdir(lock)
        with open(os.path.join(lock, "owner"), "w") as fh:
            fh.write("999999999999\n")
        os.utime(lock, (1, 1))

        with self.tbstate.turn_guard(timeout=0.1):
            pass

        self.assertFalse(os.path.exists(lock))

    def test_turn_guard_never_follows_a_corrupt_lock_symlink(self):
        outside = os.path.join(self.config_dir, "outside-lock-target")
        os.mkdir(outside)
        owner = os.path.join(outside, "owner")
        with open(owner, "w") as fh:
            fh.write("preserve me")
        lock = self.tbstate.path("turn.lock.d")
        os.symlink(outside, lock)

        with self.tbstate.turn_guard(timeout=0.1):
            pass

        with open(owner) as fh:
            self.assertEqual(fh.read(), "preserve me")

    def test_defaults_applied_to_partial_config(self):
        self.tbstate.write_json(self.tbstate.path("config.json"), {"mode": "manual"})
        config = self.tbstate.load_config()
        self.assertEqual(config["mode"], "manual")
        self.assertEqual(config["dwell_seconds"], 8)
        self.assertTrue(config["surfaces"]["spinner"])

    def test_saved_config_writes_fixed_field_stop_control(self):
        config = self.tbstate.load_config()
        config.update({"mode": "manual", "paused": True})
        config["surfaces"] = {"statusline": False, "spinner": True}

        self.tbstate.save_config(config)

        with open(self.tbstate.path("stop.control")) as fh:
            self.assertEqual(fh.read(), "manual 1 0 1\n")

    def test_invalid_values_fall_back_to_defaults(self):
        self.tbstate.write_json(
            self.tbstate.path("config.json"), {"mode": "nonsense", "dwell_seconds": "abc"}
        )
        config = self.tbstate.load_config()
        self.assertEqual(config["mode"], "timer")
        self.assertEqual(config["dwell_seconds"], 8)

    def test_excessive_dwell_is_clamped(self):
        self.tbstate.write_json(
            self.tbstate.path("config.json"), {"dwell_seconds": 999999999}
        )
        self.assertEqual(self.tbstate.load_config()["dwell_seconds"], 86400)

    def test_corrupt_config_does_not_raise(self):
        self.tbstate.atomic_write(self.tbstate.path("config.json"), "{ not json")
        self.assertEqual(self.tbstate.load_config()["mode"], "timer")

    def test_new_install_defaults_to_a_comprehension_oriented_wpm(self):
        self.assertEqual(self.tbstate.load_config()["words_per_minute"], 250)

    def test_legacy_dwell_config_keeps_fixed_seconds(self):
        self.tbstate.write_json(
            self.tbstate.path("config.json"), {"mode": "timer", "dwell_seconds": 12})
        config = self.tbstate.load_config()
        self.assertIsNone(config["words_per_minute"])
        self.assertEqual(config["dwell_seconds"], 12)

    def test_valid_non_object_config_falls_back_to_defaults(self):
        self.tbstate.write_json(self.tbstate.path("config.json"), ["not", "an", "object"])
        self.assertEqual(self.tbstate.load_config(), self.tbstate.DEFAULT_CONFIG)

    def test_non_mapping_surfaces_falls_back_to_defaults(self):
        for bad in (["statusline"], "on", 3):
            with self.subTest(bad=bad):
                self.tbstate.write_json(self.tbstate.path("config.json"), {"surfaces": bad})
                self.assertEqual(
                    self.tbstate.load_config()["surfaces"],
                    {"statusline": True, "spinner": True},
                )

    def test_status_prefix_keeps_awkward_values_as_inert_data(self):
        config = self.tbstate.load_config()
        config["prefix"] = "it's $(rm -rf /) "
        self.tbstate.save_config(config)
        with open(self.tbstate.path("status.prefix")) as fh:
            self.assertEqual(fh.read(), "it's $(rm -rf /) \n")

    def test_status_prefix_strips_terminal_commands_but_keeps_spacing(self):
        config = self.tbstate.load_config()
        config["prefix"] = "\x1b]0;bad\x07\x1b[31mBook:\u202e "
        self.tbstate.save_config(config)

        with open(self.tbstate.path("status.prefix")) as fh:
            self.assertEqual(fh.read(), "Book: \n")
        self.assertEqual(self.tbstate.terminal_prefix(config["prefix"]), "Book: ")
        self.assertFalse(os.path.exists(self.tbstate.path("hot.env")))

    def test_newlines_in_status_prefix_are_flattened(self):
        config = self.tbstate.load_config()
        config["prefix"] = "chapter\r\none\n"
        self.tbstate.save_config(config)
        with open(self.tbstate.path("status.prefix")) as fh:
            self.assertEqual(fh.read(), "chapter  one \n")

    def test_hud_defaults_off_and_is_mirrored_to_status_control(self):
        config = self.tbstate.load_config()
        self.assertFalse(config["hud"])
        config["hud"] = True
        self.tbstate.save_config(config)
        with open(self.tbstate.path("status.control")) as fh:
            self.assertEqual(fh.read(), "1 timer 8 250 0 1 1\n")

    def test_wpm_is_mirrored_to_status_control(self):
        config = self.tbstate.load_config()
        config["words_per_minute"] = 250
        self.tbstate.save_config(config)
        with open(self.tbstate.path("status.control")) as fh:
            self.assertEqual(fh.read(), "1 timer 8 250 0 1 0\n")

    def test_status_control_writer_clamps_legacy_timer_values(self):
        config = self.tbstate.load_config()
        config["dwell_seconds"] = 0
        config["words_per_minute"] = 20
        self.tbstate.save_config(config)
        with open(self.tbstate.path("status.control")) as fh:
            self.assertEqual(fh.read(), "1 timer 1 30 0 1 0\n")

    def test_non_boolean_hud_config_falls_back_to_off(self):
        for bad in ("off", 1, ["on"], None):
            with self.subTest(bad=bad):
                self.tbstate.write_json(self.tbstate.path("config.json"), {"hud": bad})
                self.assertFalse(self.tbstate.load_config()["hud"])

    def test_identical_config_update_does_not_rewrite_config_or_status_cache(self):
        self.tbstate.save_config(self.tbstate.load_config())
        before = (
            os.stat(self.tbstate.path("config.json")).st_ino,
            os.stat(self.tbstate.path("status.control")).st_ino,
            os.stat(self.tbstate.path("status.prefix")).st_ino,
        )
        self.tbstate.update_config(lambda _config: None)
        after = (
            os.stat(self.tbstate.path("config.json")).st_ino,
            os.stat(self.tbstate.path("status.control")).st_ino,
            os.stat(self.tbstate.path("status.prefix")).st_ino,
        )
        self.assertEqual(after, before)


class PositionTest(IsolatedStateCase):
    def test_new_timer_clock_rounds_up_to_avoid_an_early_boundary_turn(self):
        with mock.patch.object(self.tbstate.time, "time", return_value=100.01):
            self.tbstate.write_last_advance()
        self.assertEqual(self.tbstate.read_last_advance(), 101)

    def test_timer_progress_is_mapped_before_a_concurrent_rebuild(self):
        self.tbstate.save_item("a", {"title": "A"}, ["a1"])
        self.tbstate.save_item("b", {"title": "B"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        generation = self.tbstate.stream_generation()
        self.tbstate.write_pos(2)
        self.tbstate.atomic_write(
            self.tbstate.path("statusline.progress"), "%s 1 2\n" % generation)
        entered = threading.Event()
        rebuild_done = threading.Event()
        original_count = self.tbstate.stream_count
        calls = {"count": 0}

        def blocked_count():
            calls["count"] += 1
            if calls["count"] == 1:
                entered.set()
                rebuild_done.wait(0.25)
            return original_count()

        def replace_stream():
            entered.wait(2)
            with self.tbstate.rebuilding_stream():
                self.tbstate.save_item("x", {"title": "X"}, ["x1"])
                self.tbstate.save_queue({"items": ["x", "a", "b"]})
            rebuild_done.set()

        worker = threading.Thread(target=replace_stream)
        worker.start()
        with mock.patch.object(self.tbstate, "stream_count", side_effect=blocked_count):
            self.tbstate.consume_statusline_progress()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        bookmarks = self.tbstate.load_bookmarks()
        self.assertNotIn("x", bookmarks)
        self.assertEqual(bookmarks.get("a"), 1)
        self.assertEqual(bookmarks.get("b"), 1)

    def test_read_only_progress_consumer_skips_a_busy_turn_without_losing_state(self):
        self.seed_stream(["one", "two"], mode="manual")
        generation = self.tbstate.stream_generation()
        progress = self.tbstate.path("statusline.progress")
        self.tbstate.atomic_write(progress, "%s 1 2\n" % generation)
        acquired = threading.Event()
        release = threading.Event()

        def hold_turn():
            with self.tbstate.turn_guard():
                acquired.set()
                release.wait(2)

        worker = threading.Thread(target=hold_turn)
        worker.start()
        self.assertTrue(acquired.wait(2))
        started = time.monotonic()
        consumed = self.tbstate.consume_statusline_progress()
        elapsed = time.monotonic() - started
        release.set()
        worker.join(timeout=2)

        self.assertFalse(consumed)
        self.assertLess(elapsed, 0.5)
        self.assertTrue(os.path.exists(progress))

    def test_position_defaults_to_one(self):
        self.assertEqual(self.tbstate.read_pos(), 1)

    def test_corrupt_position_file_reads_as_one(self):
        self.tbstate.atomic_write(self.tbstate.path("pos"), "not-a-number\n")
        self.assertEqual(self.tbstate.read_pos(), 1)

    def test_position_never_goes_below_one(self):
        self.tbstate.write_pos(-5)
        self.assertEqual(self.tbstate.read_pos(), 1)


if __name__ == "__main__":
    unittest.main()
