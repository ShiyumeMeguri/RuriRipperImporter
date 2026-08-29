"""Put one story unit on stage: walk the game's own directive stream and build it.

The hook reads the unit's Timeline and hands back a STAGE -- rows that say what to
do, in seconds, against names (``endfield.story.stage``). Nothing in that stream is
Unity-shaped, so nothing here has to know what an ``ActivationTrack`` is; it knows
what ``show`` means, and there is exactly one function that means it.

That is the whole architecture: a directive kind is a key in ``REALIZERS`` and a
function under ``@realizer``. Supporting something the game adds later is one new
function and no edit to anything that already works -- which is the property the
previous design did not have, since every track class was another branch inside one
operator.

WHAT IS BUILT, and why each is the shape it is:

``motion``    an NLA strip on the rig / camera / stand-in the timeline binds it to.
              Timeline states (start, duration, clipIn, speed) in SECONDS against
              the clip's own sample rate; a strip states frames plus the action
              frames it eats. The conversion is exact, not fitted.
``camera``    the unit films through ONE camera whose animation is a sequence of
              shot clips -- so a cut is a clip boundary, and pressing play cuts
              exactly where the game cuts. That camera becomes ``scene.camera``:
              the game's main camera, which is what makes playback need no setup.
``show``      keyframed visibility, constant interpolation, because activation is a
              switch and a state smoothed between two states is one the game never
              shows.
``line``      a real 3D text object parented to that camera, visible exactly across
              its own span. Renderable on purpose: a viewport overlay cannot be
              rendered, and a story you cannot render is half a story.
``beats``     ``line``/``click``/``hold``/``jump``/``option`` also land on the scene
              as a script of BEATS. That is what the player reads to advance the
              story a click at a time, and it is scene data rather than module
              state so a saved .blend still plays.

Everything else the timeline carries that a host cannot literally play -- an audio
event, a mask, a logo -- becomes a timeline marker at its own second. Named and
timed is the honest answer; silently dropped is not.
"""

from __future__ import annotations

import os
import re

import bpy

from ... import step_loader
from ...RuriRipperPyBridge.session import cabmap_state
from . import cast, datasets

# What a directive means, by its own word, and what it needs before it can mean
# it. The registry IS the extension point: a new kind is one function under
# @realizer, plus -- only if it loads something -- one under @needs.
REALIZERS = {}
NEEDS = {}

# Where a subtitle sits in front of the camera, in metres of camera space, and how
# tall its text is there. Not a game fact -- the game draws its box in screen space
# and there is no screen space in a 3D scene -- so it is stated once here.
SUBTITLE_DISTANCE = 2.0
SUBTITLE_DROP = -0.62
SUBTITLE_SIZE = 0.085
SUBTITLE_WIDTH = 26

# A story is told in the language the game ships, and Blender's own font draws none
# of it. These are the faces Windows ships that cover CJK; the first that exists
# wins, and if none does the text objects still build with whatever Blender has.
CJK_FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
             "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc",
             "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/malgun.ttf")

# Why a CAB is wanted. The two want opposite export filters, which is exactly why
# ACQUIRE is two crossings and not one: a model is wanted whole, a clip CAB only
# for its AnimationClips, and a unit with five hundred of the latter cannot pay the
# former for them.
CLIP = "clip"
SCENERY = "scenery"

# The scene the story plays in is enormous and the story does not need it to play.
SCENE_NONE = "none"
SCENE_WINDOW = "window"
SCENE_FULL = "full"


def realizer(*kinds):
    """Register the one function that means a directive."""
    def register(function):
        for kind in kinds:
            REALIZERS[kind] = function
        return function
    return register


def needs(*kinds):
    """Register what a directive needs LOADED, as CAB names, from its row alone.

    Declaring is separate from realizing on purpose: it runs before anything is
    read, so everything the unit needs is known at once and one crossing covers
    it. A kind with no declaration needs nothing loaded -- which is a statement,
    not an omission, because the alternative (reaching for the bridge while
    realizing) is not reachable from a realizer at all."""
    def register(function):
        for kind in kinds:
            NEEDS[kind] = function
        return function
    return register


class Stage:
    """One unit being built, and what the build found.

    Deliberately a plain object rather than scene state: it lives for one build.
    What has to SURVIVE the build is written onto the scene (strips, keyframes,
    markers, the beat script), because that is what the player and the renderer
    read.
    """

    def __init__(self, context, unit, fps):
        self.context = context
        self.unit = unit
        self.fps = fps
        self.end = 0.0
        self.actors = {}
        self.targets = {}
        self.spans = {}
        self.camera = None
        self.shots = []
        self.lines = []
        self.beats = []
        self.placed = 0
        self.unplaced = []
        # What a member's build could not do. Not the same fact as a clip with
        # nothing to drive, so not the same list -- the summary says both.
        self.notes = []
        self.unresolved = []
        self.markers = 0
        self.looking = 0
        # The unit's own prefab, by object name: the stage the cast stands on and
        # the virtual cameras the shots cut between.
        self.props = {}
        self.stood = 0
        # Shots whose camera is an object in that stage: they cannot be realized until
        # the stage is up, and the stage cannot be built until the cast closure is read.
        self.pending_cuts = []
        self.unknown = set()
        # The closure every realizer builds against. Set once, by ACQUIRE.
        self.scenery = None
        self.motion = None
        self.cast = {}

    def frame(self, seconds):
        return seconds * self.fps

    def note(self, row):
        self.end = max(self.end, self.frame(row["until"]))


def build(context, unit, variant="", language="", scene_mode=SCENE_NONE):
    """Bring one unit up on stage, synchronously, and return its Stage (whose
    counters are the report), or None when the unit places nothing.

    Drives the very step sequence the modal loader drives, running every read
    inline -- so a headless build and the interactive one produce byte-for-byte
    the same scene, and this stays THE single definition of what a unit becomes."""
    return step_loader.run(build_steps(context, unit, variant, language,
                                       scene_mode))


