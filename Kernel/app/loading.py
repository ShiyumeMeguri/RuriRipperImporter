"""What a game says one thing is made of, in terms every host understands.

A panel that wants to load a character does not know how to build one -- and
must not, because "build one" means a skeleton and node materials in Blender and
a mesh file plus texture sets in Painter. What it CAN do, without knowing either,
is say what the game states this thing is: which CABs have to be read, and what
the game said is inside them.

That is :class:`Packages`. It is deliberately inert -- resolving one must touch
no scene and resolve no closure, so a whole cast's CABs are known before anything
is read and fifty performers cost one closure rather than fifty.

The host then materialises it (:meth:`Kernel.host.Host.import_packages`). One
handover, named once, instead of every panel growing a branch per host.
"""

from __future__ import annotations

import os

from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import bridge_asset_db

#: The game ships a model prefab: read its CABs and build what is in them.
PREFAB = "prefab"
#: The game ships a RECIPE instead: named meshes out of named CABs, dressed in
#: named materials, bound to a skeleton the game named separately.
PARTS = "parts"
#: The game ships a character as SEVERAL PREFABS that are one person: the first is
#: the skeleton and every other one hangs on a named bone of it, with the game's
#: own correction. What the pieces are, in what order and on which bone is the
#: game's answer (``Packages.parts``); joining them is the host's.
ASSEMBLY = "assembly"
#: The game states the thing as PLACEMENTS it has already decoded: a transform
#: tree whose rows each draw a named mesh, light something, or just carry a
#: transform other rows hang under. An engine that is not Unity reaches every host
#: this way -- its decoder hands over the same normalised forms (a decoded mesh,
#: material properties, a texture source) and never a Unity document.
PLACEMENTS = "placements"
#: The game states a WINDOW of one of its scenes: a columnar placement table the
#: decoder already reduced, plus the container paths whose closure holds what those
#: rows draw. Not PLACEMENTS: a real window's row count runs into the hundreds of
#: thousands, and turning that into one record per row -- to hand each host a list
#: it will only ever read in bulk -- is the cost this kind exists to avoid. The
#: table stays columnar all the way to the host that batches it.
SCENE_WINDOW = "scene_window"

#: What a massed environment window never renders, whichever game states it:
#: animation state (the whole reason the old path paid a per-prefab closure scan)
#: and streamed media. Excluded from the closure crossing rather than skipped
#: afterwards, so nothing is decoded to then be dropped.
WINDOW_EXCLUDED_CLASSES = ("AnimationClip", "AnimatorController",
                           "AnimatorOverrideController", "Avatar", "AudioClip",
                           "VideoClip")


def slot_paths(row, library):
    """The material each of a PLACEMENTS row's slots draws with.

    A row that states any list at all states the WHOLE answer -- the producer
    already applied the rule (the component's override where it states one, else
    the mesh's own), so the mesh's own list is only for a row that renders through
    no component of its own.

    Here rather than with the game that first produced such rows: it is a rule
    about the ROW SHAPE, which is this module's, and both hosts' materialisers
    read it. Asking a game module for it made every host import that game."""
    stated = row["materials"]
    if stated:
        return list(stated)
    entry = library.get(row["mesh"])
    return list(entry[1]) if entry else []


class Built:
    """What materialising one thing produced, and what it could not."""

    __slots__ = ("armature", "manifest", "missing", "warnings", "imported")

    def __init__(self, armature=None, manifest=None, missing=(), warnings=(), imported=0):
        #: The rig the parts were bound to, when this host has rigs.
        self.armature = armature
        self.manifest = manifest
        self.missing = list(missing)
        self.warnings = list(warnings)
        self.imported = imported


