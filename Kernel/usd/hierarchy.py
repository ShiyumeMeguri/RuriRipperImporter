"""The statement's nodes as a forest, with what the layers need to know of each: where it stands in
the scene, the path the source knows it by, how deep it is."""

from __future__ import annotations

from .transforms import local_matrix


class Hierarchy:
    """Nodes arrive parents first, so one pass in order knows every parent before its children."""

    def __init__(self, nodes):
        self.nodes = nodes
        count = len(nodes)
        self.children = [[] for _ in range(count)]
        self.roots = []
        self.locals = []
        self.worlds = []
        self.source_paths = []
        self.depths = []
        for node in nodes:
            parent = node.parent
            if parent >= 0:
                self.children[parent].append(node.index)
            else:
                self.roots.append(node.index)
            local = local_matrix(node.position, node.rotation, node.scale)
            self.locals.append(local)
            if parent >= 0:
                self.worlds.append(self.worlds[parent] @ local)
                self.source_paths.append(self.source_paths[parent] + "/" + node.name)
                self.depths.append(self.depths[parent] + 1)
            else:
                self.worlds.append(local)
                self.source_paths.append(node.name)
                self.depths.append(0)
        assert all(node.index == position for position, node in enumerate(nodes)), "nodes must be in index order"

    def subtree(self, root):
        """Every node below ``root``, parents before children, not ``root`` itself."""
        found = []
        stack = list(reversed(self.children[root]))
        while stack:
            index = stack.pop()
            found.append(index)
            stack.extend(reversed(self.children[index]))
        return found

    def maximum_depth(self):
        return max(self.depths, default=0)
