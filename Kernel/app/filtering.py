"""One search box and one Include/Exclude rule editor, for every list in every
host.

A list declares what it can be filtered BY (a :class:`FilterSpec`: field keys and
their labels) and what to re-run when the filter changes. Everything else -- the
rule storage, the selection-across-a-refill discipline, the commands, the widget
-- is shared, so a new list gets the full multi-rule filter by registering a spec
and describing one row.

NOTHING HERE MATCHES ANYTHING. Every list, whatever its size, is filtered by the
same vectorized C# engine and the same rule evaluator the asset-bundle browser
runs (Ruri.RipperHook's RuleFilter over a ColumnTable). A host-side copy of "what
does contains mean" is exactly how two browsers quietly stop agreeing -- and with
two hosts, three.
"""

from __future__ import annotations

from ...RuriRipperPyBridge.session import cabmap_state
from . import command as app_command
from . import schemas
from . import state as app_state


class FilterSpec:
    """One list's filter contract: what it can be filtered by, where its filter
    state lives, and what to re-run when that state changes."""

    __slots__ = ("key", "fields", "state_for", "apply", "quick_relation_for",
                 "row_for")

    def __init__(self, key, fields, state_for, apply, quick_relation_for=None,
                 row_for=None):
        self.key = key                  # the owning tab's key
        # ((field_key, label), ...), or a zero-arg callable returning that -- a
        # list whose columns depend on what was actually loaded (the roster's
        # differ between Characters and NPCs) declares them as they are rather
        # than tabulating a union that is wrong for both.
        # ORDER MATTERS: the first field is what a new rule starts on, so every
        # list puts the displayed NAME first. Nobody filters by id from memory.
        self.fields = fields if callable(fields) else tuple(fields)
        self.state_for = state_for      # (context) -> the record holding search/filter_rules
        self.apply = apply              # (context) -> re-run this list
        # Which relation a one-click "quick add" uses for a field; contains is the
        # sensible default and a numeric column wants exact equality instead.
        self.quick_relation_for = quick_relation_for or (lambda field: "contains")
        # (context) -> the record the quick filter builds its rules from, or None.
        # A list that offers the menu answers this; one that does not simply
        # never draws it.
        self.row_for = row_for

    def field_list(self):
        return tuple(self.fields() if callable(self.fields) else self.fields)

    def field_items(self):
        return [(key, label, "") for key, label in self.field_list()]


SPECS = {}

#: Set by the host panel: (context) -> the key of the tab currently on screen.
#: The rule editor has no argument of its own, so the active tab IS how it knows
#: whose rules to edit.
ACTIVE_SPEC_KEY = None


def register_spec(spec):
    SPECS[spec.key] = spec
    return spec


def active_spec(context):
    key = ACTIVE_SPEC_KEY(context) if ACTIVE_SPEC_KEY is not None else None
    return SPECS.get(key)


def spec_and_state(context):
    spec = active_spec(context)
    return (spec, spec.state_for(context)) if spec is not None else (None, None)


#: The one quick-filter menu. Which list it acts on is the ACTIVE spec, so a
#: second list offering the same menu is a spec that answers ``row_for`` and
#: nothing else -- rather than a second menu id both hosts have to register.
QUICK_FILTER_MENU = "RURI_MT_quick_filter"


def quick_filter_menu_entries(context):
    """Include/Exclude x every field, for the row the active list has selected."""
    spec = active_spec(context)
    row = spec.row_for(context) if spec is not None and spec.row_for else None
    if row is None:
        return []
    return quick_filter_entries(spec, lambda field: getattr(row, field, "")
                                if not isinstance(row, dict) else row.get(field, ""))


# ── selection across a refill ─────────────────────────────────────────────────
#
# A drawn list is a WINDOW onto a filtered result set, and a host tracks its
# selection as an INDEX INTO THAT WINDOW. Refill the window -- a keystroke in the
# search box, a rule edit, a folder change -- and the same index now points at a
# different row: the selection silently becomes something else, and any update
# callback on it fires as though the user had clicked that row.
#
# So a selection's identity is its KEY, never its position. Every list captures
# its key before refilling, refills inside `rebuilding()` (which makes selection
# callbacks stand down), and restores the highlight by that key afterwards. A key
# the filter now hides simply stays selected off-screen, which is what the user
# meant when they picked it.

_REBUILDING = [0]


class _Rebuilding:
    def __enter__(self):
        _REBUILDING[0] += 1
        return self

    def __exit__(self, kind, value, trace):
        _REBUILDING[0] -= 1
        return False


def rebuilding():
    """Refill a drawn list inside this. Re-entrant, so a rebuild that triggers
    another one still ends with callbacks re-armed exactly once."""
    return _Rebuilding()


