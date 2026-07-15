"""Build Blender materials from Unity ``.mat`` assets.

Game shaders vary wildly in property naming, so textures are located by trying a
prioritised list of candidate names per logical slot -- curated from the real
HGRP/Lit and HGRP/CharacterNPR shader source (ground truth: the ported .shader
files under E:\\SpeedProject\\AzureNihil\\Assets\\packages\\com.hg.render-
pipelines\\runtime\\shaders\\materials, cross-checked against their HLSL
channel-unpacking code, not guessed), with a generic keyword-substring
fallback for the single-texture slots so an entirely unrecognised shader
family still gets *something* instead of losing its textures outright. The
first populated candidate wins.
"""

from __future__ import annotations

import os

import bpy
import numpy as np

# Candidate property names per logical slot, in priority order.
BASE_COLOR_NAMES = [
    "_MainTex", "_BaseMap", "_BaseColorMap", "_BaseColorTex", "_Albedo",
    "_AlbedoMap", "_DiffuseMap", "_Diffuse", "_DiffuseTex", "_ColorTex",
]
NORMAL_NAMES = [
    "_BumpMap", "_NormalMap", "_NormalTex", "_Normal", "_NormalMap1", "_BumpMap1",
]
# HGRP/CharacterNPR hair-only: a dual-channel (RG=diffuse normal, BA=specular
# normal) split normal map, decoded with a DIFFERENT hemisphere-reconstruction
# order than every other normal slot here -- see _wire_hair_split_normal's
# docstring. Checked before the generic NORMAL_NAMES list; a material carrying
# this property is unambiguously hair (no other shader in this family declares
# it), so there's no ambiguity to resolve like MRO_NAMES vs METALLIC_GLOSS_NAMES.
SPLIT_NORMAL_NAMES = ["_SplitNormalMap"]
EMISSION_NAMES = ["_EmissionMap", "_EmissiveMap", "_EmissionTex", "_GlowMap"]

# Packed metallic/roughness(/occlusion) maps -- two conventions, ground-
# truthed against the real shader HLSL (not guessed):
#   HGRP/Lit._MROMap                    R=Metallic G=Roughness B=Occlusion
#     (lit.shader: SAMPLE_TEXTURE2D(_MROMap, ...); metallicT=mro.x
#     roughT=mro.y occT=mro.z)
#   HGRP/CharacterNPR._MetallicGlossMap R=Metallic A=Smoothness (so
#     Roughness = 1-A); G=Spec/B=ShadowMask have no Principled BSDF
#     equivalent and are left unconnected (characternpr.shader:
#     metallic=mg.r specScale=mg.g shadowMask=mg.b roughnessRaw=1.0-mg.a)
# A material only ever has one of these (they come from different shader
# families) -- MRO is tried first since its 3-channel packing is unambiguous.
# No generic fallback for this slot: guessing an unknown shader's packed-map
# channel order (MRO vs. glTF-style ORB vs. something else) risks silently
# wrong-looking-but-incorrect metal/rough/occlusion values, which is worse
# than leaving the slot at its default.
MRO_NAMES = ["_MROMap"]
METALLIC_GLOSS_NAMES = ["_MetallicGlossMap", "_SpecGlossMap"]

BASE_COLOR_FACTORS = ["_BaseColor", "_Color", "_MainColor", "_TintColor"]

# Last-resort fallback when a shader family isn't covered by the curated
# lists above: substrings to look for in ANY texture env name. Safe for
# these three slots specifically because "does this texture just BE the
# base color/normal/emission map" has no channel-order ambiguity, unlike the
# packed PBR slot above.
_GENERIC_BASE_COLOR_HINTS = ("basecolor", "albedo", "diffuse", "maintex", "basemap", "colormap")
_GENERIC_NORMAL_HINTS = ("normal", "bump")
_GENERIC_EMISSION_HINTS = ("emission", "emissive", "glow")


