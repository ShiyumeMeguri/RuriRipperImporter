"""EndField-specific limb IK correction for imported humanoid actions.

EndField does NOT ship playable limb FK in its humanoid clips: the muscle
curves for arms/legs are auto-generated approximations, and at runtime the
game recomputes hands and feet entirely through IK against dedicated target
bones that ARE precisely animated in the same clip. Importing the FK alone
leaves hands/feet visibly twisted. All of this is ground-truthed against the
real game (chr_0004_pelica, A_actor_pelica_battle_attack_01_ACL):

  * rig convention (names are the runtime's interface -- no MonoBehaviour
    config exists, the logic lives in game code):
      IK_Foot_L_001 / IK_Foot_R_001   foot targets   (parented to Root)
      IK_Knee_L_001 / IK_Knee_R_001   knee pole hints (parented to Root,
                                       rest 0.30m straight in front of knee)
      IK_Hand_L_001 / IK_Hand_R_001   hand targets   (parented to IK_Root)
  * at REST every target is EXACTLY coincident with its FK bone (zero
    position delta, identity rotation offset) -- so the runtime contract is
    simply "end bone world pose = IK target world pose";
  * in the animation the FK pose diverges from the targets by 0.4-0.73m /
    100+ degrees on hands and 0.1-0.2m / 30-44 degrees on feet -- that IS
    the reported twisting.

The solver is a standard analytic two-bone IK, run purely on the action's
baked fcurves (no scene evaluation): per frame, rebuild the involved bones'
armature-space pose by matrix recursion, place the mid joint on the
pole-hint plane (legs) or on the FK elbow plane (arms -- the rig ships no
elbow hint), aim upper/lower via minimal-arc rotation deltas (preserving FK
twist), snap the end bone's world rotation to the target's, and write ONLY
the rotation_quaternion fcurves back (positions/scales stay FK: bone lengths
are rigid, so the delta construction lands the joints exactly).

Applied only to humanoid (muscle) clips on rigs that expose the convention
-- generic clips (UI animations) ship real FK and verified correct without
this. See prefab_importer.build_selected_animations for the gate.
"""

from __future__ import annotations

import math
import re

from mathutils import Matrix, Quaternion, Vector

