"""Decode a Unity serialized Mesh (class 43) into plain numpy arrays.

The mesh stores its vertex attributes interleaved across up to four streams in a
single hex blob (``_typelessdata``).  Each of the 14 fixed channels describes one
Unity ``VertexAttribute`` (Position, Normal, Tangent, Color, TexCoord0..7,
BlendWeight, BlendIndices) by stream / byte offset / format / dimension.

Some exporters write a bogus ``dimension`` for packed channels (e.g. a 4-byte
packed normal shows up as ``format: 0, dimension: 49``).  We therefore sanitise
the dimension when computing stream strides, and we only trust a decoded normal
when it forms a sane unit-length field — otherwise the caller recomputes normals
from geometry, which is always correct.

A game that packs a whole tangent frame into one word is NOT decoded here: that
bit layout is the game's own fact and is unpacked on the read path by that
game's hook, so this module only ever sees standard Unity channels.

This module is pure (numpy only) so it can be unit-tested outside Blender.
"""

from __future__ import annotations

import numpy as np

# Semantic channel ids, in the MODERN (2018.1+) Unity VertexAttribute order --
# for 14-channel meshes the semantic IS the channel index.
(POSITION, NORMAL, TANGENT, COLOR,
 UV0, UV1, UV2, UV3, UV4, UV5, UV6, UV7,
 BLEND_WEIGHT, BLEND_INDICES) = range(14)

# Unity <= 2017 serializes EXACTLY 8 vertex channels, in its own older order --
# a different dialect of the same table, told apart by the channel COUNT the
# file itself declares (a structural fact of the serialized version, not a
# guess). Reading an old mesh with the modern order silently shifts every
# semantic: the real TexCoord0 (slot 3) lands on COLOR, TexCoord1 (slot 4 --
# routinely the lightmap UV) becomes the texture UV, and the Tangent (slot 7,
# dim=4) turns into a bogus "UV3" layer. Measured on Koikatu (Unity 5.6): every
# scene mesh textured off its lightmap UV, which reads as "all UVs wrong".
_LEGACY_CHANNEL_SLOTS = {
    POSITION: 0, NORMAL: 1, COLOR: 2,
    UV0: 3, UV1: 4, UV2: 5, UV3: 6,
    TANGENT: 7,
}


def _channel_slots(channel_count):
    """{semantic id -> declared channel index} for this mesh's dialect. Modern
    files map identically; legacy files remap, and semantics they cannot carry
    (TexCoord4..7, blend weights/indices -- pre-2018 skin lives in m_Skin, see
    _apply_legacy_skin) are simply absent."""
    if channel_count <= 8:
        return _LEGACY_CHANNEL_SLOTS
    return {semantic: semantic for semantic in range(14)}

# VertexAttributeFormat -> (numpy dtype, byte size, is_normalised, is_int).
_FORMAT = {
    0:  (np.float32, 4, False, False),  # Float32
    1:  (np.float16, 2, False, False),  # Float16
    2:  (np.uint8,   1, True,  False),  # UNorm8
    3:  (np.int8,    1, True,  False),  # SNorm8
    4:  (np.uint16,  2, True,  False),  # UNorm16
    5:  (np.int16,   2, True,  False),  # SNorm16
    6:  (np.uint8,   1, False, True),   # UInt8
    7:  (np.int8,    1, False, True),   # SInt8
    8:  (np.uint16,  2, False, True),   # UInt16
    9:  (np.int16,   2, False, True),   # SInt16
    10: (np.uint32,  4, False, True),   # UInt32
    11: (np.int32,   4, False, True),   # SInt32
}

_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def _clean_hex(value):
    """Return an even-length, hex-only string from a possibly dirty blob.

    Some Unity writers (notably meshes re-serialised from Object.Instantiate)
    append a stray non-hex character to long hex blobs.  Filtering to hex and
    trimming any odd trailing nibble makes decoding robust to that.
    """
    if not value or not isinstance(value, str):
        return ""
    if len(value) % 2 or value[-1] not in _HEX_CHARS:
        value = "".join(c for c in value if c in _HEX_CHARS)
    if len(value) % 2:
        value = value[:-1]
    return value


def _real_dimension(dim):
    """Unpack the true component count from a possibly flag-packed byte."""
    if dim is None:
        return 0
    return dim & 0x0F if dim > 15 else dim


