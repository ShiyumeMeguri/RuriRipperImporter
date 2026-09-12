"""Avatar asset parsing: the embedded skeleton, for building an armature from a standalone
Avatar asset.

This is DATA extraction, deliberately not solving: Unity's muscle solve lives on the C# side
(Ruri.RipperHook's HumanoidClipGenericizer), asked for one clip at a time through
RipperBlenderBridge.SolveHumanoidClip, which answers with ordinary per-bone transform curves
and leaves the clip asset untouched. Nothing rewrites clips globally -- that is what keeps a
Unity-bound export's humanoid clips humanoid. The only thing a host still reads off an Avatar
is its own raw skeleton -- node hierarchy, rest pose, and TOS-resolved names -- which is
populated for Generic avatars too.
"""

from __future__ import annotations

_HEXDIGITS = set("0123456789abcdefABCDEF")


def _unwrap(value):
    """Peel Unity's OffsetPtr ``{data: ...}`` indirection."""
    while isinstance(value, dict) and len(value) == 1 and "data" in value:
        value = value["data"]
    return value


def _int_array(blob):
    """Parse a little-endian hex int32 array; tolerant of AssetRipper's trailing -1 padding."""
    if isinstance(blob, (list, tuple)):
        return [int(x) for x in blob]
    text = str(blob)
    out = []
    i = 0
    while i + 8 <= len(text):
        chunk = text[i:i + 8]
        if all(c in _HEXDIGITS for c in chunk):
            out.append(int.from_bytes(bytes.fromhex(chunk), "little", signed=True))
            i += 8
        else:
            break
    return out


def _parse_tos(data):
    """The avatar's ``m_TOS`` (CRC32 path hash -> transform path) as ``{int: str}``."""
    tos = data.get("m_TOS")
    result = {}
    if isinstance(tos, list):
        for pair in tos:
            if not isinstance(pair, dict):
                continue
            key = pair.get("first")
            value = pair.get("second")
            if value is None and isinstance(key, dict):  # {key: path} flow-map variant
                for k, v in key.items():
                    key, value = k, v
            try:
                result[int(key) & 0xFFFFFFFF] = str(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(tos, dict):
        for key, value in tos.items():
            try:
                result[int(key) & 0xFFFFFFFF] = str(value)
            except (TypeError, ValueError):
                continue
    return result


def transform_paths(data):
    """Every transform path the avatar's ``m_TOS`` names -- the CRC32(path)->path
    table for the whole skeleton, which a shared-skeleton part mesh hashes its
    bones from. Returned as a plain list of full paths (Root/Bip001/...)."""
    return list(_parse_tos(data).values())


# WHICH of the avatar's two full-skeleton pose arrays is the rest a skinned mesh
# was authored against. An Avatar carries both, over the same m_AvatarSkeleton
# node order:
#
#   m_DefaultPose          the model's OWN pose -- the one its meshes' bindposes
#                          invert. Measured on Endfield npc deathgirl: every one
#                          of the face mesh's 65 bones yields the SAME residual
#                          restWorld @ bindpose (a single rigid frame change,
#                          |t| = 0.0082 for all 65), and so do brow (33/33),
#                          eyeshadow (14/14), iris (8/8) and hair (31/31) -- and
#                          all nine parts agree on that one residual to 1e-6, so
#                          they assemble consistently.
#   m_AvatarSkeletonPose   Unity's own skeleton pose, which is NOT that one. The
#                          same 65 face bones yield 65 DIFFERENT residuals under
#                          it (tongue joints off by 2.16), i.e. the face gets
#                          re-posed into an incoherent shape -- the "stretched
#                          face, teeth outside the skin" an assembled npc showed.
#
# A bone the two poses genuinely disagree about is a real rest-pose difference
# (this rig's arms are 48 degrees apart between the outfit's bind pose and the
# skeleton's), and skinning handles that correctly by construction; what a wrong
# array costs is COHERENCE, which is what the numbers above measure.
_SKELETON_POSE_FIELD = "m_DefaultPose"


def _skeleton_and_pose(data):
    """``(nodes, crc32 ids, per-node local TRS)`` of the avatar's full skeleton --
    the one place either reader below decides what an avatar's rest IS."""
    constant = _unwrap(data.get("m_Avatar") or {})
    skeleton = _unwrap(constant.get("m_AvatarSkeleton") or {})
    pose = _unwrap(constant.get(_SKELETON_POSE_FIELD) or {})
    return (skeleton.get("m_Node") or [],
            [value & 0xFFFFFFFF for value in _int_array(skeleton.get("m_ID") or [])],
            pose.get("m_X") or [])


def _trs_matrix(np, x):
    """Local TRS entry {t, q, s} -> 4x4 (Unity space), scale before rotation."""
    if not isinstance(x, dict):
        return np.eye(4, dtype=np.float64)
    t = x.get("t") or {}
    q = x.get("q") or {}
    s = x.get("s") or {}
    w = float(q.get("w", 1.0)); qx = float(q.get("x", 0.0))
    qy = float(q.get("y", 0.0)); qz = float(q.get("z", 0.0))
    rot = np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * w), 2 * (qx * qz + qy * w)],
        [2 * (qx * qy + qz * w), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * w)],
        [2 * (qx * qz - qy * w), 2 * (qy * qz + qx * w), 1 - 2 * (qx * qx + qy * qy)]],
        dtype=np.float64)
    scale = np.array([float(s.get("x", 1.0)), float(s.get("y", 1.0)), float(s.get("z", 1.0))])
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rot * scale[None, :]
    matrix[0, 3] = float(t.get("x", 0.0))
    matrix[1, 3] = float(t.get("y", 0.0))
    matrix[2, 3] = float(t.get("z", 0.0))
    return matrix


