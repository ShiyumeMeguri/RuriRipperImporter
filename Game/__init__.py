"""One folder per hooked game, holding every panel and importer that is about
that game and nothing else.

A game module is DATA: it declares which game it is and which tabs it contributes,
and the core panel reads that off this registry. No module outside these folders
names a game, so adding a title is a new folder here plus zero edits anywhere else
-- and removing one is deleting a folder.

The join to an install is EXACT and needs no table. A module's ``game_name`` is the
Unity productName the game's own player carries (the second line of its
``<Product>_Data/app.info``, which is PlayerSettings.productName), spelled exactly
as the build spells it -- the same string the upstream decoder declares as its
GameName and the same one its hook id starts with. So "which module is this
install" is a dictionary lookup on the identity the folder itself published, and
there is no mapping table, no alias list and no fuzzy match anywhere in the chain.

A BROWSER TAB IS AN INSTALL, NOT A GAME. The panel keeps a game root, a cabmap and
a browser session per install key (the product name the build carries -- see
``cabmap_state.GameSession``) and lets the user pick which one the browser is
currently on; several can be open at once, including two copies of one title. A
tab's game is what THIS registry answers about its product, and that is what
selects its tabs, its face system and its retarget tables. The upstream still
decodes ONE game at a time, so switching tabs re-selects the decoder
(``pythonnet_bridge.use_session``).
"""

from __future__ import annotations

import importlib
import os
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
    """One game's whole contribution: its identity, the tabs it adds, and the
    register/unregister pair for the bpy classes those tabs need."""

    __slots__ = ("game_name", "label", "tabs", "face_retarget", "secondary_motion",
                 "engine", "source_options", "settings_schema", "directory", "_register",
                 "_unregister")

    def __init__(self, game_name, label, tabs, register, unregister, face_retarget=None,
                 secondary_motion=None, engine=None, source_options=None, settings_schema=None):
        # The Unity productName this game's player builds under -- the install's own
        # word for itself, and the upstream decoder's GameName. Nothing translates it.
        self.game_name = game_name
        self.label = label
        # The engine FAMILY this module claims, or None. A build on an engine other
        # than Unity publishes no productName a folder here could be named after, so
        # such a module is joined to an install by the family the kernel's probe
        # reports ("UnrealEngine") -- the same string the family-wide decoder declares
        # as its GameName, which is also this module's game_name. A product-named
        # module always wins over a family one (module_for asks the product first).
        self.engine = engine
        # How this module states the values its install is READ with beyond its
        # folder, if it states any: ``draw(layout, context, config)`` painting the
        # form onto the host's identity block, ABOVE the cabmap gate -- an archive
        # key is needed to build the map at all. What the form contains comes from
        # the decoder's own published schema; the host core only offers the slot.
        self.source_options = source_options
        # The dataset that publishes that form's schema -- which options the decoder reads,
        # and which of them the mounted build cannot be read without. Stated as data so the
        # host can load the form itself the moment a tab's decoder resolves, and keep a
        # warning up for every required option still unset, without naming any option.
        self.settings_schema = settings_schema
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
        # How this game states the secondary motion its models carry -- the hair, cloth
        # and accessory chains an author tuned on the model itself. Only a game that
        # ships those settings WITH the model can answer, so the option that imports
        # them appears exactly where one does and nowhere else.
        #
        # The callable takes (context, armature, cabs, report) and writes onto whatever
        # cloth add-on is present, returning the report it was handed.
        self.secondary_motion = secondary_motion
        self.tabs = tuple(tabs)
        # The folder the module was declared in -- where the data files that are about
        # this game and nothing else live (its texture role layer, ...). Filled in by
        # discover(), which is the one place that knows which package declared what.
        self.directory = None
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


def discover():
    """Import every subpackage here and collect the GAME_MODULE(s) it declares.

    A subpackage declaring none simply is not a game module. It may declare SEVERAL:
    a family of titles that are the same game rebuilt shares one panel, and stating
    them as four modules out of one folder is what keeps that folder free of
    per-title code while each title still gets its own identity, tabs and session."""
    global _MODULES
    found = []
    for entry in pkgutil.iter_modules(__path__):
        if not entry.ispkg:
            continue
        package = importlib.import_module("{0}.{1}".format(__name__, entry.name))
        declared = getattr(package, _DECLARATION, None)
        for module in (declared if isinstance(declared, (list, tuple)) else [declared]):
            if isinstance(module, GameModule):
                module.directory = os.path.dirname(os.path.abspath(package.__file__))
                found.append(module)
    found.sort(key=lambda game: game.game_name.lower())
    _MODULES = found
    return _MODULES


def modules():
    return list(_MODULES)


def all_tabs():
    return [tab for game in _MODULES for tab in game.tabs]


def tab_by_key(key):
    """Any declared tab by its stored key, enabled or not -- what a tooltip
    needs, which must read the same whether or not that game is in front of the
    panel."""
    return next((tab for tab in all_tabs() if tab.key == key), None)


def module_for(product, engine=""):
    """The module that IS ``product``, else the one claiming the install's ``engine``
    family, else None when this add-on ships no panels for it. Exact, case-insensitive
    matches on what the install published: a game with no module is still a perfectly
    good install to browse."""
    wanted = (product or "").lower()
    by_product = next((game for game in _MODULES if game.game_name.lower() == wanted), None)
    if by_product is not None:
        return by_product
    family = (engine or "").lower()
    if not family:
        return None
    return next((game for game in _MODULES
                 if game.engine is not None and game.engine.lower() == family), None)


def face_retarget_of(game_name):
    """The facial restatement ONE game contributes, or None. What the clip-loading
    path asks before it decides whether a face can travel between characters -- the
    host never learns which games have faces, only whether this one answered."""
    game = module_for(game_name)
    return game.face_retarget if game is not None else None


def secondary_motion_of(game_name):
    """The secondary-motion reader ONE game contributes, or None. The import path asks
    before it offers to bring a model's own hair and cloth settings across; a game that
    ships none simply never answers and the option never appears."""
    game = module_for(game_name)
    return game.secondary_motion if game is not None else None


def tabs_of(game_name, engine=""):
    """The tabs ONE game contributes. Several installs are open at once, each its own
    browser tab, so a panel draws the tabs of the game the CURRENT tab's install is
    -- never the union, which would show two games' content tabs side by side."""
    game = module_for(game_name, engine)
    return list(game.tabs) if game is not None else []


def register():
    for game in discover():
        game.register()


def unregister():
    global _MODULES
    for game in reversed(_MODULES):
        game.unregister()
    _MODULES = []
