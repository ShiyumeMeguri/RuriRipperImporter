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
NPC_MATERIALS = "endfield.npc.materials"
NPC_MESHES = "endfield.npc.meshes"
CHARACTER_MODELS = "endfield.character.models"
LANGUAGE = "endfield.roster.language"
CAST = "endfield.roster.cast"
MODEL = "endfield.asset.model"
PART = "endfield.asset.part"
NAMED = "endfield.asset.named"
RANK = "endfield.asset.rank"
MODEL_ASSETS = "endfield.character.model_assets"
ANIMATIONS = "endfield.character.animations"
MORPH_LIBRARY = "endfield.morph.library"
MORPH_ASSETS = "endfield.morph.assets"
MORPH_DRIVERS = "endfield.morph.drivers"
MORPH_FLAGS = "endfield.morph.flags"
MORPH_REFERENCES = "endfield.morph.references"
MORPH_LIPSYNC = "endfield.morph.lipsync"
MORPH_AVATARS = "endfield.morph.avatars"
MORPH_CTRLS = "endfield.morph.ctrls"
MORPH_BONES = "endfield.morph.bones"
MORPH_SHADER_PARAMS = "endfield.morph.shaderparams"
UI_CANDIDATES = "endfield.ui.candidates"
UI_SCHEMA = "endfield.ui.schema"
UI_BINDINGS = "endfield.ui.bindings"
STORY_UNITS = "endfield.story.units"
STORY_CLIPS = "endfield.story.clips"
STORY_ACTORS = "endfield.story.actors"
STORY_TIMELINE = "endfield.story.timeline"
STORY_TIMELINE_SHAPE = "endfield.story.timeline_shape"
STORY_MISSIONS = "endfield.story.missions"
STORY_QUESTS = "endfield.story.quests"
STORY_LINES = "endfield.story.lines"
STORY_STAGE = "endfield.story.stage"


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
    draws: key/label/detail/group, plus the game's own columns to filter on.

    A cast that has rows the game ships nothing loadable for carries a ``shipped``
    column saying which is which -- so the list can drop what it could not load
    without a second crossing to ask."""
    return _table(CAST, cast=kind, language=language)


# ── story playback ──────────────────────────────────────────────────────────

# Where this game plays animation from during story. The first two are the
# playback systems themselves; the third is one actor's own animation folder, the
# states a dialogue scene drives it through -- which the game names for what they
# DO ("dialog_state_idle", "idle_talkhands") rather than for the shot they were
# authored in. A panel states WHICH channel it wants, never how one is filed.
CUTSCENE = "cutscene"
DIALOG = "dialog"
LIBRARY = "library"

# What a row of ``story_lines`` is. A spoken line has a speaker and an emotion; a
# reply is what the player is offered back; a subtitle is a cutscene's own text,
# which the game files without a speaker at all. A panel states WHICH it is
# drawing, never how one is told from another.
LINE_SPOKEN = "line"
LINE_OPTION = "option"
LINE_SUBTITLE = "subtitle"

# Which of the game's own rosters names an actor -- a playable character, an npc
# template, or one placement of one. Empty is the game naming them nowhere, which
# is what a camera, a prop and a crowd model all are.
ACTOR_CHARACTER = "character"
ACTOR_NPC = "npc"
ACTOR_PLACED = "placed"


def story_units(channel="", language=""):
    """Every unit of story playback the game files animations under, as the table
    itself -- so the list searches through the same C# engine every other list
    here does. One row is a cutscene or a dialogue timeline, with the shot/actor/
    clip counts of its own folder and the protagonist variants it ships in.

    Given a language the same row also says what the unit IS: the mission that
    plays it under that mission's own name, the place and chapter that mission
    belongs to, how many spoken lines the unit has, and -- for a dialogue -- the
    recap the game itself writes for that scene. Searching the list then searches
    those too, so typing a mission's name finds everything it plays."""
    return _table(STORY_UNITS, channel=channel, language=language)


def story_missions(language):
    """Every mission the game ships, read off the runtime asset it plays each one
    from: name and description in the chosen language, the level it belongs to
    under that level's own name, its chapter and kind, the character it belongs
    to, and how much story it plays."""
    return _table(STORY_MISSIONS, language=language)


def story_quests(mission, language):
    """One mission's quest graph in the order its own main path walks it: each
    objective in the words the player reads, what it waits on, which quests come
    before it, and the scene, area, npc, dialogue or cutscene it points at."""
    return _table(STORY_QUESTS, mission=mission, language=language)


