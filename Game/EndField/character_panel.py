"""UI for a character's face: browse the SkeletalMorph library the cabmap
carries, bind its ctrl drivers to whatever this rig actually exposes, drive them
live, and bake the morph animations into Blender actions.

Endfield does not animate faces with transform curves -- body clips stop at the
humanoid "Jaw Close" muscle and key no facial joint at all. Expression lives in
a separate family of assets that animate named **ctrl drivers** (a weight per
ctrl, constant for a pose, an AnimationCurve for an animation). See
skeletal_morph.py in this folder for the format.

draw_character_tab() is the draw function this game's "Character" tab declares
(see the package's GAME_MODULE), exactly like
scene_panel.draw_streaming_scene_tab -- so it
shares the core panel's cabmap-loaded gate and tab bar instead of being a
separate always-on sub-panel.

BINDING. A ctrl is a name, not a target; what it moves is per-rig, and two
kinds of target exist:

  BoneBinding      the real payload. A character's SkeletalMorphAvatarDataSO
                   (``data_facemorph_avatar_<name>.asset``, plus an ear table)
                   lists, per ctrl, a TRS delta for each bone it moves. These
                   ACCUMULATE, so the whole face is solved in one pass.
  ShapeKeyBinding  the handful of ctrls a mesh bakes as ``<ctrl>_tx_max`` shape
                   keys (pelica has four, on her eyebrows). Independent per ctrl.

Both are merged into one RigBinding, so a ctrl bound as both drives both, and a
third kind of target is a new provider class rather than a rewrite.

The avatar table only exists in an export when AssetRipper's
[SerializeReference] pass is on -- which it always is, from the C# side's own
Bootstrap.AlwaysOnHookIds. Without it the whole MonoBehaviour is dropped and
every character's face silently binds to nothing.
"""

from __future__ import annotations

import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, PointerProperty, StringProperty)

from ... import animation_builder, coordinate
from ...RuriRipperPyBridge.session import cabmap_state
from . import morph_state, roster_panel, skeletal_morph

# The game bakes a ctrl driver onto a mesh as a shape key named after the DCC
# rig channel that drove it: "<ctrl>_tx_max" (translate-X at its maximum), with
# a "_min" twin when the ctrl is bidirectional. Stripping that suffix is how a
# shape key is matched back to its ctrl.
_SHAPE_SUFFIX_RE = re.compile(r"_(?:[trs][xyz])_(max|min)$", re.IGNORECASE)

DRIVER_SECTION = "driver"

# Display names only -- the section IDENTIFIERS are the game's own kinds, which
# is why an unlisted kind still gets a readable tab from the fallback rather
# than needing an entry here.
_KIND_LABELS = {"emotion": "Emotions", "pose": "Poses", "morphanimation": "Animations",
                "cfg": "Config"}

# Kept alive at module scope: Blender's dynamic-enum items callback requires the
# returned list to outlive the call.
_section_items_cache = [(DRIVER_SECTION, "Drivers", "Every ctrl driver, live")]


def _section_items(self, context):
    """One tab per kind the loaded library actually has assets for, plus the
    Drivers view. Derived, not enumerated: a game update that adds a morph
    folder shows up as a tab, and the unparseable ``cfg`` folder never does
    (nothing in it parses, so it contributes no tab)."""
    global _section_items_cache
    items = []
    for kind in morph_state.kinds():
        if not morph_state.loaded_of_kind(kind, kind in morph_state.SCOPED_KINDS):
            continue
        items.append((kind, _KIND_LABELS.get(kind, kind.title()),
                      f"The cabmap's {kind} assets"))
    items.append((DRIVER_SECTION, "Drivers", "Every ctrl driver on this rig, live"))
    _section_items_cache = items
    return _section_items_cache


def _report_exception(op, prefix, exc):
    import traceback
    traceback.print_exc()
    op.report({"ERROR"}, f"{prefix}: {type(exc).__name__}: {exc} (full traceback in console)")


# ── rig discovery ─────────────────────────────────────────────────────────────

def resolve_rig(context):
    """The armature this tab drives: the active object when it is one, else the
    scene's only armature. Returns (armature_obj, error_message)."""
    active = context.active_object
    if active is not None and active.type == "ARMATURE":
        return active, ""
    armatures = [obj for obj in context.scene.objects if obj.type == "ARMATURE"]
    if len(armatures) == 1:
        return armatures[0], ""
    if not armatures:
        return None, "No armature in the scene -- import a character first."
    return None, "Multiple armatures -- select the one whose face you want to drive."


def rig_meshes(armature_obj):
    """Every mesh deformed by this armature: anything parented to it, plus
    anything carrying an Armature modifier aimed at it (the two ways an
    imported character's meshes attach)."""
    meshes = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.parent is armature_obj:
            meshes.append(obj)
            continue
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is armature_obj:
                meshes.append(obj)
                break
    return meshes


# ── ctrl -> Blender property binding ──────────────────────────────────────────

class MorphTarget:
    """One concrete thing a ctrl drives on this rig: an owner id-datablock, the
    RNA data path to animate, and the sign the ctrl's weight enters with (a
    "_min" shape key answers to negative weight)."""

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


