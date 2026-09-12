"""Unity's own built-in primitive meshes, rebuilt to spec.

A scene that stands a Cube in a doorway or a Quad in front of a video player
references ``unity default resources`` -- the engine's own asset file, guid
``0000000000000000e000000000000000``. That file is not part of any game's data,
so an extraction of the game does not contain it and never will: AssetRipper
skips engine built-ins BY DESIGN, because a real Unity project already has them.
Loosening any path or name match is therefore permanently useless -- there is
nothing on the other side to match against.

What a host CAN do is what Unity does: build the primitive from its own
definition. These are the engine's fixed shapes, addressed by the fileID Unity
gives each one inside that resources file.

Two tiers, and the difference is stated rather than blurred:

* ``exact=True`` -- Cube, Quad and Plane are fully determined shapes (unit box,
  unit quad, 10x10 grid of 200 triangles). What is built here IS the engine's
  mesh, vertex for vertex.
* ``exact=False`` -- Sphere, Cylinder and Capsule are the engine's DIMENSIONS
  with a tessellation of this module's choosing. Unity's own triangulations of
  them (515 / 88 / 554 vertices) are internal to the editor and are not derivable
  from anything shipped in a game build. A host that cares should say these were
  reconstructed; silently presenting them as the engine's mesh would be a lie
  about the one thing this module cannot know.

Geometry comes out in UNITY coordinates, like every other decode path here, and
the host converts. numpy only, so it tests outside any host.
"""

from __future__ import annotations

import numpy as np

from .mesh_decoder import DecodedMesh, SubMesh

# The engine's own resources file. Every reference to a built-in mesh carries it.
BUILTIN_RESOURCES_GUID = "0000000000000000e000000000000000"

# fileID -> the primitive Unity keeps at it, inside `unity default resources`.
PRIMITIVES = {
    10202: "Cube",
    10206: "Cylinder",
    10207: "Sphere",
    10208: "Capsule",
    10209: "Plane",
    10210: "Quad",
}

# Which of them this module reproduces exactly (see the module docstring).
EXACT = frozenset(("Cube", "Quad", "Plane"))


def is_builtin(ref):
    """Does this ``{fileID, guid}`` name a built-in primitive -> its name, else None."""
    if not isinstance(ref, dict):
        return None
    guid = str(ref.get("guid") or "").lower()
    if guid != BUILTIN_RESOURCES_GUID:
        return None
    try:
        file_id = int(ref.get("fileID"))
    except (TypeError, ValueError):
        return None
    return PRIMITIVES.get(file_id)


def build(name):
    """The named primitive as a DecodedMesh, or None for one not built here."""
    builder = _BUILDERS.get(name)
    if builder is None:
        return None
    positions, normals, uvs, triangles = builder()
    mesh = DecodedMesh(name)
    mesh.positions = np.asarray(positions, dtype=np.float32)
    mesh.normals = np.asarray(normals, dtype=np.float32)
    mesh.uvs = {0: np.asarray(uvs, dtype=np.float32)}
    mesh.triangles = np.asarray(triangles, dtype=np.int32).reshape(-1, 3)
    mesh.tri_material = np.zeros(len(mesh.triangles), dtype=np.int32)
    mesh.vertex_count = len(mesh.positions)
    mesh.submeshes = [SubMesh({"firstByte": 0, "indexCount": mesh.triangles.size,
                               "topology": 0, "baseVertex": 0, "firstVertex": 0,
                               "vertexCount": mesh.vertex_count}, 4)]
    return mesh


def _quad():
    """1x1 in the XY plane, facing -Z, UV filling 0-1. Unity's Quad exactly."""
    positions = [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)]
    normals = [(0.0, 0.0, -1.0)] * 4
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return positions, normals, uvs, [(0, 1, 2), (0, 2, 3)]


