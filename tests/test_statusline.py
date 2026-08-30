"""The status line hot path.

Claude Code re-runs this once per assistant message with a 5s timeout, so it is tested
for speed as well as behaviour -- and above all for never failing loudly.
"""

import os
import time
import unittest

from support import STATUSLINE, IsolatedStateCase


class StatusLineTest(IsolatedStateCase):
    def _enable_hud(self):
        config = self.tbstate.load_config()
        config["hud"] = True
        self.tbstate.save_config(config)
        self.tbstate.rebuild_stream()

    def test_prints_the_current_line(self):
        self.seed_stream(["Call me Ishmael.", "Some years ago."])
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "Call me Ishmael.")

    def test_numeric_cursor_without_final_newline_is_still_read(self):
        self.seed_stream(["one", "two"], mode="manual")
        self.tbstate.atomic_write(self.tbstate.path("pos"), "2")
        self.assertEqual(self.run_statusline().stdout.strip(), "two")

    def test_reads_first_and_last_lines_across_shards(self):
        lines = ["line-%d" % n for n in range(1, 515)]
        self.seed_stream(lines, mode="manual")
        for position in (1, 256, 257, 512, 513, 514):
            with self.subTest(position=position):
                self.tbstate.write_pos(position)
                self.assertEqual(self.run_statusline().stdout.strip(), lines[position - 1])

    def test_missing_or_corrupt_generation_is_silent(self):
        self.seed_stream(["one"], mode="manual")
        for raw in ("missing-generation\n", "../../bad\n", "not valid !\n"):
            with self.subTest(raw=raw):
                self.tbstate.atomic_write(self.tbstate.path("stream.gen"), raw)
                result = self.run_statusline()
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

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

    def test_wpm_gives_longer_fragments_more_time(self):
        long_line = " ".join("word" for _ in range(20))
        self.seed_stream(["Heading", long_line, "done"], mode="timer", wpm=60)
        self.tbstate.write_last_advance(time.time() - 3)
        self.assertEqual(self.run_statusline().stdout.strip(), long_line)
        self.assertEqual(self.tbstate.read_pos(), 2)

        self.tbstate.write_last_advance(time.time() - 5)
        self.assertEqual(self.run_statusline().stdout.strip(), long_line)
        self.assertEqual(self.tbstate.read_pos(), 2)

    def test_default_wpm_applies_the_short_fragment_floor(self):
        long_line = "one two three four five six seven eight nine ten " \
                    "eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
        self.seed_stream(["one two three four five", long_line, "done"],
                         mode="timer", wpm=250)
        self.tbstate.write_last_advance(time.time() - 2)
        self.assertEqual(" ".join(self.run_statusline().stdout.split()), long_line)
        self.tbstate.write_last_advance(time.time() - 4)
        self.assertEqual(" ".join(self.run_statusline().stdout.split()), long_line)
        self.assertEqual(self.tbstate.read_pos(), 2)

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

    def test_long_passage_wraps_without_hiding_words(self):
        passage = (
            "Its extreme downtown is the battery, where that noble mole is washed by "
            "waves, and cooled by breezes, which a few hours previous were out of sight."
        )
        self.seed_stream([passage], mode="manual")

        rows = self.run_statusline(env={"COLUMNS": "80"}).stdout.splitlines()

        self.assertGreater(len(rows), 1)
        self.assertTrue(all(len(row) <= 72 for row in rows), rows)
        self.assertEqual(" ".join(row.strip() for row in rows), passage)

    def test_optional_hud_adds_precomputed_book_progress_above_the_line(self):
        self.seed_stream(["one", "two"], mode="manual")
        self._enable_hud()

        lines = self.run_statusline().stdout.strip().split("\n")
        self.assertEqual(lines, [
            "📖 Test Item · █████░░░░░ 1/2 (50%) · manual",
            "one",
        ])

    def test_hud_marks_paused_timer_mode(self):
        self.seed_stream(["one"], mode="timer", dwell=12, paused=True)
        self._enable_hud()
        first = self.run_statusline().stdout.splitlines()[0]
        self.assertIn("timer 12s", first)
        self.assertIn("paused", first)

    def test_hud_names_the_wpm_pace(self):
        self.seed_stream(["one readable line"], mode="timer", wpm=220, paused=True)
        self._enable_hud()
        self.assertIn("220 wpm", self.run_statusline().stdout.splitlines()[0])

    def test_hud_missing_from_an_old_generation_falls_back_to_the_book_line(self):
        self.seed_stream(["one"], mode="manual")
        self._enable_hud()
        os.unlink(os.path.join(self.tbstate.stream_generation_dir(), "0.hud"))
        self.assertEqual(self.run_statusline().stdout.strip(), "one")

    def test_hud_metadata_stays_aligned_across_shard_boundaries(self):
        lines = ["line-%d" % n for n in range(1, 259)]
        self.seed_stream(lines, mode="manual")
        self._enable_hud()
        for position in (256, 257):
            with self.subTest(position=position):
                self.tbstate.write_pos(position)
                output = self.run_statusline().stdout.splitlines()
                self.assertIn("%d/258" % position, output[0])
                self.assertEqual(output[1], "line-%d" % position)

    # ------------------------------------------------------------- wrapping

    def _wrap(self, command):
        self.tbstate.atomic_write(self.tbstate.path("wrapped.cmd"), command + "\n")

    def test_wrapped_status_line_is_rendered_alongside_the_book(self):
        self.seed_stream(["Call me Ishmael."], mode="manual")
        self._wrap("echo 'my own status line'")
        lines = self.run_statusline().stdout.strip().split("\n")
        self.assertEqual(lines, ["my own status line", "Call me Ishmael."])

    def test_hud_follows_a_wrapped_status_line_without_replacing_it(self):
        self.seed_stream(["one"], mode="manual")
        self._enable_hud()
        self._wrap("echo 'my own status line'")
        lines = self.run_statusline().stdout.strip().split("\n")
        self.assertEqual(lines[0], "my own status line")
        self.assertTrue(lines[1].startswith("📖 Test Item"))
        self.assertEqual(lines[2], "one")

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

    def test_large_stdin_and_nonreading_wrapper_stay_silent(self):
        self.seed_stream(["a line"], mode="manual")
        self._wrap("exit 0")
        result = self.run_statusline(stdin_json="x" * (1024 * 1024))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "a line")
        self.assertEqual(result.stderr, "")

    def test_a_wrapped_command_pointing_back_at_us_cannot_recurse(self):
        # Regression: `pane on` from two different plugin roots used to poison wrapped.cmd
        # with our own script, which then re-read the same file and invoked itself until
        # Claude Code's 5s timeout killed it -- printing the same line a dozen times.
        self.seed_stream(["CHAPTER 14.", "line two"], mode="manual")
        self._wrap('sh "%s"' % STATUSLINE)

        start = time.time()
        result = self.run_statusline()
        elapsed = time.time() - start

        self.assertEqual(result.returncode, 0)
        self.assertLess(elapsed, 5, "status line took %.1fs -- it is recursing" % elapsed)
        lines = [line for line in result.stdout.split("\n") if line.strip()]
        self.assertEqual(lines, ["CHAPTER 14."], "expected one line, got %r" % lines)

    def test_recursion_guard_is_inherited_by_wrapped_commands(self):
        self.seed_stream(["a line"], mode="manual")
        self._wrap("printenv TB_IN_STATUSLINE")
        output = self.run_statusline().stdout
        self.assertIn("1", output)

    # ---------------------------------------------------------- robustness

    def test_corrupt_state_prints_nothing_and_exits_zero(self):
        self.seed_stream(["one", "two"])
        self.tbstate.atomic_write(self.tbstate.path("pos"), "garbage\n")
        self.tbstate.atomic_write(self.tbstate.path("count"), "also garbage\n")
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_corrupt_zero_padded_wpm_never_breaks_the_status_line(self):
        self.seed_stream(["one", "two"], mode="timer", wpm=250)
        hot = self.tbstate.path("hot.env")
        with open(hot) as fh:
            contents = fh.read()
        self.tbstate.atomic_write(hot, contents.replace("TB_WPM='250'", "TB_WPM='00'"))
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), "one")

    def test_huge_corrupt_wpm_preserves_a_wrapped_status_line(self):
        self.seed_stream(["one"], mode="timer", wpm=250)
        self._wrap("echo 'my own status line'")
        hot = self.tbstate.path("hot.env")
        with open(hot) as fh:
            contents = fh.read()
        self.tbstate.atomic_write(
            hot, contents.replace("TB_WPM='250'", "TB_WPM='999999999999999999999'"))
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.splitlines(), ["my own status line", "one"])

    def test_huge_numeric_state_is_silent_and_nonfatal(self):
        self.seed_stream(["one"], mode="timer", wpm=None)
        huge = "9" * 10000 + "\n"
        for target in (
            self.tbstate.path("pos"),
            self.tbstate.path("last"),
            os.path.join(self.tbstate.stream_generation_dir(), "count"),
        ):
            self.tbstate.atomic_write(target, huge)
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_huge_generation_name_is_silent_and_nonfatal(self):
        self.seed_stream(["one"], mode="manual")
        self.tbstate.atomic_write(self.tbstate.path("stream.gen"), "a" * 10000 + "\n")
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_corrupt_dwell_uses_a_safe_default(self):
        self.seed_stream(["one", "two"], mode="timer", wpm=None)
        hot = self.tbstate.path("hot.env")
        with open(hot) as fh:
            contents = fh.read()
        self.tbstate.atomic_write(
            hot, contents.replace("TB_DWELL='8'", "TB_DWELL='999999999999999999999'"))
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_corrupt_zero_padded_shard_word_count_is_not_fatal(self):
        self.seed_stream(["one"], mode="timer", wpm=250)
        shard = os.path.join(self.tbstate.stream_generation_dir(), "0.txt")
        self.tbstate.atomic_write(shard, "099\tone\n")
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), "one")

    def test_no_state_at_all_exits_zero_and_is_silent(self):
        import shutil
        shutil.rmtree(self.tbstate.home())
        result = self.run_statusline()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_large_stdin_without_a_wrapper_is_drained_and_silent(self):
        self.seed_stream(["one"], statusline=False)
        result = self.run_statusline(stdin_json="x" * (1024 * 1024))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_numeric_state_reads_do_not_spawn_cat_or_tr(self):
        with open(STATUSLINE) as fh:
            source = fh.read()
        self.assertNotIn('cat "$TB_DIR/pos"', source)
        self.assertNotIn("tr -d", source)
        self.assertNotIn("for _word in $LINE", source)
        self.assertNotIn("cat >/dev/null", source)

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
