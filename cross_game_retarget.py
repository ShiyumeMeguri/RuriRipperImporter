"""Cross-game animation retarget: play any game's animation on any rig.

  clip 绑定 CRC ─(m_TOS 覆盖率)─> 源 Avatar ─(cabmap 反向依赖 + 根 Animator 身份)─> 宿主角色 prefab

One decision: a rig whose declared skeleton family (``ruri_skeleton``, stamped game as
default) differs from the session's retargets through the declared table graph; equal
families bind directly. One builder: the host is imported through the ordinary prefab
path, the same one a user's own character import runs. One maths: the named preset goes
verbatim (mappings AND settings) to AnimationRetarget, which skips missing bones itself.
Nothing here reads a name, a folder or a game; faces alone stay game-specific
(``Game.face_retarget_of``).
"""

from __future__ import annotations

import collections

import bpy

try:
    from . import Game, armature_builder, prefab_importer
    from .RuriRipperPyBridge.session import cabmap_state
    from .RuriRipperPyBridge.unity import (avatar as avatar_module, bridge_asset_db,
                                           class_registry, clip_paths,
                                           hierarchy as unity_hierarchy)
except ImportError:  # standalone (non-package) testing
    import Game
    import armature_builder
    import prefab_importer
    from RuriRipperPyBridge.session import cabmap_state
    from RuriRipperPyBridge.unity import (avatar as avatar_module, bridge_asset_db,
                                          class_registry, clip_paths,
                                          hierarchy as unity_hierarchy)


ADDON_MODULE = "AnimationRetarget"


def _addon():
    """The AnimationRetarget add-on's (core, presets, api) modules, or None when it is
    not installed -- imported lazily by name so this importer still loads without it."""
    try:
        core = __import__(ADDON_MODULE + ".core", fromlist=["core"])
        presets = __import__(ADDON_MODULE + ".presets", fromlist=["presets"])
        api = __import__(ADDON_MODULE + ".api", fromlist=["api"])
    except ImportError:
        return None
    return core, presets, api


def available():
    return _addon() is not None


SKELETON_PROP = "ruri_skeleton"


def skeleton_of(arm_obj):
    """The skeleton family this rig belongs to: its own declaration, else the game it
    was imported from. One studio ships one family across many titles, so families are
    declared -- in-file on presets, on the rig here -- and filenames mean nothing."""
    if arm_obj is None:
        return ""
    return str(arm_obj.get(SKELETON_PROP) or "") or armature_builder.read_game(arm_obj)


def set_skeleton(arm_obj, name):
    if arm_obj is not None:
        arm_obj[SKELETON_PROP] = str(name or "")


def _load_spec(presets, name):
    try:
        return presets.load_preset(name)
    except Exception:
        return {}


def _compose_specs(specs):
    """Chain tables by joining on the intermediate rig's bone names: rows (a->m) then
    (m->d) become (a->d), each carrying the LAST hop's per-row params and the last
    spec's settings -- KoikatuToEndfield,EndfieldToWaifu needs no new file."""
    spec = specs[0]
    for hop_spec in specs[1:]:
        join = {}
        for row in hop_spec.get("mappings") or []:
            join.setdefault(str(row.get("source") or ""), row)
        rows = []
        for row in spec.get("mappings") or []:
            hop = join.get(str(row.get("dest") or ""))
            if hop is not None:
                rows.append(dict(hop, source=str(row.get("source") or ""),
                                 dest=str(hop.get("dest") or "")))
        spec = dict(hop_spec)
        spec["mappings"] = rows
    return spec


def _flip_spec(spec):
    flipped = dict(spec)
    flipped["mappings"] = [dict(row, source=str(row.get("dest") or ""),
                                dest=str(row.get("source") or ""))
                           for row in spec.get("mappings") or []]
    return flipped


def _spec_sides(spec):
    declared = spec.get("skeletons") or {}
    return ({str(name).lower() for name in declared.get("source") or [] if name},
            {str(name).lower() for name in declared.get("dest") or [] if name})


