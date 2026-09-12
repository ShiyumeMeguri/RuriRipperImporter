"""Turn a resolved Unity prefab (or a lone Mesh asset) into one GLB that
Substance Painter can open, plus the material table the texture/shader stage
then wires up.

This is the Painter-side twin of the Blender addon's prefab_importer: same
renderer discovery, same LOD0/shadow-proxy filtering, same bind-pose bake --
only the output differs (a glTF primitive per submesh instead of a Blender
object per renderer), because Painter's entry point is a mesh file rather than
a scene graph.

Every Texture Set Painter ends up with is named after a Unity material's own
``m_Name``, since that is the identity the shader/texture wiring keys off; the
mapping from that name back to the material document is returned alongside the
file so no later stage ever has to re-derive it from a filename.
"""

from __future__ import annotations

import os

import numpy as np

from . import gltf_writer
from ...Kernel import options as kernel_options
from ...RuriRipperPyBridge.math3d import coordinate as _coordinate
from ...RuriRipperPyBridge.unity import (hierarchy, material as unity_material_props,
                                         mesh_decoder, prefab as prefab_scan, skinning)

# Unity -> glTF: negate X, flip V, re-wind faces, keep tangent handedness. All
# four are one reflection's consequences -- see the shared Space's docstring.
coordinate = _coordinate.GLTF



class MaterialEntry:
    """One material bound to one Painter Texture Set.

    ``key`` is whatever identifies it to the engine that stated it -- a Unity
    guid, an Unreal object path -- and ``props`` its normalised properties. No
    document: what the plan builders need is the properties, and an engine that
    is not Unity has no document to give."""

    __slots__ = ("guid", "name", "props", "renderers")

    def __init__(self, guid, name, props):
        self.guid = guid
        self.name = name
        self.props = props              # MaterialProperties, or None
        self.renderers = []             # renderer display names using it


class BuildResult:
    def __init__(self, name):
        self.name = name
        self.glb_path = None
        self.materials = {}             # texture set name -> MaterialEntry
        self.mesh_count = 0
        self.triangle_count = 0
        self.vertex_count = 0
        self.skipped_lod = 0
        self.skipped_shadow = 0
        self.skipped_inactive = 0
        self.inactive_included = 0
        self.blendshapes_dropped = 0
        # (right, forward) measured off the model, or None -> keep the defaults.
        self.face_basis = None
        self.landmarks = {}
        self.warnings = []

    def summary(self):
        return ("meshes={0} tris={1} verts={2} materials={3} "
                "lod_skipped={4} shadow_skipped={5} inactive_skipped={6}".format(
                    self.mesh_count, self.triangle_count, self.vertex_count,
                    len(self.materials), self.skipped_lod, self.skipped_shadow,
                    self.skipped_inactive))

    def finish(self):
        """Close out the counters that are only meaningful once every renderer
        has been visited -- everything the build knowingly did NOT carry across
        gets said out loud here instead of being discovered later in Painter."""
        if self.inactive_included:
            self.warnings.append(
                "{0} renderer(s) are disabled or sit on an inactive GameObject and were "
                "imported anyway (they may be runtime-toggled variants). Untick 'Import "
                "inactive renderers' to match exactly what the game draws.".format(
                    self.inactive_included))
        if self.blendshapes_dropped:
            self.warnings.append(
                "{0} blend shape(s) were not carried over: Painter has no morph targets. The "
                "mesh is at its neutral shape, which is the correct one to texture "
                "on.".format(self.blendshapes_dropped))


def resolve_options(options):
    return kernel_options.resolve(options)


def _register_material(result, db, ref, renderer_name):
    """Resolve a {fileID, guid} material reference into the result's material
    table, keyed by the Texture Set name Painter will end up using."""
    if not isinstance(ref, dict):
        return None
    guid = ref.get("guid")
    if not guid:
        return None
    guid = guid.lower()
    unity_file = db.load_guid(guid)
    document = unity_file.first("Material") if unity_file else None
    name = unity_material_props.material_name(document, guid)

    existing = result.materials.get(name)
    if existing is None:
        # Parsed HERE, where the document is read -- the plan builders take the
        # normalised properties and never learn which engine produced them.
        entry = MaterialEntry(guid, name, unity_material_props.parse_material(document))
        result.materials[name] = entry
    elif existing.guid != guid:
        # Two distinct .mat assets sharing one m_Name would collapse into a
        # single Painter Texture Set -- say so rather than silently painting
        # one material's textures onto both.
        result.warnings.append(
            "Two different materials are both named '{0}' ({1} / {2}) -- Painter can only "
            "give them one Texture Set; the second is wired from the first.".format(
                name, existing.guid[:8], guid[:8]))
        entry = existing
    else:
        entry = existing
    if renderer_name not in entry.renderers:
        entry.renderers.append(renderer_name)
    return entry


