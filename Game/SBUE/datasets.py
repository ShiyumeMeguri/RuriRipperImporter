"""What the Unreal decoder publishes, and the shapes this module's panel reads it in.

Every value here is a dataset id the hook registers (``Ruri.RipperHook.Unreal.
UnrealDatasets``), reached through the one entry point ``bridge.game_data(id)``.
Nothing here parses anything and nothing here names a game: the decoder reads any
Unreal build, and what it says about the mounted one arrives columnar.
"""

from __future__ import annotations

from ...RuriRipperPyBridge.session import cabmap_state

SETTINGS_SCHEMA = "unreal.settings.schema"
SESSION = "unreal.session"
ARCHIVES = "unreal.archives"
WORLDS = "unreal.worlds"
WORLD_CELLS = "unreal.world.cells"
CHARACTERS = "unreal.characters"
SHADERS = "unreal.shaders"


def _rows(dataset_id, **args):
    table = cabmap_state.BRIDGE.game_data(dataset_id, **args)
    return [{name: table.cell(index, name) for name in table.names}
            for index in range(len(table))]


def session():
    """The mounted session as one dict, or None before a bridge exists."""
    if cabmap_state.BRIDGE is None:
        return None
    rows = _rows(SESSION)
    return rows[0] if rows else None


def archives():
    """Every archive the install ships, mounted or still waiting for a key."""
    if cabmap_state.BRIDGE is None:
        return []
    return _rows(ARCHIVES)


def characters():
    """Every character model the install ships: the package's own name, the package, and the
    folder it sits in. Read off the cabmap, so it answers for a build that publishes no
    reflection schema."""
    if cabmap_state.BRIDGE is None:
        return []
    return _rows(CHARACTERS)


def shaders(package, output):
    """Decompile every shader variant the materials of one package compiled to, into
    ``output``. One row per archive that carried them. Unreal ships no shader asset --
    a material's program is blobs in a shared archive -- so what lands on disk is the
    vertex and pixel stages as source, one file per variant."""
    if cabmap_state.BRIDGE is None or not package or not output:
        return []
    return _rows(SHADERS, package=package, output=output)


def worlds():
    """Every world outside a generated folder, with whether it is partitioned and how many cells it lists."""
    if cabmap_state.BRIDGE is None:
        return []
    return _rows(WORLDS)


def world_cells(world, **window):
    """One partitioned world's streaming cells; ``window`` passes the decoder's own
    minX/minY/maxX/maxY (Unreal units) and level arguments through unchanged, so the
    cut is made where the cells are read, not here."""
    if cabmap_state.BRIDGE is None or not world:
        return []
    return _rows(WORLD_CELLS, world=world, **window)
