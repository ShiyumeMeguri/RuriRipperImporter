"""Unity -> target-coordinate-system conversion, as data rather than as code.

Unity is LEFT-handed, +Y up, +Z forward, +X right. Every consumer here is
right-handed, so every conversion is a REFLECTION (determinant -1), and a
reflection has consequences that have to be carried consistently or the model
comes out subtly (or spectacularly) wrong:

* an entire transform converts by conjugation, ``M' = C @ M @ C`` -- valid
  because a single reflection is its own inverse -- which carries rotation,
  translation and handedness across at once with no per-quaternion guesswork;
* triangle winding flips, so faces must be re-wound to keep normals outward;
* a tangent's handedness sign (glTF TANGENT.w, where
  ``bitangent = cross(normal, tangent.xyz) * w``) flips with the cross product
  -- and flips BACK again if the target also flips the V axis, because that
  reverses dP/dv, i.e. the bitangent itself. The two cancel exactly.

The two reflections actually in use:

``BLENDER``  swap Y and Z: ``p' = (x, z, y)``. Blender is right-handed Z-up, so
             one swap fixes the up-axis and the handedness simultaneously. UV
             origin is bottom-left in both Unity and Blender, so V is untouched
             -- and therefore tangent w DOES flip here.
``GLTF``     negate X: ``p' = (-x, y, z)``. glTF is right-handed Y-up and, like
             Unity, has the asset facing +Z, so only the handedness differs.
             Negating X (rather than Z, the other single-axis option) keeps the
             model FACING THE VIEWER, which is what a Painter/glTF viewer's
             default camera wants; the 180 degrees that choice costs is
             expressible in the shader's own face-axis constants. glTF's UV
             origin is the UPPER left (glTF 2.0 3.7.5) against Unity's lower
             left, so V IS flipped -- which is exactly why tangent w comes
             through unchanged for this space and not for Blender's.

Pure numpy, so all of it is testable with no host application present.
"""

from __future__ import annotations

import math

import numpy as np


def _reflection(matrix3):
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = np.asarray(matrix3, dtype=np.float64)
    return out


# 180-degree yaw about the up axis, as a 4x4 (determinant +1). Unity's asset
# faces +Z; after the Y<->Z swap that lands on Blender +Y, but Blender's front is
# -Y, so a top-level object is turned by this to face the viewer. Kept separate
# from the reflection C, which must stay an involution -- a yaw folded into C
# would break C @ C == I.
_ROOT_YAW_180 = _reflection(((-1.0, 0.0, 0.0),
                             (0.0, -1.0, 0.0),
                             (0.0, 0.0, 1.0)))


