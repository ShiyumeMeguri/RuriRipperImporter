"""An Unreal package straight into the forms the host's builders already take.

Six datasets say everything an import needs and nothing it does not:

* ``unreal.placements`` -- every scene component one world places, parents before children;
* ``unreal.animations`` -- every sequence in a package, as the clip blob every producer writes;
* ``unreal.mesh.geometry`` -- every LOD of every mesh in one package as raw buffers, already
  in the host's basis, beside the object path of the material each slot names;
* ``unreal.mesh.skeleton`` -- the reference skeleton a skinned mesh's weights index;
* ``unreal.materials`` -- the parameter set every named material resolves to, flattened;
* ``unreal.textures`` -- the pixels of every named texture, in a container the host loads.

None of it is another engine's assets and none of it is text. Buffers reach numpy through
``frombuffer`` -- the bytes the decoder packed ARE the arrays, no copy and no parse -- and the
pixels reach the host as the encoded bytes they arrived in. What comes out the other side is a
``mesh_decoder.DecodedMesh`` and a ``material.MaterialProperties``: the same two forms the
Unity path produces, so the mesh builder and the material builder below them are shared, not
duplicated.
"""

from __future__ import annotations

import numpy as np

from ..unity import clip_curves
from ..unity import material as unity_material
from ..unity import mesh_decoder

ANIMATIONS = "unreal.animations"
PLACEMENTS = "unreal.placements"
MESH_GEOMETRY = "unreal.mesh.geometry"
MESH_SKELETON = "unreal.mesh.skeleton"
MATERIALS = "unreal.materials"
TEXTURES = "unreal.textures"

# Row kinds in the materials table (Ruri.FModelHook.Unreal.UnrealDatasets).
MATERIAL_ROW = "m"
KEYWORD_ROW = "k"
TEXTURE_ROW = "t"
SCALAR_ROW = "f"
VECTOR_ROW = "c"

# One slot's material paths inside a mesh row, joined the way the decoder joins its lists.
SLOT_SEPARATOR = ";"


def mesh_rows(bridge, package):
    """Every LOD of every mesh in one package, newest read each call (the kernel caches)."""
    return bridge.game_data(MESH_GEOMETRY, package=package)


# One vertex's skin as the decoder packs it: four weights beside the four bone slots they
# apply to (AssetRipper.Numerics.BoneWeight4). Read as a structured view, never unpacked.
SKIN_DTYPE = np.dtype([("weights", np.float32, 4), ("indices", np.int32, 4)])


