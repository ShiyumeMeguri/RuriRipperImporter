"""Cross-game animation retarget: play one game's animation on another game's rig.

A humanoid clip already retargets by itself -- muscle values are avatar-relative, and the
solve runs against whichever skeleton the clip is bound to (see prefab_importer.
_solve_humanoid_curves). A GENERIC clip cannot: its curves carry per-bone local rotations
in the SOURCE rig's own bone axes, so there is nothing avatar-shaped to re-decode. It needs
a real retarget -- world-rotation transfer through both rigs' rest poses -- and one table
saying which bone is which.

That is exactly what the AnimationRetarget add-on already is, so this module adds no maths
of its own. It contributes the two things AnimationRetarget cannot know:

* **which table** -- every armature this importer builds carries the game it came from
  (``armature_builder.UNITY_GAME_PROP``) plus its own rig and avatar (UNITY_RIG_PROP /
  UNITY_AVATAR_PROP). The target skeleton therefore states its own identity, so importing a
  clip while browsing another game's session IS the whole instruction: the pair names the
  table, the avatar rebuilds the source rig, and nothing is asked of the user;
* **the tables themselves** -- ordinary AnimationRetarget presets, so they stay editable in
  its own panel and nothing here is a second config format.

There is no panel and no button here on purpose. A cross-game import is an ordinary clip
import that noticed the target rig belongs to a different game (see
``cabmap_panel._import_clips_standalone``); a second, manual path would be a second way to
express the same intent, and the stamps already express it.

**Naming is one rule, and it is symmetric**: ``<GameA>To<GameB>.json``. One file serves both
directions -- ``AToB`` retargets A→B as written and B→A with every pair flipped -- because a
bone correspondence has no direction. Shipping ``BToA`` as a second file would be two sources
of truth for one fact, and they would drift.
"""

from __future__ import annotations

import collections
import os

import bpy

try:
    from . import armature_builder, prefab_importer
    from .RuriRipperPyBridge.session import cabmap_state
    from .RuriRipperPyBridge.unity import (avatar as avatar_module, bridge_asset_db,
                                           class_registry, clip_paths)
except ImportError:  # standalone (non-package) testing
    import armature_builder
    import prefab_importer
    from RuriRipperPyBridge.session import cabmap_state
    from RuriRipperPyBridge.unity import (avatar as avatar_module, bridge_asset_db,
                                          class_registry, clip_paths)


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


def table_name(source_game, dest_game):
    return "{0}To{1}".format(source_game, dest_game)


def find_table(source_game, dest_game):
    """The mapping table for ``source_game`` -> ``dest_game``.

    Returns ``(pairs, preset_name, flipped)``: ``pairs`` are AnimationRetarget mapping
    dicts already oriented source->dest, ``flipped`` says the file was written the other
    way round and every pair was inverted. ``(None, wanted_name, False)`` when neither
    direction exists.
    """
    addon = _addon()
    if addon is None:
        return None, table_name(source_game, dest_game), False
    _core, presets, _api = addon
    if not source_game or not dest_game:
        return None, table_name(source_game, dest_game), False

    forward = table_name(source_game, dest_game)
    reverse = table_name(dest_game, source_game)
    known = {name.lower(): name for name in presets.list_presets()}

    for wanted, flipped in ((forward, False), (reverse, True)):
        actual = known.get(wanted.lower())
        if actual is None:
            continue
        spec = presets.load_preset(actual)
        pairs = []
        for source, dest, params in presets.normalize_pairs(spec):
            entry = dict(params)
            entry["source"], entry["dest"] = (dest, source) if flipped else (source, dest)
            pairs.append(entry)
        return pairs, actual, flipped
    return None, forward, False


class CrossGameRetargetError(RuntimeError):
    """A direct cross-game clip import that cannot proceed: no source avatar covers the
    clips' bindings, or no bone table exists (the message then carries the fill-it-in
    prompt), or the AnimationRetarget add-on is missing."""


_AvatarScore = collections.namedtuple(
    "_AvatarScore", "cab guid name dependency_count score coverage tos_size")

_SOURCE_AVATAR_CACHE = {}


def _candidate_avatar_cabs(session_game):
    """Every CAB in the source game's loaded cabmap that carries an Avatar, cheapest
    (fewest cabmap dependencies) first -- the search order for the source rig."""
    session = cabmap_state.session_for(session_game)
    rows = session.ROWS
    avatar_id = class_registry.id_for_name("Avatar")
    if avatar_id is None:
        raise CrossGameRetargetError("The class registry has no 'Avatar' class id.")
    candidates = []
    for index in range(len(rows)):
        start = int(rows.class_starts[index])
        end = int(rows.class_starts[index + 1])
        if avatar_id in rows.class_flat[start:end]:
            candidates.append((int(rows.deps[index]), rows.cab(index)))
    candidates.sort(key=lambda pair: pair[0])
    return candidates, avatar_id


def _avatar_documents(unity_file):
    for document in unity_file.documents:
        if document.class_name == "Avatar":
            yield document


