"""Drive a rig's face from a ctrl-driver table, and bake those drivers as actions.

A game states its face as CTRL DRIVERS: a named weight that moves things. What a
ctrl moves is a table the game hands over (:mod:`Kernel.app.rigging`) --
per-bone TRS deltas, and a naming rule for the blend shapes a mesh may carry a ctrl
as. Nothing here knows which game wrote that table, what its assets are called, or
where it read them; a second game with a ctrl-driven face brings its own table and
this file does not change.

TWO KINDS OF TARGET, one pass:

``ShapeKeyBinding``  the ctrls a mesh bakes as ``<ctrl><suffix>`` shape keys. Each
                     drives its own key independently, so applying one never
                     disturbs another.
``BoneBinding``      the real payload. Per-ctrl TRS deltas ACCUMULATE -- several
                     ctrls routinely push the same bone -- so a pose is solved for
                     every governed bone at once.

Both are merged into one binding, so a ctrl bound as both drives both.
"""

from __future__ import annotations

import math
import re

import bpy

from . import animation_builder, coordinate, prefab_importer, rig_identity

# Unity's Transform.localEulerAngles composes Z, then X, then Y. mathutils names
# an order by the axis sequence it applies, so that is "ZXY" -- but the base pose
# in a face table is a round trip of the rig's own rest transform, which makes the
# order VERIFIABLE rather than a guess: compose the base with each candidate and
# keep whichever reproduces the armature's rest matrix.
_EULER_ORDER_CANDIDATES = ("ZXY", "XYZ", "YXZ", "ZYX", "XZY", "YZX")
_EULER_SIGN_CANDIDATES = tuple((x, y, z) for x in (1.0, -1.0)
                               for y in (1.0, -1.0) for z in (1.0, -1.0))

#: Session-lived, keyed by armature name so a stale binding from a deleted or
#: replaced rig can never be applied to the wrong object.
_BINDING = {"rig": "", "table": None, "binding": None}


def resolve_rig(context):
    """The armature to drive, by the add-on's ONE rig rule
    (prefab_importer.find_target_armature): the active object's rig -- which a
    skinned MESH stands for just as well as the skeleton, via its Armature
    modifier -- then the selection's single rig, then the scene's only one.
    Returns (armature, error message); this only words the failure."""
    rig = prefab_importer.find_target_armature(context)
    if rig is not None:
        return rig, ""
    if not any(obj.type == "ARMATURE" for obj in context.scene.objects):
        return None, "No armature in the scene -- import a character first."
    return None, ("Ambiguous rig -- select the one whose face you want to drive: its "
                  "skeleton, or any mesh skinned to it.")


def rig_meshes(rig):
    """Every mesh deformed by this armature: anything parented to it, plus
    anything carrying an Armature modifier aimed at it (the two ways an imported
    character's meshes attach)."""
    meshes = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.parent is rig:
            meshes.append(obj)
            continue
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is rig:
                meshes.append(obj)
                break
    return meshes


# ---------------------------------------------------------------------------
# ctrl -> a property of this rig
# ---------------------------------------------------------------------------
class MorphTarget:
    """One concrete thing a ctrl drives on this rig: an owner id-datablock, the
    RNA data path to animate, and the sign the ctrl's weight enters with (a
    "min" shape key answers to negative weight)."""

    __slots__ = ("owner", "data_path", "sign", "setter", "label")

    def __init__(self, owner, data_path, sign, setter, label):
        self.owner = owner
        self.data_path = data_path
        self.sign = sign
        self.setter = setter
        self.label = label

    def apply(self, weight):
        signed = weight * self.sign
        self.setter(max(signed, 0.0) if self.sign < 0 else signed)


def _key_block_setter(key_block):
    def setter(value):
        key_block.value = min(max(value, key_block.slider_min), key_block.slider_max)
    return setter


