"""What the panel remembers -- declared once, materialised by each host.

Every field below used to be a ``bpy.props`` line inside the Blender panel, which
made the panel's memory a Blender fact. It is not: which install is in front of
the user, what they typed in the search box, which rows they ticked and which
options they set are facts about a person browsing a game, and Painter's dock
needs the identical set.

Nothing here imports a host. Behaviour is named (see :class:`Kernel.app.state.Handlers`)
and bound where the host materialises the schema.
"""

from __future__ import annotations

from .. import options as kernel_options
from ..app import state
from ..app.state import Field, Schema
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import texture_roles


def _option_fields():
    """The import options THIS host honours, as panel fields.

    One table (:mod:`Kernel.options`) states what an import can be told to do;
    this materialises it as the panel's own memory, so the switch on screen and
    the key the pipeline reads are the same string by construction. An option
    whose capability this host does not answer produces no field at all -- there
    is nothing to store and nothing to draw, which is what "absent, not disabled"
    means one layer down.

    Read at import time, which is after the driver binds itself (a driver binds
    before it imports anything of its own) and long before a panel is drawn."""
    made = []
    for entry in kernel_options.schema():
        if entry.kind == kernel_options.BOOL:
            made.append(Field(entry.key, state.BOOL, entry.default,
                              entry.label, entry.description))
        elif entry.choices:
            # A fixed list of values is an enum wherever it is drawn; stored as the
            # text of the number, which is what an enum member id is.
            made.append(Field(entry.key, state.ENUM, str(entry.default),
                              entry.label, entry.description,
                              items=tuple((str(choice), str(choice), "")
                                          for choice in entry.choices)))
        else:
            made.append(Field(entry.key, state.INT, entry.default,
                              entry.label, entry.description,
                              minimum=entry.minimum, soft_maximum=entry.soft_maximum))
    return tuple(made)

#: The one tab that is not about a game: the cabmap itself. Every other tab is
#: contributed by a game module, which is why nothing here counts them.
BROWSER_TAB_ID = "assetbundle"
BROWSER_TAB_LABEL = "VirtualAssetBundle"
BROWSER_TAB_DESCRIPTION = ("Browse/search the loaded cabmap's rows and import "
                           "individual assets")
#: Tab key for a folder that publishes no product name of its own.
UNNAMED_TAB_PREFIX = "unknown"

SORT_COLUMNS = (("name", "Name"), ("type_names", "Type"),
                ("deps", "Deps"), ("source", "Source"))

#: Rule grammar. Relations and actions are the same for every list -- they are
#: the grammar, not a property of any one list; only the FIELDS differ, which is
#: what a filter spec declares.
RELATION_ITEMS = tuple((relation, cabmap_state.RELATION_LABELS[relation], "")
                       for relation in cabmap_state.RELATIONS)
ACTION_ITEMS = (
    ("include", "Include",
     "Require rows to match this rule (each Include rule further narrows the results)"),
    ("exclude", "Exclude",
     "Hide rows matching this rule -- always wins over any Include"),
)

UNMAPPED_ROLE = "unmapped"
ROLE_ITEMS = tuple([(UNMAPPED_ROLE, "(choose)", "Not decided yet")]
                   + [(role, label, description)
                      for role, label, description in texture_roles.ROLES])
CHANNEL_ITEMS = tuple((str(index), name, "Channel " + name)
                      for index, name in enumerate(texture_roles.CHANNEL_NAMES))


FILTER_RULE = Schema("FilterRule", """One Process-Monitor-style Include/Exclude rule --
[Field][Relation][Value] then [Include/Exclude]. This shape IS the wire form the C#
engine reads, so instances are handed to it directly.

``spec_key`` names the list the rule belongs to, which is what lets one rule type
serve every tab while each still offers only its own fields.""", (
    Field("spec_key", state.STRING, ""),
    Field("field", state.ENUM, None, "Field", items="rule_field_items", update="on_rule_edit"),
    Field("relation", state.ENUM, None, "Relation", items=RELATION_ITEMS, update="on_rule_edit"),
    Field("value", state.STRING, "", "Value", update="on_rule_edit"),
    Field("action", state.ENUM, None, "Action", items=ACTION_ITEMS, update="on_rule_edit"),
    Field("enabled", state.BOOL, True, "Enabled",
          "Untick to keep a rule without deleting it", update="on_rule_edit"),
))