def _resolve_shape_keys(armature_obj, meshes):
    """ctrl -> [MorphTarget] for every shape key on this rig whose name is a
    baked ctrl channel. The mesh's Key datablock owns the animation, which is
    why the target carries it as the owner rather than the mesh object."""
    bindings = {}
    for obj in meshes:
        shape_keys = obj.data.shape_keys
        if shape_keys is None:
            continue
        for key_block in shape_keys.key_blocks:
            match = _SHAPE_SUFFIX_RE.search(key_block.name)
            if match is None:
                continue
            ctrl = key_block.name[:match.start()]
            sign = -1.0 if match.group(1).lower() == "min" else 1.0
            bindings.setdefault(ctrl, []).append(MorphTarget(
                owner=shape_keys,
                data_path=key_block.path_from_id("value"),
                sign=sign,
                setter=_key_block_setter(key_block),
                label=f"{obj.name} · {key_block.name}"))
    return bindings


def _key_block_setter(key_block):
    def setter(value):
        key_block.value = min(max(value, key_block.slider_min), key_block.slider_max)
    return setter


class ShapeKeyBinding:
    """The ctrls this rig exposes as baked shape keys. Each ctrl drives its own
    key independently, so applying one never disturbs another."""

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


# Unity's Transform.localEulerAngles composes Z, then X, then Y. mathutils names
# an order by the axis sequence it applies, so that is "ZXY" -- but the base pose
# in an avatar is a round trip of the rig's own rest transform, which makes the
# order VERIFIABLE rather than a guess: compose the base with each candidate and
# keep whichever reproduces the armature's rest matrix. See BoneBinding._pick_euler_order.
_EULER_ORDER_CANDIDATES = ("ZXY", "XYZ", "YXZ", "ZYX", "XZY", "YZX")
_EULER_SIGN_CANDIDATES = tuple((x, y, z) for x in (1.0, -1.0)
                               for y in (1.0, -1.0) for z in (1.0, -1.0))


class BoneBinding:
    """The real payload of the whole system: a ctrl moves BONES, by the per-bone
    TRS deltas its character's SkeletalMorphAvatarDataSO lists.

    Unlike shape keys these accumulate -- several ctrls routinely push the same
    bone -- so a pose is applied as one pass over every bone the avatar knows:

        local(bone) = base(bone) + sum(weight_i * delta_i(bone))

    and that Unity-space local is turned into a pose-bone basis with the same
    conjugation identity the clip importer uses.
    """

    def __init__(self, armature_obj, avatars, maps):
        from mathutils import Matrix  # local: this module is imported headless too

        self.armature = armature_obj
        self.avatars = list(avatars)
        self.euler_order = "ZXY"
        self.euler_signs = (1.0, 1.0, 1.0)
        self.rest_error = float("nan")
        self._matrix = Matrix
        self._conversion = coordinate.conversion_matrix()
        self._bones = {}       # bone name -> (pose_bone, rest_local_inverted)
        self._base = {}        # bone name -> (position, rotation, scale)
        self._mappings = {}    # ctrl -> [(bone name, dposition, drotation, dscale)]

        # Merged BY BONE NAME, never by boneID: each avatar indexes its own
        # allBoneNames, so a face table's id 83 and an ear table's id 83 are
        # different bones. The name is the only identity shared across them.
        for avatar in self.avatars:
            for base in avatar.base_pose.values():
                if base.bone_name:
                    self._base.setdefault(base.bone_name,
                                          (base.position, base.rotation, base.scale))
            for ctrl, deltas in avatar.mappings.items():
                merged = self._mappings.setdefault(ctrl, [])
                merged.extend((delta.bone_name, delta.position, delta.rotation, delta.scale)
                              for delta in deltas if delta.bone_name)

        pose_bones = armature_obj.pose.bones
        name_to_rest = _rest_locals_by_bone_name(maps)
        for name in sorted(self._base):
            pose_bone = pose_bones.get(name)
            rest = name_to_rest.get(name)
            if pose_bone is None or rest is None:
                continue
            self._bones[name] = (pose_bone, rest.inverted_safe())
        self.euler_order = self._pick_euler_order(name_to_rest)

    @property
    def bone_count(self):
        """How many of the avatars' bones this rig actually has."""
        return len(self._bones)

    @property
    def base_bone_count(self):
        return len(self._base)

    @property
    def ctrls(self):
        """Only the ctrls that actually reach a bone THIS rig has -- an avatar
        entry whose bones are all missing here is not bound."""
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
        return names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")

    def _pick_euler_order(self, name_to_rest):
        """Which Euler convention the avatar's degrees are in, decided BY THE
        DATA rather than asserted: the base pose is the rig's own rest pose, so
        the right convention is whichever reproduces each bone's rest matrix.

        Both the axis order and a per-axis sign are searched, because Unity's
        euler rotations are left-handed and mathutils' are not -- which axes come
        out negated depends on the exporter's handedness fix-up, and guessing it
        would silently mirror every expression. ``rest_error`` is kept so the UI
        can show how well the winner actually fits."""
        # Every bone, not a sample: rest_error doubles as the tolerance callers
        # compare against, so it has to be a true bound rather than an average
        # over whichever bones happened to be first.
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
        euler = Euler([_radians(value * sign)
                       for value, sign in zip(rotation_degrees, signs)],
                      order or self.euler_order)
        translation = Matrix.Translation(position)
        rotation = euler.to_matrix().to_4x4()
        scaling = Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
        return translation @ rotation @ scaling

    def apply(self, weights):
        """One pass over every bone the avatar knows -- bones no active ctrl
        touches land back on their base pose, so a pose REPLACES rather than
        piling onto whatever was there."""
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
        """Which of this rig's bones the given ctrls can move -- the set an
        action needs channels for."""
        names = set()
        for ctrl in ctrls:
            for name, _p, _r, _s in self._mappings.get(ctrl, ()):
                if name in self._bones:
                    names.add(name)
        return names


