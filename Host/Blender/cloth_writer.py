"""Write a model's authored secondary motion onto a rig in this scene.

What crosses is the shared vocabulary (:mod:`Kernel.app.rigging`): a parameter is
already named and already coerced when it arrives here, so nothing below knows a
game's field names and no game knows this solver's. What IS here is the three
things only this host can answer -- what this rig calls the bone the model names, a
curve stated as a point list against one stated as keyframes, and a capsule stated
as one component against one stated as two objects.

Which bone is which is RESOLVED, never chosen: the session says which game the
model came from and the rig carries which skeleton it is, which is the same pair
the animation path already joins through the same preset files. A rig that IS the
model's own skeleton joins to nothing and the names pass through unchanged, which
is the right answer for that case rather than a missing one.
"""

from __future__ import annotations

import json
import pathlib

import bpy
import mathutils

from . import armature_builder, coordinate
from ...Kernel.app import rigging
from ...RuriRipperPyBridge.session import cabmap_state

#: The property the cloth add-on hangs its settings on. Its absence is what "this
#: application cannot hold secondary motion" means in practice.
CLOTH_ADDON_ATTRIBUTE = "ruri_cloth_physics"

COLLECTION_SUFFIX = "Colliders"
CURVE_HANDLE = 'VECTOR'
MINIMUM_RADIUS = 1e-5

AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

SKELETON_PROP = "ruri_skeleton"
PRESET_FOLDER = "AnimationRetarget"

#: Shared role -> where a config keeps the bones that play it.
_ROLE_COLLECTIONS = {
    rigging.ROOT_ROLE: ("root_bones", None),
    rigging.SKINNING_ROLE: ("skinning_bones", None),
    rigging.COLLISION_ROLE: ("collision_bones", "collider_collision"),
}


def available():
    """Whether a rig in this scene can hold these settings at all. The reader is
    this add-on's; the settings themselves belong to the cloth add-on, and with it
    absent there is nowhere to put them."""
    return hasattr(bpy.types.Object, CLOTH_ADDON_ATTRIBUTE)


def _point(vector):
    return coordinate.conversion_matrix() @ mathutils.Vector(vector)


def _quaternion(values):
    x, y, z, w = values
    return mathutils.Quaternion((w, x, y, z))


BONE_BASIS_REASON = (
    "a collider is stated in the frame of the bone it hangs under, so placing it needs the "
    "one rotation that carries that frame into this rig's -- and nothing else. It is built "
    "from the MODEL's own bone pose, published beside the collider, against the bone as it "
    "stands here: for the character the model IS, the two are one skeleton, so the pair is "
    "a pure change of axes whose origins coincide. What it must not be built from is "
    "anything stamped on the rig, because the animation path aligns a rig before it answers "
    "and that moves the bones, leaving every earlier record describing a pose that is gone")

BASIS_TAKES_MODEL_AXES_REASON = (
    "the points this basis is handed are the collider's own numbers, and those are stated "
    "in the model's axes, not in this host's: the game says Y is up and states a capsule's "
    "centre and its direction axis in that frame, so the change of axes has to happen "
    "between the collider's numbers and the bone, which is why the model's bone pose is "
    "carried across by C and NOT conjugated by it -- a conjugation C M C is the same "
    "transform seen from here, and would be right for a point that had already been "
    "converted, but every point coming through here still has to make that trip; the "
    "difference is invisible on any collider whose numbers are X only, because C swaps Y "
    "and Z and leaves X alone, which is exactly the set of colliders that used to look "
    "correct while the ones with a Y or Z offset sat a swap away from where the game "
    "puts them and the Z-axis capsules pointed along the host's Y")

BIND_AGAINST_REST_REASON = (
    "the collider's offset is stated against the bone the model binds it to, so the frame "
    "this binding is taken in is the REST pose and never whatever pose the bone is wearing "
    "when somebody presses the button: a rig with the cloth running has bones carrying a "
    "solved pose, and binding against that freezes one frame of a simulation into the "
    "collider's own resting place, which then never comes back -- it is what left this "
    "file's skirt and tail colliders holding a scale of 0.92 to 1.03 with the rig standing "
    "still; the scale channels are off for the same reason from the other side: a bone the "
    "cloth solver drives carries a stretch because a connected bone's head can only be "
    "moved by stretching its parent, and that stretch is how this host reaches a position, "
    "not a size the model states -- the game's own bones are all scale 1, and a collider "
    "that breathed with the chain would change the radius the game authored")


