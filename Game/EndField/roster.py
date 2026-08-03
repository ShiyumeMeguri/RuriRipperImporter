"""The game's own cast list, read from the game's own tables.

Everything here is a DECLARATION -- which container, which field path, which
container to resolve a text id through. The reading, the schema, the joins and
the column building all happen in C# (Ruri.Data + the TableCfg reader); this
file parses nothing and knows no byte layout.

Endfield keeps its configuration in ``Data/TableCfg/*.bytes``, one self-describing
container per table, riding the same VFS as the asset bundles. The cast comes
out of three of them, joined the way the game itself joins them:

    CharacterTable          playable characters, keyed by charId ("chr_0004_pelica")
    NpcTemplateGroupTable   named npcs, keyed by the npc's own id ("si"), each
                            carrying the templateId its model is authored under
    NpcInfoTable            every npc placement id -> that same templateId

``NpcTable`` is deliberately NOT the npc source: it only holds the npcs that
carry dialogue configuration (359 of them), so it is missing whole cast members,
and it is keyed by PLACEMENT -- one character standing in twenty scenes is twenty
rows of the same name and the same model. The template table is keyed by the unit
that can actually be loaded, which is why it neither repeats nor omits.

Display names are never in the rows themselves, and the two families reach them
differently -- which is exactly what a join CHAIN is for:

    character   name -> I18nText{id} ---------------> I18nTextTable_<LANG>[id]
    npc         name -> a text key -> TextTable[key].id -> I18nTextTable_<LANG>[id]

Selecting the language is selecting which container the last hop lands in -- one
string, not a code path.

No path is guessed anywhere here. A roster key is the id the game itself uses.
"""

from __future__ import annotations

import os

CONTAINER_DIR = "Data/TableCfg"

CHARACTER_TABLE = "CharacterTable"
NPC_TABLE = "NpcTemplateGroupTable"
NPC_INFO_TABLE = "NpcInfoTable"
TEXT_TABLE = "TextTable"

# The languages the game actually ships a text table for.
LANGUAGES = ("CN", "TC", "EN", "JP", "KR", "DE", "FR", "IT", "RU", "TH", "VN", "ID", "BR", "MX")

# Blender's locale -> the game's own language code. Matched longest-prefix, so
# "zh_HANS" and "zh_CN" both land on CN without listing every variant.
_LOCALE_PREFIXES = (
    ("zh_hant", "TC"), ("zh_tw", "TC"), ("zh_hk", "TC"),
    ("zh", "CN"),
    ("ja", "JP"), ("ko", "KR"), ("de", "DE"), ("fr", "FR"), ("it", "IT"),
    ("ru", "RU"), ("th", "TH"), ("vi", "VN"), ("id", "ID"),
    ("pt", "BR"), ("es", "MX"), ("en", "EN"),
)

# What the game itself displays when a locale it does not ship is asked for.
DEFAULT_LANGUAGE = "EN"


def container(table_name):
    """The VFS file name of one config container."""
    return "{0}/{1}.bytes".format(CONTAINER_DIR, table_name)


def text_container(language):
    return container("I18nTextTable_{0}".format(language))


def language_for_locale(locale):
    """The game language matching a Blender locale ("zh_CN" -> "CN"). Falls back
    to the game's own default for a language it does not ship."""
    key = (locale or "").lower().replace("-", "_")
    for prefix, language in _LOCALE_PREFIXES:
        if key == prefix or key.startswith(prefix + "_"):
            return language
    return DEFAULT_LANGUAGE


def vfs_roots(game_root):
    """VFS root paths in priority order -- the hot-update overlay first."""
    return [
        os.path.join(game_root, "Endfield_Data", "Persistent", "VFS"),
        os.path.join(game_root, "Endfield_Data", "StreamingAssets", "VFS"),
    ]


def character_columns(language):
    """CharacterTable, with its display name resolved through the chosen
    language's text table. Column 0 (the key) is charId and is added by C#."""
    text = text_container(language)
    return [
        ("display", "name.id", text, ""),
        ("english", "engName"),
        ("group", "profession"),
        ("weapon", "weaponType"),
        ("element", "charTypeId"),
        ("department", "department"),
        ("rarity", "rarity"),
    ]


def npc_columns(language):
    """NpcTemplateGroupTable. ``name``/``title`` are text KEYS here (not the
    ``I18nText`` the character table carries), so both take the two-hop chain."""
    chain = [container(TEXT_TABLE), text_container(language)]
    return [
        ("display", "name", chain, ["id", ""]),
        ("title", "title", chain, ["id", ""]),
        ("template", "templateId"),
    ]


def npc_template_columns():
    """Every npc placement id -> the template its model is authored under. Read
    only for its template SET: it covers models no named npc row mentions."""
    return [("template", "templateId")]


def character_rows(bridge, game_root, language):
    """One row per playable character, grouped by the game's own profession."""
    table = bridge.query_data_table(vfs_roots(game_root), container(CHARACTER_TABLE),
                                    character_columns(language))
    keys = table.values("key")
    display = table.values("display")
    english = table.values("english")
    element = table.values("element")
    weapon = table.values("weapon")
    profession = table.values("group")
    rows = []
    for index in range(table.row_count):
        rows.append({
            "key": keys[index],
            "label": display[index] or keys[index],
            "detail": " · ".join(part for part in (english[index], element[index], weapon[index]) if part),
            "group": profession[index],
        })
    rows.sort(key=lambda row: (row["group"], row["label"]))
    return rows


def npc_rows(bridge, game_root, language):
    """One row per distinct model prefab.

    The game lists an npc once per place it stands, so the raw tables repeat the
    same character (and the same prefab) dozens of times. Only the prefab can
    actually be loaded, so that is the row: templates are collapsed, and the
    named entry wins when several npcs share one model. Placement-only templates
    -- models no named npc row mentions -- are kept too, under their own id,
    because dropping them is how a cast list ends up incomplete."""
    roots = vfs_roots(game_root)
    named = bridge.query_data_table(roots, container(NPC_TABLE), npc_columns(language))
    placements = bridge.query_data_table(roots, container(NPC_INFO_TABLE), npc_template_columns())

    keys = named.values("key")
    display = named.values("display")
    title = named.values("title")
    template = named.values("template")

    by_template = {}
    for index in range(named.row_count):
        model = template[index]
        if not model:
            continue
        existing = by_template.get(model)
        # A named entry beats an unnamed one; between two named ones the first
        # wins, which is stable because the projection's row order is. An entry
        # is "named" exactly when its label is not just the template id again.
        if existing is not None and (existing["label"] != existing["key"] or not display[index]):
            continue
        by_template[model] = {
            "key": model,
            "label": display[index] or model,
            "detail": " · ".join(part for part in (keys[index], title[index]) if part),
            "group": "",
        }

    for model in placements.values("template"):
        if model and model not in by_template:
            by_template[model] = {"key": model, "label": model, "detail": "", "group": ""}

    rows = list(by_template.values())
    rows.sort(key=lambda row: row["label"])
    return rows
