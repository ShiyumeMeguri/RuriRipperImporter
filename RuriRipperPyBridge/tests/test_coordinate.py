"""The coordinate spaces, checked against the two hand-written conversions they
replaced.

This is the part of the extraction with the most room to be silently wrong -- a
sign error here mirrors a model or inverts every bitangent, and nothing crashes
-- so each space is compared element-for-element against the exact expression
the host used to run inline.
"""

from __future__ import annotations

import unittest

import numpy as np

from ..math3d import coordinate


class TestSpaceEquivalence(unittest.TestCase):
    """Element-for-element equality with the original inline implementations."""

    def setUp(self):
        rng = np.random.default_rng(20260725)
        self.points = rng.normal(size=(64, 3)).astype(np.float32)
        self.tangents = rng.normal(size=(64, 4)).astype(np.float32)
        self.tangents[:, 3] = np.where(self.tangents[:, 3] > 0, 1.0, -1.0)
        self.uvs = rng.random(size=(64, 2)).astype(np.float32)
        self.tris = rng.integers(0, 64, size=(32, 3)).astype(np.int32)
        self.matrix = rng.normal(size=(4, 4))

    def test_blender_points_are_the_yz_swap(self):
        # Original: positions[:, (0, 2, 1)].astype(np.float32, copy=True)
        expected = self.points[:, (0, 2, 1)].astype(np.float32, copy=True)
        np.testing.assert_array_equal(coordinate.BLENDER.convert_points(self.points), expected)

    def test_gltf_points_negate_x(self):
        # Original: out = points.astype(np.float32).copy(); out[:, 0] *= -1.0
        expected = self.points.astype(np.float32).copy()
        expected[:, 0] *= -1.0
        np.testing.assert_array_equal(coordinate.GLTF.convert_points(self.points), expected)

    def test_gltf_tangents_negate_x_and_keep_w(self):
        # Original: out[:, 0] *= -1.0, w untouched (the V flip cancels the
        # reflection's own sign change -- see Space.tangent_w_sign).
        expected = self.tangents.astype(np.float32).copy()
        expected[:, 0] *= -1.0
        np.testing.assert_array_equal(coordinate.GLTF.convert_tangents(self.tangents), expected)

    def test_blender_tangent_w_flips(self):
        # No V flip on the Blender side, so the reflection's sign change stands.
        self.assertEqual(coordinate.BLENDER.tangent_w_sign, -1.0)
        self.assertEqual(coordinate.GLTF.tangent_w_sign, 1.0)
        out = coordinate.BLENDER.convert_tangents(self.tangents)
        np.testing.assert_array_equal(out[:, 3], -self.tangents[:, 3])

    def test_convert_matrix_is_conjugation(self):
        for space in (coordinate.BLENDER, coordinate.GLTF):
            expected = space.matrix @ self.matrix @ space.matrix
            np.testing.assert_allclose(space.convert_matrix(self.matrix), expected)

    def test_uv_policy(self):
        np.testing.assert_array_equal(coordinate.BLENDER.convert_uvs(self.uvs), self.uvs)
        flipped = coordinate.GLTF.convert_uvs(self.uvs)
        np.testing.assert_array_equal(flipped[:, 0], self.uvs[:, 0])
        np.testing.assert_allclose(flipped[:, 1], 1.0 - self.uvs[:, 1])

    def test_winding_reverses_for_both(self):
        for space in (coordinate.BLENDER, coordinate.GLTF):
            self.assertTrue(space.reverses_winding)
            np.testing.assert_array_equal(space.reverse_winding(self.tris),
                                          self.tris[:, (0, 2, 1)])

    def test_convert_uvs_does_not_alias_the_input(self):
        original = self.uvs.copy()
        coordinate.GLTF.convert_uvs(self.uvs)
        coordinate.BLENDER.convert_uvs(self.uvs)
        np.testing.assert_array_equal(self.uvs, original)


class TestSpaceProperties(unittest.TestCase):
    """Properties that must hold for ANY space, present or future."""

    def test_reflection_is_its_own_inverse(self):
        rng = np.random.default_rng(7)
        matrix = rng.normal(size=(4, 4))
        for space in coordinate.SPACES.values():
            np.testing.assert_allclose(space.convert_matrix(space.convert_matrix(matrix)),
                                       matrix, atol=1e-12)

    def test_points_and_matrix_agree(self):
        """convert_points must move a point exactly where convert_matrix's own
        linear part would -- if these two ever disagree, geometry and node
        transforms end up in different spaces."""
        rng = np.random.default_rng(11)
        points = rng.normal(size=(16, 3)).astype(np.float32)
        for space in coordinate.SPACES.values():
            by_matrix = (points.astype(np.float64) @ space.matrix[:3, :3].T).astype(np.float32)
            np.testing.assert_allclose(space.convert_points(points), by_matrix, atol=1e-6)

    def test_determinant_is_minus_one(self):
        for space in coordinate.SPACES.values():
            self.assertAlmostEqual(float(np.linalg.det(space.matrix[:3, :3])), -1.0)

    def test_general_path_matches_permutation_fast_path(self):
        """The signed-permutation gather and the plain matrix product are two
        implementations of one thing; force the slow one and compare."""
        rng = np.random.default_rng(13)
        points = rng.normal(size=(32, 3)).astype(np.float32)
        for space in coordinate.SPACES.values():
            general = coordinate.Space(space.name + "-general",
                                       space.matrix[:3, :3] * 1.0, space.flip_v)
            general._perm, general._signs = None, None  # noqa: SLF001 - deliberate
            np.testing.assert_allclose(general.convert_points(points),
                                       space.convert_points(points), atol=1e-6)