def is_rebuilding():
    """True while a list is being refilled. Every selection-changed callback
    starts with this: a refill is not a click."""
    return _REBUILDING[0] > 0


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
    whatever it opened -- the selection is the key, not the row on screen).

    A key the refill no longer shows leaves the highlight NOWHERE (index -1),
    never on whatever row inherited that position. The cursor is an identity; a
    cursor drawn on a row that is not the selection is the UI stating something
    false -- reported from the asset browser as "searched idle, picked a clip,
    searched dead, the highlight is on a dead clip and the import still reads
    idle". Every reader of an active index already guards 0 <= i < len."""
    rows = getattr(state, entries, None)
    if rows is None:
        return False
    if wanted:
        for position, row in enumerate(rows):
            if getattr(row, key, "") == wanted and not getattr(row, "is_group", False):
                setattr(state, index, position)
                return True
        setattr(state, index, -1)
        return False
    if getattr(state, index, 0) >= len(rows):
        setattr(state, index, 0)
    return False


def enabled_rules(state):
    return [rule for rule in state.filter_rules if rule.enabled]


def has_active_query(state):
    """True when this list is showing a filtered result rather than everything --
    the same test the browser uses to swap its folder view for a flat one."""
    return cabmap_state.has_active_query(state.search, state.filter_rules)


# ── the schema's named behaviour ──────────────────────────────────────────────
_FALLBACK_FIELDS = (("name", "Name", ""),)


def rule_field_items(rule, context):
    """A rule's own vocabulary: the fields of the list it was added to. Stamped
    on the rule at creation (spec_key), so a rule stays readable even while a
    different tab is on screen."""
    spec = SPECS.get(rule.spec_key)
    return (spec.field_items() if spec is not None else None) or _FALLBACK_FIELDS


def builder_field_items(owner, context):
    """The builder row's vocabulary: the fields of the list that OWNS this state.
    Read off the owning record's class, so no tab repeats its key."""
    spec = SPECS.get(getattr(type(owner), "FILTER_SPEC_KEY", ""))
    return (spec.field_items() if spec is not None else None) or _FALLBACK_FIELDS


def on_rule_edit(rule, context):
    spec, _state = spec_and_state(context)
    if spec is not None:
        spec.apply(context)


HANDLERS = app_state.Handlers(
    "filtering",
    rule_field_items=rule_field_items,
    builder_field_items=builder_field_items,
    on_rule_edit=on_rule_edit)


# ── commands ──────────────────────────────────────────────────────────────────
def _add_rule(context, arguments):
    spec, state = spec_and_state(context)
    if spec is None:
        return {"CANCELLED"}
    if not state.new_rule_value and state.new_rule_relation not in ("is", "is_not"):
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
    return None


def _remove_rule(context, arguments):
    spec, state = spec_and_state(context)
    if spec is None:
        return {"CANCELLED"}
    index = arguments["index"]
    if 0 <= index < len(state.filter_rules):
        state.filter_rules.remove(index)
        state.filter_rules_active_index = min(state.filter_rules_active_index,
                                              len(state.filter_rules) - 1)
    spec.apply(context)
    return None


def _clear_rules(context, arguments):
    spec, state = spec_and_state(context)
    if spec is None:
        return {"CANCELLED"}
    state.filter_rules.clear()
    spec.apply(context)
    return None


def _quick_add(context, arguments):
    spec, state = spec_and_state(context)
    if spec is None:
        return {"CANCELLED"}
    rule = state.filter_rules.add()
    rule.spec_key = spec.key
    rule.field = arguments["field"]
    rule.relation = spec.quick_relation_for(arguments["field"])
    rule.value = arguments["value"]
    rule.action = arguments["action"]
    rule.enabled = True
    state.filter_rules_active_index = len(state.filter_rules) - 1
    spec.apply(context)
    return None


ADD_RULE = app_command.COMMANDS.define(
    "ruri.filter_add_rule", "Add Rule", _add_rule,
    description="Add this rule to the filter")
REMOVE_RULE = app_command.COMMANDS.define(
    "ruri.filter_remove_rule", "Remove Rule", _remove_rule,
    description="Remove this filter rule", internal=True,
    arguments=(app_state.Field("index", app_state.INT, 0),))
CLEAR_RULES = app_command.COMMANDS.define(
    "ruri.filter_clear_rules", "Clear All", _clear_rules,
    description="Remove every filter rule")
