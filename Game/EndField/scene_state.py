"""The scene-import model: the game's own scene list with its own names, split
the way the game itself splits it, and the placements of whichever piece is
selected.

Nothing here reads the game. Which containers carry a scene's name, which place
belongs to which map, and how the ids group are stated by the hook
(``endfield.scene.maps`` / ``endfield.scene.landmarks``); this module holds the
selection they feed.

The split between the two kinds of scene is the game's own, not a guess from
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

from . import datasets

# Holds the current window's discovered-but-not-yet-imported placements, so a
# host's script reload skips this module instead of throwing that discovery away.
HOLDS_PROCESS_STATE = True

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
SUMMARIES = {}          # map id -> dict, the cached per-map chunk summary
TABLE = None            # ColumnTable -- the current selection's IMPORTABLE placements, columnar
MATERIALS_BY_ROW = {}   # {placement row -> tuple of material container paths}
COUNTS = {}             # the discovery's own counts (total/no_transform/lod_filtered/distinct_assets)
SEED_PATHS = []         # list[str] -- the container paths the C# reduction extracted
RESOLVED_CABS = []      # list[str] -- the seed CABs an import of it needs
CLOSURE_CABS = 0        # how many CABs those seeds pull in, the real memory proxy
CURRENT_MAP = ""
CURRENT_WINDOW = ()     # (min_x, min_z, max_x, max_z, scene_state_id, detail_level)
STATUS = "Refresh to read the game's scene list."


def placeable_count():
    return 0 if TABLE is None else len(TABLE)


def load_scenes(language):
    """Read the game's scene list, already split into the two kinds and already
    named: which containers carry a scene's name, which place belongs to which
    map, and how the ids group are the game's own facts, stated by the hook."""
    global SCENES, LANDMARKS, STATUS

    scenes = datasets.scene_maps(language)
    LANDMARKS = {scene["id"]: [] for scene in scenes}
    for place in datasets.landmarks(language):
        rows = LANDMARKS.get(place["scene"])
        if rows is not None:
            rows.append({key: place[key] for key in ("id", "label", "named", "rect")})
    for rows in LANDMARKS.values():
        rows.sort(key=lambda row: row["id"])

    SCENES = {SELF_CONTAINED: [], STREAMING: []}
    for scene in scenes:
        kind = STREAMING if scene["streaming"] else SELF_CONTAINED
        SCENES[kind].append({key: scene[key] for key in ("id", "label", "named", "group")})
    STATUS = "{0} scenes ({1} streaming, {2} named) · {3}".format(
        len(scenes), len(SCENES[STREAMING]),
        sum(1 for row in scenes if row["named"]), language)
    return SCENES


def scaled(rect, scale):
    """A landmark's rect grown or shrunk about its own centre. 1.0 is the size the
    game itself gives the place, which is what the panel starts at."""
    min_x, min_z, max_x, max_z = rect
    centre_x, centre_z = (min_x + max_x) * 0.5, (min_z + max_z) * 0.5
    half_x, half_z = (max_x - min_x) * 0.5 * scale, (max_z - min_z) * 0.5 * scale
    return (centre_x - half_x, centre_z - half_z, centre_x + half_x, centre_z + half_z)


def load_summary(map_name):
    """One map's chunk inventory, summarized on the C# side that read the
    manifests -- scene states plus the anchored/floating split. Cached per map:
    it never changes under a session, and the controls read it on every
    redraw."""
    if map_name not in SUMMARIES:
        SUMMARIES[map_name] = datasets.chunk_summary(map_name)
    return SUMMARIES[map_name]


