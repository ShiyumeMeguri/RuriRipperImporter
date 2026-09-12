"""This game's face: browse the SkeletalMorph library, drive it, bake it.

The game does not animate faces with transform curves -- body clips stop at the
humanoid "Jaw Close" muscle and key no facial joint at all. Expression lives in a
separate family of assets that animate named **ctrl drivers** (a weight per ctrl,
constant for a pose, a curve for an animation). See ``skeletal_morph`` for the
format and ``morph_state`` for the library model.

EVERYTHING SPECIFIC TO THIS GAME IS HERE. Which assets a character's face is
filed under, how an npc's tables are declared, what a ctrl moves on which bone by
how much, how its curves evaluate -- all of it. What crosses to the host is one
neutral TABLE (``face_table``) and one neutral set of weights, because "which of
these bones does this rig actually have" and "write these as animation channels"
are the only two questions an application answers.

Nothing here imports a host.
"""

from __future__ import annotations

import re

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...RuriRipperPyBridge.session import cabmap_state
from . import cast, morph_state, skeletal_morph

STATE = "ruri_character"

DRIVER_SECTION = "driver"

#: The game bakes a ctrl driver onto a mesh as a shape key named after the DCC rig
#: channel that drove it: "<ctrl>_tx_max" (translate-X at its maximum), with a
#: "_min" twin when the ctrl is bidirectional. Handed to the host as a rule rather
#: than applied here: which meshes a rig drives is the host's answer, and which
#: names mean what is this game's.
SHAPE_KEY_RULE = {"suffix": r"_(?:[trs][xyz])_(max|min)$",
                  "signs": {"max": 1.0, "min": -1.0}}

# Display names only -- the section IDENTIFIERS are the game's own kinds, which is
# why an unlisted kind still gets a readable tab from the fallback rather than
# needing an entry here.
_KIND_LABELS = {"emotion": "Emotions", "pose": "Poses", "morphanimation": "Animations",
                "cfg": "Config"}


def state_of(context):
    return host_port.current().panel_state(context, STATE)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
MORPH_ITEM = Schema("EndfieldMorphItem", """One library entry as the panel shows
it. ``asset`` is the game's own name for it, which is the identity the whole morph
library is keyed by; the asset itself lives in morph_state (never mirrored into
panel state -- 634 animations with full curves have no business in a .blend).""", (
    Field("name", app_state.STRING, ""),
    Field("asset", app_state.STRING, ""),
    Field("kind", app_state.STRING, ""),
    Field("duration", app_state.FLOAT, 0.0),
    Field("ctrl_count", app_state.INT, 0),
    Field("bound_count", app_state.INT, 0),
    Field("animated", app_state.BOOL, False, "",
          "This entry drives its ctrls over time rather than holding one pose"),
    Field("selected", app_state.BOOL, False, "",
          "Include this animation when building actions"),
))

MORPH_DRIVER = Schema("EndfieldMorphDriver", """One ctrl driver, and what it
reaches on this rig.""", (
    Field("ctrl", app_state.STRING, ""),
    Field("channel", app_state.STRING, ""),
    Field("bound", app_state.BOOL, False),
    Field("target_label", app_state.STRING, ""),
    Field("weight", app_state.FLOAT, 0.0, "", "Drive this ctrl directly",
          subtype=app_state.FACTOR, minimum=-1.0, maximum=1.0,
          update="on_driver_weight"),
))

