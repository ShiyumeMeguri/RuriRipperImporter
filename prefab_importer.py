"""Top-level import orchestration: prefab (full model) and standalone mesh."""

from __future__ import annotations

import math
import os
import time

import bpy
from mathutils import Matrix

# `clip_paths` is aliased to clip_repair and `prefab` to prefab_scan: both names
# are already taken here by local variables (a list of .anim paths, a parsed
# prefab UnityFile) that appear in almost every function below.
try:
    from . import (armature_builder, coordinate, animation_builder,
                   material_builder, mesh_builder)
    from .RuriRipperPyBridge.unity import (asset_db, clip_curves,
                                      clip_paths as clip_repair, discovery,
                                      mesh_decoder, prefab as prefab_scan, skinning)
except ImportError:  # standalone (non-package) testing
    import armature_builder
    import coordinate
    import animation_builder
    import material_builder
    import mesh_builder
    from RuriRipperPyBridge.unity import (asset_db, clip_curves,
                                     clip_paths as clip_repair, discovery,
                                     mesh_decoder, prefab as prefab_scan, skinning)

UNITY_TAG = "unity_tag"

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
    "import_empties": False,
    # Which game this import is of (the upstream GameType member). The host knows it;
    # the importer only stamps what it is told, so nothing here names a game.
    "source_game": "",
}


class ImportReport:
    def __init__(self):
        self.armature = None
        self.mesh_objects = []
        self.cameras = []
        self.lights = []
        self.root_objects = []
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
                f"cameras={len(self.cameras)} lights={len(self.lights)} "
                f"materials={self.materials} textures={self.textures} "
                f"actions={self.actions} lod_skipped={self.skipped_lod} "
                f"shadow_skipped={self.skipped_shadow} "
                f"inactive_skipped={self.skipped_inactive} time={self.seconds:.2f}s")


def resolve_options(options):
    merged = dict(DEFAULT_OPTIONS)
    if options:
        merged.update(options)
    return merged


def import_prefab(context, prefab_path, options=None):
    options = resolve_options(options)
    assets_dir = asset_db.find_assets_dir(prefab_path)
    db = asset_db.AssetDatabase(os.path.dirname(prefab_path), assets_dir)
    prefab = db.load_file(prefab_path)
    arm_name = os.path.splitext(os.path.basename(prefab_path))[0]
    clip_files, clip_warnings = _gather_clip_files_disk(db, prefab, prefab_path, assets_dir)
    report = _import_prefab_core(context, db, prefab, arm_name, clip_files, options, True)
    report.warnings.extend(clip_warnings)
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


def import_prefab_from_db(context, db, prefab_file, options=None, name=None,
                          top_level=True):
    """Bridge-mode sibling of import_prefab: db/prefab_file are already resolved
    from an in-memory closure (pythonnet bridge) instead of a disk path -- same
    build body as import_prefab (via _import_prefab_core), only the front
    matter differs. Unlike the disk path, animation clips are NOT eagerly
    built here: a character's dependency closure can hold dozens of clips at
    ~100MB each, and building actions for all of them synchronously is what
    used to hang Blender on import. Clips are only DISCOVERED (cheap -- see
    discovery.discover_clip_refs) and reported on report.available_clips; the
    caller builds actions later, only for whichever clips the user actually
    picks in the animation browser, via build_selected_animations.

    ``top_level`` is False when a caller re-parents the result under its own
    world transform (scene placement): the pieces then convert with pure C and
    that caller applies the once-only root yaw at the placement instead."""
    options = resolve_options(options)
    arm_name = name or discovery.prefab_display_name(prefab_file)
    report = _import_prefab_core(context, db, prefab_file, arm_name, [], options, top_level)
    if options["import_animations"]:
        report.available_clips = discovery.discover_clip_refs(db, prefab_file)
    return report


