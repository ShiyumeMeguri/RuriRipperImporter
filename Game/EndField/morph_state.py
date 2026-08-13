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
ASSETS = {}           # asset name -> skeletal_morph.MorphAsset, for whatever has been loaded
AVATARS = {}          # avatar name -> skeletal_morph.MorphAvatar (the ctrl -> bone-delta tables)
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


def load(cabs):
    """Read everything these CABs carry into ASSETS and AVATARS: the poses,
    emotions, animations and lipsync configs, and the ctrl-to-bone tables. Every
    field shape is the hook's answer off the game's own typed assets; what is
    rebuilt here is the object a host samples through. Returns how many were
    added."""
    return load_assets(cabs) + load_avatars(cabs)


def load_assets(cabs):
    """The pose/emotion/animation/lipsync assets, keyed by the game's own name --
    the identity every list here already draws and the lipsync config points
    with."""
    added = 0
    curves = {}
    for row in datasets.morph_drivers(cabs):
        curves.setdefault(row["asset"], []).append(row)
    flags = {}
    for row in datasets.morph_flags(cabs):
        flags.setdefault(row["asset"], {})[row["flag"]] = row["value"]
    references = {}
    for row in datasets.morph_references(cabs):
        entry = references.setdefault(row["asset"], {})
        if row["index"] < 0:
            entry[row["field"]] = row["target"]
        else:
            entry.setdefault(row["field"], []).append(row["target"])
    phonemes = {}
    for row in datasets.morph_lipsync(cabs):
        phonemes.setdefault(row["asset"], {}).setdefault(row["set"], []).append(
            (row["slot"], row["target"]))

    for row in datasets.morph_assets(cabs):
        name = row["name"]
        if name in ASSETS:
            continue
        asset = skeletal_morph.MorphAsset(name, name, row["kind"])
        asset.duration = row["duration"]
        asset.flags = flags.get(name, {})
        asset.references = references.get(name, {})
        asset.phoneme_sets = {set_id: [target for _slot, target in sorted(slots)]
                              for set_id, slots in phonemes.get(name, {}).items()}
        for driver in curves.get(name, ()):
            asset.channels.setdefault(driver["channel"], []).append(
                skeletal_morph.MorphDriver(driver["ctrl"], driver["channel"],
                                           value=driver["value"] if driver["has_value"] else None,
                                           curve=driver["curve"]))
        ASSETS[name] = asset
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
    assets = [asset for name, asset in ASSETS.items() if name in wanted]
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