def _resolve_shape_keys(rig, meshes, rule):
    """ctrl -> [MorphTarget] for every shape key on this rig whose name is a baked
    ctrl channel, by the naming rule the GAME states. The mesh's Key datablock owns
    the animation, which is why the target carries it as the owner rather than the
    mesh object."""
    if not rule or not rule.get("suffix"):
        return {}
    pattern = re.compile(rule["suffix"], re.IGNORECASE)
    signs = dict(rule.get("signs") or {})
    bindings = {}
    for obj in meshes:
        shape_keys = obj.data.shape_keys
        if shape_keys is None:
            continue
        for key_block in shape_keys.key_blocks:
            match = pattern.search(key_block.name)
            if match is None:
                continue
            ctrl = key_block.name[:match.start()]
            end = (match.group(1) if match.groups() else "").lower()
            bindings.setdefault(ctrl, []).append(MorphTarget(
                owner=shape_keys,
                data_path=key_block.path_from_id("value"),
                sign=float(signs.get(end, 1.0)),
                setter=_key_block_setter(key_block),
                label="{0} · {1}".format(obj.name, key_block.name)))
    return bindings


class ShapeKeyBinding:
    """The ctrls this rig exposes as baked shape keys."""

    def __init__(self, targets):
        self.targets = targets  # ctrl -> [MorphTarget]

    @property
    def ctrls(self):
        return set(self.targets)

    def label_for(self, ctrl):
        targets = self.targets.get(ctrl)
        return targets[0].label if targets else ""

    def apply(self, weights):
        for ctrl, targets in self.targets.items():
            weight = weights.get(ctrl, 0.0)
            for target in targets:
                target.apply(weight)