class Packages:
    """One loadable thing: what it is, and every CAB the read has to mark."""

    __slots__ = ("key", "label", "kind", "cabs", "manifest", "meshes",
                 "missing", "dressing", "paths", "skeleton", "named_roots",
                 "seeded_only", "parts", "export_class_ids", "placements",
                 "library", "materials", "textures", "window")

    def __init__(self, key, label, kind, cabs, manifest=None, meshes=(),
                 missing=(), dressing=None, paths=(), skeleton=None,
                 named_roots="", seeded_only=False, parts=(), export_class_ids=(),
                 placements=(), library=None, materials=None, textures=None,
                 window=None):
        #: The game's own identifier for this thing.
        self.key = key
        #: What to call it on screen.
        self.label = label
        #: How the game files it -- a prefab, an assembly of parts, ...
        self.kind = kind
        #: Every CAB the closure has to cover.
        self.cabs = list(cabs)
        #: Whatever the game published ABOUT it (an npc part manifest, ...), or
        #: None. Opaque here; the game that made it is the only reader.
        self.manifest = manifest
        #: The mesh names to keep, when the game states a subset rather than
        #: "everything in those CABs".
        self.meshes = list(meshes)
        #: Part slots the game named but resolved to nothing -- reported, never
        #: worked around.
        self.missing = list(missing)
        #: Per-mesh material assignments the game states outside the prefab.
        self.dressing = dressing or {}
        #: Per-mesh addressable paths, when the game addresses by path.
        self.paths = dict(paths)
        #: Keep only the roots that ARE these assets, semicolon-separated, for a
        #: caller that already knows which asset it asked for. A game whose
        #: archives are pooled exports a closure of roots that have nothing to do
        #: with the request, and without this one entry loads a crowd.
        self.named_roots = named_roots
        #: Import exactly the roots THESE CABs seeded, by the bridge's own CAB
        #: attribution. What a caller sharing one closure with other callers
        #: needs: with a whole cast in one closure, "every root the closure
        #: exports" is everybody at once.
        self.seeded_only = seeded_only
        #: For ASSEMBLY: the pieces, in the order they are joined. Each is
        #: ``{"asset", "label", "anchor", "position", "rotation", "scale", "rig"}``
        #: -- the prefab's own name in the closure, what to call it, the BONE of the
        #: rig it hangs on, and the game's own correction stated in the GAME's space
        #: (metres, ZXY euler degrees), which each host converts with its own
        #: converter. ``rig`` marks the one piece that IS the skeleton.
        self.parts = tuple(parts)
        #: For PLACEMENTS: the rows, each ``{name, parent, mesh, materials, light,
        #: active, px..sz}`` -- a transform tree in the GAME's own space, already
        #: decoded. ``parent`` indexes back into this list, and ``materials`` is a
        #: LIST of the paths that row's slots draw with (see :func:`slot_paths`),
        #: normalised by whoever produced the rows rather than left in a decoder's
        #: own joined form.
        self.placements = tuple(placements)
        #: For PLACEMENTS: ``{mesh path: (decoded mesh, its own slot material paths,
        #: bone names, reference-skeleton rows)}``. One entry per DISTINCT mesh --
        #: a level stamping one mesh into hundreds of rows reads it once, and a host
        #: that can share geometry between placements has what it needs to.
        self.library = dict(library or {})
        #: For PLACEMENTS: ``{material path: MaterialProperties}`` -- the normalised
        #: form, never a document, because the engine that stated them may have none.
        self.materials = dict(materials or {})
        #: For PLACEMENTS: where a texture's pixels come from. Duck-typed the same
        #: as the Unity asset database where a material builder touches it
        #: (``resolve_guid`` / ``texture_bytes`` / ``asset_name`` / ``texture_is_srgb``),
        #: so both hosts' texture paths take it unchanged.
        self.textures = textures
        #: Narrow the closure export to these Unity class ids. A game whose bundles
        #: carry an art library no build ever looks at says so here, rather than
        #: paying for it once per import.
        self.export_class_ids = tuple(export_class_ids)
        #: For PARTS: the rest pose the meshes bind to, as the game resolved it
        #: -- (world rests, bone paths, leaf names, avatar data). Resolved by the
        #: game because only the game knows where its avatar template lives;
        #: stated in neutral terms because binding is the host's job.
        self.skeleton = skeleton
        #: For SCENE_WINDOW: what the decoder reduced this window to, columnar --
        #: ``table`` the kept placements, ``materials_by_row`` the sparse per-row
        #: material container paths, ``seeds`` the container paths whose closure
        #: holds what those rows draw, ``named`` what each path IS under the game's
        #: own addressable convention (prefab or loose mesh, and under which name),
        #: ``label`` what the window is called. Read in bulk by whichever host is
        #: building, never row by row here.
        self.window = window

    def __repr__(self):
        return "<Packages {0} ({1} cab(s))>".format(self.key, len(self.cabs))


