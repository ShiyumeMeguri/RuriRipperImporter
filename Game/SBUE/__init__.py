"""Unreal Engine -- every install built on that engine, claimed by the ENGINE FAMILY the
kernel's probe reports rather than by a product name: an Unreal build publishes no
Unity productName a folder here could be named after, and one decoder reads every
title of the engine (``Ruri.RipperHook.Unreal.UnrealEngine_Hook``), so one module
draws every title's panels. A title that ships its own decoder and panels claims
its install by product first and this module never sees it.

Two contributions, neither of which any Unity game needs:

``settings_schema`` WHICH dataset states the values the install is READ with beyond
                    its folder -- the engine version, AES keys, the .usmap reflection
                    schema, the texture platform, the versioning overrides FModel
                    keeps per game. The host panel draws that schema ABOVE the cabmap
                    gate, because an archive key is what makes building the map
                    possible at all; naming the dataset is all this module does.
``Scene`` tab       the levels the install ships: the self-contained ones whole, the
                    partitioned ones a streamed window at a time.
``Characters`` tab  the characters the install ships, picked by name and imported whole.

Declared as one GAME_MODULE row (see ``Game``).
"""

from __future__ import annotations

import importlib

from .. import GameModule, GameSection, GameTab
from . import datasets

#: Not one of this engine's panels asks the host for anything the browser does not
#: already ask for. A CHARACTER is an object and a LEVEL is a tree of them, and what
#: the decoder hands over for either is the same normalised placement statement --
#: which every host's builder takes, each in its own idiom: objects parented to
#: each other where there is a scene, one glTF whose nodes share their meshes where
#: the project IS a file.
SECTIONS = (GameSection("characters"), GameSection("levels"))

_LOADED = []


def _read_shaders(packages, output):
    """What the selected assets compiled to. Nothing is decided here: the decoder takes
    the packages as named and answers per archive it had to open."""
    from . import datasets
    return datasets.shaders(packages, output)


def _import_packages(context, packages, options):
    """Every package built from what the decoder states it holds.

    An Unreal build is read directly: placements, geometry, materials, texture pixels and
    animation curves all cross as data, so nothing here creates a Unity asset or parses text.

    Reading is this module's (``read.package``); BUILDING is the host's one import
    entry. That split is the whole reason an Unreal actor can reach a second host at
    all -- what comes out of the decoder is already the normalised forms both
    builders take.
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
    # the engine, and the family string its install probe reports.
    game_name="UnrealEngine",
    label="Unreal Engine",
    engine="UnrealEngine",
    sections=SECTIONS,
    tabs=(
        GameTab("scene", "Scene",
                "The levels the install ships: the self-contained ones imported whole, the "
                "partitioned worlds a streamed window at a time",
                ("levels", "draw_scene_tab")),
        GameTab("characters", "Characters",
                "Every character the install ships, picked by name and imported whole",
                ("characters", "draw")),
    ),
    importer=_import_packages,
    shaders=lambda packages, output: _read_shaders(packages, output),
    settings_schema=datasets.SETTINGS_SCHEMA,
    register=_register,
    unregister=_unregister,
)