def _needs_armature(prefab):
    """Whether this prefab's transform hierarchy is worth an armature: something
    skins to it, or the hierarchy IS the asset. A rig-only prefab (a character
    assembly's skeleton row -- Koikatu's p_cf_body_bone is 580 transforms and zero
    renderers) carries no SkinnedMeshRenderer yet is nothing BUT a skeleton, so
    keying on skinning alone silently produced no rig and the assembly built
    nothing. A static prop -- MeshRenderer/MeshFilter and no skinning -- still
    gets none."""
    if prefab.all("SkinnedMeshRenderer"):
        return True
    return not prefab.all("MeshRenderer") and not prefab.all("MeshFilter")


def _root_animator(prefab, maps):
    """The prefab's OWN Animator: the one on a hierarchy ROOT GameObject (a prefab
    routinely carries more -- weapon sub-rigs), else the first in document order."""
    root_go_ids = {node.go_id for node in maps.get("roots") or ()}
    first = None
    for doc in prefab.documents:
        if doc.class_name != "Animator":
            continue
        if first is None:
            first = doc
        go_ref = doc.data.get("m_GameObject")
        if isinstance(go_ref, dict) and go_ref.get("fileID") in root_go_ids:
            return doc
    return first


def _stamp_prefab_avatar(db, prefab, arm_obj, maps, warnings):
    """Bake the rig's whole Avatar document onto the armature (armature_builder.
    UNITY_AVATAR_PROP) -- the humanoid retarget contract. Resolved by IDENTITY:
    the root Animator's m_Avatar guid, never a name match. A prefab with no
    animator or no avatar reference simply carries no stamp (a static prop, a
    generic rig dump); a reference that fails to resolve is a real gap and says
    so."""
    animator = _root_animator(prefab, maps)
    if animator is None:
        return
    avatar_ref = animator.data.get("m_Avatar")
    guid = avatar_ref.get("guid") if isinstance(avatar_ref, dict) else None
    if not guid:
        return
    avatar_file = db.load_guid(str(guid))
    avatar_doc = avatar_file.first("Avatar") if avatar_file is not None else None
    if avatar_doc is None:
        warnings.append(f"Animator references Avatar {guid} but the closure does not carry it "
                        f"-- muscle-encoded clips will not solve on this rig.")
        return
    armature_builder.stamp_avatar(arm_obj, avatar_doc.data)
    _sort_bones_into_collections(arm_obj, warnings)


def _sort_bones_into_collections(arm_obj, warnings):
    """Group the rig's bones by what its avatar says they are. Humanoid only --
    a generic avatar states nothing about its bones, and this add-on does not
    guess from names."""
    avatar_json = armature_builder.read_avatar_json(arm_obj)
    if not avatar_json:
        return
    try:
        from .RuriRipperPyBridge.runtime import pythonnet_bridge
        slots = pythonnet_bridge.describe_humanoid_bones(avatar_json)
    except Exception as exc:
        warnings.append(f"bone collections: {type(exc).__name__}: {exc} -- rig left unsorted")
        return
    armature_builder.build_bone_collections(arm_obj, slots)


def _solve_humanoid_curves(clip, avatar_json, path_to_bone, warnings, clip_name):
    """Resolve a clip's muscle encoding (if any) into per-bone transform curves,
    in place, against the target armature's stamped avatar: the clip's float
    channels cross to the C# Animator (pythonnet_bridge.solve_humanoid_clip) and
    the solved curves fold back in with replace-by-path semantics. Which clips
    ARE muscle-encoded is solely the solver's knowledge -- a generic clip comes
    back untouched (None); a muscle clip with no/unsuitable stamp warns loudly
    and imports without its muscle-encoded body motion.

    Call AFTER repair_hashed_clip_paths: the solved curves arrive on the
    avatar's own transform paths and are re-anchored here through the same
    suffix-CRC join, so the replace-by-path merge compares both sides in the
    ARMATURE's canonical path space -- bone identity, not string luck."""
    if not any(channel.attribute and not channel.attribute.startswith("blendShape.")
               for channel in clip.floats):
        return
    try:
        from .RuriRipperPyBridge.runtime import pythonnet_bridge
    except ImportError:
        from RuriRipperPyBridge.runtime import pythonnet_bridge
    meta_json, payload = clip_curves.humanoid_float_blob(clip)
    try:
        result = pythonnet_bridge.solve_humanoid_clip(avatar_json, meta_json, payload)
    except Exception as exc:
        warnings.append(f"{clip_name}: humanoid solve failed -- {exc}")
        return
    if result is None:
        return
    solved_meta, solved_bytes, consumed, _count = result
    solved = clip_curves.ClipCurves.from_blob(solved_meta, solved_bytes)
    _repaired, unmatched = clip_repair.repair_hashed_clip_paths(solved, path_to_bone)
    if unmatched:
        warnings.append(f"{clip_name}: {unmatched} solved humanoid bone path(s) matched no "
                        f"bone of the target skeleton")
    clip_curves.merge_solved(clip, solved, consumed)


