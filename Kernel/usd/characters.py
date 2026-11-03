"""The layer of one rig: its skeleton, the meshes skinned or attached to it, their blend shapes.

Everything is written in the rig root's own space, so the scene places a character with one
transform and the character carries none. The statement's meshes arrive baked to the pose the scene
shows, in the scene's space; carrying them through the inverse of the root's world matrix puts them
in the rig's, and the joints' bind transforms are the pose they were baked at.
"""

from __future__ import annotations

import numpy as np
from pxr import Sdf, UsdSkel, Vt

from .. import statement as kernel_statement
from . import geometry, materials, sources, stages
from .names import Namer, identifier
from .transforms import move_normals, move_points, to_usd

SKELETON = "Skeleton"

#: How far apart two meshes' points may be, in metres, and still be the same mesh written twice.
_SAME = 1e-4


class Part:
    """One renderer of a rig, as it is written: the mesh with its points in the rig's space, and how
    it is bound -- to several joints with weights, or to one joint as a rigid part of it."""

    __slots__ = ("node", "mesh", "positions", "normals", "joints", "weights", "joint")

    def __init__(self, node, mesh, positions, normals, joints=None, weights=None, joint=None):
        self.node = node
        self.mesh = mesh
        self.positions = positions
        self.normals = normals
        self.joints = joints
        self.weights = weights
        self.joint = joint


def _influences(rig, mesh):
    """The mesh's four influences per vertex as joint indices of the rig and weights summing to one.
    A bone the rig does not hold counts for nothing; a vertex left with nothing binds to the first joint."""
    lookup = np.array([rig.joint_of_path.get(path, -1) for path in mesh.bone_paths], dtype=np.int64)
    indices = np.asarray(mesh.bone_indices, dtype=np.int64)
    if not len(lookup):
        return None, None, 0
    inside = (indices >= 0) & (indices < len(lookup))
    joints = np.where(inside, lookup[np.clip(indices, 0, len(lookup) - 1)], -1)
    weights = np.where(joints >= 0, np.asarray(mesh.weights, dtype=np.float64), 0.0)
    bare = weights.sum(axis=1) <= 1e-9
    weights[bare, 0] = 1.0
    joints[bare, 0] = 0
    weights = weights / weights.sum(axis=1, keepdims=True)
    return np.where(joints >= 0, joints, 0).astype(np.int32), weights.astype(np.float32), int(bare.sum())


def parts_of(statement, hierarchy, rig):
    """The rig's renderers as :class:`Part`, and a count of what could not be written: ``(parts, notes)``."""
    nodes = hierarchy.nodes
    to_rig = np.linalg.inv(hierarchy.worlds[rig.root])
    parts = []
    notes = {"missing": 0, "bare": 0}
    for index in [rig.root] + rig.members:
        node = nodes[index]
        if node.kind not in (kernel_statement.SKINNED, kernel_statement.MESH) or not node.mesh:
            continue
        mesh = statement.meshes.get(node.mesh)
        if mesh is None or not len(mesh.positions):
            notes["missing"] += 1
            continue
        if node.kind == kernel_statement.SKINNED and mesh.weights is not None:
            joints, weights, bare = _influences(rig, mesh)
            if joints is not None:
                notes["bare"] += bare
                parts.append(Part(node, mesh, move_points(to_rig, mesh.positions),
                                  None if mesh.normals is None else move_normals(to_rig, mesh.normals),
                                  joints=joints, weights=weights))
                continue
        world = np.eye(4) if index == rig.root else rig.bind[rig.joint_of[index]]
        parts.append(Part(node, mesh, move_points(world, mesh.positions),
                          None if mesh.normals is None else move_normals(world, mesh.normals),
                          joint=rig.joint_of.get(index)))
    return parts, notes


def fingerprint(rig, parts):
    """What two rigs must share to be the same character: the same joints, and the same meshes bound the same way."""
    return (tuple(rig.sources),
            tuple((part.node.name, part.node.kind, part.mesh.name, len(part.positions), len(part.mesh.triangles),
                   tuple(part.node.materials)) for part in parts))


def same_points(rig, parts, other_rig, other_parts):
    """Whether two rigs with one fingerprint also have one shape: their meshes' points and joints agree to within a tenth of a millimetre."""
    return (all(np.allclose(a, b, atol=_SAME) for a, b in zip(rig.bind, other_rig.bind))
            and all(np.allclose(a.positions, b.positions, atol=_SAME) for a, b in zip(parts, other_parts)))


def _material(stage, scope_path, key, statement, textures, layer_path, families, names, made):
    stated = statement.materials.get(key) if key else None
    if stated is None:
        return None
    if key not in made:
        made[key] = materials.author(stage, scope_path, stated, textures, layer_path, families, names)
    return made[key]


def author(layer_path, name, rig, parts, hierarchy, statement, textures, families):
    """Write the character ``name`` to ``layer_path``. Returns the root prim's path."""
    stage = stages.create(layer_path, kernel_statement.USD, "Rig {0}".format(name))
    names = Namer()
    root = UsdSkel.Root.Define(stage, "/" + name)
    stage.SetDefaultPrim(root.GetPrim())
    root_path = root.GetPath()
    for reserved in (SKELETON, materials.SCOPE):
        names.claim(str(root_path), reserved)
    skeleton = UsdSkel.Skeleton.Define(stage, root_path.AppendChild(SKELETON))
    skeleton.CreateJointsAttr(Vt.TokenArray(rig.tokens))
    skeleton.CreateBindTransformsAttr(Vt.Matrix4dArray([to_usd(matrix) for matrix in rig.bind]))
    skeleton.CreateRestTransformsAttr(Vt.Matrix4dArray([to_usd(matrix) for matrix in rig.rest]))
    skeleton.GetPrim().CreateAttribute("ruri:jointSourcePaths", Sdf.ValueTypeNames.StringArray, custom=True).Set(
        Vt.StringArray(rig.sources))
    root_node = hierarchy.nodes[rig.root]
    root.GetPrim().CreateAttribute("ruri:sourceName", Sdf.ValueTypeNames.String, custom=True).Set(root_node.name)
    if root_node.avatar:
        root.GetPrim().CreateAttribute("ruri:avatar", Sdf.ValueTypeNames.String, custom=True).Set(root_node.avatar)
    scope_path = None
    made = {}
    for part in parts:
        node = part.node
        if scope_path is None:
            scope_path = materials.scope(stage, root_path)
        slot_materials = [_material(stage, scope_path, key, statement, textures, layer_path, families, names, made)
                           for key in node.materials]
        path = root_path.AppendChild(names.claim(str(root_path), identifier(node.name)))
        geom = geometry.author(stage, path, part.mesh, positions=part.positions, normals=part.normals)
        geometry.bind_materials(geom, part.mesh, slot_materials, names)
        if part.weights is not None:
            geometry.skin(geom, skeleton.GetPath(), part.joints, part.weights)
            morphs = statement.morphs.get(part.mesh.key, ())
            if morphs:
                geometry.blend_shapes(stage, geom, morphs, names)
        elif part.joint is not None:
            geometry.attach(geom, skeleton.GetPath(), part.joint)
        relative = "" if node.index == rig.root else rig.sources[rig.joint_of[node.index]]
        sources.describe(geom.GetPrim(), node, relative)
    stage.GetRootLayer().Save()
    return root_path
