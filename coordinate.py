"""Unity <-> Blender coordinate conversion.

Unity is left-handed, Y-up.  Blender is right-handed, Z-up.  Converting between
them requires swapping the Y and Z axes, which is a reflection (determinant -1)
that simultaneously fixes the up-axis and the handedness:

    p_blender = (x, z, y)

Because a single reflection ``C`` is its own inverse, an entire transform is
converted by conjugation::

    M_blender = C @ M_unity @ C

This converts rotation, translation and handedness consistently with no
per-quaternion guesswork.  Since ``det(C) = -1`` the conversion flips triangle
winding, so faces must have their winding reversed to keep normals outward.
"""

from __future__ import annotations

import numpy as np

try:
    from mathutils import Matrix, Quaternion, Vector
except ImportError:  # allows importing the module for pure unit tests
    Matrix = Quaternion = Vector = None


# 4x4 reflection that swaps Y and Z (its own inverse).
def conversion_matrix():
    return Matrix((
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))


def convert_matrix(unity_matrix):
    """Conjugate a Unity 4x4 (mathutils.Matrix) into Blender space."""
    c = conversion_matrix()
    return c @ unity_matrix @ c


def unity_trs(position, rotation, scale):
    """Build a Unity-space TRS matrix from dict components {x,y,z[,w]}."""
    t = Matrix.Translation((position["x"], position["y"], position["z"]))
    q = Quaternion((rotation["w"], rotation["x"], rotation["y"], rotation["z"]))
    r = q.to_matrix().to_4x4()
    s = Matrix.Diagonal((scale["x"], scale["y"], scale["z"], 1.0))
    return t @ r @ s


def convert_points(positions):
    """Convert an (n, 3) numpy array of Unity positions to Blender space."""
    return positions[:, (0, 2, 1)].astype(np.float32, copy=True)


def reverse_winding(triangles):
    """Reverse triangle winding to compensate for the reflection."""
    return triangles[:, (0, 2, 1)]
