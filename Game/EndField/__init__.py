"""Endfield (Arknights: Endfield) -- everything the add-on shows only for this
game, across every hooked version of it.

Two tabs, neither of which means anything for another title:

``Scene``      the streaming-scene import: pick a map, discover its entity
               placements out of the game's own chunk format, import the lot.
``Character``  the SkeletalMorph facial system: browse the emotion/pose/lipsync
               library, bind its ctrl drivers to a rig, bake its animations.

Declared as one GAME_MODULE row (see ``Game``), so the core panel reveals both
tabs exactly while an ``EndField_*`` hook is ticked and never names this game
itself.
"""

from __future__ import annotations

from .. import GameModule, GameTab
from . import character_panel, scene_panel


def _register():
    scene_panel.register()
    character_panel.register()


def _unregister():
    character_panel.unregister()
    scene_panel.unregister()


GAME_MODULE = GameModule(
    game_name="EndField",
    label="Endfield",
    # Pre-ticked the first time the DLL lists it (see RURI_OT_refresh_hooks);
    # 1.2.4's class also answers to this id via AlsoCoversVersions.
    default_hook_id="EndField_1.3.3",
    tabs=(
        GameTab("scene", "Scene",
                "Discover a whole map's placements and import it in one go",
                scene_panel.draw_scene_tab),
        GameTab("character", "Character",
                "Drive an imported character's face: the SkeletalMorph "
                "emotion/pose/lipsync library and its morph animations",
                character_panel.draw_character_tab),
    ),
    register=_register,
    unregister=_unregister,
)
