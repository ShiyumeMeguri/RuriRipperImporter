"""This game's answer to "bring the model's own secondary motion across".

Two ways in, one implementation, and neither of them is here: the import options
carry it as a switch and the asset browser as a button, both drawn by the core panel
off the capability this module declares. What IS here is the reading itself -- the
core never learns that these settings exist, only that this game answered.

Nothing is configured. Which bone of this rig is which bone of that model is the
question the animation path already answers, off the session's game and the rig's
own identity, so it is answered the same way here (see ``cloth.BoneNames``).
"""

from __future__ import annotations

import bpy

from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry
from . import cloth, cloth_apply

CLOTH_ADDON_ATTRIBUTE = "ruri_cloth_physics"


def addon_present():
    """Whether a rig in this scene can even hold these settings. The reader is this
    add-on's; the settings themselves belong to the cloth add-on, and with it absent
    there is nowhere to put them, so the option does not appear."""
    return hasattr(bpy.types.Object, CLOTH_ADDON_ATTRIBUTE)


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


def provide(context, rig, cabs):
    """Write the secondary motion those model prefabs state onto ``rig``.

    This is the callable the game module declares (see ``Game.GameModule``), so both
    ways in reach it without the host ever learning what these settings are -- only
    whether this game answered. Returns a report to word, or None for nothing to do.
    """
    if not addon_present() or cabmap_state.BRIDGE is None:
        return None
    texts = _prefab_texts(cabs)
    if not texts:
        return None
    reading = cloth.read(texts)
    if not reading["configs"]:
        return None
    names = cloth.BoneNames.resolve(cabmap_state.active_key(), rig)
    report = cloth_apply.Report()
    report.table = names.label
    return cloth_apply.apply(context, rig, reading, names, report)


def register():
    pass


def unregister():
    pass
