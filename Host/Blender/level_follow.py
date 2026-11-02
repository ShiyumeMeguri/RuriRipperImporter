"""A level linked from another file, under the world this file lights it with.

A level can be kept whole in one file while other files link what they need of it under a world of their own: the level's
state stood up at another viewer's spot, with that spot's irradiance, reflection probes and phase. Its globals reach linked
materials by themselves, since they read them off the scene's world as view-layer attributes. Two things are bound tighter
and cannot be saved from the linking file: the level images, which the linked shader groups point at directly, and the
box lists, which are per-object globals on the linked objects. Linked data can be changed in memory, so when a file opens
or its world changes they are made to say what the world states. Every node that reads a level image reads the very image
the world states, as the source binds one texture for the whole level: the copy each linked file carries under the same
name is never uploaded. No shader changes with it, since a node's image is a binding and not code.
"""

from __future__ import annotations

import json

import bpy

from . import material_builder

#: The image each node read before it followed a world in this session, by its tree's session id, its name and the
#: socket it binds the image on ("" for the node's own image).
_OWN = {}


def follow(scene):
    """Make the level images and per-object globals in memory say what the world of ``scene`` states. Returns how many
    image bindings and objects changed."""
    world = scene.world
    if world is None:
        return 0
    return _follow_images(world) + _follow_object_globals(world)


def release():
    """Point every node that followed a world back at the image its own file binds."""
    trees = {tree.session_uid: tree for tree in _trees()}
    for (tree_uid, node_name, socket), own in _OWN.items():
        tree = trees.get(tree_uid)
        node = tree.nodes.get(node_name) if tree is not None else None
        if node is not None:
            _bind(node, socket, own)
    _OWN.clear()


def _trees():
    yield from bpy.data.node_groups
    for material in bpy.data.materials:
        if material.node_tree is not None:
            yield material.node_tree


def _bound(node):
    """(socket identifier, or "" for the node's own image; image) for every image ``node`` binds."""
    image = getattr(node, "image", None)
    if isinstance(image, bpy.types.Image):
        yield "", image
    for socket in node.inputs:
        if socket.type == "IMAGE" and not socket.is_linked and socket.default_value is not None:
            yield socket.identifier, socket.default_value


def _bind(node, socket, image):
    if socket:
        next(entry for entry in node.inputs if entry.identifier == socket).default_value = image
    else:
        node.image = image


def _follow_images(world):
    held = world.get(material_builder.LEVEL_IMAGES_PROPERTY)
    if held is None:
        return 0
    stated = {key: held[key] for key in held.keys() if held[key] is not None}
    changed = 0
    for tree in _trees():
        for node in tree.nodes:
            for socket, image in list(_bound(node)):
                own = stated.get(image.name)
                if own is None or own == image:
                    continue
                _OWN.setdefault((tree.session_uid, node.name, socket), image)
                _bind(node, socket, own)
                changed += 1
    return changed


def _follow_object_globals(world):
    stated = world.get(material_builder.LEVEL_OBJECT_GLOBALS_PROPERTY)
    if stated is None:
        return 0
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    changed = 0
    for name, rows in json.loads(stated).items():
        for obj in meshes:
            row = rows.get(obj.name)
            held = obj.get(name)
            if row is None:
                if held is None:
                    continue
                del obj[name]
            elif held is not None and list(held) == row:
                continue
            else:
                obj[name] = row
            obj.update_tag()
            changed += 1
    return changed
