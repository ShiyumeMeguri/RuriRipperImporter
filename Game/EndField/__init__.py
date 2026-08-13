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
               (``scene_state`` + ``scene_importer``,
                ``ui_scene_state`` + ``ui_scene_importer``)
``Character``  the SkeletalMorph facial system: browse the emotion/pose/lipsync
               library, bind its ctrl drivers to a rig, bake its animations.
               (``skeletal_morph`` + ``morph_state``)

All of it lives here, including the parts that touch no bpy: the game's
addressable-path conventions and its studio-written MonoBehaviour schemas are
still ONE GAME'S facts, and ``RuriRipperPyBridge`` -- shared with a host that has no
such feature -- may not carry them.

Declared as one GAME_MODULE row (see ``Game``), so the core panel reveals both
tabs exactly while an ``EndField_*`` hook is ticked and never names this game
itself.
"""

from __future__ import annotations

from .. import GameModule, GameTab
from . import character_panel, roster_panel, scene_panel, shader


def _register():
    scene_panel.register()
    roster_panel.register()
    character_panel.register()
    # CharacterNPR materials build as generated Ruri Uber node groups instead of
    # the host's Principled fallback -- a graph provider, so the host core stays
    # game-blind (see shader/__init__ and material_builder.GRAPH_PROVIDERS).
    shader.register()


def _unregister():
    shader.unregister()
    character_panel.unregister()
    roster_panel.unregister()
    scene_panel.unregister()


GAME_MODULE = GameModule(
    game_name="EndField",
    label="Endfield",
    # The identity this game's player builds under, as its own app.info states it. Nothing is
    # pre-ticked any more: pointing the panel at an install is what selects a game, so a hook is
    # enabled when the folder IS this game and never on a guess. The company name (Gryphline) is
    # deliberately not listed -- it also ships Ex Astris, and an ambiguous match must not pick one.
    project_names=("Endfield",),
    tabs=(
        GameTab("streamingscene", "StreamingScene",
                "The game's own scenes: the self-contained ones, and one named place "
                "of an open-world map at a time",
                scene_panel.draw_streaming_scene_tab),
        GameTab("character", "Character",
                "Drive an imported character's face: the SkeletalMorph "
                "emotion/pose/lipsync library and its morph animations",
                character_panel.draw_character_tab),
    ),
    register=_register,
    unregister=_unregister,
)