def _flatten(entries):
    """Normalize ``m_TexEnvs``/``m_Colors`` to a plain ``{name: value}`` dict.

    A real Unity Editor "Force Text" save serializes these C# Dictionary fields
    as a list of single-key maps (``- _BaseMap: {...}``); AssetRipper's own YAML
    writer instead emits the same data as one nested map directly. Both are
    valid on-disk shapes for the same data model -- accept either."""
    if isinstance(entries, dict):
        return entries
    out = {}
    for entry in entries or []:
        if isinstance(entry, dict):
            for key, value in entry.items():
                out[key] = value
    return out


def _image_from_png_bytes(png, name):
    """Decode raw PNG bytes (already produced by the C# bridge's texture
    exporter -- AssetRipper's own TextureConverter, so no compressed/mipmap
    formats ever reach here) straight into a Blender image via a bulk pixel
    push, no temp file. Blender bundles Pillow, so this stays fully in-memory."""
    from PIL import Image
    import io

    try:
        im = Image.open(io.BytesIO(png)).convert("RGBA")
    except Exception:
        return None
    width, height = im.size
    if width <= 0 or height <= 0:
        return None
    arr = np.asarray(im, dtype=np.float32) / 255.0
    arr = arr[::-1, :, :]  # PNG rows are top-first; Blender images are bottom-first.
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    image.pixels.foreach_set(arr.reshape(-1))
    image.pack()
    _disable_alpha_interpretation(image)
    return image


def _disable_alpha_interpretation(image):
    """These game shaders' texture alpha channel is routinely repurposed for
    something other than opacity (AO, emission mask, a packed PBR channel,
    ...) -- Blender's default alpha_mode ('Straight') treats a 4th channel
    as real transparency for viewport/render blending regardless of whether
    the shader graph ever wires the Alpha output anywhere, which reads as
    incorrect see-through material. 'NONE' makes Blender ignore the channel
    entirely, matching that it was never opacity data to begin with."""
    try:
        image.alpha_mode = "NONE"
    except Exception:
        pass


def _first_texture(tex_envs, names, generic_hints=()):
    for name in names:
        env = tex_envs.get(name)
        if isinstance(env, dict):
            tex = env.get("m_Texture")
            if isinstance(tex, dict) and tex.get("guid"):
                return name, tex
    for key, env in tex_envs.items():
        lower = key.lower()
        if any(hint in lower for hint in generic_hints) and isinstance(env, dict):
            tex = env.get("m_Texture")
            if isinstance(tex, dict) and tex.get("guid"):
                return key, tex
    return None, None


def _wire_packed_mro(nt, bsdf, img, location):
    """R=Metallic G=Roughness B=Occlusion (HGRP/Lit._MROMap convention). AO
    isn't wired -- Principled BSDF has no direct occlusion socket."""
    x, y = location
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.location = (x, y)
    node.label = "MRO"
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (x + 300, y)
    nt.links.new(node.outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs["Red"], bsdf.inputs["Metallic"])
    nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])


def _wire_packed_metallic_gloss(nt, bsdf, img, location):
    """R=Metallic A=Smoothness (Roughness=1-Smoothness); G=Spec/B=ShadowMask
    have no Principled BSDF equivalent and are left unconnected (HGRP/
    CharacterNPR._MetallicGlossMap convention)."""
    x, y = location
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.location = (x, y)
    node.label = "MetallicGlossMap"
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (x + 300, y)
    nt.links.new(node.outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs["Red"], bsdf.inputs["Metallic"])
    invert = nt.nodes.new("ShaderNodeMath")
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    invert.location = (x + 300, y - 180)
    nt.links.new(node.outputs["Alpha"], invert.inputs[1])
    nt.links.new(invert.outputs["Value"], bsdf.inputs["Roughness"])


