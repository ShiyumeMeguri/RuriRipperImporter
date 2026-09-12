"""Browse the game's cast the way the game itself lists it -- in any host.

The rows come from the game's own config containers: playable characters keyed by
charId and grouped by the game's own profession, and npcs collapsed to one row per
distinct model prefab. Names are the real localized names, in whichever language
the HOST is running in -- its locale picks which text container the C# side joins
through, so switching the application's language switches the roster with no
reload of anything else.

The list behaves like the bundle browser next door: type to filter, click to
select, Load to bring it in. What "bring it in" MEANS is the host's
(:meth:`Kernel.host.Host.import_packages`); this module's business ends at
resolving the row to what the game says it is made of.

Nothing here imports a host. The Blender panel next door is a shell that
materialises this declaration; Painter's dock renders the same one.
"""

from __future__ import annotations

import json

from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command, filtering
from ...Kernel.app import layout as app_layout
from ...Kernel.app import loading, schemas
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state
from ...RuriRipperPyBridge.session import cabmap_state
from . import cast, datasets

STATE = "ruri_roster"
BROWSER_STATE = "ruri_cabmap"
SPEC_KEY = "Endfield:character"

CHARACTERS = datasets.CHARACTERS
NPCS = datasets.NPCS
CAST_PANE = "cast"
STORY_PANE = "story"

# Column labels worth spelling out; anything else reads as its own column name,
# so a column the hook adds to a cast shows up here with no edit.
_FIELD_LABELS = {"key": "Id", "display": "Name", "english": "English", "group": "Profession",
                 "npc": "Npc Id", "template": "Template", "label": "Name", "detail": "Detail",
                 "also": "Also Worn By", "shipped": "Has A Model"}

#: Loaded row lists, by (kind, language). Module scope, not panel state:
#: rebuilding the drawn list must not cost a re-read, and a column table is not
#: something a host's property system can hold anyway.
_ROWS = {}


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def browser_of(context):
    return host_port.current().panel_state(context, BROWSER_STATE)


def detail_level(context):
    """Which detail level to load, as the ONE place that states it.

    Not a setting of this tab: the same number decides what the bundle browser
    imports and what a story unit stages, and three copies of it meant picking a
    level here changed nothing there."""
    return browser_of(context).detail_level


def language(state):
    """The game language this roster is shown in -- the host's own locale, mapped
    onto the languages the game ships."""
    return datasets.language_for_locale(host_port.current().locale())


def rows(state):
    return _ROWS.get((state.kind, language(state)))


def rebuild(state):
    """Rebuild the drawn line list.

    The filter is NOT evaluated here: the search text and the Include/Exclude
    rules go to the same C# engine the bundle browser searches with, over the very
    buffers this table was built from (one ASCII fold per column, then a parallel
    vectorized sweep, then the shared rule evaluator). This side receives row ids
    and reads cells."""
    with filtering.rebuilding():
        _fill(state)


def _fill(state):
    # The selection is the cast member, not the row number: refilling this list
    # (a keystroke, a rule edit) must not hand the Load button a different one.
    chosen = filtering.selected_key(state)
    state.entries.clear()
    table = rows(state)
    if table is None:
        return
    matched = cabmap_state.BRIDGE.search_data_table(table, state.search.strip(),
                                                    state.filter_rules)
    # A row the game ships no model for gets no Load button, so it is not drawn --
    # offering one would be a lie. Which rows those are is the cast's own column
    # (a cast whose every row is loadable has no such column), and the drop happens
    # here rather than by subsetting the table out from under the search: the row
    # ids come back against the WHOLE table.
    labels = table.values("label")
    groups = table.values("group")
    shipped = table.values("shipped") if "shipped" in table.names else None
    # The ones the game actually names come FIRST. A label falls back to the row's
    # own id when the game names it nothing, and ids are ASCII while the names are
    # not -- so plain alphabetical order pushed every named npc past 1600 ids, and
    # the list read as "this game has no localized names at all".
    named = table.values("display") if "display" in table.names else None
    order = sorted((int(index) for index in matched if shipped is None or shipped[int(index)]),
                   key=lambda index: (0 if named is not None and named[index] else 1,
                                      groups[index], labels[index]))
    matched_count = len(order)
    # The whole match set, not a first-N slice: this cast tops out in the low
    # thousands, so it materializes in one go and the list simply SCROLLS. Cutting
    # it at a boundary reads as "the game ships no more of these".
    drawn = [{name: table.cell(index, name) for name in ("key", "label", "detail", "group")}
             for index in order[:cabmap_state.LIST_CAP]]

    counts = {}
    for row in drawn:
        counts[row["group"]] = counts.get(row["group"], 0) + 1

    current_group = None
    for index, row in enumerate(drawn):
        if row["group"] and row["group"] != current_group:
            current_group = row["group"]
            header = state.entries.add()
            header.label = "{0}  ({1})".format(current_group, counts[current_group])
            header.group = current_group
            header.is_group = True
        entry = state.entries.add()
        entry.label = row["label"]
        entry.key = row["key"]
        entry.group = row["group"]
        entry.detail = row["detail"]
        entry.row_index = index
    state.status = "{0} of {1} {2} · {3}{4}".format(
        matched_count, table.row_count, state.kind, language(state),
        "" if matched_count == len(drawn) else
        " · showing {0}, narrow your search to see the rest".format(len(drawn)))
    filtering.restore_selection(state, chosen)


