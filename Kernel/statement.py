"""What a selection IS, read back as typed arrays.

The kernel answers every selection -- a cabmap row, a roster entry, a scene
window, an NPC recipe, an assembly plan, a level, a single mesh -- with ONE set
of tables, already in the basis the host asked for. This is the only module that
turns those bytes into arrays, and it is deliberately the only one: a second
place that knew a blob's stride would be a second place to keep in step with the
kernel that writes it.

Nothing here decides anything. There is no hierarchy walk, no skinning, no
coordinate conversion, no material vocabulary and no per-game branch -- those all
happened on the other side, where they are one implementation over the buffers
the decoder built rather than a python loop per asset. What a host does with the
answer is the host's; what the answer IS, is here.

A statement is asked for by SEED. A seed is the payload of any published row: an
archive or container path of the loaded map is the engine's own flattening, and
anything else is explained by the hook that published it. A panel therefore never
spells a seed -- it hands over the payload of the row the user picked.
"""

from __future__ import annotations

import json

import numpy as np

from .bridge import session

ROOTS = "core.statement.roots"
NODES = "core.statement.nodes"
MESHES = "core.statement.meshes"
MORPHS = "core.statement.morphs"
SKELETONS = "core.statement.skeletons"
AVATARS = "core.statement.avatars"
MATERIALS = "core.statement.materials"
TEXTURES = "core.statement.textures"
TEXTURE = "core.statement.texture"
CLIPS = "core.statement.clips"
SOURCE_SKELETON = "core.clip.source_skeleton"
REPORT = "core.statement.report"

#: Basis names the kernel converts geometry and transforms into. A host states
#: which of them it IS; nothing on this side converts anything.
UNITY = "unity"
BLENDER = "blender"
GLTF = "gltf"

SEPARATOR = ";"

#: Node kinds, as the flattener words them.
EMPTY = "empty"
MESH = "mesh"
SKINNED = "skinned"
LIGHT = "light"
CAMERA = "camera"

#: How a renderer draws into shadow maps -- Unity's own ShadowCastingMode values --
#: and the value of a node the source states nothing about.
SHADOWS_UNSTATED = -1
SHADOWS_OFF = 0
SHADOWS_ON = 1
SHADOWS_TWO_SIDED = 2
SHADOWS_ONLY = 3

#: Material row kinds.
_MATERIAL = "m"
_KEYWORD = "k"
_PASS = "p"
_SHADER_PASS = "s"
_TEXTURE = "t"
_SCALAR = "f"
_VECTOR = "c"
_ROLE = "r"
_UNCLAIMED = "u"
_ENCODING = "n"

#: The pass a renderer draws into a light's shadow map with (the engine's word, compared case-blind).
SHADOW_CASTER_PASS = "shadowcaster"


BASES = "core.bases"

_BASES = {}


def basis(name):
    """What one basis IS, as the kernel declares it: ``{"reverses_winding",
    "flip_v", "matrix", "root", "aim"}`` with every matrix as a row-major 4x4
    array.

    ``aim`` is the LOCAL turn a camera or a light takes to point the way this
    basis points them -- the one thing about a converted transform that is not
    the conversion, and the reason it is read here rather than written as a
    quarter turn about an axis somebody has to remember.

    Read rather than restated. A host whose own maths runs in the ENGINE's basis
    -- an animation curve is measured there, so a rest pose has to be subtracted
    there -- still converts its answer at the end, and a second copy of these
    numbers on this side would be a second definition of what a basis is."""
    if name not in _BASES:
        table = session.table(BASES)
        try:
            for index in range(table.row_count):
                row = table.row(index)
                _BASES[row["basis"]] = {
                    "reverses_winding": bool(row["reverses_winding"]),
                    "flip_v": bool(row["flip_v"]),
                    "matrix": np.array([row["m{0}".format(element)] for element in range(16)],
                                       dtype=np.float64).reshape(4, 4),
                    "root": np.array([row["r{0}".format(element)] for element in range(16)],
                                     dtype=np.float64).reshape(4, 4),
                    "aim": np.array([row["a{0}".format(element)] for element in range(16)],
                                    dtype=np.float64).reshape(4, 4),
                }
        finally:
            table.close()
    return _BASES[name]


