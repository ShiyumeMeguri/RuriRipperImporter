"""Renderer discovery: the filters both importers now share."""

from __future__ import annotations

import unittest

import numpy as np

from ..unity import hierarchy, prefab, unity_yaml

# One LODGroup (renderer 700 at LOD0, 701 at LOD1), one shadow-only renderer,
# one renderer on an inactive GameObject, and one ordinary static MeshRenderer
# with a MeshFilter and a static-batch window.
PREFAB = """--- !u!1 &100
GameObject:
  m_Component:
  - component: {fileID: 400}
  - component: {fileID: 700}
  m_Name: Body
  m_IsActive: 1
--- !u!4 &400
Transform:
  m_GameObject: {fileID: 100}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_LocalScale: {x: 1, y: 1, z: 1}
  m_Father: {fileID: 0}
--- !u!137 &700
SkinnedMeshRenderer:
  m_GameObject: {fileID: 100}
  m_Enabled: 1
  m_CastShadows: 1
  m_Mesh: {fileID: 4300000, guid: aaaa0000aaaa0000aaaa0000aaaa0000, type: 2}
  m_Materials:
  - {fileID: 2100000, guid: bbbb0000bbbb0000bbbb0000bbbb0000, type: 2}
  m_Bones:
  - {fileID: 400}
--- !u!1 &101
GameObject:
  m_Component:
  - component: {fileID: 401}
  - component: {fileID: 701}
  m_Name: BodyLod1
  m_IsActive: 1
--- !u!4 &401
Transform:
  m_GameObject: {fileID: 101}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 0, y: 1, z: 0}
  m_LocalScale: {x: 1, y: 1, z: 1}
  m_Father: {fileID: 400}
--- !u!137 &701
SkinnedMeshRenderer:
  m_GameObject: {fileID: 101}
  m_Enabled: 1
  m_CastShadows: 1
  m_Mesh: {fileID: 4300000, guid: aaaa0000aaaa0000aaaa0000aaaa0000, type: 2}
--- !u!1 &102
GameObject:
  m_Component:
  - component: {fileID: 402}
  - component: {fileID: 702}
  m_Name: ShadowProxy
  m_IsActive: 1
--- !u!4 &402
Transform:
  m_GameObject: {fileID: 102}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_LocalScale: {x: 1, y: 1, z: 1}
  m_Father: {fileID: 400}
--- !u!137 &702
SkinnedMeshRenderer:
  m_GameObject: {fileID: 102}
  m_Enabled: 1
  m_CastShadows: 3
  m_Mesh: {fileID: 4300000, guid: aaaa0000aaaa0000aaaa0000aaaa0000, type: 2}
--- !u!1 &103
GameObject:
  m_Component:
  - component: {fileID: 403}
  - component: {fileID: 3300}
  - component: {fileID: 2300}
  m_Name: Prop
  m_IsActive: 0
--- !u!4 &403
Transform:
  m_GameObject: {fileID: 103}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 2, y: 0, z: 0}
  m_LocalScale: {x: 1, y: 1, z: 1}
  m_Father: {fileID: 400}
--- !u!33 &3300
MeshFilter:
  m_GameObject: {fileID: 103}
  m_Mesh: {fileID: 4300000, guid: cccc0000cccc0000cccc0000cccc0000, type: 2}
--- !u!23 &2300
MeshRenderer:
  m_GameObject: {fileID: 103}
  m_Enabled: 1
  m_CastShadows: 1
  m_StaticBatchInfo:
    firstSubMesh: 2
    subMeshCount: 3
  m_Materials:
  - {fileID: 2100000, guid: dddd0000dddd0000dddd0000dddd0000, type: 2}
--- !u!205 &2050
LODGroup:
  m_GameObject: {fileID: 100}
  m_LODs:
  - screenRelativeHeight: 0.5
    renderers:
    - renderer: {fileID: 700}
  - screenRelativeHeight: 0.1
    renderers:
    - renderer: {fileID: 701}
"""


def _load():
    return unity_yaml.UnityFile("<memory>", unity_yaml.parse_text(PREFAB))


class TestLodDiscard(unittest.TestCase):
    def test_lod1_only_renderers_are_discarded(self):
        self.assertEqual(prefab.lod_discard_set(_load()), {701})

    def test_a_renderer_listed_at_lod0_too_is_kept(self):
        text = PREFAB.replace("    - renderer: {fileID: 701}",
                              "    - renderer: {fileID: 700}")
        unity_file = unity_yaml.UnityFile("<memory>", unity_yaml.parse_text(text))
        self.assertEqual(prefab.lod_discard_set(unity_file), set())


