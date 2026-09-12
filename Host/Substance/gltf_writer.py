"""Minimal, dependency-free binary glTF (.glb) writer.

Substance Painter has no API to hand it geometry in memory -- ``project.create``
takes a mesh FILE path, and the accepted list is exactly ".fbx, .abc, .obj,
.dae, .ply, .gltf, .glb, .usd, .usda, .usdc, .usdz" (straight from the
application's own file-type validation string). GLB is the only one of those
that is a single self-contained binary file, carries per-vertex data verbatim
(float32 positions/normals/tangents, several UV sets, vertex colours) with no
re-quantisation, and names its materials -- which is what Painter turns into
Texture Set names, the identity the whole shader/texture wiring keys off.

So the decoded Unity mesh data goes straight into a GLB built here in memory
and written once; nothing is re-encoded through an intermediate interchange
format and nothing is lost on the way.

Everything written is plain glTF 2.0 core -- no extensions -- so the importer
on Painter's side (Assimp) has no optional feature to fall back on.
"""

from __future__ import annotations

import json
import struct

import numpy as np

# glTF componentType
_FLOAT = 5126
_UNSIGNED_INT = 5125

# glTF bufferView target
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963

_TYPE_OF_COMPONENTS = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}

_GLB_MAGIC = 0x46546C67   # 'glTF'
_CHUNK_JSON = 0x4E4F534A  # 'JSON'
_CHUNK_BIN = 0x004E4942   # 'BIN\0'


def _pad4(length):
    return (4 - (length % 4)) % 4


