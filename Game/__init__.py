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
import importlib.util
import os
import pkgutil

# What a subpackage must expose to be a game module at all.
_DECLARATION = "GAME_MODULE"

# The generated shading a game folder may carry, as the generator writes it
# (``Kernel.shaderstack``). A stack is per GAME, a module is per PANEL SET, and the two
# do not have to line up: one engine family draws every one of its titles' panels out of
# a single module (see SBUE), while each of those titles shades differently and so ships
# its own stack. So stacks are found by the folder, not by the module -- a folder with a
# stack and no GAME_MODULE is an ordinary case, not an oversight.
_STACK_PACKAGE = "shader"

_MODULES = []
_STACKS = []


class GameTab:
    """One tab a game contributes to the host panel. ``draw(layout, context)``
    is handed the panel's own already-gated layout.

    ``draw`` may instead be ``("module", "function")``, which is imported the
    first time the tab is actually drawn. That is not laziness for its own sake:
    a tab's panel module is where its host classes live, and a host that cannot
    offer the tab must never import it. Endfield's Character tab drives a facial
    expression library onto a rig -- there is no rig in Painter, so importing
    that module there would be importing a panel to then not show.

    ``requires`` is the host protocol the tab needs (``Kernel.host.Rig`` and
    friends), or None for one every host can offer. A host that cannot answer
    does not get the tab AT ALL -- not a disabled one. A control that exists but
    cannot work is worse than one that is honestly missing."""

    __slots__ = ("id", "label", "description", "_draw", "requires", "owner")

    def __init__(self, tab_id, label, description, draw, requires=None):
        self.id = tab_id
        self.label = label
        self.description = description
        self._draw = draw
        self.requires = requires
        self.owner = None

    @property
    def available(self):
        from ..Kernel import host as host_port
        return self.requires is None or self.requires in host_port.current().capabilities

    @property
    def draw(self):
        """The panel body, imported on first use when it was named rather than
        handed over."""
        if isinstance(self._draw, tuple):
            module_name, function_name = self._draw
            module = importlib.import_module(
                "{0}.{1}".format(self.owner.package, module_name))
            self._draw = getattr(module, function_name)
        return self._draw

    @property
    def key(self):
        """The identifier the host stores and compares. Qualified by the owning
        game, so two games can both ship a "scene" tab without coordinating."""
        return "{0}:{1}".format(self.owner.game_name if self.owner else "", self.id)

    def __repr__(self):
        return "<GameTab {0}>".format(self.key)


class GameSection:
    """One PART of a tab: a panel module, and the capability it needs.

    A tab that a host cannot answer for in full loses the SECTION it cannot
    answer for, not the whole tab -- rearranging a layout for the host that
    cannot is how the difference gets built INTO the product. That rule only
    holds if a section is a declared thing: this is the one statement of "which
    parts exist and what each needs", and the three places that used to each
    carry their own copy -- which module to import, which to register, whether to
    draw it -- all ask it instead.

    ``id`` is the panel module's name inside the game package, so the join needs
    no table."""

    __slots__ = ("id", "requires", "owner")

    def __init__(self, section_id, requires=None):
        self.id = section_id
        self.requires = requires
        self.owner = None

    @property
    def available(self):
        from ..Kernel import host as host_port
        return self.requires is None or self.requires in host_port.current().capabilities

    @property
    def key(self):
        """Qualified by the owning game, like a tab's."""
        return "{0}:{1}".format(self.owner.game_name if self.owner else "", self.id)

    def __repr__(self):
        return "<GameSection {0}>".format(self.key)


def section(sections, name):
    """The one section called ``name``. Raises rather than answering "no" for a
    name nobody declared -- a section that is not declared is a typo, and
    silently reading it as unavailable is how a feature disappears quietly."""
    for one in sections:
        if one.id == name:
            return one
    raise KeyError(name)


