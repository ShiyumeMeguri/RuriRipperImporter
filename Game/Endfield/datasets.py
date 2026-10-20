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

from ...Kernel.app import picture, staging
from ...Kernel.bridge import cabmap_state

MAPS = "endfield.scene.maps"
LANDMARKS = "endfield.scene.landmarks"
CHUNK_SUMMARY = "endfield.scene.chunks"
SCENE_STATES = "endfield.scene.states"
SCENE_MAP = "endfield.scene.map"
PLACEMENTS = "endfield.scene.placements"
PLACEMENT_MATERIALS = "endfield.scene.placement_materials"
PLACEMENT_COUNTS = "endfield.scene.placement_counts"
SEED_PATHS = "endfield.scene.seed_paths"
NPC_PARTS = "endfield.npc.parts"
CHARACTER_MODELS = "endfield.character.models"
LANGUAGE = "endfield.roster.language"
CAST = "endfield.roster.cast"
MODEL = "endfield.asset.model"
PART = "endfield.asset.part"
NAMED = "endfield.asset.named"
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
FACE_RETARGET = "endfield.face.retarget"
UI_STAGES = "endfield.ui.stages"
UI_STAGE = "endfield.ui.stage"
STORY_UNITS = "endfield.story.units"
STORY_CLIPS = "endfield.story.clips"
STORY_ACTORS = "endfield.story.actors"
STORY_MISSIONS = "endfield.story.missions"
STORY_QUESTS = "endfield.story.quests"
STORY_LINES = "endfield.story.lines"
STORY_STAGE = "endfield.story.stage"
SCENE_ENVIRONMENT = "endfield.scene.environment"
SCENE_AMBIENT = "endfield.scene.ambient"
SCENE_ANCHOR = "endfield.scene.anchor"
SCENE_GLOBALS = "endfield.scene.globals"
SCENE_IRRADIANCE = "endfield.scene.irradiance"
SCENE_REFLECTION = "endfield.scene.reflection"
SCENE_FOG = "endfield.scene.fog"
RENDER_PIPELINE = "endfield.render.pipeline"
SCENE_COOKIES = "endfield.scene.cookies"
SCENE_CLOUD_SHADOW = "endfield.scene.cloud_shadow"
SCENE_SHADOW_RAMP = "endfield.scene.shadow_ramp"
SCENE_WATER = "endfield.scene.water"
SCENE_DECALS = "endfield.scene.decals"
SCENE_DECAL_BOXES = "endfield.scene.decal_boxes"
#: The names the game's scene stack reads its deferred decals through: the per-object
#: global holding where an object's run of decal indices starts and how long it is, and
#: the data table those runs go into.
DECAL_RANGE = "_DecalRange"
DECAL_LISTS = "_DecalLists"
PROBE_RANGE = "_ReflectionProbeRange"
PROBE_LISTS = "_ReflectionProbeLists"
SCENE_REFLECTION_BOXES = "endfield.scene.reflection_boxes"
SCENE_WATER_WETNESS = "endfield.scene.water_wetness"
SCENE_WATER_WETNESS_BOXES = "endfield.scene.water_wetness_boxes"
#: The names the game's scene stack reads its water wetness mask through: the per-object global
#: holding an object's run of water proxy triangles, and the data table those runs go into.
WETNESS_RANGE = "_WaterWetnessRange"
WETNESS_LISTS = "_WaterWetnessLists"


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

# The kinds the cast list is narrowed by. The hook declares the same three words and
# they ARE what the facet switch shows; a panel states WHICH kind a row is, never how
# that kind is read. A character's in-world actor and the model its menus pose are two
# of them, because which one you want is a fact about the row rather than a second
# switch beside the list.
CHARACTERS = "Characters"
UI_MODELS = "UI Models"
NPCS = "NPCs"

#: The model family each kind resolves through, in the game's own words.
POST_MODEL = "postmodel"
UI_MODEL = "uimodel"


def language_for_locale(locale=""):
    """The game language a host locale reads as. Which languages exist, and which
    locale lands on which, are the game's own facts; WHICH locale that is, is the
    session's -- stating nothing here asks about the language the host declared."""
    rows = _rows(LANGUAGE, locale=str(locale or ""))
    return rows[0]["language"] if rows else ""


