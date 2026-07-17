"""Unity YAML Importer — import Unity serialized prefabs/meshes into Blender.

Reads Unity's text (YAML) ``.prefab`` and Mesh ``.asset`` files directly, with
no Unity install required.  A prefab imports as a full model: armature from the
transform hierarchy, LOD0 skinned meshes, materials with auto-detected base/normal
textures, and every AnimationClip referenced by its Animator controller as an
action.
"""

bl_info = {
    "name": "RuriRipperImporter",
    "author": "ShiyumeMeguri",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > Unity Prefab / Unity Mesh, and 3D Viewport > N-panel > RuriRipper",
    "description": "Import Unity YAML prefabs (skeleton + LOD0 skinned meshes + "
                   "materials + animation clips) and standalone meshes, either from disk "
                   "or, via an in-process pythonnet bridge into Ruri.RipperHook, directly "
                   "from a cabmap-resolved game install with zero intermediate files.",
    "category": "Import-Export",
}

import importlib

from . import (unity_yaml, mesh_decoder, coordinate, asset_db, hierarchy,
               armature_builder, mesh_builder, material_builder,
               animation_builder, prefab_importer, bridge_asset_db,
               pythonnet_bootstrap, pythonnet_bridge, cabmap_state, cabmap_panel,
               scene_state, scene_panel)

# Reload submodules on addon re-registration during development -- EXCEPT the
# ones that hold real, expensive-to-rebuild process state (the claimed CLR
# runtime handle, the loaded bridge/DLL type, the loaded cabmap + its 260k-row
# cache, the discovered-but-not-yet-imported scene placements): a reload
# resets a module's globals to their source-code defaults even though the
# underlying state they were tracking (a process-wide CLR runtime that can
# never be re-claimed once set; a cabmap already paid for with a
# multi-second load) is still very much alive. Reloading them on every
# re-register both throws away that live state for no reason AND desyncs
# their "already done" guards from reality (this is exactly what caused a
# second, spurious set_runtime() attempt after a Reload-Scripts-triggered
# reload). cabmap_panel/scene_panel are safe to reload -- just UI/operator
# code, no state of their own (PropertyGroup data lives on bpy.types.Scene).
_STATEFUL_MODULES = (pythonnet_bootstrap, pythonnet_bridge, cabmap_state, scene_state)
for _mod in (unity_yaml, mesh_decoder, coordinate, asset_db, hierarchy,
             armature_builder, mesh_builder, material_builder,
             animation_builder, prefab_importer, bridge_asset_db, cabmap_panel,
             scene_panel):
    importlib.reload(_mod)

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


def _on_ripperhook_repo_change(self, context):
    pythonnet_bridge.set_repo_root(self.ripperhook_repo)


class RuriRipperImporterPreferences(bpy.types.AddonPreferences):
    """Edit > Preferences > Add-ons > RuriRipperImporter. Holds the one path that differs per
    machine (this workspace is synced across machines that check the Ruri-RipperHook repo out
    under different drive letters/paths) instead of it being hardcoded in pythonnet_bridge.py --
    Blender persists this in the user's saved preferences, so it only needs setting once per
    machine."""
    bl_idname = __package__

    ripperhook_repo: StringProperty(
        name="Ruri-RipperHook Bin Dir",
        subtype="DIR_PATH",
        description="The built bin folder that directly contains Ruri.RipperHook.dll, e.g. "
                    "<your Ruri-RipperHook checkout>/AssetRipper/Source/0Bins/AssetRipper/Debug",
        update=_on_ripperhook_repo_change)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "ripperhook_repo")
        layout.label(text="Point this directly at the folder containing the built Ruri.RipperHook.dll "
                          "(e.g. .../AssetRipper/Source/0Bins/AssetRipper/Debug).", icon="INFO")


