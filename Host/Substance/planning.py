"""How one stated material becomes one Texture Set: which of this application's channels its
textures are split into, which samplers and uniforms it sets, and which part of the generated
shader it is.

Two answers, one per material, and ``plan.shaded`` says which:

* the generated shader's own projection manifest, when that shader's part vocabulary claims the
  material's shader (``panel.parts[].shader`` or one of its ``aliases``). The manifest is the
  generator's contract: it routes every source texture channel to an input by a named operation,
  states each input's storage, and names every uniform;
* what the reader's role layers say the material's textures mean, when it does not. Those land in
  this application's standard channels and the set keeps this application's own shader -- the
  generated one would read them as a part it was never told about.

No routing table lives here: the manifest states the first, the roles state the second, and what
is left is this application's own channel vocabulary.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os

from . import shader

#: Operations the kernel's texture bake understands (``core.texture.bake``).
OP_COPY_RGB = "copy_rgb"
OP_COPY_RGBA = "copy_rgba"
OP_INVERT_A = "invert_a"
OP_NORMAL_UNITY = "normal_unity"
OP_NORMAL_SPLIT_RG = "normal_split_rg"
OP_NORMAL_SPLIT_BA = "normal_split_ba"
EXTRACT = "extract:"
INVERT = "invert:"

_CHANNEL_LETTERS = "rgba"

#: The bake operations a manifest may name; one it names beyond these is refused at load.
_BAKE_OPS = ("invert", "repack_normal", "unpack_normal_alpha_green", "unpack_normal_pair")

#: How one channel is stored: its format, whether it is colour, and the label a user channel shows.
ChannelSpec = collections.namedtuple("ChannelSpec", ("format", "srgb", "label"))

#: This application's own channel vocabulary: the token its script API names a channel by, and the
#: ChannelType members that are the same channel across its versions.
CHANNEL_TYPES = dict(
    {"basecolor": ("BaseColor",), "opacity": ("Opacity",), "metallic": ("Metallic",),
     "specularlevel": ("SpecularLevel", "Specularlevel"), "roughness": ("Roughness",),
     "height": ("Height",), "normal": ("Normal",), "emissive": ("Emissive",),
     "ambientOcclusion": ("AO", "AmbientOcclusion", "Ao")},
    **{"user{0}".format(index): ("User{0}".format(index),) for index in range(8)})

#: The standard channels a role lands in, and how each is stored.
_STANDARD = {
    "basecolor": ChannelSpec("sRGB8", True, ""),
    "normal": ChannelSpec("RGB16F", False, ""),
    "emissive": ChannelSpec("sRGB8", True, ""),
    "metallic": ChannelSpec("L8", False, ""),
    "roughness": ChannelSpec("L8", False, ""),
    "ambientOcclusion": ChannelSpec("L8", False, ""),
    "specularlevel": ChannelSpec("L8", False, ""),
    "opacity": ChannelSpec("L8", False, ""),
    "height": ChannelSpec("L8", False, ""),
}

#: A role a texture carries whole: the standard channel it lands in, and the split.
_WHOLE_ROLES = {"base_color": ("basecolor", OP_COPY_RGB), "normal": ("normal", OP_NORMAL_UNITY),
                "emission": ("emissive", OP_COPY_RGB)}

#: A role a texture carries in one channel: the standard channel, and whether it is stored inverted.
_CHANNEL_ROLES = (("metallic", "metallic", False), ("roughness", "roughness", False),
                  ("smoothness", "roughness", True), ("occlusion", "ambientOcclusion", False),
                  ("specular", "specularlevel", False), ("opacity", "opacity", False),
                  ("height", "height", False))

#: The face axes of the generated shader's Face part, in this application's world: the exported
#: mesh differs from what the SDF expects by a half turn about Y, so the .mat's Unity-space
#: (0,0,1) and (1,0,0) are mirrored the way the geometry is.
FACE_FORWARD = [0.0, 0.0, 1.0]
FACE_RIGHT = [-1.0, 0.0, 0.0]

_manifest_cache = {"path": None, "mtime": None, "data": None}


class TextureJob:
    """One source texture decoded into one destination."""

    __slots__ = ("guid", "op", "kind", "target", "source_property")

    #: Bump whenever the bake rules change: the cache is keyed by texture and operation and
    #: reused verbatim unless force_rebuild is on.
    BAKE_VERSION = 5

    def __init__(self, guid, op, kind, target, source_property):
        self.guid = guid
        self.op = op
        self.kind = kind
        self.target = target
        self.source_property = source_property

    def file_stem(self, texture_name):
        """The baked file's name: the texture's own name to read it by, a digest of its WHOLE key to
        tell it apart, the operation and the bake rules. The key is where the build files the texture
        -- archive chain, collection, path id -- so every texture of one archive family shares any
        prefix of it, and a name cut from a prefix gives all of them one file."""
        digest = hashlib.blake2b(self.guid.encode("utf-8"), digest_size=8).hexdigest()
        return "{0}_{1}_{2}_v{3}".format(texture_name, digest, self.op, self.BAKE_VERSION)


class MaterialPlan:
    """What one Texture Set becomes. ``channels`` states how every channel a job fills is stored;
    ``mesh_maps`` names the baked mesh map usage every mesh map job fills."""

    __slots__ = ("name", "key", "shaded", "part", "part_label", "uniforms", "channel_jobs",
                 "param_jobs", "mesh_map_jobs", "channels", "mesh_maps", "warnings")

    def __init__(self, name, key, shaded):
        self.name = name
        self.key = key
        self.shaded = shaded
        self.part = -1
        self.part_label = ""
        self.uniforms = {}
        self.channel_jobs = []
        self.param_jobs = []
        self.mesh_map_jobs = []
        self.channels = {}
        self.mesh_maps = {}
        self.warnings = []


def srgb_to_linear(value):
    """Unity's exact GammaToLinearSpace for a colour channel. Above 1 the engine leaves the sRGB
    piece and uses a plain 2.2 exponent, which is where HDR colours live."""
    if value <= 0.04045:
        return value / 12.92
    if value < 1.0:
        return ((value + 0.055) / 1.055) ** 2.4
    return value ** 2.2


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------
def load_manifest():
    """The generated shader's projection manifest, cached by modification time. None when this
    game ships no generated shader for this application."""
    stack = shader.stack()
    if stack is None:
        return None
    path = stack.manifest_path()
    mtime = os.path.getmtime(path)
    if _manifest_cache["path"] == path and _manifest_cache["mtime"] == mtime:
        return _manifest_cache["data"]
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    unknown = [operation for operation in data.get("operations", []) if operation not in _BAKE_OPS]
    if unknown:
        raise RuntimeError(
            "manifest {0} uses bake operations this plugin does not implement: {1}".format(
                os.path.basename(path), ", ".join(unknown)))
    _manifest_cache.update(path=path, mtime=mtime, data=data)
    return data


def _parts(manifest):
    return (manifest.get("panel") or {}).get("parts", [])


def claimable_shaders():
    """The source shaders the generated shader's part vocabulary claims, as one line."""
    manifest = load_manifest()
    if manifest is None:
        return ""
    return ", ".join(sorted({name for part in _parts(manifest)
                             for name in [part.get("shader", "")] + list(part.get("aliases") or [])
                             if name}))


