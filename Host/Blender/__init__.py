"""The Blender driver: this application's answers to :class:`Kernel.host.Host`,
its add-on lifecycle, and the only place ``bpy`` is allowed to appear outside a
game's own panels.

A statement lands here as Blender data: its skeletons as armatures, its meshes
at the level it states, its materials as node graphs, its performances as
actions.
"""

from __future__ import annotations

import importlib
import os
import sys

import bpy
from bpy.props import BoolProperty, StringProperty

from ...Kernel import bootstrap as kernel_bootstrap
from ...Kernel import host as host_port

#: The add-on module name Blender registered, which is what an AddonPreferences
#: is keyed by and what ``preferences.addons`` is looked up with. Derived from
#: this driver's own dotted name so the folder can be renamed without a constant
#: going stale.
ADDON = __package__.split(".")[0]

#: The main light a level's environment states, one per document: standing a new
#: environment up rewrites this light rather than adding a second sun.
LEVEL_SUN = "Ruri Level Sun"

_BIN_DIR_HINT = ('Click "Download / Update RipperHook" in Edit > Preferences > Add-ons > '
                 'RuriRipperImporter (it downloads the reader and sets the path; a check is also '
                 'running in the background now), or set "Ruri-RipperHook Bin Dir" there by hand.')


def _preferences():
    """This add-on's preferences, or None.

    ``.get``, not ``[ADDON]``: an add-on enabled with no saved preferences entry
    (a ``--factory-startup`` session, a first run) has none, and raising here
    would abort the REST of register() while everything above it stayed
    registered -- a half-live add-on whose operators exist but whose game
    registry is empty, which reads as "the feature silently does nothing"
    rather than as a startup failure."""
    entry = bpy.context.preferences.addons.get(ADDON)
    return entry.preferences if entry is not None else None


