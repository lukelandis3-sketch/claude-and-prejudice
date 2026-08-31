"""The reader pane's pure parts: key mapping and frame rendering."""

import io
import os
import sys
import unittest
from unittest import mock

import support  # noqa: F401  (path setup)
import reader


class KeyMappingTest(unittest.TestCase):
    def test_advance_keys(self):
        for key in (" ", "n", "j", "\x1b[C", "\x1b[B"):
            self.assertEqual(reader.action_for(key), "advance", repr(key))

    def test_back_keys(self):
        for key in ("b", "k", "\x1b[D", "\x1b[A"):
            self.assertEqual(reader.action_for(key), "back", repr(key))

    def test_quit_keys(self):
        for key in ("q", "\x03", "\x04", "\x1b"):
            self.assertEqual(reader.action_for(key), "quit", repr(key))

    def test_pause_and_pace_keys(self):
        self.assertEqual(reader.action_for("p"), "pause")
        for key in ("+", "="):
            self.assertEqual(reader.action_for(key), "faster")
        self.assertEqual(reader.action_for("-"), "slower")

    def test_unknown_keys_do_nothing(self):
        for key in ("z", "1", "", "\t"):
            self.assertIsNone(reader.action_for(key), repr(key))


class ReadKeyTest(unittest.TestCase):
    """Regression: a buffered one-byte read drained the escape sequence, so arrows quit."""

    def setUp(self):
        import os
        import pty
        import tty
        self.master, slave = pty.openpty()
        tty.setcbreak(slave)
        self._saved_stdin = sys.stdin
        sys.stdin = os.fdopen(slave, "r")
        reader.clear_pending_keys()

    def tearDown(self):
        import os
        sys.stdin.close()
        sys.stdin = self._saved_stdin
        os.close(self.master)

    def _send(self, raw):
        import os
        os.write(self.master, raw)
        return reader.read_key(2.0)

    def test_arrow_keys_arrive_whole_and_do_not_quit(self):
        self.assertEqual(reader.action_for(self._send(b"\x1b[C")), "advance")
        self.assertEqual(reader.action_for(self._send(b"\x1b[D")), "back")
        self.assertEqual(reader.action_for(self._send(b"\x1b[B")), "advance")
        self.assertEqual(reader.action_for(self._send(b"\x1b[A")), "back")

    def test_plain_keys_still_work(self):
        self.assertEqual(reader.action_for(self._send(b" ")), "advance")
        self.assertEqual(reader.action_for(self._send(b"q")), "quit")

    def test_rapid_plain_keys_are_returned_one_at_a_time(self):
        os.write(self.master, b"nn")
        self.assertEqual(reader.read_key(2.0), "n")
        self.assertEqual(reader.read_key(0), "n")

    def test_a_bare_escape_still_quits(self):
        self.assertEqual(reader.action_for(self._send(b"\x1b")), "quit")

    def test_timeout_returns_none_so_the_caller_can_recheck_state(self):
        self.assertIsNone(reader.read_key(0.05))


class FrameTest(unittest.TestCase):
    def test_wraps_to_the_given_width(self):
        line = "Call me Ishmael. " * 8
        for row in reader.frame(line, "Moby Dick", 1, 100, width=40):
            self.assertLessEqual(len(row), 40)

    def test_footer_carries_position_and_percent(self):
        rows = reader.frame("A line.", "Moby Dick", 25, 100, width=80)
        self.assertIn("Moby Dick", rows[-1])
        self.assertIn("25/100", rows[-1])
        self.assertIn("25.0%", rows[-1])

    def test_footer_shows_the_mode_when_given(self):
        rows = reader.frame("A line.", "Book", 1, 10, width=80, mode="manual")
        self.assertIn("manual", rows[-1])

    def test_empty_stream_says_so_rather_than_rendering_blank(self):
        rows = reader.frame("", None, 1, 0, width=40)
        self.assertIn("(nothing queued)", rows[0])

    def test_zero_total_does_not_divide_by_zero(self):
        rows = reader.frame("", None, 1, 0, width=40)
        self.assertIn("0.0%", rows[-1])

    def test_narrow_terminal_is_survivable(self):
        for row in reader.frame("Some prose here.", "A Very Long Book Title", 3, 9, width=10):
            self.assertLessEqual(len(row), 10)

    def test_context_marks_the_current_line_and_respects_height(self):
        context = [
            ("A remembered line.", False),
            ("The current line.", True),
            ("The next line.", False),
        ]
        rows = reader.frame(
            "The current line.", "Book", 2, 3, width=40, height=6,
            mode="250 wpm", context=context)
        self.assertLessEqual(len(rows), 6)
        self.assertIn("  A remembered line.", rows)
        self.assertIn("📖 The current line.", rows)
        self.assertIn("  The next line.", rows)

    def test_tiny_terminal_prioritizes_the_book_over_controls(self):
        rows = reader.frame(
            "Still readable.", "Book", 1, 1, width=20, height=2,
            context=[("Still readable.", True)])
        self.assertEqual(rows, ["📖 Still readable."])


class RunTest(unittest.TestCase):
    class FakeStdin:
        def isatty(self):
            return True

        def fileno(self):
            return 0

    def test_uses_the_alternate_screen_and_restores_it(self):
        output = io.StringIO()
        snapshot = ([("one", True)], "Book", 1, 1, "manual")
        with mock.patch.object(sys, "stdin", self.FakeStdin()), \
                mock.patch.object(sys, "stdout", output), \
                mock.patch("termios.tcgetattr", return_value=[]), \
                mock.patch("termios.tcsetattr"), \
                mock.patch("tty.setcbreak"), \
                mock.patch.object(reader, "read_key", return_value="q"):
            self.assertEqual(reader.run(lambda: snapshot, lambda: None, lambda: None), 0)
        self.assertIn(reader.ENTER_SCREEN, output.getvalue())
        self.assertTrue(output.getvalue().endswith(reader.EXIT_SCREEN))

    def test_resize_redraws_an_unchanged_snapshot(self):
        output = io.StringIO()
        snapshot = ([("one", True)], "Book", 1, 1, "manual")
        sizes = [os.terminal_size((80, 24)), os.terminal_size((40, 12))]
        with mock.patch.object(sys, "stdin", self.FakeStdin()), \
                mock.patch.object(sys, "stdout", output), \
                mock.patch("termios.tcgetattr", return_value=[]), \
                mock.patch("termios.tcsetattr"), \
                mock.patch("tty.setcbreak"), \
                mock.patch.object(reader, "read_key", side_effect=[None, "q"]), \
                mock.patch.object(reader.shutil, "get_terminal_size", side_effect=sizes):
            reader.run(lambda: snapshot, lambda: None, lambda: None)
        self.assertGreaterEqual(output.getvalue().count("\x1b[2J\x1b[H"), 2)


if __name__ == "__main__":
    unittest.main()