def _format_size(fmt):
    entry = _FORMAT.get(fmt)
    return entry[1] if entry else 4


def _unpack_normal_10_10_10(words):
    """Both common R10G10B10A2 normal decodes of a (n,) uint32 word array --
    [SNorm 10-bit, UNorm-remapped 10-bit] -- each as (n,3) float32. The caller
    keeps whichever passes the unit-length trust gate, so a wrong guess can
    never leak garbage normals into the mesh (it just falls back, exactly the
    pre-existing behaviour for undecodable fields)."""
    x_bits = (words & 0x3FF).astype(np.int32)
    y_bits = ((words >> 10) & 0x3FF).astype(np.int32)
    z_bits = ((words >> 20) & 0x3FF).astype(np.int32)

    def snorm(bits):
        signed = np.where(bits >= 512, bits - 1024, bits).astype(np.float32)
        return np.maximum(signed / 511.0, -1.0)

    def unorm(bits):
        return bits.astype(np.float32) / 1023.0 * 2.0 - 1.0

    return [np.stack([snorm(x_bits), snorm(y_bits), snorm(z_bits)], axis=1),
            np.stack([unorm(x_bits), unorm(y_bits), unorm(z_bits)], axis=1)]


class SubMesh:
    __slots__ = ("first_index", "index_count", "topology", "base_vertex",
                 "first_vertex", "vertex_count")

    def __init__(self, d, index_size):
        self.first_index = d.get("firstByte", 0) // index_size
        self.index_count = d.get("indexCount", 0)
        self.topology = d.get("topology", 0)
        self.base_vertex = d.get("baseVertex", 0)
        self.first_vertex = d.get("firstVertex", 0)
        self.vertex_count = d.get("vertexCount", 0)


class DecodedMesh:
    """Geometry decoded from a Unity Mesh, in Unity coordinates."""

    def __init__(self, name):
        self.name = name
        self.positions = None        # (n, 3) float32
        self.normals = None          # (n, 3) float32 or None
        self.tangents = None         # (n, 4) float32 or None (xyz + sign)
        self.colors = None           # (n, 4) float32 or None
        self.uvs = {}                # uv_layer_index -> (n, 2) float32
        self.bone_weights = None     # (n, 4) float32 or None
        self.bone_indices = None     # (n, 4) int32 or None
        self.triangles = None        # (m, 3) int32 (already winding-handled? no)
        self.submeshes = []          # list[SubMesh]
        self.tri_material = None     # (m,) int32 submesh/material index per tri
        self.bind_poses = None       # (b, 4, 4) float32 column-major Unity
        self.bone_name_hashes = None # (b,) uint32
        self.blendshapes = []        # list of dicts: name, frames[...]
        self.vertex_count = 0
        # Length of m_VariableBoneCountWeights. The influence count is NOT capped
        # at four anywhere above: bone_weights/bone_indices keep whatever width
        # the BlendWeight/BlendIndices channels declare, and the host builds one
        # vertex group entry per influence. This field is Unity's OTHER place to
        # park influences, in a packing whose byte layout nothing in this tree
        # derives, so its length travels as a fact to report rather than a skin
        # silently presented as complete. Zero on every mesh measured so far.
        self.variable_bone_count_weights = 0


def _decode_channel(blob, stream_offsets, stream_strides, channel, count):
    """Decode one channel into an (count, dim) float array (or int for indices).

    The vertex buffer IS an array of stride-sized records with this channel's
    field at a fixed offset inside each -- exactly what a numpy structured
    dtype states. One zero-copy strided view plus one astype replaces the old
    per-byte int64 index-matrix gather, which built an (n, field_bytes) index
    and fancy-indexed the whole buffer through it (8 index bytes paid per data
    byte, per channel, per mesh)."""
    stream = channel.get("stream", 0)
    offset = channel.get("offset", 0)
    fmt = channel.get("format", 0)
    dim = _real_dimension(channel.get("dimension", 0))
    if dim == 0:
        return None
    dtype, size, normalised, is_int = _FORMAT.get(fmt, (np.float32, 4, False, False))
    stride = stream_strides[stream]
    record = np.dtype({"names": ["v"], "formats": [(np.dtype(dtype).newbyteorder("<"), (dim,))],
                       "offsets": [offset], "itemsize": stride})
    view = np.frombuffer(blob, dtype=record, count=count, offset=stream_offsets[stream])["v"]
    if is_int:
        return view.astype(np.int32)
    out = view.astype(np.float32)
    if normalised:
        if np.issubdtype(dtype, np.signedinteger):
            out = np.maximum(out / float(np.iinfo(dtype).max), -1.0)
        else:
            out = out / float(np.iinfo(dtype).max)
    return out


