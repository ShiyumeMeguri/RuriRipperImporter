"""RuriRipperImporter -- one add-on, one plugin, one checkout.

Reads a game install straight through an in-process pythonnet bridge into
Ruri.RipperHook: a cabmap selection resolves its own dependency closure inside
the running application, and the Unity documents and texture bytes come back IN
MEMORY, with no extraction step and no folder of intermediates on the way. An
already-extracted Unity YAML folder on disk works too.

THE SAME FOLDER IS BOTH PLUGINS. Blender loads it out of ``scripts/addons`` and
calls :func:`register`; Substance Painter loads it out of ``python/plugins`` and
calls :func:`start_plugin`. Which application this is gets read off the
interpreter (``Host.detect``), and only that application's driver is ever
imported -- so the half of the tree the other host owns costs this one nothing,
not even an import.

The layering, outermost first:

``Host/<name>/``        one driver per application. The ONLY place that
                        application's API appears.
``Kernel/``             the host-neutral application core: the import option
                        schema, the shader-stack projection, the bring-up
                        sequence. Zero host imports, checkable by grep.
``RuriRipperPyBridge/`` the data layer, a shared submodule: the CLR bridge,
                        Unity/Unreal decoding, the cabmap session. Zero host
                        imports and zero knowledge of any one game.
``Game/<game>/``        one folder per hooked game: its datasets and its panels,
                        and nothing about it anywhere else.
"""

bl_info = {
    "name": "RuriRipperImporter",
    "author": "ShiyumeMeguri",
    "version": (3, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > Unity Asset, and 3D Viewport > N-panel > RuriRipper",
    "description": "Import a game install's models, materials, textures and animation "
                   "directly through an in-process bridge into Ruri.RipperHook -- "
                   "cabmap-resolved, with zero intermediate files. The same package is "
                   "also the Substance Painter plugin.",
    "category": "Import-Export",
}

from . import Host

_DRIVER = Host.load()


# ---------------------------------------------------------------------------
# Blender's add-on lifecycle
# ---------------------------------------------------------------------------
def register():
    _DRIVER.reload_modules()
    _DRIVER.register()


def unregister():
    _DRIVER.unregister()


# ---------------------------------------------------------------------------
# Substance Painter's plugin lifecycle
# ---------------------------------------------------------------------------
def start_plugin():
    _DRIVER.start_plugin()


def close_plugin():
    _DRIVER.close_plugin()


def reload_plugin():
    """Painter calls this between close_plugin() and start_plugin()."""
    _DRIVER.reload_modules()
