"""N-panel UI: cabmap gate, browsable/filterable/sortable row list, and
import-with-dependencies actions. Mirrors the WinForms 'Virtual Asset List'
browser's feature set (columns, search, tri-state sort, load/import actions)
adapted to Blender's bpy UI toolkit -- right-click context-menu actions become
buttons, since UIList has no native per-row context menu.

Hard gate (no single-file import path exists in this panel at all): every
widget below the cabmap picker lives in a sub-layout with
`enabled = state.loaded`, every operator's poll() re-checks the same flag,
and this module never calls prefab_importer with a user-picked path -- only
with bridge-sourced in-memory data from a resolved cabmap selection.
"""

from __future__ import annotations

import os
import re
import traceback

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                        PointerProperty, StringProperty)

try:
    from . import cabmap_state, pythonnet_bootstrap, pythonnet_bridge, bridge_asset_db, prefab_importer, scene_panel
except ImportError:  # standalone (non-package) testing
    import cabmap_state
    import pythonnet_bootstrap
    import pythonnet_bridge
    import bridge_asset_db
    import prefab_importer
    import scene_panel

_HOOK_IDS_DEFAULT = "EndField_1.3.3"  # pre-ticked on first successful hook refresh, if present
_SORT_COLUMNS = (("name", "Name"), ("type_names", "Type"), ("deps", "Deps"), ("source", "Source"))

# Static EnumProperty item lists (Blender wants a stable list, not a callable, to avoid its
# known dynamic-items string-lifetime footgun) built from cabmap_state's plain-Python field/
# relation/action tables -- single source of truth for both the filter engine and this UI.
_FIELD_ITEMS = [(f, cabmap_state.FIELD_LABELS[f], "") for f in cabmap_state.FILTER_FIELDS]
_RELATION_ITEMS = [(r, cabmap_state.RELATION_LABELS[r], "") for r in cabmap_state.RELATIONS]
_ACTION_ITEMS = [
    ("include", "Include", "Require rows to match this rule (each Include rule further narrows the results)"),
    ("exclude", "Exclude", "Hide rows matching this rule -- always wins over any Include"),
]


def _rebuild_window(state):
    total, window = cabmap_state.display_window()
    state.window.clear()
    for idx, row in window:
        item = state.window.add()
        item.row_index = idx
        item.cab = row["cab"]
        item.name = row["name"]
        item.container = row["container"]
        item.type_names = row["type_names"]
        item.source = row["source"]
        item.deps = row["deps"]
    shown = len(window)
    cap_note = (f" (capped at {cabmap_state.DISPLAY_CAP} -- narrow your search to see the rest)"
                if total > shown else "")
    state.status = f"Showing {shown} / {total} matching virtual files{cap_note}."


def _redraw_all(context):
    screen = getattr(context, "screen", None)
    for area in (screen.areas if screen else []):
        area.tag_redraw()


def _reapply_and_refresh(context):
    """Re-run the filter (quick search AND every Include/Exclude rule) and
    rebuild the displayed window -- call after ANY rule or search change."""
    state = context.scene.ruri_cabmap
    cabmap_state.apply_filter(state.search, state.filter_rules)
    _rebuild_window(state)
    _redraw_all(context)


def _on_search_edit(self, context):
    cabmap_state.schedule_filter(self.search, lambda: (_rebuild_window(context.scene.ruri_cabmap), _redraw_all(context)))


def _on_filter_rule_edit(self, context):
    _reapply_and_refresh(context)


def _hook_ids(state):
    return [item.id for item in state.available_hooks if item.selected]


_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|]')


def _default_cabmap_filename(hook_ids):
    """A sensible default cabmap filename from the checked hook id(s) (e.g.
    "EndField_1.3.3.cabmap", or "EndField_1.3.3+GirlsFrontline2_1.0.cabmap" for
    more than one) -- used to auto-complete the Cabmap field when it's a bare
    folder with no filename (see RURI_OT_build_cabmap)."""
    stem = "+".join(hook_ids) if hook_ids else "output"
    return _FILENAME_UNSAFE.sub("_", stem) + ".cabmap"


def _auto_default_cabmap_filename(state):
    """If state.cabmap_path is a non-empty path that (still) resolves to a bare folder, fill in a
    default filename built from the checked hook(s) -- writes straight onto state.cabmap_path so
    it's what's shown in the field AND what Blender's file-browser popup pre-fills/lets you edit
    the next time the user clicks its folder icon (that browser seeds its filename box from the
    property's CURRENT string value, so the default has to already be in the property before the
    popup opens, not just patched in at Build time). A completely empty cabmap_path is left
    alone here -- there's no folder yet to build a default INTO (see _on_game_root_change, which
    seeds one first). Called from both that callback and RURI_OT_refresh_hooks (refreshes the
    filename once the real hook selection is known)."""
    raw = bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""
    if raw and (raw.endswith(("\\", "/")) or os.path.isdir(raw)):
        state.cabmap_path = os.path.join(raw, _default_cabmap_filename(_hook_ids(state)))


def _resolve_build_output_path(state):
    """Resolve state.cabmap_path into a concrete output FILE path for Build -- belt-and-suspenders
    on top of _auto_default_cabmap_filename (which keeps the field itself defaulted as the user
    goes) in case cabmap_path still ends up bare (e.g. typed/pasted a folder right before
    clicking Build, with no chance for the update callback to run in between). Returns "" if
    there's truly nothing to build a path from."""
    _auto_default_cabmap_filename(state)
    return bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""


def _report_exception(op, prefix, exc):
    """self.report() truncates to one line and str(exc) alone drops the
    exception type + traceback -- print the full traceback to console (where
    it's actually diagnosable) and surface a short, still-useful summary in
    Blender's status bar / info log."""
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


class RURI_PG_cabmap_row(bpy.types.PropertyGroup):
    """One windowed/displayed row -- a small proxy, never the full 260k-row set."""
    row_index: IntProperty()
    cab: StringProperty()
    name: StringProperty()
    container: StringProperty()
    type_names: StringProperty()
    source: StringProperty()
    deps: IntProperty()


class RURI_PG_hook_entry(bpy.types.PropertyGroup):
    """One hook id (e.g. "EndField_1.3.3") as reported live by RipperBlenderBridge.
    ListAvailableHooks() -- see RURI_OT_refresh_hooks. `selected` drives the checkbox in the
    N-panel's Hooks box; multiple can be ticked at once, since Initialize() accepts more than one
    hook id (e.g. a VFS-game hook plus an independent AR_* export-side hook)."""
    id: StringProperty()
    selected: BoolProperty(default=False)