def _wire_hair_split_normal(nt, bsdf, img, bump_scale, location):
    """HGRP CharacterNPR hair's _SplitNormalMap: NOT a standard DXT5nm normal map
    -- ground-truthed against characternpr_hair.shader's own unpack (the split-
    normal branch, `#if defined(_NORMALMAP) && defined(_SPECULAR_NORMALMAP)`):

        dnX = (R*2-1) * _BumpScale;  dnY = (G*2-1) * _BumpScale
        dnZ = sqrt(max(1 - dnX*dnX - dnY*dnY, eps))     -- hemisphere reconstruction,
        N = normalize(dnX*tangent + dnY*bitangent + dnZ*normal)   AFTER scale is
                                                            applied (the body shader
                                                            reconstructs Z from the
                                                            UNscaled x/y instead --
                                                            do not conflate the two;
                                                            hair's order matters here).

    B,A pack a SEPARATE specular-highlight normal (specN in the shader, used only
    to shift the anisotropic highlight lobes) -- left unwired: Principled BSDF has
    exactly one Normal input, shared by diffuse and specular, with no equivalent
    second-normal slot to route a genuinely different specular normal into: doing
    so would need a fully custom anisotropic BSDF graph, not a material-property
    linking pass.

    Built from raw Math/Vector Math nodes for the decode, then fed back through a
    Normal Map node (Strength=1) purely to reuse Blender's own tangent-space ->
    object-space transform (automatic tangent/bitangent from the active UV map,
    matching what the shader's own tanWS/bitWS would be) -- there is no Blender
    node that accepts an already-tangent-space vector and just transforms it, so
    the decoded (dnX, dnY, dnZ) is re-encoded to 0..1 first so the Normal Map
    node's own built-in *2-1 decode reconstructs this exact vector rather than
    double-applying a second decode."""
    x, y = location
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.location = (x, y)
    tex.label = "Hair Split Normal (RG=diffuse, BA=spec, spec unused)"

    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (x + 260, y)
    nt.links.new(tex.outputs["Color"], sep.inputs["Color"])

    rg_raw = nt.nodes.new("ShaderNodeCombineXYZ")
    rg_raw.location = (x + 460, y)
    nt.links.new(sep.outputs["Red"], rg_raw.inputs["X"])
    nt.links.new(sep.outputs["Green"], rg_raw.inputs["Y"])

    # (RG * 2 - 1) -- Z left at 0 for now, filled in after the hemisphere step.
    rg_unit = nt.nodes.new("ShaderNodeVectorMath")
    rg_unit.operation = "MULTIPLY_ADD"
    rg_unit.location = (x + 660, y)
    rg_unit.inputs[1].default_value = (2.0, 2.0, 0.0)
    rg_unit.inputs[2].default_value = (-1.0, -1.0, 0.0)
    nt.links.new(rg_raw.outputs["Vector"], rg_unit.inputs[0])

    dn_xy = nt.nodes.new("ShaderNodeVectorMath")
    dn_xy.operation = "SCALE"
    dn_xy.location = (x + 860, y)
    dn_xy.inputs["Scale"].default_value = bump_scale
    nt.links.new(rg_unit.outputs["Vector"], dn_xy.inputs[0])

    # sumSq = dnX*dnX + dnY*dnY (Z component is 0, so a self dot product gives
    # exactly this with no separate per-axis Math nodes).
    sum_sq = nt.nodes.new("ShaderNodeVectorMath")
    sum_sq.operation = "DOT_PRODUCT"
    sum_sq.location = (x + 1060, y)
    nt.links.new(dn_xy.outputs["Vector"], sum_sq.inputs[0])
    nt.links.new(dn_xy.outputs["Vector"], sum_sq.inputs[1])

    one_minus = nt.nodes.new("ShaderNodeMath")
    one_minus.operation = "SUBTRACT"
    one_minus.location = (x + 1260, y)
    one_minus.inputs[0].default_value = 1.0
    nt.links.new(sum_sq.outputs["Value"], one_minus.inputs[1])

    clamped = nt.nodes.new("ShaderNodeMath")
    clamped.operation = "MAXIMUM"
    clamped.location = (x + 1420, y)
    clamped.inputs[1].default_value = 1e-4
    nt.links.new(one_minus.outputs["Value"], clamped.inputs[0])

    dn_z = nt.nodes.new("ShaderNodeMath")
    dn_z.operation = "SQRT"
    dn_z.location = (x + 1580, y)
    nt.links.new(clamped.outputs["Value"], dn_z.inputs[0])

    z_vec = nt.nodes.new("ShaderNodeCombineXYZ")
    z_vec.location = (x + 1580, y - 160)
    nt.links.new(dn_z.outputs["Value"], z_vec.inputs["Z"])

    full_normal = nt.nodes.new("ShaderNodeVectorMath")
    full_normal.operation = "ADD"
    full_normal.location = (x + 1780, y)
    nt.links.new(dn_xy.outputs["Vector"], full_normal.inputs[0])
    nt.links.new(z_vec.outputs["Vector"], full_normal.inputs[1])

    # Re-encode 0..1 for the Normal Map node's own decode (see docstring).
    encoded = nt.nodes.new("ShaderNodeVectorMath")
    encoded.operation = "MULTIPLY_ADD"
    encoded.location = (x + 1980, y)
    encoded.inputs[1].default_value = (0.5, 0.5, 0.5)
    encoded.inputs[2].default_value = (0.5, 0.5, 0.5)
    nt.links.new(full_normal.outputs["Vector"], encoded.inputs[0])

    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nmap.location = (x + 2180, y)
    nmap.inputs["Strength"].default_value = 1.0
    nt.links.new(encoded.outputs["Vector"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])


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
        key = self.db.resolve_guid(guid)
        if not key:
            return None
        cached = self._image_cache.get(key)
        if cached is None:
            if hasattr(self.db, "png_bytes"):
                png = self.db.png_bytes(key)
                if png is None:
                    return None
                cached = _image_from_png_bytes(png, key)
            else:
                if not os.path.isfile(key):
                    return None
                try:
                    cached = bpy.data.images.load(key, check_existing=True)
                except RuntimeError:
                    return None
                _disable_alpha_interpretation(cached)
            if cached is None:
                return None
            self._image_cache[key] = cached
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
        floats = _flatten(props.get("m_Floats"))

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
        base_name, base_tex = _first_texture(tex_envs, BASE_COLOR_NAMES, _GENERIC_BASE_COLOR_HINTS)
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

        # Normal map -- hair's dual-channel split normal takes priority (see
        # SPLIT_NORMAL_NAMES/_wire_hair_split_normal's docstring): its presence
        # unambiguously identifies a hair material, and the generic DXT5nm path
        # below would decode it completely wrong (different channel layout,
        # different hemisphere-reconstruction order).
        _sname, split_tex = _first_texture(tex_envs, SPLIT_NORMAL_NAMES)
        if split_tex:
            img = self._load_image(split_tex["guid"], non_color=True)
            if img:
                bump_scale = floats.get("_BumpScale", 1.0)
                _wire_hair_split_normal(nt, bsdf, img, bump_scale, (-400, -250))
        else:
            _nname, normal_tex = _first_texture(tex_envs, NORMAL_NAMES, _GENERIC_NORMAL_HINTS)
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

        # Packed metallic/roughness(/occlusion) -- MRO tried first, then
        # MetallicGlossMap; see the module docstring for the ground-truthed
        # channel layout of each. No generic fallback here (see MRO_NAMES).
        _mroname, mro_tex = _first_texture(tex_envs, MRO_NAMES)
        if mro_tex:
            img = self._load_image(mro_tex["guid"], non_color=True)
            if img:
                _wire_packed_mro(nt, bsdf, img, (-400, -420))
        else:
            _mgname, mg_tex = _first_texture(tex_envs, METALLIC_GLOSS_NAMES)
            if mg_tex:
                img = self._load_image(mg_tex["guid"], non_color=True)
                if img:
                    _wire_packed_metallic_gloss(nt, bsdf, img, (-400, -420))

        # Emission.
        _ename, emis_tex = _first_texture(tex_envs, EMISSION_NAMES, _GENERIC_EMISSION_HINTS)
        if emis_tex:
            img = self._load_image(emis_tex["guid"])
            if img:
                node = nt.nodes.new("ShaderNodeTexImage")
                node.image = img
                node.location = (-400, -750)
                node.label = "Emission"
                if "Emission Color" in bsdf.inputs:
                    nt.links.new(node.outputs["Color"], bsdf.inputs["Emission Color"])
                    bsdf.inputs["Emission Strength"].default_value = 1.0

        return mat
