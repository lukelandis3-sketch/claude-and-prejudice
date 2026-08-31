"""The `book` launcher and its backwards-compatible `tb` alias."""

import os
import subprocess
import tempfile
import unittest

import support
from support import IsolatedStateCase


class EntryPointTest(IsolatedStateCase):
    def run_book(self, *args, **kwargs):
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self.config_dir
        env["CLAUDE_PLUGIN_ROOT"] = support.REPO
        env.update(kwargs.pop("env", {}))
        binary = kwargs.pop("binary", os.path.join(support.REPO, "bin", "book"))
        return subprocess.run([binary] + list(args), capture_output=True, text=True,
                              env=env, timeout=60, cwd=kwargs.pop("cwd", "/"))

    def test_is_executable(self):
        self.assertTrue(os.access(os.path.join(support.REPO, "bin", "book"), os.X_OK))

    def test_forwards_arguments_and_advances(self):
        self.seed_stream(["one", "two", "three"], mode="manual")
        result = self.run_book("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "two")
        self.assertEqual(self.tbstate.read_pos(), 2)

    def test_short_aliases_work(self):
        # Full words are primary, but short aliases remain useful for hotkeys.
        self.seed_stream(["one", "two", "three"], mode="manual")
        self.assertEqual(self.run_book("n").stdout.strip(), "two")
        self.assertEqual(self.run_book("n").stdout.strip(), "three")
        self.assertEqual(self.run_book("b").stdout.strip(), "two")

    def test_forwards_exit_codes(self):
        self.assertEqual(self.run_book("--nonsense").returncode, 2)
        self.assertEqual(self.run_book("load", "/nonexistent.epub").returncode, 1)

    def test_local_help_uses_the_local_book_command(self):
        result = self.run_book("help")
        self.assertIn("book <title|url|file>", result.stdout)
        self.assertNotIn("/thinking-book:book", result.stdout)

    def test_local_errors_never_recommend_plugin_only_commands(self):
        result = self.run_book("add")
        self.assertEqual(result.returncode, 1)
        self.assertIn("book add <title|url|file>", result.stderr)
        self.assertNotIn("/thinking-book", result.stderr)

    def test_local_manual_dashboard_uses_the_launcher_already_running(self):
        self.seed_stream(["one", "two"], mode="manual")
        result = self.run_book("status", env={"PATH": "/usr/bin:/bin"})
        self.assertIn("book next", result.stdout)
        self.assertIn("book back", result.stdout)
        self.assertNotIn("install-cli", result.stdout)

    def test_works_through_a_symlink_from_another_directory(self):
        # This is how it lands on PATH, so it must resolve its own location.
        link_dir = os.path.join(self.config_dir, "bin")
        os.makedirs(link_dir, exist_ok=True)
        link = os.path.join(link_dir, "book")
        os.symlink(os.path.join(support.REPO, "bin", "book"), link)

        result = self.run_book("version", binary=link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(support.REPO, result.stdout)

    def test_symlink_cycle_fails_quickly_instead_of_hanging(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "first")
            second = os.path.join(directory, "second")
            os.symlink("second", first)
            os.symlink("first", second)
            script = os.path.join(support.REPO, "bin", "book")
            # Source the script so the shell can supply a cyclic $0; executing the cycle
            # directly is rejected by the kernel before book can diagnose it.
            result = subprocess.run(
                ["sh", "-c", '. "$1"', first, script],
                capture_output=True, text=True, timeout=5,
                env=dict(os.environ, CLAUDE_CONFIG_DIR=self.config_dir),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())

    def test_legacy_tb_launcher_still_works(self):
        self.seed_stream(["one", "two"], mode="manual")
        result = self.run_book(
            "next", binary=os.path.join(support.REPO, "bin", "tb"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "two")


class InstallCliTest(IsolatedStateCase):
    def test_creates_a_symlink_and_is_idempotent(self):
        target = os.path.join(self.config_dir, "somebin")
        first = self.run_cli("install-cli", target)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Installed", first.stdout)
        link = os.path.join(target, "book")
        self.assertTrue(os.path.islink(link))

        second = self.run_cli("install-cli", target)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Already installed", second.stdout)

    def test_warns_when_the_directory_is_not_on_path(self):
        target = os.path.join(self.config_dir, "offpath")
        result = self.run_cli("install-cli", target)
        self.assertIn(os.path.join(target, "book") + " next", result.stdout)
        self.assertIn("add %s to PATH" % target, result.stdout)

    def test_says_how_to_use_it_when_the_directory_is_on_path(self):
        target = os.path.join(self.config_dir, "onpath")
        os.makedirs(target, exist_ok=True)
        result = self.run_cli("install-cli", target,
                              env={"PATH": target + os.pathsep + os.environ.get("PATH", "")})
        self.assertIn("another terminal", result.stdout)
        self.assertIn("book next", result.stdout)
        self.assertNotIn("tb n", result.stdout)
        self.assertNotIn("add %s to PATH" % target, result.stdout)

    def test_refuses_to_clobber_an_unrelated_file(self):
        target = os.path.join(self.config_dir, "occupied")
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "book"), "w") as fh:
            fh.write("someone else's book command")
        result = self.run_cli("install-cli", target)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)

    def test_repoints_a_stale_launcher_from_an_older_plugin_cache(self):
        target = os.path.join(self.config_dir, "bin")
        old = os.path.join(self.config_dir, "cache", "thinking-book", "0.8.2", "bin")
        os.makedirs(old)
        old_book = os.path.join(old, "book")
        with open(old_book, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.makedirs(target)
        link = os.path.join(target, "book")
        os.symlink(old_book, link)

        result = self.run_cli("install-cli", target)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Updated", result.stdout)
        self.assertEqual(
            os.path.realpath(link), os.path.realpath(os.path.join(support.REPO, "bin", "book")))

    def test_repoints_a_live_launcher_from_an_arbitrarily_named_plugin_root(self):
        target = os.path.join(self.config_dir, "bin")
        old_root = os.path.join(self.config_dir, "reader-plugin")
        os.makedirs(os.path.join(old_root, "bin"))
        os.makedirs(os.path.join(old_root, "scripts"))
        old_book = os.path.join(old_root, "bin", "book")
        with open(old_book, "w") as fh:
            fh.write("#!/bin/sh\n")
        with open(os.path.join(old_root, "scripts", "thinking_book.py"), "w") as fh:
            fh.write("# marker\n")
        os.makedirs(target)
        os.symlink(old_book, os.path.join(target, "book"))

        result = self.run_cli("install-cli", target)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Updated", result.stdout)

    def test_does_not_repoint_an_unrelated_live_bin_book_symlink(self):
        target = os.path.join(self.config_dir, "bin")
        unrelated = os.path.join(self.config_dir, "someone-else", "bin")
        os.makedirs(unrelated)
        old_book = os.path.join(unrelated, "book")
        with open(old_book, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.makedirs(target)
        link = os.path.join(target, "book")
        os.symlink(old_book, link)

        result = self.run_cli("install-cli", target)

        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(os.path.realpath(link), os.path.realpath(old_book))


if __name__ == "__main__":
    unittest.main()