class TestIterRenderers(unittest.TestCase):
    def setUp(self):
        self.file = _load()
        nodes, _roots = hierarchy.build_hierarchy(self.file)
        self.go_to_node = {n.go_id: n for n in nodes.values()}

    def _run(self, **options):
        stats = prefab.SkipStats()
        found = list(prefab.iter_renderers(self.file, self.go_to_node, options, stats))
        return found, stats

    def test_defaults_keep_lod0_and_drop_the_shadow_proxy(self):
        found, stats = self._run()
        self.assertEqual([r.file_id for r in found], [700, 2300])
        self.assertEqual(stats.lod, 1)
        self.assertEqual(stats.shadow, 1)
        self.assertEqual(stats.inactive, 0)
        self.assertEqual(stats.inactive_included, 1)  # the Prop's GameObject is inactive

    def test_skinned_before_static(self):
        found, _stats = self._run()
        self.assertEqual([r.kind for r in found], ["skinned", "static"])

    def test_lod_filter_can_be_turned_off(self):
        found, stats = self._run(detail_level=prefab.EVERY_LEVEL)
        self.assertIn(701, [r.file_id for r in found])
        self.assertEqual(stats.lod, 0)

    def test_shadow_proxies_can_be_kept(self):
        found, stats = self._run(import_shadow_proxies=True)
        self.assertIn(702, [r.file_id for r in found])
        self.assertEqual(stats.shadow, 0)

    def test_inactive_can_be_excluded(self):
        found, stats = self._run(import_inactive=False)
        self.assertEqual([r.file_id for r in found], [700])
        self.assertEqual(stats.inactive, 1)
        self.assertEqual(stats.inactive_included, 0)

    def test_skinned_renderer_fields(self):
        found, _stats = self._run()
        skinned = found[0]
        self.assertEqual(skinned.name, "Body")
        self.assertTrue(skinned.is_skinned)
        self.assertEqual(skinned.mesh_ref["guid"], "aaaa0000aaaa0000aaaa0000aaaa0000")
        self.assertEqual(len(skinned.material_refs), 1)
        self.assertEqual(skinned.bones, [{"fileID": 400}])
        self.assertIsNone(skinned.submesh_range)
        self.assertFalse(skinned.disabled)

    def test_static_renderer_resolves_its_mesh_filter_and_batch_window(self):
        found, _stats = self._run()
        static = found[1]
        self.assertEqual(static.name, "Prop")
        self.assertEqual(static.mesh_ref["guid"], "cccc0000cccc0000cccc0000cccc0000")
        self.assertEqual(static.submesh_range, (2, 3))
        self.assertTrue(static.disabled)  # inactive GameObject, imported anyway

    def test_disabled_renderer_component_counts_as_disabled(self):
        text = PREFAB.replace("""SkinnedMeshRenderer:
  m_GameObject: {fileID: 100}
  m_Enabled: 1""", """SkinnedMeshRenderer:
  m_GameObject: {fileID: 100}
  m_Enabled: 0""")
        unity_file = unity_yaml.UnityFile("<memory>", unity_yaml.parse_text(text))
        nodes, _roots = hierarchy.build_hierarchy(unity_file)
        go_to_node = {n.go_id: n for n in nodes.values()}
        stats = prefab.SkipStats()
        found = list(prefab.iter_renderers(unity_file, go_to_node, {}, stats))
        self.assertTrue(found[0].disabled)
        self.assertEqual(stats.inactive_included, 2)


class TestHierarchy(unittest.TestCase):
    def test_paths_and_world_matrices(self):
        unity_file = _load()
        nodes, roots = hierarchy.build_hierarchy(unity_file)
        self.assertEqual([r.file_id for r in roots], [400])
        by_path = {n.path: n for n in nodes.values()}
        self.assertIn("BodyLod1", by_path)
        self.assertEqual(by_path[""].name, "Body")  # the root's path is empty
        # Child world = parent world @ child local.
        child = by_path["BodyLod1"]
        np.testing.assert_allclose(child.world[:3, 3], [0.0, 1.0, 0.0])
        self.assertFalse(by_path["Prop"].active)

    def test_world_matrices_helper(self):
        nodes, _roots = hierarchy.build_hierarchy(_load())
        worlds = hierarchy.world_matrices(nodes)
        self.assertEqual(set(worlds), set(nodes))
        np.testing.assert_allclose(worlds[403][:3, 3], [2.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