def resolve_union(hierarchy_cabs, clip_cabs):
    """ONE bridge resolve covering a mixed selection: hierarchy rows plus clip rows.

    A clip row's closure co-seeds most of what a character row's does, so resolving
    the two separately re-read everything the first one had just read. Resolved
    together, the hierarchy import's root set is restricted to ITS OWN sub-closure
    -- a root is dropped only when its CAB attribution places it POSITIVELY outside
    the hierarchy rows' closure, so an unattributed root stays, exactly as inclusive
    as two separate resolves were."""
    seeds = list(hierarchy_cabs)
    for cab in clip_cabs:
        if cab not in seeds:
            seeds.append(cab)
    assets, roots, seed_roots, clips_by_cab, scene_roots = \
        cabmap_state.BRIDGE.import_cabs(seeds)
    union = {name.lower() for name in cabmap_state.BRIDGE.resolve_closure_cab_names(seeds)}
    hierarchy = {name.lower() for name in
                 cabmap_state.BRIDGE.resolve_closure_cab_names(hierarchy_cabs)}
    clip_only = union - hierarchy
    root_cabs = cabmap_state.BRIDGE.root_cabs_by_guid
    kept = [guid for guid in roots if root_cabs.get(guid, "") not in clip_only]
    return {
        "db": bridge_asset_db.BridgeAssetDatabase(
            assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
            mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
            asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid,
            texture_srgb=cabmap_state.BRIDGE.texture_srgb_by_guid),
        "roots": kept,
        "seed_roots": seed_roots,
        "scene_roots": scene_roots,
        "clips_by_cab": clips_by_cab,
    }


def part_correction(part):
    """One assembly piece's own correction, as a matrix in the GAME's space.

    The game states position in metres and rotation as ZXY euler DEGREES, which is
    Unity's own convention -- the same one every transform this toolchain reads is
    written in. Computed here rather than in each host: two hosts each turning
    degrees into a rotation is two conventions that agree until one is edited, and
    both of them already know how to convert a Unity-space matrix into their own."""
    import math

    import numpy as np

    from ...RuriRipperPyBridge.math3d import coordinate as unity_space

    position = tuple(part.get("position") or (0.0, 0.0, 0.0))
    rotation = tuple(part.get("rotation") or (0.0, 0.0, 0.0))
    scale = tuple(part.get("scale") or (1.0, 1.0, 1.0))
    if position == (0.0, 0.0, 0.0) and rotation == (0.0, 0.0, 0.0) and scale == (1.0, 1.0, 1.0):
        return np.eye(4, dtype=np.float64)

    # ZXY intrinsic, which is what Unity's inspector shows and what the game's own
    # tables are written in: q = Y * X * Z applied right to left.
    half = [math.radians(angle) * 0.5 for angle in rotation]
    sines = [math.sin(value) for value in half]
    cosines = [math.cos(value) for value in half]
    quaternions = (
        (sines[0], 0.0, 0.0, cosines[0]),   # X
        (0.0, sines[1], 0.0, cosines[1]),   # Y
        (0.0, 0.0, sines[2], cosines[2]),   # Z
    )
    combined = _multiply(_multiply(quaternions[1], quaternions[0]), quaternions[2])
    return unity_space.unity_trs(
        {"x": position[0], "y": position[1], "z": position[2]},
        {"x": combined[0], "y": combined[1], "z": combined[2], "w": combined[3]},
        {"x": scale[0], "y": scale[1], "z": scale[2]})


def _multiply(left, right):
    """Hamilton product of two (x, y, z, w) quaternions."""
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz)


def avatar_skeleton(db, avatar_file):
    """``(world_rests, paths)`` from an Avatar document in ``db`` -- the standing
    rest a shared-skeleton part mesh bakes against, plus its transform paths.

    Read here rather than in the game that asks for it: an Avatar is Unity's, not
    any title's, and the host materialiser needs the same read for the part-local
    skeleton a mesh introduces."""
    from ...RuriRipperPyBridge.unity import avatar
    document = avatar_file.first("Avatar") if avatar_file is not None else None
    if document is None:
        return {}, []
    return avatar.skeleton_world_rests(document.data), avatar.transform_paths(document.data)


