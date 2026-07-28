"""Top-level import orchestration: prefab (full model) and standalone mesh."""

from __future__ import annotations

import os
import re
import time

import numpy as np

# `clip_paths` is aliased to clip_repair and `prefab` to prefab_scan: both names
# are already taken here by local variables (a list of .anim paths, a parsed
# prefab UnityFile) that appear in almost every function below.
try:
    from . import (armature_builder, coordinate, animation_builder,
                   material_builder, mesh_builder)
    from .ruri_pybridge.unity import (asset_db, asset_paths, clip_curves,
                                      clip_paths as clip_repair, discovery,
                                      mesh_decoder, prefab as prefab_scan, skinning)
except ImportError:  # standalone (non-package) testing
    import armature_builder
    import coordinate
    import animation_builder
    import material_builder
    import mesh_builder
    from ruri_pybridge.unity import (asset_db, asset_paths, clip_curves,
                                     clip_paths as clip_repair, discovery,
                                     mesh_decoder, prefab as prefab_scan, skinning)

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
        self.skipped_inactive = 0
        self.warnings = []
        self.seconds = 0.0
        self.maps = None        # hierarchy/bone maps (for external clip application)
        self.db = None          # AssetDatabase (for further resolution)
        self.path_to_meshobjects = None
        self.available_clips = []  # bridge mode only: discovery.discover_clip_refs() results

    def summary(self):
        return (f"armature_bones={self.bones} meshes={len(self.mesh_objects)} "
                f"materials={self.materials} textures={self.textures} "
                f"actions={self.actions} lod_skipped={self.skipped_lod} "
                f"shadow_skipped={self.skipped_shadow} "
                f"inactive_skipped={self.skipped_inactive} time={self.seconds:.2f}s")


def _resolve_options(options):
    merged = dict(DEFAULT_OPTIONS)
    if options:
        merged.update(options)
    return merged


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
    discovery.discover_clip_refs) and reported on report.available_clips; the
    caller builds actions later, only for whichever clips the user actually
    picks in the animation browser, via build_selected_animations."""
    options = _resolve_options(options)
    arm_name = name or discovery.prefab_display_name(prefab_file)
    report = _import_prefab_core(context, db, prefab_file, arm_name, [], options)
    if options["import_animations"]:
        report.available_clips = discovery.discover_clip_refs(db, prefab_file)
    return report


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


def build_selected_animations(db, arm_obj, maps, path_to_meshobjects, guids, options):
    """Build Blender actions for exactly the given clip guids -- the checked
    subset from the animation browser. This is the only place that now pays
    the full parse + keyframe-insertion cost per clip; it's deferred until the
    user explicitly picks a clip rather than paying it for every clip in a
    character's closure up front.

    Before building, every clip gets clip_repair.repair_hashed_clip_paths against the
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
        repaired, unmatched = clip_repair.repair_hashed_clip_paths(clip, path_to_bone)
        if unmatched:
            warnings.append(f"{clip_name}: {unmatched} hashed curve "
                            f"path(s) matched no bone of '{arm_obj.name}' (skipped)")
        if clip_repair.clip_is_humanoid(clip):
            # The muscle encoding is resolved on the C# side before export
            # (HumanoidToGenericProcessor); seeing it here means that hook was not enabled, and
            # this importer has no muscle solver of its own to fall back on.
            warnings.append(f"{clip_name}: still carries muscle curves -- enable the "
                            f"AR_HumanoidToGeneric hook, or the body will not move")
        action, slot, n_frames = animation_builder.build_action(
            clip, arm_obj, maps, path_to_meshobjects, options)

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
        except ImportError:  # standalone (non-package) testing
            import hierarchy
        nodes, roots = hierarchy.build_hierarchy(prefab)
        maps = {"nodes": nodes, "roots": roots,
                "file_id_to_bone": {}, "path_to_bone": {},
                "file_id_to_world": hierarchy.world_matrices(nodes)}

    nodes = maps["nodes"]
    go_to_node = {n.go_id: n for n in nodes.values()}

    mat_builder = material_builder.MaterialBuilder(db, options) if options["import_materials"] else None

    path_to_meshobjects = {}

    # Which renderers actually draw -- LODGroup levels, ShadowsOnly proxies,
    # disabled/inactive GameObjects and static-batch windows are all decided by
    # the shared rules (ruri_pybridge.unity.prefab), so this add-on and the
    # Painter plugin cannot disagree about what a prefab contains.
    stats = prefab_scan.SkipStats()
    for renderer in prefab_scan.iter_renderers(prefab, go_to_node, options, stats):
        if renderer.is_skinned:
            obj = _import_skinned(context, db, renderer, arm_obj, maps,
                                  mat_builder, options, report)
            if obj is not None and renderer.node is not None:
                path_to_meshobjects.setdefault(renderer.node.path, []).append(obj)
        else:
            obj = _import_static(context, db, renderer, mat_builder, options, report)
        if obj is not None:
            report.mesh_objects.append(obj)
    report.skipped_lod += stats.lod
    report.skipped_shadow += stats.shadow
    report.skipped_inactive += stats.inactive

    if mat_builder is not None:
        report.materials = len(mat_builder._cache)
        report.textures = len(mat_builder._image_cache)

    # Animations: every gathered clip (source differs disk vs. bridge mode) as actions.
    if options["import_animations"] and arm_obj is not None:
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
            if clip_repair.clip_is_humanoid(humanoid_probe):
                # Muscle curves are resolved to per-bone transform curves on the C# side before
                # export; a clip still carrying them means that pass did not run, and there is no
                # muscle solver here to compensate.
                report.warnings.append(
                    f"{clip_name}: still carries muscle curves -- enable the AR_HumanoidToGeneric "
                    f"hook, or this clip's body motion will be absent.")
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
            for path in discovery.clip_paths_from_controller(db, controller_file):
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


