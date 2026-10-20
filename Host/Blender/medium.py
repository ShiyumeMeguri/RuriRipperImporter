"""A level's participating medium, as an EEVEE volume the renderer integrates itself.

The source integrates its fog in a grid of froxels and every material reads the integral at
its own depth. EEVEE integrates volume objects in a froxel grid of its own and composites
the result over each surface after the surface's material has run -- so the medium goes in
as a volume object, and the material's own share of the integral is the identity, which is
what the generated stacks fold that query to. A world volume would not do: EEVEE switches
every sun off under a world volume that absorbs.

The volume object is volume GEOMETRY -- an empty mesh whose node modifier emits one grid of density
one over the domain, wearing the medium's material -- displayed as bounds. Material Preview draws no
mesh displayed below solid, so a closed mesh box would drop the whole medium there; EEVEE syncs volume
geometry whatever its display, and Workbench draws nothing displayed below solid, so the viewport's
solid mode keeps showing the level instead of a box around it. EEVEE also skips a volume object whose
material reads none of its grids (as Cycles does), so the grid is the medium's extent: the material
scales everything by whether the grid's density reaches half, which holds everywhere inside the domain
(its voxels are cell-centred) and nowhere outside.

The medium's extinction is grey: ``density_scale * sum(density * 2^-max(-127, falloff * (y -
height)))`` over the source's own height ``y``, one term per stated layer. It scatters its
albedo times that and absorbs the rest -- a Volume Scatter and a Volume Absorption of one
colour and one density, whose extinctions sum to that density in every channel. What it adds
of the sky and of itself goes in as volume emission: ``max(dot(L1, (g * ray, 1)) *
ambient_scale, 0)`` per channel times the scattering, plus the stated emission. Everything
is scaled by the near fade-in over the distance travelled past the range's start.

The sun it scatters is the level sun itself. The source lights its medium with the main light,
through the main light's shadow maps, but along the phase's atmosphere direction
(``lightConfig.rotationAtmosphere``, which a level may point away from the main light's own), in the
colour the phase states before decoding it and at the undivided intensity, scaled by the medium's
direct scattering; the level sun carries the decoded colour at the intensity over pi. The direction
only enters through the scattering's anisotropy: an isotropic medium is lit the same from any
direction, so it is stood up whatever the two directions are, and a medium that states no main light
at all needs none. An anisotropic medium lit along a direction the level sun does not have, or
stating light the sun cannot carry (a channel the sun emits nothing in), is not stood up -- the
import says so -- since one sun cannot carry two directions or a colour it does not have. A second sun
would not do: EEVEE tags shadow pages for every sun at every visible pixel and froxel whatever
its light linking says, and two suns overflow the shadow pool at full resolution, which EEVEE
answers by dropping shadows. EEVEE's volume lighting is linear in the light's colour and in the
medium's scattering colour, so the difference is exact to fold: the level sun takes the volume
share ``I * s * max(k) / E`` (``k`` = stated colour / sun colour per channel, ``E`` its energy),
the medium's scattering and absorption colours become ``albedo * k / max(k)`` -- their
extinctions still sum to the density, every factor stays at most one -- and every lamp copy
takes its colour over ``k / max(k)``, so a lamp scatters exactly what it did before.

A lamp scatters into the medium by the volume factor the statement gave it. The source lights
its medium with its lamps unshadowed (unless the medium says otherwise) and without the
inverse-square singularity: its falloff is ``1 / (d^2 + 1)``. EEVEE's volume falloff for a
sphere light of radius ``r``, ``2 / (d^2 + r^2 + d sqrt(d^2 + r^2))``, tends to
``1 / (d^2 + 0.75 r^2)``, so a copy of radius ``2 / sqrt(3)`` has the source's far field and a
finite centre (half again the source's value there). Each such lamp gets that copy, for the
medium alone, and gives up its own volume share.

The grid is the source's: its slices over ``[start, end]`` with its log2 depth distribution
and one froxel per its pixel tile. The source's slices sit at ``(2^(z/S) - O) / B`` for slice
``z`` of ``Z``; EEVEE's at the same form over ``t = z / (Z - 1)`` with ``S' = 4 (1 -
distribution)``, so ``distribution = 1 - S / (4 (Z - 1))``. EEVEE's own world and probe
lighting of volumes is clamped away (the medium states its sky in-scatter itself), and its
volume self-shadowing is off, as the source's is.
"""

