"""Endfield (Arknights: Endfield) -- everything the add-on has for this game and
nothing else, across every hooked version of it.

Two tabs, neither of which means anything for another title:

``StreamingScene``  the game's own scenes, every kind it ships, switched inside
               the tab: ``Scene`` the self-contained ones -- pick one from the
               game's own list and import it whole; ``World`` the open-world maps
               -- pick one of the places the game itself names in map01/map02 and
               import that place, at the size the game gives it, out of the game's
               own chunk format; ``UI`` the lit little stages an interface stands
               a model on (CharInfo, CharFormation, WeaponInfo), loaded around a
               character already in the scene.
               (``scene`` + ``scene_state``;
                ``ui_scene`` + ``ui_scene_state`` for the UI half, which is the
                one part that needs a scene to load a stage AROUND)
``Character``  the cast, and what can be done to the one you loaded. Its own row
               switches what the tab lists: ``Characters``/``NPCs`` the two casts,
               ``Story`` the animations story playback uses, filed the way the game
               files them -- a cutscene by shot / kind / actor, a dialogue timeline
               by spoken line. Under it, the SkeletalMorph facial system: browse
               the emotion/pose/lipsync library, bind its ctrl drivers to a rig,
               bake its animations. Each SECTION asks for what it needs, so a host
               with no rigs still gets the cast.
               (``character`` composing ``roster`` + ``story`` +
                ``face``; ``skeletal_morph`` + ``morph_state``)

All of it lives here, including the parts that touch no bpy: the game's
addressable-path conventions and its studio-written MonoBehaviour schemas are
still ONE GAME'S facts, and ``RuriRipperPyBridge`` -- shared with a host that has no
such feature -- may not carry them.

Declared as one GAME_MODULE row (see ``Game``), so the core panel reveals both tabs
exactly while the install in front of it IS Endfield, and never names this game
itself.
"""

from __future__ import annotations

import importlib

from ...Kernel import host as host_port
from ...Kernel.app import look
from .. import GameModule, GameSection, GameTab
from . import shader

#: The parts the two tabs are composed of, each with the capability its host must
#: answer, or None for one every host can offer. This is the ONE statement of it:
#: the module is imported and registered only when the answer is yes (a panel module
#: is where its host classes live, and importing one to then not show it is how a
#: plugin ends up requiring a host feature it never uses), and the tab bodies ask
#: this same declaration rather than re-testing the capability themselves.
SECTIONS = (
    GameSection("roster"),
    GameSection("scene"),
    GameSection("ui_scene", host_port.SCENE_GRAPH),
    GameSection("story", host_port.ANIMATION),
    GameSection("face", host_port.MORPH_TARGETS),
)

_LOADED = []


def _register():
    _LOADED[:] = [importlib.import_module("." + one.id, __name__)
                  for one in SECTIONS if one.available]
    for module in _LOADED:
        module.register()
    # CharacterNPR materials build as generated Ruri Uber node groups instead of
    # the host's Principled fallback -- a graph provider, so the host core stays
    # game-blind (see shader/__init__ and material_builder.GRAPH_PROVIDERS).
    shader.register()


def _unregister():
    shader.unregister()
    for module in reversed(_LOADED):
        module.unregister()
    _LOADED[:] = []




def _face_retarget(*arguments):
    """Restate a clip's baked facial performance in this character's own
    expression vocabulary. Only reached from the clip-loading path, which needs a
    rig to load onto -- so the module is imported there rather than here."""
    from . import face_retarget
    return face_retarget.provide(*arguments)


def _secondary_motion(*arguments):
    """READ the model's own hair/cloth chains. Writing them onto a rig is the
    host's; this side never learns which solver holds them."""
    from . import secondary_motion
    return secondary_motion.read(*arguments)


GAME_MODULE = GameModule(
    # The productName this game's player builds under, as its own app.info states it --
    # the same string the upstream decoder declares, so the join is equality.
    game_name="Endfield",
    label="Endfield",
    sections=SECTIONS,
    tabs=(
        # A scene window is hundreds of separate placements with their own
        # transforms, which every host can hold: as many objects where there is a
        # scene, as one glTF whose nodes share their meshes where the project IS a
        # file. What the window is gets stated once (loading.SCENE_WINDOW) and
        # built by whichever host is there. The one half that does NOT cross is
        # the display stage, and it says so inside the tab.
        GameTab("streamingscene", "StreamingScene",
                "The game's own scenes: the self-contained ones, and one named place "
                "of an open-world map at a time",
                ("scene", "draw_streaming_scene_tab")),
        # 名册 + 剧情 + 表情库是一格,一直都是。其中「驱动一张脸」需要形态键,
        # 「播一段剧情」需要动画面 —— 答不出的宿主少的是那一段,不是整格:
        # 为一个宿主做不到的一段把另一个宿主的布局也改掉,那是把差异做进来。
        GameTab("character", "Character",
                "The game's own cast, and what can be done to the one you loaded: "
                "its story animations, and the SkeletalMorph emotion/pose/lipsync "
                "library",
                ("character", "draw_tab")),
        # 「一帧最终长什么样」是这个游戏的问题(是它的着色栈在管画面),但答案由宿主给:
        # 材质参数、主光、后处理链是一个宿主的三段回答,显示端的环境/LUT/tonemap 是另一个
        # 宿主的一段回答。所以这一格画的是 look 注册表里本宿主答得出的那些段,而不是某个
        # 宿主的面板 —— 它因此在两端都在,只是里面的段不一样。
        GameTab("post", "Look",
                "How the frame is finally shown: this game's shading knobs, and whatever "
                "this application exposes about its display",
                look.draw),
    ),
    # A UI or cutscene clip carries its face in the BONE tracks, so importing one onto
    # another character needs the performance read off the geometry and restated in that
    # character's own expression vocabulary. The host's one clip-loading path asks for
    # this; the maths is the hook's (RipperBlenderBridge.SolveFaceRetarget).
    face_retarget=_face_retarget,
    # The hair/cloth/accessory chains this game tunes ON the model prefab itself,
    # which is why they can travel with an import at all (see secondary_motion.read).
    secondary_motion=_secondary_motion,
    register=_register,
    unregister=_unregister,
)
