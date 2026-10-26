"""The Substance Painter driver: this application's answers to
:class:`Kernel.host.Host`, its plugin lifecycle, and the only place
``substance_painter`` and Qt are allowed to appear.

Painter's entry point is a mesh FILE -- ``project.create`` takes a path and
there is no in-memory geometry API -- so an import here decodes the model and
writes one self-contained .glb, then splits every source texture into Painter's
engine channels and wires the generated shader onto each Texture Set. Nothing
else is written to disk: the dependency closure is resolved in-process and comes
back as Unity documents and texture bytes in memory, exactly as it does in
Blender.

WHAT THE DOCK SHOWS is not decided here and not written here: it is the kernel's
panel description, the same one Blender renders into its N-panel, drawn through
the Qt renderer next door. This module starts it, gives the kernel somewhere to
keep panel state, and puts the dock where the stock panels live.
"""

from __future__ import annotations

import importlib
import sys

import substance_painter.logging
import substance_painter.ui

from ...Kernel import bootstrap as kernel_bootstrap
from ...Kernel import host as host_port

ADDON = __package__.split(".")[0]

#: Panel state by the name it was filed under. Painter's answer to what Blender
#: keeps on the Scene; both are reached the same way (Host.panel_state).
_STATES = {}

#: What to call when the kernel says the screen is out of date. The dock adds
#: itself here; nothing else in the driver knows a dock exists.
_REPAINT = []

_BIN_DIR_HINT = "Set it in the RuriRipper panel's 'RipperHook bin' field."


class SubstanceHost(host_port.Host, host_port.TextureCache, host_port.TextureSets,
                    host_port.DisplaySettings):
    """What Painter can do, and where Painter keeps what the user typed.

    The three below are the ones something actually asks about: its texture
    inputs are files on disk (so a bake cache, and a way to invalidate it, mean
    something), it groups surfaces into fixed-resolution texture sets, and its
    viewport display is settable by the plugin -- which the ported shader
    depends on, since it applies the game's tonemap itself."""

    name = "Substance"

    #: The reader's word for the basis a glTF file -- what Painter opens -- is written in.
    basis = "gltf"

    def log(self, level, message):
        writer = (substance_painter.logging.error if level == host_port.ERROR
                  else substance_painter.logging.warning if level == host_port.WARNING
                  else substance_painter.logging.info)
        writer("[RuriRipper] {0}".format(message))

    def workspace_dir(self):
        return settings.workspace_dir()

    def preset_dir(self):
        """None: Painter's presets are shelf resources it indexes itself, not a
        folder a plugin may drop json into. The workspace already sits with the
        user profile, so it is the home here."""
        return None

    def bin_dir(self):
        return settings.get("ripperhook_bin", "")

    def bin_dir_hint(self):
        return _BIN_DIR_HINT

    def texture_containers(self):
        """Painter decodes through Qt, which ships no tga/exr codec. Declaring
        this makes the bridge convert exactly the textures Qt cannot read and
        hand every other one over byte-identical to the game's own container."""
        return ("png", "jpeg", "bmp")

    def locale(self):
        """Painter publishes no UI language to plugins, so this is the system's
        -- which is what Painter itself was started under."""
        from PySide6.QtCore import QLocale
        return QLocale.system().name()

    def absolute_path(self, path):
        import os
        return os.path.abspath(path) if path else ""

    def schedule(self, seconds, call):
        from PySide6 import QtCore

        def fire():
            again = call()
            if again is not None:
                self.schedule(again, call)

        QtCore.QTimer.singleShot(int(seconds * 1000), fire)

    def redraw(self):
        """Qt is retained-mode, so "repaint" means rebuilding the dock from the
        description that changed. The panel owns that; this is how the kernel
        asks for it without knowing there is a panel."""
        for repaint in list(_REPAINT):
            repaint()

    def register_state(self, name, schema, handlers, extra=None):
        """A plain value bag. Painter has no property system to hang this on, and
        nothing to gain from pretending otherwise -- what matters is that a panel
        body cannot tell which kind it is holding.

        A SUBCLASS per schema, because ``extra`` is class-level: a panel's own
        filter spec key and its ``as_options`` are answers about THAT panel, and
        setting them on the shared Bag made the last state registered answer for
        every other one. Blender's side has always made a type per schema; this
        is the same statement.

        It is also where the panel's MEMORY is wired up. Blender's state is RNA
        on the Scene, so which installs are open comes back with the .blend and
        its driver has nothing to do; this one is a plain object that dies with
        the process, so the fields the schema marks remembered are read back out
        of the settings file here and written to it whenever they change. WHICH
        fields those are is not decided here -- that is a statement about the
        value, made once beside it (``state.Field.remembered``)."""
        from ...Kernel.app import bag as app_bag
        kind = type(schema.name + "Bag", (app_bag.Bag,), dict(extra or {}))
        made = kind(schema, handlers,
                    changed=lambda: settings.remember_panel(
                        name, app_bag.remembered_values(made)))
        app_bag.restore(made, settings.recalled_panel(name))
        _STATES[name] = made
        return made

    def unregister_state(self, name):
        _STATES.pop(name, None)

    def panel_state(self, context, name):
        found = _STATES.get(name)
        if found is None:
            raise KeyError(
                "no panel state registered under {0!r} -- a panel reaching for one "
                "it never declared is a panel drawing someone else's".format(name))
        return found

    def materialise(self, context, statement, options, report=None):
        from . import materialise as materialiser
        return materialiser.materialise(context, statement, dict(options or {}),
                                        report if report is not None else [])


