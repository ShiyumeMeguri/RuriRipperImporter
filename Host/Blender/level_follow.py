"""A level linked from another file, under the world this file lights it with.

A level can be kept whole in one file while other files link what they need of it under a world of their own: the level's
state stood up at another viewer's spot, with that spot's irradiance, reflection probes and phase. Its globals reach linked
materials by themselves, since they read them off the scene's world as view-layer attributes. Two things are bound tighter
and cannot be saved from the linking file: the level images, which the linked shader groups point at directly, and the
box lists, which are per-object globals on the linked objects. Linked data can be changed in memory, so when a file opens
or its world changes they are made to say what the world states. An image takes the world's texels into its own buffer, so
no node changes and no shader rebuilds. A world from the level's own file finds everything already saying so and changes
nothing.
"""

from __future__ import annotations

import json

import bpy
import numpy

from . import material_builder

#: Which stated image each linked image follows in this session, by session id: comparing texels again is not free.
_FOLLOWED = {}


def follow(scene):
    """Make the level images and per-object globals in memory say what the world of ``scene`` states. Returns how many
    images and objects changed."""
    world = scene.world
    if world is None:
        return 0
    return _follow_images(world) + _follow_object_globals(world)


def _same_texels(image, stated):
    """Tile sets answer by size alone: each tile is an OpenEXR stamped with the time it was written, so the same tiles
    never match byte for byte, and the same tiles compress to the same size."""
    ours = [entry.packed_file for entry in image.packed_files]
    theirs = [entry.packed_file for entry in stated.packed_files]
    return len(ours) == len(theirs) and all(mine.size == other.size for mine, other in zip(ours, theirs)) and (
        stated.source == "TILED" or all(mine.data == other.data for mine, other in zip(ours, theirs)))


def _follow_images(world):
    held = world.get(material_builder.LEVEL_IMAGES_PROPERTY)
    if held is None:
        return 0
    stated = {key: held[key] for key in held.keys() if held[key] is not None}
    changed = 0
    for image in bpy.data.images:
        own = stated.get(image.name)
        if own is None or own == image or _FOLLOWED.get(image.session_uid) == own.session_uid:
            continue
        if not _same_texels(image, own):
            if own.source == "TILED":
                raise RuntimeError("[level-follow] level image {0} is a tile set and differs from the one {1} states: a tile "
                                   "set cannot be handed over in memory".format(image.name, world.name))
            _take_texels(image, own)
            changed += 1
        _FOLLOWED[image.session_uid] = own.session_uid
    return changed


def _take_texels(image, own):
    width, height = own.size
    if tuple(image.size) != (width, height):
        image.scale(width, height)
    texels = numpy.empty(width * height * own.channels, dtype=numpy.float32)
    own.pixels.foreach_get(texels)
    image.pixels.foreach_set(texels)
    image.update()
    own.buffers_free()


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
