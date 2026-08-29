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

from ...RuriRipperPyBridge.session import cabmap_state
from . import datasets, roster_panel

# What a directive means, by its own word. The registry IS the extension point.
REALIZERS = {}

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
        self.unresolved = []
        self.markers = 0
        self.looking = 0
        self.unknown = set()

    def frame(self, seconds):
        return seconds * self.fps

    def note(self, row):
        self.end = max(self.end, self.frame(row["until"]))


def build(context, unit, variant="", language="", scene_mode=SCENE_NONE):
    """Bring one unit up on stage. Returns the Stage, whose counters are the report.

    The order is the game's own: the cast first (a clip needs the rig it drives to
    exist), then every directive in time order.
    """
    rows = _stage_rows(unit, variant, language)
    if not rows:
        return None
    stage = Stage(context, unit, _scene_fps(context, rows))
    for row in rows:
        stage.note(row)

    # The shots first, and the scene pointed at the camera BEFORE anybody is
    # imported: a character import builds its outline shells against the scene
    # camera, and with none set it falls back to a default view basis and says so.
    # A cutscene is a camera performance anyway -- the actors are what it films.
    shots = [row for row in rows if _drives_camera(row)]
    # Everything read straight off the bridge -- the shots, and whatever motion
    # lands on a stand-in rather than a rig -- marked and crossed once.
    prefetch_curves([row["sourceCab"] for row in rows
                     if row["sourceCab"] and not row["model"]])
    for row in shots:
        _realize(stage, row)
    if stage.camera is not None:
        context.scene.camera = stage.camera
        context.scene.frame_set(int(round(min((stage.frame(row["at"]) for row in shots),
                                              default=0.0))))

    _load_cast(stage, rows)
    for row in rows:
        if not _drives_camera(row):
            _realize(stage, row)
    _finish(stage)
    if scene_mode != SCENE_NONE:
        _load_scene(stage, scene_mode)
    return stage


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


# ── the cast ────────────────────────────────────────────────────────────────

def _load_cast(stage, rows):
    """Bring in everyone the unit animates, and remember which rig IS them.

    Who plays in the unit is the game's own filing, which the stage states as the
    actor a motion directive drives. A token the game ships no model for is
    reported rather than silently skipped -- the story still plays without them.
    """
    wanted = {}
    for row in rows:
        # Only what the game ships a model for. WHICH performers those are is the
        # hook's answer, not a kind list kept here -- that would be game vocabulary.
        if not row["model"] or not row["sourceCab"]:
            continue
        actor = _actor_of(row)
        if actor:
            wanted.setdefault(actor, []).append(row["sourceCab"])
    _spawn_missing(stage, wanted)
    for actor, cabs in wanted.items():
        rig = _rig_for(stage.context, actor)
        if rig is None:
            stage.unresolved.append(actor)
            continue
        stage.actors[actor] = rig
        if not _make_active(stage.context, rig):
            raise RuntimeError(
                "The view layer would not make {0} the active object, so this unit's "
                "animation has no skeleton to land on. It is usually a leftover rig in "
                "an excluded collection -- clear the scene and load again.".format(rig.name))
        _import_cabs(stage.context, list(dict.fromkeys(cabs)))


def _spawn_missing(stage, wanted):
    """Bring in every model the unit needs, in ONE import.

    A model import needs no active rig -- only a clip import does -- so importing
    them one actor at a time buys nothing and pays a closure resolution per actor.
    They are marked together and the rigs are matched back to their actors after,
    by the same name rule a rig is found by in any later session.
    """
    missing = {}
    for actor in wanted:
        if _rig_for(stage.context, actor) is not None:
            continue
        rows = _model_rows_for(actor)
        if rows:
            missing[actor] = [row["cab"] for row in rows]
    if not missing:
        return
    before = {obj.name for obj in stage.context.scene.objects if obj.type == "ARMATURE"}
    _import_cabs(stage.context, list(dict.fromkeys(
        cab for cabs in missing.values() for cab in cabs)))
    added = [obj for obj in stage.context.scene.objects
             if obj.type == "ARMATURE" and obj.name not in before]
    for actor in missing:
        token = actor.lower()
        for rig in added:
            if token in rig.name.lower():
                _ACTOR_RIGS[actor] = rig.name
                break


def _actor_of(row):
    """Who a directive moves -- as the hook resolved it, never as a leaf guessed off
    the bound object's path.

    That guess is exactly what broke this: a binding path ends in the PREFAB name
    (``P_npc_major_death_01``), which resolves to no character model, so the loader
    fell through to loose parts and dropped 361 unskinned meshes at the origin. The
    game states the performer in the clip's own name and the hook already parses
    and roster-resolves exactly that."""
    return row["actor"] or ""


_ACTOR_RIGS = {}
_CHARACTER_IDS = {}


def _character_ids():
    if not _CHARACTER_IDS:
        try:
            table = datasets.story_actors()
        except Exception:
            return _CHARACTER_IDS
        for row in range(len(table)):
            character = table.cell(row, "character")
            if character:
                _CHARACTER_IDS[table.cell(row, "actor")] = character
    return _CHARACTER_IDS