def _radians(degrees):
    import math
    return math.radians(degrees)


def _rest_locals_by_bone_name(maps):
    """bone name -> its Unity-space rest local matrix, out of the rig identity
    stamped on the armature at import time (the same maps the clip importer
    keys transform curves through)."""
    if not maps:
        return {}
    path_to_bone = maps.get("path_to_bone") or {}
    rest = {}
    for node in (maps.get("nodes") or {}).values():
        path = getattr(node, "path", "")
        bone_name = path_to_bone.get(path)
        if bone_name:
            rest[bone_name] = node.local
    return rest


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


def _avatars_for(state):
    """The avatar tables belonging to this rig's character.

    Joined by the game's own tag when the character is known: its data asset
    states a SkeletalMorphComponentData tagId and each avatar table states the
    same one back, so the pairing is identity rather than a guess about names.
    A rig the roster never loaded has no character to look up, and only then is
    the name token all there is to go on."""
    tag = roster_panel.character_tag(state.character_token)
    if tag:
        matched = morph_state.avatars_for_tag(tag)
        if matched:
            return matched
    return morph_state.avatars_for(state.character_token)


def resolve_bindings(armature_obj, avatars=()):
    """Everything this rig exposes: baked shape keys always, plus the avatar's
    bone table when one is loaded for this character. Both are merged into one
    RigBinding -- a ctrl bound as both drives both."""
    providers = [ShapeKeyBinding(_resolve_shape_keys(armature_obj, rig_meshes(armature_obj)))]
    avatars = [a for a in (avatars or ()) if a.mappings]
    if avatars:
        maps = _rig_maps(armature_obj)
        if maps:
            bone_binding = BoneBinding(armature_obj, avatars, maps)
            if bone_binding.ctrls:
                providers.append(bone_binding)
    return RigBinding(armature_obj.name, providers)


def _rig_maps(armature_obj):
    """The Unity rig identity stamped on the armature at import time. Without it
    there is no rest matrix to conjugate against, so bone binding is impossible
    and says so rather than posing the face wrongly."""
    from ... import prefab_importer
    try:
        return prefab_importer.maps_from_stamped_armature(armature_obj)
    except Exception:
        return None


# Session-lived: rebuilt by Scan Rig, keyed by armature name so a stale binding
# from a deleted/replaced rig can never be applied to the wrong object.
BINDING = None


def _bindings_for(state):
    """The current rig's binding, rebuilt in place whenever it does not belong
    to the armature the tab is pointed at -- an addon reload drops the module
    globals while the parsed library survives, and a re-imported rig is a new
    object with the same name. Healing here rather than requiring another Scan
    Rig keeps "bound" from silently meaning "bound to something else".
    Read-only over bpy data, so it is safe from a draw callback."""
    global BINDING
    if not state.armature_name:
        return RigBinding("", [])
    if BINDING is None or BINDING.armature_name != state.armature_name:
        armature_obj = bpy.data.objects.get(state.armature_name)
        if armature_obj is None or armature_obj.type != "ARMATURE":
            return RigBinding("", [])
        BINDING = resolve_bindings(armature_obj, _avatars_for(state))
    return BINDING


# ── applying weights ──────────────────────────────────────────────────────────

def apply_weights(state, weights):
    """Drive every bound ctrl to its weight and every other bound ctrl to zero,
    so applying a pose REPLACES the face instead of accumulating onto whatever
    the previous one left behind."""
    intensity = state.intensity
    scaled = {ctrl: weight * intensity for ctrl, weight in weights.items()}
    _bindings_for(state).apply(scaled)
    _sync_driver_rows(state, weights)


def clear_face(state):
    apply_weights(state, {})


def _sync_driver_rows(state, weights):
    """Mirror the applied weights back onto the Drivers list so its sliders
    show what the face is actually doing. Writes through the raw RNA property
    to avoid re-entering the slider's own update callback."""
    for row in state.drivers:
        row["weight"] = weights.get(row.ctrl, 0.0)


def _on_driver_weight(self, context):
    """Dragging one slider re-applies the WHOLE driver set, not just that ctrl:
    bone deltas accumulate, so a bone several ctrls share can only be solved
    from all of their weights at once. The other sliders keep their values, so
    what the user sees is still "I moved this one"."""
    state = context.scene.ruri_character
    _bindings_for(state).apply({row.ctrl: row.weight for row in state.drivers})


# ── property groups ───────────────────────────────────────────────────────────

class RURI_PG_morph_item(bpy.types.PropertyGroup):
    """One library entry as the UI shows it. ``asset`` is the game's own name for
    it, which is the identity the whole morph library is keyed by; the asset
    itself lives in morph_state (never mirrored into RNA -- 634 animations with
    full curves have no business in the .blend)."""
    name: StringProperty()
    asset: StringProperty()
    kind: StringProperty()
    duration: FloatProperty()
    ctrl_count: IntProperty()
    bound_count: IntProperty()
    animated: BoolProperty(default=False,
                           description="This entry drives its ctrls over time rather than "
                                       "holding one pose")
    selected: BoolProperty(name="", default=False,
                           description="Include this animation when building actions")


class RURI_PG_morph_driver(bpy.types.PropertyGroup):
    ctrl: StringProperty()
    channel: StringProperty()
    bound: BoolProperty(default=False)
    target_label: StringProperty()
    weight: FloatProperty(name="", default=0.0, min=-1.0, max=1.0, subtype="FACTOR",
                          update=_on_driver_weight,
                          description="Drive this ctrl directly")


