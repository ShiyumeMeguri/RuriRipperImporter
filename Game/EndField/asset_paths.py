"""Rules over Unity addressable / asset paths.

Everything here is pure string work over the paths a scene placement resolves
to (Endfield's StringPathHash LUT hands back things like
``.../models/s_building.fbx##building_col1`` or
``.../Prefabs/P_anm_com_satellite+1_001_01.prefab``), plus the LOD-sibling
selection built on top of them. No host, no db, no I/O -- which also makes it
the one part of scene import that is trivially testable.
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


def lod_group_key(asset_path, px, py, pz):
    """(rounded position, stem with its LOD/collision suffix stripped) --
    identifies the parallel sibling entities a real map places for the SAME
    instance at different detail levels: confirmed against base01_lv002 that a
    numbered-LOD and/or col1-collision sibling sits at the EXACT SAME position
    as its lod0 render counterpart, as separate ECS entities. Position is
    rounded to collapse float noise between siblings placed identically."""
    stem = _VARIANT_SUFFIX_RE.sub("", expected_mesh_name(asset_path))
    return (round(px, 2), round(py, 2), round(pz, 2), stem)


def select_best_lod(rows):
    """Group placements into per-instance LOD-sibling sets (lod_group_key) and
    keep only the best-ranked (lod_rank) member of each.

    Deliberately NOT a "drop everything whose name isn't lod0" filter: that
    assumes a LOD0 sibling always exists, and when it doesn't (only
    _lod1/_lod2/_col1 variants were ever placed for that instance) it drops the
    instance entirely -- confirmed as exactly what silently deleted
    base01_lv002's building-shell/floor piece, which the game does ship, just
    not at LOD0."""
    groups = {}
    for row in rows:
        key = lod_group_key(row["asset_path"], row["px"], row["py"], row["pz"])
        groups.setdefault(key, []).append(row)
    return [min(members, key=lambda r: lod_rank(r["asset_path"]))
            for members in groups.values()]


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
