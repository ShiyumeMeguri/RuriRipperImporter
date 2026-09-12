"""Bind-pose baking, and the cabmap browser's rule engine."""

from __future__ import annotations

import unittest
import zlib

import numpy as np

from ..session import cabmap_state
from ..unity import skinning


def _crc(path):
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


class TestResolveBonePaths(unittest.TestCase):
    """The closure that rebuilds Unity transform paths from leaf names + the
    CRC32 hashes a mesh carries, on a small hand-built tree."""

    # Root/Bip001 splits into a pelvis chain and a head.
    TREE = ("Root",
            "Root/Bip001",
            "Root/Bip001/Bip001_Pelvis",
            "Root/Bip001/Bip001_Pelvis/Bip001_Spine",
            "Root/Bip001/Bip001_Head")
    LEAVES = ("Bip001_Head", "Root", "Bip001", "Bip001_Spine",
              "Bip001_Pelvis", "UnusedLeaf")

    def test_full_tree_reconstructs_from_bare_leaves(self):
        hashes = [_crc(path) for path in self.TREE]
        resolved = skinning.resolve_bone_paths(hashes, self.LEAVES)
        self.assertEqual({resolved[_crc(p)] for p in self.TREE}, set(self.TREE))
        self.assertEqual(len(resolved), len(self.TREE))

    def test_parent_before_child_by_construction(self):
        # Every resolved path's own parent prefix is also resolved -- what lets
        # the armature builder set edit-bone parents in one pass.
        hashes = [_crc(path) for path in self.TREE]
        resolved = skinning.resolve_bone_paths(hashes, self.LEAVES)
        by_path = set(resolved.values())
        for path in by_path:
            parent = path.rsplit("/", 1)[0]
            if parent != path:
                self.assertIn(parent, by_path, "parent of %r missing" % path)

    def test_seed_path_bootstraps_when_root_is_not_a_leaf(self):
        # Drop "Root" from the leaves; a seed path supplies the missing parent.
        leaves = tuple(name for name in self.LEAVES if name != "Root")
        hashes = [_crc(path) for path in self.TREE]
        without_seed = skinning.resolve_bone_paths(hashes, leaves)
        self.assertNotIn(_crc("Root"), without_seed)
        with_seed = skinning.resolve_bone_paths(hashes, leaves, seed_paths=("Root",))
        self.assertEqual({with_seed[_crc(p)] for p in self.TREE}, set(self.TREE))

    def test_unresolvable_hash_is_absent_not_invented(self):
        hashes = [_crc("Root"), 0xDEADBEEF]
        resolved = skinning.resolve_bone_paths(hashes, self.LEAVES)
        self.assertEqual(resolved, {_crc("Root"): "Root"})

    def test_empty_inputs(self):
        self.assertEqual(skinning.resolve_bone_paths([], []), {})
        self.assertEqual(skinning.resolve_bone_paths([_crc("X")], []), {})


class _Decoded:
    """The subset of mesh_decoder.DecodedMesh that bake_bind_pose touches."""

    def __init__(self, positions, bind_poses, bone_indices, bone_weights,
                 normals=None, tangents=None):
        self.positions = positions
        self.bind_poses = bind_poses
        self.bone_indices = bone_indices
        self.bone_weights = bone_weights
        self.normals = normals
        self.tangents = tangents


def _translation(x, y, z):
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, 3] = (x, y, z)
    return matrix