CHARACTER = Schema("EndfieldCharacter", """The face section's whole state.""", (
    Field("armature_name", app_state.STRING, "", "Rig"),
    Field("character_token", app_state.STRING, "", "Character",
          "Name fragment that marks this character's own facial animations in the "
          "cabmap (e.g. \"pelica\")"),
    Field("face_morph", app_state.STRING, "", "Face Tables",
          "The face-morph avatar this rig's entity DECLARES (e.g. "
          "\"FacialMorph/Avatar/Boy/ardashir\"). Filled in from the game's own data "
          "when the rig is loaded; it names the ctrl-to-bone tables, which for an npc "
          "is never its own name"),
    Field("section", app_state.ENUM, None, "Section", items="section_items",
          update="on_section"),
    Field("status", app_state.STRING, "Scan a rig to begin."),
    Field("library_ready", app_state.BOOL, False),
    Field("intensity", app_state.FLOAT, 1.0, "Intensity",
          "Scales every weight an applied pose writes",
          subtype=app_state.FACTOR, minimum=0.0, maximum=1.0),
    Field("show_unbound", app_state.BOOL, False, "Show Unbound",
          "Also list ctrl drivers this rig has no target for"),
    Field("apply_on_click", app_state.BOOL, True, "Apply on Click",
          "Selecting an entry poses the face immediately"),
    Field("items", app_state.COLLECTION, element=MORPH_ITEM),
    Field("items_active_index", app_state.INT, 0, update="on_item_activated"),
    Field("drivers", app_state.COLLECTION, element=MORPH_DRIVER),
    Field("drivers_active_index", app_state.INT, 0),
    Field("phoneme_set", app_state.ENUM, None, "Set",
          "Which phoneme pose set the mouth buttons use", items="phoneme_set_items"),
), include=(schemas.LOADING_STATE,))


# ---------------------------------------------------------------------------
# The table this game hands the host
# ---------------------------------------------------------------------------
def avatars_for(state):
    """The avatar tables belonging to this rig, by whichever identity the GAME
    states for it -- never by what the rig happens to be called.

    Three kinds of entity state it three different ways, so this reads the one that
    exists rather than retrying a chain: an npc's prefab info names its face tables
    outright (``facialMorphAvatarName``); a playable character's data asset states a
    SkeletalMorphComponentData tagId that each avatar table states back; a rig from
    neither -- imported from somewhere else entirely -- has no declaration anywhere,
    and only then is its name all there is.

    The npc case is why the name token cannot be the primary: the game gives
    ``npc_spl_adaxier_01`` a face table called ``ardashir``, and 227 npcs share one
    called ``boy_face_common_a_01``. Matching on the rig's name found neither, so
    every npc bound zero ctrls and no expression did anything."""
    declared = state.face_morph or cast.declared_face_morph(state.armature_name)
    if declared:
        matched = morph_state.avatars_for_declaration(declared)
        if matched:
            return matched
    tag = cast.character_tag(state.character_token)
    if tag:
        matched = morph_state.avatars_for_tag(tag)
        if matched:
            return matched
    return morph_state.avatars_for(state.character_token)


def face_table(state):
    """This character's ctrl drivers, in the neutral terms a host takes.

    Merged BY BONE, never by boneID: each avatar indexes its own allBoneNames, so a
    face table's id 83 and an ear table's id 83 are different bones. The transform
    each one IS -- its name -- is the only identity shared across them."""
    base = {}
    deltas = {}
    for avatar in avatars_for(state):
        if not avatar.mappings:
            continue
        for entry in avatar.base_pose.values():
            if entry.bone_name:
                base.setdefault(entry.bone_name,
                                (entry.position, entry.rotation, entry.scale))
        for ctrl, listed in avatar.mappings.items():
            merged = deltas.setdefault(ctrl, [])
            for delta in listed:
                if delta.bone_name:
                    merged.append((delta.bone_name, delta.position, delta.rotation,
                                   delta.scale))
    return {"base": base, "deltas": deltas, "shape_rule": SHAPE_KEY_RULE}


#: The table last handed over, so the host's binding cache can tell one build from
#: another by identity. Rebuilt whenever the library or the rig changes.
_TABLE = {"state": None}


def table_of(state):
    if _TABLE["state"] is None:
        _TABLE["state"] = face_table(state)
    return _TABLE["state"]


def retable(state):
    _TABLE["state"] = face_table(state)
    return _TABLE["state"]


def _bindings(context, state):
    rig = _rig(context, state)
    if rig is None:
        return {"ctrls": {}, "via": [], "missing_bones": [], "rest_error": None,
                "reason": ""}
    return host_port.current().face_bindings(context, rig, table_of(state))


def _rig(context, state):
    """The armature this section drives. Named on the state so a scan of one rig is
    not silently applied to another."""
    return host_port.current().rig_named(state.armature_name, context)