def _decode_vertex_channels(mesh, blob, channels, count):
    """Decode every vertex channel out of the raw vertex buffer into `mesh`,
    shared verbatim between the YAML path (hex-decoded _typelessdata) and the
    bridge blob path (the same bytes, never hex-encoded at all). Returns the
    packed-normal candidates the unit-length gate cannot judge (resolved
    against the geometry once the triangles exist)."""
    # Compute per-stream strides from the channels (sanitising dimensions),
    # then lay streams out back-to-back as Unity serialises them.
    stream_strides = {}
    for ch in channels:
        dim = _real_dimension(ch.get("dimension", 0))
        if dim == 0:
            continue
        stream = ch.get("stream", 0)
        end = ch.get("offset", 0) + dim * _format_size(ch.get("format", 0))
        stream_strides[stream] = max(stream_strides.get(stream, 0), end)
    # Unity aligns the start of every vertex stream to a 16-byte boundary.
    stream_offsets = {}
    running = 0
    for stream in sorted(stream_strides):
        running = (running + 15) & ~15
        stream_offsets[stream] = running
        running += stream_strides[stream] * count

    packed_candidates = []
    slots = _channel_slots(len(channels))

    def ch(semantic):
        index = slots.get(semantic)
        return channels[index] if index is not None and index < len(channels) else None

    pos_ch = ch(POSITION)
    if pos_ch and _real_dimension(pos_ch.get("dimension")):
        mesh.positions = _decode_channel(blob, stream_offsets, stream_strides, pos_ch, count)[:, :3]

    nrm_ch = ch(NORMAL)
    if nrm_ch and _real_dimension(nrm_ch.get("dimension")):
        decoded = _decode_channel(blob, stream_offsets, stream_strides, nrm_ch, count)
        candidates = []
        if decoded is not None and decoded.shape[1] >= 3:
            candidates.append(decoded[:, :3].astype(np.float32))
        # A SINGLE-component normal channel is necessarily packed -- a normal
        # cannot be one number. That is the real test, not the "dimension >
        # 15" flag-packed spelling, which only some writers use: a writer that
        # declares a plain 1-component channel would fall through to no normals
        # at all if the flag were the gate.
        # Offer every known packing; the unit-length gate below picks
        # whichever (if any) is genuinely the encoding used.
        if decoded is not None and decoded.shape[1] == 1:
            packed_words = decoded.view(np.uint32).reshape(-1) if decoded.dtype == np.float32 \
                else decoded.astype(np.uint32).reshape(-1)
            candidates.extend(_unpack_normal_10_10_10(packed_words))
        for cand in candidates:
            lengths = np.linalg.norm(cand, axis=1)
            # Trust stored normals only if they are predominantly unit length.
            if np.mean(np.abs(lengths - 1.0) < 0.15) > 0.9:
                mesh.normals = cand / np.clip(lengths[:, None], 1e-6, None)
                break
        if mesh.normals is None:
            # Nothing looked unit length, so the length gate has no answer.
            # Hand every candidate to the geometric arbiter instead -- it can
            # only fill in normals that would otherwise be missing entirely.
            packed_candidates.extend(candidates)

    tan_ch = ch(TANGENT)
    if tan_ch and _real_dimension(tan_ch.get("dimension")) >= 3:
        t = _decode_channel(blob, stream_offsets, stream_strides, tan_ch, count)
        tl = np.linalg.norm(t[:, :3], axis=1)
        if np.mean(np.abs(tl - 1.0) < 0.15) > 0.9:
            mesh.tangents = t if t.shape[1] == 4 else np.pad(t, ((0, 0), (0, 4 - t.shape[1])), constant_values=1.0)
    elif tan_ch and _real_dimension(tan_ch.get("dimension")) == 1:
        # 一个数装不下切线 ⇒ 某家自研的位打包。那是资产方的事实,该由读取该游戏的
        # hook 在读路径上解开(见 EndfieldVertexFrame),到这里还是单分量就说明没人解,
        # 而内核没有可猜的标准读法。
        print("[mesh-decoder] 单分量 TANGENT:该游戏的顶点打包没有在读路径解开,切线留空",
              flush=True)

    col_ch = ch(COLOR)
    if col_ch and _real_dimension(col_ch.get("dimension")):
        c = _decode_channel(blob, stream_offsets, stream_strides, col_ch, count)
        mesh.colors = c if c.shape[1] == 4 else np.pad(c, ((0, 0), (0, 4 - c.shape[1])), constant_values=1.0)

    for layer, semantic in enumerate(range(UV0, UV7 + 1)):
        uch = ch(semantic)
        if uch and _real_dimension(uch.get("dimension")) >= 2:
            mesh.uvs[layer] = _decode_channel(blob, stream_offsets, stream_strides, uch, count)[:, :2]

    w_ch = ch(BLEND_WEIGHT)
    i_ch = ch(BLEND_INDICES)
    has_w = w_ch is not None and _real_dimension(w_ch.get("dimension")) > 0
    has_i = i_ch is not None and _real_dimension(i_ch.get("dimension")) > 0
    if has_i:
        mesh.bone_indices = _decode_channel(blob, stream_offsets, stream_strides, i_ch, count)
        if has_w:
            mesh.bone_weights = _decode_channel(blob, stream_offsets, stream_strides, w_ch, count)
        else:
            # Indices present without weights: a rigid bind where each
            # vertex follows a single bone with full weight.
            weights = np.zeros(mesh.bone_indices.shape, dtype=np.float32)
            weights[:, 0] = 1.0
            mesh.bone_weights = weights
    return packed_candidates