def score_source_avatars(session_game, clip_crcs, stop_at_full=True):
    """Rank the source game's Avatar CABs by how many of the clips' binding CRCs each
    avatar's ``m_TOS`` covers -- the measured answer to "which rig were these clips
    authored on", with no name guessing. Cheapest candidates are imported first and a
    100%-covering avatar short-circuits the walk; the rest are scanned and the argmax
    wins. Ordered by _rank_key (coverage, then base-rig completeness). Returns [_AvatarScore]."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    candidates, avatar_id = _candidate_avatar_cabs(session_game)
    bridge.use_game(session_game)
    want = len(clip_crcs)
    ranked = []
    for dependency_count, cab in candidates:
        assets = bridge.import_cabs([cab], export_class_ids=[avatar_id])[0]
        cab_db = bridge_asset_db.BridgeAssetDatabase(
            assets, asset_paths=bridge.asset_paths_by_guid)
        for guid in list(cab_db.all_guids()):
            unity_file = cab_db.load_guid(guid)
            if unity_file is None:
                continue
            for document in _avatar_documents(unity_file):
                tos = avatar_module._parse_tos(document.data)
                score = len(set(tos.keys()) & clip_crcs)
                name = str(document.data.get("m_Name") or guid)
                ranked.append(_AvatarScore(cab, guid, name, dependency_count, score,
                                           score / want if want else 0.0, len(tos)))
                if stop_at_full and want and score >= want:
                    ranked.sort(key=_rank_key)
                    return ranked
    ranked.sort(key=_rank_key)
    return ranked


def _rank_key(item):
    """Best coverage first; among ties the fewest-dependency, most-complete skeleton
    (a base body rig over a garment variant that merely embeds it), then name."""
    return (-item.score, item.dependency_count, -item.tos_size, item.name)


def _load_source_avatar_file(bridge, session_game, cab, avatar_name):
    """The exported Avatar UnityFile named ``avatar_name`` in ``cab`` -- matched by m_Name,
    never by guid: AssetRipper mints a fresh guid on every export, so the guid a scoring pass
    saw is meaningless in a later one, while the avatar's own name is stable."""
    bridge.use_game(session_game)
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