def cast(language):
    """The WHOLE cast, already joined to its display names and reduced to what a
    list draws: kind/key/label/detail/group, plus the game's own columns to filter
    on. One table, because it is one list -- which half a row is, is its ``kind``,
    and narrowing by that is the facet switch every list here already has.

    A row the game ships nothing loadable for carries a ``shipped`` column saying
    so, which is how the list drops it without a second crossing to ask."""
    return _table(CAST, language=language)


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


def story_actors(channel="", language=""):
    """Everyone the story animates, under the name the game gives them and with
    the STORIES they appear in by those stories' own names -- one row per PERSON,
    not per file shape, since the game writes the same one's morph clips under
    several naming conventions. A token the game's roster does not know (a camera,
    a prop, a crowd model) stays as written and is simply unnamed."""
    return _table(STORY_ACTORS, channel=channel, language=language)


# ── scenes ──────────────────────────────────────────────────────────────────

def scene_map_table(language):
    """The scene table exactly as the hook built it -- what the scene LIST is a
    view of. The dict form below stays for the loaders, which genuinely want every
    row's values; a list wants a view, not rows."""
    return _table(MAPS, language=language)


def landmark_table(language):
    """The places table exactly as the hook built it."""
    return _table(LANDMARKS, language=language)


def scene_maps(language):
    """Every scene the game ships streaming data for, under its own name, its own
    grouping, and the game's own streaming/self-contained split."""
    return [{"id": row["map"], "label": row["label"], "named": bool(_int(row["named"])),
             "group": row["group"], "streaming": bool(_int(row["streaming"])),
             "seed": row["seed"]}
            for row in _rows(MAPS, language=language)]


def landmarks(language):
    """Every place the game's map UI names, with the streaming scene it belongs to
    (empty for a place that is its own level)."""
    return [{"id": row["levelId"], "scene": row["scene"], "label": row["label"],
             "named": bool(_int(row["named"])),
             "is_single_level": bool(_int(row["isSingleLevel"])),
             "rect": (row["minX"], row["minZ"], row["maxX"], row["maxZ"]),
             "seed": row["seed"]}
            for row in _rows(LANDMARKS, language=language)]


def scene_map(level_id, label, budget):
    """The picture the game's map UI draws for one place, as a :class:`Kernel.app.picture.Picture`:
    the finest detail its map sprites ship whose longer side fits ``budget`` pixels."""
    table = _table(SCENE_MAP, levelId=level_id, budget=int(budget))
    try:
        rect = tuple(table.cell(0, side) for side in ("minX", "minZ", "maxX", "maxZ"))
        height, width = int(table.cell(0, "height")), int(table.cell(0, "width"))
        pixels = table.view(0, "pixels").reshape(height, width, 4).copy()
    finally:
        table.close()
    return picture.Picture(rect, pixels, label)


def _viewer(map_name, anchor, states):
    """The arguments every camera-centred reading of one map goes by: the viewer, a
    point in the game's own world, and the scene states whose content streams."""
    x, y, z = anchor
    return {"map": map_name, "x": x, "y": y, "z": z, "states": [str(state) for state in states]}


def scene_environment(map_name, anchor, states):
    """The environment a viewer at ``anchor`` stands under, off the phase the map's own
    environment volumes put it under, in the shape
    :meth:`Kernel.host.SceneGraph.apply_environment` takes: the sky irradiance the phase
    bakes -- nine coefficients per channel, red then green then blue, as the source's own
    shading stack samples them -- and its main light as staging targets."""
    viewer = _viewer(map_name, anchor, states)
    sky = sorted(_rows(SCENE_AMBIENT, **viewer), key=lambda row: int(float(row["index"])))
    row = _rows(SCENE_ENVIRONMENT, **viewer)[0]

    def number(name):
        return float(row[name])

    return {
        "label": row["volume"],
        "ambient": {"label": sky[0]["volume"],
                    "coefficients": [[float(entry[channel]) for entry in sky]
                                     for channel in ("r", "g", "b")]},
        "light": [
            (staging.LIGHT_DIRECTION, (number("lightX"), number("lightY"), number("lightZ"))),
            (staging.LIGHT_COLOR, (number("lightR"), number("lightG"), number("lightB"))),
            (staging.LIGHT_ENERGY, number("lightIntensity")),
            (staging.LIGHT_ANGLE, number("lightRadius")),
            (staging.LIGHT_SHADOWS, number("lightShadows")),
            (staging.LIGHT_VOLUME, number("lightVolume")),
        ],
    }


