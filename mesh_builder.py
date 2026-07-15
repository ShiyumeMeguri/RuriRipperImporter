"""Turn a decoded Unity mesh into a Blender object: geometry, UVs, vertex
colours, skin weights, an armature modifier, blendshapes and material slots."""

from __future__ import annotations

import numpy as np

try:
    from . import coordinate
except ImportError:
    import coordinate

import bpy


def build_mesh_object(context, decoded, name, armature_obj, smr_bones,
                      file_id_to_bone, materials, options):
    """Create and return a Blender mesh object for one SkinnedMeshRenderer.

    decoded         : DecodedMesh (Unity coordinates)
    smr_bones       : ordered list of bone Transform fileIDs from m_Bones
    file_id_to_bone : {transform fileID -> armature bone name}
    materials       : list of bpy.types.Material in submesh order (may contain None)
    """
    mesh = bpy.data.meshes.new(name)

    positions = coordinate.convert_points(decoded.positions)
    triangles = coordinate.reverse_winding(decoded.triangles)

    n_verts = len(positions)
    n_tris = len(triangles)

    mesh.vertices.add(n_verts)
    mesh.vertices.foreach_set("co", positions.reshape(-1))

    mesh.loops.add(n_tris * 3)
    mesh.polygons.add(n_tris)
    loop_verts = triangles.reshape(-1).astype(np.int32)
    mesh.loops.foreach_set("vertex_index", loop_verts)
    loop_starts = (np.arange(n_tris, dtype=np.int32) * 3)
    mesh.polygons.foreach_set("loop_start", loop_starts)
    mesh.polygons.foreach_set("loop_total", np.full(n_tris, 3, dtype=np.int32))
    if decoded.tri_material is not None and len(decoded.tri_material) == n_tris:
        mesh.polygons.foreach_set("material_index", decoded.tri_material.astype(np.int32))

    mesh.update(calc_edges=True)

    # UV layers (one per stored Unity TexCoord channel).
    for layer_index in sorted(decoded.uvs):
        uv = decoded.uvs[layer_index]
        uv_layer = mesh.uv_layers.new(name=f"UV{layer_index}" if layer_index else "UVMap")
        per_loop = uv[loop_verts]
        if options.get("flip_v", False):
            per_loop = per_loop.copy()
            per_loop[:, 1] = 1.0 - per_loop[:, 1]
        uv_layer.data.foreach_set("uv", per_loop.reshape(-1))

    # Vertex colours.
    if decoded.colors is not None and options.get("import_colors", True):
        color_attr = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="CORNER")
        color_attr.data.foreach_set("color", decoded.colors[loop_verts].reshape(-1))

    # Custom split normals if the stored normals decoded sanely.
    if decoded.normals is not None and options.get("import_normals", True):
        normals = coordinate.convert_points(decoded.normals)
        try:
            mesh.normals_split_custom_set_from_vertices(normals.tolist())
        except (RuntimeError, ValueError):
            pass

    for poly in mesh.polygons:
        poly.use_smooth = True

    mesh.validate(clean_customdata=False)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)

    # Material slots.
    for mat in materials:
        obj.data.materials.append(mat)

    # Skinning.
    if (decoded.bone_weights is not None and decoded.bone_indices is not None
            and smr_bones and armature_obj is not None):
        _apply_skin(obj, decoded, smr_bones, file_id_to_bone)
        modifier = obj.modifiers.new("Armature", "ARMATURE")
        modifier.object = armature_obj
        modifier.use_vertex_groups = True
        obj.parent = armature_obj

    # Blendshapes -> shape keys.
    if decoded.blendshapes and options.get("import_blendshapes", True):
        _apply_blendshapes(obj, decoded)

    return obj


def _apply_skin(obj, decoded, smr_bones, file_id_to_bone):
    """Create vertex groups and assign skin weights."""
    # Map each m_Bones slot to an armature bone name.
    slot_to_group = {}
    groups = {}
    for slot, bone_ref in enumerate(smr_bones):
        file_id = bone_ref.get("fileID") if isinstance(bone_ref, dict) else None
        bone_name = file_id_to_bone.get(file_id)
        if not bone_name:
            continue
        group = groups.get(bone_name)
        if group is None:
            group = obj.vertex_groups.new(name=bone_name)
            groups[bone_name] = group
        slot_to_group[slot] = group

    indices = decoded.bone_indices
    weights = decoded.bone_weights
    n_verts, n_inf = indices.shape

    # Accumulate (vertex, weight) per group, then add in bulk per group.
    # Weights for the same bone reached through different influence slots are
    # summed for the same vertex.
    per_group = {}
    for j in range(n_inf):
        col_idx = indices[:, j]
        col_w = weights[:, j]
        nonzero = np.nonzero(col_w > 1e-6)[0]
        for v in nonzero:
            slot = int(col_idx[v])
            group = slot_to_group.get(slot)
            if group is None:
                continue
            bucket = per_group.setdefault(group, {})
            bucket[int(v)] = bucket.get(int(v), 0.0) + float(col_w[v])

    for group, bucket in per_group.items():
        # Group vertices by identical (rounded) weight to minimise add() calls.
        by_weight = {}
        for v, w in bucket.items():
            by_weight.setdefault(round(w, 6), []).append(v)
        for w, verts in by_weight.items():
            group.add(verts, w, "REPLACE")


def _apply_blendshapes(obj, decoded):
    mesh = obj.data
    basis = obj.shape_key_add(name="Basis", from_mix=False)
    base_co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", base_co)
    base_co = base_co.reshape(-1, 3)

    for shape in decoded.blendshapes:
        frames = shape["frames"]
        for fi, frame in enumerate(frames):
            suffix = "" if len(frames) == 1 else f"_{fi}"
            key = obj.shape_key_add(name=shape["name"] + suffix, from_mix=False)
            # Object.shape_key_add() defaults a new key's `.value` to 1.0 (confirmed against the
            # actual Blender API, not assumed) -- meaning every blendshape this loop creates
            # applies at full strength simultaneously unless explicitly zeroed, which is what
            # made every imported character's mesh look distorted: Unity's SkinnedMeshRenderer
            # starts every blend shape weight at 0 unless a clip specifically drives it, so the
            # correct rest pose here is every non-Basis key OFF, exactly matching that default.
            key.value = 0.0
            co = base_co.copy()
            for index, delta_v, _delta_n in frame["deltas"]:
                # Convert the Unity-space delta into Blender space (swap Y/Z).
                co[index] += np.array((delta_v[0], delta_v[2], delta_v[1]), dtype=np.float32)
            key.data.foreach_set("co", co.reshape(-1))