class Built:
    """What materialising one statement produced, and what it could not."""

    __slots__ = ("rig", "objects", "missing", "warnings", "imported", "performances")

    def __init__(self, rig=None, objects=(), missing=(), warnings=(), imported=0,
                 performances=0):
        #: The skeleton the parts were bound to, on a host that has rigs.
        self.rig = rig
        self.objects = list(objects)
        self.missing = list(missing)
        self.warnings = list(warnings)
        self.imported = imported
        #: How many performances the same selection stated and this host played.
        #: Counted apart from the objects because a selection can state one and
        #: place nothing -- an animation row IS that case.
        self.performances = performances

    def summary(self):
        return "{0} object(s), {1} performance(s), {2} missing, {3} warning(s)".format(
            self.imported, self.performances, len(self.missing), len(self.warnings))


def _floats(raw, width):
    if not raw:
        return np.zeros((0, width), dtype=np.float32)
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, width)


class Node:
    """One transform of one seed: what sits on it, where it sits, what it draws."""

    __slots__ = ("index", "parent", "name", "path", "kind", "active", "mesh",
                 "skeleton", "materials", "anchor", "position", "rotation", "scale",
                 "light", "camera", "tag", "shadows", "main_light_shadows", "two_sided_shadows", "object_parameters",
                 "collection")

    def __init__(self, row):
        self.index = int(row["node"])
        self.parent = int(row["parent"])
        self.name = row["name"]
        self.path = row["path"]
        self.kind = row["kind"]
        self.active = bool(row["active"])
        self.mesh = row["mesh"]
        self.skeleton = row["skeleton"]
        self.materials = [entry for entry in row["materials"].split(SEPARATOR) if entry]
        #: The bone of the statement's own skeleton this node hangs under, when
        #: the flattening states one (an assembly piece's anchor).
        self.anchor = row["anchor"]
        self.position = (row["px"], row["py"], row["pz"])
        self.rotation = (row["qx"], row["qy"], row["qz"], row["qw"])
        self.scale = (row["sx"], row["sy"], row["sz"])
        #: A light's colour is linear (the reader decodes what the source keeps sRGB-encoded) and
        #: its intensity the source's own; angles are full cone angles in degrees. ``fade`` is the
        #: source's camera-distance fade as four coefficients, ``parameters`` what its light list
        #: carries beyond geometry and radiance, one row of four per vector, in the order the shading
        #: stack that reads them declares.
        self.light = (None if row["light_kind"] < 0 else {
            "kind": int(row["light_kind"]),
            "color": (row["light_r"], row["light_g"], row["light_b"]),
            "intensity": row["light_intensity"], "range": row["light_range"],
            "angle": row["light_angle"], "inner_angle": row["light_inner_angle"],
            "width": row["light_width"], "height": row["light_height"],
            "shadows": bool(row["light_shadows"]), "shadow_resolution": int(row["light_shadow_resolution"]),
            "volume": row["light_volume"], "specular": row["light_specular"],
            "fade": _floats(row["light_fade"], 4).reshape(-1),
            "parameters": _floats(row["light_parameters"], 4)})
        self.camera = (None if row["ortho"] < 0 else {
            "fov": row["fov"], "near": row["near"], "far": row["far"],
            "orthographic": bool(row["ortho"]), "ortho_size": row["ortho_size"]})
        self.tag = row["tag"]
        #: How the node's renderer draws into shadow maps (SHADOWS_*): the renderer's
        #: fact, not its mesh's. SHADOWS_UNSTATED where the source states nothing.
        self.shadows = int(row["cast_shadows"])
        #: Whether a casting renderer's shadow falls in the directional light's
        #: cascades; a streamed renderer can cast for local lights only.
        self.main_light_shadows = bool(row["main_light_shadows"])
        #: Whether both faces of the renderer cast, whatever its material's shadow culling says.
        self.two_sided_shadows = bool(row["two_sided_shadows"])
        #: What the pipeline binds for this renderer's own draw beyond its materials:
        #: {name: (x, y, z, w)}, each under the name the shading stacks read it by.
        names = [entry for entry in row["object_parameter_names"].split(SEPARATOR) if entry]
        values = _floats(row["object_parameters"], 4)
        self.object_parameters = {name: tuple(float(component) for component in values[index])
                                  for index, name in enumerate(names)}
        #: The collection this node is gathered under apart from the rest of the import (the
        #: pipeline's own draws, by the shader that draws them), or empty for none.
        self.collection = row["collection"]

    @property
    def renders(self):
        return self.kind in (MESH, SKINNED) and bool(self.mesh)

    def __repr__(self):
        return "<Node {0} {1}>".format(self.index, self.path)