FILTER_STATE = Schema("FilterState", """The filter state every filterable list carries.
One declaration rather than one per tab that can drift.

A list's own ``search`` field is NOT here: its update differs per list -- the
260k-row browser debounces, a list of 83 scenes has no reason to.""", (
    Field("filter_rules", state.COLLECTION, element=FILTER_RULE),
    Field("filter_rules_active_index", state.INT, 0),
    Field("new_rule_field", state.ENUM, None, "Field", items="builder_field_items"),
    Field("new_rule_relation", state.ENUM, "contains", "Relation", items=RELATION_ITEMS),
    Field("new_rule_value", state.STRING, "", "Value"),
    Field("new_rule_action", state.ENUM, None, "Action", items=ACTION_ITEMS),
))

LOADING_STATE = Schema("LoadingState", """What makes a panel drivable, and drawable,
by the step loader. Declared once so a panel cannot support half of it.""", (
    Field("loading", state.BOOL, False),
    Field("load_line", state.STRING, ""),
    Field("progress", state.FLOAT, 0.0, subtype=state.FACTOR, minimum=0.0, maximum=1.0),
))

CABMAP_ROW = Schema("CabmapRow", """One windowed/displayed row -- a small proxy, never
the full 260k-row set. ``selected`` is a pure display MIRROR of the authoritative
selection (which survives the window being rebuilt on every filter/sort edit); all
mutation goes through the click/select-all commands, never by writing this flag.

Does double duty as a virtual FOLDER entry (is_folder=True), so the folder browser and
the flat search/rule results share one collection and one list widget -- only
folder_name/file_count are meaningful on a folder row, only the rest on a file row.""", (
    Field("is_folder", state.BOOL, False),
    Field("folder_name", state.STRING, ""),
    Field("file_count", state.INT, 0),
    Field("row_index", state.INT, 0),
    Field("cab", state.STRING, ""),
    Field("name", state.STRING, ""),
    Field("container", state.STRING, ""),
    Field("type_names", state.STRING, ""),
    Field("source", state.STRING, ""),
    Field("deps", state.INT, 0),
    Field("selected", state.BOOL, False),
))

TEXTURE_ROLE = Schema("TextureRole", """One texture property name no layer of the role
table states, met while building the current game's materials, with the role the user
picks for it.""", (
    Field("name", state.STRING, ""),
    Field("count", state.INT, 0),
    Field("example", state.STRING, ""),
    Field("role", state.ENUM, UNMAPPED_ROLE, "Role", items=ROLE_ITEMS),
    Field("channel", state.ENUM, "0", "Channel", items=CHANNEL_ITEMS),
))

SOURCE_OPTION = Schema("SourceOption", """One value an install is READ with, as the
decoder declared it: name, kind (text|flag|choice|path|entries), the choices a choice
kind offers ('|'-joined), its default and what it means. The form the host draws IS the
decoder's published schema -- these rows are filled from that dataset, never authored
here.""", (
    Field("name", state.STRING, ""),
    Field("kind", state.STRING, ""),
    Field("value", state.STRING, "", "Value"),
    Field("path_value", state.STRING, "", "Path", subtype=state.FILE),
    Field("flag_value", state.BOOL, False, "Enabled"),
    Field("choices", state.STRING, ""),
    # A choice option's value AS a choice, so both hosts draw the decoder's own
    # list as a dropdown. The items are read off ``choices`` on this very row,
    # which is why they are a named handler rather than a constant list.
    Field("choice_value", state.ENUM, "", "Choice", items="source_option_choices"),
    Field("default", state.STRING, ""),
    Field("description", state.STRING, ""),
    # The mounted build cannot be read without this one (the decoder said so).
    Field("required", state.BOOL, False),
    # What the install is READ WITH right now, whoever answered it. A decoder that
    # recognises the build answers some options by itself -- its engine, its archive
    # keys, its reflection schema -- and an empty field beside an install that is
    # already readable reads as "you still have to find this", which is the opposite
    # of what happened. Shown, never stored: what a user types still wins.
    Field("effective", state.STRING, ""),
))

