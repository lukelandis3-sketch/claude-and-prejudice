"""The hook-facing subcommands: advance policy, and never blowing up a turn."""

import json
import os
import time
import unittest

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

    def test_sync_writes_hot_env_for_the_shell(self):
        self.seed_stream(["A line."])
        os.unlink(self.tbstate.path("hot.env"))
        self.run_cli("sync")
        self.assertTrue(os.path.exists(self.tbstate.path("hot.env")))

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

    def test_timer_mode_leaves_advancing_to_the_status_line_when_it_is_on(self):
        # The status line runs far more often, so Stop must not double-advance.
        self.seed_stream(["one", "two"], mode="timer", dwell=0, statusline=True)
        self.run_cli("advance")
        self.assertEqual(self.pos(), 1)

    def test_timer_mode_advances_on_stop_when_status_line_is_off(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=1, statusline=False)
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

    def test_hooks_print_nothing_on_the_happy_path(self):
        self.seed_stream(["one", "two"])
        for command in ("sync", "advance", "restore"):
            result = self.run_cli(command)
            self.assertEqual(result.stdout, "", "%s printed %r" % (command, result.stdout))

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

    def test_unknown_command_is_an_error_not_a_crash(self):
        result = self.run_cli("nonsense")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