class Mesh:
    """One mesh as raw arrays, already in the requested basis."""

    __slots__ = ("key", "name", "positions", "normals", "tangents", "colors",
                 "uvs", "triangles", "sections", "weights", "bone_indices",
                 "bone_paths", "bindposes", "skeleton", "lod", "baked")

    def __init__(self, row):
        self.key = row["key"]
        self.name = row["name"]
        self.positions = _floats(row["positions"], 3)
        normals = _floats(row["normals"], 3)
        self.normals = normals if len(normals) else None
        tangents = _floats(row["tangents"], 4)
        self.tangents = tangents if len(tangents) else None
        colors = _floats(row["colors"], 4)
        self.colors = colors if len(colors) else None
        self.triangles = (np.frombuffer(row["indices"], dtype=np.uint32).reshape(-1, 3)
                          if row["indices"] else np.zeros((0, 3), dtype=np.uint32))
        #: ``(first index, index count, material slot)`` per run of triangles
        #: drawing one slot -- the order a material slot list is filled in.
        self.sections = (np.frombuffer(row["sections"], dtype=np.int32).reshape(-1, 3)
                         if row["sections"] else np.zeros((0, 3), dtype=np.int32))
        self.uvs = self._uv_sets(row)
        self.weights, self.bone_indices = self._skin(row)
        self.bone_paths = [entry for entry in row["bones"].split(SEPARATOR) if entry]
        count = len(self.bone_paths)
        self.bindposes = (np.frombuffer(row["bindposes"], dtype=np.float32)
                          .reshape(-1, 4, 4) if row["bindposes"] and count
                          else np.zeros((0, 4, 4), dtype=np.float32))
        self.skeleton = row["skeleton"]
        self.lod = int(row["lod"])
        #: Whether the geometry arrived already baked to the skeleton's rest.
        self.baked = bool(row["baked"])

    def _uv_sets(self, row):
        sets = [entry for entry in row["uvSets"].split(SEPARATOR) if entry]
        flat = _floats(row["uv"], 2)
        stride = len(self.positions)
        return {int(name): flat[index * stride:(index + 1) * stride]
                for index, name in enumerate(sets)} if stride else {}

    def _skin(self, row):
        """Four influences a vertex: weights then bone slots, one 32-byte record
        per vertex. One stride, stated once by the kernel that packs it."""
        raw = row["skin"]
        if not raw:
            return None, None
        packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 32)
        return (packed[:, :16].copy().view(np.float32),
                packed[:, 16:].copy().view(np.int32))

    @property
    def slots(self):
        """The material slots this mesh draws, in the order it draws them."""
        seen = []
        for _first, _count, slot in self.sections.tolist():
            if slot not in seen:
                seen.append(slot)
        return seen

    def __repr__(self):
        return "<Mesh {0} ({1} verts)>".format(self.name, len(self.positions))


class Morph:
    """One blend shape of one mesh, sparse."""

    __slots__ = ("mesh", "name", "vertices", "positions", "normals", "tangents",
                 "weight", "frames")

    def __init__(self, row):
        self.mesh = row["mesh"]
        self.name = row["name"]
        self.vertices = (np.frombuffer(row["vertices"], dtype=np.uint32)
                         if row["vertices"] else np.zeros(0, dtype=np.uint32))
        self.positions = _floats(row["delta_positions"], 3)
        self.normals = _floats(row["delta_normals"], 3)
        self.tangents = _floats(row["delta_tangents"], 3)
        self.weight = row["weight"]
        self.frames = int(row["frames"])

    def __repr__(self):
        return "<Morph {0}.{1}>".format(self.mesh, self.name)


