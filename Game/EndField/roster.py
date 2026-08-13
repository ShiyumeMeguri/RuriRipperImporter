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

from . import datasets

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
    """Read from NpcInfoTable, which is the only container listing EVERY model,
    and reach the name by chaining out of it:

        npcId -> NpcTemplateGroupTable[npcId].name   (a text key)
              -> TextTable[key].id                   (a numeric text id)
              -> I18nTextTable_<LANG>[id]            (the string)

    Three hops, one declaration. Both tables are keyed by the same npc id, which
    is what makes the first hop legal at all."""
    chain = [container(NPC_TABLE), container(TEXT_TABLE), text_container(language)]
    return [
        ("display", "npcId", chain, ["name", "id", ""]),
        ("title", "npcId", chain, ["title", "id", ""]),
        ("template", "templateId"),
        ("npc", "npcId"),
    ]


def npc_template_columns():
    """Every npc placement id -> the template its model is authored under. Read
    only for its template SET: it covers models no named npc row mentions."""
    return [("template", "templateId")]


CHARACTERS = "characters"
NPCS = "npcs"

# How a projected table is DRAWN: which column is the row's identity (also what
# "load" and "reveal" resolve against), which is its name, which trail after it,
# and which one groups it. Data, so a new cast is a new entry here.
DISPLAY = {
    CHARACTERS: {"key": "key", "label": "display", "detail": ("english", "element", "weapon"), "group": "group"},
    NPCS: {"key": "template", "label": "display", "detail": ("npc", "title"), "group": ""},
}


def load(language, kind):
    """The projected table for one cast, built entirely on the C# side -- read,
    joined, deduplicated and searchable there. Nothing is assembled here."""
    if kind == CHARACTERS:
        return datasets.projected_table(container(CHARACTER_TABLE), character_columns(language))
    # One row per distinct model prefab, keeping the named entry when several
    # npcs share a model -- the collapse the raw table cannot express itself.
    return datasets.projected_table(container(NPC_INFO_TABLE), npc_columns(language),
                                    distinct_by="template", prefer_non_empty="display")


def buildable_templates():
    """The templates the game actually ships an assembled model for, from its own
    manifest. A roster row outside this set (an enemy, a prop, a cut character)
    has nothing to load, so offering it a Load button would be a lie."""
    return datasets.npc_manifest()


def row(table, index, kind):
    """One drawn row, pulled straight out of the columns."""
    spec = DISPLAY[kind]
    key = table.cell(index, spec["key"])
    return {
        "key": key,
        "label": table.cell(index, spec["label"]) or key,
        "detail": " · ".join(part for part in
                             (table.cell(index, column) for column in spec["detail"]) if part),
        "group": table.cell(index, spec["group"]) if spec["group"] else "",
    }


