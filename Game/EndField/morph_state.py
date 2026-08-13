"""The facial-morph browser's model, with no widgets in it: which SkeletalMorph
assets the loaded cabmap has, which of them belong to one character, and the
parsed library once they are actually loaded.

Same split as cabmap_state/scene_state -- discovery is cheap and runs off the
row table alone (container paths, no VFS decrypt, no AssetRipper export);
parsing only happens for the CABs a host actually asks for. Nothing here
imports bpy: a host materializes whatever subset its UI shows.
"""

from __future__ import annotations

from . import datasets, skeletal_morph

# Holds the parsed morph library (one export + parse per asset), so a host's
# script reload skips this module instead of paying for it again.
HOLDS_PROCESS_STATE = True

LIBRARY = {}          # kind -> [entry dict]; entry = {cab, name, path, kind}
CHARACTER_TOKEN = ""  # the name fragment that marks a character's own assets ("pelica")
ASSETS = {}           # guid -> skeletal_morph.MorphAsset, for whatever has been loaded
ASSETS_BY_NAME = {}   # m_Name -> MorphAsset (the identity a host's lists show)
AVATARS = {}          # guid -> skeletal_morph.MorphAvatar (the ctrl -> bone-delta tables)
SCOPED_KINDS = set()  # kinds the last load narrowed to CHARACTER_TOKEN, see plan_load

# A kind bigger than this is narrowed to the character rather than loaded whole:
# the shared emotion/pose library is a few dozen tiny assets, but the animation
# folder is 600+ assets of full curve data, 95% of it other characters'. This is
# a SIZE policy, not a list of folder names -- whatever the game reorganises
# into, the rule still applies, and plan_load reports exactly what it left out.
SCOPE_BUDGET = 200


def reset():
    global CHARACTER_TOKEN
    LIBRARY.clear()
    ASSETS.clear()
    ASSETS_BY_NAME.clear()
    AVATARS.clear()
    SCOPED_KINDS.clear()
    CHARACTER_TOKEN = ""


def discover(character_token=""):
    """Bucket every SkeletalMorph asset of the loaded cabmap by the kind the GAME
    files it under. Where that family lives and how a folder names a kind are the
    hook's answer (``endfield.morph.library``); the bucketing is over the whole
    map at once, so this is one crossing rather than a scan of 260k rows.

    ``character_token`` is just remembered here; it filters at read time
    (entries_for) so a host can re-scope without re-scanning."""
    global CHARACTER_TOKEN
    LIBRARY.clear()
    CHARACTER_TOKEN = (character_token or "").strip().lower()
    for row in datasets.morph_library():
        LIBRARY.setdefault(row["kind"], []).append({
            "cab": row["cab"],
            "name": row["name"],
            "path": row["path"],
            "kind": row["kind"],
        })
    for entries in LIBRARY.values():
        entries.sort(key=lambda entry: entry["name"].lower())
    return LIBRARY


def kinds():
    return sorted(LIBRARY)


def entries_for(kind, character_only=False):
    """The discovered entries of one kind. ``character_only`` narrows to the
    assets whose name carries CHARACTER_TOKEN -- the per-character morph
    animations, as opposed to the shared emotion/pose library every character
    draws from."""
    entries = LIBRARY.get(kind, ())
    if not character_only or not CHARACTER_TOKEN:
        return list(entries)
    return [entry for entry in entries if CHARACTER_TOKEN in entry["name"].lower()]


def counts(character_only=False):
    return {kind: len(entries_for(kind, character_only)) for kind in kinds()}


def plan_load():
    """What a host should actually resolve: every kind, narrowed to the
    character wherever the kind is too big to load whole (SCOPE_BUDGET).

    Records the narrowed kinds in SCOPED_KINDS -- that set IS the single source
    of truth for "this kind is per-character", so a host's list never has to
    re-guess it -- and returns (entries, dropped) so the caller can SAY how much
    it left out instead of silently truncating."""
    SCOPED_KINDS.clear()
    entries, dropped = [], 0
    for kind in kinds():
        whole = entries_for(kind)
        if len(whole) <= SCOPE_BUDGET or not CHARACTER_TOKEN:
            entries.extend(whole)
            continue
        scoped = entries_for(kind, character_only=True)
        SCOPED_KINDS.add(kind)
        entries.extend(scoped)
        dropped += len(whole) - len(scoped)
    return entries, dropped


