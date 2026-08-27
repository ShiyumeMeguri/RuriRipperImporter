"""Write one model's authored secondary motion onto a rig in this scene.

Nothing here knows a parameter name: every scalar goes through the table in
``cloth``, so the set of parameters this transfers is a property of that table and
of nothing else. What IS here is the two shape changes the two sides genuinely
disagree on -- a curve stated as keyframes against one stated as a point list, and
a capsule stated as one component against one stated as two objects.
"""

from __future__ import annotations


import bpy
import mathutils

from . import cloth

COLLECTION_SUFFIX = "Colliders"
CURVE_HANDLE = 'VECTOR'
MINIMUM_RADIUS = 1e-5

AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

# Unity is Y up and left handed, Blender is Z up and right handed; a point of one
# reads as the other by this swap, which is the same one the mesh import uses.
def _point(vector):
    return mathutils.Vector((vector[0], -vector[2], vector[1]))


def _quaternion(values):
    x, y, z, w = values
    return mathutils.Quaternion((w, x, -z, y))


class Report:
    def __init__(self):
        self.configs = 0
        self.values = 0
        self.curves = 0
        self.colliders = 0
        self.attributes = 0
        self.missing_bones = set()
        self.unknown_paths = {}
        self.unsupported_colliders = []
        self.worst_attribute_error = 0.0
        self.table = ""

    def lines(self):
        found = ["%d 条配置, %d 个参数, %d 条曲线, %d 个碰撞体, %d 条逐骨骼属性%s"
                 % (self.configs, self.values, self.curves, self.colliders, self.attributes,
                    ("; 骨骼名经 %s" % self.table) if self.table else "; 骨骼名直接对应")]
        if self.worst_attribute_error:
            found.append("逐骨骼属性回接最大偏差 %.4f m" % self.worst_attribute_error)
        if self.missing_bones:
            found.append("骨骼预设里没有的名字 %d 个: %s"
                         % (len(self.missing_bones), ", ".join(sorted(self.missing_bones)[:8])))
        if self.unknown_paths:
            found.append("映射表没有的字段 %d 个: %s"
                         % (len(self.unknown_paths), ", ".join(sorted(self.unknown_paths)[:8])))
        if self.unsupported_colliders:
            found.append("形状不认识的碰撞体 %d 个" % len(self.unsupported_colliders))
        return found


def _assign(owner, path, value):
    parts = path.split(".")
    for name in parts[:-1]:
        owner = getattr(owner, name)
    leaf = parts[-1]
    if leaf.isdigit():
        owner[int(leaf)] = value
        return
    setattr(owner, leaf, value)


def _coerce(kind, table, number, path):
    if kind == cloth.BOOLEAN:
        return number != 0.0
    if kind == cloth.ENUM:
        identifier = table.get(int(round(number)))
        if identifier is None:
            raise ValueError("字段 %s 的值 %g 不在已知取值里, 已知的是 %s"
                             % (path, number, sorted(table)))
        return identifier
    return number


def _curve_points(samples):
    step = 1.0 / (cloth.CURVE_SAMPLES - 1)
    return ";".join("%.6g,%.6g,%s" % (index * step, value, CURVE_HANDLE)
                    for index, value in enumerate(samples))


def _capsule_ends(collider):
    """The two ends of a capsule, in the frame of the bone it hangs under.

    The source states one component: a centre, an axis, a length and a radius at
    each end. This add-on states two objects, one per end, each carrying its own
    radius -- so the conversion is to walk half the length out along the axis in
    both directions, or the whole length in one, which is what the source's own
    switch chooses between.
    """
    axis = mathutils.Vector(AXES[collider["direction"] % 3])
    length = collider["size"][2]
    centre = mathutils.Vector(collider["center"])
    if collider["aligned_on_center"]:
        first = centre - axis * (length * 0.5)
        second = centre + axis * (length * 0.5)
    else:
        first = centre
        second = centre + axis * length
    rotation = _quaternion((collider["local_rotation"]))
    offset = _point(collider["local_position"])
    return (offset + rotation @ _point(first), offset + rotation @ _point(second))


def _radii(collider):
    start = max(float(collider["size"][0]), MINIMUM_RADIUS)
    end = max(float(collider["size"][1]), MINIMUM_RADIUS) \
        if collider["radius_separation"] else start
    return start, end


def _collection_for(scene, rig):
    name = "%s.%s" % (rig.name, COLLECTION_SUFFIX)
    for collection in scene.collection.children_recursive:
        if collection.name == name:
            return collection
    collection = bpy.data.collections.new(name)
    scene.collection.children.link(collection)
    return collection


def _clear_colliders(scene, rig):
    name = "%s.%s" % (rig.name, COLLECTION_SUFFIX)
    for collection in list(scene.collection.children_recursive):
        if collection.name != name:
            continue
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)


def _empty(collection, name, display_type, radius):
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = display_type
    empty.empty_display_size = max(float(radius), MINIMUM_RADIUS)
    collection.objects.link(empty)
    return empty