def _model_rows_for(actor):
    """The importable rows of one performer's model, in the order the game states
    it: the prefab its own character data declares, else the prefab named after it,
    else whatever part the game files under that name."""
    character = _character_ids().get(actor, "")
    if character:
        declared = roster_panel.character_model(character)
        if declared:
            rows = datasets.part_rows(declared, cast=datasets.CHARACTERS)
            if rows:
                return rows
    rows = datasets.model_rows(actor, "postmodel", cast=datasets.CHARACTERS)
    return rows or datasets.part_rows(actor)


def _import_cabs(context, cabs):
    """Run the browser's own import over exactly these CABs. One import path, so a
    fix there is a fix here."""
    before = {obj.name for obj in context.scene.objects if obj.type == "ARMATURE"}
    cabmap_state.clear_selection()
    cabmap_state.SELECTED_CABS.update(cabs)
    result = bpy.ops.ruri.import_selected()
    added = [obj for obj in context.scene.objects
             if obj.type == "ARMATURE" and obj.name not in before]
    return ("FINISHED" in result), added


def _rig_for(context, actor):
    if not actor:
        return None
    known = bpy.data.objects.get(_ACTOR_RIGS.get(actor, ""))
    if known is not None and known.type == "ARMATURE" and known.name in context.scene.objects:
        return known
    token = actor.lower()
    matches = [obj for obj in context.scene.objects
               if obj.type == "ARMATURE" and token in obj.name.lower()]
    return matches[0] if len(matches) == 1 else None


def _make_active(context, armature):
    """Point the scene at one rig, and say whether it took.

    The clip import resolves which skeleton to drive from the ACTIVE object, and
    a view layer refuses to activate an object it does not hold -- so an object
    left over in an excluded or unlinked collection activates silently as
    nothing, and the import then reports the scene as having no unambiguous
    skeleton. Which is a true statement about the scene and a useless one about
    the cause, so this returns the fact instead of assuming it."""
    if armature.name not in context.view_layer.objects:
        context.scene.collection.objects.link(armature)
    for obj in context.selected_objects:
        obj.select_set(False)
    armature.select_set(True)
    context.view_layer.objects.active = armature
    return context.view_layer.objects.active is armature


# ── the directives ──────────────────────────────────────────────────────────

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
    actor = _actor_of(row)
    rig = stage.actors.get(actor)
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
        stage.unplaced.append(row["source"])
        return
    _place_strip(camera, row, built[0], stage.fps)
    stage.shots.append(row)
    stage.placed += 1


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
    spawned = _spawn_prefab(stage.context, row)
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


def _spawn_prefab(context, row):
    before = {obj.name for obj in context.scene.objects}
    cabmap_state.clear_selection()
    cabmap_state.SELECTED_CABS.add(row["referenceCab"])
    if "FINISHED" not in bpy.ops.ruri.import_selected():
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


def prefetch_curves(cabs):
    """One crossing for every clip the unit will read.

    A crossing resolves a closure and lays bundles out; doing that once per clip is
    the same work repeated, and on a unit with seventy shots it is seventy times.
    Marking them all and crossing once is the same read, once.
    """
    from ...RuriRipperPyBridge.unity import bridge_asset_db, class_registry

    wanted = [cab for cab in dict.fromkeys(cabs) if cab and (cab, None) not in _CURVES]
    if not wanted:
        return 0
    assets, _roots, _seeds, clips_by_cab, _scenes = cabmap_state.BRIDGE.import_cabs(
        wanted, export_class_ids=[class_registry.id_for_name("AnimationClip")])
    database = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=cabmap_state.BRIDGE.clip_curves_by_guid,
        asset_paths=cabmap_state.BRIDGE.asset_paths_by_guid)
    for cab in wanted:
        _CURVES[(cab, None)] = True
        for guid in clips_by_cab.get(cab.lower(), []):
            found = database.clip_curves(guid)
            if found is not None:
                _CURVES.setdefault((cab, found.name), found)
                _CURVES.setdefault((cab, ""), found)
    return len(wanted)


def _curves_of(cab, clip_name):
    """This clip's curves out of the prefetch, else one crossing for its own cab."""
    found = _CURVES.get((cab, clip_name)) or _CURVES.get((cab, ""))
    if found is not None:
        return found
    if (cab, None) in _CURVES:
        return None
    prefetch_curves([cab])
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
    _CHARACTER_IDS.clear()
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
    if stage.spans:
        parts.append("{0} visibility window(s)".format(len(stage.spans)))
    if stage.markers:
        parts.append("{0} marker(s)".format(stage.markers))
    if stage.unplaced:
        parts.append("{0} clip(s) had nothing to drive".format(len(stage.unplaced)))
    if stage.unresolved:
        parts.append("no model for " + ", ".join(stage.unresolved[:4]))
    if stage.unknown:
        parts.append("no realizer yet for " + ", ".join(sorted(stage.unknown)[:4]))
    return " · ".join(parts)
