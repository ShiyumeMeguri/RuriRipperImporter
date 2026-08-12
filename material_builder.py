"""Build Blender materials from Unity ``.mat`` assets.

READING the .mat -- Unity's three property-table serialisations, and the
prioritised candidate names that locate a logical slot (base colour, normal,
packed PBR, emission) across wildly different game shaders -- is shared with the
Painter plugin (``RuriRipperPyBridge.unity.material``), because it is a fact about
Unity and the HGRP shader family rather than about Blender. This module is what
Blender does with the answer: a Principled BSDF node graph.
"""

from __future__ import annotations

import os

import bpy

try:
    from .RuriRipperPyBridge.unity import material as unity_material
except ImportError:  # standalone (non-package) testing
    from RuriRipperPyBridge.unity import material as unity_material

# Game-shader graph providers. This core stays game-blind (see Game/__init__'s
# charter): a game module registers a callable ``provider(builder, props) ->
# bpy.Material | None`` and SELF-selects by the material's own property
# signature, returning None to decline. First claimant wins; no claimant means
# the Principled BSDF fallback below. Registration order is registration order
# -- game modules register on their own register() and remove on unregister().
GRAPH_PROVIDERS = []


# Unity's own two built-in resource files, by the guids every project references
# them under: `unity default resources` and `unity_builtin_extra`. A shader
# reference into either is a stock Unity shader (Standard, Sprites/Default...),
# which no game's shader graph can be.
_BUILTIN_RESOURCE_GUIDS = frozenset((
    "0000000000000000e000000000000000",
    "0000000000000000f000000000000000",
))


def _is_builtin_shader(props):
    ref = props.shader_ref if isinstance(props.shader_ref, dict) else None
    return str((ref or {}).get("guid") or "").lower() in _BUILTIN_RESOURCE_GUIDS


def register_graph_provider(provider):
    if provider not in GRAPH_PROVIDERS:
        GRAPH_PROVIDERS.append(provider)


def unregister_graph_provider(provider):
    if provider in GRAPH_PROVIDERS:
        GRAPH_PROVIDERS.remove(provider)


# Vertex stages, same contract as GRAPH_PROVIDERS: each generated shader module
# registers its own ``apply_vertex_stage(objects=None, camera=None) -> int`` on
# register() and removes it on unregister(). Every stage only touches materials
# and modifiers it owns, so running all of them is order-independent. Consumers
# call apply_vertex_stages() -- never a module by name.
VERTEX_STAGES = []


def register_vertex_stage(stage):
    if stage not in VERTEX_STAGES:
        VERTEX_STAGES.append(stage)


def unregister_vertex_stage(stage):
    if stage in VERTEX_STAGES:
        VERTEX_STAGES.remove(stage)


def apply_vertex_stages(objects=None, camera=None):
    return sum(stage(objects=objects, camera=camera) for stage in VERTEX_STAGES)


def _image_from_texture_bytes(data, name):
    """Load an exported texture's raw bytes (produced by AssetRipper's own
    TextureConverter, so no compressed/mipmap formats ever reach here) into a
    Blender image through a temp file and Blender's OWN native image loader --
    the same path disk-mode texture loading already used (bpy.data.images.load),
    NOT Pillow. Measured on the real Pelica texture set (66 textures): the
    previous PIL-decode + numpy + foreach_set push cost 5.3s wall (dominated by
    Pillow's own import, itself slowed by a known CPython enum-module
    regression); the native loader does the same 66 textures in 0.16s --
    pixel-identical (0.0 max delta), since it's the exact decoder Blender uses
    for every other image in the file.

    The container is deliberately not named or checked anywhere: Blender's loader
    identifies an image by its content, so png/tga/exr all arrive the same way and
    a new export format needs no code here."""
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".img", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        try:
            image = bpy.data.images.load(tmp.name)
        except RuntimeError:
            return None
        image.name = name
        target = "//textures/" + name + _image_extension(image)
        # Pack first (it reads the file still on disk), THEN name both records.
        # Unpack writes to the path stamped on the packed-file record, NOT to
        # image.filepath -- which is why clearing image.filepath used to leave
        # every unpacked texture named tmpXXXXXXXX.img. Exactly one packed file
        # exists here: this loads a single image, never a UDIM tile set.
        image.pack()
        image.packed_files[0].filepath = target
        image.filepath_raw = target
    finally:
        os.unlink(tmp.name)
    _disable_alpha_interpretation(image)
    return image