def _cube():
    """1x1x1 about the origin, six faces of four vertices so every face carries
    its own normal and its own full 0-1 UV -- Unity's Cube exactly (24 vertices,
    12 triangles), not an 8-vertex box."""
    faces = (
        ((0.0, 0.0, -1.0), ((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5))),
        ((0.0, 0.0, 1.0), ((0.5, -0.5, 0.5), (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5))),
        ((-1.0, 0.0, 0.0), ((-0.5, -0.5, 0.5), (-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (-0.5, 0.5, 0.5))),
        ((1.0, 0.0, 0.0), ((0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, -0.5))),
        ((0.0, 1.0, 0.0), ((-0.5, 0.5, -0.5), (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5))),
        ((0.0, -1.0, 0.0), ((-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, -0.5, -0.5), (-0.5, -0.5, -0.5))),
    )
    positions, normals, uvs, triangles = [], [], [], []
    for normal, corners in faces:
        base = len(positions)
        positions.extend(corners)
        normals.extend([normal] * 4)
        uvs.extend(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
        triangles.append((base, base + 1, base + 2))
        triangles.append((base, base + 2, base + 3))
    return positions, normals, uvs, triangles


def _plane():
    """10x10 in the XZ plane, facing +Y, as an 11x11 grid -- Unity's Plane
    exactly (121 vertices, 200 triangles). Its UV runs 0-1 across the whole
    thing."""
    steps = 10
    positions, normals, uvs, triangles = [], [], [], []
    for row in range(steps + 1):
        for column in range(steps + 1):
            u, v = column / steps, row / steps
            positions.append((u * 10.0 - 5.0, 0.0, v * 10.0 - 5.0))
            normals.append((0.0, 1.0, 0.0))
            uvs.append((u, v))
    for row in range(steps):
        for column in range(steps):
            here = row * (steps + 1) + column
            triangles.append((here, here + steps + 1, here + steps + 2))
            triangles.append((here, here + steps + 2, here + 1))
    return positions, normals, uvs, triangles


def _sphere(segments=24, rings=16):
    """Diameter 1 about the origin -- the engine's size, this module's
    tessellation (see the module docstring on why the engine's own is not
    recoverable)."""
    positions, normals, uvs, triangles = [], [], [], []
    for ring in range(rings + 1):
        v = ring / rings
        polar = v * np.pi
        y, radius = np.cos(polar), np.sin(polar)
        for segment in range(segments + 1):
            u = segment / segments
            azimuth = u * 2.0 * np.pi
            direction = (float(np.sin(azimuth) * radius), float(y), float(np.cos(azimuth) * radius))
            positions.append((direction[0] * 0.5, direction[1] * 0.5, direction[2] * 0.5))
            normals.append(direction)
            uvs.append((u, 1.0 - v))
    for ring in range(rings):
        for segment in range(segments):
            here = ring * (segments + 1) + segment
            below = here + segments + 1
            triangles.append((here, below, below + 1))
            triangles.append((here, below + 1, here + 1))
    return positions, normals, uvs, triangles


def _cylinder(segments=24):
    """Diameter 1, height 2 about the origin -- the engine's size, this module's
    tessellation. Caps get their own ring of vertices so their normals are flat."""
    positions, normals, uvs, triangles = [], [], [], []
    half = 1.0
    for segment in range(segments + 1):
        u = segment / segments
        azimuth = u * 2.0 * np.pi
        x, z = float(np.sin(azimuth) * 0.5), float(np.cos(azimuth) * 0.5)
        direction = (float(np.sin(azimuth)), 0.0, float(np.cos(azimuth)))
        positions.append((x, half, z))
        normals.append(direction)
        uvs.append((u, 1.0))
        positions.append((x, -half, z))
        normals.append(direction)
        uvs.append((u, 0.0))
    for segment in range(segments):
        top, bottom = segment * 2, segment * 2 + 1
        triangles.append((top, bottom, bottom + 2))
        triangles.append((top, bottom + 2, top + 2))
    for sign, normal in ((half, (0.0, 1.0, 0.0)), (-half, (0.0, -1.0, 0.0))):
        centre = len(positions)
        positions.append((0.0, sign, 0.0))
        normals.append(normal)
        uvs.append((0.5, 0.5))
        for segment in range(segments + 1):
            azimuth = segment / segments * 2.0 * np.pi
            x, z = float(np.sin(azimuth) * 0.5), float(np.cos(azimuth) * 0.5)
            positions.append((x, sign, z))
            normals.append(normal)
            uvs.append((x + 0.5, z + 0.5))
        for segment in range(segments):
            rim = centre + 1 + segment
            triangles.append((centre, rim, rim + 1) if sign > 0 else (centre, rim + 1, rim))
    return positions, normals, uvs, triangles


def _capsule(segments=24, rings=16):
    """Diameter 1, total height 2 about the origin -- a sphere cut at its equator
    with a cylinder of height 1 between the halves. The engine's size, this
    module's tessellation."""
    positions, normals, uvs, triangles = [], [], [], []
    offset = 0.5   # half the cylindrical section
    for ring in range(rings + 1):
        v = ring / rings
        polar = v * np.pi
        y, radius = float(np.cos(polar)), float(np.sin(polar))
        shift = offset if y >= 0.0 else -offset
        for segment in range(segments + 1):
            u = segment / segments
            azimuth = u * 2.0 * np.pi
            direction = (float(np.sin(azimuth) * radius), y, float(np.cos(azimuth) * radius))
            positions.append((direction[0] * 0.5, direction[1] * 0.5 + shift, direction[2] * 0.5))
            normals.append(direction)
            uvs.append((u, 1.0 - v))
    for ring in range(rings):
        for segment in range(segments):
            here = ring * (segments + 1) + segment
            below = here + segments + 1
            triangles.append((here, below, below + 1))
            triangles.append((here, below + 1, here + 1))
    return positions, normals, uvs, triangles


_BUILDERS = {
    "Quad": _quad,
    "Cube": _cube,
    "Plane": _plane,
    "Sphere": _sphere,
    "Cylinder": _cylinder,
    "Capsule": _capsule,
}