# ---------------------------------------------------------------------------
# The handlers the schema names
# ---------------------------------------------------------------------------
def _section_items(state, context):
    """One tab per kind the loaded library actually has assets for, plus the
    Drivers view. Derived, not enumerated: a game update that adds a morph folder
    shows up as a tab, and the unparseable ``cfg`` folder never does (nothing in it
    parses, so it contributes no tab)."""
    items = []
    for kind in morph_state.kinds():
        if not morph_state.loaded_of_kind(kind, kind in morph_state.SCOPED_KINDS):
            continue
        items.append((kind, _KIND_LABELS.get(kind, kind.title()),
                      "The cabmap's {0} assets".format(kind)))
    items.append((DRIVER_SECTION, "Drivers", "Every ctrl driver on this rig, live"))
    return items


def _phoneme_set_items(state, context):
    """The phoneme sets the lipsync config declares. Rebuilt from the parsed
    library, so a game update that adds a set just shows up."""
    lipsync = _lipsync_asset()
    sets = sorted(lipsync.phoneme_sets) if lipsync is not None else []
    return [(str(one), "Set {0}".format(one), "") for one in sets]


def _on_section(state, context):
    populate_items(state, context)


def _on_item_activated(state, context):
    """Highlighting a STATIC entry poses the face; an animated one is a clip to be
    built, not a pose to snap to, so it stays inert. Decided per item, not per
    section."""
    if filtering.is_rebuilding():
        return
    index = state.items_active_index
    if not state.apply_on_click or not (0 <= index < len(state.items)):
        return
    if state.items[index].animated:
        return
    _apply_item(state, context, index)


def _on_driver_weight(state, context):
    """Dragging one slider re-applies the WHOLE driver set, not just that ctrl:
    bone deltas accumulate, so a bone several ctrls share can only be solved from
    all of their weights at once. The other sliders keep their values, so what the
    user sees is still "I moved this one"."""
    rig = _rig(context, state)
    if rig is None:
        return
    host_port.current().drive_face(
        context, rig, table_of(state),
        {row.ctrl: row.weight for row in state.drivers})


HANDLERS = app_state.Handlers(
    "Endfield.face", section_items=_section_items,
    phoneme_set_items=_phoneme_set_items, on_section=_on_section,
    on_item_activated=_on_item_activated, on_driver_weight=_on_driver_weight)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
def apply_weights(state, context, weights):
    """Drive every bound ctrl to its weight, scaled by the panel's intensity."""
    rig = _rig(context, state)
    if rig is None:
        return
    intensity = state.intensity
    host_port.current().drive_face(
        context, rig, table_of(state),
        {ctrl: weight * intensity for ctrl, weight in weights.items()})
    # Mirror the applied weights back onto the Drivers list so its sliders show what
    # the face is actually doing. Written through the collection's own record, which
    # does not re-enter the slider's update.
    for row in state.drivers:
        row["weight"] = weights.get(row.ctrl, 0.0)


def _apply_item(state, context, index):
    if not (0 <= index < len(state.items)):
        return False
    asset = morph_state.ASSETS.get(state.items[index].asset)
    if asset is None:
        return False
    apply_weights(state, context, skeletal_morph.sample_weights(asset, 0.0))
    return True


def _lipsync_asset():
    for asset in morph_state.ASSETS.values():
        if asset.kind == "lipsync":
            return asset
    return None


# ---------------------------------------------------------------------------
# The lists
# ---------------------------------------------------------------------------
def _character_only(state):
    """Whether this section's kind was narrowed to the character at load time. Read
    from morph_state, which decided it -- never re-guessed from the section name."""
    return state.section in morph_state.SCOPED_KINDS


def populate_items(state, context):
    """Refill the visible list for the current section from the parsed library.

    Refilling is not a click: the highlight is restored by the asset it was on, so
    switching section or reloading the library cannot silently pose the face with
    whatever inherited the index."""
    with filtering.rebuilding():
        _fill_items(state, context)


def _fill_items(state, context):
    chosen = filtering.selected_key(state, "items", "items_active_index", "asset")
    state.items.clear()
    if state.section == DRIVER_SECTION:
        return
    bound = _bindings(context, state)["ctrls"]
    for asset in morph_state.loaded_of_kind(state.section, _character_only(state)):
        item = state.items.add()
        item.name = asset.name
        item.asset = asset.name
        item.kind = asset.kind
        item.duration = asset.duration
        item.animated = asset.is_animated()
        ctrls = asset.ctrl_names()
        item.ctrl_count = len(ctrls)
        item.bound_count = sum(1 for ctrl in ctrls if ctrl in bound)
    filtering.restore_selection(state, chosen, "items", "items_active_index", "asset")
    populate_drivers(state, context)


