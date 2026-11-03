"""The layer of one static piece: a mesh with the materials it is drawn with.

A piece is a mesh and the list of materials its slots wear, and nothing about where it stands: every
object of a scene that draws the same mesh with the same materials is one piece, written once and
referred to from the scene. Its root prim is the identity, so the scene's own transform for each object
is the only transform an instance has.
"""

from __future__ import annotations

from pxr import Sdf, UsdGeom

from . import geometry, materials, stages
from .. import statement as kernel_statement
from .names import Namer, identifier

class Piece:
    """What a scene needs to refer to a written piece."""

    __slots__ = ("name", "layer_path", "root")

    def __init__(self, name, layer_path):
        self.name = name
        self.layer_path = layer_path
        self.root = Sdf.Path("/" + name)


def author(layer_path, name, mesh, material_keys, statement, textures, families):
    """Write the piece ``name`` to ``layer_path``. Returns the :class:`Piece`."""
    stage = stages.create(layer_path, kernel_statement.USD, "Static piece {0}".format(name))
    names = Namer()
    root = UsdGeom.Xform.Define(stage, "/" + name)
    stage.SetDefaultPrim(root.GetPrim())
    names.claim(str(root.GetPath()), materials.SCOPE)
    prim = root.GetPrim()
    prim.CreateAttribute("ruri:sourceName", Sdf.ValueTypeNames.String, custom=True).Set(mesh.name)
    prim.CreateAttribute("ruri:sourceKey", Sdf.ValueTypeNames.String, custom=True).Set(mesh.key)
    made = {}
    slot_materials = []
    folder = None
    for key in material_keys:
        stated = statement.materials.get(key) if key else None
        if stated is None:
            slot_materials.append(None)
            continue
        if key not in made:
            if folder is None:
                folder = materials.scope(stage, root.GetPath())
            made[key] = materials.author(stage, folder, stated, textures, layer_path, families, names)
        slot_materials.append(made[key])
    mesh_path = root.GetPath().AppendChild(names.claim(str(root.GetPath()), identifier(mesh.name)))
    geom = geometry.author(stage, mesh_path, mesh)
    geometry.bind_materials(geom, mesh, slot_materials, names)
    stage.GetRootLayer().Save()
    return Piece(name, layer_path)
