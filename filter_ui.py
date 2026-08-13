"""The one search box + Include/Exclude rule editor, for every list in the panel.

A list declares what it can be filtered BY (a FilterSpec: field keys and their
labels) and what to re-run when the filter changes. Everything else -- the
widget, the rule storage, the popover, the operators -- is shared, so a new tab
gets the full multi-rule filter by registering a spec and calling
draw_search_row, with no matching code of its own.

Nothing here matches anything. Every list, whatever its size, is filtered by the
SAME vectorized C# engine and the SAME rule evaluator the asset-bundle browser
runs (Ruri.RipperHook's RuleFilter over a ColumnTable); a Python-side copy of
"what does contains mean" is exactly how two browsers quietly stop agreeing.

The popover panels are HEADER-region on purpose: a UI-region panel is a SIDEBAR
panel, and a categoryless one lands in Blender's catch-all "Misc" tab. HEADER is
Blender's own convention for popover-only panels, reachable exactly through the
layout.popover() that names them.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

try:
    from .RuriRipperPyBridge.session import cabmap_state
except ImportError:  # standalone (non-package) testing
    from RuriRipperPyBridge.session import cabmap_state

# Relations and actions are the same everywhere: they are the RULE grammar, not a
# property of any one list. Only the FIELDS differ per list, which is what a spec
# declares.
RELATION_ITEMS = [(r, cabmap_state.RELATION_LABELS[r], "") for r in cabmap_state.RELATIONS]
ACTION_ITEMS = [
    ("include", "Include", "Require rows to match this rule (each Include rule further narrows the results)"),
    ("exclude", "Exclude", "Hide rows matching this rule -- always wins over any Include"),
]


class FilterSpec:
    """One list's filter contract: what it can be filtered by, where its filter
    state lives, and what to re-run when that state changes."""

    __slots__ = ("key", "fields", "state_for", "apply", "quick_relation_for")

    def __init__(self, key, fields, state_for, apply, quick_relation_for=None):
        self.key = key                  # the owning tab's key
        # ((field_key, label), ...), or a zero-arg callable returning that -- a
        # list whose columns depend on what was actually loaded (the roster's
        # differ between Characters and NPCs) declares them as they are rather
        # than tabulating a union that is wrong for both.
        # ORDER MATTERS: the first field is what a new rule starts on, so every
        # list puts the displayed NAME first. Nobody filters by id from memory.
        self.fields = fields if callable(fields) else tuple(fields)
        self.state_for = state_for      # (context) -> the PropertyGroup holding search/filter_rules
        self.apply = apply              # (context) -> re-run this list
        # Which relation a one-click "quick add" uses for a field; contains is the
        # sensible default and a numeric column wants exact equality instead.
        self.quick_relation_for = quick_relation_for or (lambda field: "contains")

    def field_list(self):
        return tuple(self.fields() if callable(self.fields) else self.fields)

    def field_items(self):
        return [(key, label, "") for key, label in self.field_list()]


SPECS = {}

# Set by the host panel: (context) -> the key of the tab currently on screen. The
# popover has no argument of its own, so the active tab IS how it knows whose
# rules to edit.
ACTIVE_SPEC_KEY = None


def register_spec(spec):
    SPECS[spec.key] = spec
    return spec


def active_spec(context):
    key = ACTIVE_SPEC_KEY(context) if ACTIVE_SPEC_KEY is not None else None
    return SPECS.get(key)


def _spec_and_state(context):
    spec = active_spec(context)
    return (spec, spec.state_for(context)) if spec is not None else (None, None)


# Blender's dynamic-enum items callbacks must not hand back a list that dies with
# the call -- the C-level enum can end up pointing at freed strings. Each cache is
# kept alive at module scope, keyed by nothing more than "the last one returned",
# which is all Blender ever holds.
_rule_field_cache = [("name", "Name", "")]
_builder_field_cache = [("name", "Name", "")]


def _rule_field_items(self, context):
    """A rule's own vocabulary: the fields of the list it was added to. Stamped
    on the rule at creation (spec_key), so a rule stays readable even while a
    different tab is on screen."""
    global _rule_field_cache
    spec = SPECS.get(self.spec_key)
    _rule_field_cache = (spec.field_items() if spec is not None else None) or [("name", "Name", "")]
    return _rule_field_cache


def _builder_field_items(self, context):
    """The builder row's vocabulary: the fields of the list that OWNS this state.
    Read off the owning PropertyGroup class, so no tab repeats its key in RNA."""
    global _builder_field_cache
    spec = SPECS.get(getattr(type(self), "FILTER_SPEC_KEY", ""))
    _builder_field_cache = (spec.field_items() if spec is not None else None) or [("name", "Name", "")]
    return _builder_field_cache


def _on_rule_edit(self, context):
    spec, _state = _spec_and_state(context)
    if spec is not None:
        spec.apply(context)


class RURI_PG_filter_rule(bpy.types.PropertyGroup):
    """One Process-Monitor-style Include/Exclude rule -- [Field][Relation][Value]
    then [Include/Exclude]. The field/relation/value/action/enabled shape is the
    wire form the C# engine reads, so these instances are handed to it directly.

    ``spec_key`` names the list the rule belongs to; it is what lets one rule
    type serve every tab while each still offers only its own fields."""
    spec_key: StringProperty()
    field: EnumProperty(name="Field", items=_rule_field_items, update=_on_rule_edit)
    relation: EnumProperty(name="Relation", items=RELATION_ITEMS, update=_on_rule_edit)
    value: StringProperty(name="Value", update=_on_rule_edit)
    action: EnumProperty(name="Action", items=ACTION_ITEMS, update=_on_rule_edit)
    enabled: BoolProperty(name="Enabled", default=True, update=_on_rule_edit,
                          description="Untick to keep a rule without deleting it")


class FilterStateMixin:
    """The filter state every filterable list carries. Blender picks annotations
    up through the class hierarchy, so this is one declaration rather than one
    per tab that can drift.

    A tab's PropertyGroup sets FILTER_SPEC_KEY (a plain class attribute, not an
    RNA property) to the spec it belongs to, and declares its own ``search``:
    the update callback differs per list -- the 260k-row browser debounces, a
    list of 83 scenes has no reason to."""
    filter_rules: CollectionProperty(type=RURI_PG_filter_rule)
    filter_rules_active_index: IntProperty()
    new_rule_field: EnumProperty(name="Field", items=_builder_field_items)
    new_rule_relation: EnumProperty(name="Relation", items=RELATION_ITEMS, default="contains")
    new_rule_value: StringProperty(name="Value")
    new_rule_action: EnumProperty(name="Action", items=ACTION_ITEMS)


# ── selection across a refill ─────────────────────────────────────────────────
#
# A drawn list is a WINDOW onto a filtered result set, and Blender tracks its
# selection as an INDEX INTO THAT WINDOW. Refill the window -- a keystroke in the
# search box, a rule edit, a folder change -- and the same index now points at a
# different row: the selection silently becomes something else, and any update
# callback on it fires as though the user had clicked that row.
#
# So a selection's identity is its KEY, never its position. Every list here
# captures its key before refilling, refills inside `rebuilding()` (which makes
# selection callbacks stand down), and restores the highlight by that key
# afterwards. A key the filter now hides simply stays selected off-screen, which
# is what the user meant when they picked it.

_REBUILDING = 0


class _Rebuilding:
    def __enter__(self):
        global _REBUILDING
        _REBUILDING += 1
        return self

    def __exit__(self, kind, value, trace):
        global _REBUILDING
        _REBUILDING -= 1
        return False


def rebuilding():
    """Refill a drawn list inside this. Re-entrant, so a rebuild that triggers
    another one still ends with callbacks re-armed exactly once."""
    return _Rebuilding()


def is_rebuilding():
    """True while a list is being refilled. Every selection-changed callback
    starts with this: a refill is not a click."""
    return _REBUILDING > 0


def selected_key(state, entries="entries", index="active_index", key="key"):
    """The key of whatever is selected right now, or "" -- captured BEFORE a
    refill so it can be restored after one."""
    rows = getattr(state, entries, None)
    position = getattr(state, index, -1)
    if rows is None or not (0 <= position < len(rows)):
        return ""
    return getattr(rows[position], key, "") or ""


def restore_selection(state, wanted, entries="entries", index="active_index", key="key"):
    """Put the highlight back on the row carrying ``wanted``. True when it is
    still in the list; False when the current filter hides it (the caller keeps
    whatever it opened -- the selection is the key, not the row on screen)."""
    rows = getattr(state, entries, None)
    if rows is None:
        return False
    if wanted:
        for position, row in enumerate(rows):
            if getattr(row, key, "") == wanted and not getattr(row, "is_group", False):
                setattr(state, index, position)
                return True
    if getattr(state, index, 0) >= len(rows):
        setattr(state, index, 0)
    return False


def enabled_rules(state):
    return [rule for rule in state.filter_rules if rule.enabled]


def has_active_query(state):
    """True when this list is showing a filtered result rather than everything --
    the same test the browser uses to swap its folder view for a flat one."""
    return cabmap_state.has_active_query(state.search, state.filter_rules)


# ── operators ─────────────────────────────────────────────────────────────────

class RURI_OT_filter_add_rule(bpy.types.Operator):
    """Add the rule assembled in the builder row (Field/Relation/Value/Action) --
    the Process Monitor dialog's "Add" button."""
    bl_idname = "ruri.filter_add_rule"
    bl_label = "Add Rule"
    bl_description = "Add this rule to the filter"

    def execute(self, context):
        spec, state = _spec_and_state(context)
        if spec is None:
            self.report({"WARNING"}, "No filterable list is on screen.")
            return {"CANCELLED"}
        if not state.new_rule_value and state.new_rule_relation not in ("is", "is_not"):
            self.report({"WARNING"}, "Enter a value for the rule first.")
            return {"CANCELLED"}
        rule = state.filter_rules.add()
        rule.spec_key = spec.key
        rule.field = state.new_rule_field
        rule.relation = state.new_rule_relation
        rule.value = state.new_rule_value
        rule.action = state.new_rule_action
        rule.enabled = True
        state.filter_rules_active_index = len(state.filter_rules) - 1
        state.new_rule_value = ""
        spec.apply(context)
        return {"FINISHED"}