class GameModule:
    """One game's whole contribution: its identity, the tabs it adds, and the
    register/unregister pair for the bpy classes those tabs need."""

    __slots__ = ("game_name", "label", "tabs", "sections", "face_retarget",
                 "secondary_motion", "shadow_proxies", "engine", "settings_schema",
                 "shaders", "all_shaders", "directory", "package", "_look", "_register",
                 "_unregister")

    def __init__(self, game_name, label, tabs, register, unregister, sections=(),
                 face_retarget=None, secondary_motion=None, shadow_proxies=False, engine=None,
                 settings_schema=None, shaders=None, all_shaders=None, look=None):
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
        # The dataset that publishes the schema of the values this install is READ with
        # beyond its folder -- which options the decoder reads, and which of them the
        # mounted build cannot be read without. Stating the DATASET is the whole of a
        # module's contribution: the form itself -- rows, widgets, apply, the warning
        # for a required option still unset -- is the host panel's, drawn ABOVE the
        # cabmap gate because an archive key is what makes building the map possible
        # at all. So no option name is spelled anywhere on this side, and a decoder
        # that adds one needs no edit.
        self.settings_schema = settings_schema
        # How this game states a face, if it states one at all. A clip whose facial
        # animation is baked into its bone tracks means nothing on another character's
        # rig, so the one clip path (Kernel.app.loading.perform) asks the game that owns
        # the clip to restate it on the rig it is played on. A game with no facial system
        # simply declares none.
        #
        # The callable takes (context, armature, clip, options, into) and returns a
        # one-line report, or None when it had nothing to do. ``clip`` is the clip as it
        # LANDED on ``armature``: its curve paths re-anchored on that rig's bones -- a binding
        # the clip stores only as a hash names no bone until a skeleton is given -- and its
        # values as the author keyed them, so reading a performance still asks where a bone
        # was relative to the rest of the rig it was made on. ``into`` is where that
        # performance landed: an object plays one performance, so a face given its own would
        # replace the body it came with.
        self.face_retarget = face_retarget
        # How this game states the secondary motion its models carry -- the hair, cloth
        # and accessory chains an author tuned on the model itself. Only a game that
        # ships those settings WITH the model can answer, so the option that imports
        # them appears exactly where one does and nowhere else.
        #
        # The callable takes (context, armature, cabs, report) and writes onto whatever
        # cloth add-on is present, returning the report it was handed.
        self.secondary_motion = secondary_motion
        # Whether this game's builds carry shadow proxies: renderers drawn only into the
        # shadow map, standing in for the shadows of renderers that cast none of their
        # own. Only such a game offers keeping them; everywhere else they are dropped.
        self.shadow_proxies = shadow_proxies
        # There is no importer here, deliberately: whatever engine a build is on, a row loads
        # by handing its seed to the ONE load (Kernel.app.loading), and what the seed IS is
        # answered by that game's statement source on the reader side.
        # How this game answers "what did these assets compile to". A build whose engine
        # ships shaders AS assets is answered by the shared reader -- the closure of the
        # rows, every shader in it, decompiled. A build whose engine ships none (a
        # material's program is blobs in an archive shared by thousands) cannot be, so
        # its module says how instead, and no caller learns which kind it is looking at.
        #
        # The callable takes (packages, output) and returns one row per archive written.
        self.shaders = shaders
        # The SAME question with nothing named to narrow it: everything this install ships.
        # A module that answers one answers both, because the two are one lane -- which
        # packages "everything" is stays on the far side, where the map already states it.
        #
        # The callable takes (output) and returns one row per archive written.
        self.all_shaders = all_shaders
        # What this game says about how its frame is finally graded, drawn at the head of the
        # Look tab every install has. A game's post-processing objects and the algorithm that
        # turns them into a grade are its own -- no two titles share either -- so the tab is the
        # kernel's and what is in it is the game's. ``("module", "function")`` names a draw in
        # one of this game's SECTIONS, imported on first use and absent wherever that section's
        # host capability is.
        self._look = look
        self.tabs = tuple(tabs)
        # The parts those tabs are composed of, each with the capability it needs
        # (see GameSection). Stated here so the same join that proves no TAB is
        # missing without a declared reason proves it of the sections -- which is
        # the level the hiding actually happens at.
        #
        # Copied rather than kept: a family of titles is four modules out of one
        # folder sharing ONE declaration, and a row can only name one owner, so
        # rows taken as-is would all end up named after whichever title was built
        # last.
        self.sections = tuple(GameSection(one.id, one.requires) for one in sections)
        # The folder the module was declared in -- where the data files that are about
        # this game and nothing else live (its texture role layer, ...). Filled in by
        # discover(), which is the one place that knows which package declared what.
        self.directory = None
        # The dotted package a lazily-named tab body is imported out of. Filled
        # in by discover(), which is the one place that knows which package
        # declared what.
        self.package = ""
        self._register = register
        self._unregister = unregister
        for tab in self.tabs:
            tab.owner = self
        for one in self.sections:
            one.owner = self

    def look(self):
        """This game's own Look body, or None when it states none or its section is not one this
        host can offer."""
        if self._look is None or callable(self._look):
            return self._look
        module_name, function_name = self._look
        if not section(self.sections, module_name).available:
            return None
        module = importlib.import_module("{0}.{1}".format(self.package, module_name))
        return getattr(module, function_name)

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
    per-title code while each title still gets its own identity, tabs and session.

    The same walk picks up the generated shading each folder carries, for the same
    reason the modules are picked up here: which titles exist is the folder listing,
    and nowhere else names one."""
    global _MODULES, _STACKS
    found = []
    stacks = []
    for entry in pkgutil.iter_modules(__path__):
        if not entry.ispkg:
            continue
        package = importlib.import_module("{0}.{1}".format(__name__, entry.name))
        stack = _host_stack(package)
        if stack is not None:
            stacks.append(stack)
        declared = getattr(package, _DECLARATION, None)
        for module in (declared if isinstance(declared, (list, tuple)) else [declared]):
            if isinstance(module, GameModule):
                module.directory = os.path.dirname(os.path.abspath(package.__file__))
                module.package = package.__name__
                found.append(module)
    found.sort(key=lambda game: game.game_name.lower())
    _MODULES = found
    _STACKS = stacks
    return _MODULES


def _host_stack(package):
    """The generated stack a game folder carries for the host this process runs inside, when
    that projection registers anything. The per-host folder is named after the host, so which
    one is ours needs no table; a projection that ships assets only has nothing to register."""
    from ..Kernel import host as host_port

    folder = "{0}.{1}".format(package.__name__, _STACK_PACKAGE)
    if importlib.util.find_spec(folder) is None:
        return None
    projection = "{0}.{1}".format(folder, host_port.current().name)
    if importlib.util.find_spec(projection) is None:
        return None
    stack = importlib.import_module(projection)
    return stack if hasattr(stack, "register") and hasattr(stack, "unregister") else None


def modules():
    return list(_MODULES)


def stack_modules():
    """The generated stack packages this session registered, one per game folder that ships a projection for the
    host the process runs inside."""
    return list(_STACKS)


def reload_stack(stack):
    """Load one registered stack package again from disk, in place: unregister what the old generation put into
    the host, run the package's source again (its manifest and runtime come off disk as they are now), register
    what the new generation declares. The package object stays the same one, so every holder of it sees the new
    generation. The data the old generation made is not touched here -- dropping it is the caller's step, in
    the old generation's own vocabulary, before this."""
    if stack not in _STACKS:
        raise KeyError("{0} is not a stack package this session registered".format(getattr(stack, "__name__", stack)))
    stack.unregister()
    importlib.reload(stack)
    stack.register()
    return stack


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