class Bone:
    __slots__ = ("index", "parent", "name", "path", "identity", "position",
                 "rotation", "scale", "humanoid")

    def __init__(self, row):
        self.index = int(row["bone"])
        self.parent = int(row["parent"])
        self.name = row["name"]
        self.path = row["path"]
        #: What a rig carries for this bone -- the engine's own transform path,
        #: which is what a later session reads the rig's identity back off.
        self.identity = row["identity"]
        self.position = (row["px"], row["py"], row["pz"])
        self.rotation = (row["qx"], row["qy"], row["qz"], row["qw"])
        self.scale = (row["sx"], row["sy"], row["sz"])
        self.humanoid = row["humanoid"]

    def __repr__(self):
        return "<Bone {0}>".format(self.path)


class Skeleton:
    __slots__ = ("key", "bones", "avatar")

    def __init__(self, key, bones, avatar=""):
        self.key = key
        self.bones = bones
        #: The avatar this skeleton was built with, in the form the solver reads
        #: back -- what a host stamps on the rig and hands to a clip request.
        self.avatar = avatar

    def paths(self):
        return [bone.path for bone in self.bones]

    def __len__(self):
        return len(self.bones)

    def __repr__(self):
        return "<Skeleton {0} ({1} bones)>".format(self.key, len(self.bones))


class TextureRole:
    """One texture a role layer named, and what it was named as."""

    __slots__ = ("name", "guid", "role", "channels", "encoding")

    def __init__(self, name, guid, encoding=""):
        #: The material's own property name, and the texture key behind it. The
        #: second is called ``guid`` because that is what every consumer of a
        #: texture reference calls it, generated shader stacks included.
        self.name = name
        self.guid = guid
        self.role = None
        self.channels = {}
        self.encoding = encoding

    def __repr__(self):
        return "<TextureRole {0}={1}>".format(self.name, self.role or self.channels)


class _Values:
    """The value roles, read either as a scalar or as four components.

    ONE row carries both readings -- a role's value is four numbers and a scalar
    role simply left three of them zero -- so there is nothing here that has to
    know which roles are colours. The caller knows what it is asking for."""

    __slots__ = ("_rows", "_width")

    def __init__(self, rows, width):
        self._rows = rows
        self._width = width

    def get(self, role, default=None):
        found = self._rows.get(role)
        if found is None:
            return default
        return found[0] if self._width == 1 else tuple(found)

    def __contains__(self, role):
        return role in self._rows

    def __iter__(self):
        return iter(self._rows)

    def keys(self):
        return self._rows.keys()


class Roles:
    """What each of a material's textures and numbers was resolved to mean."""

    __slots__ = ("textures", "colors", "floats", "unmapped")

    def __init__(self, textures, values, unmapped):
        self.textures = textures
        self.colors = _Values(values, 4)
        self.floats = _Values(values, 1)
        #: Property names no layer of the role table states.
        self.unmapped = unmapped

    def first(self, role):
        """The texture carrying ``role`` whole, or None."""
        return next((entry for entry in self.textures if entry.role == role), None)

    def with_channel(self, role):
        """``(texture, channel)`` for a role stored in one channel, or
        ``(None, -1)``."""
        for entry in self.textures:
            if role in entry.channels:
                return entry, entry.channels[role]
        return None, -1

    def packed(self):
        """Every texture whose CHANNELS carry roles, in table order."""
        return [entry for entry in self.textures if entry.channels]

    @property
    def transparent(self):
        """Whether it blends over what is behind it: a blend mode past cutout, or a
        transparent surface type. Stating neither is opaque, as every pipeline has it."""
        return (self.floats.get("blend_mode", 0.0) >= 1.5
                or self.floats.get("surface_type", 0.0) >= 0.5)

    @property
    def clipped(self):
        """Whether it alpha-tests: the cutout blend mode, or an alpha clip switched on."""
        mode = self.floats.get("blend_mode", 0.0)
        return 0.5 <= mode < 1.5 or self.floats.get("alpha_clip", 0.0) >= 0.5