def decoded_mesh(table, row):
    """One row of ``unreal.mesh.geometry`` as a DecodedMesh -- the same form the Unity mesh
    decoder produces, so the host's one mesh builder takes either.

    Every array is a view onto the bytes the decoder packed: float32 positions, normals,
    tangents, colours and coordinates, uint32 indices, and the skin as its own packed record.
    Nothing is parsed and nothing is copied.
    """
    decoded = mesh_decoder.DecodedMesh(table.cell(row, "name"))
    decoded.positions = _floats(table.cell(row, "positions"), 3)
    decoded.normals = _floats(table.cell(row, "normals"), 3)
    decoded.tangents = _floats(table.cell(row, "tangents"), 4)
    decoded.colors = _floats(table.cell(row, "colors"), 4)
    decoded.vertex_count = 0 if decoded.positions is None else len(decoded.positions)
    # Coordinate sets are sparse: the row names the sets it carries, and the buffer holds
    # them in that order, so a set the engine left out stays left out rather than shifting
    # every later set down one index.
    coordinates = _floats(table.cell(row, "uv"), 2)
    sets = _indices(table.cell(row, "uvSets"))
    if coordinates is not None and sets:
        stride = len(coordinates) // len(sets)
        for position, index in enumerate(sets):
            decoded.uvs[index] = coordinates[position * stride:(position + 1) * stride]
    indices = np.frombuffer(table.cell(row, "indices"), dtype=np.uint32)
    decoded.triangles = indices.reshape(-1, 3).astype(np.int32)
    # A section is (first index, index count, material slot): the slot every triangle it
    # covers draws with. Written straight into the per-triangle slot array, which is what
    # the mesh builder assigns to the polygons.
    sections = np.frombuffer(table.cell(row, "sections"), dtype=np.int32).reshape(-1, 3)
    triangle_slots = np.zeros(len(decoded.triangles), dtype=np.int32)
    for first, count, slot in sections:
        triangle_slots[first // 3:(first + count) // 3] = slot
    decoded.tri_material = triangle_slots
    skin = table.cell(row, "skin")
    if skin:
        packed = np.frombuffer(skin, dtype=SKIN_DTYPE)
        decoded.bone_weights = packed["weights"]
        decoded.bone_indices = packed["indices"]
    return decoded


def mesh_bones(table, row):
    """The bone names this mesh's weight indices address, in index order; empty for a mesh
    that is not skinned. The host's skin stage takes a list of bone REFERENCES and a map from
    reference to name; here a bone's name is its own reference, so the map is the identity."""
    joined = table.cell(row, "bones")
    return joined.split(SLOT_SEPARATOR) if joined else []


def skeleton(bridge, package):
    """``{mesh name: [bone, ...]}`` for every skeletal mesh in one package, parents first.

    Each bone is a dict of the fields an armature is built from: ``name``, ``parent`` (the
    index of its parent, -1 at the root), its rest ``position``/``rotation``/``scale`` in the
    host's basis, and the ``path`` a clip addresses it by.
    """
    table = bridge.game_data(MESH_SKELETON, package=package)
    meshes = table.values("mesh")
    names = table.values("bone")
    paths = table.values("path")
    parents = table.values("parent")
    fields = {field: table.values(field) for field in
              ("px", "py", "pz", "qx", "qy", "qz", "qw", "sx", "sy", "sz")}
    built = {}
    for index in range(len(table)):
        built.setdefault(meshes[index], []).append({
            "name": names[index],
            "parent": int(parents[index]),
            "position": (fields["px"][index], fields["py"][index], fields["pz"][index]),
            "rotation": (fields["qx"][index], fields["qy"][index],
                         fields["qz"][index], fields["qw"][index]),
            "scale": (fields["sx"][index], fields["sy"][index], fields["sz"][index]),
            "path": paths[index],
        })
    return built


def _indices(joined):
    """A ';'-joined list of whole numbers, as a list of ints."""
    return [int(part) for part in joined.split(SLOT_SEPARATOR) if part] if joined else []


def animations(bridge, package):
    """``{sequence name: ClipCurves}`` for every animation sequence in one package.

    The decoder writes the same blob the bridge writes for a Unity clip -- one JSON index and one
    float32 payload -- so this is the reader that already exists, not a second one.
    """
    table = bridge.game_data(ANIMATIONS, package=package)
    return {table.cell(index, "name"): clip_curves.ClipCurves.from_blob(
        table.cell(index, "meta"), table.cell(index, "curves"))
        for index in range(len(table))}


def mesh_material_paths(table, row):
    """The object path of the material each of this mesh's slots names, in slot order.
    An empty entry is a slot the mesh leaves unset, which stays an empty Blender slot."""
    joined = table.cell(row, "materials")
    return joined.split(SLOT_SEPARATOR) if joined else []


def material_properties(bridge, paths):
    """``{object path: MaterialProperties}`` for every material path asked for.

    The decoder resolves each material the way the engine does -- the base material's cached
    defaults, each instance overriding by name, then what the compiled base pass proves -- and
    states the result one row per entry. This groups those rows back into the same normalised
    property tables a Unity ``.mat`` parses into, so the host's material builder never learns
    which engine it is looking at.
    """
    wanted = [path for path in dict.fromkeys(paths) if path]
    if not wanted:
        return {}
    table = bridge.game_data(MATERIALS, material=wanted)
    owners = table.values("material")
    kinds = table.values("kind")
    names = table.values("name")
    textures = table.values("texture")
    x = table.values("x")
    y = table.values("y")
    z = table.values("z")
    w = table.values("w")
    built = {}
    for index in range(len(table)):
        owner = owners[index]
        entry = built.get(owner)
        if entry is None:
            entry = built[owner] = unity_material.MaterialProperties(
                name="", document=None, shader_ref=None, tex_envs={}, textures={},
                texture_st={}, floats={}, colors={}, active_keywords=set())
        kind = kinds[index]
        if kind == MATERIAL_ROW:
            entry.name = names[index]
            entry.shader_name = textures[index]
        elif kind == KEYWORD_ROW:
            entry.keywords.add(names[index])
        elif kind == TEXTURE_ROW:
            if textures[index]:
                entry.textures[names[index]] = textures[index]
        elif kind == SCALAR_ROW:
            entry.floats[names[index]] = float(x[index])
        elif kind == VECTOR_ROW:
            entry.colors[names[index]] = [float(x[index]), float(y[index]),
                                          float(z[index]), float(w[index])]
    return built


class UnrealTextureSource:
    """The database surface a host's material builder resolves images through, backed by the
    decoder's own pixels instead of a folder of exported files.

    Same duck-typed shape as ``unity.bridge_asset_db.BridgeAssetDatabase`` where the builder
    touches it -- ``resolve_guid``, ``texture_bytes``, ``asset_path``, ``asset_name``,
    ``texture_is_srgb`` -- keyed by the texture's object path, which is what an Unreal material
    names its textures by. Presence of ``texture_bytes`` is what tells the builder to decode in
    memory rather than load a file, exactly as in bridge mode.
    """

    EXTENSION = ".png"

    def __init__(self, bridge):
        self._bridge = bridge
        self._images = {}
        self._names = {}
        self._srgb = {}

    def request(self, paths):
        """Fetch the pixels of every path not held yet. One call, however many textures:
        a texture is read from its archive once and decoded on every core."""
        missing = [path for path in dict.fromkeys(paths)
                   if path and path not in self._images]
        if not missing:
            return
        table = self._bridge.game_data(TEXTURES, texture=missing)
        paths_read = table.values("texture")
        names = table.values("name")
        srgb = table.values("srgb")
        for index in range(len(table)):
            path = paths_read[index]
            self._images[path] = table.cell(index, "image")
            self._names[path] = names[index]
            self._srgb[path] = srgb[index] == "1"
        # A path the decoder answered nothing for is held as absent on purpose: asking again
        # every time a material mentions it would re-read the archive for a texture that
        # is not there.
        for path in missing:
            self._images.setdefault(path, None)

    def resolve_guid(self, key):
        return key if key and self._images.get(key) else None

    def texture_bytes(self, key):
        return self._images.get(key) if key else None

    def texture_is_srgb(self, key):
        return self._srgb.get(key) if key else None

    def asset_path(self, key):
        name = self._names.get(key) if key else None
        return None if name is None else name + self.EXTENSION

    def asset_name(self, key):
        return self._names.get(key) if key else None

    def _text(self, key):
        """No Unreal asset is Unity text. The material builder asks this only to read a
        Shader asset's name, which an Unreal material states outright (MaterialProperties
        .shader_name), so nothing here needs to answer."""
        return None


def _floats(blob, width):
    """A packed float32 buffer as an (n, width) view, or None when the column is empty."""
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32).reshape(-1, width)