def _section_changed(self, context):
    _populate_items(context.scene.ruri_character)


class RURI_PG_character(bpy.types.PropertyGroup):
    armature_name: StringProperty(name="Rig")
    character_token: StringProperty(
        name="Character",
        description="Name fragment that marks this character's own facial "
                    "animations in the cabmap (e.g. \"pelica\")")
    section: EnumProperty(name="Section", items=_section_items, update=_section_changed)
    status: StringProperty(default="Scan a rig to begin.")
    library_ready: BoolProperty(default=False)

    intensity: FloatProperty(name="Intensity", default=1.0, min=0.0, max=1.0,
                             subtype="FACTOR",
                             description="Scales every weight an applied pose writes")
    show_unbound: BoolProperty(name="Show Unbound", default=False,
                               description="Also list ctrl drivers this rig has no target for")
    apply_on_click: BoolProperty(name="Apply on Click", default=True,
                                 description="Selecting an entry poses the face immediately")

    items: CollectionProperty(type=RURI_PG_morph_item)
    items_active_index: IntProperty(update=lambda self, context: _on_item_activated(context))
    drivers: CollectionProperty(type=RURI_PG_morph_driver)
    drivers_active_index: IntProperty()

    phoneme_set: EnumProperty(name="Set", items=lambda self, context: _phoneme_set_items(),
                              description="Which phoneme pose set the mouth buttons use")


# ── library population ────────────────────────────────────────────────────────

def _character_only(state):
    """Whether this section's kind was narrowed to the character at load time.
    Read from morph_state, which decided it -- never re-guessed from the
    section name."""
    return state.section in morph_state.SCOPED_KINDS


def _populate_items(state):
    """Refill the visible list for the current section from the parsed library."""
    state.items.clear()
    if state.section == DRIVER_SECTION:
        return
    bindings = _bindings_for(state)
    for asset in morph_state.loaded_of_kind(state.section, _character_only(state)):
        item = state.items.add()
        item.name = asset.name
        item.asset = asset.name
        item.kind = asset.kind
        item.duration = asset.duration
        item.animated = asset.is_animated()
        ctrls = asset.ctrl_names()
        item.ctrl_count = len(ctrls)
        item.bound_count = sum(1 for ctrl in ctrls if ctrl in bindings)
    state.items_active_index = min(state.items_active_index, max(len(state.items) - 1, 0))
    _populate_drivers(state)


def _populate_drivers(state):
    """One row per ctrl in the whole loaded library, bound ones first -- the
    complete vocabulary this character could be driven with, with the truth
    about which of it this rig can actually move."""
    state.drivers.clear()
    bindings = _bindings_for(state)
    channel_of = {}
    for asset in morph_state.ASSETS.values():
        for driver in asset.drivers():
            channel_of.setdefault(driver.ctrl, driver.channel)
    # A rig can expose ctrls the loaded animation library never mentions -- list
    # those too, so the Drivers view is the rig's full capability, not just what
    # this character's clips happen to use.
    for ctrl in bindings.ctrls:
        channel_of.setdefault(ctrl, "")
    bound_ctrls = bindings.ctrls
    for ctrl in sorted(channel_of, key=lambda c: ctrl_sort_key(c, bound_ctrls)):
        row = state.drivers.add()
        row.ctrl = ctrl
        row.channel = (skeletal_morph.channel_label(channel_of[ctrl])
                       if channel_of[ctrl] else skeletal_morph.ctrl_group(ctrl).title())
        row.bound = ctrl in bound_ctrls
        row.target_label = bindings.label_for(ctrl)


def ctrl_sort_key(ctrl, bound_ctrls):
    return (0 if ctrl in bound_ctrls else 1, skeletal_morph.ctrl_group(ctrl), ctrl)


def _on_item_activated(context):
    """Highlighting a STATIC entry poses the face; an animated one is a clip to
    be built, not a pose to snap to, so it stays inert. Decided per item, not
    per section."""
    state = context.scene.ruri_character
    index = state.items_active_index
    if not state.apply_on_click or not (0 <= index < len(state.items)):
        return
    if state.items[index].animated:
        return
    _apply_item(state, index)


def _apply_item(state, index):
    if not (0 <= index < len(state.items)):
        return False
    asset = morph_state.ASSETS.get(state.items[index].asset)
    if asset is None:
        return False
    apply_weights(state, skeletal_morph.sample_weights(asset, 0.0))
    return True


def _phoneme_set_items():
    """The phoneme sets the lipsync config declares, as enum items. Rebuilt
    from the parsed library, so a game update that adds a set just shows up."""
    global _phoneme_items_cache
    sets = []
    for asset in morph_state.ASSETS.values():
        if asset.kind == "lipsync":
            sets = sorted(asset.phoneme_sets)
            break
    _phoneme_items_cache = [(str(s), f"Set {s}", "") for s in sets] or [("", "(no lipsync config)", "")]
    return _phoneme_items_cache


# Kept alive at module scope: Blender's dynamic-enum items callback requires the
# returned list to outlive the call (a fresh literal can leave the C-level enum
# pointing at freed strings).
_phoneme_items_cache = [("", "(no lipsync config)", "")]


def _lipsync_asset():
    for asset in morph_state.ASSETS.values():
        if asset.kind == "lipsync":
            return asset
    return None