def story_lines(unit="", mission="", language=""):
    """What is said, in playback order -- a dialogue scene's lines with speaker,
    display name and the emotion the face is driven to, the replies the player is
    offered, and a cutscene's subtitles. By unit it answers that one unit; by
    mission it answers every unit that mission plays."""
    return _table(STORY_LINES, unit=unit, mission=mission, language=language)


def story_clips(channel="", unit="", actor="", language=""):
    """The animations of ONE unit, or of ONE actor across every unit it plays in.

    Same columns either way: the unit and shot the row belongs to, the kind of
    thing it moves, the actor under the name the game gives them, the mission
    playing that unit under its own name, and whether the row is an importable
    clip (a dialogue timeline files the morph asset next to the clip that drives
    it). Asked by actor, the answer also carries that one's own animation
    library."""
    return _table(STORY_CLIPS, channel=channel, unit=unit, actor=actor, language=language)


def story_stage(unit, variant="", language=""):
    """One unit as a STAGE: every directive its own Timeline gives, in time order
    and in seconds -- what moves, which shot is live, what is said and by whom,
    where playback holds for a click and where an option jumps to.

    This is the game's performance stated in what a HOST DOES. Nothing in it is
    Unity-shaped, so nothing on this side has to know what an ActivationTrack is;
    a directive is a word, and the stage builder has one function per word."""
    return _table(STORY_STAGE, unit=unit, variant=variant, language=language)


def story_timeline(unit, variant=""):
    """One unit's playback PLAN, read off its own Timeline assets: every clip the
    timeline places, the track and bound object it belongs to, and the times the
    timeline gives it -- start, duration, clip-in, speed, blends, in seconds --
    plus the clip's own sample rate. Laying these out reproduces the scene the
    game plays; the clip list next door only says which takes exist."""
    return _table(STORY_TIMELINE, unit=unit, variant=variant)


def story_timeline_shape(unit, variant=""):
    """What one unit's Timeline assets carry, field by field. Diagnostic: the
    reader binds to field names, and this is how a drift in them is seen."""
    return _table(STORY_TIMELINE_SHAPE, unit=unit, variant=variant)


def story_actors(channel="", language=""):
    """Everyone the story animates, under the name the game gives them and with
    the STORIES they appear in by those stories' own names -- one row per PERSON,
    not per file shape, since the game writes the same one's morph clips under
    several naming conventions. A token the game's roster does not know (a camera,
    a prop, a crowd model) stays as written and is simply unnamed."""
    return _table(STORY_ACTORS, channel=channel, language=language)


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
    window once.

    The placement rows stay COLUMNAR (the ColumnTable itself): a real window is
    10^5 rows, read only in bulk -- transform columns feed one batched matrix
    build, the asset-path column one distinct-key pass. Materializing a python
    dict per row was pure overhead paid before a single object existed. Only the
    sparse per-row material lists are folded into a plain dict here."""
    window = {"map": map_name, "minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z,
              "sceneState": list(scene_state_ids), "lod0Only": lod0_only}
    table = _table(PLACEMENTS, **window)

    materials = _table(PLACEMENT_MATERIALS, **window)
    materials_by_row = {}
    if len(materials):
        placement_column = materials.values("placement")
        path_column = materials.values("path")
        for i in range(len(materials)):
            materials_by_row.setdefault(int(placement_column[i]), []).append(path_column[i])

    counts = _rows(PLACEMENT_COUNTS, **window)
    count = counts[0] if counts else {}
    return {
        "table": table,
        "materials_by_row": {row: tuple(paths) for row, paths in materials_by_row.items()},
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


# ── the facial morph library ────────────────────────────────────────────────

def morph_library():
    """Every SkeletalMorph asset the loaded map carries, bucketed by the kind the
    game files it under. Where that family lives and how its folders name a kind
    are the game's own filing."""
    return _rows(MORPH_LIBRARY)


def morph_assets(cabs):
    """The pose/emotion/animation/lipsync assets these CABs carry. Which of the
    four an asset IS comes from the fields it carries, which is the hook's read."""
    return [{"name": row["name"], "kind": row["kind"], "duration": row["duration"],
             "animated": bool(_int(row["animated"]))}
            for row in _rows(MORPH_ASSETS, cab=list(cabs))]