class Space:
    """One Unity -> target conversion. Immutable; construct the module-level
    constants below rather than one of these per call."""

    __slots__ = ("name", "matrix", "flip_v", "root_rotation", "_matrix3",
                 "_perm", "_signs", "_determinant")

    def __init__(self, name, matrix3, flip_v, root_rotation=None):
        self.name = name
        self.matrix = _reflection(matrix3)
        self.flip_v = bool(flip_v)
        # Applied once to a top-level import object's world matrix, never to
        # bones, vertices or nested children (see convert_root_matrix).
        self.root_rotation = (np.eye(4, dtype=np.float64) if root_rotation is None
                              else np.asarray(root_rotation, dtype=np.float64))
        self._matrix3 = self.matrix[:3, :3]
        self._determinant = float(np.linalg.det(self._matrix3))
        self._perm, self._signs = _signed_permutation(self._matrix3)

    def __repr__(self):
        return "<Space {0}>".format(self.name)

    # -- properties derived from the reflection itself ----------------------

    @property
    def reverses_winding(self):
        """True when the conversion mirrors, i.e. when faces need re-winding."""
        return self._determinant < 0.0

    @property
    def tangent_w_sign(self):
        """What happens to glTF-style tangent handedness w. The reflection
        flips every cross product; a V flip reverses the bitangent again. Both
        together cancel, which is why this is +1 for glTF and -1 for Blender --
        derived, not tabulated."""
        sign = -1.0 if self.reverses_winding else 1.0
        if self.flip_v:
            sign = -sign
        return sign

    # -- conversions --------------------------------------------------------

    def convert_matrix(self, unity_matrix):
        """Conjugate a Unity 4x4 into this space: ``C @ M @ C`` (float64)."""
        return self.matrix @ np.asarray(unity_matrix, dtype=np.float64) @ self.matrix

    def convert_root_matrix(self, unity_matrix):
        """convert_matrix plus the once-only top-level yaw: ``R @ C @ M @ C``.
        For a top-level import object only; det(R) = +1 so winding and tangents
        are untouched. An identity input yields R itself."""
        return self.root_rotation @ self.convert_matrix(unity_matrix)

    def convert_matrices(self, unity_matrices):
        """convert_matrix over an (n, 4, 4) stack in one broadcast matmul --
        the massed-placement path, where n runs to 10^5 and a python-level
        loop over conjugations would be the cost."""
        mats = np.asarray(unity_matrices, dtype=np.float64)
        return self.matrix[None] @ mats @ self.matrix[None]

    def convert_root_matrices(self, unity_matrices):
        """convert_root_matrix over an (n, 4, 4) stack: ``R @ C @ M @ C``,
        batched. Every row is a TOP-LEVEL object's world matrix (a scene
        placement); the root yaw applies once per row by construction."""
        return self.root_rotation[None] @ self.convert_matrices(unity_matrices)

    def convert_points(self, points):
        """Convert an (n, 3) array of Unity positions -- or of directions such
        as normals, which transform identically under a pure reflection -- into
        this space, as float32.

        For a signed permutation (both spaces here are one) this is a gather,
        not a matrix product: exact, and no temporaries."""
        pts = np.asarray(points, dtype=np.float32)
        if self._perm is not None:
            out = pts[:, self._perm].copy()
            if self._signs is not None:
                out *= self._signs
            return out
        return (pts @ self._matrix3.T.astype(np.float32)).astype(np.float32, copy=False)

    # Directions and positions differ only under a translation, which a
    # reflection has none of -- keep the distinct name for callers that want to
    # say which one they mean.
    convert_directions = convert_points

    def convert_tangents(self, tangents):
        """Convert an (n, 4) Unity tangent array (xyz + handedness sign).

        xyz is a surface direction (dP/du) and converts like any other; w
        follows ``tangent_w_sign`` -- see that property for the derivation. Do
        not "fix" w to always flip: that is correct for a reflection ALONE and
        wrong the moment the target also flips V."""
        tan = np.asarray(tangents, dtype=np.float32)
        out = np.empty((len(tan), 4), dtype=np.float32)
        out[:, :3] = self.convert_points(tan[:, :3])
        out[:, 3] = tan[:, 3] * np.float32(self.tangent_w_sign)
        return out

    def convert_uvs(self, uvs):
        """Unity UV -> this space's UV. Unity puts (0, 0) at the BOTTOM-left of
        the image; a target whose origin is the top-left needs V flipped, or
        every texture ends up mirrored vertically on the model."""
        out = np.asarray(uvs, dtype=np.float32).copy()
        if self.flip_v:
            out[:, 1] = 1.0 - out[:, 1]
        return out

    def reverse_winding(self, triangles):
        """Re-wind triangles to compensate for the reflection (a no-op copy for
        a hypothetical determinant-positive space)."""
        tris = np.asarray(triangles)
        return tris[:, (0, 2, 1)] if self.reverses_winding else tris.copy()


def _signed_permutation(matrix3):
    """(perm, signs) when every row of a 3x3 has exactly one entry and it is
    +-1, else (None, None). ``out = points[:, perm] * signs``; ``signs`` is
    None when every sign is positive, so the common case stays a pure gather."""
    perm = []
    signs = []
    for row in range(3):
        nonzero = [col for col in range(3) if matrix3[row, col] != 0.0]
        if len(nonzero) != 1:
            return None, None
        col = nonzero[0]
        value = matrix3[row, col]
        if value not in (1.0, -1.0):
            return None, None
        perm.append(col)
        signs.append(value)
    if all(sign == 1.0 for sign in signs):
        return tuple(perm), None
    return tuple(perm), np.asarray(signs, dtype=np.float32)


# 4x4 reflection swapping Y and Z (its own inverse). The top-level yaw turns the
# converted asset to face Blender's -Y front.
BLENDER = Space("blender", ((1.0, 0.0, 0.0),
                            (0.0, 0.0, 1.0),
                            (0.0, 1.0, 0.0)), flip_v=False,
                root_rotation=_ROOT_YAW_180)

# 4x4 reflection negating X (its own inverse). glTF's own -X negation already
# leaves the model facing the viewer, so its top-level rotation is the identity.
GLTF = Space("gltf", ((-1.0, 0.0, 0.0),
                      (0.0, 1.0, 0.0),
                      (0.0, 0.0, 1.0)), flip_v=True)

SPACES = {space.name: space for space in (BLENDER, GLTF)}


