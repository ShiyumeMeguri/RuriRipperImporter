"""Putting a stated selection into this document: rigs, objects, meshes,
materials, textures and morph targets.

THE ONE IMPORT PATH. There is no second one for a prefab, a scene window, an
assembled character, a level or a single mesh, because the kernel states all of
them the same way -- a transform tree, the meshes those transforms draw, the
skeletons they are skinned to, the materials they wear, the textures those read,
and the blend shapes they carry, every number already in this host's own basis.
What differs between a character and a level is how many rows come back.

Nothing here decides anything about the DATA: no hierarchy walk over engine
documents, no bind-pose baking, no coordinate conversion, no level-of-detail
rule, no material vocabulary, no per-game branch. Those all happened on the other
side over the buffers the decoder built. What is here is Blender: how a mesh
datablock is filled, how an armature's edit bones are laid out, how weights go in
through bmesh, how a shape key is added.
"""

from __future__ import annotations

import numpy as np

import bpy
from mathutils import Matrix, Quaternion, Vector

from ...Kernel import statement as kernel_statement
from . import derived_state, light_parameters, material_builder, renderer, rig_identity, shadow_casting

#: The custom property a placed object carries its stated tag under, so a camera
#: the game tagged is found again by what the game called it.
TAG = "unity_tag"

#: Cosmetic bone length for a bone with no child to point at.
_DEFAULT_BONE_LENGTH = 0.03
_MAX_BONE_LENGTH = 0.3
_MIN_BONE_LENGTH = 0.005

#: A game rig routinely carries a few hundred bones whose lengths are cosmetic;
#: octahedral bodies at those lengths bury the mesh they drive, while sticks stay
#: readable at any bone count.
_DISPLAY_TYPE = "STICK"

_WEIGHT_EPSILON = 1e-6

#: Which bone collection a humanoid slot belongs to. The slot vocabulary is the
#: engine's own and arrives on the bone; this is only how a person wants them
#: grouped in the outliner.
_HUMANOID_REGIONS = (
    ("Finger", "Fingers"), ("Thumb", "Fingers"), ("Index", "Fingers"),
    ("Middle", "Fingers"), ("Ring", "Fingers"), ("Little", "Fingers"),
    ("Shoulder", "Arms"), ("Arm", "Arms"), ("Hand", "Arms"),
    ("Leg", "Legs"), ("Foot", "Legs"), ("Toe", "Legs"),
    ("Eye", "Head"), ("Jaw", "Head"), ("Head", "Head"), ("Neck", "Head"),
    ("Hips", "Torso"), ("Spine", "Torso"), ("Chest", "Torso"),
)


def materialise(context, statement, options, report=None):
    """Put one statement into the document. Returns
    :class:`Kernel.statement.Built`."""
    return _Materialisation(context, statement, dict(options or {}), report).run()


def build_rig(context, statement, key, options=None):
    """One stated skeleton as an armature, nothing that draws with it, the active object and the
    selection left as they were."""
    view_layer = context.view_layer
    active = view_layer.objects.active
    selected = list(context.selected_objects)
    for chosen in selected:
        chosen.select_set(False)
    rig = _Materialisation(context, statement, dict(options or {}), None)._build_rig(key)
    rig.select_set(False)
    for chosen in selected:
        chosen.select_set(True)
    view_layer.objects.active = active
    return rig


def _trs(position, rotation, scale):
    """The local transform of one stated row, as a matrix. The rotation arrives
    as (x, y, z, w) and mathutils wants (w, x, y, z)."""
    return (Matrix.Translation(Vector(position))
            @ Quaternion((rotation[3], rotation[0], rotation[1], rotation[2])).to_matrix().to_4x4()
            @ Matrix.Diagonal(Vector(scale)).to_4x4())


#: The basis' own aim turn, as a Matrix. Read from the kernel once: it is a
#: statement about this host's camera and light convention, not a number this
#: file gets to have an opinion about.
_AIM = []