# ---------------------------------------------------------------------------
# What this rig calls each bone the model names
# ---------------------------------------------------------------------------
def _preset_files():
    for folder in bpy.utils.script_paths(subdir="presets"):
        directory = pathlib.Path(folder) / PRESET_FOLDER
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                yield path


def _family_names(end):
    if not isinstance(end, dict):
        return set()
    names = {str(end.get("family", ""))}
    names.update(str(alias) for alias in end.get("aliases") or ())
    return {name.lower() for name in names if name}


def _joins(document, source, dest):
    return (source.lower() in _family_names(document.get("source"))
            and dest.lower() in _family_names(document.get("dest")))


class BoneNames:
    """What the rig in the scene calls each bone the model names."""

    def __init__(self, mapping, label):
        self._mapping = dict(mapping)
        self.label = label
        self.missing = set()

    @classmethod
    def resolve(cls, session_key, rig):
        """The rename table joining the model's skeleton to this rig's, read as a FILE.

        Deliberately not the animation path's resolver: that one aligns the two
        skeletons before it answers, which re-poses the rig -- and a collider is
        placed relative to a bone, so a rig that moved under it lands the collider
        somewhere else. All that is wanted here is the pair of names, which the
        preset states outright.
        """
        source = str(cabmap_state.game_of(session_key) or "")
        dest = str(rig.get(SKELETON_PROP) or "") or armature_builder.read_game(rig)
        if not source or not dest:
            return cls({}, "")
        bones = {bone.name for bone in rig.data.bones}
        best = ({}, "", -1)
        for path in _preset_files():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not _joins(document, source, dest):
                continue
            pairs = {}
            for entry in document.get("mappings") or ():
                name = str(entry.get("source", ""))
                target = str(entry.get("dest", ""))
                if name and target:
                    pairs[name] = target
            # One family ships several builds (a girl's and a boy's), and they declare
            # the same two families -- so which one this rig IS is settled by how many
            # of its bones the table actually names, never by the file's name.
            hits = sum(1 for target in pairs.values() if target in bones)
            if hits > best[2]:
                best = (pairs, path.stem, hits)
        return cls(best[0], best[1])

    def __len__(self):
        return len(self._mapping)

    def of(self, name):
        if not self._mapping:
            return name
        found = self._mapping.get(name)
        if found is None:
            self.missing.add(name)
        return found


def _bone_basis(rig, bone_name, collider):
    """Model bone-local, in the MODEL's axes -> this rig's bone-local, in this host's.

    Returns the basis and how far the two frames' origins ended up apart -- one skeleton
    puts that at zero, and anything else means the pair does not describe one bone."""
    bone = rig.data.bones.get(bone_name)
    if bone is None:
        return None, 0.0
    rotation = _quaternion(collider["bone_rotation"]).normalized()
    source = mathutils.Matrix.Translation(collider["bone_position"]) \
        @ rotation.to_matrix().to_4x4()
    here = coordinate.conversion_matrix() @ source
    return (bone.matrix_local.inverted_safe() @ here,
            (here.translation - bone.matrix_local.translation).length)


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
        self.unplaced_colliders = []
        self.worst_bone_gap = 0.0
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
            found.append("共用词汇里没有的字段 %d 个: %s"
                         % (len(self.unknown_paths), ", ".join(sorted(self.unknown_paths)[:8])))
        if self.unsupported_colliders:
            found.append("形状不认识的碰撞体 %d 个" % len(self.unsupported_colliders))
        if self.worst_bone_gap > 1e-4:
            found.append("碰撞体宿主骨与模型对不上, 最大原点偏差 %.4f m —— 这不是同一副骨架"
                         % self.worst_bone_gap)
        if self.unplaced_colliders:
            found.append("骨架没有记录来源姿势, 放不下的碰撞体 %d 个"
                         % len(self.unplaced_colliders))
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


def _curve_points(samples):
    """A curve stated as N evenly spaced samples, as this add-on's point list. The
    count comes from the list itself: how many a game publishes is that game's."""
    step = 1.0 / max(len(samples) - 1, 1)
    return ";".join("%.6g,%.6g,%s" % (index * step, value, CURVE_HANDLE)
                    for index, value in enumerate(samples))


