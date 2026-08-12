"""Koikatu -- everything the add-on has for this game and nothing else.

Two tabs, neither of which means anything for another title:

``Scene``      the game's own places: its LEVELS (real Unity scenes under
               ``map/scene``, named by its own ``mapinfo`` tables) and the
               STUDIO backgrounds (prefabs, named by the studio's own catalog).
               (``scene_state`` + ``studio_info`` + ``scene_panel``)
``Character``  a character is assembled, not shipped -- one skeleton plus a
               prefab per slot, joined by bone name. Build one from a character
               card or from the customization catalog directly, drive her face
               through the head's own blend-shape pattern system, and put the
               studio's catalogued animations on her.
               (``chara_file`` + ``catalog`` + ``chara_state`` +
                ``chara_importer`` + ``face_state`` + ``face_importer`` +
                ``chara_panel``)

This module is a PANEL and nothing else. Every list it draws is a dataset the
game's own hook publishes (``Ruri.RipperHook.Koikatu`` -- see ``datasets``): the
customization catalog, the cast, a build plan, the places, the animations, the
expressions, a head's pattern table. The MessagePack tables, the character-card
container and the bundle addressing are all read there, in C#, off the loaded
objects; nothing on this side parses a byte of the game.

What is left here is the part that can only happen in Blender: building the
prefabs a plan names onto one armature, and setting shape-key values.

The game is recognised either by its hook or by the build's own identity
(``project_names``, matched against the Unity productName/companyName its players
carry) -- pointing the panel at the install already says which game it is.
"""

from __future__ import annotations

from .. import GameModule, GameTab
from . import chara_panel, scene_panel


def _register():
    scene_panel.register()
    chara_panel.register()


def _unregister():
    chara_panel.unregister()
    scene_panel.unregister()


GAME_MODULE = GameModule(
    game_name="Koikatu",
    label="Koikatu",
    # Every player this project ships: the game, its VR build and the character
    # studio, as their own app.info states them.
    project_names=("Koikatu", "KoikatuVR", "CharaStudio", "illusion__Koikatu",
                   "illusion_Koikatu"),
    tabs=(
        GameTab("scene", "Scene",
                "The game's own levels and the studio's backgrounds",
                scene_panel.draw_scene_tab),
        GameTab("character", "Character",
                "Assemble a character from her card or from the game's own "
                "customization catalog, then drive her face and her animations",
                chara_panel.draw_character_tab),
    ),
    register=_register,
    unregister=_unregister,
)