# ── operators ─────────────────────────────────────────────────────────────────

class RURI_OT_character_scan(bpy.types.Operator):
    """Bind the rig and index the cabmap's morph library -- both cheap: the
    binding is a walk over this armature's meshes, and discovery reads the
    already-loaded row table's container paths (no VFS decrypt, no export)."""
    bl_idname = "ruri.character_scan"
    bl_label = "Scan Rig"
    bl_description = "Detect the character rig, bind its ctrl drivers, and index the cabmap's facial-morph library"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.scene.ruri_cabmap.loaded and len(cabmap_state.ROWS) > 0

    def execute(self, context):
        global BINDING
        state = context.scene.ruri_character
        armature_obj, error = resolve_rig(context)
        if armature_obj is None:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        state.armature_name = armature_obj.name
        token = state.character_token.strip() or _suggest_token(armature_obj.name)
        morph_state.discover(token)
        state.character_token = token
        # Bound AFTER discovery: the bone table comes from this character's
        # avatar, which only exists once Load Library has run, so a first scan
        # binds shape keys only and Load Library re-binds with the real table.
        BINDING = resolve_bindings(armature_obj, morph_state.avatars_for(token))

        counts = morph_state.counts()
        own = sum(morph_state.counts(character_only=True).values()) if token else 0
        state.status = (f"{sum(counts.values())} morph asset(s) indexed"
                        + (f", {own} for '{token}'" if token else "")
                        + f"; {len(BINDING)} ctrl(s) bound on {armature_obj.name}.")
        state.library_ready = False
        state.items.clear()
        state.drivers.clear()
        self.report({"INFO"}, state.status)
        return {"FINISHED"}


def _suggest_token(armature_name):
    """Guess the character token from the rig's own name by testing each of its
    name fragments against the discovered library: the fragment that matches
    some assets but not most of them is the character's. Derived, not tabulated
    -- a rig naming convention this importer has never seen still resolves."""
    if not morph_state.LIBRARY:
        morph_state.discover("")
    names = [entry["name"].lower()
             for entries in morph_state.LIBRARY.values() for entry in entries]
    if not names:
        return ""
    best, best_hits = "", 0
    for fragment in re.split(r"[^A-Za-z0-9]+", armature_name.lower()):
        if len(fragment) < 4 or fragment.isdigit():
            continue
        hits = sum(1 for name in names if fragment in name)
        if 0 < hits < len(names) // 2 and hits > best_hits:
            best, best_hits = fragment, hits
    return best


class RURI_OT_character_load_library(bpy.types.Operator):
    """Resolve and parse the morph assets themselves. This is the first point
    anything is exported at all -- discovery above only read the cabmap's own
    tables."""
    bl_idname = "ruri.character_load_library"
    bl_label = "Load Library"
    bl_description = ("Export and parse the shared emotion/pose/lipsync library plus this "
                      "character's own morph animations")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(morph_state.LIBRARY) and cabmap_state.BRIDGE is not None

    def execute(self, context):
        state = context.scene.ruri_character
        entries, dropped = morph_state.plan_load()
        cabs = morph_state.cabs_for(entries)
        if not cabs:
            self.report({"WARNING"}, "Nothing to load -- run Scan Rig first.")
            return {"CANCELLED"}
        if dropped:
            # Never a silent cap: say what was left out and why.
            self.report({"INFO"}, f"{dropped} asset(s) skipped -- "
                                  f"{sorted(morph_state.SCOPED_KINDS)} narrowed to "
                                  f"'{morph_state.CHARACTER_TOKEN}'.")

        try:
            parsed = morph_state.load(cabs)
        except Exception as exc:
            _report_exception(self, "Morph library load failed", exc)
            return {"CANCELLED"}

        # Re-bind now, not before: the character's avatar -- the ctrl-to-bone
        # table the whole system runs on -- only exists after this load.
        global BINDING
        armature_obj = bpy.data.objects.get(state.armature_name)
        avatars = _avatars_for(state)
        if armature_obj is not None and armature_obj.type == "ARMATURE":
            BINDING = resolve_bindings(armature_obj, avatars)
        bindings = _bindings_for(state)
        vocabulary = morph_state.all_ctrl_names()
        bound = sum(1 for ctrl in vocabulary if ctrl in bindings)
        state.library_ready = True
        bone_binding = bindings.bone_binding()
        via = []
        if bone_binding is not None:
            via.append(f"{len(bone_binding.ctrls)} via bones")
        shape_binding = bindings.shape_binding()
        if shape_binding is not None and shape_binding.ctrls:
            via.append(f"{len(shape_binding.ctrls)} via shape keys")
        state.status = (f"{parsed} asset(s) parsed · {len(vocabulary)} ctrl driver(s) · "
                        f"{bound} bound on {state.armature_name or 'this rig'}"
                        + (" (" + ", ".join(via) + ")" if via else "") + ".")
        if not avatars:
            self.report({"WARNING"}, "No SkeletalMorphAvatarDataSO matched this character -- "
                                     "only baked shape keys can be driven. Check the Character "
                                     "token names the right operator.")
        _populate_items(state)
        _populate_drivers(state)
        self.report({"INFO"}, state.status)
        return {"FINISHED"}


def _bridge_asset_db_module():
    """Imported lazily -- only the bridge path needs it, and this tab draws long
    before any bridge session exists."""
    from ...RuriRipperPyBridge.unity import bridge_asset_db
    return bridge_asset_db


