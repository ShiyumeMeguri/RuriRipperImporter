"""Blender's side of the Unity -> Blender conversion: a ``mathutils`` face on
the shared numpy one.

The conversion ITSELF -- swap Y and Z, which is a reflection (determinant -1)
that fixes the up-axis and the handedness at once, plus everything that follows
from being a reflection (winding, tangent handedness) -- lives in
``ruri_pybridge.math3d.coordinate`` as ``BLENDER``, where it is testable with no
Blender running and cannot drift apart from the glTF space beside it.

What is Blender-specific, and all that stays here, is the TYPE: ``eb.matrix``
and ``obj.matrix_world`` take a ``mathutils.Matrix``, and the animation builder
does its rest-pose maths with ``Matrix``/``Quaternion`` methods. So this module
converts at that boundary and leaves every call site unchanged.

Vertex arrays are not converted to anything: they are numpy on both sides and go
straight through to ``foreach_set``.
"""

from __future__ import annotations

from mathutils import Matrix

try:
    from .ruri_pybridge.math3d import coordinate as _shared
except ImportError:  # standalone (non-package) testing
    from ruri_pybridge.math3d import coordinate as _shared

SPACE = _shared.BLENDER


def _as_matrix(array):
    """numpy 4x4 -> mathutils.Matrix (both are row-major, so this is direct)."""
    return Matrix([[float(v) for v in row] for row in array])


def conversion_matrix():
    """The reflection C, as a Matrix. Its own inverse, so ``C @ M @ C`` converts
    a transform in either direction."""
    return _as_matrix(SPACE.matrix)


def convert_matrix(unity_matrix):
    """Conjugate a Unity 4x4 -- numpy array OR mathutils.Matrix -- into Blender
    space, returning a Matrix."""
    return _as_matrix(SPACE.convert_matrix(unity_matrix))


def unity_trs(position, rotation, scale):
    """Unity-space TRS from dict components {x,y,z[,w]}, as a Matrix. Still in
    UNITY space -- conversion is the separate, explicit step above."""
    return _as_matrix(_shared.unity_trs(position, rotation, scale))


def convert_points(positions):
    """(n, 3) Unity positions/normals -> Blender space, float32."""
    return SPACE.convert_points(positions)


def convert_tangents(tangents):
    """(n, 4) Unity tangents -> Blender space. The handedness sign FLIPS here,
    unlike the glTF space where the V flip cancels it -- see
    Space.tangent_w_sign for the derivation."""
    return SPACE.convert_tangents(tangents)


def reverse_winding(triangles):
    """Reverse triangle winding to compensate for the reflection."""
    return SPACE.reverse_winding(triangles)