class BoneBinding:
    """A ctrl moves BONES, by the per-bone TRS deltas the table lists.

    Unlike shape keys these accumulate -- several ctrls routinely push the same
    bone -- so a pose is applied as one pass over every bone the table knows:

        local(bone) = base(bone) + sum(weight_i * delta_i(bone))

    and that source-space local is turned into a pose-bone basis with the same
    conjugation identity the clip importer uses.

    A table names bones in the SOURCE's words (``browLf01Joint``) and this rig
    answers to whatever its user renamed them to, so every name crossing in is
    translated through the rig's own identity. Nothing below this constructor
    speaks the source's names: once ingested, every bone name here is a bone of
    THIS rig.
    """

    def __init__(self, rig, table, identity):
        from mathutils import Matrix

        self.armature = rig
        self.identity = identity
        self.euler_order = "ZXY"
        self.euler_signs = (1.0, 1.0, 1.0)
        self.rest_error = float("nan")
        self._matrix = Matrix
        self._conversion = coordinate.conversion_matrix()
        self._bones = {}       # bone name -> (pose_bone, rest_local_inverted)
        self._base = {}        # bone name -> (position, rotation, scale)
        self._mappings = {}    # ctrl -> [(bone name, dposition, drotation, dscale)]
        self.unmatched_source_names = set()

        for source_name, base in (table.get("base") or {}).items():
            bone = self._rig_bone(source_name)
            if bone:
                self._base.setdefault(bone, (base[0], base[1], base[2]))
        for ctrl, deltas in (table.get("deltas") or {}).items():
            merged = self._mappings.setdefault(ctrl, [])
            for source_name, position, rotation, scale in deltas:
                bone = self._rig_bone(source_name)
                if bone:
                    merged.append((bone, position, rotation, scale))

        pose_bones = rig.pose.bones
        name_to_rest = identity.rest_by_bone()
        for name in sorted(self._base):
            pose_bone = pose_bones.get(name)
            rest = name_to_rest.get(name)
            if pose_bone is None or rest is None:
                continue
            self._bones[name] = (pose_bone, rest.inverted_safe())
        self.euler_order = self._pick_euler_order(name_to_rest)

    def _rig_bone(self, source_name):
        """This rig's bone for a name the table wrote, remembering the ones it has
        none for -- a table half of which lands nowhere is a real fact about this
        rig, and the panel says it rather than showing a face that does less than
        it should for no stated reason."""
        if not source_name:
            return None
        bone = self.identity.bone_of(source_name)
        if bone is None:
            self.unmatched_source_names.add(source_name)
        return bone

    @property
    def bone_count(self):
        return len(self._bones)

    @property
    def governed_bones(self):
        """EVERY bone this table owns on this rig -- not just the ones some
        particular expression moves.

        That distinction is the whole difference between a retargeted face and a
        broken one. When a restated face replaces a source performance, the bones no
        expression happens to drive must fall back to THIS rig's rest, not keep the
        source character's absolute transform: the two rigs hold the same named bone
        differently (measured: 7.4 deg apart at the median, 180 at the worst), so a
        leftover source curve is a bone wrenched somewhere it was never meant to be.
        Measured on the nose joint, which no ctrl of that character drives: a constant
        113 deg rotation and 4cm offset for the whole clip -- the face visibly
        deformed while every expression curve was correct."""
        return set(self._bones)

    @property
    def ctrls(self):
        """Only the ctrls that actually reach a bone THIS rig has."""
        bound = set()
        for ctrl, deltas in self._mappings.items():
            if any(name in self._bones for name, _p, _r, _s in deltas):
                bound.add(ctrl)
        return bound

    def label_for(self, ctrl):
        names = [name for name, _p, _r, _s in self._mappings.get(ctrl, ())
                 if name in self._bones]
        if not names:
            return ""
        return names[0] + (" +{0}".format(len(names) - 1) if len(names) > 1 else "")

    def _pick_euler_order(self, name_to_rest):
        """Which Euler convention the table's degrees are in, decided BY THE DATA
        rather than asserted: the base pose is the rig's own rest pose, so the right
        convention is whichever reproduces each bone's rest matrix.

        Both the axis order and a per-axis sign are searched, because the source's
        euler rotations are left-handed and mathutils' are not -- which axes come out
        negated depends on the exporter's handedness fix-up, and guessing it would
        silently mirror every expression. ``rest_error`` is kept so the panel can show
        how well the winner actually fits."""
        # Every bone, not a sample: rest_error doubles as the tolerance callers
        # compare against, so it has to be a true bound rather than an average over
        # whichever bones happened to be first.
        samples = [(name, base) for name, base in sorted(self._base.items())
                   if name in name_to_rest]
        best, best_error = ("ZXY", (1.0, 1.0, 1.0)), None
        for order in _EULER_ORDER_CANDIDATES:
            for signs in _EULER_SIGN_CANDIDATES:
                error = 0.0
                for name, (position, rotation, scale) in samples:
                    composed = self._compose(position, rotation, scale, order, signs)
                    rest = name_to_rest[name]
                    error = max(error, max(abs(a - b) for row_a, row_b in zip(composed, rest)
                                           for a, b in zip(row_a, row_b)))
                    if best_error is not None and error >= best_error:
                        break
                if best_error is None or error < best_error:
                    best, best_error = (order, signs), error
        self.rest_error = best_error if best_error is not None else float("nan")
        self.euler_signs = best[1]
        return best[0]

    def _compose(self, position, rotation_degrees, scale, order=None, signs=None):
        from mathutils import Euler

        Matrix = self._matrix
        signs = signs if signs is not None else self.euler_signs
        euler = Euler([math.radians(value * sign)
                       for value, sign in zip(rotation_degrees, signs)],
                      order or self.euler_order)
        translation = Matrix.Translation(position)
        rotation = euler.to_matrix().to_4x4()
        scaling = Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
        return translation @ rotation @ scaling

    def apply(self, weights):
        """One pass over every bone the table knows -- bones no active ctrl touches
        land back on their base pose, so a pose REPLACES rather than piling onto
        whatever was there."""
        accumulated = {name: [list(base[0]), list(base[1]), list(base[2])]
                       for name, base in self._base.items() if name in self._bones}
        for ctrl, deltas in self._mappings.items():
            weight = weights.get(ctrl, 0.0)
            if weight == 0.0:
                continue
            for name, delta_position, delta_rotation, delta_scale in deltas:
                slot = accumulated.get(name)
                if slot is None:
                    continue
                for axis in range(3):
                    slot[0][axis] += delta_position[axis] * weight
                    slot[1][axis] += delta_rotation[axis] * weight
                    slot[2][axis] += delta_scale[axis] * weight

        conversion = self._conversion
        for name, (pose_bone, rest_inverted) in self._bones.items():
            position, rotation, scale = accumulated[name]
            local = self._compose(position, rotation, scale)
            pose_bone.matrix_basis = conversion @ (rest_inverted @ local) @ conversion

    def basis_at(self, weights, name):
        """The pose-bone basis one bone would take at these weights, without
        touching the scene -- what the action baker keys."""
        entry = self._bones.get(name)
        base = self._base.get(name)
        if entry is None or base is None:
            return None
        position, rotation, scale = list(base[0]), list(base[1]), list(base[2])
        for ctrl, deltas in self._mappings.items():
            weight = weights.get(ctrl, 0.0)
            if weight == 0.0:
                continue
            for delta_name, delta_position, delta_rotation, delta_scale in deltas:
                if delta_name != name:
                    continue
                for axis in range(3):
                    position[axis] += delta_position[axis] * weight
                    rotation[axis] += delta_rotation[axis] * weight
                    scale[axis] += delta_scale[axis] * weight
        local = self._compose(position, rotation, scale)
        return self._conversion @ (entry[1] @ local) @ self._conversion

    def bones_for(self, ctrls):
        """Which of this rig's bones the given ctrls can move -- the set an action
        needs channels for."""
        names = set()
        for ctrl in ctrls:
            for name, _p, _r, _s in self._mappings.get(ctrl, ()):
                if name in self._bones:
                    names.add(name)
        return names