class RURI_PG_filter_rule(bpy.types.PropertyGroup):
    """One Process-Monitor-style Include/Exclude rule -- [Field][Relation][Value]
    then [Include/Exclude], matching cabmap_state.SimpleRule's shape exactly so
    the plain-Python filter engine can consume these PropertyGroup instances
    directly (duck typing: .field/.relation/.value/.action/.enabled)."""
    field: EnumProperty(name="Field", items=_FIELD_ITEMS, update=_on_filter_rule_edit)
    relation: EnumProperty(name="Relation", items=_RELATION_ITEMS, update=_on_filter_rule_edit)
    value: StringProperty(name="Value", update=_on_filter_rule_edit)
    action: EnumProperty(name="Action", items=_ACTION_ITEMS, update=_on_filter_rule_edit)
    enabled: BoolProperty(name="Enabled", default=True, update=_on_filter_rule_edit,
                          description="Untick to keep a rule without deleting it")


class RURI_PG_animation_clip(bpy.types.PropertyGroup):
    """One discovered-but-not-yet-built animation clip -- see
    prefab_importer.discover_clip_refs_from_db. `selected` drives the
    checkbox in RURI_UL_animation_clips; nothing here has been parsed past a
    cheap name/size peek, so ticking a box is free until Import is clicked."""
    guid: StringProperty()
    name: StringProperty()
    size_bytes: IntProperty()
    selected: BoolProperty(default=False)


def _format_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"~{size:.0f}{unit}" if unit == "B" else f"~{size:.1f}{unit}"
        size /= 1024.0
    return f"~{size:.1f}GB"


def _on_game_root_change(self, context):
    """Seed Cabmap with a default file path the first time Game Root is set, so Blender's
    file-browser popup (opened from Cabmap's folder icon) already has a filename pre-filled --
    that popup seeds its filename box from the property's current string, it can't be told a
    default separately. Only fires when Cabmap is still empty -- never overwrites a path the
    user already picked or typed."""
    if not self.cabmap_path and self.game_root:
        self.cabmap_path = self.game_root
        _auto_default_cabmap_filename(self)


class RURI_PG_cabmap(bpy.types.PropertyGroup):
    game_root: StringProperty(name="Game Root", subtype="DIR_PATH",
                              description="The game's install root directory",
                              update=_on_game_root_change)
    cabmap_path: StringProperty(name="Cabmap", subtype="FILE_PATH",
                                description="Existing cabmap FILE to load, or output path to build one -- "
                                            "defaults to a filename built from the checked hook(s), editable")
    available_hooks: CollectionProperty(type=RURI_PG_hook_entry)
    available_hooks_active_index: IntProperty()
    hooks_status: StringProperty(default="Click Refresh to list hooks compiled into Ruri.RipperHook.dll.")
    loaded: BoolProperty(default=False)
    active_tab: EnumProperty(
        name="Tab",
        items=[
            ("assetbundle", "VirtualAssetBundle", "Browse/search the loaded cabmap's rows and import individual assets"),
            ("scene", "Scene", "Discover a whole map's placements and import it in one go"),
        ],
        default="assetbundle")
    search: StringProperty(name="Search", update=_on_search_edit,
                           description="Filter by Name / Container / Source / Type")
    status: StringProperty(default="No cabmap loaded.")
    window: CollectionProperty(type=RURI_PG_cabmap_row)
    active_index: IntProperty()

    filter_rules: CollectionProperty(type=RURI_PG_filter_rule)
    filter_rules_active_index: IntProperty()
    # The rule currently being assembled in the "new rule" builder row (Process
    # Monitor's [Field][Relation][Value] then [Action] + Add).
    new_rule_field: EnumProperty(name="Field", items=_FIELD_ITEMS)
    new_rule_relation: EnumProperty(name="Relation", items=_RELATION_ITEMS, default="contains")
    new_rule_value: StringProperty(name="Value")
    new_rule_action: EnumProperty(name="Action", items=_ACTION_ITEMS)

    lod0_only: BoolProperty(name="LOD0 Only", default=True)
    import_materials: BoolProperty(name="Import Materials", default=True)
    import_textures: BoolProperty(name="Import Textures", default=True)
    import_skeleton: BoolProperty(name="Import Skeleton", default=True)
    import_animations: BoolProperty(
        name="Discover Animations", default=True,
        description="List this character's animation clips in the Animations "
                    "panel below after import. Clips are NOT built until you "
                    "check them there and click Import -- a single clip can "
                    "be 100+MB, so nothing is loaded automatically")

    animation_character_name: StringProperty(default="")
    available_clips: CollectionProperty(type=RURI_PG_animation_clip)
    available_clips_active_index: IntProperty()

    def as_options(self):
        return {
            "lod0_only": self.lod0_only,
            "import_materials": self.import_materials,
            "import_textures": self.import_textures,
            "import_skeleton": self.import_skeleton,
            "import_animations": self.import_animations,
        }


class RURI_OT_refresh_hooks(bpy.types.Operator):
    """Populate the Hooks checklist straight from RipperBlenderBridge.ListAvailableHooks() --
    the C# side's own reflection over every hook type compiled into Ruri.RipperHook.dll. Ticked
    state is preserved across a re-refresh for any id that's still present."""
    bl_idname = "ruri.refresh_hooks"
    bl_label = "Refresh Hooks"
    bl_description = "List the hook ids compiled into Ruri.RipperHook.dll"

    @classmethod
    def poll(cls, context):
        return pythonnet_bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        try:
            hook_ids = pythonnet_bridge.list_available_hooks()
        except Exception as exc:
            _report_exception(self, "Refresh hooks failed", exc)
            return {"CANCELLED"}

        previously_selected = {item.id for item in state.available_hooks if item.selected}
        had_any_before = len(state.available_hooks) > 0
        state.available_hooks.clear()
        for hook_id in hook_ids:
            item = state.available_hooks.add()
            item.id = hook_id
            item.selected = (hook_id in previously_selected
                             or (not had_any_before and hook_id == _HOOK_IDS_DEFAULT))
        state.hooks_status = (f"{len(hook_ids)} hook(s) available." if hook_ids
                              else "No hooks found in Ruri.RipperHook.dll.")
        _auto_default_cabmap_filename(state)
        self.report({"INFO"}, state.hooks_status)
        return {"FINISHED"}


