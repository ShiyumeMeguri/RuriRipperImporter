"""Rigs: which nodes of a scene are characters, and the skeleton each one is.

A node that animates with an avatar and has skinned meshes somewhere below it is the root of a
rig; the outermost such node owns everything beneath it. A skinned mesh outside every such rig
belongs to the smallest subtree that holds both the mesh and the bones it is skinned to. Every
node below a root is a joint of its skeleton -- the transforms a mesh is skinned to, the ones a
part is attached to, and the rest -- so a rig is cut from the scene with nothing left behind.
"""

from __future__ import annotations

from .. import statement as kernel_statement
from .names import identifier


def _common_ancestor(hierarchy, members):
    """The deepest node every member stands under or is, or -1 when they share none."""
    chains = []
    for member in members:
        chain = set()
        cursor = member
        while cursor >= 0:
            chain.add(cursor)
            cursor = hierarchy.nodes[cursor].parent
        chains.append(chain)
    common = set.intersection(*chains)
    return max(common, key=lambda node: hierarchy.depths[node], default=-1)


def find(hierarchy, meshes):
    """``(roots, covered)``: the node index of each rig's root, and for every node whether some rig covers it."""
    nodes = hierarchy.nodes
    skinned_below = [False] * len(nodes)
    for index in range(len(nodes) - 1, -1, -1):
        node = nodes[index]
        if node.kind == kernel_statement.SKINNED:
            skinned_below[index] = True
        if skinned_below[index] and node.parent >= 0:
            skinned_below[node.parent] = True
    roots = []
    covered = [False] * len(nodes)
    for index, node in enumerate(nodes):
        if node.parent >= 0 and covered[node.parent]:
            covered[index] = True
        elif node.avatar and skinned_below[index]:
            roots.append(index)
            covered[index] = True
    by_path = {}
    for node in nodes:
        by_path.setdefault(node.path, node.index)
    wanted = {}
    for node in nodes:
        mesh = meshes.get(node.mesh) if node.kind == kernel_statement.SKINNED else None
        if mesh is None or covered[node.index]:
            continue
        members = [node.index] + [by_path[path] for path in mesh.bone_paths if path in by_path]
        root = _common_ancestor(hierarchy, members)
        if root >= 0:
            wanted.setdefault(root, []).append(node.index)
    for root in sorted(wanted, key=lambda index: (hierarchy.depths[index], index)):
        below = [root] + hierarchy.subtree(root)
        if not any(covered[index] for index in below):
            roots.append(root)
            for index in below:
                covered[index] = True
    return sorted(roots), covered


def active_within(hierarchy, rig, index):
    """Whether the object at ``index`` and every object between it and the rig's root is itself active. The root's own
    state, and whatever sits above it, belongs to the scene that places the rig."""
    while index != rig.root:
        if index < 0:
            raise ValueError("the ancestors of a joint do not reach the root of its rig.")
        node = hierarchy.nodes[index]
        if not node.active_self:
            return False
        index = node.parent
    return True


class Rig:
    """The skeleton below one rig root.

    ``members`` are the node indices below the root, parents first; joint ``j`` is ``members[j]``. Each joint
    has the token path USD names it by (legal, unique among its siblings), the path the source names it by,
    the index of its parent joint (-1 for a child of the root), its local matrix, and its world matrix
    relative to the root -- the bind pose, because the meshes arrive baked to the pose the scene shows."""

    def __init__(self, hierarchy, root, names):
        self.root = root
        self.members = hierarchy.subtree(root)
        self.joint_of = {node: joint for joint, node in enumerate(self.members)}
        self.tokens = []
        self.sources = []
        self.parents = []
        self.rest = []
        self.bind = []
        scope = ("rig", root)
        for joint, index in enumerate(self.members):
            node = hierarchy.nodes[index]
            parent = self.joint_of.get(node.parent, -1)
            leaf = names.claim((scope, parent), identifier(node.name))
            self.tokens.append(leaf if parent < 0 else self.tokens[parent] + "/" + leaf)
            self.sources.append(node.name if parent < 0 else self.sources[parent] + "/" + node.name)
            self.parents.append(parent)
            local = hierarchy.locals[index]
            self.rest.append(local)
            self.bind.append(local if parent < 0 else self.bind[parent] @ local)
        self.joint_of_path = {}
        for joint, index in enumerate(self.members):
            self.joint_of_path.setdefault(hierarchy.nodes[index].path, joint)