def cabs_of(packages):
    """Every CAB a group of these needs, each counted once -- what one closure
    resolve is asked for."""
    seen = []
    marked = set()
    for one in packages:
        for cab in one.cabs:
            if cab not in marked:
                marked.add(cab)
                seen.append(cab)
    return seen


# ---------------------------------------------------------------------------
# Crossing the bridge
# ---------------------------------------------------------------------------
#: Per install: {cab: archive} and {archive: rows} for the archives that are
#: gone. Session state -- a cabmap load fills it and a rebuild invalidates it.
_ARCHIVES = {}


def _archives():
    """Which archive each CAB lives in, and which of those archives are gone.

    The map names 40-odd chunk files for a quarter-million CABs, so asking the
    filesystem once per ARCHIVE answers it for every CAB in it."""
    key = cabmap_state.active_key()
    if key in _ARCHIVES:
        return _ARCHIVES[key]
    table = cabmap_state.BRIDGE.enumerate_table()
    root = cabmap_state.BRIDGE.game_root or ""
    by_cab = {}
    rows = {}
    for index in range(len(table.cabs)):
        source = str(table.source(index))
        by_cab[table.cab(index)] = source
        rows[source] = rows.get(source, 0) + 1
    gone = {source: count for source, count in rows.items()
            if not os.path.isfile(os.path.join(root, source.replace("\\", "/")))}
    _ARCHIVES[key] = (by_cab, gone)
    return _ARCHIVES[key]


def unreachable_rows():
    """(archives gone, cab rows in them) for the loaded map -- what a rebuild would
    bring back. (0, 0) for a map that matches the install it was built from."""
    try:
        _by_cab, gone = _archives()
    except Exception:
        return 0, 0
    return len(gone), sum(gone.values())


def forget_archives():
    _ARCHIVES.clear()


def unreachable_seeds(seeds):
    """The seeds whose archive the install no longer has, with the archive named.

    Asked BEFORE a crossing, so a load that cannot possibly work says why instead
    of resolving a closure and reporting the empty result as the thing's own
    shape."""
    try:
        by_cab, gone = _archives()
    except Exception:
        return []
    return [(cab, by_cab[cab]) for cab in seeds
            if cab in by_cab and by_cab[cab] in gone]


class StaleCabmapError(RuntimeError):
    """The map names an archive the install no longer has.

    Its own type because the remedy is specific and is not "try again": the game
    replaced a chunk, and the map has to be rebuilt against what is on disk now."""


def resolve_closure(seeds, export_class_ids=None):
    """The one bridge crossing every import flow shares: resolve a cab set's
    dependency closure, export it in memory, and wrap it in the asset db that
    snapshots the bridge's per-call blob maps (clip curves / mesh blobs / asset
    paths, each replaced wholesale by import_cabs -- so the db must be built here,
    right after the crossing, before any other crossing can overwrite them).

    Touches NO bpy on purpose: a modal loader runs this on a worker thread and
    hands the result to the main thread to build from, and a synchronous caller
    runs it inline. Returns {db, roots, seed_roots, clips_by_cab, scene_roots}."""
    seeds = list(seeds)
    stale = unreachable_seeds(seeds)
    if stale and len(stale) == len(seeds):
        raise StaleCabmapError(
            "All {0} of these CAB(s) live in '{1}', which this install no longer has -- "
            "the game replaced that archive in a patch and the cabmap predates it. "
            "Rebuild the cabmap.".format(len(stale), stale[0][1]))
    assets, roots, seed_roots, clips_by_cab, scene_roots = \
        cabmap_state.BRIDGE.import_cabs(seeds, export_class_ids=export_class_ids)
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
        mesh_blobs=cabmap_state.BRIDGE.mesh_blobs_by_guid,
        asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid,
        texture_srgb=cabmap_state.BRIDGE.texture_srgb_by_guid)
    return {"db": db, "roots": roots, "seed_roots": seed_roots,
            "clips_by_cab": clips_by_cab, "scene_roots": scene_roots}