def scene_globals(map_name, anchor, states):
    """The engine globals the map sets for every material where a viewer at ``anchor``
    stands, keyed by the engine's own global names -- already packed the way the build
    packs them, so they go to the shading stacks as they are."""
    return {row["name"]: tuple(float(row[axis]) for axis in ("x", "y", "z", "w"))
            for row in _rows(SCENE_GLOBALS, **_viewer(map_name, anchor, states))}


def scene_irradiance(map_name, anchor):
    """The map's baked irradiance rebuilt around ``anchor`` (a point in the game's own
    world) as the game's camera clipmaps -- a level-resources payload the host applies
    as it is."""
    x, y, z = anchor
    return cabmap_state.BRIDGE.game_data_blob(SCENE_IRRADIANCE, map=map_name, x=x, y=y, z=z)


def render_pipeline():
    """What the game's render pipeline makes on the CPU and binds for every material,
    whatever the level (its multiscattering energy table) -- a level-resources payload
    the host applies as it is."""
    return cabmap_state.BRIDGE.game_data_blob(RENDER_PIPELINE)


def scene_cookies(map_name, states):
    """The light cookie atlas the map's lamps sample their cookies from, in the given scene states
    -- a level-resources payload the host applies as it is."""
    return cabmap_state.BRIDGE.game_data_blob(
        SCENE_COOKIES, map=map_name, states=[str(state) for state in states])


def scene_decals(map_name, rect, states):
    """The deferred decals one world rect of the map streams in the given scene states -- their
    records in drawing order and the textures they sample, a level-resources payload the host
    applies as it is."""
    min_x, min_z, max_x, max_z = rect
    return cabmap_state.BRIDGE.game_data_blob(
        SCENE_DECALS, map=map_name, minX=min_x, minZ=min_z, maxX=max_x, maxZ=max_z,
        sceneState=[str(state) for state in states])


def scene_decal_boxes(map_name, rect, states):
    """The same decals' boxes in drawing order, in the shape
    :meth:`Kernel.host.SceneGraph.apply_box_lists` takes: per decal, the column-major matrix taking
    the unit cube onto its box, in the game's own world."""
    min_x, min_z, max_x, max_z = rect
    rows = _rows(SCENE_DECAL_BOXES, map=map_name, minX=min_x, minZ=min_z, maxX=max_x, maxZ=max_z,
                 sceneState=[str(state) for state in states])
    return [[float(row["o{0}".format(index)]) for index in range(16)] for row in rows]


def scene_water_wetness(map_name, rect, states):
    """What the game's water wetness pass draws with for one world rect of the map in the given scene
    states -- every water proxy triangle with its proxy's parameters, the pass's 3D noise and whether it
    runs, a level-resources payload the host applies as it is."""
    min_x, min_z, max_x, max_z = rect
    return cabmap_state.BRIDGE.game_data_blob(
        SCENE_WATER_WETNESS, map=map_name, minX=min_x, minZ=min_z, maxX=max_x, maxZ=max_z,
        sceneState=[str(state) for state in states])


def scene_water_wetness_boxes(map_name, rect, states):
    """The same water proxy triangles in the payload's order, in the shape
    :meth:`Kernel.host.SceneGraph.apply_box_lists` takes: per triangle, the column-major matrix taking
    the unit cube onto the region its wetness mask can change, in the game's own world."""
    min_x, min_z, max_x, max_z = rect
    rows = _rows(SCENE_WATER_WETNESS_BOXES, map=map_name, minX=min_x, minZ=min_z, maxX=max_x, maxZ=max_z,
                 sceneState=[str(state) for state in states])
    return [[float(row["o{0}".format(index)]) for index in range(16)] for row in rows]


