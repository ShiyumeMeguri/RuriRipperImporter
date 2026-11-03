"""A node's local transform, as the matrix it composes and as the xform ops that carry it.

Matrices here are column-vector 4x4 (``world = parent @ local``, translation in the last
column), which is how the statement composes them. USD's own are row-vector, so a matrix
crosses into USD transposed and not otherwise.
"""

from __future__ import annotations

import numpy as np
from pxr import Gf, UsdGeom


def rotation_matrix(rotation):
    """The 3x3 rotation of a quaternion given as (x, y, z, w), normalised first."""
    x, y, z, w = (float(component) for component in rotation)
    length = (x * x + y * y + z * z + w * w) ** 0.5
    if length == 0.0:
        return np.eye(3)
    x, y, z, w = x / length, y / length, z / length, w / length
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def local_matrix(position, rotation, scale):
    """Translate, then rotate, then scale: the local transform a node states."""
    matrix = np.eye(4)
    matrix[:3, :3] = rotation_matrix(rotation) * np.asarray(scale, dtype=np.float64)[None, :]
    matrix[:3, 3] = position
    return matrix


def to_usd(matrix):
    """A column-vector 4x4 as USD's row-vector matrix."""
    return Gf.Matrix4d(np.asarray(matrix, dtype=np.float64).T.tolist())


def move_points(matrix, points):
    """Points carried through a 4x4."""
    return (points.astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]).astype(np.float32)


def move_normals(matrix, normals):
    """Normals carried through a 4x4: by the inverse transpose of its linear part, and kept unit length."""
    carried = normals.astype(np.float64) @ np.linalg.inv(matrix[:3, :3])
    lengths = np.linalg.norm(carried, axis=1, keepdims=True)
    return (carried / np.where(lengths > 1e-12, lengths, 1.0)).astype(np.float32)


def author(prim, position, rotation, scale):
    """The node's local transform as translate, orient and scale ops, each only when it is not the
    identity, so an object that sits at the origin carries no op at all."""
    xformable = UsdGeom.Xformable(prim)
    if any(position):
        xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
    x, y, z, w = (float(component) for component in rotation)
    if (x, y, z) != (0.0, 0.0, 0.0) or w != 1.0:
        length = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
        xformable.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(
            Gf.Quatf(w / length, Gf.Vec3f(x / length, y / length, z / length)))
    if tuple(scale) != (1.0, 1.0, 1.0):
        xformable.AddScaleOp(UsdGeom.XformOp.PrecisionFloat).Set(
            Gf.Vec3f(float(scale[0]), float(scale[1]), float(scale[2])))