# ---------------------------------------------------------------------------
# Geometry -> glTF primitives
# ---------------------------------------------------------------------------
def _primitives_from_decoded(decoded, material_indices, options, submesh_range=None):
    """One glTF primitive per submesh, each compacted down to only the vertices
    that submesh actually references (so a Texture Set never carries another
    material's vertices).

    ``submesh_range`` is Unity's static-batch (firstSubMesh, subMeshCount): a
    batched renderer draws only that window of a shared mesh, and its
    m_Materials list is indexed from the START of that window."""
    triangles = decoded.triangles
    if triangles is None or len(triangles) == 0:
        return []

    positions = coordinate.convert_points(decoded.positions)
    normals = (coordinate.convert_points(decoded.normals)
               if decoded.normals is not None else None)
    tangents = (coordinate.convert_tangents(decoded.tangents)
                if decoded.tangents is not None else None)
    colors = (np.ascontiguousarray(decoded.colors, dtype=np.float32)
              if decoded.colors is not None else None)
    # glTF requires TEXCOORD_n to run consecutively from 0, and Assimp (which
    # is what Painter decodes glTF with) stops counting UV channels at the
    # first gap -- a Unity mesh carrying UV0 and UV2 but no UV1 would silently
    # lose UV2 entirely. Re-index to 0..n-1 in ascending Unity order, so UV0
    # (the set Painter paints on) stays UV0 and nothing is dropped.
    uvs = {}
    for slot, layer in enumerate(sorted(decoded.uvs)):
        data = decoded.uvs[layer]
        # The V flip is part of the conversion, not a preference -- Unity's
        # texture origin is bottom-left, glTF's is top-left (coordinate.convert_uvs).
        uvs[slot] = coordinate.convert_uvs(data)

    winding = coordinate.reverse_winding(triangles)
    submesh_of_tri = decoded.tri_material
    submesh_count = max(len(decoded.submeshes), 1)
    if submesh_range is not None:
        first, count = submesh_range
        submeshes = range(first, min(first + count, submesh_count))
    else:
        first = 0
        submeshes = range(submesh_count)

    primitives = []
    for submesh in submeshes:
        if submesh_of_tri is not None and len(submesh_of_tri) == len(winding):
            selected = winding[submesh_of_tri == submesh]
        elif submesh == 0:
            selected = winding
        else:
            selected = winding[:0]
        if len(selected) == 0:
            continue

        slot = submesh - first
        used, remapped = np.unique(selected.reshape(-1), return_inverse=True)
        primitive = {
            "positions": positions[used],
            "indices": remapped.astype(np.uint32).reshape(-1, 3),
            "material": material_indices[slot] if slot < len(material_indices) else None,
        }
        if normals is not None:
            primitive["normals"] = normals[used]
        if tangents is not None:
            primitive["tangents"] = tangents[used]
        if colors is not None:
            primitive["colors"] = colors[used]
        if uvs:
            primitive["uvs"] = {layer: data[used] for layer, data in uvs.items()}
        primitives.append(primitive)
    return primitives