def _load_clip(clip_path):
    """The one disk clip loader: run the raw-text parser
    (clip_curves.ClipCurves.from_yaml_text -- validated bitwise-identical to
    the generic parser on real 82MB clips at ~3x the speed) on the .anim file.
    Raises OSError/ValueError on an unreadable file or any structural surprise
    -- callers report and skip; nothing here re-parses through a second path."""
    with open(clip_path, "r", encoding="utf-8", errors="ignore") as handle:
        return clip_curves.ClipCurves.from_yaml_text(handle.read())


def _gather_clip_files_disk(db, prefab, prefab_path, assets_dir):
    """Disk-mode clip gathering: resolve _gather_clip_paths's paths with the
    one clip parser. Returns (clip_files, warnings); a file that cannot be
    parsed is skipped with an explicit warning, never re-parsed via the
    generic YAML parser."""
    clip_files = []
    warnings = []
    for clip_path in _gather_clip_paths(db, prefab, prefab_path, assets_dir):
        try:
            clip_files.append(_load_clip(clip_path))
        except (OSError, ValueError) as exc:
            warnings.append(f"clip {os.path.basename(clip_path)} skipped: {exc}")
    return clip_files, warnings


def build_selected_animations(db, arm_obj, maps, path_to_meshobjects, guids, options,
                              display_names=None, activate=False):
    """Build Blender actions for exactly the given clip guids -- the checked
    subset from the animation browser. This is the only place that now pays
    the full parse + keyframe-insertion cost per clip; it's deferred until the
    user explicitly picks a clip rather than paying it for every clip in a
    character's closure up front.

    Before building, every clip gets clip_repair.repair_hashed_clip_paths against the
    target armature: clips exported without their rig in scope carry
    "path_0x<CRC32>_" placeholder paths, and the armature's own bone paths
    are the hash preimages -- so a standalone-imported clip binds to the
    user's selected skeleton exactly when the hashes match. A muscle-encoded
    clip then solves against the armature's own stamped avatar
    (_solve_humanoid_curves) -- any .anim from any project, no export scope
    involved.

    Assignment: importing ONE clip always puts it on the armature -- picking a single
    animation IS the request to see it, and leaving it as a loose datablock for the user
    to hunt down in the Action editor is not a neutral default. A multi-clip batch keeps
    the don't-clobber guard: which of N would be arbitrary, so it only fills an armature
    that has no action yet. Returns (built, warnings).

    ``activate`` overrides that guard: the count alone cannot tell "N clips the user
    ticked one by one" apart from "ONE thing the user picked that happens to be stored
    as N clips" (a game whose catalog row expands to a family of camera/intensity
    variants). Only the caller knows which it is, and for the latter staying silent is
    the wrong default -- the user asked to see this animation.

    ``display_names`` ({guid: name}) renames the products and every warning about them.
    A clip's m_Name is whatever the game's own controller state was called, which for
    some games is unreadable; a caller holding the catalog label passes it here.

    Returns (built, warnings, actions) where ``actions`` is {guid: (action, slot)} -- a
    caller that has more of THIS clip to write (a face restated into the destination's own
    expressions, say) adds it to that clip's own action. An object plays one action, so a
    second action beside it would simply replace this one."""
    built = 0
    first = None
    warnings = []
    actions = {}
    guids = list(guids)
    assign_always = activate or len(guids) == 1
    has_action = arm_obj.animation_data is not None and arm_obj.animation_data.action is not None
    path_to_bone = maps.get("path_to_bone") or {}
    avatar_json = armature_builder.read_avatar_json(arm_obj)
    for guid in guids:
        # The single clip surface: the bridge blob (zero-parse) or the disk
        # raw-text parser (asset_db.clip_curves) -- same API, no YAML fallback.
        # A malformed .anim raises there; caught per-guid so one bad clip in a
        # multi-clip batch doesn't abort the rest.
        try:
            clip = db.clip_curves(guid)
        except ValueError as exc:
            warnings.append(f"clip {guid}: {exc} -- skipped")
            continue
        if clip is None:
            warnings.append(f"clip {guid}: no clip data in the source closure "
                            f"(missing blob or .anim) -- skipped")
            continue
        clip_name = (display_names or {}).get(guid) or clip.name or guid
        repaired, unmatched = clip_repair.repair_hashed_clip_paths(clip, path_to_bone)
        if unmatched:
            warnings.append(f"{clip_name}: {unmatched} hashed curve "
                            f"path(s) matched no bone of '{arm_obj.name}' (skipped)")
        _solve_humanoid_curves(clip, avatar_json, path_to_bone, warnings, clip_name)
        action, slot, n_frames = animation_builder.build_action(
            clip, arm_obj, maps, path_to_meshobjects, options, display_name=clip_name)

        built += 1
        actions[guid] = (action, slot)
        if first is None:
            first = (action, slot)
    if first is not None and (assign_always or not has_action):
        animation_builder.adopt_action(arm_obj, first[0], first[1])
    return built, warnings, actions


