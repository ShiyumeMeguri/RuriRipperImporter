"""The Blender driver: this application's answers to :class:`Kernel.host.Host`,
its add-on lifecycle, and the only place ``bpy`` is allowed to appear outside a
game's own panels.

A prefab imports as a full model here: armature from the transform hierarchy,
skinned meshes at the chosen detail level, materials as node graphs, and every
AnimationClip its Animator controller references as an action.
"""

from __future__ import annotations

import importlib
import os
import sys

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ...Kernel import bootstrap as kernel_bootstrap
from ...Kernel import host as host_port
from ...Kernel import options as kernel_options

#: The add-on module name Blender registered, which is what an AddonPreferences
#: is keyed by and what ``preferences.addons`` is looked up with. Derived from
#: this driver's own dotted name so the folder can be renamed without a constant
#: going stale.
ADDON = __package__.split(".")[0]

_BIN_DIR_HINT = ('Set it in Edit > Preferences > Add-ons > RuriRipperImporter > '
                 '"Ruri-RipperHook Bin Dir" (the checkout\'s Source/0Bins/<config>), '
                 'or set the RURI_RIPPERHOOK_BIN environment variable.')


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


class BlenderHost(host_port.Host):
    """What Blender can do, and where Blender keeps the one path that differs
    per machine."""

    name = "Blender"

    #: A skeleton, an animation surface and morph targets -- which is what the
    #: tabs that pose a rig, play a performance or drive a face ask for. The
    #: three Painter answers instead (a bake cache, texture sets, plugin-settable
    #: display) are all no: Blender builds materials as node graphs in memory and
    #: lights its own viewport.
    capabilities = frozenset((
        host_port.SKELETON,
        host_port.ANIMATION,
        host_port.MORPH_TARGETS,
        host_port.COMPOSITOR,
        host_port.SCENE_GRAPH,
        host_port.NODE_MATERIALS,
    ))

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
        active = (context or bpy.context).object
        return active if active is not None and active.type == "ARMATURE" else None

    def clear_scene(self, context):
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)

    def load_display_stage(self, context, stage, options):
        from . import ui_stage
        return ui_stage.load(context, stage, options)

    def write_secondary_motion(self, context, rig, reading):
        from . import cloth_writer
        return cloth_writer.write(context, rig, reading)

    def rig_memory(self, rig):
        """The object itself: Blender's custom properties ARE a mapping on it,
        and they travel with the .blend, which is the whole point."""
        return rig

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

    def import_clips(self, context, clip_cab, clip_guids, database, options,
                     display_names=None, activate=False):
        from . import packages as materialiser
        return materialiser.build_clips(context, clip_cab, clip_guids, database,
                                        options, display_names, activate)

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

    def import_packages(self, context, packages, options=None, report=None,
                        resolved=None):
        from ...Kernel.app import loading
        from . import packages as materialiser
        if resolved is None and packages.kind != loading.PLACEMENTS:
            resolved = loading.resolve_closure(
                packages.cabs, export_class_ids=list(packages.export_class_ids) or None)
        return materialiser.materialise(context, packages, resolved, options, report)


#: Bound BEFORE this driver's own modules are imported, and long before
#: register(): the option schema a module reads at import is the schema for THIS
#: host's capabilities, and a game's shader stack resolves which generated folder
#: is "ours" off the host's name. Both are questions with no answer until a host
#: is bound, which is why the imports below come after this line rather than at
#: the top of the file.
HOST = host_port.bind(BlenderHost())

