"""The collision volumes of the nodes, as guide prims.

A volume on a node of the scene is a child prim of that node's prim. A volume on a joint of a rig is a child of the rig
root, placed where the joint's rest puts it and naming the joint it follows, because a joint is not a prim to hang it
under. A box, sphere or capsule is the core schema of that shape; a mesh collider refers to the piece that draws the same
mesh, or to a piece of its own where nothing draws it. Whatever shape a prim has it is a guide: what states the volume is
its ``ruri:collider:`` attributes, in the source's own meaning and the layer's basis, and a reader that cares for the
volume reads those and never the geometry.
"""

from __future__ import annotations

from pxr import Gf, Sdf, UsdGeom

from . import transforms
from .layout import Layout

#: The child prim of a node that states one of its volumes, and the one of a rig that states a volume of one of its joints --
#: apart, because a rig's layer and the scene that refers to it both put children under the same root prim.
NAME = "Collider"
JOINT_NAME = "JointCollider"

_IDENTITY = (0.0, 0.0, 0.0, 1.0)
_AXES = ("X", "Y", "Z")


def _state(prim, name, kind, value):
    prim.CreateAttribute("ruri:collider:" + name, kind, custom=True).Set(value)


def _vector(values):
    return Gf.Vec3f(float(values[0]), float(values[1]), float(values[2]))


def _place(prim, center, scale, frame):
    """The shape at ``center`` of its owner's space; ``frame`` is that space in the layer's, or None where the prim is
    already a child of its owner."""
    if frame is None:
        transforms.author(prim, center, _IDENTITY, scale)
        return
    UsdGeom.Xformable(prim).AddTransformOp().Set(
        transforms.to_usd(frame @ transforms.local_matrix(center, _IDENTITY, scale)))


def author(stage, layer_path, parent_path, collider, names, piece=None, frame=None, joint=None):
    """The volume ``collider`` as a child of the prim at ``parent_path``. A mesh collider needs the ``piece`` that holds
    its mesh; one on a joint (``frame`` and ``joint`` given) is not written. Returns whether anything was written."""
    path = parent_path.AppendChild(names.claim(str(parent_path), JOINT_NAME if joint else NAME))
    if collider.kind == "mesh":
        if piece is None or frame is not None:
            return False
        prim = UsdGeom.Xform.Define(stage, path).GetPrim()
        prim.GetReferences().AddReference(Layout.reference(layer_path, piece.layer_path), piece.root)
        prim.SetInstanceable(True)
    elif collider.kind == "box":
        shape = UsdGeom.Cube.Define(stage, path)
        shape.CreateSizeAttr(1.0)
        prim = shape.GetPrim()
        _place(prim, collider.center, collider.size, frame)
    elif collider.kind == "sphere":
        shape = UsdGeom.Sphere.Define(stage, path)
        shape.CreateRadiusAttr(float(collider.radius))
        prim = shape.GetPrim()
        _place(prim, collider.center, (1.0, 1.0, 1.0), frame)
    elif collider.kind == "capsule":
        shape = UsdGeom.Capsule.Define(stage, path)
        shape.CreateRadiusAttr(float(collider.radius))
        shape.CreateHeightAttr(max(float(collider.height) - 2.0 * float(collider.radius), 0.0))
        shape.CreateAxisAttr(_AXES[collider.axis])
        prim = shape.GetPrim()
        _place(prim, collider.center, (1.0, 1.0, 1.0), frame)
    else:
        raise ValueError("a collider of kind '{0}' is not one this writer knows.".format(collider.kind))
    UsdGeom.Imageable(prim).CreatePurposeAttr(UsdGeom.Tokens.guide)
    _state(prim, "kind", Sdf.ValueTypeNames.Token, collider.kind)
    _state(prim, "trigger", Sdf.ValueTypeNames.Bool, bool(collider.trigger))
    _state(prim, "enabled", Sdf.ValueTypeNames.Bool, bool(collider.enabled))
    _state(prim, "layer", Sdf.ValueTypeNames.Int, int(collider.layer))
    if joint:
        _state(prim, "joint", Sdf.ValueTypeNames.Token, joint)
    if collider.material:
        _state(prim, "material", Sdf.ValueTypeNames.Token, collider.material)
    if collider.kind == "mesh":
        _state(prim, "convex", Sdf.ValueTypeNames.Bool, bool(collider.convex))
        _state(prim, "mesh", Sdf.ValueTypeNames.Token, collider.mesh)
        return True
    _state(prim, "center", Sdf.ValueTypeNames.Float3, _vector(collider.center))
    if collider.kind == "box":
        _state(prim, "size", Sdf.ValueTypeNames.Float3, _vector(collider.size))
        return True
    _state(prim, "radius", Sdf.ValueTypeNames.Float, float(collider.radius))
    if collider.kind == "capsule":
        _state(prim, "height", Sdf.ValueTypeNames.Float, float(collider.height))
        _state(prim, "axis", Sdf.ValueTypeNames.Int, int(collider.axis))
    return True
