"""Where a game's generated shader stack lands, per host.

``Ruri.RenderPipelines.Generator`` compiles one recipe per target and writes its
output into this add-on. What it writes differs completely by target -- Blender
gets node-group ``.blend`` libraries plus an inlined-manifest runtime module,
Painter gets GLSL plus a projection manifest -- but WHOSE it is does not: both
are the same game's shading, projected onto a different host.

So the layout is ``Game/<game>/shader/<host>/`` and the join needs no table:
:attr:`Kernel.host.Host.name` IS the folder name. A host asks for the stacks
projected onto it; a game with no recipe for that host simply has no folder, and
the host says so instead of falling back to something that is not the game's
shading.

Nothing here names a game, and nothing here names a host.
"""

from __future__ import annotations

import os

from . import host as host_port

_GAMES_DIR_NAME = "Game"
_STACK_DIR_NAME = "shader"
_MANIFEST_SUFFIX = ".manifest.json"


def _package_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Stack:
    """One game's shading, as this host received it."""

    __slots__ = ("game", "host", "directory")

    def __init__(self, game, host, directory):
        self.game = game
        self.host = host
        self.directory = directory

    def manifest_path(self):
        """The projection manifest the generator wrote beside the shader -- the
        contract that says which source texture channels were split into which
        host inputs by which named operation.

        Exactly one per stack. Two would mean two contracts for one shader and
        no honest way to pick, so that is an error rather than a first match."""
        found = sorted(name for name in os.listdir(self.directory)
                       if name.endswith(_MANIFEST_SUFFIX))
        if len(found) != 1:
            raise RuntimeError(
                "{0} shader stack for {1} holds {2} projection manifests ({3}) -- "
                "re-run the generator for this target".format(
                    self.game, self.host, len(found), ", ".join(found) or "none"))
        return os.path.join(self.directory, found[0])

    def shader_name(self):
        """The generated shader's own name, read off the manifest's filename --
        the generator names both after the recipe, so there is nothing to keep
        in step by hand."""
        return os.path.basename(self.manifest_path())[:-len(_MANIFEST_SUFFIX)]

    def asset(self, filename):
        """A companion file shipped inside the stack (an environment map, a
        grading LUT). Missing is an error: the shader documents these as
        requirements, and a stack without them is an incomplete generator run."""
        path = os.path.join(self.directory, filename)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                "{0}'s {1} shader stack is missing {2} -- re-run the generator "
                "for this target".format(self.game, self.host, filename))
        return path

    def __repr__(self):
        return "<Stack {0}/{1}>".format(self.game, self.host)


def stacks(host_name=None):
    """Every game stack projected onto this host, by game name."""
    if host_name is None:
        host_name = host_port.current().name
    games_dir = os.path.join(_package_root(), _GAMES_DIR_NAME)
    found = {}
    for game in sorted(os.listdir(games_dir)):
        directory = os.path.join(games_dir, game, _STACK_DIR_NAME, host_name)
        if os.path.isdir(directory):
            found[game] = Stack(game, host_name, directory)
    return found


def stack_for(game, host_name=None):
    """One game's stack, or None when the generator ships none for this host."""
    return stacks(host_name).get(game)


def require(game, host_name=None):
    if host_name is None:
        host_name = host_port.current().name
    found = stack_for(game, host_name)
    if found is None:
        available = sorted(stacks(host_name))
        raise RuntimeError(
            "no {0} shader stack for {1}. Generated stacks here: {2}. Add a recipe whose "
            "destination is Game/{1}/shader/{0} and re-run "
            "Ruri.RenderPipelines.Generator.".format(
                host_name, game, ", ".join(available) or "none"))
    return found
