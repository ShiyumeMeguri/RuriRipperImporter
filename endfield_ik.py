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

# (human upper, human lower, human end, target bone, hint bone or None,
#  human shoulder or None)
#
# The shoulder entry drives the clavicle-assist pass (arms only). Ground
# truth for why it exists: the battle clip's IK_Hand targets reach up to
# 0.593m from the shoulder while the arm is only 0.448m long -- yet the
# targets are exact runtime animation, so in-game the hand DOES land on them,
# which is only possible if the runtime recruits the clavicle. And the muscle
# data's shoulder channels are the same auto-generated filler as the limb FK:
# they hold the clavicle at a constant 24.0deg away from the skeleton's own
# rest (measured: muscle-zero == the avatar's internal reference pose, which
# sits exactly 24.0deg off the prefab rest on both clavicles -- the reported
# permanent shrug). So the runtime model is: clavicle at BIND pose, swung
# toward the target only when the target outranges the arm.
_CHAINS = (
    ("LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "IK_Foot_L_001", "IK_Knee_L_001", None),
    ("RightUpperLeg", "RightLowerLeg", "RightFoot", "IK_Foot_R_001", "IK_Knee_R_001", None),
    ("LeftUpperArm", "LeftLowerArm", "LeftHand", "IK_Hand_L_001", None, "LeftShoulder"),
    ("RightUpperArm", "RightLowerArm", "RightHand", "IK_Hand_R_001", None, "RightShoulder"),
)

# Maximum clavicle-assist swing. The largest shortfall observed in real data
# (0.593 vs 0.448m) needs ~0.15m of shoulder travel; with a ~0.17m clavicle
# that is safely inside this cap, and capping keeps a pathological target from
# folding the chest.
_SHOULDER_ASSIST_MAX_RAD = math.radians(35.0)

# Per-frame IK weight from target-vs-FK-end distance: full IK at/below
# _IK_FULL_DIST, pure muscle FK at/above _IK_OFF_DIST, linear in between.
# Re-measured after the fork muscle-attribute remap fixed the FK arms (the
# original 8/20cm band was calibrated against BROKEN arm muscle data):
#   * authored PINS sit at d ~ 0: support feet track their targets within
#     4-22mm across every locomotion clip, and sprint_stepon's hand-plant
#     frames put the hand target 1-8mm from the (now correct) FK hand;
#   * loose followers must NOT engage: on locomotion the hand targets hover
#     10-17cm from the FK hands (tracking, not pinning) and the old band
#     dragged the now-correct arms up to 17.5deg toward them; battle hand
#     targets sit 0.4-0.8m away (weapon-space anchors, never wrist pins);
#   * where the target IS near the FK end, IK~=FK and the blend is harmless
#     by construction.
# 2cm/5cm separates the two populations with a wide margin on every sampled
# clip: pins are <=2.2cm, followers >=7cm.
_IK_FULL_DIST = 0.02
_IK_OFF_DIST = 0.05

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

    def basis_quat(self, bone_name, frame):
        """Just the FK rotation basis off the curves (for w=0 passthrough)."""
        def read(index, default):
            fc = self._by_key.get((bone_name, "rotation_quaternion", index))
            return fc.evaluate(frame) if fc is not None else default
        quat = Quaternion((read(0, 1.0), read(1, 0.0), read(2, 0.0), read(3, 0.0)))
        if quat.magnitude < 1e-8:
            return Quaternion((1.0, 0.0, 0.0, 0.0))
        return quat.normalized()

    def quat_fcurves(self, bone_name):
        return [self._by_key.get((bone_name, "rotation_quaternion", i)) for i in range(4)]

    def bone_animated(self, bone_name):
        """Whether this action carries ANY fcurve for the bone. For the IK
        target bones this is the per-clip IK enable switch: locomotion clips
        (walk_loop, sprint...) ship NO curves for the IK bones at all -- their
        targets sit parked at rest, and solving toward a parked target is what
        contorted the arms of every non-combat animation. Clips that DO drive
        their targets (battle, run_stop, dialog_walk) get the solve. Verified
        across the real game's clips; the IK_Root-as-master-switch hypothesis
        was tested and disproved (constant in battle clips too)."""
        return any(key[0] == bone_name for key in self._by_key)


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


# FK elbow offsets under this many meters off the shoulder->target axis are
# treated as "no usable bend plane". Ground truth: in the battle clip the
# auto-generated FK arm goes fully straight for whole stretches (elbow 1.5mm
# off-axis for 10+ frames) -- any per-frame perpendicular extracted there is
# numeric noise, and the arbitrary-orthogonal fallback the first version used
# let the bend plane spin freely, flipping the whole arm (and every sleeve/
# deco bone riding on it) to the character's back.
_POLE_DEGENERATE = 0.02


def _solve_mid_position(a, target_pos, pole_dir_hint, l1, l2):
    """Analytic two-bone mid-joint placement: on the a->target axis at the
    law-of-cosines split, pushed toward the (already continuity-filtered)
    pole direction's plane."""
    to_target = target_pos - a
    dist = max(min(to_target.length, l1 + l2 - 1e-5), abs(l1 - l2) + 1e-5)
    axis = to_target.normalized() if to_target.length > 1e-8 else Vector((0.0, 0.0, 1.0))
    d1 = (l1 * l1 + dist * dist - l2 * l2) / (2.0 * dist)
    h_sq = l1 * l1 - d1 * d1
    h = math.sqrt(h_sq) if h_sq > 0.0 else 0.0
    pole_dir = pole_dir_hint - axis * pole_dir_hint.dot(axis)
    if pole_dir.length < 1e-8:
        pole_dir = axis.orthogonal()
    return a + axis * d1 + pole_dir.normalized() * h


class _PoleTracker:
    """Temporally-coherent bend-plane direction for a chain.

    Per frame the raw pole comes from a hint bone (legs) or the FK mid joint
    (arms -- the rig ships no elbow hint). Raw FK poles are NOT trustworthy
    frame-to-frame: the auto-generated FK arm collapses onto the
    shoulder->target axis (degenerate, no plane at all) and can hop to the
    opposite side between frames (a >90deg plane jump in one frame is FK
    noise, not intent). This tracker keeps the last GOOD direction and reuses
    it through degenerate stretches, and refuses side-hops by keeping the
    previous side when the new direction points >90deg away."""

    def __init__(self):
        self._last = None

    def resolve(self, a, target_pos, raw_pole_pos, fallback_dir):
        to_target = target_pos - a
        axis = to_target.normalized() if to_target.length > 1e-8 else Vector((0.0, 0.0, 1.0))
        raw = raw_pole_pos - a
        perp = raw - axis * raw.dot(axis)

        direction = None
        if perp.length >= _POLE_DEGENERATE:
            direction = perp.normalized()
            if self._last is not None and direction.dot(self._last) < 0.0:
                direction = self._last
        elif self._last is not None:
            direction = self._last
        else:
            fb = fallback_dir - axis * fallback_dir.dot(axis)
            direction = fb.normalized() if fb.length > 1e-6 else axis.orthogonal()

        self._last = direction
        return direction


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
    for h_upper, h_lower, h_end, target_name, hint_name, h_shoulder in _CHAINS:
        upper = bone_targets.get(h_upper)
        lower = bone_targets.get(h_lower)
        end = bone_targets.get(h_end)
        shoulder = bone_targets.get(h_shoulder) if h_shoulder is not None else None
        if not (upper and lower and end and upper in bones and lower in bones and end in bones
                and target_name in bones and (hint_name is None or hint_name in bones)):
            continue
        if not sampler.bone_animated(target_name):
            # This clip does not drive this IK target -- IK is OFF for this
            # chain at runtime; the muscle FK is the pose (see bone_animated).
            continue
        if shoulder is not None and shoulder not in bones:
            shoulder = None
        chains.append((upper, lower, end, target_name, hint_name, shoulder))
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
    for upper, lower, end, target_name, _hint, _sh in chains:
        end_offsets[end] = (rest[target_name].to_quaternion().inverted()
                            @ rest[end].to_quaternion())

    frames = range(int(frame_start), int(frame_end) + 1)
    n_frames = len(frames)
    # Output: bone -> (4, n_frames) quaternion basis values.
    out = {}
    for upper, lower, end, _t, _h, shoulder in chains:
        for n in (upper, lower, end) + ((shoulder,) if shoulder else ()):
            out[n] = [[0.0] * n_frames for _ in range(4)]

    # One bend-plane tracker per chain, carried ACROSS frames -- see _PoleTracker.
    trackers = {chain: _PoleTracker() for chain in chains}
    # Fallback bend side when even frame 0 is degenerate: elbows/knees hang
    # downward in armature space, a neutral side that matches a combat idle.
    down = Vector((0.0, 0.0, -1.0))

    # Per-chain constants. Division of trust, per measurement:
    #   * the auto-generated FK's ROTATIONS are garbage (single-frame 180deg
    #     spins) and are never consulted;
    #   * the FK's elbow POSITION side, however, is the animation's real
    #     intent -- and it degrades exactly when it stops mattering (a
    #     near-straight arm has h ~= 0, so the bend side is cosmetic there),
    #     so it serves as the arm bend-plane hint through _PoleTracker's
    #     continuity filter. Legs use their real animated IK_Knee hints.
    #   * segment lengths come from rest (bones are rigid);
    #   * segment ROTATIONS are back-derived from the end target through the
    #     chain's REST relative rotations (wrist keeps its rest relationship
    #     to the forearm, elbow to the upper arm), each aim-corrected onto the
    #     solved joint positions. Twist therefore follows the hand/foot --
    #     anatomically how pronation works -- instead of being pinned to the
    #     bend plane (which let the wrist wind up ~180deg against the forearm)
    #     or extracted from FK (which re-imported the spins). At rest inputs
    #     the whole construction reproduces rest exactly.
    consts = {}
    for chain in chains:
        upper, lower, end, target_name, hint_name, shoulder = chain
        rest_a = rest[upper].translation
        rest_k = rest[lower].translation
        rest_t = rest[end].translation
        rot_u = rest[upper].to_quaternion()
        rot_l = rest[lower].to_quaternion()
        rot_e = rest[end].to_quaternion()
        consts[chain] = {
            "l1": (rest_k - rest_a).length,
            "l2": (rest_t - rest_k).length,
            # Bone-vector directions in each bone's LOCAL frame (for aim correction).
            "aim_local_u": rot_u.inverted() @ (rest_k - rest_a).normalized(),
            "aim_local_l": rot_l.inverted() @ (rest_t - rest_k).normalized(),
            # Rest relative rotations for the back-derivation.
            "rel_end_inv": (rot_l.inverted() @ rot_e).inverted(),
            "rel_lower_inv": (rot_u.inverted() @ rot_l).inverted(),
        }

    for fi, frame in enumerate(frames):
        pose = {}
        for n in ordered:
            local = rest_rel[n] @ sampler.basis(n, frame)
            parent = parent_of[n]
            pose[n] = (pose[parent] @ local) if parent is not None else local

        for chain in chains:
            upper, lower, end, target_name, hint_name, shoulder = chain
            c = consts[chain]
            l1, l2 = c["l1"], c["l2"]
            reach = l1 + l2
            target = pose[target_name]
            target_pos = target.translation
            chain_bones = ((shoulder,) if shoulder else ()) + (upper, lower, end)

            # Per-frame IK weight from how closely the target tracks the FK
            # end THIS frame (see _IK_FULL_DIST block comment).
            d_fk = (target_pos - pose[end].translation).length
            w = (_IK_OFF_DIST - d_fk) / (_IK_OFF_DIST - _IK_FULL_DIST)
            w = 0.0 if w < 0.0 else (1.0 if w > 1.0 else w)

            base_pose = None
            if shoulder is not None:
                # Clavicle baseline = the skeleton's OWN rest (basis identity),
                # at ANY weight: the muscle shoulder data is auto-generated
                # filler holding a constant 24deg shrug (see _CHAINS doc), so
                # it is discarded outright.
                sh_parent = parent_of[shoulder]
                base_pose = (pose[sh_parent] @ rest_rel[shoulder]) if sh_parent is not None \
                    else rest_rel[shoulder]

            if w <= 1e-4:
                # Pure muscle FK for the limb. The clavicle still sits at its
                # rest baseline, so the UPPER bone's basis is re-derived to
                # keep the arm's muscle WORLD pose unchanged under the new
                # parent -- the correction is absorbed in the clavicle/upper
                # seam instead of swinging the whole arm by the shrug delta.
                for n in chain_bones:
                    if n == shoulder:
                        bq = Quaternion((1.0, 0.0, 0.0, 0.0))
                    elif shoulder is not None and n == upper:
                        bq = (rest_rel[upper].to_quaternion().inverted()
                              @ base_pose.to_quaternion().inverted()
                              @ pose[upper].to_quaternion())
                        bq.normalize()
                    else:
                        bq = sampler.basis_quat(n, frame)
                    col = out[n]
                    col[0][fi] = bq.w
                    col[1][fi] = bq.x
                    col[2][fi] = bq.y
                    col[3][fi] = bq.z
                continue

            new_rot = {}

            if shoulder is not None:
                # Clavicle assist (arms): from the rest baseline the clavicle
                # swings toward the target only as far as needed for the arm
                # to reach -- the recruitment the runtime provably performs
                # (targets outrange the bare arm in the real data).
                c_head = base_pose.translation
                s0 = (base_pose @ rest_rel[upper]).translation

                swing = Quaternion((1.0, 0.0, 0.0, 0.0))
                if (target_pos - s0).length > reach * 0.999:
                    arm_dir = s0 - c_head
                    tgt_dir = target_pos - c_head
                    axis = arm_dir.cross(tgt_dir)
                    if axis.length > 1e-8 and arm_dir.length > 1e-8:
                        axis.normalize()
                        # Bisect the swing angle that brings the shoulder just
                        # within reach of the target (monotonic toward the
                        # target), capped at _SHOULDER_ASSIST_MAX_RAD.
                        lo, hi = 0.0, _SHOULDER_ASSIST_MAX_RAD
                        for _ in range(24):
                            mid = 0.5 * (lo + hi)
                            s_mid = c_head + Quaternion(axis, mid) @ arm_dir
                            if (target_pos - s_mid).length > reach * 0.999:
                                lo = mid
                            else:
                                hi = mid
                        swing = Quaternion(axis, hi)

                new_rot[shoulder] = swing @ base_pose.to_quaternion()
                a = c_head + swing @ (s0 - c_head)
            else:
                a = pose[upper].translation

            if hint_name is not None:
                raw_pole = pose[hint_name].translation
            else:
                # FK elbow position: the side it sits on is the animation's
                # intent (see the consts block comment); the tracker bridges
                # its degenerate straight-arm stretches, exactly where the
                # side stops mattering.
                raw_pole = pose[lower].translation

            pole_dir = trackers[chain].resolve(a, target_pos, raw_pole, down)
            k_new = _solve_mid_position(a, target_pos, pole_dir, l1, l2)

            # Back-derived world rotations (see the consts block comment):
            # end = target; lower = end at its REST wrist relationship,
            # aim-corrected onto k_new->target; upper = lower at its REST
            # elbow relationship, aim-corrected onto a->k_new. The aim
            # corrections also guarantee the joints land exactly on the
            # solved positions regardless of twist.
            r_end = target.to_quaternion() @ end_offsets[end]

            candidate_l = r_end @ c["rel_end_inv"]
            aim_now = candidate_l @ c["aim_local_l"]
            r_lower = aim_now.rotation_difference(target_pos - k_new) @ candidate_l

            candidate_u = r_lower @ c["rel_lower_inv"]
            aim_now = candidate_u @ c["aim_local_u"]
            r_upper = aim_now.rotation_difference(k_new - a) @ candidate_u

            new_rot[upper] = r_upper
            new_rot[lower] = r_lower
            new_rot[end] = r_end

            # Blend the solved world rotations toward the FK pose by the
            # frame's weight, then convert to pose basis chaining through the
            # SAME blended parent rotations (self-consistent hierarchy). The
            # clavicle's low-weight reference is its rest BASELINE (never the
            # muscle shrug); the limb's is the muscle world pose -- matching
            # exactly what the w=0 passthrough writes, so the blend converges
            # continuously into it.
            if w < 1.0:
                for n in chain_bones:
                    if n == shoulder:
                        fk_ref = base_pose.to_quaternion()
                    else:
                        fk_ref = pose[n].to_quaternion()
                    new_rot[n] = fk_ref.slerp(new_rot[n], w)

            for n in chain_bones:
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

    return [f"{(shoulder + '+') if shoulder else ''}{upper}->{end} via {target_name}"
            for upper, lower, end, target_name, _h, shoulder in chains]