class RURI_OT_character_apply(bpy.types.Operator):
    bl_idname = "ruri.character_apply"
    bl_label = "Apply"
    bl_description = "Pose the face with the highlighted entry"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_character
        return len(state.items) > 0

    def execute(self, context):
        state = context.scene.ruri_character
        if not _apply_item(state, state.items_active_index):
            self.report({"WARNING"}, "Nothing to apply.")
            return {"CANCELLED"}
        return {"FINISHED"}


class RURI_OT_character_clear(bpy.types.Operator):
    bl_idname = "ruri.character_clear"
    bl_label = "Rest Face"
    bl_description = "Zero every bound ctrl driver"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        clear_face(context.scene.ruri_character)
        return {"FINISHED"}


class RURI_OT_character_apply_phoneme(bpy.types.Operator):
    """Pose one phoneme of the selected lipsync set. The slot index is the
    position within that set's pose list -- the config's own ordering, which is
    what the game indexes with too."""
    bl_idname = "ruri.character_apply_phoneme"
    bl_label = "Phoneme"
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return _lipsync_asset() is not None

    def execute(self, context):
        state = context.scene.ruri_character
        lipsync = _lipsync_asset()
        try:
            set_id = int(state.phoneme_set)
        except (TypeError, ValueError):
            self.report({"WARNING"}, "No phoneme set selected.")
            return {"CANCELLED"}
        poses = lipsync.phoneme_sets.get(set_id) or []
        if not (0 <= self.slot < len(poses)) or not poses[self.slot]:
            self.report({"WARNING"}, "This set has no pose in that slot.")
            return {"CANCELLED"}
        asset = morph_state.ASSETS.get(poses[self.slot])
        if asset is None:
            self.report({"WARNING"}, "That phoneme pose is not in the loaded closure.")
            return {"CANCELLED"}
        apply_weights(state, skeletal_morph.sample_weights(asset, 0.0))
        return {"FINISHED"}


class RURI_OT_character_select_all(bpy.types.Operator):
    bl_idname = "ruri.character_select_all"
    bl_label = "Select"
    bl_description = "Check / uncheck every listed animation"
    bl_options = {"REGISTER"}

    mode: EnumProperty(items=[("ALL", "All", ""), ("NONE", "None", ""),
                              ("INVERT", "Invert", "")], default="ALL")

    def execute(self, context):
        for item in context.scene.ruri_character.items:
            item.selected = (self.mode == "ALL" if self.mode != "INVERT" else not item.selected)
        return {"FINISHED"}


class RURI_OT_character_build_actions(bpy.types.Operator):
    """Bake the checked morph animations into Blender actions on the shape-key
    datablocks their ctrls resolve to. Keyframes carry the game's own times and
    weights -- no resampling -- with Bezier handles from each key's in/out
    slopes, so the curve a bound ctrl plays is the curve the game authored."""
    bl_idname = "ruri.character_build_actions"
    bl_label = "Build Checked Actions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        state = context.scene.ruri_character
        return any(item.selected for item in state.items)

    def execute(self, context):
        state = context.scene.ruri_character
        bindings = _bindings_for(state)
        if not bindings:
            self.report({"ERROR"}, "No ctrl driver on this rig is bound -- nothing to key.")
            return {"CANCELLED"}

        fps = context.scene.render.fps
        built, skipped = 0, 0
        for item in state.items:
            if not item.selected:
                continue
            asset = morph_state.ASSETS.get(item.asset)
            if asset is None or not asset.is_animated():
                skipped += 1
                continue
            if build_morph_action(asset, bindings, fps):
                built += 1
            else:
                skipped += 1

        if not built:
            self.report({"WARNING"}, f"Nothing built -- none of the {skipped} checked "
                                     f"animation(s) drive a ctrl bound on this rig.")
            return {"CANCELLED"}
        message = f"Built {built} morph action(s)."
        if skipped:
            message += f" {skipped} skipped (no bound ctrl)."
        state.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}


def build_morph_action(asset, bindings, fps):
    """Bake one animation onto whatever this rig binds it to: shape-key value
    curves where the ctrl is a baked blendshape, pose-bone transforms where it
    moves bones. Returns True when at least one curve was written."""
    wrote = _build_shape_key_action(asset, bindings.shape_binding(), fps)
    wrote |= _build_bone_action(asset, bindings.bone_binding(), fps)
    return wrote


def _build_shape_key_action(asset, shape_binding, fps):
    """Shape-key channels keep the game's own key times -- a ctrl maps 1:1 onto
    one key's value, so there is nothing to resample."""
    if shape_binding is None:
        return False
    by_owner = {}
    for driver in asset.drivers():
        if driver.curve is None:
            continue
        for target in shape_binding.targets.get(driver.ctrl, ()):
            by_owner.setdefault(target.owner, []).append((driver, target))
    if not by_owner:
        return False

    for owner, pairs in by_owner.items():
        if owner.animation_data is None:
            owner.animation_data_create()
        action = bpy.data.actions.new(asset.name)
        if hasattr(action, "use_fake_user"):
            action.use_fake_user = True
        fcurves, slot = animation_builder._prepare_channels(action, action.name, "KEY")
        for driver, target in pairs:
            _write_weight_fcurve(fcurves, driver, target, fps)
        owner.animation_data.action = action
        if slot is not None:
            owner.animation_data.action_slot = slot
    return True


