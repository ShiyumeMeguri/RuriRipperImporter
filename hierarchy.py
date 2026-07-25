"""Blender's view of a Unity prefab's Transform hierarchy.

The tree itself -- parents, per-node Unity-space local/world matrices, stable
root-relative paths (the key animation curves bind to) -- is built by
``ruri_pybridge.unity.hierarchy`` in plain numpy. This module is only the
boundary: it hands back the same nodes with ``local``/``world`` as
``mathutils.Matrix``, because everything downstream in the add-on does rest-pose
maths on them with Matrix/Quaternion methods (``.to_quaternion()``,
``.inverted_safe()``, ``.translation``, ``.to_scale()``) and assigns them
straight to ``eb.matrix``/``obj.matrix_world``.

Still UNITY space, exactly as before -- ``coordinate.convert_matrix`` is the
separate, explicit step.
"""

from __future__ import annotations

from mathutils import Matrix

try:
    from .ruri_pybridge.unity import hierarchy as _shared
except ImportError:  # standalone (non-package) testing
    from ruri_pybridge.unity import hierarchy as _shared

Node = _shared.Node


def _as_matrix(array):
    return Matrix([[float(v) for v in row] for row in array])


def build_hierarchy(unity_file):
    """Return (nodes_by_id, roots) for all transforms in a prefab UnityFile,
    with mathutils matrices on every node."""
    nodes, roots = _shared.build_hierarchy(unity_file)
    for node in nodes.values():
        node.local = _as_matrix(node.local)
        node.world = _as_matrix(node.world)
    return nodes, roots


def world_matrices(nodes):
    """{transform fileID -> Unity-space world 4x4 as numpy} -- what
    ``skinning.bake_bind_pose`` consumes. numpy, not Matrix: the bake is matrix
    maths over every bone at once."""
    import numpy as np

    return {file_id: np.array(node.world, dtype=np.float64)
            for file_id, node in nodes.items()}
