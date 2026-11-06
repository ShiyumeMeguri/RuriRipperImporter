"""The sky a level's camera sees, on the world that lights the level, for camera rays only.

The source draws its sky as a dome around the camera with its procedural sky material, or as the cube box, and
then its cloud pass, as its sky renderer decides per phase; nothing it lights reads the sky. The stack that ports
those materials states its draw layers, the row of the level's sky statement that decides each entry, and builds
the camera branch on the world. This module draws the entries those rows decide and states, on the groups the
stack builds, every value the source's sky renderer prepared at the viewer: a four-component value reaches a
group as its vector input plus the float input named after it with ``_w``.
"""

from __future__ import annotations

from . import material_builder


def _stage():
    stages = list(material_builder.SKY_STAGES)
    if len(stages) != 1:
        raise RuntimeError("[sky] {0} shading stacks draw a sky; a level draws one".format(len(stages)))
    return stages[0]


def _chosen(stage, rows):
    """Per draw layer, the entry the level's rows decide the source draws, or None."""
    chosen = []
    for index, layer in enumerate(stage.sky_layers()):
        drawn = []
        for suffix in layer["entries"]:
            row = stage.sky_entry(suffix)["draw_row"]
            if row not in rows:
                raise RuntimeError("[sky] the level states no {0}, which decides whether {1} is drawn".format(row, suffix))
            if rows[row][0] == 1.0:
                drawn.append(suffix)
        if len(drawn) > 1:
            raise RuntimeError("[sky] layer {0} would draw {1}; a layer draws one".format(index, drawn))
        chosen.append(drawn[0] if drawn else None)
    return chosen


def _state(node, rows, read):
    for socket in node.inputs:
        name = socket.name
        if name in rows:
            value = rows[name]
            socket.default_value = value[:3] if socket.type == "VECTOR" else value[0]
        elif name.endswith("_w") and name[:-2] in rows:
            name = name[:-2]
            socket.default_value = rows[name][3]
        else:
            continue
        read.add(name)


def apply(scene, rows):
    """Stand the sky the level's rows ({name: (x, y, z, w)}) describe up on ``scene``'s world for camera rays.
    Returns the lines to report."""
    world = scene.world
    if world is None:
        raise RuntimeError("[sky] the scene has no world to draw its sky on")
    stage = _stage()
    chosen = _chosen(stage, rows)
    nodes = stage.build_sky_world(world, chosen)
    read = {stage.sky_entry(suffix)["draw_row"] for layer in stage.sky_layers() for suffix in layer["entries"]}
    for node in nodes.values():
        _state(node, rows, read)
    unread = sorted(set(rows) - read)
    return ["sky: {0} drawn; {1} value(s) no drawn entry reads{2}".format(
        ", ".join(suffix for suffix in chosen if suffix) or "nothing", len(unread),
        (": " + ", ".join(unread[:8])) if unread else "")]