#: Bound BEFORE the modules below are imported: the settings store's own key set
#: is the import-option schema filtered by THIS host's capabilities, which is a
#: question with no answer until a host is bound.
HOST = host_port.bind(SubstanceHost())

from . import settings                                             # noqa: E402

# The workspace has to be known, and the private runtime folder on sys.path,
# before anything that imports numpy -- which the importer transitively does.
kernel_bootstrap.configure()

from . import dock, render, shelf_sync                             # noqa: E402

#: Modules whose globals track real, expensive or unrepeatable process state: a
#: CoreCLR runtime that can never be re-claimed once set, an installed runtime
#: folder, a multi-second cabmap load, discovered placements. Reloading one
#: would reset its globals to source defaults while what it tracks is still very
#: much alive. Each says so itself (HOLDS_PROCESS_STATE); nothing lists them.
_plugin_widgets = []
_panel = None


def _holds_process_state(module):
    return bool(getattr(module, "HOLDS_PROCESS_STATE", False))


def _reload_tree(root_name):
    for name, module in list(sys.modules.items()):
        if ((name == root_name or name.startswith(root_name + "."))
                and not _holds_process_state(module)):
            importlib.reload(module)


def reload_modules():
    """Painter calls this between close_plugin() and start_plugin(), so an edit
    takes effect without restarting the application.

    Bottom-up, and this module last of its own tree -- the driver body re-binds
    against modules that are already new, and reloading it mutates it in place,
    which is why the caller's reference to it stays valid."""
    _reload_tree(ADDON + ".Kernel")
    _reload_tree(ADDON + ".Kernel")
    for name in ("settings", "render", "gltf_writer", "model_builder", "unity_material",
                 "manifest_plan", "texture_pipeline", "shelf_sync", "sp_apply", "importer",
                 "packages", "shader", "dock"):
        module = sys.modules.get(__name__ + "." + name)
        if module is not None:
            importlib.reload(module)
    importlib.reload(sys.modules[__name__])


# ---------------------------------------------------------------------------
# Painter's plugin lifecycle
# ---------------------------------------------------------------------------
def start_plugin():
    """Bring up the kernel's panel in this application.

    The order is the same one Blender's register() runs, for the same reasons:
    the browser files its state before anything reads it, the games declare
    their tabs and commands next, and the surfaces the descriptions name are
    registered last -- a popover or a menu is addressed by an id, and the
    description is the only thing that knows those ids exist."""
    global _panel
    from ...Kernel.app import browser, look
    from ... import Game
    from . import display_panel
    shelf_sync.start()
    browser.register()
    # This host's own answer to "what does the frame finally look like": its
    # display is a fixed set of choices rather than a graph, and the ported shader
    # states requirements for exactly those. Registered before the games, because
    # a game's Look tab draws whatever the host registered.
    display_panel.register()
    look.register_section("display", "Display", display_panel.draw,
                          host_port.DisplaySettings)
    Game.register()
    _panel = dock.RuriRipperDock()
    # The kernel asks for a repaint without knowing there is a panel; this is
    # what a repaint IS here, because Qt is retained-mode.
    _REPAINT.append(_panel.rebuild)
    placed = substance_painter.ui.add_dock_widget(_panel)
    _plugin_widgets.append(_panel)
    _dock_into_side_strip(placed)
    HOST.log(host_port.INFO,
             "loaded -- the RuriRipper dock is in the right-hand panel strip "
             "(its 'R' button reopens it if closed).")


def close_plugin():
    global _panel
    from ...Kernel.app import browser, look
    from ... import Game
    from . import display_panel
    _REPAINT.clear()
    for widget in _plugin_widgets:
        substance_painter.ui.delete_ui_element(widget)
    _plugin_widgets.clear()
    _panel = None
    Game.unregister()
    look.drop_section("display")
    display_panel.unregister()
    browser.unregister()
    shelf_sync.stop()


def _dock_into_side_strip(placed):
    """Put the dock where the stock panels live: tabbed into the right-hand
    strip, next to Shader Settings and friends.

    Only the first time THIS dock name is seen. Painter persists each dock's
    area and geometry by objectName, so forcing the position on every start
    would undo the user's own arrangement -- but a record that says only "placed"
    outlives the name it was true of, and the panel then comes up floating with
    nothing to restore it. Recording the name is self-invalidating."""
    if placed is None or settings.get("dock_placed_for", "") == placed.objectName():
        return
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDockWidget
    try:
        main_window = substance_painter.ui.get_main_window()
        neighbours = [d for d in main_window.findChildren(QDockWidget)
                      if d is not placed and not d.isFloating() and d.isVisible()
                      and main_window.dockWidgetArea(d) == Qt.RightDockWidgetArea]
        main_window.addDockWidget(Qt.RightDockWidgetArea, placed)
        if neighbours:
            # Tabify onto the existing right-hand group rather than stealing
            # vertical space from it.
            main_window.tabifyDockWidget(neighbours[-1], placed)
        placed.show()
        placed.raise_()
        settings.set_many(dock_placed_for=placed.objectName())
    except Exception as exc:
        HOST.log(host_port.WARNING,
                 "could not place the dock automatically ({0}) -- drag it where you want "
                 "it and Painter will remember.".format(exc))
