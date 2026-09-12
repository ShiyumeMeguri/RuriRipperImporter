"""A source that is not Unity, straight into the forms the host's builders already take.

A decoder for an engine the host does not read natively publishes the same five things,
whatever engine it is: what a thing PLACES, the buffers of every mesh those placements
draw, the reference skeleton a skinned one indexes, the parameters of every material and
the pixels of every texture. The COLUMNS are one contract; only the dataset ids differ.

So the reading lives here once and each source binds its own ids to it (see
``unreal.direct`` and ``sims4.direct``). Buffers reach numpy through ``frombuffer`` -- the
bytes the decoder packed ARE the arrays, no copy and no parse -- and the pixels reach the
host as the encoded bytes they arrived in. What comes out is a ``mesh_decoder.DecodedMesh``
and a ``material.MaterialProperties``: the same two forms the Unity path produces, so the
mesh builder and the material builder below them are shared, not duplicated.
"""

from __future__ import annotations

import numpy as np

from .unity import material as unity_material
from .unity import mesh_decoder

# Row kinds in a materials table.
MATERIAL_ROW = "m"
KEYWORD_ROW = "k"
TEXTURE_ROW = "t"
SCALAR_ROW = "f"
VECTOR_ROW = "c"

#: One slot's paths inside a row, joined the way a decoder joins its lists.
SLOT_SEPARATOR = ";"

# One vertex's skin as a decoder packs it: four weights beside the four bone slots they
# apply to (AssetRipper.Numerics.BoneWeight4). Read as a structured view, never unpacked.
SKIN_DTYPE = np.dtype([("weights", np.float32, 4), ("indices", np.int32, 4)])


def decoded_mesh(table, row):
    """One row of a mesh-geometry table as a DecodedMesh -- the same form the Unity mesh
    decoder produces, so the host's one mesh builder takes either.

    Every array is a view onto the bytes the decoder packed: float32 positions, normals,
    tangents, colours and coordinates, uint32 indices, and the skin as its own packed
    record. Nothing is parsed and nothing is copied.
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


def mesh_material_paths(table, row):
    """The path of the material each of this mesh's slots names, in slot order.
    An empty entry is a slot the mesh leaves unset, which stays an empty host slot."""
    joined = table.cell(row, "materials")
    return joined.split(SLOT_SEPARATOR) if joined else []


def skeleton_rows(table):
    """``{mesh name: [bone, ...]}`` out of a mesh-skeleton table, parents first.

    Each bone is a dict of the fields an armature is built from: ``name``, ``parent`` (the
    index of its parent, -1 at the root), its rest ``position``/``rotation``/``scale`` in the
    host's basis, and the ``path`` a clip addresses it by.
    """
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


def material_rows(table):
    """``{path: MaterialProperties}`` out of a materials table.

    A decoder resolves each material the way its engine does and states the result one row
    per entry. This groups those rows back into the same normalised property tables a Unity
    ``.mat`` parses into, so the host's material builder never learns which engine it is
    looking at.
    """
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


class TextureSource:
    """The database surface a host's material builder resolves images through, backed by a
    decoder's own pixels instead of a folder of exported files.

    Same duck-typed shape as ``unity.bridge_asset_db.BridgeAssetDatabase`` where the builder
    touches it -- ``resolve_guid``, ``texture_bytes``, ``asset_path``, ``asset_name``,
    ``texture_is_srgb`` -- keyed by whatever the decoder's materials name their textures by.
    Presence of ``texture_bytes`` is what tells the builder to decode in memory rather than
    load a file, exactly as in bridge mode.
    """

    EXTENSION = ".png"

    def __init__(self, bridge, dataset, argument="texture"):
        self._bridge = bridge
        self._dataset = dataset
        self._argument = argument
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
        table = self._bridge.game_data(self._dataset, **{self._argument: missing})
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
        """No asset of these engines is Unity text. The material builder asks this only to
        read a Shader asset's name, which such a material states outright
        (MaterialProperties.shader_name), so nothing here needs to answer."""
        return None


def rows(table):
    """A table as plain row dicts, each column read once."""
    columns = {name: table.values(name) for name in table.names}
    return [{name: values[index] for name, values in columns.items()}
            for index in range(len(table))]


def _indices(joined):
    """A ';'-joined list of whole numbers, as a list of ints."""
    return [int(part) for part in joined.split(SLOT_SEPARATOR) if part] if joined else []


def _floats(blob, width):
    """A packed float32 buffer as an (n, width) view, or None when the column is empty."""
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32).reshape(-1, width)