def populate_drivers(state, context):
    """One row per ctrl in the whole loaded library, bound ones first -- the
    complete vocabulary this character could be driven with, with the truth about
    which of it this rig can actually move."""
    state.drivers.clear()
    bound = _bindings(context, state)["ctrls"]
    channel_of = {}
    for asset in morph_state.ASSETS.values():
        for driver in asset.drivers():
            channel_of.setdefault(driver.ctrl, driver.channel)
    # A rig can expose ctrls the loaded animation library never mentions -- list
    # those too, so the Drivers view is the rig's full capability, not just what
    # this character's clips happen to use.
    for ctrl in bound:
        channel_of.setdefault(ctrl, "")
    for ctrl in sorted(channel_of, key=lambda one: ctrl_sort_key(one, bound)):
        row = state.drivers.add()
        row.ctrl = ctrl
        row.channel = (skeletal_morph.channel_label(channel_of[ctrl])
                       if channel_of[ctrl] else skeletal_morph.ctrl_group(ctrl).title())
        row.bound = ctrl in bound
        row.target_label = bound.get(ctrl, "")


def ctrl_sort_key(ctrl, bound_ctrls):
    return (0 if ctrl in bound_ctrls else 1, skeletal_morph.ctrl_group(ctrl), ctrl)


# ---------------------------------------------------------------------------
# What the buttons do
# ---------------------------------------------------------------------------
def _loaded(context):
    return app_browser.state_of(context).loaded and len(cabmap_state.ROWS) > 0


def _library_loadable(context):
    return bool(morph_state.LIBRARY) and cabmap_state.BRIDGE is not None


def _has_items(context):
    return len(state_of(context).items) > 0


def _has_checked(context):
    return any(item.selected for item in state_of(context).items)


def _has_lipsync(context):
    return _lipsync_asset() is not None


def _suggest_token(armature_name):
    """Guess the character token from the rig's own name by testing each of its name
    fragments against the discovered library: the fragment that matches some assets
    but not most of them is the character's. Derived, not tabulated -- a rig naming
    convention this importer has never seen still resolves."""
    if not morph_state.LIBRARY:
        morph_state.discover("")
    names = [entry["name"].lower()
             for entries in morph_state.LIBRARY.values() for entry in entries]
    if not names:
        return ""
    best, best_hits = "", 0
    for fragment in re.split(r"[^A-Za-z0-9]+", armature_name.lower()):
        if len(fragment) < 4 or fragment.isdigit():
            continue
        hits = sum(1 for name in names if fragment in name)
        if 0 < hits < len(names) // 2 and hits > best_hits:
            best, best_hits = fragment, hits
    return best


def _scan(context, arguments):
    """Bind the rig and index the cabmap's morph library -- both cheap: the binding
    is a walk over this armature's meshes, and discovery reads the already-loaded
    row table's container paths (no VFS decrypt, no export)."""
    state = state_of(context)
    host = host_port.current()
    rig = host.selected_rig(context)
    if rig is None:
        state.status = ("Select the character whose face you want to drive: its "
                        "skeleton, or any mesh skinned to it.")
        return {"CANCELLED"}

    # The declaration belongs to the RIG, so a scan of a different one never
    # inherits the last one's face (which would bind some other npc's ctrls and look
    # like it worked). Whatever loaded this rig may have stated it already;
    # otherwise the rig is named after its entity, so its own name is the key the
    # game files that declaration under.
    if rig.name != state.armature_name:
        state.face_morph = ""
    state.armature_name = rig.name
    state.face_morph = state.face_morph or cast.declared_face_morph(rig.name)
    # An npc rig names its own template, which is the EXACT key its per-line
    # dialogue assets are filed under. Guessing a name fragment instead picks
    # whichever fragment matches most, and for an npc that is its body-type word --
    # scoping the load to every sibling that shares it.
    token = (state.character_token.strip()
             or cast.npc_template(rig.name)
             or _suggest_token(rig.name))
    morph_state.discover(token)
    state.character_token = token
    # Retabled AFTER discovery: the bone table comes from this rig's own avatar,
    # which only exists once Load Library has run, so a first scan binds shape keys
    # only and Load Library re-tables with the real one.
    retable(state)

    counts = morph_state.counts()
    own = sum(morph_state.counts(character_only=True).values()) if token else 0
    state.status = ("{0} morph asset(s) indexed".format(sum(counts.values()))
                    + (", {0} for '{1}'".format(own, token) if token else "")
                    + "; {0} ctrl(s) bound on {1}.".format(
                        len(_bindings(context, state)["ctrls"]), rig.name))
    state.library_ready = False
    state.items.clear()
    state.drivers.clear()
    return None


