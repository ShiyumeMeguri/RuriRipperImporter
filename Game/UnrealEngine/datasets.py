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