class RURI_OT_build_cabmap(bpy.types.Operator):
    bl_idname = "ruri.build_cabmap"
    bl_label = "Build Cabmap"
    bl_description = "Scan the game root and build a fresh cabmap (can take a long time for a full game)"

    # Zero checked hooks is a VALID configuration, not a missing prerequisite: a plain
    # un-bundled/un-encrypted Unity player build (level0/sharedassetsN.assets/resources.assets)
    # needs no game hook at all -- the generic scan handles it, with readable names harvested
    # straight from the assets' own m_Name fields (GameBundleHook.HarvestAssetNames). Hooks are
    # only for games with custom encryption/VFS/typetree drift.
    @classmethod
    def poll(cls, context):
        return pythonnet_bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        root = bpy.path.abspath(state.game_root) if state.game_root else ""
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Pick a valid game root directory first.")
            return {"CANCELLED"}
        out = _resolve_build_output_path(state)
        if not out:
            self.report({"ERROR"}, "Pick an output path for the cabmap file first.")
            return {"CANCELLED"}
        out_dir = os.path.dirname(out)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            self.report({"ERROR"}, f"Can't create output folder '{out_dir}': {exc}")
            return {"CANCELLED"}
        try:
            bridge = cabmap_state.ensure_bridge(_hook_ids(state))
            code = bridge.build_cab_map(root, out)
            if code != 0:
                self.report({"ERROR"}, f"Build failed (exit {code}) -- see console.")
                return {"CANCELLED"}
            bridge.load_cab_map(out)
            cabmap_state.load_rows()
            _reapply_and_refresh(context)
            state.loaded = True
        except Exception as exc:
            _report_exception(self, "Build cabmap failed", exc)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Cabmap built: {len(cabmap_state.ROWS)} CABs.")
        return {"FINISHED"}


class RURI_OT_load_cabmap(bpy.types.Operator):
    bl_idname = "ruri.load_cabmap"
    bl_label = "Load Cabmap"
    bl_description = "Load an existing cabmap file -- required before browsing/importing anything"

    # Zero checked hooks is valid -- see RURI_OT_build_cabmap.poll.
    @classmethod
    def poll(cls, context):
        return pythonnet_bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        path = bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""
        if not path or not os.path.isfile(path):
            self.report({"ERROR"}, "Pick a valid cabmap file first.")
            return {"CANCELLED"}
        try:
            bridge = cabmap_state.ensure_bridge(_hook_ids(state))
            bridge.load_cab_map(path)
            cabmap_state.load_rows()
            _reapply_and_refresh(context)
            state.loaded = True
        except Exception as exc:
            _report_exception(self, "Load cabmap failed", exc)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Cabmap loaded: {len(cabmap_state.ROWS)} CABs.")
        return {"FINISHED"}


class RURI_OT_cabmap_sort(bpy.types.Operator):
    bl_idname = "ruri.cabmap_sort"
    bl_label = "Sort"
    column: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded

    def execute(self, context):
        cabmap_state.cycle_sort(self.column)
        _rebuild_window(context.scene.ruri_cabmap)
        return {"FINISHED"}


class RURI_OT_filter_add_rule(bpy.types.Operator):
    """Add the rule currently assembled in the builder row (Field/Relation/
    Value/Action) -- the Process Monitor dialog's "Add" button."""
    bl_idname = "ruri.filter_add_rule"
    bl_label = "Add Rule"
    bl_description = "Add this rule to the filter"

    def execute(self, context):
        state = context.scene.ruri_cabmap
        if not state.new_rule_value and state.new_rule_relation not in ("is", "is_not"):
            self.report({"WARNING"}, "Enter a value for the rule first.")
            return {"CANCELLED"}
        rule = state.filter_rules.add()
        rule.field = state.new_rule_field
        rule.relation = state.new_rule_relation
        rule.value = state.new_rule_value
        rule.action = state.new_rule_action
        rule.enabled = True
        state.filter_rules_active_index = len(state.filter_rules) - 1
        state.new_rule_value = ""
        _reapply_and_refresh(context)
        return {"FINISHED"}


class RURI_OT_filter_remove_rule(bpy.types.Operator):
    bl_idname = "ruri.filter_remove_rule"
    bl_label = "Remove Rule"
    bl_description = "Remove this filter rule"
    bl_options = {"INTERNAL"}
    index: IntProperty()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        if 0 <= self.index < len(state.filter_rules):
            state.filter_rules.remove(self.index)
            state.filter_rules_active_index = min(state.filter_rules_active_index, len(state.filter_rules) - 1)
        _reapply_and_refresh(context)
        return {"FINISHED"}


class RURI_OT_filter_clear_rules(bpy.types.Operator):
    bl_idname = "ruri.filter_clear_rules"
    bl_label = "Clear All"
    bl_description = "Remove every filter rule"

    def execute(self, context):
        context.scene.ruri_cabmap.filter_rules.clear()
        _reapply_and_refresh(context)
        return {"FINISHED"}


class RURI_OT_filter_quick_add(bpy.types.Operator):
    """One-click rule-from-a-row, mirroring the WinForms browser's right-click
    'Include > Container contains "..."' quick-filter menu (Blender's UIList
    has no native per-row context menu, so this is invoked from a dropdown
    menu button instead -- see RURI_MT_quick_filter)."""
    bl_idname = "ruri.filter_quick_add"
    bl_label = "Quick Add Rule"
    bl_options = {"INTERNAL"}
    field: EnumProperty(items=_FIELD_ITEMS)
    action: EnumProperty(items=_ACTION_ITEMS)
    value: StringProperty()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        rule = state.filter_rules.add()
        rule.field = self.field
        rule.relation = "is" if self.field == "deps" else "contains"
        rule.value = self.value
        rule.action = self.action
        rule.enabled = True
        state.filter_rules_active_index = len(state.filter_rules) - 1
        _reapply_and_refresh(context)
        return {"FINISHED"}


class RURI_MT_quick_filter(bpy.types.Menu):
    """Dynamically built from the selected row's actual values -- Include/
    Exclude x Name/Container/Type/Source/Deps, ten one-click actions total,
    exactly the Process Monitor right-click pattern."""
    bl_idname = "RURI_MT_quick_filter"
    bl_label = "Quick Filter Selected Row"

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap
        if not (0 <= state.active_index < len(state.window)):
            layout.label(text="No row selected", icon="INFO")
            return
        row = state.window[state.active_index]
        for action_id, action_label, _desc in _ACTION_ITEMS:
            layout.label(text=action_label + ":")
            for field_id, field_label, _fdesc in _FIELD_ITEMS:
                value = str(getattr(row, field_id))
                display = value if len(value) <= 40 else value[:37] + "..."
                relation_word = "is" if field_id == "deps" else "contains"
                op = layout.operator(RURI_OT_filter_quick_add.bl_idname,
                                     text=f"{field_label} {relation_word} \"{display}\"")
                op.field = field_id
                op.action = action_id
                op.value = value
            if action_id != _ACTION_ITEMS[-1][0]:
                layout.separator()


def _selected_row(state):
    if 0 <= state.active_index < len(state.window):
        return state.window[state.active_index]
    return None


