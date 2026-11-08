"""The components of the camera package that sit on the nodes of a scene, as records under the nodes that carry them.

A node that carries any of them gets one child ``Scope`` named ``Cinemachine``, and under it one ``Scope`` per component,
named after the class of the component's script, holding the component's serialized members by the five rules of
``records``. Nothing here decides what a member means: the source states every field under the name its script gives it, a
reference to another node as that node's source path, and a reader that models a component reads the members it uses and
refuses the ones it does not. What this module decides is only where each component goes, and it decides it
without guessing: a component whose path does not name exactly one node of the scene, or whose node the scene layer does
not write, is refused rather than left out.
"""

from __future__ import annotations

from . import records
from .names import identifier

#: The child prim of a node that holds its components of the package.
NAME = "Cinemachine"


def by_node(components, hierarchy):
    """The components keyed by the index of the node each sits on, in the order the statement states them. A path that names
    no node, or more than one, is the one place this goes wrong, and it is refused."""
    indices = {}
    for index, path in enumerate(hierarchy.source_paths):
        indices.setdefault(path, []).append(index)
    placed = {}
    for component in components:
        found = indices.get(component.path, ())
        if len(found) != 1:
            raise ValueError("the {0} on '{1}' names {2} node(s) of the scene, and a component sits on exactly one.".format(
                component.class_name, component.path, len(found)))
        placed.setdefault(found[0], []).append(component)
    return placed


def author(stage, prim, components, names, counts):
    """The ``components`` of one node as the ``Cinemachine`` scope of ``prim``. ``counts`` tallies ``prims`` and
    ``attributes``."""
    wanted = names.claim(str(prim.GetPath()), NAME)
    if wanted != NAME:
        raise ValueError("'{0}' has a child already named {1}, and the components of the camera package are read from "
                         "that name.".format(prim.GetPath(), NAME))
    holder = stage.DefinePrim(prim.GetPath().AppendChild(NAME), "Scope")
    counts["prims"] += 1
    taken = set()
    for component in components:
        name = identifier(component.class_name)
        if name in taken:
            raise ValueError("'{0}' carries more than one {1}.".format(prim.GetPath(), component.class_name))
        taken.add(name)
        records.attach(stage, holder, name, component.record, counts)