QUICK_ADD = app_command.COMMANDS.define(
    "ruri.filter_quick_add", "Quick Add Rule", _quick_add,
    description="Filter by this row's value", internal=True,
    # Plain strings, not enums: the vocabulary is per-spec, and one command's
    # argument cannot depend on which list invoked it.
    arguments=(app_state.Field("field", app_state.STRING, ""),
               app_state.Field("action", app_state.STRING, ""),
               app_state.Field("value", app_state.STRING, "")))

#: The rule editor's id. Blender opens it as a native popover (the same idiom the
#: Outliner's funnel-icon filter uses); Painter opens it as a small dialog.
RULES_PANEL = "RURI_PT_filter_popover"


# ── descriptions ──────────────────────────────────────────────────────────────
def draw_search_row(layout, state, extra_operator=None, search_field="search",
                    rules=True, extra_arguments=None):
    """The shared widget: quick-search box + the funnel that opens the rule
    editor, with the active rule count as the funnel's badge. ``extra_operator``
    is an optional (command_id, icon) a list wants on the same row (a Refresh,
    typically), so every tab's search line reads identically.

    The funnel belongs to the tab's OWN list -- the rule editor edits whichever
    list the tab declared. A second list inside the same tab says so (``rules``
    off) rather than drawing a funnel that would edit its neighbour's rules."""
    row = layout.row(align=True)
    row.prop(state, search_field, icon="VIEWZOOM", text="")
    active = sum(1 for rule in state.filter_rules if rule.enabled) if rules else 0
    # A text badge (the active rule count) carries the "filter active" signal
    # instead of a second icon.
    if rules:
        row.popover(RULES_PANEL, text=str(active) if active else "", icon="FILTER")
    if extra_operator is not None:
        # A shared command serves several lists, so the button carries WHICH list
        # pressed it -- stated by the caller, because only it knows.
        pressed = row.operator(extra_operator[0], text="", icon=extra_operator[1])
        for name, value in (extra_arguments or {}).items():
            setattr(pressed, name, value)
    return row


def draw_rules(layout, context):
    """The rule editor body. Non-modal and live-apply: every edit re-filters
    immediately, no OK/Cancel/Apply step."""
    spec, state = spec_and_state(context)
    if spec is None:
        layout.label(text="No filterable list is on screen.", icon="INFO")
        return

    column = layout.column(align=True)
    column.label(text="Display rows matching ALL these conditions:")
    note = column.column(align=True)
    note.scale_y = 0.8
    note.label(text="(no rules ⇒ show all; every enabled rule", icon="BLANK1")
    note.label(text="must hold -- Include requires a match,", icon="BLANK1")
    note.label(text="Exclude requires a non-match)", icon="BLANK1")

    layout.separator()
    builder = layout.column(align=True)
    builder.prop(state, "new_rule_field", text="")
    builder.prop(state, "new_rule_relation", text="")
    builder.prop(state, "new_rule_value", text="", icon="GREASEPENCIL")
    row = builder.row(align=True)
    row.prop(state, "new_rule_action", text="")
    row.operator(ADD_RULE.id, text="Add", icon="ADD")

    layout.separator()
    if len(state.filter_rules) == 0:
        layout.label(text="No rules yet.", icon="INFO")
        return

    rules = layout.column(align=True)
    for index, rule in enumerate(state.filter_rules):
        row = rules.row(align=True)
        row.prop(rule, "enabled", text="")
        sub = row.row(align=True)
        sub.scale_x = 0.9
        sub.prop(rule, "field", text="")
        sub.prop(rule, "relation", text="")
        row.prop(rule, "value", text="")
        icon = "ADD" if rule.action == "include" else "REMOVE"
        action = row.row(align=True)
        action.scale_x = 0.7
        action.prop(rule, "action", text="", icon=icon)
        row.operator(REMOVE_RULE.id, text="", icon="X").index = index

    layout.separator()
    layout.operator(CLEAR_RULES.id, icon="TRASH")


def quick_filter_entries(spec, value_of):
    """Include/Exclude x every field the spec declares, for one selected row.
    ``value_of(field_key)`` reads that field off the row, so this knows nothing
    about any particular list's shape."""
    entries = []
    for action_id, action_label, _description in schemas.ACTION_ITEMS:
        entries.append({"separator": True, "text": action_label + ":"})
        for field_id, field_label in spec.field_list():
            value = str(value_of(field_id))
            display = value if len(value) <= 40 else value[:37] + "..."
            relation = spec.quick_relation_for(field_id)
            word = cabmap_state.RELATION_LABELS.get(relation, relation)
            entries.append({
                "text": '{0} {1} "{2}"'.format(field_label, word, display),
                "command": QUICK_ADD.id,
                "values": {"field": field_id, "action": action_id, "value": value},
            })
    return entries
