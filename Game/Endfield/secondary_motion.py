"""This game's answer to "bring the model's own secondary motion across".

Two ways in, one implementation, and neither of them is here: the import options
carry it as a switch and the asset browser as a button, both drawn by the core panel
off the capability the option declares. What IS here is the READING -- the core
never learns that these settings exist, only that this game answered.

Writing them onto a rig is the host's (``Host.write_secondary_motion``), because
which solver holds them and what it calls each parameter is a fact about the
application, not about this game. Nothing here imports a host.
"""

from __future__ import annotations

from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry
from . import cloth


def _prefab_texts(cabs):
    mono_behaviour = class_registry.id_for_name("MonoBehaviour")
    assets, _roots, _seeds, _clips, _scenes = cabmap_state.BRIDGE.import_cabs(
        cabs, [mono_behaviour] if mono_behaviour is not None else None)
    paths = cabmap_state.BRIDGE.asset_paths_by_guid
    texts = []
    for guid, blob in assets.items():
        if not str(paths.get(guid, "")).lower().endswith(".prefab"):
            continue
        try:
            texts.append(blob.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return texts


def read(cabs):
    """What those model prefabs state about their secondary motion, in the shared
    vocabulary (:mod:`Kernel.app.rigging`), or None for nothing to bring across.

    This is the callable the game module declares (see ``Game.GameModule``), so both
    ways in reach it without the host ever learning what these settings are -- only
    whether this game answered."""
    if cabmap_state.BRIDGE is None:
        return None
    texts = _prefab_texts(cabs)
    if not texts:
        return None
    reading = cloth.read(texts)
    return reading if reading["configs"] else None
