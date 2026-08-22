"""Turn a decoded Unity mesh into a Blender object: geometry, UVs, vertex
colours, skin weights, an armature modifier, blendshapes and material slots."""

from __future__ import annotations

import numpy as np

try:
    from . import coordinate, derived_state
except ImportError:
    import coordinate
    import derived_state

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

    # Vertex colours. A mesh that carries no COLOR channel still feeds one to any
    # shader that declares COLOR0: the GPU supplies the unbound stream's default,
    # which Unity binds as opaque white. Leaving the attribute out instead makes
    # Blender's Attribute node read zero, and every shader that multiplies by
    # vertex colour collapses to black -- measured on this game's own display
    # stage, whose backdrop is `_TintColor(0.917) * COLOR0` and renders white in
    # game while every one of its meshes reports `Color dimension=0`.
    if options.get("import_colors", True):
        color_attr = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="CORNER")
        if decoded.colors is not None:
            color_attr.data.foreach_set("color", decoded.colors[loop_verts].reshape(-1))
        else:
            color_attr.data.foreach_set("color", np.ones(len(mesh.loops) * 4, dtype=np.float32))

    # Custom split normals if the stored normals decoded sanely. Blender keeps
    # these in an INT16_2D corner attribute, so a round trip is lossy by ~0.15
    # degrees worst case (measured on real character meshes: 0.004 deg mean) --
    # that is the storage format, not a decode error.
    if decoded.normals is not None and options.get("import_normals", True):
        normals = coordinate.convert_points(decoded.normals)
        try:
            mesh.normals_split_custom_set_from_vertices(normals.tolist())
        except (RuntimeError, ValueError) as exc:
            # Never silent: without custom normals the mesh falls back to
            # Blender's own averaged normals, which on these models differ by
            # up to ~45 degrees (measured) and read as a shading bug with no
            # visible cause.
            print(f"[RuriRipper] {name}: custom split normals rejected "
                  f"({type(exc).__name__}: {exc}) -- Blender will average its own "
                  f"instead, which will not match the game's shading.")

    mesh.polygons.foreach_set("use_smooth", np.ones(n_tris, dtype=bool))

    mesh.validate(clean_customdata=False)
    mesh.update()

    _bake_tangents(mesh, decoded)

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

    # 造完报一声。这里是**每一条**导入路径造网格的必经之处,所以派生态(顶点腿、
    # 后处理、兑现节点)不需要任何入口记得手动收尾 —— 见 derived_state 的开篇。
    derived_state.announce(obj)
    return obj


def _bake_tangents(mesh, decoded):
    """切线写进 corner 域属性,着色图直读 —— 属性**恒存在**,免去"缺属性静默读到零向量"
    (实锤:半边脸紫)。有游戏切线用游戏的(w 按 Unity 原值:换轴与图内 b2u 两次反射抵消);
    没有就退回 Blender 自算的 UV 切线,并翻一次手性(只经 b2u 一次反射)。

    corner→顶点的对照**从建好的网格上现读**,不能沿用建面时那份:`mesh.validate()` 会
    丢掉退化面/重复面,活下来的 corner 少于当初写进去的,拿旧数组去 foreach_set 长度对不上,
    Blender 只回一句 "internal error setting the array" 就把整趟导入打断
    (EXILIUM 的场景网格实测会踩)。"""
    tan_attr = mesh.attributes.new(name="ruri_tangent", type="FLOAT_VECTOR", domain="CORNER")
    sign_attr = mesh.attributes.new(name="ruri_tangent_sign", type="FLOAT", domain="CORNER")
    loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_verts)

    if decoded.tangents is not None:
        per_vertex = coordinate.convert_points(decoded.tangents[:, :3])
        tan_attr.data.foreach_set("vector", np.ascontiguousarray(
            per_vertex[loop_verts], dtype=np.float32).reshape(-1))
        sign_attr.data.foreach_set("value", np.ascontiguousarray(
            decoded.tangents[loop_verts, 3], dtype=np.float32))
        return

    try:
        mesh.calc_tangents()
    except RuntimeError as exc:
        print(f"[RuriRipper] {mesh.name}: 无游戏切线且 UV 切线算不出({exc})——"
              f"法线贴图与各向异性会错,切线属性留零。")
        return
    n_loops = len(mesh.loops)
    tangents = np.empty(n_loops * 3, dtype=np.float32)
    signs = np.empty(n_loops, dtype=np.float32)
    mesh.loops.foreach_get("tangent", tangents)
    mesh.loops.foreach_get("bitangent_sign", signs)
    tan_attr.data.foreach_set("vector", tangents)
    sign_attr.data.foreach_set("value", -signs)


