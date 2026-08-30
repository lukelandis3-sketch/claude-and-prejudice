"""The hook-facing subcommands: advance policy, and never blowing up a turn."""

import json
import os
import time
import unittest

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

    def test_sync_writes_hot_env_for_the_shell(self):
        self.seed_stream(["A line."])
        os.unlink(self.tbstate.path("hot.env"))
        self.run_cli("sync")
        self.assertTrue(os.path.exists(self.tbstate.path("hot.env")))

    def test_sync_rebuilds_when_generation_directory_is_gone(self):
        import shutil
        self.seed_stream(["one", "two"], mode="manual")
        shutil.rmtree(self.tbstate.path("stream-generations"))
        self.run_cli("sync", "--quiet")
        self.assertEqual(self.tbstate.stream_line(1), "one")
        self.assertEqual(self.run_statusline().stdout.strip(), "one")

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

    def test_sync_clears_the_previous_sessions_statusline_liveness(self):
        self.seed_stream(["one"])
        self.tbstate.atomic_write(self.tbstate.path("statusline.live.global"), "")
        self.run_cli("sync", "--quiet")
        self.assertFalse(os.path.exists(self.tbstate.path("statusline.live.global")))

    def test_statusline_liveness_is_scoped_by_session_id_when_available(self):
        self.seed_stream(["one", "two"], mode="timer", dwell=600, statusline=True)
        self.tbstate.write_last_advance(time.time())
        self.run_statusline(env={"CLAUDE_CODE_SESSION_ID": "session-a"})
        self.assertTrue(os.path.exists(self.tbstate.path("statusline.live.session-a")))
        self.tbstate.write_last_advance(time.time() - 1000)
        self.run_cli("advance", "--quiet", env={"CLAUDE_CODE_SESSION_ID": "session-b"})
        self.assertEqual(self.pos(), 2)

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
        self.assertIn("thinking-book", result.stdout)
        self.assertIn("setup", result.stdout)
        self.assertNotIn("unknown command", result.stderr)

    def test_setup_command_uses_native_questions_and_only_the_safe_cli(self):
        path = os.path.join(support.REPO, "commands", "setup.md")
        with open(path) as fh:
            source = fh.read()
        self.assertIn("allowed-tools: AskUserQuestion, Bash(python3:*)", source)
        self.assertIn("thinking_book.py", source)
        self.assertIn("version output above", source)
        self.assertIn("spinner only", source)
        self.assertNotIn("settings.json", source)

    def test_help_command_prints_task_oriented_help(self):
        result = self.run_cli("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Read something now", result.stdout)

    def test_manual_hook_does_not_rewrite_an_unchanged_spinner(self):
        self.seed_stream(["one"], mode="manual")
        self.run_cli("sync", "--quiet")
        before = os.stat(self.tbstate.settings_path()).st_ino
        self.run_cli("advance", "--quiet")
        self.assertEqual(os.stat(self.tbstate.settings_path()).st_ino, before)

    def test_unknown_command_is_an_error_not_a_crash(self):
        result = self.run_cli("nonsense")
        self.assertEqual(result.returncode, 2)

    def test_unknown_command_names_the_version_and_path_it_ran_from(self):
        # A directory-source install goes stale silently: `unknown command 'repair'` gave
        # no hint that the fix was a git pull.
        result = self.run_cli("repair-typo")
        self.assertEqual(result.returncode, 2)
        self.assertIn("thinking-book", result.stderr)
        self.assertIn(support.REPO, result.stderr)
        self.assertIn("git pull", result.stderr)
        self.assertIn("repair", result.stderr)  # the real command is listed

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