def shadow_proxies_of(game_name):
    """Whether ONE game's builds carry shadow proxies. The import panel asks before it
    offers to keep them; a game that states none never shows the option."""
    game = module_for(game_name)
    return game is not None and game.shadow_proxies


def shaders_of(game_name, engine=""):
    """How ONE game answers what its assets compiled to, or None to use the shared reader.
    Asked wherever a selection can be decompiled, so the button is the same button on
    every install and only the answer differs."""
    game = module_for(game_name, engine)
    return game.shaders if game is not None else None


def all_shaders_of(game_name, engine=""):
    """How ONE game answers what its WHOLE install compiled to, or None to use the shared
    reader. The same contribution point as shaders_of with nothing selected to narrow it."""
    game = module_for(game_name, engine)
    return game.all_shaders if game is not None else None


def look_of(game_name, engine=""):
    """The Look body ONE game states about how its frame is graded, or None."""
    game = module_for(game_name, engine)
    return game.look() if game is not None else None


def tabs_of(game_name, engine=""):
    """The tabs ONE game contributes. Several installs are open at once, each its own
    browser tab, so a panel draws the tabs of the game the CURRENT tab's install is
    -- never the union, which would show two games' content tabs side by side."""
    game = module_for(game_name, engine)
    return [tab for tab in game.tabs if tab.available] if game is not None else []


def register():
    for game in discover():
        game.register()
    # 生成的着色栈自己往宿主的注册表里挂 provider,核心一个游戏名都不认识
    # (见 material_builder.GRAPH_PROVIDERS)。挂哪几套按文件夹认,所以一个游戏的栈
    # 上不上场与它有没有面板模块无关 —— 鸣潮的面板是引擎家族那一份画的,栈是它自己的。
    for stack in _STACKS:
        stack.register()


def unregister():
    global _MODULES, _STACKS
    for stack in reversed(_STACKS):
        stack.unregister()
    _STACKS = []
    for game in reversed(_MODULES):
        game.unregister()
    _MODULES = []
