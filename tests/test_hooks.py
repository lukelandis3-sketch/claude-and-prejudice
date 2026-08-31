"""The hook-facing subcommands: advance policy, and never blowing up a turn."""

import json
import os
import threading
import time
import unittest
import contextlib
import io
from unittest import mock

import support
from support import IsolatedStateCase


class HookTest(IsolatedStateCase):
    def settings(self):
        path = self.tbstate.settings_path()
        if not os.path.exists(path):
            return {}
        with open(path) as fh:
            return json.load(fh)

    def spinner_line(self):
        return (self.settings().get("spinnerVerbs") or {}).get("verbs", [None])[0]

    def pos(self):
        return self.tbstate.read_pos()

    # ------------------------------------------------------------------ SessionStart

    def test_sync_creates_settings_file_and_shows_current_line(self):
        self.seed_stream(["First line.", "Second line."])
        result = self.run_cli("sync")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(self.tbstate.settings_path()))
        self.assertEqual(self.spinner_line(), "First line.")

    def test_sync_writes_inert_status_state_for_the_shell(self):
        self.seed_stream(["A line."])
        os.unlink(self.tbstate.path("status.control"))
        os.unlink(self.tbstate.path("status.prefix"))
        self.run_cli("sync")
        self.assertTrue(os.path.exists(self.tbstate.path("status.control")))
        self.assertTrue(os.path.exists(self.tbstate.path("status.prefix")))

    def test_sync_does_not_publish_empty_generations_without_a_queue(self):
        self.run_cli("sync", "--quiet")
        self.run_cli("sync", "--quiet")
        self.assertFalse(os.path.exists(self.tbstate.path("stream.gen")))

    def test_sync_retires_a_stale_stream_when_the_queue_is_empty(self):
        self.seed_stream(["ghost prose"], mode="manual")
        self.tbstate.save_queue({"items": []})

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertFalse(os.path.exists(self.tbstate.path("stream.gen")))
        self.assertEqual(self.run_statusline().stdout, "")

    def test_sync_cannot_retire_a_book_installed_during_empty_queue_repair(self):
        self.seed_stream(["ghost prose"], mode="manual")
        self.tbstate.save_queue({"items": []})
        entered = threading.Event()
        replacement_done = threading.Event()
        original_retire = self.tbstate.retire_stream

        def delayed_retire():
            entered.set()
            replacement_done.wait(0.25)
            original_retire()

        def install_replacement():
            entered.wait(2)
            with self.tbstate.rebuilding_stream():
                self.tbstate.save_item(
                    "replacement", {"title": "Replacement", "kind": "book"}, ["new prose"])
                self.tbstate.save_queue({"items": ["replacement"]})
            replacement_done.set()

        worker = threading.Thread(target=install_replacement)
        worker.start()
        import thinking_book
        with mock.patch.object(self.tbstate, "retire_stream", side_effect=delayed_retire):
            thinking_book.cmd_sync(["--quiet"])
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(self.tbstate.load_queue()["items"], ["replacement"])
        self.assertTrue(self.tbstate.stream_generation())
        self.assertEqual(self.tbstate.stream_line(1), "new prose")

    def test_sync_cannot_reenable_surfaces_after_off_completes(self):
        self.seed_stream(["one"], mode="manual")
        entered = threading.Event()
        release = threading.Event()
        original_write_hot = self.tbstate.write_hot_state

        def delayed_write_hot(config):
            entered.set()
            release.wait(5)
            return original_write_hot(config)

        import thinking_book
        with mock.patch.object(self.tbstate, "write_hot_state", side_effect=delayed_write_hot):
            sync = threading.Thread(target=thinking_book.cmd_sync, args=(["--quiet"],))
            sync.start()
            self.assertTrue(entered.wait(5), "sync did not reach hot-state publication")

            def turn_off():
                with contextlib.redirect_stdout(io.StringIO()):
                    thinking_book.cmd_off([])

            off = threading.Thread(target=turn_off)
            off.start()
            time.sleep(0.1)
            release.set()
            sync.join(timeout=5)
            off.join(timeout=5)

        self.assertFalse(sync.is_alive())
        self.assertFalse(off.is_alive())
        config = self.tbstate.load_config()
        self.assertTrue(config["paused"])
        self.assertEqual(config["surfaces"], {"statusline": False, "spinner": False})
        with open(self.tbstate.path("status.control")) as fh:
            self.assertEqual(fh.read().split()[4:7], ["1", "0", "0"])
        self.assertNotIn("spinnerVerbs", self.settings())

    def test_sync_repoints_a_missing_statusline_from_an_older_install(self):
        import thinking_book
        old = 'sh "/tmp/old claude-and-prejudice/scripts/statusline.sh"'
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({
                "theme": "dark",
                "statusLine": {"type": "command", "command": old, "padding": 3},
            }, fh)

        self.run_cli("sync", "--quiet")

        settings = self.settings()
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["statusLine"]["command"], thinking_book.statusline_command())
        self.assertEqual(settings["statusLine"]["padding"], 3)

        self.run_cli("off")
        self.assertNotIn("statusLine", self.settings())
        self.assertEqual(self.settings()["theme"], "dark")

    def test_sync_repoints_an_older_install_even_while_its_cache_still_exists(self):
        import thinking_book
        old_root = os.path.join(self.config_dir, "cache", "claude-and-prejudice")
        old_scripts = os.path.join(old_root, "scripts")
        os.makedirs(old_scripts)
        for name in ("statusline.sh", "thinking_book.py"):
            with open(os.path.join(old_scripts, name), "w") as fh:
                fh.write("# old cache\n")
        old = 'sh "%s"' % os.path.join(old_scripts, "statusline.sh")
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": {"type": "command", "command": old}}, fh)

        self.run_cli("sync", "--quiet")

        self.assertEqual(
            self.settings()["statusLine"]["command"], thinking_book.statusline_command())

    def test_sync_repair_restores_the_statusline_an_old_install_wrapped(self):
        import thinking_book
        old = 'sh "/tmp/claude-thinking-book/scripts/statusline.sh"'
        original = {"type": "command", "command": "my-prompt", "padding": 2}
        config = self.tbstate.load_config()
        config["wrapped_statusline"] = original
        self.tbstate.save_config(config)
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": {"type": "command", "command": old}}, fh)

        self.run_cli("sync", "--quiet")
        self.assertEqual(
            self.settings()["statusLine"]["command"], thinking_book.statusline_command())

        self.run_cli("off")
        self.assertEqual(self.settings()["statusLine"], original)

    def test_sync_does_not_repair_a_disabled_statusline_surface(self):
        old = 'sh "/tmp/old-thinking-book/scripts/statusline.sh"'
        config = self.tbstate.load_config()
        config["surfaces"]["statusline"] = False
        self.tbstate.save_config(config)
        with open(self.tbstate.settings_path(), "w") as fh:
            json.dump({"statusLine": {"type": "command", "command": old}}, fh)

        self.run_cli("sync", "--quiet")

        self.assertEqual(self.settings()["statusLine"]["command"], old)

    def test_malformed_settings_do_not_prevent_stream_recovery(self):
        import shutil
        self.seed_stream(["one", "two"], mode="manual")
        os.unlink(self.tbstate.path("status.control"))
        os.unlink(self.tbstate.path("status.prefix"))
        shutil.rmtree(self.tbstate.path("stream-generations"))
        with open(self.tbstate.settings_path(), "w") as fh:
            fh.write("{ broken")

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertTrue(os.path.exists(self.tbstate.path("status.control")))
        self.assertTrue(os.path.exists(self.tbstate.path("status.prefix")))
        self.assertEqual(self.tbstate.stream_line(1), "one")

    def test_sync_rebuilds_when_generation_directory_is_gone(self):
        import shutil
        self.seed_stream(["one", "two"], mode="manual")
        shutil.rmtree(self.tbstate.path("stream-generations"))
        self.run_cli("sync", "--quiet")
        self.assertEqual(self.tbstate.stream_line(1), "one")
        self.assertEqual(self.run_statusline().stdout.strip(), "📖 one")

    def test_sync_upgrades_legacy_shards_when_wpm_is_enabled(self):
        self.seed_stream(["one two three"], mode="timer", wpm=250)
        marker = os.path.join(self.tbstate.stream_generation_dir(), "format")
        os.unlink(marker)
        self.run_cli("sync", "--quiet")
        self.assertTrue(self.tbstate.stream_has_word_counts())

    def test_sync_repairs_a_missing_hud_shard_when_hud_is_enabled(self):
        self.seed_stream(["one", "two"], mode="manual")
        config = self.tbstate.load_config()
        config["hud"] = True
        self.tbstate.save_config(config)
        self.tbstate.rebuild_stream(include_hud=True)
        hud = os.path.join(self.tbstate.stream_generation_dir(), "0.hud")
        os.unlink(hud)

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(os.path.isfile(hud))
        self.assertIn("Test Item ·", self.run_statusline().stdout)

    def test_sync_repairs_a_truncated_existing_hud_shard(self):
        self.seed_stream(
            ["line-%d" % number for number in range(300)], mode="manual")
        config = self.tbstate.load_config()
        config["hud"] = True
        self.tbstate.save_config(config)
        self.tbstate.rebuild_stream(include_hud=True)
        hud = os.path.join(self.tbstate.stream_generation_dir(), "0.hud")
        with open(hud) as fh:
            first = fh.readline()
        self.tbstate.atomic_write(hud, first)
        self.tbstate.write_pos(2)

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        status = self.run_statusline().stdout.splitlines()
        self.assertTrue(status[0].startswith("Test Item ·"), status)
        self.assertIn("2/300", status[0])

    def test_sync_moves_a_legacy_root_index_into_the_generation(self):
        self.seed_stream(["one", "two"], mode="manual")
        generation_index = os.path.join(self.tbstate.stream_generation_dir(), "index")
        with open(generation_index) as fh:
            legacy_index = fh.read()
        self.tbstate.atomic_write(self.tbstate.path("stream.idx"), legacy_index)
        os.unlink(generation_index)

        self.run_cli("sync", "--quiet")

        self.assertTrue(self.tbstate.stream_has_index())
        self.assertFalse(os.path.exists(self.tbstate.path("stream.idx")))

    def test_sync_repairs_a_malformed_generation_index(self):
        self.seed_stream(["one", "two"], mode="manual")
        index = os.path.join(self.tbstate.stream_generation_dir(), "index")
        self.tbstate.atomic_write(index, "garbage\n")

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([row[1] for row in self.tbstate.load_index()], ["test-item"])
        self.assertEqual(self.tbstate.stream_line(2), "two")

    def test_sync_repairs_a_count_that_truncates_the_last_passages(self):
        self.seed_stream(["one", "two", "three"], mode="manual")
        count = os.path.join(self.tbstate.stream_generation_dir(), "count")
        self.tbstate.atomic_write(count, "1\n")

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.tbstate.stream_count(), 3)
        self.assertEqual(self.tbstate.stream_line(3), "three")

    def test_sync_repairs_a_truncated_intermediate_shard(self):
        lines = ["line-%d" % number for number in range(600)]
        self.seed_stream(lines, mode="manual")
        shard = os.path.join(self.tbstate.stream_generation_dir(), "0.txt")
        with open(shard) as fh:
            first_record = fh.readline()
        self.tbstate.atomic_write(shard, first_record)

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.tbstate.stream_line(2), "line-1")
        self.assertEqual(self.tbstate.stream_line(600), "line-599")

    def test_sync_repairs_same_size_invalid_utf8_in_a_shard(self):
        self.seed_stream(["one", "two"], mode="manual")
        shard = os.path.join(self.tbstate.stream_generation_dir(), "0.txt")
        with open(shard, "rb") as fh:
            corrupted = bytearray(fh.read())
        corrupted[2] = 0xff
        self.tbstate.atomic_write_bytes(shard, bytes(corrupted))

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.tbstate.stream_line(1), "one")
        self.assertEqual(self.tbstate.stream_line(2), "two")

    def test_sync_repairs_a_stream_that_no_longer_matches_the_queue(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.tbstate.save_item(
            "second-item", {"title": "Second Item", "kind": "book"}, ["three"])
        self.tbstate.save_queue({"items": ["test-item", "second-item"]})

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.tbstate.stream_count(), 3)
        self.assertEqual(
            [row[1] for row in self.tbstate.load_index()],
            ["test-item", "second-item"],
        )
        self.assertEqual(self.tbstate.stream_line(3), "three")

    def test_sync_clamps_a_cursor_beyond_the_recovered_stream(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.tbstate.write_pos(999)

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.tbstate.read_pos(), 2)
        self.assertEqual(self.tbstate.stream_line(self.tbstate.read_pos()), "two")

    def test_sync_recovers_from_an_overlong_generation_pointer(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.tbstate.atomic_write(self.tbstate.path("stream.gen"), "a" * 10_000 + "\n")

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertLessEqual(len(self.tbstate.stream_generation()), 64)
        self.assertEqual(self.tbstate.stream_line(1), "one")

    def test_sync_while_fully_off_does_not_recreate_settings(self):
        config = self.tbstate.load_config()
        config["paused"] = True
        config["surfaces"] = {"statusline": False, "spinner": False}
        self.tbstate.save_config(config)
        try:
            os.unlink(self.tbstate.settings_path())
        except OSError:
            pass

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(os.path.exists(self.tbstate.settings_path()))

    # -------------------------------------------------------------------- Stop: turn

    def test_turn_mode_advances_exactly_one_line_per_stop(self):
        self.seed_stream(["one", "two", "three"], mode="turn")
        for expected in ("two", "three"):
            self.run_cli("advance")
            self.assertEqual(self.spinner_line(), expected)

    def test_turn_mode_stops_at_the_end_of_the_stream(self):
        self.seed_stream(["only line"], mode="turn")
        self.run_cli("advance")
        self.run_cli("advance")
        self.assertEqual(self.pos(), 1)
        self.assertEqual(self.spinner_line(), "only line")

    def test_explicit_next_consumes_the_following_turn_mode_stop(self):
        self.seed_stream(["one", "two", "three"], mode="turn")
        env = {"CLAUDE_CODE_SESSION_ID": "session-next"}

        self.run_cli("next", env=env)
        self.run_cli("advance", "--quiet", env=env)

        self.assertEqual(self.pos(), 2)
        self.assertEqual(self.spinner_line(), "two")

    def test_explicit_back_consumes_the_following_turn_mode_stop(self):
        self.seed_stream(["one", "two", "three"], mode="turn")
        self.tbstate.write_pos(3)
        env = {"CLAUDE_CODE_SESSION_ID": "session-back"}

        self.run_cli("back", env=env)
        self.run_cli("advance", "--quiet", env=env)

        self.assertEqual(self.pos(), 2)
        self.assertEqual(self.spinner_line(), "two")

    # ------------------------------------------------------------------ Stop: manual

    def test_manual_mode_never_advances_on_stop(self):
        self.seed_stream(["one", "two"], mode="manual")
        for _ in range(3):
            self.run_cli("advance")
        self.assertEqual(self.pos(), 1)

    def test_manual_mode_still_advances_via_next(self):
        self.seed_stream(["one", "two"], mode="manual")
        result = self.run_cli("next")
        self.assertEqual(result.stdout.strip(), "two")
        self.assertEqual(self.pos(), 2)

    # ------------------------------------------------------------------- Stop: timer

    def test_timer_mode_advances_when_statusline_is_configured_but_not_live(self):
        # A freshly installed statusLine may not mount until restart. Its config flag
        # alone must not make Stop defer to a surface that has never run this session.
        self.seed_stream(["one", "two"], mode="timer", dwell=0, statusline=True)
        self.tbstate.write_last_advance(time.time() - 5)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 2)

    def test_timer_mode_leaves_advancing_to_a_live_statusline(self):
        # Once the surface has actually run, Stop must not double-advance.
        self.seed_stream(["one", "two"], mode="timer", dwell=600, statusline=True)
        self.tbstate.write_last_advance(time.time())
        self.run_statusline()
        self.assertTrue(os.path.exists(self.tbstate.path("statusline.live.global")))
        self.tbstate.write_last_advance(time.time() - 1000)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 1)

    def test_stop_takes_over_the_clock_after_pane_off(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=600, statusline=True)
        self.tbstate.write_last_advance(time.time())
        self.run_statusline()
        self.run_cli("pane", "off")
        self.tbstate.write_last_advance(time.time() - 1000)
        self.run_cli("advance", "--quiet")
        self.assertEqual(self.pos(), 2)

    def test_timer_mode_advances_on_stop_when_status_line_is_off(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=1, statusline=False)
        self.tbstate.write_last_advance(time.time() - 5)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 2)

    def test_concurrent_timer_stop_hooks_advance_only_once(self):
        self.seed_stream(
            ["one", "two", "three"], mode="timer", dwell=1, statusline=False)
        self.tbstate.write_last_advance(time.time() - 10)
        first_reading = threading.Event()
        second_reading = threading.Event()
        calls = {"count": 0}
        gate = threading.Lock()
        original_read = self.tbstate.read_last_advance

        def coordinated_read():
            with gate:
                calls["count"] += 1
                number = calls["count"]
            if number == 1:
                first_reading.set()
                second_reading.wait(0.25)
            elif number == 2:
                second_reading.set()
            return original_read()

        import thinking_book
        with mock.patch.object(
                self.tbstate, "read_last_advance", side_effect=coordinated_read):
            first = threading.Thread(target=thinking_book.cmd_advance, args=(["--quiet"],))
            second = threading.Thread(target=thinking_book.cmd_advance, args=(["--quiet"],))
            first.start()
            self.assertTrue(first_reading.wait(2))
            second.start()
            first.join(timeout=5)
            second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(self.tbstate.read_pos(), 2)

    def test_busy_import_makes_quiet_stop_skip_instead_of_waiting(self):
        self.seed_stream(["one", "two"], mode="turn")
        acquired = threading.Event()
        release = threading.Event()

        def hold_turn():
            with self.tbstate.turn_guard():
                acquired.set()
                release.wait(2)

        worker = threading.Thread(target=hold_turn)
        worker.start()
        self.assertTrue(acquired.wait(2))
        import thinking_book
        started = time.monotonic()
        thinking_book.cmd_advance(["--quiet"])
        elapsed = time.monotonic() - started
        release.set()
        worker.join(timeout=2)

        self.assertLess(elapsed, 0.5)
        self.assertEqual(self.tbstate.read_pos(), 1)

    def test_stop_uses_word_count_when_status_line_is_off(self):
        long_line = " ".join("word" for _ in range(20))
        self.seed_stream(["Heading", long_line, "done"], mode="timer",
                         statusline=False, wpm=60)
        self.tbstate.write_last_advance(time.time() - 3)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 2)
        self.tbstate.write_last_advance(time.time() - 5)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 2)

    def test_timer_mode_cold_start_shows_the_first_line_before_advancing(self):
        # With no clock yet, the page must not read as infinitely overdue.
        self.seed_stream(["one", "two"], mode="timer", dwell=1, statusline=False)
        self.tbstate.write_last_advance(0)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 1)
        self.assertEqual(self.spinner_line(), "one")

    def test_timer_mode_holds_the_line_inside_the_dwell_window(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=600, statusline=False)
        self.tbstate.write_last_advance(time.time())
        self.run_cli("advance")
        self.assertEqual(self.pos(), 1)

    # ---------------------------------------------------------- POSIX dispatcher

    def _fake_python_env(self):
        fake_bin = os.path.join(self.config_dir, "fake-bin")
        os.makedirs(fake_bin, exist_ok=True)
        marker = os.path.join(self.config_dir, "python-ran")
        fake_python = os.path.join(fake_bin, "python3")
        with open(fake_python, "w") as fh:
            fh.write("#!/bin/sh\n: > \"$CLAUDE_CONFIG_DIR/python-ran\"\nexit 9\n")
        os.chmod(fake_python, 0o755)
        return marker, {"PATH": fake_bin + os.pathsep + os.environ["PATH"]}

    def test_posix_stop_skips_python_for_clean_manual_state(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.run_cli("sync", "--quiet")
        marker, env = self._fake_python_env()

        result = self.run_stop(env=env)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(os.path.exists(marker))

    def test_posix_stop_skips_python_when_live_timer_and_spinner_are_current(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=600)
        self.run_cli("sync", "--quiet")
        self.run_statusline()
        marker, env = self._fake_python_env()

        result = self.run_stop(env=env)

        self.assertEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(marker))

    def test_posix_stop_falls_back_for_turn_mode(self):
        self.seed_stream(["one", "two"], mode="turn")
        self.run_cli("sync", "--quiet")
        marker, env = self._fake_python_env()

        result = self.run_stop(env=env)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertTrue(os.path.exists(marker))

    def test_posix_stop_falls_back_when_spinner_cursor_is_dirty(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.run_cli("sync", "--quiet")
        self.tbstate.write_pos(2)
        marker, env = self._fake_python_env()

        self.run_stop(env=env)

        self.assertTrue(os.path.exists(marker))
        self.run_stop()
        with open(self.tbstate.path("spinner.cursor")) as fh:
            self.assertEqual(len(fh.read().split()), 3)

    def test_posix_stop_is_silent_and_nonfatal_with_corrupt_control_state(self):
        self.seed_stream(["one"], mode="manual")
        self.tbstate.atomic_write(self.tbstate.path("stop.control"), "exit 42\n")
        marker, env = self._fake_python_env()

        result = self.run_stop(env=env)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertTrue(os.path.exists(marker))

    def test_manual_next_cannot_leave_a_stale_turn_suppression_marker(self):
        self.seed_stream(["one", "two", "three"], mode="manual")
        env = {"CLAUDE_CODE_SESSION_ID": "manual-then-turn"}
        self.run_cli("sync", "--quiet", env=env)

        self.run_cli("next", env=env)
        self.run_stop(env=env)
        self.run_cli("mode", "turn", env=env)
        self.run_stop(env=env)

        self.assertEqual(self.pos(), 3)

    def test_empty_library_stop_never_starts_python(self):
        self.run_cli("sync", "--quiet")
        marker, env = self._fake_python_env()

        result = self.run_stop(env=env)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(os.path.exists(marker))

    def test_paused_turn_mode_stop_never_starts_python(self):
        self.seed_stream(["one", "two"], mode="turn")
        self.run_cli("pause")
        self.run_cli("sync", "--quiet")
        marker, env = self._fake_python_env()

        self.run_stop(env=env)

        self.assertFalse(os.path.exists(marker))

    def test_old_two_field_spinner_cursor_safely_falls_back_once(self):
        self.seed_stream(["one"], mode="manual")
        self.run_cli("sync", "--quiet")
        generation = self.tbstate.stream_generation()
        self.tbstate.atomic_write(
            self.tbstate.path("spinner.cursor"), "%s 1\n" % generation)
        marker, env = self._fake_python_env()

        self.run_stop(env=env)

        self.assertTrue(os.path.exists(marker))

    def test_pause_clears_an_unconsumed_explicit_turn_marker(self):
        self.seed_stream(["one", "two"], mode="turn")
        env = {"CLAUDE_CODE_SESSION_ID": "pause-after-next"}
        self.run_cli("next", env=env)
        marker = self.tbstate.path("stop.skip.pause-after-next")
        self.assertTrue(os.path.exists(marker))

        self.run_cli("pause", env=env)

        self.assertFalse(os.path.exists(marker))

    def test_mode_change_clears_an_unconsumed_explicit_turn_marker(self):
        self.seed_stream(["one", "two"], mode="turn")
        env = {"CLAUDE_CODE_SESSION_ID": "mode-after-next"}
        self.run_cli("next", env=env)
        marker = self.tbstate.path("stop.skip.mode-after-next")
        self.assertTrue(os.path.exists(marker))

        self.run_cli("mode", "manual", env=env)

        self.assertFalse(os.path.exists(marker))

    # ------------------------------------------------------------------------ paused

    def test_paused_never_advances(self):
        self.seed_stream(["one", "two"], mode="turn", paused=True)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 1)

    # ---------------------------------------------------------------------- surfaces

    def test_spinner_surface_off_leaves_settings_untouched(self):
        self.seed_stream(["one", "two"])
        config = self.tbstate.load_config()
        config["surfaces"]["spinner"] = False
        self.tbstate.save_config(config)
        self.run_cli("advance")
        self.assertNotIn("spinnerVerbs", self.settings())

    def test_restore_removes_the_spinner_override(self):
        self.seed_stream(["one"])
        self.run_cli("sync")
        self.assertIsNotNone(self.spinner_line())
        self.run_cli("restore")
        self.assertNotIn("spinnerVerbs", self.settings())

    def test_sync_clears_the_previous_sessions_statusline_liveness(self):
        self.seed_stream(["one"])
        self.tbstate.atomic_write(self.tbstate.path("statusline.live.global"), "")
        self.run_cli("sync", "--quiet")
        self.assertFalse(os.path.exists(self.tbstate.path("statusline.live.global")))

    def test_sync_removes_only_stale_statusline_liveness_markers(self):
        self.seed_stream(["one"])
        stale = self.tbstate.path("statusline.live.stale")
        recent = self.tbstate.path("statusline.live.recent")
        self.tbstate.atomic_write(stale, "")
        self.tbstate.atomic_write(recent, "")
        old = time.time() - 31 * 24 * 60 * 60
        os.utime(stale, (old, old))
        self.run_cli("sync", "--quiet")
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(recent))

    def test_statusline_liveness_is_scoped_by_session_id_when_available(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=600, statusline=True)
        self.tbstate.write_last_advance(time.time())
        self.run_statusline(env={"CLAUDE_CODE_SESSION_ID": "session-a"})
        self.assertTrue(os.path.exists(self.tbstate.path("statusline.live.session-a")))
        self.tbstate.write_last_advance(time.time() - 1000)
        self.run_cli("advance", "--quiet", env={"CLAUDE_CODE_SESSION_ID": "session-b"})
        self.assertEqual(self.pos(), 2)

    def test_invalid_or_oversized_session_ids_use_the_global_marker(self):
        import thinking_book
        self.seed_stream(["one"])
        for session_id in ("abc\n", "x" * 65):
            with self.subTest(session_id=session_id):
                os.environ["CLAUDE_CODE_SESSION_ID"] = session_id
                self.assertEqual(
                    thinking_book.statusline_live_path(),
                    self.tbstate.path("statusline.live.global"),
                )
                result = self.run_statusline(env={"CLAUDE_CODE_SESSION_ID": session_id})
                self.assertEqual(result.returncode, 0)
                self.assertTrue(os.path.exists(self.tbstate.path("statusline.live.global")))

    # ------------------------------------------------------------------- robustness

    def test_hooks_exit_zero_with_no_book_queued(self):
        for command in ("sync", "advance", "restore"):
            result = self.run_cli(command)
            self.assertEqual(result.returncode, 0, "%s: %s" % (command, result.stderr))

    def test_hooks_exit_zero_with_corrupt_state(self):
        self.seed_stream(["one", "two"])
        self.tbstate.atomic_write(self.tbstate.path("pos"), "garbage")
        self.tbstate.atomic_write(self.tbstate.path("config.json"), "{{{")
        self.tbstate.atomic_write(self.tbstate.path("queue.json"), "not json")
        for command in ("sync", "advance", "restore"):
            result = self.run_cli(command)
            self.assertEqual(result.returncode, 0, "%s: %s" % (command, result.stderr))

    def test_hooks_print_nothing_when_invoked_with_quiet(self):
        # hooks.json passes --quiet; a person running the same command gets feedback.
        self.seed_stream(["one", "two"])
        for command in ("sync", "advance", "restore"):
            result = self.run_cli(command, "--quiet")
            self.assertEqual(result.stdout, "", "%s printed %r" % (command, result.stdout))

    def test_hook_commands_report_when_run_interactively(self):
        self.seed_stream(["one", "two"])
        result = self.run_cli("sync")
        self.assertEqual(result.stdout.strip(), "one")

    def test_network_failure_is_reported_cleanly_not_as_a_traceback(self):
        # Unreachable host: the user should get one line, not a stack trace.
        result = self.run_cli("read", "https://localhost:1/nope")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(result.stderr.strip())

    def test_missing_file_is_reported_cleanly(self):
        result = self.run_cli("load", "/nonexistent/book.epub")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    def test_bare_invocation_prints_the_dashboard_not_an_unknown_command(self):
        # Regression: a quoted "$ARGUMENTS" with nothing typed delivers one empty string.
        result = self.run_cli("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No book yet", result.stdout)
        self.assertIn("/thinking-book:book <title|url|file>", result.stdout)
        self.assertNotIn("unknown command", result.stderr)

    def test_setup_command_uses_native_questions_and_only_the_safe_cli(self):
        path = os.path.join(support.REPO, "commands", "setup.md")
        with open(path) as fh:
            source = fh.read()
        self.assertIn("allowed-tools: Bash(python3:*)", source)
        self.assertNotIn("AskUserQuestion", source)
        self.assertIn("thinking_book.py", source)
        self.assertIn("title, URL, or file path", source)
        self.assertNotIn("settings.json", source)
        self.assertIn("!`python3", source)

    def test_commands_are_user_invocable_only_and_cost_no_always_on_context(self):
        command_dir = os.path.join(support.REPO, "commands")
        names = sorted(name for name in os.listdir(command_dir) if name.endswith(".md"))
        self.assertEqual(names, ["b.md", "book.md", "n.md", "setup.md"])
        for name in names:
            with self.subTest(name=name):
                with open(os.path.join(command_dir, name)) as fh:
                    frontmatter = fh.read().split("---", 2)[1]
                self.assertIn("disable-model-invocation: true", frontmatter)

    def test_help_command_prints_task_oriented_help(self):
        result = self.run_cli("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Read: /thinking-book:book <title|url|file>", result.stdout)
        self.assertNotIn("refresh-feeds", result.stdout)
        self.assertNotIn("All commands:", result.stdout)
        self.assertNotIn("display on", result.stdout)

    def test_setup_is_one_short_backend_call_with_sensible_defaults(self):
        path = os.path.join(support.REPO, "commands", "setup.md")
        with open(path) as fh:
            source = fh.read()
        self.assertIn('thinking_book.py" start "$ARGUMENTS"', source)
        for forbidden in ("preflight", "preset", "retry", "summary", "dashboard",
                          "run exactly", "run no tools"):
            self.assertNotIn(forbidden, source)
        self.assertLessEqual(len(source.split()), 40)

    def test_command_prompts_require_verbatim_output(self):
        for name in ("book.md", "setup.md"):
            with open(os.path.join(support.REPO, "commands", name)) as fh:
                source = fh.read()
            self.assertIn("verbatim", source)
            self.assertNotIn("briefly", source)

    def test_manual_hook_does_not_rewrite_an_unchanged_spinner(self):
        self.seed_stream(["one"], mode="manual")
        self.run_cli("sync", "--quiet")
        before = os.stat(self.tbstate.settings_path()).st_ino
        self.run_cli("advance", "--quiet")
        self.assertEqual(os.stat(self.tbstate.settings_path()).st_ino, before)

    def test_unknown_command_is_an_error_not_a_crash(self):
        result = self.run_cli("statsu")
        self.assertEqual(result.returncode, 2)

    def test_unknown_command_names_the_version_and_path_it_ran_from(self):
        # A directory-source install goes stale silently: `unknown command 'repair'` gave
        # no hint that the fix was a git pull.
        result = self.run_cli("repair-typo")
        self.assertEqual(result.returncode, 2)
        self.assertIn("thinking-book", result.stderr)
        self.assertIn(support.REPO, result.stderr)
        self.assertIn("git pull", result.stderr)
        self.assertIn("/thinking-book:book help", result.stderr)

    def test_arguments_arriving_as_one_blob_are_split(self):
        # Slash commands pass "$ARGUMENTS" as a single quoted argument.
        self.seed_stream(["one", "two", "three"], mode="manual")
        result = self.run_cli("next 2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "three")

    def test_a_pasted_second_slash_command_is_ignored_not_executed(self):
        # Regression: unquoted $ARGUMENTS let a pasted newline reach the shell, which
        # tried to run "/thinking-book:book" as a program.
        self.seed_stream(["one", "two"], mode="manual")
        result = self.run_cli("next\n/thinking-book:book pane on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("second slash command", result.stderr)
        self.assertEqual(result.stdout.strip(), "two")

    def test_a_real_path_argument_is_not_mistaken_for_a_slash_command(self):
        result = self.run_cli("load", "/nonexistent/book.epub")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)
        self.assertNotIn("second slash command", result.stderr)

    def test_one_blob_load_preserves_unquoted_and_quoted_paths_with_spaces(self):
        directory = os.path.join(self.config_dir, "My Books")
        os.makedirs(directory)
        path = os.path.join(directory, "small book.txt")
        with open(path, "w") as fh:
            fh.write("A readable sentence in a path containing spaces.")
        for blob in ("load " + path, 'load "%s"' % path):
            with self.subTest(blob=blob):
                result = self.run_cli(blob)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_path_with_a_colon_is_reported_as_a_path(self):
        path = os.path.join(self.config_dir, "missing: book.txt")
        result = self.run_cli("load " + path)
        self.assertEqual(result.returncode, 1)
        self.assertIn(path, result.stderr)
        self.assertNotIn("second slash command", result.stderr)

    def test_path_command_still_filters_a_pasted_second_slash_command(self):
        path = os.path.join(self.config_dir, "book.txt")
        with open(path, "w") as fh:
            fh.write("A readable sentence.")
        result = self.run_cli("load %s\n/thinking-book:book off" % path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("second slash command", result.stderr)

    def test_version_reports_the_manifest_version_and_root(self):
        result = self.run_cli("version")
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(support.REPO, ".claude-plugin", "plugin.json")) as fh:
            expected = json.load(fh)["version"]
        self.assertIn(expected, result.stdout)
        self.assertIn(support.REPO, result.stdout)

    def test_plugin_root_tolerates_a_trailing_slash(self):
        result = self.run_cli("version", env={"CLAUDE_PLUGIN_ROOT": support.REPO + "/"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("//", result.stdout)


if __name__ == "__main__":
    unittest.main()
