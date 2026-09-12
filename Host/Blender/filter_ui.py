"""Blender's side of the shared search box + Include/Exclude rule editor.

Everything the filter IS -- the spec registry, the rule storage, the
selection-across-a-refill discipline, the commands, and what the editor says --
lives in :mod:`Kernel.app.filtering`, because none of it is about Blender. What
is here: the RNA materialisation of the rule record, the operator wrappers, and
the popover the editor is opened as.

The popover panel is HEADER-region on purpose: a UI-region panel is a SIDEBAR
panel, and a categoryless one lands in Blender's catch-all "Misc" tab. HEADER is
Blender's own convention for popover-only panels, reachable exactly through the
``layout.popover()`` that names them.

``draw_search_row`` still takes a bpy layout, so a panel that has not been ported
to the neutral description yet keeps working: it describes the row with the
kernel builder and renders that description into the layout it was handed.
"""

from __future__ import annotations

import bpy

from ...Kernel.app import filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import schemas
from . import render, rna

# Re-exported so the panels that already import them from here keep working; the
# declaration is the kernel's.
FilterSpec = filtering.FilterSpec
SPECS = filtering.SPECS
register_spec = filtering.register_spec
active_spec = filtering.active_spec
rebuilding = filtering.rebuilding
is_rebuilding = filtering.is_rebuilding
selected_key = filtering.selected_key
restore_selection = filtering.restore_selection
enabled_rules = filtering.enabled_rules
has_active_query = filtering.has_active_query
HANDLERS = filtering.HANDLERS
RELATION_ITEMS = list(schemas.RELATION_ITEMS)
ACTION_ITEMS = list(schemas.ACTION_ITEMS)


def set_active_spec_key(reader):
    """Tell the rule editor which list is on screen. A function rather than a
    module attribute: a module can intercept reads (PEP 562) but not writes, so
    assigning ``filter_ui.ACTIVE_SPEC_KEY`` here would create a shadow nobody
    reads while the kernel's stayed None."""
    filtering.ACTIVE_SPEC_KEY = reader


#: The rule record and the filter block, generated from the ONE declaration. The
#: record is built here and handed to every other list that contains it --
#: Blender registers a class once, and a second copy under the same name is a
#: registration error rather than a second rule type.
REGISTRY = rna.Registry(filtering.HANDLERS)
RURI_PG_filter_rule = REGISTRY.build(schemas.FILTER_RULE,
                                     {"FilterRule": "RURI_PG_filter_rule"})
#: What every filterable list inherits. A tab's PropertyGroup sets
#: FILTER_SPEC_KEY (a plain class attribute, not an RNA property) to the spec it
#: belongs to, and declares its own ``search``: the update callback differs per
#: list -- the 260k-row browser debounces, a list of 83 scenes has no reason to.
FilterStateMixin = REGISTRY.mixin(schemas.FILTER_STATE, "FilterStateMixin")
#: The rule class other schemas' registries take as prebuilt.
PREBUILT = {schemas.FILTER_RULE.name: RURI_PG_filter_rule}

class RURI_PT_filter_popover(bpy.types.Panel):
    """Process-Monitor-style Include/Exclude rule editor, Blender-native as a
    popover (the same idiom the Outliner's own funnel-icon filter uses) rather
    than a cramped multi-column row squeezed into the narrow N-panel sidebar --
    opened from the funnel button next to the search box."""
    bl_idname = filtering.RULES_PANEL
    bl_label = "Filter Rules"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 20

    def draw(self, context):
        described = app_layout.Layout()
        filtering.draw_rules(described, context)
        render.draw(described.node, self.layout, context)


def draw_search_row(layout, state, extra_operator=None):
    """Describe the shared search row and render it into a bpy layout."""
    described = app_layout.Layout()
    filtering.draw_search_row(described, state, extra_operator)
    render.draw(described.node, layout, bpy.context)
    return layout


def draw_quick_filter_menu(layout, spec, value_of):
    """The quick-filter menu body for one selected row."""
    first = True
    for entry in filtering.quick_filter_entries(spec, value_of):
        if entry.get("separator"):
            if not first:
                layout.separator()
            first = False
            layout.label(text=entry["text"])
            continue
        operator = layout.operator(entry["command"], text=entry["text"])
        for name, value in entry["values"].items():
            setattr(operator, name, value)


def register():
    REGISTRY.register()
    bpy.utils.register_class(RURI_PT_filter_popover)


def unregister():
    bpy.utils.unregister_class(RURI_PT_filter_popover)
    REGISTRY.unregister()
    # SPECS is deliberately NOT cleared: a spec is a module-level declaration
    # made at import time, and an addon re-register does not re-import. Clearing
    # here would leave every list with no vocabulary and no way to re-apply.