def resolve_retarget_spec(session_key, dest_arm):
    """(composed spec, label) joining the session's skeleton family to this rig's.

    Every preset declares which two families it bridges (``skeletons.source/dest``,
    alias lists so sister titles sharing one rig share one table); the rig declares
    its family (``ruri_skeleton``, stamped game as default). Equal or unknown
    families = ({}, ""), the direct-bind path. A missing direct table composes the
    shortest declared chain -- breadth-first, either direction per hop, deterministic
    by preset name."""
    addon = _addon()
    if addon is None:
        return {}, ""
    _core, presets, _api = addon
    source_id = str(cabmap_state.game_of(session_key) or "").lower()
    dest_id = skeleton_of(dest_arm).lower()
    if not source_id or not dest_id or source_id == dest_id:
        return {}, ""

    edges = []
    for name in sorted(presets.list_presets()):
        spec = _load_spec(presets, name)
        side_a, side_b = _spec_sides(spec)
        if spec.get("mappings") and side_a and side_b:
            edges.append((name, spec, side_a, side_b))

    frontier = [({source_id}, [], [])]
    seen = [frozenset({source_id})]
    while frontier:
        position, specs, names = frontier.pop(0)
        if dest_id in position:
            return ((specs[0] if len(specs) == 1 else _compose_specs(specs)),
                    ",".join(names))
        if len(specs) >= 4:
            continue
        for name, spec, side_a, side_b in edges:
            steps = []
            if position & side_a:
                steps.append((spec, side_b))
            if position & side_b:
                steps.append((_flip_spec(spec), side_a))
            for crossed, landing in steps:
                key = frozenset(landing)
                if key in seen:
                    continue
                seen.append(key)
                frontier.append((set(landing), specs + [crossed], names + [name]))
    return {}, ""


class CrossGameRetargetError(RuntimeError):
    """A direct cross-game clip import that cannot proceed: no source avatar covers the
    clips' bindings, or no bone table exists (the message then carries the fill-it-in
    prompt), or the AnimationRetarget add-on is missing."""


_AvatarScore = collections.namedtuple(
    "_AvatarScore", "cab name dependency_count score coverage tos_size")

_SOURCE_AVATAR_CACHE = {}


def _class_ids_at(rows, index):
    """The ClassIDs one cabmap row carries, decoded from the columnar
    class_starts/class_flat pair -- the one reader of that encoding here."""
    start = int(rows.class_starts[index])
    end = int(rows.class_starts[index + 1])
    return set(int(class_id) for class_id in rows.class_flat[start:end])


def _candidate_avatar_cabs(session_key):
    """Every CAB in the source install's loaded cabmap that carries an Avatar, cheapest
    (fewest cabmap dependencies) first -- the search order for the source rig."""
    session = cabmap_state.session_for(session_key)
    rows = session.ROWS
    avatar_id = class_registry.id_for_name("Avatar")
    if avatar_id is None:
        raise CrossGameRetargetError("The class registry has no 'Avatar' class id.")
    candidates = []
    for index in range(len(rows)):
        if avatar_id in _class_ids_at(rows, index):
            candidates.append((int(rows.deps[index]), rows.cab(index)))
    candidates.sort(key=lambda pair: pair[0])
    return candidates, avatar_id


def _avatar_documents(unity_file):
    for document in unity_file.documents:
        if document.class_name == "Avatar":
            yield document


_ScannedAvatar = collections.namedtuple(
    "_ScannedAvatar", "cab name dependency_count tos_size crcs")

_AVATAR_INDEX = {}


def _avatar_index(session_key):
    state = _AVATAR_INDEX.get(session_key)
    if state is None:
        candidates, _avatar_id = _candidate_avatar_cabs(session_key)
        state = {"order": candidates, "position": 0, "seen": [], "parsed": set()}
        _AVATAR_INDEX[session_key] = state
    return state


