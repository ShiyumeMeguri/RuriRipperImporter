"""Headless: import one character through the roster (postmodel), check every
CharacterNPR material came out as a generated Ruri Uber node-group instance,
then render a turnaround still and report pixel statistics.

    blender.exe --background --python Game/EndField/shader/selftest_uber.py -- \
        [--chr <charId>] [--out <png path>]

Same self-locating + env-overridable shape as ../selftest_npc.py.
"""

from __future__ import annotations

import math
import os
import sys
import traceback

import bpy

ADDON = "RuriRipperImporter"
_HERE = os.path.dirname(os.path.abspath(__file__))
ADDONS_DIR = os.environ.get(
    "RURI_SELFTEST_ADDONS",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))))
BIN_DIR = os.environ.get(
    "RURI_RIPPERHOOK_BIN",
    "D:/Ruri/Git/FractalTools/Ruri-RipperHook/AssetRipper/Source/0Bins/AssetRipper/Release")
GAME_ROOT = os.environ.get(
    "RURI_SELFTEST_GAME_ROOT", "E:/Games/GRYPHLINK/games/Arknights Endfield")
CABMAP = os.environ.get("RURI_SELFTEST_CABMAP", os.path.join(GAME_ROOT, "EndField_1.4.4.cabmap"))
HOOK_ID = os.environ.get("RURI_SELFTEST_HOOK", "EndField_1.4.4")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
CHR = argv[argv.index("--chr") + 1] if "--chr" in argv else "chr_0017_yvonne"
OUT = argv[argv.index("--out") + 1] if "--out" in argv else os.path.join(_HERE, "selftest_uber.png")

failures = []


