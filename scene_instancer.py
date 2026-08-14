"""Game-blind massed-placement kernel: N placements of K distinct assets as ONE
point-cloud object driving GPU instancing, never N scene objects.

A real streaming map places 10^5 objects. One ``bpy.types.Object`` per placement
is 10^5 depsgraph nodes, 10^5 outliner rows and 10^5 draw calls -- the session is
dead after the import even if the import itself were free. The running game never
does that either; it instances. So does this:

* every DISTINCT asset is built ONCE as a source object, linked into a sources
  collection that is deliberately NOT in the scene (unlinked data is exactly how
  Blender spells "exists but is not drawn or selectable");
* every placement is ONE POINT of an instancer mesh, carrying two attributes:
  ``ruri_asset`` (int, which source) and ``ruri_xform`` (float4x4, its world
  transform). Writing all placements is two ``foreach_set`` memcpys;
* a shared geometry-nodes group turns points into instances: Collection Info
  (separate children, reset children) -> Instance on Points (pick instance by
  ``ruri_asset``) -> Set Instance Transform (by ``ruri_xform``). Pick-instance
  indexes the collection's children in NAME order, so sources are prefixed with
  their zero-padded index -- the order is therefore stated, not assumed;
* the group has a ``Realize`` switch (default off). Instances are not
  individually selectable by design -- flipping Realize on the modifier turns
  them into real editable geometry when a session genuinely needs that.

Hidden placements (a renderer that draws nothing in the game right now but is
imported for completeness) go on a SECOND instancer object whose own visibility
is off -- per-instance hiding does not exist, per-object does.

Everything here is data in, objects out: no game names, no asset semantics.
"""

from __future__ import annotations

import numpy as np

import bpy

NODE_GROUP_NAME = "RuriSceneInstances"
ASSET_ATTRIBUTE = "ruri_asset"
TRANSFORM_ATTRIBUTE = "ruri_xform"

# Blender stores a float4x4 attribute in its internal matrix layout while the
# numpy rows here are mathutils-convention (row-major, translation in the last
# column). If instances land translated-but-unrotated-wrongly, the storage is
# transposed relative to this assumption -- flip this one constant.
TRANSPOSE_ATTRIBUTE_MATRICES = True


def _interface_socket(tree, name, in_out, socket_type):
    return tree.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)