class BlenderHost(host_port.Host, host_port.SceneGraph, host_port.Compositor, host_port.Rig,
                  host_port.Timeline, host_port.Retarget, host_port.MorphTargets, host_port.NodeMaterials,
                  host_port.RegionPicking):
    """What Blender can do, and where Blender keeps the one path that differs
    per machine. A skeleton, an animation surface and morph targets, a scene of
    separate objects with a compositor behind it, node-graph materials, and a 3D
    viewport to point into a picture with; the three Painter answers instead (a
    bake cache, texture sets, plugin-settable display) are not Blender's to answer."""

    name = "Blender"

    #: The reader's word for the basis Blender's geometry and transforms are in.
    basis = "blender"

    def log(self, level, message):
        print("[RuriRipper] {0}".format(message))

    def workspace_dir(self):
        """None: Blender's add-on directory is a checkout like Painter's, but
        Blender has no per-application resources folder that is a better home
        than the platform's own per-user data directory. Let the shared resolver
        pick it."""
        return None

    def preset_dir(self):
        """Blender's own preset tree, under this add-on's registered name --
        the folder its users already browse for everything else they saved, and
        a folder Blender keeps across an add-on reinstall."""
        return os.path.join(bpy.utils.user_resource("SCRIPTS", path="presets"), ADDON)

    def bin_dir(self):
        preferences = _preferences()
        return preferences.ripperhook_repo if preferences is not None else ""

    def bin_dir_hint(self):
        return _BIN_DIR_HINT

    def texture_containers(self):
        """Blender reads tga natively, so declaring nothing (keep each texture's
        authored container) looks like the neutral choice -- and is the expensive
        one, measured on a real scene window: the exporter's tga writer runs
        inside a process-wide lock while its png writer is lockless FPng, and tga
        is uncompressed. Same window, same pixels (both containers encode the
        identical 8-bit buffer, so this is lossless): texture stage 7.9s -> 4.1s,
        encode CPU 117s -> 29s, payload 1472MB -> 754MB. exr/hdr stay declared
        because a float texture naturally picks one of those, and png would
        truncate it to 8 bits."""
        return ("png", "exr", "hdr")

    def locale(self):
        """Which text container a game's localized names are joined through.
        Switching Blender's language switches every roster with nothing else
        reloaded."""
        return bpy.app.translations.locale

    def absolute_path(self, path):
        return bpy.path.abspath(path) if path else ""

    def schedule(self, seconds, call):
        bpy.app.timers.register(call, first_interval=seconds)

    def redraw(self):
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

    def selected_rig(self, context):
        """The rig the active object stands for (a mesh stands for the armature its
        modifier binds), else the one rig the selection resolves to, else the
        scene's only armature. None when nothing resolves or the choice is
        ambiguous."""
        from . import rig_identity

        context = context or bpy.context
        active = rig_identity.armature_of(getattr(context, "active_object", None))
        if active is not None:
            return active
        selected = {rig_identity.armature_of(obj)
                    for obj in getattr(context, "selected_objects", ())}
        selected.discard(None)
        if selected:
            return next(iter(selected)) if len(selected) == 1 else None
        armatures = [obj for obj in context.scene.objects if obj.type == "ARMATURE"]
        return armatures[0] if len(armatures) == 1 else None

    def clear_scene(self, context):
        """Empty the document: the objects, the collections holding them, and
        every data-block they were the last user of.

        Deleting the objects is not emptying it. A window import files its
        placements under a collection per distinct asset, and the meshes,
        materials and images those objects point at are DATA-BLOCKS that outlive
        them -- Blender keeps a zero-user data-block until something purges it.
        Measured on one 82-asset scene imported twice: after the second reset the
        file held 164 collections, 166 meshes and 249 images for a scene showing
        none of them, and the pile grows with every reset until the next window is
        being built on top of every window before it.

        Selecting is also not the same as covering: a selection reaches what the
        view layer lets it, so anything in a hidden or excluded collection
        survived a reset that claimed to have emptied the document.

        The collection the view layer had active is among the removed ones, and a
        view layer left pointing at nothing gives the next import nowhere to link
        into -- so the scene's own collection, which is what is left, becomes the
        active one."""
        bpy.data.batch_remove(list(bpy.data.objects) + list(bpy.data.collections))
        bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        view_layer = (context or bpy.context).view_layer
        view_layer.active_layer_collection = view_layer.layer_collection

    def apply_renderer(self, context):
        from . import renderer
        renderer.apply(context.scene)

    def apply_environment(self, context, environment):
        from . import ui_stage, world_builder
        ambient = environment["ambient"]
        answered = world_builder.build(context, ambient["coefficients"], ambient["label"])
        ui_stage.apply_environment(context, environment["light"], LEVEL_SUN)
        return answered

    def apply_post_inputs(self, context, values):
        from . import material_builder
        return material_builder.apply_post_inputs(context.scene, values)

    def apply_level_resources(self, context, values, payloads):
        from . import material_builder
        return material_builder.apply_level_resources(context.scene, values, payloads)

    def apply_box_lists(self, context, boxes, range_attribute, list_table):
        from . import box_lists
        return box_lists.apply(context, boxes, range_attribute, list_table)

    def apply_medium(self, context, medium):
        from . import medium as medium_builder
        return medium_builder.apply(context, medium, bpy.data.objects.get(LEVEL_SUN))

    def source_view_position(self, context):
        """Where the document is being looked at from, in the source's world: the scene
        camera, else the first 3D view's own viewpoint, else the middle of what is in
        the scene. A level's camera-centred state (its irradiance clipmaps) is built
        around this point, the way the source builds it around its camera.

        Placements are read from the evaluated dependency graph: an object moved since
        the last evaluation still carries its old world matrix on the original, and a
        camera placed in the same script as the import reads as sitting at the origin."""
        from mathutils import Vector
        from . import material_builder
        scene = context.scene
        depsgraph = context.evaluated_depsgraph_get()
        position = None
        if scene.camera is not None:
            position = scene.camera.evaluated_get(depsgraph).matrix_world.translation.copy()
        else:
            manager = context.window_manager
            for window in (manager.windows if manager is not None else ()):
                for area in window.screen.areas:
                    if area.type == "VIEW_3D":
                        region = area.spaces.active.region_3d
                        position = region.view_matrix.inverted().translation.copy()
                        break
                if position is not None:
                    break
        if position is None:
            evaluated = [obj.evaluated_get(depsgraph) for obj in scene.objects if obj.type == "MESH"]
            corners = [obj.matrix_world @ Vector(corner) for obj in evaluated for corner in obj.bound_box]
            if not corners:
                return None
            low = Vector([min(c[i] for c in corners) for i in range(3)])
            high = Vector([max(c[i] for c in corners) for i in range(3)])
            position = (low + high) * 0.5
        basis = material_builder.world_basis()
        return tuple(sum(basis[row][column] * position[row] for row in range(3)) for column in range(3))

    def load_display_stage(self, context, stage, options):
        from . import ui_stage
        return ui_stage.load(context, stage, options)

    def pick_rectangle(self, context, picture, rectangle, done, snap=0.0):
        region_picker.request(context, picture, rectangle, done, snap)

    def picking(self):
        return region_picker.active()

    def stop_picking(self):
        region_picker.stop()

    def write_secondary_motion(self, context, rig, reading):
        from . import cloth_writer
        return cloth_writer.write(context, rig, reading)

    def rig_paths(self, rig):
        from . import rig_identity
        return sorted(set(rig_identity.bone_paths(rig).values()))

    def rig_seed(self, rig):
        from . import rig_identity
        return rig_identity.seed_of(rig)

    def rig_avatar(self, rig):
        from . import rig_identity
        return rig_identity.avatar_of(rig)

    def rig_rest(self, context, rig):
        from . import bone_poses
        return bone_poses.rest(context, rig)

    def bake_bone_poses(self, context, rig, source_names, frame_count, payload,
                        name, into=None):
        from . import bone_poses
        return bone_poses.key(context, rig, source_names, frame_count, payload,
                              name, into)

    def rig_named(self, rig_name, context=None):
        found = bpy.data.objects.get(rig_name) if rig_name else None
        return found if found is not None and found.type == "ARMATURE" else None

    def frame_rate(self, context):
        return (context or bpy.context).scene.render.fps

    def set_frame_range(self, context, start, end):
        from . import animation_builder
        animation_builder.set_frame_range((context or bpy.context).scene, start, end)

    def face_bindings(self, context, rig, table):
        from . import face_rig
        return face_rig.bindings(context, rig, table)

    def drive_face(self, context, rig, table, weights):
        from . import face_rig
        return face_rig.drive(context, rig, table, weights)

    def bake_face(self, context, rig, table, tracks, frames, fps, name,
                  into=None):
        from . import face_rig
        return face_rig.bake(context, rig, table, tracks, frames, fps, name, into)

    def drive_blend_shapes(self, context, rig, weights):
        from . import blend_shapes
        return blend_shapes.drive(context, rig, weights)

    def play(self, context, rig, clips, options, activate=False):
        from . import animation_builder
        return animation_builder.play(context, rig, clips, options, activate)

    def stand_in_rig(self, context, statement, skeleton_key, options):
        from . import materialise
        return materialise.build_rig(context, statement, skeleton_key, options)

    def discard_rig(self, context, rig):
        from . import retarget
        retarget.discard(rig)

    def retarget(self, context, source_rig, rig, source_name, performances, activate=False):
        from . import retarget
        return retarget.carry(context, source_rig, rig, source_name, performances, activate)

    def register_state(self, name, schema, handlers, extra=None):
        from . import rna
        return rna.register_state(name, schema, handlers, extra)

    def unregister_state(self, name):
        from . import rna
        rna.unregister_state(name)

    def panel_state(self, context, name):
        """``context`` may be None: a filter spec's field vocabulary is asked
        from places that have no context of their own, and Blender's own
        ``bpy.context`` is the right answer there rather than a failure."""
        scene = (context or bpy.context).scene
        return getattr(scene, name)

    def materialise(self, context, statement, options, report=None):
        from . import materialise as materialiser
        return materialiser.materialise(context, statement, options, report)