def check(label, ok, detail=""):
    print("[uber-selftest] {0} {1}{2}".format(
        "PASS" if ok else "FAIL", label, (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def main():
    # factory-startup 自带 Cube/Camera/Light —— 不清掉,那个默认立方体会挡住角色
    # 并把取景 bbox 撑大。
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    if ADDONS_DIR not in sys.path:
        sys.path.insert(0, ADDONS_DIR)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.preferences.addons[ADDON].preferences.ripperhook_repo = BIN_DIR

    from RuriRipperImporter.ruri_pybridge.runtime import bootstrap
    from RuriRipperImporter.Game.EndField import roster
    if not check("bootstrap", bootstrap.ensure_blocking(report_fn=print), bootstrap.last_error() or ""):
        return

    cab = bpy.context.scene.ruri_cabmap
    cab.game_root = GAME_ROOT
    cab.cabmap_path = CABMAP
    bpy.ops.ruri.refresh_hooks()
    for item in cab.available_hooks:
        item.selected = item.id == HOOK_ID
    check("load cabmap", "FINISHED" in bpy.ops.ruri.load_cabmap())

    # ── 单角色读取:与 Character/NPC tab **完全等价**(同一批解析函数 + 同一个导入算子)。
    #   roster 列得出就走 roster_load;列不出(未上线/非 CharacterTable 成员)就直接用
    #   roster_panel 自己的精确 prefab 解析 —— 它现在只认 <id>_<model_kind> 精确 stem,
    #   闭包只含这一个 prefab 的依赖,不会把整个游戏拖进内存。
    from RuriRipperImporter.Game.EndField import roster_panel
    from RuriRipperImporter.ruri_pybridge.session import cabmap_state

    state = bpy.context.scene.ruri_roster
    state.kind = roster.CHARACTERS
    state.model_kind = "postmodel"
    state.lod = 0
    state.load_expressions = False
    check("roster refresh", "FINISHED" in bpy.ops.ruri.roster_refresh())

    import time
    t_load = time.perf_counter()
    index = next((i for i, e in enumerate(state.entries) if e.key == CHR), -1)
    if index >= 0:
        state.active_index = index
        check("roster load '{0}'".format(CHR), "FINISHED" in bpy.ops.ruri.roster_load())
    else:
        print("[uber-selftest] '{0}' 不在 CharacterTable({1} 条),走精确 prefab 直读"
              .format(CHR, len(state.entries)), flush=True)
        hits = roster_panel._prefab_rows(CHR, state.model_kind)
        if not check("精确 prefab '{0}_postmodel' 命中".format(CHR), bool(hits),
                     ", ".join(p for _r, p in hits) or "cabmap 里没有这个名字"):
            return
        chosen, level = roster_panel._at_detail_level(hits, state.lod)
        cabmap_state.clear_selection()
        for row, _path in chosen:
            cabmap_state.SELECTED_CABS.add(cabmap_state.ROWS.cab(row))
        print("[uber-selftest] 选中 {0} 个 CAB(LOD{1}),调用 ruri.import_selected"
              .format(len(cabmap_state.SELECTED_CABS), level), flush=True)
        check("import_selected", "FINISHED" in bpy.ops.ruri.import_selected())
    print("[uber-selftest] 导入耗时 {0:.1f}s".format(time.perf_counter() - t_load), flush=True)

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    check("meshes imported", len(meshes) > 0, "{0} mesh object(s)".format(len(meshes)))

    # ── every CharacterNPR material must be a generated uber instance ────────
    used = []
    for obj in meshes:
        for mat in obj.data.materials:
            if mat is not None and mat not in used:
                used.append(mat)
    uber = [m for m in used if m.get("ruri_uber_part")]
    check("uber materials claimed", len(uber) > 0,
          "{0}/{1} materials".format(len(uber), len(used)))
    # 逐材质诊断:part / 组 / 真绑上的贴图 / PBR 关键 socket 实值。
    # 「看上去太暗」的判据全在这几个数里:_UseMetallicGlossMap 开而 map 没绑 → 走白图;
    # _Metallic 高 / _Smoothness 高 → 镜面无环境 = 黑;贴图键没绑 = 名字对不上。
    PROBE = ("_UseMetallicGlossMap", "_Metallic", "_Smoothness", "_Specular",
             "_UseDiffRampMap", "_UseSDFLightmap", "_SurfaceType", "_CharaPartID")
    for mat in used:
        part = mat.get("ruri_uber_part") or "(principled fallback)"
        grp = next((n for n in (mat.node_tree.nodes if mat.node_tree else [])
                    if n.type == "GROUP"), None)
        print("[uber-selftest]   {0:38s} part={1:14s} group={2}".format(
            mat.name, part, grp.node_tree.name if grp else "-"), flush=True)
        if grp is None:
            continue
        vals = []
        for name in PROBE:
            sock = grp.inputs.get(name)
            if sock is not None:
                try:
                    vals.append("{0}={1:.3g}".format(name, float(sock.default_value)))
                except TypeError:
                    vals.append("{0}={1}".format(name, tuple(round(c, 3) for c in sock.default_value)))
        print("[uber-selftest]       {0}".format("  ".join(vals)), flush=True)
        bound = sorted(n.image.name for n in grp.node_tree.nodes
                       if n.type == "TEX_IMAGE" and n.image is not None
                       and n.image.source != "GENERATED")
        placeholder = sorted(n.image.name for n in grp.node_tree.nodes
                             if n.type == "TEX_IMAGE" and n.image is not None
                             and n.image.source == "GENERATED")
        print("[uber-selftest]       绑上: {0}".format(", ".join(bound) or "(无)"), flush=True)
        print("[uber-selftest]       占位: {0}".format(", ".join(placeholder) or "(无)"), flush=True)
    prefixed = [g for g in bpy.data.node_groups if g.name.startswith("U") and ":" in g.name]
    check("per-material group sets", len(prefixed) > 0, "{0} prefixed groups".format(len(prefixed)))

    # ── camera framing from the BODY bounds, then a straight-on render ───────
    # The potential/skill VFX prefab is a building-sized effect slab that both
    # blows up the bbox and parks itself in front of the lens -- hide any mesh
    # whose material claimed as VFX (matches the game: skill FX aren't idle-on),
    # and exclude the overlay shadow boards from framing (kept for render).
    import mathutils
    def _part_of(obj):
        parts = {m.get("ruri_uber_part") for m in obj.data.materials if m is not None}
        parts.discard(None)
        return parts
    body_meshes = []
    for obj in meshes:
        parts = _part_of(obj)
        if parts == {"VFX"}:
            obj.hide_render = True
            continue
        if parts and parts <= {"OverlayShadow"}:
            continue  # 渲染保留,取景不计(贴脸小板)。
        body_meshes.append(obj)
    frame_meshes = body_meshes or meshes

    lo = mathutils.Vector((1e9, 1e9, 1e9))
    hi = mathutils.Vector((-1e9, -1e9, -1e9))
    for obj in frame_meshes:
        for corner in obj.bound_box:
            world = obj.matrix_world @ mathutils.Vector(corner)
            lo = mathutils.Vector(map(min, lo, world))
            hi = mathutils.Vector(map(max, hi, world))
    center = (lo + hi) / 2
    height = max(hi.z - lo.z, 0.1)
    distance = height * 2.6

    bpy.ops.object.camera_add(
        location=(center.x, center.y - distance, center.z),
        rotation=(math.pi / 2, 0.0, 0.0))
    cam = bpy.context.object
    cam.data.lens = 50
    bpy.context.scene.camera = cam
    print("[uber-selftest] framing: {0} body mesh(es), height={1:.2f}m".format(
        len(frame_meshes), height))

    sc = bpy.context.scene
    sc.view_settings.view_transform = "Standard"  # NPR 终值直出,不叠 AgX
    sc.render.resolution_x = 832
    sc.render.resolution_y = 1216
    sc.render.film_transparent = True
    sc.render.filepath = OUT
    sc.render.image_settings.file_format = "PNG"
    if sc.world is not None:
        sc.world.use_nodes = True
    bpy.ops.render.render(write_still=True)
    check("render written", os.path.isfile(OUT), OUT)

    probe = bpy.data.images.load(OUT)
    px = list(probe.pixels)
    w, h = probe.size
    covered = colored = 0
    shades = set()
    for i in range(0, len(px), 4):
        if px[i + 3] > 0.5:
            covered += 1
            if px[i] + px[i + 1] + px[i + 2] > 0.02:
                colored += 1
            shades.add((round(px[i], 2), round(px[i + 1], 2), round(px[i + 2], 2)))
    check("character covers frame", w * h * 0.05 < covered < w * h * 0.95,
          "{0}/{1} px".format(covered, w * h))
    check("shaded (non-black)", colored > covered * 0.5,
          "{0}/{1} covered px carry colour".format(colored, covered))
    check("colour variation (真着色,非平色)", len(shades) > 50,
          "{0} distinct shades".format(len(shades)))


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("uncaught exception")

print("[uber-selftest] {0} failure(s): {1}".format(len(failures), ", ".join(failures) or "none"))
sys.exit(1 if failures else 0)
