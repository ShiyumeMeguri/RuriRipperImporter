"""A level's boxes, handed to the objects they reach -- its deferred decals, its reflection probes' boxes.

The source culls such boxes per pixel before shading (the decal pass projects each box into its GBuffer;
the probe binning marks the boxes that reach each froxel); a host shading each surface in one pass
walks them inside the material instead, and a material can only afford the boxes that can reach its
own object. Each object's list is the indices -- in the order the boxes are given, which is the order
the shading walks them -- of the boxes whose world bounds meet the object's world bounds: a
conservative cut, since the material tests every pixel against the box itself. Only objects with a
material that reads the per-object global get a list -- which materials read it is whatever their
graphs say (an object attribute node of that name, at any group depth).

Objects reaching the same boxes share one run: the distinct lists go end to end into one data table,
and each object carries where its run starts and how many entries it has as a per-object global (the
value minus the default the stacks declare, the encoding every per-object global uses). An object no
box reaches carries an empty run -- and, when that equals the declared default, nothing at all, so it
reads the default. The runs go with the level's world too: which boxes a viewer binds changes with where
it stands, so a file lighting linked objects through its own world hands them the runs that world holds
(see :mod:`level_follow`).
"""
import numpy
from mathutils import Matrix, Vector

from . import material_builder

_UNIT_CORNERS = [Vector((x, y, z, 1.0)) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]


def apply(context, boxes, range_attribute, list_table):
    """Bind ``boxes`` -- each the column-major matrix taking the unit cube onto a box in the SOURCE's
    world, in the order the shading walks them -- to the objects they reach, through the per-object
    global ``range_attribute`` and the data table ``list_table`` the stacks read. Returns the lines to
    report."""
    base = material_builder.object_attribute_base(range_attribute)
    layout = material_builder.level_image_layout(list_table)
    if base is None or layout is None:
        return ["no shading stack reads box lists ({0}, {1})".format(range_attribute, list_table)]
    basis = Matrix(material_builder.world_basis()).to_4x4()
    bounds = [_bounds([(basis @ (_matrix(columns) @ corner)).xyz for corner in _UNIT_CORNERS]) for columns in boxes]
    depsgraph = context.evaluated_depsgraph_get()
    readers = {}
    runs = {}
    entries = []
    reached = 0
    stated = {}
    for obj in context.scene.objects:
        if obj.type != "MESH":
            continue
        if not any(slot.material is not None and slot.material.node_tree is not None
                   and _reads(slot.material.node_tree, range_attribute, readers) for slot in obj.material_slots):
            if range_attribute in obj:
                del obj[range_attribute]
            continue
        evaluated = obj.evaluated_get(depsgraph)
        low, high = _bounds([evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box])
        hits = tuple(index for index, (box_low, box_high) in enumerate(bounds)
                     if all(low[axis] <= box_high[axis] and box_low[axis] <= high[axis] for axis in range(3)))
        first = 0
        if hits:
            first = runs.get(hits)
            if first is None:
                first = runs[hits] = len(entries)
                entries.extend(hits)
            reached += 1
        value = [float(first), float(len(hits)), 0.0, 0.0]
        if value == list(base):
            if range_attribute in obj:
                del obj[range_attribute]
            continue
        stated[obj.name] = obj[range_attribute] = [component - default for component, default in zip(value, base)]
    capacity = int(layout["size"][1])
    if len(entries) > capacity:
        raise ValueError("[box-lists] {0} entries in {1} distinct run(s) overflow {2}, which holds {3}".format(
            len(entries), len(runs), list_table, capacity))
    rows = numpy.zeros((len(entries), 1, 4), dtype=numpy.float32)
    rows[:, 0, 0] = entries
    material_builder.write_level_table(context.scene, list_table, rows)
    material_builder.state_object_global(context.scene, range_attribute, stated)
    return (["{0} box(es) of {1} reach {2} object(s) through {3} distinct run(s)".format(
        len(boxes), list_table, reached, len(runs))] if boxes else [])


def _reads(tree, name, readers):
    """Whether ``tree`` reads the per-object global ``name``, through any depth of groups (memoised per tree)."""
    key = tree.as_pointer()
    known = readers.get(key)
    if known is None:
        known = readers[key] = any(
            (node.bl_idname == "ShaderNodeAttribute" and node.attribute_type == "OBJECT" and node.attribute_name == name)
            or (node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None and _reads(node.node_tree, name, readers))
            for node in tree.nodes)
    return known


def _matrix(columns):
    """A column-major 4x4 as a matrix."""
    return Matrix([columns[0:4], columns[4:8], columns[8:12], columns[12:16]]).transposed()


def _bounds(points):
    return (Vector(tuple(min(point[axis] for point in points) for axis in range(3))),
            Vector(tuple(max(point[axis] for point in points) for axis in range(3))))
