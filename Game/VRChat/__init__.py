"""VRChat scene projection and online world browser."""

import importlib
import importlib.util
from ...Kernel import host as host_port
from ...Kernel.app import remote_sources
from .. import GameModule, GameTab

_projection = None


def register():
    global _projection
    name = __name__ + "." + host_port.current().name
    if importlib.util.find_spec(name) is not None:
        _projection = importlib.import_module(name)
        _projection.register()


def unregister():
    global _projection
    if _projection is not None:
        _projection.unregister()
        _projection = None


SOURCE_UI = remote_sources.Panel(
    source="vrchat", product="VRChat", decoders=("VRChat",),
    local_dataset="vrchat.worlds", import_on_download=True,
    in_place=True, import_latest=True, local_tab="VRChat:world",
    directory_dataset="vrchat.download.directory",
)


GAME_MODULE = GameModule(
    "VRChat",
    "VRChat",
    (
        GameTab(
            "world",
            "World",
            "Browse and import downloaded worlds",
            SOURCE_UI.local,
            requires=host_port.CommandDispatch, requires_map=False,
        ),
        GameTab(
            "remote",
            "Remote",
            "Import online worlds, reusing a verified current cache or downloading first",
            SOURCE_UI,
            requires=host_port.CommandDispatch, requires_map=False,
        ),
    ),
    register,
    unregister,
    source_ui=SOURCE_UI,
)
