"""The game's own cast list, read from the game's own tables.

Everything here is a DECLARATION -- which container, which field path, which
container to resolve a text id through. The reading, the schema, the joins and
the column building all happen in C# (Ruri.Data + the TableCfg reader); this
file parses nothing and knows no byte layout.

Endfield keeps its configuration in ``Data/TableCfg/*.bytes``, one self-describing
container per table, riding the same VFS as the asset bundles. The cast comes
out of three of them, joined the way the game itself joins them:

    CharacterTable   playable characters, keyed by charId ("chr_0004_pelica")
    NpcTable         npcs, keyed by npcId, grouped by the game's own npcGroupId
    NpcInfoTable     npcId -> the avatar templet id its model is authored under

Display names are never in those rows: a name is an ``I18nText`` carrying a
numeric id, and the text lives in ``I18nTextTable_<LANG>``. Selecting the
language is therefore selecting which container to join through -- one string,
not a code path.

No path is guessed anywhere here. A roster key is the id the game itself uses.
"""

from __future__ import annotations

import os

CONTAINER_DIR = "Data/TableCfg"

CHARACTER_TABLE = "CharacterTable"
NPC_TABLE = "NpcTable"
NPC_INFO_TABLE = "NpcInfoTable"

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
    """NpcTable. ``group`` is the game's own npcGroupId, which is what its own
    tooling groups npcs by; the faction line is the localized label."""
    text = text_container(language)
    return [
        ("display", "name.id", text, ""),
        ("title", "title.id", text, ""),
        ("faction", "faction.id", text, ""),
        ("group", "npcGroupId"),
        ("icon", "headIcon"),
    ]


def npc_template_columns():
    """npcId -> the avatar templet id the npc's model is authored under. The
    game's own link; nothing about it is derivable from the npc id."""
    return [("template", "templateId")]


def load_characters(bridge, game_root, language):
    return bridge.query_data_table(vfs_roots(game_root), container(CHARACTER_TABLE),
                                   character_columns(language))


def load_npcs(bridge, game_root, language):
    return bridge.query_data_table(vfs_roots(game_root), container(NPC_TABLE),
                                   npc_columns(language))


def load_npc_templates(bridge, game_root):
    return bridge.query_data_table(vfs_roots(game_root), container(NPC_INFO_TABLE),
                                   npc_template_columns())


def grouped(table, group_column="group", label_column="display"):
    """Rows bucketed by the game's own grouping field, each bucket sorted by the
    label the user actually sees. Returns [(group, [(row_index, label, key)])],
    groups in the game's own ordering of first appearance made stable by name."""
    buckets = {}
    keys = table.values("key")
    groups = table.values(group_column)
    labels = table.values(label_column)
    for index in range(table.row_count):
        buckets.setdefault(groups[index], []).append(
            (index, labels[index] or keys[index], keys[index]))
    for members in buckets.values():
        members.sort(key=lambda member: member[1])
    return sorted(buckets.items(), key=lambda bucket: bucket[0])