def _is_clip_only_row(row):
    """A browser row that hosts AnimationClips and no importable GameObject
    hierarchy -- selecting it and clicking Import means "import these clips",
    not "import a prefab" (a clip CAB's closure contains no .prefab at all;
    confirmed against the real game, its dependency count is literally 0).
    Covers both a bundled clip CAB and a non-bundled per-asset AnimationClip
    row (see prefab_importer/ReadFullMetadataRows -- "<file>::<pathID>")."""
    return "AnimationClip" in row.type_names and "GameObject" not in row.type_names


def _import_single_asset(op, context, state, db, textures, guid, class_name, name):
    """Import exactly one non-hierarchy asset resolved from a per-asset browser
    row (a non-bundled file's Mesh/Material/Texture2D, keyed "<file>::<pathID>")
    -- the asset-level granularity a plain player build browses at. Clips and
    GameObject hierarchies never reach here (dispatched earlier)."""
    if class_name == "Mesh":
        mesh_file = db.load_guid(guid)
        if mesh_file is None:
            op.report({"ERROR"}, "Resolved mesh document failed to parse -- see console.")
            return {"CANCELLED"}
        report = prefab_importer.import_mesh_from_db(context, db, mesh_file, state.as_options())
        for warning in report.warnings[:5]:
            op.report({"WARNING"}, warning)
        if not report.mesh_objects:
            op.report({"ERROR"}, "Mesh decoded empty -- see console.")
            return {"CANCELLED"}
        op.report({"INFO"}, f"Imported mesh '{name}'.")
        return {"FINISHED"}

    if class_name == "Material":
        try:
            from . import material_builder
        except ImportError:
            import material_builder
        builder = material_builder.MaterialBuilder(db, prefab_importer._resolve_options(state.as_options()))
        mat = builder.build_from_ref({"guid": guid})
        if mat is None:
            op.report({"ERROR"}, "Material failed to build -- see console.")
            return {"CANCELLED"}
        op.report({"INFO"}, f"Imported material '{mat.name}' (browse it in the material list).")
        return {"FINISHED"}

    if class_name is None and guid in textures:
        # Texture rows resolve to PNG bytes, not a YAML document.
        import tempfile
        png = textures[guid]
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            tmp.write(png)
            tmp.close()
            image = bpy.data.images.load(tmp.name)
            image.name = name
            image.pack()
            image.filepath = ""
        finally:
            os.unlink(tmp.name)
        op.report({"INFO"}, f"Imported texture '{name}' (packed into this .blend, see the image list).")
        return {"FINISHED"}

    op.report({"ERROR"}, f"Row resolved to a {class_name or 'non-document'} asset -- no importer "
                         f"for this type yet (meshes, materials, textures, clips, and GameObject "
                         f"hierarchies are supported).")
    return {"CANCELLED"}


def _resolve_target_armature(context):
    """The armature a standalone clip import should drive, plus its rebuilt
    maps: the user's ACTIVE armature first (their explicit choice), else the
    scene's only armature (unambiguous). maps come from the Unity rig identity
    stamped on the armature at import time (any session), falling back to the
    live import-session state when the stamp predates the feature. Returns
    (arm_obj, maps) or (None, error_message)."""
    candidates = []
    active = context.active_object
    if active is not None and active.type == "ARMATURE":
        candidates.append(active)
    else:
        scene_arms = [o for o in context.scene.objects if o.type == "ARMATURE"]
        if len(scene_arms) == 1:
            candidates.append(scene_arms[0])
        elif not scene_arms:
            return None, ("No armature in the scene -- import the character first, or "
                          "select the skeleton the animation should drive.")
        else:
            return None, ("Multiple armatures in the scene -- select the one the "
                          "animation should drive, then retry.")

    arm_obj = candidates[0]
    maps = prefab_importer.maps_from_stamped_armature(arm_obj)
    if maps is None:
        build_state = cabmap_state.ANIMATION_BUILD_STATE
        if (build_state is not None and build_state.get("arm_name") == arm_obj.name
                and build_state.get("maps") is not None):
            maps = build_state["maps"]
    if maps is None:
        return None, (f"Armature '{arm_obj.name}' carries no Unity rig identity (imported "
                      f"before this feature, or by another tool) -- re-import the character "
                      f"once, then animations attach to it standalone from then on.")
    return arm_obj, maps


def _import_clips_standalone(op, context, state, clip_cab, clip_guids, db):
    """Shared tail of both standalone clip flows (a clip-only row through
    Import (Append)/(Reset Scene), and Import Checked Animations discovered
    off a clip-only row): resolve the target armature from the user's
    selection, verify the clips actually fit that skeleton (path/CRC32 match
    against the armature's own bone paths), then build actions. Returns the
    operator result set."""
    arm_obj, maps_or_error = _resolve_target_armature(context)
    if arm_obj is None:
        op.report({"ERROR"}, maps_or_error)
        return {"CANCELLED"}
    maps = maps_or_error

    # Humanoid support: without a muscle retargeter, a humanoid clip (body
    # motion = muscle float curves, not transform curves) imports as an
    # almost-motionless action. Sources, in order: any USABLE Avatar in this
    # closure (co-seeded rig-FBX CABs; stubs are auto-skipped by content
    # probing), else the avatar YAML stamped onto the target armature at
    # character-import time -- battle clips' own dependency neighborhood
    # contains NO character rig at all (their controller is attached by game
    # code, not bundle dependencies; verified: its only Avatar is a weapon
    # stub), so the referential travels with the skeleton instead.
    # build_action self-gates: generic clips are untouched by the
    # retargeter's presence.
    if maps.get("retargeter") is None:
        retargeter = (prefab_importer.find_retargeter_in_db(db, maps["path_to_bone"])
                      or prefab_importer.retargeter_from_stamped_armature(
                          arm_obj, maps["path_to_bone"]))
        if retargeter is not None:
            maps = dict(maps)
            maps["retargeter"] = retargeter

    # Compatibility gate: at least one transform curve must resolve to a bone
    # of the chosen armature (string path or CRC32-of-path match). A clip for
    # a completely different rig fails loudly instead of importing a no-op.
    path_to_bone = maps["path_to_bone"]
    any_ratio = 0.0
    checked_any = False
    for guid in clip_guids:
        clip_file = db.load_guid(guid)
        clip_doc = clip_file.first("AnimationClip") if clip_file is not None else None
        if clip_doc is None:
            continue
        ratio, total = prefab_importer.clip_path_match_ratio(clip_doc.data, path_to_bone)
        if total:
            checked_any = True
            any_ratio = max(any_ratio, ratio)
    if checked_any and any_ratio == 0.0:
        op.report({"ERROR"}, f"None of the clip's curve paths match armature "
                             f"'{arm_obj.name}' (wrong character?) -- select the right "
                             f"skeleton and retry.")
        return {"CANCELLED"}
    if checked_any and any_ratio < 0.5:
        op.report({"WARNING"}, f"Only {any_ratio:.0%} of curve paths match armature "
                               f"'{arm_obj.name}' -- importing anyway.")

    try:
        built, warnings = prefab_importer.build_selected_animations(
            db, arm_obj, maps, None, clip_guids, state.as_options())
    except Exception as exc:
        _report_exception(op, "Animation import failed", exc)
        return {"CANCELLED"}
    for warning in warnings[:5]:
        op.report({"WARNING"}, warning)
    op.report({"INFO"}, f"Built {built} animation action(s) on {arm_obj.name}.")
    return {"FINISHED"}