class RigBinding:
    """Everything one rig can do with ctrl drivers, across every kind of target."""

    def __init__(self, armature_name, providers):
        self.armature_name = armature_name
        self.providers = [provider for provider in providers if provider is not None]

    @property
    def ctrls(self):
        bound = set()
        for provider in self.providers:
            bound |= provider.ctrls
        return bound

    def label_for(self, ctrl):
        for provider in self.providers:
            label = provider.label_for(ctrl)
            if label:
                return label
        return ""

    def apply(self, weights):
        for provider in self.providers:
            provider.apply(weights)

    def bone_binding(self):
        return next((p for p in self.providers if isinstance(p, BoneBinding)), None)

    def shape_binding(self):
        return next((p for p in self.providers if isinstance(p, ShapeKeyBinding)), None)

    def __contains__(self, ctrl):
        return any(ctrl in provider.ctrls for provider in self.providers)

    def __len__(self):
        return len(self.ctrls)


# ---------------------------------------------------------------------------
# The three verbs the port names
# ---------------------------------------------------------------------------
def _binding_for(rig, table):
    """This rig's binding for this table, rebuilt whenever either changed.

    An add-on reload drops the module globals while the parsed library survives,
    and a re-imported rig is a new object with the same name. Healing here keeps
    "bound" from silently meaning "bound to something else"."""
    if rig is None:
        return RigBinding("", [])
    if (_BINDING["binding"] is None or _BINDING["rig"] != rig.name
            or _BINDING["table"] is not table):
        providers = [ShapeKeyBinding(_resolve_shape_keys(
            rig, rig_meshes(rig), (table or {}).get("shape_rule")))]
        if (table or {}).get("deltas"):
            identity = rig_identity.of(rig)
            if identity is not None:
                bones = BoneBinding(rig, table, identity)
                if bones.ctrls:
                    providers.append(bones)
        _BINDING.update(rig=rig.name, table=table,
                        binding=RigBinding(rig.name, providers))
    return _BINDING["binding"]


def bindings(context, rig, table):
    """What this rig can drive, and what each ctrl reaches -- the report the panel
    words. Read-only over bpy data, so it is safe from a draw callback."""
    binding = _binding_for(rig, table)
    bones = binding.bone_binding()
    shapes = binding.shape_binding()
    via = []
    if bones is not None:
        via.append((len(bones.ctrls), "via bones"))
    if shapes is not None and shapes.ctrls:
        via.append((len(shapes.ctrls), "via shape keys"))
    return {
        "ctrls": {ctrl: binding.label_for(ctrl) for ctrl in binding.ctrls},
        "via": via,
        "missing_bones": sorted(bones.unmatched_source_names) if bones is not None else [],
        "rest_error": bones.rest_error if bones is not None else None,
        "reason": "" if bones is not None else _no_bone_binding_reason(rig, table),
    }