class _ImportOptionsMixin:
    lod0_only: BoolProperty(
        name="LOD0 Only",
        description="When a LODGroup is present, import only the highest-quality "
                    "LOD0 renderers and discard the rest",
        default=True)
    import_materials: BoolProperty(name="Import Materials", default=True)
    import_textures: BoolProperty(name="Import Textures", default=True)
    import_skeleton: BoolProperty(name="Import Skeleton", default=True)
    import_animations: BoolProperty(name="Import Animations", default=True)
    import_normals: BoolProperty(name="Import Stored Normals", default=True)
    import_colors: BoolProperty(name="Import Vertex Colors", default=True)
    import_blendshapes: BoolProperty(name="Import Blendshapes", default=True)
    flip_v: BoolProperty(name="Flip UV V", default=False)
    endfield_ik: BoolProperty(
        name="EndField IK (constraints)", default=False,
        description="On rigs exposing EndField's IK_* target bones, set up a "
                    "posing-aid rig: live IK/Copy-Rotation constraints (plus "
                    "four hidden RuriIK.* effector helper bones) targeting "
                    "the clip's animated IK bones, ALL at influence 0 -- "
                    "playback stays bit-identical raw FK until you raise a "
                    "constraint's influence yourself. Off (the default) adds "
                    "nothing at all")

    def as_options(self):
        return {
            "lod0_only": self.lod0_only,
            "import_materials": self.import_materials,
            "import_textures": self.import_textures,
            "import_skeleton": self.import_skeleton,
            "import_animations": self.import_animations,
            "import_normals": self.import_normals,
            "import_colors": self.import_colors,
            "import_blendshapes": self.import_blendshapes,
            "flip_v": self.flip_v,
            "endfield_ik": self.endfield_ik,
        }


class IMPORT_OT_unity_asset(bpy.types.Operator, ImportHelper, _ImportOptionsMixin):
    """Import a Unity asset: prefab (full model + clips), mesh, anim, or controller."""

    bl_idname = "import_scene.unity_asset"
    bl_label = "Import Unity Asset"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".prefab"
    filter_glob: StringProperty(
        default="*.prefab;*.asset;*.anim;*.controller", options={"HIDDEN"})

    def execute(self, context):
        report = prefab_importer.import_asset(context, self.filepath, self.as_options())
        self.report({"INFO"}, "Unity asset import: " + report.summary())
        for warning in report.warnings[:5]:
            self.report({"WARNING"}, warning)
        return {"FINISHED"}


def _menu_asset(self, context):
    self.layout.operator(IMPORT_OT_unity_asset.bl_idname,
                         text="Unity Asset (.prefab / .asset / .anim / .controller)")


_CLASSES = (RuriRipperImporterPreferences, IMPORT_OT_unity_asset)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_asset)
    cabmap_panel.register()
    scene_panel.register()
    # Repairs "action assigned but no slot picked" states after any UI-driven
    # action assignment -- see animation_builder's slotted-action notes (the
    # imported data plays only through its slot, and most UI surfaces outside
    # the Action editor don't auto-pick one).
    animation_builder.register_slot_autofix()
    # Push the user's saved repo-path preference into pythonnet_bridge BEFORE the early CoreCLR
    # claim below, since _dll_dir() (called from claim_runtime_early -> _runtime_config_path)
    # needs it to find Ruri.RipperHook.dll at all.
    prefs = bpy.context.preferences.addons[__package__].preferences
    pythonnet_bridge.set_repo_root(prefs.ripperhook_repo)
    # Claim the process-wide CLR runtime (CoreCLR) as early as possible, before
    # any other addon in this profile gets a chance to trigger its own lazy
    # `import clr` (which defaults to .NET Framework on Windows and would
    # permanently lock out our net10.0 DLL for the rest of this Blender
    # session -- pythonnet allows exactly one runtime per process). Cheap and
    # synchronous (just registers a config; the actual runtime spins up lazily
    # on first real CLR use) -- a no-op if pythonnet isn't installed yet, or if
    # the repo path preference isn't set yet (_dll_dir() raises, caught below).
    try:
        pythonnet_bridge.claim_runtime_early()
    except Exception as exc:  # best-effort -- _ensure_runtime() retries for real on first use
        print(f"[RuriRipper] early CoreCLR claim skipped: {exc}")
    # Non-blocking: a first-time pythonnet install can take 10-60s and must not
    # freeze Blender's UI. The N-panel gates on pythonnet_bootstrap.is_ready()
    # until this finishes.
    pythonnet_bootstrap.ensure_pythonnet_async(report_fn=print)


def unregister():
    animation_builder.unregister_slot_autofix()
    scene_panel.unregister()
    cabmap_panel.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(_menu_asset)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