def ensure_node_group():
    """The one shared instancing node group, created on first use. Idempotent by
    name: a .blend that already carries it (an earlier import this session, an
    appended library) reuses it unchanged."""
    tree = bpy.data.node_groups.get(NODE_GROUP_NAME)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(NODE_GROUP_NAME, "GeometryNodeTree")
    _interface_socket(tree, "Geometry", "INPUT", "NodeSocketGeometry")
    _interface_socket(tree, "Sources", "INPUT", "NodeSocketCollection")
    _interface_socket(tree, "Realize", "INPUT", "NodeSocketBool")
    _interface_socket(tree, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes = tree.nodes
    links = tree.links
    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-700, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (700, 0)

    collection_info = nodes.new("GeometryNodeCollectionInfo")
    collection_info.location = (-450, -160)
    collection_info.transform_space = "ORIGINAL"
    collection_info.inputs["Separate Children"].default_value = True
    collection_info.inputs["Reset Children"].default_value = True
    links.new(group_in.outputs["Sources"], collection_info.inputs["Collection"])

    asset_attr = nodes.new("GeometryNodeInputNamedAttribute")
    asset_attr.location = (-450, 140)
    asset_attr.data_type = "INT"
    asset_attr.inputs["Name"].default_value = ASSET_ATTRIBUTE

    transform_attr = nodes.new("GeometryNodeInputNamedAttribute")
    transform_attr.location = (-200, 260)
    transform_attr.data_type = "FLOAT4X4"
    transform_attr.inputs["Name"].default_value = TRANSFORM_ATTRIBUTE

    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (-150, 0)
    instance.inputs["Pick Instance"].default_value = True
    links.new(group_in.outputs["Geometry"], instance.inputs["Points"])
    links.new(collection_info.outputs["Instances"], instance.inputs["Instance"])
    links.new(asset_attr.outputs["Attribute"], instance.inputs["Instance Index"])

    set_transform = nodes.new("GeometryNodeSetInstanceTransform")
    set_transform.location = (100, 0)
    links.new(instance.outputs["Instances"], set_transform.inputs["Instances"])
    links.new(transform_attr.outputs["Attribute"], set_transform.inputs["Transform"])

    realize = nodes.new("GeometryNodeRealizeInstances")
    realize.location = (330, -160)
    links.new(set_transform.outputs["Instances"], realize.inputs["Geometry"])

    switch = nodes.new("GeometryNodeSwitch")
    switch.location = (520, 0)
    switch.input_type = "GEOMETRY"
    links.new(group_in.outputs["Realize"], switch.inputs["Switch"])
    links.new(set_transform.outputs["Instances"], switch.inputs["False"])
    links.new(realize.outputs["Geometry"], switch.inputs["True"])
    links.new(switch.outputs["Output"], group_out.inputs["Geometry"])
    return tree


def build_sources_collection(name, source_objects):
    """File the distinct-asset source objects under one collection that is NOT
    linked anywhere in the scene, renaming each with its zero-padded pick index
    so the collection's name-ordered children ARE the pick order. The sources
    arrive linked into the working collection (the mesh builder links what it
    builds); they are moved, not copied."""
    collection = bpy.data.collections.new(name)
    for index, obj in enumerate(source_objects):
        obj.name = "{0:04d} {1}".format(index, obj.name)
        for holder in list(obj.users_collection):
            holder.objects.unlink(obj)
        collection.objects.link(obj)
    return collection


def _write_points(mesh, asset_indices, matrices):
    count = len(asset_indices)
    mesh.vertices.add(count)
    asset_attr = mesh.attributes.new(ASSET_ATTRIBUTE, "INT", "POINT")
    asset_attr.data.foreach_set("value", np.ascontiguousarray(asset_indices, dtype=np.int32))
    transform_attr = mesh.attributes.new(TRANSFORM_ATTRIBUTE, "FLOAT4X4", "POINT")
    mats = np.ascontiguousarray(matrices, dtype=np.float32)
    if TRANSPOSE_ATTRIBUTE_MATRICES:
        mats = np.ascontiguousarray(mats.transpose(0, 2, 1))
    transform_attr.data.foreach_set("value", mats.reshape(-1))


def _bind_modifier(obj, tree, sources_collection):
    """Bind the group and fill its inputs through the modifier's own typed
    interface (``modifier.properties.inputs.<socket identifier>.value``). The
    older ``modifier["Socket_2"] = value`` IDProperty spelling is gone: a
    NodesModifier reports "this type doesn't support IDProperties" outright,
    so the socket identifier now names an RNA attribute instead of a key."""
    modifier = obj.modifiers.new("RuriInstances", "NODES")
    modifier.node_group = tree
    inputs = modifier.properties.inputs
    for socket in tree.interface.items_tree:
        if getattr(socket, "in_out", "") != "INPUT":
            continue
        if socket.name == "Sources":
            getattr(inputs, socket.identifier).value = sources_collection
        elif socket.name == "Realize":
            getattr(inputs, socket.identifier).value = False
    return modifier


def build_instancer(context, name, sources_collection, asset_indices, matrices, hidden=False):
    """One instancer object: a point per placement. ``asset_indices`` is (n,)
    int, ``matrices`` (n, 4, 4) float world transforms in Blender space (root
    yaw already folded in -- these ARE the final object matrices). Returns the
    object, or None for an empty row set."""
    if len(asset_indices) == 0:
        return None
    mesh = bpy.data.meshes.new(name)
    _write_points(mesh, asset_indices, matrices)
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    _bind_modifier(obj, ensure_node_group(), sources_collection)
    if hidden:
        obj.hide_viewport = True
        obj.hide_render = True
    return obj