def _no_bone_binding_reason(rig, table):
    """Why this rig's face table reached no bone, said in the words of the ONE
    thing that fixes it. A table that binds nothing looks exactly like "this
    character has no face", so the difference is never left to be guessed."""
    if not (table or {}).get("deltas"):
        return ""
    identity = rig_identity.of(rig)
    if identity is None:
        return ("'{0}' bones carry no source identity, so a face table naming the "
                "source's bones cannot reach them -- import the character through this "
                "add-on, which is what puts it there.".format(
                    rig.name if rig else "this rig"))
    return ("None of this character's face-table bones exist on '{0}' -- its {1} bone(s) "
            "are a different skeleton, or the declaration names another "
            "character.".format(rig.name, len(identity)))


def drive(context, rig, table, weights):
    """Drive every bound ctrl to its weight and every other bound ctrl to zero, so
    applying a pose REPLACES the face instead of accumulating onto whatever the
    previous one left behind."""
    _binding_for(rig, table).apply(weights)


def bake(context, rig, table, tracks, frames, fps, name, into=None):
    """Bake one animation onto whatever this rig binds it to: shape-key value
    curves where the ctrl is a baked blend shape, pose-bone transforms where it
    moves bones. Returns True when at least one curve was written.

    ``tracks`` is ``{ctrl: [(time, value, in_slope, out_slope)]}`` -- the game's own
    keys, in seconds, with per-second slopes. ``frames`` is that same animation
    already SAMPLED per frame by the game, as ``[{ctrl: weight}]``: several ctrls
    with independent key times sum into one bone, so the bone half has no shared key
    grid to keep, and how a game evaluates its own curves between keys is that
    game's answer rather than an interpolation invented here.

    ``into`` is an existing (action, slot) to write the BONE half into: a clip whose
    body animation is already keyed there, whose facial channels this replaces."""
    binding = _binding_for(rig, table)
    wrote = _bake_shape_keys(binding.shape_binding(), tracks, fps, name)
    wrote |= _bake_bones(binding.bone_binding(), tracks, frames, fps, name, into)
    return wrote


def _bake_shape_keys(shape_binding, tracks, fps, name):
    """Shape-key channels keep the game's own key times -- a ctrl maps 1:1 onto one
    key's value, so there is nothing to resample."""
    if shape_binding is None:
        return False
    by_owner = {}
    for ctrl, keys in tracks.items():
        if not keys:
            continue
        for target in shape_binding.targets.get(ctrl, ()):
            by_owner.setdefault(target.owner, []).append((keys, target))
    if not by_owner:
        return False

    for owner, pairs in by_owner.items():
        action = bpy.data.actions.new(name)
        if hasattr(action, "use_fake_user"):
            action.use_fake_user = True
        fcurves, slot = animation_builder._prepare_channels(action, action.name, "KEY")
        for keys, target in pairs:
            _write_weight_fcurve(fcurves, keys, target, fps)
        # A shape-key action is one HALF of a face animation (the bone half lands
        # next door), so it must not retime the scene on its own -- the caller does
        # that once, for the whole animation.
        animation_builder.adopt_action(owner, action, slot, frame_range=False)
    return True