class RURI_OT_filter_remove_rule(bpy.types.Operator):
    bl_idname = "ruri.filter_remove_rule"
    bl_label = "Remove Rule"
    bl_description = "Remove this filter rule"
    bl_options = {"INTERNAL"}
    index: IntProperty()

    def execute(self, context):
        spec, state = _spec_and_state(context)
        if spec is None:
            return {"CANCELLED"}
        if 0 <= self.index < len(state.filter_rules):
            state.filter_rules.remove(self.index)
            state.filter_rules_active_index = min(state.filter_rules_active_index,
                                                  len(state.filter_rules) - 1)
        spec.apply(context)
        return {"FINISHED"}


class RURI_OT_filter_clear_rules(bpy.types.Operator):
    bl_idname = "ruri.filter_clear_rules"
    bl_label = "Clear All"
    bl_description = "Remove every filter rule"

    def execute(self, context):
        spec, state = _spec_and_state(context)
        if spec is None:
            return {"CANCELLED"}
        state.filter_rules.clear()
        spec.apply(context)
        return {"FINISHED"}


class RURI_OT_filter_quick_add(bpy.types.Operator):
    """One-click rule-from-a-row, mirroring the WinForms browser's right-click
    'Include > Container contains "..."' quick-filter menu (Blender's UIList has
    no native per-row context menu, so this is invoked from a dropdown menu
    button instead)."""
    bl_idname = "ruri.filter_quick_add"
    bl_label = "Quick Add Rule"
    bl_options = {"INTERNAL"}
    # Plain strings, not enums: the vocabulary is per-spec, and an operator
    # property's enum items cannot depend on which list invoked it.
    field: StringProperty()
    action: StringProperty()
    value: StringProperty()

    def execute(self, context):
        spec, state = _spec_and_state(context)
        if spec is None:
            return {"CANCELLED"}
        rule = state.filter_rules.add()
        rule.spec_key = spec.key
        rule.field = self.field
        rule.relation = spec.quick_relation_for(self.field)
        rule.value = self.value
        rule.action = self.action
        rule.enabled = True
        state.filter_rules_active_index = len(state.filter_rules) - 1
        spec.apply(context)
        return {"FINISHED"}


