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
        self.assertIn("not on your PATH", result.stdout)

    def test_says_how_to_use_it_when_the_directory_is_on_path(self):
        target = os.path.join(self.config_dir, "onpath")
        os.makedirs(target, exist_ok=True)
        result = self.run_cli("install-cli", target,
                              env={"PATH": target + os.pathsep + os.environ.get("PATH", "")})
        self.assertIn("another terminal", result.stdout)
        self.assertIn("book next", result.stdout)
        self.assertNotIn("tb n", result.stdout)
        self.assertNotIn("not on your PATH", result.stdout)

    def test_refuses_to_clobber_an_unrelated_file(self):
        target = os.path.join(self.config_dir, "occupied")
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "book"), "w") as fh:
            fh.write("someone else's book command")
        result = self.run_cli("install-cli", target)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)


if __name__ == "__main__":
    unittest.main()
