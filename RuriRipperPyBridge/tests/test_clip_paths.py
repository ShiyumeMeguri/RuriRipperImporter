"""Re-anchoring animation curve bindings onto a skeleton."""

from __future__ import annotations

import unittest
import zlib

import numpy as np

from ..unity import clip_paths
from ..unity.clip_curves import Channel, ClipCurves

PATH_TO_BONE = {
    "chr_0013_postmodel/Root": "Root",
    "chr_0013_postmodel/Root/Bip001": "Bip001",
    "chr_0013_postmodel/Root/Bip001/Bip001 Spine": "Bip001 Spine",
}


def _crc(text):
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def _channel(path, dimensions, attribute=""):
    return Channel(path,
                   np.empty(0, dtype=np.float64),
                   np.zeros((0, dimensions), dtype=np.float64),
                   np.zeros((0, dimensions), dtype=np.float64),
                   np.zeros((0, dimensions), dtype=np.float64),
                   attribute=attribute)


def _clip(rotations=None, positions=None, scales=None, eulers=None, floats=None):
    clip = ClipCurves()
    clip.rotations = rotations or []
    clip.positions = positions or []
    clip.scales = scales or []
    clip.eulers = eulers or []
    clip.floats = floats or []
    return clip


class TestSuffixCrcTable(unittest.TestCase):
    def setUp(self):
        self.table = clip_paths.build_suffix_crc_table(PATH_TO_BONE)

    def test_every_suffix_resolves_to_its_full_path(self):
        self.assertEqual(self.table[_crc("Root/Bip001")], "chr_0013_postmodel/Root/Bip001")
        self.assertEqual(self.table[_crc("Bip001")], "chr_0013_postmodel/Root/Bip001")
        self.assertEqual(self.table[_crc("chr_0013_postmodel/Root")],
                         "chr_0013_postmodel/Root")

    def test_known_unity_hash(self):
        """Ground truth: crc32(b"Root") is what the game's own
        path_0xB6C65665_* placeholder carries."""
        self.assertEqual(_crc("Root"), 0xB6C65665)

    def test_longest_suffix_wins_a_collision(self):
        table = clip_paths.build_suffix_crc_table({"a/b": "b", "b": "b"})
        self.assertEqual(table[_crc("b")], "a/b")


class TestEntryCrc(unittest.TestCase):
    def test_hashed_placeholder_is_read_literally(self):
        self.assertEqual(clip_paths.entry_crc("path_0xB6C65665_WvpMuNH"), 0xB6C65665)

    def test_plain_path_hashes(self):
        self.assertEqual(clip_paths.entry_crc("Root/Bip001"), _crc("Root/Bip001"))


class TestRepair(unittest.TestCase):
    def test_hashed_paths_are_restored(self):
        clip = _clip(rotations=[
            _channel("path_0x{0:X}_wrap".format(_crc("Root/Bip001")), 4),
            _channel("path_0xDEADBEEF_ghost", 4),
        ])
        repaired, unmatched = clip_paths.repair_hashed_clip_paths(clip, PATH_TO_BONE)
        self.assertEqual((repaired, unmatched), (1, 1))
        self.assertEqual(clip.rotations[0].path, "chr_0013_postmodel/Root/Bip001")

    def test_a_differently_nested_string_path_is_re_anchored(self):
        clip = _clip(rotations=[_channel("Root/Bip001", 4)])
        repaired, unmatched = clip_paths.repair_hashed_clip_paths(clip, PATH_TO_BONE)
        self.assertEqual((repaired, unmatched), (1, 0))
        self.assertEqual(clip.rotations[0].path, "chr_0013_postmodel/Root/Bip001")

    def test_already_matching_paths_are_untouched(self):
        clip = _clip(rotations=[_channel("chr_0013_postmodel/Root", 4)])
        repaired, unmatched = clip_paths.repair_hashed_clip_paths(clip, PATH_TO_BONE)
        self.assertEqual((repaired, unmatched), (0, 0))


class TestMatchRatio(unittest.TestCase):
    def test_ratio_counts_transform_curves_only(self):
        clip = _clip(
            rotations=[_channel("Root/Bip001", 4), _channel("Nope/Missing", 4)],
            floats=[_channel("whatever", 1, attribute="Spine Front-Back")])
        ratio, total = clip_paths.clip_path_match_ratio(clip, PATH_TO_BONE)
        self.assertEqual(total, 2)
        self.assertAlmostEqual(ratio, 0.5)

    def test_no_transform_curves(self):
        self.assertEqual(clip_paths.clip_path_match_ratio(_clip(), PATH_TO_BONE), (0.0, 0))


if __name__ == "__main__":
    unittest.main()