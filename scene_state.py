"""Plain-Python (non-bpy) backing store for the scene-import feature:
discovered maps, the current map's placements, and the cheap fidelity
estimate the panel shows before committing to an import.

Deliberately not bpy CollectionProperty-backed, for the same reason
cabmap_state.py isn't: a real map's placement count runs into the
thousands, and that data is only ever read in bulk (discover -> estimate ->
import) -- never edited row-by-row the way the cabmap browser's filter/sort
UI needs, so there's no reason to pay bpy RNA allocation cost for it.
"""

from __future__ import annotations

import os

try:
    from . import prefab_importer
except ImportError:  # standalone (non-package) testing
    import prefab_importer

DISCOVERING_LABEL = "Discovering..."

MAPS = []              # list[str] -- discover_maps() output
PLACEMENTS = []         # list[dict] -- discover_placements() output for CURRENT_MAP
RESOLVED_CABS = []      # list[str] -- resolve_cabs() output, the CABs an import needs
CURRENT_MAP = ""
STATUS = "No map discovered yet."


def vfs_roots(game_root):
    """VFS root paths in priority order: the hot-update overlay first, then
    the base client. Both are needed together -- a patch's manifest can list
    a chunk it never duplicated because that patch didn't change it, so a
    chunk can only be found under the base client even though the overlay's
    manifest mentions it too (confirmed against the real game; see
    RipperBlenderBridge.ExtractFirstAvailable's doc comment in Ruri.RipperHook)."""
    return [
        os.path.join(game_root, "Endfield_Data", "Persistent", "VFS"),
        os.path.join(game_root, "Endfield_Data", "StreamingAssets", "VFS"),
    ]


def discover_maps(bridge, game_root):
    global MAPS
    MAPS = bridge.enumerate_scene_maps(vfs_roots(game_root))
    return MAPS


def discover_placements(bridge, game_root, map_name):
    """Discover every mesh-bearing placement for map_name. Resets state tied
    to whatever map was previously discovered -- a different map's estimate
    isn't meaningful once the underlying placement list has changed."""
    global PLACEMENTS, CURRENT_MAP, STATUS
    PLACEMENTS = bridge.discover_scene_placements(vfs_roots(game_root), map_name)
    CURRENT_MAP = map_name
    STATUS = f"Discovered {len(PLACEMENTS)} placement(s) for {map_name}."
    return PLACEMENTS


def placeable(lod0_only=False):
    """Placements with a ground-truth-verified transform and a resolved
    asset path -- see RipperBlenderBridge.DiscoverScenePlacements' doc
    comment (Ruri.RipperHook) for the three transform sources this covers
    (ECS blob LocalToWorld, FBPropertyBytesData pose, FBPropertyBoundsData.
    Center -- the third tier was previously missing, which silently excluded
    large static architecture like floors/walls/terrain that carries only a
    bounds-center transform; see EndfieldSceneBridge.DecodeStreamingChunk-
    Placements). Placements without any of the three are excluded entirely,
    not placed at the origin -- a Mono/Proxy entity with no resolvable
    transform isn't geometry and doesn't need placing. lod0_only additionally
    keeps only the best-AVAILABLE LOD sibling per placement instance (see
    prefab_importer.select_best_lod) instead of blindly dropping every
    non-zero-LOD-suffixed entity -- a piece whose ONLY shipped variant is,
    say, _lod2 (no _lod0 sibling exists at all for that instance) used to be
    dropped entirely by the old per-entity suffix filter, silently deleting
    real, visible-in-game geometry (confirmed: this is exactly what dropped
    base01_lv002's building-shell/floor piece -- its only siblings were
    _lod2 and a collision-only _col1, no _lod0 at all)."""
    rows = [p for p in PLACEMENTS if p["has_transform"] and p["asset_path"]]
    if lod0_only:
        rows = prefab_importer.select_best_lod(rows)
    return rows


def estimate(lod0_only=False):
    """Cheap summary for the pre-import confirm step: distinct assets, total
    placements, how many are placeable vs. excluded -- split into the two
    genuinely different exclusion reasons (previously conflated into one
    misleading "excluded (no transform)" UI label): a placement with no
    resolvable transform/asset_path at all (no_transform) vs. one that DOES
    have both but got dropped by the LOD0-only filter as a non-zero-LOD
    duplicate of a piece already covered by its LOD0 (lod_filtered). On a
    real map lod_filtered is normally the much larger bucket -- most pieces
    ship their whole LOD1/2/3/... chain alongside LOD0 -- so reporting both
    under "no transform" reads as far more data loss than is actually
    happening. And (once resolve_cabs() has run) how many CABs those
    resolve to."""
    with_transform = [p for p in PLACEMENTS if p["has_transform"] and p["asset_path"]]
    placeable_rows = placeable(lod0_only)
    distinct = {p["asset_path"] for p in placeable_rows}
    return {
        "total_placements": len(PLACEMENTS),
        "placeable": len(placeable_rows),
        "excluded": len(PLACEMENTS) - len(placeable_rows),
        "no_transform": len(PLACEMENTS) - len(with_transform),
        "lod_filtered": len(with_transform) - len(placeable_rows) if lod0_only else 0,
        "distinct_assets": len(distinct),
        "resolved_cabs": len(RESOLVED_CABS),
    }


def resolve_cabs(bridge, lod0_only=False):
    """Resolve every placeable placement's distinct asset path to the CAB
    names hosting them (requires a loaded cabmap on bridge), PLUS every
    distinct material_asset_paths entry (see discover_placements --
    ultimately EndfieldSceneBridge.cs's FBPropertyAssetData AssetType==1
    resolution: the entity's own real material hash, the same StringPathHash
    LUT as its mesh) so real materials -- and their own texture dependencies
    -- come along in the same closure; paths that don't resolve are silently
    dropped by resolve_cabs_for_paths, same as any other unmatched path.
    Populates RESOLVED_CABS -- the seed set ImportCabs needs to pull in the
    whole scene's dependency closure (geometry + materials + textures) in
    one call."""
    global RESOLVED_CABS
    rows = placeable(lod0_only)
    mesh_paths = {p["asset_path"] for p in rows}
    material_paths = {path for p in rows for path in (p.get("material_asset_paths") or ())}
    all_paths = sorted(mesh_paths | material_paths)
    RESOLVED_CABS = bridge.resolve_cabs_for_paths(all_paths) if all_paths else []
    return RESOLVED_CABS


def reset():
    global MAPS, PLACEMENTS, RESOLVED_CABS, CURRENT_MAP, STATUS
    MAPS = []
    PLACEMENTS = []
    RESOLVED_CABS = []
    CURRENT_MAP = ""
    STATUS = "No map discovered yet."