def _build_triangles(mesh, indices, submesh_dicts, index_size):
    """Slice the index buffer into per-submesh triangle blocks -- shared
    between both decode paths (the blob's subMeshes dicts deliberately carry
    the exact YAML key names)."""
    tris = []
    tri_mat = []
    for si, sd in enumerate(submesh_dicts):
        sm = SubMesh(sd, index_size)
        mesh.submeshes.append(sm)
        if sm.topology != 0:
            continue  # only triangle lists are imported
        seg = indices[sm.first_index:sm.first_index + sm.index_count] + sm.base_vertex
        n_tri = len(seg) // 3
        if n_tri:
            block = seg[:n_tri * 3].reshape(n_tri, 3)
            tris.append(block)
            tri_mat.append(np.full(n_tri, si, dtype=np.int32))
    if tris:
        mesh.triangles = np.concatenate(tris).astype(np.int32)
        mesh.tri_material = np.concatenate(tri_mat)
    else:
        mesh.triangles = np.empty((0, 3), np.int32)
        mesh.tri_material = np.empty(0, np.int32)


def decode_mesh(doc):
    """Decode a parsed Mesh document (UnityDocument.data) into a DecodedMesh."""
    data = doc.data if hasattr(doc, "data") else doc
    mesh = DecodedMesh(data.get("m_Name", "Mesh"))

    vdata = data.get("m_VertexData") or {}
    count = vdata.get("m_VertexCount", 0)
    mesh.vertex_count = count
    channels = vdata.get("m_Channels") or []
    blob_hex = _clean_hex(vdata.get("_typelessdata"))
    blob = bytes.fromhex(blob_hex) if blob_hex else b""

    packed_candidates = []
    if count and blob:
        packed_candidates = _decode_vertex_channels(mesh, blob, channels, count)

    # Index buffer and submeshes.
    index_format = data.get("m_IndexFormat", 0)
    index_dtype = np.uint16 if index_format == 0 else np.uint32
    index_size = 2 if index_format == 0 else 4
    ib_hex = _clean_hex(data.get("m_IndexBuffer"))
    indices = np.frombuffer(bytes.fromhex(ib_hex), dtype=index_dtype).astype(np.int64) if ib_hex else np.empty(0, np.int64)
    _build_triangles(mesh, indices, data.get("m_SubMeshes") or [], index_size)

    _resolve_packed_normals(mesh, packed_candidates)

    # Bind poses (4x4 each, Unity row labels e00..e33 are row-major elements).
    bind = data.get("m_BindPose") or []
    if bind:
        mats = np.empty((len(bind), 4, 4), dtype=np.float32)
        for i, m in enumerate(bind):
            for r in range(4):
                for c in range(4):
                    mats[i, r, c] = m.get(f"e{r}{c}", 0.0)
        mesh.bind_poses = mats

    mesh.bone_name_hashes = _decode_uint_hex(data.get("m_BoneNameHashes"))

    _apply_legacy_skin(mesh, data.get("m_Skin"))

    mesh.variable_bone_count_weights = _variable_bone_count_weights(
        data.get("m_VariableBoneCountWeights"))

    mesh.blendshapes = _decode_blendshapes(data.get("m_Shapes") or {})
    return mesh


