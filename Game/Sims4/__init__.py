"""The Sims 4 -- a setup of that game, claimed by the ENGINE FAMILY the kernel's probe
reports rather than by a product name: this game ships no Unity player, so there is no
productName a folder here could be named after, and its one decoder
(``Ruri.RipperHook.Sims4.Sims4_Hook``) reads every setup of it.

A SETUP is either half of what the game installs: the folder it is installed in, which
ships the catalogue every lot is built out of, or the user folder beside the player's
documents, which holds the saves, the tray and whatever the player added to Mods. Both are
this game and both are read the same way; which one is open only changes what is there.

Two contributions:

``Scene`` tab  every saved lot the setup carries -- a whole house each, imported assembled.
``Prop`` tab   every object the setup can place, picked by name and imported whole.

Declared as one GAME_MODULE row (see ``Game``).
"""

from __future__ import annotations

import importlib

from .. import GameModule, GameSection, GameTab

#: This game's panels ask the host for nothing the browser does not already ask for. An
#: OBJECT is a mesh tree, and what the decoder hands over for one is the same normalised
#: placement statement every other non-Unity source hands over -- which every host's
#: builder takes, each in its own idiom.
SECTIONS = (GameSection("scene"), GameSection("props"))

_LOADED = []


def _import_packages(context, packages, options):
    """Every object built from what the decoder states it holds.

    A setup of this game is read directly: placements, geometry, materials and texture
    pixels all cross as data, so nothing here creates a Unity asset or parses text.

    Reading is this module's (``read.package``); BUILDING is the host's one import entry.
    That split is the whole reason an object of this game can reach a second host at all.
    """
    from ...Kernel import host as host_port
    from . import read
    built = 0
    for package in dict.fromkeys(packages):
        stated = read.package(package, package, options)
        if stated is None:
            continue
        built += host_port.current().import_packages(context, stated, options).imported
    return built


def _register():
    _LOADED[:] = [importlib.import_module("." + one.id, __name__)
                  for one in SECTIONS if one.available]
    for module in _LOADED:
        module.register()


def _unregister():
    for module in reversed(_LOADED):
        module.unregister()
    _LOADED[:] = []


GAME_MODULE = GameModule(
    # The family-wide decoder's GameName -- the GameType member the kernel declares for
    # this game, and the family string its install probe reports.
    game_name="Sims4",
    label="The Sims 4",
    engine="Sims4",
    sections=SECTIONS,
    tabs=(
        GameTab("scene", "Scene",
                "Every saved lot this setup carries -- a whole house each, imported assembled",
                ("scene", "draw")),
        GameTab("props", "Prop",
                "Every object this setup can place, picked by name and imported whole",
                ("props", "draw")),
    ),
    importer=_import_packages,
    register=_register,
    unregister=_unregister,
)