def _import_prefab_core(context, db, prefab, arm_name, clip_files, options, top_level):
    """Shared build body for import_prefab / import_prefab_from_db: armature,
    LOD0 skinned + static meshes, materials, and animation actions from an
    already-resolved db + prefab UnityFile + pre-gathered clip UnityFiles.

    ``top_level`` applies the once-only root yaw R to this prefab's own top-level
    objects (the armature object, and each unparented static mesh). A caller that
    re-parents the whole result under its own placement passes False and applies
    R at that placement instead, so R is never doubled."""
    report = ImportReport()
    start = time.time()

    arm_obj = None
    if options["import_skeleton"] and _needs_armature(prefab):
        arm_obj, maps = armature_builder.build_armature(context, prefab, arm_name)
        if top_level:
            arm_obj.matrix_world = coordinate.root_matrix()
        report.armature = arm_obj
        report.bones = len(arm_obj.data.bones)
        report.root_objects.append(arm_obj)
        _stamp_prefab_avatar(db, prefab, arm_obj, maps, report.warnings)
        armature_builder.stamp_game(arm_obj, options.get("source_game"))
    else:
        try:
            from . import hierarchy
        except ImportError:
            import hierarchy
        nodes, roots = hierarchy.build_hierarchy(prefab)
        maps = {"nodes": nodes, "roots": roots,
                "file_id_to_bone": {}, "path_to_bone": {},
                "file_id_to_world": hierarchy.world_matrices(nodes)}

    nodes = maps["nodes"]
    go_to_node = {n.go_id: n for n in nodes.values()}

    mat_builder = material_builder.MaterialBuilder(db, options) if options["import_materials"] else None

    path_to_meshobjects = {}
    content = {}

    stats = prefab_scan.SkipStats()
    for renderer in prefab_scan.iter_renderers(prefab, go_to_node, options, stats):
        if renderer.is_skinned and arm_obj is not None:
            obj = _import_skinned(context, db, renderer, arm_obj, maps,
                                  mat_builder, options, report)
        else:
            obj = _import_static(context, db, renderer, mat_builder, options, report,
                                 top_level, place=arm_obj is not None)
        if obj is None:
            continue
        report.mesh_objects.append(obj)
        if renderer.node is not None:
            path_to_meshobjects.setdefault(renderer.node.path, []).append(obj)
            if arm_obj is None:
                content[renderer.node.file_id] = obj
                if renderer.disabled:
                    obj.hide_viewport = True
                    obj.hide_render = True
    report.skipped_lod += stats.lod
    report.skipped_shadow += stats.shadow
    report.skipped_inactive += stats.inactive

    if arm_obj is None:
        for camera in prefab_scan.iter_cameras(prefab, go_to_node, options):
            obj = _camera_object(camera)
            report.cameras.append(obj)
            content.setdefault(camera.node.file_id, obj)
        for light in prefab_scan.iter_lights(prefab, go_to_node, options):
            obj = _light_object(light)
            report.lights.append(obj)
            content.setdefault(light.node.file_id, obj)
        _build_object_tree(context, nodes, maps["roots"], content, options, top_level, report)
    else:
        report.root_objects.extend(o for o in report.mesh_objects if o.parent is None)

    if mat_builder is not None:
        report.materials = len(mat_builder._cache)
        report.textures = len(mat_builder._image_cache)

    # Animations: every gathered clip (source differs disk vs. bridge mode) as actions.
    if options["import_animations"] and arm_obj is not None:
        actions = []
        avatar_json = armature_builder.read_avatar_json(arm_obj)
        clip_path_to_bone = maps.get("path_to_bone") or {}
        for clip in clip_files:
            _solve_humanoid_curves(clip, avatar_json, clip_path_to_bone,
                                   report.warnings, clip.name)
            action, slot, _frames = animation_builder.build_action(
                clip, arm_obj, maps, path_to_meshobjects, options)
            actions.append((action, slot))
        report.actions = len(actions)
        if actions:
            first_action, first_slot = actions[0]
            animation_builder.adopt_action(arm_obj, first_action, first_slot,
                                           scene=context.scene)

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