def _op_for(operation, channels):
    if operation == "unpack_normal_alpha_green":
        return OP_NORMAL_UNITY
    if operation == "unpack_normal_pair":
        return OP_NORMAL_SPLIT_RG if channels == "rg" else OP_NORMAL_SPLIT_BA
    if operation == "invert":
        return OP_INVERT_A if channels == "a" else INVERT + channels
    if operation == "":
        if channels == "rgba":
            return OP_COPY_RGBA
        if channels == "rgb":
            return OP_COPY_RGB
        return EXTRACT + channels
    raise RuntimeError("unmapped bake operation {0!r} on channels {1!r}".format(operation, channels))


def _manifest_plan(manifest, part, name, key, stated, texture_exists):
    """Every manifest input whose source the material binds gets a job, every uniform the panel
    declares takes the material's value, and the part is the one claiming its shader."""
    plan = MaterialPlan(name, key, shaded=True)
    bound = {prop: texture for prop, texture in stated.textures.items()
             if texture and texture_exists(texture)}
    for entry in manifest.get("inputs", []):
        input_id = entry.get("Id", "")
        raw = entry.get("Kind", "RawTexture") == "RawTexture"
        mesh_map = entry.get("MeshMap", "")
        for source in entry.get("Sources", []):
            prop = source.get("Source", "")
            if prop not in bound:
                continue
            job = TextureJob(bound[prop], _op_for(source.get("Operation", ""),
                                                  source.get("Channels", "rgba")),
                             "param" if raw else "mesh_map" if mesh_map else "channel", input_id, prop)
            if raw:
                plan.param_jobs.append(job)
            elif mesh_map:
                plan.mesh_map_jobs.append(job)
                plan.mesh_maps[input_id] = mesh_map
            else:
                plan.channel_jobs.append(job)
                plan.channels[input_id] = ChannelSpec(
                    entry.get("Format", ""), entry.get("Encoding") == "Color",
                    entry.get("Label", "") if entry.get("Kind") == "OverflowChannel" else "")

    # The generated shader's parameter names are the source's property names, so values pass
    # through 1:1; the colour ones take the conversion the engine applies on upload, and WHICH ones
    # those are is the generator's own evaluation (panel.uniforms[].srgb) -- neither the widget nor
    # the property's name can answer it.
    panel = manifest.get("panel") or {}
    declared = {uniform.get("name", "") for uniform in panel.get("uniforms", [])}
    gamma = {uniform.get("name", "") for uniform in panel.get("uniforms", []) if uniform.get("srgb")}
    for prop, value in stated.floats.items():
        if prop in declared:
            plan.uniforms[prop] = srgb_to_linear(float(value)) if prop in gamma else value
    for prop, rgba in stated.colors.items():
        if prop in declared:
            plan.uniforms[prop] = ([srgb_to_linear(float(v)) for v in rgba[:3]] + [float(v) for v in rgba[3:]]
                                   if prop in gamma else list(rgba))
    keywords = set(panel.get("keywords", []))
    for keyword in stated.keywords:
        if keyword in keywords:
            plan.uniforms[keyword] = True
    for prop, st in stated.texture_st.items():
        if prop + "_ST" in declared:
            plan.uniforms[prop + "_ST"] = list(st)
    # This application's own library parameters its native functions must evaluate at for the source's
    # meaning (the baked occlusion at full strength): the generator states them, the panel keeps them live.
    plan.uniforms.update(panel.get("hostParameters") or {})

    plan.part, plan.part_label = part.get("value", 0), part.get("name", "")
    variant = panel.get("variantUniform", "")
    if variant:
        plan.uniforms[variant] = plan.part
    if plan.part_label == "Face":
        if "_FaceForward" in declared:
            plan.uniforms["_FaceForward"] = list(FACE_FORWARD)
        if "_FaceRight" in declared:
            plan.uniforms["_FaceRight"] = list(FACE_RIGHT)
    return plan


