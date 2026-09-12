"""Material property reading -- the serialisation shapes both hosts must agree
on."""

from __future__ import annotations

import unittest

from ..unity import material, unity_yaml

# AssetRipper's writer: nested maps.
NESTED = """--- !u!21 &2100000
Material:
  m_Name: chr_body
  m_Shader: {fileID: 4800000, guid: 1111111111111111111111111111abcd, type: 3}
  m_ShaderKeywords: _NORMALMAP _ALPHATEST_ON
  m_SavedProperties:
    m_TexEnvs:
      _BaseMap:
        m_Texture: {fileID: 2800000, guid: aaaa1111aaaa1111aaaa1111aaaa1111, type: 3}
        m_Scale: {x: 2, y: 1}
        m_Offset: {x: 0, y: 0.5}
      _BumpMap:
        m_Texture: {fileID: 2800000, guid: bbbb2222bbbb2222bbbb2222bbbb2222, type: 3}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
      _EmptySlot:
        m_Texture: {fileID: 0}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    m_Ints:
      _CullMode: 2
      _BumpScale: 9
    m_Floats:
      _BumpScale: 1.5
      _Metallic: 0.25
    m_Colors:
      _BaseColor: {r: 1, g: 0.5, b: 0.25, a: 1}
"""

# A real Unity "Force Text" save: lists of single-key maps.
LISTED = """--- !u!21 &2100000
Material:
  m_Name: chr_body
  m_ValidKeywords:
  - _NORMALMAP
  m_InvalidKeywords:
  - _DETAIL_ON
  m_SavedProperties:
    m_TexEnvs:
    - _MainTex:
        m_Texture: {fileID: 2800000, guid: cccc3333cccc3333cccc3333cccc3333, type: 3}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    - _CustomDiffuseTex:
        m_Texture: {fileID: 2800000, guid: dddd4444dddd4444dddd4444dddd4444, type: 3}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    m_Floats:
    - _Cutoff: 0.5
    m_Colors:
    - _Color: {r: 0, g: 0, b: 1, a: 1}
"""


def _doc(text):
    return unity_yaml.UnityFile("<memory>", unity_yaml.parse_text(text)).first("Material")


class TestParseMaterial(unittest.TestCase):
    def test_nested_shape(self):
        props = material.parse_material(_doc(NESTED))
        self.assertEqual(props.name, "chr_body")
        self.assertEqual(props.textures["_BaseMap"], "aaaa1111aaaa1111aaaa1111aaaa1111")
        self.assertEqual(props.textures["_BumpMap"], "bbbb2222bbbb2222bbbb2222bbbb2222")
        self.assertNotIn("_EmptySlot", props.textures)  # no guid -> not a texture
        self.assertEqual(props.colors["_BaseColor"], [1.0, 0.5, 0.25, 1.0])
        self.assertEqual(props.texture_st["_BaseMap"], [2.0, 1.0, 0.0, 0.5])
        self.assertEqual(props.shader_ref["guid"], "1111111111111111111111111111abcd")

    def test_listed_shape(self):
        props = material.parse_material(_doc(LISTED))
        self.assertEqual(props.textures["_MainTex"], "cccc3333cccc3333cccc3333cccc3333")
        self.assertEqual(props.float("_Cutoff"), 0.5)
        self.assertEqual(props.colors["_Color"], [0.0, 0.0, 1.0, 1.0])

    def test_ints_merge_under_floats_but_floats_win(self):
        props = material.parse_material(_doc(NESTED))
        self.assertEqual(props.float("_CullMode"), 2.0)     # only in m_Ints
        self.assertEqual(props.float("_BumpScale"), 1.5)    # m_Floats wins

    def test_keywords_across_serialisations(self):
        self.assertEqual(material.parse_material(_doc(NESTED)).keywords,
                         {"_NORMALMAP", "_ALPHATEST_ON"})
        # Only the VALID list is enabled.
        self.assertEqual(material.parse_material(_doc(LISTED)).keywords, {"_NORMALMAP"})

    def test_none_document_is_tolerated(self):
        props = material.parse_material(None)
        self.assertEqual(props.textures, {})
        self.assertEqual(props.float("_Metallic", 0.75), 0.75)


class TestSlotLookup(unittest.TestCase):
    def test_named_slots_in_the_given_order(self):
        props = material.parse_material(_doc(LISTED))
        name, guid = props.find_texture(["_NotPresent", "_MainTex"])
        self.assertEqual(name, "_MainTex")
        self.assertEqual(guid, "cccc3333cccc3333cccc3333cccc3333")

    def test_no_match(self):
        props = material.parse_material(_doc(LISTED))
        self.assertEqual(props.find_texture(["_Nope"]), (None, None))

    def test_find_color(self):
        props = material.parse_material(_doc(NESTED))
        self.assertEqual(props.find_color(["_BaseColor", "_Color"]),
                         [1.0, 0.5, 0.25, 1.0])
        self.assertIsNone(props.find_color(["_Missing"]))


class TestMaterialName(unittest.TestCase):
    def test_document_name_wins(self):
        self.assertEqual(material.material_name(_doc(NESTED), "abc"), "chr_body")

    def test_guid_stand_in(self):
        self.assertEqual(material.material_name(None, "0123456789abcdef" * 2),
                         "Material_01234567")

    def test_last_resort(self):
        self.assertEqual(material.material_name(None, None), "Material")


class TestFlatten(unittest.TestCase):
    def test_both_shapes_agree(self):
        listed = [{"_A": 1}, {"_B": 2}]
        nested = {"_A": 1, "_B": 2}
        self.assertEqual(material.flatten(listed), material.flatten(nested))

    def test_none(self):
        self.assertEqual(material.flatten(None), {})


if __name__ == "__main__":
    unittest.main()