from __future__ import annotations

import json
import math

import bpy
from mathutils import Vector

from . import material_builder, plugin_data

#: The custom property on a level's world that keeps the medium's statement as JSON: the level's content, like its
#: globals and its texel blocks, so a file that holds the world holds the fog. The volume, the lamps' copies and
#: the shares handed over are derived from it and are made again whenever a file opens (see :func:`restore`).
STATEMENT_PROPERTY = "ruri_level_medium"
MEDIUM = "Ruri Level Medium"
RECEIVERS = "Ruri Level Medium Receivers"
TWIN_SUFFIX = " (medium)"
#: On a lamp's copy: the lamp it copies.
TWIN_OF = "ruri_medium_twin_of"
#: On a lamp's own light data: the volume share it handed its copy.
HANDED_SHARE = "ruri_medium_share"
#: On the level sun's light data: the volume share the level stated for it, before the medium's.
STATED_SHARE = "ruri_medium_stated_share"
TWIN_RADIUS = 2.0 / math.sqrt(3.0)
#: What EEVEE's indirect volume lighting is clamped to: its SH energy limit, zero excluded.
NO_INDIRECT = 1e-9
TILE_SIZES = ("1", "2", "4", "8", "16")
#: The domain grid's voxels per axis and the density that keeps every one of them active.
GRID_RESOLUTION = 2
GRID_DENSITY = 1.0
#: The grid the domain emits and the medium's material reads as its extent.
GRID_NAME = "density"


def apply(context, medium, sun):
    """Stand ``medium`` (see :meth:`Kernel.host.SceneGraph.apply_medium`) up in the scene,
    lit by ``sun`` (the level sun object), replacing whatever medium a previous call stood up, and keep its
    statement on the level's world. ``medium`` None is a phase that integrates no fog: whatever stood up
    comes out and the world states none. Returns the lines to report."""
    scene = context.scene
    withdraw(scene)
    world = material_builder.level_world(scene)
    if medium is None:
        if STATEMENT_PROPERTY in world:
            del world[STATEMENT_PROPERTY]
        return []
    world[STATEMENT_PROPERTY] = json.dumps(medium)
    return _stand_up(scene, medium, sun)


def restore(scene, sun):
    """Stand the medium the scene's world states up again. After a file opens none of it is in memory (the volume
    and the lamps' copies are runtime data); the world's statement is what is left. A world that states none
    leaves the scene as it is. Returns the lines to report."""
    world = scene.world
    stated = world.get(STATEMENT_PROPERTY) if world is not None else None
    if stated is None:
        return []
    withdraw(scene)
    return _stand_up(scene, json.loads(stated), sun)


def _stand_up(scene, medium, sun):
    if sun is None:
        raise RuntimeError("[medium] the medium scatters the level's main light, and no level sun is stood up")
    unbuildable = _unbuildable(sun, medium)
    if unbuildable is not None:
        return ["medium {0} not stood up: {1}".format(medium["label"], unbuildable)]
    tint = _sun_share(sun, medium)
    domain = _domain(scene, medium, tint)
    receivers = plugin_data.born(bpy.data.collections.new(RECEIVERS))
    receivers.objects.link(domain)
    copies = _lamp_copies(scene, medium, receivers, tint)
    _integration(scene, medium)
    return ["medium {0}: {1:g}-{2:g} m, {3} lamp(s) in it".format(
        medium["label"], medium["range"][0], medium["range"][1], copies)]