INSTALL_CONFIG = Schema("InstallConfig", """ONE INSTALL's browser inputs -- one open tab.
Several can be set up at once; the scalar game_root/cabmap_path/browsed_dir/loaded on the
browser are a get/set VIEW onto whichever of these it is currently on.

``key`` is WHICH INSTALL: the productName the build itself carries, or ``unknown_N`` for a
folder that publishes none. It is the tab's label and the identity its browser session and
cabmap slot are filed under. ``game_name`` is that same productName as the install
PUBLISHED it -- two installs of one title are two tabs (two keys) sharing one game --
``game_version`` is its own bundleVersion and ``engine_version`` the Unity version its
serialized files state. All are read once, where the folder is typed, never re-derived.

``decoder_id`` is the ONE decoder this tab reads through: resolved from that identity by
the kernel, replaceable per tab, and empty for a plain Unity build that needs none.""", (
    Field("key", state.STRING, ""),
    Field("game_name", state.STRING, ""),
    Field("game_version", state.STRING, ""),
    Field("engine_version", state.STRING, ""),
    # The engine FAMILY the install runs on ("Unity", "UnrealEngine", ...), as the
    # kernel's probe reported it: what selects a family-wide decoder and the module
    # that draws this engine's panels when the product itself has neither.
    Field("engine_family", state.STRING, ""),
    Field("decoder_id", state.STRING, ""),
    Field("game_root", state.STRING, ""),
    Field("cabmap_path", state.STRING, ""),
    Field("browsed_dir", state.STRING, ""),
    # Empty means "wherever this install's own root says", which is what the browser's
    # scalar view answers with -- so a tab set up before anyone thought about shaders
    # still has a place to put them, and a tab the user pointed elsewhere keeps it.
    Field("shader_output", state.STRING, ""),
    # How this install is READ beyond its folder, as JSON {name: value}: whatever its
    # decoder declared it needs to open the files (an archive key, an engine version,
    # a schema file). Stated by the module that claims the install, pushed to the
    # kernel before every probe, build and load.
    Field("source_options", state.STRING, ""),
    # The form rows behind that JSON, filled from the decoder's own schema dataset.
    Field("source_option_rows", state.COLLECTION, element=SOURCE_OPTION),
    Field("source_option_schema", state.STRING, ""),
))

ANIMATION_CLIP = Schema("AnimationClip", """One discovered-but-not-yet-built animation
clip. ``selected`` drives the checkbox; nothing here has been parsed past a cheap
name/size peek, so ticking a box is free until Import is clicked.

``folder`` is the game's own folder for that clip, which is what a list of a few hundred
clips is worth reading by; ``visible`` is the filter's verdict on this row. The filter
HIDES rather than removes, so a clip checked before the box was typed into is still
checked -- and still imported -- afterwards.""", (
    Field("guid", state.STRING, ""),
    Field("name", state.STRING, ""),
    Field("folder", state.STRING, ""),
    Field("size_bytes", state.INT, 0),
    Field("selected", state.BOOL, False),
    Field("visible", state.BOOL, True),
))