def morph_drivers(cabs):
    """One ctrl inside one channel. ``curve`` is a clip_curves.Channel rebuilt
    from the keyframes the hook handed over as raw float32 -- the same channel
    object the transform curves use, so a host samples both through one
    vectorized evaluator."""
    from ...RuriRipperPyBridge.unity import clip_curves
    import numpy

    built = []
    for row in _rows(MORPH_DRIVERS, cab=list(cabs)):
        keys = _int(row["keys"])
        curve = None
        if keys:
            frames = numpy.frombuffer(row["curve"], dtype="<f4").reshape((keys, 4)).astype(numpy.float64)
            curve = clip_curves.Channel(row["ctrl"], frames[:, 0].copy(),
                                        frames[:, 1:2].copy(), frames[:, 2:3].copy(),
                                        frames[:, 3:4].copy(), attribute=row["ctrl"])
        built.append({"asset": row["asset"], "channel": row["channel"], "ctrl": row["ctrl"],
                      "has_value": bool(_int(row["hasValue"])), "value": row["value"],
                      "curve": curve})
    return built


def morph_flags(cabs):
    """The boolean switches an asset carries, by the game's own field names."""
    return [{"asset": row["asset"], "flag": row["flag"], "value": bool(_int(row["value"]))}
            for row in _rows(MORPH_FLAGS, cab=list(cabs))]


def morph_references(cabs):
    """What one asset points at, by the pointed-at asset's own name. ``index`` is
    -1 for a single reference and the slot for a list."""
    return [{"asset": row["asset"], "field": row["field"], "index": _int(row["index"]),
             "target": row["target"]}
            for row in _rows(MORPH_REFERENCES, cab=list(cabs))]


def morph_lipsync(cabs):
    """A lipsync config's phoneme sets: which pose fills which slot of which set."""
    return [{"asset": row["asset"], "set": _int(row["set"]), "slot": _int(row["slot"]),
             "target": row["target"]}
            for row in _rows(MORPH_LIPSYNC, cab=list(cabs))]


def morph_avatars(cabs):
    """The ctrl-to-bone tables these CABs carry, with the character tag the game
    joins each to. Read off the game's own typed assets by the hook."""
    return [{"name": row["name"], "tag_id": _int(row["tagId"])}
            for row in _rows(MORPH_AVATARS, cab=list(cabs))]


def morph_ctrls(cabs):
    """Every ctrl a table declares, including the ones that move no bone at all
    -- a ctrl the game states but nothing moves is not the same as one it never
    states, and a rig binding pass has to be able to tell them apart."""
    return [{"avatar": row["avatar"], "ctrl": row["ctrl"], "bones": _int(row["bones"])}
            for row in _rows(MORPH_CTRLS, cab=list(cabs))]


def morph_bones(cabs):
    """What one ctrl moves: a per-bone TRS delta. A row with no ctrl is that
    bone's base pose (weight 0)."""
    return [{"avatar": row["avatar"], "ctrl": row["ctrl"], "bone_id": _int(row["boneId"]),
             "bone": row["bone"],
             "position": (row["px"], row["py"], row["pz"]),
             "rotation": (row["rx"], row["ry"], row["rz"]),
             "scale": (row["sx"], row["sy"], row["sz"])}
            for row in _rows(MORPH_BONES, cab=list(cabs))]


def morph_shader_params(cabs):
    """The ctrls that drive a material parameter rather than geometry."""
    return [{"avatar": row["avatar"], "ctrl": row["ctrl"], "param": row["param"],
             "default": row["default"]}
            for row in _rows(MORPH_SHADER_PARAMS, cab=list(cabs))]


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


def npc_meshes(cabs):
    """{part slot: {detail level: [{name, path}]}} for one avatar-mesh family.

    A template's part names are SLOT names in this table, not asset names -- which
    mesh a slot wears, and WHERE that mesh lives, are stated here and nowhere else.
    The slot's own meshPathHash resolves through the game's addressable hash table
    to the container path, so nothing has to be matched by name: the mesh an npc
    wears is routinely a sub-asset of a shared fbx, or a baked mesh under a
    generated/ tree, neither of which carries its own name anywhere findable."""
    texts = _mono_behaviour_texts(cabs)
    if not texts:
        return {}
    slots = {}
    for row in _rows(NPC_MESHES, assetText=texts):
        levels = slots.setdefault(str(row["part"]), {})
        levels.setdefault(_int(row["lod"]), []).append(
            {"name": str(row["mesh"]), "path": str(row["path"])})
    return slots


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
