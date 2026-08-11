"""Endfield (Arknights: Endfield) -- everything the add-on has for this game and
nothing else, across every hooked version of it.

Two tabs, neither of which means anything for another title:

``Scene``      the self-contained scenes -- pick one from the game's own list and
               import it whole.
``World``      the open-world maps -- pick one of the places the game itself names
               in map01/map02 and import that place, at the size the game gives
               it, out of the game's own chunk format.
               (both: ``scene_state`` + ``asset_paths`` + ``scene_importer``)
``Character``  the SkeletalMorph facial system: browse the emotion/pose/lipsync
               library, bind its ctrl drivers to a rig, bake its animations.
               (``skeletal_morph`` + ``morph_state``)

All of it lives here, including the parts that touch no bpy: the game's
addressable-path conventions and its studio-written MonoBehaviour schemas are
still ONE GAME'S facts, and ``ruri_pybridge`` -- shared with a host that has no
such feature -- may not carry them.

Declared as one GAME_MODULE row (see ``Game``), so the core panel reveals both
tabs exactly while an ``EndField_*`` hook is ticked and never names this game
itself.
"""

from __future__ import annotations

from .. import GameModule, GameTab
from . import character_panel, roster_panel, scene_panel, shader
from ...ruri_pybridge.unity import mesh_decoder

# 本作的顶点压法:法线与切线同挤在一个 32-bit word 里(bit30 标志 / 低 20 位八面体法线 /
# bits20-29 切平面内角 / bit31 手性)。位布局算法是通用的、住在内核;"这批资产用的是它"
# 只有游戏模块知道,所以在这里显式投票 —— 内核绝不按游戏名猜。
_VERTEX_PACKING = "oct20_frame"


def _register():
    mesh_decoder.ENABLED_PACKINGS.add(_VERTEX_PACKING)
    scene_panel.register()
    roster_panel.register()
    character_panel.register()
    # CharacterNPR materials build as generated Ruri Uber node groups instead of
    # the host's Principled fallback -- a graph provider, so the host core stays
    # game-blind (see shader/__init__ and material_builder.GRAPH_PROVIDERS).
    shader.register()


def _unregister():
    mesh_decoder.ENABLED_PACKINGS.discard(_VERTEX_PACKING)
    shader.unregister()
    character_panel.unregister()
    roster_panel.unregister()
    scene_panel.unregister()


GAME_MODULE = GameModule(
    game_name="EndField",
    label="Endfield",
    # Pre-ticked the first time the DLL lists it (see RURI_OT_refresh_hooks);
    # 1.2.4's class also answers to this id via AlsoCoversVersions.
    default_hook_id="EndField_1.4.4",
    tabs=(
        GameTab("scene", "Scene",
                "The self-contained scenes -- small enough to import whole",
                scene_panel.draw_scene_tab),
        GameTab("world", "World",
                "The open-world maps -- import one named place of map01/map02 at a time",
                scene_panel.draw_world_tab),
        GameTab("character", "Character",
                "Drive an imported character's face: the SkeletalMorph "
                "emotion/pose/lipsync library and its morph animations",
                character_panel.draw_character_tab),
    ),
    register=_register,
    unregister=_unregister,
)
