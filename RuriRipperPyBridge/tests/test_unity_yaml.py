"""The Unity YAML subset parser and the class-id registry."""

from __future__ import annotations

import unittest

from ..unity import class_registry, unity_yaml

PREFAB = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100
GameObject:
  m_ObjectHideFlags: 0
  m_Component:
  - component: {fileID: 400}
  - component: {fileID: 13700}
  m_Name: Root
  m_IsActive: 1
--- !u!4 &400
Transform:
  m_GameObject: {fileID: 100}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 1.5, y: -2, z: 0.25}
  m_LocalScale: {x: 1, y: 1, z: 1}
  m_Children:
  - {fileID: 401}
  m_Father: {fileID: 0}
--- !u!137 &13700
SkinnedMeshRenderer:
  m_GameObject: {fileID: 100}
  m_Enabled: 1
  m_CastShadows: 1
  m_Materials:
  - {fileID: 2100000, guid: 0123456789abcdef0123456789abcdef, type: 2}
  m_Mesh: {fileID: 4300000, guid: fedcba9876543210fedcba9876543210, type: 2}
--- !u!114 &99 stripped
MonoBehaviour:
  m_Script: {fileID: 11500000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.docs = unity_yaml.parse_text(PREFAB)
        self.file = unity_yaml.UnityFile("<memory>", self.docs)

    def test_document_headers(self):
        self.assertEqual([d.class_id for d in self.docs], [1, 4, 137, 114])
        self.assertEqual([d.file_id for d in self.docs], [100, 400, 13700, 99])
        self.assertTrue(self.docs[3].stripped)
        self.assertFalse(self.docs[0].stripped)

    def test_lookup_by_class_name_and_id(self):
        self.assertIsNotNone(self.file.first("GameObject"))
        self.assertEqual(self.file.first("Transform").file_id, 400)
        self.assertEqual(len(self.file.all("SkinnedMeshRenderer")), 1)
        self.assertIs(self.file.get(13700), self.docs[2])
        self.assertIsNone(self.file.first("LODGroup"))

    def test_flow_maps_and_scalars(self):
        transform = self.file.get(400).data
        self.assertEqual(transform["m_LocalPosition"], {"x": 1.5, "y": -2, "z": 0.25})
        self.assertIsInstance(transform["m_LocalPosition"]["y"], int)
        self.assertIsInstance(transform["m_LocalPosition"]["x"], float)
        self.assertEqual(transform["m_Father"]["fileID"], 0)

    def test_block_sequences(self):
        components = self.file.get(100).data["m_Component"]
        self.assertEqual([c["component"]["fileID"] for c in components], [400, 13700])
        children = self.file.get(400).data["m_Children"]
        self.assertEqual(children, [{"fileID": 401}])

    def test_guid_references_stay_strings(self):
        mesh = self.file.get(13700).data["m_Mesh"]
        self.assertEqual(mesh["guid"], "fedcba9876543210fedcba9876543210")
        self.assertEqual(mesh["fileID"], 4300000)

    def test_name_field(self):
        self.assertEqual(self.file.get(100).data["m_Name"], "Root")


class TestScalarHandling(unittest.TestCase):
    def test_long_digit_blob_stays_a_string(self):
        text = ("--- !u!43 &1\nMesh:\n  m_DataSize: "
                + "1" * 64 + "\n")
        doc = unity_yaml.parse_text(text)[0]
        self.assertIsInstance(doc.data["m_DataSize"], str)

    def test_quoted_key_and_doubled_quote(self):
        text = "--- !u!114 &1\nMonoBehaviour:\n  'it''s': 3\n"
        doc = unity_yaml.parse_text(text)[0]
        self.assertEqual(doc.data["it's"], 3)

    def test_empty_value_is_none(self):
        text = "--- !u!114 &1\nMonoBehaviour:\n  m_Empty:\n  m_After: 1\n"
        doc = unity_yaml.parse_text(text)[0]
        self.assertIsNone(doc.data["m_Empty"])
        self.assertEqual(doc.data["m_After"], 1)

    def test_flow_sequence_of_maps(self):
        text = "--- !u!114 &1\nMonoBehaviour:\n  m_List: [{a: 1}, {b: 2}]\n"
        doc = unity_yaml.parse_text(text)[0]
        self.assertEqual(doc.data["m_List"], [{"a": 1}, {"b": 2}])


class TestClassRegistry(unittest.TestCase):
    def test_registry_is_loaded(self):
        self.assertTrue(class_registry.is_loaded())

    def test_known_ids(self):
        self.assertEqual(class_registry.canonical_name(1), "GameObject")
        self.assertEqual(class_registry.canonical_name(137), "SkinnedMeshRenderer")
        self.assertEqual(class_registry.id_for_name("Transform"), 4)

    def test_unknown_lookups_degrade_quietly(self):
        self.assertIsNone(class_registry.id_for_name("NotAUnityClass"))
        self.assertIsNone(class_registry.canonical_name(None))
        self.assertEqual(class_registry.names_for_id(None), [])

    def test_canonical_name_wins_over_the_written_name(self):
        """The header's numeric id is the stable identity; the name written in
        the file is only a fallback."""
        doc = unity_yaml.parse_text("--- !u!137 &1\nSomeOldRendererName:\n  m_Enabled: 1\n")[0]
        self.assertEqual(doc.class_name, "SkinnedMeshRenderer")
        self.assertEqual(doc.raw_class_name, "SomeOldRendererName")


if __name__ == "__main__":
    unittest.main()
