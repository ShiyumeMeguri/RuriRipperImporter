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

This module is pure (numpy only) so it can be unit-tested outside Blender.
"""

from __future__ import annotations

import numpy as np

# Unity VertexAttribute order == channel index.
(POSITION, NORMAL, TANGENT, COLOR,
 UV0, UV1, UV2, UV3, UV4, UV5, UV6, UV7,
 BLEND_WEIGHT, BLEND_INDICES) = range(14)

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


def _decode_channel(blob, stream_offsets, stream_strides, channel, count):
    """Decode one channel into an (count, dim) float array (or int for indices)."""
    stream = channel.get("stream", 0)
    offset = channel.get("offset", 0)
    fmt = channel.get("format", 0)
    dim = _real_dimension(channel.get("dimension", 0))
    if dim == 0:
        return None
    dtype, size, normalised, is_int = _FORMAT.get(fmt, (np.float32, 4, False, False))
    stride = stream_strides[stream]
    base = stream_offsets[stream] + offset
    # Gather the bytes for this channel across all vertices via a strided view.
    raw = np.frombuffer(blob, dtype=np.uint8)
    # Build an index matrix: for each vertex, the `size*dim` bytes of the field.
    field_bytes = size * dim
    starts = base + np.arange(count, dtype=np.int64) * stride
    idx = starts[:, None] + np.arange(field_bytes, dtype=np.int64)[None, :]
    gathered = raw[idx].reshape(count, dim, size)
    values = gathered.reshape(count * dim, size)
    # Pad to the dtype width and reinterpret.
    out = np.ascontiguousarray(values).view(dtype).reshape(count, dim)
    if is_int:
        return out.astype(np.int32)
    out = out.astype(np.float32)
    if normalised:
        if np.issubdtype(dtype, np.signedinteger):
            out = np.maximum(out / float(np.iinfo(dtype).max), -1.0)
        else:
            out = out / float(np.iinfo(dtype).max)
    return out


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

    def ch(i):
        return channels[i] if i < len(channels) else None

    if count and blob:
        pos_ch = ch(POSITION)
        if pos_ch and _real_dimension(pos_ch.get("dimension")):
            mesh.positions = _decode_channel(blob, stream_offsets, stream_strides, pos_ch, count)[:, :3]

        nrm_ch = ch(NORMAL)
        if nrm_ch and _real_dimension(nrm_ch.get("dimension")):
            decoded = _decode_channel(blob, stream_offsets, stream_strides, nrm_ch, count)
            candidates = []
            if decoded is not None and decoded.shape[1] >= 3:
                candidates.append(decoded[:, :3].astype(np.float32))
            # Flag-packed channel ("format: 0, dimension: 49" style): the field
            # is ONE 32-bit word carrying an R10G10B10A2 normal -- try both
            # common bit interpretations; the unit-length gate below picks
            # whichever (if either) is real.
            if decoded is not None and decoded.shape[1] == 1 and (nrm_ch.get("dimension") or 0) > 15:
                packed_words = decoded.view(np.uint32).reshape(-1) if decoded.dtype == np.float32 \
                    else decoded.astype(np.uint32).reshape(-1)
                candidates.extend(_unpack_normal_10_10_10(packed_words))
            for cand in candidates:
                lengths = np.linalg.norm(cand, axis=1)
                # Trust stored normals only if they are predominantly unit length.
                if np.mean(np.abs(lengths - 1.0) < 0.15) > 0.9:
                    mesh.normals = cand / np.clip(lengths[:, None], 1e-6, None)
                    break

        tan_ch = ch(TANGENT)
        if tan_ch and _real_dimension(tan_ch.get("dimension")) >= 3:
            t = _decode_channel(blob, stream_offsets, stream_strides, tan_ch, count)
            tl = np.linalg.norm(t[:, :3], axis=1)
            if np.mean(np.abs(tl - 1.0) < 0.15) > 0.9:
                mesh.tangents = t if t.shape[1] == 4 else np.pad(t, ((0, 0), (0, 4 - t.shape[1])), constant_values=1.0)

        col_ch = ch(COLOR)
        if col_ch and _real_dimension(col_ch.get("dimension")):
            c = _decode_channel(blob, stream_offsets, stream_strides, col_ch, count)
            mesh.colors = c if c.shape[1] == 4 else np.pad(c, ((0, 0), (0, 4 - c.shape[1])), constant_values=1.0)

        for layer, ci in enumerate(range(UV0, UV7 + 1)):
            uch = ch(ci)
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

    # Index buffer and submeshes.
    index_format = data.get("m_IndexFormat", 0)
    index_dtype = np.uint16 if index_format == 0 else np.uint32
    index_size = 2 if index_format == 0 else 4
    ib_hex = _clean_hex(data.get("m_IndexBuffer"))
    indices = np.frombuffer(bytes.fromhex(ib_hex), dtype=index_dtype).astype(np.int64) if ib_hex else np.empty(0, np.int64)

    submeshes = data.get("m_SubMeshes") or []
    tris = []
    tri_mat = []
    for si, sd in enumerate(submeshes):
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

    mesh.blendshapes = _decode_blendshapes(data.get("m_Shapes") or {})
    return mesh


def _decode_uint_hex(hexstr):
    cleaned = _clean_hex(hexstr)
    if not cleaned:
        return None
    try:
        return np.frombuffer(bytes.fromhex(cleaned), dtype=np.uint32)
    except ValueError:
        return None


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
            out.append((
                v.get("index", 0),
                (vtx.get("x", 0.0), vtx.get("y", 0.0), vtx.get("z", 0.0)),
                (nrm.get("x", 0.0), nrm.get("y", 0.0), nrm.get("z", 0.0)),
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
            })
        result.append({"name": name, "frames": chan_frames})
    return result