def withdraw(scene):
    """Take a stood-up medium back out: its volume, the lamps' copies, every lamp's volume
    share back where the statement put it and the level sun's back to what the level stated."""
    for obj in list(bpy.data.objects):
        if obj.name == MEDIUM or TWIN_OF in obj:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Light):
                    bpy.data.lights.remove(data)
                elif isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
    for light in bpy.data.lights:
        for key in (HANDED_SHARE, STATED_SHARE):
            if key in light:
                light.volume_factor = float(light[key])
                del light[key]
    receivers = bpy.data.collections.get(RECEIVERS)
    if receivers is not None:
        bpy.data.collections.remove(receivers)
    tree = bpy.data.node_groups.get(MEDIUM)
    if tree is not None and tree.users == 0:
        bpy.data.node_groups.remove(tree)
    material = bpy.data.materials.get(MEDIUM)
    if material is not None and material.users == 0:
        bpy.data.materials.remove(material)
    _ = scene


def _domain(scene, medium, tint):
    """A box around everything in the scene, grown by the medium's range: wherever the view
    stands among the scene, all of the range it integrates is inside."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    corners = [obj.matrix_world @ Vector(corner)
               for obj in (candidate.evaluated_get(depsgraph) for candidate in scene.objects
                           if candidate.type == "MESH")
               for corner in obj.bound_box]
    reach = float(medium["range"][1])
    if corners:
        low = Vector([min(c[i] for c in corners) - reach for i in range(3)])
        high = Vector([max(c[i] for c in corners) + reach for i in range(3)])
    else:
        low, high = Vector((-reach,) * 3), Vector((reach,) * 3)
    domain = plugin_data.born(bpy.data.objects.new(MEDIUM, plugin_data.born(bpy.data.meshes.new(MEDIUM))))
    modifier = domain.modifiers.new(MEDIUM, "NODES")
    modifier.node_group = _grid_tree(low, high, _material(medium, tint))
    domain.display_type = "BOUNDS"
    domain.hide_select = True
    scene.collection.objects.link(domain)
    return domain


def _grid_tree(low, high, material):
    """The node tree that makes the domain volume geometry: one grid from ``low`` to ``high`` at the
    coarsest resolution, every voxel active (a density off the background) and wearing ``material``."""
    tree = bpy.data.node_groups.get(MEDIUM)
    if tree is not None:
        bpy.data.node_groups.remove(tree)
    tree = plugin_data.born(bpy.data.node_groups.new(MEDIUM, "GeometryNodeTree"))
    tree.is_modifier = True
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    cube = tree.nodes.new("GeometryNodeVolumeCube")
    cube.inputs["Density"].default_value = GRID_DENSITY
    cube.inputs["Background"].default_value = 0.0
    cube.inputs["Min"].default_value = tuple(low)
    cube.inputs["Max"].default_value = tuple(high)
    for axis in "XYZ":
        cube.inputs["Resolution " + axis].default_value = GRID_RESOLUTION
    worn = tree.nodes.new("GeometryNodeSetMaterial")
    worn.inputs["Material"].default_value = material
    output = tree.nodes.new("NodeGroupOutput")
    tree.links.new(cube.outputs["Volume"], worn.inputs["Geometry"])
    tree.links.new(worn.outputs["Geometry"], output.inputs["Geometry"])
    return tree


def _material(medium, tint):
    material = bpy.data.materials.get(MEDIUM) or plugin_data.born(bpy.data.materials.new(MEDIUM))
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    graph = _Graph(tree)
    basis = material_builder.world_basis()
    column = [tuple(basis[row][axis] for row in range(3)) for axis in range(3)]

    geometry = graph.node("ShaderNodeNewGeometry")
    camera = graph.node("ShaderNodeCameraData")
    height = graph.vector("DOT_PRODUCT", geometry.outputs["Position"], column[1])
    extinction = 0.0
    for layer_height, layer_density, layer_falloff in medium["layers"]:
        exponent = graph.math("MINIMUM", graph.math(
            "MULTIPLY", graph.math("SUBTRACT", float(layer_height), height), float(layer_falloff)), 127.0)
        extinction = graph.math("ADD", extinction, graph.math(
            "MULTIPLY", graph.math("POWER", 2.0, exponent), float(layer_density)))
    extinction = graph.math("MAXIMUM", graph.math("MULTIPLY", extinction, float(medium["density_scale"])), 0.0)

    start = float(medium["range"][0])
    distance = camera.outputs["View Distance"]
    travelled = graph.math("MULTIPLY", distance, graph.math(
        "SUBTRACT", 1.0, graph.math("DIVIDE", start, camera.outputs["View Z Depth"])))
    fade = graph.math("MULTIPLY", travelled, float(medium["near_fade"]), clamp=True)
    extent = graph.node("ShaderNodeAttribute")
    extent.attribute_type = "GEOMETRY"
    extent.attribute_name = GRID_NAME
    fade = graph.math("MULTIPLY", fade, graph.math("GREATER_THAN", extent.outputs["Fac"], GRID_DENSITY * 0.5))
    extinction = graph.math("MULTIPLY", extinction, fade)

    albedo = tuple(float(component) for component in medium["albedo"])
    anisotropy = float(medium["anisotropy"])
    tinted = tuple(component * share for component, share in zip(albedo, tint))
    scatter = graph.node("ShaderNodeVolumeScatter")
    scatter.inputs["Color"].default_value = tinted + (1.0,)
    scatter.inputs["Anisotropy"].default_value = anisotropy
    graph.link(extinction, scatter.inputs["Density"])
    absorption = graph.node("ShaderNodeVolumeAbsorption")
    absorption.inputs["Color"].default_value = tinted + (1.0,)
    graph.link(extinction, absorption.inputs["Density"])

    incoming = geometry.outputs["Incoming"]
    channels = []
    for channel, vector in enumerate(medium["ambient"]):
        toward = tuple(sum(basis[row][axis] * float(vector[axis]) for axis in range(3)) for row in range(3))
        along = graph.math("MULTIPLY", graph.vector("DOT_PRODUCT", incoming, toward), -anisotropy)
        radiance = graph.math("MAXIMUM", graph.math(
            "MULTIPLY", graph.math("ADD", along, float(vector[3])), float(medium["ambient_scale"])), 0.0)
        channels.append(graph.math("MULTIPLY", graph.math("MULTIPLY", radiance, albedo[channel]), extinction))
    emitted = graph.node("ShaderNodeCombineXYZ")
    for axis, value in zip("XYZ", channels):
        graph.link(value, emitted.inputs[axis])
    stated = graph.vector("SCALE", tuple(float(component) for component in medium["emission"]), fade)
    emission = graph.node("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1.0
    graph.link(graph.vector("ADD", emitted.outputs["Vector"], stated), emission.inputs["Color"])

    joined = graph.node("ShaderNodeAddShader")
    tree.links.new(scatter.outputs[0], joined.inputs[0])
    tree.links.new(absorption.outputs[0], joined.inputs[1])
    total = graph.node("ShaderNodeAddShader")
    tree.links.new(joined.outputs[0], total.inputs[0])
    tree.links.new(emission.outputs[0], total.inputs[1])
    output = graph.node("ShaderNodeOutputMaterial")
    tree.links.new(total.outputs[0], output.inputs["Volume"])
    return material


def _unbuildable(sun, medium):
    """Why the level sun cannot carry this medium's main light, or None when it can (see the module
    docstring): a medium stating no main light, or scattering it isotropically, asks nothing of the
    sun's direction; one that states light has to find every channel of it in the sun."""
    stated = medium["light"]
    if float(stated["intensity"]) * float(stated["scale"]) == 0.0:
        return None
    data = sun.data
    if data.energy <= 0.0 or min(data.color) <= 0.0:
        return "the level sun emits nothing in some channel of the light the medium scatters"
    if float(medium["anisotropy"]) == 0.0:
        return None
    basis = material_builder.world_basis()
    toward = Vector(tuple(sum(basis[row][axis] * float(stated["direction"][axis]) for axis in range(3))
                          for row in range(3))).normalized()
    facing = (sun.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    if toward.dot(facing) < 1.0 - 1e-6:
        return ("it scatters anisotropically (g = {0:g}) along the atmosphere's light {1}, and the level sun "
                "points along {2}".format(float(medium["anisotropy"]),
                                          tuple(round(component, 5) for component in toward),
                                          tuple(round(component, 5) for component in facing)))
    return None


def _sun_share(sun, medium):
    """Give the level sun its share of the medium and return the tint the medium's scattering
    carries per channel (see the module docstring); :func:`_unbuildable` has already said the sun
    can carry it. A medium that states no main light gives the sun no share and has no colour to
    fold."""
    stated = medium["light"]
    data = sun.data
    data[STATED_SHARE] = data.volume_factor
    if float(stated["intensity"]) * float(stated["scale"]) == 0.0:
        data.volume_factor = 0.0
        return (1.0, 1.0, 1.0)
    ratio = [float(want) / float(have) for want, have in zip(stated["color"], data.color)]
    peak = max(ratio)
    data.volume_factor = float(stated["intensity"]) * float(stated["scale"]) * peak / data.energy
    return tuple(component / peak for component in ratio)


def _lamp_copies(scene, medium, receivers, tint):
    shadowed = bool(medium["punctual_shadows"])
    copies = 0
    for obj in list(scene.objects):
        if obj.type != "LIGHT" or obj.data.type == "SUN" or TWIN_OF in obj or obj.data.volume_factor <= 0.0:
            continue
        share = obj.data.volume_factor
        data = plugin_data.born(obj.data.copy())
        data.name = obj.data.name + TWIN_SUFFIX
        data.volume_factor = share
        data.diffuse_factor = 0.0
        data.specular_factor = 0.0
        data.transmission_factor = 0.0
        data.use_shadow = shadowed and obj.data.use_shadow
        data.shadow_soft_size = TWIN_RADIUS
        untinted = [component / share_of for component, share_of in zip(obj.data.color, tint)]
        brightest = max(max(untinted), 1.0)
        data.color = tuple(component / brightest for component in untinted)
        data.energy = obj.data.energy * brightest
        copy = plugin_data.born(bpy.data.objects.new(obj.name + TWIN_SUFFIX, data))
        copy.matrix_world = obj.matrix_world.copy()
        copy[TWIN_OF] = obj.name
        copy.light_linking.receiver_collection = receivers
        scene.collection.objects.link(copy)
        obj.data[HANDED_SHARE] = share
        obj.data.volume_factor = 0.0
        copies += 1
    return copies


def _integration(scene, medium):
    start, end = (float(value) for value in medium["range"])
    slices, tile, distribution = medium["grid"]
    eevee = scene.eevee
    eevee.use_volume_custom_range = True
    eevee.volumetric_start = max(start, 1e-6)
    eevee.volumetric_end = end
    if str(int(tile)) not in TILE_SIZES:
        raise ValueError("[medium] the source's froxel tile of {0} px is not one EEVEE integrates at {1}"
                         .format(tile, TILE_SIZES))
    eevee.volumetric_tile_size = str(int(tile))
    eevee.volumetric_samples = int(slices)
    eevee.volumetric_sample_distribution = min(max(1.0 - float(distribution) / (4.0 * (int(slices) - 1)), 0.0), 1.0)
    eevee.use_volumetric_shadows = False
    eevee.clamp_volume_indirect = NO_INDIRECT


class _Graph:
    """Node wiring for one tree: a value is a constant or an output socket."""

    def __init__(self, tree):
        self.tree = tree

    def node(self, kind):
        return self.tree.nodes.new(kind)

    def link(self, value, socket):
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, socket)
        else:
            socket.default_value = value

    def math(self, operation, first, second, clamp=False):
        node = self.node("ShaderNodeMath")
        node.operation = operation
        node.use_clamp = clamp
        self.link(first, node.inputs[0])
        self.link(second, node.inputs[1])
        return node.outputs[0]

    def vector(self, operation, first, second):
        node = self.node("ShaderNodeVectorMath")
        node.operation = operation
        self.link(first, node.inputs[0])
        if operation == "SCALE":
            self.link(second, node.inputs["Scale"])
        else:
            self.link(second, node.inputs[1])
        return node.outputs["Value" if operation == "DOT_PRODUCT" else "Vector"]