def _decode_mesh_ref(db, mesh_ref, result, owner):
    """Resolve + decode a mesh reference (shared), then say -- in Painter's own
    terms -- whatever the result costs the user."""
    loaded = prefab_scan.load_mesh(db, mesh_ref, owner)
    if not loaded.ok:
        if loaded.problem == "not_found":
            result.warnings.append("{0}: mesh {1} not found in the resolved closure".format(
                owner, loaded.detail[:8]))
        elif loaded.problem == "no_mesh_document":
            result.warnings.append("{0}: referenced asset holds no Mesh document".format(owner))
        elif loaded.problem == "empty":
            result.warnings.append("{0}: {1}".format(owner, loaded.detail))
        return None
    decoded = loaded.decoded

    # Partial losses, where SOMETHING still comes through: say so rather than
    # letting the difference show up later as a shading or seam mystery.
    if loaded.dropped_topologies:
        result.warnings.append(
            "{0}: dropped {1} submesh(es) -- only triangle lists can be textured.".format(
                owner, ", ".join(mesh_decoder.TOPOLOGY_NAMES.get(t, str(t))
                                 for t in loaded.dropped_topologies)))
    if decoded.normals is None:
        result.warnings.append(
            "{0}: stored normals were unusable (absent, or they failed the unit-length "
            "check) -- Painter will generate its own.".format(owner))
    if not decoded.uvs:
        result.warnings.append(
            "{0}: mesh has NO UV set -- it cannot be textured until it is unwrapped.".format(
                owner))
    if decoded.blendshapes:
        result.blendshapes_dropped += len(decoded.blendshapes)
    return decoded


def _measure_face_basis(landmarks, result):
    """Measure the character's own right/forward axes from the geometry we just
    wrote, in the space we wrote it in.

    The SDF face shader needs unity_ObjectToWorld's column 0 (right) and column 2
    (forward). Painter's mesh has no matrix -- and Painter documents no
    world-space handedness anywhere in its shipped docs -- so a fixed convention
    is a guess that is silently wrong half the time. These axes are instead read
    off anatomical landmarks that the renderer names already identify, which is
    an actual measurement of THIS character in the exported space:

      * right   = from the "..._l_..." renderer's centroid to the "..._r_..."
                  one (eyebrow/eye pairs). Left-to-right is the definition of
                  the character's right.
      * up      = the model's longest bounding axis (a standing character),
                  signed so the head landmarks are on the positive side.
      * forward = cross(up, right), signed so the eye/face landmarks sit on the
                  positive side of the head's centre -- a face points forwards.

    ``landmarks`` maps renderer name -> (centroid, lo, hi). Returns
    (right, forward) or None when the character carries no usable pair, in which
    case the caller keeps its documented defaults.
    """
    if not landmarks:
        return None
    lows = np.array([v[1] for v in landmarks.values()], dtype=np.float64)
    highs = np.array([v[2] for v in landmarks.values()], dtype=np.float64)
    lo, hi = lows.min(axis=0), highs.max(axis=0)
    extent = hi - lo
    up_axis = int(np.argmax(extent))

    def pick(*needles):
        hits = [c for name, (c, _l, _h) in landmarks.items()
                if any(n in name.lower() for n in needles)]
        return np.mean(hits, axis=0) if hits else None

    up = np.zeros(3)
    up[up_axis] = 1.0
    centre = (lo + hi) * 0.5
    head = pick("face", "eye", "brow", "iris", "hair")
    if head is None:
        result.warnings.append(
            "face basis: no face/eye/brow renderer to locate the head, keeping the default "
            "axes -- check _FaceRight/_FaceForward if the face shadow looks wrong.")
        return None
    head = np.asarray(head, dtype=np.float64)
    if head[up_axis] < centre[up_axis]:
        up = -up

    # FORWARD from the head landmarks alone: a face points where the face, eyes
    # and brows sit relative to the body's centre. This needs no left/right pair
    # -- plenty of characters have a single "brow_01" renderer rather than an
    # "_l_"/"_r_" pair, and requiring one made the measurement bail on them.
    forward = head - centre
    forward = forward - up * float(np.dot(forward, up))   # horizontal part only
    norm = np.linalg.norm(forward)
    if norm < 1e-6:
        result.warnings.append(
            "face basis: the head sits directly above the body centre with no horizontal "
            "offset, so forward cannot be measured; keeping the defaults.")
        return None
    forward /= norm
    # Snap to the dominant axis. A character's forward IS an axis; the small
    # off-axis part of a centroid difference would otherwise tilt the plane the
    # SDF sweeps in, which is exactly what draws a diagonal edge across the face.
    dominant = int(np.argmax(np.abs(forward)))
    snapped = np.zeros(3)
    snapped[dominant] = 1.0 if forward[dominant] > 0 else -1.0
    forward = snapped

    # RIGHT is then derived, not measured: this exporter mirrors X
    # (coordinate.convert_points), so the model in Painter is a mirror image of
    # the Unity character and its anatomical right is the reflected one -- hence
    # the minus in front of the cross product.
    right = -np.cross(up, forward)
    norm = np.linalg.norm(right)
    if norm < 1e-9:
        return None
    right /= norm

    # When the character DOES carry a left/right pair, use it as an independent
    # CHECK on that reasoning rather than as the source of it.
    left_mark = pick("_l_", "_left", "left_")
    right_mark = pick("_r_", "_right", "right_")
    if left_mark is not None and right_mark is not None:
        measured = (np.asarray(right_mark, dtype=np.float64)
                    - np.asarray(left_mark, dtype=np.float64))
        norm = np.linalg.norm(measured)
        if norm > 1e-9 and float(np.dot(measured / norm, right)) < 0.0:
            result.warnings.append(
                "face basis: the model's own left/right renderers disagree with the derived "
                "right axis -- following the geometry and flipping it.")
            right = -right

    result.warnings.append(
        "face basis measured from the model: right={0} forward={1} (up {2}{3})".format(
            [round(float(v), 3) for v in right],
            [round(float(v), 3) for v in forward],
            "+" if up[up_axis] > 0 else "-", "XYZ"[up_axis]))
    return [float(v) for v in right], [float(v) for v in forward]