BROWSER = Schema("Browser", """The cabmap browser's whole state.""", (
    # Per-INSTALL inputs live in `games`, one entry per open tab; the four scalars
    # below (game_root/cabmap_path/browsed_dir/loaded) are a get/set VIEW onto the
    # entry for current_tab, so every reader of state.game_root / state.loaded works
    # unchanged while each tab keeps its own.
    # Remembered: WHICH INSTALLS ARE OPEN is the one thing nobody should have to
    # say twice. Everything an install is -- its folder, its cabmap, the decoder
    # it reads through, the identity its build published, the folder it was left
    # browsing -- is an entry here, so the tab comes back exactly as it was left
    # and only the cabmap itself (process state, never stored) needs loading.
    Field("games", state.COLLECTION, element=INSTALL_CONFIG, remembered=True),
    Field("current_tab", state.STRING, UNNAMED_TAB_PREFIX + "_1", remembered=True),

    Field("game_root", state.STRING, "", "Game Root",
          "The game's install root directory -- typing one names this tab after the "
          "product the build calls itself",
          subtype=state.DIRECTORY, getter="get_game_root", setter="set_game_root"),
    Field("cabmap_path", state.STRING, "", "Cabmap",
          "Existing cabmap FILE to load, or output path to build one -- defaults to a "
          "filename built from the install's own name, editable",
          subtype=state.FILE, getter="get_cabmap_path", setter="set_cabmap_path"),
    Field("shader_output", state.STRING, "", "Shader Folder",
          "Where Decompile Shaders writes the source -- defaults to RuriShaderOutput "
          "under this install's own root, editable, remembered per tab",
          subtype=state.DIRECTORY, getter="get_shader_output", setter="set_shader_output"),
    # Read-only on purpose: it is an observation of the live session, so there is
    # nothing for a caller to set and no way for it to disagree with reality.
    Field("loaded", state.BOOL, False, getter="get_loaded"),
    # A plain string, not an enum: the tab set is whatever the current install's game
    # contributes, and a dynamic-enum stores an INDEX into a list that changes the
    # moment the browser moves to another install -- the stored value would then point
    # at a different tab. A key nobody offers any more simply falls back to the browser.
    Field("active_tab", state.STRING, BROWSER_TAB_ID),
    Field("search", state.STRING, "", "Search",
          "Filter by Name / Container / Source / Type", update="on_search_edit"),
    # The virtual folder the browser is in. Kept with the panel (so it survives a save)
    # rather than in the live session, whose current directory is session state --
    # reloading a cabmap then lands back on this folder instead of dumping the user at
    # the root every time. Per install, so each reopens where it was left.
    Field("browsed_dir", state.STRING, "",
          getter="get_browsed_dir", setter="set_browsed_dir"),
    Field("status", state.STRING, "No cabmap loaded."),
    Field("window", state.COLLECTION, element=CABMAP_ROW),
    Field("active_index", state.INT, 0),
    # WHICH row the cursor is on, by identity. active_index is a position into a
    # filtered, capped window, so a search edit or a folder change moves what sits
    # under it; this is what the window is re-pointed at afterwards, and it survives
    # the row being filtered out and coming back.
    Field("cursor_cab", state.STRING, ""),

    # Row column widths. Each factor is relative to the space LEFT after the previous
    # column (nested split semantics). Container/Type/Deps keep the WinForms reference
    # browser's pixel widths converted through that nesting (Container 320/960 of what
    # is left after Name, Type 150/960 after both, Deps 50/960 after all three --
    # Source, no slider, is the last cell and fills the rest); Name overrides it at
    # 0.45, since the asset name is the one cell whose content is a long unique
    # identifier worth reading in full and the reference's 240/960 truncated nearly
    # every row -- but the container path is the second-longest cell and the old
    # defaults starved it to a twelfth of the row, so it is widened to about a quarter.
    # On screen these land at roughly Name .45 / Path .25 / Type .12 / Deps .05 / Source .13.
    Field("col_name_factor", state.FLOAT, 0.45, "Name", "Width of the Name column",
          subtype=state.FACTOR, minimum=0.05, maximum=0.95),
    Field("col_container_factor", state.FLOAT, 0.45, "Path",
          "Width of the Path (virtual container path) column",
          subtype=state.FACTOR, minimum=0.05, maximum=0.95),
    Field("col_type_factor", state.FLOAT, 0.4, "Type", "Width of the Type column",
          subtype=state.FACTOR, minimum=0.05, maximum=0.95),
    Field("col_deps_factor", state.FLOAT, 0.28, "Deps",
          "Width of the Deps column -- Source fills whatever's left",
          subtype=state.FACTOR, minimum=0.05, maximum=0.95),
    # A browser is the one list worth giving the screen to, and 12 rows of a
    # 260k-row map was a keyhole.
    Field("browser_rows", state.INT, 22, "Rows",
          "How many rows of the file list to show at once", minimum=6, maximum=60),

    # 导入选项由**唯一一张表**(Kernel/options.py)生成 —— 屏幕上的开关和管线读的
    # 键是同一个字符串,宿主答不出的能力对应的选项直接不存在而不是灰着。
    *_option_fields(),
    # 这一个不是选项,是 Game Shaders 的**第二个记忆值**:一个角色十几张材质,
    # 那点代价换来的是它本来的样子,当然默认开;一个场景窗口几百上千张,同样的
    # 代价就是几秒对几分钟 —— 而地形石头用内置 BSDF 看着并不差。
    Field("scene_shaders", state.BOOL, False, "Game Shaders",
          "Rebuild the game's own shading stack instead of the host's built-in BSDF. "
          "Off by default for scenes -- a scene window is hundreds of materials, and "
          "geometry/textures are identical either way. Turn it on when you are actually "
          "rendering this window rather than still deciding what to import"),
    Field("animation_character_name", state.STRING, ""),
    Field("animation_search", state.STRING, "", "Filter",
          "Filter the discovered clips by name or by the game's own folder",
          update="on_animation_search", live=True),
    Field("available_clips", state.COLLECTION, element=ANIMATION_CLIP),
    Field("available_clips_active_index", state.INT, 0),
    Field("texture_roles", state.COLLECTION, element=TEXTURE_ROLE),
), include=(FILTER_STATE, LOADING_STATE))
