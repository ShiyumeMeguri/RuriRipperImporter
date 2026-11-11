"""What a rig's joints carry besides being joints: a camera, the components of the camera package, a light.

Every node below a rig's root is a joint of its skeleton, and a joint is not a prim to hang a state under, so what a joint
carries that the rig's layer would otherwise lose is stated the way a volume on a joint is (``colliders``): as a child prim
of the rig's root that names the joint it follows (``ruri:joint``) and says whether the object it sits on is active as far
as the rig goes (``ruri:activeSelf``, the object and every object between it and the root).

A joint that carries a camera or any component of the camera package is a ``JointCinemachine`` prim: the camera's own
state as ``ruri:camera:`` attributes, and the components in the same ``Cinemachine`` scope a node of the scene has them in.
A joint that carries a light is a ``JointLight`` prim with the light's state as ``ruri:light:`` attributes. Several of
either are told apart by the usual numbered suffix (``JointCinemachine_2``).
"""

from __future__ import annotations

import json

from pxr import Sdf

from . import cinemachine as cinemachine_module
from . import rigs, sources

#: The child prims of a rig's root that state what one of its joints carries.
CAMERA_NAME = "JointCinemachine"
LIGHT_NAME = "JointLight"


class Carried:
    """One joint that carries something: the node and its joint's index, whether the object is active as far as the rig goes,
    the components of the camera package it carries, and ``key``, what two rigs must share for this joint to be the same: the
    joint, whether it is active, the camera, the light and every component, each as the source states it."""

    __slots__ = ("node", "joint", "active", "components", "key")

    def __init__(self, node, joint, active, components, key):
        self.node = node
        self.joint = joint
        self.active = active
        self.components = components
        self.key = key


def _key(node, joint, active, components):
    return (joint, active, bool(node.component_enabled),
            None if node.camera is None else json.dumps(node.camera, sort_keys=True, default=str),
            None if node.light is None else json.dumps(node.light, sort_keys=True, default=str),
            tuple((component.class_name, json.dumps(component.record, sort_keys=True, ensure_ascii=False))
                  for component in components))


def find(rig, hierarchy, components):
    """The joints of ``rig`` that carry a camera, a light or components of the camera package, in joint order.
    ``components`` maps a node index to the components of the camera package it carries (``cinemachine.by_node``)."""
    found = []
    for joint, index in enumerate(rig.members):
        node = hierarchy.nodes[index]
        held = tuple(components.get(index, ()))
        if held or node.camera is not None or node.light is not None:
            active = rigs.active_within(hierarchy, rig, index)
            found.append(Carried(index, joint, active, held, _key(node, joint, active, held)))
    return found


def _joint(prim, token, active):
    prim.CreateAttribute("ruri:joint", Sdf.ValueTypeNames.Token, custom=True).Set(token)
    prim.CreateAttribute("ruri:activeSelf", Sdf.ValueTypeNames.Bool, custom=True).Set(bool(active))


def author(stage, root_path, rig, carried, hierarchy, names, counts=None):
    """What ``carried`` states, as child prims of the rig root at ``root_path``. ``counts`` tallies ``prims`` and ``attributes``
    of the components' records. Returns the number of prims written."""
    counts = counts if counts is not None else {"prims": 0, "attributes": 0}
    node = hierarchy.nodes[carried.node]
    token = rig.tokens[carried.joint]
    written = 0
    if carried.components or node.camera is not None:
        holder = stage.DefinePrim(root_path.AppendChild(names.claim(str(root_path), CAMERA_NAME)), "Scope")
        _joint(holder, token, carried.active)
        if node.camera is not None:
            sources.camera(holder, node)
        if carried.components:
            cinemachine_module.author(stage, holder, carried.components, names, counts)
        written += 1
    if node.light is not None:
        holder = stage.DefinePrim(root_path.AppendChild(names.claim(str(root_path), LIGHT_NAME)), "Scope")
        _joint(holder, token, carried.active)
        sources.light(holder, node)
        written += 1
    return written
