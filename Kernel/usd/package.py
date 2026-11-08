"""The cut of a statement into layers, and the order they are written in.

A scene is cut three ways. What a rig covers is one character, written once however many times the
scene has it. What draws a mesh outside any rig is a static piece, one per distinct pair of mesh and
materials. What is left -- the hierarchy -- is the scene layer, which refers to all of them. Two
formats are two complete trees; they share the textures beside the layers.
"""

from __future__ import annotations

import copy
import time

import numpy as np

from .. import statement as kernel_statement
from . import animations, characters, rigs, scene, statics
from .hierarchy import Hierarchy
from .layout import Layout
from .materials import TextureStore
from .names import Namer, file_stem, identifier
from .transforms import move_normals, move_points


class _Character:
    """A written character: where its layer is and the prim in it a scene refers to."""

    __slots__ = ("layer_path", "root", "fingerprint", "rig", "parts")

    def __init__(self, layer_path, root, fingerprint, rig, parts):
        self.layer_path = layer_path
        self.root = root
        self.fingerprint = fingerprint
        self.rig = rig
        self.parts = parts


def _carried(mesh, matrix):
    """The mesh with its points and normals carried through ``matrix`` -- a skinned mesh no rig owns, written as
    the static piece of the pose the scene shows."""
    moved = copy.copy(mesh)
    moved.positions = move_points(matrix, mesh.positions)
    moved.normals = None if mesh.normals is None else move_normals(matrix, mesh.normals)
    return moved


def export(statement, scene_name, root, product, families, extension, textures=None, performances=None):
    """Write the statement as the tree of layers of ``extension`` under ``root``/assets/``product``. The
    statement must have been read in the usd basis. ``performances``, when given, is asked for the clips each new
    character plays -- ``performances(rig, root_node) -> [clip]`` -- and each is written as a layer of the character's.
    Returns a dict of what was written."""
    if statement.basis != kernel_statement.USD:
        raise ValueError("a statement is written to USD in the {0} basis, and this one is in {1}.".format(
            kernel_statement.USD, statement.basis))
    started = time.time()
    layout = Layout(root, product, extension)
    folders = Namer()
    textures = textures if textures is not None else TextureStore(statement)
    hierarchy = Hierarchy(statement.nodes)
    rig_roots, covered = rigs.find(hierarchy, statement.meshes)
    report = {"extension": extension, "nodes": len(hierarchy.nodes), "depth": hierarchy.maximum_depth(),
              "roots": len(hierarchy.roots), "rigs": len(rig_roots), "rig_missing_meshes": 0,
              "rig_bare_vertices": 0, "mesh_missing": 0, "unrigged_skinned": 0}

    stated = statement.colliders
    written = []
    offered = {}
    placed_keys = set()
    placed = {}
    on_joints = set()
    report["colliders_on_joints"] = 0
    report["colliders_on_joints_unwritten"] = 0
    for index in rig_roots:
        rig = rigs.Rig(hierarchy, index, Namer())
        parts, notes = characters.parts_of(statement, hierarchy, rig)
        report["rig_missing_meshes"] += notes["missing"]
        report["rig_bare_vertices"] += notes["bare"]
        carried_volumes = [volume for volume in stated if volume.node in rig.joint_of]
        volumes = [volume for volume in carried_volumes if volume.kind != "mesh"]
        on_joints.update(volume.node for volume in carried_volumes)
        report["colliders_on_joints"] += len(volumes)
        report["colliders_on_joints_unwritten"] += len(carried_volumes) - len(volumes)
        print_key = characters.fingerprint(rig, parts, volumes)
        match = next((entry for entry in written if entry.fingerprint == print_key
                      and characters.same_points(rig, parts, entry.rig, entry.parts)), None)
        if match is None:
            name = folders.claim("characters", file_stem(hierarchy.nodes[index].name))
            layer_path = layout.character(name)
            match = _Character(layer_path, characters.author(layer_path, name + "_postmodel", rig, parts, hierarchy,
                                                             statement, textures, families, volumes),
                               print_key, rig, parts)
            written.append(match)
            for clip in (performances(rig, hierarchy.nodes[index]) if performances is not None else ()):
                made = animations.author(layout.animation(name, animations.layer_stem(clip)), identifier(clip.name), rig,
                                         hierarchy, clip)
                entry = dict(made, character=name, clip=clip.name, key=clip.key)
                offered.setdefault(clip.key, clip)
                if made["written"]:
                    placed_keys.add(clip.key)
                report.setdefault("animations" if made["written"] else "animation_misses", []).append(entry)
        placed[index] = match
    report["character_layers"] = len(written)
    for key, clip in offered.items():
        if key not in placed_keys:
            carried = animations.author_carrier(layout.clip(animations.layer_stem(clip)), identifier(clip.name), clip)
            report.setdefault("animation_carriers", []).append(dict(carried, clip=clip.name, key=key))

    pieces = {}
    by_signature = {}
    for node in hierarchy.nodes:
        if covered[node.index] or node.kind not in (kernel_statement.MESH, kernel_statement.SKINNED) or not node.mesh:
            continue
        mesh = statement.meshes.get(node.mesh)
        if mesh is None or not len(mesh.positions):
            report["mesh_missing"] += 1
            continue
        if node.kind == kernel_statement.SKINNED:
            report["unrigged_skinned"] += 1
            mesh = _carried(mesh, np.linalg.inv(hierarchy.worlds[node.index]))
        signature = (node.mesh, node.kind, tuple(node.materials))
        piece = by_signature.get(signature)
        if piece is None:
            name = folders.claim(("pieces", scene_name), file_stem(mesh.name or node.name))
            piece = statics.author(layout.static(scene_name, name), name, mesh, node.materials, statement,
                                   textures, families)
            by_signature[signature] = piece
        pieces[node.index] = piece
    report["piece_layers"] = len(by_signature)
    report["piece_instances"] = len(pieces)

    drawn = {}
    for signature, piece in by_signature.items():
        if signature[1] == kernel_statement.MESH:
            drawn.setdefault(signature[0], piece)
    collision = {}
    for collider in stated:
        if collider.kind != "mesh" or collider.mesh in drawn or collider.mesh in collision:
            continue
        mesh = statement.meshes.get(collider.mesh)
        if mesh is None or not len(mesh.positions):
            continue
        name = folders.claim(("pieces", scene_name), file_stem(mesh.name or collider.mesh) + "_collision")
        collision[collider.mesh] = statics.author(layout.static(scene_name, name), name, mesh, [], statement, textures,
                                                  families)
    report["collision_layers"] = len(collision)
    report["collider_rows"] = len(stated)
    placements = {}
    for collider in stated:
        if collider.node not in on_joints:
            placements.setdefault(collider.node, []).append(collider)
    report.update(scene.author(layout.scene(scene_name), scene_name, statement, hierarchy, set(rig_roots), covered,
                               placed, pieces, placements, {**collision, **drawn}))
    report["textures_written"] = textures.written
    report["textures_linked"] = textures.linked
    report["textures_copied"] = textures.copied
    report["texture_bytes"] = textures.total_bytes
    report["textures_without_pixels"] = len(textures.missing)
    report["seconds"] = round(time.time() - started, 1)
    return report
