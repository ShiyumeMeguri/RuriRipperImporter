"""The cut of a statement into layers, and the order they are written in.

A scene is cut three ways. What a rig covers is one character, written once however many times the
scene has it. What draws a mesh outside any rig is a static piece, one per distinct pair of mesh and
materials. What is left -- the hierarchy -- is the scene layer, which refers to all of them. Two
formats are two complete trees; they share the textures beside the layers.
"""

from __future__ import annotations

import copy
import os
import shutil
import time

import numpy as np

from .. import statement as kernel_statement
from . import animations, attachments, characters, rigs, scene, statics
from . import cinemachine as cinemachine_module
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


def _rest_of(rig, hierarchy):
    """What a clip's layer of ``rig`` depends on besides the clip: its joints, and the rest each joint falls back to where the clip
    has no curve of a kind for it. Two rigs with one answer get one layer of a clip, byte for byte."""
    return (tuple(rig.sources), tuple(rig.tokens),
            tuple((node.position, node.rotation, node.scale) for node in (hierarchy.nodes[index] for index in rig.members)))


def _link(first, layer):
    """``layer`` as a second name for the file ``first``, which is what it would be written as."""
    os.makedirs(os.path.dirname(layer), exist_ok=True)
    try:
        os.link(first, layer)
    except OSError:
        shutil.copyfile(first, layer)


def export(statement, scene_name, root, product, families, extension, textures=None, performances=None, cinemachine=None,
          taken_characters=(), named_characters=None, owners=None):
    """Write the statement as the tree of layers of ``extension`` under ``root``/assets/``product``. The
    statement must have been read in the usd basis. ``performances``, when given, is asked for the clips each new
    character plays -- ``performances(rig, root_node) -> [clip]`` -- and each is written as a layer of the character's; ``owners``,
    when given, is asked where each is played -- ``owners(rig, root_node, clip) -> [path in the rig of the object of an Animator
    that plays the clip]`` -- which is what a curve of an empty path is anchored to (``animations.author``).
    ``cinemachine``, when given, are the components of the camera package the scene carries (``Statement.cinemachine``, read
    with ``scripts``), each written under the node it sits on in the scene layer, or, for a node inside a rig, in the rig's
    layer (``attachments``). ``taken_characters`` are the names (before the ``_postmodel`` suffix) the tree under ``root`` and
    the live tree already hold for other characters, which no character of this scene takes. ``named_characters`` maps the
    source path of a rig root to the names its characters already have, in the order the scene refers its rigs of that path
    (several rigs of one scene can have one path: siblings of one name), so that writing the scene again gives the same
    characters the same names whatever else changed: the first character written for a name keeps it (the name must be in
    ``taken_characters`` too, so that no other character takes it), and a second character a name would go to -- a rig
    that shared the first one's layer before and no longer does -- is named as a new one. Returns a dict of what was written,
    with ``character_names``: for each character layer written, its name, the source path and node index of the rig root it
    was written for, and the name that rig had (``named_characters``, empty for a rig without one)."""
    if statement.basis != kernel_statement.USD:
        raise ValueError("a statement is written to USD in the {0} basis, and this one is in {1}.".format(
            kernel_statement.USD, statement.basis))
    started = time.time()
    layout = Layout(root, product, extension)
    folders = Namer()
    for taken in taken_characters:
        folders.claim("characters", taken)
    named = dict(named_characters or {})
    occurrences = {}
    assigned = set()
    textures = textures if textures is not None else TextureStore(statement)
    hierarchy = Hierarchy(statement.nodes)
    components = cinemachine_module.by_node(cinemachine or (), hierarchy)
    rig_roots, covered = rigs.find(hierarchy, statement.meshes)
    report = {"extension": extension, "nodes": len(hierarchy.nodes), "depth": hierarchy.maximum_depth(),
              "roots": len(hierarchy.roots), "rigs": len(rig_roots), "rig_missing_meshes": 0,
              "rig_bare_vertices": 0, "mesh_missing": 0, "unrigged_skinned": 0}

    stated = statement.colliders
    written = []
    offered = {}
    performed = {}
    placed_keys = set()
    placed = {}
    on_joints = set()
    on_rigs = set()
    report["colliders_on_joints"] = 0
    report["colliders_on_joints_unwritten"] = 0
    report["camera_joints"] = 0
    report["camera_joint_components"] = 0
    report["light_joints"] = 0
    for index in rig_roots:
        root_path = hierarchy.source_paths[index]
        nth = occurrences.get(root_path, 0)
        occurrences[root_path] = nth + 1
        kept = named.get(root_path, ())[nth] if nth < len(named.get(root_path, ())) else ""
        rig = rigs.Rig(hierarchy, index, Namer())
        parts, notes = characters.parts_of(statement, hierarchy, rig)
        report["rig_missing_meshes"] += notes["missing"]
        report["rig_bare_vertices"] += notes["bare"]
        carried_volumes = [volume for volume in stated if volume.node in rig.joint_of]
        volumes = [volume for volume in carried_volumes if volume.kind != "mesh"]
        on_joints.update(volume.node for volume in carried_volumes)
        report["colliders_on_joints"] += len(volumes)
        report["colliders_on_joints_unwritten"] += len(carried_volumes) - len(volumes)
        attached = attachments.find(rig, hierarchy, components)
        on_rigs.update(carried.node for carried in attached)
        for carried in attached:
            node = hierarchy.nodes[carried.node]
            report["camera_joints"] += bool(carried.components or node.camera is not None)
            report["camera_joint_components"] += len(carried.components)
            report["light_joints"] += node.light is not None
        print_key = characters.fingerprint(rig, parts, volumes, attached, hierarchy)
        match = next((entry for entry in written if entry.fingerprint == print_key
                      and characters.same_points(rig, parts, entry.rig, entry.parts)), None)
        if match is None:
            if kept and kept.lower() not in assigned:
                name = kept
                if not folders.holds("characters", name):
                    folders.claim("characters", name)
            else:
                name = folders.claim("characters", file_stem(hierarchy.nodes[index].name))
            assigned.add(name.lower())
            report.setdefault("character_names", []).append({"name": name, "root": root_path, "index": index, "was": kept})
            layer_path = layout.character(name)
            match = _Character(layer_path, characters.author(layer_path, name + "_postmodel", rig, parts, hierarchy,
                                                             statement, textures, families, volumes, attached),
                               print_key, rig, parts)
            written.append(match)
            rest = _rest_of(rig, hierarchy)
            for clip in (performances(rig, hierarchy.nodes[index]) if performances is not None else ()):
                layer = layout.animation(name, animations.layer_stem(clip))
                where = tuple(dict.fromkeys(owners(rig, hierarchy.nodes[index], clip))) if owners is not None else ()
                known = performed.get((rest, clip.key, where))
                if known is None:
                    made = animations.author(layer, identifier(clip.name), rig, hierarchy, clip, where)
                    performed[(rest, clip.key, where)] = (layer, made)
                else:
                    first, made = known
                    if made["written"]:
                        _link(first, layer)
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
    in_scene = {node: held for node, held in components.items() if node not in on_rigs}
    report.update(scene.author(layout.scene(scene_name), scene_name, statement, hierarchy, set(rig_roots), covered,
                               placed, pieces, placements, {**collision, **drawn}, in_scene))
    report["textures_written"] = textures.written
    report["textures_linked"] = textures.linked
    report["textures_copied"] = textures.copied
    report["texture_bytes"] = textures.total_bytes
    report["textures_without_pixels"] = len(textures.missing)
    report["seconds"] = round(time.time() - started, 1)
    return report
