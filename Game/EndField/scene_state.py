"""The scene-import model: the game's own scene list with its own names, one
map's chunk inventory, and the placements of whichever streaming window is
selected.

Two things here are declarations rather than logic. The names come out of the
game's own config containers, exactly the way the roster's do -- which container,
which field path, which text container to resolve the id through -- and nothing
is parsed on this side:

    MapIdTable      the open-world maps, keyed by the same id the streaming data
                    is filed under ("map02" -> "武陵")
    LevelDescTable  every level the game names, same shape ("dung01_wrdg001" ->
                    "谷地像差")

And the window is the game's own: a disc of chunk cells around a centre cell,
gated by scene state, which is literally what the running game holds
(StreamingSceneV2.primaryStreamingSourceData = StreamingUnsafeUtilsV2.
CreateStreamingSourceData(streamingPos, chunkLoadRadius, ...) plus
StreamingSceneV2.SetSceneState). Selecting the cells and bounding the map-wide
chunks against them happens in C#; this file only says which window to look at.

Same reasoning as cabmap_state for holding this as plain data: a real map's
placement count runs into the hundreds of thousands and is only ever read in
bulk (discover -> estimate -> resolve -> import), never edited row by row.
"""

from __future__ import annotations

from . import asset_paths, roster

# Holds the current window's discovered-but-not-yet-imported placements, so a
# host's script reload skips this module instead of throwing that discovery away.
HOLDS_PROCESS_STATE = True

MAP_ID_TABLE = "MapIdTable"
LEVEL_DESC_TABLE = "LevelDescTable"

# Chunk cells around the centre, each way. One cell of a real map is already a
# few hundred CABs; the whole of map02 is 26811, which is where "150 GB" comes
# from. Start at the smallest window that shows a piece of world in context.
DEFAULT_RADIUS = 1

MAPS = []               # list[dict] -- one per scene the game ships streaming data for
CHUNKS = {}             # map id -> list[dict], the cached per-map chunk inventory
PLACEMENTS = []         # list[dict] -- the current window's placements
RESOLVED_CABS = []      # list[str] -- the seed CABs an import of that window needs
CLOSURE_CABS = 0        # how many CABs those seeds pull in, the real memory proxy
CURRENT_MAP = ""
CURRENT_WINDOW = ()     # (center_x, center_y, radius, scene_state_id)
STATUS = "Refresh to read the game's scene list."


def vfs_roots(game_root):
    """VFS root paths in priority order -- the hot-update overlay first, then the
    base client. Both are needed together: a patch's manifest can list a chunk it
    never duplicated because that patch didn't change it (see
    RipperBlenderBridge.ExtractFirstAvailable in Ruri.RipperHook)."""
    return roster.vfs_roots(game_root)


def name_columns(language):
    """One column: the scene's display name, resolved through the chosen
    language's text container. Both naming tables carry it under the same field,
    so one declaration serves both."""
    return [("display", "showName.id", roster.text_container(language), "")]


def load_maps(bridge, game_root, language):
    """Every scene the game ships streaming data for, under the name the game
    itself shows for it.

    LevelDescTable is read first and MapIdTable second, so the open-world maps
    take their map-level name where both name the same id. A scene neither names
    keeps its own id as its label -- the game ships no other name for it."""
    global MAPS, STATUS
    roots = vfs_roots(game_root)
    names = {}
    for table in (LEVEL_DESC_TABLE, MAP_ID_TABLE):
        rows = bridge.query_data_table(roots, roster.container(table), name_columns(language))
        for index in range(rows.row_count):
            display = rows.cell(index, "display")
            if display:
                names[rows.cell(index, "key")] = display
    MAPS = [{"id": scene_id, "label": names.get(scene_id, scene_id),
             "named": scene_id in names, "group": family(scene_id)}
            for scene_id in bridge.enumerate_scene_maps(roots)]
    named = sum(1 for row in MAPS if row["named"])
    STATUS = "{0} scenes, {1} named · {2}".format(len(MAPS), named, language)
    return MAPS


def family(scene_id):
    """The scene id's own family prefix ("dung01_cdg005" -> "dung01"), which is
    how the game files them. Only used to group the drawn list."""
    return scene_id.split("_", 1)[0]


def load_chunks(bridge, game_root, map_name):
    """One map's chunk inventory, read from the VFS manifests alone -- no chunk
    byte is touched. Cached per map: it never changes under a session, and the
    window controls read it on every redraw."""
    if map_name not in CHUNKS:
        CHUNKS[map_name] = bridge.enumerate_scene_chunks(vfs_roots(game_root), map_name)
    return CHUNKS[map_name]


