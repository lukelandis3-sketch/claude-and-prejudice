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

    def test_sync_does_not_publish_empty_generations_without_a_queue(self):
        self.run_cli("sync", "--quiet")
        self.run_cli("sync", "--quiet")
        self.assertFalse(os.path.exists(self.tbstate.path("stream.gen")))

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

    def test_sync_repair_restores_the_statusline_an_old_install_wrapped(self):
        import thinking_book
        old = 'sh "/tmp/old-thinking-book/scripts/statusline.sh"'
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
        os.unlink(self.tbstate.path("hot.env"))
        shutil.rmtree(self.tbstate.path("stream-generations"))
        with open(self.tbstate.settings_path(), "w") as fh:
            fh.write("{ broken")

        result = self.run_cli("sync", "--quiet")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertTrue(os.path.exists(self.tbstate.path("hot.env")))
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
