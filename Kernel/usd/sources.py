"""What the source says about a node, as ``ruri:`` attributes on the prim that stands for it.

The attributes are of basic types only (string, int, bool, float, float[]), so a reader needs no
schema of ours to read them, and a prim keeps the name its source gave it in ``ruri:sourcePath``
whatever USD allowed it to be called.
"""

from __future__ import annotations

from pxr import Sdf, UsdGeom, Vt

from .. import statement as kernel_statement


UNTAGGED = "Untagged"


def _attribute(prim, name, kind, value):
    prim.CreateAttribute(name, kind, custom=True).Set(value)


def describe(prim, node, source_path):
    """The node's place in its source, whether its own object is active (activeSelf, whatever its ancestors are), what it is, and its tag unless that is the default."""
    _attribute(prim, "ruri:sourcePath", Sdf.ValueTypeNames.String, source_path)
    _attribute(prim, "ruri:layer", Sdf.ValueTypeNames.Int, int(node.layer))
    _attribute(prim, "ruri:activeSelf", Sdf.ValueTypeNames.Bool, bool(node.active_self))
    _attribute(prim, "ruri:nodeKind", Sdf.ValueTypeNames.Token, node.kind)
    if node.tag and node.tag != UNTAGGED:
        _attribute(prim, "ruri:tag", Sdf.ValueTypeNames.String, node.tag)
    if not node.active_self:
        UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    if node.kind in (kernel_statement.MESH, kernel_statement.SKINNED):
        _attribute(prim, "ruri:castShadows", Sdf.ValueTypeNames.Int, int(node.shadows))
    if node.camera is not None:
        _camera(prim, node)
    if node.light is not None:
        _light(prim, node)


def renderer_state(prim, node):
    """Whether the renderer ``prim`` stands for is itself enabled, apart from the activity of the object it sits on."""
    _attribute(prim, "ruri:enabled", Sdf.ValueTypeNames.Bool, bool(node.component_enabled))


def _camera(prim, node):
    camera = node.camera
    _attribute(prim, "ruri:camera:enabled", Sdf.ValueTypeNames.Bool, bool(node.component_enabled))
    _attribute(prim, "ruri:camera:fieldOfView", Sdf.ValueTypeNames.Float, float(camera["fov"]))
    _attribute(prim, "ruri:camera:near", Sdf.ValueTypeNames.Float, float(camera["near"]))
    _attribute(prim, "ruri:camera:far", Sdf.ValueTypeNames.Float, float(camera["far"]))
    _attribute(prim, "ruri:camera:orthographic", Sdf.ValueTypeNames.Bool, bool(camera["orthographic"]))
    _attribute(prim, "ruri:camera:orthographicSize", Sdf.ValueTypeNames.Float, float(camera["ortho_size"]))


def _light(prim, node):
    light = node.light
    _attribute(prim, "ruri:light:enabled", Sdf.ValueTypeNames.Bool, bool(node.component_enabled))
    _attribute(prim, "ruri:light:type", Sdf.ValueTypeNames.Int, int(light["kind"]))
    _attribute(prim, "ruri:light:color", Sdf.ValueTypeNames.FloatArray,
               Vt.FloatArray([float(component) for component in light["color"]]))
    for key in ("intensity", "range", "reach", "angle", "inner_angle", "width", "height", "volume", "specular"):
        _attribute(prim, "ruri:light:" + _camel(key), Sdf.ValueTypeNames.Float, float(light[key]))
    _attribute(prim, "ruri:light:shadows", Sdf.ValueTypeNames.Bool, bool(light["shadows"]))
    _attribute(prim, "ruri:light:shadowResolution", Sdf.ValueTypeNames.Int, int(light["shadow_resolution"]))


def _camel(key):
    head, *tail = key.split("_")
    return head + "".join(word.capitalize() for word in tail)
