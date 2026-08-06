"""Headless: import one assembled npc through the add-on's own operator and check
the meshes actually came out wearing the materials the template asks for.

    blender.exe --background --python Game/EndField/selftest_npc.py -- [--npc <templateId>]
"""

from __future__ import annotations

import os
import sys
import traceback

import bpy

# Same self-locating + overridable shape as selftest_scene.py next door.
ADDON = "RuriRipperImporter"
ADDONS_DIR = os.environ.get(
    "RURI_SELFTEST_ADDONS",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
BIN_DIR = os.environ.get(
    "RURI_RIPPERHOOK_BIN",
    "D:/Ruri/Git/FractalTools/Ruri-RipperHook/AssetRipper/Source/0Bins/AssetRipper/Release")
GAME_ROOT = os.environ.get(
    "RURI_SELFTEST_GAME_ROOT", "E:/Games/GRYPHLINK/games/Arknights Endfield")
CABMAP = os.environ.get("RURI_SELFTEST_CABMAP", os.path.join(GAME_ROOT, "EndField_1.4.4.cabmap"))
HOOK_ID = os.environ.get("RURI_SELFTEST_HOOK", "EndField_1.4.4")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
NPC = argv[argv.index("--npc") + 1] if "--npc" in argv else "npc_girl_efstaff_a_04"

failures = []


def check(label, ok, detail=""):
    print("[selftest] {0} {1}{2}".format("PASS" if ok else "FAIL", label, (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def main():
    if ADDONS_DIR not in sys.path:
        sys.path.insert(0, ADDONS_DIR)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.preferences.addons[ADDON].preferences.ripperhook_repo = BIN_DIR

    from RuriRipperImporter.ruri_pybridge.runtime import bootstrap
    from RuriRipperImporter.ruri_pybridge.session import cabmap_state
    from RuriRipperImporter.Game.EndField import roster, roster_panel
    if not check("bootstrap", bootstrap.ensure_blocking(report_fn=print), bootstrap.last_error() or ""):
        return

    cab = bpy.context.scene.ruri_cabmap
    cab.game_root = GAME_ROOT
    cab.cabmap_path = CABMAP
    bpy.ops.ruri.refresh_hooks()
    for item in cab.available_hooks:
        item.selected = item.id == HOOK_ID
    check("load cabmap", "FINISHED" in bpy.ops.ruri.load_cabmap())

    roots = roster.vfs_roots(GAME_ROOT)
    info = cabmap_state.BRIDGE.npc_prefab_parts(roots, NPC)
    check("manifest names an assembly table", bool(info.get("avatar_mesh")),
          "avatar_mesh={0!r} parts={1}".format(info.get("avatar_mesh"), len(info["parts"])))

    assigned = roster_panel._npc_materials(bpy.context, info, NPC)
    check("materials resolved", len(assigned) > 0, "{0} submesh row(s)".format(len(assigned)))

    # Import through the real roster operator.
    state = bpy.context.scene.ruri_roster
    state.kind = roster.NPCS
    check("roster refresh", "FINISHED" in bpy.ops.ruri.roster_refresh())
    index = next((i for i, e in enumerate(state.entries) if e.key == NPC), -1)
    if not check("'{0}' is listed".format(NPC), index >= 0, "{0} entries".format(len(state.entries))):
        return
    state.active_index = index
    check("roster load", "FINISHED" in bpy.ops.ruri.roster_load())

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    check("meshes imported", len(meshes) > 0, "{0} mesh object(s)".format(len(meshes)))
    with_mat = [o for o in meshes if len(o.data.materials) > 0 and o.data.materials[0] is not None]
    check("meshes carry materials", len(with_mat) > 0,
          "{0}/{1} meshes have a material".format(len(with_mat), len(meshes)))
    for obj in meshes:
        names = [m.name if m else None for m in obj.data.materials]
        print("[selftest]   {0}  ->  {1}".format(obj.name, names))

    images = [i for i in bpy.data.images if i.size[0] > 0]
    check("textures loaded", len(images) > 0, "{0} image(s)".format(len(images)))


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("uncaught exception")

print("[selftest] {0} failure(s): {1}".format(len(failures), ", ".join(failures) or "none"))
sys.exit(1 if failures else 0)
