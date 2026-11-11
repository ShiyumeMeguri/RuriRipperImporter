"""The scene layer: the transform hierarchy, and a reference wherever it draws something already written.

Every node the scene keeps is a prim at its place in the hierarchy, carrying its local transform and what
the source says of it. A node that draws a static piece has a child prim that refers to the piece's layer and
is instanceable, so the pieces of one kind are one prototype however many objects draw them. The root of a
rig refers to the rig's layer, and everything below it is that layer's. Nothing here is geometry.
"""

from __future__ import annotations

from pxr import Sdf, UsdGeom, UsdSkel

from .. import statement as kernel_statement
from . import cinemachine as cinemachine_module
from . import colliders as collider_module
from . import sources, stages, transforms
from .layout import Layout
from .names import Namer, identifier

#: The child prim of a node that draws a static piece.
RENDERER = "MeshRenderer"


def _refer(prim, scene_layer, target_layer, target_root):
    prim.GetReferences().AddReference(Layout.reference(scene_layer, target_layer), target_root)


def author(layer_path, scene_name, statement, hierarchy, rig_roots, covered, characters, pieces, colliders=None,
           collision_pieces=None, cinemachine=None):
    """Write the scene ``scene_name`` to ``layer_path``. ``characters`` maps a rig root's node index to the
    written character, ``pieces`` a node index to the written piece it draws, ``colliders`` a node index to the
    volumes it carries (a node below a rig root has none here: its rig holds them), ``collision_pieces`` a
    mesh key to the written piece holding it and ``cinemachine`` a node index to the components of the camera package
    it carries (``cinemachine.by_node``, less the nodes inside a rig, whose rig's layer states them as ``attachments``; one
    that reaches here for a node this layer does not write is refused). Returns counts of what was written."""
    stage = stages.create(layer_path, kernel_statement.USD, "Scene {0}".format(scene_name))
    top_path = Sdf.Path("/" + identifier(scene_name))
    top = UsdGeom.Xform.Define(stage, top_path)
    stage.SetDefaultPrim(top.GetPrim())
    for root in statement.roots:
        if (root.position, root.rotation, root.scale) != ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)):
            raise ValueError("the usd basis states no top-level turn, and root '{0}' asks for one.".format(root.label))
    names = Namer()
    paths = {}
    colliders = colliders or {}
    collision_pieces = collision_pieces or {}
    cinemachine = cinemachine or {}
    counts = {"prims": 1, "references": 0, "instanceable": 0, "characters": 0, "inside_rigs": 0, "colliders": 0,
              "colliders_without_geometry": 0, "cinemachine_nodes": 0, "cinemachine_components": 0}
    stated = {"prims": 0, "attributes": 0}
    for node in hierarchy.nodes:
        index = node.index
        if covered[index] and index not in rig_roots:
            counts["inside_rigs"] += 1
            continue
        parent_path = top_path if node.parent < 0 else paths[node.parent]
        path = parent_path.AppendChild(names.claim(str(parent_path), identifier(node.name)))
        prim = (UsdSkel.Root.Define(stage, path) if index in rig_roots else UsdGeom.Xform.Define(stage, path)).GetPrim()
        transforms.author(prim, node.position, node.rotation, node.scale)
        sources.describe(prim, node, hierarchy.source_paths[index])
        paths[index] = path
        counts["prims"] += 1
        if index in cinemachine:
            cinemachine_module.author(stage, prim, cinemachine[index], names, stated)
            counts["cinemachine_nodes"] += 1
            counts["cinemachine_components"] += len(cinemachine[index])
        if index in rig_roots:
            character = characters[index]
            _refer(prim, layer_path, character.layer_path, character.root)
            counts["references"] += 1
            counts["characters"] += 1
        elif index in pieces:
            piece = pieces[index]
            renderer = UsdGeom.Xform.Define(stage, path.AppendChild(names.claim(str(path), RENDERER))).GetPrim()
            sources.renderer_state(renderer, node)
            _refer(renderer, layer_path, piece.layer_path, piece.root)
            renderer.SetInstanceable(True)
            counts["prims"] += 1
            counts["references"] += 1
            counts["instanceable"] += 1
        for collider in colliders.get(index, ()):
            if collider_module.author(stage, layer_path, path, collider, names, collision_pieces.get(collider.mesh)):
                counts["prims"] += 1
                counts["colliders"] += 1
                counts["references"] += collider.kind == "mesh"
                counts["instanceable"] += collider.kind == "mesh"
            else:
                counts["colliders_without_geometry"] += 1
    lost = sorted(hierarchy.source_paths[index] for index in cinemachine if index not in paths)
    if lost:
        raise ValueError("components of the camera package sit on nodes the scene layer does not write and no rig's layer "
                         "states: {0}.".format(", ".join(lost)))
    counts["cinemachine_prims"] = stated["prims"]
    counts["cinemachine_attributes"] = stated["attributes"]
    stage.GetRootLayer().Save()
    return counts
