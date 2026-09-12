"""Reconstruct the Transform hierarchy of a Unity prefab.

Every Unity ``Transform`` (class 4) is a node with a parent (``m_Father``), a
local TRS and a back-reference to its ``GameObject`` (which carries the name and
the list of attached components). This module rebuilds that tree, computes each
node's Unity-space world matrix, and assigns each node a stable root-relative
path (which is also the key Unity's animation curves bind to).

Matrices are plain numpy 4x4s in UNITY space. Converting them into the host's
own space is the host's job (``math3d.coordinate``), and a host with its own
matrix type -- Blender's ``mathutils.Matrix`` -- wraps them at that same
boundary rather than this module knowing anything about it.
"""

from __future__ import annotations

import numpy as np

from ..math3d import coordinate

# The path an AnimationClip binds the ANIMATOR'S OWN node by. It is the empty
# string, and it is a real binding rather than a missing one: a clip's extracted
# root motion (the travel a humanoid solve splits out of RootT/MotionT) is bound
# by exactly this path and by no other. So every consumer tests a path with
# ``is None`` -- "no path known" -- and never for truthiness, or the whole of a
# character's travel disappears with nothing reported.
ANIMATOR_ROOT_PATH = ""


class Node:
    __slots__ = ("file_id", "go_id", "name", "active", "local", "world",
                 "parent", "children", "path", "components")

    def __init__(self, file_id):
        self.file_id = file_id
        self.go_id = 0
        self.name = "Node"
        self.active = True
        self.local = np.eye(4, dtype=np.float64)   # Unity-space local TRS
        self.world = np.eye(4, dtype=np.float64)   # Unity-space world matrix
        self.parent = None
        self.children = []
        self.path = ""
        self.components = []                       # list of component fileIDs


def build_hierarchy(unity_file):
    """Return (nodes_by_id, roots) for all transforms in a prefab UnityFile."""
    gameobjects = {d.file_id: d for d in unity_file.all("GameObject")}
    transforms = unity_file.all("Transform")
    # Unity also uses RectTransform (class 224) for UI; treat the same way.
    transforms += unity_file.all("RectTransform")

    nodes = {}
    for tr in transforms:
        node = Node(tr.file_id)
        data = tr.data
        go_ref = data.get("m_GameObject") or {}
        node.go_id = go_ref.get("fileID", 0)
        go = gameobjects.get(node.go_id)
        if go:
            node.name = str(go.data.get("m_Name", "Node"))
            node.active = bool(go.data.get("m_IsActive", 1))
            comps = go.data.get("m_Component") or []
            for c in comps:
                ref = c.get("component") if isinstance(c, dict) else None
                if isinstance(ref, dict):
                    node.components.append(ref.get("fileID"))
        pos = data.get("m_LocalPosition") or {"x": 0, "y": 0, "z": 0}
        rot = data.get("m_LocalRotation") or {"x": 0, "y": 0, "z": 0, "w": 1}
        scl = data.get("m_LocalScale") or {"x": 1, "y": 1, "z": 1}
        node.local = coordinate.unity_trs(pos, rot, scl)
        nodes[tr.file_id] = node

    # Wire parents/children.
    for tr in transforms:
        node = nodes[tr.file_id]
        father = (tr.data.get("m_Father") or {}).get("fileID", 0)
        parent = nodes.get(father)
        if parent is not None:
            node.parent = parent
            parent.children.append(node)

    roots = [n for n in nodes.values() if n.parent is None]

    # Compute world matrices and paths from the roots down (iterative DFS).
    for root in roots:
        root.world = root.local
        root.path = ANIMATOR_ROOT_PATH
        stack = [root]
        while stack:
            node = stack.pop()
            for child in node.children:
                child.world = node.world @ child.local
                child.path = (child.name if node.path == ANIMATOR_ROOT_PATH
                              else node.path + "/" + child.name)
                stack.append(child)
    return nodes, roots


def world_matrices(nodes):
    """{transform fileID -> Unity-space world 4x4} -- what the bind-pose bake
    consumes."""
    return {file_id: node.world for file_id, node in nodes.items()}