# ── the popover ───────────────────────────────────────────────────────────────

class RURI_PT_filter_popover(bpy.types.Panel):
    """Process-Monitor-style Include/Exclude rule editor, Blender-native as a
    popover (the same idiom the Outliner's own funnel-icon filter uses) rather
    than a cramped multi-column row squeezed into the narrow N-panel sidebar --
    opened from the funnel button next to the search box. Non-modal and
    live-apply: every edit re-filters immediately, no OK/Cancel/Apply step."""
    bl_idname = "RURI_PT_filter_popover"
    bl_label = "Filter Rules"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 20

    def draw(self, context):
        layout = self.layout
        spec, state = _spec_and_state(context)
        if spec is None:
            layout.label(text="No filterable list is on screen.", icon="INFO")
            return

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
            return

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


def draw_search_row(layout, state, extra_operator=None):
    """The shared widget: quick-search box + the funnel that opens the rule
    editor, with the active rule count as the funnel's badge. ``extra_operator``
    is an optional (bl_idname, icon) a list wants on the same row (a Refresh,
    typically), so every tab's search line reads identically."""
    row = layout.row(align=True)
    row.prop(state, "search", icon="VIEWZOOM", text="")
    active = sum(1 for rule in state.filter_rules if rule.enabled)
    # Text badge (the active rule count) carries the "filter active" signal
    # instead of a second icon -- keeps this to icons actually in Blender's set.
    row.popover(RURI_PT_filter_popover.bl_idname,
                text=str(active) if active else "", icon="FILTER")
    if extra_operator is not None:
        row.operator(extra_operator[0], text="", icon=extra_operator[1])
    return row