def _graph_avatar_cabs(session_key, clip_cab):
    """Avatar CABs the cabmap can actually REACH from this clip, cheapest first.

    Two reverse hops then forward: the clip's dependents (an AnimatorController, a
    timeline .playable, a montage/cpuanim .asset), then THEIR dependents (the prefab
    whose Animator names the controller), then those CABs' forward closures -- which is
    where an Avatar finally sits. Measured over 301 sampled clip CABs: one hop reaches an
    avatar for 2.7%, adding the second hop takes it to **33.6%**, candidate sets stay
    small (max 30) and the whole walk costs 1.8ms.

    It is not universal -- 46% of this game's AnimatorControllers have no dependent at
    all and 63% of character postmodels reference no controller (the controller is picked
    at runtime from gameplay config by entity id), which is exactly the case pelica's
    clips fall in: hop1 = 2 controllers, hop2 = 0. So this is a CANDIDATE SOURCE, not a
    judgement: whatever it returns still has to pass the same 100%-coverage test as every
    other candidate, and when it returns nothing the ordered walk runs as before. That is
    also why a wrong-but-reachable avatar (a weapon's, another actor's -- both common in
    the sample) costs nothing: it cannot cover a body clip's bindings, so it is rejected
    on measurement rather than on where it came from."""
    bridge = cabmap_state.BRIDGE
    if bridge is None or not clip_cab:
        return []
    rows = cabmap_state.session_for(session_key).ROWS
    index_of = rows.cab_to_index()
    avatar_id = class_registry.id_for_name("Avatar")
    hop1 = bridge.find_direct_dependents([clip_cab])
    if not hop1:
        return []
    hop2 = [cab for cab in bridge.find_direct_dependents(hop1) if cab not in set(hop1)]
    found = []
    for cab in bridge.resolve_closure_cab_names(hop1 + hop2):
        index = index_of.get(cab)
        if index is not None and avatar_id in _class_ids_at(rows, index):
            found.append((int(rows.deps[index]), cab))
    found.sort()
    return found


def _score_of(entry, clip_crcs, want):
    hits = len(entry.crcs & clip_crcs)
    return _AvatarScore(entry.cab, entry.name, entry.dependency_count, hits,
                        hits / want if want else 0.0, entry.tos_size)


def _scan_one_avatar_cab(bridge, avatar_id, dependency_count, cab):
    """Every Avatar in one CAB, as _ScannedAvatar rows (its m_TOS reduced to the CRC set
    -- the only thing coverage is measured against)."""
    assets = bridge.import_cabs([cab], export_class_ids=[avatar_id])[0]
    cab_db = bridge_asset_db.BridgeAssetDatabase(assets, asset_paths=bridge.asset_paths_by_guid)
    found = []
    for guid in list(cab_db.all_guids()):
        unity_file = cab_db.load_guid(guid)
        if unity_file is None:
            continue
        for document in _avatar_documents(unity_file):
            tos = avatar_module._parse_tos(document.data)
            found.append(_ScannedAvatar(cab, str(document.data.get("m_Name") or guid),
                                        dependency_count, len(tos), frozenset(tos.keys())))
    return found


def score_source_avatars(session_key, clip_crcs, stop_at_full=True, clip_cab=None):
    """Rank the source game's Avatars by how many of the clips' binding CRCs each one's
    ``m_TOS`` covers -- the measured answer to "which rig were these clips authored on",
    with no name guessing. A 100%-covering avatar short-circuits the walk; otherwise
    everything is scanned and the argmax wins. Ordered by _rank_key (coverage, then
    base-rig completeness). Returns [_AvatarScore].

    Candidates are tried in three tiers, and **the tiers are only an order** -- every one
    of them faces the identical coverage test, so which tier an avatar came from can never
    change whether it is accepted:

      1. what this install already scanned (free -- see _AVATAR_INDEX);
      2. what the cabmap can reach from the clip itself (1.8ms -- see _graph_avatar_cabs);
      3. every Avatar CAB in the map, cheapest first, resuming where it last stopped.

    Tier 3 is the one that always terminates the search, and it is why an install whose
    graph does not link clips to avatars still resolves correctly, just slower."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    avatar_id = class_registry.id_for_name("Avatar")
    state = _avatar_index(session_key)
    want = len(clip_crcs)
    ranked = []

    def absorb(entries):
        """Score freshly parsed avatars into the running rank; True on a full cover."""
        for entry in entries:
            state["seen"].append(entry)
            item = _score_of(entry, clip_crcs, want)
            ranked.append(item)
            if stop_at_full and want and item.score >= want:
                return True
        return False

    ranked.extend(_score_of(entry, clip_crcs, want) for entry in state["seen"])
    if stop_at_full and want and any(item.score >= want for item in ranked):
        ranked.sort(key=_rank_key)
        return ranked

    bridge.use_session(session_key)
    for dependency_count, cab in _graph_avatar_cabs(session_key, clip_cab):
        if cab in state["parsed"]:
            continue
        state["parsed"].add(cab)
        if absorb(_scan_one_avatar_cab(bridge, avatar_id, dependency_count, cab)):
            ranked.sort(key=_rank_key)
            return ranked

    order = state["order"]
    while state["position"] < len(order):
        dependency_count, cab = order[state["position"]]
        state["position"] += 1
        if cab in state["parsed"]:
            continue
        state["parsed"].add(cab)
        if absorb(_scan_one_avatar_cab(bridge, avatar_id, dependency_count, cab)):
            ranked.sort(key=_rank_key)
            return ranked
    ranked.sort(key=_rank_key)
    return ranked


def _rank_key(item):
    """Best coverage first; among ties the fewest-dependency, most-complete skeleton
    (a base body rig over a garment variant that merely embeds it), then name."""
    return (-item.score, item.dependency_count, -item.tos_size, item.name)


def _load_source_avatar_file(bridge, session_key, cab, avatar_name):
    """The exported Avatar UnityFile named ``avatar_name`` in ``cab`` -- matched by m_Name,
    never by guid: AssetRipper mints a fresh guid on every export, so the guid a scoring pass
    saw is meaningless in a later one, while the avatar's own name is stable."""
    bridge.use_session(session_key)
    avatar_id = class_registry.id_for_name("Avatar")
    assets = bridge.import_cabs([cab], export_class_ids=[avatar_id])[0]
    cab_db = bridge_asset_db.BridgeAssetDatabase(assets, asset_paths=bridge.asset_paths_by_guid)
    for guid in list(cab_db.all_guids()):
        unity_file = cab_db.load_guid(guid)
        if unity_file is None:
            continue
        document = unity_file.first("Avatar")
        if document is not None and str(document.data.get("m_Name") or "") == avatar_name:
            return unity_file
    return None


