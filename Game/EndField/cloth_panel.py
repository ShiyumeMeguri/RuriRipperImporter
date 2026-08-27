"""The button that pulls a model's authored secondary motion onto a rig here.

It sits in the Character tab because that is where a rig has just been imported,
and it reads the model prefab the browser is already pointing at: the settings
live ON that prefab, so what is selected in the browser IS the source.
"""

from __future__ import annotations

import pathlib

import bpy
from bpy.props import StringProperty

from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry
from . import cloth, cloth_apply

PRESET_FOLDER = "AnimationRetarget"
CLOTH_ADDON_ATTRIBUTE = "ruri_cloth_physics"


def preset_paths():
    found = []
    for folder in bpy.utils.script_paths(subdir="presets"):
        directory = pathlib.Path(folder) / PRESET_FOLDER
        if directory.is_dir():
            found.extend(sorted(path for path in directory.glob("*.json")))
    return found


def _cloth_addon_present():
    return hasattr(bpy.types.Object, CLOTH_ADDON_ATTRIBUTE)


def _selected_cabs():
    return list(cabmap_state.selected_cabs())


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


class RURI_OT_endfield_cloth_import(bpy.types.Operator):
    bl_idname = "ruri_ripper.endfield_cloth_import"
    bl_label = "读取角色布料配置"
    bl_description = ("把浏览器选中的模型 prefab 上作者写好的次级运动配置, 整套写到当前骨架上。"
                      "骨架上原有的配置会被清空重建")
    bl_options = {'REGISTER', 'UNDO'}

    preset: StringProperty(name="骨骼名预设", default="")

    def execute(self, context):
        rig = context.object
        if rig is None or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "先选中要写入的骨架")
            return {'CANCELLED'}
        if not _cloth_addon_present():
            self.report({'ERROR'}, "布料插件没有启用, 骨架上没有可写入的属性")
            return {'CANCELLED'}
        if cabmap_state.BRIDGE is None:
            self.report({'ERROR'}, "还没有打开安装, 先在上面加载 cabmap")
            return {'CANCELLED'}
        cabs = _selected_cabs()
        if not cabs:
            self.report({'ERROR'}, "浏览器里先选中角色的模型 prefab")
            return {'CANCELLED'}

        texts = _prefab_texts(cabs)
        if not texts:
            self.report({'ERROR'}, "选中的行里没有 prefab")
            return {'CANCELLED'}

        names = cloth.BoneNames.from_preset(self.preset) if self.preset \
            else cloth.BoneNames.identity()
        reading = cloth.read(texts)
        if not reading["configs"]:
            self.report({'WARNING'}, "这个 prefab 上没有次级运动配置")
            return {'CANCELLED'}

        report = cloth_apply.Report()
        cloth_apply.apply(context, rig, reading, names, report)
        for line in report.lines():
            self.report({'INFO'}, line)
        if report.missing_bones or report.unknown_paths:
            self.report({'WARNING'}, "; ".join(report.lines()[1:]))
        return {'FINISHED'}


def draw_cloth_block(layout, context):
    if not _cloth_addon_present():
        return
    box = layout.box()
    box.label(text="布料配置", icon='MOD_CLOTH')
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        box.label(text="选中骨架后可用", icon='INFO')
        return
    presets = preset_paths()
    state = context.scene.ruri_endfield_cloth
    row = box.row(align=True)
    row.prop(state, "preset", text="")
    operator = box.operator(RURI_OT_endfield_cloth_import.bl_idname, icon='IMPORT')
    operator.preset = state.preset if state.preset != 'NONE' else ""
    box.label(text="会清空 %s 上现有的全部配置" % rig.name, icon='ERROR')


def _preset_items(self, context):
    items = [('NONE', "不转换骨骼名", "prefab 里的骨骼名直接当本骨架的名字用")]
    for path in preset_paths():
        items.append((str(path), path.stem, str(path)))
    return items


class RURI_PG_endfield_cloth(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(name="骨骼名预设", items=_preset_items)


_CLASSES = (RURI_PG_endfield_cloth, RURI_OT_endfield_cloth_import)


def register():
    for entry in _CLASSES:
        bpy.utils.register_class(entry)
    bpy.types.Scene.ruri_endfield_cloth = bpy.props.PointerProperty(type=RURI_PG_endfield_cloth)


def unregister():
    if hasattr(bpy.types.Scene, "ruri_endfield_cloth"):
        del bpy.types.Scene.ruri_endfield_cloth
    for entry in reversed(_CLASSES):
        bpy.utils.unregister_class(entry)