def _fallback_material(builder, result, name):
    """A renderer slot with no resolvable Unity material still needs a NAMED
    glTF material, or Painter drops that geometry into an anonymous default
    Texture Set that nothing can then be wired to. Name it after the renderer
    and give it a plan of its own (shader defaults, no textures)."""
    entry = result.materials.get(name)
    if entry is None:
        entry = MaterialEntry(None, name, None)
        result.materials[name] = entry
    if name not in entry.renderers:
        entry.renderers.append(name)
    return builder.material("renderer:" + name, name)


def _emit(builder, result, name, decoded, node_matrix, material_entries, options,
          submesh_range=None):
    material_indices = []
    for entry in material_entries:
        if entry is None:
            material_indices.append(_fallback_material(builder, result, name))
        else:
            material_indices.append(builder.material(entry.guid, entry.name))
    # A renderer whose m_Materials is shorter than its submesh list (Unity
    # simply stops drawing the surplus) still has to name every submesh here,
    # or those triangles land in an anonymous Texture Set.
    wanted = submesh_range[1] if submesh_range else max(len(decoded.submeshes), 1)
    while len(material_indices) < max(wanted, 1):
        material_indices.append(_fallback_material(builder, result, name))

    primitives = _primitives_from_decoded(decoded, material_indices, options, submesh_range)
    if not primitives:
        return
    stacked = np.concatenate([p["positions"] for p in primitives], axis=0)
    result.landmarks[name] = (stacked.mean(axis=0).astype(np.float64),
                              stacked.min(axis=0).astype(np.float64),
                              stacked.max(axis=0).astype(np.float64))
    mesh_index = builder.mesh(name, primitives)
    builder.node(name, mesh_index, node_matrix)
    result.mesh_count += 1
    result.vertex_count += int(sum(len(p["positions"]) for p in primitives))
    result.triangle_count += int(sum(len(p["indices"]) for p in primitives))


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def build_from_prefab(db, prefab, name, out_path, options=None):
    """Build ``out_path`` (a .glb) from ONE full-YAML prefab UnityFile."""
    return build_from_prefabs(db, [prefab], name, out_path, options)