_AIM = Matrix.Rotation(math.pi / 2.0, 4, "X")
_AIM_INV = _AIM.inverted()


def _camera_object(camera):
    data = bpy.data.cameras.new(camera.name)
    data.clip_start = camera.near
    data.clip_end = camera.far
    if camera.orthographic:
        data.type = "ORTHO"
        data.ortho_scale = camera.orthographic_size * 2.0
    else:
        data.sensor_fit = "VERTICAL"
        data.angle_y = math.radians(camera.fov)
    obj = bpy.data.objects.new(camera.name, data)
    obj.hide_viewport = obj.hide_render = camera.disabled
    obj[UNITY_TAG] = camera.tag
    return obj


def _light_object(light):
    kind = {0: "SPOT", 1: "SUN", 2: "POINT"}.get(light.type, "AREA")
    data = bpy.data.lights.new(light.name, kind)
    data.color = light.color
    data.energy = light.intensity
    if kind == "SPOT":
        data.spot_size = math.radians(light.spot_angle)
        if light.spot_angle > 0.0:
            data.spot_blend = 1.0 - min(light.inner_spot_angle / light.spot_angle, 1.0)
    elif kind == "AREA":
        data.shape = "RECTANGLE"
        data.size, data.size_y = light.area_size
    if kind != "SUN":
        data.use_custom_distance = True
        data.cutoff_distance = light.range
    obj = bpy.data.objects.new(light.name, data)
    obj.hide_viewport = obj.hide_render = light.disabled
    return obj


