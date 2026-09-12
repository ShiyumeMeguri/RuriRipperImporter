"""Texture roles -- the layered mapping both hosts resolve a material through."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from ..unity import material, texture_roles, unity_yaml

STANDARD = """--- !u!21 &2100000
Material:
  m_Name: wall
  m_Shader: {fileID: 46, guid: 0000000000000000f000000000000000, type: 0}
  m_SavedProperties:
    m_TexEnvs:
      _MainTex:
        m_Texture: {fileID: 2800000, guid: aaaa1111aaaa1111aaaa1111aaaa1111, type: 3}
      _BumpMap:
        m_Texture: {fileID: 2800000, guid: bbbb2222bbbb2222bbbb2222bbbb2222, type: 3}
      _MetallicGlossMap:
        m_Texture: {fileID: 2800000, guid: cccc3333cccc3333cccc3333cccc3333, type: 3}
      _DetailMask:
        m_Texture: {fileID: 2800000, guid: dddd4444dddd4444dddd4444dddd4444, type: 3}
      _Create_jm_main_skin_head:
        m_Texture: {fileID: 2800000, guid: eeee5555eeee5555eeee5555eeee5555, type: 3}
    m_Floats:
      _Metallic: 0.25
      _Glossiness: 0.6
      _BumpScale: 1.5
    m_Colors:
      _Color: {r: 1, g: 0.5, b: 0.25, a: 1}
"""

PROVEN = """--- !u!21 &2100000
Material:
  m_Name: head
  m_ValidKeywords:
  - RURI_TEXTURE_ROLES_FROM_SHADER
  m_SavedProperties:
    m_TexEnvs:
    - T_Head_D:
        m_Texture: {fileID: 2800000, guid: aaaa1111aaaa1111aaaa1111aaaa1111, type: 3}
    - T_Head_ORM:
        m_Texture: {fileID: 2800000, guid: cccc3333cccc3333cccc3333cccc3333, type: 3}
    - _MainTex:
        m_Texture: {fileID: 2800000, guid: aaaa1111aaaa1111aaaa1111aaaa1111, type: 3}
    - _PackedMap:
        m_Texture: {fileID: 2800000, guid: cccc3333cccc3333cccc3333cccc3333, type: 3}
    m_Floats:
    - _PackedMapMetallic: 1
    - _PackedMapRoughness: 2
    - _PackedMapOcclusion: 0
"""


def _props(text):
    document = unity_yaml.UnityFile("<memory>", unity_yaml.parse_text(text)).first("Material")
    return material.parse_material(document)


class TestDefaultLayer(unittest.TestCase):
    def setUp(self):
        self.table = texture_roles.RoleTable.load(texture_roles.layer_paths(None, None))

    def test_default_layer_is_beside_the_module(self):
        self.assertEqual(self.table.sources, [texture_roles.default_layer_path()])
        self.assertEqual(self.table.proven_keyword, "RURI_TEXTURE_ROLES_FROM_SHADER")

    def test_standard_material(self):
        resolution = self.table.resolve(_props(STANDARD))
        self.assertEqual(resolution.first("base_color").name, "_MainTex")
        self.assertEqual(resolution.first("normal").name, "_BumpMap")
        texture, channel = resolution.with_channel("smoothness")
        self.assertEqual((texture.name, channel), ("_MetallicGlossMap", 3))
        texture, channel = resolution.with_channel("metallic")
        self.assertEqual((texture.name, channel), ("_MetallicGlossMap", 0))
        self.assertEqual(resolution.colors["base_color"], [1.0, 0.5, 0.25, 1.0])
        self.assertEqual(resolution.floats["smoothness"], 0.6)
        self.assertEqual(resolution.floats["normal_strength"], 1.5)
        # Known and deliberately unwired is not unmapped; an unknown name is.
        self.assertEqual(resolution.unmapped, ["_Create_jm_main_skin_head"])
        self.assertFalse(resolution.proven)

    def test_proven_material_reports_nothing_unmapped(self):
        resolution = self.table.resolve(_props(PROVEN))
        self.assertTrue(resolution.proven)
        self.assertEqual(resolution.unmapped, [])
        self.assertEqual(resolution.first("base_color").name, "_MainTex")
        packed = resolution.packed()
        self.assertEqual([texture.name for texture in packed], ["_PackedMap"])
        self.assertEqual(packed[0].channels, {"metallic": 1, "roughness": 2, "occlusion": 0})


class TestLayers(unittest.TestCase):
    def test_later_layer_overrides_and_user_entries_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            game_layer = os.path.join(directory, "game.json")
            user_layer = os.path.join(directory, "user", "wall.json")
            with open(game_layer, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "textures": {"_MainTex": {"role": "emission"}}}, handle)
            texture_roles.save_entries(user_layer, {
                "_Create_jm_main_skin_head": texture_roles.entry_for("base_color", 0),
                "_DetailMask": texture_roles.entry_for("occlusion", 1),
            })
            table = texture_roles.RoleTable.load(texture_roles.layer_paths(game_layer, user_layer))
            resolution = table.resolve(_props(STANDARD))
            self.assertEqual(resolution.first("emission").name, "_MainTex")
            self.assertEqual(resolution.first("base_color").name, "_Create_jm_main_skin_head")
            texture, channel = resolution.with_channel("occlusion")
            self.assertEqual((texture.name, channel), ("_DetailMask", 1))
            self.assertEqual(resolution.unmapped, [])
            # A second save keeps what the first wrote.
            texture_roles.save_entries(user_layer, {"_Extra": texture_roles.entry_for("none", 0)})
            saved = texture_roles.read_layer(user_layer)
            self.assertEqual(set(saved["textures"]), {"_Create_jm_main_skin_head", "_DetailMask", "_Extra"})

    def test_entry_shapes(self):
        self.assertEqual(texture_roles.entry_for("normal", 2), {"role": "normal"})
        self.assertEqual(texture_roles.entry_for("roughness", 2), {"channels": {"roughness": 2}})
        self.assertEqual(texture_roles.entry_for("none", 0), {"role": "none"})
        with self.assertRaises(ValueError):
            texture_roles.entry_for("shininess", 0)


if __name__ == "__main__":
    unittest.main()
