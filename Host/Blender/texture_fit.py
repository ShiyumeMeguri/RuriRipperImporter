"""A level's textures fitted to the film shot in it: the top mips no frame of the film samples go.

The source keeps only the mips its streaming feedback says the view samples and holds the rest on disk. A host that
renders with every mip of every image in video memory overflows the card long before the source would, and the overflow
slows the renderer more than anything else in the frame. A film's camera path is known in advance, so the same answer is
computed once: per image, the finest density of screen pixels per metre any frame gives any object using it, times the
metres one repeat of the image covers on that object, is how many texels the image needs across. What is above that is
never sampled.

An explicit step, never part of an import: it is true of this camera path at this output size only. The kept levels hold
what the GPU's own mip chain held (a 2x2 box per level, in linear light for sRGB colour, on the raw values for data and
alpha), so what a frame samples stays what it sampled wherever the estimate holds.

Run as a script on the file that holds a film's world, it applies a table to that file (see :func:`fit`).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile

import bpy
import numpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

#: How many texels per screen pixel the worst spot keeps. Half: at the nearest frame of the nearest object one texel
#: covers two pixels, which measured as no visible change because that spot is rarely where the film looks.
MARGIN = 0.5
#: Below this the top level is not worth dropping.
MINIMUM_SIDE = 64
#: A clipped caster's leaf shape lands on whatever receives its shadow, wherever that is on screen: its alpha is held to
#: this many screen pixels per metre whether the caster is seen or not.
SHADOW_DENSITY = 300.0
#: Frames between two looks at the camera; a camera path does not jump further than this between cuts that matter.
FRAME_STEP = 8
#: Nearer than this an object's bounds say too little, so its triangles answer.
EXACT_WITHIN = 40.0
#: The share of a slot's area its stretch speaks for: a sliver with a collapsed UV does not speak for the slot.
STRETCH_SHARE = 0.99
MATERIAL_IMAGES = "ruri_uber_images"
MATERIAL_FLOATS = "ruri_uber_floats"
MATERIAL_TILING = "ruri_uber_st"
_NEAREST_METRES = 0.05


def _half_picture(camera, scene):
    """How far the picture reaches per metre ahead, across and up, the way the render frames it: its aspect and the
    camera's sensor fit are in it."""
    top_right, _bottom_right, bottom_left = camera.data.view_frame(scene=scene)[:3]
    ahead = abs(top_right.z)
    return (top_right.x - bottom_left.x) / 2.0 / ahead, (top_right.y - bottom_left.y) / 2.0 / ahead


def _camera_path(scene):
    """Where the scene's camera stands, looks and how wide it sees, every few frames of the scene's range."""
    keep = scene.frame_current
    positions, forwards, tangents = [], [], []
    for frame in range(scene.frame_start, scene.frame_end + 1, FRAME_STEP):
        scene.frame_set(frame)
        evaluated = scene.camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
        matrix = numpy.array(evaluated.matrix_world)
        positions.append(matrix[:3, 3])
        forwards.append(-matrix[:3, 2] / numpy.linalg.norm(matrix[:3, 2]))
        tangents.append(_half_picture(evaluated, scene))
    scene.frame_set(keep)
    return numpy.array(positions), numpy.array(forwards), numpy.array(tangents)