def _image_extension(image):
    """The extension Blender itself uses for this image's DETECTED format, read
    out of Blender's own format table rather than a map here -- a container this
    importer has never seen still unpacks under a truthful name.

    Falls back to the format's own name, never to a guess like ".png": naming a
    DDS ".png" would be worse than an unusual extension."""
    fallback = "." + (image.file_format or "img").lower()
    render = getattr(getattr(bpy.context, "scene", None), "render", None)
    if render is None:
        return fallback
    previous = render.image_settings.file_format
    try:
        render.image_settings.file_format = image.file_format
        return render.file_extension or fallback
    except (TypeError, ValueError):
        # Blender can READ formats it cannot render to (dds, ...); those simply
        # have no entry in that table.
        return fallback
    finally:
        render.image_settings.file_format = previous


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
    -- ground-truthed instruction-by-instruction against the real compiled
    shader (characternpr_hair, variant b101):

        _491 = R*2-1 ;  _493 = G*2-1                          (no AG multiply: the
                                                               alpha here is the
                                                               SPEC normal's Y)
        _501 = max(sqrt(1 - min(dot(xy,xy), 1)), 1e-16)       <- from the UNSCALED xy
        _507 = _491 * _BumpScale ;  _508 = _493 * _BumpScale  <- scale hits xy only
        N = normalize(_501*normal + _507*tangent + _508*bitangent)

    The hemisphere reconstruction uses the UNSCALED x/y and the scale is applied
    to x/y afterwards. That is the same order the body/skin shader uses (skin
    b113: _462/_470/_474) and the same one fur and VFX use -- there is NO
    per-part difference, contrary to what this docstring previously claimed.
    Reconstructing Z from the SCALED x/y (which this function used to do, on the
    strength of that claim) collapses Z to ~0 once _BumpScale > 1, because
    1 - scaled^2 goes negative and clamps; the two orders agree only at
    _BumpScale == 1, which is why it went unnoticed.

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

    # sumSq = dot(xy, xy) on the UNSCALED xy (rg_unit, NOT dn_xy) -- see the
    # docstring: the shader reconstructs Z before applying _BumpScale. Z's
    # component is 0 in rg_unit, so a self dot product is exactly x*x + y*y.
    sum_sq = nt.nodes.new("ShaderNodeVectorMath")
    sum_sq.operation = "DOT_PRODUCT"
    sum_sq.location = (x + 1060, y)
    nt.links.new(rg_unit.outputs["Vector"], sum_sq.inputs[0])
    nt.links.new(rg_unit.outputs["Vector"], sum_sq.inputs[1])

    # min(dot, 1) before the subtract, matching the shader's own clamp.
    capped = nt.nodes.new("ShaderNodeMath")
    capped.operation = "MINIMUM"
    capped.location = (x + 1160, y)
    capped.inputs[1].default_value = 1.0
    nt.links.new(sum_sq.outputs["Value"], capped.inputs[0])

    one_minus = nt.nodes.new("ShaderNodeMath")
    one_minus.operation = "SUBTRACT"
    one_minus.location = (x + 1260, y)
    one_minus.inputs[0].default_value = 1.0
    nt.links.new(capped.outputs["Value"], one_minus.inputs[1])

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
            if hasattr(self.db, "texture_bytes"):
                data = self.db.texture_bytes(key)
                if data is None:
                    return None
                # The image is named what the GAME names it -- the closure
                # carries every asset's exported path. The guid stays the cache
                # identity; it was never a name.
                cached = _image_from_texture_bytes(data, self.db.asset_name(key) or key)
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
        props = unity_material.parse_material(doc)
        name = props.name or "UnityMaterial"

        # Game-shader providers first (each declines with None); fallback below.
        # A material whose shader lives in Unity's own built-in resource files is
        # by construction a STOCK shader, so no game provider can claim it and
        # none is asked -- a provider that reported "this shader is not in the
        # closure" would be right and useless, since that file never is.
        for provider in ([] if _is_builtin_shader(props) else GRAPH_PROVIDERS):
            try:
                claimed = provider(self, props)
            except Exception:
                import traceback
                traceback.print_exc()
                # 响亮失败:provider 炸掉 ≠ 无人认领——静默滑进 Principled 会把移植 bug
                # 伪装成"材质没被认领"(实锤:悬空 gen.PART_DEFAULTS 让全员兜底了一轮)。
                print("[material] !! provider {0} EXCEPTION on '{1}' -- falling back to Principled, "
                      "graph is NOT the game shader".format(
                          getattr(provider, "__module__", provider), name))
                claimed = None
            if claimed is not None:
                return claimed

        # 就地改写同名材质(网格按名字绑材质;另起新料会得 .001 后缀留下双份)。下方整树清空重建,幂等。
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        output = nt.nodes.new("ShaderNodeOutputMaterial")
        output.location = (600, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (200, 0)
        nt.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        # Base colour.
        base_name, base_guid = props.find_texture(
            unity_material.BASE_COLOR_NAMES, unity_material.GENERIC_BASE_COLOR_HINTS)
        if base_guid:
            img = self._load_image(base_guid)
            if img:
                node = nt.nodes.new("ShaderNodeTexImage")
                node.image = img
                node.location = (-400, 100)
                node.label = base_name
                nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
                if self.options.get("connect_alpha", True):
                    nt.links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
        else:
            col = props.find_color(unity_material.BASE_COLOR_FACTORS)
            if col is not None:
                bsdf.inputs["Base Color"].default_value = tuple(col)

        # Normal map -- hair's dual-channel split normal takes priority (see
        # material.SPLIT_NORMAL_NAMES / _wire_hair_split_normal's docstring):
        # its presence unambiguously identifies a hair material, and the generic
        # DXT5nm path below would decode it completely wrong (different channel
        # layout, different hemisphere-reconstruction order).
        _sname, split_guid = props.find_texture(unity_material.SPLIT_NORMAL_NAMES)
        if split_guid:
            img = self._load_image(split_guid, non_color=True)
            if img:
                bump_scale = props.float("_BumpScale", 1.0)
                _wire_hair_split_normal(nt, bsdf, img, bump_scale, (-400, -250))
        else:
            _nname, normal_guid = props.find_texture(
                unity_material.NORMAL_NAMES, unity_material.GENERIC_NORMAL_HINTS)
            if normal_guid:
                img = self._load_image(normal_guid, non_color=True)
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
        # MetallicGlossMap; the ground-truthed channel layout of each is
        # documented on material.MRO_NAMES. No generic fallback for this slot.
        _mroname, mro_guid = props.find_texture(unity_material.MRO_NAMES)
        if mro_guid:
            img = self._load_image(mro_guid, non_color=True)
            if img:
                _wire_packed_mro(nt, bsdf, img, (-400, -420))
        else:
            _mgname, mg_guid = props.find_texture(unity_material.METALLIC_GLOSS_NAMES)
            if mg_guid:
                img = self._load_image(mg_guid, non_color=True)
                if img:
                    _wire_packed_metallic_gloss(nt, bsdf, img, (-400, -420))

        # Emission.
        _ename, emis_guid = props.find_texture(
            unity_material.EMISSION_NAMES, unity_material.GENERIC_EMISSION_HINTS)
        if emis_guid:
            img = self._load_image(emis_guid)
            if img:
                node = nt.nodes.new("ShaderNodeTexImage")
                node.image = img
                node.location = (-400, -750)
                node.label = "Emission"
                if "Emission Color" in bsdf.inputs:
                    nt.links.new(node.outputs["Color"], bsdf.inputs["Emission Color"])
                    bsdf.inputs["Emission Strength"].default_value = 1.0

        return mat