def build_steps(context, unit, variant="", language="", scene_mode=SCENE_NONE):
    """The unit build as a sequence of steps a driver can pace: a cross-boundary
    read off the main thread, the bpy write on it. Yields ``_Read`` for a read
    whose result it needs back and ``_Mark`` to pace the writes; RETURNS the
    finished Stage, or None when the unit places nothing.

    The order is the game's own and unchanged: the shots and the scene camera
    first (a character import builds its outline shells against the scene camera,
    and a cutscene is a camera performance anyway -- the actors are what it films),
    then the cast, then every remaining directive in time order.
    """
    rows = yield step_loader.Read(lambda: _stage_rows(unit, variant, language), 0.12)
    if not rows:
        return None
    stage = Stage(context, unit, _scene_fps(context, rows))
    for row in rows:
        stage.note(row)

    # MANIFEST: everything this unit will ever need, before anything is read.
    detail = context.scene.ruri_cabmap.detail_level
    manifest = yield step_loader.Read(lambda: _manifest(rows, detail, unit), 0.25)
    stage.cast = manifest["cast"]
    stage.unresolved.extend(manifest["unresolved"])

    # ACQUIRE, first crossing: every clip, filtered to AnimationClip. Before the
    # models because the shots come first and they are read straight off it.
    stage.motion = yield step_loader.Read(lambda: _acquire_clips(manifest["clips"]), 0.45)
    _hold_curves(stage, manifest["direct"])
    for row in rows:
        if _drives_camera(row):
            _realize(stage, row)
    if stage.camera is not None:
        context.scene.camera = stage.camera
        context.scene.frame_set(int(round(min(
            (stage.frame(row["at"]) for row in rows if _drives_camera(row)), default=0.0))))

    # ACQUIRE, second crossing: every model, prop and effect the unit puts on
    # stage -- the whole cast in ONE read, which is the point of the manifest.
    stage.scenery = yield step_loader.Read(lambda: _acquire_scenery(manifest["scenery"]), 0.62)
    _raise_stage(stage, manifest)
    for pending in stage.pending_cuts:
        if _cut_to(stage, pending):
            stage.placed += 1
        else:
            stage.unplaced.append(pending["source"])
    yield from _cast_steps(stage, manifest)
    _stand_cast(stage, rows)

    for row in rows:
        if not _drives_camera(row):
            _realize(stage, row)
    yield step_loader.Mark(0.94)
    _finish(stage)
    if scene_mode != SCENE_NONE:
        _load_scene(stage, scene_mode)
    yield step_loader.Mark(1.0)
    return stage


# -- manifest -----------------------------------------------------------------

def _manifest(rows, level=0, unit=""):
    """What this unit needs, as a value: who is in it, and every CAB to mark.

    Pure with respect to the scene and to the closure -- it asks the game's own
    tables what a performer loads as, and answers with names, never with imports.
    That is what makes the whole cast markable in one read instead of resolving
    itself one member at a time while it builds."""
    members = {}
    for row in rows:
        if not row["model"]:
            continue
        key = _identity(row)
        if key and key not in members:
            members[key] = {"key": key, "label": row["performer"] or row["actor"],
                            "character": row["character"], "template": row["template"],
                            "binding": row["target"]}
    resolved = cast.resolve(list(members.values()), level)

    scenery = cast.cabs_of(resolved.values()) + _stage_cabs(unit)
    clips = []
    direct = []
    by_actor = {}
    for row in rows:
        declare = NEEDS.get(row["kind"])
        if declare is None:
            continue
        for role, cab in declare(row):
            if not cab:
                continue
            if role is not CLIP:
                scenery.append(cab)
                continue
            clips.append(cab)
            key = _identity(row) if row["model"] else ""
            if key and key in resolved:
                # Whose clip it is, so the animation lands on the rig that IS them.
                by_actor.setdefault(key, []).append(cab)
            else:
                # A clip driving something with no rig -- the camera, a prop, a
                # performer the game ships no model for -- is read as curves onto
                # a stand-in instead, so its CAB is wanted a second way.
                direct.append(cab)
    return {"cast": resolved,
            "unresolved": [members[key]["label"] or key
                           for key in members if key not in resolved],
            "scenery": list(dict.fromkeys(scenery)),
            "stage": _stage_cabs(unit),
            "clips": list(dict.fromkeys(clips)),
            "direct": list(dict.fromkeys(direct)),
            "by_actor": {key: list(dict.fromkeys(cabs)) for key, cabs in by_actor.items()}}


def _stage_cabs(unit):
    """The CABs of the unit's own prefab -- the stage itself.

    It holds what the clips do not: where each performer stands, and the virtual
    cameras the shots cut between. Asked of the cabmap by the unit's own name
    rather than assembled out of a path, so nothing here encodes how this game
    lays its folders out."""
    if not unit:
        return []
    try:
        return [row["cab"] for row in datasets.named_rows(unit)]
    except Exception:
        return []


def _identity(row):
    """The game's own identity for a performer -- the character it IS, the npc
    template its model is assembled from, else the token the story files it under.

    Never the display token when an identity exists: one person is written several
    ways across a unit (``gentleman_hsteacher_10`` on its clip, ``npc_gentleman_
    hsteacher_10`` on its binding), and keying on the writing makes one person two
    performers, each loading the same model onto a rig the other cannot use."""
    return row["character"] or row["template"] or row["actor"] or ""


def _acquire_clips(cabs):
    """One crossing for every clip the unit plays. Filtered to AnimationClip
    because that is all any of them is wanted for -- a unit with five hundred clip
    CABs would otherwise serialize five hundred bundles whole."""
    from ...RuriRipperPyBridge.unity import class_registry
    from ... import cabmap_panel
    if not cabs:
        return None
    return cabmap_panel.resolve_import_closure(
        cabs, [class_registry.id_for_name("AnimationClip")])


def _acquire_scenery(cabs):
    """One crossing for every model, prop and effect. Unfiltered: a model IS its
    closure -- meshes, materials, textures, avatars."""
    from ... import cabmap_panel
    return cabmap_panel.resolve_import_closure(cabs) if cabs else None


