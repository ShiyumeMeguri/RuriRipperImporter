"""Top-level import orchestration: prefab (full model) and standalone mesh."""

from __future__ import annotations

import os
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
    matter and clip-gathering source differ. Clip gathering uses the closure
    itself (controller-referenced clips by guid, plus every AnimationClip
    document already present in the closure) instead of a disk folder walk,
    since the whole dependency closure already IS the relevant scope."""
    options = _resolve_options(options)
    arm_name = name or _prefab_display_name(prefab_file)
    clip_files = _gather_clip_files_from_db(db, prefab_file)
    return _import_prefab_core(context, db, prefab_file, arm_name, clip_files, options)


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


def _gather_clip_files_from_db(db, prefab):
    """Bridge-mode clip gathering: controller-referenced clips (resolved by guid,
    not by path/extension) plus every AnimationClip document already present in
    the closure -- the bridge equivalent of the disk importer's controller-refs
    + loose-.anim-folder-walk (there is no folder to walk; the closure already
    is the scope)."""
    clips = []
    seen_ids = set()

    animator = prefab.first("Animator")
    controller_ref = animator.data.get("m_Controller") if animator is not None else None
    if isinstance(controller_ref, dict) and controller_ref.get("guid"):
        controller_file = db.load_guid(controller_ref["guid"])
        if controller_file is not None:
            guids = set()
            for doc in controller_file.documents:
                _collect_guids(doc.data, guids)
            for guid in guids:
                if guid in seen_ids:
                    continue
                clip_file = db.load_guid(guid)
                if clip_file is not None and clip_file.first("AnimationClip") is not None:
                    seen_ids.add(guid)
                    clips.append(clip_file)

    for guid in db.all_guids():
        if guid in seen_ids:
            continue
        clip_file = db.load_guid(guid)
        if clip_file is not None and clip_file.first("AnimationClip") is not None:
            seen_ids.add(guid)
            clips.append(clip_file)

    return clips


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