class GlbBuilder:
    """Accumulates materials/meshes/nodes and serialises one GLB.

    Buffers are appended as raw little-endian numpy blobs, each padded to a
    4-byte boundary so every accessor's byteOffset satisfies the spec's
    component-size alignment rule without per-accessor offsets ever being
    computed by hand.
    """

    def __init__(self, generator="RuriRipperImporterSubstance"):
        self._json = {
            "asset": {"version": "2.0", "generator": generator},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "materials": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [],
        }
        self._blobs = []
        self._length = 0
        self._material_index = {}

    # -- buffer plumbing ----------------------------------------------------

    def _append_blob(self, data, target):
        payload = data.tobytes() if isinstance(data, np.ndarray) else bytes(data)
        offset = self._length
        self._blobs.append(payload)
        self._length += len(payload)
        padding = _pad4(self._length)
        if padding:
            self._blobs.append(b"\x00" * padding)
            self._length += padding
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        self._json["bufferViews"].append(view)
        return len(self._json["bufferViews"]) - 1

    def _add_accessor(self, array, component_type, target, with_bounds=False):
        array = np.ascontiguousarray(array)
        count = array.shape[0]
        components = 1 if array.ndim == 1 else array.shape[1]
        view = self._append_blob(array, target)
        accessor = {
            "bufferView": view,
            "componentType": component_type,
            "count": int(count),
            "type": _TYPE_OF_COMPONENTS[components],
        }
        if with_bounds and count:
            # POSITION is the one accessor where min/max are REQUIRED by the
            # spec (importers use it for the scene bounds Painter frames on).
            values = array.reshape(count, components)
            accessor["min"] = [float(v) for v in values.min(axis=0)]
            accessor["max"] = [float(v) for v in values.max(axis=0)]
        self._json["accessors"].append(accessor)
        return len(self._json["accessors"]) - 1

    # -- public building ----------------------------------------------------

    def material(self, key, name, base_color=(1.0, 1.0, 1.0, 1.0), double_sided=True):
        """Get-or-create a material by a stable key (the Unity material guid).

        ``name`` becomes the glTF material name, which is what Painter reads
        back as the Texture Set name -- so it must be the Unity material's own
        m_Name, verbatim."""
        existing = self._material_index.get(key)
        if existing is not None:
            return existing
        index = len(self._json["materials"])
        self._json["materials"].append({
            "name": name,
            "doubleSided": bool(double_sided),
            "pbrMetallicRoughness": {
                "baseColorFactor": [float(c) for c in base_color],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
        })
        self._material_index[key] = index
        return index

    def mesh(self, name, primitives):
        """primitives: list of dicts with keys
             positions   (n,3) float32   -- required
             indices     (m,3) int       -- required
             normals     (n,3) float32   -- optional
             tangents    (n,4) float32   -- optional
             colors      (n,4) float32   -- optional
             uvs         {layer: (n,2) float32} -- optional
             material    int | None      -- index from material()
        """
        entries = []
        for primitive in primitives:
            positions = np.ascontiguousarray(primitive["positions"], dtype=np.float32)
            indices = np.ascontiguousarray(
                np.asarray(primitive["indices"]).reshape(-1), dtype=np.uint32)
            attributes = {
                "POSITION": self._add_accessor(positions, _FLOAT, _ARRAY_BUFFER, with_bounds=True),
            }
            normals = primitive.get("normals")
            if normals is not None:
                attributes["NORMAL"] = self._add_accessor(
                    np.ascontiguousarray(normals, dtype=np.float32), _FLOAT, _ARRAY_BUFFER)
            tangents = primitive.get("tangents")
            if tangents is not None:
                attributes["TANGENT"] = self._add_accessor(
                    np.ascontiguousarray(tangents, dtype=np.float32), _FLOAT, _ARRAY_BUFFER)
            for layer, uv in sorted((primitive.get("uvs") or {}).items()):
                attributes["TEXCOORD_{0}".format(layer)] = self._add_accessor(
                    np.ascontiguousarray(uv, dtype=np.float32), _FLOAT, _ARRAY_BUFFER)
            colors = primitive.get("colors")
            if colors is not None:
                attributes["COLOR_0"] = self._add_accessor(
                    np.ascontiguousarray(colors, dtype=np.float32), _FLOAT, _ARRAY_BUFFER)

            entry = {
                "attributes": attributes,
                "indices": self._add_accessor(indices, _UNSIGNED_INT, _ELEMENT_ARRAY_BUFFER),
                "mode": 4,  # TRIANGLES
            }
            if primitive.get("material") is not None:
                entry["material"] = int(primitive["material"])
            entries.append(entry)

        self._json["meshes"].append({"name": name, "primitives": entries})
        return len(self._json["meshes"]) - 1

    def node(self, name, mesh_index=None, matrix=None, parent=None):
        entry = {"name": name}
        if mesh_index is not None:
            entry["mesh"] = int(mesh_index)
        if matrix is not None:
            # glTF matrices are COLUMN-major float arrays; our matrices are
            # numpy row-major, so the flattened transpose is what goes out.
            entry["matrix"] = [float(v) for v in
                               np.asarray(matrix, dtype=np.float64).T.reshape(-1)]
        self._json["nodes"].append(entry)
        index = len(self._json["nodes"]) - 1
        if parent is None:
            self._json["scenes"][0]["nodes"].append(index)
        else:
            self._json["nodes"][parent].setdefault("children", []).append(index)
        return index

    def is_empty(self):
        return not self._json["meshes"]

    def to_bytes(self):
        binary = b"".join(self._blobs)
        document = dict(self._json)
        if binary:
            document["buffers"] = [{"byteLength": len(binary)}]
        else:
            document.pop("buffers", None)
            document.pop("bufferViews", None)
            document.pop("accessors", None)

        json_bytes = json.dumps(document, separators=(",", ":"),
                                ensure_ascii=False).encode("utf-8")
        json_bytes += b" " * _pad4(len(json_bytes))

        total = 12 + 8 + len(json_bytes) + (8 + len(binary) if binary else 0)
        out = bytearray()
        out += struct.pack("<III", _GLB_MAGIC, 2, total)
        out += struct.pack("<II", len(json_bytes), _CHUNK_JSON)
        out += json_bytes
        if binary:
            out += struct.pack("<II", len(binary), _CHUNK_BIN)
            out += binary
        return bytes(out)

    def write(self, path):
        data = self.to_bytes()
        with open(path, "wb") as handle:
            handle.write(data)
        return len(data)