class TestRootRotation(unittest.TestCase):
    """The once-only top-level yaw R, orthogonal to the reflection C."""

    def test_root_rotation_determinant_is_plus_one(self):
        # A rotation, never a reflection -- so winding and tangents are untouched
        # wherever it is applied.
        for space in coordinate.SPACES.values():
            self.assertAlmostEqual(float(np.linalg.det(space.root_rotation[:3, :3])), 1.0)

    def test_blender_is_a_180_yaw_and_gltf_is_identity(self):
        np.testing.assert_allclose(coordinate.BLENDER.root_rotation,
                                   np.diag([-1.0, -1.0, 1.0, 1.0]), atol=1e-12)
        np.testing.assert_allclose(coordinate.GLTF.root_rotation, np.eye(4), atol=1e-12)

    def test_convert_root_matrix_is_R_times_convert_matrix(self):
        rng = np.random.default_rng(20260804)
        matrix = rng.normal(size=(4, 4))
        for space in coordinate.SPACES.values():
            expected = space.root_rotation @ space.convert_matrix(matrix)
            np.testing.assert_allclose(space.convert_root_matrix(matrix), expected, atol=1e-12)

    def test_identity_input_yields_R(self):
        for space in coordinate.SPACES.values():
            np.testing.assert_allclose(space.convert_root_matrix(np.eye(4)),
                                       space.root_rotation, atol=1e-12)

    def test_root_yaw_does_not_leak_into_convert_matrix(self):
        # convert_matrix is still the pure conjugation C @ M @ C: the reflection
        # is untouched, so its involution below still holds.
        rng = np.random.default_rng(3)
        matrix = rng.normal(size=(4, 4))
        for space in coordinate.SPACES.values():
            np.testing.assert_allclose(space.convert_matrix(matrix),
                                       space.matrix @ matrix @ space.matrix, atol=1e-12)


class TestUnityTrs(unittest.TestCase):
    def test_identity(self):
        matrix = coordinate.unity_trs({"x": 0, "y": 0, "z": 0},
                                      {"x": 0, "y": 0, "z": 0, "w": 1},
                                      {"x": 1, "y": 1, "z": 1})
        np.testing.assert_allclose(matrix, np.eye(4), atol=1e-15)

    def test_translation_lands_in_the_last_column(self):
        matrix = coordinate.unity_trs({"x": 1.5, "y": -2.0, "z": 3.25},
                                      {"x": 0, "y": 0, "z": 0, "w": 1},
                                      {"x": 1, "y": 1, "z": 1})
        np.testing.assert_allclose(matrix[:3, 3], [1.5, -2.0, 3.25])

    def test_quarter_turn_about_y(self):
        s = 2.0 ** -0.5
        matrix = coordinate.unity_trs({"x": 0, "y": 0, "z": 0},
                                      {"x": 0.0, "y": s, "z": 0.0, "w": s},
                                      {"x": 1, "y": 1, "z": 1})
        # Unity is left-handed: +90 deg about Y takes +Z to +X.
        np.testing.assert_allclose(matrix[:3, :3] @ np.array([0.0, 0.0, 1.0]),
                                   [1.0, 0.0, 0.0], atol=1e-12)

    def test_scale_is_applied_before_rotation(self):
        matrix = coordinate.unity_trs({"x": 0, "y": 0, "z": 0},
                                      {"x": 0, "y": 0, "z": 0, "w": 1},
                                      {"x": 2.0, "y": 3.0, "z": 4.0})
        np.testing.assert_allclose(np.diag(matrix)[:3], [2.0, 3.0, 4.0])

    def test_unnormalised_quaternion_is_normalised(self):
        matrix = coordinate.unity_trs({"x": 0, "y": 0, "z": 0},
                                      {"x": 0.0, "y": 2.0, "z": 0.0, "w": 2.0},
                                      {"x": 1, "y": 1, "z": 1})
        np.testing.assert_allclose(np.linalg.det(matrix[:3, :3]), 1.0, atol=1e-12)

    def test_degenerate_quaternion_falls_back_to_identity(self):
        matrix = coordinate.unity_trs({"x": 0, "y": 0, "z": 0},
                                      {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0},
                                      {"x": 1, "y": 1, "z": 1})
        np.testing.assert_allclose(matrix, np.eye(4), atol=1e-15)

    def test_missing_components_default(self):
        matrix = coordinate.unity_trs({}, {}, {})
        np.testing.assert_allclose(matrix, np.eye(4), atol=1e-15)


if __name__ == "__main__":
    unittest.main()