def _resolve_source_avatar(session_key, clip_cab, clip_crcs):
    """The Avatar UnityFile the clips were authored on, plus its _AvatarScore. Cached per
    (install, clip CAB) so a second clip from the same pack skips the whole scan."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    key = (session_key, clip_cab)
    cached = _SOURCE_AVATAR_CACHE.get(key)
    if cached is not None:
        unity_file = _load_source_avatar_file(bridge, session_key, cached.cab, cached.name)
        if unity_file is not None:
            return unity_file, cached
    ranked = score_source_avatars(session_key, clip_crcs, clip_cab=clip_cab)
    if not ranked or ranked[0].score == 0:
        raise CrossGameRetargetError(
            "No {0} avatar's skeleton covers the selected clip's bindings -- there is nothing "
            "to retarget from.".format(session_key))
    best = ranked[0]
    _SOURCE_AVATAR_CACHE[key] = best
    unity_file = _load_source_avatar_file(bridge, session_key, best.cab, best.name)
    if unity_file is None:
        raise CrossGameRetargetError("The chosen source avatar CAB could not be re-read.")
    return unity_file, best


def _has_action(arm_obj):
    return (arm_obj.animation_data is not None
            and arm_obj.animation_data.action is not None)


def _discard_baked(baked_actions):
    """Remove the intermediate actions baked onto the host."""
    for action in baked_actions:
        try:
            bpy.data.actions.remove(action)
        except Exception:
            pass


def _discard_host(host_objects):
    """The host lives only for the duration of one retarget call -- it is rebuilt from
    the game data whenever needed, so nothing of it ever stays in the scene."""
    blocks = []
    for obj in host_objects:
        if obj.data is not None:
            blocks.append(obj.data)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
    for data in blocks:
        if data.users:
            continue
        for pool in (bpy.data.armatures, bpy.data.meshes, bpy.data.curves):
            try:
                pool.remove(data)
                break
            except Exception:
                continue


def _host_candidate_cabs(session_key, avatar_cab):
    """The CABs that could be "this character's own model prefab", cheapest first.

    Reverse dependency is CAB-granular, and a character's fbx CAB carries its Mesh
    alongside its Avatar -- so what comes back is everything that USES this character
    (measured: 183 CABs for one Endfield character -- cutscenes, dialogs, levelseqs,
    a UI model), not the host itself. **The order here only affects speed, never
    correctness**: which one is the host is answered by the hard test in
    _resolve_host_cab, and this merely puts the smallest closures first so the common
    case hits in one or two tries (measured: 2nd candidate for one character, 3rd for
    another). Carrying both a GameObject and an Animator is necessary, so that filter
    runs first and is free."""
    bridge = cabmap_state.BRIDGE
    session = cabmap_state.session_for(session_key)
    rows = session.ROWS
    index_of = rows.cab_to_index()
    gameobject_id = class_registry.id_for_name("GameObject")
    animator_id = class_registry.id_for_name("Animator")
    if gameobject_id is None or animator_id is None:
        raise CrossGameRetargetError("The class registry has no 'GameObject'/'Animator' class id.")
    ranked = []
    for cab in bridge.find_direct_dependents([avatar_cab]):
        index = index_of.get(cab)
        if index is None:
            continue
        classes = _class_ids_at(rows, index)
        if gameobject_id not in classes or animator_id not in classes:
            continue
        closure = bridge.resolve_closure_cab_names([cab])
        ranked.append((len(closure), int(rows.deps[index]), cab))
    ranked.sort()
    ordered = [cab for _closure_size, _dependency_count, cab in ranked]
    self_index = index_of.get(avatar_cab)
    if self_index is not None and avatar_cab not in ordered:
        classes = _class_ids_at(rows, self_index)
        if gameobject_id in classes and animator_id in classes:
            ordered.append(avatar_cab)
    return ordered


def _prefab_roots(bridge, cab):
    """(db, [(guid, root prefab file), ...]) for one candidate CAB, hierarchy classes
    only, its own seed asset first -- one CAB can hold many prefabs (a whole chara
    bundle), and any of them may be the host."""
    class_ids = [class_registry.id_for_name(name)
                 for name in ("GameObject", "Transform", "Animator", "Avatar")]
    if any(class_id is None for class_id in class_ids):
        raise CrossGameRetargetError("The class registry is missing a hierarchy class id.")
    assets, roots, seed_roots, _clips, _scenes = bridge.import_cabs(
        [cab], export_class_ids=class_ids)
    db = bridge_asset_db.BridgeAssetDatabase(assets, asset_paths=bridge.asset_paths_by_guid)
    head = seed_roots.get(cab)
    ordered = ([head] if head else []) + [guid for guid in roots if guid != head]
    return db, [(guid, db.load_guid(guid)) for guid in ordered]


def _resolve_host_cab(session_key, source):
    """The CAB holding the character the clips were authored on -- the animation's HOST.

    One test, and it is hard: **the Animator on the prefab's ROOT GameObject names
    exactly this Avatar** (prefab_importer.root_animator / animator_avatar -- identity
    through the m_Avatar guid, then the avatar's own stable m_Name). It says "the
    animated thing IS this prefab", which is the difference that decides the rest pose:
    a levelseq/cutscene prefab that merely STAGES the character carries the staged pose,
    and building the source from one of those moves every bone (measured: 595/595 bones
    differ, worst 612 units). A UI model and another character's cutscene both pass the
    weaker "has a root animator" test and both name a different avatar, so the avatar
    identity is not optional either (measured: 4 root-animator prefabs among one
    character's 180 candidates, only 2 of them this character's own).

    Nothing here reads a name, a folder or a game. The cabmap's reverse dependency
    gives the candidates, the Avatar the clips measurably bind to picks among them."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    bridge.use_session(session_key)
    for cab in _host_candidate_cabs(session_key, source.cab):
        db, prefab_files = _prefab_roots(bridge, cab)
        for guid, prefab_file in prefab_files:
            if prefab_file is None:
                continue
            _nodes, roots = unity_hierarchy.build_hierarchy(prefab_file)
            animator = prefab_importer.root_animator(prefab_file, {"roots": roots})
            document = prefab_importer.animator_avatar(db, animator)
            if document is not None and str(document.data.get("m_Name") or "") == source.name:
                return cab, str(bridge.asset_paths_by_guid.get(guid) or "")
    raise CrossGameRetargetError(
        "No prefab in {0} is rooted on avatar '{1}' -- the character these clips were "
        "authored on is not in this install's cabmap, so there is no rig to retarget "
        "from.".format(session_key, source.name))


def _build_host_rig(context, session_key, host, options):
    """Import the host character through the prefab import path -- the ONE skeleton
    builder. Materials/textures/clip discovery are visual-only and stay off (measured:
    454 rest matrices max|delta| = 0, 7.2s -> 2.7s); everything skeletal rides the
    caller's own options untouched."""
    host_cab, host_path = host
    bridge = cabmap_state.BRIDGE
    bridge.use_session(session_key)
    assets, roots, seed_roots, _clips, _scenes = bridge.import_cabs([host_cab])
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=bridge.clip_curves_by_guid,
        mesh_blobs=bridge.mesh_blobs_by_guid, asset_paths=bridge.asset_paths_by_guid)
    guid = next((candidate for candidate in roots
                 if str(bridge.asset_paths_by_guid.get(candidate) or "") == host_path),
                None) if host_path else None
    if guid is None:
        guid = seed_roots.get(host_cab)
    prefab_file = db.load_guid(guid) if guid else None
    if prefab_file is None:
        raise CrossGameRetargetError("The host character's own asset could not be resolved.")
    host_options = dict(options or {})
    host_options["import_animations"] = False
    host_options["import_materials"] = False
    host_options["import_textures"] = False
    known = set(bpy.data.objects)
    report = prefab_importer.import_prefab_from_db(context, db, prefab_file, host_options)
    host_objects = [obj for obj in bpy.data.objects if obj not in known]
    for obj in host_objects:
        obj.hide_viewport = True
        obj.hide_render = True
    if report.armature is None:
        _discard_host(host_objects)
        raise CrossGameRetargetError(
            "The host character built no skeleton to bake the clips onto.")
    return report.armature, host_objects


