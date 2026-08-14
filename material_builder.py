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
    return props.shader_guid() in _BUILTIN_RESOURCE_GUIDS


def _material_content_digest(doc):
    """A stable digest of what a Material document DECLARES -- its shader
    reference and its whole property table -- independent of which guid or CAB
    carries it. json with sorted keys over the already-parsed document data, so
    two content-equal materials digest equally regardless of key order."""
    import hashlib
    import json

    payload = {
        "shader": doc.data.get("m_Shader"),
        "properties": doc.data.get("m_SavedProperties"),
        "name": doc.data.get("m_Name"),
    }
    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _shader_identity(builder, props):
    guid = props.shader_guid()
    if not guid:
        return "<no m_Shader reference>"
    return unity_material.shader_identity(builder.db._text(guid)) or "guid " + guid


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


# Capability rewires, same contract again: each generated shader module registers
# its own ``rewire_capabilities(material) -> bool``. A shader's environment
# queries -- ambient irradiance, specular radiance, the main light -- are cut at
# the group interface and answered from scene state, so changing that state (a
# different sun, a per-material light override, another world) leaves the answer
# stale until the fulfilment side is rebuilt.
#
# Only the fulfilment nodes are rebuilt, never the graph: a full material rebuild
# would take the user's tuned parameters back to shipped defaults. A module
# returns False for a material it did not build, so asking all of them is safe
# and order-independent.
CAPABILITY_REWIRES = []


def register_capability_rewire(rewire):
    if rewire not in CAPABILITY_REWIRES:
        CAPABILITY_REWIRES.append(rewire)


def unregister_capability_rewire(rewire):
    if rewire in CAPABILITY_REWIRES:
        CAPABILITY_REWIRES.remove(rewire)


def rewire_capabilities(materials=None):
    """Re-answer the environment queries of every Ruri-built material.

    ``materials`` defaults to every material in the file. Returns how many were
    claimed and rewired.
    """
    import bpy

    pool = list(bpy.data.materials) if materials is None else list(materials)
    return sum(1 for mat in pool
               if mat is not None and any(rewire(mat) for rewire in CAPABILITY_REWIRES))


# Post stages, same contract again, except that a stage is the generated MODULE
# rather than one function: post-processing owns scene-level state (the
# compositor tree, the view transform), so it has to be able to hand that state
# back as well as take it -- install / uninstall / installed / stage_node.
# Post-processing is a whole-frame operation done ONCE after compositing;
# putting it in the materials would run it again per overlapping transparent
# layer, so a stage owns the scene's compositor tree, never a material.
POST_STAGES = []


def register_post_stage(stage):
    if stage not in POST_STAGES:
        POST_STAGES.append(stage)


def unregister_post_stage(stage):
    if stage in POST_STAGES:
        POST_STAGES.remove(stage)


def apply_post_stages(scene):
    """Install every registered post stage onto a scene. More than one stage
    would mean two owners of one compositor tree, so that is reported rather
    than silently letting the last one win."""
    if len(POST_STAGES) > 1:
        print("[material] !! {0} post stages registered; a scene has ONE compositor tree, "
              "the last installed wins".format(len(POST_STAGES)))
    return [stage.install(scene) for stage in POST_STAGES]


def remove_post_stages(scene):
    """Hand the scene back the compositor state it had before any stage was
    installed. Without this a load is one-way: the user's own compositor tree
    and view transform are gone with no way to say what they were."""
    return [stage.uninstall(scene) for stage in POST_STAGES]


def post_stages_installed(scene):
    return [stage for stage in POST_STAGES if stage.installed(scene)]


def _image_from_texture_bytes(data, name):
    """Load an exported texture's raw bytes (produced by AssetRipper's own
    TextureConverter, so no compressed/mipmap formats ever reach here) into a
    Blender image via a PERSISTENT cache file and Blender's OWN native image
    loader (bpy.data.images.load) -- NOT Pillow; measured pixel-identical and
    30x faster on the real Pelica texture set.

    The image is NOT packed. Packing pinned every texture's full bytes inside
    the .blend session forever -- on a scene window with hundreds of textures
    that is gigabytes of working set Blender can never evict. Loaded from a
    real file instead, Blender's own image cache pages pixels in and out under
    its own memory budget, and a re-import reuses both the file and the loaded
    datablock (check_existing).

    The cache is CONTENT-ADDRESSED, and has to be: an export batch mints fresh
    guids every run, so keying on a guid gives a cache that never hits and
    grows by the whole texture set per import (measured: 213 textures -> 426
    files after two runs). A digest of the bytes also self-invalidates when the
    game updates a texture, and collapses duplicates shipped under several
    names. The cache lives in the toolchain workspace (never the addon
    checkout, never a temp dir that vanishes while the .blend still references
    it); the container is deliberately not named or checked anywhere -- Blender
    identifies an image by content, so png/tga/exr all arrive the same way and
    a new export format needs no code here."""
    import hashlib

    try:
        from .RuriRipperPyBridge.runtime import workspace
    except ImportError:  # standalone (non-package) testing
        from RuriRipperPyBridge.runtime import workspace

    cache_dir = workspace.subdir("texture_cache")
    path = os.path.join(cache_dir,
                        "{0}.img".format(hashlib.blake2b(data, digest_size=16).hexdigest()))
    try:
        if not os.path.isfile(path) or os.path.getsize(path) != len(data):
            with open(path, "wb") as handle:
                handle.write(data)
    except OSError:
        return None
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return None
    image.name = name
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
        self._content_cache = {}    # property-content digest -> bpy material
        self._image_cache = {}      # path -> bpy image

    def build_from_ref(self, ref):
        """Build/return a material from a {fileID, guid} reference.

        Two cache layers, both identity-true: the guid (this exact asset), then
        a digest of the material's OWN declared content (shader reference +
        m_SavedProperties). A streaming map ships the same material stamped
        into many chunks under distinct guids; content-equal materials build
        one Blender material, not hundreds of identical ones."""
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
        digest = _material_content_digest(doc)
        mat = self._content_cache.get(digest)
        if mat is None:
            mat = self._build(doc)
            self._content_cache[digest] = mat
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
        game_shader = not _is_builtin_shader(props)
        for provider in ([] if not game_shader else GRAPH_PROVIDERS):
            try:
                claimed = provider(self, props)
            except Exception:
                import traceback
                traceback.print_exc()
                print("[material] !! provider {0} EXCEPTION on '{1}' -- falling back to Principled, "
                      "graph is NOT the game shader".format(
                          getattr(provider, "__module__", provider), name))
                claimed = None
            if claimed is not None:
                return claimed
        if game_shader:
            print("[material] !! UNCLAIMED '{0}' shader={1} -- no generated stack covers it, "
                  "falling back to Principled, graph is NOT the game shader".format(
                      name, _shader_identity(self, props)))

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