class RURI_OT_import_selected(bpy.types.Operator):
    bl_idname = "ruri.import_selected"
    bl_label = "Import Selected"
    bl_description = "Resolve the selected row's dependency closure in memory and import it into the scene"
    bl_options = {"REGISTER", "UNDO"}
    reset_scene: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_cabmap
        selected_row = _selected_row(state)
        if selected_row is None:
            self.report({"WARNING"}, "No row selected.")
            return {"CANCELLED"}

        # A clip-only row is an ANIMATION import, not a prefab import: its closure
        # contains no .prefab at all (the old flow's "No importable (.prefab) asset
        # found" dead end). Resolve it onto the user's selected skeleton instead --
        # co-seeding the rig-FBX CAB (found through the cabmap's own dependency
        # graph, nearest Animator dependent's forward closure) so AssetRipper
        # restores the clips' hashed curve paths to real strings during export.
        if _is_clip_only_row(selected_row):
            if self.reset_scene:
                self.report({"ERROR"}, "An animation needs an existing skeleton -- use "
                                       "Import (Append) so the armature survives.")
                return {"CANCELLED"}
            seeds = [selected_row.cab]
            try:
                seeds.extend(cabmap_state.BRIDGE.find_associated_avatar_cabs(selected_row.cab))
                documents, textures, roots, seed_roots, clips_by_cab, _scene_roots = \
                    cabmap_state.BRIDGE.import_cabs(seeds)
            except Exception as exc:
                _report_exception(self, "Import (bridge) failed", exc)
                return {"CANCELLED"}
            clip_guids = clips_by_cab.get(selected_row.cab.lower(), [])
            if not clip_guids:
                self.report({"ERROR"}, "The resolved closure exported no AnimationClip for "
                                       "this row -- see console.")
                return {"CANCELLED"}
            db = bridge_asset_db.BridgeAssetDatabase(documents, textures)
            return _import_clips_standalone(self, context, state, selected_row.cab,
                                            clip_guids, db)

        cabs = [selected_row.cab]
        if self.reset_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        try:
            documents, textures, roots, seed_roots, _clips_by_cab, scene_roots = \
                cabmap_state.BRIDGE.import_cabs(cabs)
        except Exception as exc:
            _report_exception(self, "Import (bridge) failed", exc)
            return {"CANCELLED"}

        db = bridge_asset_db.BridgeAssetDatabase(documents, textures)
        options = state.as_options()
        imported = 0
        # A single selected row's dependency closure routinely resolves to MORE than one root
        # .prefab (e.g. an actor prefab pulling in a separate portrait/"uimodel" variant as a
        # second top-level asset) -- and since discover_clip_refs_from_db's fallback scans the
        # WHOLE shared closure db, every co-resolved root can end up reporting the SAME clip set,
        # not a distinguishing one. Attribute the animation browser to whichever imported root IS
        # the selected row's own asset, per seed_roots -- resolved bridge-side directly through
        # the cabmap's own CAB/addressable-path identity (see RipperBridge.import_cabs), not by
        # matching display names against GameObject names (two identifiers Unity gives no
        # guarantee ever equal each other, even for a totally unambiguous single-root import).
        primary_guid = seed_roots.get(selected_row.cab)
        is_asset_row = "::" in selected_row.cab  # per-asset virtual row of a non-bundled file

        # A per-asset row that resolved to a NON-hierarchy asset (Mesh/Material/Texture2D)
        # imports exactly that one asset -- the whole point of asset-level rows. This MUST
        # dispatch before the roots check below: a lone mesh/texture closure legitimately
        # exports zero .prefab/.unity roots.
        if is_asset_row and primary_guid is not None:
            text = db.raw_text(primary_guid)
            class_name = prefab_importer._peek_class_and_name(text)[0] if text else None
            if class_name != "GameObject":
                return _import_single_asset(self, context, state, db, textures,
                                            primary_guid, class_name, selected_row.name)
        if is_asset_row and primary_guid is None:
            self.report({"ERROR"}, "This asset didn't export as its own file (engine built-in, or "
                                   "embedded in a scene/host hierarchy) -- import its host file row "
                                   "instead.")
            return {"CANCELLED"}

        if not roots:
            self.report({"WARNING"}, "No importable (.prefab/.unity) asset found in the resolved closure.")
            return {"CANCELLED"}

        # A non-bundled level file resolves to ITS OWN scene root, and a per-asset GameObject row
        # to its own prefab: import exactly that root. Without this, selecting level0 would also
        # drag in every shared .prefab the whole dependency closure exports (a plain build's
        # sharedassets host dozens of unrelated GameObject hierarchies) -- the scene already
        # instantiates everything it actually uses.
        import_roots = roots
        if primary_guid is not None and (primary_guid in scene_roots or is_asset_row):
            import_roots = [primary_guid]

        primary_report = None
        for root_guid in import_roots:
            prefab_file = db.load_guid(root_guid)
            if prefab_file is None:
                continue
            report = prefab_importer.import_prefab_from_db(context, db, prefab_file, options)
            imported += 1
            for warning in report.warnings[:5]:
                self.report({"WARNING"}, warning)
            if root_guid == primary_guid:
                primary_report = report

        if primary_report is None and imported > 0:
            self.report({"WARNING"}, "Could not match an imported root back to the selected row -- "
                                     "animation browser not populated.")
        _populate_animation_browser(state, primary_report)
        self.report({"INFO"}, f"Imported {imported} asset(s) from {len(cabs)} selected row(s).")
        return {"FINISHED"}


def _populate_animation_browser(state, report):
    """Refresh the Animations sub-panel from a just-finished import's report
    (or clear it out on multi-character imports / imports with no armature,
    where per-character clip browsing doesn't apply)."""
    state.available_clips.clear()
    state.animation_character_name = ""
    cabmap_state.clear_animation_build_state()
    if report is None or report.armature is None or not report.available_clips:
        return
    state.animation_character_name = report.armature.name
    for ref in report.available_clips:
        item = state.available_clips.add()
        item.guid = ref["guid"]
        item.name = ref["name"]
        item.size_bytes = ref["size_bytes"]
    cabmap_state.set_animation_build_state(
        report.db, report.armature.name, report.maps, report.path_to_meshobjects)