def draw_quick_filter_menu(layout, spec, value_of):
    """The quick-filter menu body for one selected row: Include/Exclude x every
    field the spec declares. ``value_of(field_key)`` reads that field off the
    row, so this knows nothing about any particular list's shape."""
    for action_id, action_label, _desc in ACTION_ITEMS:
        layout.label(text=action_label + ":")
        for field_id, field_label in spec.field_list():
            value = str(value_of(field_id))
            display = value if len(value) <= 40 else value[:37] + "..."
            relation = spec.quick_relation_for(field_id)
            relation_word = cabmap_state.RELATION_LABELS.get(relation, relation)
            op = layout.operator(RURI_OT_filter_quick_add.bl_idname,
                                 text=f"{field_label} {relation_word} \"{display}\"")
            op.field = field_id
            op.action = action_id
            op.value = value
        if action_id != ACTION_ITEMS[-1][0]:
            layout.separator()


_CLASSES = (
    RURI_PG_filter_rule,
    RURI_OT_filter_add_rule,
    RURI_OT_filter_remove_rule,
    RURI_OT_filter_clear_rules,
    RURI_OT_filter_quick_add,
    RURI_PT_filter_popover,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    # SPECS is deliberately NOT cleared: a spec is a module-level declaration
    # made at import time, and an addon re-register does not re-import. Clearing
    # here would leave every list with no vocabulary and no way to re-apply.
