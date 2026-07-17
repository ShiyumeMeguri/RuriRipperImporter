"""Top-level import orchestration: prefab (full model) and standalone mesh."""

from __future__ import annotations

import os
import re
import time

import numpy as np

try:
    from . import (armature_builder, asset_db, clip_curves, coordinate, animation_builder,
                   material_builder, mesh_builder, mesh_decoder)
except ImportError:
    import armature_builder
    import asset_db
    import clip_curves
    import coordinate
    import animation_builder
    import material_builder
    import mesh_builder
    import mesh_decoder

import bpy

DEFAULT_OPTIONS = {
    "lod0_only": True,
    "import_materials": True,
    "import_textures": True,
    "import_skeleton": True,
    "import_animations": True,
    "import_normals": True,
    "import_colors": True,
    "import_blendshapes": True,
    "connect_alpha": True,
    "flip_v": False,
    "import_shadow_proxies": False,
}

# Unity ShadowCastingMode.ShadowsOnly — these renderers are invisible to the
# camera (shadow proxies) and are skipped unless explicitly requested.
_SHADOWS_ONLY = 3


class ImportReport:
    def __init__(self):
        self.armature = None
        self.mesh_objects = []
        self.materials = 0
        self.textures = 0
        self.actions = 0
        self.bones = 0
        self.skipped_lod = 0
        self.skipped_shadow = 0
        self.warnings = []
        self.seconds = 0.0
        self.maps = None        # hierarchy/bone maps (for external clip application)
        self.db = None          # AssetDatabase (for further resolution)
        self.path_to_meshobjects = None
        self.available_clips = []  # bridge mode only: discover_clip_refs_from_db() results

    def summary(self):
        return (f"armature_bones={self.bones} meshes={len(self.mesh_objects)} "
                f"materials={self.materials} textures={self.textures} "
                f"actions={self.actions} lod_skipped={self.skipped_lod} "
                f"shadow_skipped={self.skipped_shadow} time={self.seconds:.2f}s")


def _resolve_options(options):
    merged = dict(DEFAULT_OPTIONS)
    if options:
        merged.update(options)
    return merged


def _lod_discard_set(prefab):
    """Return the set of renderer fileIDs that belong to LOD1+ (to discard)."""
    keep = set()
    discard = set()
    for group in prefab.all("LODGroup"):
        lods = group.data.get("m_LODs") or []
        for level, lod in enumerate(lods):
            for ref in (lod.get("renderers") or []):
                renderer = ref.get("renderer") if isinstance(ref, dict) else None
                fid = renderer.get("fileID") if isinstance(renderer, dict) else None
                if fid is None:
                    continue
                if level == 0:
                    keep.add(fid)
                else:
                    discard.add(fid)
    return discard - keep


def _go_name(prefab, go_id):
    go = prefab.get(go_id)
    return str(go.data.get("m_Name", "Object")) if go else "Object"


def import_prefab(context, prefab_path, options=None):
    options = _resolve_options(options)
    assets_dir = asset_db.find_assets_dir(prefab_path)
    db = asset_db.AssetDatabase(os.path.dirname(prefab_path), assets_dir)
    prefab = db.load_file(prefab_path)
    arm_name = os.path.splitext(os.path.basename(prefab_path))[0]
    clip_files = _gather_clip_files_disk(db, prefab, prefab_path, assets_dir)
    report = _import_prefab_core(context, db, prefab, arm_name, clip_files, options)
    fbx_hint = _fbx_instance_hint(prefab)
    if fbx_hint:
        report.warnings.insert(0, fbx_hint)
    return report


def _fbx_instance_hint(prefab):
    """An actionable diagnosis for the classic dead-end: a prefab that only
    REFERENCES a binary .fbx (a thin PrefabInstance wrapper) carries no YAML
    geometry at all, so the import comes out empty-looking with no explanation.
    Detect the shape and say exactly what to do about it."""
    has_instance = prefab.first("PrefabInstance") is not None
    has_geometry = (prefab.first("SkinnedMeshRenderer") is not None
                    or prefab.first("MeshFilter") is not None)
    if has_instance and not has_geometry:
        return ("This prefab only references a binary .fbx (a thin PrefabInstance "
                "wrapper) -- it carries no YAML geometry to import. Run Unity's "
                "'Ruri > Dump Model to YAML (for Blender)' (unity_editor/"
                "RuriYamlDumper.cs) on the model and import the generated "
                "<model>_yaml/<model>.prefab instead.")
    return None


def import_prefab_from_db(context, db, prefab_file, options=None, name=None):
    """Bridge-mode sibling of import_prefab: db/prefab_file are already resolved
    from an in-memory closure (pythonnet bridge) instead of a disk path -- same
    build body as import_prefab (via _import_prefab_core), only the front
    matter differs. Unlike the disk path, animation clips are NOT eagerly
    built here: a character's dependency closure can hold dozens of clips at
    ~100MB each, and building actions for all of them synchronously is what
    used to hang Blender on import. Clips are only DISCOVERED (cheap -- see
    discover_clip_refs_from_db) and reported on report.available_clips; the
    caller builds actions later, only for whichever clips the user actually
    picks in the animation browser, via build_selected_animations."""
    options = _resolve_options(options)
    arm_name = name or _prefab_display_name(prefab_file)
    report = _import_prefab_core(context, db, prefab_file, arm_name, [], options)
    if options["import_animations"]:
        report.available_clips = discover_clip_refs_from_db(db, prefab_file)
    return report


def _prefab_display_name(prefab):
    root = prefab.first("GameObject")
    if root is not None:
        name = root.data.get("m_Name")
        if name:
            return str(name)
    return "UnityModel"


