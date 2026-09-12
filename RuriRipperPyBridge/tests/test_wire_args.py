"""The spelling dataset arguments cross in.

Every argument goes over as text, so the two sides have to agree on how a value
is written. They already agree on finite numbers; the three words they do NOT
agree on are the ones this covers -- a whole-map window is stated as infinities,
and Python's ``inf`` is not a number to the kernel that reads it back.
"""

from __future__ import annotations

import unittest

from ..runtime.pythonnet_bridge import _wire


class TestWireSpelling(unittest.TestCase):

    def test_infinities_use_the_kernels_own_words(self):
        self.assertEqual(_wire(float("-inf")), "-Infinity")
        self.assertEqual(_wire(float("inf")), "Infinity")
        self.assertEqual(_wire(float("nan")), "NaN")

    def test_finite_numbers_round_trip_as_themselves(self):
        for value in (0.0, -0.5, 1.5, 1234.0, 1e20, -1e-7):
            self.assertEqual(_wire(value), str(value))

    def test_flags_cross_as_one_and_zero(self):
        self.assertEqual(_wire(True), "1")
        self.assertEqual(_wire(False), "0")

    def test_text_and_whole_numbers_are_left_alone(self):
        self.assertEqual(_wire("map01"), "map01")
        self.assertEqual(_wire(3), "3")
        self.assertEqual(_wire(-2), "-2")


if __name__ == "__main__":
    unittest.main()