def _cast_steps(stage, manifest):
    """Put the cast on stage off the two closures, and land each performer's clips
    on the rig that IS them.

    Building is per member and the animation build is per rig -- deliberately, and
    it costs nothing: neither crosses the bridge. What used to be per member was
    the READ, and that is now behind them both."""
    from ... import cross_game_retarget
    # Distinct MEMBERS, not distinct spellings: several tokens of one performer
    # share one Loadable, and building it twice would give one person two rigs.
    members = list({loadable.key: loadable for loadable in stage.cast.values()}.values())
    for index, loadable in enumerate(members, start=1):
        rig = _rig_for(stage.context, loadable)
        if rig is None and stage.scenery is not None:
            before = {obj.name for obj in stage.context.scene.objects
                      if obj.type == "ARMATURE"}
            built = cast.build(stage.context, loadable, stage.scenery)
            stage.notes.extend(built.warnings)
            rig = _adopt(stage.context, loadable, built, before)
        if rig is None:
            stage.unresolved.append(loadable.label)
            continue
        for spelling, found in stage.cast.items():
            if found.key == loadable.key:
                stage.actors[spelling] = rig
        yield step_loader.Mark(0.62 + 0.16 * (index / max(len(members), 1)))

    if stage.motion is None:
        return
    landing = {}
    for spelling, rig in stage.actors.items():
        wanted = landing.setdefault(rig.name, [rig, []])
        wanted[1].extend(manifest["by_actor"].get(spelling, ()))
    landing = [(name, rig, list(dict.fromkeys(cabs)))
               for name, (rig, cabs) in landing.items()]
    for index, (_key, rig, cabs) in enumerate(landing, start=1):
        if not cabs:
            continue
        # The active object is set exactly as the per-actor import set it -- so the
        # context each build sees is unchanged -- but the animation lands via the
        # explicit armature, so one closure feeds every actor.
        if not _make_active(stage.context, rig):
            raise RuntimeError(
                "The view layer would not make {0} the active object, so this unit's "
                "animation has no skeleton to land on. It is usually a leftover rig in "
                "an excluded collection -- clear the scene and load again.".format(rig.name))
        cross_game_retarget.build_clips_onto_from_closure(
            stage.context, rig, list(cabs), stage.motion)
        yield step_loader.Mark(0.78 + 0.16 * (index / max(len(landing), 1)))


def _raise_stage(stage, manifest):
    """Build the unit's own prefab -- the thing the performers stand on.

    Imported like any other model, out of the closure already read. What it
    contributes is a hierarchy of empties whose names are exactly the segments the
    timeline's binding paths are written in, which is what makes standing the cast
    on it a lookup rather than a guess."""
    from ... import cabmap_panel
    if stage.scenery is None or not manifest["stage"]:
        return
    before = {obj.name for obj in stage.context.scene.objects}
    rows = [{"cab": cab, "name": stage.unit} for cab in manifest["stage"]]
    try:
        cabmap_panel.import_hierarchy_from_closure(
            cast._Reporter(stage.notes), stage.context, stage.context.scene.ruri_cabmap,
            rows, stage.scenery, only_seeded=True,
            # Empties ON and skeleton OFF, both deliberately: the empties ARE the
            # stage (a placement is a transform and nothing else), and the prefab
            # builds its transform tree only when it has no armature -- a stage that
            # happens to embed a character would otherwise take the skinned path and
            # drop all 600 of its placement nodes. The cast is imported separately.
            options=dict(stage.context.scene.ruri_cabmap.as_options(),
                         import_empties=True, import_skeleton=False))
    except Exception as failure:
        stage.notes.append("The unit's own stage did not build: {0}".format(failure))
        return
    stage.props = {obj.name: obj for obj in stage.context.scene.objects
                   if obj.name not in before}


def _anchor_for(stage, binding):
    """The object in the unit's own prefab that a binding path names.

    A binding is a transform path INTO that prefab, so its deepest segment that
    the prefab actually holds is the thing the track drives -- the same
    deepest-first walk the cast resolution does, asked of the scene this time."""
    for segment in reversed([part for part in (binding or "").split("/") if part]):
        found = stage.props.get(segment)
        if found is not None:
            return found
        for name, obj in stage.props.items():
            if name.rsplit(".", 1)[0] == segment:
                return obj
    return None


def _stand_cast(stage, rows):
    """Put each performer where the unit's own prefab puts them.

    Parented rather than copied, so a stage the game animates carries its cast
    with it. A performer whose binding names nothing in the prefab is left where
    it is and said out loud -- standing it at the origin silently is what this
    exists to stop."""
    from mathutils import Matrix
    if not stage.props:
        return
    placed = 0
    for row in rows:
        if not row["model"]:
            continue
        rig = stage.actors.get(_identity(row))
        if rig is None or rig.parent is not None:
            continue
        anchor = _anchor_for(stage, row["target"])
        if anchor is None:
            continue
        rig.parent = anchor
        rig.matrix_parent_inverse = Matrix.Identity(4)
        placed += 1
    stage.stood = placed


def _drives_camera(row):
    """Whether this directive is part of the camera performance -- either a shot
    going live, or motion on the camera itself."""
    return bool(row["camera"]) or row["kind"] == "camera"


def _realize(stage, row):
    found = REALIZERS.get(row["kind"])
    if found is None:
        stage.unknown.add(row["class"] or row["kind"])
        return
    found(stage, row)


def _stage_rows(unit, variant, language):
    table = datasets.story_stage(unit, variant, language)
    names = table.names
    rows = [{name: table.cell(index, name) for name in names} for index in range(len(table))]
    for row in rows:
        for name in ("at", "until", "clipIn", "speed", "blendIn", "blendOut", "rate", "length",
                     "crossFade"):
            row[name] = _real(row.get(name))
        for name in ("order", "branch", "becomes", "reverse", "onTop", "muted", "model",
                     "camera"):
            row[name] = int(_real(row.get(name)))
    return rows