# (human upper, human lower, human end, target bone, hint bone or None)
_CHAINS = (
    ("LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "IK_Foot_L_001", "IK_Knee_L_001"),
    ("RightUpperLeg", "RightLowerLeg", "RightFoot", "IK_Foot_R_001", "IK_Knee_R_001"),
    ("LeftUpperArm", "LeftLowerArm", "LeftHand", "IK_Hand_L_001", None),
    ("RightUpperArm", "RightLowerArm", "RightHand", "IK_Hand_R_001", None),
)

_REQUIRED_TARGETS = ("IK_Foot_L_001", "IK_Foot_R_001", "IK_Hand_L_001", "IK_Hand_R_001")

_BONE_PATH_RE = re.compile(r'pose\.bones\["(.+?)"\]\.(\w+)')


def detect_rig(arm_obj):
    """True when the armature exposes EndField's IK-bone convention."""
    bones = arm_obj.data.bones
    return all(name in bones for name in _REQUIRED_TARGETS)


class _FcurveSampler:
    """Per-bone basis (loc/quat/scale) sampling straight off an action's baked
    fcurves -- no scene/depsgraph evaluation, so the correction is exact
    against what was imported and orders of magnitude faster per frame."""

    def __init__(self, channelbag):
        self._by_key = {}
        for fc in channelbag.fcurves:
            match = _BONE_PATH_RE.match(fc.data_path)
            if match:
                self._by_key[(match.group(1), match.group(2), fc.array_index)] = fc

    def basis(self, bone_name, frame):
        def read(prop, index, default):
            fc = self._by_key.get((bone_name, prop, index))
            return fc.evaluate(frame) if fc is not None else default

        loc = Vector((read("location", 0, 0.0), read("location", 1, 0.0), read("location", 2, 0.0)))
        quat = Quaternion((read("rotation_quaternion", 0, 1.0), read("rotation_quaternion", 1, 0.0),
                           read("rotation_quaternion", 2, 0.0), read("rotation_quaternion", 3, 0.0)))
        if quat.magnitude < 1e-8:
            quat = Quaternion((1.0, 0.0, 0.0, 0.0))
        scale = Vector((read("scale", 0, 1.0), read("scale", 1, 1.0), read("scale", 2, 1.0)))
        return Matrix.LocRotScale(loc, quat.normalized(), scale)

    def quat_fcurves(self, bone_name):
        return [self._by_key.get((bone_name, "rotation_quaternion", i)) for i in range(4)]


def _ancestors_first(arm_obj, names):
    """The given bones plus every ancestor, parents before children."""
    needed = set()
    for name in names:
        bone = arm_obj.data.bones.get(name)
        while bone is not None and bone.name not in needed:
            needed.add(bone.name)
            bone = bone.parent
    def depth(name):
        d, bone = 0, arm_obj.data.bones[name]
        while bone.parent is not None:
            d += 1
            bone = bone.parent
        return d
    return sorted(needed, key=depth)


def _solve_mid_position(a, target_pos, pole_pos, l1, l2):
    """Analytic two-bone mid-joint placement: on the a->target axis at the
    law-of-cosines split, pushed toward the pole's plane."""
    to_target = target_pos - a
    dist = max(min(to_target.length, l1 + l2 - 1e-5), abs(l1 - l2) + 1e-5)
    axis = to_target.normalized() if to_target.length > 1e-8 else Vector((0.0, 0.0, 1.0))
    d1 = (l1 * l1 + dist * dist - l2 * l2) / (2.0 * dist)
    h_sq = l1 * l1 - d1 * d1
    h = math.sqrt(h_sq) if h_sq > 0.0 else 0.0
    pole_vec = pole_pos - a
    pole_dir = pole_vec - axis * pole_vec.dot(axis)
    if pole_dir.length < 1e-6:
        # Pole degenerate (on the axis): keep whatever perpendicular exists.
        pole_dir = axis.orthogonal()
    return a + axis * d1 + pole_dir.normalized() * h


def apply_to_action(arm_obj, action, bone_targets, frame_start, frame_end):
    """Rewrite the limb bones' rotation fcurves of `action` so the chains obey
    the clip's own IK target bones. bone_targets: {human name: bone name}
    (from the humanoid retargeter). Returns a per-chain summary string list
    (empty when the rig/chains didn't resolve)."""
    bag = action.layers[0].strips[0].channelbag(action.slots[0]) if hasattr(action, "layers") else None
    if bag is None:
        return []
    sampler = _FcurveSampler(bag)
    bones = arm_obj.data.bones

    chains = []
    for h_upper, h_lower, h_end, target_name, hint_name in _CHAINS:
        upper = bone_targets.get(h_upper)
        lower = bone_targets.get(h_lower)
        end = bone_targets.get(h_end)
        if not (upper and lower and end and upper in bones and lower in bones and end in bones
                and target_name in bones and (hint_name is None or hint_name in bones)):
            continue
        chains.append((upper, lower, end, target_name, hint_name))
    if not chains:
        return []

    # Rest matrices + parent-relative rest offsets for the pose recursion.
    involved = [n for chain in chains for n in chain if n is not None]
    ordered = _ancestors_first(arm_obj, involved)
    rest = {n: bones[n].matrix_local for n in ordered}
    rest_rel = {}
    parent_of = {}
    for n in ordered:
        parent = bones[n].parent
        parent_of[n] = parent.name if parent is not None else None
        rest_rel[n] = (rest[parent.name].inverted() @ rest[n]) if parent is not None else rest[n]

    # Rest rotation offset end-bone <- IK-target (identity on the real rig --
    # verified rest-coincident -- but carried anyway so a variant rig with a
    # deliberate offset still lands on its own convention).
    end_offsets = {}
    for upper, lower, end, target_name, _hint in chains:
        end_offsets[end] = (rest[target_name].to_quaternion().inverted()
                            @ rest[end].to_quaternion())

    frames = range(int(frame_start), int(frame_end) + 1)
    n_frames = len(frames)
    # Output: bone -> (4, n_frames) quaternion basis values.
    out = {}
    for upper, lower, end, _t, _h in chains:
        for n in (upper, lower, end):
            out[n] = [[0.0] * n_frames for _ in range(4)]

    for fi, frame in enumerate(frames):
        pose = {}
        for n in ordered:
            local = rest_rel[n] @ sampler.basis(n, frame)
            parent = parent_of[n]
            pose[n] = (pose[parent] @ local) if parent is not None else local

        for upper, lower, end, target_name, hint_name in chains:
            p_upper, p_lower, p_end = pose[upper], pose[lower], pose[end]
            a = p_upper.translation
            k_fk = p_lower.translation
            t_fk = p_end.translation
            target = pose[target_name]
            target_pos = target.translation
            l1 = (k_fk - a).length
            l2 = (t_fk - k_fk).length
            pole_pos = pose[hint_name].translation if hint_name is not None else k_fk

            k_new = _solve_mid_position(a, target_pos, pole_pos, l1, l2)

            # Minimal-arc deltas preserve the FK twist along each segment. The
            # deltas rotate each segment rigidly about its own head, so with
            # rest offsets and basis translations untouched the joints land
            # exactly on the solved positions -- only ROTATION basis changes.
            delta_u = (k_fk - a).rotation_difference(k_new - a)
            r_upper = delta_u @ p_upper.to_quaternion()
            delta_l = (delta_u @ (t_fk - k_fk)).rotation_difference(target_pos - k_new)
            r_lower = delta_l @ delta_u @ p_lower.to_quaternion()
            r_end = target.to_quaternion() @ end_offsets[end]

            # World rotation -> pose basis, chaining through the NEW parent rotations.
            new_rot = {upper: r_upper, lower: r_lower, end: r_end}
            for n in (upper, lower, end):
                parent = parent_of[n]
                if parent in new_rot:
                    parent_rot = new_rot[parent]
                elif parent is not None:
                    parent_rot = pose[parent].to_quaternion()
                else:
                    parent_rot = Quaternion((1.0, 0.0, 0.0, 0.0))
                basis_q = rest_rel[n].to_quaternion().inverted() @ parent_rot.inverted() @ new_rot[n]
                basis_q.normalize()
                col = out[n]
                col[0][fi] = basis_q.w
                col[1][fi] = basis_q.x
                col[2][fi] = basis_q.y
                col[3][fi] = basis_q.z

    # Quaternion continuity per bone (avoid sign flips between frames).
    for n, cols in out.items():
        for fi in range(1, n_frames):
            dot = sum(cols[c][fi] * cols[c][fi - 1] for c in range(4))
            if dot < 0.0:
                for c in range(4):
                    cols[c][fi] = -cols[c][fi]

    # Write back: rotation curves only.
    for n, cols in out.items():
        fcs = sampler.quat_fcurves(n)
        for ci, fc in enumerate(fcs):
            if fc is None:
                fc = bag.fcurves.new(f'pose.bones["{n}"].rotation_quaternion', index=ci)
                fc.keyframe_points.add(n_frames)
                for fi, frame in enumerate(frames):
                    fc.keyframe_points[fi].co = (float(frame), cols[ci][fi])
            else:
                for kp in fc.keyframe_points:
                    fi = int(round(kp.co[0])) - int(frame_start)
                    if 0 <= fi < n_frames:
                        kp.co[1] = cols[ci][fi]
                        kp.handle_left[1] = cols[ci][fi]
                        kp.handle_right[1] = cols[ci][fi]
            fc.update()

    return [f"{upper}->{end} via {target_name}" for upper, lower, end, target_name, _h in chains]
