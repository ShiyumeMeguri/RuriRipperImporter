"""The game's own UI display scenes -- the lit little stage a character/weapon
stands on behind an interface (CharInfo, CharFormation, WeaponInfo...).

Unlike the world scenes next door, the game registers these NOWHERE: none of its
693 TableCfg containers lists them, and neither does DataConst's roster of
gameplay config assets. What it does ship is one **HGEnvironmentPhase** asset per
display stage -- sun, sky SH, exposure, colour grading -- filed next to that
interface's own prefabs under the game's own prefab root
(``PathDef.DYNAMIC_PREFAB_PATH``), with the stage's post-processing as
**VolumeProfile** assets and its character-lighting overrides as an
**HGCharacterVolume** in the same folder.

So the list below IS that set of environment assets, and nothing here is
inferred from the shape of a name: an asset is claimed because its ``m_Script``
resolves to a class named ``HGEnvironmentPhase``, read from the MonoScript the
closure itself carries. The folder is the game's own filing, and it is what
groups an environment with the volumes that belong to it.

Two stages in one folder (CharInfo ships CharInfo_* and Qianneng_*) are two
rows, and each takes the volumes whose own ``m_Name`` starts with its stem --
the only link the game leaves, since the pairing lives in UI code rather than in
any asset. Every volume in the folder is reported either way, so a mispaired one
is visible rather than silently dropped.
"""

from __future__ import annotations

from ...RuriRipperPyBridge.unity import unity_yaml
from . import datasets

# Discovery holds no bpy data, and re-running it costs a bridge import -- keep it
# across a script reload like the other scene state does.
HOLDS_PROCESS_STATE = True

# AssetRipper writes a MonoScript as a .cs under its own Scripts tree; the file's
# stem is the class. That is how a guid becomes a type here.
_SCRIPT_SUFFIX = ".cs"

SCENES = []      # list[dict] -- one row per HGEnvironmentPhase found
DOCUMENTS = {}   # container path -> parsed UnityDocument.data of that asset
STATUS = "Refresh to read the game's UI display scenes."


def reset():
    global SCENES, DOCUMENTS, STATUS
    SCENES = []
    DOCUMENTS = {}
    STATUS = "Refresh to read the game's UI display scenes."


def _class_of_script(exported_path):
    """The class a MonoScript guid stands for: the stem of the .cs AssetRipper
    wrote for it. None for a guid that is not a script."""
    if not exported_path:
        return None
    tail = str(exported_path).replace("\\", "/").rsplit("/", 1)[-1]
    if not tail.endswith(_SCRIPT_SUFFIX):
        return None
    return tail[: -len(_SCRIPT_SUFFIX)]


def _mono_behaviour_doc(unity_file):
    """(m_Name, script guid, data) for a MonoBehaviour asset, or None."""
    doc = unity_file.first("MonoBehaviour")
    if doc is None:
        return None
    script = doc.data.get("m_Script")
    guid = script.get("guid") if isinstance(script, dict) else None
    if not guid:
        return None
    return str(doc.data.get("m_Name") or ""), str(guid).lower(), doc.data


