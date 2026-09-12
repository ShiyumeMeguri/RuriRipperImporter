"""The engine's own primitives, and the routing that reaches them.

These exist because a reference to `unity default resources` can NEVER resolve
out of a game's data -- so a regression here is not a wrong mesh, it is a
renderer that silently disappears from a scene.
"""

import unittest

import numpy as np

from ..unity import builtin_meshes, prefab as prefab_scan


class BuiltinRouting(unittest.TestCase):
    def test_recognises_the_engine_resources_guid(self):
        self.assertEqual(
            builtin_meshes.is_builtin({"fileID": 10202, "guid": builtin_meshes.BUILTIN_RESOURCES_GUID}),
            "Cube")
        self.assertEqual(
            builtin_meshes.is_builtin({"fileID": 10210, "guid": builtin_meshes.BUILTIN_RESOURCES_GUID.upper()}),
            "Quad")

    def test_ignores_ordinary_references(self):
        self.assertIsNone(builtin_meshes.is_builtin({"fileID": 4300000, "guid": "a" * 32}))
        self.assertIsNone(builtin_meshes.is_builtin(None))
        self.assertIsNone(builtin_meshes.is_builtin({"guid": builtin_meshes.BUILTIN_RESOURCES_GUID}))

    def test_unknown_file_id_in_the_engine_file_is_not_invented(self):
        self.assertIsNone(builtin_meshes.is_builtin(
            {"fileID": 12345, "guid": builtin_meshes.BUILTIN_RESOURCES_GUID}))

    def test_load_mesh_builds_it_without_any_database(self):
        """The whole point: no closure holds these, so resolution must not need one."""
        loaded = prefab_scan.load_mesh(
            None, {"fileID": 10210, "guid": builtin_meshes.BUILTIN_RESOURCES_GUID}, "Screen")
        self.assertTrue(loaded.ok)
        self.assertEqual(loaded.name, "Quad")
        self.assertEqual(loaded.builtin, "Quad (exact)")
        self.assertEqual(len(loaded.decoded.positions), 4)

    def test_a_reconstruction_says_so(self):
        loaded = prefab_scan.load_mesh(
            None, {"fileID": 10207, "guid": builtin_meshes.BUILTIN_RESOURCES_GUID}, "Sphere")
        self.assertTrue(loaded.ok)
        self.assertEqual(loaded.builtin, "Sphere (reconstructed)")


class BuiltinGeometry(unittest.TestCase):
    def _mesh(self, name):
        mesh = builtin_meshes.build(name)
        self.assertIsNotNone(mesh, name)
        return mesh

    def test_every_primitive_is_well_formed(self):
        for name in builtin_meshes.PRIMITIVES.values():
            with self.subTest(name):
                mesh = self._mesh(name)
                count = len(mesh.positions)
                self.assertGreater(count, 0)
                self.assertEqual(len(mesh.normals), count)
                self.assertEqual(len(mesh.uvs[0]), count)
                self.assertGreater(len(mesh.triangles), 0)
                # Every index addresses a vertex this mesh actually has -- an
                # off-by-one in a generator shows up here and nowhere else.
                self.assertTrue((mesh.triangles >= 0).all())
                self.assertLess(int(mesh.triangles.max()), count)
                self.assertEqual(len(mesh.tri_material), len(mesh.triangles))
                self.assertEqual(mesh.submeshes[0].index_count, mesh.triangles.size)
                # A normal that is not unit length is a normal nothing can shade with.
                lengths = np.linalg.norm(mesh.normals, axis=1)
                np.testing.assert_allclose(lengths, 1.0, atol=1e-5)

    def test_cube_is_the_engine_shape(self):
        """24 vertices, not 8: each face carries its own normal and its own UV."""
        mesh = self._mesh("Cube")
        self.assertEqual(len(mesh.positions), 24)
        self.assertEqual(len(mesh.triangles), 12)
        np.testing.assert_allclose(mesh.positions.min(axis=0), [-0.5, -0.5, -0.5])
        np.testing.assert_allclose(mesh.positions.max(axis=0), [0.5, 0.5, 0.5])
        # Six distinct face normals, each used by four vertices.
        unique = {tuple(np.round(row, 5)) for row in mesh.normals}
        self.assertEqual(len(unique), 6)

    def test_quad_is_the_engine_shape(self):
        mesh = self._mesh("Quad")
        self.assertEqual(len(mesh.positions), 4)
        self.assertEqual(len(mesh.triangles), 2)
        np.testing.assert_allclose(mesh.positions[:, 2], 0.0)
        np.testing.assert_allclose(mesh.normals, [[0, 0, -1]] * 4)
        np.testing.assert_allclose(sorted(mesh.uvs[0].flatten().tolist()), [0, 0, 0, 0, 1, 1, 1, 1])

    def test_plane_is_the_engine_shape(self):
        """10x10 in XZ, an 11x11 grid -- 121 vertices and 200 triangles."""
        mesh = self._mesh("Plane")
        self.assertEqual(len(mesh.positions), 121)
        self.assertEqual(len(mesh.triangles), 200)
        np.testing.assert_allclose(mesh.positions.min(axis=0), [-5.0, 0.0, -5.0])
        np.testing.assert_allclose(mesh.positions.max(axis=0), [5.0, 0.0, 5.0])
        np.testing.assert_allclose(mesh.normals, [[0, 1, 0]] * 121)

    def test_round_shapes_have_the_engine_dimensions(self):
        """Their tessellation is this module's, but their SIZE is the engine's --
        a primitive placed at scale 1 has to be the size the game gave it."""
        sphere = self._mesh("Sphere")
        np.testing.assert_allclose(sphere.positions.min(axis=0), [-0.5, -0.5, -0.5], atol=1e-6)
        np.testing.assert_allclose(sphere.positions.max(axis=0), [0.5, 0.5, 0.5], atol=1e-6)

        cylinder = self._mesh("Cylinder")
        np.testing.assert_allclose(cylinder.positions.min(axis=0), [-0.5, -1.0, -0.5], atol=1e-6)
        np.testing.assert_allclose(cylinder.positions.max(axis=0), [0.5, 1.0, 0.5], atol=1e-6)

        capsule = self._mesh("Capsule")
        np.testing.assert_allclose(capsule.positions.min(axis=0), [-0.5, -1.0, -0.5], atol=1e-6)
        np.testing.assert_allclose(capsule.positions.max(axis=0), [0.5, 1.0, 0.5], atol=1e-6)

    def test_exact_set_matches_what_is_claimed(self):
        """EXACT is a claim about knowledge, so it may only name primitives that
        are built here at all."""
        self.assertTrue(builtin_meshes.EXACT <= set(builtin_meshes.PRIMITIVES.values()))


if __name__ == "__main__":
    unittest.main()
