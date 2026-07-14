"""Top-level import orchestration: prefab (full model) and standalone mesh."""

from __future__ import annotations

import os
import re
import time

import numpy as np

try:
    from . import (armature_builder, asset_db, coordinate, animation_builder,
                   material_builder, mesh_builder, mesh_decoder)
except ImportError:
    import armature_builder
    import asset_db
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
    return _import_prefab_core(context, db, prefab, arm_name, clip_files, options)


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


def _gather_clip_files_disk(db, prefab, prefab_path, assets_dir):
    """Disk-mode clip gathering: resolve _gather_clip_paths's paths into loaded UnityFiles."""
    clip_files = []
    for clip_path in _gather_clip_paths(db, prefab, prefab_path, assets_dir):
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
    character's closure up front."""
    built = 0
    first = None
    has_action = arm_obj.animation_data is not None and arm_obj.animation_data.action is not None
    for guid in guids:
        clip_file = db.load_guid(guid)
        if clip_file is None:
            continue
        clip_doc = clip_file.first("AnimationClip")
        if clip_doc is None:
            continue
        action, slot, _frames = animation_builder.build_action(
            clip_doc, arm_obj, maps, path_to_meshobjects, options)
        built += 1
        if first is None:
            first = (action, slot)
    if first is not None and not has_action:
        _assign_first_action(arm_obj, first[0], first[1])
    return built


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
        retargeter = _load_retargeter(db, prefab)
        if retargeter is not None:
            maps["retargeter"] = retargeter
        actions = []
        for clip_file in clip_files:
            clip_doc = clip_file.first("AnimationClip")
            if clip_doc is None:
                continue
            action, slot, _frames = animation_builder.build_action(
                clip_doc, arm_obj, maps, path_to_meshobjects, options)
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


def _load_retargeter(db, prefab):
    """Build a humanoid muscle retargeter from the prefab's Animator avatar.

    Humanoid clips carry the body's motion as muscle floats, not transform
    curves, so the human bones need the avatar's Muscle Referential to play.
    Returns None for non-humanoid rigs or when the avatar can't be resolved."""
    animator = prefab.first("Animator")
    if animator is None:
        return None
    avatar_ref = animator.data.get("m_Avatar")
    if not (isinstance(avatar_ref, dict) and avatar_ref.get("guid")):
        return None
    avatar_file = db.load_guid(avatar_ref["guid"])
    if avatar_file is None:
        return None
    try:
        from . import humanoid_retarget
    except ImportError:
        import humanoid_retarget
    try:
        return humanoid_retarget.HumanoidRetargeter(avatar_file)
    except Exception as exc:
        print(f"[RuriRipperImporter] humanoid retarget unavailable: {exc}")
        return None


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


def is_lod0_or_unleveled(asset_path):
    """True unless a scene placement's own mesh sub-object name carries an
    explicit non-zero LOD suffix (e.g. '..._lod1', '..._lod2'). Used by
    scene_state.placeable(lod0_only=True) to drop the lower-detail LOD chain
    variants a real map places for every piece -- these dominate a full
    scene's placement count without adding visible detail at the distance the
    game actually shows them. Assets with no _lodN suffix at all (single-LOD
    props, collision/shadow proxies) are left alone -- they aren't part of a
    LOD chain to begin with."""
    match = _LOD_SUFFIX_RE.search(_expected_mesh_name(asset_path))
    return match is None or match.group(1) == "0"


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


def import_scene_placements(context, db, placements, options=None):
    """Import a batch of scene placements (see scene_state.py) into the
    current scene, against an already-resolved closure db covering every CAB
    those placements need. Resolves each placement's expected mesh
    sub-object by name (build_mesh_name_index_from_db) and imports each
    DISTINCT mesh exactly once; every further placement of the same mesh
    becomes a linked-data duplicate (shares the mesh datablock, only the
    object-level transform differs) instead of a second full import -- a
    real map can place the same prop (foliage, generic colliders, ...)
    hundreds of times, and re-decoding identical mesh bytes that many times
    would be exactly the kind of eagerly-repeated cost the animation browser
    fix (see cabmap_state.py) already had to solve for a similar reason.

    When import_materials is on, each distinct mesh's material(s) are
    resolved directly from its own placement's material_asset_paths (see
    scene_state.resolve_cabs, which seeds those same paths into the CAB
    closure so their CABs -- and their own texture dependencies -- come
    along in the same import_cabs call) via build_material_name_index_from_db
    -- no naming-convention guess.
    Returns (imported_count, placed_count, unresolved_count)."""
    options = _resolve_options(options)
    name_index = build_mesh_name_index_from_db(db)
    mat_builder = material_builder.MaterialBuilder(db, options) if options["import_materials"] else None
    material_index = build_material_name_index_from_db(db) if mat_builder is not None else {}
    obj_by_guid = {}
    imported = 0
    placed = 0
    unresolved = 0

    for placement in placements:
        expected_name = _expected_mesh_name(placement["asset_path"])
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
        try:
            clip_file = asset_db.AssetDatabase(os.path.dirname(clip_path),
                                               asset_db.find_assets_dir(clip_path)).load_file(clip_path)
        except OSError:
            continue
        clip_doc = clip_file.first("AnimationClip")
        if clip_doc is None:
            continue
        action, slot, _frames = animation_builder.build_action(
            clip_doc, arm, maps, None, _resolve_options(options))
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