def _resolve_source_avatar(session_game, clip_cab, clip_crcs):
    """The Avatar UnityFile the clips were authored on, plus its _AvatarScore. Cached per
    (game, clip CAB) so a second clip from the same pack skips the whole scan."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    key = (session_game, clip_cab)
    cached = _SOURCE_AVATAR_CACHE.get(key)
    if cached is not None:
        unity_file = _load_source_avatar_file(bridge, session_game, cached.cab, cached.name)
        if unity_file is not None:
            return unity_file, cached
    ranked = score_source_avatars(session_game, clip_crcs)
    if not ranked or ranked[0].score == 0:
        raise CrossGameRetargetError(
            "No {0} avatar's skeleton covers the selected clip's bindings -- there is nothing "
            "to retarget from.".format(session_game))
    best = ranked[0]
    _SOURCE_AVATAR_CACHE[key] = best
    unity_file = _load_source_avatar_file(bridge, session_game, best.cab, best.name)
    if unity_file is None:
        raise CrossGameRetargetError("The chosen source avatar CAB could not be re-read.")
    return unity_file, best


def _has_action(arm_obj):
    return (arm_obj.animation_data is not None
            and arm_obj.animation_data.action is not None)


def _discard_scaffold(temp_arm, baked_actions):
    """Remove the throwaway source rig (and its armature data) and the intermediate baked
    actions, leaving only the retarget products on the destination rig."""
    data = temp_arm.data if temp_arm is not None else None
    if temp_arm is not None:
        try:
            bpy.data.objects.remove(temp_arm, do_unlink=True)
        except Exception:
            pass
    if data is not None and data.users == 0:
        try:
            bpy.data.armatures.remove(data)
        except Exception:
            pass
    for action in baked_actions:
        try:
            bpy.data.actions.remove(action)
        except Exception:
            pass


def _missing_table_prompt(api, source_game, dest_game, source_arm, dest_arm):
    """Export both skeletons (AnimationRetarget's own format and directory) and return a
    complete, paste-ready prompt for authoring the missing bone table. Called while the
    source rig is still alive -- the export must precede its teardown."""
    source_config = api.export_skeleton_structure(source_arm)
    dest_config = api.export_skeleton_structure(dest_arm)
    table_path = os.path.join(api.mapping_dir(),
                              "{0}To{1}.json".format(source_game, dest_game))
    return (
        "No bone table for {src} -> {dst}. Write it as one AnimationRetarget preset, then "
        "re-run the import.\n"
        "  target table : {table}\n"
        "  {src} skeleton: {src_cfg}\n"
        "  {dst} skeleton: {dst_cfg}\n"
        "How to write it:\n"
        "  - Read the two skeleton JSONs and map ONLY the shared humanoid body bones: hips, "
        "the spine chain, neck, head, both clavicle/upper-arm/forearm/hand, both "
        "thigh/calf/foot/toe, and the finger joints. Take each bone name verbatim from those "
        "files; invent nothing.\n"
        "  - Leave every accessory bone OUT of the table -- skirt, breast, hair, tail and any "
        "other physics or decoration bones are not mapped.\n"
        "  - One file is bidirectional: source = {src} (the game named first in the file "
        "name); the {dst} -> {src} direction reads the same rows with source and dest "
        "swapped, so do not also write a {dst}To{src}.json.\n"
        "  - On the hips entry and the root entry, set loc.enabled = true and scale_mode = "
        "AUTO so root translation and rig scale transfer."
    ).format(src=source_game, dst=dest_game, table=table_path,
             src_cfg=source_config, dst_cfg=dest_config)


def retarget_clips_onto(context, session_game, clip_cab, clip_guids, db, dest_arm, options):
    """Import ``clip_guids`` (already resolved into ``db``, a source-game closure) straight
    onto ``dest_arm`` -- a rig stamped with a DIFFERENT game -- without importing the source
    character at all. Picks the source avatar whose TOS best covers the clips' bindings, bakes
    the clips onto a throwaway rig built from it, retargets that onto ``dest_arm`` through the
    <source>To<dest> bone table, and discards the scaffold. Returns (product_actions, warnings);
    raises CrossGameRetargetError when no avatar covers the clips or no bone table exists (the
    message is then the paste-ready prompt for writing one)."""
    addon = _addon()
    if addon is None:
        raise CrossGameRetargetError(
            "The AnimationRetarget add-on is not enabled -- cross-game clip import needs its "
            "retarget maths.")
    core, _presets, api = addon
    dest_game = armature_builder.read_game(dest_arm)

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

    avatar_file, source = _resolve_source_avatar(session_game, clip_cab, clip_crcs)

    warnings = []
    products = []
    baked_actions = []
    temp_arm = armature_builder.build_armature_from_avatar(
        context, avatar_file, name="{0}_xg_source".format(source.name))
    armature_builder.stamp_game(temp_arm, session_game)
    try:
        temp_maps = prefab_importer.maps_from_stamped_armature(temp_arm)
        if temp_maps is None:
            raise CrossGameRetargetError(
                "The chosen source avatar built a skeleton with no TOS-named bones.")
        before = set(bpy.data.actions)
        _built, build_warnings = prefab_importer.build_selected_animations(
            db, temp_arm, temp_maps, None, clip_guids, options)
        warnings.extend(build_warnings)
        baked_actions = [action for action in bpy.data.actions if action not in before]
        if not baked_actions:
            raise CrossGameRetargetError("No source action baked from the selected clip(s).")

        pairs, _table_label, _flipped = find_table(session_game, dest_game)
        if pairs is None:
            raise CrossGameRetargetError(
                _missing_table_prompt(api, session_game, dest_game, temp_arm, dest_arm))

        results, errors = api.retarget_actions(
            temp_arm, dest_arm, pairs, {"suffix": "_" + dest_game.lower()}, baked_actions)
        for action_name, message in errors:
            warnings.append("{0}: {1}".format(action_name, message))
        products = [dest_action for _source_action, dest_action, _info in results]
        if not products:
            raise CrossGameRetargetError(
                "Nothing retargeted from {0} to {1} -- the bone table matched no shared bone.".format(
                    session_game, dest_game))
        if len(clip_guids) == 1 or not _has_action(dest_arm):
            core.assign_action(dest_arm, products[0])
    finally:
        _discard_scaffold(temp_arm, baked_actions)
    return products, warnings


def load_clips_onto(context, session_game, clip_cab, clip_guids, db, dest_arm, maps, options):
    """THE animation-loading entry point. Every panel calls this and nothing else.

    Whether a clip needs a cross-game retarget is one decision, made from one fact --
    the game stamped on the target armature versus the game whose session the clip
    came from -- so it is made here, once. A second copy of this branch in a game's
    own panel is how the two quietly stop agreeing.

    Returns (built, warnings): ``built`` counts the actions that ended up on
    ``dest_arm``.
    """
    dest_game = armature_builder.read_game(dest_arm)
    if session_game and dest_game and dest_game != session_game:
        products, warnings = retarget_clips_onto(
            context, session_game, clip_cab, clip_guids, db, dest_arm, options)
        return len(products), warnings
    if maps is None:
        maps = prefab_importer.maps_from_stamped_armature(dest_arm)
    if maps is None:
        raise CrossGameRetargetError(
            "'{0}' carries no Unity rig identity -- import the character through this "
            "add-on once, then animations attach to it from then on.".format(dest_arm.name))
    ratio, checked = _binding_match(db, clip_guids, maps["path_to_bone"])
    if checked and ratio == 0.0:
        raise CrossGameRetargetError(
            "None of the clip's curve paths match armature '{0}', and it carries no game "
            "stamp naming another game to retarget from -- select the right skeleton, or "
            "re-import it so it knows which game it is from.".format(dest_arm.name))
    built, warnings = prefab_importer.build_selected_animations(
        db, dest_arm, maps, None, clip_guids, options)
    if checked and ratio < 0.5:
        warnings.insert(0, "Only {0:.0%} of curve paths match armature '{1}' -- "
                           "imported anyway.".format(ratio, dest_arm.name))
    return built, warnings


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