def _apply_skin(obj, decoded, smr_bones, file_id_to_bone):
    """Create vertex groups and assign skin weights through bmesh's deform
    layer -- one C-level write per (vertex, group) entry instead of one
    VertexGroup.add() call per distinct weight VALUE (continuous float
    weights make those buckets mostly singletons, so the add() call count
    was effectively per-entry; measured on the real Pelica set: 2.6x faster
    overall, 5.3x on the largest mesh). The from_mesh/to_mesh round-trip is
    lossless for everything this importer writes -- UVs, corner colors,
    material indices, smooth flags AND custom split normals all verified
    0.0-delta on Blender 5.1 real data."""
    indices = decoded.bone_indices
    weights = decoded.bone_weights
    n_verts, n_inf = indices.shape

    # Map each WEIGHTED m_Bones slot to a vertex-group index. Unity lists the whole
    # skeleton in m_Bones, so binding every slot leaves hundreds of all-zero groups on
    # every mesh, and each one is a real attribute that Join Geometry has to merge.
    n_slots = len(smr_bones)
    valid = indices < n_slots
    weighted_slots = np.unique(indices[valid & (weights > 1e-6)])
    slot_to_group_index = np.full(n_slots, -1, dtype=np.int64)
    group_index_by_bone = {}
    for slot in weighted_slots.tolist():
        bone_ref = smr_bones[slot]
        file_id = bone_ref.get("fileID") if isinstance(bone_ref, dict) else None
        bone_name = file_id_to_bone.get(file_id)
        if not bone_name:
            continue
        group_index = group_index_by_bone.get(bone_name)
        if group_index is None:
            group_index = obj.vertex_groups.new(name=bone_name).index
            group_index_by_bone[bone_name] = group_index
        slot_to_group_index[slot] = group_index

    group_ids = np.where(valid, slot_to_group_index[np.where(valid, indices, 0)], -1)
    keep = (weights > 1e-6) & (group_ids >= 0)
    if not keep.any():
        return
    vert_ids = np.broadcast_to(np.arange(n_verts, dtype=np.int64)[:, None],
                               (n_verts, n_inf))[keep]
    flat_groups = group_ids[keep].astype(np.int64)
    flat_weights = weights[keep].astype(np.float64)

    # Weights for the same bone reached through different influence slots are
    # summed for the same vertex, then rounded once -- byte-identical storage
    # to the previous per-bucket rounding.
    combined = vert_ids * (flat_groups.max() + 1) + flat_groups
    unique_keys, first_index, inverse = np.unique(
        combined, return_index=True, return_inverse=True)
    if len(unique_keys) != len(combined):
        summed = np.zeros(len(unique_keys), dtype=np.float64)
        np.add.at(summed, inverse, flat_weights)
        vert_ids = vert_ids[first_index]
        flat_groups = flat_groups[first_index]
        flat_weights = summed
    flat_weights = np.round(flat_weights, 6)

    order = np.argsort(vert_ids, kind="stable")
    verts_list = vert_ids[order].tolist()
    groups_list = flat_groups[order].tolist()
    weights_list = flat_weights[order].tolist()

    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    deform_layer = bm.verts.layers.deform.verify()
    bm.verts.ensure_lookup_table()
    bm_verts = bm.verts
    deform_vert = None
    current_vertex = -1
    for k in range(len(verts_list)):
        vertex = verts_list[k]
        if vertex != current_vertex:
            deform_vert = bm_verts[vertex][deform_layer]
            current_vertex = vertex
        deform_vert[groups_list[k]] = weights_list[k]
    bm.to_mesh(obj.data)
    bm.free()


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
            # 每条 delta 是 (下标, 位置, 法线, 切线):形变顶点的三个 delta 与 C# 侧
            # MeshRawBlob 的 40 字节步长、解码器的 dtype 是**同一份约定**,三处必须同时改 ——
            # 少改这一处就是 `too many values to unpack`,少改另两处就是 numpy 除不尽。
            for index, delta_v, _delta_n, _delta_t in frame["deltas"]:
                # Convert the Unity-space delta into Blender space (swap Y/Z).
                co[index] += np.array((delta_v[0], delta_v[2], delta_v[1]), dtype=np.float32)
            key.data.foreach_set("co", co.reshape(-1))