class RURI_UL_hooks(bpy.types.UIList):
    """Checkbox-per-hook list -- template_list gives this a fixed, scrollable height
    (see RURI_PT_cabmap.draw's rows=) instead of the box growing to fit every hook id
    Ruri.RipperHook.dll reports, which gets long once more than a couple games are hooked."""
    bl_idname = "RURI_UL_hooks"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        layout.prop(item, "selected", text=item.id)


class RURI_UL_cabmap(bpy.types.UIList):
    bl_idname = "RURI_UL_cabmap"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        split = layout.split(factor=0.34)
        split.label(text=item.name or item.cab)
        rest = split.split(factor=0.32)
        rest.label(text=item.type_names)
        tail = rest.split(factor=0.15)
        tail.label(text=str(item.deps))
        tail.label(text=item.source)


class RURI_UL_animation_clips(bpy.types.UIList):
    """Checkbox-per-clip list for the Animations sub-panel. Uses Blender's
    built-in name filter (the funnel icon) rather than a hand-rolled search --
    a character's own clip count is small enough (tens, not the cabmap's
    260k rows) that no debouncing/windowing is needed here."""
    bl_idname = "RURI_UL_animation_clips"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.name)
        # size_bytes is 0 for a cheaply-discovered-but-not-yet-resolved clip
        # (see RURI_OT_discover_animations -- pure cabmap metadata has no
        # per-asset byte size); showing "0 B" would misleadingly read as an
        # empty clip rather than "size not known yet."
        row.label(text=_format_size(item.size_bytes) if item.size_bytes > 0 else "size unknown")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = bpy.types.UI_UL_list.filter_items_by_name(
            self.filter_name, self.bitflag_filter_item, items, "name")
        order = bpy.types.UI_UL_list.sort_items_by_name(items, "name") if self.use_filter_sort_alpha else []
        return flags, order


class RURI_PT_filter_popover(bpy.types.Panel):
    """Process-Monitor-style Include/Exclude rule editor, Blender-native as a
    popover (the same idiom the Outliner's own funnel-icon filter uses)
    rather than a cramped multi-column row squeezed into the narrow N-panel
    sidebar -- opened from the funnel button next to the search box.
    Non-modal and live-apply: every edit re-filters immediately, no
    OK/Cancel/Apply step."""
    bl_idname = "RURI_PT_filter_popover"
    bl_label = "Filter Rules"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_ui_units_x = 20

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap

        col = layout.column(align=True)
        col.label(text="Display rows matching ALL these conditions:")
        sub = col.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="(no rules ⇒ show all; every enabled rule", icon="BLANK1")
        sub.label(text="must hold -- Include requires a match,", icon="BLANK1")
        sub.label(text="Exclude requires a non-match)", icon="BLANK1")

        layout.separator()
        builder = layout.column(align=True)
        builder.prop(state, "new_rule_field", text="")
        builder.prop(state, "new_rule_relation", text="")
        builder.prop(state, "new_rule_value", text="", icon="GREASEPENCIL")
        row = builder.row(align=True)
        row.prop(state, "new_rule_action", text="")
        row.operator(RURI_OT_filter_add_rule.bl_idname, text="Add", icon="ADD")

        layout.separator()
        if len(state.filter_rules) == 0:
            layout.label(text="No rules yet.", icon="INFO")
        else:
            rules_box = layout.column(align=True)
            for index, rule in enumerate(state.filter_rules):
                row = rules_box.row(align=True)
                row.prop(rule, "enabled", text="")
                sub = row.row(align=True)
                sub.scale_x = 0.9
                sub.prop(rule, "field", text="")
                sub.prop(rule, "relation", text="")
                row.prop(rule, "value", text="")
                icon = "ADD" if rule.action == "include" else "REMOVE"
                sub2 = row.row(align=True)
                sub2.scale_x = 0.7
                sub2.prop(rule, "action", text="", icon=icon)
                remove = row.operator(RURI_OT_filter_remove_rule.bl_idname, text="", icon="X")
                remove.index = index

            layout.separator()
            layout.operator(RURI_OT_filter_clear_rules.bl_idname, icon="TRASH")


class RURI_PT_cabmap(bpy.types.Panel):
    bl_idname = "RURI_PT_cabmap"
    bl_label = "RuriRipper"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap

        if not pythonnet_bootstrap.is_ready():
            err = pythonnet_bootstrap.last_error()
            if err:
                layout.label(text="pythonnet install failed:", icon="ERROR")
                layout.label(text=err[:60])
            else:
                layout.label(text="Installing pythonnet bridge...", icon="INFO")
            return

        top = layout.column()
        hooks_box = top.box()
        hooks_header = hooks_box.row(align=True)
        hooks_header.label(text="Hooks")
        hooks_header.operator(RURI_OT_refresh_hooks.bl_idname, text="", icon="FILE_REFRESH")
        if not state.available_hooks:
            hooks_box.label(text=state.hooks_status, icon="INFO")
        else:
            hooks_box.template_list(RURI_UL_hooks.bl_idname, "", state, "available_hooks",
                                    state, "available_hooks_active_index", rows=6)
        top.prop(state, "game_root")
        top.prop(state, "cabmap_path")
        row = top.row(align=True)
        row.operator(RURI_OT_build_cabmap.bl_idname, text="Build")
        row.operator(RURI_OT_load_cabmap.bl_idname, text="Load")

        layout.separator()
        gated = layout.column()
        gated.enabled = state.loaded
        if not state.loaded:
            layout.label(text="Build or load a cabmap to browse/import.", icon="LOCKED")

        tabs = gated.row(align=True)
        tabs.prop(state, "active_tab", expand=True)

        if state.active_tab == "assetbundle":
            search_row = gated.row(align=True)
            search_row.prop(state, "search", icon="VIEWZOOM")
            active_rules = sum(1 for r in state.filter_rules if r.enabled)
            # Text badge (active rule count) carries the "filter active" signal instead of a second
            # icon -- keeps this to icons actually in Blender's icon set.
            search_row.popover(RURI_PT_filter_popover.bl_idname,
                               text=str(active_rules) if active_rules else "", icon="FILTER")

            sort_col, sort_dir = cabmap_state.sort_state()
            sort_row = gated.row(align=True)
            for col_key, col_label in _SORT_COLUMNS:
                arrow = ""
                if sort_col == col_key:
                    arrow = " ▲" if sort_dir == 1 else (" ▼" if sort_dir == 2 else "")
                op = sort_row.operator(RURI_OT_cabmap_sort.bl_idname, text=col_label + arrow)
                op.column = col_key

            gated.template_list(RURI_UL_cabmap.bl_idname, "", state, "window",
                                state, "active_index", rows=12)
            row = gated.row(align=True)
            row.label(text=state.status)
            row.menu(RURI_MT_quick_filter.bl_idname, text="", icon="COLLAPSEMENU")

            opts = gated.box()
            opts.prop(state, "lod0_only")
            opts.prop(state, "import_materials")
            opts.prop(state, "import_textures")
            opts.prop(state, "import_skeleton")

            actions = gated.row(align=True)
            op = actions.operator(RURI_OT_import_selected.bl_idname, text="Import (Append)")
            op.reset_scene = False
            op = actions.operator(RURI_OT_import_selected.bl_idname, text="Import (Reset Scene)")
            op.reset_scene = True
        else:
            scene_panel.draw_scene_tab(gated, context)


