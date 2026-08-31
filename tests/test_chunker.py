import unittest

import support  # noqa: F401  (path setup)
import chunker


class ChunkerTest(unittest.TestCase):
    def test_splits_sentences_and_keeps_them_whole(self):
        text = "Call me Ishmael. It was a dark night. The sea was calm."
        self.assertEqual(
            chunker.to_fragments(text),
            ["Call me Ishmael.", "It was a dark night.", "The sea was calm."],
        )

    def test_abbreviations_do_not_end_a_sentence(self):
        fragments = chunker.to_fragments("Dr. Smith went home. Mrs. Dalloway said she would buy the flowers.")
        self.assertEqual(len(fragments), 2)
        self.assertTrue(fragments[0].startswith("Dr. Smith"))
        self.assertTrue(fragments[1].startswith("Mrs. Dalloway"))

    def test_never_emits_empty_or_punctuation_only_fragments(self):
        # An empty verbs array makes Claude Code silently fall back to stock gerunds.
        for raw in ["", "   ", "...", "!!!", "\n\n\n", "123 456", "-- -- --"]:
            for fragment in chunker.to_fragments(raw):
                self.assertTrue(fragment.strip(), "empty fragment from %r" % raw)
                self.assertTrue(any(c.isalpha() for c in fragment))

    def test_long_sentence_is_split_at_clause_boundaries(self):
        long_sentence = (
            "It was the best of times, and it was the worst of times; it was the age of "
            "wisdom, and it was the age of foolishness, which was the epoch of belief, "
            "and it was the season of Light, and it was the spring of hope."
        )
        fragments = chunker.to_fragments(long_sentence, hard_max=80)
        self.assertGreater(len(fragments), 1)
        for fragment in fragments:
            self.assertLessEqual(len(fragment), 80)

    def test_default_fragments_fit_the_status_line(self):
        passage = (
            "Its extreme downtown is the battery, where that noble mole is washed by "
            "waves, and cooled by breezes, which a few hours previous were out of sight."
        )
        fragments = chunker.to_fragments(passage)

        self.assertGreater(len(fragments), 1)
        self.assertTrue(all(len(fragment) <= 100 for fragment in fragments), fragments)
        self.assertEqual(" ".join(fragments), passage)

    def test_single_word_longer_than_max_is_still_emitted(self):
        fragments = chunker.to_fragments("a" * 200 + " tail.", hard_max=50)
        self.assertTrue(fragments)
        for fragment in fragments:
            self.assertLessEqual(len(fragment), 50)

    def test_control_characters_and_ansi_are_stripped(self):
        raw = "Hello \x1b[31mred\x1b[0m world\x07 again."
        fragment = chunker.to_fragments(raw)[0]
        self.assertNotIn("\x1b", fragment)
        self.assertNotIn("\x07", fragment)

    def test_remote_prose_cannot_emit_c1_osc_or_bidi_terminal_controls(self):
        raw = (
            "Safe \x9b31mred\x9b0m "
            "\x1b]8;;https://invalid.example\x07link\x1b]8;;\x07 "
            "invoice\u061c\u200e\u200f\u202egpj.exe."
        )
        fragment = chunker.to_fragments(raw)[0]
        self.assertEqual(fragment, "Safe red link invoicegpj.exe.")
        self.assertFalse(any(
            ord(char) < 32 or 0x7f <= ord(char) <= 0x9f
            or char in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
            for char in fragment
        ))

    def test_paragraphs_never_merge_into_one_fragment(self):
        # Regression: collapsing blank lines ran unrelated blocks together, producing
        # fragments like "By Herman Melville CONTENTS ETYMOLOGY."
        fragments = chunker.to_fragments("MOBY-DICK.\n\nBy Herman Melville\n\nCONTENTS")
        self.assertEqual(fragments, ["MOBY-DICK.", "By Herman Melville", "CONTENTS"])

    def test_single_newline_is_a_soft_wrap_not_a_boundary(self):
        fragments = chunker.to_fragments("Call me Ishmael. Some years ago\nI went to sea.")
        self.assertEqual(fragments, ["Call me Ishmael.", "Some years ago I went to sea."])

    def test_numbered_headings_keep_their_titles(self):
        # Regression: "CHAPTER 14. Nantucket." split into two useless fragments.
        for raw, expected in [
            ("CHAPTER 14. Nantucket.", "CHAPTER 14. Nantucket."),
            ("PART II. The Return.", "PART II. The Return."),
            ("BOOK 3. Aftermath.", "BOOK 3. Aftermath."),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(chunker.to_fragments(raw), [expected])

    def test_heading_rejoin_does_not_swallow_following_prose(self):
        fragments = chunker.to_fragments("CHAPTER 1. Loomings.\n\nCall me Ishmael.")
        self.assertEqual(fragments, ["CHAPTER 1. Loomings.", "Call me Ishmael."])

    def test_fragments_never_contain_newlines(self):
        # The stream file is one fragment per line; a newline would corrupt the index.
        fragments = chunker.to_fragments("First line.\nSecond line.\n\nThird line.")
        for fragment in fragments:
            self.assertNotIn("\n", fragment)


if __name__ == "__main__":
    unittest.main()