#: Bound BEFORE this driver's own modules are imported, and long before
#: register(): the option schema a module reads at import is the schema for THIS
#: host's capabilities, and a game's shader stack resolves which generated folder
#: is "ours" off the host's name. Both are questions with no answer until a host
#: is bound, which is why the imports below come after this line rather than at
#: the top of the file.
HOST = host_port.bind(BlenderHost())

from ... import Game                                                    # noqa: E402
from . import (rna, render, coordinate, rig_identity, plugin_data, linked_twins, material_builder,  # noqa: E402
               material_panel, derived_state, allocator, animation_builder, step_loader, browser_panel,
               shader_inputs, shader_version, shader_update_panel, post_panel, viewpoint, picture_view,
               region_picker, bin_update)


# ---------------------------------------------------------------------------
# Development reload
# ---------------------------------------------------------------------------
def _holds_process_state(module):
    """Whether a module says it tracks real, expensive-to-rebuild process state.

    A reload resets a module's globals to their source-code defaults even though
    what they were tracking (a process-wide CLR runtime that can never be
    re-claimed once set; a cabmap already paid for with a multi-second load) is
    still very much alive, which both throws that away and desyncs the module's
    "already done" guards from reality. Which modules those are is each module's
    own declaration, not a list kept here: a list here would have to know every
    game's session modules, and would silently reload a new one."""
    return bool(getattr(module, "HOLDS_PROCESS_STATE", False))


