"""Put one story unit on stage: walk the game's own directive stream and build it.

The hook reads the unit's Timeline and hands back a STAGE -- rows that say what to
do, in seconds, against names (``endfield.story.stage``). Nothing in that stream is
Unity-shaped, so nothing here has to know what an ``ActivationTrack`` is; it knows
what ``show`` means, and there is exactly one function that means it.

That is the whole architecture: a directive kind is a key of the ``REALIZERS`` point
and a function under ``@realizer``. Supporting something the game adds later is one new
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
from bpy_extras import anim_utils

from ....Host.Blender import nla, rig_identity, step_loader
from ....Kernel import extensions
from ....Kernel import statement as kernel_statement
from ....Kernel.app import loading
from ....Kernel.bridge import cabmap_state
from .. import datasets

# What a directive means, by its own word, and what it needs before it can mean
# it. A new kind is one function under @realizer, plus -- only if it loads
# something -- one under @needs.
REALIZERS = extensions.point("story directive realizers",
                             "Directive kind -> the one function that means it on stage.")
NEEDS = extensions.point("story directive needs",
                         "Directive kind -> what it needs LOADED, as CAB names, from its row alone.")

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

# Why a CAB is wanted: a clip is read for its curves. A model is not declared here at
# all -- it is a seed the stage table already states, and it loads as one.
CLIP = "clip"

# The scene the story plays in is enormous and the story does not need it to play.
SCENE_NONE = "none"
SCENE_WINDOW = "window"
SCENE_FULL = "full"


def realizer(*kinds):
    """Register the one function that means a directive."""
    def register(function):
        for kind in kinds:
            REALIZERS.add(function, key=kind)
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
            NEEDS.add(function, key=kind)
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
        # Vertical field of view per virtual camera, in radians. Part of the state a
        # shot cuts to, and different on every one of them.
        self.lenses = {}
        # Shots whose camera is an object in that stage: they cannot be realized until
        # the stage is up, and the stage cannot be built until the cast closure is read.
        self.pending_cuts = []
        self.unknown = set()
        # The options every model of this unit is loaded with -- the browser's own.
        self.options = {}
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

    # MANIFEST: who is in it and which clips it plays, off the stage table alone.
    stage.options = context.scene.ruri_cabmap.as_options()
    manifest = _manifest(rows)
    stage.cast = manifest["cast"]
    stage.unresolved.extend(manifest["unresolved"])

    # The curves the unit reads straight off a clip -- the shots first, because the
    # shots and the scene camera come first.
    yield step_loader.Read(lambda: _hold_curves(manifest["direct"], stage.options), 0.45)
    for row in rows:
        if _drives_camera(row):
            _realize(stage, row)
    if stage.camera is not None:
        context.scene.camera = stage.camera
        context.scene.frame_set(int(round(min(
            (stage.frame(row["at"]) for row in rows if _drives_camera(row)), default=0.0))))

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

def _manifest(rows):
    """What this unit needs, as a value: who is in it, the seed each of them loads from, and
    which clips land on whom -- all of it stated by the stage table, so nothing is resolved
    here and nothing is read."""
    members = {}
    unresolved = []
    for row in rows:
        if not row["model"]:
            continue
        key = _identity(row)
        if not key or key in members or key in unresolved:
            continue
        if row["seed"]:
            members[key] = {"key": key, "label": row["performer"] or row["actor"],
                            "seed": row["seed"]}
        else:
            unresolved.append(key)
    direct = []
    by_actor = {}
    for row in rows:
        declare = NEEDS.get(row["kind"])
        if declare is None:
            continue
        for _role, cab in declare(row):
            if not cab:
                continue
            key = _identity(row)
            if key and key in members:
                # Whose clip it is, so the animation lands on the rig that IS them.
                by_actor.setdefault(key, []).append(cab)
            else:
                # A clip driving something with no rig -- the camera, a prop, a
                # performer the game ships no model for -- is read as curves onto a
                # stand-in instead.
                direct.append(cab)
    return {"cast": members,
            "unresolved": unresolved,
            "stage": next((row["set"] for row in rows if row["set"]), ""),
            "direct": list(dict.fromkeys(direct)),
            "by_actor": {key: list(dict.fromkeys(cabs)) for key, cabs in by_actor.items()}}


def _identity(row):
    """The game's own identity for a performer -- the character it IS, the npc
    template its model is assembled from, else the token the story files it under.

    Never the display token when an identity exists: one person is written several
    ways across a unit (``gentleman_hsteacher_10`` on its clip, ``npc_gentleman_
    hsteacher_10`` on its binding), and keying on the writing makes one person two
    performers, each loading the same model onto a rig the other cannot use."""
    return row["character"] or row["template"] or row["actor"] or ""


def _cast_steps(stage, manifest):
    """Put the cast on stage, one load per performer, and land each performer's clips on
    the rig that IS them."""
    for index, member in enumerate(stage.cast.values(), start=1):
        rig = _rig_for(stage.context, member)
        if rig is None:
            before = {obj.name for obj in stage.context.scene.objects
                      if obj.type == "ARMATURE"}
            built = loading.load(stage.context, [member["seed"]], stage.options)
            stage.notes.extend(built.warnings)
            rig = _adopt(stage.context, member, built, before)
        if rig is None:
            stage.unresolved.append(member["label"])
            continue
        stage.actors[member["key"]] = rig
        yield step_loader.Mark(0.62 + 0.16 * (index / max(len(stage.cast), 1)))

    landing = [(stage.actors[key], cabs) for key, cabs in manifest["by_actor"].items()
               if key in stage.actors]
    for index, (rig, cabs) in enumerate(landing, start=1):
        played, lines = loading.perform(stage.context, cabs, rig=rig, options=stage.options)
        if not played:
            stage.notes.extend(lines[:1])
        yield step_loader.Mark(0.78 + 0.16 * (index / max(len(landing), 1)))


def _raise_stage(stage, manifest):
    """Build the unit's own set -- the thing the performers stand on.

    Loaded like any other model, from the set seed the stage table states. What it
    contributes is a hierarchy of empties whose names are exactly the segments the
    timeline's binding paths are written in, which is what makes standing the cast on
    it a lookup rather than a guess -- so its empties are kept -- and the virtual
    cameras the shots cut between, built with the lens each states."""
    if not manifest["stage"]:
        return
    before = {obj.name for obj in stage.context.scene.objects}
    built = loading.load(stage.context, [manifest["stage"]],
                         dict(stage.options, import_empties=True))
    if not built.imported:
        stage.notes.append("The unit's own stage did not build: " + "; ".join(built.warnings[:2]))
        return
    stage.props = {obj.name: obj for obj in stage.context.scene.objects
                   if obj.name not in before}
    stage.lenses = _lenses_of(stage)


def _lenses_of(stage):
    """{object name: vertical field of view in radians} for the virtual cameras the unit's
    own set declares -- read off the cameras it was built with, which carry the lens each
    virtual camera states."""
    return {name: obj.data.angle_y for name, obj in stage.props.items()
            if obj.type == "CAMERA"}


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


def _rig_for(context, member):
    """The armature that IS this member, if the scene already holds it."""
    known = bpy.data.objects.get(_ACTOR_RIGS.get(member["key"], ""))
    if known is not None and known.type == "ARMATURE" and known.name in context.scene.objects:
        return known
    for token in (member["key"], member["label"]):
        needle = (token or "").lower()
        if not needle:
            continue
        matches = [obj for obj in context.scene.objects
                   if obj.type == "ARMATURE" and needle in obj.name.lower()]
        if len(matches) == 1:
            return matches[0]
    return None


def _adopt(context, member, built, before):
    """Which armature the load produced -- the rig the host states it built, else what
    appeared during THIS member's load. Naming is what put 361 unbound meshes at the origin
    once already."""
    rig = built.rig if built is not None else None
    if rig is None:
        added = [obj for obj in context.scene.objects
                 if obj.type == "ARMATURE" and obj.name not in before]
        rig = added[0] if added else None
    if rig is not None:
        _ACTOR_RIGS[member["key"]] = rig.name
    return rig


def rig_named(context, token):
    """The armature a token names, when the scene holds exactly one."""
    needle = (token or "").lower()
    if not needle:
        return None
    matches = [obj for obj in context.scene.objects
               if obj.type == "ARMATURE" and needle in obj.name.lower()]
    return matches[0] if len(matches) == 1 else None


def land_clips(context, by_actor):
    """Play clips onto the rigs of the ones they animate. Returns (built, homeless). A clip
    whose actor has no rig in the scene lands on whatever is active, which is the case the
    plain browser import was always for."""
    options = context.scene.ruri_cabmap.as_options()
    built, homeless = 0, []
    for actor, cabs in by_actor.items():
        rig = rig_named(context, actor)
        if rig is None and actor:
            homeless.append(actor)
        if rig is None:
            rig = context.object if getattr(context.object, "type", "") == "ARMATURE" else None
        if rig is None:
            continue
        played, _lines = loading.perform(context, list(cabs), rig=rig, options=options)
        built += played
    return built, homeless


# ── the directives ──────────────────────────────────────────────────────────

@needs("motion", "additive", "camera", "morph")
def _needs_clip(row):
    """A motion, a shot, or a FACE is a clip: the timeline names it and the CAB it
    lives in.

    An EXPRESSION is a clip like any other here -- the game files a dialogue's expression,
    lipsync and blink under morphanim/ as plain AnimationClips named exactly as the
    directive names them, not as the SkeletalMorph assets the Character tab reads.
    So they need no reader of their own; they need to be marked.

    Lipsync and auto-blink are NOT clips and are not marked: their directive names a
    config or the object the runtime toggles, not an animation, so they stay what
    they honestly are -- a mark on the timeline saying when the game switched them."""
    return [(CLIP, row["sourceCab"])]


@realizer("motion", "additive", "morph")
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
    _key_lens(stage, row["source"], int(round(stage.frame(row["at"]))))
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
    # The stage was parented into place moments ago and the view layer has not
    # evaluated it yet; matrix_world would still be the pre-placement value.
    stage.context.view_layer.update()
    # No turn: the importer already brought this transform into Blender's basis, and
    # measuring every shot says so -- the placed matrix used AS IS aims at the cast on
    # 27 of 40 shots while every rotated variant aims at it on none. What made it look
    # down was the stale matrix above, not a missing convention.
    world = vcam.matrix_world
    camera.location = world.to_translation()
    camera.rotation_quaternion = world.to_quaternion()
    camera.keyframe_insert("location", frame=frame)
    camera.keyframe_insert("rotation_quaternion", frame=frame)
    _key_lens(stage, vcam.name, frame)
    return True


def _key_lens(stage, vcam_name, frame):
    """Give the camera the field of view this shot was composed at.

    Every shot, not only the ones realized as a cut: a shot that resolves to a real
    clip moves the camera through its own animation and still has to be seen through
    that virtual camera's lens. Constant interpolation, because a lens change at a
    cut is a cut."""
    camera = stage.camera
    angle = stage.lenses.get(vcam_name)
    if camera is not None and angle:
        camera.data.sensor_fit = "VERTICAL"
        camera.data.angle_y = angle
        # The lens belongs to the camera DATA and needs an action of its own. Keying it
        # without one lands the channel in whatever action is already reachable -- here
        # the OBJECT's, which holds location and rotation -- and the lens curve is then
        # never created at all: measured, the data action held only location and
        # rotation_quaternion, and every shot showed one static focal length.
        if camera.data.animation_data is None:
            camera.data.animation_data_create()
        own = camera.data.animation_data
        shared = camera.animation_data.action if camera.animation_data else None
        if own.action is None or own.action is shared:
            own.action = bpy.data.actions.new(camera.name + "_lens")
        camera.data.keyframe_insert("lens", frame=frame)
        for curve in _curves(own.action, own.action_slot):
            for point in curve.keyframe_points:
                point.interpolation = "CONSTANT"
    animation = camera.animation_data
    for curve in (_curves(animation.action, animation.action_slot) if animation else ()):
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


@realizer("audio", "voice", "mask", "logo", "lipsync", "blink")
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
    """The effect prefab the story ignites, loaded like any other model from its archive.
    Repeated ignitions of one effect ask for the same seed, which the reader answers from
    the statement it already made."""
    context = stage.context
    before = {obj.name for obj in context.scene.objects}
    built = loading.load(context, [row["referenceCab"]], stage.options)
    if not built.imported:
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


# The font PATH that worked, never the font itself. A datablock cached across
# builds is a StructRNA that clearing the scene or reloading the file removes, and
# the next build then dies on a reference to something that no longer exists
# ("StructRNA of type VectorFont has been removed"). A path cannot go stale.
_FONT = []


def _cjk_font():
    """A face that can actually draw the story. Blender ships none that covers CJK,
    so the first one the host has wins; with none, the text still builds.

    Asked of bpy every time rather than remembered: ``check_existing`` hands back
    the font already in the file when there is one, so this costs a name lookup and
    can never hold a datablock that has since been freed."""
    for path in (_FONT or CJK_FONTS):
        if not os.path.isfile(path):
            continue
        try:
            font = bpy.data.fonts.load(path, check_existing=True)
        except RuntimeError:
            continue
        if not _FONT:
            _FONT.append(path)
        return font
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
    for curve in (_curves(animation.action, animation.action_slot) if animation else ()):
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT"


def _curves(action, slot):
    """The curves an action plays for one slot: the layered action keeps them per slot, and a slot
    nothing was keyed for has none."""
    channelbag = anim_utils.action_get_channelbag_for_slot(action, slot) if action is not None else None
    return channelbag.fcurves if channelbag is not None else ()


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
    from ....Host.Blender import animation_builder
    stamped = action.get(animation_builder.SAMPLE_RATE_KEY)
    if stamped:
        return float(stamped)
    frames = action.frame_range[1] - action.frame_range[0]
    if row["length"] > 0.0 and frames > 0.0:
        return frames / row["length"]
    return row["rate"] if row["rate"] > 0 else fps


def _place_strip(rig, row, action, fps):
    """Put one action where the timeline puts it. Timeline states a clip in SECONDS
    against its own sample rate; a strip states a frame span plus the action frames
    it consumes. The two are the same statement in different units."""
    animation = rig.animation_data or rig.animation_data_create()
    start = row["at"] * fps
    span = max((row["until"] - row["at"]) * fps, 1.0)
    track = nla.free_track(animation, row["track"] or "Timeline", start, start + span)
    sample = _action_rate(action, row, fps)
    consumed = max((row["until"] - row["at"]) * (row["speed"] or 1.0) * sample, 1.0 / sample)
    source_start = row["clipIn"] * sample
    slot = action.slots[0] if len(action.slots) else None
    strip = nla.place_strip(track, action, slot, start, start + span,
                            source_start, source_start + consumed,
                            min(row["blendIn"] * fps, span), min(row["blendOut"] * fps, span))
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

    from ....Host.Blender import animation_builder, coordinate

    # A timeline reuses takes -- the same clip is routinely placed twice -- and
    # each build is a bridge import, so one build per clip is the whole budget.
    # A cached action is a datablock, and a scene clear or a file reload frees it --
    # after which touching it at all raises rather than answering. Asking IS the
    # test, so the stale entry is dropped and the clip rebuilt.
    cached = _OBJECT_ACTIONS.get((cab, clip_name))
    if cached is not None:
        try:
            cached[0].users
        except ReferenceError:
            _OBJECT_ACTIONS.pop((cab, clip_name), None)
            cached = None
    if cached is not None:
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
    fcurves, slot = animation_builder.prepare_channels(action, action.name, "OBJECT")
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
    # ONLY the channels the clip actually carries. A clip with position and no
    # rotation is normal -- a recorded camera dolly is exactly that -- and writing
    # the missing half anyway keys an all-zero quaternion, which is not "no
    # rotation" but a degenerate one: it destroys whatever the object was aimed at.
    if position is not None:
        for index in range(3):
            _write_curve(fcurves.new("location", index=index), keys, locations[:, index])
    if rotation is not None:
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


class _ObjectCurves:
    """One stated clip, as the object it drives reads it: its own transform curves, and every
    value curve under the path it animates."""

    __slots__ = ("name", "sample_rate", "positions", "rotations", "floats", "_duration")

    def __init__(self, clip):
        self.name = clip.name
        self.sample_rate = clip.sample_rate
        own = [channel for channel in clip.channels if not channel.path]
        self.positions = [channel for channel in own if channel.kind == "pos"]
        self.rotations = [channel for channel in own if channel.kind == "rot"]
        self.floats = [channel for channel in clip.channels if channel.kind == "float"]
        self._duration = clip.duration

    def max_time(self):
        return self._duration


def _hold_curves(cabs, options):
    """Keep the curves of every clip the unit reads DIRECTLY -- the shots, and whatever
    motion lands on a stand-in rather than a rig -- as ONE statement of their archives, in
    the engine's own basis, which is what a stand-in's transform is converted from.

    TEMPORARY, root cause pending: each is read against the stand-in's own hierarchy --
    itself -- which is not the object the game binds. Only the curves on the animated object
    itself and its float curves are kept, which are the ones a stand-in can carry; the real
    target is the set's bound object, read with its own hierarchy and the avatar its Animator
    was built with, which the stage does not build yet."""
    if not cabs:
        return 0
    held = 0
    with loading.statement(cabs, options) as stated:
        clips = stated.in_basis(kernel_statement.UNITY).clips(
            paths=(rig_identity.ANIMATOR_ROOT_PATH,), avatar="")
    for clip in clips:
        curves = _ObjectCurves(clip)
        _CURVES[(clip.archive, None)] = True
        _CURVES.setdefault((clip.archive, clip.name), curves)
        _CURVES.setdefault((clip.archive, ""), curves)
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
    from ....Host.Blender import animation_builder

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
    from .. import scene_state
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
    seed = scene_state.seed()
    if not seed:
        stage.unresolved.append("(the level's window resolves to nothing importable)")
        return
    # The kernel's one load, exactly as the Scene tab uses it -- so a level brought in
    # behind a cutscene is built by the same pass as one brought in by hand.
    loading.load(stage.context, [seed], stage.options)


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
    from .. import scene_state
    for rows in (scene_state.LANDMARKS or {}).values():
        for row in rows:
            if row["id"] == level:
                return dict(row, scene=_scene_of(level))
    return None


def _scene_of(level):
    from .. import scene_state
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

    from ....Host.Blender import coordinate
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
            for curve in _curves(strip.action, strip.action_slot):
                if curve.data_path != "location":
                    continue
                values = [point.co[1] for point in curve.keyframe_points]
                if not values:
                    continue
                low, high = min(values), max(values)
                seen = axes.get(curve.array_index)
                axes[curve.array_index] = ((min(low, seen[0]), max(high, seen[1]))
                                           if seen else (low, high))
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
    from .. import identity

    _ACTOR_RIGS.clear()
    identity.forget()
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