def _load_clip_fast(clip_path):
    """Disk clip fast path: regex+numpy extraction straight off the raw text
    (clip_curves.ClipCurves.from_yaml_text -- validated bitwise-identical to
    the full parser on real 82MB clips at ~3x the speed, more against a cold
    cache). Returns None when the text isn't a clip or has a structural
    surprise -- the caller falls back to the full YAML parser."""
    try:
        with open(clip_path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
        return clip_curves.ClipCurves.from_yaml_text(text)
    except (OSError, ValueError):
        return None


def _gather_clip_files_disk(db, prefab, prefab_path, assets_dir):
    """Disk-mode clip gathering: resolve _gather_clip_paths's paths into
    ClipCurves (fast path) or loaded UnityFiles (fallback)."""
    clip_files = []
    for clip_path in _gather_clip_paths(db, prefab, prefab_path, assets_dir):
        clip = _load_clip_fast(clip_path)
        if clip is not None:
            clip_files.append(clip)
            continue
        try:
            clip_files.append(db.load_file(clip_path))
        except OSError:
            continue
    return clip_files


_CLASS_HEADER_RE = re.compile(r"^---\s+!u!\d+\s+&-?\d+(?:\s+stripped)?\s*$", re.MULTILINE)
_NAME_FIELD_RE = re.compile(r"^\s*m_Name:\s*(.*?)\s*$", re.MULTILINE)
_PEEK_CHARS = 4096  # Unity always writes the class name + m_Name within the
                     # first few dozen lines of an object, however large the
                     # trailing curve/blob data further down the document is.


def _peek_class_and_name(text):
    """Cheap classification without a full unity_yaml.parse_text: read the
    class name off the line right after the document header, and m_Name from
    a bounded prefix of the body. A dense AnimationClip can run to 100+MB, and
    most guids in a closure aren't clips at all -- this keeps closure-wide
    discovery O(clip count) instead of O(total closure bytes)."""
    prefix = text[:_PEEK_CHARS]
    header = _CLASS_HEADER_RE.search(prefix)
    if header is None:
        return None, None
    rest = prefix[header.end():].lstrip("\r\n")
    line_end = rest.find("\n")
    class_line = rest if line_end == -1 else rest[:line_end]
    class_name = class_line.split(":", 1)[0].strip() or None
    name_match = _NAME_FIELD_RE.search(prefix)
    name = name_match.group(1).strip() or None if name_match else None
    return class_name, name


def discover_clip_refs_from_db(db, prefab):
    """Bridge-mode animation clip DISCOVERY: same guid scope as the old eager
    gather (controller-referenced clips plus every AnimationClip document
    present in the closure) but returns lightweight metadata (guid/name/
    approximate size) via _peek_class_and_name instead of a full parse -- so
    browsing what's available doesn't pay to decode every clip up front. That
    cost is deferred to build_selected_animations, and only for whichever
    clips the user actually checks in the animation browser."""
    refs = []
    seen_ids = set()

    def _consider(guid):
        if guid in seen_ids:
            return
        text = db.raw_text(guid)
        if text is None:
            return
        class_name, name = _peek_class_and_name(text)
        if class_name != "AnimationClip":
            return
        seen_ids.add(guid)
        refs.append({"guid": guid, "name": name or guid, "size_bytes": len(text)})

    animator = prefab.first("Animator")
    controller_ref = animator.data.get("m_Controller") if animator is not None else None
    if isinstance(controller_ref, dict) and controller_ref.get("guid"):
        controller_file = db.load_guid(controller_ref["guid"])
        if controller_file is not None:
            guids = set()
            for doc in controller_file.documents:
                _collect_guids(doc.data, guids)
            for guid in guids:
                _consider(guid)

    for guid in db.all_guids():
        _consider(guid)

    refs.sort(key=lambda r: r["name"].lower())
    return refs


def build_selected_animations(db, arm_obj, maps, path_to_meshobjects, guids, options):
    """Build Blender actions for exactly the given clip guids -- the checked
    subset from the animation browser. This is the only place that now pays
    the full parse + keyframe-insertion cost per clip; it's deferred until the
    user explicitly picks a clip rather than paying it for every clip in a
    character's closure up front.

    Before building, every clip gets repair_hashed_clip_paths against the
    target armature: clips exported without their rig in scope carry
    "path_0x<CRC32>_" placeholder paths, and the armature's own bone paths
    are the hash preimages -- so a standalone-imported clip binds to the
    user's selected skeleton exactly when the hashes match. Returns
    (built, warnings)."""
    built = 0
    first = None
    warnings = []
    has_action = arm_obj.animation_data is not None and arm_obj.animation_data.action is not None
    path_to_bone = maps.get("path_to_bone") or {}
    for guid in guids:
        # Bridge fast path first: the exporter already handed this clip's
        # curves across as raw float32 arrays (see clip_curves.ClipCurves.
        # from_blob) -- no YAML parse at all. Falls back to the document.
        clip = db.clip_curves(guid) if hasattr(db, "clip_curves") else None
        if clip is None:
            clip_file = db.load_guid(guid)
            if clip_file is None:
                continue
            clip_doc = clip_file.first("AnimationClip")
            if clip_doc is None:
                continue
            clip = clip_curves.ClipCurves.from_document(clip_doc.data)
        clip_name = clip.name or guid
        repaired, unmatched = repair_hashed_clip_paths(clip, path_to_bone)
        if unmatched:
            warnings.append(f"{clip_name}: {unmatched} hashed curve "
                            f"path(s) matched no bone of '{arm_obj.name}' (skipped)")
        is_humanoid = clip_is_humanoid(clip)
        if maps.get("retargeter") is None and is_humanoid:
            warnings.append(f"{clip_name}: humanoid (muscle) clip but "
                            f"no Avatar in scope -- body motion dropped, only generic curves "
                            f"imported")
        action, slot, n_frames = animation_builder.build_action(
            clip, arm_obj, maps, path_to_meshobjects, options)

        # EndField ships humanoid clips whose limb FK is an auto-generated
        # approximation; the real hand/foot poses live in the clip's own
        # animated IK target bones and the game recomputes the limbs at
        # runtime. Rigs exposing that convention get the same correction here
        # -- see endfield_ik.py's module doc for the ground truth. Generic
        # clips ship real FK and are left alone.
        retargeter = maps.get("retargeter")
        if (options.get("endfield_ik", True) and is_humanoid and retargeter is not None):
            try:
                from . import endfield_ik
            except ImportError:
                import endfield_ik
            if endfield_ik.detect_rig(arm_obj):
                try:
                    endfield_ik.apply_to_action(arm_obj, action, retargeter.bone_targets(),
                                                0, max(0, n_frames - 1))
                except Exception as exc:
                    warnings.append(f"{clip_name}: IK correction "
                                    f"failed ({type(exc).__name__}: {exc}) -- FK kept")

        built += 1
        if first is None:
            first = (action, slot)
    if first is not None and not has_action:
        _assign_first_action(arm_obj, first[0], first[1])
    return built, warnings


def _import_prefab_core(context, db, prefab, arm_name, clip_files, options):
    """Shared build body for import_prefab / import_prefab_from_db: armature,
    LOD0 skinned + static meshes, materials, and animation actions from an
    already-resolved db + prefab UnityFile + pre-gathered clip UnityFiles."""
    report = ImportReport()
    start = time.time()

    # Armature from the transform hierarchy.
    arm_obj = None
    maps = None
    if options["import_skeleton"]:
        arm_obj, maps = armature_builder.build_armature(context, prefab, arm_name)
        report.armature = arm_obj
        report.bones = len(arm_obj.data.bones)
    else:
        # Still need the hierarchy maps for naming/skinning resolution.
        try:
            from . import hierarchy
        except ImportError:
            import hierarchy
        nodes, roots = hierarchy.build_hierarchy(prefab)
        import numpy as _np
        maps = {"nodes": nodes, "roots": roots,
                "file_id_to_bone": {}, "path_to_bone": {},
                "file_id_to_world": {fid: _np.array(n.world, dtype=_np.float64)
                                     for fid, n in nodes.items()}}

    nodes = maps["nodes"]
    go_to_node = {n.go_id: n for n in nodes.values()}

    mat_builder = material_builder.MaterialBuilder(db, options) if options["import_materials"] else None
    discard = _lod_discard_set(prefab) if options["lod0_only"] else set()

    path_to_meshobjects = {}

    keep_shadow = options.get("import_shadow_proxies", False)
    renderers = prefab.all("SkinnedMeshRenderer")
    for smr in renderers:
        if smr.file_id in discard:
            report.skipped_lod += 1
            continue
        if not keep_shadow and smr.data.get("m_CastShadows") == _SHADOWS_ONLY:
            report.skipped_shadow += 1
            continue
        obj = _import_skinned(context, db, prefab, smr, arm_obj, maps,
                              mat_builder, options, report, go_to_node)
        if obj is not None:
            report.mesh_objects.append(obj)
            node = go_to_node.get((smr.data.get("m_GameObject") or {}).get("fileID"))
            if node:
                path_to_meshobjects.setdefault(node.path, []).append(obj)

    # Static meshes (MeshRenderer + MeshFilter).
    for mr in prefab.all("MeshRenderer"):
        if mr.file_id in discard:
            report.skipped_lod += 1
            continue
        obj = _import_static(context, db, prefab, mr, maps, mat_builder,
                             options, report, go_to_node)
        if obj is not None:
            report.mesh_objects.append(obj)

    if mat_builder is not None:
        report.materials = len(mat_builder._cache)
        report.textures = len(mat_builder._image_cache)

    # Animations: every gathered clip (source differs disk vs. bridge mode) as actions.
    if options["import_animations"] and arm_obj is not None:
        # The Animator's own m_Avatar reference first (semantically THE avatar),
        # but fall back to scanning the closure: a UI-variant prefab's Animator
        # can point at a stub/generic Avatar (empty m_TOS, no muscle referential)
        # while the rig's REAL humanoid Avatar sits right next to it in the same
        # closure -- confirmed against the real game (pelica uimodel).
        retargeter = _load_retargeter(db, prefab, maps.get("path_to_bone"))
        if retargeter is None:
            retargeter = find_retargeter_in_db(db, maps.get("path_to_bone"))
        if retargeter is not None:
            maps["retargeter"] = retargeter
            # The referential travels with the skeleton -- see the helper's doc.
            _stamp_avatar_on_armature(arm_obj, db, retargeter)
        actions = []
        for clip_file in clip_files:
            if isinstance(clip_file, clip_curves.ClipCurves):
                clip = clip_file
                humanoid_probe = clip
                clip_name = clip.name
            else:
                clip_doc = clip_file.first("AnimationClip")
                if clip_doc is None:
                    continue
                clip = clip_doc
                humanoid_probe = clip_doc.data
                clip_name = clip_doc.data.get("m_Name", "clip")
            if retargeter is None and clip_is_humanoid(humanoid_probe):
                # Without the Avatar's muscle referential a humanoid clip's body
                # motion is mathematically unrecoverable -- the action comes out
                # empty-looking. Say so loudly instead of importing silence: the
                # fix is on the SOURCE side (the dump/prefab must carry the
                # Animator + Avatar; e.g. an FBX imported with avatarSetup=
                # CopyFromOther exposes NO Animator on its prefab, so RuriYaml-
                # Dumper finds no Avatar to extract -- reimport the FBX with
                # CreateFromThisModel, or dump the model that owns the Avatar).
                report.warnings.append(
                    f"{clip_name}: humanoid (muscle) clip but no "
                    f"Avatar in the prefab/closure -- body motion dropped. Re-dump with the "
                    f"Animator+Avatar included.")
            action, slot, _frames = animation_builder.build_action(
                clip, arm_obj, maps, path_to_meshobjects, options)
            actions.append((action, slot))
        report.actions = len(actions)
        if actions:
            first_action, first_slot = actions[0]
            _assign_first_action(arm_obj, first_action, first_slot)

    report.maps = maps
    report.db = db
    report.path_to_meshobjects = path_to_meshobjects
    report.seconds = time.time() - start
    return report


def _collect_guids(obj, out):
    """Recursively collect every guid referenced anywhere in a parsed structure."""
    if isinstance(obj, dict):
        guid = obj.get("guid")
        if isinstance(guid, str) and len(guid) == 32:
            out.add(guid)
        for value in obj.values():
            _collect_guids(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _collect_guids(value, out)


def clips_from_controller(db, controller_file):
    """Resolve every AnimationClip referenced (at any depth) by a controller.

    Returns de-duplicated absolute .anim paths.  Animator controllers reference
    their clips through ``m_Motion`` on states and through blend-tree children;
    collecting every guid and keeping those that resolve to a clip file captures
    them all without guessing at the structure.
    """
    paths = []
    seen = set()
    guids = set()
    for doc in controller_file.documents:
        _collect_guids(doc.data, guids)
    for guid in guids:
        path = db.resolve_guid(guid)
        if not path:
            continue
        ap = os.path.abspath(path)
        key = ap.lower()
        if key not in seen and key.endswith(".anim") and os.path.isfile(ap):
            seen.add(key)
            paths.append(ap)
    return paths


def _load_retargeter(db, prefab, path_to_bone=None):
    """Build a humanoid muscle retargeter from the prefab's Animator avatar.

    Humanoid clips carry the body's motion as muscle floats, not transform
    curves, so the human bones need the avatar's Muscle Referential to play.
    Returns None for non-humanoid rigs or when the avatar can't be resolved.
    path_to_bone (the armature's own Unity paths) feeds the CRC32 fallback
    TOS -- see _fallback_tos_from_paths."""
    animator = prefab.first("Animator")
    if animator is None:
        return None
    avatar_ref = animator.data.get("m_Avatar")
    if not (isinstance(avatar_ref, dict) and avatar_ref.get("guid")):
        return None
    return _retargeter_from_avatar_file(db.load_guid(avatar_ref["guid"]), path_to_bone)


def find_retargeter_in_db(db, path_to_bone=None):
    """Standalone-clip sibling of _load_retargeter: there is no prefab/Animator
    to follow an m_Avatar reference from, but the standalone flow co-seeds the
    clip's associated rig-FBX CAB into the same closure (see
    RipperBridge.find_associated_avatar_cab) precisely so the Avatar asset IS
    in the exported db -- find it by class peek and build the retargeter from
    it directly. First Avatar document wins (a co-seeded closure carries
    exactly the one rig's avatar). Returns None when the closure has no
    Avatar (the standalone import then still builds whatever generic
    transform curves the clip carries, and build_selected_animations warns
    when the clip is actually humanoid). path_to_bone: the TARGET armature's
    Unity paths, required for stripped avatars -- see _fallback_tos_from_paths."""
    if not hasattr(db, "all_guids"):
        # Disk-mode AssetDatabase has no guid-keyed closure to scan; the
        # Animator's own m_Avatar reference (_load_retargeter) is the only
        # avatar source there.
        return None
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if text is None:
            continue
        class_name, _name = _peek_class_and_name(text)
        if class_name != "Avatar":
            continue
        retargeter = _retargeter_from_avatar_file(db.load_guid(guid), path_to_bone)
        if retargeter is not None:
            return retargeter
    return None


def _fallback_tos_from_paths(path_to_bone):
    """{CRC32 hash: transform path} built from the armature's OWN bone paths --
    the replacement for a stripped avatar's empty m_TOS. The avatar skeleton's
    m_ID entries are CRC32 of each node's transform path (the same hash space
    as animation curve-path hashes, verified empirically against the real
    game), so hashing the target skeleton's paths reproduces exactly the
    lookups an intact m_TOS would satisfy: as long as there IS a skeleton, the
    hash mapping is recoverable from it."""
    if not path_to_bone:
        return None
    import zlib
    return {zlib.crc32(p.encode("utf-8")) & 0xFFFFFFFF: p for p in path_to_bone}


def _retargeter_from_avatar_file(avatar_file, path_to_bone=None):
    if avatar_file is None:
        return None
    try:
        from . import humanoid_retarget
    except ImportError:
        import humanoid_retarget
    try:
        retargeter = humanoid_retarget.HumanoidRetargeter(
            avatar_file, fallback_tos=_fallback_tos_from_paths(path_to_bone))
    except Exception as exc:
        print(f"[RuriRipperImporter] humanoid retarget unavailable: {exc}")
        return None
    if not retargeter.bone_targets():
        # An avatar whose bone names resolved through NEITHER HumanDescription
        # nor TOS (stripped avatar and no usable fallback) drives nothing --
        # surface that instead of handing back a silent no-op.
        print("[RuriRipperImporter] humanoid retarget unavailable: avatar maps no bones "
              "(stripped m_TOS and no matching armature paths)")
        return None
    return retargeter


def _stamp_avatar_on_armature(arm_obj, db, retargeter):
    """Persist the WORKING avatar's raw YAML onto the armature (zlib+base64
    custom property) so a standalone clip import can rebuild the exact same
    muscle retargeter from the armature alone. This matters because a clip's
    own dependency neighborhood does NOT reliably contain the character's
    rig: confirmed against the real game, pelica's battle clips reach only
    their battle AnimatorController (no prefab depends on IT through bundle
    dependencies -- it's attached by game code), whose closure's only Avatar
    is a 7KB weapon stub. The armature the user selects IS the character,
    so the referential travels with it."""
    try:
        from . import armature_builder
    except ImportError:
        import armature_builder
    source_key = getattr(retargeter, "source_key", None)
    if not source_key:
        return
    text = db.raw_text(source_key)
    if not text:
        return
    import base64
    import zlib
    arm_obj[armature_builder.AVATAR_YAML_PROP] = base64.b64encode(
        zlib.compress(text.encode("utf-8"), 6)).decode("ascii")


def retargeter_from_stamped_armature(arm_obj, path_to_bone=None):
    """Rebuild the humanoid muscle retargeter from the avatar YAML stamped on
    an armature at character-import time (see _stamp_avatar_on_armature) --
    the fallback for standalone clip imports whose own closure carries no
    usable Avatar. Returns None when the armature has no stamp (pre-feature
    import, or a character whose avatar never resolved)."""
    try:
        from . import armature_builder, unity_yaml
    except ImportError:
        import armature_builder
        import unity_yaml
    raw = arm_obj.get(armature_builder.AVATAR_YAML_PROP)
    if not raw:
        return None
    import base64
    import zlib
    try:
        text = zlib.decompress(base64.b64decode(raw)).decode("utf-8")
    except Exception:
        return None
    avatar_file = unity_yaml.UnityFile("stamped_avatar", unity_yaml.parse_text(text, "stamped_avatar"))
    return _retargeter_from_avatar_file(avatar_file, path_to_bone)


def clip_is_humanoid(clip_data):
    """Whether a parsed AnimationClip drives a humanoid rig: humanoid body
    motion ships as muscle/root float curves (attribute names like
    "Spine Front-Back" / "RootT.x"), not transform curves -- the exact
    predicate _bake_muscles gates on, exposed for callers that need to KNOW
    (e.g. to warn that a humanoid clip was imported without an Avatar in
    scope, which would silently drop the entire body's motion)."""
    try:
        from . import humanoid_retarget
    except ImportError:
        import humanoid_retarget
    if isinstance(clip_data, clip_curves.ClipCurves):
        return any(humanoid_retarget.is_muscle(ch.attribute) or humanoid_retarget.is_root(ch.attribute)
                   for ch in clip_data.floats)
    for entry in clip_data.get("m_FloatCurves") or []:
        attribute = entry.get("attribute") or ""
        if humanoid_retarget.is_muscle(attribute) or humanoid_retarget.is_root(attribute):
            return True
    return False


def _gather_clip_paths(db, prefab, prefab_path, assets_dir=None):
    """Clips for a prefab import: those referenced by its Animator controller,
    plus every loose ``.anim`` file found by a scoped folder walk.

    Humanoid muscle clips are avatar-portable and Unity often ships large clip
    libraries (battle/dialog/interact/...) that no AnimatorController
    references directly -- only the ones actually wired into a state machine.
    Without this, those clips are invisible to the importer even though the
    avatar can play every one of them. This mirrors RuriYamlDumper.cs's
    ``LoadAllAssetsAtPath`` step (which grabs every clip embedded in a source
    model, not just controller-referenced ones).

    The walk root is tiered because the two producers this addon reads shape
    a "character's own clips" folder completely differently:
      * RuriYamlDumper.cs dumps a SELF-CONTAINED sibling folder
        (``<model>_yaml/Anim/*.anim``) that can sit anywhere inside a live
        Unity project's ``Assets/`` -- walking the prefab's OWN directory
        finds exactly that folder's clips; walking the whole project's
        ``Assets/`` (find_assets_dir) would sweep in every OTHER character's
        clips too (real project layouts keep many characters under one
        ``Assets/``, so this is not a hypothetical).
      * An AssetRipper Unity-project export scatters a character's clips by
        ORIGINAL addressable path (e.g. ``.../actor/girl/pelica/animations/
        battle/*.anim``), nowhere near the prefab's own directory (e.g.
        ``.../postmodels/characters/``) -- only the export's ``Assets/`` root
        is guaranteed to be that one character's exclusive scope (by
        construction of the exporting batch, which puts one character's
        closure in its own dedicated output directory).
    Which scope applies is decided by a plain existence probe (does the
    prefab's own directory contain ANY ``.anim`` file, regardless of whether
    the controller already covers it) -- not by how many NEW clips it
    contributes after dedup, which would wrongly read "this folder holds only
    the controller's own clip" as "this folder is empty, widen the search"."""
    paths = []
    seen = set()

    animator = prefab.first("Animator")
    controller_ref = animator.data.get("m_Controller") if animator is not None else None
    if isinstance(controller_ref, dict) and controller_ref.get("guid"):
        controller_file = db.load_guid(controller_ref["guid"])
        if controller_file is not None:
            for path in clips_from_controller(db, controller_file):
                key = path.lower()
                if key not in seen:
                    seen.add(key)
                    paths.append(path)

    def _has_any_clip(root):
        """Cheap existence probe, independent of ``seen`` -- tier selection must
        not be confused by clips this scope holds that the controller already
        covered (a folder holding ONLY the controller's own clip is still the
        right scope, not a signal to fall back wider)."""
        if not root or not os.path.isdir(root):
            return False
        for _dirpath, _dirs, files in os.walk(root):
            if any(name.lower().endswith(".anim") for name in files):
                return True
        return False

    def _walk_for_clips(root):
        found = []
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not name.lower().endswith(".anim"):
                    continue
                ap = os.path.abspath(os.path.join(dirpath, name))
                key = ap.lower()
                if key not in seen:
                    seen.add(key)
                    found.append(ap)
        return found

    own_dir = os.path.dirname(os.path.abspath(prefab_path))
    scope = own_dir if _has_any_clip(own_dir) else assets_dir
    if scope:
        paths.extend(_walk_for_clips(scope))
    return paths


def _assign_first_action(arm_obj, action, slot=None):
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    try:
        arm_obj.animation_data.action = action
        if slot is not None and hasattr(arm_obj.animation_data, "action_slot"):
            arm_obj.animation_data.action_slot = slot
    except Exception:
        pass


def _build_materials(db, prefab, renderer, mat_builder, report):
    materials = []
    if mat_builder is None:
        return materials
    for ref in (renderer.data.get("m_Materials") or []):
        materials.append(mat_builder.build_from_ref(ref))
    return materials


def _import_skinned(context, db, prefab, smr, arm_obj, maps, mat_builder,
                    options, report, go_to_node):
    mesh_ref = smr.data.get("m_Mesh")
    if not isinstance(mesh_ref, dict) or not mesh_ref.get("guid"):
        return None
    mesh_file = db.load_guid(mesh_ref["guid"])
    if mesh_file is None:
        report.warnings.append(f"Mesh {mesh_ref.get('guid')} not found")
        return None
    mesh_doc = mesh_file.first("Mesh")
    if mesh_doc is None:
        return None
    decoded = mesh_decoder.decode_mesh(mesh_doc)
    if decoded.positions is None or len(decoded.positions) == 0:
        return None

    name = _go_name(prefab, (smr.data.get("m_GameObject") or {}).get("fileID"))
    materials = _build_materials(db, prefab, smr, mat_builder, report)
    smr_bones = smr.data.get("m_Bones") or []
    # Bake vertices from mesh-local into bind-pose world space so they align
    # with the armature regardless of the mesh's authored coordinate frame.
    _bake_bind_pose(decoded, smr_bones, maps.get("file_id_to_world", {}))
    obj = mesh_builder.build_mesh_object(
        context, decoded, name, arm_obj, smr_bones,
        maps["file_id_to_bone"], materials, options)
    return obj


def _bake_bind_pose(decoded, smr_bones, file_id_to_world):
    """Transform mesh-local vertices to their bind-pose world positions.

    bind_world(v) = sum_j w_j * (boneWorld_j @ bindpose_j) @ v_local

    This reconstructs the exact pose the mesh has at rest, in world space, so it
    aligns with the armature whose bones are placed at their world transforms.
    """
    if (decoded.bind_poses is None or decoded.bone_weights is None
            or decoded.bone_indices is None or not smr_bones):
        return
    n = len(decoded.positions)
    n_bones = len(smr_bones)
    world = np.tile(np.eye(4, dtype=np.float64), (n_bones, 1, 1))
    for slot, ref in enumerate(smr_bones):
        fid = ref.get("fileID") if isinstance(ref, dict) else None
        wmat = file_id_to_world.get(fid)
        if wmat is not None:
            world[slot] = wmat
    bind = decoded.bind_poses.astype(np.float64)
    count = min(n_bones, bind.shape[0])
    skin = np.tile(np.eye(4, dtype=np.float64), (n_bones, 1, 1))
    skin[:count] = world[:count] @ bind[:count]

    idx = np.clip(decoded.bone_indices, 0, n_bones - 1)
    weights = decoded.bone_weights
    vh = np.concatenate([decoded.positions.astype(np.float64),
                         np.ones((n, 1))], axis=1)
    baked = np.zeros((n, 3), dtype=np.float64)
    for j in range(idx.shape[1]):
        mats = skin[idx[:, j]]
        transformed = np.einsum("nij,nj->ni", mats, vh)[:, :3]
        baked += weights[:, j, None] * transformed
    decoded.positions = baked.astype(np.float32)

    if decoded.normals is not None:
        rot = skin[:, :3, :3]
        nvecs = decoded.normals.astype(np.float64)
        baked_n = np.zeros((n, 3), dtype=np.float64)
        for j in range(idx.shape[1]):
            mats = rot[idx[:, j]]
            baked_n += weights[:, j, None] * np.einsum("nij,nj->ni", mats, nvecs)
        lengths = np.linalg.norm(baked_n, axis=1, keepdims=True)
        lengths[lengths < 1e-6] = 1.0
        decoded.normals = (baked_n / lengths).astype(np.float32)


def _import_static(context, db, prefab, mr, maps, mat_builder, options, report,
                   go_to_node):
    go_id = (mr.data.get("m_GameObject") or {}).get("fileID")
    node = go_to_node.get(go_id)
    if node is None:
        return None
    # Find the MeshFilter on the same GameObject.
    mesh_ref = None
    for comp_id in node.components:
        comp = prefab.get(comp_id)
        if comp and comp.class_name == "MeshFilter":
            mesh_ref = comp.data.get("m_Mesh")
            break
    if not isinstance(mesh_ref, dict) or not mesh_ref.get("guid"):
        return None
    mesh_file = db.load_guid(mesh_ref["guid"])
    if mesh_file is None:
        return None
    mesh_doc = mesh_file.first("Mesh")
    if mesh_doc is None:
        return None
    decoded = mesh_decoder.decode_mesh(mesh_doc)
    if decoded.positions is None or len(decoded.positions) == 0:
        return None
    name = _go_name(prefab, go_id)
    materials = _build_materials(db, prefab, mr, mat_builder, report)
    obj = mesh_builder.build_mesh_object(
        context, decoded, name, None, [], {}, materials, options)
    obj.matrix_world = coordinate.convert_matrix(node.world)
    return obj


def import_mesh(context, mesh_path, options=None):
    """Import a standalone Unity Mesh .asset as a single static object."""
    options = _resolve_options(options)
    report = ImportReport()
    start = time.time()
    assets_dir = asset_db.find_assets_dir(mesh_path)
    db = asset_db.AssetDatabase(os.path.dirname(mesh_path), assets_dir)
    mesh_file = db.load_file(mesh_path)
    mesh_doc = mesh_file.first("Mesh")
    if mesh_doc is None:
        report.warnings.append("No Mesh object in file")
        return report
    decoded = mesh_decoder.decode_mesh(mesh_doc)
    name = os.path.splitext(os.path.basename(mesh_path))[0]
    obj = mesh_builder.build_mesh_object(context, decoded, name, None, [], {}, [], options)
    report.mesh_objects.append(obj)
    report.seconds = time.time() - start
    return report


def import_mesh_from_db(context, db, mesh_file, options=None, materials=None):
    """Bridge-mode sibling of import_mesh: a standalone mesh, no armature.
    Scene placements (see scene_state.py) resolve to one specific named mesh
    inside a multi-object FBX (e.g. "...building_001.fbx##building_001_lod2"),
    not a full prefab/GameObject hierarchy -- there is no MeshRenderer on the
    mesh's own CAB to read a material list from (confirmed: every scene mesh
    CAB checked holds exactly one Mesh document, nothing else). Real
    materials, when available, are resolved by the CALLER via a sibling
    prefab (see _scene_materials_for) and passed in here; when none were
    found (or import_materials is off), `materials` is None/empty and the
    mesh imports flat, matching the prior behavior."""
    options = _resolve_options(options)
    report = ImportReport()
    start = time.time()
    mesh_doc = mesh_file.first("Mesh")
    if mesh_doc is None:
        report.warnings.append("No Mesh object in file")
        return report
    decoded = mesh_decoder.decode_mesh(mesh_doc)
    if decoded.positions is None or len(decoded.positions) == 0:
        report.warnings.append("Empty mesh")
        return report
    name = str(mesh_doc.data.get("m_Name") or "Mesh")
    obj = mesh_builder.build_mesh_object(context, decoded, name, None, [], {}, materials or [], options)
    report.mesh_objects.append(obj)
    report.seconds = time.time() - start
    return report


def build_mesh_name_index_from_db(db):
    """Peek every document's class+name (see _peek_class_and_name -- cheap,
    no full parse) and index the Mesh-classed ones by LOWERCASED name. CabMap
    only maps container path -> CAB name, not -> guid, and a single CAB can
    host several named sub-objects (a multi-object FBX) -- this is what lets
    a scene placement's expected sub-object name (parsed from its
    ##subname-suffixed AssetPath, see _expected_mesh_name) resolve to a
    specific guid once its CAB has been imported by name alone.
    Lowercased because the hash-LUT-resolved AssetPath is consistently
    all-lowercase while a real Mesh's m_Name preserves its original authored
    casing (confirmed against the real game: AssetPath "...col1_um01" vs the
    actual m_Name "...COL1_UM01") -- the same case-insensitive join CabMap's
    own container-path normalization already needed, for the same reason."""
    index = {}
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if text is None:
            continue
        class_name, name = _peek_class_and_name(text)
        if class_name == "Mesh" and name:
            index[name.lower()] = guid
    return index


def _expected_mesh_name(asset_path):
    """The specific named sub-object a scene placement's hash-LUT-resolved
    AssetPath refers to: either the ##subname suffix (a multi-object FBX,
    e.g. "...building.fbx##building_col1"), or the file stem for a bare
    single-object .mesh path (Unity's convention: a standalone .mesh asset's
    own Mesh object is named after the file). Lowercased to match
    build_mesh_name_index_from_db's keys -- see that function's docstring."""
    if "##" in asset_path:
        return asset_path.split("##", 1)[1].lower()
    leaf = asset_path.rsplit("/", 1)[-1]
    return (leaf.rsplit(".", 1)[0] if "." in leaf else leaf).lower()


_LOD_SUFFIX_RE = re.compile(r"_lod(\d+)$", re.IGNORECASE)
_VARIANT_SUFFIX_RE = re.compile(r"_(?:lod\d+|col\d+_[a-z]+\d*)$", re.IGNORECASE)
_COL_SUFFIX_RE = re.compile(r"_col\d+_", re.IGNORECASE)


def _lod_rank(asset_path):
    """Lower is more preferred: lod0=0, lod1=1, ..., unsuffixed/unleveled=-1
    (as good as lod0 -- a single-LOD piece), collision meshes (_colN_xxx) last
    (rank 1000) since they routinely ship with zero render geometry (see
    import_scene_placements' mesh-decode note) -- tried only when nothing
    else in the group exists at all."""
    name = _expected_mesh_name(asset_path)
    match = _LOD_SUFFIX_RE.search(name)
    if match:
        return int(match.group(1))
    if _COL_SUFFIX_RE.search(name):
        return 1000
    return -1


def _lod_group_key(asset_path, px, py, pz):
    """(rounded position, base stem with its LOD/collision suffix stripped) --
    identifies the parallel sibling entities a real map places for the SAME
    instance at different detail levels: confirmed against base01_lv002 that
    a numbered-LOD and/or col1-collision sibling sits at the EXACT SAME
    position as its lod0 render counterpart, as separate ECS entities (see
    EndfieldSceneBridge.DecodeStreamingChunkPlacements). Position is rounded
    to collapse float noise between siblings placed identically. Used to pick
    the best AVAILABLE variant per instance (select_best_lod) instead of a
    blind per-entity suffix filter, which wrongly drops an entire instance
    whenever its only shipped variant happens to be a non-zero LOD (e.g. a
    piece with only _lod2 + _col1 siblings and no _lod0 at all -- confirmed
    this is what silently dropped base01_lv002's building-shell/floor piece
    even though the game genuinely ships visible geometry for it, just not
    at LOD0)."""
    stem = _VARIANT_SUFFIX_RE.sub("", _expected_mesh_name(asset_path))
    return (round(px, 2), round(py, 2), round(pz, 2), stem)


def select_best_lod(rows):
    """Group placements into per-instance LOD-sibling sets (_lod_group_key)
    and keep only the best-ranked (_lod_rank) member of each group. Replaces
    a blind "keep unless explicitly non-zero-LOD" filter, which assumes a
    LOD0 sibling always exists -- when it doesn't (only _lod1/_lod2/_col1
    variants were ever placed for that instance), the old filter dropped the
    instance entirely instead of falling back to whatever detail level the
    game actually shipped."""
    groups = {}
    for row in rows:
        key = _lod_group_key(row["asset_path"], row["px"], row["py"], row["pz"])
        groups.setdefault(key, []).append(row)
    return [min(members, key=lambda r: _lod_rank(r["asset_path"])) for members in groups.values()]


class _StampedNode:
    """Minimal stand-in for hierarchy.Node carrying exactly the two fields
    animation_builder.build_action reads off maps["nodes"] values: the Unity
    transform path and the Unity-space LOCAL rest matrix. Rebuilt from the
    rig identity build_armature stamps onto every armature it creates
    (armature_builder.UNITY_RIG_PROP) -- see maps_from_stamped_armature."""
    __slots__ = ("path", "local")

    def __init__(self, path, local):
        self.path = path
        self.local = local


def maps_from_stamped_armature(arm_obj):
    """Rebuild the maps dict build_action needs (nodes with .path/.local +
    path_to_bone) from the Unity rig identity stamped onto an armature at
    import time (armature_builder.build_armature, persisted in the .blend as
    a custom property) -- what lets a standalone animation import target ANY
    armature this addon ever built, in any session, without the character
    import's live state. Returns None for armatures with no stamp (imported
    by something else, or by a build older than the stamping)."""
    import json as _json
    from mathutils import Matrix

    try:
        from . import armature_builder
    except ImportError:
        import armature_builder

    raw = arm_obj.get(armature_builder.UNITY_RIG_PROP)
    if not raw:
        return None
    try:
        stamped = _json.loads(raw)["paths"]
    except (ValueError, KeyError, TypeError):
        return None

    nodes = {}
    path_to_bone = {}
    live_bones = {b.name for b in arm_obj.data.bones}
    for index, (path, entry) in enumerate(stamped.items()):
        bone = entry.get("bone")
        flat = entry.get("local")
        if not bone or bone not in live_bones or not flat or len(flat) != 16:
            continue
        local = Matrix((flat[0:4], flat[4:8], flat[8:12], flat[12:16]))
        nodes[index] = _StampedNode(path, local)
        path_to_bone[path] = bone
    if not path_to_bone:
        return None
    return {
        "nodes": nodes,
        "roots": [],
        "file_id_to_bone": {},
        "path_to_bone": path_to_bone,
        "file_id_to_world": {},
    }


# AssetRipper's placeholder for an animation curve path it could not restore to
# a transform-path string (the rig wasn't in the export scope): the raw Unity
# binding hash -- CRC32 of the UTF-8 path string, verified empirically against
# the real game (crc32(b"Root") == 0xB6C65665 == the exported "path_0xB6C65665_
# WvpMuNH" placeholder, exact match across every probe) -- hex-encoded with a
# random uniquifying suffix.
_HASHED_PATH_RE = re.compile(r"^path_0x([0-9A-Fa-f]{1,8})_")

_CURVE_LIST_FIELDS = ("m_RotationCurves", "m_PositionCurves", "m_ScaleCurves",
                      "m_EulerCurves", "m_FloatCurves")


def build_suffix_crc_table(path_to_bone):
    """{CRC32 -> full stamped path} over EVERY level-suffix of every bone path
    of the target armature. Unity's animation binding hashes are CRC32 of the
    path RELATIVE to the Animator's own node, while the armature stamps paths
    from the prefab ROOT -- and different model variants nest the rig at
    different depths (confirmed against the real game: a uimodel's clip paths
    start "Root/Bip001/..." while a postmodel armature stamps
    "chr_0013_aglina_postmodel/.../Root/Bip001/..."), so whole-path CRC never
    matches across variants. Enumerating each path's suffixes ("a/b/c", "b/c",
    "c") makes the join prefix-agnostic: the clip's Animator-relative path IS
    one of the suffixes when the bone genuinely exists under the selected
    skeleton. Suffix-CRC collisions keep the LONGEST suffix (deepest anchor
    wins -- a leaf-only match can be ambiguous, a long chain can't). This is
    path-structure identity, not display-name guessing: the same segments
    Unity itself hashed, just re-anchored."""
    import zlib

    table = {}
    for path in path_to_bone:
        parts = path.split("/")
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            crc = zlib.crc32(suffix.encode("utf-8")) & 0xFFFFFFFF
            prev = table.get(crc)
            if prev is None or len(suffix) > prev[0]:
                table[crc] = (len(suffix), path)
    return {crc: path for crc, (_length, path) in table.items()}


def _entry_crc(path):
    """The binding CRC32 a curve entry's path stands for: hashed placeholders
    ("path_0x<hex>_junk") carry it literally; restored string paths hash to
    it (crc32 of the UTF-8 path) -- one join key for both forms."""
    import zlib

    hash_match = _HASHED_PATH_RE.match(path)
    if hash_match:
        return int(hash_match.group(1), 16)
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def repair_hashed_clip_paths(clip_data, path_to_bone):
    """Rewrite a parsed AnimationClip's curve paths to the target armature's
    OWN stamped full paths, joining through the suffix-CRC table (see
    build_suffix_crc_table): hashed placeholders resolve by their literal
    CRC32, and already-restored string paths that don't literally appear in
    path_to_bone (rig nested at a different depth in this model variant)
    resolve by hashing -- both land on the exact path build_action's
    path_to_bone lookup needs. Returns (repaired, unmatched) counts; paths
    already matching the armature verbatim are left untouched."""
    table = build_suffix_crc_table(path_to_bone)
    repaired = 0
    unmatched = 0
    if isinstance(clip_data, clip_curves.ClipCurves):
        for channels in clip_data.all_channel_lists():
            for channel in channels:
                path = channel.path or ""
                if not path or path in path_to_bone:
                    continue
                real = table.get(_entry_crc(path))
                if real is None:
                    unmatched += 1
                    continue
                channel.path = real
                repaired += 1
        return repaired, unmatched
    for field in _CURVE_LIST_FIELDS:
        for entry in clip_data.get(field) or []:
            # "path:" with no value parses to an EXISTING key holding None (root-level
            # curves) -- `or ""` covers both missing and null, .get default only the former.
            path = entry.get("path") or ""
            if not path or path in path_to_bone:
                continue
            real = table.get(_entry_crc(path))
            if real is None:
                unmatched += 1
                continue
            entry["path"] = real
            repaired += 1
    return repaired, unmatched


def clip_path_match_ratio(clip_data, path_to_bone):
    """Fraction of the clip's transform-curve paths that resolve to a bone of
    the target armature -- the compatibility check for importing a clip onto
    the user's selected skeleton. Uses the same suffix-CRC join as
    repair_hashed_clip_paths (hashed and string paths alike), so a clip whose
    Animator-relative paths anchor anywhere inside the armature's hierarchy
    counts as matching. Returns (ratio, total); (0.0, 0) for a clip with no
    transform curves at all (e.g. pure blendshape clips)."""
    table = build_suffix_crc_table(path_to_bone)
    total = 0
    matched = 0
    if isinstance(clip_data, clip_curves.ClipCurves):
        for channels in clip_data.transform_channel_lists():
            for channel in channels:
                path = channel.path or ""
                if not path:
                    continue
                total += 1
                if path in path_to_bone or _entry_crc(path) in table:
                    matched += 1
        return (matched / total if total else 0.0), total
    for field in ("m_RotationCurves", "m_PositionCurves", "m_ScaleCurves", "m_EulerCurves"):
        for entry in clip_data.get(field) or []:
            path = entry.get("path") or ""  # "path:" null-valued for root-level curves
            if not path:
                continue
            total += 1
            if path in path_to_bone or _entry_crc(path) in table:
                matched += 1
    return (matched / total if total else 0.0), total


def build_material_name_index_from_db(db):
    """Peek every document's class+name (see _peek_class_and_name) and index
    the Material-classed ones by LOWERCASED name -- mirrors
    build_mesh_name_index_from_db exactly, just filtering a different class.
    Joins a scene placement's own material_asset_paths (see scene_state.py,
    ultimately EndfieldSceneBridge.cs's FBPropertyAssetData AssetType==1
    resolution -- the entity's own real material hash, ground-truthed
    against EndFieldSceneLoader's SceneLoaderWindow.cs CollectAssetPathsTyped/
    ResolveHash/AttachMeshAndMaterials) to a guid once its CAB is in the
    resolved closure."""
    index = {}
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if text is None:
            continue
        class_name, name = _peek_class_and_name(text)
        if class_name == "Material" and name:
            index[name.lower()] = guid
    return index


def _scene_materials_for(material_index, mat_builder, material_asset_paths):
    """Real materials for a scene-placed mesh, resolved directly from its own
    material_asset_paths -- the entity's actual material hash(es), resolved
    through the same StringPathHash LUT as its mesh. [] when the entity
    carries no material, none resolved, or import_materials is off."""
    if mat_builder is None or not material_asset_paths:
        return []
    materials = []
    for path in material_asset_paths:
        guid = material_index.get(_expected_mesh_name(path))
        if guid is None:
            continue
        mat = mat_builder.build_from_ref({"guid": guid})
        if mat is not None:
            materials.append(mat)
    return materials


def is_full_prefab_path(asset_path):
    """True when a scene placement's resolved asset_path is itself a real
    .prefab (the DynamicScene family -- Model/Effect/Tree, resolved via
    EndfieldSceneBridge.cs's DecodeDynamicSceneChunkPlacements -- always
    resolves to a full authored prefab, carrying its own real Renderer +
    Materials already) rather than a raw FBX mesh sub-asset (the Streaming
    family's '...models/s_x.fbx##subname' shape, which needs the separate
    mesh + material-hash resolution path)."""
    return asset_path.lower().endswith(".prefab")


def _prefab_asset_stem(asset_path):
    """Basename (no extension, lowercased) of a resolved .prefab asset_path,
    e.g. '.../Prefabs/P_anm_com_satellite+1_001_01.prefab' ->
    'p_anm_com_satellite+1_001_01' -- for matching against
    build_prefab_name_index_from_roots' keys."""
    leaf = asset_path.rsplit("/", 1)[-1]
    stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    return stem.lower()


def build_prefab_name_index_from_roots(db, roots):
    """{prefab stem (lowercased, from its own display name) -> guid}, built
    from ImportCabs' own top-level-.prefab guid list for the resolved
    closure. Used to resolve DynamicScene Model/Effect/Tree placements
    (is_full_prefab_path) to a specific guid -- mirrors
    build_mesh_name_index_from_db's name-based join, but keyed off each
    root's own display name rather than a peeked Mesh's m_Name, since
    `roots` is already the small, pre-filtered set of prefab-classed
    top-level assets in the closure (no full-closure scan needed)."""
    index = {}
    for guid in roots:
        prefab_file = db.load_guid(guid)
        if prefab_file is None:
            continue
        name = _prefab_display_name(prefab_file)
        if name:
            index[name.lower()] = guid
    return index


def _duplicate_hierarchy(context, anchor):
    """Deep-copies an anchor Empty and every descendant object beneath it
    (sharing mesh/material/armature DATA -- only object-level transform and
    parent differ), returning the new anchor. The prefab-placement
    equivalent of the mesh-instancing pattern import_scene_placements
    already uses for Streaming meshes -- a repeated DynamicScene prop
    (a satellite dish, a decoration) shares one import instead of a second
    full prefab rebuild."""
    def _walk(obj):
        new_obj = obj.copy()
        if obj.data is not None:
            new_obj.data = obj.data
        context.collection.objects.link(new_obj)
        for child in obj.children:
            new_child = _walk(child)
            new_child.parent = new_obj
        return new_obj
    return _walk(anchor)


def _place_prefab_report(context, report, placement):
    """Wraps every top-level object import_prefab_from_db produced (mesh
    objects with no parent, plus the armature if any -- static/environmental
    DynamicScene prefabs are not expected to have one, but this stays
    correct either way) under a new anchor Empty, then moves that anchor to
    the placement's resolved world transform. Only objects with no parent
    need the placement applied -- Blender already propagates parent-to-child
    transforms for anything nested under an armature or another mesh."""
    top_level = []
    if report.armature is not None and report.armature.parent is None:
        top_level.append(report.armature)
    for obj in report.mesh_objects:
        if obj.parent is None and obj not in top_level:
            top_level.append(obj)
    if not top_level:
        return None

    anchor = bpy.data.objects.new(f"{top_level[0].name}_placement", None)
    context.collection.objects.link(anchor)
    for obj in top_level:
        obj.parent = anchor

    unity_matrix = coordinate.unity_trs(
        {"x": placement["px"], "y": placement["py"], "z": placement["pz"]},
        {"x": placement["qx"], "y": placement["qy"], "z": placement["qz"], "w": placement["qw"]},
        {"x": placement["sx"], "y": placement["sy"], "z": placement["sz"]})
    anchor.matrix_world = coordinate.convert_matrix(unity_matrix)
    return anchor


def import_scene_placements(context, db, placements, roots=(), options=None):
    """Import a batch of scene placements (see scene_state.py) into the
    current scene, against an already-resolved closure db covering every CAB
    those placements need. Covers BOTH scene-data families in one pass:

    - Streaming family (raw FBX mesh sub-assets, e.g.
      '...models/s_x.fbx##subname'): resolves the expected mesh sub-object
      by name (build_mesh_name_index_from_db), materials from the
      placement's own material_asset_paths (build_material_name_index_from_db
      -- FBPropertyAssetData AssetType==1, same hashLut as the mesh, no
      naming-convention guess), and builds via import_mesh_from_db.
    - DynamicScene family (Model/Effect/Tree, resolved to REAL .prefab
      paths -- is_full_prefab_path): resolves the prefab by name against
      `roots` (build_prefab_name_index_from_roots) and builds via
      import_prefab_from_db, which already brings real Renderer + Materials
      (no separate material-hash lookup needed for these).

    Either way, imports each DISTINCT asset exactly once; every further
    placement of the same asset becomes a linked-data duplicate (mesh path:
    .copy() sharing mesh data; prefab path: _duplicate_hierarchy sharing the
    whole object graph's data) instead of a second full import -- a real map
    can place the same prop hundreds of times, and re-decoding identical
    bytes that many times would be exactly the kind of eagerly-repeated cost
    the animation browser fix (see cabmap_state.py) already had to solve for
    a similar reason.
    Returns (imported_count, placed_count, unresolved_count)."""
    options = _resolve_options(options)
    name_index = build_mesh_name_index_from_db(db)
    prefab_index = build_prefab_name_index_from_roots(db, roots)
    mat_builder = material_builder.MaterialBuilder(db, options) if options["import_materials"] else None
    material_index = build_material_name_index_from_db(db) if mat_builder is not None else {}
    obj_by_guid = {}
    anchor_by_guid = {}
    imported = 0
    placed = 0
    unresolved = 0

    for placement in placements:
        asset_path = placement["asset_path"]

        if is_full_prefab_path(asset_path):
            guid = prefab_index.get(_prefab_asset_stem(asset_path))
            if guid is None:
                unresolved += 1
                continue

            base_anchor = anchor_by_guid.get(guid)
            if base_anchor is None:
                prefab_file = db.load_guid(guid)
                if prefab_file is None:
                    unresolved += 1
                    continue
                report = import_prefab_from_db(context, db, prefab_file, options)
                anchor = _place_prefab_report(context, report, placement)
                if anchor is None:
                    unresolved += 1
                    continue
                anchor_by_guid[guid] = anchor
                imported += 1
                placed += 1
                continue

            target = _duplicate_hierarchy(context, base_anchor)
        else:
            expected_name = _expected_mesh_name(asset_path)
            guid = name_index.get(expected_name)
            if guid is None:
                unresolved += 1
                continue

            base_obj = obj_by_guid.get(guid)
            if base_obj is None:
                mesh_file = db.load_guid(guid)
                if mesh_file is None:
                    unresolved += 1
                    continue
                materials = _scene_materials_for(material_index, mat_builder, placement.get("material_asset_paths") or [])
                report = import_mesh_from_db(context, db, mesh_file, options, materials)
                if not report.mesh_objects:
                    unresolved += 1
                    continue
                base_obj = report.mesh_objects[0]
                obj_by_guid[guid] = base_obj
                imported += 1
                target = base_obj
            else:
                target = base_obj.copy()
                target.data = base_obj.data
                context.collection.objects.link(target)

        unity_matrix = coordinate.unity_trs(
            {"x": placement["px"], "y": placement["py"], "z": placement["pz"]},
            {"x": placement["qx"], "y": placement["qy"], "z": placement["qz"], "w": placement["qw"]},
            {"x": placement["sx"], "y": placement["sy"], "z": placement["sz"]})
        target.matrix_world = coordinate.convert_matrix(unity_matrix)
        placed += 1

    return imported, placed, unresolved


# --- unified entry point ----------------------------------------------------

def import_asset(context, path, options=None):
    """Import any supported Unity asset, dispatching on its type.

    Supports .prefab (full model + its animator's clips), Mesh .asset,
    .anim (clip), and .controller / animator (all referenced clips).  Clips and
    controllers apply onto the active (or first) armature in the scene.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".prefab":
        return import_prefab(context, path, options)
    if ext == ".anim":
        return import_clip(context, path, options)
    if ext == ".controller":
        return import_controller(context, path, options)
    if ext == ".asset":
        unity_file = asset_db.AssetDatabase(os.path.dirname(path),
                                            asset_db.find_assets_dir(path)).load_file(path)
        classes = {d.class_name for d in unity_file.documents}
        if "Mesh" in classes:
            return import_mesh(context, path, options)
        if "AnimationClip" in classes:
            return import_clip(context, path, options)
        if classes & {"AnimatorController", "AnimatorOverrideController"}:
            return import_controller(context, path, options)
        report = ImportReport()
        report.warnings.append("Unsupported .asset type: " + ", ".join(sorted(classes)))
        return report
    # Fall back to prefab handling for unknown extensions.
    return import_prefab(context, path, options)


def _active_armature(context):
    obj = getattr(context, "active_object", None)
    if obj is not None and obj.type == "ARMATURE":
        return obj
    for o in context.scene.objects:
        if o.type == "ARMATURE":
            return o
    return None


def _maps_from_armature(arm_obj):
    """Build clip-targeting maps from an existing armature's rest pose.

    Lets standalone clips/controllers apply onto a model previously imported by
    this add-on, where bone names equal the source GameObject names.
    """
    from mathutils import Matrix  # noqa: F401 (kept for clarity / future use)

    conv = coordinate.conversion_matrix()

    class _Node:
        __slots__ = ("path", "local")

    def bone_path(bone):
        names = []
        cursor = bone
        while cursor.parent is not None:
            names.append(cursor.name)
            cursor = cursor.parent
        return "/".join(reversed(names))

    nodes = {}
    path_to_bone = {}
    for bone in arm_obj.data.bones:
        if bone.parent is None:
            local_blender = bone.matrix_local
        else:
            local_blender = bone.parent.matrix_local.inverted_safe() @ bone.matrix_local
        node = _Node()
        node.path = bone_path(bone)
        node.local = conv @ local_blender @ conv  # local, back to Unity space
        nodes[bone.name] = node
        if node.path:
            path_to_bone[node.path] = bone.name
    return {"nodes": nodes, "path_to_bone": path_to_bone}


def _find_retargeter_near(clip_path):
    """Locate an Avatar ``.asset`` near a clip and build a muscle retargeter.

    Clip-only imports (a clip applied onto an existing armature) have no prefab
    Animator reference, so the avatar is found by name in the character's folder
    tree.  Humanoid clips store the body as muscle floats; without this the body
    bones get no curves and stay at the bind (A) pose.
    """
    try:
        from . import humanoid_retarget
    except ImportError:
        import humanoid_retarget
    db = asset_db.AssetDatabase(os.path.dirname(clip_path),
                                asset_db.find_assets_dir(clip_path))
    root = os.path.dirname(os.path.abspath(clip_path))
    # Climb to the character root (a folder holding a 'models'/'model' subdir),
    # bounded so we never scan the whole project.
    for _ in range(5):
        if (os.path.isdir(os.path.join(root, "models"))
                or os.path.isdir(os.path.join(root, "model"))):
            break
        parent = os.path.dirname(root)
        if parent == root:
            break
        root = parent
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".asset") and "avatar" in name.lower():
                try:
                    unity_file = db.load_file(os.path.join(dirpath, name))
                except OSError:
                    continue
                if unity_file.first("Avatar") is not None:
                    try:
                        return humanoid_retarget.HumanoidRetargeter(unity_file)
                    except Exception as exc:
                        print(f"[RuriRipperImporter] avatar {name} unusable: {exc}")
                        continue
    return None


def _apply_clip_paths(context, clip_paths, options):
    """Build actions from clip paths onto the active armature."""
    report = ImportReport()
    start = time.time()
    arm = _active_armature(context)
    if arm is None:
        report.warnings.append("No armature in the scene to apply clips to. "
                               "Import the model first, then the clips.")
        report.seconds = time.time() - start
        return report
    report.armature = arm
    report.bones = len(arm.data.bones)
    maps = _maps_from_armature(arm)
    # Humanoid clips carry the body as muscle floats; locate the avatar near the
    # clips so the body retargets here too (not only in the prefab path).
    if clip_paths:
        retargeter = _find_retargeter_near(clip_paths[0])
        if retargeter is not None:
            maps["retargeter"] = retargeter
    first = None
    for clip_path in clip_paths:
        clip = _load_clip_fast(clip_path)
        if clip is None:
            try:
                clip_file = asset_db.AssetDatabase(os.path.dirname(clip_path),
                                                   asset_db.find_assets_dir(clip_path)).load_file(clip_path)
            except OSError:
                continue
            clip_doc = clip_file.first("AnimationClip")
            if clip_doc is None:
                continue
            clip = clip_doc
        action, slot, _frames = animation_builder.build_action(
            clip, arm, maps, None, _resolve_options(options))
        report.actions += 1
        if first is None:
            first = (action, slot)
    if first is not None:
        _assign_first_action(arm, first[0], first[1])
    report.seconds = time.time() - start
    return report


def import_clip(context, clip_path, options=None):
    """Import a single .anim as an action onto the active armature."""
    return _apply_clip_paths(context, [clip_path], options)


def import_controller(context, controller_path, options=None):
    """Import every AnimationClip referenced by a controller onto the armature."""
    db = asset_db.AssetDatabase(os.path.dirname(controller_path),
                                asset_db.find_assets_dir(controller_path))
    controller_file = db.load_file(controller_path)
    clip_paths = clips_from_controller(db, controller_file)
    return _apply_clip_paths(context, clip_paths, options)