# ---------------------------------------------------------------------------
# Unity-space construction (target-system independent)
# ---------------------------------------------------------------------------
def unity_trs(position, rotation, scale):
    """Build a Unity-space TRS 4x4 from dict components ``{x, y, z[, w]}`` as
    they appear in the YAML. Stays in UNITY space -- conversion is a separate,
    explicit step, so nothing accidentally converts twice."""
    x = float(rotation.get("x", 0.0))
    y = float(rotation.get("y", 0.0))
    z = float(rotation.get("z", 0.0))
    w = float(rotation.get("w", 1.0))
    # math.sqrt, not ``** 0.5``: the power operator goes through libm ``pow``,
    # which is not required to be correctly rounded and measurably is not (1 ULP
    # off on ~0.04% of real quaternions), while sqrt is IEEE-correctly-rounded.
    # That is also what the batched path computes, so the two agree bitwise.
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm > 1e-12:
        x, y, z, w = x / norm, y / norm, z / norm, w / norm
    else:
        x, y, z, w = 0.0, 0.0, 0.0, 1.0

    rot = np.array([
        [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
        [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
        [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
    ], dtype=np.float64)

    scl = np.array([float(scale.get("x", 1.0)),
                    float(scale.get("y", 1.0)),
                    float(scale.get("z", 1.0))], dtype=np.float64)

    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rot * scl[None, :]   # R @ diag(S)
    matrix[0, 3] = float(position.get("x", 0.0))
    matrix[1, 3] = float(position.get("y", 0.0))
    matrix[2, 3] = float(position.get("z", 0.0))
    return matrix


def unity_trs_batch(positions, quaternions, scales):
    """(n, 4, 4) Unity-space TRS matrices from columnar components, all at once:
    ``positions``/``scales`` are (n, 3), ``quaternions`` (n, 4) in x/y/z/w order
    -- exactly the columns a placement table carries. The same math as
    ``unity_trs`` (normalize, quaternion-to-matrix, R @ diag(S), translate),
    vectorized so 10^5 placements are ~a dozen numpy passes rather than 10^5
    python-level constructions. Stays in UNITY space; conversion is the
    separate, explicit step (``Space.convert_root_matrices``)."""
    pos = np.asarray(positions, dtype=np.float64)
    quat = np.asarray(quaternions, dtype=np.float64)
    scl = np.asarray(scales, dtype=np.float64)
    n = len(pos)

    # Summed column by column in the SAME order as the scalar unity_trs above --
    # np.linalg.norm reassociates and lands a last-bit different length, which
    # propagates into every matrix entry (measured: 3.6e-15 worst case). Same
    # order here means the batch is bitwise equal to the per-row build, which is
    # what makes this a lossless rewrite rather than an almost-equal one.
    squared = quat * quat
    norms = np.sqrt(squared[:, 0] + squared[:, 1] + squared[:, 2] + squared[:, 3])[:, None]
    unit = np.divide(quat, norms, out=np.zeros_like(quat), where=norms > 1e-12)
    degenerate = (norms <= 1e-12).reshape(-1)
    if degenerate.any():
        unit[degenerate] = (0.0, 0.0, 0.0, 1.0)
    x, y, z, w = unit[:, 0], unit[:, 1], unit[:, 2], unit[:, 3]

    rot = np.empty((n, 3, 3), dtype=np.float64)
    rot[:, 0, 0] = 1.0 - 2.0 * (y * y + z * z)
    rot[:, 0, 1] = 2.0 * (x * y - z * w)
    rot[:, 0, 2] = 2.0 * (x * z + y * w)
    rot[:, 1, 0] = 2.0 * (x * y + z * w)
    rot[:, 1, 1] = 1.0 - 2.0 * (x * x + z * z)
    rot[:, 1, 2] = 2.0 * (y * z - x * w)
    rot[:, 2, 0] = 2.0 * (x * z - y * w)
    rot[:, 2, 1] = 2.0 * (y * z + x * w)
    rot[:, 2, 2] = 1.0 - 2.0 * (x * x + y * y)

    matrices = np.zeros((n, 4, 4), dtype=np.float64)
    matrices[:, :3, :3] = rot * scl[:, None, :]   # R @ diag(S), rowwise
    matrices[:, :3, 3] = pos
    matrices[:, 3, 3] = 1.0
    return matrices


def transform_points(matrix, points):
    """Apply a 4x4 to an (n, 3) point array (whatever space both are in)."""
    pts = np.asarray(points, dtype=np.float64)
    homogeneous = np.concatenate([pts, np.ones((len(pts), 1), dtype=np.float64)], axis=1)
    return (homogeneous @ np.asarray(matrix, dtype=np.float64).T)[:, :3].astype(np.float32)


def transform_directions(matrix, vectors):
    """Apply a 4x4's rotation/scale part to an (n, 3) direction array and
    renormalise -- for normals carried through a node transform."""
    vecs = np.asarray(vectors, dtype=np.float64)
    out = vecs @ np.asarray(matrix, dtype=np.float64)[:3, :3].T
    lengths = np.linalg.norm(out, axis=1, keepdims=True)
    lengths[lengths < 1e-9] = 1.0
    return (out / lengths).astype(np.float32)