class Material:
    """One material: what it declares, and what those declarations mean.

    The declaration half is spelled exactly as a generated shader stack reads it
    (``name``/``shader_name``/``textures``/``texture_st``/``floats``/``colors``/
    ``keywords``/``disabled_passes``), because a stack is handed this object
    itself. The meaning half is :attr:`roles`, resolved against the layered role
    table on the kernel side."""

    __slots__ = ("key", "name", "shader_name", "textures", "texture_st", "floats",
                 "colors", "keywords", "disabled_passes", "shader_passes", "roles")

    def __init__(self, key):
        self.key = key
        self.name = ""
        self.shader_name = ""
        self.textures = {}
        self.texture_st = {}
        self.floats = {}
        self.colors = {}
        self.keywords = []
        self.disabled_passes = []
        #: ``(name, light_mode)`` per pass its shader draws with; empty for an engine
        #: whose materials have no shader passes to state.
        self.shader_passes = []
        self.roles = None

    @property
    def casts_shadow(self):
        """Whether its shader draws a shadow-caster pass this material leaves on. The
        engine finds a pass by its LightMode tag, or by its name when untagged, and a
        material disables one by that same word, case-blind. None when no passes are
        stated: the engine behind it has none, and the host keeps its own default."""
        if not self.shader_passes:
            return None
        disabled = {name.lower() for name in self.disabled_passes}
        return any((light_mode or name).lower() == SHADOW_CASTER_PASS
                   and (light_mode or name).lower() not in disabled
                   for name, light_mode in self.shader_passes)

    @property
    def shader_ref(self):
        """The reference form a consumer that only ever read a reference asks
        for. The statement carries the shader by NAME -- which is the identity a
        stack claims by -- so there is no address to hand back."""
        return {}

    def __repr__(self):
        return "<Material {0} ({1})>".format(self.name, self.shader_name)


class Texture:
    """One texture of a selection: what it is, and its pixels ON DEMAND.

    The bytes are fetched per texture rather than carried in the table beside
    every other texture's: one selection's images run to gigabytes, which is more
    than a single column can hold at all, and a host loads them one at a time.
    Nothing is remembered here -- an image lands in the document and the bytes
    are done -- so a scene costs one image resident rather than all of them."""

    __slots__ = ("key", "name", "srgb", "container", "bytes", "wrap_u", "wrap_v", "filter", "_statement")

    def __init__(self, row, statement):
        self.key = row["texture"]
        self.name = row["name"]
        #: Whether the ASSET ITSELF declares sRGB encoding -- a fact about the
        #: texture, never about the slot it happens to be bound in.
        self.srgb = bool(row["srgb"])
        #: The texture's OWN sampler state -- wrap along u and v (repeat / clamp /
        #: mirror / mirroronce) and filter (point / bilinear / trilinear, or default
        #: where the engine leaves it to a group setting): what a shader that samples
        #: it through its own sampler gets.
        self.wrap_u = row["wrap_u"]
        self.wrap_v = row["wrap_v"]
        self.filter = row["filter"]
        self.container = row["container"]
        #: How many bytes its pixels are, stated without fetching them.
        self.bytes = int(row["bytes"])
        self._statement = statement

    @property
    def image(self):
        return self._statement.payload(TEXTURE, texture=self.key)

    def __repr__(self):
        return "<Texture {0} ({1} bytes)>".format(self.name, self.bytes)


#: How many components one kind of curve carries. Stated by the kernel that packs
#: the payload; this is the reader's half of that one statement.
_CURVE_DIMENSIONS = {"rot": 4, "pos": 3, "scale": 3, "euler": 3}


