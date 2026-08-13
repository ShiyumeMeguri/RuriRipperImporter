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
TABLE = "endfield.table"
CHARACTER_MODELS = "endfield.character.models"


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


# ── the game's own config containers ────────────────────────────────────────

def projected_table(container, column_specs, distinct_by="", prefer_non_empty=""):
    """One container projected into columns -- the same ColumnTable the roster and
    the scene lists draw and search. ``column_specs`` is (name, path) or
    (name, path, through_file, through_path); a join chain may be a list of hops.

    One spec crosses as one ``column`` value, its four fields separated by '|'
    (a chain's hops by ';'), so how many columns there are is stated by how many
    times the name repeats rather than by a count argument."""
    columns = []
    for spec in column_specs:
        name, path = spec[0], spec[1]
        through = spec[2] if len(spec) > 2 else ""
        through_path = spec[3] if len(spec) > 3 else ""
        if not isinstance(through, str):
            through = ";".join(through)
        if not isinstance(through_path, str):
            through_path = ";".join(through_path)
        columns.append("|".join((name, path, through, through_path)))
    return _table(TABLE, container=container, distinctBy=distinct_by,
                  preferNonEmpty=prefer_non_empty, column=columns)


# ── scenes ──────────────────────────────────────────────────────────────────

def scene_maps():
    return _column(MAPS, "map")


def landmarks():
    return [{"level_id": row["levelId"],
             "is_single_level": bool(_int(row["isSingleLevel"])),
             "min_x": row["minX"], "min_z": row["minZ"],
             "max_x": row["maxX"], "max_z": row["maxZ"]}
            for row in _rows(LANDMARKS)]


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
