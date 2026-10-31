"""Carrying performances from one skeleton to another with the RuriActionRetarget add-on's tables and maths."""

from __future__ import annotations

import importlib

import bpy

from . import animation_builder, rig_identity


def discard(rig):
    bpy.data.batch_remove([rig, rig.data])


def carry(context, source_rig, rig, source_name, performances, activate):
    """Returns (landed, lines) for the performances keyed on ``source_rig`` (``{clip key: (action,
    slot)}``), carried onto ``rig`` through the table joining ``source_name`` to its skeleton family."""
    compose = importlib.import_module("RuriActionRetarget.compose")
    api = importlib.import_module("RuriActionRetarget.api")
    edges = compose.load_edges()
    family = rig_identity.skeleton_of(rig)
    route = compose.route_for(source_name, family, edges, dest_bones=[bone.name for bone in rig.data.bones])
    if route is None or not route.hops:
        raise LookupError("No bone table joins '{0}' to '{1}'.".format(source_name, family))
    spec = compose.compose(route, edges)
    keys = {action: key for key, (action, _slot) in performances.items()}
    results, errors = api.retarget_actions(source_rig, rig, spec["mappings"], spec.get("settings"), list(keys))
    landed = {}
    lines = ["{0}: {1}".format(name, message) for name, message in errors]
    for action, product, info in results:
        landed[keys[action]] = (product, product.slots[0] if len(product.slots) else None)
        lines.append("{0}: {1} frame(s) on {2} bone(s) through {3}".format(
            product.name, info["frames"], info["bones"], route.via))
        lines.extend(info["warnings"])
    for action in keys:
        bpy.data.actions.remove(action)
    if activate and landed:
        animation_builder.adopt_action(rig, *next(iter(landed.values())), scene=context.scene)
    return landed, lines
