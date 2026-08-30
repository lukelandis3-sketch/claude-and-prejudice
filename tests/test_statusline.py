"""The status line hot path.

Claude Code re-runs this once per assistant message with a 5s timeout, so it is tested
for speed as well as behaviour -- and above all for never failing loudly.
"""

import os
import time
import unittest

from support import IsolatedStateCase


class StatusLineTest(IsolatedStateCase):
    def test_prints_the_current_line(self):
        self.seed_stream(["Call me Ishmael.", "Some years ago."])
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "Call me Ishmael.")

    def test_timer_mode_advances_once_the_dwell_has_passed(self):
        self.seed_stream(["one", "two", "three"], mode="timer", dwell=1)
        self.tbstate.write_last_advance(time.time() - 10)
        self.assertEqual(self.run_statusline().stdout.strip(), "two")
        self.assertEqual(self.tbstate.read_pos(), 2)

    def test_timer_mode_holds_inside_the_dwell_window(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=600)
        self.tbstate.write_last_advance(time.time())
        for _ in range(3):
            self.assertEqual(self.run_statusline().stdout.strip(), "one")
        self.assertEqual(self.tbstate.read_pos(), 1)

    def test_a_long_idle_costs_one_line_not_hundreds(self):
        self.seed_stream(["l%d" % n for n in range(500)], mode="timer", dwell=1)
        self.tbstate.write_last_advance(time.time() - 86400)
        self.run_statusline()
        self.assertEqual(self.tbstate.read_pos(), 2)

    def test_turn_and_manual_modes_do_not_advance_here(self):
        for mode in ("turn", "manual"):
            with self.subTest(mode=mode):
                self.seed_stream(["one", "two"], mode=mode, dwell=1)
                self.tbstate.write_last_advance(time.time() - 100)
                self.run_statusline()
                self.assertEqual(self.tbstate.read_pos(), 1)

    def test_paused_does_not_advance(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=1, paused=True)
        self.tbstate.write_last_advance(time.time() - 100)
        self.run_statusline()
        self.assertEqual(self.tbstate.read_pos(), 1)

    def test_does_not_advance_past_the_end(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=1)
        self.tbstate.write_pos(2)
        self.tbstate.write_last_advance(time.time() - 100)
        self.assertEqual(self.run_statusline().stdout.strip(), "two")
        self.assertEqual(self.tbstate.read_pos(), 2)

    def test_statusline_surface_off_prints_nothing(self):
        self.seed_stream(["one"], statusline=False)
        self.assertEqual(self.run_statusline().stdout.strip(), "")

    def test_prefix_is_applied(self):
        self.seed_stream(["one"], mode="manual")
        config = self.tbstate.load_config()
        config["prefix"] = "book: "
        self.tbstate.save_config(config)
        self.assertEqual(self.run_statusline().stdout.strip(), "book: one")

    # ------------------------------------------------------------- wrapping

    def _wrap(self, command):
        self.tbstate.atomic_write(self.tbstate.path("wrapped.cmd"), command + "\n")

    def test_wrapped_status_line_is_rendered_alongside_the_book(self):
        self.seed_stream(["Call me Ishmael."], mode="manual")
        self._wrap("echo 'my own status line'")
        lines = self.run_statusline().stdout.strip().split("\n")
        self.assertEqual(lines, ["my own status line", "Call me Ishmael."])

    def test_wrapped_status_line_receives_the_session_json_on_stdin(self):
        self.seed_stream(["a line"], mode="manual")
        self._wrap("cat")
        output = self.run_statusline(stdin_json='{"model":"opus"}').stdout
        self.assertIn('{"model":"opus"}', output)

    def test_wrapped_status_line_survives_our_state_being_absent(self):
        for name in ("hot.env", "pos", "count", "stream.txt"):
            path = self.tbstate.path(name)
            if os.path.exists(path):
                os.unlink(path)
        self._wrap("echo 'still here'")
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "still here")

    def test_failing_wrapped_command_does_not_break_the_book_line(self):
        self.seed_stream(["a line"], mode="manual")
        self._wrap("exit 7")
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "a line")

    # ---------------------------------------------------------- robustness

    def test_corrupt_state_prints_nothing_and_exits_zero(self):
        self.seed_stream(["one", "two"])
        self.tbstate.atomic_write(self.tbstate.path("pos"), "garbage\n")
        self.tbstate.atomic_write(self.tbstate.path("count"), "also garbage\n")
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_no_state_at_all_exits_zero_and_is_silent(self):
        import shutil
        shutil.rmtree(self.tbstate.home())
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_empty_stream_prints_nothing(self):
        self.tbstate.save_queue({"items": []})
        self.tbstate.rebuild_stream()
        self.tbstate.save_config(self.tbstate.load_config())
        self.assertEqual(self.run_statusline().stdout.strip(), "")

    # ----------------------------------------------------------- speed

    def test_stays_far_inside_the_five_second_timeout(self):
        # A full-length novel, and a bookmark deep into it.
        self.seed_stream(["line %d of a long book." % n for n in range(20000)], mode="manual")
        self.tbstate.write_pos(19000)

        start = time.time()
        for _ in range(20):
            self.assertEqual(self.run_statusline().returncode, 0)
        average = (time.time() - start) / 20

        # Generous bound: this asserts the design (no full scans, no Python), not the host.
        self.assertLess(average, 0.5, "status line averaged %.3fs per invocation" % average)


if __name__ == "__main__":
    unittest.main()
