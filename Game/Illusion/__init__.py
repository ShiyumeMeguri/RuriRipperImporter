"""The Illusion / ILLGAMES family -- four titles that ship the SAME game, rebuilt.

They share an install layout (``abdata``), a parts-catalog model, a character-card
container, a category numbering and a rig bone vocabulary. Where they differ --
which container their lists are written in, which blocks a card carries, which
prefabs the base rig is -- is stated once per title in the upstream hook's own
``IllusionProfile`` (``Ruri.RipperHook.Illusion``), never here.

So this package is ONE panel serving all four. Every list it draws is a dataset the
hook publishes under that title's prefix (see ``datasets``); the MessagePack and
MemoryPack tables, the card container, the bundle addressing and the build plan are
all read there, in C#, off the loaded objects. Nothing on this side parses a byte of
a game, and nothing here is written per title -- what is left is the part that can
only happen in Blender: building the prefabs a plan names onto one armature, and
setting shape-key values.

Two tabs:

``Scene``      the title's own places: its LEVELS (real Unity scenes, named by its
               own map tables) and the STUDIO backgrounds (prefabs, named by the
               studio's catalog). Listed only for the titles that ship a studio.
``Character``  a character is assembled, not shipped -- one skeleton plus a prefab
               per slot, joined by bone name. Build one from a character card,
               drive her face through the head's own blend-shape pattern system,
               and put the catalogued animations on her.

Each title is recognised by the identity its own build carries (``project_names``,
matched against the Unity productName/companyName its players report) -- pointing
the panel at the install already says which one it is.
"""

from __future__ import annotations

from .. import GameModule, GameTab
from . import chara_panel, scene_panel

_SCENE_TAB = ("scene", "Scene",
              "The game's own levels and the studio's backgrounds",
              scene_panel.draw_scene_tab)
_CHARACTER_TAB = ("character", "Character",
                  "Assemble a character from her card or from the game's own "
                  "customization catalog, then drive her face and her animations",
                  chara_panel.draw_character_tab)

# The bpy classes are the PACKAGE's, shared by every title in it, and a class
# registers exactly once -- so the first title carries the real register/unregister
# and the rest declare none. Which title that is does not matter: the host registers
# every module it discovers, and unregisters them in reverse.
_registered = []


def _register():
    if _registered:
        return
    scene_panel.register()
    chara_panel.register()
    _registered.append(True)


def _unregister():
    if not _registered:
        return
    chara_panel.unregister()
    scene_panel.unregister()
    _registered.clear()


def _title(game_name, label, project_names, tabs):
    return GameModule(
        game_name=game_name,
        label=label,
        project_names=project_names,
        tabs=tuple(GameTab(*tab) for tab in tabs),
        register=_register,
        unregister=_unregister,
    )


# The titles this family covers. All four ship both a place list and a cast, each
# through whichever reader its own generation needs (the hook registers that per
# title), so all four state both tabs. A list a title does not ship comes back empty
# from its own section rather than costing the whole tab.
GAME_MODULE = [
    _title("Koikatu", "Koikatu",
           ("Koikatu", "KoikatuVR", "illusion__Koikatu", "illusion_Koikatu"),
           (_SCENE_TAB, _CHARACTER_TAB)),
    _title("KoikatsuSunshine", "Koikatsu Sunshine",
           ("KoikatsuSunshine", "KoikatsuSunshine_VR",
            "illusion__KoikatsuSunshine", "illusion_KoikatsuSunshine"),
           (_SCENE_TAB, _CHARACTER_TAB)),
    _title("HoneyCome", "HoneyCome", ("HoneyCome",), (_SCENE_TAB, _CHARACTER_TAB)),
    _title("SamabakeScramble", "Summer Vacation! Scramble",
           ("SamabakeScramble",), (_SCENE_TAB, _CHARACTER_TAB)),
]
