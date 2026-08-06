"""The scene-import model: the game's own scene list with its own names, split
the way the game itself splits it, and the placements of whichever piece is
selected.

Everything here is a DECLARATION over the game's own data. The names come out of
its config containers, exactly the way the roster's do -- which container, which
field path, which text container to resolve the id through -- and nothing is
parsed on this side:

    MapIdTable      the open-world maps, keyed by the same id the streaming data
                    is filed under ("map02" -> "武陵")
    LevelDescTable  every level the game names, same shape ("map01_lv007" ->
                    "供能高地")

The split between the two kinds of scene is the game's own too, not a guess from
names. Its map UI publishes one entry per place it can display, each with a world
rect and an ``isSingleLevel`` flag (see EndfieldSceneLandmarks in
Ruri.RipperHook). A place that is its own level is a SELF-CONTAINED scene, small
enough to load whole. A place that is not belongs to the open-world map whose id
it starts with, and that map is a STREAMING scene: far too big to hold at once
(map02 whole is 26811 CABs), so it is imported one named place at a time, at the
size the game itself gives that place.

Same reasoning as cabmap_state for holding this as plain data: a real map's
placement count runs into the hundreds of thousands and is only ever read in bulk
(discover -> estimate -> resolve -> import), never edited row by row.
"""

from __future__ import annotations

from . import asset_paths, roster

# Holds the current window's discovered-but-not-yet-imported placements, so a
# host's script reload skips this module instead of throwing that discovery away.
HOLDS_PROCESS_STATE = True

MAP_ID_TABLE = "MapIdTable"
LEVEL_DESC_TABLE = "LevelDescTable"

# A whole scene, as a rect no piece of world falls outside of.
WHOLE = (float("-inf"), float("-inf"), float("inf"), float("inf"))

# What an import actually costs in memory, measured rather than guessed: five real
# imports in a headless Blender, peak working set straight from the OS --
#   340 CABs -> 6.43 GB   925 -> 8.15 GB   2970 -> 22.36 GB
#   3258 -> 20.68 GB      3878 -> 22.00 GB
# which is a fixed base (Blender + the CLR + the loaded cabmap) plus a per-CAB
# slope. The fit below sits within ~3.6 GB of all five and deliberately runs HIGH
# at the top end, so the panel warns early rather than late; extrapolated, a whole
# map02 lands at ~154 GB, which is what a whole-map import was actually observed
# to do. Re-measure and update these two numbers if the import path's memory
# behaviour changes; they are the only thing standing between a user and a machine
# that starts swapping.
MEMORY_BASE_GB = 3.9
MEMORY_PER_CAB_GB = 0.0056


def projected_peak_gb(closure_cabs):
    """Roughly what importing a closure of this size will peak at, in GB."""
    return MEMORY_BASE_GB + MEMORY_PER_CAB_GB * closure_cabs


def system_memory_gb():
    """This machine's physical RAM, or None where it cannot be read -- the panel
    only compares against it when it knows it."""
    import ctypes
    if not hasattr(ctypes, "WinDLL"):
        return None

    class _Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64)]

    status = _Status()
    status.dwLength = ctypes.sizeof(_Status)
    try:
        if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except OSError:
        return None
    return status.ullTotalPhys / (1024.0 ** 3)

SELF_CONTAINED = "self_contained"
STREAMING = "streaming"

SCENES = {SELF_CONTAINED: [], STREAMING: []}   # kind -> list[dict]
LANDMARKS = {}          # streaming map id -> list[dict], its named places
CHUNKS = {}             # map id -> list[dict], the cached per-map chunk inventory
PLACEMENTS = []         # list[dict] -- the current selection's placements
RESOLVED_CABS = []      # list[str] -- the seed CABs an import of it needs
CLOSURE_CABS = 0        # how many CABs those seeds pull in, the real memory proxy
CURRENT_MAP = ""
CURRENT_WINDOW = ()     # (min_x, min_z, max_x, max_z, scene_state_id)
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


def load_scenes(bridge, game_root, language):
    """Read the game's scene list, split into the two kinds, with the names the
    game itself shows.

    LevelDescTable is read first and MapIdTable second, so an open-world map takes
    its map-level name where both name the same id. A scene neither names keeps
    its own id as its label -- the game ships no other name for it."""
    global SCENES, LANDMARKS, STATUS
    roots = vfs_roots(game_root)

    names = {}
    for table in (LEVEL_DESC_TABLE, MAP_ID_TABLE):
        rows = bridge.query_data_table(roots, roster.container(table), name_columns(language))
        for index in range(rows.row_count):
            display = rows.cell(index, "display")
            if display:
                names[rows.cell(index, "key")] = display

    scene_ids = bridge.enumerate_scene_maps(roots)
    places = bridge.scene_landmarks(roots)

    # A place that is not its own level belongs to the streaming map whose id it
    # starts with -- matched against the real scene list, so nothing is inferred
    # from the shape of a name.
    LANDMARKS = {scene_id: [] for scene_id in scene_ids}
    for place in places:
        if place["is_single_level"]:
            continue
        for scene_id in scene_ids:
            if place["level_id"].startswith(scene_id + "_"):
                LANDMARKS[scene_id].append(_named(place, names))
                break
    for rows in LANDMARKS.values():
        rows.sort(key=lambda row: row["id"])

    SCENES = {SELF_CONTAINED: [], STREAMING: []}
    for scene_id in scene_ids:
        kind = STREAMING if LANDMARKS[scene_id] else SELF_CONTAINED
        SCENES[kind].append({"id": scene_id, "label": names.get(scene_id, scene_id),
                             "named": scene_id in names, "group": family(scene_id)})
    STATUS = "{0} scenes ({1} streaming, {2} named) · {3}".format(
        len(scene_ids), len(SCENES[STREAMING]),
        sum(1 for rows in SCENES.values() for row in rows if row["named"]), language)
    return SCENES


