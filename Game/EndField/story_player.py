"""Play a built story the way the game plays it: run, hold, click, run again.

A dialogue scene is not a film. The game's own Timeline says where it holds
(``hold``), where the player may click (``click``), what is said (``line``) and
what is offered (``option``) -- so playing one back faithfully is not a player
somebody invents, it is those four words executed.

Playback is BLENDER'S OWN. Pressing play is pressing play: the strips, the camera
and the subtitle objects were built onto the scene, so the story runs, renders and
scrubs with nothing here in the loop. What this module adds is the one thing native
playback has no notion of -- stopping at a hold until the reader is ready:

    a frame handler watches for the playhead entering a ``hold`` span and cancels
    playback there. Advancing jumps past that hold and starts running again.

The beats live on the SCENE, not in this module, so a saved .blend still knows its
own story and reopening one does not require rebuilding the stage.
"""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       StringProperty)

from . import datasets

# How a story runs. Continuous is a cutscene; stepped is a dialogue, and the
# difference is only whether a hold stops the playhead.
RUN_CONTINUOUS = "continuous"
RUN_STEPPED = "stepped"

OPTION_SEPARATOR = " | "


class RURI_PG_story_beat(bpy.types.PropertyGroup):
    """One thing the story does at one frame, in the game's own vocabulary."""
    kind: StringProperty()
    start: IntProperty()
    end: IntProperty()
    speaker: StringProperty()
    text: StringProperty()
    emotion: StringProperty()
    options: StringProperty()
    jump_to: IntProperty(default=-1)


class RURI_PG_story_script(bpy.types.PropertyGroup):
    """The built story, as the scene's own data."""
    unit: StringProperty()
    beats: CollectionProperty(type=RURI_PG_story_beat)
    active: IntProperty()
    mode: EnumProperty(
        name="Playback",
        items=[(RUN_CONTINUOUS, "Continuous",
                "Play straight through, the way a cutscene plays"),
               (RUN_STEPPED, "Click to advance",
                "Hold where the game holds and wait for a click, the way a dialogue plays")],
        default=RUN_CONTINUOUS)
    waiting: BoolProperty(default=False)
    chosen: IntProperty(default=-1)


def _script(context):
    return getattr(context.scene, "ruri_story_script", None)


def spoken_at(script, frame):
    """The line being said at this frame, or None. What the panel reads to show the
    story text while the playhead moves."""
    if script is None:
        return None
    found = None
    for beat in script.beats:
        if beat.kind == "line" and beat.start <= frame <= beat.end:
            found = beat
    return found


def holding_at(script, frame):
    """The hold the playhead is inside, or None."""
    if script is None:
        return None
    for beat in script.beats:
        if beat.kind == "hold" and beat.start <= frame <= beat.end:
            return beat
    return None


def offered_at(script, frame):
    """The options the game offers around this frame, as a list of strings."""
    if script is None:
        return []
    for beat in script.beats:
        if beat.kind == "option" and beat.start - 1 <= frame <= beat.end + 1 and beat.options:
            return [part for part in beat.options.split(OPTION_SEPARATOR) if part]
    return []


def _next_after(script, frame, kinds=("line",)):
    """The next beat of these kinds strictly after this frame."""
    best = None
    for beat in script.beats:
        if beat.kind in kinds and beat.start > frame and (best is None or beat.start < best.start):
            best = beat
    return best


@persistent
def _on_frame(scene, _depsgraph=None):
    """Stop where the game stops. Runs on every frame change, so it costs a scan of
    the beat list only while a stepped story is actually playing."""
    script = getattr(scene, "ruri_story_script", None)
    if script is None or script.mode != RUN_STEPPED or not script.beats or script.waiting:
        return
    if not bpy.context.screen or not bpy.context.screen.is_animation_playing:
        return
    if holding_at(script, scene.frame_current) is None:
        return
    script.waiting = True
    bpy.ops.screen.animation_cancel(restore_frame=False)


class RURI_OT_story_play(bpy.types.Operator):
    """Play the built story from the top."""
    bl_idname = "ruri.story_play"
    bl_label = "Play Story"
    bl_description = "Play this unit from its first frame through the scene camera"

    @classmethod
    def poll(cls, context):
        script = _script(context)
        return script is not None and len(script.beats) > 0

    def execute(self, context):
        script = _script(context)
        script.waiting = False
        script.chosen = -1
        context.scene.frame_set(int(context.scene.frame_start))
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        bpy.ops.screen.animation_play()
        return {"FINISHED"}