def selected(state):
    if 0 <= state.active_index < len(state.entries):
        entry = state.entries[state.active_index]
        if not entry.is_group:
            return entry
    return None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
ROSTER_ENTRY = Schema("RosterEntry", """One drawn line: either a group header or a
cast member.""", (
    Field("label", app_state.STRING, ""),
    Field("key", app_state.STRING, ""),
    Field("group", app_state.STRING, ""),
    Field("detail", app_state.STRING, ""),
    Field("is_group", app_state.BOOL, False),
    Field("row_index", app_state.INT, -1),
))

_PANE_ITEMS = (
    (CHARACTERS, "Characters", "Playable characters, grouped by the game's own profession"),
    (NPCS, "NPCs", "Non-playable cast, one row per distinct model prefab"),
    (STORY_PANE, "Story", "The animations the game plays during story, under its own filing"),
)
_KIND_ITEMS = _PANE_ITEMS[:2]

ROSTER = Schema("Roster", """The cast browser's state.""", (
    Field("pane", app_state.ENUM, CHARACTERS, "Pane", items="pane_items",
          update="on_pane_change"),
    Field("kind", app_state.ENUM, CHARACTERS, "Cast", items=_KIND_ITEMS,
          update="on_kind_change"),
    Field("search", app_state.STRING, "", "Filter",
          "Filter by displayed name, id or group", update="on_filter_edit", live=True),
    Field("entries", app_state.COLLECTION, element=ROSTER_ENTRY),
    Field("active_index", app_state.INT, 0),
    Field("status", app_state.STRING, "Load a cabmap, then refresh the roster."),
    Field("language", app_state.STRING, ""),
    Field("load_expressions", app_state.BOOL, False, "Expressions",
          "Also load this character's SkeletalMorph expression library. Off by "
          "default: it is a separate, much larger asset family than the model"),
    Field("model_kind", app_state.ENUM, "postmodel", "Model",
          "Which of the game's own model families to import",
          items=(("postmodel", "Post", "The in-world actor model"),
                 ("uimodel", "UI", "The model menus and portraits pose"))),
), include=(schemas.FILTER_STATE, schemas.LOADING_STATE))


def _pane_items(state, context):
    """The two casts, plus Story where the host has an animation surface to play
    one on. A pane offered in a host that cannot show it is a tab that opens onto
    nothing."""
    if host_port.ANIMATION in host_port.current().capabilities:
        return _PANE_ITEMS
    return _KIND_ITEMS


def _on_filter_edit(state, context):
    rebuild(state)


def _on_pane_change(state, context):
    """三格一行里前两格选的是 cast,第三格根本不是 cast —— 所以 pane 是显示面、
    kind 仍是「哪个 cast」。pane 单向写 kind(反向永不发生),两者语义各自完整。"""
    if state.pane in (CHARACTERS, NPCS) and state.kind != state.pane:
        state.kind = state.pane


def _on_kind_change(state, context):
    """Switching cast only redraws; it never fires the refresh command. A command
    invoked from a property update runs with the UI mid-update, and its poll
    failing there raises rather than reporting."""
    if rows(state) is None:
        state.entries.clear()
        state.status = "Refresh to read the {0} out of the game's tables.".format(state.kind)
        return
    rebuild(state)


HANDLERS = app_state.Handlers(
    "Endfield.roster", base=filtering.HANDLERS,
    pane_items=_pane_items,
    on_filter_edit=_on_filter_edit,
    on_pane_change=_on_pane_change,
    on_kind_change=_on_kind_change)