def load_from_db(db, guids=None):
    """Parse every SkeletalMorph pose/animation asset in an already-resolved
    closure into ASSETS/ASSETS_BY_NAME. Sniffs the raw text first (one substring
    test) so a closure full of meshes and textures costs one failed sniff each
    rather than a full YAML parse each -- the same cheap-sniff shape the clip
    discovery uses. Returns the number of assets newly parsed."""
    added = 0
    for guid in (guids if guids is not None else list(db.all_guids())):
        guid = guid.lower()
        if guid in ASSETS:
            continue
        text = db.raw_text(guid) if hasattr(db, "raw_text") else None
        if not text:
            continue
        if skeletal_morph.CTRL_KEY not in text and skeletal_morph.LIPSYNC_KEY not in text:
            continue
        unity_file = db.load_guid(guid)
        document = unity_file.first("MonoBehaviour") if unity_file is not None else None
        asset = skeletal_morph.parse_document(document, guid=guid)
        if asset is None:
            continue
        ASSETS[guid] = asset
        if asset.name:
            ASSETS_BY_NAME[asset.name] = asset
        added += 1
    return added


def load_avatars(cabs):
    """The ctrl-to-bone tables these CABs carry, read by the hook off the game's
    own typed assets (``endfield.morph.avatars``/``.ctrls``/``.bones``/
    ``.shaderparams``) rather than re-parsed out of an export. Keyed by name --
    the identity every list here already uses. Returns how many were added."""
    added = 0
    for row in datasets.morph_avatars(cabs):
        name = row["name"]
        if name in AVATARS:
            continue
        avatar = skeletal_morph.MorphAvatar(name, name, int(row["tag_id"]))
        AVATARS[name] = avatar
        added += 1
    for row in datasets.morph_ctrls(cabs):
        avatar = AVATARS.get(row["avatar"])
        if avatar is not None:
            avatar.mappings.setdefault(row["ctrl"], [])
    for row in datasets.morph_bones(cabs):
        avatar = AVATARS.get(row["avatar"])
        if avatar is None:
            continue
        delta = skeletal_morph.MorphBoneDelta(
            row["bone_id"], row["bone"], row["position"], row["rotation"], row["scale"])
        if row["ctrl"]:
            avatar.mappings.setdefault(row["ctrl"], []).append(delta)
        else:
            avatar.base_pose[delta.bone_id] = delta
            if delta.bone_name and delta.bone_name not in avatar.bone_names:
                avatar.bone_names.append(delta.bone_name)
    for row in datasets.morph_shader_params(cabs):
        avatar = AVATARS.get(row["avatar"])
        if avatar is not None:
            avatar.shader_params[row["ctrl"]] = {"param": row["param"], "default": row["default"]}
    return added


def avatars_for_tag(tag_id):
    """Every avatar table the game itself joins to one character.

    A character's data asset states its SkeletalMorphComponentData tagId, and
    each avatar table states the same tagId back -- so this is the game's own
    identity join, not a guess about names. It is exact: an unmatched tag
    returns nothing rather than some other character's face."""
    if not tag_id:
        return []
    matches = [avatar for avatar in AVATARS.values()
               if getattr(avatar, "tag_id", 0) == tag_id]
    return sorted(matches, key=lambda avatar: avatar.name)


def avatars_for(token=""):
    """EVERY avatar table belonging to one character, not one of them: the game
    splits a face across several (``data_facemorph_avatar_pelica`` for the face
    proper, ``data_earmorph_avatar_pelica`` for her ears), and they are additive
    parts of the same rig -- the ear ctrls appear in the same animations as the
    brow ones. Picking one would silently drop the other half of her face.

    Falls back to every loaded avatar only when nothing carries the token AND
    exactly one is loaded; an unmatched token with several loaded returns
    nothing rather than binding some other character's face onto this rig."""
    token = (token or CHARACTER_TOKEN or "").strip().lower()
    if token:
        matches = [avatar for avatar in AVATARS.values() if token in avatar.name.lower()]
        if matches:
            return sorted(matches, key=lambda avatar: avatar.name)
    loaded = list(AVATARS.values())
    return loaded if len(loaded) == 1 else []


def loaded_of_kind(kind, character_only=False):
    """Parsed assets of one kind, in the discovered order, so a host's list
    keeps the same ordering before and after loading."""
    wanted = {entry["name"] for entry in entries_for(kind, character_only)}
    assets = [asset for name, asset in ASSETS_BY_NAME.items() if name in wanted]
    assets.sort(key=lambda asset: asset.name.lower())
    return assets


def all_ctrl_names():
    """Every ctrl the loaded library mentions -- the vocabulary a rig is
    matched against."""
    names = set()
    for asset in ASSETS.values():
        names.update(asset.ctrl_names())
    return sorted(names)


def cabs_for(entries):
    """The CAB list a host seeds import_cabs with for these entries, de-duped
    and in discovery order."""
    seen = set()
    cabs = []
    for entry in entries:
        cab = entry["cab"]
        if cab not in seen:
            seen.add(cab)
            cabs.append(cab)
    return cabs