class _Film:
    """How many screen pixels per metre the film can give an object."""

    def __init__(self, scene):
        self.positions, self.forwards, self.tangents = _camera_path(scene)
        self.height = scene.render.resolution_y * scene.render.resolution_percentage / 100.0
        self.cone = numpy.arctan(numpy.hypot(self.tangents[:, 0], self.tangents[:, 1]))
        self.trees = {}

    def density(self, obj, matrix, depsgraph):
        """Most pixels per metre any frame gives ``obj``, 0 when it is never in view. The distance is to its nearest
        triangle in that frame: a long wall counts where the camera comes closest, and a camera inside a building's
        bounds does not touch it."""
        corners = numpy.array([list(corner) for corner in obj.bound_box], dtype=numpy.float64)
        corners = corners @ matrix[:3, :3].T + matrix[:3, 3]
        low, high = corners.min(axis=0), corners.max(axis=0)
        towards = (low + high) / 2.0 - self.positions
        length = numpy.maximum(numpy.linalg.norm(towards, axis=1), 1e-6)
        angle = numpy.arccos(numpy.clip((towards * self.forwards).sum(axis=1) / length, -1.0, 1.0))
        reach = numpy.arcsin(numpy.minimum(float(numpy.linalg.norm(high - low)) / 2.0 / length, 1.0))
        seen = angle - reach <= self.cone
        if not seen.any():
            return 0.0
        distance = numpy.linalg.norm(self.positions - numpy.clip(self.positions, low, high), axis=1)
        density = self.height / (2.0 * self.tangents[:, 1] * numpy.maximum(distance, _NEAREST_METRES))
        if distance[seen].min() >= EXACT_WITHIN:
            return float(density[seen].max())
        tree = self.trees.get(obj.data)
        if tree is None:
            tree = self.trees[obj.data] = BVHTree.FromObject(obj, depsgraph)
        inverse = numpy.linalg.inv(matrix)
        rows = numpy.flatnonzero(seen)
        local = self.positions[rows] @ inverse[:3, :3].T + inverse[:3, 3]
        best = 0.0
        for row, point in zip(rows, local):
            if density[row] <= best:
                continue
            nearest = tree.find_nearest(Vector(point))[0]
            if nearest is not None:
                gap = float(numpy.linalg.norm(matrix[:3, :3] @ (numpy.array(nearest) - point)))
                best = max(best, self.height / (2.0 * self.tangents[row, 1] * max(gap, _NEAREST_METRES)))
        return best


class _Stretch:
    """Metres of an object per one UV unit, per material slot and UV layer. In world space, so a flat caster's stretched
    mesh answers the same as the mesh it stands for."""

    def __init__(self):
        self.meshes = {}
        self.answers = {}

    def _mesh(self, mesh):
        held = self.meshes.get(mesh)
        if held is not None:
            return held
        mesh.calc_loop_triangles()
        count = len(mesh.loop_triangles)
        held = None
        if count and len(mesh.uv_layers):
            vertices = numpy.empty(count * 3, dtype=numpy.int32)
            loops = numpy.empty(count * 3, dtype=numpy.int32)
            slots = numpy.empty(count, dtype=numpy.int32)
            mesh.loop_triangles.foreach_get("vertices", vertices)
            mesh.loop_triangles.foreach_get("loops", loops)
            mesh.loop_triangles.foreach_get("material_index", slots)
            points = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float32)
            mesh.vertices.foreach_get("co", points)
            corners = points.reshape(-1, 3)[vertices].astype(numpy.float64)
            areas = []
            for layer in mesh.uv_layers:
                uv = numpy.empty(len(mesh.loops) * 2, dtype=numpy.float32)
                layer.data.foreach_get("uv", uv)
                uv = uv.reshape(-1, 2)[loops].reshape(-1, 3, 2).astype(numpy.float64)
                first, second = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
                areas.append(numpy.abs(first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) / 2.0)
            held = (corners, slots, areas)
        self.meshes[mesh] = held
        return held

    def of(self, obj, matrix):
        turn = matrix[:3, :3]
        key = (obj.data, tuple(numpy.round(numpy.linalg.norm(turn, axis=0), 16).tolist()))
        answer = self.answers.get(key)
        if answer is not None:
            return answer
        answer = {}
        held = self._mesh(obj.data)
        if held is not None:
            corners, slots, uv_areas = held
            world = (corners @ turn.T).reshape(-1, 3, 3)
            areas = numpy.linalg.norm(numpy.cross(world[:, 1] - world[:, 0], world[:, 2] - world[:, 0]), axis=1) / 2.0
            for layer, uv_area in enumerate(uv_areas):
                valid = (uv_area > 1e-12) & (areas > 1e-12)
                ratio = numpy.zeros(len(areas))
                ratio[valid] = numpy.sqrt(areas[valid] / uv_area[valid])
                for slot in numpy.unique(slots):
                    chosen = (slots == slot) & valid
                    if not chosen.any():
                        continue
                    order = numpy.argsort(ratio[chosen])
                    weights = numpy.cumsum(areas[chosen][order])
                    at = numpy.searchsorted(weights, weights[-1] * STRETCH_SHARE)
                    answer.setdefault(int(slot), {})[layer] = float(ratio[chosen][order][min(at, len(order) - 1)])
        self.answers[key] = answer
        return answer