def _load_library(context, arguments):
    """Resolve and parse the morph assets themselves. This is the first point
    anything is exported at all -- discovery above only read the cabmap's own
    tables."""
    state = state_of(context)
    entries, dropped = morph_state.plan_load()
    cabs = morph_state.cabs_for(entries)
    if not cabs:
        state.status = "Nothing to load -- run Scan Rig first."
        return {"CANCELLED"}

    parsed = yield command.Read(lambda: morph_state.load(cabs), 0.8)

    # Re-table now, not before: the character's avatar -- the ctrl-to-bone table the
    # whole system runs on -- only exists after this load.
    retable(state)
    bound = _bindings(context, state)
    vocabulary = morph_state.all_ctrl_names()
    hits = sum(1 for ctrl in vocabulary if ctrl in bound["ctrls"])
    state.library_ready = True
    via = ", ".join("{0} {1}".format(count, how) for count, how in bound["via"])
    lines = []
    if dropped:
        # Never a silent cap: say what was left out and why.
        lines.append("{0} asset(s) skipped -- {1} narrowed to '{2}'.".format(
            dropped, sorted(morph_state.SCOPED_KINDS), morph_state.CHARACTER_TOKEN))
    if not avatars_for(state):
        lines.append("No avatar table matched this character -- only baked shape keys "
                     "can be driven. Check the Character token.")
    elif bound["reason"]:
        lines.append(bound["reason"])
    elif bound["missing_bones"]:
        missing = bound["missing_bones"]
        lines.append("{0} face bone(s) this character's tables name are not on '{1}': "
                     "{2}{3}".format(len(missing), state.armature_name,
                                     ", ".join(missing[:4]),
                                     " ..." if len(missing) > 4 else ""))
    state.status = ("{0} asset(s) parsed · {1} ctrl driver(s) · {2} bound on {3}".format(
        parsed, len(vocabulary), hits, state.armature_name or "this rig")
        + (" (" + via + ")" if via else "") + ". " + "  ".join(lines))
    populate_items(state, context)
    populate_drivers(state, context)


def _apply(context, arguments):
    state = state_of(context)
    if not _apply_item(state, context, state.items_active_index):
        state.status = "Nothing to apply."
        return {"CANCELLED"}
    return None


def _clear(context, arguments):
    apply_weights(state_of(context), context, {})
    return None


def _apply_phoneme(context, arguments):
    """Pose one phoneme of the selected lipsync set. The slot index is the position
    within that set's pose list -- the config's own ordering, which is what the game
    indexes with too."""
    state = state_of(context)
    lipsync = _lipsync_asset()
    try:
        set_id = int(state.phoneme_set)
    except (TypeError, ValueError):
        state.status = "No phoneme set selected."
        return {"CANCELLED"}
    poses = lipsync.phoneme_sets.get(set_id) or []
    slot = int(arguments["slot"])
    if not (0 <= slot < len(poses)) or not poses[slot]:
        state.status = "This set has no pose in that slot."
        return {"CANCELLED"}
    asset = morph_state.ASSETS.get(poses[slot])
    if asset is None:
        state.status = "That phoneme pose is not in the loaded closure."
        return {"CANCELLED"}
    apply_weights(state, context, skeletal_morph.sample_weights(asset, 0.0))
    return None


def _select_all(context, arguments):
    mode = arguments["mode"]
    for item in state_of(context).items:
        item.selected = (mode == "ALL" if mode != "INVERT" else not item.selected)
    return None