class RURI_OT_discover_animations(bpy.types.Operator):
    """Cheap animation-clip discovery for the selected row: walks the
    ALREADY-LOADED cabmap's own dependency graph (CabMap.
    ResolveClosureCabNames -- pure in-memory, no VFS decrypt, no AssetRipper
    export) and filters to CABs whose TypeNames (also already loaded, per
    CAB) include AnimationClip. No db is resolved at this point -- a clip's
    guid is only ever needed once the user actually checks it and clicks
    Import Checked Animations, which is also the first point anything gets
    exported/built at all, including the character itself."""
    bl_idname = "ruri.discover_animations"
    bl_label = "Discover Animations"
    bl_description = "List this row's animation clips from the cabmap's own dependency graph -- cheap, nothing exported/built yet"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_cabmap
        return (state.loaded and cabmap_state.BRIDGE is not None
                and 0 <= state.active_index < len(state.window))

    def execute(self, context):
        state = context.scene.ruri_cabmap
        selected_row = _selected_row(state)
        try:
            closure_cabs = cabmap_state.BRIDGE.resolve_closure_cab_names([selected_row.cab])
        except Exception as exc:
            _report_exception(self, "Discover animations failed", exc)
            return {"CANCELLED"}

        rows_by_cab = cabmap_state.rows_by_cab()
        clip_rows = [rows_by_cab[cab] for cab in closure_cabs
                     if cab in rows_by_cab and "AnimationClip" in rows_by_cab[cab]["type_names"]]
        clip_rows.sort(key=lambda r: r["name"].lower())

        state.available_clips.clear()
        state.animation_character_name = selected_row.name
        for row in clip_rows:
            item = state.available_clips.add()
            # A CAB name for now, not a real Unity guid -- translated to real
            # clip guid(s) through the export's own clips_by_cab capture once
            # the lazy build below actually resolves this closure (a clip
            # CAB's fbx display name and its clips' m_Names genuinely differ,
            # and one CAB can host several clips -- identity, never names).
            item.guid = row["cab"]
            item.name = row["name"]
            item.size_bytes = 0  # not known without resolving/exporting -- see RURI_UL_animation_clips
        cabmap_state.set_animation_discovery_state(selected_row.cab, state.as_options())

        if clip_rows:
            self.report({"INFO"}, f"Found {len(clip_rows)} clip(s). Check the ones you want, then Import Checked Animations.")
        else:
            self.report({"INFO"}, "No animation clips found in this selection's dependency closure.")
        return {"FINISHED"}


class RURI_OT_import_selected_animations(bpy.types.Operator):
    bl_idname = "ruri.import_selected_animations"
    bl_label = "Import Checked Animations"
    bl_description = "Build the character (if not already in the scene) and Blender actions for the checked clips"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_cabmap
        return (cabmap_state.ANIMATION_BUILD_STATE is not None
                and any(item.selected for item in state.available_clips))

    def execute(self, context):
        state = context.scene.ruri_cabmap
        build_state = cabmap_state.ANIMATION_BUILD_STATE
        if build_state is None:
            self.report({"WARNING"}, "No character discovered -- click Discover Animations first.")
            return {"CANCELLED"}

        checked_keys = [item.guid for item in state.available_clips if item.selected]
        if not checked_keys:
            self.report({"WARNING"}, "No animations checked.")
            return {"CANCELLED"}

        arm_obj = bpy.data.objects.get(build_state["arm_name"]) if build_state["arm_name"] else None
        if arm_obj is None or arm_obj.type != "ARMATURE":
            # Discovery-only state (or the armature was deleted since) --
            # THIS is the first point the closure actually gets resolved/
            # exported at all. checked_keys are still CAB names here (the
            # cheap discovery lists CAB rows); clips_by_cab from the export
            # translates them to real clip guids through the cabmap's own
            # identity -- a clip CAB's fbx display name and its clips'
            # m_Names genuinely differ, so there is nothing to join by name.
            seed_cab = build_state["seed_cab"]
            seeds = [seed_cab]
            seed_row = cabmap_state.rows_by_cab().get(seed_cab)
            seed_is_clip_only = (seed_row is not None
                                 and "AnimationClip" in seed_row["type_names"]
                                 and "GameObject" not in seed_row["type_names"])
            try:
                if seed_is_clip_only:
                    # A bare clip CAB's closure carries no rig; co-seed the
                    # associated rig-FBX CAB(s) so AssetRipper restores the
                    # clips' hashed curve paths to real strings and the real
                    # Avatar (not a stub) is in scope for humanoid retargeting.
                    seeds.extend(cabmap_state.BRIDGE.find_associated_avatar_cabs(seed_cab))
                documents, textures, roots, seed_roots, clips_by_cab, _scene_roots = \
                    cabmap_state.BRIDGE.import_cabs(seeds)
            except Exception as exc:
                _report_exception(self, "Import (bridge) failed", exc)
                return {"CANCELLED"}
            db = bridge_asset_db.BridgeAssetDatabase(documents, textures)

            selected_guids = []
            for cab in checked_keys:
                for guid in clips_by_cab.get(cab.lower(), []):
                    if guid not in selected_guids:
                        selected_guids.append(guid)

            if not roots:
                # Animation-only closure (the discovered row was itself a clip
                # CAB): attach onto the user's selected skeleton instead of
                # requiring a character build.
                if not selected_guids:
                    self.report({"ERROR"}, "The checked row(s) exported no AnimationClip -- see console.")
                    return {"CANCELLED"}
                return _import_clips_standalone(self, context, state, seed_cab, selected_guids, db)

            # Character closure: build the character once. Its own asset is
            # resolved bridge-side through the cabmap's CAB identity
            # (seed_roots) -- not a name match.
            primary_guid = seed_roots.get(seed_cab)
            prefab_file = db.load_guid(primary_guid) if primary_guid else None
            if prefab_file is None:
                self.report({"ERROR"}, "Could not resolve the discovered character's own asset "
                                       "in its exported closure.")
                return {"CANCELLED"}

            report = prefab_importer.import_prefab_from_db(context, db, prefab_file, build_state["options"])
            for warning in report.warnings[:5]:
                self.report({"WARNING"}, warning)
            if report.armature is None:
                self.report({"ERROR"}, "This character has no skeleton to attach animations to.")
                return {"CANCELLED"}
            arm_obj = report.armature
            cabmap_state.mark_animation_build_done(db, arm_obj.name, report.maps, report.path_to_meshobjects)
            build_state = cabmap_state.ANIMATION_BUILD_STATE

            # Upgrade the browser from CAB rows to the REAL clips (guid-keyed,
            # names + sizes now knowable), carrying the user's checked state
            # across through clips_by_cab -- this is deliberate and visible,
            # not a side effect: from here on the list shows exactly what can
            # be built, and a second Import needs no lazy build at all.
            refs = prefab_importer.discover_clip_refs_from_db(db, prefab_file)
            state.available_clips.clear()
            state.animation_character_name = arm_obj.name
            for ref in refs:
                item = state.available_clips.add()
                item.guid = ref["guid"]
                item.name = ref["name"]
                item.size_bytes = ref["size_bytes"]
                item.selected = ref["guid"] in selected_guids
            if not selected_guids:
                self.report({"WARNING"}, "The checked row(s) mapped to no exported clip -- "
                                         "pick from the refreshed list and import again.")
                return {"CANCELLED"}
            guids = selected_guids
        else:
            # The armature (and a real guid-keyed browser) already exist --
            # every checked item.guid IS a clip guid; just validate.
            db = build_state["db"]
            guids = []
            unresolved = []
            for item in state.available_clips:
                if not item.selected:
                    continue
                direct = db.load_guid(item.guid)
                if direct is not None and direct.first("AnimationClip") is not None:
                    guids.append(item.guid)
                else:
                    unresolved.append(item.name)
            if unresolved:
                self.report({"WARNING"}, f"{len(unresolved)} checked clip(s) not found in the resolved "
                                         f"closure: {', '.join(unresolved[:3])}{'...' if len(unresolved) > 3 else ''}")
            if not guids:
                self.report({"ERROR"}, "None of the checked clips could be resolved.")
                return {"CANCELLED"}

        try:
            built, build_warnings = prefab_importer.build_selected_animations(
                build_state["db"], arm_obj, build_state["maps"],
                build_state["path_to_meshobjects"], guids, state.as_options())
        except Exception as exc:
            _report_exception(self, "Animation import failed", exc)
            return {"CANCELLED"}
        for warning in build_warnings[:5]:
            self.report({"WARNING"}, warning)
        self.report({"INFO"}, f"Built {built} animation action(s) on {arm_obj.name}.")
        return {"FINISHED"}