class Channel:
    """One curve of one clip: what it drives, and its keys.

    Times, values and both tangents, each key-major across components -- the
    layout every clip producer writes, so a repaired clip reads exactly like a
    raw one."""

    __slots__ = ("kind", "path", "attribute", "class_id", "times", "values",
                 "in_slopes", "out_slopes")

    def __init__(self, entry, floats):
        self.kind = entry["kind"]
        self.path = entry["path"]
        self.attribute = entry.get("attr") or ""
        self.class_id = int(entry.get("classId") or 0)
        keys = int(entry["keys"])
        width = _CURVE_DIMENSIONS.get(self.kind, 1)
        start = int(entry["off"])
        self.times = floats[start:start + keys]
        block = keys * width
        values = start + keys
        self.values = floats[values:values + block].reshape(keys, width)
        self.in_slopes = floats[values + block:values + 2 * block].reshape(keys, width)
        self.out_slopes = floats[values + 2 * block:values + 3 * block].reshape(keys, width)

    @property
    def duration(self):
        return float(self.times[-1]) if len(self.times) else 0.0

    def sample(self, times):
        """The curve at each of ``times``, cubic-Hermite between its own keys and
        clamped to the first and last outside them -- which is how the engine
        that authored it evaluates its own curves."""
        keys = len(self.times)
        if keys == 0:
            return np.zeros((len(times), self.values.shape[1] if self.values.size else 1))
        if keys == 1:
            return np.tile(self.values[0], (len(times), 1))
        wanted = np.clip(np.asarray(times, dtype=np.float64),
                         self.times[0], self.times[-1])
        right = np.clip(np.searchsorted(self.times, wanted, side="right"), 1, keys - 1)
        left = right - 1
        span = (self.times[right] - self.times[left]).astype(np.float64)
        span = np.where(span <= 0.0, 1.0, span)
        t = ((wanted - self.times[left]) / span)[:, None]
        span = span[:, None]
        t2 = t * t
        t3 = t2 * t
        return ((2 * t3 - 3 * t2 + 1) * self.values[left]
                + (t3 - 2 * t2 + t) * span * self.out_slopes[left]
                + (-2 * t3 + 3 * t2) * self.values[right]
                + (t3 - t2) * span * self.in_slopes[right])

    def __repr__(self):
        return "<Channel {0} {1}>".format(self.kind, self.path)


class Clip:
    __slots__ = ("key", "name", "archive", "meta", "curves", "_channels")

    def __init__(self, row):
        self.key = row["clip"]
        self.name = row["name"]
        #: The archive the clip came out of, as the reader states it.
        self.archive = row["cab"]
        self.meta = json.loads(row["meta"]) if row["meta"] else {}
        self.curves = row["curves"]
        self._channels = None

    @property
    def sample_rate(self):
        return float(self.meta.get("sampleRate") or 0.0)

    @property
    def channels(self):
        if self._channels is None:
            floats = (np.frombuffer(self.curves, dtype=np.float32) if self.curves
                      else np.zeros(0, dtype=np.float32))
            self._channels = [Channel(entry, floats)
                              for entry in self.meta.get("curves") or ()]
        return self._channels

    def of_kind(self, kind):
        return [channel for channel in self.channels if channel.kind == kind]

    @property
    def bindings(self):
        return sorted({channel.path for channel in self.channels
                       if channel.kind in _CURVE_DIMENSIONS and channel.path})

    @property
    def duration(self):
        return max((channel.duration for channel in self.channels), default=0.0)

    def __repr__(self):
        return "<Clip {0}>".format(self.name)


def source_host(paths, near=""):
    """The seed of the prefab rooted on the skeleton whose transform table covers these clip bindings
    best, or "" when none does. ``near`` is the archive the clips came from."""
    arguments = {"paths": list(paths)}
    if near:
        arguments["clip"] = near
    table = session.table(SOURCE_SKELETON, **arguments)
    try:
        return table.cell(0, "host_seed") if table.row_count else ""
    finally:
        table.close()


class Report:
    __slots__ = ("seed", "what", "count", "detail")

    def __init__(self, row):
        self.seed = row["seed"]
        self.what = row["what"]
        self.count = int(row["count"])
        self.detail = row["detail"]

    def __repr__(self):
        return "<Report {0} x{1}>".format(self.what, self.count)


class Root:
    __slots__ = ("seed", "node", "label", "kind", "position", "rotation", "scale")

    def __init__(self, row):
        self.seed = row["seed"]
        self.node = int(row["node"])
        self.label = row["label"]
        self.kind = row["kind"]
        self.position = (row["px"], row["py"], row["pz"])
        self.rotation = (row["qx"], row["qy"], row["qz"], row["qw"])
        self.scale = (row["sx"], row["sy"], row["sz"])

    def __repr__(self):
        return "<Root {0} ({1})>".format(self.label, self.kind)


