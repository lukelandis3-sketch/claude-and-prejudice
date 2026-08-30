"""Identity of our own status line, and the shapes settings.json can hold."""

import os
import unittest

import support  # noqa: F401  (path setup)
import thinking_book as tb


class IdentityTest(unittest.TestCase):
    def _fake_install(self, root):
        os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
        for name in ("statusline.sh", "thinking_book.py"):
            open(os.path.join(root, "scripts", name), "w").close()
        return 'sh "%s/scripts/statusline.sh"' % root

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_recognises_our_script_under_a_path_containing_spaces(self):
        # Regression: the token regex stopped at whitespace, so a macOS-style path with a
        # space failed the sibling check -- and the self-wrapping bug came back.
        command = self._fake_install(os.path.join(self._tmp.name, "My Projects", "reader"))
        self.assertTrue(tb.is_our_statusline(command))

    def test_recognises_our_script_without_spaces(self):
        command = self._fake_install(os.path.join(self._tmp.name, "plain", "reader"))
        self.assertTrue(tb.is_our_statusline(command))

    def test_leaves_a_third_party_status_line_alone(self):
        for command in ("my-own-prompt --fancy", "npx -y ccstatusline@latest", ""):
            self.assertFalse(tb.is_our_statusline(command), command)

    def test_non_string_wrapped_statusline_is_not_ours(self):
        for value in (42, [], True):
            self.assertFalse(tb.is_our_statusline(value), repr(value))

    def test_an_unrelated_statusline_sh_is_not_ours(self):
        root = os.path.join(self._tmp.name, "someone-else")
        os.makedirs(root)
        open(os.path.join(root, "statusline.sh"), "w").close()  # no thinking_book.py beside it
        self.assertFalse(tb.is_our_statusline('sh "%s/statusline.sh"' % root))

    def test_accepts_a_dict_or_a_plain_string(self):
        command = self._fake_install(os.path.join(self._tmp.name, "either", "reader"))
        self.assertTrue(tb.is_our_statusline({"type": "command", "command": command}))
        self.assertTrue(tb.is_our_statusline(command))

    def test_recognises_a_missing_script_from_the_public_repo_name(self):
        command = 'sh "/tmp/old claude-and-prejudice/scripts/statusline.sh"'
        self.assertTrue(tb.is_our_statusline(command))


class StatusLineEntryTest(unittest.TestCase):
    def test_a_string_becomes_a_command_entry(self):
        self.assertEqual(tb.as_statusline_entry("my-prompt"),
                         {"type": "command", "command": "my-prompt"})

    def test_a_dict_passes_through(self):
        entry = {"type": "command", "command": "x", "padding": 1}
        self.assertEqual(tb.as_statusline_entry(entry), entry)

    def test_anything_else_is_none(self):
        for value in (None, 42, [], True):
            self.assertIsNone(tb.as_statusline_entry(value), repr(value))


if __name__ == "__main__":
    unittest.main()
