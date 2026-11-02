"""Which objects throw which shadows, as their renderers state it.

A renderer states how it draws into shadow maps (Unity's ShadowCastingMode), and a
streamed renderer also whether that shadow falls in the directional light's
cascades. Shadows-only is object visibility. The cascades have no per-object
switch here: an object blocks a light or it does not, per light, and that is
light linking. The objects that cast for local lights only are gathered in one
collection, each excluded, and that collection is the blocker set of every
directional light -- the shading stacks light each of them as their main light --
and an exclude-only set leaves every other object blocking it.
"""

from __future__ import annotations

import bpy
import numpy

MAIN_LIGHT_EXCLUSIONS = "Ruri Main Light Shadow Exclusions"
#: A column of the object matrix summing below 1e-10 is what the draw manager calls flat; this leaves room for a scale of 500.
_FLAT_SCALE = 1e-13
#: On a shadow-only child that casts for an object the camera sees (see :func:`flatten_alpha_casters`).
FLAT_CASTER_PROPERTY = "ruri_flat_caster"


def shadow_only(obj):
    """Seen by shadow rays and nothing else: no camera, reflection, refraction,
    medium or light probe."""
    obj.visible_camera = False
    obj.visible_diffuse = False
    obj.visible_glossy = False
    obj.visible_transmission = False
    obj.visible_volume_scatter = False
    obj.hide_probe_volume = True
    obj.hide_probe_sphere = True
    obj.hide_probe_plane = True


def exclude_from_main_light(obj):
    """Keep ``obj`` from blocking the directional lights while it blocks every other one."""
    collection = bpy.data.collections.get(MAIN_LIGHT_EXCLUSIONS)
    if collection is None:
        collection = bpy.data.collections.new(MAIN_LIGHT_EXCLUSIONS)
    if collection.objects.get(obj.name) is obj:
        return
    collection.objects.link(obj)
    # A link appends: the entry just made is the last one, and it starts included --
    # an include in a blocker set would make it one of the ONLY blockers.
    collection.collection_objects[len(collection.collection_objects) - 1].light_linking.link_state = "EXCLUDE"


def bind_directional_lights(scene):
    """Make the exclusion set the blocker set of every directional light in ``scene`` that has none
    yet; a light the user gave a blocker set of their own keeps it. Returns how many were bound."""
    collection = bpy.data.collections.get(MAIN_LIGHT_EXCLUSIONS)
    if collection is None:
        return 0
    bound = 0
    for obj in scene.objects:
        if obj.type != "LIGHT" or obj.data.type != "SUN" or obj.library is not None:
            continue
        if obj.light_linking.blocker_collection is None:
            obj.light_linking.blocker_collection = collection
            bound += 1
    return bound


def flatten_alpha_casters(objects):
    """A level's casters with a clipped shadow, ready for final renders. In a final render EEVEE redraws, every sample,
    each shadow page a caster with a transparent shadow touches (eevee_shadow.cc:769,971), because its transparency may
    be stochastic. A clip is not: every sample draws the same shadow. EEVEE skips that redraw for a caster whose bounding
    sphere is invalid (eevee_shadow_tag_update.bsl.hh:53), and a matrix column below 1e-10 makes it so while the bounds'
    corners stay valid for the light's depth range (draw_resource_finalize.bsl.hh:76-81, eevee_shadow_tilemap_bounds.bsl.hh:140).
    So such a caster flattens along its local Z over a mesh stretched back by the same factor: same world positions, same
    shadow, no redraw. One the camera also sees hands its shadow to a shadow-only child, whose stretched normals nothing
    reads. A flat caster never refreshes a page when it moves in the viewport, so this is for a level that stays put; a
    deformed caster keeps its own shadow. Returns how many objects now cast flat."""
    exclusions = bpy.data.collections.get(MAIN_LIGHT_EXCLUSIONS)
    excluded = set(exclusions.objects) if exclusions is not None else ()
    stretched = {}
    flattened = 0

    def stretched_mesh(mesh):
        made = stretched.get(mesh)
        if made is None:
            made = stretched[mesh] = mesh.copy()
            positions = numpy.empty(len(made.vertices) * 3, dtype=numpy.float32)
            made.vertices.foreach_get("co", positions)
            positions[2::3] /= _FLAT_SCALE
            made.vertices.foreach_set("co", positions)
            made.update()
        return made

    for obj in objects:
        if (obj.type != "MESH" or not obj.visible_shadow or obj.hide_render or len(obj.modifiers) or not len(obj.data.polygons)
                or not any(slot.material is not None and slot.material.use_transparent_shadow
                           and slot.material.get("ruri_uber_stack") for slot in obj.material_slots)):
            continue
        flattened += 1
        if not obj.visible_camera:
            obj.data = stretched_mesh(obj.data)
            obj.scale[2] *= _FLAT_SCALE
            continue
        twin = bpy.data.objects.new(obj.name + " Flat Caster", stretched_mesh(obj.data))
        for collection in obj.users_collection:
            if collection != exclusions:
                collection.objects.link(twin)
        for key in obj.keys():
            twin[key] = obj[key]
        twin[FLAT_CASTER_PROPERTY] = 1.0
        for index, slot in enumerate(obj.material_slots):
            if slot.link == "OBJECT":
                twin.material_slots[index].link = "OBJECT"
                twin.material_slots[index].material = slot.material
        twin.parent = obj
        twin.scale = (1.0, 1.0, _FLAT_SCALE)
        shadow_only(twin)
        if obj in excluded:
            exclude_from_main_light(twin)
        obj.visible_shadow = False
    return flattened


def drop_orphan_casters():
    """A flat caster whose object was deleted would go on throwing its shadow from the world origin. Returns how many went."""
    orphans = [obj for obj in bpy.data.objects
               if obj.library is None and obj.parent is None and FLAT_CASTER_PROPERTY in obj]
    bpy.data.batch_remove(orphans)
    return len(orphans)