def scene_reflection_boxes(map_name, anchor, states):
    """The boxes of the reflection probes a viewer at ``anchor`` binds, in slot order from slot 1, in the
    shape :meth:`Kernel.host.SceneGraph.apply_box_lists` takes: per probe, the column-major matrix taking
    the unit cube onto the region its box test admits, in the game's own world."""
    rows = _rows(SCENE_REFLECTION_BOXES, **_viewer(map_name, anchor, states))
    return [[float(row["o{0}".format(index)]) for index in range(16)] for row in rows]


def scene_cloud_shadow(map_name, anchor, states):
    """The cloud shadow texture the level binds where a viewer at ``anchor`` (a point in the
    game's own world) stands, in the given scene states -- a level-resources payload the host
    applies as it is."""
    x, y, z = anchor
    return cabmap_state.BRIDGE.game_data_blob(
        SCENE_CLOUD_SHADOW, map=map_name, x=x, y=y, z=z, states=[str(state) for state in states])


def scene_shadow_ramp(map_name, anchor, states):
    """The ramp the main light's shadowed share is tinted through where a viewer at ``anchor``
    (a point in the game's own world) stands, in the given scene states -- a level-resources
    payload the host applies as it is."""
    x, y, z = anchor
    return cabmap_state.BRIDGE.game_data_blob(
        SCENE_SHADOW_RAMP, map=map_name, x=x, y=y, z=z, states=[str(state) for state in states])


def scene_water(map_name, rect, anchor, states):
    """What the level's water passes read where a viewer at ``anchor`` (a point in the game's own
    world) stands over one world rect, in the given scene states -- the water data array (the level's
    own configurations and those of the rect's planes), its LOD parameters and its textures, a
    level-resources payload the host applies as it is. A level without water states only its LOD
    parameters."""
    min_x, min_z, max_x, max_z = rect
    x, y, z = anchor
    return cabmap_state.BRIDGE.game_data_blob(
        SCENE_WATER, map=map_name, minX=min_x, minZ=min_z, maxX=max_x, maxZ=max_z,
        sceneState=[str(state) for state in states], x=x, y=y, z=z)


def scene_reflection(map_name, anchor, states):
    """The reflection probes a camera at ``anchor`` (a point in the game's own world)
    uses, in the given scene states, rebuilt as the game's probe records and
    octahedral probe array -- a level-resources payload the host applies as it is."""
    x, y, z = anchor
    return cabmap_state.BRIDGE.game_data_blob(
        SCENE_REFLECTION, map=map_name, x=x, y=y, z=z, states=[str(state) for state in states])


def scene_medium(map_name, anchor, states):
    """The participating medium the map integrates where a viewer at ``anchor`` stands -- its
    volumetric fog, off the phase the viewer is under -- in the shape
    :meth:`Kernel.host.SceneGraph.apply_medium` takes, or None when that phase runs none."""
    rows = _rows(SCENE_FOG, **_viewer(map_name, anchor, states))
    if not rows:
        return None
    row = rows[0]

    def number(name):
        return float(row[name])

    def triple(prefix, suffixes="RGB"):
        return tuple(number(prefix + suffix) for suffix in suffixes)

    return {
        "label": row["volume"],
        "range": (number("start"), number("end")),
        "grid": (int(number("slices")), int(number("tilePixels")), number("distribution")),
        "albedo": triple("albedo"),
        "density_scale": number("densityScale"),
        "layers": tuple((number("layerHeight" + str(i)), number("layerDensity" + str(i)),
                         number("layerFalloff" + str(i))) for i in range(2)),
        "anisotropy": number("anisotropy"),
        "near_fade": number("nearFade"),
        "emission": triple("emission"),
        "ambient_scale": number("ambientScale"),
        "ambient": tuple(triple("ambient" + channel, ("x", "y", "z", "w")) for channel in "RGB"),
        "light": {
            "direction": triple("light", "XYZ"),
            "color": triple("light"),
            "intensity": number("lightIntensity"),
            "scale": number("lightScale"),
        },
        "punctual_shadows": number("punctualShadows") > 0.5,
    }