def _aim_turn():
    if not _AIM:
        _AIM.append(Matrix(kernel_statement.basis(kernel_statement.BLENDER)["aim"].tolist()))
    return _AIM[0]


def _humanoid_collection(slot):
    side = ""
    body = slot
    for prefix, suffix in (("Left", ".L"), ("Right", ".R")):
        if slot.startswith(prefix):
            side = suffix
            body = slot[len(prefix):]
            break
    for word, region in _HUMANOID_REGIONS:
        if word in body:
            return region + side
    return "Other" + side


class _Materialisation:
    """One run. Holds what was built so far, so a node can be parented to
    whatever its parent turned into and a mesh drawn twice is built once."""

    def __init__(self, context, statement, options, report):
        self.context = context
        self.statement = statement
        self.options = options
        self.report = report
        self.materials = material_builder.MaterialBuilder(statement, options)
        self.objects = []
        self.warnings = []
        self.missing = []
        #: skeleton key -> (armature object, {bone path: bone name})
        self.rigs = {}
        #: node index -> the object that node became, or None
        self.built = {}
        #: node index -> the transform a skipped node passes down to its children
        self.carried = {}
        #: node index -> whether it took this basis' aim turn, so its children can
        #: take the inverse and stay where the source put them
        self.aimed = {}
        #: mesh key -> the mesh datablock, so an instanced mesh is built once
        self.meshes = {}
        #: per-draw values the statement binds that no shading stack reads -> how many objects carried one
        self.unread_parameters = {}

    # -- the run ------------------------------------------------------------
    def run(self):
        roots = {root.node: root for root in self.statement.roots}
        for key in self._skeleton_keys():
            derived_state.announce(self._build_rig(key))
        for node in self.statement.nodes:
            self._build_node(node, roots)
        self._stamp_light_frames()
        for entry in self.statement.report:
            self.warnings.append("{0} x{1}: {2}".format(entry.what, entry.count, entry.detail))
        for name, count in sorted(self.unread_parameters.items()):
            self.warnings.append("per-draw value no shading stack reads x{0}: {1}".format(count, name))
        self._fit_shadow_pool()
        # Building nothing is an ANSWER, and it is one of two different answers:
        # the selection stated nothing, or it stated transforms and not one of
        # them draws. Handing back an empty document without saying which is the
        # one result nobody can act on -- it looks exactly like a broken import.
        if not self.objects:
            self.warnings.append(
                "nothing was placed: this selection states {0} transform(s) and none of them "
                "draws anything -- turn on 'Import Empties' to place them anyway".format(
                    len(self.statement.nodes))
                if self.statement.nodes else
                "nothing was placed: this selection states no transform at all")
        first_rig = next(iter(self.rigs.values()), None)
        return kernel_statement.Built(
            rig=None if first_rig is None else first_rig[0],
            objects=self.objects, missing=self.missing, warnings=self.warnings,
            imported=len(self.objects))

    def _stamp_light_frames(self):
        """The world orientation of every light this run placed (:func:`light_parameters.stamp_frame`), once the
        hierarchy it hangs in is complete: a world matrix is the chain of parent, parent inverse and local matrices."""
        for obj in self.objects:
            if obj.type != "LIGHT":
                continue
            world = obj.matrix_basis
            child = obj
            while child.parent is not None:
                world = child.parent.matrix_basis @ child.matrix_parent_inverse @ world
                child = child.parent
            light_parameters.stamp_frame(obj, world)

    def _fit_shadow_pool(self):
        """A statement that brings shadow-casting lights fits the pool they draw from."""
        if any(obj.type == "LIGHT" and obj.data.use_shadow for obj in self.objects):
            renderer.fit_shadow_pool(self.context.scene)

    def _skeleton_keys(self):
        """The skeletons something SKINNED actually draws with.

        Every seed states its transform tree as a skeleton, because a clip binds
        to that tree whether or not anything is skinned to it. Building a rig for
        one nothing is skinned to would put a few hundred bones in the document
        for a courtyard -- so what decides is a skinned renderer, not the
        statement merely carrying the tree."""
        return [key for key in self.statement.skeletons
                if any(node.skeleton == key and node.kind == kernel_statement.SKINNED
                       for node in self.statement.nodes)]

    # -- rigs ---------------------------------------------------------------
    def _build_rig(self, key):
        skeleton = self.statement.skeletons[key]
        armature = bpy.data.armatures.new(skeleton.bones[0].name if skeleton.bones else key)
        armature.display_type = _DISPLAY_TYPE
        rig = bpy.data.objects.new(armature.name, armature)
        self.context.collection.objects.link(rig)

        worlds = self._bone_worlds(skeleton)
        children = {}
        for bone in skeleton.bones:
            if bone.parent >= 0:
                children.setdefault(bone.parent, []).append(bone.index)

        self.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = armature.edit_bones
        made = {}
        for bone in skeleton.bones:
            edit_bone = edit_bones.new(bone.name)
            length = self._bone_length(bone, children, worlds)
            edit_bone.head = (0.0, 0.0, 0.0)
            edit_bone.tail = (0.0, length, 0.0)
            edit_bone.matrix = worlds[bone.index]
            edit_bone.length = length
            made[bone.index] = edit_bone
        for bone in skeleton.bones:
            if bone.parent in made:
                made[bone.index].parent = made[bone.parent]
        # Names may have been uniquified, and a caller binding weights needs the
        # ones that stuck -- read them while the edit bones are still live.
        names = {bone.index: made[bone.index].name for bone in skeleton.bones}
        bpy.ops.object.mode_set(mode="OBJECT")

        # The frame the whole statement sits in, which every root states alike:
        # a skinned mesh is baked to the rig's own rest inside that frame, so the
        # rig object carries the frame and the mesh carries nothing.
        frame = next(iter(self.statement.roots), None)
        rig.matrix_world = (_trs(frame.position, frame.rotation, frame.scale)
                            if frame is not None else Matrix.Identity(4))
        self._stamp(rig, skeleton, names)
        self._group_humanoid(rig, skeleton, names)
        self.rigs[key] = (rig, {bone.path: names[bone.index] for bone in skeleton.bones})
        self.objects.append(rig)
        return rig

    def _bone_worlds(self, skeleton):
        """Every bone's rest world matrix, parents before children -- which the
        kernel guarantees the row order is."""
        worlds = {}
        for bone in skeleton.bones:
            local = _trs(bone.position, bone.rotation, bone.scale)
            parent = worlds.get(bone.parent)
            worlds[bone.index] = local if parent is None else parent @ local
        return worlds

    def _bone_length(self, bone, children, worlds):
        head = worlds[bone.index].translation
        for child in children.get(bone.index, ()):
            distance = (worlds[child].translation - head).length
            if distance > 1e-5:
                return max(min(distance, _MAX_BONE_LENGTH), _MIN_BONE_LENGTH)
        return _DEFAULT_BONE_LENGTH

    def _stamp(self, rig, skeleton, names):
        """What this rig IS, written where it survives a rename and a reopen.

        The rest table is stamped in the basis the ENGINE's own animation data is
        written in, not this host's: a clip's curves arrive in that basis, and a
        rest pose measured in a different one cannot be subtracted from them. The
        kernel converts it -- this side asks the same statement for the same
        skeleton in that basis, which costs one more table and no second read of
        the selection."""
        source = self.statement.in_basis(kernel_statement.UNITY).skeletons.get(skeleton.key)
        rests = {}
        for bone in (source.bones if source is not None else skeleton.bones):
            name = names.get(bone.index)
            if not name or bone.identity is None:
                continue
            local = _trs(bone.position, bone.rotation, bone.scale)
            rests[bone.identity] = {"bone": name,
                                    "local": [value for row in local for value in row]}
        rig_identity.stamp(rig, rests)
        rig_identity.stamp_avatar(rig, skeleton.avatar)
        rig_identity.stamp_source(rig, self.options.get("source_game", ""))
        # The FIRST seed: a selection that built one rig out of several seeds put the
        # character first and what it wears after it.
        seeds = list(self.statement.seeds)
        rig_identity.stamp_seed(rig, seeds[0] if seeds else "")

    def _group_humanoid(self, rig, skeleton, names):
        """Sort a humanoid rig's bones into bone collections, as the avatar states
        them. A generic rig states nothing and gets nothing: there is no statement
        anywhere of what its bones are, and guessing from names is how a rig ends
        up mis-sorted with no way to tell. Bones the avatar never mentions (twist
        helpers, skirt, hair, props) are left out of every collection rather than
        swept into a bucket -- no collection means unclassified, which is the
        truth about them."""
        grouped = {}
        for bone in skeleton.bones:
            if bone.humanoid:
                found = rig.data.bones.get(names.get(bone.index, ""))
                if found is not None:
                    grouped.setdefault(_humanoid_collection(bone.humanoid), []).append(found)
        for name in sorted(grouped):
            collection = rig.data.collections.get(name) or rig.data.collections.new(name)
            for bone in grouped[name]:
                collection.assign(bone)

    # -- nodes --------------------------------------------------------------
    def _build_node(self, node, roots):
        root = roots.get(node.index)
        local = _trs(node.position, node.rotation, node.scale)
        if root is not None:
            local = _trs(root.position, root.rotation, root.scale) @ local
        carried = self.carried.get(node.parent)
        if carried is not None:
            local = carried @ local
        # A camera or a light is the one transform whose LOCAL AXES mean something
        # beyond where it sits: the engine points them along one axis and this host
        # along another, and a converted transform used as-is aims every one of them
        # a quarter turn off. The turn is the basis' own statement, it lands only on
        # the aimable node, and a child of one carries its inverse so that turning
        # the parent does not carry the child around with it.
        aimed = node.light is not None or node.camera is not None
        if self.aimed.get(node.parent):
            local = _aim_turn().inverted() @ local
        if aimed:
            local = local @ _aim_turn()
        self.aimed[node.index] = aimed

        if node.kind == kernel_statement.SKINNED and node.mesh:
            self._build_skinned(node)
            return
        data = self._data_for(node)
        if data is None and not self._needs_empty(node):
            self.carried[node.index] = local
            return
        made = bpy.data.objects.new(node.name, data)
        self._collection_of(node).objects.link(made)
        self._draws_now(made, node)
        if isinstance(data, bpy.types.Mesh):
            self._cast_shadows(made, node)
            self._object_parameters(made, node)
        parent = self.built.get(node.parent)
        if parent is not None:
            made.parent = parent
            made.matrix_parent_inverse = Matrix.Identity(4)
        made.matrix_basis = local
        if node.tag:
            made[TAG] = node.tag
        self._apply_morphs(made, node.mesh)
        self.built[node.index] = made
        self.objects.append(made)
        derived_state.announce(made)

    @staticmethod
    def _draws_now(made, node):
        """Whether the object draws right now is the source's fact. An inactive object or a
        disabled renderer is stated so that the title's run-time toggle has something to turn
        on, and it draws nothing until then -- so it arrives hidden from the viewport and the
        render, present and one click from showing."""
        if not node.active:
            made.hide_viewport = True
            made.hide_render = True

    def _cast_shadows(self, made, node):
        """Whether the object throws a shadow, and whether it shows at all.

        The renderer states how it draws into shadow maps. Off throws none. A casting
        renderer still needs a material with an enabled shadow-caster pass (water,
        decals, effects and light beams have none); materials that state no passes -- an
        engine without them -- leave the host's default standing, as does a source that
        states nothing of the renderer. Shadows-only throws one and draws nothing else:
        no camera, reflection or light probe sees it. A casting renderer whose shadow
        stays out of the directional light's cascades blocks local lights only."""
        verdicts = [stated.casts_shadow for stated in
                    (self.statement.materials.get(key) for key in node.materials)
                    if stated is not None and stated.casts_shadow is not None]
        if node.shadows == kernel_statement.SHADOWS_OFF:
            made.visible_shadow = False
        elif verdicts:
            made.visible_shadow = any(verdicts)
        if node.shadows == kernel_statement.SHADOWS_ONLY:
            shadow_casting.shadow_only(made)
        if node.shadows > kernel_statement.SHADOWS_OFF and not node.main_light_shadows:
            shadow_casting.exclude_from_main_light(made)
        if node.two_sided_shadows:
            made[material_builder.SHADOW_TWO_SIDED_PROPERTY] = 1.0

    def _collection_of(self, node):
        """Where a node's object goes: the collection the import runs in, or the child of it the
        statement gathers the node under -- one per name, made on first use and reused by every
        later import into the same collection."""
        home = self.context.collection
        if not node.collection:
            return home
        gathered = home.children.get(node.collection)
        if gathered is None:
            gathered = bpy.data.collections.new(node.collection)
            home.children.link(gathered)
        return gathered

    def _object_parameters(self, made, node):
        """What the pipeline binds for the renderer's own draw, where the shading stacks read it: each per-draw
        value as the per-object global of its name, written as the difference from the default the stacks declare
        (the encoding every per-object global uses) and not at all where it equals that default. A value no stack
        reads has nowhere to go and is counted for the report."""
        for name, value in node.object_parameters.items():
            base = material_builder.object_attribute_base(name)
            if base is None:
                self.unread_parameters[name] = self.unread_parameters.get(name, 0) + 1
                continue
            difference = [component - default for component, default in zip(value, base)]
            if any(difference):
                made[name] = difference
            elif name in made:
                del made[name]

    def _needs_empty(self, node):
        """Whether a transform with nothing on it still has to exist: because the
        user asked for every one, or because something under it will be parented
        to it."""
        if self.options.get("import_empties"):
            return True
        return any(other.parent == node.index and other.renders
                   for other in self.statement.nodes)

    def _data_for(self, node):
        if node.kind == kernel_statement.MESH and node.mesh:
            return self._mesh_data(node)
        if node.light is not None:
            return self._light_data(node)
        if node.camera is not None:
            return self._camera_data(node)
        return None

    def _build_skinned(self, node):
        """A skinned mesh is placed at its SKELETON, not at its own transform:
        the kernel already baked the geometry to that skeleton's rest, in the
        statement's own frame, which is what makes a mesh whose renderer sits
        somewhere arbitrary in the tree land on the rig."""
        data = self._mesh_data(node)
        if data is None:
            return
        made = bpy.data.objects.new(node.name, data)
        self._collection_of(node).objects.link(made)
        self._draws_now(made, node)
        self._cast_shadows(made, node)
        self._object_parameters(made, node)
        self.built[node.index] = made
        self.objects.append(made)
        rig, bone_names = self.rigs.get(node.skeleton, (None, {}))
        mesh = self.statement.meshes.get(node.mesh)
        if rig is not None and mesh is not None and mesh.weights is not None:
            self._apply_skin(made, mesh, bone_names)
            modifier = made.modifiers.new("Armature", "ARMATURE")
            modifier.object = rig
            modifier.use_vertex_groups = True
            made.parent = rig
            made.matrix_parent_inverse = Matrix.Identity(4)
            made.matrix_basis = Matrix.Identity(4)
        elif rig is not None:
            made.parent = rig
            made.matrix_parent_inverse = Matrix.Identity(4)
            made.matrix_basis = Matrix.Identity(4)
        else:
            self.missing.append("{0}: no rig for skeleton {1}".format(node.name, node.skeleton))
        self._apply_morphs(made, node.mesh)
        derived_state.announce(made)

    # -- meshes -------------------------------------------------------------
    def _mesh_data(self, node):
        stated = self.statement.meshes.get(node.mesh)
        if stated is None or not len(stated.positions):
            self.missing.append("{0}: the statement carries no geometry for {1}".format(
                node.name, node.mesh))
            return None
        slots = [self.materials.build(key) for key in node.materials]
        shared = self.meshes.get((node.mesh, tuple(node.materials)))
        if shared is not None:
            return shared
        data = self._build_mesh(stated, node.name, slots)
        self.meshes[(node.mesh, tuple(node.materials))] = data
        derived_state.announce(data)
        return data

    def _build_mesh(self, stated, name, slots):
        mesh = bpy.data.meshes.new(name)
        positions = stated.positions
        triangles = stated.triangles.astype(np.int32)
        vertex_count = len(positions)
        triangle_count = len(triangles)

        mesh.vertices.add(vertex_count)
        mesh.vertices.foreach_set("co", positions.reshape(-1))
        mesh.loops.add(triangle_count * 3)
        mesh.polygons.add(triangle_count)
        corner_vertices = triangles.reshape(-1)
        # A corner naming a vertex the mesh does not have is not a mesh Blender
        # can refuse -- foreach_set stores whatever it is given, and the index is
        # only read much later, when something asks for topology, by which point
        # it writes THROUGH that index and takes the process down with an access
        # violation and no Python left to report it. So the one place a stated
        # number becomes Blender's memory is where it is checked.
        if triangle_count and (corner_vertices.max() >= vertex_count or corner_vertices.min() < 0):
            outside = int(((corner_vertices >= vertex_count) | (corner_vertices < 0)).sum())
            bpy.data.meshes.remove(mesh)
            raise RuntimeError(
                "'{0}' was stated with {1} corner(s) naming vertices outside its own {2} "
                "(index range {3}..{4}) -- refusing to build it, because Blender would not "
                "fault until it next read the topology.".format(
                    name, outside, vertex_count,
                    int(corner_vertices.min()), int(corner_vertices.max())))
        mesh.loops.foreach_set("vertex_index", corner_vertices)
        mesh.polygons.foreach_set(
            "loop_start", np.arange(triangle_count, dtype=np.int32) * 3)
        mesh.polygons.foreach_set("material_index", self._slot_per_triangle(stated))
        mesh.update(calc_edges=True)

        for index in sorted(stated.uvs):
            layer = mesh.uv_layers.new(name="UVMap" if index == 0 else "UV{0}".format(index))
            layer.data.foreach_set("uv", stated.uvs[index][corner_vertices].reshape(-1))

        # A mesh that carries no colour channel still feeds one to any shader that
        # declares it: the GPU supplies the unbound stream's default, which the
        # engine binds as opaque white. Leaving the attribute out makes Blender's
        # Attribute node read zero, and every shader that multiplies by vertex
        # colour collapses to black.
        colours = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="CORNER")
        if stated.colors is not None:
            colours.data.foreach_set("color", stated.colors[corner_vertices].reshape(-1))
        else:
            colours.data.foreach_set(
                "color", np.ones(len(mesh.loops) * 4, dtype=np.float32))

        if stated.normals is not None:
            try:
                # A LIST, not the array: the setter walks its argument through the
                # python sequence protocol, and handing it the ndarray costs 7.7
                # seconds over one level for the copy tolist() avoids.
                mesh.normals_split_custom_set_from_vertices(stated.normals.tolist())
            except (RuntimeError, ValueError) as exc:
                # Never silent: without custom normals the mesh falls back to
                # Blender's own averaged normals, which on these models differ by
                # up to 45 degrees and read as a shading bug with no visible cause.
                self.warnings.append(
                    "{0}: custom split normals rejected ({1}) -- Blender will average "
                    "its own, which will not match the game".format(name, exc))
        mesh.polygons.foreach_set("use_smooth", np.ones(triangle_count, dtype=bool))

        mesh.validate(clean_customdata=False)
        mesh.update()
        for material in slots:
            mesh.materials.append(material)
        return mesh

    def _slot_per_triangle(self, stated):
        """The material slot each triangle draws with, from the stated runs."""
        per_triangle = np.zeros(len(stated.triangles), dtype=np.int32)
        for first, count, slot in stated.sections.tolist():
            per_triangle[first // 3:(first + count) // 3] = slot
        return per_triangle

    def _apply_skin(self, obj, stated, bone_names):
        """Vertex groups and weights through bmesh's deform layer -- one C-level
        write per (vertex, group) entry instead of one VertexGroup.add() call per
        distinct weight VALUE. Continuous float weights make those buckets mostly
        singletons, so the call count was effectively per entry; measured 2.6x
        faster overall on a real character and 5.3x on its largest mesh."""
        indices = stated.bone_indices
        weights = stated.weights
        vertex_count, influences = indices.shape
        slots = len(stated.bone_paths)

        # Bind only the slots something is actually weighted to. The engine lists
        # the whole skeleton, so binding every slot leaves hundreds of all-zero
        # groups on every mesh, and each one is a real attribute that a later join
        # has to merge.
        valid = indices < slots
        weighted = np.unique(indices[valid & (weights > _WEIGHT_EPSILON)])
        slot_to_group = np.full(max(slots, 1), -1, dtype=np.int64)
        by_bone = {}
        for slot in weighted.tolist():
            name = bone_names.get(stated.bone_paths[slot], "")
            if not name:
                continue
            group = by_bone.get(name)
            if group is None:
                group = by_bone[name] = obj.vertex_groups.new(name=name).index
            slot_to_group[slot] = group

        groups = np.where(valid, slot_to_group[np.where(valid, indices, 0)], -1)
        keep = (weights > _WEIGHT_EPSILON) & (groups >= 0)
        if not keep.any():
            return
        vertices = np.broadcast_to(np.arange(vertex_count, dtype=np.int64)[:, None],
                                   (vertex_count, influences))[keep]
        flat_groups = groups[keep].astype(np.int64)
        flat_weights = weights[keep].astype(np.float64)

        # Weights reaching one bone through different influence slots are summed
        # for the same vertex, then rounded once.
        combined = vertices * (flat_groups.max() + 1) + flat_groups
        unique, first, inverse = np.unique(combined, return_index=True, return_inverse=True)
        if len(unique) != len(combined):
            summed = np.zeros(len(unique), dtype=np.float64)
            np.add.at(summed, inverse, flat_weights)
            vertices = vertices[first]
            flat_groups = flat_groups[first]
            flat_weights = summed
        flat_weights = np.round(flat_weights, 6)

        order = np.argsort(vertices, kind="stable")
        vertex_list = vertices[order].tolist()
        group_list = flat_groups[order].tolist()
        weight_list = flat_weights[order].tolist()

        import bmesh
        mesh = bmesh.new()
        mesh.from_mesh(obj.data)
        deform = mesh.verts.layers.deform.verify()
        mesh.verts.ensure_lookup_table()
        vertex_table = mesh.verts
        current = -1
        entry = None
        for index in range(len(vertex_list)):
            vertex = vertex_list[index]
            if vertex != current:
                entry = vertex_table[vertex][deform]
                current = vertex
            entry[group_list[index]] = weight_list[index]
        mesh.to_mesh(obj.data)
        mesh.free()

    def _apply_morphs(self, obj, mesh_key):
        """Shape keys are added ONCE per mesh datablock even when several objects
        instance it: the keys live in the mesh, and adding them again on the
        second object would double every offset."""
        if obj.type != "MESH" or obj.data.shape_keys is not None:
            return
        apply_morphs(obj, self.statement.morphs.get(mesh_key, ()))

    # -- lights and cameras -------------------------------------------------
    def _light_data(self, node):
        """A stated light as a Blender light that hands a light loop the source's own quantity.

        A source's point or spot intensity is a radiant intensity; Blender's point and spot
        power is radiant flux, and EEVEE hands a light loop ``power / (4 pi)`` for them
        (``Light::point_radiance_get``). A sun's strength is already the irradiance it hands
        over. So point and spot power is the intensity times 4 pi, and a sun's is the
        intensity as stated. The source's lights are points: no radius. What a light scatters
        into a participating medium is scaled by the volume factor it states, its specular
        reflections by the specular factor it states.

        A stated shadow resolution is the texels the source's shadow map spans across the
        light's shadow frustum: a spot's cone, each face of a point light's cube (a 90 degree
        frustum). EEVEE limits a local light's shadow to a texel size one unit from the light,
        so the limit is ``2 tan(frustum / 2) / texels`` -- the source's own resolution, never
        finer. Without one EEVEE keeps its own limit.

        A cone blends from its inner angle to its outer one; Blender blends over the fraction
        ``spot_blend`` of the cosine span from the outer edge to the axis, so the same span is
        ``(cos inner - cos outer) / (1 - cos outer)``."""
        stated = node.light
        kind = {0: "SPOT", 1: "SUN", 2: "POINT", 3: "AREA"}.get(stated["kind"], "POINT")
        light = bpy.data.lights.new(node.name, type=kind)
        light.color = stated["color"]
        light.use_shadow = stated["shadows"]
        light.volume_factor = stated["volume"]
        light.specular_factor = stated["specular"]
        if kind in ("POINT", "SPOT"):
            light.energy = stated["intensity"] * 4.0 * np.pi
            light.shadow_soft_size = 0.0
            light.use_custom_distance = True
            light.cutoff_distance = stated["range"]
        else:
            light.energy = stated["intensity"]
        if kind == "SPOT":
            outer = np.radians(stated["angle"])
            cos_outer = np.cos(outer * 0.5)
            cos_inner = np.cos(np.radians(min(stated["inner_angle"], stated["angle"])) * 0.5)
            light.spot_size = outer
            light.spot_blend = float(np.clip((cos_inner - cos_outer) / max(1.0 - cos_outer, 1e-6), 0.0, 1.0))
        if stated["shadow_resolution"] > 0 and kind in ("POINT", "SPOT"):
            frustum = light.spot_size if kind == "SPOT" else np.pi * 0.5
            light.shadow_maximum_resolution = float(2.0 * np.tan(frustum * 0.5) / stated["shadow_resolution"])
        if kind == "AREA":
            light.size = stated["width"]
            light.size_y = stated["height"]
        light_parameters.stamp(light, stated)
        return light

    def _camera_data(self, node):
        stated = node.camera
        camera = bpy.data.cameras.new(node.name)
        camera.type = "ORTHO" if stated["orthographic"] else "PERSP"
        camera.lens_unit = "FOV"
        camera.angle_y = np.radians(stated["fov"])
        camera.ortho_scale = stated["ortho_size"] * 2.0
        camera.clip_start = max(stated["near"], 1e-4)
        camera.clip_end = stated["far"]
        return camera


def apply_morphs(obj, morphs):
    """Add one mesh's stated blend shapes as shape keys.

    Every non-basis key is created at zero: a renderer starts every blend shape
    weight at 0 unless something drives it, while Blender defaults a new key to
    full strength -- which is what made every imported character look distorted."""
    if not morphs:
        return 0
    mesh = obj.data
    obj.shape_key_add(name="Basis", from_mix=False)
    rest = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", rest)
    rest = rest.reshape(-1, 3)
    made = 0
    for morph in morphs:
        key = obj.shape_key_add(name=morph.name, from_mix=False)
        key.value = 0.0
        moved = rest.copy()
        if len(morph.vertices):
            moved[morph.vertices] += morph.positions
        key.data.foreach_set("co", moved.reshape(-1))
        made += 1
    return made
