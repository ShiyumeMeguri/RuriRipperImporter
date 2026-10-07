"""The post-processing stack a loaded game puts on this scene.

A game's post chain is scene-level state: it owns ``scene.compositing_node_group``
and the colour-management transform. An import that installs one silently is a
one-way door -- whatever the user had is gone with nothing saying what it was --
so a stage records what it overwrote and this panel is where it gets handed back.

Nothing here knows what any chain does. A stage is a generated module that
publishes ``install`` / ``uninstall`` / ``installed`` / ``stage_node``; the
parameters drawn below are simply the unconnected input sockets of the group
node the stage put in the tree, so a chain that grows a knob grows a row here
with no code change.

Drawn as a top-level tab of the main panel rather than a panel of its own: it is
scene state, so it belongs beside the browser and the game's tabs, not under
whichever game happens to be open.
"""

from __future__ import annotations

import bpy

from . import derived_state, material_builder, material_panel
from ...Kernel import host as host_port
from ...Kernel.app import look


def _stage_nodes(scene):
    """(stage module, its group node) for every installed stage."""
    found = []
    for stage in material_builder.POST_STAGES:
        if not stage.installed(scene):
            continue
        node = stage.stage_node(scene)
        if node is not None:
            found.append((stage, node))
    return found


class RURI_OT_post_install(bpy.types.Operator):
    bl_idname = "ruri.post_install"
    bl_label = "Install Post Chain"
    bl_description = "Put the loaded game's post-processing onto this scene's compositor"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(material_builder.POST_STAGES)

    def execute(self, context):
        # 明说要装就整条重建;关卡写的输入与面板旋钮是场景记下的内容,照记下的写回(回出厂值用重置参数)。
        # 自动收尾那条路是「装过就不动」。
        material_builder.apply_post_stages(context.scene, force=True)
        self.report({"INFO"}, "Post chain installed on the compositor.")
        return {"FINISHED"}


class RURI_OT_post_remove(bpy.types.Operator):
    bl_idname = "ruri.post_remove"
    bl_label = "Remove Post Chain"
    bl_description = ("Take the game's post-processing back off and restore the compositor tree, "
                      "compositing switch and view transform this scene had before it was installed")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(material_builder.POST_STAGES)

    def execute(self, context):
        restored = material_builder.remove_post_stages(context.scene)
        if any(restored):
            self.report({"INFO"}, "Post chain removed; previous compositor state restored.")
        else:
            self.report({"WARNING"}, "Post chain removed, but no saved state was found to restore.")
        return {"FINISHED"}


class RURI_OT_post_viewport_preview(bpy.types.Operator):
    bl_idname = "ruri.post_viewport_preview"
    bl_label = "Viewport Preview"
    bl_description = ("Also run this chain in the 3D viewport. Off, it runs in the final render only: the viewport "
                      "compositor re-runs every chain on each redraw -- every orbit step and every sample while the "
                      "view converges -- so heavy screen-space chains stay out of it until asked for")
    bl_options = {"REGISTER"}

    group: bpy.props.StringProperty()
    kind: bpy.props.StringProperty()

    def execute(self, context):
        stage = next((one for one in material_builder.POST_STAGES if one.post["group"] == self.group), None)
        if stage is None or not stage.installed(context.scene):
            self.report({"WARNING"}, "'{0}' is not installed on this scene.".format(self.group))
            return {"CANCELLED"}
        stage.set_viewport_preview(context.scene, self.kind, not stage.viewport_previewed(context.scene, self.kind))
        return {"FINISHED"}


class RURI_OT_post_reset(bpy.types.Operator):
    bl_idname = "ruri.post_reset"
    bl_label = "Reset Parameters"
    bl_description = "Put every parameter of the installed chain back to the value the game ships"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(_stage_nodes(context.scene))

    def execute(self, context):
        count = 0
        for _stage, node in _stage_nodes(context.scene):
            for item in node.node_tree.interface.items_tree:
                if getattr(item, "in_out", "") != "INPUT":
                    continue
                socket = node.inputs.get(item.name)
                if socket is None or socket.is_linked:
                    continue
                if not hasattr(item, "default_value") or not hasattr(socket, "default_value"):
                    continue
                socket.default_value = item.default_value
                count += 1
        self.report({"INFO"}, "{0} parameter(s) reset.".format(count))
        return {"FINISHED"}


class RURI_OT_derived_rebuild(bpy.types.Operator):
    bl_idname = "ruri.derived_rebuild"
    bl_label = "Rebuild Derived State"
    bl_description = ("Force-rebuild everything this add-on derives from the scene: the face "
                      "basis, how every Ruri material reads the world, and the post chain. The "
                      "vertex stack (fur shells, outlines) is NOT rebuilt here: it is built only "
                      "at the moment an import reads the game's materials")
    bl_options = {"REGISTER"}

    def execute(self, context):
        derived_state.rebuild_all()
        if derived_state.LAST_ERROR:
            self.report({"ERROR"}, "派生态重建失败: " + derived_state.LAST_ERROR)
            return {"FINISHED"}
        self.report({"INFO"}, "Derived state rebuilt.")
        return {"FINISHED"}