def _build_materials(renderer, mat_builder):
    """The renderer's material slots, in Unity's own order (a slot that doesn't
    resolve stays as None, so the remaining slots keep their indices)."""
    if mat_builder is None:
        return []
    return [mat_builder.build_from_ref(ref) for ref in renderer.material_refs]


def _decode_renderer_mesh(db, renderer, report):
    """Follow a renderer's mesh reference through the shared resolver and turn
    whatever went wrong into this add-on's own wording. None means no geometry."""
    loaded = prefab_scan.load_mesh(db, renderer.mesh_ref, renderer.name)
    if loaded.ok:
        if loaded.dropped_topologies:
            report.warnings.append(
                f"{renderer.name}: dropped {len(loaded.dropped_topologies)} non-triangle "
                f"submesh(es) ({', '.join(mesh_decoder.TOPOLOGY_NAMES.get(t, str(t)) for t in loaded.dropped_topologies)}).")
        return loaded.decoded
    if loaded.problem == "not_found":
        report.warnings.append(f"Mesh {loaded.detail} not found")
    elif loaded.problem == "empty":
        report.warnings.append(f"{renderer.name}: {loaded.detail}")
    return None


def _import_skinned(context, db, renderer, arm_obj, maps, mat_builder, options, report):
    decoded = _decode_renderer_mesh(db, renderer, report)
    if decoded is None:
        return None
    materials = _build_materials(renderer, mat_builder)
    # Bake vertices from mesh-local into bind-pose world space so they align
    # with the armature regardless of the mesh's authored coordinate frame.
    skinning.bake_bind_pose(decoded, renderer.bones, maps.get("file_id_to_world", {}))
    return mesh_builder.build_mesh_object(
        context, decoded, renderer.name, arm_obj, renderer.bones,
        maps["file_id_to_bone"], materials, options)