def _build_bone_action(asset, bone_binding, fps):
    """Bone channels are SAMPLED per frame, not keyed at the source times: a
    bone's transform is the sum of every ctrl pushing it, and those ctrls have
    independent key times, so there is no shared key grid to preserve. Same
    approach the clip importer takes for transform curves.

    Only the bones this animation's own ctrls can move get channels -- a face
    animation must not overwrite bones it never mentions."""
    if bone_binding is None:
        return False
    animated = {driver.ctrl for driver in asset.drivers() if driver.curve is not None}
    bones = bone_binding.bones_for(animated)
    if not bones:
        return False

    import numpy as np

    duration = asset.duration or max(
        (driver.curve.last_time() for driver in asset.drivers() if driver.curve is not None),
        default=0.0)
    frame_count = max(1, int(round(duration * fps)) + 1)
    times = np.arange(frame_count, dtype=np.float64) / fps
    frames = times * fps

    samples = [skeletal_morph.sample_weights(asset, float(time)) for time in times]

    armature_obj = bone_binding.armature
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()
    action = bpy.data.actions.new(asset.name)
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
        animation_builder._write_bone_fcurves(fcurves, bone_name, frames,
                                              locations, quaternions, scales)

    armature_obj.animation_data.action = action
    if slot is not None:
        armature_obj.animation_data.action_slot = slot
    return True


def _write_weight_fcurve(fcurves, driver, target, fps):
    """The ctrl's weight curve as one fcurve on the target's value. Unity keys
    are cubic Hermite (value + in/out slope); Blender's Bezier handles express
    the identical segment when each handle sits a third of the way into it --
    the standard Hermite-to-Bezier conversion, so no resampling is needed and
    the game's own key times survive."""
    curve = driver.curve
    times = curve.times
    if not len(times):
        return
    values = curve.values[:, 0] * target.sign
    if target.sign < 0.0:
        values = values.clip(min=0.0)
    fcurve = fcurves.new(target.data_path)
    fcurve.keyframe_points.add(len(times))
    for index, point in enumerate(fcurve.keyframe_points):
        frame = float(times[index]) * fps
        point.co = (frame, float(values[index]))
        point.interpolation = "BEZIER"
        # Slopes are per SECOND in Unity; on a frame-based fcurve they are per
        # frame, hence the /fps. The handle sits one third of the neighbouring
        # segment away -- shorter at the ends, where there is only one segment.
        previous_gap = (float(times[index] - times[index - 1]) * fps) if index else 0.0
        next_gap = (float(times[index + 1] - times[index]) * fps
                    if index + 1 < len(times) else 0.0)
        in_slope = float(curve.in_slopes[index, 0]) * target.sign / fps
        out_slope = float(curve.out_slopes[index, 0]) * target.sign / fps
        left = previous_gap / 3.0 if previous_gap else (next_gap / 3.0 if next_gap else 1.0)
        right = next_gap / 3.0 if next_gap else (previous_gap / 3.0 if previous_gap else 1.0)
        point.handle_left_type = "FREE"
        point.handle_right_type = "FREE"
        point.handle_left = (frame - left, float(values[index]) - in_slope * left)
        point.handle_right = (frame + right, float(values[index]) + out_slope * right)
    fcurve.update()


# ── lists ─────────────────────────────────────────────────────────────────────

class RURI_UL_morph_items(bpy.types.UIList):
    """Emotions/poses read as one-click entries; animations get a checkbox and a
    duration, because they are built in batches rather than applied."""

    bl_idname = "RURI_UL_morph_items"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        if item.animated:
            row.prop(item, "selected", text="")
        coverage = row.row(align=True)
        # Unbound entries stay listed and stay usable -- they are real game
        # data; greying them says "this rig cannot show it" without hiding it.
        coverage.active = item.bound_count > 0
        coverage.label(text=item.name)
        meta = row.row(align=True)
        meta.alignment = "RIGHT"
        if item.duration:
            meta.label(text=f"{item.duration:.2f}s")
        meta.label(text=f"{item.bound_count}/{item.ctrl_count}")


class RURI_UL_morph_drivers(bpy.types.UIList):
    bl_idname = "RURI_UL_morph_drivers"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        head = row.row(align=True)
        head.active = item.bound
        head.label(text=item.ctrl)
        row.label(text=item.channel)
        slider = row.row(align=True)
        slider.enabled = item.bound
        slider.prop(item, "weight", text="")

    def filter_items(self, context, data, property_name):
        state = context.scene.ruri_character
        rows = getattr(data, property_name)
        flags = [self.bitflag_filter_item] * len(rows)
        if not state.show_unbound:
            for index, row in enumerate(rows):
                if not row.bound:
                    flags[index] = 0
        return flags, []


# ── tab ───────────────────────────────────────────────────────────────────────