def _host_rig_for(context, session_key, clip_cab, clip_crcs, options):
    """(host armature, its rig maps, every object imported with it) -- built fresh for
    this one call and discarded by the caller when the retarget is done."""
    _avatar_file, source = _resolve_source_avatar(session_key, clip_cab, clip_crcs)
    host_arm, host_objects = _build_host_rig(
        context, session_key, _resolve_host_cab(session_key, source), options)
    maps = prefab_importer.maps_from_stamped_armature(host_arm)
    if maps is None:
        _discard_host(host_objects)
        raise CrossGameRetargetError(
            "The host character '{0}' carries no Unity rig identity.".format(host_arm.name))
    return host_arm, maps, host_objects


def retarget_clips_onto(context, session_key, clip_cab, clip_guids, db, dest_arm, options,
                        spec, table_label, display_names=None, activate=False):
    """Import ``clip_guids`` (already resolved into ``db``, a source-game closure) onto
    ``dest_arm`` -- a rig that names a bone table -- by way of the clips' OWN host
    character. Resolves the avatar the clips measurably bind to, resolves and imports the
    character prefab rooted on that avatar, bakes the clips onto it, and hands the whole
    table (mappings AND settings) to AnimationRetarget. Returns (product_actions,
    warnings); raises CrossGameRetargetError when no avatar covers the clips, no host
    prefab is rooted on it, or the named table is unreadable/empty."""
    addon = _addon()
    if addon is None:
        raise CrossGameRetargetError(
            "The AnimationRetarget add-on is not enabled -- cross-game clip import needs its "
            "retarget maths.")
    core, _presets, api = addon

    clip_crcs = set()
    for guid in clip_guids:
        clip = db.clip_curves(guid)
        if clip is None:
            continue
        for channels in clip.transform_channel_lists():
            for channel in channels:
                if channel.path:
                    clip_crcs.add(clip_paths.entry_crc(channel.path))
    if not clip_crcs:
        raise CrossGameRetargetError("The selected clip(s) carry no transform bindings to retarget.")

    mappings = list(spec.get("mappings") or [])
    if not mappings:
        raise CrossGameRetargetError(
            "Bone table {0!r} for armature '{1}' could not be read or is empty."
            .format(table_label, dest_arm.name))

    host_arm, host_maps, host_objects = _host_rig_for(
        context, session_key, clip_cab, clip_crcs, options)

    warnings = []
    products = []
    before = set(bpy.data.actions)
    baked_actions = []
    try:
        _built, build_warnings, _actions = prefab_importer.build_selected_animations(
            db, host_arm, host_maps, None, clip_guids, options, display_names)
        warnings.extend(build_warnings)
        baked_actions = [action for action in bpy.data.actions if action not in before]
        if not baked_actions:
            raise CrossGameRetargetError("No source action baked from the selected clip(s).")

        results, errors = api.retarget_actions(
            host_arm, dest_arm, mappings, spec.get("settings") or {}, baked_actions)
        for action_name, message in errors:
            warnings.append("{0}: {1}".format(action_name, message))
        products = [dest_action for _source_action, dest_action, _info in results]
        if not products:
            raise CrossGameRetargetError(
                "Nothing retargeted onto '{0}' -- bone table '{1}' matched no shared bone.".format(
                    dest_arm.name, table_label))
        if activate or len(clip_guids) == 1 or not _has_action(dest_arm):
            core.assign_action(dest_arm, products[0])
    finally:
        _discard_baked(baked_actions)
        _discard_host(host_objects)
    return products, warnings


