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

try:
    from . import derived_state, material_builder
except ImportError:
    import derived_state
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
        # 明说要装就整条重建(参数回出厂值);自动收尾那条路是「装过就不动」。
        material_builder.apply_post_stages(context.scene, force=True)
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


MAIN_LIGHT_OVERRIDE = "ruri_main_light"


def _ruri_materials(objects):
    """The Ruri-built materials of these objects, in a stable order.

    ``ruri_uber_part`` is written by the generated stack when it builds a
    material, so it is the one honest test of "we made this" -- no name matching.
    """
    found = {}
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            mat = slot.material
            if mat is not None and mat.get("ruri_uber_part") is not None:
                found[mat.name] = mat
    return [found[k] for k in sorted(found)]


def _scene_light_summary(scene):
    """What the shading resolves to when no material overrides it."""
    suns = sorted((o.name for o in scene.objects
                   if o.type == "LIGHT" and o.data.type == "SUN"))
    if suns:
        return suns[0], "Scene sun"
    return None, "Forward light (follows the view)"


class RURI_OT_main_light_set(bpy.types.Operator):
    bl_idname = "ruri.main_light_set"
    bl_label = "Override Main Light"
    bl_description = ("Point the selected objects' Ruri materials at this light instead of the "
                      "scene sun, and re-answer their lighting queries")
    bl_options = {"REGISTER", "UNDO"}

    light: bpy.props.StringProperty(name="Light")

    def execute(self, context):
        light = bpy.data.objects.get(self.light)
        if light is None or light.type != "LIGHT":
            self.report({"ERROR"}, "'{0}' is not a light object.".format(self.light))
            return {"CANCELLED"}
        mats = _ruri_materials(context.selected_objects)
        if not mats:
            self.report({"WARNING"}, "The selection has no Ruri-built materials.")
            return {"CANCELLED"}
        # The override lives on the MATERIAL because the node graph is built per
        # material -- that is the scope this can actually act on. The selection
        # is just how the user points at it.
        for mat in mats:
            mat[MAIN_LIGHT_OVERRIDE] = light
        count = material_builder.rewire_capabilities(mats)
        self.report({"INFO"}, "{0} material(s) now lit by '{1}'.".format(count, light.name))
        return {"FINISHED"}


class RURI_OT_main_light_clear(bpy.types.Operator):
    bl_idname = "ruri.main_light_clear"
    bl_label = "Use Scene Light"
    bl_description = "Drop the override and go back to the scene sun (or the forward light)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        mats = [m for m in _ruri_materials(context.selected_objects)
                if m.get(MAIN_LIGHT_OVERRIDE) is not None]
        if not mats:
            self.report({"WARNING"}, "Nothing in the selection is overridden.")
            return {"CANCELLED"}
        for mat in mats:
            del mat[MAIN_LIGHT_OVERRIDE]
        count = material_builder.rewire_capabilities(mats)
        self.report({"INFO"}, "{0} material(s) back on the scene light.".format(count))
        return {"FINISHED"}


class RURI_OT_derived_rebuild(bpy.types.Operator):
    bl_idname = "ruri.derived_rebuild"
    bl_label = "Rebuild Derived State"
    bl_description = ("Force-rebuild everything this add-on derives from the scene: the vertex "
                      "stack (fur shells, outlines, face basis), how every Ruri material reads "
                      "the lights and world, the light tables and the post chain. Imports, light "
                      "and camera edits already do this by themselves; this button is for edits "
                      "nothing can watch, e.g. rewiring the world's own node tree")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        derived_state.rebuild_all()
        if derived_state.LAST_ERROR:
            self.report({"ERROR"}, "派生态重建失败: " + derived_state.LAST_ERROR)
            return {"FINISHED"}
        self.report({"INFO"}, "Derived state rebuilt.")
        return {"FINISHED"}


def _draw_main_light(layout, context):
    """Which light the game's shading reads.

    The shader asks the scene one question -- "what is the main directional
    light" -- and the answer is wired from Blender's own light object (its
    matrix and colour, through drivers, so it stays live). This section is where
    that answer gets redirected per character.
    """
    box = layout.box()
    box.label(text="Main light", icon="LIGHT_SUN")

    sun, fallback = _scene_light_summary(context.scene)
    row = box.row()
    row.label(text="Scene default")
    row.label(text=sun or fallback, icon="LIGHT_SUN" if sun else "CAMERA_DATA")
    box.operator(RURI_OT_derived_rebuild.bl_idname, icon="FILE_REFRESH")
    # 派生态失败在画面上与「这个着色器本来就长这样」无法区分,所以它得一直挂在这里
    # 喊,而不是只在控制台滚一行过去。
    if derived_state.LAST_ERROR:
        alert = box.row()
        alert.alert = True
        alert.label(text=derived_state.LAST_ERROR, icon="ERROR")

    mats = _ruri_materials(context.selected_objects)
    if not mats:
        box.label(text="Select an object to give it its own light.", icon="RESTRICT_SELECT_ON")
        return

    overridden = {m.name: m[MAIN_LIGHT_OVERRIDE] for m in mats
                  if m.get(MAIN_LIGHT_OVERRIDE) is not None}
    sub = box.column(align=True)
    sub.label(text="Selected: {0} Ruri material(s)".format(len(mats)))
    if overridden:
        names = sorted({v.name for v in overridden.values() if v is not None})
        sub.label(text="Overridden by: " + ", ".join(names), icon="LIGHT_DATA")
        if len(overridden) != len(mats):
            sub.label(text="{0} of them still on the scene light.".format(len(mats) - len(overridden)),
                      icon="INFO")
        sub.operator(RURI_OT_main_light_clear.bl_idname, icon="X")

    picker = box.column(align=True)
    picker.label(text="Light this selection with:")
    lights = [o for o in context.scene.objects if o.type == "LIGHT"]
    if not lights:
        picker.label(text="This scene has no light objects.", icon="ERROR")
        return
    grid = picker.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for light in sorted(lights, key=lambda o: o.name):
        op = grid.operator(RURI_OT_main_light_set.bl_idname, text=light.name,
                           icon="LIGHT_" + light.data.type)
        op.light = light.name


def draw_post_tab(layout, context):
    scene = context.scene
    _draw_main_light(layout, context)
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


# 面板本体不在这里注册:后处理是主面板的一格 tab(cabmap_panel._post_tab),
# 与游戏无关、装了后处理栈才出现。这里只留它的操作符。
_CLASSES = (
    RURI_OT_post_install,
    RURI_OT_post_remove,
    RURI_OT_post_reset,
    RURI_OT_main_light_set,
    RURI_OT_main_light_clear,
    RURI_OT_derived_rebuild,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