def _follow_bone(view_layer, empty, rig, bone_name, location):
    view_layer.update()
    constraint = empty.constraints.new('CHILD_OF')
    constraint.name = "Ruri Follow Bone"
    constraint.target = rig
    constraint.subtarget = bone_name
    view_layer.update()
    pose_bone = rig.pose.bones.get(bone_name)
    space = rig.matrix_world @ pose_bone.matrix if pose_bone is not None \
        else mathutils.Matrix.Identity(4)
    constraint.inverse_matrix = space.inverted_safe()
    empty.matrix_basis = mathutils.Matrix.Translation(space @ location)
    view_layer.update()


def apply(context, rig, reading, bone_names, report):
    """Replace every configuration on ``rig`` with the ones the model states."""
    scene = context.scene
    view_layer = context.view_layer
    settings = rig.ruri_cloth_physics
    settings.configs.clear()
    _clear_colliders(scene, rig)

    collection = None
    collider_objects = {}
    for entry in reading["colliders"]:
        bone = bone_names.of(entry["bone"])
        if not bone or bone not in rig.data.bones:
            report.missing_bones.add(entry["bone"])
            continue
        if entry["kind"] != "CAPSULE":
            report.unsupported_colliders.append(entry["index"])
            continue
        if collection is None:
            collection = _collection_for(scene, rig)
        start_radius, end_radius = _radii(entry)
        first, second = _capsule_ends(entry)
        base = "%s.%s" % (rig.name, bone)
        start = _empty(collection, "Capsule.%s.01" % base, 'CIRCLE', start_radius)
        end = _empty(collection, "Capsule.%s.02" % base, 'CIRCLE', end_radius)
        for empty, location in ((start, first), (end, second)):
            _follow_bone(view_layer, empty, rig, bone, location)
        holder = start.ruri_cloth_physics_collider
        holder.is_collider = True
        holder.shape = 'CAPSULE'
        holder.enabled = True
        holder.end_object = end
        collider_objects[entry["index"]] = start
        report.colliders += 1

    by_config = {}
    for entry in reading["config_colliders"]:
        by_config.setdefault(entry["config"], []).append(entry["collider"])

    values_by_config = {}
    for entry in reading["values"]:
        values_by_config.setdefault(entry["config"], []).append(entry)
    curves_by_config = {}
    for entry in reading["curves"]:
        curves_by_config.setdefault(entry["config"], []).append(entry)
    bones_by_config = {}
    for entry in reading["bones"]:
        bones_by_config.setdefault(entry["config"], []).append(entry)
    attributes_by_config = {}
    for entry in reading["attributes"]:
        attributes_by_config.setdefault(entry["config"], []).append(entry)

    for declared in reading["configs"]:
        index = declared["index"]
        config = settings.configs.add()
        config.name = declared["name"]
        config.enabled = True
        report.configs += 1

        for entry in values_by_config.get(index, ()):
            row = cloth.VALUE_BY_SOURCE.get(entry["path"])
            if row is None:
                if entry["path"] not in cloth.UNMAPPED:
                    report.unknown_paths[entry["path"]] = entry["value"]
                continue
            _assign(config, row[1], _coerce(row[2], row[3], entry["value"], entry["path"]))
            report.values += 1

        for entry in curves_by_config.get(index, ()):
            target = cloth.CURVE_BY_SOURCE.get(entry["path"])
            if target is None:
                if entry["path"] not in cloth.UNMAPPED:
                    report.unknown_paths[entry["path"]] = entry["value"]
                continue
            holder = config
            for name in target.split("."):
                holder = getattr(holder, name)
            holder.value = entry["value"]
            holder.use_curve = entry["use"]
            if entry["use"]:
                holder.points_serialized = _curve_points(entry["samples"])
            report.curves += 1

        for entry in bones_by_config.get(index, ()):
            bone = bone_names.of(entry["bone"])
            if not bone or bone not in rig.data.bones:
                report.missing_bones.add(entry["bone"])
                continue
            if entry["role"] == cloth.ROOT_ROLE:
                config.root_bones.add().bone = bone
            elif entry["role"] == cloth.SKINNING_ROLE:
                config.skinning_bones.add().bone = bone
            elif entry["role"] == cloth.COLLISION_ROLE:
                config.collider_collision.collision_bones.add().bone = bone
            elif entry["role"] == cloth.IGNORE_ROLE:
                override = config.attribute_overrides.add()
                override.bone = bone
                override.attribute = 'IGNORE'

        for entry in attributes_by_config.get(index, ()):
            bone = bone_names.of(entry["bone"])
            if not bone or bone not in rig.data.bones:
                report.missing_bones.add(entry["bone"])
                continue
            override = config.attribute_overrides.add()
            override.bone = bone
            override.attribute = cloth.ATTRIBUTE_KINDS.get(entry["attribute"], 'MOVE')
            report.attributes += 1
            report.worst_attribute_error = max(report.worst_attribute_error, entry["error"])

        for collider_index in by_config.get(index, ()):
            empty = collider_objects.get(collider_index)
            if empty is not None:
                config.collider_collision.collider_references.add().object = empty

        config.rebuild_pending = True

    settings.active_config_index = 0
    return report
