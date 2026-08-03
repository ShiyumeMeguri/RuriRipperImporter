"""One folder per hooked game, holding every panel and importer that is about
that game and nothing else.

A game module is DATA: it declares which upstream game it belongs to and which
tabs it contributes, and the core panel reads that off this registry. No module
outside these folders names a game, so adding a title is a new folder here plus
zero edits anywhere else -- and removing one is deleting a folder.

The join to the upstream hook set is exact rather than conventional. A hook id is
``{GameName}_{Version}`` (``Ruri.Hook.RuriHook.BuildHookId``, e.g.
``EndField_1.4.4``), so the game a hook belongs to is everything before its last
underscore, and a module's ``game_name`` is that same ``GameType`` member. A
module's tabs are therefore visible exactly while one of its game's hooks is
enabled -- every version of it, with no per-version list to maintain.
"""

from __future__ import annotations

import importlib
import pkgutil

# What a subpackage must expose to be a game module at all.
_DECLARATION = "GAME_MODULE"

_MODULES = []


class GameTab:
    """One tab a game contributes to the host panel. ``draw(layout, context)``
    is handed the panel's own already-gated layout."""

    __slots__ = ("id", "label", "description", "draw", "owner")

    def __init__(self, tab_id, label, description, draw):
        self.id = tab_id
        self.label = label
        self.description = description
        self.draw = draw
        self.owner = None

    @property
    def key(self):
        """The identifier the host stores and compares. Qualified by the owning
        game, so two games can both ship a "scene" tab without coordinating."""
        return "{0}:{1}".format(self.owner.game_name if self.owner else "", self.id)

    def __repr__(self):
        return "<GameTab {0}>".format(self.key)


class GameModule:
    """One game's whole contribution: its upstream identity, the tabs it adds,
    and the register/unregister pair for the bpy classes those tabs need."""

    __slots__ = ("game_name", "label", "tabs", "default_hook_id",
                 "_register", "_unregister")

    def __init__(self, game_name, label, tabs, register, unregister, default_hook_id=""):
        self.game_name = game_name          # the upstream GameType member, e.g. "EndField"
        self.label = label
        self.tabs = tuple(tabs)
        self.default_hook_id = default_hook_id
        self._register = register
        self._unregister = unregister
        for tab in self.tabs:
            tab.owner = self

    def register(self):
        self._register()

    def unregister(self):
        self._unregister()

    def __repr__(self):
        return "<GameModule {0} ({1} tab(s))>".format(self.game_name, len(self.tabs))


def game_of(hook_id):
    """The game a hook id belongs to -- ``EndField_1.4.4`` -> ``EndField``,
    ``AR_HumanoidToGeneric_`` -> ``AR_HumanoidToGeneric``. Derived from the same
    ``{GameName}_{Version}`` rule the DLL builds its ids with, so no mapping
    table exists to drift away from the ids it actually reports."""
    return (hook_id or "").rsplit("_", 1)[0]


def discover():
    """Import every subpackage here and collect the GAME_MODULE it declares.
    A subpackage declaring none simply is not a game module."""
    global _MODULES
    found = []
    for entry in pkgutil.iter_modules(__path__):
        if not entry.ispkg:
            continue
        package = importlib.import_module("{0}.{1}".format(__name__, entry.name))
        declared = getattr(package, _DECLARATION, None)
        if isinstance(declared, GameModule):
            found.append(declared)
    found.sort(key=lambda game: game.game_name.lower())
    _MODULES = found
    return _MODULES


def modules():
    return list(_MODULES)


def all_tabs():
    return [tab for game in _MODULES for tab in game.tabs]


def tab_by_key(key):
    """Any declared tab by its stored key, enabled or not -- what a tooltip
    needs, which must read the same whether or not the hook is ticked."""
    return next((tab for tab in all_tabs() if tab.key == key), None)


def active_modules(hook_ids):
    """The modules whose game has at least one of its hooks enabled."""
    enabled = {game_of(hook_id).lower() for hook_id in hook_ids}
    return [game for game in _MODULES if game.game_name.lower() in enabled]


def active_tabs(hook_ids):
    return [tab for game in active_modules(hook_ids) for tab in game.tabs]


def default_hook_ids():
    """Hook ids the modules ask to be pre-ticked the first time one appears in
    the DLL's own listing."""
    return {game.default_hook_id for game in _MODULES if game.default_hook_id}


def register():
    for game in discover():
        game.register()


def unregister():
    global _MODULES
    for game in reversed(_MODULES):
        game.unregister()
    _MODULES = []