#: Driver modules in dependency order, leaves first -- what a reload needs,
#: because a module rebound with ``from .x import name`` captures the name and
#: not the module. Anything under this driver that is NOT listed is still
#: reloaded, just after these, so forgetting one costs ordering rather than
#: correctness.
_DRIVER_ORDER = (rna, render, coordinate, rig_identity, plugin_data, linked_twins, material_builder, material_panel,
                 derived_state, animation_builder, step_loader, browser_panel, shader_inputs, shader_version,
                 shader_update_panel, post_panel, picture_view, region_picker, bin_update)


def _reload_tree(root_name):
    """Reload a package and everything under it, in sys.modules order -- which
    is insertion order, and therefore puts parents before children."""
    for name, module in list(sys.modules.items()):
        if ((name == root_name or name.startswith(root_name + "."))
                and not _holds_process_state(module)):
            importlib.reload(module)


def _reload_games():
    """The game subtree, DEEPEST-first: a game package's GAME_MODULE captures
    both the registry's GameTab/GameModule classes and its own panels' draw
    functions, so it has to be rebuilt after all of them.

    An import that died PARTWAY leaves orphans behind: python drops the package
    it failed on out of sys.modules but keeps whichever children already
    imported, and reloading one of those raises "parent not in sys.modules" --
    which would make a single bad module unrecoverable without restarting the
    host. Drop the orphans instead, so the next import of that package starts
    clean and the real error is the one the user sees."""
    prefix = ADDON + ".Game"
    importlib.reload(sys.modules[prefix])

    def orphaned(name):
        parent = name.rpartition(".")[0]
        while parent.startswith(prefix):
            if parent not in sys.modules:
                return True
            parent = parent.rpartition(".")[0]
        return False

    for name in [key for key in list(sys.modules)
                 if key.startswith(prefix + ".") and orphaned(key)]:
        del sys.modules[name]
    subtree = [entry for entry in sys.modules.items()
               if entry[0].startswith(prefix + ".") and not _holds_process_state(entry[1])]
    for _name, module in sorted(subtree, key=lambda entry: -entry[0].count(".")):
        importlib.reload(module)


def reload_modules():
    """Re-import everything an edit can have changed, so a re-registration
    during development takes effect without restarting Blender.

    Bottom-up: the kernel, then this driver, then the games -- each layer picks
    up the new objects of the one below rather than holding references to the
    previous generation. This module goes last of its own tree, so the driver body
    re-binds against modules that are already new; reloading it mutates it in
    place, which is why the caller's reference to it stays valid and its
    register() is the fresh one."""
    _reload_tree(ADDON + ".Kernel")
    for module in _DRIVER_ORDER:
        importlib.reload(module)
    listed = {module.__name__ for module in _DRIVER_ORDER}
    for name, module in list(sys.modules.items()):
        if (name.startswith(__name__ + ".") and name not in listed
                and not _holds_process_state(module)):
            importlib.reload(module)
    _reload_games()
    importlib.reload(sys.modules[__name__])


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def _on_paths_changed(self, context):
    kernel_bootstrap.republish_paths()