def discover_placements(map_name, rect, scene_state_id, detail_level):
    """What one world rect of one map places, reduced on the C# side that
    decoded it: TABLE holds only the importable rows (columnar, see
    datasets.placements), MATERIALS_BY_ROW the sparse per-row material paths,
    SEED_PATHS the container paths an import needs, COUNTS the drop accounting.
    Resets state tied to whatever was discovered before -- a different
    selection's estimate is not meaningful once the placement set has changed."""
    global TABLE, MATERIALS_BY_ROW, COUNTS, SEED_PATHS, RESOLVED_CABS, CLOSURE_CABS
    global CURRENT_MAP, CURRENT_WINDOW, STATUS
    min_x, min_z, max_x, max_z = rect
    # A scene state filter is a LIST on the wire; the panel picks one at a time,
    # and "no state chosen" is the empty list rather than a sentinel number.
    chosen = "" if scene_state_id is None else str(scene_state_id).strip()
    result = datasets.placements(
        map_name, min_x, min_z, max_x, max_z,
        [chosen] if chosen else [], detail_level)
    TABLE = result["table"]
    MATERIALS_BY_ROW = result["materials_by_row"]
    SEED_PATHS = result["seed_paths"]
    COUNTS = {key: result[key] for key in ("total", "no_transform", "lod_filtered", "distinct_assets")}
    RESOLVED_CABS = []
    CLOSURE_CABS = 0
    CURRENT_MAP = map_name
    CURRENT_WINDOW = (min_x, min_z, max_x, max_z, scene_state_id, detail_level)
    STATUS = "{0} placement(s) in {1}, state {2}.".format(len(TABLE), map_name, scene_state_id)
    return TABLE


def estimate():
    """The pre-import confirm numbers, straight off the C# reduction (COUNTS)
    plus the CAB resolution once resolve_cabs() has run. Nothing is recomputed
    here: the panel redraws on every mouse move, and a real window's row set is
    10^5 -- which is exactly why the reduction lives on the side that already
    held the rows. The two exclusion buckets stay separate (previously
    conflated into one misleading "excluded (no transform)" label): no_transform
    is a row that was never geometry, lod_filtered a real piece dropped only
    because a better detail level of the same instance is already kept -- on a
    real map the much larger bucket, and reporting both as "no transform" reads
    as far more data loss than is happening."""
    return {
        "total_placements": COUNTS.get("total", 0),
        "placeable": placeable_count(),
        "no_transform": COUNTS.get("no_transform", 0),
        "lod_filtered": COUNTS.get("lod_filtered", 0),
        "distinct_assets": COUNTS.get("distinct_assets", 0),
        "resolved_cabs": len(RESOLVED_CABS),
        "closure_cabs": CLOSURE_CABS,
    }


def resolve_cabs(bridge):
    """Resolve the discovery's SEED_PATHS -- every kept placement's distinct
    mesh path PLUS its material paths, already extracted and sorted by the C#
    reduction -- to the CAB names hosting them (requires a loaded cabmap on
    bridge); paths that don't resolve are silently dropped by
    resolve_cabs_for_paths, same as any other unmatched path. Populates
    RESOLVED_CABS -- the seed set ImportCabs needs to pull in the whole
    selection's dependency closure (geometry + materials + textures) in one
    call -- and CLOSURE_CABS, how big that closure is, which is the one number
    that says whether it fits in memory before paying for it."""
    global RESOLVED_CABS, CLOSURE_CABS
    RESOLVED_CABS = bridge.resolve_cabs_for_paths(SEED_PATHS) if SEED_PATHS else []
    CLOSURE_CABS = len(bridge.resolve_closure_cab_names(RESOLVED_CABS)) if RESOLVED_CABS else 0
    return RESOLVED_CABS


def reset():
    global SCENES, LANDMARKS, SUMMARIES, TABLE, MATERIALS_BY_ROW, COUNTS, SEED_PATHS
    global RESOLVED_CABS, CLOSURE_CABS, CURRENT_MAP, CURRENT_WINDOW, STATUS
    SCENES = {SELF_CONTAINED: [], STREAMING: []}
    LANDMARKS = {}
    SUMMARIES = {}
    TABLE = None
    MATERIALS_BY_ROW = {}
    COUNTS = {}
    SEED_PATHS = []
    RESOLVED_CABS = []
    CLOSURE_CABS = 0
    CURRENT_MAP = ""
    CURRENT_WINDOW = ()
    STATUS = "Refresh to read the game's scene list."