def skeleton_world_rests(data):
    """``{crc32(path): Unity world rest 4x4}`` for the avatar's FULL skeleton.

    The rest a shared-skeleton part mesh is bind-baked against lives in
    ``m_AvatarSkeleton`` (the whole transform tree, parent links + CRC32-of-path
    ids) posed by the array ``_SKELETON_POSE_FIELD`` names (per-node local TRS)
    -- NOT ``m_Human.m_Skeleton`` (the 24-bone humanoid subset, normalised to a
    different, near-origin frame). World matrices are numpy 4x4 in Unity space, so
    ``skinning.bake_bind_pose`` and ``coordinate.convert_matrix`` both consume them
    directly. Empty when the avatar carries no full skeleton pose."""
    import numpy as np

    nodes, ids, locals_ = _skeleton_and_pose(data)
    if not nodes or not locals_:
        return {}

    world = [None] * len(nodes)

    def resolve(index):
        if world[index] is not None:
            return world[index]
        local = _trs_matrix(np, locals_[index] if index < len(locals_) else None)
        parent = int(nodes[index].get("m_ParentId", -1))
        world[index] = resolve(parent) @ local if 0 <= parent < len(nodes) else local
        return world[index]

    for index in range(len(nodes)):
        resolve(index)
    return {ids[index]: world[index] for index in range(len(nodes)) if index < len(ids)}


def skeleton_nodes(data):
    """The avatar's full embedded skeleton -- one entry per raw node in m_ParentId's index
    space: ``(name, parent_index, (tx,ty,tz), (qw,qx,qy,qz), path)``. Sourced from
    ``m_AvatarSkeleton`` + the pose array ``_SKELETON_POSE_FIELD`` names (the whole transform
    tree, present for humanoid and generic avatars alike -- m_Human.m_Skeleton is only the
    normalised 24-bone humanoid subset, see skeleton_world_rests). An armature built from
    this therefore rests exactly where the meshes that skin to it were bound. Names resolve
    through TOS path leaves, falling back to ``bone_{i}`` when TOS misses the node."""
    tos = _parse_tos(data)
    nodes, skel_ids, rest = _skeleton_and_pose(data)

    result = []
    for index in range(len(nodes)):
        parent = int(nodes[index].get("m_ParentId", -1))
        path = tos.get(skel_ids[index]) if index < len(skel_ids) else None
        name = path.rsplit("/", 1)[-1] if path else f"bone_{index}"
        if index < len(rest):
            x = rest[index]
            t = (float(x["t"]["x"]), float(x["t"]["y"]), float(x["t"]["z"]))
            q = (float(x["q"]["w"]), float(x["q"]["x"]), float(x["q"]["y"]), float(x["q"]["z"]))
        else:
            t = (0.0, 0.0, 0.0)
            q = (1.0, 0.0, 0.0, 0.0)
        result.append((name, parent, t, q, path))
    return result
