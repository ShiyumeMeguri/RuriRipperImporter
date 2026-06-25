"""Build Blender materials from Unity ``.mat`` assets.

Game shaders vary wildly in property naming, so textures are located by trying a
prioritised list of common names for each slot.  Per the import requirement, the
base-colour slot accepts ``_MainTex`` or ``_BaseMap`` (and a few more), and the
normal slot accepts ``_BumpMap`` / ``_NormalMap`` and friends.  The first
populated candidate wins.
"""

from __future__ import annotations

import os

import bpy

# Candidate property names per logical slot, in priority order.
BASE_COLOR_NAMES = [
    "_MainTex", "_BaseMap", "_BaseColorMap", "_BaseColorTex", "_Albedo",
    "_AlbedoMap", "_DiffuseMap", "_Diffuse", "_DiffuseTex", "_ColorTex",
]
NORMAL_NAMES = [
    "_BumpMap", "_NormalMap", "_NormalTex", "_Normal", "_NormalMap1", "_BumpMap1",
]
EMISSION_NAMES = ["_EmissionMap", "_EmissiveMap", "_EmissionTex", "_GlowMap"]
MASK_NAMES = ["_MaskMap", "_MetallicGlossMap", "_SpecGlossMap", "_PBRMap"]

BASE_COLOR_FACTORS = ["_BaseColor", "_Color", "_MainColor", "_TintColor"]


def _flatten(entries):
    out = {}
    for entry in entries or []:
        if isinstance(entry, dict):
            for key, value in entry.items():
                out[key] = value
    return out


def _first_texture(tex_envs, names):
    for name in names:
        env = tex_envs.get(name)
        if isinstance(env, dict):
            tex = env.get("m_Texture")
            if isinstance(tex, dict) and tex.get("guid"):
                return name, tex
    return None, None


class MaterialBuilder:
    def __init__(self, asset_db, options):
        self.db = asset_db
        self.options = options
        self._cache = {}            # guid -> bpy material
        self._image_cache = {}      # path -> bpy image

    def build_from_ref(self, ref):
        """Build/return a material from a {fileID, guid} reference."""
        if not isinstance(ref, dict):
            return None
        guid = ref.get("guid")
        if not guid:
            return None
        if guid in self._cache:
            return self._cache[guid]
        unity_file = self.db.load_guid(guid)
        doc = unity_file.first("Material") if unity_file else None
        if doc is None:
            mat = bpy.data.materials.new("UnityMaterial")
            self._cache[guid] = mat
            return mat
        mat = self._build(doc)
        self._cache[guid] = mat
        return mat

    def _load_image(self, guid, non_color=False):
        path = self.db.resolve_guid(guid)
        if not path or not os.path.isfile(path):
            return None
        cached = self._image_cache.get(path)
        if cached is None:
            try:
                cached = bpy.data.images.load(path, check_existing=True)
            except RuntimeError:
                return None
            self._image_cache[path] = cached
        if non_color:
            try:
                cached.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        return cached

    def _build(self, doc):
        data = doc.data
        name = data.get("m_Name", "UnityMaterial")
        props = data.get("m_SavedProperties") or {}
        tex_envs = _flatten(props.get("m_TexEnvs"))
        colors = _flatten(props.get("m_Colors"))

        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        output = nt.nodes.new("ShaderNodeOutputMaterial")
        output.location = (600, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (200, 0)
        nt.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        # Base colour.
        base_name, base_tex = _first_texture(tex_envs, BASE_COLOR_NAMES)
        if base_tex:
            img = self._load_image(base_tex["guid"])
            if img:
                node = nt.nodes.new("ShaderNodeTexImage")
                node.image = img
                node.location = (-400, 100)
                node.label = base_name
                nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
                if self.options.get("connect_alpha", True):
                    nt.links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
        else:
            for factor in BASE_COLOR_FACTORS:
                col = colors.get(factor)
                if isinstance(col, dict):
                    bsdf.inputs["Base Color"].default_value = (
                        col.get("r", 1.0), col.get("g", 1.0), col.get("b", 1.0), col.get("a", 1.0))
                    break

        # Normal map.
        _nname, normal_tex = _first_texture(tex_envs, NORMAL_NAMES)
        if normal_tex:
            img = self._load_image(normal_tex["guid"], non_color=True)
            if img:
                node = nt.nodes.new("ShaderNodeTexImage")
                node.image = img
                node.location = (-400, -250)
                node.label = "Normal"
                nmap = nt.nodes.new("ShaderNodeNormalMap")
                nmap.location = (-100, -250)
                nt.links.new(node.outputs["Color"], nmap.inputs["Color"])
                nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

        # Emission.
        _ename, emis_tex = _first_texture(tex_envs, EMISSION_NAMES)
        if emis_tex:
            img = self._load_image(emis_tex["guid"])
            if img:
                node = nt.nodes.new("ShaderNodeTexImage")
                node.image = img
                node.location = (-400, -550)
                node.label = "Emission"
                if "Emission Color" in bsdf.inputs:
                    nt.links.new(node.outputs["Color"], bsdf.inputs["Emission Color"])
                    bsdf.inputs["Emission Strength"].default_value = 1.0

        return mat
