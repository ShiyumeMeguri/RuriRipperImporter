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

A hook is not the only way to name a game. A module also declares
``project_names``: the identities the BUILD itself carries -- its Unity
``productName``/``companyName``, read off the install by
``RuriRipperPyBridge.unity.build_identity``. Pointing the panel at an install IS
saying which game it is, so that join is what actually selects a game, and it
needs no hook ticked to work.

A BROWSER TAB IS AN INSTALL, NOT A GAME. The panel keeps a game root, a cabmap
and a browser session per install key (the product name the build carries -- see
``cabmap_state.GameSession``) and lets the user pick which one the browser is
currently on; several can be open at once, including two copies of one title. A
tab's ``game_name`` is what THIS registry answers about its folder, and that is
what selects its tabs, its face system and its retarget tables. The upstream
still decodes ONE game at a time -- game hooks are mutually exclusive there (they
patch the same methods with different layouts, see ``RuriHook.ApplyHooks``) -- so
switching tabs re-selects the decoder (``pythonnet_bridge.use_session``).
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

    __slots__ = ("game_name", "label", "tabs", "project_names", "face_retarget",
                 "_register", "_unregister")

    def __init__(self, game_name, label, tabs, register, unregister, project_names=(),
                 face_retarget=None):
        self.game_name = game_name          # the upstream GameType member, e.g. "EndField"
        self.label = label
        # How this game states a face, if it states one at all. A clip whose facial
        # animation is baked into its bone tracks means nothing on another character's
        # rig, so the ONE clip-loading path asks the game that owns the clip to restate
        # it (see cross_game_retarget.load_clips_onto). A game with no facial system
        # simply declares none and that path stays untouched.
        #
        # The callable takes (context, armature, clip, options, into) and returns a
        # one-line report, or None when it had nothing to do. ``clip`` is anchored to the
        # rig it was AUTHORED on, not to the one it is being played on -- the host resolves
        # and loads that skeleton first, because reading a performance means asking where a
        # bone was relative to ITS OWN rest. ``into`` is that clip's own (action, slot) to
        # write the face INTO, or None for a caller with no action of its own: an object
        # plays one action, so a face given its own would replace the body it came with.
        self.face_retarget = face_retarget
        self.tabs = tuple(tabs)
        # The Unity productName/companyName values this game's players build
        # under -- one project can ship several (a game, its VR build, its
        # studio), and any of them identifies it.
        self.project_names = frozenset(name.lower() for name in project_names)
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
    ``AR_ShaderDecompiler_`` -> ``AR_ShaderDecompiler``. Derived from the same
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


def face_retarget_of(game_name):
    """The facial restatement ONE game contributes, or None. What the clip-loading
    path asks before it decides whether a face can travel between characters -- the
    host never learns which games have faces, only whether this one answered."""
    for game in _MODULES:
        if game.game_name == game_name:
            return game.face_retarget
    return None


def tabs_of(game_name):
    """The tabs ONE game contributes. Several installs are open at once, each its own
    browser tab, so a panel draws the tabs of the game the CURRENT tab's install is
    -- never the union, which would show two games' content tabs side by side."""
    for game in _MODULES:
        if game.game_name == game_name:
            return list(game.tabs)
    return []


def recognised_game(project_names):
    """The game an install IS, from the identity its own build carries -- or None
    when nothing matches, or when more than one module claims it.

    This is what selects a game now. Nothing is pre-ticked on a guess: a hook is
    enabled because the folder in front of the panel is that game."""
    identities = {str(name).lower() for name in project_names}
    matched = [game for game in _MODULES if game.project_names & identities]
    return matched[0] if len(matched) == 1 else None


def register():
    for game in discover():
        game.register()


def unregister():
    global _MODULES
    for game in reversed(_MODULES):
        game.unregister()
    _MODULES = []
