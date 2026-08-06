"""Every button, both tabs, headless: drives the add-on's real operators through
the full state machine (refresh -> select -> toggle every control -> read ->
import), and REDRAWS both tabs after every single step through a mock layout
that validates what a real draw would touch (property names via bl_rna,
operator idnames via bpy.ops) -- draw code is pure Python until it hits the
layout, so this catches exactly the class of bug a headless operator run
cannot (the CURRENT_WINDOW unpack crash was draw-only).

    blender.exe --background --python Game/EndField/selftest_scene.py -- [--import-scene <id>]
                                                                [--import-landmark <levelId>] [--scale 0.5]

Without the import flags every non-import button still runs. Fails loudly
(non-zero exit) so it can be looped.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

import bpy

# This file lives inside the add-on package, so the addons dir is its own
# grandparent -- no path needs configuring to run it from a checkout. The
# machine-specific values fall back to this machine's real install and can be
# overridden per environment.
ADDON = "RuriRipperImporter"
ADDONS_DIR = os.environ.get(
    "RURI_SELFTEST_ADDONS",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
BIN_DIR = os.environ.get(
    "RURI_RIPPERHOOK_BIN",
    "D:/Ruri/Git/FractalTools/Ruri-RipperHook/AssetRipper/Source/0Bins/AssetRipper/Release")
GAME_ROOT = os.environ.get(
    "RURI_SELFTEST_GAME_ROOT", "E:/Games/GRYPHLINK/games/Arknights Endfield")
CABMAP = os.environ.get("RURI_SELFTEST_CABMAP",
                        os.path.join(GAME_ROOT, "EndField_1.4.4.cabmap"))
HOOK_ID = os.environ.get("RURI_SELFTEST_HOOK", "EndField_1.4.4")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default):
    return argv[argv.index(name) + 1] if name in argv else default


IMPORT_SCENE = arg("--import-scene", "")
IMPORT_LANDMARK = arg("--import-landmark", "")
SCALE = float(arg("--scale", "1.0"))

failures = []


def check(label, condition, detail=""):
    print("[selftest] {0} {1}{2}".format("PASS" if condition else "FAIL", label,
                                         (" -- " + detail) if detail else ""))
    if not condition:
        failures.append(label)
    return condition


class MockLayout:
    """Stands in for bpy.types.UILayout: accepts the same drawing calls, returns
    more of itself for the container ones, and VALIDATES the two things a real
    draw would blow up on -- a property name the group does not have, and an
    operator idname that is not registered. Everything else about drawing is
    C-side rendering this cannot and need not reproduce."""

    enabled = True
    alignment = "LEFT"

    def __init__(self, problems):
        self._problems = problems

    def _child(self, *args, **kwargs):
        return MockLayout(self._problems)

    row = column = box = split = _child

    def label(self, **kwargs):
        pass

    def separator(self, **kwargs):
        pass

    def prop(self, data, name, **kwargs):
        if name not in data.bl_rna.properties:
            self._problems.append("prop {0!r} not on {1}".format(name, type(data).__name__))

    def operator(self, idname, **kwargs):
        module, _, func = idname.partition(".")
        if not hasattr(getattr(bpy.ops, module), func):
            self._problems.append("operator {0!r} not registered".format(idname))
        return type("_OpProxy", (), {"__setattr__": lambda *_a: None})()

    def __getattr__(self, _name):
        return self._child

    def template_list(self, list_id, _lid, data, propname, active_data, active_propname, **kwargs):
        if propname not in data.bl_rna.properties:
            self._problems.append("list prop {0!r} not on {1}".format(propname, type(data).__name__))
        if active_propname not in active_data.bl_rna.properties:
            self._problems.append("active prop {0!r} not on {1}".format(
                active_propname, type(active_data).__name__))


def draw_both(stage):
    """Draw every tab this game registers, exactly as the host panel would."""
    from RuriRipperImporter import Game
    problems = []
    for tab in Game.active_tabs([HOOK_ID]):
        try:
            tab.draw(MockLayout(problems), bpy.context)
        except Exception as exc:
            problems.append("{0} draw: {1}: {2}".format(tab.id, type(exc).__name__, exc))
            traceback.print_exc()
    check("draw after {0}".format(stage), not problems, "; ".join(problems))


def select(state, key):
    index = next((i for i, e in enumerate(state.entries) if e.key == key), -1)
    if index >= 0:
        state.active_index = index
    return index


def main():
    if ADDONS_DIR not in sys.path:
        sys.path.insert(0, ADDONS_DIR)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.preferences.addons[ADDON].preferences.ripperhook_repo = BIN_DIR

    from RuriRipperImporter.ruri_pybridge.runtime import bootstrap
    from RuriRipperImporter.ruri_pybridge.session import cabmap_state
    from RuriRipperImporter.Game.EndField import scene_state
    if not check("bootstrap ready", bootstrap.ensure_blocking(report_fn=print),
                 bootstrap.last_error() or ""):
        return
    draw_both("addon enable (nothing loaded)")

    cab = bpy.context.scene.ruri_cabmap
    cab.game_root = GAME_ROOT
    cab.cabmap_path = CABMAP
    check("refresh hooks", "FINISHED" in bpy.ops.ruri.refresh_hooks())
    for item in cab.available_hooks:
        item.selected = item.id == HOOK_ID
    check("load cabmap", "FINISHED" in bpy.ops.ruri.load_cabmap())
    draw_both("cabmap load")

    check("scene refresh", "FINISHED" in bpy.ops.ruri.scene_refresh())
    draw_both("scene refresh")

    box = bpy.context.scene.ruri_scene_box
    world = bpy.context.scene.ruri_scene_world

    # -- Scene tab: filter box, selection, every toggle, read ------------------
    box.search = "dung02"
    check("scene filter narrows", 0 < len(box.entries), "{0} lines".format(len(box.entries)))
    draw_both("scene filter")
    box.search = ""
    small = "dung02_dg005"
    if check("select {0}".format(small), select(box, small) >= 0):
        draw_both("scene selection (summary read)")
        box.lod0_only = False
        box.reset_scene = False
        check("scene read (lod0 off)", "FINISHED" in bpy.ops.ruri.scene_discover(kind=scene_state.SELF_CONTAINED))
        est_all = scene_state.estimate()
        draw_both("scene read lod0 off")
        box.lod0_only = True
        check("scene read (lod0 on)", "FINISHED" in bpy.ops.ruri.scene_discover(kind=scene_state.SELF_CONTAINED))
        est_lod0 = scene_state.estimate()
        check("lod0 filter actually filters", est_lod0["placeable"] < est_all["placeable"],
              "{0} -> {1}".format(est_all["placeable"], est_lod0["placeable"]))
        check("counts add up", est_lod0["total_placements"] ==
              est_lod0["placeable"] + est_lod0["no_transform"] + est_lod0["lod_filtered"],
              str(est_lod0))
        draw_both("scene read lod0 on")

    # -- World tab: dropdown, both maps, filter, state enum, scale, read ------
    for map_row in scene_state.SCENES[scene_state.STREAMING]:
        world.world_map = map_row["id"]
        check("map {0} lists places".format(map_row["id"]), len(world.entries) > 0,
              "{0} lines".format(len(world.entries)))
        draw_both("map switch {0}".format(map_row["id"]))
    world.world_map = "map01"
    world.search = "lv007"
    check("world filter narrows", len(world.entries) == 1, "{0} lines".format(len(world.entries)))
    world.search = ""
    if check("select map01_lv007", select(world, "map01_lv007") >= 0):
        states = scene_state.SUMMARIES["map01"]["scene_state_ids"]
        check("state enum populated", len(states) > 1, str(states))
        world.scene_state_id = str(states[1])
        world.scale = 0.3
        draw_both("world controls set")
        check("world read (state {0}, scale 0.3)".format(states[1]),
              "FINISHED" in bpy.ops.ruri.scene_discover(kind=scene_state.STREAMING))
        draw_both("world read")
        est = scene_state.estimate()
        check("world estimate priced", est["closure_cabs"] > 0, str(est))
        world.scene_state_id = str(states[0])
        world.scale = 1.0

    # -- Imports (each optional; run in separate processes for real sizes) -----
    if IMPORT_SCENE:
        if check("import scene '{0}' selectable".format(IMPORT_SCENE), select(box, IMPORT_SCENE) >= 0):
            box.reset_scene = True
            box.lod0_only = True
            started = time.perf_counter()
            check("scene import", "FINISHED" in bpy.ops.ruri.scene_import(kind=scene_state.SELF_CONTAINED),
                  "{0:.1f}s".format(time.perf_counter() - started))
            meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
            check("scene import placed objects", len(meshes) > 0, "{0} meshes".format(len(meshes)))
            draw_both("scene import")
    if IMPORT_LANDMARK:
        map_id = next((r["id"] for r in scene_state.SCENES[scene_state.STREAMING]
                       if IMPORT_LANDMARK.startswith(r["id"] + "_")), "")
        world.world_map = map_id
        if check("import landmark '{0}' selectable".format(IMPORT_LANDMARK),
                 select(world, IMPORT_LANDMARK) >= 0):
            world.scale = SCALE
            world.reset_scene = True
            started = time.perf_counter()
            check("world import", "FINISHED" in bpy.ops.ruri.scene_import(kind=scene_state.STREAMING),
                  "{0:.1f}s".format(time.perf_counter() - started))
            meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
            check("world import placed objects", len(meshes) > 0, "{0} meshes".format(len(meshes)))
            spread = max((max(abs(o.location.x), abs(o.location.y), abs(o.location.z))
                          for o in meshes), default=0.0)
            check("world import not at origin", spread > 1.0, "max |coord| = {0:.1f}".format(spread))
            draw_both("world import")


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("uncaught exception")

print("[selftest] {0} failure(s): {1}".format(len(failures), ", ".join(failures) or "none"))
sys.exit(1 if failures else 0)
