"""EXILIUM (Girls' Frontline 2) -- everything the add-on has for this game and
nothing else.

Two tabs, neither of which means anything for another title:

``Scene``      every scene the game ships, under the path its own catalog states,
               grouped by the folder tree the game files them in. (``scene``)
``Character``  the cast: the units the game lets you field, named through whichever
               text package the host's locale reads, and every model any of them is
               built from -- outfits, enemies, summons. (``roster``)

Three facts about this title decide the shape of both, and all three live upstream
in ``Ruri.RipperHook.EXILIUM`` rather than here:

* it builds every asset under its GUID instead of its path, so a bundle names
  nothing a config row could join to -- the content catalog is the only statement
  of which asset an address is;
* it hashes that address before writing it, so a path is checked by hashing it the
  same way rather than read back out;
* most of its archives are a byte range inside another archive, so a loader that
  reads only the files the folder lists reaches about a seventh of the game.

Declared as one GAME_MODULE row (see ``Game``), so the core panel reveals both tabs
exactly while the install in front of it IS this game, and never names it itself.
"""

from __future__ import annotations

import importlib

from .. import GameModule, GameSection, GameTab
from ...Kernel.app import prefabs as app_prefabs
from . import mesh_resolver

#: The parts the two tabs are composed of, each with the capability its host must
#: answer. Imported and registered ONLY when that answer is yes: a panel module is
#: where its host classes live, and importing one to then not show it is how a
#: plugin ends up requiring a host feature it never uses.
#: Both tabs of this game are the same act -- pick one of the things the game's
#: own catalog names, and run the browser's own import over what it resolved to.
#: That needs nothing of the host the browser does not already need, so both
#: cross and neither declares a capability.
SECTIONS = (GameSection("roster"), GameSection("scene"))

_LOADED = []


def _register():
    _LOADED[:] = [importlib.import_module("." + one.id, __name__)
                  for one in SECTIONS if one.available]
    for module in _LOADED:
        module.register()
    # A character prefab here carries renderers with no mesh in them: the geometry
    # is listed beside the rig and attached at run time. The ONE prefab path asks
    # whoever owns the prefab for the missing mesh; this is that answer, and a
    # prefab keeping no such list simply declines.
    app_prefabs.register_mesh_resolver(mesh_resolver.provide)
    app_prefabs.register_detail_rule(mesh_resolver.detail)


def _unregister():
    app_prefabs.unregister_detail_rule(mesh_resolver.detail)
    app_prefabs.unregister_mesh_resolver(mesh_resolver.provide)
    mesh_resolver.forget()
    for module in reversed(_LOADED):
        module.unregister()
    _LOADED[:] = []


GAME_MODULE = GameModule(
    # The productName this game's player builds under, as its own app.info states it --
    # the same string the upstream decoder declares, so the join is equality.
    game_name="EXILIUM",
    label="Girls' Frontline 2",
    sections=SECTIONS,
    tabs=(
        GameTab("scene", "Scene",
                "Every scene the game ships, under the path its own catalog states",
                ("scene", "draw")),
        GameTab("character", "Character",
                "The cast the game lets you field, and every model any of them is built from",
                ("roster", "draw")),
    ),
    register=_register,
    unregister=_unregister,
)