from ... import Game                                                    # noqa: E402
from . import (rna, render, coordinate, hierarchy, rig_identity, armature_builder,  # noqa: E402
               mesh_builder, material_builder, material_panel, derived_state,
               animation_builder, prefab_importer, filter_ui, step_loader, cabmap_panel,
               cross_game_retarget, post_panel)


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
_DRIVER_ORDER = (rna, render, coordinate, hierarchy, rig_identity, armature_builder, mesh_builder,
                 material_builder, material_panel, derived_state, animation_builder,
                 prefab_importer, filter_ui, step_loader, cabmap_panel, cross_game_retarget,
                 post_panel)


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

    Bottom-up: the data layer, then the kernel, then this driver, then the
    games -- each layer picks up the new objects of the one below rather than
    holding references to the previous generation. This module goes last of its
    own tree, so the driver body re-binds against modules that are already new;
    reloading it mutates it in place, which is why the caller's reference to it
    stays valid and its register() is the fresh one."""
    _reload_tree(ADDON + ".RuriRipperPyBridge")
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
# Preferences and the File > Import entry
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

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "ripperhook_repo")
        layout.label(text="Point this at the folder every reader builds into "
                          "(e.g. .../Ruri-RipperHook/Source/0Bins/Debug).", icon="INFO")


def option_annotations(capabilities):
    """Blender property annotations for every import option this host honours.

    Generated rather than typed out: the schema (Kernel/options.py) is the one
    place an option exists, and a widget that has drifted from what the pipeline
    reads is a switch the user flips for nothing."""
    annotations = {}
    for entry in kernel_options.schema(capabilities):
        if entry.kind == kernel_options.BOOL:
            annotations[entry.key] = BoolProperty(
                name=entry.label, description=entry.description, default=entry.default)
        elif entry.kind == kernel_options.INT:
            keywords = {}
            if entry.minimum is not None:
                keywords["min"] = entry.minimum
            if entry.soft_maximum is not None:
                keywords["soft_max"] = entry.soft_maximum
            annotations[entry.key] = IntProperty(
                name=entry.label, description=entry.description, default=entry.default,
                **keywords)
        else:
            raise TypeError("no Blender property for option kind {0!r} ({1})".format(
                entry.kind, entry.key))
    return annotations


class IMPORT_OT_unity_asset(bpy.types.Operator, ImportHelper):
    """Import a Unity asset: prefab (full model + clips), mesh, anim, or controller."""

    bl_idname = "import_scene.unity_asset"
    bl_label = "Import Unity Asset"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".prefab"
    filter_glob: StringProperty(
        default="*.prefab;*.asset;*.anim;*.controller", options={"HIDDEN"})

    def as_options(self):
        return {entry.key: getattr(self, entry.key)
                for entry in kernel_options.schema(HOST.capabilities)}

    def execute(self, context):
        report = prefab_importer.import_asset(context, self.filepath, self.as_options())
        self.report({"INFO"}, "Unity asset import: " + report.summary())
        for warning in report.warnings[:5]:
            self.report({"WARNING"}, warning)
        return {"FINISHED"}


IMPORT_OT_unity_asset.__annotations__ = dict(
    IMPORT_OT_unity_asset.__annotations__, **option_annotations(HOST.capabilities))


def _menu_asset(self, context):
    self.layout.operator(IMPORT_OT_unity_asset.bl_idname,
                         text="Unity Asset (.prefab / .asset / .anim / .controller)")


_CLASSES = (RuriRipperImporterPreferences, IMPORT_OT_unity_asset)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_asset)
    cabmap_panel.register()
    # 派生态调度器:导入产物、灯、相机的变更从这里统一收敛成一次重建。装在游戏之前,
    # 这样一个游戏的着色栈注册进来的阶段第一次被用到时,调度器已经在监听了。
    derived_state.register()
    # 材质参数面板:每个生成着色栈把自己的接口 + 读写路径注册进来(Game.register 里发生),
    # 所以这一格要先立起来。面板本体全场只有一个,画的是选中网格持有的那张材质。
    material_panel.register()
    # Every game folder under Game/ registers itself; the panel above names none
    # of them and simply draws whatever tabs the enabled hooks turn on.
    Game.register()
    # Drawn after Game so a stage registered by a game's shader package is
    # already there to be listed.
    post_panel.register()
    # Every command anything declared, wrapped as an operator. After the games,
    # because that is when every command exists.
    render.register_commands()
    # Keymaps last of all: an entry sets properties on the operator it names.
    cabmap_panel.register_keymaps()
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
    cabmap_panel.unregister_keymaps()
    render.unregister_commands()
    render.unregister_lists()
    animation_builder.unregister_slot_autofix()
    post_panel.unregister()
    Game.unregister()
    material_panel.unregister()
    derived_state.unregister()
    cabmap_panel.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(_menu_asset)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