def _variable_bone_count_weights(field):
    """How many words m_VariableBoneCountWeights holds. The field is a container
    whose m_Data is null on a mesh that skins within the fixed four influences,
    which is every mesh measured so far."""
    if not isinstance(field, dict):
        return 0
    payload = field.get("m_Data")
    return len(payload) if isinstance(payload, (list, str)) else 0


def _apply_legacy_skin(mesh, skin):
    """Skin weights from the mesh's own ``m_Skin`` array.

    Before Unity 2018 there were no BlendWeight/BlendIndices vertex channels at
    all: a skinned mesh carries a parallel ``BoneWeights4`` array instead, one
    entry per vertex. Nothing in the vertex stream says so, and a mesh decoded
    without this simply comes out with no vertex groups -- which is what every
    Unity 5 character did before this existed.

    Only ever consulted when the stream itself carried none, so a modern mesh is
    untouched."""
    if mesh.bone_indices is not None or not skin:
        return
    weights = np.zeros((len(skin), 4), dtype=np.float32)
    indices = np.zeros((len(skin), 4), dtype=np.int32)
    for vertex, entry in enumerate(skin):
        if not isinstance(entry, dict):
            continue
        for slot in range(4):
            weights[vertex, slot] = float(entry.get("weight[{0}]".format(slot), 0.0) or 0.0)
            indices[vertex, slot] = int(entry.get("boneIndex[{0}]".format(slot), 0) or 0)
    mesh.bone_weights = weights
    mesh.bone_indices = indices


def decode_mesh_blob(meta_json, payload):
    """Bridge fast path: decode a MeshRawBlob (JSON index + raw little-endian
    payload, see Ruri.RipperHook's MeshRawBlob.cs) into the same DecodedMesh
    the YAML path yields. The vertex/index bytes are the exact bytes the YAML
    document would have carried as hex text, and every decode rule (formats,
    packed-normal candidates, trust gates, submesh windows) is the SHARED code
    above -- so the two paths cannot drift."""
    import json

    meta = json.loads(meta_json)
    view = memoryview(payload)
    sections = meta.get("sections") or {}

    def section(key):
        entry = sections.get(key)
        if not entry or not entry.get("len"):
            return view[:0]
        return view[entry["off"]:entry["off"] + entry["len"]]

    mesh = DecodedMesh(str(meta.get("name") or "Mesh"))
    count = int(meta.get("vertexCount") or 0)
    mesh.vertex_count = count

    blob = section("vertexData")
    channels = meta.get("channels") or []
    packed_candidates = []
    if count and len(blob):
        packed_candidates = _decode_vertex_channels(mesh, blob, channels, count)

    index_size = int(meta.get("indexSize") or 2)
    index_dtype = np.uint16 if index_size == 2 else np.uint32
    index_bytes = section("indexBuffer")
    indices = np.frombuffer(index_bytes, dtype=index_dtype).astype(np.int64) \
        if len(index_bytes) else np.empty(0, np.int64)
    _build_triangles(mesh, indices, meta.get("subMeshes") or [], index_size)

    _resolve_packed_normals(mesh, packed_candidates)

    bind_bytes = section("bindPose")
    if len(bind_bytes):
        mesh.bind_poses = np.frombuffer(bind_bytes, dtype="<f4").reshape(-1, 4, 4).copy()

    hash_bytes = section("boneNameHashes")
    mesh.bone_name_hashes = np.frombuffer(hash_bytes, dtype="<u4").copy() if len(hash_bytes) else None

    # The pre-2018 skin array, when the stream carried no blend channels (see
    # _apply_legacy_skin). Four float weights then four int32 bone indices per
    # vertex, which is one strided view each rather than a per-vertex loop.
    skin_bytes = section("skin")
    if mesh.bone_indices is None and len(skin_bytes):
        skin = np.frombuffer(skin_bytes, dtype="<u4").reshape(-1, 8)
        mesh.bone_weights = skin[:, :4].copy().view(np.float32)
        mesh.bone_indices = skin[:, 4:].copy().view(np.int32)

    mesh.variable_bone_count_weights = int(meta.get("variableBoneCountWeights") or 0)

    mesh.blendshapes = _decode_blendshapes_blob(meta, section("shapeVertices"))
    return mesh


