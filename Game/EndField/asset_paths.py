"""Rules over Unity addressable / asset paths.

Pure string work over the paths the game's own data hands back (Endfield's
StringPathHash LUT: ``.../models/s_building.fbx##building_col1``,
``.../Prefabs/P_anm_com_satellite+1_001_01.prefab``). No host, no db, no I/O.

The BULK consumer of these rules -- selecting one detail level across a scene
window's 10^5 placements -- lives in C# (SceneAssetPaths in Ruri.RipperHook),
next to the rows it filters. What stays here serves the character-side flows,
which rank a handful of candidate paths per click, and the import-side join of
an asset path to its named mesh sub-object.
"""

from __future__ import annotations

import re

_LOD_SUFFIX_RE = re.compile(r"_lod(\d+)$", re.IGNORECASE)
_VARIANT_SUFFIX_RE = re.compile(r"_(?:lod\d+|col\d+_[a-z]+\d*)$", re.IGNORECASE)
_COL_SUFFIX_RE = re.compile(r"_col\d+_", re.IGNORECASE)


def expected_mesh_name(asset_path):
    """The specific named sub-object an asset path refers to: either the
    ``##subname`` suffix (a multi-object FBX, e.g.
    ``...building.fbx##building_col1``), or the file stem for a bare
    single-object ``.mesh`` path (Unity's convention: a standalone .mesh
    asset's own Mesh object is named after the file).

    Lowercased, because the hash-LUT-resolved AssetPath is consistently
    all-lowercase while a real Mesh's m_Name preserves its authored casing
    (confirmed against the real game: AssetPath ``...col1_um01`` vs the actual
    m_Name ``...COL1_UM01``) -- the same case-insensitive join the cabmap's own
    container-path normalization already needed, for the same reason."""
    if "##" in asset_path:
        return asset_path.split("##", 1)[1].lower()
    leaf = asset_path.rsplit("/", 1)[-1]
    return (leaf.rsplit(".", 1)[0] if "." in leaf else leaf).lower()


def lod_rank(asset_path):
    """Lower is more preferred: lod0=0, lod1=1, ..., unsuffixed/unleveled=-1
    (as good as lod0 -- a single-LOD piece), collision meshes (``_colN_xxx``)
    last (rank 1000) since they routinely ship with zero render geometry, so
    they are only ever taken when nothing else in the group exists at all."""
    name = expected_mesh_name(asset_path)
    match = _LOD_SUFFIX_RE.search(name)
    if match:
        return int(match.group(1))
    if _COL_SUFFIX_RE.search(name):
        return 1000
    return -1


def lod_family_stem(name):
    """A mesh/asset name with its LOD or collision variant suffix stripped -- the
    key that groups an asset's parallel detail-level siblings so one level can be
    kept out of a closure that ships every LOD."""
    return _VARIANT_SUFFIX_RE.sub("", expected_mesh_name(name))


def is_full_prefab_path(asset_path):
    """True when a placement's resolved asset_path is itself a real ``.prefab``
    (the DynamicScene family -- Model/Effect/Tree -- which always resolves to a
    full authored prefab carrying its own Renderer + Materials) rather than a
    raw FBX mesh sub-asset (the Streaming family's ``...fbx##subname`` shape,
    which needs the separate mesh + material-hash resolution path)."""
    return asset_path.lower().endswith(".prefab")


def prefab_asset_stem(asset_path):
    """Basename without extension, lowercased -- the key a resolved .prefab
    path joins to a prefab-name index on."""
    leaf = asset_path.rsplit("/", 1)[-1]
    stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    return stem.lower()