def tracks_of(asset):
    """One animation's ctrl curves as ``{ctrl: [(time, value, in_slope, out_slope)]}``
    -- the game's own keys, in seconds, with per-second slopes."""
    found = {}
    for driver in asset.drivers():
        curve = driver.curve
        if curve is None or not len(curve.times):
            continue
        found[driver.ctrl] = [
            (float(curve.times[index]), float(curve.values[index, 0]),
             float(curve.in_slopes[index, 0]), float(curve.out_slopes[index, 0]))
            for index in range(len(curve.times))]
    return found


def frames_of(asset, fps):
    """The same animation sampled per frame, by THIS GAME's own evaluator. The bone
    half needs it: several ctrls with independent key times sum into one bone, and
    how the curves read between keys is the game's answer."""
    duration = asset.duration or max(
        (driver.curve.last_time() for driver in asset.drivers()
         if driver.curve is not None), default=0.0)
    count = max(1, int(round(duration * fps)) + 1)
    return [skeletal_morph.sample_weights(asset, index / float(fps))
            for index in range(count)]


def _build_actions(context, arguments):
    """Bake the checked morph animations. Keyframes carry the game's own times and
    weights on the shape-key half -- no resampling -- while the bone half is sampled
    per frame, because a bone is the sum of every ctrl pushing it."""
    state = state_of(context)
    host = host_port.current()
    rig = _rig(context, state)
    bound = _bindings(context, state)
    if rig is None or not bound["ctrls"]:
        state.status = "No ctrl driver on this rig is bound -- nothing to key."
        return {"CANCELLED"}

    fps = host.frame_rate(context)
    table = table_of(state)
    built, skipped = 0, 0
    playing = None
    for item in state.items:
        if not item.selected:
            continue
        asset = morph_state.ASSETS.get(item.asset)
        if asset is None or not asset.is_animated():
            skipped += 1
            continue
        wrote = yield command.Read(
            lambda _a=asset: host.bake_face(context, rig, table, tracks_of(_a),
                                            frames_of(_a, fps), fps, _a.name), 0.9)
        if wrote:
            built += 1
            playing = asset
        else:
            skipped += 1

    if not built:
        state.status = ("Nothing built -- none of the {0} checked animation(s) drive "
                        "a ctrl bound on this rig.".format(skipped))
        return
    # One face animation is two actions (shape keys + bones), so neither half retimes
    # on its own; the document is aimed once, at whichever animation is left playing
    # -- the same "importing it IS the request to see it" rule every other clip flow
    # follows.
    if playing is not None and playing.duration:
        host.set_frame_range(context, 0.0, playing.duration * fps)
    state.status = "Built {0} morph action(s).".format(built) + (
        " {0} skipped (no bound ctrl).".format(skipped) if skipped else "")


SCAN = command.COMMANDS.define(
    "ruri.character_scan", "Scan Rig", _scan,
    description="Detect the character rig, bind its ctrl drivers, and index the "
                "cabmap's facial-morph library",
    icon="FILE_REFRESH", requires=host_port.MORPH_TARGETS, poll=_loaded)
LOAD_LIBRARY = command.COMMANDS.define(
    "ruri.character_load_library", "Load Library", _load_library,
    description="Export and parse the shared emotion/pose/lipsync library plus this "
                "character's own morph animations",
    icon="IMPORT", requires=host_port.MORPH_TARGETS, poll=_library_loadable,
    steps=True, status_state=STATE, failure="Morph library load failed")
APPLY = command.COMMANDS.define(
    "ruri.character_apply", "Apply", _apply,
    description="Pose the face with the highlighted entry",
    icon="PLAY", requires=host_port.MORPH_TARGETS, poll=_has_items)
CLEAR = command.COMMANDS.define(
    "ruri.character_clear", "Rest Face", _clear,
    description="Zero every bound ctrl driver",
    icon="LOOP_BACK", requires=host_port.MORPH_TARGETS)
APPLY_PHONEME = command.COMMANDS.define(
    "ruri.character_apply_phoneme", "Phoneme", _apply_phoneme,
    description="Pose one phoneme of the selected lipsync set",
    requires=host_port.MORPH_TARGETS, poll=_has_lipsync,
    arguments=(Field("slot", app_state.INT, 0),))