def _resolve_packed_normals(mesh, candidates):
    """Pick a packed-normal decode by testing it against the mesh itself.

    Encodings whose output is unit length by construction (octahedral) sail
    through a unit-length check no matter how wrong the bit layout guess was, so
    that check proves nothing for them. What DOES prove it is the geometry: a
    correct vertex normal points the same way as the average of the faces around
    that vertex. A wrong bit interpretation produces directions uncorrelated
    with the surface and scores near zero.

    Only ever runs when the ordinary decode already failed, so a mesh that
    decoded normally can never be affected.
    """
    if mesh.normals is not None or not candidates:
        return
    if mesh.triangles is None or len(mesh.triangles) == 0 or mesh.positions is None:
        return

    tris = mesh.triangles
    p0 = mesh.positions[tris[:, 0]]
    face = np.cross(mesh.positions[tris[:, 1]] - p0, mesh.positions[tris[:, 2]] - p0)
    lengths = np.linalg.norm(face, axis=1, keepdims=True)
    face = face / np.clip(lengths, 1e-12, None)

    # Area-weighted average of the incident face normals, per vertex.
    accum = np.zeros(mesh.positions.shape, dtype=np.float64)
    for corner in range(3):
        np.add.at(accum, tris[:, corner], face)
    lengths = np.linalg.norm(accum, axis=1, keepdims=True)
    used = (lengths > 1e-9).reshape(-1)
    if not used.any():
        return
    geometric = accum / np.clip(lengths, 1e-12, None)

    best_score = 0.0
    best = None
    for candidate in candidates:
        if candidate.shape != mesh.positions.shape:
            continue
        score = float(np.mean(np.einsum("ij,ij->i", candidate[used].astype(np.float64),
                                        geometric[used])))
        if score > best_score:
            best_score, best = score, candidate
    # Threshold measured, not picked: over real Unity character meshes whose
    # normals decode correctly the score runs +0.71 .. +0.99 (the low end is a
    # thin double-sided sheet -- an eyebrow card -- where opposing faces cancel),
    # while a wrong bit interpretation lands near 0. The sign is positive because
    # Unity's stored winding already yields outward face normals; that was
    # measured on the same meshes rather than assumed from handedness.
    if best is not None and best_score > 0.35:
        mesh.normals = best


def _decode_uint_hex(hexstr):
    cleaned = _clean_hex(hexstr)
    if not cleaned:
        return None
    try:
        return np.frombuffer(bytes.fromhex(cleaned), dtype=np.uint32)
    except ValueError:
        return None


def _decode_blendshapes_blob(meta, vertex_bytes):
    """Blob-side blendshape decode -- same output structure as _decode_blendshapes
    (name, per-frame weight/has_normals/has_tangents and (index, vertex delta,
    normal delta, tangent delta) tuples), read straight off the packed 40-byte
    entries instead of YAML dicts."""
    channels = meta.get("shapeChannels") or []
    if not channels:
        return []
    frames_meta = meta.get("shapeFrames") or []
    full_weights = meta.get("fullWeights") or []
    entry_dtype = np.dtype([("index", "<u4"), ("v", "<f4", (3,)),
                            ("n", "<f4", (3,)), ("t", "<f4", (3,))])
    entries = np.frombuffer(vertex_bytes, dtype=entry_dtype) if len(vertex_bytes) else \
        np.empty(0, dtype=entry_dtype)

    def frame_deltas(first, vcount):
        rows = entries[first:first + vcount]
        return [(int(row["index"]),
                 (float(row["v"][0]), float(row["v"][1]), float(row["v"][2])),
                 (float(row["n"][0]), float(row["n"][1]), float(row["n"][2])),
                 (float(row["t"][0]), float(row["t"][1]), float(row["t"][2])))
                for row in rows]

    result = []
    for ci, chan in enumerate(channels):
        raw_name = chan.get("name")
        name = str(raw_name) if raw_name is not None else f"blendshape{ci}"
        frame_index = int(chan.get("frameIndex") or 0)
        frame_count = int(chan.get("frameCount") or 0)
        chan_frames = []
        for fi in range(frame_index, frame_index + frame_count):
            fr = frames_meta[fi]
            weight = full_weights[fi] if fi < len(full_weights) else 100.0
            chan_frames.append({
                "weight": weight,
                "deltas": frame_deltas(int(fr.get("firstVertex") or 0), int(fr.get("vertexCount") or 0)),
                "has_normals": bool(fr.get("hasNormals", 0)),
                "has_tangents": bool(fr.get("hasTangents", 0)),
            })
        result.append({"name": name, "frames": chan_frames})
    return result