def draw_derived_section(layout, context):
    """派生态的强制重建,与最近一次落地炸掉的阶段:派生态失败在画面上与「这个着色器本来就长这样」无法区分,
    所以它得一直挂在这里喊,而不是只在控制台滚一行过去。"""
    layout.operator(RURI_OT_derived_rebuild.bl_idname, icon="FILE_REFRESH")
    if derived_state.LAST_ERROR:
        alert = layout.row()
        alert.alert = True
        alert.label(text=derived_state.LAST_ERROR, icon="ERROR")


def draw_materials_section(layout, context):
    """选中材质的参数 —— 逐材质、改得最频繁的那一层,所以排在最前。

    A host that shows a material's own parameters natively needs no such panel and
    registers none; this one's materials are GENERATED node groups, which have no
    native surface at all.

    Drawn natively (``layout.native``): what it puts on screen is made of this
    application's own objects -- the slot list belonging to the mesh, an image
    datablock picker, a search field over the rig's bones -- and none of those has a
    neutral spelling, because none of the things they pick exists in the other
    host."""
    layout.native(material_panel.draw_materials)


def draw_post_chain_section(layout, context):
    """这一格是「画面」的最后一层:装在场景合成器上的那条后处理链。"""
    scene = context.scene
    if not material_builder.POST_STAGES:
        layout.label(text="No game shader package is loaded, so no post chain exists.", icon="INFO")
        return

    installed = material_builder.post_stages_installed(scene)
    header = layout.row(align=True)
    header.operator(RURI_OT_post_install.bl_idname, icon="IMPORT")
    remove = header.row(align=True)
    remove.enabled = bool(installed)
    remove.operator(RURI_OT_post_remove.bl_idname, icon="TRASH")

    if not installed:
        layout.label(text="Not installed -- the scene keeps its own compositor.", icon="CHECKBOX_DEHLT")
        return

    # What the chain took over, said out loud: these three are exactly what
    # Remove puts back, and they are the ones a user notices going missing.
    state = layout.box()
    state.label(text="Scene state this chain owns", icon="SCENE_DATA")
    row = state.row()
    row.label(text="Compositor tree")
    row.label(text=scene.compositing_node_group.name if scene.compositing_node_group else "(none)")
    state.prop(scene.render, "use_compositing")
    state.prop(scene.render, "compositor_device", text="Device")
    state.prop(scene.view_settings, "view_transform")
    state.label(text="Viewport preview: shading popover > Compositor", icon="INFO")

    for stage, node in _stage_nodes(scene):
        box = layout.box()
        head = box.row()
        head.label(text=node.node_tree.name, icon="NODE_COMPOSITING")
        head.operator(RURI_OT_post_reset.bl_idname, text="", icon="LOOP_BACK")
        kinds = stage.viewport_preview_kinds()
        if kinds:
            preview = box.column(align=True)
            preview.label(text="Also in the viewport:", icon="RESTRICT_VIEW_OFF")
            for kind in kinds:
                on = stage.viewport_previewed(scene, kind)
                toggle = preview.operator(RURI_OT_post_viewport_preview.bl_idname,
                                          text=kind.replace("_", " ").title(), depress=on,
                                          icon="CHECKBOX_HLT" if on else "CHECKBOX_DEHLT")
                toggle.group = stage.post["group"]
                toggle.kind = kind
        drawn = 0
        for socket in node.inputs:
            if socket.is_linked:
                continue
            if not hasattr(socket, "default_value"):
                continue
            box.prop(socket, "default_value", text=socket.name)
            drawn += 1
        if drawn == 0:
            box.label(text="This chain exposes no parameters yet.", icon="INFO")


# 面板本体不在这里注册:后处理是主面板的一格 tab,与游戏无关、装了后处理栈才出现。
# 这里只留它的操作符,以及这三段各自向 look 注册册。
_CLASSES = (
    RURI_OT_post_install,
    RURI_OT_post_remove,
    RURI_OT_post_viewport_preview,
    RURI_OT_post_reset,
    RURI_OT_derived_rebuild,
)

#: The three answers THIS host has to "what does the frame finally look like",
#: each with the capability that makes it mean anything. A host that answers none
#: of them registers none, and the tab says so instead of standing empty.
_SECTIONS = (
    ("materials", "Materials", draw_materials_section, host_port.NodeMaterials),
    ("derived", "Derived state", draw_derived_section, host_port.SceneGraph),
    ("post_chain", "Post chain", draw_post_chain_section, host_port.Compositor),
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for key, label, draw, requires in _SECTIONS:
        look.register_section(key, label, draw, requires)


def unregister():
    for key, _label, _draw, _requires in _SECTIONS:
        look.drop_section(key)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
