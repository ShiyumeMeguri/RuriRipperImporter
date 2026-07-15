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
import traceback

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                        PointerProperty, StringProperty)

try:
    from . import cabmap_state, pythonnet_bootstrap, bridge_asset_db, prefab_importer
except ImportError:  # standalone (non-package) testing
    import cabmap_state
    import pythonnet_bootstrap
    import bridge_asset_db
    import prefab_importer

_HOOK_IDS_DEFAULT = "EndField_1.3.3"
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
    return [h.strip() for h in state.hook_ids.split(",") if h.strip()]


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


class RURI_PG_cabmap(bpy.types.PropertyGroup):
    game_root: StringProperty(name="Game Root", subtype="DIR_PATH",
                              description="The game's install root directory")
    cabmap_path: StringProperty(name="Cabmap", subtype="FILE_PATH",
                                description="Existing cabmap to load, or output path to build one")
    hook_ids: StringProperty(name="Hook", default=_HOOK_IDS_DEFAULT,
                             description="Comma-separated Ruri.RipperHook hook id(s), e.g. EndField_1.3.3")
    loaded: BoolProperty(default=False)
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


class RURI_OT_build_cabmap(bpy.types.Operator):
    bl_idname = "ruri.build_cabmap"
    bl_label = "Build Cabmap"
    bl_description = "Scan the game root and build a fresh cabmap (can take a long time for a full game)"

    @classmethod
    def poll(cls, context):
        return pythonnet_bootstrap.is_ready()

    def execute(self, context):
        state = context.scene.ruri_cabmap
        root = bpy.path.abspath(state.game_root) if state.game_root else ""
        out = bpy.path.abspath(state.cabmap_path) if state.cabmap_path else ""
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Pick a valid game root directory first.")
            return {"CANCELLED"}
        if not out:
            self.report({"ERROR"}, "Pick an output path for the cabmap file first.")
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


def _prefab_stem(display_name):
    """Strip a trailing '.prefab' (case-insensitively) so a browser row's
    `name` (e.g. 'gacha_char_pelica_actor.prefab') can be compared against
    prefab_importer._prefab_display_name's output (no extension)."""
    stem = display_name or ""
    if stem.lower().endswith(".prefab"):
        stem = stem[: -len(".prefab")]
    return stem.lower()


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
        cabs = [selected_row.cab] if selected_row is not None else []
        if not cabs:
            self.report({"WARNING"}, "No row selected.")
            return {"CANCELLED"}

        if self.reset_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        try:
            documents, textures, roots = cabmap_state.BRIDGE.import_cabs(cabs)
        except Exception as exc:
            _report_exception(self, "Import (bridge) failed", exc)
            return {"CANCELLED"}
        if not roots:
            self.report({"WARNING"}, "No importable (.prefab) asset found in the resolved closure.")
            return {"CANCELLED"}

        db = bridge_asset_db.BridgeAssetDatabase(documents, textures)
        options = state.as_options()
        imported = 0
        # A single selected row's dependency closure routinely resolves to
        # MORE than one root .prefab (e.g. an actor prefab pulling in a
        # separate portrait/"uimodel" variant as a second top-level asset) --
        # and since discover_clip_refs_from_db's fallback scans the WHOLE
        # shared closure db, every co-resolved root can end up reporting the
        # SAME clip set, not a distinguishing one. "how many roots have
        # clips" is therefore not a usable signal. Attribute the animation
        # browser to whichever imported root's own prefab name matches what
        # the user actually selected in the list.
        selected_stem = _prefab_stem(selected_row.name)
        primary_report = None
        for root_guid in roots:
            prefab_file = db.load_guid(root_guid)
            if prefab_file is None:
                continue
            report = prefab_importer.import_prefab_from_db(context, db, prefab_file, options)
            imported += 1
            for warning in report.warnings[:5]:
                self.report({"WARNING"}, warning)
            if _prefab_stem(prefab_importer._prefab_display_name(prefab_file)) == selected_stem:
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
        top.prop(state, "hook_ids")
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
            # A CAB name for now, not a real Unity guid -- translated to one
            # (via build_clip_name_index_from_db) only once the lazy build
            # below actually resolves this closure. See RURI_PG_animation_clip.
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
            # exported at all: lazily build the character now, exactly
            # once, only because the user actually asked to attach a clip.
            try:
                documents, textures, roots = cabmap_state.BRIDGE.import_cabs([build_state["seed_cab"]])
            except Exception as exc:
                _report_exception(self, "Import (bridge) failed", exc)
                return {"CANCELLED"}
            if not roots:
                self.report({"WARNING"}, "No importable (.prefab) asset found in the resolved closure.")
                return {"CANCELLED"}
            db = bridge_asset_db.BridgeAssetDatabase(documents, textures)

            selected_row_name = state.animation_character_name
            selected_stem = _prefab_stem(selected_row_name)
            prefab_file = None
            for root_guid in roots:
                candidate = db.load_guid(root_guid)
                if candidate is not None and _prefab_stem(prefab_importer._prefab_display_name(candidate)) == selected_stem:
                    prefab_file = candidate
                    break
            if prefab_file is None:
                self.report({"ERROR"}, "Could not match an imported root back to the discovered character.")
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

        # item.guid is either a REAL Unity guid already (populated by
        # import_prefab_from_db's own discover_clip_refs_from_db side effect
        # when a full character import happened via Import (Append)/(Reset
        # Scene)) or a CAB name not yet translated (from the cheap
        # RURI_OT_discover_animations pass above). Try it directly as a guid
        # first; only fall back to a name-based join (same pattern as
        # build_mesh_name_index_from_db et al.) if that doesn't resolve to a
        # real AnimationClip document. clip_index is built lazily -- only
        # paid for if at least one checked item actually needs it.
        db = build_state["db"]
        clip_index = None
        guids = []
        unresolved = []
        for item in state.available_clips:
            if not item.selected:
                continue
            direct = db.load_guid(item.guid)
            if direct is not None and direct.first("AnimationClip") is not None:
                guids.append(item.guid)
                continue
            if clip_index is None:
                clip_index = prefab_importer.build_clip_name_index_from_db(db)
            guid = clip_index.get(prefab_importer.expected_clip_name_from_cab_row_name(item.name))
            if guid is None:
                unresolved.append(item.name)
                continue
            guids.append(guid)
        if unresolved:
            self.report({"WARNING"}, f"{len(unresolved)} checked clip(s) not found in the resolved "
                                     f"closure: {', '.join(unresolved[:3])}{'...' if len(unresolved) > 3 else ''}")
        if not guids:
            self.report({"ERROR"}, "None of the checked clips could be resolved.")
            return {"CANCELLED"}

        try:
            built = prefab_importer.build_selected_animations(
                build_state["db"], arm_obj, build_state["maps"],
                build_state["path_to_meshobjects"], guids, state.as_options())
        except Exception as exc:
            _report_exception(self, "Animation import failed", exc)
            return {"CANCELLED"}
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
    """Checkbox animation browser -- mirrors the Scene Import panel's
    discover-then-select-then-commit shape (RURI_PT_scene_import): always
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
        return context.scene.ruri_cabmap.loaded

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
    RURI_PG_filter_rule,
    RURI_PG_animation_clip,
    RURI_PG_cabmap,
    RURI_UL_cabmap,
    RURI_UL_animation_clips,
    RURI_OT_filter_add_rule,
    RURI_OT_filter_remove_rule,
    RURI_OT_filter_clear_rules,
    RURI_OT_filter_quick_add,
    RURI_MT_quick_filter,
    RURI_PT_filter_popover,
    RURI_PT_cabmap,
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