def _build_object_tree(context, nodes, roots, content, options, top_level, report):
    keep = set(content)
    if options["import_empties"]:
        keep.update(nodes)
    else:
        for file_id in list(keep):
            node = nodes[file_id].parent
            while node is not None and node.file_id not in keep:
                keep.add(node.file_id)
                node = node.parent

    def build(node, parent, parent_aimed):
        if node.file_id not in keep:
            return
        obj = content.get(node.file_id)
        if obj is None:
            obj = bpy.data.objects.new(node.name, None)
        if obj.name not in context.collection.objects:
            context.collection.objects.link(obj)
        aimed = obj.type in ("CAMERA", "LIGHT")
        local = (coordinate.convert_root_matrix(node.local)
                 if parent is None and top_level else coordinate.convert_matrix(node.local))
        if parent_aimed:
            local = _AIM_INV @ local
        if aimed:
            local = local @ _AIM
        obj.parent = parent
        obj.matrix_basis = local
        if not node.active:
            obj.hide_viewport = obj.hide_render = True
        if parent is None:
            report.root_objects.append(obj)
        for child in node.children:
            build(child, obj, aimed)

    for root in roots:
        build(root, None, False)


def _import_static(context, db, renderer, mat_builder, options, report, top_level, place=True):
    decoded = _decode_renderer_mesh(db, renderer, report)
    if decoded is None:
        return None
    materials = _build_materials(renderer, mat_builder)
    obj = mesh_builder.build_mesh_object(
        context, decoded, renderer.name, None, [], {}, materials, options)
    if place:
        convert = coordinate.convert_root_matrix if top_level else coordinate.convert_matrix
        obj.matrix_world = convert(renderer.node.world)
    return obj


def import_mesh(context, mesh_path, options=None):
    """Import a standalone Unity Mesh .asset as a single static object."""
    options = resolve_options(options)
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


def import_mesh_from_db(context, db, mesh_file, options=None, materials=None, skeleton=None):
    """Bridge-mode sibling of import_mesh: a standalone mesh.

    A mesh reached on its own -- a lone Mesh CAB, or one named sub-object of a
    multi-object FBX -- carries no MeshRenderer to read a material list from, so
    `materials` is whatever the caller could resolve for it by other means (a
    game's scene importer resolves them from the placement's own material
    hashes). Empty/None imports the mesh flat.

    `skeleton` is an armature_builder.SkeletonBinder when an assembler is building
    ONE rig from several shared-skeleton part meshes (an npc's body/hair/tail):
    the binder builds or extends that rig from this mesh's own bind poses and
    binds every vertex to it. Without one the mesh imports unskinned -- the plain
    browser semantics for a loose mesh."""
    options = resolve_options(options)
    report = ImportReport()
    start = time.time()
    mesh_doc = mesh_file.first("Mesh")
    if mesh_doc is None:
        report.warnings.append("No Mesh object in file")
        return report
    # Same resolution the prefab path uses -- the closure's raw geometry blob
    # first, the YAML document only when this database carries no blob for it.
    # A second decode call here is what let a standalone mesh silently miss the
    # fast path every prefab mesh already took.
    loaded = prefab_scan.load_mesh(db, {"guid": mesh_file.path},
                                   str(mesh_doc.data.get("m_Name") or "Mesh"))
    if not loaded.ok:
        report.warnings.append(loaded.detail or f"'{loaded.name}' decoded to zero vertices.")
        return report
    decoded = loaded.decoded
    name = loaded.name or "Mesh"
    smr_bones, file_id_to_bone = (skeleton.bind(context, decoded)
                                  if skeleton is not None else (None, None))
    if smr_bones is not None:
        # build_mesh_object attaches the armature modifier and parents the mesh
        # onto the rig when given a bone list.
        obj = mesh_builder.build_mesh_object(context, decoded, name, skeleton.armature,
                                             smr_bones, file_id_to_bone, materials or [], options)
    else:
        obj = mesh_builder.build_mesh_object(context, decoded, name, None, [], {},
                                             materials or [], options)
    report.mesh_objects.append(obj)
    report.seconds = time.time() - start
    return report