class Statement:
    """One flattening, as the tables a host reads.

    Each table is asked for the first time something reads it: a host building
    geometry only never pays for the texture bytes, and a host that draws a
    report never decodes a mesh. The kernel keeps ONE flattening behind these, so
    asking for a second table of the same request costs the read and nothing
    else.

    That flattening -- the selection's meshes and every one of its images -- is
    this statement's to let go: :meth:`close` (or leaving a ``with`` block, or the
    statement being dropped) tells the reader it will not be asked about again.
    Left to the next load to replace, it stayed resident for as long as the session
    sat idle after the last one. The same selection in another basis
    (:meth:`in_basis`) reads the very same flattening, so it holds on to this one
    and lets go through it rather than on its own."""

    __slots__ = ("_arguments", "_cache", "_owner", "_closed")

    def __init__(self, arguments, owner=None):
        self._arguments = arguments
        self._cache = {}
        self._owner = owner
        self._closed = False

    def close(self):
        """Done with this selection: drop what was read here and, unless this is the same
        selection in another basis, let the reader drop the flattening."""
        if self._closed:
            return
        self._closed = True
        self._cache = {}
        if self._owner is None:
            session.release_statement(**self._arguments)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @classmethod
    def of(cls, seeds, basis=UNITY, detail=0, inactive=True, shadow_proxies=False,
           roles=(), containers=()):
        """Ask what these seeds are.

        ``detail`` is the level to keep and -1 every level; ``inactive`` keeps
        renderers the game has switched off; ``shadow_proxies`` keeps
        renderers that draw only into the shadow map; ``roles`` are texture-role
        layer files, later overriding earlier; ``containers`` are the image
        containers this host loads directly."""
        seeds = [str(seed) for seed in (seeds if isinstance(seeds, (list, tuple, set))
                                        else [seeds]) if str(seed)]
        if not seeds:
            raise ValueError("a statement needs at least one seed")
        return cls({"seed": seeds, "basis": basis, "detail": detail,
                    "inactive": inactive, "shadow_proxies": shadow_proxies,
                    "roles": list(roles), "containers": list(containers)})

    @property
    def arguments(self):
        return dict(self._arguments)

    @property
    def seeds(self):
        return list(self._arguments["seed"])

    @property
    def basis(self):
        return self._arguments["basis"]

    def _rows(self, dataset_id, build):
        cached = self._cache.get(dataset_id)
        if cached is None:
            table = session.table(dataset_id, **self._arguments)
            try:
                cached = build(table)
            finally:
                table.close()
            self._cache[dataset_id] = cached
        return cached

    # -- the tables ---------------------------------------------------------
    @property
    def roots(self):
        return self._rows(ROOTS, lambda table: [
            Root(table.row(index)) for index in range(table.row_count)])

    @property
    def nodes(self):
        return self._rows(NODES, lambda table: [
            Node(table.row(index)) for index in range(table.row_count)])

    @property
    def meshes(self):
        return self._rows(MESHES, lambda table: {
            table.cell(index, "key"): Mesh(table.row(index))
            for index in range(table.row_count)})

    @property
    def morphs(self):
        """Blend shapes by mesh key, in the order the mesh states them."""
        def build(table):
            found = {}
            for index in range(table.row_count):
                morph = Morph(table.row(index))
                found.setdefault(morph.mesh, []).append(morph)
            return found
        return self._rows(MORPHS, build)

    @property
    def skeletons(self):
        def build(table):
            found = {}
            for index in range(table.row_count):
                row = table.row(index)
                found.setdefault(row["skeleton"], []).append(Bone(row))
            avatars = self._avatars()
            return {key: Skeleton(key, bones, avatars.get(key, ""))
                    for key, bones in found.items()}
        return self._rows(SKELETONS, build)

    def _avatars(self):
        return self._rows(AVATARS, lambda table: {
            table.cell(index, "skeleton"): table.cell(index, "avatar")
            for index in range(table.row_count)})

    @property
    def materials(self):
        return self._rows(MATERIALS, _materials)

    @property
    def textures(self):
        return self._rows(TEXTURES, lambda table: {
            table.cell(index, "texture"): Texture(table.row(index), self)
            for index in range(table.row_count)})

    @property
    def report(self):
        return self._rows(REPORT, lambda table: [
            Report(table.row(index)) for index in range(table.row_count)])

    def clips(self, *, paths, avatar):
        """The clips these seeds carry, as the target they play on reads them. THERE IS NO
        READING A CLIP ON ITS OWN: READ THE TARGET'S SKELETON FIRST, THEN ITS CLIPS.

        A clip stores every binding as the CRC32 of a bone path, and names a bone only against
        a skeleton: without the target's ``paths`` every bone curve would be a
        ``path_0x<crc>_`` placeholder, which matches no bone anywhere and reads exactly like a
        clip that animates nothing -- a face measured as absent, a body that does not move. So
        both arguments are required here, and the reader refuses a clip request without them.

        ``paths`` are the target's object hierarchy -- the reversible strings Unity itself
        hashes once to bind a clip -- onto which every curve is re-anchored. ``avatar`` is the
        avatar that hierarchy was built with, against which a muscle-encoded clip is solved into
        bone curves. A clip request is not cached with the rest: the same seeds are asked for
        different targets."""
        arguments = dict(self._arguments)
        arguments.update({"paths": list(paths), "avatar": avatar})
        table = session.table(CLIPS, **arguments)
        try:
            return [Clip(table.row(index)) for index in range(table.row_count)]
        finally:
            table.close()

    def payload(self, dataset_id, **arguments):
        """A BLOB dataset asked about THIS selection.

        The seeds and every reading option go with the question, so a host that
        wants one more answer about what it is already building never restates
        them -- and cannot restate them differently, which is how a bake ended up
        flattening nothing at all."""
        merged = dict(self._arguments)
        merged.update(arguments)
        return session.blob(dataset_id, **merged)

    def in_basis(self, basis):
        """The same selection, read out in another basis.

        A basis converts the TABLES a flattening is read out of and nothing that
        is flattened, so this costs one more table build and never a second read
        of the selection. What asks for it is a host that has to state one thing
        in the basis its animation data is written in while building everything
        else in its own."""
        if basis == self._arguments["basis"]:
            return self
        arguments = dict(self._arguments)
        arguments["basis"] = basis
        return Statement(arguments, owner=self._owner or self)

    # -- what a host asks while it builds -----------------------------------
    def children_of(self, node_index):
        return [node for node in self.nodes if node.parent == node_index]

    def skeleton_of(self, node):
        return self.skeletons.get(node.skeleton) if node.skeleton else None

    def __repr__(self):
        return "<Statement {0} seed(s) basis={1}>".format(
            len(self._arguments["seed"]), self._arguments["basis"])