def draw_character_tab(layout, context):
    """Draw the Character tab's content into ``layout``. The core panel already
    handled the cabmap-loaded gate every tab shares.

    Two halves: the game's own cast list on top (roster_panel), and below it the
    face rig for whichever character is actually in the scene."""
    roster_panel.draw_roster(layout.box(), context)

    state = context.scene.ruri_character

    rig_row = layout.row(align=True)
    rig_row.prop(state, "character_token", icon="OUTLINER_OB_ARMATURE")
    rig_row.operator(RURI_OT_character_scan.bl_idname, text="", icon="FILE_REFRESH")

    if state.armature_name:
        info = layout.row(align=True)
        info.label(text=state.armature_name, icon="ARMATURE_DATA")
        if not state.library_ready:
            info.operator(RURI_OT_character_load_library.bl_idname,
                          text="Load Library", icon="IMPORT")

    layout.label(text=state.status, icon="INFO")

    if not state.library_ready:
        return

    body = layout.column()
    body.row(align=True).prop(state, "section", expand=True)

    if state.section == DRIVER_SECTION:
        _draw_drivers(body, state)
        return

    body.template_list(RURI_UL_morph_items.bl_idname, "", state, "items",
                       state, "items_active_index", rows=12)

    # Animated entries are BUILT in batches; static ones are APPLIED. Which
    # controls appear follows the data in the list, not the tab's name.
    if any(item.animated for item in state.items):
        bar = body.row(align=True)
        for mode, label in (("ALL", "All"), ("NONE", "None"), ("INVERT", "Invert")):
            bar.operator(RURI_OT_character_select_all.bl_idname, text=label).mode = mode
        checked = sum(1 for item in state.items if item.selected)
        bar.label(text=f"{checked} checked" if checked else "")
        body.operator(RURI_OT_character_build_actions.bl_idname, icon="ACTION")
        return

    controls = body.column(align=True)
    controls.prop(state, "intensity", slider=True)
    row = controls.row(align=True)
    row.operator(RURI_OT_character_apply.bl_idname, icon="PLAY")
    row.operator(RURI_OT_character_clear.bl_idname, icon="LOOP_BACK")
    controls.prop(state, "apply_on_click")
    _draw_phonemes(body, state)


def _draw_phonemes(layout, state):
    """The mouth shapes as a keyboard rather than a list: the lipsync config
    maps a phoneme SET to an ordered list of poses, so the buttons are that
    list's own slots -- a set with more slots simply grows more buttons."""
    lipsync = _lipsync_asset()
    if lipsync is None:
        return
    # Only where the mouth shapes themselves are: the keyboard is a shortcut
    # INTO the listed poses, so it appears exactly when the list contains some
    # of the poses the lipsync config points at.
    referenced = {name for poses in lipsync.phoneme_sets.values() for name in poses if name}
    if not any(item.asset in referenced for item in state.items):
        return
    box = layout.box()
    header = box.row(align=True)
    header.label(text="Lip Sync", icon="SORTALPHA")
    header.prop(state, "phoneme_set", text="")
    try:
        poses = lipsync.phoneme_sets.get(int(state.phoneme_set)) or []
    except (TypeError, ValueError):
        return
    keys = box.row(align=True)
    for slot, pose in enumerate(poses):
        asset = morph_state.ASSETS.get(pose) if pose else None
        cell = keys.row(align=True)
        cell.enabled = asset is not None
        # The pose's own name tail is the phoneme ("..._normal_a" -> "A"); an
        # empty slot keeps its place so the keyboard layout stays stable.
        label = asset.name.rsplit("_", 1)[-1].upper() if asset else "·"
        cell.operator(RURI_OT_character_apply_phoneme.bl_idname, text=label).slot = slot


def _draw_drivers(layout, state):
    bindings = _bindings_for(state)
    total = len(state.drivers)
    bound = sum(1 for row in state.drivers if row.bound)
    header = layout.row(align=True)
    header.label(text=f"{bound} of {total} ctrl driver(s) bound", icon="CON_ACTION")
    header.prop(state, "show_unbound", toggle=True)
    layout.template_list(RURI_UL_morph_drivers.bl_idname, "", state, "drivers",
                         state, "drivers_active_index", rows=14)
    layout.operator(RURI_OT_character_clear.bl_idname, icon="LOOP_BACK")

    bone_binding = bindings.bone_binding()
    if bone_binding is not None:
        info = layout.box()
        signs = "".join("+-"[value < 0] for value in bone_binding.euler_signs)
        info.label(text=f"{bone_binding.bone_count} bone(s) driven · euler "
                        f"{bone_binding.euler_order} {signs}", icon="BONE_DATA")
        # The avatar's base pose IS this rig's rest pose, so how closely the two
        # agree is a live correctness readout, not decoration: a large number
        # means the table got matched to the wrong rig.
        # Calibrated, not guessed: the right rig fits to ~1e-2 (a few bones sit
        # at a neutral morph pose rather than the bind pose), while binding the
        # WRONG character's table measured ~1.7 -- so anything past 0.5 means
        # the table does not belong to this rig.
        info.label(text=f"Rest-pose fit: {bone_binding.rest_error:.1e}"
                        + ("  (wrong rig?)" if bone_binding.rest_error > 0.5 else ""))
    elif not bindings.ctrls:
        note = layout.box()
        note.label(text="Nothing bound. This rig has no baked ctrl", icon="ERROR")
        note.label(text="shape keys and no avatar table matched it --")
        note.label(text="run Load Library, and check the Character token.")
    elif bound < total:
        note = layout.box()
        note.label(text="Unbound ctrls belong to other characters'", icon="INFO")
        note.label(text="clips in the shared library.")


_CLASSES = (
    RURI_PG_morph_item,
    RURI_PG_morph_driver,
    RURI_PG_character,
    RURI_UL_morph_items,
    RURI_UL_morph_drivers,
    RURI_OT_character_scan,
    RURI_OT_character_load_library,
    RURI_OT_character_apply,
    RURI_OT_character_clear,
    RURI_OT_character_apply_phoneme,
    RURI_OT_character_select_all,
    RURI_OT_character_build_actions,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ruri_character = PointerProperty(type=RURI_PG_character)


def unregister():
    global BINDING
    del bpy.types.Scene.ruri_character
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    BINDING = None
    morph_state.reset()
