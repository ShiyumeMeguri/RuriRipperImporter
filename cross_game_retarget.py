"""Cross-game animation retarget: play one game's animation on another game's rig.

A humanoid clip already retargets by itself -- muscle values are avatar-relative, and the
solve runs against whichever skeleton the clip is bound to (see prefab_importer.
_solve_humanoid_curves). A GENERIC clip cannot: its curves carry per-bone local rotations
in the SOURCE rig's own bone axes, so there is nothing avatar-shaped to re-decode. It needs
a real retarget -- world-rotation transfer through both rigs' rest poses -- and one table
saying which bone is which.

That is exactly what the AnimationRetarget add-on already is, so this module adds no maths
of its own. It contributes the two things AnimationRetarget cannot know:

* **which table** -- every armature this importer builds is stamped with the game it came
  from (``armature_builder.UNITY_GAME_PROP``), so a source rig and a target rig NAME the
  pair, and the table is found by identity instead of the user hunting through a preset list;
* **the tables themselves** -- ordinary AnimationRetarget presets, so they stay editable in
  its own panel and nothing here is a second config format.

**Naming is one rule, and it is symmetric**: ``<GameA>To<GameB>.json``. One file serves both
directions -- ``AToB`` retargets A→B as written and B→A with every pair flipped -- because a
bone correspondence has no direction. Shipping ``BToA`` as a second file would be two sources
of truth for one fact, and they would drift.
"""

from __future__ import annotations

import os

import bpy

try:
    from . import armature_builder, prefab_importer
except ImportError:  # standalone (non-package) testing
    import armature_builder
    import prefab_importer


ADDON_MODULE = "AnimationRetarget"


def _addon():
    """The AnimationRetarget add-on's own modules, or None when it is not installed.

    Imported lazily and by name: this importer works perfectly well without it (the
    retarget is an extra, not a dependency), and a hard import would make every
    session fail on a profile that does not have it.
    """
    try:
        core = __import__(ADDON_MODULE + ".core", fromlist=["core"])
        presets = __import__(ADDON_MODULE + ".presets", fromlist=["presets"])
    except ImportError:
        return None
    return core, presets


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
    _core, presets = addon
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


def _armatures(context):
    return [obj for obj in context.scene.objects if obj.type == "ARMATURE"]


def resolve_pair(context):
    """(source rig, dest rig) for a retarget, or (None, reason).

    The DEST is the armature the user is acting on -- the same choice every other
    animation flow makes (prefab_importer.find_target_armature, which also accepts a
    selected mesh and resolves the rig it is bound to). The SOURCE is the one other
    stamped armature whose game differs; more than one candidate is ambiguous and says so
    rather than picking.
    """
    dest = prefab_importer.find_target_armature(context)
    if dest is None:
        return None, ("No target skeleton -- select the armature (or a mesh bound to it) "
                      "the animation should end up on.")
    dest_game = armature_builder.read_game(dest)
    if not dest_game:
        return None, ("'{0}' carries no game identity -- re-import it through the cabmap "
                      "browser once, then it knows which game it is from.".format(dest.name))
    candidates = [obj for obj in _armatures(context)
                  if obj is not dest and armature_builder.read_game(obj)
                  and armature_builder.read_game(obj) != dest_game]
    if not candidates:
        return None, ("No other game's skeleton in the scene -- import the character whose "
                      "animation you want to borrow, then retarget onto '{0}'.".format(dest.name))
    if len(candidates) > 1:
        return None, ("{0} other skeletons could be the source ({1}) -- delete or hide the "
                      "ones you don't mean.".format(
                          len(candidates), ", ".join(obj.name for obj in candidates[:4])))
    return (candidates[0], dest), None


def describe(context):
    """One line for the panel: what would happen if the button were pressed now."""
    if not available():
        return "AnimationRetarget add-on not enabled", False
    pair, reason = resolve_pair(context)
    if pair is None:
        return reason, False
    source, dest = pair
    source_game = armature_builder.read_game(source)
    dest_game = armature_builder.read_game(dest)
    pairs, name, flipped = find_table(source_game, dest_game)
    if pairs is None:
        return "No '{0}' table in the AnimationRetarget presets".format(name), False
    direction = " (reversed)" if flipped else ""
    return "{0} -> {1}: '{2}'{3}, {4} bone(s)".format(
        source_game, dest_game, name, direction, len(pairs)), True


class RURI_OT_retarget_cross_game(bpy.types.Operator):
    """Bake this scene's other-game animations onto the selected skeleton, through the
    <GameA>To<GameB> bone table -- the retarget maths is AnimationRetarget's own."""
    bl_idname = "ruri.retarget_cross_game"
    bl_label = "Retarget Onto This Skeleton"
    bl_description = ("Retarget every animation on the other game's skeleton onto the "
                      "selected one, using the matching <GameA>To<GameB> bone table")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return available()

    def execute(self, context):
        addon = _addon()
        if addon is None:
            self.report({"ERROR"}, "The AnimationRetarget add-on is not enabled.")
            return {"CANCELLED"}
        core, _presets = addon

        pair, reason = resolve_pair(context)
        if pair is None:
            self.report({"ERROR"}, reason)
            return {"CANCELLED"}
        source, dest = pair
        source_game = armature_builder.read_game(source)
        dest_game = armature_builder.read_game(dest)

        pairs, name, flipped = find_table(source_game, dest_game)
        if pairs is None:
            self.report({"ERROR"}, (
                "No bone table for {0} -> {1}. Write '{2}.json' into the AnimationRetarget "
                "presets folder (either direction works -- the table is symmetric).".format(
                    source_game, dest_game, name)))
            return {"CANCELLED"}

        actions = core.list_bakeable_actions(source)
        if not actions:
            self.report({"ERROR"}, "'{0}' has no animation to retarget.".format(source.name))
            return {"CANCELLED"}

        spec = {"mappings": pairs}
        results, errors = core.bake_all(source, dest, spec,
                                        {"suffix": "_" + dest_game.lower()}, actions)
        for action_name, message in errors[:5]:
            self.report({"WARNING"}, "{0}: {1}".format(action_name, message))
        if not results:
            self.report({"ERROR"}, "Nothing baked -- see the warnings above.")
            return {"CANCELLED"}
        self.report({"INFO"}, "Retargeted {0} action(s) {1} -> {2} onto '{3}' via '{4}'{5}.".format(
            len(results), source_game, dest_game, dest.name, name,
            " reversed" if flipped else ""))
        return {"FINISHED"}


class RURI_PT_cross_game_retarget(bpy.types.Panel):
    """Only visible with the AnimationRetarget add-on enabled -- with no retarget maths
    available there is nothing this panel could offer."""
    bl_idname = "RURI_PT_cross_game_retarget"
    bl_label = "Cross-Game Retarget"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return available()

    def draw(self, context):
        layout = self.layout
        message, ready = describe(context)
        layout.label(text=message, icon="CHECKMARK" if ready else "INFO")
        row = layout.row()
        row.enabled = ready
        row.operator(RURI_OT_retarget_cross_game.bl_idname, icon="ARMATURE_DATA")


CLASSES = (RURI_OT_retarget_cross_game, RURI_PT_cross_game_retarget)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