def _number(floats, name, default):
    value = floats.get(name, default)
    try:
        return float(value)
    except TypeError:
        return float(value[0])


def _metres_per_repeat(slot_name, rule, floats, tiling, per_layer):
    """Metres one repeat of this image covers. ``rule`` is what the stack's kernel samples the slot with."""
    largest = max(per_layer.values()) if per_layer else 0.0
    world_type = _number(floats, rule["WorldType"], 0.0) if "WorldType" in rule else 0.0
    if 0.5 < world_type < 1.5:
        stretch = 1.0
    else:
        layer = 0
        if "Set" in rule:
            layer = 1 if _number(floats, rule["Set"], 0.0) > 0.5 else 0
        elif world_type > 1.5:
            layer = 2
        stretch = per_layer.get(layer, largest)
    stated = tiling.get(rule.get("St", slot_name)) or (1.0, 1.0, 0.0, 0.0)
    tile = max(min(abs(float(stated[0])), abs(float(stated[1]))), 1e-3)
    if "Tile" in rule:
        tile *= max(abs(_number(floats, rule["Tile"], 1.0)), 1e-3)
    return stretch / tile


def plan(scene, slot_rules, clip_switches, shadow_slots):
    """{image: levels to drop} for the film of ``scene``: its camera over its frame range at its output size.
    ``slot_rules`` is how the materials sample each slot ({slot: {Set, St, Tile, WorldType}}, ``Keep`` for a slot read at
    an explicit level), ``clip_switches`` the material values that turn its clip on and ``shadow_slots`` the slots
    a clipped shadow reads. An image something deformed or animated uses stays whole, it has no one place to be seen from;
    so does one in a slot no rule names, since nothing says how it is sampled."""
    film = _Film(scene)
    stretch = _Stretch()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    placed = [(instance.object.original, numpy.array(instance.matrix_world, dtype=numpy.float64))
              for instance in depsgraph.object_instances]
    needed = {}
    whole = set()
    for obj, matrix in placed:
        if obj.type != "MESH" or obj.hide_render or not len(obj.material_slots):
            continue
        drawn, casts = obj.visible_camera, obj.visible_shadow
        moving = bool(obj.modifiers) or obj.parent_type == "ARMATURE" or obj.animation_data is not None
        density = film.density(obj, matrix, depsgraph) if drawn and not moving else 0.0
        per_slot = stretch.of(obj, matrix) if not moving else {}
        for index, slot in enumerate(obj.material_slots):
            material = slot.material
            if material is None or MATERIAL_IMAGES not in material:
                continue
            floats = material.get(MATERIAL_FLOATS) or {}
            tiling = material.get(MATERIAL_TILING) or {}
            clips = any(_number(floats, switch, 0.0) > 0.5 for switch in clip_switches)
            for slot_name, image in material[MATERIAL_IMAGES].items():
                if not isinstance(image, bpy.types.Image):
                    continue
                rule = slot_rules.get(slot_name)
                if moving or rule is None or rule.get("Keep"):
                    whole.add(image)
                    continue
                metres = _metres_per_repeat(slot_name, rule, floats, tiling, per_slot.get(index, {}))
                shadow = SHADOW_DENSITY * metres if casts and clips and slot_name in shadow_slots else 0.0
                needed[image] = max(needed.get(image, 0.0), density * metres, shadow)
    levels = {}
    for image, texels in needed.items():
        if image in whole:
            continue
        width, height = image.size
        side = max(width, height)
        wanted = max(texels * MARGIN, MINIMUM_SIDE)
        drop = 0
        while (side >> (drop + 1)) >= wanted and width % (2 << drop) == 0 and height % (2 << drop) == 0:
            drop += 1
        if drop:
            levels[image] = drop
    return levels


def _to_linear(values):
    return numpy.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def _to_srgb(values):
    return numpy.where(values <= 0.0031308, values * 12.92,
                       1.055 * numpy.power(numpy.maximum(values, 1e-9), 1.0 / 2.4) - 0.055)


