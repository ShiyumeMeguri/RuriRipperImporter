"""What this game publishes, and the shapes this add-on's panels read it in.

Every one of these used to be its own bridge method, its own delegate slot on the
upstream's central hook table, and its own DTO record -- three center-class edits
per capability, and none of the results could be searched, sorted or cached
because none of them was the shared columnar shape. They are now datasets the
game's own hook registers, reached through the one entry point
(``bridge.game_data(id, **args)``), and this module is the small amount of shape
that is genuinely the PANEL's: turning a table into the dicts its lists draw.

Arguments go by NAME, and none of them says where the game is installed: the
session is opened on an install and the hook that decodes it declares which
folders under it hold content, so a caller states WHAT it wants and never WHERE.

Nothing here parses anything. A dataset arrives columnar, already searchable
under its own handle and already cached by (id, args).
"""

from __future__ import annotations

from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import class_registry

MAPS = "endfield.scene.maps"
LANDMARKS = "endfield.scene.landmarks"
CHUNK_SUMMARY = "endfield.scene.chunks"
SCENE_STATES = "endfield.scene.states"
PLACEMENTS = "endfield.scene.placements"
PLACEMENT_MATERIALS = "endfield.scene.placement_materials"
PLACEMENT_COUNTS = "endfield.scene.placement_counts"
SEED_PATHS = "endfield.scene.seed_paths"
NPC_PARTS = "endfield.npc.parts"
NPC_MANIFEST = "endfield.npc.manifest"
NPC_MATERIALS = "endfield.npc.materials"
CHARACTER_MODELS = "endfield.character.models"
LANGUAGE = "endfield.roster.language"
CAST = "endfield.roster.cast"
MODEL = "endfield.asset.model"
PART = "endfield.asset.part"
NAMED = "endfield.asset.named"
RANK = "endfield.asset.rank"
MODEL_ASSETS = "endfield.character.model_assets"
ANIMATIONS = "endfield.character.animations"
UI_CANDIDATES = "endfield.ui.candidates"
UI_SCHEMA = "endfield.ui.schema"
UI_BINDINGS = "endfield.ui.bindings"


def _table(dataset_id, **args):
    return cabmap_state.BRIDGE.game_data(dataset_id, **args)


def _rows(dataset_id, **args):
    table = _table(dataset_id, **args)
    return [{name: table.cell(index, name) for name in table.names}
            for index in range(len(table))]


def _column(dataset_id, column, **args):
    table = _table(dataset_id, **args)
    return [table.cell(index, column) for index in range(len(table))]


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# ── the cast ────────────────────────────────────────────────────────────────

# The two casts the game publishes. The hook declares the same two words; a panel
# states WHICH cast it wants, never how that cast is read.
CHARACTERS = "characters"
NPCS = "npcs"


def language_for_locale(locale):
    """The game language a host locale reads as. Which languages exist, and which
    locale lands on which, are the game's own facts."""
    rows = _rows(LANGUAGE, locale=str(locale or ""))
    return rows[0]["language"] if rows else ""


def cast(kind, language):
    """One cast, already joined to its display names and reduced to what a list
    draws: key/label/detail/group, plus the game's own columns to filter on."""
    return _table(CAST, cast=kind, language=language)


# ── scenes ──────────────────────────────────────────────────────────────────

def scene_maps(language):
    """Every scene the game ships streaming data for, under its own name, its own
    grouping, and the game's own streaming/self-contained split."""
    return [{"id": row["map"], "label": row["label"], "named": bool(_int(row["named"])),
             "group": row["group"], "streaming": bool(_int(row["streaming"]))}
            for row in _rows(MAPS, language=language)]


def landmarks(language):
    """Every place the game's map UI names, with the streaming scene it belongs to
    (empty for a place that is its own level)."""
    return [{"id": row["levelId"], "scene": row["scene"], "label": row["label"],
             "named": bool(_int(row["named"])),
             "is_single_level": bool(_int(row["isSingleLevel"])),
             "rect": (row["minX"], row["minZ"], row["maxX"], row["maxZ"])}
            for row in _rows(LANDMARKS, language=language)]


def chunk_summary(map_name):
    counts = _rows(CHUNK_SUMMARY, map=map_name)
    row = counts[0] if counts else {}
    return {
        "scene_state_ids": [_int(state) for state in _column(SCENE_STATES, "sceneState", map=map_name)],
        "anchored_files": _int(row.get("anchoredFiles", 0)),
        "anchored_bytes": _int(row.get("anchoredBytes", 0)),
        "floating_files": _int(row.get("floatingFiles", 0)),
        "floating_bytes": _int(row.get("floatingBytes", 0)),
    }


def placements(map_name, min_x, min_z, max_x, max_z, scene_state_ids, lod0_only):
    """One world rect's importable content. The placements, their material paths
    and the drop accounting are three datasets over ONE discovery -- the reader
    memoizes on the argument set they share, so asking for all three decodes the
    window once."""
    window = {"map": map_name, "minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z,
              "sceneState": list(scene_state_ids), "lod0Only": lod0_only}
    rows = _rows(PLACEMENTS, **window)
    for row in rows:
        row["asset_path"] = row.pop("assetPath")
        row["entity_name"] = row.pop("entityName")
        row["source_chunk"] = row.pop("sourceChunk")
        row["material_asset_paths"] = []

    for material in _rows(PLACEMENT_MATERIALS, **window):
        index = _int(material["placement"])
        if 0 <= index < len(rows):
            rows[index]["material_asset_paths"].append(material["path"])

    counts = _rows(PLACEMENT_COUNTS, **window)
    count = counts[0] if counts else {}
    return {
        "placements": rows,
        "seed_paths": _column(SEED_PATHS, "path", **window),
        "total": _int(count.get("total", 0)),
        "no_transform": _int(count.get("noTransform", 0)),
        "lod_filtered": _int(count.get("lodFiltered", 0)),
        "distinct_assets": _int(count.get("distinctAssets", 0)),
    }