def _part_of(manifest, stated):
    """The part claiming this material, or None: of the parts naming its shader, the one whose
    discriminator the material turns on, else the one with none. The material's NAME is not a
    criterion -- a shipped material named like an effect points at the character shader."""
    claimants = [part for part in _parts(manifest) if stated.shader_name and (
        part.get("shader") == stated.shader_name or stated.shader_name in (part.get("aliases") or []))]
    for part in claimants:
        gate = part.get("discriminator") or ""
        if gate and float(stated.floats.get(gate, 0.0) or 0.0) > 0.5:
            return part
    return next((part for part in claimants if not (part.get("discriminator") or "")), None)


# ---------------------------------------------------------------------------
# The roles
# ---------------------------------------------------------------------------
def _role_plan(name, key, stated, texture_exists):
    """The textures the role layers name, split into this application's standard channels; a
    channel two roles would fill keeps the first."""
    plan = MaterialPlan(name, key, shaded=False)
    roles = stated.roles
    for entry in roles.textures if roles is not None else ():
        if not texture_exists(entry.guid):
            continue
        whole = _WHOLE_ROLES.get(entry.role)
        if whole is not None and whole[0] not in plan.channels:
            _add(plan, whole[0], whole[1], entry)
        for role, channel, inverted in _CHANNEL_ROLES:
            index = entry.channels.get(role)
            if index is None or channel in plan.channels:
                continue
            letter = _CHANNEL_LETTERS[index]
            _add(plan, channel, (INVERT if inverted else EXTRACT) + letter, entry)
    return plan


def _add(plan, channel, op, entry):
    plan.channel_jobs.append(TextureJob(entry.guid, op, "channel", channel, entry.name))
    plan.channels[channel] = _STANDARD[channel]


# ---------------------------------------------------------------------------
# The one question
# ---------------------------------------------------------------------------
def plan_for(name, key, stated, texture_exists):
    """One Texture Set's plan: by the generated shader's manifest when its part vocabulary claims
    the material's shader, by the material's roles when it does not."""
    manifest = load_manifest()
    part = _part_of(manifest, stated) if manifest is not None else None
    if part is not None:
        return _manifest_plan(manifest, part, name, key, stated, texture_exists)
    return _role_plan(name, key, stated, texture_exists)