class RuriRipperImporterPreferences(bpy.types.AddonPreferences):
    """Edit > Preferences > Add-ons > RuriRipperImporter. Holds the paths that
    differ per machine (this workspace is synced across machines that check the
    Ruri-RipperHook repo out under different drive letters/paths) instead of them
    being hardcoded -- Blender persists this in the user's saved preferences, so
    it only needs setting once per machine."""
    bl_idname = ADDON

    ripperhook_repo: StringProperty(
        name="Ruri-RipperHook Bin Dir",
        subtype="DIR_PATH",
        description="The built bin folder that directly contains Ruri.RipperHook.dll, e.g. "
                    "<your Ruri-RipperHook checkout>/Source/0Bins/Debug. Every reader builds "
                    "into it -- Unity through the kernel, Unreal through the module beside it -- "
                    "so this one path is all there is to set",
        update=_on_paths_changed)

    ripperhook_auto_update: BoolProperty(
        name="Keep RipperHook Up To Date",
        description="Check for a newer RipperHook build when Blender starts and whenever the reader "
                    "fails to start, and move the bin dir to it. A bin dir that is not one this add-on "
                    "downloaded is yours and is never touched",
        default=True)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "ripperhook_repo")
        by_hand = bin_update.by_hand(self)
        row = layout.row()
        row.enabled = not by_hand and not bin_update.running()
        row.operator(bin_update.RURI_OT_UpdateRipperHook.bl_idname, icon="IMPORT")
        if by_hand:
            layout.label(text="Bin Dir is set by hand, so it is never downloaded over or changed. "
                              "Clear it to let RipperHook be downloaded and kept up to date.", icon="INFO")
        else:
            layout.prop(self, "ripperhook_auto_update")
        if bin_update.message():
            layout.label(text=bin_update.message(), icon="ERROR" if bin_update.failed() else "INFO")
        layout.label(text="Point this at the folder every reader builds into "
                          "(e.g. .../Ruri-RipperHook/Source/0Bins/Debug).", icon="INFO")


def register():
    bpy.utils.register_class(RuriRipperImporterPreferences)
    bin_update.register()
    # The records every panel state shares, before the first state that contains one.
    rna.register_shared()
    # ⛔ 插件自己的数据严禁写进 .blend:存盘前的守卫。
    plugin_data.register()
    # link 进来的材质画的是本会话编的替身;存盘那一刻本地用户指回库里的材质。
    linked_twins.register()
    browser_panel.register()
    # 派生态调度器:导入产物、灯、相机的变更从这里统一收敛成一次重建。装在游戏之前,
    # 这样一个游戏的着色栈注册进来的阶段第一次被用到时,调度器已经在监听了。
    derived_state.register()
    # 开文件时先由派生态现建(后台当场落地),再把旧文件释放掉的内存还给系统。
    allocator.register()
    # 合成树读的视点:界面里跟着用户正在转的那个 3D 视图走,文件换了 / 撤销了就重新认领。
    viewpoint.register()
    # 材质参数面板:每个生成着色栈把自己的接口 + 读写路径注册进来(Game.register 里发生),
    # 所以这一格要先立起来。面板本体全场只有一个,画的是选中网格持有的那张材质。
    material_panel.register()
    picture_view.register()
    region_picker.register()
    # Every game folder under Game/ registers itself; the panel above names none
    # of them and simply draws whatever tabs the enabled hooks turn on.
    Game.register()
    # The top of the Look tab: after Game, because it lists the shader packages Game registered, and before
    # the sections below so that it is the first thing the tab says.
    shader_update_panel.register()
    # Drawn after Game so a stage registered by a game's shader package is
    # already there to be listed.
    post_panel.register()
    # Every command and surface anything declared, as Blender classes. After the
    # games, because that is when every one of them exists.
    render.register()
    # Keymaps last of all: an entry sets properties on the operator it names.
    browser_panel.register_keymaps()
    # Repairs "action assigned but no slot picked" states after any UI-driven
    # action assignment -- see animation_builder's slotted-action notes (the
    # imported data plays only through its slot, and most UI surfaces outside
    # the Action editor don't auto-pick one).
    animation_builder.register_slot_autofix()
    # Now that the preferences class exists, the machine paths it holds can be
    # read: this pushes them into the bridge and claims the process-wide CLR
    # runtime before any other add-on in this profile triggers its own lazy
    # `import clr`.
    kernel_bootstrap.configure()


def unregister():
    browser_panel.unregister_keymaps()
    render.unregister()
    animation_builder.unregister_slot_autofix()
    post_panel.unregister()
    shader_update_panel.unregister()
    Game.unregister()
    region_picker.unregister()
    picture_view.unregister()
    material_panel.unregister()
    viewpoint.unregister()
    allocator.unregister()
    derived_state.unregister()
    browser_panel.unregister()
    linked_twins.unregister()
    plugin_data.unregister()
    rna.unregister_shared()
    bin_update.unregister()
    bpy.utils.unregister_class(RuriRipperImporterPreferences)