class TestBakeBindPose(unittest.TestCase):
    def test_rigid_single_bone(self):
        """One bone translated by (10, 0, 0): every vertex moves with it, since
        boneWorld @ bindpose is exactly that translation."""
        decoded = _Decoded(
            positions=np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 0.0]], dtype=np.float32),
            bind_poses=np.tile(np.eye(4, dtype=np.float32), (1, 1, 1)),
            bone_indices=np.zeros((2, 1), dtype=np.int32),
            bone_weights=np.ones((2, 1), dtype=np.float32))
        self.assertTrue(skinning.bake_bind_pose(
            decoded, [{"fileID": 400}], {400: _translation(10.0, 0.0, 0.0)}))
        np.testing.assert_allclose(decoded.positions,
                                   [[11.0, 2.0, 3.0], [10.0, 0.0, 0.0]], atol=1e-6)

    def test_weights_blend_between_bones(self):
        decoded = _Decoded(
            positions=np.zeros((1, 3), dtype=np.float32),
            bind_poses=np.tile(np.eye(4, dtype=np.float32), (2, 1, 1)),
            bone_indices=np.array([[0, 1]], dtype=np.int32),
            bone_weights=np.array([[0.25, 0.75]], dtype=np.float32))
        skinning.bake_bind_pose(
            decoded, [{"fileID": 1}, {"fileID": 2}],
            {1: _translation(0.0, 0.0, 0.0), 2: _translation(4.0, 0.0, 0.0)})
        np.testing.assert_allclose(decoded.positions, [[3.0, 0.0, 0.0]], atol=1e-6)

    def test_normals_use_the_inverse_transpose(self):
        """A bone with non-uniform scale: the normal of a plane must stay
        perpendicular to it, which only the inverse transpose guarantees. A
        surface tangent along +X under scale (2, 1, 4) keeps its direction; the
        +Z normal must too, and would NOT under the plain linear part."""
        scale = np.diag([2.0, 1.0, 4.0, 1.0])
        decoded = _Decoded(
            positions=np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
            bind_poses=np.tile(np.eye(4, dtype=np.float32), (1, 1, 1)),
            bone_indices=np.zeros((1, 1), dtype=np.int32),
            bone_weights=np.ones((1, 1), dtype=np.float32),
            normals=np.array([[0.0, 0.0, 1.0]], dtype=np.float32))
        skinning.bake_bind_pose(decoded, [{"fileID": 1}], {1: scale})
        np.testing.assert_allclose(decoded.positions, [[2.0, 0.0, 0.0]], atol=1e-6)
        np.testing.assert_allclose(decoded.normals, [[0.0, 0.0, 1.0]], atol=1e-6)

    def test_tangent_handedness_survives(self):
        scale = np.diag([2.0, 1.0, 1.0, 1.0])
        decoded = _Decoded(
            positions=np.zeros((1, 3), dtype=np.float32),
            bind_poses=np.tile(np.eye(4, dtype=np.float32), (1, 1, 1)),
            bone_indices=np.zeros((1, 1), dtype=np.int32),
            bone_weights=np.ones((1, 1), dtype=np.float32),
            tangents=np.array([[1.0, 0.0, 0.0, -1.0]], dtype=np.float32))
        skinning.bake_bind_pose(decoded, [{"fileID": 1}], {1: scale})
        self.assertEqual(decoded.tangents.shape, (1, 4))
        self.assertEqual(float(decoded.tangents[0, 3]), -1.0)
        np.testing.assert_allclose(decoded.tangents[0, :3], [1.0, 0.0, 0.0], atol=1e-6)

    def test_unskinned_mesh_reports_false(self):
        decoded = _Decoded(positions=np.zeros((1, 3), dtype=np.float32),
                           bind_poses=None, bone_indices=None, bone_weights=None)
        self.assertFalse(skinning.bake_bind_pose(decoded, [], {}))

    def test_missing_bone_world_falls_back_to_identity(self):
        decoded = _Decoded(
            positions=np.array([[1.0, 1.0, 1.0]], dtype=np.float32),
            bind_poses=np.tile(np.eye(4, dtype=np.float32), (1, 1, 1)),
            bone_indices=np.zeros((1, 1), dtype=np.int32),
            bone_weights=np.ones((1, 1), dtype=np.float32))
        self.assertTrue(skinning.bake_bind_pose(decoded, [{"fileID": 999}], {}))
        np.testing.assert_allclose(decoded.positions, [[1.0, 1.0, 1.0]], atol=1e-6)


class TestQueryDispatch(unittest.TestCase):
    """Rule EVALUATION lives in the C# CabTableSearch engine (single implementation,
    shared with the WinForms browser); host-free coverage here is limited to the
    dispatch policy that stays in python."""

    def test_has_active_query(self):
        self.assertTrue(cabmap_state.has_active_query("chr", ()))
        self.assertFalse(cabmap_state.has_active_query("   ", ()))
        rule = cabmap_state.Rule("name", "contains", "x", "include", enabled=False)
        self.assertFalse(cabmap_state.has_active_query("", [rule]))
        rule.enabled = True
        self.assertTrue(cabmap_state.has_active_query("", [rule]))


if __name__ == "__main__":
    unittest.main()
