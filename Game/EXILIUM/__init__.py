"""EXILIUM (Girls' Frontline 2) -- everything the add-on has for this game and
nothing else.

Two tabs, neither of which means anything for another title:

``Scene``      every scene the game ships, under the path its own catalog states,
               grouped by the folder tree the game files them in. (``scene_panel``)
``Character``  the cast: the units the game lets you field, named through whichever
               text package Blender's locale reads, and every model any of them is
               built from -- outfits, enemies, summons. (``roster_panel``)

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

from .. import GameModule, GameTab
from ... import prefab_importer
from . import mesh_resolver, roster_panel, scene_panel


def _register():
    roster_panel.register()
    scene_panel.register()
    # A character prefab here carries renderers with no mesh in them: the geometry
    # is listed beside the rig and attached at run time. The host's ONE prefab path
    # asks whoever owns the prefab for the missing mesh; this is that answer, and a
    # prefab keeping no such list simply declines.
    prefab_importer.register_mesh_resolver(mesh_resolver.provide)


def _unregister():
    prefab_importer.unregister_mesh_resolver(mesh_resolver.provide)
    mesh_resolver.forget()
    scene_panel.unregister()
    roster_panel.unregister()


GAME_MODULE = GameModule(
    # The productName this game's player builds under, as its own app.info states it --
    # the same string the upstream decoder declares, so the join is equality.
    game_name="EXILIUM",
    label="Girls' Frontline 2",
    tabs=(
        GameTab("scene", "Scene",
                "Every scene the game ships, under the path its own catalog states",
                scene_panel.draw_scene_tab),
        GameTab("character", "Character",
                "The cast the game lets you field, and every model any of them is built from",
                roster_panel.draw_roster),
    ),
    register=_register,
    unregister=_unregister,
)