def _write_weight_fcurve(fcurves, keys, target, fps):
    """The ctrl's weight curve as one fcurve on the target's value. Source keys are
    cubic Hermite (value + in/out slope); Blender's Bezier handles express the
    identical segment when each handle sits a third of the way into it -- the
    standard Hermite-to-Bezier conversion, so no resampling is needed and the game's
    own key times survive."""
    sign = target.sign
    values = [max(value * sign, 0.0) if sign < 0.0 else value * sign
              for _time, value, _in, _out in keys]
    fcurve = fcurves.new(target.data_path)
    fcurve.keyframe_points.add(len(keys))
    for index, point in enumerate(fcurve.keyframe_points):
        time, _value, in_slope, out_slope = keys[index]
        frame = float(time) * fps
        point.co = (frame, values[index])
        point.interpolation = "BEZIER"
        # Slopes are per SECOND at the source; on a frame-based fcurve they are per
        # frame, hence the /fps. The handle sits one third of the neighbouring
        # segment away -- shorter at the ends, where there is only one segment.
        previous_gap = (float(time - keys[index - 1][0]) * fps) if index else 0.0
        next_gap = (float(keys[index + 1][0] - time) * fps
                    if index + 1 < len(keys) else 0.0)
        left = previous_gap / 3.0 if previous_gap else (next_gap / 3.0 if next_gap else 1.0)
        right = next_gap / 3.0 if next_gap else (previous_gap / 3.0 if previous_gap else 1.0)
        point.handle_left_type = "FREE"
        point.handle_right_type = "FREE"
        point.handle_left = (frame - left, values[index] - float(in_slope) * sign / fps * left)
        point.handle_right = (frame + right, values[index] + float(out_slope) * sign / fps * right)
    fcurve.update()


def _bake_bones(bone_binding, tracks, frames, fps, name, into):
    """Bone channels come from the per-frame samples the game handed over: a bone's
    transform is the sum of every ctrl pushing it, and those ctrls have independent
    key times, so there is no shared key grid to preserve.

    Only the bones this animation's own ctrls can move get channels -- a face
    animation must not overwrite bones it never mentions."""
    if bone_binding is None or not frames:
        return False
    animated = {ctrl for ctrl, keys in tracks.items() if keys}
    bones = bone_binding.bones_for(animated)
    if not bones:
        return False

    import numpy as np

    samples = list(frames)
    frame_count = len(samples)
    frame_numbers = np.arange(frame_count, dtype=np.float64)

    rig = bone_binding.armature
    if into is not None:
        action, slot = into
        fcurves = animation_builder.channels_of(action, slot)
        # EVERY bone these face tables own, not just the ones this expression moves:
        # a face bone left holding the source character's curve is a bone wrenched out
        # of place (see BoneBinding.governed_bones). The ones no expression drives are
        # meant to sit at this rig's own rest, and dropping their channels is what puts
        # them there.
        governed = bone_binding.governed_bones
        replaced = animation_builder.release_bones(rig, fcurves, governed)
        print("[face] {0}: released {1} source facial channel(s) over {2} governed "
              "bone(s) (basis reset to rest), {3} of them re-driven by this expression, "
              "in '{4}'".format(name, replaced, len(governed), len(bones), action.name),
              flush=True)
    else:
        action = bpy.data.actions.new(name)
        if hasattr(action, "use_fake_user"):
            action.use_fake_user = True
        fcurves, slot = animation_builder._prepare_channels(action, action.name, "OBJECT")

    for bone_name in sorted(bones):
        locations = np.empty((frame_count, 3), dtype=np.float32)
        quaternions = np.empty((frame_count, 4), dtype=np.float32)
        scales = np.empty((frame_count, 3), dtype=np.float32)
        for index, weights in enumerate(samples):
            basis = bone_binding.basis_at(weights, bone_name)
            location, rotation, scale = basis.decompose()
            locations[index] = (location.x, location.y, location.z)
            quaternions[index] = (rotation.w, rotation.x, rotation.y, rotation.z)
            scales[index] = (scale.x, scale.y, scale.z)
        animation_builder._write_bone_fcurves(fcurves, bone_name, frame_numbers,
                                              locations, quaternions, scales)

    # Writing into a clip's own action: it is already assigned and already spans the
    # clip -- re-adopting it would only retime the scene to the face's own length.
    if into is None:
        # Same reason as the shape-key half: one animation, one retime, done by the
        # caller once both halves exist.
        animation_builder.adopt_action(rig, action, slot, frame_range=False)
    return True


def forget():
    """Drop the cached binding -- what an unregister owes a session-lived cache."""
    _BINDING.update(rig="", table=None, binding=None)