def scene_grading(map_name, anchor, states):
    """The colour grading the map applies where a viewer at ``anchor`` stands, ready for
    the post stage's own inputs.

    ``inputs`` is keyed by the post stage's own input names and holds the DIFFERENCE from
    identity, which is what those sockets take: an entry parameter's socket default in
    Blender is always zero, so only a difference makes "nothing drives it" mean "no
    grading" -- a session with a character and no scene has to land on identity, not on
    contrast 0. The identity differs per component, which is why the subtraction is
    spelled out rather than applied in bulk: hue is an offset (identity 0) while
    saturation, contrast and the colour filter are multipliers (identity 1).

    ``exposureStops`` is the phase's manual exposure compensation, in stops -- zero is
    identity as it stands, since the multiplier is two to that power. ``exposureAuto``
    says the phase meters its frame instead (the build's histogram auto exposure), and the
    metering's own parameters ride along as the phase states them: the EV range the
    histogram spans, the EV range the slots clamp into, the luminance percentile band kept,
    the exposure clamp range, the metering mode and the compensation the metered
    exposure is multiplied by. They mean something only with ``exposureAuto`` on, so they
    go out as stated rather than as differences.

    White balance is the LMS coefficients the build computes from the phase's temperature
    and tint (the identity when the phase turns it off); they are multipliers, so the
    difference from one goes out.

    The bloom inputs are the build's own packed constants as they stand: their zero is
    already "no bloom" (the intensity packs as two to its power minus one), so there is
    no identity to subtract.

    The screen-space reflection inputs are the post stack's switch and the two fades its
    trace passes read (screen edge; mirror angle as multiply and add). Their zero is the
    switch off, which is identity; the fades mean something only with it on, so they go
    out as the volumes state them.
    """
    row = _rows(SCENE_ENVIRONMENT, **_viewer(map_name, anchor, states))[0]
    return {
        "tonemap": float(row.get("tonemap") or 0),
        "inputs": {
            "exposureAuto": float(row["exposureAuto"]),
            "exposureStops": float(row["exposureStops"]),
            "exposureAutoCompensation": float(row["exposureAutoCompensation"]),
            "exposureRange": (float(row["exposureRangeX"]), float(row["exposureRangeY"])),
            "exposureHistogramRange": (float(row["exposureHistogramX"]), float(row["exposureHistogramY"])),
            "exposurePixelRange": (float(row["exposurePixelX"]), float(row["exposurePixelY"])),
            "exposureClampRange": (float(row["exposureClampX"]), float(row["exposureClampY"])),
            "exposureMeteringMode": float(row["exposureMetering"]),
            "gradeColorBalance": (float(row["gradeBalanceL"]) - 1.0,
                                  float(row["gradeBalanceM"]) - 1.0,
                                  float(row["gradeBalanceS"]) - 1.0),
            "gradeColorFilter": (float(row["gradeFilterR"]) - 1.0,
                                 float(row["gradeFilterG"]) - 1.0,
                                 float(row["gradeFilterB"]) - 1.0),
            "gradeHueSatCon": (float(row["gradeHue"]),
                               float(row["gradeSaturation"]) - 1.0,
                               float(row["gradeContrast"]) - 1.0),
            "bloomParams": tuple(float(row["bloomParams" + axis]) for axis in "XYZW"),
            "bloomThreshold": tuple(float(row["bloomThreshold" + axis]) for axis in "XYZW"),
            "bloomTint": tuple(float(row["bloomTint" + channel]) for channel in "RGB"),
            "bloomScatter": float(row["bloomScatter"]),
            "bloomResolution": float(row["bloomResolution"]),
            "ssrEnabled": float(row["ssrEnable"]),
            "ssrScreenFade": float(row["ssrScreenFade"]),
            "ssrMirrorFade": (float(row["ssrMirrorMultiply"]), float(row["ssrMirrorAdd"])),
        },
    }


def chunk_summary(map_name):
    counts = _rows(CHUNK_SUMMARY, map=map_name)
    row = counts[0] if counts else {}
    return {
        "scene_state_ids": [_int(state) for state in _column(SCENE_STATES, "sceneState", map=map_name)],
        "anchored_files": _int(row.get("anchoredFiles", 0)),
        "anchored_bytes": _int(row.get("anchoredBytes", 0)),
        "floating_files": _int(row.get("floatingFiles", 0)),
        "floating_bytes": _int(row.get("floatingBytes", 0)),
        "cell_size": _int(row.get("cellSize", 0)),
    }