SELECT_ALL = command.COMMANDS.define(
    "ruri.character_select_all", "Select", _select_all,
    description="Check / uncheck every listed animation",
    requires=host_port.MORPH_TARGETS,
    arguments=(Field("mode", app_state.STRING, "ALL"),))
BUILD_ACTIONS = command.COMMANDS.define(
    "ruri.character_build_actions", "Build Checked Actions", _build_actions,
    description="Bake the checked morph animations onto whatever this rig binds "
                "their ctrls to",
    icon="ACTION", requires=host_port.MORPH_TARGETS, poll=_has_checked, steps=True,
    status_state=STATE, failure="Building morph actions failed")


# ---------------------------------------------------------------------------
# What it looks like
# ---------------------------------------------------------------------------
#: Emotions/poses read as one-click entries; animations get a checkbox and a
#: duration, because they are BUILT in batches rather than applied.
_ITEM_COLUMNS = (
    app_layout.ListColumn("", width=0.08, prop="selected",
                          enabled=lambda row: bool(row.animated)),
    # Unbound entries stay listed and stay usable -- they are real game data;
    # greying them says "this rig cannot show it" without hiding it.
    app_layout.ListColumn("name", width=0.72,
                          active=lambda row: row.bound_count > 0),
    app_layout.ListColumn(lambda row: "{0:.2f}s".format(row.duration) if row.duration else "",
                          align=app_layout.RIGHT),
    app_layout.ListColumn(lambda row: "{0}/{1}".format(row.bound_count, row.ctrl_count),
                          align=app_layout.RIGHT),
)

_DRIVER_COLUMNS = (
    app_layout.ListColumn("ctrl", width=0.45, active=lambda row: row.bound),
    app_layout.ListColumn("channel", width=0.5),
    app_layout.ListColumn("", prop="weight", enabled=lambda row: row.bound),
)


def draw(layout, context):
    """The face section. The tab above it already handled the cabmap gate."""
    state = state_of(context)
    command.draw_progress(layout, state)

    rig_row = layout.row(align=True)
    rig_row.prop(state, "character_token", icon="OUTLINER_OB_ARMATURE")
    rig_row.operator(SCAN.id, text="", icon="FILE_REFRESH")

    if state.armature_name:
        info = layout.row(align=True)
        info.label(text=state.armature_name, icon="ARMATURE_DATA")
        if not state.library_ready:
            info.operator(LOAD_LIBRARY.id, text="Load Library", icon="IMPORT")

    layout.label(text=state.status, icon="INFO")
    if not state.library_ready:
        return

    body = layout.column()
    body.row(align=True).prop(state, "section", expand=True)

    if state.section == DRIVER_SECTION:
        _draw_drivers(body, context, state)
        return

    body.list(state, "items", "items_active_index", _ITEM_COLUMNS, rows=12,
              identifier="morph_items")

    # Animated entries are BUILT in batches; static ones are APPLIED. Which
    # controls appear follows the data in the list, not the section's name.
    if any(item.animated for item in state.items):
        bar = body.row(align=True)
        for mode, label in (("ALL", "All"), ("NONE", "None"), ("INVERT", "Invert")):
            bar.operator(SELECT_ALL.id, text=label).mode = mode
        checked = sum(1 for item in state.items if item.selected)
        bar.label(text="{0} checked".format(checked) if checked else "")
        body.operator(BUILD_ACTIONS.id, icon="ACTION")
        return

    controls = body.column(align=True)
    controls.prop(state, "intensity", slider=True)
    row = controls.row(align=True)
    row.operator(APPLY.id, icon="PLAY")
    row.operator(CLEAR.id, icon="LOOP_BACK")
    controls.prop(state, "apply_on_click")
    _draw_phonemes(body, state)


