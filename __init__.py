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
import sys

from . import (Game, coordinate, hierarchy, armature_builder,
               mesh_builder, material_builder, animation_builder,
               prefab_importer, cabmap_panel, cross_game_retarget)
from .RuriRipperPyBridge.runtime import bootstrap, pythonnet_bridge

# Reload submodules on addon re-registration during development -- EXCEPT the
# ones that say they hold real, expensive-to-rebuild process state. A reload
# resets a module's globals to their source-code defaults even though what they
# were tracking (a process-wide CLR runtime that can never be re-claimed once
# set; a cabmap already paid for with a multi-second load) is still very much
# alive, which both throws that away and desyncs the module's "already done"
# guards from reality. Which modules those are is each module's own declaration
# (HOLDS_PROCESS_STATE), not a list kept here: a list here would have to know
# every game's session modules, and would silently reload a new one.
def _holds_process_state(module):
    return bool(getattr(module, "HOLDS_PROCESS_STATE", False))


# Shared package first (in sys.modules order, i.e. parents before children), so
# the add-on modules reloaded after it pick up the new objects rather than
# holding references to the previous generation.
_shared_prefix = __package__ + ".RuriRipperPyBridge."
for _name, _mod in list(sys.modules.items()):
    if _name.startswith(_shared_prefix) and not _holds_process_state(_mod):
        importlib.reload(_mod)
for _mod in (coordinate, hierarchy, armature_builder,
             mesh_builder, material_builder, animation_builder, prefab_importer,
             cabmap_panel, cross_game_retarget):
    importlib.reload(_mod)
# The game subtree goes registry-first then DEEPEST-first: a game package's
# GAME_MODULE captures both the registry's GameTab/GameModule classes and its own
# panels' draw functions, so it has to be rebuilt after all of them.
#
# An import that died PARTWAY leaves orphans behind: python drops the package it
# failed on out of sys.modules but keeps whichever children already imported, and
# reloading one of those raises "parent not in sys.modules" -- which would make a
# single bad module unrecoverable without restarting the host. Drop the orphans
# instead, so the next import of that package starts clean and the real error is
# the one the user sees.
_game_prefix = __package__ + ".Game"
importlib.reload(sys.modules[_game_prefix])
def _orphaned(name):
    parent = name.rpartition(".")[0]
    while parent.startswith(_game_prefix):
        if parent not in sys.modules:
            return True
        parent = parent.rpartition(".")[0]
    return False


for _name in [_key for _key in list(sys.modules) if _key.startswith(_game_prefix + ".") and _orphaned(_key)]:
    del sys.modules[_name]
_game_modules = [_entry for _entry in sys.modules.items()
                 if _entry[0].startswith(_game_prefix + ".")
                 and not _holds_process_state(_entry[1])]
for _name, _mod in sorted(_game_modules, key=lambda entry: -entry[0].count(".")):
    importlib.reload(_mod)

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


_BIN_DIR_HINT = ('Set it in Edit > Preferences > Add-ons > RuriRipperImporter > '
                 '"Ruri-RipperHook Bin Dir", or set the RURI_RIPPERHOOK_BIN '
                 'environment variable.')


def _on_ripperhook_repo_change(self, context):
    pythonnet_bridge.set_bin_dir(self.ripperhook_repo)


class RURI_PG_remembered_game(bpy.types.PropertyGroup):
    """One game's remembered browser inputs, kept in the addon preferences so the
    folder a game was last pointed at survives across .blend files and Blender
    restarts -- what the scene's own per-game config (RURI_PG_game_config, gone the
    moment a file closes) cannot do. Only the paths the user chose per game live
    here; loaded/session state stays on the scene."""
    game_name: StringProperty()
    game_root: StringProperty()
    cabmap_path: StringProperty()
    browsed_dir: StringProperty()


class RuriRipperImporterPreferences(bpy.types.AddonPreferences):
    """Edit > Preferences > Add-ons > RuriRipperImporter. Holds the one path that differs per
    machine (this workspace is synced across machines that check the Ruri-RipperHook repo out
    under different drive letters/paths) instead of it being hardcoded in pythonnet_bridge.py --
    Blender persists this in the user's saved preferences, so it only needs setting once per
    machine."""
    bl_idname = __package__

    # One entry per game the user has ever pointed the panel at -- the persistent
    # memory the per-game browser tabs read on open and write on every path change.
    remembered_games: CollectionProperty(type=RURI_PG_remembered_game)

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


_CLASSES = (RURI_PG_remembered_game, RuriRipperImporterPreferences, IMPORT_OT_unity_asset)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_asset)
    cabmap_panel.register()
    # Every game folder under Game/ registers itself; the panel above names none
    # of them and simply draws whatever tabs the enabled hooks turn on.
    Game.register()
    # Repairs "action assigned but no slot picked" states after any UI-driven
    # action assignment -- see animation_builder's slotted-action notes (the
    # imported data plays only through its slot, and most UI surfaces outside
    # the Action editor don't auto-pick one).
    animation_builder.register_slot_autofix()
    # Push the user's saved bin-dir preference into pythonnet_bridge BEFORE the early CoreCLR
    # claim below, since _dll_dir() (called from claim_runtime_early -> _runtime_config_path)
    # needs it to find Ruri.RipperHook.dll at all. The hint goes with it: the shared bridge
    # has no idea where THIS host keeps that setting, and says so in its error.
    prefs = bpy.context.preferences.addons[__package__].preferences
    pythonnet_bridge.set_bin_dir(prefs.ripperhook_repo)
    pythonnet_bridge.set_bin_dir_hint(_BIN_DIR_HINT)
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
    # Non-blocking: a first-time dependency install can take 10-60s and must not
    # freeze Blender's UI. The N-panel gates on bootstrap.is_ready() until this
    # finishes. on_ready closes the one window the synchronous claim above
    # cannot cover: pythonnet only becoming importable partway through the
    # session, after that claim already no-opped.
    bootstrap.ensure_async(report_fn=print, on_ready=pythonnet_bridge.claim_runtime_early)


def unregister():
    animation_builder.unregister_slot_autofix()
    Game.unregister()
    cabmap_panel.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(_menu_asset)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