def _capsule_ends(collider):
    """The two sphere centres of a capsule, in the frame of the bone it hangs under.

    Ported from the source's own step-data build rather than reasoned about: the axis
    runs from the start along +dir and the end along -dir, and BOTH ends are pulled in
    by their own radius, because the stated length is the capsule's whole extent
    including the round caps while these two points are the centres of those caps.
    """
    axis = mathutils.Vector(AXES[collider["direction"] % 3])
    if collider["reverse_direction"]:
        axis = -axis
    start_radius, end_radius = _radii(collider)
    length = collider["size"][2]
    aligned = collider["aligned_on_center"]
    start_length = length * 0.5 if aligned else 0.0
    end_length = length * 0.5 if aligned else (length - start_radius)
    start_length = max(start_length - start_radius, 0.0)
    end_length = max(end_length - end_radius, 0.0)
    rotation = _quaternion(collider["local_rotation"])
    origin = mathutils.Vector(collider["local_position"]) \
        + rotation @ mathutils.Vector(collider["center"])
    return (origin + rotation @ (axis * start_length),
            origin - rotation @ (axis * end_length))


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
    constraint.use_scale_x = False
    constraint.use_scale_y = False
    constraint.use_scale_z = False
    bone = rig.data.bones.get(bone_name)
    space = rig.matrix_world @ bone.matrix_local if bone is not None \
        else mathutils.Matrix.Identity(4)
    constraint.inverse_matrix = space.inverted_safe()
    empty.matrix_basis = mathutils.Matrix.Translation(space @ location)
    view_layer.update()


def write(context, rig, reading):
    """Replace every configuration on ``rig`` with the ones the model states.

    Returns the lines to word, or None when this application cannot hold them --
    which is a fact about the application, so it is answered here."""
    if not available():
        return None
    report = Report()
    names = BoneNames.resolve(cabmap_state.active_key(), rig)
    report.table = names.label
    report.unknown_paths.update(reading.get("unknown") or {})
    _apply(context, rig, reading, names, report)
    report.missing_bones.update(names.missing)
    return report.lines()


def _apply(context, rig, reading, bone_names, report):
    scene = context.scene
    view_layer = context.view_layer
    settings = getattr(rig, CLOTH_ADDON_ATTRIBUTE)
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
        basis, origin_gap = _bone_basis(rig, bone, entry)
        if basis is None:
            report.unplaced_colliders.append(entry["index"])
            continue
        report.worst_bone_gap = max(report.worst_bone_gap, origin_gap)
        if collection is None:
            collection = _collection_for(scene, rig)
        start_radius, end_radius = _radii(entry)
        first, second = (basis @ point for point in _capsule_ends(entry))
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
            # The path is the solver's own; a value arrives in the solver's own
            # terms. The ONE thing that still has to happen on this side is a
            # direction, which was stated in the game's axes.
            value = entry["value"]
            if entry["kind"] == rigging.DIRECTION:
                value = _point(value)
            try:
                _assign(config, entry["path"], value)
            except (AttributeError, TypeError, ValueError) as failure:
                # The solver IS the truth about its own field names, so a path it
                # does not carry is reported from where that truth is.
                report.unknown_paths[entry["path"]] = "{0}: {1}".format(
                    type(failure).__name__, failure)
                continue
            report.values += 1

        for entry in curves_by_config.get(index, ()):
            holder = config
            for name in entry["path"].split("."):
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
            if entry["role"] == rigging.IGNORE_ROLE:
                override = config.attribute_overrides.add()
                override.bone = bone
                override.attribute = rigging.IGNORE_ATTRIBUTE
                continue
            collection_name, holder_name = _ROLE_COLLECTIONS.get(entry["role"], (None, None))
            if collection_name is None:
                continue
            holder = getattr(config, holder_name) if holder_name else config
            getattr(holder, collection_name).add().bone = bone

        for entry in attributes_by_config.get(index, ()):
            bone = bone_names.of(entry["bone"])
            if not bone or bone not in rig.data.bones:
                report.missing_bones.add(entry["bone"])
                continue
            override = config.attribute_overrides.add()
            override.bone = bone
            override.attribute = entry["attribute"]
            report.attributes += 1
            report.worst_attribute_error = max(report.worst_attribute_error, entry["error"])

        for collider_index in by_config.get(index, ()):
            empty = collider_objects.get(collider_index)
            if empty is not None:
                config.collider_collision.collider_references.add().object = empty

        config.rebuild_pending = True

    settings.active_config_index = 0
    return report