# ── resolving a name to the rows that hold it ───────────────────────────────

def _selection(dataset_id, **args):
    """Rows of one selection dataset, in the game's own preference order. Every
    row is (cab, container, lod_rank, variant) -- the CAB an import seeds with,
    the addressable path it resolved to, and the two facts the game states about
    a path: which detail level it is, and whether it is the skinned variant."""
    return [{"cab": row["cab"], "container": row["container"],
             "lod_rank": _int(row["lodRank"]), "variant": bool(_int(row["variant"]))}
            for row in _rows(dataset_id, **args)]


def model_rows(name, family, cast=""):
    """The rows of the prefab named ``<name>_<family>`` exactly."""
    return _selection(MODEL, name=name, family=family, cast=cast)


def part_rows(part, cast=""):
    """One assembled part's rows -- prefab where the game ships one, else the
    authored skinned mesh, else its material-variant family's shared mesh."""
    return _selection(PART, part=part, cast=cast)


def named_rows(stem):
    """Every row whose asset leaf IS this name, any extension."""
    return _selection(NAMED, stem=stem)


def ranked(names):
    """{name: {mesh_name, stem, extension, lod_rank, family_stem, is_prefab}} for a
    batch of asset paths or mesh names. One crossing for the whole batch: these
    are the game's naming conventions, and a per-name call inside an import loop
    would be a round trip per placement."""
    names = [str(name) for name in names]
    if not names:
        return {}
    return {row["name"]: {"mesh_name": row["meshName"], "stem": row["stem"],
                          "lod_rank": _int(row["lodRank"]), "family_stem": row["familyStem"],
                          "is_prefab": bool(_int(row["isPrefab"]))}
            for row in _rows(RANK, name=names)}


def character_model_cabs():
    """The CABs holding the game's own per-character data assets."""
    return _column(MODEL_ASSETS, "cab")


def animation_anchor(name, cast):
    """Where this one's body animations live, or None when the game ships none.
    ``group`` is non-empty when the anchor is the shared library of a body type
    rather than this one's own folder -- which the panel says out loud."""
    rows = _rows(ANIMATIONS, name=name, cast=cast)
    if not rows:
        return None
    row = rows[0]
    return {"anchor": row["anchor"], "hits": _int(row["hits"]), "group": row["group"]}


# ── ui display stages ───────────────────────────────────────────────────────

def ui_candidates():
    """The assets a display stage is made of, and the stage prefab of each folder.
    Where they live and which prefab IS the stage are the game's own filing."""
    return _rows(UI_CANDIDATES)


def ui_schema():
    """{role: [value]} -- what a stage is made of in the game's own class names,
    and how it names the two halves of one stage."""
    schema = {}
    for row in _rows(UI_SCHEMA):
        schema.setdefault(row["role"], []).append(row["value"])
    return schema


def ui_bindings():
    """Where a stage's own values land in the host: one row per (source field,
    host target). ``gate`` is 'override' for a value that only counts when the
    volume actually overrides it."""
    return [{"source": row["source"], "target": row["target"], "slot": _int(row["slot"]),
             "components": row["components"], "gate": row["gate"]}
            for row in _rows(UI_BINDINGS)]


# ── npcs and characters ─────────────────────────────────────────────────────

def npc_manifest():
    return set(_column(NPC_MANIFEST, "template"))


def npc_parts(template_id):
    """What one npc template is assembled from. The template's own fields repeat on
    every part row -- a template IS its parts -- so the first row carries them."""
    rows = _rows(NPC_PARTS, template=template_id)
    first = rows[0] if rows else {}
    return {
        "character_id": first.get("characterId", ""),
        "lod_count": _int(first.get("lodCount", 0)),
        "facial_morph": first.get("facialMorph", ""),
        "avatar_templet": first.get("avatarTemplet", ""),
        "avatar_mesh": first.get("avatarMesh", ""),
        "parts": [row["part"] for row in rows],
    }


def npc_materials(template_id, cabs):
    """{mesh name: [material container path]} for one template."""
    assigned = {}
    for text in _mono_behaviour_texts(cabs):
        for row in _rows(NPC_MATERIALS, template=template_id, assetText=text):
            assigned.setdefault(str(row["mesh"]).lower(), []).append(row["material"])
        if assigned:
            break
    return assigned


def character_models(cabs):
    """{character id: {model, tag, asset}} -- a character's model prefab is not
    derivable from its id, so its own data asset is the only source."""
    texts = _mono_behaviour_texts(cabs)
    if not texts:
        return {}
    return {row["characterId"]: {"model": row["model"], "tag": row["tag"], "asset": row["asset"]}
            for row in _rows(CHARACTER_MODELS, assetText=texts)}


def _mono_behaviour_texts(cabs):
    """The serialized text of every MonoBehaviour in a set of CABs.

    These two readers parse a data asset's fields out of its YAML rather than off
    the typed object, so text IS their input. Producing it needs no game
    knowledge -- ``import_cabs`` narrowed to one class is the generic entry
    everything uses -- which is why it happens here and not as a second
    game-specific bridge method."""
    cabs = list(cabs)
    if not cabs or cabmap_state.BRIDGE is None:
        return []
    mono_behaviour = class_registry.id_for_name("MonoBehaviour")
    assets, _roots, _seeds, _clips, _scenes = cabmap_state.BRIDGE.import_cabs(
        cabs, [mono_behaviour] if mono_behaviour is not None else None)
    texts = []
    for blob in assets.values():
        try:
            texts.append(blob.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return texts