def _filter_fields():
    """The rule vocabulary = the columns the CURRENTLY loaded roster table has.
    Characters and NPCs are different projections, so their filterable fields
    genuinely differ -- read off the table, never tabulated.

    The displayed NAME leads, because the first field is the one a new rule starts
    on. Table order would put the row key first -- column 0 of any projection is
    its key -- which defaults the filter to an id nobody has memorised."""
    if not host_port.bound():
        return (("label", "Name"),)
    try:
        table = rows(host_port.current().panel_state(None, STATE))
    except Exception:
        # Asking for THIS game's columns while another game's install is mounted
        # is a question its data layer answers by raising; the rule editor is not
        # the place that breaks over it.
        table = None
    if table is None:
        return (("label", "Name"),)
    names = sorted(table.names, key=lambda name: 0 if name == "label" else 1)
    return tuple((name, _FIELD_LABELS.get(name, name.replace("_", " ").title()))
                 for name in names)


FILTER_SPEC = filtering.register_spec(filtering.FilterSpec(
    key=SPEC_KEY, fields=_filter_fields,
    state_for=state_of,
    apply=lambda context: rebuild(state_of(context))))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _loaded(context):
    return browser_of(context).loaded and cabmap_state.BRIDGE is not None


def _has_selection(context):
    return _loaded(context) and selected(state_of(context)) is not None


def _refresh(context, arguments):
    """Read the cast out of the game's own config containers."""
    state = state_of(context)
    tongue = language(state)
    state.language = tongue
    try:
        table = datasets.cast(state.kind, tongue)
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    _ROWS[(state.kind, tongue)] = table
    rebuild(state)
    return None


def _load(context, arguments):
    """What loading the selected one IS, as steps -- resolve, read, hand over.

    The kind is not a branch: a playable character and an npc are two answers to
    one question (``cast.resolve``), and which one this row gets is the game's own
    filing, not a case this command picks."""
    state = state_of(context)
    entry = selected(state)
    member = {"key": entry.key, "label": entry.label,
              "character": entry.key if state.kind == CHARACTERS else "",
              "template": entry.key if state.kind == NPCS else ""}
    packages = yield command.Read(
        lambda: cast.resolve([member], detail_level(context)).get(member["key"] or ""), 0.3)
    if packages is None:
        return loading.Built(warnings=[
            "The game states no model for '{0}'.".format(entry.label or entry.key)])
    resolved = yield command.Read(lambda: loading.resolve_closure(packages.cabs), 0.75)
    yield command.Mark(0.85)
    return host_port.current().import_packages(
        context, packages, browser_of(context).as_options(), resolved=resolved)


def _settle_load(context, built):
    """The face library is a separate asset family, so it is a separate import --
    offered only where there is a face to put it on."""
    state = state_of(context)
    if built is None or (built.armature is None and not built.imported):
        return {"CANCELLED"}
    if state.load_expressions and host_port.MORPH_TARGETS in host_port.current().capabilities:
        from . import face
        face.load_library_for(context, selected(state),
                                         (built.manifest or {}).get("facial_morph", ""))
    return {"FINISHED"}


def _animation_rules(anchor):
    """按钮装进浏览器的 Include 规则集(全部成立才显示 —— 规则编辑器的 AND 语义)。
    搜索框刻意留空:规则是这个按钮的查询,搜索框留给使用者在其上再缩小范围。"""
    return [
        {"field": "container", "relation": "contains", "value": anchor, "action": "include"},
        {"field": "type_names", "relation": "contains", "value": "AnimationClip", "action": "include"},
    ]


def _animations(context, arguments):
    """List this one's animation clips over in the bundle browser.

    Anchored on the container path, not the name: the id also keys thousands of
    per-line dialogue morph clips, and a name search buries the body animation
    library under them. Falls back to the body-type group's shared library when
    this one ships no animation folder of its own -- said out loud rather than
    substituted silently, because "these are not hers" matters."""
    state = state_of(context)
    entry = selected(state)
    if entry is None:
        return {"CANCELLED"}
    found = datasets.animation_anchor(entry.key, state.kind)
    if found is None:
        state.status = "No animation folder for '{0}' in the loaded cabmap.".format(entry.label)
        return {"CANCELLED"}
    state.status = (
        "'{0}' ships no animations of its own; showing the {1} body-type library it "
        "actually plays ({2} rows).".format(entry.label, found["group"], found["hits"])
        if found["group"] else
        "{0}: {1} animation rows.".format(entry.label, found["hits"]))
    return command.COMMANDS.get("ruri.cabmap_show_rules").run(
        context, {"rules": json.dumps(_animation_rules(found["anchor"]))})