def placements(map_name, min_x, min_z, max_x, max_z, scene_state_ids, detail_level):
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
              "sceneState": list(scene_state_ids), "detailLevel": int(detail_level)}
    table = _table(PLACEMENTS, **window)

    # A material list is POSITIONAL -- slot i dresses sub-mesh i -- so it is built
    # by the sub-mesh index the decoder states, never by arrival order. A sub-mesh
    # no renderer claims stays an empty slot rather than shifting the rest along.
    materials = _table(PLACEMENT_MATERIALS, **window)
    materials_by_row = {}
    if len(materials):
        placement_column = materials.values("placement")
        sub_mesh_column = materials.values("subMesh")
        path_column = materials.values("path")
        for i in range(len(materials)):
            slots = materials_by_row.setdefault(int(placement_column[i]), [])
            index = int(sub_mesh_column[i])
            while len(slots) <= index:
                slots.append("")
            slots[index] = path_column[i]

    counts = _rows(PLACEMENT_COUNTS, **window)
    count = counts[0] if counts else {}
    return {
        "table": table,
        "materials_by_row": {row: tuple(paths) for row, paths in materials_by_row.items()},
        "seed_paths": _column(SEED_PATHS, "path", **window),
        "total_renderers": _int(count.get("totalRenderers", 0)),
        "no_transform": _int(count.get("noTransform", 0)),
        "detail_filtered": _int(count.get("detailFiltered", 0)),
        "stand_in_filtered": _int(count.get("standInFiltered", 0)),
        "no_renderers": _int(count.get("noRenderers", 0)),
        "distinct_assets": _int(count.get("distinctAssets", 0)),
        "seed": str(count.get("seed", "")),
    }


def scene_anchor(map_name, min_x, min_z, max_x, max_z, scene_state_ids, detail_level, viewer):
    """Where the hook resolves one window's camera-centred state (``endfield.scene.anchor``) for a document looked
    at from ``viewer`` (a point in the source's world, or None): ``((x, y, z), from_view)``."""
    window = {"map": map_name, "minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z,
              "sceneState": list(scene_state_ids), "detailLevel": int(detail_level), "hasViewer": viewer is not None}
    if viewer is not None:
        window.update(viewerX=viewer[0], viewerY=viewer[1], viewerZ=viewer[2])
    row = _rows(SCENE_ANCHOR, **window)[0]
    return (float(row["x"]), float(row["y"]), float(row["z"])), bool(int(float(row["fromView"])))


# ── resolving a name to the rows that hold it ───────────────────────────────

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
    """The pose/emotion/animation/lipsync assets these CABs carry, each with the
    archive it was read from. Which of the four an asset IS comes from the fields it
    carries, which is the hook's read."""
    return [{"name": row["name"], "kind": row["kind"], "cab": row["cab"], "duration": row["duration"],
             "animated": bool(_int(row["animated"]))}
            for row in _rows(MORPH_ASSETS, cab=list(cabs))]


def morph_drivers(cabs):
    """One ctrl inside one channel. ``curve`` is a clip_curves.Channel rebuilt
    from the keyframes the hook handed over as raw float32 -- the same channel
    object the transform curves use, so a host samples both through one
    vectorized evaluator."""
    from ...Kernel.unity import clip_curves
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

def ui_stages():
    """Every display stage the game ships, one row each -- a table a panel draws as it is."""
    return _table(UI_STAGES)


def ui_stage(stage):
    """One display stage, resolved by the hook: one row per value it states. ``kind`` says what the
    row is -- a light or world value (``env``), a character parameter (``param``) or a seed of the
    stage's own art (``prefab``) -- and ``components`` how many of x..w it carries."""
    return _rows(UI_STAGE, stage=stage)


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


def character_models(cabs):
    """{character id: {model, tag, asset}} -- a character's model prefab is not
    derivable from its id, so its own data asset is the only source."""
    return {row["characterId"]: {"model": row["model"], "tag": row["tag"], "asset": row["asset"]}
            for row in _rows(CHARACTER_MODELS, cab=list(cabs))}