def _materials(table):
    """The material rows folded back into one record per material."""
    found = {}
    textures = {}
    encodings = {}
    values = {}
    unmapped = {}
    for index in range(table.row_count):
        row = table.row(index)
        key = row["material"]
        material = found.get(key)
        if material is None:
            material = found[key] = Material(key)
            textures[key] = []
            encodings[key] = {}
            values[key] = {}
            unmapped[key] = []
        kind = row["kind"]
        name = row["name"]
        if kind == _MATERIAL:
            material.name = name
            material.shader_name = row["texture"]
        elif kind == _KEYWORD:
            material.keywords.append(name)
        elif kind == _PASS:
            material.disabled_passes.append(name)
        elif kind == _SHADER_PASS:
            material.shader_passes.append((name, row["texture"]))
        elif kind == _TEXTURE:
            material.textures[name] = row["texture"]
            material.texture_st[name] = [row["x"], row["y"], row["z"], row["w"]]
        elif kind == _SCALAR:
            material.floats[name] = row["x"]
        elif kind == _VECTOR:
            material.colors[name] = [row["x"], row["y"], row["z"], row["w"]]
        elif kind == _ENCODING:
            encodings[key][row["texture"]] = name
        elif kind == _ROLE:
            texture = row["texture"]
            if not texture:
                values[key][name] = [row["x"], row["y"], row["z"], row["w"]]
                continue
            entry = next((one for one in textures[key] if one.guid == texture), None)
            if entry is None:
                entry = TextureRole(_property_of(material, texture), texture)
                textures[key].append(entry)
            if row["x"] < 0:
                entry.role = name
            else:
                entry.channels[name] = int(row["x"])
        elif kind == _UNCLAIMED:
            unmapped[key].append(name)
    for key, material in found.items():
        for entry in textures[key]:
            entry.encoding = encodings[key].get(entry.guid, "")
        material.roles = Roles(textures[key], values[key], unmapped[key])
    return found


def _property_of(material, texture):
    """The material's own property name for a texture key -- what a node label
    and an unmapped report are worded with."""
    for name, key in material.textures.items():
        if key == texture:
            return name
    return texture