def _real(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _scene_fps(context, rows):
    """The frame rate the timeline itself states, applied to the scene so one
    Blender frame IS one game frame."""
    stated = {row["rate"] for row in rows if _real(row.get("rate")) > 0}
    if not stated:
        return float(context.scene.render.fps)
    context.scene.render.fps = int(round(max(stated)))
    context.scene.render.fps_base = 1.0
    return float(context.scene.render.fps)


# ── the cast ──────────────────────────────────────────────────

# identity -> the armature that IS them, for this session. Keyed by the game's own
# identity rather than by the token a clip happens to spell them with.
_ACTOR_RIGS = {}


def _rig_for(context, loadable):
    """The armature that IS this member, if the scene already holds it."""
    known = bpy.data.objects.get(_ACTOR_RIGS.get(loadable.key, ""))
    if known is not None and known.type == "ARMATURE" and known.name in context.scene.objects:
        return known
    for token in (loadable.key, loadable.label):
        needle = (token or "").lower()
        if not needle:
            continue
        matches = [obj for obj in context.scene.objects
                   if obj.type == "ARMATURE" and needle in obj.name.lower()]
        if len(matches) == 1:
            return matches[0]
    return None


def _adopt(context, loadable, built, before):
    """Which armature the build produced -- observed, not guessed from a name.

    An assembled npc hands its own rig back; a prefab import creates whatever the
    prefab holds, so what appeared during THIS member's build is the answer. Naming
    is what put 361 unbound meshes at the origin once already."""
    if built is not None and getattr(built, "armature", None) not in (None, True):
        rig = built.armature
    else:
        added = [obj for obj in context.scene.objects
                 if obj.type == "ARMATURE" and obj.name not in before]
        rig = added[0] if added else None
    if rig is not None:
        _ACTOR_RIGS[loadable.key] = rig.name
    return rig


def _make_active(context, armature):
    """Point the scene at one rig, and say whether it took.

    The clip build resolves which skeleton to drive from the ACTIVE object, and a
    view layer refuses to activate an object it does not hold -- so an object left
    over in an excluded or unlinked collection activates silently as nothing, and
    the build then reports the scene as having no unambiguous skeleton. Which is a
    true statement about the scene and a useless one about the cause, so this
    returns the fact instead of assuming it."""
    if armature.name not in context.view_layer.objects:
        context.scene.collection.objects.link(armature)
    for obj in context.selected_objects:
        obj.select_set(False)
    armature.select_set(True)
    context.view_layer.objects.active = armature
    return context.view_layer.objects.active is armature


def rig_named(context, token):
    """The armature a token names, when the scene holds exactly one."""
    needle = (token or "").lower()
    if not needle:
        return None
    matches = [obj for obj in context.scene.objects
               if obj.type == "ARMATURE" and needle in obj.name.lower()]
    return matches[0] if len(matches) == 1 else None


def land_clips(context, by_actor):
    """Build clips onto the rigs of the ones they animate. Returns (built, homeless).

    ONE closure for every clip in the batch, then a build per rig -- the same shape
    the whole-unit load has, for the same reason: resolving a closure is what costs,
    and a loop that resolves one per actor pays it once per actor for bundles they
    largely share. A clip whose actor has no rig in the scene lands on whatever is
    active, which is the case the plain browser import was always for."""
    from ... import cabmap_panel, cross_game_retarget
    from ...RuriRipperPyBridge.unity import class_registry
    seeds = list(dict.fromkeys(cab for cabs in by_actor.values() for cab in cabs))
    if not seeds:
        return 0, []
    resolved = cabmap_panel.resolve_import_closure(
        seeds, [class_registry.id_for_name("AnimationClip")])
    built, homeless = 0, []
    for actor, cabs in by_actor.items():
        rig = rig_named(context, actor)
        if rig is None and actor:
            homeless.append(actor)
        if rig is None:
            rig = context.object if getattr(context.object, "type", "") == "ARMATURE" else None
        if rig is None:
            continue
        _make_active(context, rig)
        cross_game_retarget.build_clips_onto_from_closure(context, rig, list(cabs), resolved)
        built += len(cabs)
    return built, homeless


# ── the directives ──────────────────────────────────────────────────────────

@needs("motion", "additive", "camera")
def _needs_clip(row):
    """A motion or a shot is a clip: the timeline names it and the CAB it lives in."""
    return [(CLIP, row["sourceCab"])]


@needs("effect")
def _needs_effect(row):
    """An effect is a prefab the story ignites -- loaded like any other model."""
    return [(SCENERY, row["referenceCab"])]


@realizer("motion", "additive")
def _realize_motion(stage, row):
    target, action = _target_of(stage, row)
    if target is None or action is None:
        stage.unplaced.append(row["source"])
        return
    stage.targets[row["track"]] = target
    _place_strip(target, row, action, stage.fps)
    stage.placed += 1


def _target_of(stage, row):
    """What a motion directive drives, and the action to drive it with.

    A performer with a rig gets the action the importer built for that clip. What a
    cutscene moves besides its cast -- the camera it films through, a prop, a light
    rig -- is a transform track on something the story spawns, so it gets a real
    camera or a stand-in and the clip is read straight onto it.
    """
    rig = stage.actors.get(_identity(row))
    if rig is not None:
        action = _action_named(row["source"])
        if action is not None:
            return rig, action
    if not row["sourceCab"]:
        return None, None
    if _is_camera(row):
        target = stage.camera or _camera_of(stage.context, stage.unit)
        stage.camera = target
    else:
        target = _empty_for(stage.context, "{0}_{1}".format(stage.unit, row["track"]))
    built = _object_action(target, row["sourceCab"], row["source"])
    return target, (built[0] if built is not None else None)


def _is_camera(row):
    """Whether this directive drives the camera the unit films through -- which
    the hook decides, because which token IS the camera is naming knowledge and
    the game writes it two different ways."""
    return bool(row["camera"])


@realizer("camera")
def _realize_camera(stage, row):
    """A shot going live.

    A dialogue films through Cinemachine: several virtual cameras stand around the
    scene and a clip on the camera track says which one is live from when to when.
    The camera the SCENE films through is the game's main camera -- the one thing
    Cinemachine drives -- so a shot is realized as that camera taking the virtual
    one's animation across the shot's own span, which is exactly what the brain
    does at runtime and exactly what makes pressing play cut where the game cuts.
    """
    camera = stage.camera or _camera_of(stage.context, stage.unit)
    stage.camera = camera
    if not row["sourceCab"]:
        stage.shots.append(row)
        return
    built = _object_action(camera, row["sourceCab"], row["source"])
    if built is None:
        # A dialogue does not animate its camera: it stands several virtual cameras
        # in the unit's own prefab and the shot says which one is live. So the clip
        # name is an OBJECT here, and the shot is realized by putting the camera
        # where that object is for the span -- which is what the runtime does.
        if not stage.props:
            stage.pending_cuts.append(row)
            stage.shots.append(row)
            return
        if _cut_to(stage, row):
            stage.shots.append(row)
            stage.placed += 1
            return
        stage.unplaced.append(row["source"])
        return
    _place_strip(camera, row, built[0], stage.fps)
    stage.shots.append(row)
    stage.placed += 1


def _cut_to(stage, row):
    """Put the camera on the virtual camera this shot makes live, for its span.

    Keyed rather than parented: a unit cuts between several of them, and a camera
    can only have one parent. Constant interpolation, because a cut is a cut."""
    vcam = stage.props.get(row["source"]) or _anchor_for(stage, row["source"])
    if vcam is None:
        return False
    camera = stage.camera
    frame = int(round(stage.frame(row["at"])))
    camera.rotation_mode = "QUATERNION"
    world = vcam.matrix_world
    camera.location = world.to_translation()
    camera.rotation_quaternion = world.to_quaternion()
    camera.keyframe_insert("location", frame=frame)
    camera.keyframe_insert("rotation_quaternion", frame=frame)
    animation = camera.animation_data
    for curve in (animation.action.fcurves if animation and animation.action else []):
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT"
    return True


@realizer("show")
def _realize_show(stage, row):
    stage.spans.setdefault(row["track"], []).append((row["at"], row["until"]))


@realizer("effect")
def _realize_effect(stage, row):
    """An effect the story ignites: the timeline names the prefab and when it
    lives, so it is loaded and switched on for exactly that span."""
    if not row["referenceCab"]:
        _realize_marker(stage, row)
        return
    spawned = _spawn_prefab(stage, row)
    if spawned is None:
        _realize_marker(stage, row)
        return
    stage.targets[row["track"]] = spawned
    stage.spans.setdefault(row["track"], []).append((row["at"], row["until"]))


@realizer("line")
def _realize_line(stage, row):
    if not row["text"]:
        return
    stage.lines.append(row)
    _beat(stage, row)


@realizer("click", "hold", "jump", "option")
def _realize_beat(stage, row):
    _beat(stage, row)


@realizer("audio", "voice", "mask", "logo", "morph", "lipsync", "blink")
def _realize_marker(stage, row):
    """Something the story does that a scene cannot literally play back. WHEN it
    happens is part of the performance and belongs on the timeline."""
    label = row["reference"] or row["source"] or row["track"]
    name = "{0}: {1}".format(row["kind"], label)
    stage.context.scene.timeline_markers.new(name[:63], frame=int(round(stage.frame(row["at"]))))
    stage.markers += 1


@realizer("nested")
def _realize_nested(stage, row):
    """A control clip is how the master timeline places a sub-timeline, and the
    hook already folded that offset into every row's own time. Nothing is left to
    do with it, and saying so is why it is not an `unknown`."""
    return


# ── what the player reads ───────────────────────────────────────────────────

def _beat(stage, row):
    stage.beats.append(row)


def _write_script(stage):
    """The beat script, on the scene. Scene data rather than module state so it
    survives save/load and so scrubbing the playhead is all the player needs."""
    script = stage.context.scene.ruri_story_script
    script.unit = stage.unit
    script.beats.clear()
    for row in sorted(stage.beats, key=lambda entry: (entry["at"], entry["order"])):
        beat = script.beats.add()
        beat.kind = row["kind"]
        beat.start = int(round(stage.frame(row["at"])))
        beat.end = int(round(stage.frame(row["until"])))
        beat.speaker = row["who"] or row["speaker"]
        beat.text = row["text"]
        beat.emotion = row["emotion"]
        beat.options = row["options"]
        beat.branch = row["branch"]
        beat.reverse = row["reverse"] > 0
        beat.becomes = row["becomes"]
        beat.fired = False
    script.active = 0


# ── the objects ─────────────────────────────────────────────────────────────

def _camera_of(context, unit):
    """The camera the unit films through, created once. This becomes the scene
    camera, which is what makes the story play with nothing else switched on."""
    name = unit + "_camera"
    existing = bpy.data.objects.get(name)
    if existing is not None and existing.type == "CAMERA":
        if existing.name not in context.scene.objects:
            context.scene.collection.objects.link(existing)
        return existing
    camera = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    camera.rotation_mode = "QUATERNION"
    context.scene.collection.objects.link(camera)
    return camera


def _empty_for(context, name):
    existing = bpy.data.objects.get(name)
    if existing is None:
        existing = bpy.data.objects.new(name, None)
        existing.empty_display_type = "PLAIN_AXES"
        existing.rotation_mode = "QUATERNION"
    if existing.name not in context.scene.objects:
        context.scene.collection.objects.link(existing)
    return existing


def _spawn_prefab(stage, row):
    """The effect prefab, out of the closure ACQUIRE already read.

    It used to run a whole import per effect row -- twenty-nine of them on one real
    unit, twenty-eight of which re-read the same bundle. Its CAB is declared with
    everything else now, so this only builds."""
    from ... import cabmap_panel
    context = stage.context
    if stage.scenery is None:
        return None
    before = {obj.name for obj in context.scene.objects}
    warnings = []
    _ok, imported = cabmap_panel.import_hierarchy_from_closure(
        cast._Reporter(warnings), context, context.scene.ruri_cabmap,
        [{"cab": row["referenceCab"], "name": row["reference"]}], stage.scenery,
        only_seeded=True)
    if not imported:
        return None
    added = [obj for obj in context.scene.objects if obj.name not in before]
    if not added:
        return None
    holder = _empty_for(context, "{0}_{1}".format(row["track"], row["reference"] or "effect"))
    for obj in added:
        if obj.parent is None and obj is not holder:
            obj.parent = holder
    return holder


def _subtitles(stage):
    """One text object per spoken line, parented to the camera and visible exactly
    across its own span.

    One object per line rather than one whose body animates, because Blender cannot
    keyframe a text body -- and one per line is the more useful shape anyway: each
    carries its own in and out frames, so scrubbing shows the line that is actually
    being said.
    """
    if not stage.lines or stage.camera is None:
        return 0
    font = _cjk_font()
    holder = bpy.data.collections.get(stage.unit + "_subtitles")
    if holder is None:
        holder = bpy.data.collections.new(stage.unit + "_subtitles")
        stage.context.scene.collection.children.link(holder)
    for existing in list(holder.objects):
        bpy.data.objects.remove(existing, do_unlink=True)

    built = 0
    for index, row in enumerate(stage.lines):
        curve = bpy.data.curves.new("{0}_line_{1:03d}".format(stage.unit, index), "FONT")
        curve.body = _spoken(row)
        curve.align_x = "CENTER"
        curve.align_y = "CENTER"
        curve.size = SUBTITLE_SIZE
        if font is not None:
            curve.font = font
        text = bpy.data.objects.new(curve.name, curve)
        holder.objects.link(text)
        text.parent = stage.camera
        text.location = (0.0, SUBTITLE_DROP, -SUBTITLE_DISTANCE)
        text.rotation_euler = (0.0, 0.0, 0.0)
        if row["branch"]:
            text[BRANCH_KEY] = row["branch"]
        _keyframe_visibility(text, [(row["at"], row["until"])], stage.fps)
        built += 1
    return built


def _spoken(row):
    """One line as a reader reads it: who says it, then what they say, wrapped so a
    long line does not run off both sides of the frame."""
    body = "\n".join(_wrapped(row["text"], SUBTITLE_WIDTH))
    speaker = row["who"] or row["speaker"]
    return "{0}\n{1}".format(speaker, body) if speaker else body


def _wrapped(text, width):
    remaining = (text or "").strip()
    while remaining:
        if len(remaining) <= width:
            yield remaining
            return
        cut = remaining.rfind(" ", 0, width + 1)
        cut = cut if cut > width // 2 else width
        yield remaining[:cut].rstrip()
        remaining = remaining[cut:].lstrip()


_FONT = []


def _cjk_font():
    """A face that can actually draw the story. Blender ships none that covers CJK,
    so the first one the host has wins; with none, the text still builds."""
    if _FONT:
        return _FONT[0]
    for path in CJK_FONTS:
        if not os.path.isfile(path):
            continue
        try:
            _FONT.append(bpy.data.fonts.load(path, check_existing=True))
        except RuntimeError:
            continue
        return _FONT[0]
    return None


# What a built thing belongs to, written onto it so the gate can find it again
# without keeping a second index in module state.
BRANCH_KEY = "ruri_story_branch"


def gate_branches(context, option, last_option):
    """Turn on exactly the clips the chosen option enables, and turn the rest off.

    The rule is the game's own: a clip is enabled when its branch is 0 (it belongs
    to no branch at all), or when its branch is the option now in force, or the one
    before it. Nothing here decides which clips those are -- the timeline tagged
    them and the stage carried the tag through.
    """
    switched = 0
    for obj in context.scene.objects:
        branch = obj.get(BRANCH_KEY)
        if branch:
            obj.hide_viewport = obj.hide_render = branch not in (option, last_option)
            switched += 1
        animation = obj.animation_data
        for track in (animation.nla_tracks if animation else []):
            for strip in track.strips:
                branch = strip.action.get(BRANCH_KEY) if strip.action else None
                if not branch:
                    continue
                strip.mute = branch not in (option, last_option)
                switched += 1
    return switched


def _keyframe_visibility(target, spans, fps):
    """Visible exactly across these spans. Constant interpolation, because
    visibility is a switch and a state smoothed between two is one nobody shows."""
    if target is None or not spans:
        return
    for attribute in ("hide_viewport", "hide_render"):
        setattr(target, attribute, True)
        target.keyframe_insert(attribute, frame=0)
        for start, stop in spans:
            setattr(target, attribute, False)
            target.keyframe_insert(attribute, frame=max(0, int(round(start * fps))))
            setattr(target, attribute, True)
            target.keyframe_insert(attribute, frame=int(round(stop * fps)) + 1)
    animation = target.animation_data
    for curve in (animation.action.fcurves if animation and animation.action else []):
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT"


# ── strips ──────────────────────────────────────────────────────────────────

# Built object actions, by (cab, clip). A timeline reuses takes -- the same clip
# is routinely placed twice -- and each build is a bridge import, so one build
# per clip is the whole budget.
_OBJECT_ACTIONS = {}


def _action_named(clip):
    if not clip:
        return None
    exact = bpy.data.actions.get(clip)
    if exact is not None:
        return exact
    numbered = [action for action in bpy.data.actions
                if action.name.rsplit(".", 1)[0] == clip]
    return max(numbered, key=lambda action: action.name) if numbered else None


def _action_rate(action, row, fps):
    """How many action frames one second of this clip is. Read off the STAMP the
    importer put on the action, never off the clip asset's own sample rate: an ACL
    clip routinely decodes at a rate its own field does not carry."""
    from ... import animation_builder
    stamped = action.get(animation_builder.SAMPLE_RATE_KEY)
    if stamped:
        return float(stamped)
    frames = action.frame_range[1] - action.frame_range[0]
    if row["length"] > 0.0 and frames > 0.0:
        return frames / row["length"]
    return row["rate"] if row["rate"] > 0 else fps


def _free_track(animation, name, start, end):
    """The NLA track this clip goes on. Timeline lets two clips of one track
    overlap (that is how it cross-blends); Blender does not, so an overlap becomes
    another layer of the same track rather than a dropped or moved clip."""
    layer = 0
    while True:
        layered = name if layer == 0 else "{0}.{1}".format(name, layer + 1)
        track = next((entry for entry in animation.nla_tracks if entry.name == layered), None)
        if track is None:
            track = animation.nla_tracks.new()
            track.name = layered
            return track
        if all(strip.frame_end <= start + 1e-3 or strip.frame_start >= end - 1e-3
               for strip in track.strips):
            return track
        layer += 1


def _place_strip(rig, row, action, fps):
    """Put one action where the timeline puts it. Timeline states a clip in SECONDS
    against its own sample rate; a strip states a frame span plus the action frames
    it consumes. The two are the same statement in different units."""
    animation = rig.animation_data or rig.animation_data_create()
    start = row["at"] * fps
    span = max((row["until"] - row["at"]) * fps, 1.0)
    track = _free_track(animation, row["track"] or "Timeline", start, start + span)
    parking = max(int(round(start)),
                  int(max((strip.frame_end for strip in track.strips), default=0.0)) + 1)
    strip = track.strips.new(action.name, parking, action)
    sample = _action_rate(action, row, fps)
    consumed = max((row["until"] - row["at"]) * (row["speed"] or 1.0) * sample, 1.0 / sample)
    strip.action_frame_start = row["clipIn"] * sample
    strip.action_frame_end = strip.action_frame_start + consumed
    strip.scale = span / consumed
    strip.frame_start_ui = start
    strip.blend_in = min(row["blendIn"] * fps, span)
    strip.blend_out = min(row["blendOut"] * fps, span)
    strip.extrapolation = "NOTHING"
    # A clip the timeline tags with a branch plays only while that branch is the
    # chosen option, and nothing is chosen until the reader chooses.
    strip.mute = row["muted"] > 0 or row["branch"] != 0
    if row["branch"]:
        action[BRANCH_KEY] = row["branch"]
    return strip


def _object_action(camera, cab, clip_name):
    """Build the object action one camera clip states.

    The clip is a transform track like any other, so it is read through the same
    curve path every clip takes; what is camera-specific is only the two
    conventions at the end -- Unity's basis (converted by the shared reflection,
    exactly as a top-level import object is) and Unity's camera facing +Z with up
    +Y against Blender's -Z with up +Y, which is one fixed local quarter turn."""
    import numpy
    from mathutils import Matrix, Quaternion, Vector

    from ... import animation_builder, coordinate
    from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry

    # A timeline reuses takes -- the same clip is routinely placed twice -- and
    # each build is a bridge import, so one build per clip is the whole budget.
    cached = _OBJECT_ACTIONS.get((cab, clip_name))
    if cached is not None and cached[0].users >= 0:
        return cached

    curves = _curves_of(cab, clip_name)
    if curves is None:
        return None

    rate = curves.sample_rate or 60.0
    frames = max(1, int(round(curves.max_time() * rate)) + 1)
    times = numpy.arange(frames, dtype=numpy.float64) / rate
    position = curves.positions[0].sample(times) if curves.positions else None
    rotation = curves.rotations[0].sample(times) if curves.rotations else None

    action = bpy.data.actions.new(curves.name)
    action.use_fake_user = True
    action[animation_builder.SAMPLE_RATE_KEY] = float(rate)
    fcurves, slot = animation_builder._prepare_channels(action, action.name, "OBJECT")
    facing = Matrix.Rotation(numpy.pi / 2.0, 4, "X")
    locations = numpy.zeros((frames, 3), dtype=numpy.float64)
    quaternions = numpy.zeros((frames, 4), dtype=numpy.float64)
    for frame in range(frames):
        translation = Matrix.Translation(Vector(position[frame]) if position is not None else Vector())
        turn = Quaternion((rotation[frame][3], rotation[frame][0], rotation[frame][1],
                           rotation[frame][2])).to_matrix().to_4x4() if rotation is not None \
            else Matrix.Identity(4)
        world = coordinate.convert_root_matrix(translation @ turn) @ facing
        locations[frame] = world.to_translation()
        turned = world.to_quaternion()
        quaternions[frame] = (turned.w, turned.x, turned.y, turned.z)

    keys = numpy.arange(frames, dtype=numpy.float64)
    if position is not None or rotation is not None:
        for index in range(3):
            _write_curve(fcurves.new("location", index=index), keys, locations[:, index])
        for index in range(4):
            _write_curve(fcurves.new("rotation_quaternion", index=index), keys, quaternions[:, index])
    # A clip that animates no transform at all animates VALUES -- a light's
    # intensity, a material's colour. Those land as animated custom properties on
    # the stand-in, so the curve the game plays is in the scene and readable
    # rather than dropped for having nowhere obvious to go.
    written = set()
    for channel in getattr(curves, "floats", []) or []:
        # The WHOLE path is the key: a clip animates several values whose leaf
        # names collide ("...position.x" and "...scale.x" are both "x"), and two
        # curves cannot share one custom property.
        name = "_".join(part for part in (getattr(channel, "path", ""),
                                          getattr(channel, "attribute", "")) if part)
        key = re.sub(r"[^0-9A-Za-z_]", "_", name) or "value"
        if key in written:
            continue
        written.add(key)
        samples = channel.sample(times)
        values = samples[:, 0] if getattr(samples, "ndim", 1) > 1 else samples
        camera[key] = float(values[0])
        _write_curve(fcurves.new('["{0}"]'.format(key), index=0), keys, values)
    if not len(fcurves):
        bpy.data.actions.remove(action)
        return None
    _OBJECT_ACTIONS[(cab, clip_name)] = (action, slot)
    return action, slot


# The curves of every clip the unit reads straight off the bridge, by (cab, name).
# Filled by one crossing for the whole unit; a miss falls to a crossing for that
# one cab, which is what a clip the prefetch could not see needs.
_CURVES = {}


def _hold_curves(stage, cabs):
    """Keep the curves of every clip the unit reads DIRECTLY -- the shots, and
    whatever motion lands on a stand-in rather than a rig.

    Out of the closure ACQUIRE already read: this used to be a crossing of its own,
    and before that a crossing per clip, which on a unit with seventy shots was
    seventy resolves of the same handful of bundles."""
    from ...RuriRipperPyBridge.unity import bridge_asset_db
    if stage.motion is None or not cabs:
        return 0
    database = stage.motion["db"]
    assert isinstance(database, bridge_asset_db.BridgeAssetDatabase)
    held = 0
    for cab in cabs:
        _CURVES[(cab, None)] = True
        for guid in stage.motion["clips_by_cab"].get(cab.lower(), []):
            found = database.clip_curves(guid)
            if found is not None:
                _CURVES.setdefault((cab, found.name), found)
                _CURVES.setdefault((cab, ""), found)
                held += 1
    return held


def _curves_of(cab, clip_name):
    """This clip's curves, out of what the unit read. A clip the manifest did not
    declare is a clip nothing asked for -- reported as unplaced, never fetched
    behind the build's back."""
    return _CURVES.get((cab, clip_name)) or _CURVES.get((cab, ""))


def _write_curve(curve, frames, values):
    import numpy
    curve.keyframe_points.add(count=len(frames))
    flat = numpy.empty(len(frames) * 2, dtype=numpy.float64)
    flat[0::2] = frames
    flat[1::2] = values
    curve.keyframe_points.foreach_set("co", flat)
    curve.keyframe_points.foreach_set("interpolation", [1] * len(frames))
    curve.update()


# ── finishing ───────────────────────────────────────────────────────────────

def _finish(stage):
    """Everything that can only be said once the whole stream has been walked."""
    from ... import animation_builder

    for track, windows in stage.spans.items():
        _keyframe_visibility(stage.targets.get(track), windows, stage.fps)
    stage.subtitles = _subtitles(stage)
    _write_script(stage)
    if stage.camera is not None:
        # The scene was pointed at this camera before the cast came in, because the
        # shells are built against it. What is left is to LOOK through it, which is
        # the difference between a built performance and one that is already framed
        # the way the game opens it.
        stage.context.scene.camera = stage.camera
        stage.looking = _look_through(stage.context)
    if stage.placed or stage.lines:
        animation_builder.set_frame_range(stage.context.scene, 0.0, stage.end)
    stage.context.scene.frame_set(int(stage.context.scene.frame_start))


def _look_through(context):
    """Put every 3D view into the scene camera.

    This is the "already in the camera" half of pressing play: the game opens a
    cutscene in its own camera, and a viewport still on the user's orbit shows the
    performance from the side. Returns how many views were switched, which is 0 in
    a window-less session and is reported rather than assumed."""
    switched = 0
    for area in getattr(context.screen, "areas", ()) if context.screen else ():
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type == "VIEW_3D":
                space.region_3d.view_perspective = "CAMERA"
                switched += 1
    return switched


def _load_scene(stage, mode):
    """The place the unit plays in, when asked for.

    Off by default and deliberately so: a landmark is thousands of bundles and tens
    of gigabytes, and the performance plays perfectly without it. When it IS asked
    for, the cutscene's own camera says how much is worth bringing -- the story can
    only ever show the part of the level that camera travels through, so `window`
    imports exactly that rectangle instead of the whole place.
    """
    from . import scene_state
    level = _level_of(stage.unit)
    if not level:
        stage.unresolved.append("(no level stated for this unit)")
        return
    place = _place_of(level)
    if place is None:
        stage.unresolved.append("(no world rectangle for " + level + ")")
        return
    rect = place["rect"]
    if mode == SCENE_WINDOW:
        travelled = _camera_rect(stage)
        if travelled is not None:
            rect = _clamped(travelled, rect)
    scene_state.discover_placements(place["scene"], rect, "", True)
    if len(scene_state.TABLE or []) == 0:
        stage.unresolved.append("(the level places nothing in that window)")
        return
    scene_state.resolve_cabs(cabmap_state.BRIDGE)
    from . import scene_importer
    scene_importer.import_scene_window(stage.context, cabmap_state.BRIDGE)


def _level_of(unit):
    """The level the game's own level scripts play this unit in."""
    try:
        table = datasets.story_units(language="")
    except Exception:
        return ""
    for index in range(len(table)):
        if table.cell(index, "unit") == unit:
            return table.cell(index, "level")
    return ""


def _place_of(level):
    from . import scene_state
    for rows in (scene_state.LANDMARKS or {}).values():
        for row in rows:
            if row["id"] == level:
                return dict(row, scene=_scene_of(level))
    return None


def _scene_of(level):
    from . import scene_state
    for scene, rows in (scene_state.LANDMARKS or {}).items():
        if any(row["id"] == level for row in rows):
            return scene
    return level


def _camera_rect(stage):
    """The world rectangle the camera actually travels through.

    The camera was built through the add-on's own basis conversion, so coming back
    to the world the level is placed in goes through that same conversion inverted
    -- never through a sign flip written out by hand here.
    """
    from mathutils import Vector

    from ... import coordinate
    if stage.camera is None or stage.camera.animation_data is None:
        return None
    # The camera is a SEQUENCE of shots, so its motion is on strips rather than on
    # one active action -- reading only the active action would read nothing, which
    # is the whole reason the window came out empty.
    axes = {}
    for track in stage.camera.animation_data.nla_tracks:
        for strip in track.strips:
            if strip.action is None:
                continue
            for curve in strip.action.fcurves:
                if curve.data_path != "location":
                    continue
                values = [point.co[1] for point in curve.keyframe_points]
                if not values:
                    continue
                low, high = min(values), max(values)
                seen = axes.get(curve.array_index)
                axes[curve.array_index] = (min(low, seen[0]), max(high, seen[1]))                     if seen else (low, high)
    if not {0, 1, 2} <= set(axes):
        return None
    back = coordinate.conversion_matrix().inverted()
    corners = []
    for x in axes[0]:
        for y in axes[1]:
            for z in axes[2]:
                world = back @ Vector((x, y, z))
                corners.append((world.x, world.z))
    return (min(part[0] for part in corners), min(part[1] for part in corners),
            max(part[0] for part in corners), max(part[1] for part in corners))


def _clamped(travelled, place):
    """The camera's rectangle, grown to what a shot can actually see and kept inside
    the level the game gives. A camera sees past where it stands, so the window is
    padded rather than cropped to the path itself."""
    padding = 64.0
    return (max(place[0], travelled[0] - padding), max(place[1], travelled[1] - padding),
            min(place[2], travelled[2] + padding), min(place[3], travelled[3] + padding))


def forget():
    """Drop what one session cached. The rigs, the actions and the character join
    all belong to a loaded install; unloading one has to forget all three together
    or the next install inherits the last one's answers."""
    _ACTOR_RIGS.clear()
    cast.forget()
    _OBJECT_ACTIONS.clear()
    _CURVES.clear()
    _FONT.clear()


def summary(stage):
    parts = ["{0}: {1} clip(s) on {2} actor(s) at {3} fps".format(
        stage.unit, stage.placed, len(stage.actors), int(stage.fps))]
    if getattr(stage, "subtitles", 0):
        parts.append("{0} subtitle(s)".format(stage.subtitles))
    if stage.camera is not None:
        parts.append("through {0}{1}".format(
            stage.camera.name,
            "" if stage.looking else " (no 3D view to look through it)"))
    if stage.beats:
        parts.append("{0} beat(s)".format(len(stage.beats)))
    if stage.stood:
        parts.append("{0} performer(s) stood on the unit's own stage".format(stage.stood))
    if stage.spans:
        parts.append("{0} visibility window(s)".format(len(stage.spans)))
    if stage.markers:
        parts.append("{0} marker(s)".format(stage.markers))
    if stage.unplaced:
        parts.append("{0} clip(s) had nothing to drive".format(len(stage.unplaced)))
    if stage.notes:
        parts.append(stage.notes[0] if len(stage.notes) == 1
                     else "{0} model note(s), first: {1}".format(len(stage.notes),
                                                                 stage.notes[0]))
    if stage.unresolved:
        parts.append("no model for " + ", ".join(stage.unresolved[:4]))
    if stage.unknown:
        parts.append("no realizer yet for " + ", ".join(sorted(stage.unknown)[:4]))
    return " · ".join(parts)