def drop_mips(image, levels):
    """Make the image its own mip ``levels`` down, the way the GPU builds that mip, and pack it."""
    width, height = image.size
    data = numpy.empty(width * height * 4, dtype=numpy.float32)
    image.pixels.foreach_get(data)
    data = data.reshape(height, width, 4)
    colour = image.colorspace_settings.name == "sRGB" and not image.is_float
    if colour:
        data[..., :3] = _to_linear(data[..., :3])
    for _level in range(levels):
        rows, columns = data.shape[:2]
        data = data.reshape(rows // 2, 2, columns // 2, 2, 4).mean(axis=(1, 3), dtype=numpy.float32)
    if colour:
        data[..., :3] = _to_srgb(data[..., :3])
    image.scale(width >> levels, height >> levels)
    image.pixels.foreach_set(data.ravel())
    image.update()
    image.pack()


def _keep_fitted(world, libraries, held_property):
    """Copy each image of ``libraries`` ({file: {image: levels to drop}}) out of its file, fit the copy and leave it on
    ``world``, in place of what an earlier fit left there."""
    held = world.get(held_property)
    bound = {key: held[key] for key in held.keys() if held[key] is not None} if held is not None else {}
    for path, table in libraries.items():
        for name in table:
            stale = bound.pop(name, None)
            if stale is not None:
                bpy.data.images.remove(stale)
        with bpy.data.libraries.load(path, link=False) as (_source, target):
            target.images = list(table)
        for image in target.images:
            drop_mips(image, table[image.name])
            image.buffers_free()
            bound[image.name] = image
    world[held_property] = bound


def _keep_on_linked_world(world, libraries, held_property):
    """The world lives in another file, so a Blender of its own does the work on that file, without the add-ons: they
    would rebuild what the file holds. Its old version stays beside it as .blend1. The session then reads the file again."""
    path = bpy.path.abspath(world.library.filepath, library=world.library.library)
    table = {"World": world.name, "Property": held_property, "Libraries": libraries}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(table, handle, ensure_ascii=False)
    try:
        subprocess.run([bpy.app.binary_path, "-b", path, "--factory-startup", "--python-exit-code", "1",
                        "--python", os.path.abspath(__file__), "--", handle.name], check=True)
    finally:
        os.remove(handle.name)
    world.library.reload()


def fit(scene, slot_rules, clip_switches, shadow_slots):
    """Fit the images ``scene`` draws to its film. An image in this file changes here. A linked image stays whole in the
    file that holds it, since other files link that file too: what this film keeps of it goes on the world that lights
    the film, and a linked image takes what the world holds of its name (see :mod:`level_follow`). Returns the lines to
    report."""
    from . import level_follow, material_builder
    level_follow.release()
    levels = plan(scene, slot_rules, clip_switches, shadow_slots)
    before = after = 0
    local = 0
    libraries = {}
    for image, drop in levels.items():
        width, height = image.size
        before += width * height
        after += (width >> drop) * (height >> drop)
        if image.library is None:
            drop_mips(image, drop)
            local += 1
        else:
            path = bpy.path.abspath(image.library.filepath, library=image.library.library)
            libraries.setdefault(path, {})[image.name] = drop
    if libraries:
        world = material_builder.level_world(scene)
        if world.library is None:
            _keep_fitted(world, libraries, material_builder.LEVEL_IMAGES_PROPERTY)
        else:
            _keep_on_linked_world(world, libraries, material_builder.LEVEL_IMAGES_PROPERTY)
    level_follow.follow(scene)
    return ["{0} image(s) fitted to this film at {1}x{2}, {3:.2f} -> {4:.2f} GB with mips ({5} in this file, {6} kept on its world)".format(
        len(levels), scene.render.resolution_x, scene.render.resolution_y, before * 16 / 3 / 2 ** 30, after * 16 / 3 / 2 ** 30,
        local, len(levels) - local)]


def _apply_table(path):
    with open(path, encoding="utf-8") as handle:
        table = json.load(handle)
    _keep_fitted(bpy.data.worlds[table["World"]], table["Libraries"], table["Property"])
    # What the importer holds at runtime has no user of its own here, and a save writes no block without one.
    for block in bpy.data.user_map():
        if block.library is None and not block.users and not block.use_fake_user:
            block.use_extra_user = True
    bpy.ops.wm.save_mainfile()


if __name__ == "__main__":
    _apply_table(sys.argv[sys.argv.index("--") + 1])