def load_clips_onto(context, session_key, clip_cab, clip_guids, db, dest_arm, maps, options,
                    display_names=None, activate=False):
    """THE animation-loading entry point. Every panel calls this and nothing else.

    Whether a clip needs a cross-game retarget is one decision, made from one fact --
    the game stamped on the target armature versus the game whose session the clip
    came from -- so it is made here, once. A second copy of this branch in a game's
    own panel is how the two quietly stop agreeing.

    Returns (built, warnings): ``built`` counts the actions that ended up on
    ``dest_arm``.

    ``display_names`` ({guid: name}) names the products after something the caller
    knows better than the clip does -- see build_selected_animations. It rides both
    branches, so a retargeted product is the same name plus the destination suffix.

    ``activate`` makes the first product the armature's active action even when the
    batch holds several clips -- for a caller whose N clips are one user pick, not N.
    Rides both branches too, so which one it is never changes what the user sees.
    """
    if maps is None:
        maps = prefab_importer.maps_from_stamped_armature(dest_arm)

    spec, table_label = resolve_retarget_spec(session_key, dest_arm)
    if spec.get("mappings"):
        products, warnings = retarget_clips_onto(
            context, session_key, clip_cab, clip_guids, db, dest_arm, options,
            spec, table_label, display_names, activate)
        return len(products), warnings
    source_id = str(cabmap_state.game_of(session_key) or "")
    dest_id = skeleton_of(dest_arm)
    if source_id and dest_id and source_id.lower() != dest_id.lower():
        raise CrossGameRetargetError(
            "No declared table chain joins skeleton family '{0}' to '{1}' -- declare the "
            "pair in a preset's skeletons.source/dest lists (chains compose "
            "automatically).".format(source_id, dest_id))

    if maps is None:
        raise CrossGameRetargetError(
            "'{0}' carries no Unity rig identity -- import the character through this "
            "add-on once, then animations attach to it from then on.".format(dest_arm.name))
    ratio, checked = _binding_match(db, clip_guids, maps["path_to_bone"])
    if checked and ratio == 0.0:
        raise CrossGameRetargetError(
            "None of the clip's curve paths match armature '{0}', and it names no bone "
            "table to retarget through -- select the right skeleton, or declare its {1} "
            "family.".format(dest_arm.name, SKELETON_PROP))

    built, warnings, actions = prefab_importer.build_selected_animations(
        db, dest_arm, maps, None, clip_guids, options, display_names, activate)
    if checked and ratio < 0.5:
        warnings.insert(0, "Only {0:.0%} of curve paths match armature '{1}' -- "
                           "imported anyway.".format(ratio, dest_arm.name))
    warnings.extend(_retarget_faces(context, session_key, clip_cab, clip_guids, db,
                                    dest_arm, options, actions))
    return built, warnings


