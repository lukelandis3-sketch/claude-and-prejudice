import os
import unittest

from support import IsolatedStateCase


class StreamTest(IsolatedStateCase):
    def test_rebuild_concatenates_items_in_queue_order(self):
        self.tbstate.save_item("a", {"title": "Book A", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Book B", "kind": "article"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.assertEqual(self.tbstate.rebuild_stream(), 3)
        self.assertEqual(self.tbstate.stream_line(1), "a1")
        self.assertEqual(self.tbstate.stream_line(3), "b1")

    def test_count_cache_matches_stream(self):
        self.seed_stream(["one", "two", "three"])
        self.assertEqual(self.tbstate.stream_count(), 3)
        with open(self.tbstate.path("count")) as fh:
            self.assertEqual(fh.read().strip(), "3")

    def test_stream_line_out_of_range_is_empty(self):
        self.seed_stream(["only"])
        self.assertEqual(self.tbstate.stream_line(0), "")
        self.assertEqual(self.tbstate.stream_line(99), "")

    def test_item_at_maps_position_to_its_item(self):
        self.tbstate.save_item("a", {"title": "Book A", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "Book B", "kind": "article"}, ["b1"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.assertEqual(self.tbstate.item_at(1)[1], "a")
        self.assertEqual(self.tbstate.item_at(2)[1], "a")
        self.assertEqual(self.tbstate.item_at(3)[1], "b")

    def test_empty_items_are_skipped_without_breaking_the_index(self):
        self.tbstate.save_item("empty", {"title": "Nothing", "kind": "book"}, [])
        self.tbstate.save_item("real", {"title": "Something", "kind": "book"}, ["x1"])
        self.tbstate.save_queue({"items": ["empty", "real"]})
        self.assertEqual(self.tbstate.rebuild_stream(), 1)
        self.assertEqual(self.tbstate.item_at(1)[1], "real")

    def test_metadata_newlines_cannot_corrupt_the_stream_index(self):
        self.tbstate.save_item("a", {"title": "Bad\nTitle", "kind": "book"}, ["a1"])
        self.tbstate.save_queue({"items": ["a"]})
        self.tbstate.rebuild_stream()
        self.assertEqual(self.tbstate.item_at(1)[3], "Bad Title")

    def test_corrupt_generation_cannot_traverse_outside_stream_directory(self):
        outside = os.path.join(self.config_dir, "outside")
        os.makedirs(outside)
        os.makedirs(self.tbstate.path("stream-generations"))
        self.tbstate.atomic_write(os.path.join(outside, "0.txt"), "not book data\n")
        self.tbstate.atomic_write(self.tbstate.path("stream.gen"), "../../outside\n")
        self.assertEqual(self.tbstate.stream_line(1), "")

    def test_stream_generation_handles_shard_boundaries(self):
        lines = ["line-%d" % n for n in range(1, 515)]
        self.seed_stream(lines, mode="manual")
        self.assertTrue(self.tbstate.stream_generation())
        for position in (1, 256, 257, 512, 513, 514):
            with self.subTest(position=position):
                self.assertEqual(self.tbstate.stream_line(position), lines[position - 1])

    def test_locate_and_resolve_use_item_relative_offsets(self):
        self.tbstate.save_item("a", {"title": "A", "kind": "book"}, ["a1", "a2"])
        self.tbstate.save_item("b", {"title": "B", "kind": "book"}, ["b1", "b2", "b3"])
        self.tbstate.save_queue({"items": ["a", "b"]})
        self.tbstate.rebuild_stream()
        self.assertEqual(self.tbstate.locate_position(4), ("b", 2))
        self.assertEqual(self.tbstate.resolve_position("b", 2), 4)
        self.assertEqual(self.tbstate.resolve_position("a", 99), 2)


class ConfigTest(IsolatedStateCase):
    def test_defaults_applied_to_partial_config(self):
        self.tbstate.write_json(self.tbstate.path("config.json"), {"mode": "manual"})
        config = self.tbstate.load_config()
        self.assertEqual(config["mode"], "manual")
        self.assertEqual(config["dwell_seconds"], 8)
        self.assertTrue(config["surfaces"]["spinner"])

    def test_invalid_values_fall_back_to_defaults(self):
        self.tbstate.write_json(
            self.tbstate.path("config.json"), {"mode": "nonsense", "dwell_seconds": "abc"}
        )
        config = self.tbstate.load_config()
        self.assertEqual(config["mode"], "timer")
        self.assertEqual(config["dwell_seconds"], 8)

    def test_corrupt_config_does_not_raise(self):
        self.tbstate.atomic_write(self.tbstate.path("config.json"), "{ not json")
        self.assertEqual(self.tbstate.load_config()["mode"], "timer")

    def test_valid_non_object_config_falls_back_to_defaults(self):
        self.tbstate.write_json(self.tbstate.path("config.json"), ["not", "an", "object"])
        self.assertEqual(self.tbstate.load_config(), self.tbstate.DEFAULT_CONFIG)

    def test_non_mapping_surfaces_falls_back_to_defaults(self):
        for bad in (["statusline"], "on", 3):
            with self.subTest(bad=bad):
                self.tbstate.write_json(self.tbstate.path("config.json"), {"surfaces": bad})
                self.assertEqual(
                    self.tbstate.load_config()["surfaces"],
                    {"statusline": True, "spinner": True},
                )

    def test_hot_env_quotes_awkward_values(self):
        config = self.tbstate.load_config()
        config["prefix"] = "it's $(rm -rf /) "
        self.tbstate.save_config(config)
        with open(self.tbstate.path("hot.env")) as fh:
            contents = fh.read()
        self.assertIn("TB_PREFIX='it'\\''s $(rm -rf /) '", contents)


class PositionTest(IsolatedStateCase):
    def test_position_defaults_to_one(self):
        self.assertEqual(self.tbstate.read_pos(), 1)

    def test_corrupt_position_file_reads_as_one(self):
        self.tbstate.atomic_write(self.tbstate.path("pos"), "not-a-number\n")
        self.assertEqual(self.tbstate.read_pos(), 1)

    def test_position_never_goes_below_one(self):
        self.tbstate.write_pos(-5)
        self.assertEqual(self.tbstate.read_pos(), 1)


if __name__ == "__main__":
    unittest.main()