def discover(bridge):
    """Read the game's UI display stages out of its own assets.

    Where those assets live, which prefab IS the stage, which classes a stage is
    made of and how it names the two halves of one are the game's own answers
    (``endfield.ui.candidates`` / ``endfield.ui.schema``). One bridge import for
    the whole candidate set (MonoBehaviour + MonoScript only -- no meshes, no
    textures), then every asset is classified by the class its m_Script names."""
    global SCENES, DOCUMENTS, STATUS
    published = datasets.ui_candidates()
    configs = [row for row in published if row["kind"] == "config"]
    if not configs:
        SCENES, DOCUMENTS = [], {}
        STATUS = "The game ships no display-stage config assets in this cabmap."
        return SCENES

    schema = datasets.ui_schema()
    environment_class = (schema.get("environmentClass") or [""])[0]
    volume_profile_class = (schema.get("volumeProfileClass") or [""])[0]
    character_volume_class = (schema.get("characterVolumeClass") or [""])[0]
    stem_suffixes = schema.get("stemSuffix") or []

    cab_of_path = {row["path"]: row["cab"] for row in configs}
    folder_of_path = {row["path"]: row["folder"] for row in configs}
    group_of_folder = {row["folder"]: row["group"] for row in published}
    candidates = sorted(cab_of_path)
    stage_prefabs = {}
    for row in published:
        if row["kind"] == "stage":
            stage_prefabs.setdefault(row["folder"], []).append(row["path"])
    for paths in stage_prefabs.values():
        paths.sort()

    cabs = sorted(set(cab_of_path.values()))
    assets, _roots, seed_roots, _clips, _scene_roots = bridge.import_cabs(
        cabs, [MONO_BEHAVIOUR_CLASS_ID, MONO_SCRIPT_CLASS_ID])
    exported = dict(bridge.asset_paths_by_guid)

    # guid -> class, for the MonoScripts the closure brought along.
    class_of_guid = {}
    for guid, path in exported.items():
        class_name = _class_of_script(path)
        if class_name:
            class_of_guid[guid] = class_name

    # Which container path an imported asset came from. seed_roots answers it
    # directly when the bridge could attribute a seed to its own asset; the
    # remainder join on the game's own file name, which for these config assets
    # IS the asset's m_Name (charinfo/qianneng_env.asset <-> Qianneng_Env) --
    # and only ever within the 17 candidates, never across the library.
    guid_of_cab = {str(k).lower(): str(v).lower() for k, v in dict(seed_roots).items()}
    path_of_guid = {}
    for path, cab in cab_of_path.items():
        guid = guid_of_cab.get(cab.lower())
        if guid:
            path_of_guid[guid] = path
    path_of_stem = {}
    for path in candidates:
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        path_of_stem.setdefault(stem.lower(), []).append(path)

    # Every MonoBehaviour in the closure, by guid -- a VolumeProfile's own
    # components have no container path of their own, so they are reachable
    # only through the profile's `components` list, which is the game's own
    # link and the only correct way to say which overrides a stage applies.
    behaviours = {}
    for guid, blob in assets.items():
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not text.lstrip().startswith("%YAML"):
            continue
        unity_file = unity_yaml.UnityFile(None, unity_yaml.parse_text(text))
        parsed = _mono_behaviour_doc(unity_file)
        if parsed is None:
            continue
        name, script_guid, data = parsed
        behaviours[guid] = {"name": name, "guid": guid, "data": data,
                            "class": class_of_guid.get(script_guid, "")}

    environments, volumes = [], []
    documents = {}
    for guid, entry in behaviours.items():
        path = path_of_guid.get(guid)
        if path is None:
            matches = path_of_stem.get(entry["name"].lower(), ())
            path = matches[0] if len(matches) == 1 else None
        if path is None:
            continue
        documents[path] = entry["data"]
        record = dict(entry, path=path, folder=folder_of_path.get(path, ""))
        if entry["class"] == environment_class:
            environments.append(record)
        elif entry["class"] == volume_profile_class:
            record["components"] = _components(entry["data"], behaviours)
            volumes.append(record)

    rows = []
    for env in sorted(environments, key=lambda r: (r["folder"], r["name"])):
        folder = env["folder"]
        group = group_of_folder.get(folder, "")
        stem = _stem(env["name"], stem_suffixes)
        siblings = [v for v in volumes if v["folder"] == folder]
        own = [v for v in siblings
               if _stem(v["name"], stem_suffixes).lower().startswith(stem.lower())] or siblings
        rows.append({
            "id": "{0}/{1}".format(group, env["name"]),
            "label": env["name"],
            "group": group,
            "env": env,
            "volumes": own,
            "char_volumes": [c for v in own for c in v["components"]
                             if c["class"] == character_volume_class],
            "stage_prefabs": stage_prefabs.get(folder, []),
        })

    SCENES, DOCUMENTS = rows, documents
    STATUS = "{0} display stage(s) in {1} folder(s).".format(
        len(rows), len({row["group"] for row in rows}))
    return SCENES


MONO_BEHAVIOUR_CLASS_ID = 114
MONO_SCRIPT_CLASS_ID = 115


def _components(profile_data, behaviours):
    """A VolumeProfile's override stack, in the order the profile lists it."""
    listed = profile_data.get("components")
    if not isinstance(listed, list):
        return []
    resolved = []
    for ref in listed:
        guid = ref.get("guid") if isinstance(ref, dict) else None
        entry = behaviours.get(str(guid).lower()) if guid else None
        if entry is not None:
            resolved.append(entry)
    return resolved


def _stem(name, suffixes):
    """``CharInfo_Env`` -> ``CharInfo`` -- the game's own naming of the two
    halves of one stage, by the suffixes the hook publishes. Only ever used to
    pair inside a single folder."""
    for suffix in suffixes:
        if name.lower().endswith(suffix.lower()):
            return name[: -len(suffix)]
    return name


def row_by_id(scene_id):
    for row in SCENES:
        if row["id"] == scene_id:
            return row
    return None


def document(container_path):
    return DOCUMENTS.get(container_path)


def overridden(data, field):
    """A VolumeParameter's value, but only when the volume actually overrides it
    -- an unticked row in the game's inspector contributes nothing, and reading
    its m_Value anyway is how a stale default gets mistaken for a setting."""
    entry = data.get(field)
    if not isinstance(entry, dict):
        return None
    if not _truthy(entry.get("overrideState")):
        return None
    return entry.get("m_Value")


def _truthy(value):
    return bool(value) and value not in (0, "0", False)
