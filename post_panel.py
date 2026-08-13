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
"""

from __future__ import annotations

import bpy

try:
    from . import material_builder
except ImportError:
    import material_builder


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
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(material_builder.POST_STAGES)

    def execute(self, context):
        material_builder.apply_post_stages(context.scene)
        self.report({"INFO"}, "Post chain installed on the compositor.")
        return {"FINISHED"}


class RURI_OT_post_remove(bpy.types.Operator):
    bl_idname = "ruri.post_remove"
    bl_label = "Remove Post Chain"
    bl_description = ("Take the game's post-processing back off and restore the compositor tree, "
                      "compositing switch and view transform this scene had before it was installed")
    bl_options = {"REGISTER", "UNDO"}

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


class RURI_OT_post_reset(bpy.types.Operator):
    bl_idname = "ruri.post_reset"
    bl_label = "Reset Parameters"
    bl_description = "Put every parameter of the installed chain back to the value the game ships"
    bl_options = {"REGISTER", "UNDO"}

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


def draw_post_tab(layout, context):
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


class RURI_PT_post(bpy.types.Panel):
    bl_idname = "RURI_PT_post"
    bl_label = "Post Processing"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_post_tab(self.layout, context)


_CLASSES = (
    RURI_OT_post_install,
    RURI_OT_post_remove,
    RURI_OT_post_reset,
    RURI_PT_post,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
