"""Reconstruct the Transform hierarchy of a Unity prefab.

Every Unity ``Transform`` (class 4) is a node with a parent (``m_Father``), a
local TRS and a back-reference to its ``GameObject`` (which carries the name and
the list of attached components).  This module rebuilds that tree, computes each
node's Unity-space world matrix, and assigns each node a stable root-relative
path (used to map animation curves onto bones).
"""

from __future__ import annotations

try:
    from . import coordinate
except ImportError:
    import coordinate

from mathutils import Matrix


class Node:
    __slots__ = ("file_id", "go_id", "name", "active", "local", "world",
                 "parent", "children", "path", "components")

    def __init__(self, file_id):
        self.file_id = file_id
        self.go_id = 0
        self.name = "Node"
        self.active = True
        self.local = Matrix.Identity(4)   # Unity-space local TRS
        self.world = Matrix.Identity(4)   # Unity-space world matrix
        self.parent = None
        self.children = []
        self.path = ""
        self.components = []              # list of component fileIDs


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
        stack = [(root, root.name)]
        root.world = root.local
        root.path = ""  # the animator root has an empty relative path
        while stack:
            node, _ = stack.pop()
            for child in node.children:
                child.world = node.world @ child.local
                child.path = child.name if node.path == "" else node.path + "/" + child.name
                stack.append((child, child.path))
    return nodes, roots