def _decode_blendshapes(shapes):
    """Decode m_Shapes into [{name, frames:[{weight, deltas:(k,) idx + offsets}]}]."""
    verts = shapes.get("vertices") or []
    frames = shapes.get("shapes") or []
    channels = shapes.get("channels") or []
    full_weights = shapes.get("fullWeights") or []
    if not channels:
        return []

    # Pre-extract per-frame delta vertices.
    def frame_deltas(first, vcount):
        out = []
        for v in verts[first:first + vcount]:
            vtx = v.get("vertex") or {}
            nrm = v.get("normal") or {}
            tan = v.get("tangent") or {}
            out.append((
                v.get("index", 0),
                (vtx.get("x", 0.0), vtx.get("y", 0.0), vtx.get("z", 0.0)),
                (nrm.get("x", 0.0), nrm.get("y", 0.0), nrm.get("z", 0.0)),
                (tan.get("x", 0.0), tan.get("y", 0.0), tan.get("z", 0.0)),
            ))
        return out

    result = []
    for ci, chan in enumerate(channels):
        # A purely-numeric blendshape name (e.g. "0") parses as a Unity YAML int
        # scalar, not a string -- the channel name is always semantically a
        # string regardless of what shape its raw text happens to have.
        raw_name = chan.get("name")
        name = str(raw_name) if raw_name is not None else f"blendshape{ci}"
        frame_index = chan.get("frameIndex", 0)
        frame_count = chan.get("frameCount", 0)
        chan_frames = []
        for fi in range(frame_index, frame_index + frame_count):
            fr = frames[fi]
            weight = full_weights[fi] if fi < len(full_weights) else 100.0
            chan_frames.append({
                "weight": weight,
                "deltas": frame_deltas(fr.get("firstVertex", 0), fr.get("vertexCount", 0)),
                "has_normals": bool(fr.get("hasNormals", 0)),
                "has_tangents": bool(fr.get("hasTangents", 0)),
            })
        result.append({"name": name, "frames": chan_frames})
    return result


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------
TOPOLOGY_NAMES = {1: "Quads", 2: "Lines", 3: "LineStrip", 4: "Points"}


def diagnose_empty(doc, name):
    """Why a Mesh decoded to nothing.

    Unity can store geometry in three places and this decoder reads exactly one
    of them (the plain vertex-data blob), so an empty result has to say WHICH of
    the other two is holding the data instead of silently vanishing. Returns a
    finished sentence -- the answer is about Unity's storage formats, not about
    any host, so both importers say the same thing."""
    data = doc.data
    compressed = data.get("m_CompressedMesh") or {}
    vertices = compressed.get("m_Vertices") or {}
    if int(vertices.get("m_NumItems") or 0) > 0:
        return ("'{0}' is a COMPRESSED mesh (Model Importer > Mesh Compression). Its vertex "
                "data is bit-packed in m_CompressedMesh, which this decoder does not read -- "
                "re-import the model with Mesh Compression = Off.".format(name))
    stream = data.get("m_StreamData") or {}
    if int(stream.get("size") or 0) > 0 or (stream.get("path") or ""):
        return ("'{0}' keeps its vertex data in an external stream file ({1}); the YAML "
                "carries none. Export with the resource file resolved.".format(
                    name, stream.get("path") or "?"))
    topologies = {int(sm.get("topology") or 0) for sm in (data.get("m_SubMeshes") or [])}
    exotic = sorted(t for t in topologies if t != 0)
    if exotic and 0 not in topologies:
        return "'{0}' has no triangle submeshes (topology {1}) -- nothing to draw.".format(
            name, ", ".join(TOPOLOGY_NAMES.get(t, str(t)) for t in exotic))
    return "'{0}' decoded to zero vertices.".format(name)
