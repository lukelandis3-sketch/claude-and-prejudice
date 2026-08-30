"""bin/tb -- the short entry point that makes `!tb n` and hotkeys practical."""

import os
import subprocess
import unittest

import support
from support import IsolatedStateCase


class EntryPointTest(IsolatedStateCase):
    def run_tb(self, *args, **kwargs):
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self.config_dir
        env["CLAUDE_PLUGIN_ROOT"] = support.REPO
        env.update(kwargs.pop("env", {}))
        binary = kwargs.pop("binary", os.path.join(support.REPO, "bin", "tb"))
        return subprocess.run([binary] + list(args), capture_output=True, text=True,
                              env=env, timeout=60, cwd=kwargs.pop("cwd", "/"))

    def test_is_executable(self):
        self.assertTrue(os.access(os.path.join(support.REPO, "bin", "tb"), os.X_OK))

    def test_forwards_arguments_and_advances(self):
        self.seed_stream(["one", "two", "three"], mode="manual")
        result = self.run_tb("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "two")
        self.assertEqual(self.tbstate.read_pos(), 2)

    def test_short_aliases_work(self):
        # The README promises `tb n` / `!tb n`; brevity is the entire point.
        self.seed_stream(["one", "two", "three"], mode="manual")
        self.assertEqual(self.run_tb("n").stdout.strip(), "two")
        self.assertEqual(self.run_tb("n").stdout.strip(), "three")
        self.assertEqual(self.run_tb("b").stdout.strip(), "two")

    def test_forwards_exit_codes(self):
        self.assertEqual(self.run_tb("nonsense").returncode, 2)
        self.assertEqual(self.run_tb("load", "/nonexistent.epub").returncode, 1)

    def test_works_through_a_symlink_from_another_directory(self):
        # This is how it lands on PATH, so it must resolve its own location.
        link_dir = os.path.join(self.config_dir, "bin")
        os.makedirs(link_dir, exist_ok=True)
        link = os.path.join(link_dir, "tb")
        os.symlink(os.path.join(support.REPO, "bin", "tb"), link)

        result = self.run_tb("version", binary=link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(support.REPO, result.stdout)


class InstallCliTest(IsolatedStateCase):
    def test_creates_a_symlink_and_is_idempotent(self):
        target = os.path.join(self.config_dir, "somebin")
        first = self.run_cli("install-cli", target)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Installed", first.stdout)
        link = os.path.join(target, "tb")
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
        self.assertIn("!tb n", result.stdout)
        self.assertNotIn("not on your PATH", result.stdout)

    def test_refuses_to_clobber_an_unrelated_file(self):
        target = os.path.join(self.config_dir, "occupied")
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "tb"), "w") as fh:
            fh.write("someone else's tb")
        result = self.run_cli("install-cli", target)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)


if __name__ == "__main__":
    unittest.main()