def _import_static(context, db, renderer, mat_builder, options, report):
    decoded = _decode_renderer_mesh(db, renderer, report)
    if decoded is None:
        return None
    materials = _build_materials(renderer, mat_builder)
    obj = mesh_builder.build_mesh_object(
        context, decoded, renderer.name, None, [], {}, materials, options)
    obj.matrix_world = coordinate.convert_matrix(renderer.node.world)
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
    name = str(mesh_doc.data.get("m_Name") or "Mesh")
    decoded = mesh_decoder.decode_mesh(mesh_doc)
    if decoded.positions is None or len(decoded.positions) == 0:
        # Say WHICH of Unity's three vertex-data storages holds it instead.
        report.warnings.append(mesh_decoder.diagnose_empty(mesh_doc, name))
        return report
    obj = mesh_builder.build_mesh_object(context, decoded, name, None, [], {}, materials or [], options)
    report.mesh_objects.append(obj)
    report.seconds = time.time() - start
    return report


def import_avatar_from_db(context, db, avatar_file, options=None, name=None):
    """Bridge-mode standalone Avatar import: a Blender armature built straight from the avatar's
    OWN embedded skeleton (armature_builder.build_armature_from_avatar), independent of any
    accompanying rig FBX/prefab."""
    options = _resolve_options(options)
    report = ImportReport()
    start = time.time()
    avatar_doc = avatar_file.first("Avatar")
    if avatar_doc is None:
        report.warnings.append("No Avatar object in file")
        return report
    resolved_name = name or str(avatar_doc.data.get("m_Name") or "Avatar")
    try:
        arm_obj = armature_builder.build_armature_from_avatar(context, avatar_file, resolved_name)
    except Exception as exc:
        report.warnings.append(f"Avatar skeleton parse failed: {type(exc).__name__}: {exc}")
        return report
    report.armature = arm_obj
    report.bones = len(arm_obj.data.bones)
    report.seconds = time.time() - start
    return report


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


def _scene_materials_for(material_index, mat_builder, material_asset_paths):
    """Real materials for a scene-placed mesh, resolved directly from its own
    material_asset_paths -- the entity's actual material hash(es), resolved
    through the same StringPathHash LUT as its mesh. [] when the entity
    carries no material, none resolved, or import_materials is off."""
    if mat_builder is None or not material_asset_paths:
        return []
    materials = []
    for path in material_asset_paths:
        guid = material_index.get(asset_paths.expected_mesh_name(path))
        if guid is None:
            continue
        mat = mat_builder.build_from_ref({"guid": guid})
        if mat is not None:
            materials.append(mat)
    return materials


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
      by name (discovery.mesh_name_index), materials from the
      placement's own material_asset_paths (discovery.material_name_index
      -- FBPropertyAssetData AssetType==1, same hashLut as the mesh, no
      naming-convention guess), and builds via import_mesh_from_db.
    - DynamicScene family (Model/Effect/Tree, resolved to REAL .prefab
      paths -- asset_paths.is_full_prefab_path): resolves the prefab by name against
      `roots` (discovery.prefab_name_index) and builds via
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
    name_index = discovery.mesh_name_index(db)
    prefab_index = discovery.prefab_name_index(db, roots)
    mat_builder = material_builder.MaterialBuilder(db, options) if options["import_materials"] else None
    material_index = discovery.material_name_index(db) if mat_builder is not None else {}
    obj_by_guid = {}
    anchor_by_guid = {}
    imported = 0
    placed = 0
    unresolved = 0

    for placement in placements:
        asset_path = placement["asset_path"]

        if asset_paths.is_full_prefab_path(asset_path):
            guid = prefab_index.get(asset_paths.prefab_asset_stem(asset_path))
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
            expected_name = asset_paths.expected_mesh_name(asset_path)
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
    clip_paths = discovery.clip_paths_from_controller(db, controller_file)
    return _apply_clip_paths(context, clip_paths, options)