def _retarget_faces(context, session_key, clip_cab, clip_guids, db, dest_arm, options,
                    actions):
    """Restate each clip's baked facial performance on this rig, when the user asked
    for it and the clip's game states what a face IS.

    Lives on the ONE clip-loading path for the same reason the cross-game branch does:
    a body clip and its face are one import, and a second copy of this decision in a
    game's own panel is how the two stop agreeing. The host stays game-blind -- it asks
    the registry whether this game contributed a facial restatement and passes the clip
    through; a game with no facial system contributes none and nothing happens.

    ``actions`` is that clip's own {guid: (action, slot)} from the body build, and the
    face goes INTO it. Two reasons, both measured, and both invisible without it:

    * an object plays ONE action, so a face on its own action replaces the body it
      arrived with -- whichever the user assigns, the other half stops playing;
    * the body build already keyed the SOURCE character's facial bones onto this rig
      (same standard bone names, so they bind), which is the untranslated geometry this
      whole feature exists to avoid. Writing the face into the same action lets it
      REPLACE those channels instead of losing a fight with them.
    """
    if not options.get("retarget_face"):
        return []
    provider = Game.face_retarget_of(cabmap_state.game_of(session_key)
                                     or armature_builder.read_game(dest_arm))
    if provider is None:
        return []
    reports = []
    for guid in clip_guids:
        try:
            clip = source_anchored_clip(session_key, clip_cab, guid, db)
        except Exception as exc:
            reports.append("Face retarget skipped for one clip: {0}".format(exc))
            continue
        if clip is None:
            continue
        try:
            report = provider(context, dest_arm, clip, options, actions.get(guid))
        except Exception as exc:
            # Never silent: the body animation DID land, so a face that did not is a
            # partial result the user has to be told about by name.
            reports.append("Face retarget skipped for '{0}': {1}".format(clip.name, exc))
            continue
        if report:
            reports.append(report)
    return reports


