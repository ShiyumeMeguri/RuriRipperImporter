"""A mesh, its material binding, its skin and its blend shapes as USD prims.

The arrays come from the statement already in the basis the layer is written in, so a mesh is
written as it is: one point per vertex, one triangle per face, every attribute per vertex.
"""

from __future__ import annotations

import numpy as np
from pxr import Gf, Sdf, UsdGeom, UsdShade, UsdSkel, Vt

from .names import identifier

_INDEX = np.int32


def uv_name(index):
    """The primvar a texture-coordinate set is written as: the first is ``st`` (the name a preview
    surface reads), the others ``st1``, ``st2`` and so on."""
    return "st" if index == 0 else "st{0}".format(index)


def _floats(values):
    return np.ascontiguousarray(values, dtype=np.float32)


def author(stage, path, mesh, positions=None, normals=None):
    """The mesh at ``path``. ``positions`` and ``normals`` stand in for the statement's own where the
    caller has carried them into another space."""
    geom = UsdGeom.Mesh.Define(stage, path)
    points = _floats(mesh.positions if positions is None else positions)
    triangles = np.ascontiguousarray(mesh.triangles, dtype=_INDEX)
    geom.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points))
    geom.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(triangles), 3, dtype=_INDEX)))
    geom.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(triangles.reshape(-1)))
    geom.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    if len(points):
        geom.CreateExtentAttr(Vt.Vec3fArray.FromNumpy(np.stack([points.min(axis=0), points.max(axis=0)])))
    shading = mesh.normals if normals is None else normals
    if shading is not None and len(shading) == len(points):
        geom.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(_floats(shading)))
        geom.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    primvars = UsdGeom.PrimvarsAPI(geom)
    for index, coordinates in sorted(mesh.uvs.items()):
        primvar = primvars.CreatePrimvar(uv_name(index), Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
        primvar.Set(Vt.Vec2fArray.FromNumpy(_floats(coordinates)))
    colors = mesh.colors
    if colors is not None and len(colors) == len(points) and not np.all(colors == 1.0):
        display = primvars.CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex)
        display.Set(Vt.Vec3fArray.FromNumpy(_floats(colors[:, :3])))
        if not np.all(colors[:, 3] == 1.0):
            opacity = primvars.CreatePrimvar("displayOpacity", Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex)
            opacity.Set(Vt.FloatArray.FromNumpy(_floats(colors[:, 3])))
    return geom


def bind_materials(geom, mesh, slot_materials, names):
    """Bind the material of each slot the mesh draws: on the mesh itself when it draws one, else on a
    subset of faces per slot. A slot whose material is missing is left unbound."""
    slots = mesh.slots or [0]
    if len(slots) == 1:
        material = slot_materials[slots[0]] if slots[0] < len(slot_materials) else None
        if material is not None:
            UsdShade.MaterialBindingAPI.Apply(geom.GetPrim()).Bind(material)
        return
    per_face = np.zeros(len(mesh.triangles), dtype=_INDEX)
    for first, count, slot in mesh.sections.tolist():
        per_face[first // 3:(first + count) // 3] = slot
    for slot in slots:
        subset = UsdGeom.Subset.CreateGeomSubset(
            geom, names.claim(str(geom.GetPath()), "Slot{0}".format(slot)), UsdGeom.Tokens.face,
            Vt.IntArray.FromNumpy(np.flatnonzero(per_face == slot).astype(_INDEX)),
            "materialBind", UsdGeom.Tokens.partition)
        material = slot_materials[slot] if slot < len(slot_materials) else None
        if material is not None:
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material)


def skin(geom, skeleton_path, joint_indices, weights):
    """Bind the mesh to a skeleton by a per-vertex run of joint indices and weights. The mesh is
    already at the skeleton's bind pose, so its own bind transform is the identity."""
    binding = UsdSkel.BindingAPI.Apply(geom.GetPrim())
    binding.CreateSkeletonRel().SetTargets([skeleton_path])
    influences = joint_indices.shape[1]
    binding.CreateJointIndicesPrimvar(False, influences).Set(
        Vt.IntArray.FromNumpy(np.ascontiguousarray(joint_indices, dtype=_INDEX).reshape(-1)))
    binding.CreateJointWeightsPrimvar(False, influences).Set(Vt.FloatArray.FromNumpy(_floats(weights).reshape(-1)))
    binding.CreateGeomBindTransformAttr().Set(Gf.Matrix4d(1.0))
    return binding


def attach(geom, skeleton_path, joint):
    """Bind a mesh that is not skinned to one joint, as a rigid part of it."""
    binding = UsdSkel.BindingAPI.Apply(geom.GetPrim())
    binding.CreateSkeletonRel().SetTargets([skeleton_path])
    binding.CreateJointIndicesPrimvar(True, 1).Set(Vt.IntArray([int(joint)]))
    binding.CreateJointWeightsPrimvar(True, 1).Set(Vt.FloatArray([1.0]))
    binding.CreateGeomBindTransformAttr().Set(Gf.Matrix4d(1.0))
    return binding


def blend_shapes(stage, geom, morphs, names):
    """One blend shape prim per morph, named as the source names it where USD allows and carrying the
    source's name where it does not."""
    binding = UsdSkel.BindingAPI.Apply(geom.GetPrim())
    tokens = []
    targets = []
    for morph in morphs:
        name = names.claim(str(geom.GetPath()), identifier(morph.name or "Shape"))
        shape = UsdSkel.BlendShape.Define(stage, geom.GetPath().AppendChild(name))
        shape.CreatePointIndicesAttr(Vt.IntArray.FromNumpy(np.ascontiguousarray(morph.vertices, dtype=_INDEX)))
        shape.CreateOffsetsAttr(Vt.Vec3fArray.FromNumpy(_floats(morph.positions).reshape(-1, 3)))
        if len(morph.normals) == len(morph.positions) and len(morph.normals):
            shape.CreateNormalOffsetsAttr(Vt.Vec3fArray.FromNumpy(_floats(morph.normals).reshape(-1, 3)))
        prim = shape.GetPrim()
        prim.CreateAttribute("ruri:sourceName", Sdf.ValueTypeNames.String, custom=True).Set(morph.name)
        prim.CreateAttribute("ruri:frameWeight", Sdf.ValueTypeNames.Float, custom=True).Set(float(morph.weight))
        tokens.append(name)
        targets.append(shape.GetPath())
    binding.CreateBlendShapesAttr(Vt.TokenArray(tokens))
    binding.CreateBlendShapeTargetsRel().SetTargets(targets)
    return tokens