def _reveal(context, arguments):
    """Open where the selected cast member lives, over in the bundle browser. The
    query is the id the game itself keys that row by -- no path convention of ours
    is involved."""
    state = state_of(context)
    entry = selected(state)
    if entry is None:
        return {"CANCELLED"}
    reveal = command.COMMANDS.get("ruri.cabmap_reveal")
    if state.kind == CHARACTERS:
        found = datasets.model_rows(entry.key, state.model_kind, cast=CHARACTERS)
        if found:
            row = found[0]
            return reveal.run(context, {"query": entry.key, "cab": row["cab"],
                                        "folder": row["container"].rpartition("/")[0]})
        return reveal.run(context, {"query": entry.key, "cab": "", "folder": ""})
    # An npc's own name reaches no mesh at all (the meshes are named after the art
    # family, not the template), so reveal the first mesh its slot table names.
    _info, hits, _missing = cast.model_parts(entry.key, detail_level(context))
    if hits:
        cab, meshes = hits[0]
        return reveal.run(context, {"query": meshes[0], "cab": cab, "folder": ""})
    return reveal.run(context, {"query": entry.key, "cab": "", "folder": ""})


REFRESH = command.COMMANDS.define(
    "ruri.roster_refresh", "Refresh Roster", _refresh,
    description="Read the character/npc roster out of the game's own data tables",
    poll=_loaded)
LOAD = command.COMMANDS.define(
    "ruri.roster_load", "Load Model", _load,
    description="Import this one's model, exactly as the bundle browser would",
    poll=_has_selection, steps=True, status_state=STATE, settle=_settle_load,
    failure="Loading this one's model failed")
ANIMATIONS = command.COMMANDS.define(
    "ruri.roster_animations", "Find Animations", _animations,
    description=("Switch to the bundle browser and list this one's animation clips "
                 "(body animations, not the per-line dialogue morphs)"),
    poll=_has_selection)
REVEAL = command.COMMANDS.define(
    "ruri.roster_reveal", "Open Containing Folder", _reveal,
    description="Switch to the bundle browser and open where this one's assets live",
    poll=lambda context: selected(state_of(context)) is not None)


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------
#: Three siblings in a row share the width equally, which is what this list has
#: always looked like: name, then the game's own id, then the detail hard right.
#: As split factors that is a third of the whole, then half of what is left.
_COLUMNS = (
    app_layout.ListColumn("label", "Name", width=0.34, icon="OUTLINER_OB_ARMATURE"),
    # The game's own id, dimmed: with several rows sharing a display name it is
    # the only thing that tells them apart. Blank when the name already IS the
    # id, so nothing is printed twice.
    app_layout.ListColumn(lambda row: "" if row.key == row.label else "({0})".format(row.key),
                          "Id", width=0.5, enabled=False),
    app_layout.ListColumn("detail", "Detail", align=app_layout.RIGHT),
)


def draw(layout, context):
    """The cast browser. Returns which pane is on screen, so the tab that owns it
    can hand the rest of its space to the story browser instead."""
    state = state_of(context)

    if state.loading:
        layout.progress(state.progress, state.load_line)
    head = layout.row(align=True)
    head.prop(state, "pane", expand=True)
    head.operator(REFRESH.id, text="", icon="FILE_REFRESH")

    if state.pane == STORY_PANE:
        return STORY_PANE

    filtering.draw_search_row(layout, state)
    layout.list(state, "entries", "active_index", _COLUMNS, rows=10,
                identifier="roster", group_key="is_group",
                group_column=app_layout.ListColumn("label"))
    layout.label(text=state.status, icon="INFO")

    entry = selected(state)
    options = layout.column(align=True)
    options.enabled = entry is not None
    options.row(align=True).prop(state, "model_kind", expand=True)
    # 与浏览器**同一份**导入选项 —— Load 走的本来就是浏览器自己的导入,
    # 所以这里画的就是那一份,不是它的第二个子集。
    app_browser.draw_import_options(options, context, browser_of(context))
    # 表情库是这个页签自己的事:它是另一族资产、另一次导入,只有能驱动形态键的
    # 宿主才有地方放。
    if host_port.MORPH_TARGETS in host_port.current().capabilities:
        options.prop(state, "load_expressions", toggle=True, icon="SHAPEKEY_DATA")
    options.operator(LOAD.id, icon="IMPORT")
    options.operator(REVEAL.id, icon="FILE_FOLDER")
    options.operator(ANIMATIONS.id, icon="ANIM_DATA")
    return state.pane


def register():
    host_port.current().register_state(
        STATE, ROSTER, HANDLERS, extra={"FILTER_SPEC_KEY": SPEC_KEY})


def unregister():
    host_port.current().unregister_state(STATE)
    _ROWS.clear()