def _draw_phonemes(layout, state):
    """The mouth shapes as a keyboard rather than a list: the lipsync config maps a
    phoneme SET to an ordered list of poses, so the buttons are that list's own
    slots -- a set with more slots simply grows more buttons."""
    lipsync = _lipsync_asset()
    if lipsync is None:
        return
    # Only where the mouth shapes themselves are: the keyboard is a shortcut INTO
    # the listed poses, so it appears exactly when the list contains some of the
    # poses the lipsync config points at.
    referenced = {name for poses in lipsync.phoneme_sets.values() for name in poses if name}
    if not any(item.asset in referenced for item in state.items):
        return
    box = layout.box()
    header = box.row(align=True)
    header.label(text="Lip Sync", icon="SORTALPHA")
    header.prop(state, "phoneme_set", text="")
    try:
        poses = lipsync.phoneme_sets.get(int(state.phoneme_set)) or []
    except (TypeError, ValueError):
        return
    keys = box.row(align=True)
    for slot, pose in enumerate(poses):
        asset = morph_state.ASSETS.get(pose) if pose else None
        cell = keys.row(align=True)
        cell.enabled = asset is not None
        # The pose's own name tail is the phoneme ("..._normal_a" -> "A"); an empty
        # slot keeps its place so the keyboard layout stays stable.
        label = asset.name.rsplit("_", 1)[-1].upper() if asset else "·"
        cell.operator(APPLY_PHONEME.id, text=label).slot = slot


def _draw_drivers(layout, context, state):
    bound = _bindings(context, state)
    total = len(state.drivers)
    hits = sum(1 for row in state.drivers if row.bound)
    header = layout.row(align=True)
    header.label(text="{0} of {1} ctrl driver(s) bound".format(hits, total),
                 icon="CON_ACTION")
    header.prop(state, "show_unbound", toggle=True)
    # A driver the rig has no bone for is HIDDEN rather than removed while the
    # toggle says so -- the row is real game data and comes back when it is off.
    layout.list(state, "drivers", "drivers_active_index", _DRIVER_COLUMNS, rows=14,
                identifier="morph_drivers",
                visible_key="" if state.show_unbound else "bound")
    layout.operator(CLEAR.id, icon="LOOP_BACK")

    if bound["rest_error"] is not None:
        info = layout.box()
        info.label(text=", ".join("{0} {1}".format(count, how)
                                  for count, how in bound["via"]) or "nothing bound",
                   icon="BONE_DATA")
        # The table's base pose IS this rig's rest pose, so how closely the two agree
        # is a live correctness readout, not decoration: a large number means the
        # table got matched to the wrong rig. Calibrated, not guessed: the right rig
        # fits to ~1e-2 (a few bones sit at a neutral morph pose rather than the bind
        # pose), while binding the WRONG character's table measured ~1.7 -- so
        # anything past 0.5 means the table does not belong to this rig.
        line = info.row()
        line.alert = bound["rest_error"] > 0.5
        line.label(text="Rest-pose fit: {0:.1e}".format(bound["rest_error"])
                        + ("  (wrong rig?)" if bound["rest_error"] > 0.5 else ""))
    elif not bound["ctrls"]:
        note = layout.box()
        note.label(text="Nothing bound. This rig has no baked ctrl", icon="ERROR")
        note.label(text="shape keys and no avatar table matched it --")
        note.label(text="run Load Library, and check the Character token.")
    elif hits < total:
        note = layout.box()
        note.label(text="Unbound ctrls belong to other characters'", icon="INFO")
        note.label(text="clips in the shared library.")


def load_library_for(context, entry, facial_morph):
    """Bring this character's face up right after the cast browser built her.

    The same two moves the section's own buttons make -- scan the rig that was just
    built, then export and parse the library that rig's declaration names -- so the
    quick path and the manual one cannot drift.

    The declaration is seeded BETWEEN them, not before: a scan of a rig it has not
    seen clears the declaration on purpose (inheriting the last one's face binds
    some other character's ctrls and looks like it worked), and what the roster read
    off the game's own manifest is exactly what fills that gap for an npc whose face
    table is not named after it."""
    state = state_of(context)
    if SCAN.run(context, {}) == {"CANCELLED"}:
        return {"CANCELLED"}
    if facial_morph and not state.face_morph:
        state.face_morph = facial_morph
    if entry is not None and not state.character_token:
        state.character_token = str(getattr(entry, "key", "") or "")
    return LOAD_LIBRARY.run(context, {})


def register():
    host_port.current().register_state(STATE, CHARACTER, HANDLERS)


def unregister():
    host_port.current().unregister_state(STATE)
    _TABLE["state"] = None
    morph_state.reset()