def _named(place, names):
    """One landmark row: the game's id, the game's name for it, and the rect the
    game gives it."""
    return {
        "id": place["level_id"],
        "label": names.get(place["level_id"], place["level_id"]),
        "named": place["level_id"] in names,
        "rect": (place["min_x"], place["min_z"], place["max_x"], place["max_z"]),
    }


def scaled(rect, scale):
    """A landmark's rect grown or shrunk about its own centre. 1.0 is the size the
    game itself gives the place, which is what the panel starts at."""
    min_x, min_z, max_x, max_z = rect
    centre_x, centre_z = (min_x + max_x) * 0.5, (min_z + max_z) * 0.5
    half_x, half_z = (max_x - min_x) * 0.5 * scale, (max_z - min_z) * 0.5 * scale
    return (centre_x - half_x, centre_z - half_z, centre_x + half_x, centre_z + half_z)


def family(scene_id):
    """The scene id's own family prefix ("dung01_cdg005" -> "dung01"), which is
    how the game files them. Only used to group the drawn list."""
    return scene_id.split("_", 1)[0]


def load_chunks(bridge, game_root, map_name):
    """One map's chunk inventory, read from the VFS manifests alone -- no chunk
    byte is touched. Cached per map: it never changes under a session, and the
    controls read it on every redraw."""
    if map_name not in CHUNKS:
        CHUNKS[map_name] = bridge.enumerate_scene_chunks(vfs_roots(game_root), map_name)
    return CHUNKS[map_name]


def scene_states(chunks):
    """The scene states this map ships, lowest first. States are alternate
    dressings of the same world (a quest changing what stands there), so a load
    normally wants exactly one."""
    return sorted({c["scene_state_id"] for c in chunks})


def inventory_summary(chunks):
    """What the map ships, split the way a window can address it: cell-anchored
    chunks versus the map-wide and dynamic ones that no rect selects by name and
    that get bounded against the window's cells instead."""
    anchored = [c for c in chunks if c["has_grid"]]
    floating = [c for c in chunks if not c["has_grid"]]
    return {
        "anchored_files": len(anchored),
        "anchored_bytes": sum(c["length"] for c in anchored),
        "floating_files": len(floating),
        "floating_bytes": sum(c["length"] for c in floating),
    }


def discover_placements(bridge, game_root, map_name, rect, scene_state_id):
    """The placements inside one world rect of one map. Resets state tied to
    whatever was discovered before -- a different selection's estimate is not
    meaningful once the placement list has changed."""
    global PLACEMENTS, RESOLVED_CABS, CLOSURE_CABS, CURRENT_MAP, CURRENT_WINDOW, STATUS
    min_x, min_z, max_x, max_z = rect
    PLACEMENTS = bridge.discover_scene_placements(
        vfs_roots(game_root), map_name, min_x, min_z, max_x, max_z, [scene_state_id])
    RESOLVED_CABS = []
    CLOSURE_CABS = 0
    CURRENT_MAP = map_name
    CURRENT_WINDOW = (min_x, min_z, max_x, max_z, scene_state_id)
    STATUS = "{0} placement(s) in {1}, state {2}.".format(len(PLACEMENTS), map_name, scene_state_id)
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
    whole selection's dependency closure (geometry + materials + textures) in
    one call -- and CLOSURE_CABS, how big that closure is, which is the one
    number that says whether it fits in memory before paying for it."""
    global RESOLVED_CABS, CLOSURE_CABS
    rows = placeable(lod0_only)
    mesh_paths = {p["asset_path"] for p in rows}
    material_paths = {path for p in rows for path in (p.get("material_asset_paths") or ())}
    all_paths = sorted(mesh_paths | material_paths)
    RESOLVED_CABS = bridge.resolve_cabs_for_paths(all_paths) if all_paths else []
    CLOSURE_CABS = len(bridge.resolve_closure_cab_names(RESOLVED_CABS)) if RESOLVED_CABS else 0
    return RESOLVED_CABS


def reset():
    global SCENES, LANDMARKS, CHUNKS, PLACEMENTS, RESOLVED_CABS, CLOSURE_CABS
    global CURRENT_MAP, CURRENT_WINDOW, STATUS
    SCENES = {SELF_CONTAINED: [], STREAMING: []}
    LANDMARKS = {}
    CHUNKS = {}
    PLACEMENTS = []
    RESOLVED_CABS = []
    CLOSURE_CABS = 0
    CURRENT_MAP = ""
    CURRENT_WINDOW = ()
    STATUS = "Refresh to read the game's scene list."