def build_from_prefabs(db, prefabs, name, out_path, options=None, placements=None):
    """Build ``out_path`` (a .glb) out of EVERY prefab given.

    A Painter project is one mesh FILE, which is not the same as one asset: a
    glTF file carries a node hierarchy, so several roots land in it exactly as
    several objects land in a scene -- each renderer at its own world transform,
    each material still its own texture set. Taking only the first root instead
    was a shortfall, not a difference between the hosts: a multi-row selection, a
    character assembled out of part prefabs and a scene window all resolve to
    several roots and all mean "put them in front of me".

    ``placements`` is an optional Unity-space 4x4 per prefab, for a root whose
    own transform tree is not where it belongs -- a character part authored
    relative to the bone the game hangs it on. None means "where the prefab
    itself says", which is every ordinary root."""
    options = resolve_options(options)
    result = BuildResult(name)
    builder = gltf_writer.GlbBuilder()
    stats = prefab_scan.SkipStats()
    kept = [(prefab, (placements or [None] * len(prefabs))[position])
            for position, prefab in enumerate(prefabs) if prefab is not None]
    prefabs = [prefab for prefab, _placement in kept]

    for prefab, placement in kept:
        # Each root brings its OWN transform tree: world matrices are only
        # meaningful within the prefab that states them.
        nodes, _roots = hierarchy.build_hierarchy(prefab)
        file_id_to_world = hierarchy.world_matrices(nodes)
        go_to_node = {node.go_id: node for node in nodes.values()}

        # Which renderers actually draw -- LODGroup levels, ShadowsOnly proxies,
        # disabled/inactive GameObjects and static-batch windows -- is decided by
        # the shared rules (RuriRipperPyBridge.unity.prefab), so this plugin and
        # the Blender add-on cannot disagree about what a prefab contains.
        for renderer in prefab_scan.iter_renderers(prefab, go_to_node, options, stats):
            decoded = _decode_mesh_ref(db, renderer.mesh_ref, result, renderer.name)
            if decoded is None:
                continue
            entries = [_register_material(result, db, ref, renderer.name)
                       for ref in renderer.material_refs]
            node = renderer.node
            if renderer.is_skinned and skinning.bake_bind_pose(decoded, renderer.bones,
                                                              file_id_to_world):
                # Vertices are already in world space -- the node must not move
                # them again, unless this whole root is being placed somewhere
                # its own tree does not say.
                node_matrix = (coordinate.convert_matrix(placement)
                               if placement is not None else None)
            else:
                world = node.world if node is not None else np.eye(4, dtype=np.float64)
                if placement is not None:
                    world = placement @ world
                node_matrix = coordinate.convert_matrix(world)
            _emit(builder, result, renderer.name, decoded, node_matrix, entries,
                  options, renderer.submesh_range)

    result.skipped_lod += stats.lod
    result.skipped_shadow += stats.shadow
    result.skipped_inactive += stats.inactive
    result.inactive_included += stats.inactive_included

    result.face_basis = _measure_face_basis(result.landmarks, result)
    result.finish()
    if builder.is_empty():
        result.warnings.append(_empty_diagnosis(prefabs[0]) if prefabs else
                               "Nothing importable was resolved.")
        return result

    _write(builder, out_path, result)
    return result


def build_from_mesh(db, mesh_file, name, out_path, options=None, material_refs=()):
    """Build ``out_path`` from a standalone Mesh asset (no renderer, so no
    material binding of its own unless the caller supplies one)."""
    options = resolve_options(options)
    result = BuildResult(name)
    builder = gltf_writer.GlbBuilder()

    mesh_doc = mesh_file.first("Mesh") if hasattr(mesh_file, "first") else mesh_file
    if mesh_doc is None:
        result.warnings.append("No Mesh document in the selected asset.")
        return result
    decoded = mesh_decoder.decode_mesh(mesh_doc)
    if decoded.positions is None or len(decoded.positions) == 0:
        result.warnings.append("The selected Mesh carries no vertex data.")
        return result

    display = str(mesh_doc.data.get("m_Name", name))
    entries = [_register_material(result, db, ref, display) for ref in material_refs]
    _emit(builder, result, display, decoded, None, entries, options)
    result.finish()
    if builder.is_empty():
        result.warnings.append("The selected Mesh produced no triangles.")
        return result
    _write(builder, out_path, result)
    return result


def _write(builder, out_path, result):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    builder.write(out_path)
    result.glb_path = out_path


def _empty_diagnosis(prefab):
    """An actionable diagnosis for the classic dead-end: a prefab that only
    REFERENCES a binary model file (a thin PrefabInstance wrapper) carries no
    YAML geometry at all, so the build comes out empty-looking with no
    explanation. Detect the shape and say exactly what to do about it."""
    has_instance = prefab.first("PrefabInstance") is not None
    has_geometry = (prefab.first("SkinnedMeshRenderer") is not None
                    or prefab.first("MeshFilter") is not None)
    if has_instance and not has_geometry:
        return ("This prefab only references a binary model (a thin PrefabInstance wrapper) "
                "-- it carries no YAML geometry. Import it through the cabmap bridge instead, "
                "or run Unity's 'Ruri > Dump Model to YAML' on the model and pick the "
                "generated <model>_yaml/<model>.prefab.")
    return "No SkinnedMeshRenderer/MeshRenderer with usable geometry found in this asset."