def source_anchored_clip(session_key, clip_cab, guid, db):
    """The clip with its curves anchored to the rig it was AUTHORED on, not to the
    one it is being played on.

    A clip imported on its own carries no readable bindings at all -- measured: all
    359 curve paths of a real UI clip arrive as ``path_0x<CRC32>_...`` placeholders,
    because AssetRipper can only restore a binding to a string when that skeleton is
    in the export scope. The normal import path then repairs them against the
    DESTINATION rig, which is exactly right for playing body motion on it.

    For a face it is not. Reading the performance means asking "where was this bone
    relative to ITS OWN rest", and the destination's skeleton answers a different
    question -- it happens not to explode only because these characters share a bone
    vocabulary. So the source skeleton is resolved and loaded here as a dependency
    (by CRC coverage of the clip's own bindings -- measurement, not the clip's name)
    and a FRESH copy of the curves is anchored to it; the destination-repaired clip
    the body import already produced is left alone.
    """
    blob = cabmap_state.BRIDGE.clip_curves_by_guid.get(guid) if cabmap_state.BRIDGE else None
    if blob is None:
        # No raw blob (a disk-mode session): the db's clip is all there is.
        return db.clip_curves(guid)

    from .RuriRipperPyBridge.unity import clip_curves as clip_curves_module
    clip = clip_curves_module.ClipCurves.from_blob(blob[0], blob[1])
    crcs = {clip_paths.entry_crc(channel.path)
            for channels in clip.transform_channel_lists()
            for channel in channels if channel.path}
    if not crcs:
        return clip

    unity_file, score = _resolve_source_avatar(session_key, clip_cab or guid, crcs)
    document = unity_file.first("Avatar") if unity_file is not None else None
    if document is None:
        raise CrossGameRetargetError(
            "the skeleton these curves were authored on could not be read back")
    paths = avatar_module.transform_paths(document.data)
    source_paths = {path: path.rsplit("/", 1)[-1] for path in paths if path}
    repaired, unmatched = clip_paths.repair_hashed_clip_paths(clip, source_paths)
    if repaired == 0:
        raise CrossGameRetargetError(
            "'{0}' covers none of this clip's bindings once loaded".format(score.name))
    print("[face] anchored '{0}' to source rig '{1}' ({2:.0%} binding coverage) · "
          "{3} curve(s) repaired, {4} unmatched".format(
              clip.name, score.name, score.coverage, repaired, unmatched), flush=True)
    return clip


def _binding_match(db, clip_guids, path_to_bone):
    """Best fraction of any clip's transform-curve paths that resolve to a bone of the
    target rig, and whether anything was checkable. A clip with no data, or one that
    fails to parse, is skipped -- the real per-clip complaint fires at build time."""
    best = 0.0
    checked = False
    for guid in clip_guids:
        try:
            clip = db.clip_curves(guid)
        except ValueError:
            continue
        if clip is None:
            continue
        ratio, total = clip_paths.clip_path_match_ratio(clip, path_to_bone)
        if total:
            checked = True
            best = max(best, ratio)
    return best, checked