def grid_extent(chunks):
    """(min_x, max_x, min_y, max_y) over the cells the map actually ships, or
    None when it ships none that a cell window can address."""
    cells = [c for c in chunks if c["has_grid"]]
    if not cells:
        return None
    return (min(c["grid_x"] for c in cells), max(c["grid_x"] for c in cells),
            min(c["grid_y"] for c in cells), max(c["grid_y"] for c in cells))


def scene_states(chunks):
    """The scene states this map ships, lowest first. States are alternate
    dressings of the same cells (a quest changing what stands there), so a window
    normally wants exactly one."""
    return sorted({c["scene_state_id"] for c in chunks})


def busiest_cell(chunks, scene_state_id):
    """The cell this map ships the most bytes for, at that scene state -- a
    landing spot for the window, not a claim about where the map's content is.
    None when the state has no cell-addressable chunk at all."""
    cells = [c for c in chunks if c["has_grid"] and c["scene_state_id"] == scene_state_id]
    if not cells:
        return None
    busiest = max(cells, key=lambda c: c["length"])
    return (busiest["grid_x"], busiest["grid_y"])


def inventory_summary(chunks):
    """What the map ships, split the way a window can address it: cell-anchored
    chunks versus the map-wide and dynamic ones that no cell window selects and
    that get bounded against the window's cells instead."""
    anchored = [c for c in chunks if c["has_grid"]]
    floating = [c for c in chunks if not c["has_grid"]]
    return {
        "anchored_files": len(anchored),
        "anchored_bytes": sum(c["length"] for c in anchored),
        "floating_files": len(floating),
        "floating_bytes": sum(c["length"] for c in floating),
    }


def discover_placements(bridge, game_root, map_name, center_x, center_y, radius, scene_state_id):
    """The placements of one streaming window. Resets state tied to whatever
    window was discovered before -- a different window's estimate is not
    meaningful once the placement list has changed."""
    global PLACEMENTS, RESOLVED_CABS, CLOSURE_CABS, CURRENT_MAP, CURRENT_WINDOW, STATUS
    PLACEMENTS = bridge.discover_scene_placements(
        vfs_roots(game_root), map_name, center_x, center_y, radius, [scene_state_id])
    RESOLVED_CABS = []
    CLOSURE_CABS = 0
    CURRENT_MAP = map_name
    CURRENT_WINDOW = (center_x, center_y, radius, scene_state_id)
    STATUS = "{0} placement(s) in {1} ({2},{3}) r{4} state {5}.".format(
        len(PLACEMENTS), map_name, center_x, center_y, radius, scene_state_id)
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
    asset_paths.select_best_lod) instead of blindly dropping every
    non-zero-LOD-suffixed entity -- a piece whose ONLY shipped variant is,
    say, _lod2 (no _lod0 sibling exists at all for that instance) used to be
    dropped entirely by the old per-entity suffix filter, silently deleting
    real, visible-in-game geometry (confirmed: this is exactly what dropped
    base01_lv002's building-shell/floor piece -- its only siblings were
    _lod2 and a collision-only _col1, no _lod0 at all)."""
    rows = [p for p in PLACEMENTS if p["has_transform"] and p["asset_path"]]
    if lod0_only:
        rows = asset_paths.select_best_lod(rows)
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
    happening. And (once resolve_cabs() has run) the CABs those resolve to:
    the seeds, and the closure those seeds pull in, which is what an import
    actually has to hold."""
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
        "closure_cabs": CLOSURE_CABS,
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
    whole window's dependency closure (geometry + materials + textures) in
    one call -- and CLOSURE_CABS, how big that closure is, which is the one
    number that says whether the window fits in memory before paying for it."""
    global RESOLVED_CABS, CLOSURE_CABS
    rows = placeable(lod0_only)
    mesh_paths = {p["asset_path"] for p in rows}
    material_paths = {path for p in rows for path in (p.get("material_asset_paths") or ())}
    all_paths = sorted(mesh_paths | material_paths)
    RESOLVED_CABS = bridge.resolve_cabs_for_paths(all_paths) if all_paths else []
    CLOSURE_CABS = len(bridge.resolve_closure_cab_names(RESOLVED_CABS)) if RESOLVED_CABS else 0
    return RESOLVED_CABS


def reset():
    global MAPS, CHUNKS, PLACEMENTS, RESOLVED_CABS, CLOSURE_CABS, CURRENT_MAP, CURRENT_WINDOW, STATUS
    MAPS = []
    CHUNKS = {}
    PLACEMENTS = []
    RESOLVED_CABS = []
    CLOSURE_CABS = 0
    CURRENT_MAP = ""
    CURRENT_WINDOW = ()
    STATUS = "Refresh to read the game's scene list."