def import_avatar_from_db(context, db, avatar_file, options=None, name=None):
    """Bridge-mode standalone Avatar import: a Blender armature built straight from the avatar's
    OWN embedded skeleton (armature_builder.build_armature_from_avatar), independent of any
    accompanying rig FBX/prefab."""
    options = resolve_options(options)
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
    armature_builder.stamp_game(arm_obj, options.get("source_game"))
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
    from mathutils import Matrix

    try:
        from . import armature_builder
    except ImportError:
        import armature_builder

    stamped = armature_builder.read_rig(arm_obj)
    if not stamped:
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


def armature_of(obj):
    """The armature ``obj`` stands for -- the ONE rule every flow in this add-on
    resolves a rig by, so "which skeleton did I pick" never means two things.

    An Armature MODIFIER pointing at a rig IS the binding, so selecting a mesh
    that carries one is exactly as good as selecting the skeleton itself: that
    is what the user is looking at and clicking on, and requiring the rig object
    to be picked out of the outliner instead is a demand with no meaning behind
    it. An armature PARENT is the structural fallback, for something hung off a
    bone with no modifier of its own.

    Deliberately not gated on obj.type == "MESH": a curve, a lattice or any
    other deformable carries the same modifier and means the same thing by it."""
    if obj is None:
        return None
    if obj.type == "ARMATURE":
        return obj
    for modifier in getattr(obj, "modifiers", ()):
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object
    parent = obj.parent
    return parent if parent is not None and parent.type == "ARMATURE" else None


def find_target_armature(context):
    """The armature a clip import should drive: the active object's rig (an
    armature, or the armature a selected mesh is bound to), else the single
    rig the selection resolves to, else the scene's single armature. None when
    nothing resolves or the choice is ambiguous -- the caller words the error."""
    active = armature_of(getattr(context, "active_object", None))
    if active is not None:
        return active
    selected = {armature_of(obj) for obj in getattr(context, "selected_objects", ())}
    selected.discard(None)
    if len(selected) == 1:
        return next(iter(selected))
    if selected:
        return None
    scene_armatures = [obj for obj in context.scene.objects if obj.type == "ARMATURE"]
    return scene_armatures[0] if len(scene_armatures) == 1 else None


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
    """Build actions from disk clip paths (any project's .anim) onto the active
    armature: hashed/foreign curve paths re-anchor through the suffix-CRC join,
    and a muscle-encoded clip solves against the armature's stamped avatar --
    the same treatment the bridge flow gives its clips."""
    report = ImportReport()
    start = time.time()
    arm = find_target_armature(context)
    if arm is None:
        report.warnings.append("No armature resolves from the selection or scene to apply "
                               "clips to. Import the model first, or select the skeleton "
                               "(or a mesh bound to it).")
        report.seconds = time.time() - start
        return report
    report.armature = arm
    report.bones = len(arm.data.bones)
    # The stamped rig identity is the truth when present (Unity-space rest,
    # original paths); deriving from live bones is for armatures built by
    # something else entirely.
    maps = maps_from_stamped_armature(arm) or _maps_from_armature(arm)
    path_to_bone = maps.get("path_to_bone") or {}
    avatar_json = armature_builder.read_avatar_json(arm)
    first = None
    for clip_path in clip_paths:
        try:
            clip = _load_clip(clip_path)
        except (OSError, ValueError) as exc:
            report.warnings.append(f"clip {os.path.basename(clip_path)} skipped: {exc}")
            continue
        clip_name = clip.name or os.path.basename(clip_path)
        _repaired, unmatched = clip_repair.repair_hashed_clip_paths(clip, path_to_bone)
        if unmatched:
            report.warnings.append(f"{clip_name}: {unmatched} hashed curve path(s) matched "
                                   f"no bone of '{arm.name}' (skipped)")
        _solve_humanoid_curves(clip, avatar_json, path_to_bone, report.warnings, clip_name)
        action, slot, _frames = animation_builder.build_action(
            clip, arm, maps, None, resolve_options(options))
        report.actions += 1
        if first is None:
            first = (action, slot)
    if first is not None:
        animation_builder.adopt_action(arm, first[0], first[1], scene=context.scene)
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