class RURI_OT_story_advance(bpy.types.Operator):
    """Click on: leave the hold the story is waiting at and run to the next one.

    This is the game's own click. Where it resumes is where the timeline says the
    hold ends, so nothing here decides pacing -- the authored scene does."""
    bl_idname = "ruri.story_advance"
    bl_label = "Advance"
    bl_description = "Advance the story past the line it is holding on"

    @classmethod
    def poll(cls, context):
        script = _script(context)
        return script is not None and len(script.beats) > 0

    def execute(self, context):
        script = _script(context)
        scene = context.scene
        held = holding_at(script, scene.frame_current)
        target = held.end + 1 if held is not None else None
        if target is None:
            following = _next_after(script, scene.frame_current)
            if following is None:
                self.report({"INFO"}, "The story has no line after this one.")
                return {"CANCELLED"}
            target = following.start
        scene.frame_set(min(int(target), int(scene.frame_end)))
        script.waiting = False
        if script.mode == RUN_STEPPED and not context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()
        return {"FINISHED"}


class RURI_OT_story_goto_beat(bpy.types.Operator):
    """Put the playhead on one line. Reading a story is also jumping around in it."""
    bl_idname = "ruri.story_goto_beat"
    bl_label = "Go To Line"
    bl_description = "Move the playhead to this line"
    frame: IntProperty()

    def execute(self, context):
        script = _script(context)
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        context.scene.frame_set(int(self.frame))
        if script is not None:
            script.waiting = False
        return {"FINISHED"}


class RURI_OT_story_choose(bpy.types.Operator):
    """Take one of the options the story offers here."""
    bl_idname = "ruri.story_choose"
    bl_label = "Choose"
    bl_description = "Take this option and carry on"
    index: IntProperty()

    def execute(self, context):
        script = _script(context)
        script.chosen = self.index
        scene = context.scene
        # Where a chosen option carries the story is stated by the jump the game
        # files under that option's own index. With none stated for it, the story
        # simply carries on, which is what a dialogue with one outcome does.
        jump = next((beat for beat in script.beats
                     if beat.kind == "jump" and beat.jump_to == self.index
                     and beat.start >= scene.frame_current), None)
        if jump is not None:
            scene.frame_set(int(jump.end + 1))
            self.report({"INFO"}, "Took option {0}.".format(self.index + 1))
        else:
            return bpy.ops.ruri.story_advance()
        script.waiting = False
        return {"FINISHED"}


def draw_player(layout, context):
    """The story as it plays: what is being said now, and the way through it."""
    script = _script(context)
    if script is None or not script.beats:
        return
    frame = context.scene.frame_current
    box = layout.box()
    head = box.row(align=True)
    head.label(text=script.unit, icon="PLAY")
    head.prop(script, "mode", text="")

    line = spoken_at(script, frame)
    said = box.column(align=True)
    said.scale_y = 0.9
    if line is None:
        row = said.row()
        row.enabled = False
        row.label(text="(nothing is said at this frame)", icon="REC")
    else:
        if line.speaker:
            who = said.row()
            who.label(text=line.speaker, icon="OUTLINER_OB_ARMATURE")
            if line.emotion:
                mood = who.row()
                mood.enabled = False
                mood.alignment = "RIGHT"
                mood.label(text=line.emotion)
        for chunk in _wrapped(line.text, 38):
            said.label(text=chunk)

    options = offered_at(script, frame)
    for index, option in enumerate(options):
        chosen = box.operator(RURI_OT_story_choose.bl_idname, text=option, icon="DOT")
        chosen.index = index

    run = box.row(align=True)
    run.scale_y = 1.3
    run.operator(RURI_OT_story_play.bl_idname, icon="PLAY", text="Play From Start")
    advance = run.row(align=True)
    advance.alert = script.waiting
    advance.operator(RURI_OT_story_advance.bl_idname, icon="FRAME_NEXT",
                     text="Click To Advance" if script.waiting else "Next Line")
    if script.waiting:
        note = box.row()
        note.enabled = False
        note.label(text="Holding here, the way the game holds.", icon="PAUSE")


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


_CLASSES = (
    RURI_PG_story_beat,
    RURI_PG_story_script,
    RURI_OT_story_play,
    RURI_OT_story_advance,
    RURI_OT_story_goto_beat,
    RURI_OT_story_choose,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_story_script = bpy.props.PointerProperty(type=RURI_PG_story_script)
    if _on_frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame)


def unregister():
    if _on_frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame)
    del bpy.types.Scene.ruri_story_script
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