class RURI_OT_animation_select_all(bpy.types.Operator):
    bl_idname = "ruri.animation_select_all"
    bl_label = "Select All / None"
    bl_description = "Check or uncheck every listed animation clip"
    bl_options = {"REGISTER", "UNDO"}
    select: BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return len(context.scene.ruri_cabmap.available_clips) > 0

    def execute(self, context):
        for item in context.scene.ruri_cabmap.available_clips:
            item.selected = self.select
        return {"FINISHED"}


class RURI_PT_animation_browser(bpy.types.Panel):
    """Checkbox animation browser -- mirrors the Scene tab's
    discover-then-select-then-commit shape (scene_panel.draw_scene_tab): always
    visible once a cabmap is loaded, with its own "Discover Animations"
    button front and center, rather than being an invisible side effect of
    the generic Import buttons gated behind an easy-to-miss checkbox (the
    original shape -- poll()'d on available_clips already being non-empty,
    only reachable by first checking "Discover Animations" above then
    clicking Import -- was reported back as "I checked the box and nothing
    happened," since checking a box is not a visibly-actionable step)."""
    bl_idname = "RURI_PT_animation_browser"
    bl_label = "Animations"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RuriRipper"
    bl_parent_id = "RURI_PT_cabmap"

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_cabmap
        return state.loaded and state.active_tab == "assetbundle"

    def draw(self, context):
        layout = self.layout
        state = context.scene.ruri_cabmap

        row = layout.row(align=True)
        row.operator(RURI_OT_discover_animations.bl_idname, icon="VIEWZOOM")

        if not state.available_clips:
            layout.label(text="Select a row above, then click Discover Animations.", icon="INFO")
            return

        layout.label(text=f"Clips for: {state.animation_character_name}", icon="ARMATURE_DATA")

        row = layout.row(align=True)
        op = row.operator(RURI_OT_animation_select_all.bl_idname, text="All")
        op.select = True
        op = row.operator(RURI_OT_animation_select_all.bl_idname, text="None")
        op.select = False

        layout.template_list(RURI_UL_animation_clips.bl_idname, "", state, "available_clips",
                             state, "available_clips_active_index", rows=8)

        selected = [item for item in state.available_clips if item.selected]
        summary = f"Selected: {len(selected)} clip(s)" if selected else "Nothing checked yet."
        layout.label(text=summary)

        layout.operator(RURI_OT_import_selected_animations.bl_idname, icon="IMPORT")


_CLASSES = (
    # PropertyGroups first, and RURI_PG_filter_rule/RURI_PG_cabmap_row specifically
    # before RURI_PG_cabmap -- Blender requires a CollectionProperty's target type
    # to already be registered.
    RURI_PG_cabmap_row,
    RURI_PG_hook_entry,
    RURI_PG_filter_rule,
    RURI_PG_animation_clip,
    RURI_PG_cabmap,
    RURI_UL_hooks,
    RURI_UL_cabmap,
    RURI_UL_animation_clips,
    RURI_OT_filter_add_rule,
    RURI_OT_filter_remove_rule,
    RURI_OT_filter_clear_rules,
    RURI_OT_filter_quick_add,
    RURI_MT_quick_filter,
    RURI_PT_filter_popover,
    RURI_PT_cabmap,
    RURI_OT_refresh_hooks,
    RURI_OT_build_cabmap,
    RURI_OT_load_cabmap,
    RURI_OT_cabmap_sort,
    RURI_OT_import_selected,
    RURI_OT_discover_animations,
    RURI_OT_import_selected_animations,
    RURI_OT_animation_select_all,
    RURI_PT_animation_browser,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_cabmap = PointerProperty(type=RURI_PG_cabmap)


def unregister():
    del bpy.types.Scene.ruri_cabmap
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    cabmap_state.reset()
