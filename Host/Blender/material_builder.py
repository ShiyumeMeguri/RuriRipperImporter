"""What a stated material becomes in this host, and the door a shader stack
plugs into.

Two things live here and they are one unit. The first is the DOOR: a generated
shader stack (``Game/<game>/shader/Blender/``) is a projection of a recipe, and
that projection names this module and these functions as the place it registers
itself. Which functions those are is DATA inside the product -- its manifest
carries ``registry_module``/``register_fn`` and the rest -- so the names here are
the product's statement about its host, not a choice made here. Behind each name
is one :class:`Kernel.extensions.ExtensionPoint`; there is no list in this file.

The second is the FALLBACK: a material no stack claims still has to be built,
and for an engine-standard shader the Principled BSDF IS the faithful build --
same physically-based model, and the roles the kernel resolved say which of the
material's own textures and numbers feed which input. Nothing here matches a
property by how its name looks: the vocabulary is the layered role table, merged
on the kernel side.
"""

from __future__ import annotations

import hashlib
import os

import bpy

from . import linked_twins as _linked_twins
from . import plugin_data as _plugin_data
from ...Kernel import extensions

#: A stack registers ``provider(builder, props) -> bpy.types.Material | None``
#: and SELF-SELECTS by the material's own shader identity, returning None to
#: decline. First claimant wins; no claimant lands on the fallback below.
GRAPH_PROVIDERS = extensions.point(
    "blender.graph_providers",
    "Node graphs a generated shader stack builds for the materials it claims.")

#: ``apply_vertex_stage(objects, camera=None) -> int``. Every stage only
#: touches materials and modifiers it owns, so running all of them is
#: order-independent. The ONE caller is the derived-state scheduler, and the
#: ONE input it passes is the objects an import just built from the game's
#: materials. This point carries the TOPOLOGY half only -- the geometry tree and
#: the modifier holding it -- and nothing ever rebuilds it afterwards.
VERTEX_STAGES = extensions.point(
    "blender.vertex_stages",
    "The vertex tree and its modifier a shading stack builds when assets arrive.")

#: ``push_camera_basis(objects=None, camera=None) -> int``. The camera basis,
#: the half FOV and the backbuffer size are UNIFORMS baked into the vertex tree
#: -- an outline is authored as a constant width in screen pixels, so they go
#: stale the moment the camera moves. What goes stale is the VALUE, not the
#: topology: a stage re-fills those sockets on trees that already exist and
#: never builds one.
#:
#: 拆出来的理由是行为不是性能:与建树合在一条上时,推一下镜头就等于按材质**现值**重判
#: 一次「这张材质该不该有描边」,于是用户手删掉的修改器会自己长回来,而画面上没有任何
#: 东西说明是谁加的。生成修改器只属于从游戏导入的那一刻。
CAMERA_STAGES = extensions.point(
    "blender.camera_stages",
    "Camera uniforms a shading stack re-fills on vertex trees that already exist.")

#: ``apply_rig_basis(objects=None) -> int``. The face basis is a per-object
#: uniform wired into the MATERIAL, never a geometry attribute, so
#: re-translating a bone identity into today's bone name touches no modifier at
#: all -- which is why it is its own point instead of a side effect of building
#: the vertex tree.
RIG_STAGES = extensions.point(
    "blender.rig_stages",
    "Re-translating a stack's bone identities into the rig's current bone names.")

#: ``rewire_capabilities(material) -> bool``. A shader's environment queries are
#: cut at the group interface and answered from scene state, so changing that
#: state leaves the answer stale until the fulfilment nodes are rebuilt. Only
#: those nodes, never the graph: a full rebuild would take the user's tuned
#: parameters back to shipped defaults.
CAPABILITY_REWIRES = extensions.point(
    "blender.capability_rewires",
    "Re-answering a built material's environment queries after the scene changed.")

#: The scene state a material's environment answers were last built against,
#: stamped on the material when it is built and whenever it is rewired. A rewire
#: whose state is the one already built in is skipped: an import states its world
#: before it builds a single material, so the world-driven rewire that follows
#: used to rebuild every fulfilment node it had just built (445 s of a 537 s flush).
CAPABILITY_STATE_PROPERTY = "ruri_capability_state"

#: A whole MODULE rather than one function: post-processing owns scene-level
#: state (the compositor tree, the view transform), so it has to be able to hand
#: that state back as well as take it -- install / uninstall / installed.
POST_STAGES = extensions.point(
    "blender.post_stages",
    "Whole-frame processing a shading stack installs onto the scene.")

#: ``level_global_bases() -> {name: (statement, [default, ...])}``. Engine globals a
#: stack reads LIVE from the level's world instead of baking them into its materials --
#: per-level values such as fog -- each with the default its recipe declares and what
#: states it: ``level_global`` (the level's environment phase) or ``volume_global`` (the
#: volume stack the camera stands in -- post-processing switches, character lighting).
#: The stack's graph reads ``property + default``, so a property that was never written
#: answers the default and a session with no level in it is unchanged.
LEVEL_GLOBALS = extensions.point(
    "blender.level_globals",
    "Per-level engine globals a shading stack reads live from the level's world.")

#: ``level_image_layouts() -> {name: layout}``. The images a stack reads level
#: state through, since the host's material nodes only sample 2D images: a 2D
#: texture as itself, 3D textures and 2D texture arrays laid out as atlases,
#: uniform struct arrays as data tables. Per name, the image, its kind and size, how it is laid out and the
#: texel format -- plus, for a volume, the in-slice address modes that decide what
#: the ring around each slice holds.
LEVEL_IMAGES = extensions.point(
    "blender.level_images",
    "Images a shading stack reads level state through, which the host fills.")

#: ``object_attribute_bases() -> {name: [default, ...]}``. Engine globals a stack reads
#: PER OBJECT from the object's own custom properties -- which of a level's decals reach
#: that object, say -- each with the default its recipe declares. The graph reads
#: ``property + default``, so an object nothing was written on answers the default.
OBJECT_ATTRIBUTES = extensions.point(
    "blender.object_attributes",
    "Per-object engine globals a shading stack reads from object custom properties.")

#: ``texel_size_bases() -> {name: [4 floats]}``. The ``<texture>_TexelSize`` globals a
#: stack reads a level texture's size through when the texture is made at the size its
#: level states: each time the host fills such a texture's image it writes (1/w, 1/h, w, h)
#: minus the default on the scene, the way level globals are written. An image never
#: filled keeps the default -- its one-texel placeholder's own size.
TEXEL_SIZES = extensions.point(
    "blender.texel_sizes",
    "Texel-size globals of level textures made at their level's own size.")

#: ``light_parameter_attributes() -> [name, ...]``. The LIGHT attributes a stack's light
#: loop reads a source's own per-light parameters through, one per parameter vector in the
#: order a statement states them; the host stamps each stated light's vectors under them
#: when it makes the light (:mod:`light_parameters`). A stack that lights through the
#: host's own light alone reads none.
LIGHT_PARAMETERS = extensions.point(
    "blender.light_parameters",
    "Per-light parameters of a source's own light model a shading stack reads as light attributes.")

#: The LIGHT attribute every stack's light template reads a light's camera-distance fade
#: through: four coefficients (a, k, b, m), the light scaled by
#: saturate(1 + a - d²k) · saturate(1 + d²m - b) on the camera's squared distance d². The
#: host stamps it on a stated light that fades (:mod:`light_parameters`); a light without it
#: reads zeros, no fade. Declared on this registry module because every stack reaches the
#: host through it.
LIGHT_FADE_PROPERTY = "ruri_light_fade"

#: The LIGHT attribute every stack's light template reads a light's world orientation
#: through -- the one thing about a light the host's light loop does not hand over: the
#: (x, y, z) of its world rotation quaternion with w >= 0. The host stamps it on a stated
#: light once the import has placed it (:mod:`light_parameters`), the content it was stated
#: with like its culling box; a light without it reads zeros, no turn.
LIGHT_FRAME_PROPERTY = "ruri_light_frame"

#: ``render_footprint_attributes() -> [name, ...]``. Scene attributes a stack reads the
#: world size of one render output pixel through: (orthographic term, perspective term
#: per unit of view depth). The host keeps them on the scene from the render camera and
#: the output size (:mod:`viewpoint`), so a lens or resolution change rewrites a value and
#: rewires nothing.
RENDER_FOOTPRINTS = extensions.point(
    "blender.render_footprints",
    "Scene attributes a shading stack reads the render's per-pixel world size through.")

#: ``view_window_attributes() -> {"columns": [4 names], "inverse_columns": [4 names],
#: "clip": name, "screen": name} | None``. Object attributes a stack's vertex-stage programs
#: read the view being drawn through: that view's projection column by column (xyz, w) and
#: its inverse, (near, far, orthographic) and its size in pixels. The host keeps them on the
#: objects that read them (``view_window_readers``) for the 3D viewport the user is looking
#: through, and for the render camera while a render runs (:mod:`viewpoint`); the view matrix
#: itself the GPU already has.
VIEW_WINDOWS = extensions.point(
    "blender.view_windows",
    "Object attributes a shading stack reads the projection of the view being drawn through.")

#: ``view_window_readers(scene) -> [object, ...]``. The objects whose materials read the view
#: window. Asked after a file opens, an undo step, and whenever a stack says they changed
#: (:func:`view_window_readers_changed`) -- never on a tick.
VIEW_WINDOW_READERS = extensions.point(
    "blender.view_window_readers",
    "The objects whose materials read the view window.")

#: ``purge() -> int``. The plugin's own data is never written to a .blend (see
#: :mod:`plugin_data`); a generated runtime drops what an older build of it saved into a
#: file before that was so, by its own vocabulary. The load pass runs every one of them
#: before anything compiles.
PLUGIN_PURGES = extensions.point(
    "blender.plugin_purges",
    "Dropping what an older build of a shading stack saved into a file.")

#: ``compile_all() -> [material]``. A stack compiles every material it claims that this
#: session has not compiled yet -- after a file opens that is every one -- from the record
#: the material carries. The graph is the plugin's to rebuild; the record is the content.
MATERIAL_COMPILES = extensions.point(
    "blender.material_compiles",
    "Compiling a shading stack's materials from their records.")

#: Custom property stamped on every material this module or a stack builds.
SOURCE_KEY_PROPERTY = "ruri_source_key"
#: Marker on an image datablock: its colour space is already what the ASSET
#: declares, and nothing that infers one from a slot may overwrite it.
COLORSPACE_STATED_PROPERTY = "ruri_colorspace_stated"
#: The texture's own sampler state (wrap_u / wrap_v / filter), stamped on the image
#: it loads into; the generated stacks read it for slots sampled through the
#: texture's own sampler.
SAMPLING_STATED_PROPERTY = "ruri_sampling"
#: Channel 3 of an RGBA map. Blender never colour-manages alpha, so a role that
#: reads only this channel puts no requirement on the image's colour space.
_ALPHA_CHANNEL = 3

_ORPHAN_FRAME_LABEL = "Unclaimed shader: {0}"
_CHANNEL_NAMES = ("R", "G", "B", "A")


# ---------------------------------------------------------------------------
# The door -- the names a generated product binds to
# ---------------------------------------------------------------------------
def register_graph_provider(provider):
    GRAPH_PROVIDERS.add(provider)


def unregister_graph_provider(provider):
    GRAPH_PROVIDERS.remove(provider)


def register_vertex_stage(stage):
    VERTEX_STAGES.add(stage)


def unregister_vertex_stage(stage):
    VERTEX_STAGES.remove(stage)


def register_camera_stage(stage):
    CAMERA_STAGES.add(stage)


def unregister_camera_stage(stage):
    CAMERA_STAGES.remove(stage)


def register_rig_stage(stage):
    RIG_STAGES.add(stage)


def unregister_rig_stage(stage):
    RIG_STAGES.remove(stage)


def register_capability_rewire(rewire):
    CAPABILITY_REWIRES.add(rewire)


def unregister_capability_rewire(rewire):
    CAPABILITY_REWIRES.remove(rewire)


def register_post_stage(stage):
    POST_STAGES.add(stage)


def unregister_post_stage(stage):
    POST_STAGES.remove(stage)


def register_level_globals(bases):
    LEVEL_GLOBALS.add(bases)


def unregister_level_globals(bases):
    LEVEL_GLOBALS.remove(bases)


def register_volume_textures(layouts):
    """The door a stack hands its level-image layouts through (see LEVEL_IMAGES). Every
    deployed stack of every game binds this name, so it keeps it while the layouts it
    carries grew kinds beyond 3D textures."""
    LEVEL_IMAGES.add(layouts)


def unregister_volume_textures(layouts):
    LEVEL_IMAGES.remove(layouts)


def register_object_attributes(bases):
    OBJECT_ATTRIBUTES.add(bases)


def unregister_object_attributes(bases):
    OBJECT_ATTRIBUTES.remove(bases)


def register_texel_sizes(bases):
    TEXEL_SIZES.add(bases)


def unregister_texel_sizes(bases):
    TEXEL_SIZES.remove(bases)


def _texel_size_base(name):
    """The default the registered stacks declare for one texel-size global, or None when no
    stack reads it; two different defaults for one name are refused."""
    known = None
    for provider in TEXEL_SIZES:
        base = provider().get(name)
        if base is None:
            continue
        if known is not None and list(known) != list(base):
            raise ValueError("[material] texel size {0} has two defaults: {1} and {2}".format(
                name, known, list(base)))
        known = list(base)
    return known


def object_attribute_base(name):
    """The default the registered stacks declare for one per-object global, or None when no
    stack reads it. Two stacks declaring different defaults for one name cannot share an object
    property, so that is refused."""
    known = None
    for provider in OBJECT_ATTRIBUTES:
        base = provider().get(name)
        if base is None:
            continue
        if known is not None and list(known) != list(base):
            raise ValueError("[material] per-object global {0} has two defaults: {1} and {2}".format(
                name, known, list(base)))
        known = list(base)
    return known


def level_image_layout(name):
    """The layout the registered stacks read one level image through, or None when no stack
    reads it. Two stacks laying one resource out differently cannot share its image."""
    found = None
    for provider in LEVEL_IMAGES:
        layout = provider().get(name)
        if layout is None:
            continue
        if found is not None and found != layout:
            raise ValueError("[material] level image {0} has two layouts".format(name))
        found = layout
    return found


def write_level_table(name, rows):
    """Fill one level data table from ``rows``, a float array shaped (elements, fields, 4) in
    element order; the rows past them stay zero."""
    layout = level_image_layout(name)
    if layout is None or layout["kind"] != "table":
        raise ValueError("[material] no stack reads a data table {0}".format(name))
    image = volume_image(layout)
    image.pixels.foreach_set(_table_pixels([rows[None]], layout).ravel())
    image.pack()
    _plugin_data.content(image)


def register_light_parameters(attributes):
    LIGHT_PARAMETERS.add(attributes)


def unregister_light_parameters(attributes):
    LIGHT_PARAMETERS.remove(attributes)


def register_render_footprints(attributes):
    RENDER_FOOTPRINTS.add(attributes)


def unregister_render_footprints(attributes):
    RENDER_FOOTPRINTS.remove(attributes)


def render_footprint_attributes():
    """Every scene attribute the loaded stacks read the render's pixel footprint through."""
    return sorted({name for provider in RENDER_FOOTPRINTS for name in provider()})


def register_view_windows(attributes):
    VIEW_WINDOWS.add(attributes)


def unregister_view_windows(attributes):
    VIEW_WINDOWS.remove(attributes)


def view_window_attributes():
    """The distinct view-window layouts the loaded stacks read, each stated once."""
    layouts = []
    for provider in VIEW_WINDOWS:
        layout = provider()
        if layout and layout not in layouts:
            layouts.append(layout)
    return layouts


def register_view_window_readers(readers):
    VIEW_WINDOW_READERS.add(readers)


def unregister_view_window_readers(readers):
    VIEW_WINDOW_READERS.remove(readers)


def view_window_readers(scene):
    """Every object of ``scene`` some loaded stack says reads the view window, each once."""
    found = {}
    for provider in VIEW_WINDOW_READERS:
        for obj in provider(scene):
            found.setdefault(obj.as_pointer(), obj)
    return list(found.values())


def view_window_readers_changed():
    """A stack built or removed what reads the view window: the host asks again before it next writes."""
    from . import viewpoint
    viewpoint.invalidate_readers()


def register_plugin_purge(purge):
    PLUGIN_PURGES.add(purge)


def unregister_plugin_purge(purge):
    PLUGIN_PURGES.remove(purge)


def register_material_compile(compile_all):
    MATERIAL_COMPILES.add(compile_all)


def unregister_material_compile(compile_all):
    MATERIAL_COMPILES.remove(compile_all)


def plugin_data(block):
    """The door a generated product makes its own datablocks through: runtime data, never
    written to a .blend (see :mod:`plugin_data`)."""
    return _plugin_data.born(block)


def content(block):
    """The door a generated product hands a datablock it made over to the document through
    (a material instance copied off a template): written with the file from now on."""
    return _plugin_data.content(block)


def twinned(material):
    """Whether this session already draws a linked material through a compiled twin
    (see :mod:`linked_twins`)."""
    return _linked_twins.twinned(material)


def adopt_twins(pairs):
    """The door a generated stack hands the twins it compiled from linked materials'
    records through, as (linked material, twin) pairs: every user draws the twin, and
    every save keeps the file pointing at the library's material (see :mod:`linked_twins`)."""
    return _linked_twins.adopt(pairs)


def register_material_panel(panel):
    """A stack hands over its INTERFACE and its own read/write paths, not a panel:
    a session holds several stacks, and one panel each would stack N property
    pages beside each other while a person only ever wants the one the selected
    mesh is wearing. The host draws that single panel; this is the door the
    generated product can see."""
    from . import material_panel
    material_panel.register_stack(panel)


def unregister_material_panel(panel):
    from . import material_panel
    material_panel.unregister_stack(panel)


def viewpoint():
    """The object a compositor tree reads the view it is finishing through, with the name of the custom property
    that carries the view's projection (see :mod:`viewpoint`). A stack builds the nodes that read it; which view it
    stands for -- the viewport being looked through, else the scene camera -- is the host's to keep current."""
    from . import viewpoint as viewpoint_module
    return viewpoint_module.viewpoint()


# ---------------------------------------------------------------------------
# What the derived-state scheduler runs
# ---------------------------------------------------------------------------
def apply_vertex_stages(objects):
    return sum(stage(objects=objects) for stage in VERTEX_STAGES)


def push_camera_stages(objects=None, camera=None):
    return sum(stage(objects=objects, camera=camera) for stage in CAMERA_STAGES)


def apply_rig_stages(objects=None):
    return sum(stage(objects=objects) for stage in RIG_STAGES)


def rebuild_plugin_data(purge):
    """The load pass. ``purge``: a file just opened or the stacks just loaded, so every piece
    of the plugin's own data in memory is dropped first -- this session's, or an older build's
    that a file carries -- and then every material a stack claims is compiled from its record.
    Without it only the materials this session has not compiled yet (appended from another
    file) are. The compiled materials' environment answers are the current scene's, and are
    stamped so. One stack failing leaves the others compiling; the failures are raised
    together afterwards. A twin that left its session and came back with this file (pasted,
    appended, saved without the plugin) is handed back to its material before anything is
    dropped or compiled (see :mod:`linked_twins`). Returns ``(dropped, compiled)``."""
    dropped = 0
    failures = _linked_twins.return_strays()
    if purge:
        _linked_twins.release()
        dropped = _plugin_data.purge() + sum(drop() for drop in PLUGIN_PURGES)
    compiled = []
    for compile_all in MATERIAL_COMPILES:
        try:
            compiled.extend(compile_all())
        except Exception as error:
            failures.append(str(error))
    state = capability_state(bpy.context.scene)
    for material in compiled:
        material[CAPABILITY_STATE_PROPERTY] = state
    declared = {name for provider in LEVEL_GLOBALS for name in provider()}
    for scene in bpy.data.scenes:
        kept = sorted(name for name in scene.keys() if name in declared)
        if scene.library is None and kept:
            failures.append("[material] scene {0} keeps a level's globals on itself ({1} of them, e.g. {2}): a level's "
                            "globals, texel blocks and grading live on the level's world -- import the level "
                            "again".format(scene.name, len(kept), kept[0]))
    if failures:
        raise RuntimeError("; ".join(failures))
    return dropped, len(compiled)


def _state_value(value):
    if isinstance(value, bpy.types.ID):
        return value.name_full
    if isinstance(value, bpy.types.bpy_struct):
        return None
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(value))
    if hasattr(value, "__len__") and not isinstance(value, str):
        return tuple(round(float(component), 6) for component in value)
    return value


def _world_state(world):
    """The world as the environment answers sample it: each node's own settings and
    input values, and the links. A node's generic editor state (location, selection)
    is not read by any answer, so nudging the world in the editor rebuilds nothing."""
    if world is None or world.node_tree is None:
        return world.name_full if world is not None else None
    generic = set(bpy.types.Node.bl_rna.properties.keys()) - {"mute"}
    nodes = []
    for node in world.node_tree.nodes:
        settings = tuple((prop.identifier, _state_value(getattr(node, prop.identifier, None)))
                         for prop in node.bl_rna.properties if prop.identifier not in generic)
        inputs = tuple((socket.identifier, _state_value(getattr(socket, "default_value", None)))
                       for socket in node.inputs)
        nodes.append((node.name, node.bl_idname, settings, inputs))
    links = [(link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier)
             for link in world.node_tree.links]
    return world.name_full, tuple(sorted(nodes, key=repr)), tuple(sorted(links))


def capability_state(scene):
    """Everything a material's environment answers read from the scene, as one
    comparable digest: the world's content (the environment is a snapshot of its
    nodes). Lights are not in it -- the stacks read them through the host's own light
    loop, live."""
    return hashlib.sha1(repr(_world_state(scene.world)).encode("utf-8")).hexdigest()


def rewire_capabilities(materials=None, force=False):
    """Re-answer the environment queries of every built material whose answers were
    built against another scene state than the current one (``force`` rewires them
    all). A stack returns False for a material it did not build, so asking all of
    them is safe and order-independent. Returns how many were claimed and rewired."""
    state = capability_state(bpy.context.scene)
    pool = list(bpy.data.materials) if materials is None else list(materials)
    rewired = 0
    for material in pool:
        if material is None or (not force and material.get(CAPABILITY_STATE_PROPERTY) == state):
            continue
        if any(rewire(material) for rewire in CAPABILITY_REWIRES):
            material[CAPABILITY_STATE_PROPERTY] = state
            rewired += 1
    return rewired


def apply_post_stages(scene, force=False):
    """Install onto a scene the post stage that grades what it holds.

    A session carries every deployed game's stacks, and a scene has ONE compositor
    tree, so the stage is the one whose own content is in the scene: each stage
    states which shading stacks' materials it grades (``grades``). A scene holding
    two stages' content has no single answer, and that is reported with nothing
    installed rather than letting whichever installs last win.

    Already installed is skipped by default: install() rebuilds the whole
    compositor tree and writes the view transform and the viewport's compositor
    switch back to shipped values, so re-running it on every import would take
    those away from the user each time (the stage's own inputs are the scene's and
    come back). ``force`` is the panel button that means start over. An installed
    stage still rebuilds its image chains when the output size moved: a bloom
    pyramid's level count and level sizes are the frame's. The tree is the
    plugin's own data, so after a file opens no stage is installed and this is
    what puts it back."""
    graded = [stage for stage in POST_STAGES if stage.grades(scene)]
    if len(graded) > 1:
        print("[material] !! the scene holds content of {0} post stages ({1}); a scene has ONE "
              "compositor tree, so none is installed".format(
                  len(graded), sorted(stage.post["group"] for stage in graded)))
        return []
    installed = []
    for stage in graded:
        if force or not stage.installed(scene):
            installed.append(stage.install(scene))
        else:
            stage.refresh_chains(scene)
    return installed


def remove_post_stages(scene):
    """Hand the scene back the compositor state it had before any stage was
    installed. Without this a load is one-way."""
    return [stage.uninstall(scene) for stage in POST_STAGES]


def post_stages_installed(scene):
    return [stage for stage in POST_STAGES if stage.installed(scene)]


def world_basis():
    """The source-world -> Blender-world basis every generated shading stack computes
    in, row-major (b = M @ u): the reflection plus the once-only top-level turn, off the
    one coordinate space the importer places everything with. A stack's kernel works in
    the SOURCE's world, not in each object's local frame -- the local frame is the world
    only for an object whose transform IS that turn, and a scene's placements each carry
    their own. The generator states the same matrix in its recipe (it builds templates
    with no plugin loaded); a stack checks it against this at registration."""
    from .coordinate import SPACE
    return (SPACE.root_rotation @ SPACE.matrix)[:3, :3].tolist()


def apply_post_inputs(scene, values):
    """Drive the host-side inputs of the post stage they belong to.

    ``values`` is keyed by the stage's own input names; each is the DIFFERENCE from
    identity, because an entry parameter's socket default is always zero and only a
    difference makes "nothing drives it" mean "no change" -- a session with a character
    and no scene has to land on identity. A value input's entry is its components; an
    image input's entry is its texels, one row of them, which the stage turns into the
    image its group samples.

    The stage is the one whose declared inputs are exactly the names supplied: a set that
    is any other stage's inputs, or a stage's inputs with one missing, is refused rather
    than part-written -- the missing one would silently fall back to identity and the
    picture would be quietly wrong with nothing to show for it.

    The inputs live on the installed stage, so a stage not installed yet is installed
    here first: an import states its grading before the derived-state pass would get
    round to installing the stage, and written into nothing the grading was lost."""
    supplied = set(values)
    owners = [stage for stage in POST_STAGES
              if set(stage.extra_inputs()) | set(stage.extra_images()) == supplied]
    if len(owners) != 1:
        raise KeyError("[material] {0} post stage(s) take exactly the inputs {1}; the stages take {2}".format(
            len(owners), sorted(supplied),
            {stage.post["group"]: sorted(stage.extra_inputs() + stage.extra_images()) for stage in POST_STAGES}))
    stage = owners[0]
    if not stage.installed(scene):
        stage.install(scene)
    written = stage.set_extra(scene, [values[name] for name in stage.extra_inputs()])
    images = {}
    for name in stage.extra_images():
        texels = [float(texel) for texel in values[name]]
        images[name] = (len(texels), 1, [channel for texel in texels for channel in (texel, texel, texel, 1.0)])
    return written + stage.set_images(scene, images)


def apply_level_resources(scene, values, payloads):
    """Apply everything one level states for every material: its engine globals and
    the texel blocks its stacks read (3D textures, texture arrays, data tables).

    ``values`` holds globals already in hand, keyed by the engine's own names;
    ``payloads`` are level-resources blobs (named globals plus named texel blocks, see
    :func:`_level_resources`). They are merged before anything is written -- the
    completeness rule below is about the level's whole state, and a level states it
    through more than one source (fog is static, the irradiance clipmaps and the
    reflection probes follow the camera). A name two sources both state with
    different values is refused.

    Returns ``(written, unclaimed)``: the names written, and the supplied names no
    registered stack reads."""
    merged = {name: tuple(value) for name, value in values.items()}
    blocks = {}
    for payload in payloads:
        stated_globals, stated_blocks = _level_resources(payload)
        for name, value in stated_globals.items():
            if name in merged and merged[name] != value:
                raise ValueError("[material] level global {0} stated twice: {1} and {2}".format(
                    name, merged[name], value))
            merged[name] = value
        for name, block in stated_blocks.items():
            if name in blocks:
                raise ValueError("[material] level block {0} stated twice".format(name))
            blocks[name] = block
    written, unclaimed = _apply_level_globals(scene, merged)
    filled, unread = _apply_level_images(scene, blocks)
    return written + filled, unclaimed + unread


def level_world(scene):
    """The world a level's shared state lives on: its engine globals, the texel blocks its stacks read
    (bound under :data:`LEVEL_IMAGES_PROPERTY`) and the grading it states. The stacks read the globals
    through view-layer attributes, which Blender looks up on the view layer, then the scene, then the
    scene's world -- so every scene drawn through the level's world reads the same state: the level's own
    file, and a shot that links the level's objects and world."""
    if scene.world is None:
        raise RuntimeError("[material] scene {0} has no world: a level's globals, texel blocks and grading "
                           "live on the level's world".format(scene.name))
    return scene.world


#: The custom property on a level's world that holds the images its texel blocks were filled into, keyed by
#: image name: the level's content, so the world is their user and they go wherever the world is linked.
LEVEL_IMAGES_PROPERTY = "ruri_level_images"


def _apply_level_globals(scene, values):
    """Write one level's engine globals where the stacks read them live: on the level's world
    (see :func:`level_world`).

    ``values`` is keyed by the engine's own global names, each the level's value in
    the source's own convention. What lands on the world is the DIFFERENCE from the
    stack's declared default, because the graph reads ``property + default`` and an
    unwritten property reads zero.

    Completeness is judged per statement: what one thing states (the level's phase, or
    the volume stack the camera stands in) comes whole or not at all. A stack that is
    handed some of one statement's globals but not all is refused: the missing ones would
    silently stay at the recipe default and the picture would be quietly wrong. A stack
    handed none of a statement's globals is not that statement's consumer here (another
    game's stack, or a display stage that states its volumes and no level) and is left
    alone. Two stacks declaring different defaults, or different statements, for one name
    cannot share a property, so that is refused too."""
    bases = {}
    statements = {}
    for provider in LEVEL_GLOBALS:
        declared = provider()
        for statement in sorted({kind for kind, _ in declared.values()}):
            named = sorted(name for name, (kind, _) in declared.items() if kind == statement)
            supplied = [name for name in named if name in values]
            if supplied and len(supplied) != len(named):
                raise KeyError("[material] a stack reads {0} globals {1}; {2} not supplied".format(
                    statement, named, sorted(name for name in named if name not in values)))
        for name, (statement, base) in declared.items():
            known = bases.setdefault(name, list(base))
            if known != list(base):
                raise ValueError("[material] level global {0} has two defaults: {1} and {2}".format(
                    name, known, list(base)))
            if statements.setdefault(name, statement) != statement:
                raise ValueError("[material] level global {0} is stated by both {1} and {2}".format(
                    name, statements[name], statement))
    rows = {}
    for name, value in values.items():
        base = bases.get(name)
        if base is None:
            continue
        if len(value) != len(base):
            raise ValueError("[material] level global {0} has {1} components, the stack reads {2}".format(
                name, len(value), len(base)))
        rows[name] = [float(component) - component_base for component, component_base in zip(value, base)]
    if rows:
        world = level_world(scene)
        for name, row in rows.items():
            world[name] = row
        world.update_tag()
    return list(rows), sorted(name for name in values if name not in bases)


_LEVEL_RESOURCES_MAGIC = 0x52564C52
_LEVEL_RESOURCES_VERSION = 3
_TEXEL_FORMATS = {1: "<f2", 2: "u1", 3: "<f4"}
_BLOCK_KINDS = {0: "volume", 1: "array", 2: "tiles"}


def _level_size(size, mip):
    """One dimension of a block at one mip, as the writer counts it: never below one texel,
    except for a block that states none at all -- a table of no records holds none anywhere."""
    return 0 if size == 0 else max(1, size >> mip)


def _level_resources(payload):
    """``(globals, blocks)`` from one level-resources blob: little-endian ``"RLVR",
    version``, then named four-component globals, then named texel blocks -- a kind
    byte (0 = 3D texture, 1 = 2D texture array), ``width, height, depth, mips,
    channels``, a format byte (1 = half, 2 = unorm8, 3 = float) and the texels of every
    mip from the largest, each x fastest, then y, then z. A mip halves a volume's depth
    with its width and height and keeps an array's slice count; a uniform array of
    four-component rows is a one-slice array. A tile set (kind 2) states its tile count
    as depth and then each tile as its own ``width, height`` and texels, first mip only.

    A block comes back as ``(kind, levels)``: one float32 array per mip shaped (depth,
    height, width, channels), unorm8 already divided by 255; a tile set's levels are its
    tiles, each shaped (height, width, channels)."""
    import struct
    import numpy
    view = memoryview(payload)
    magic, version = struct.unpack_from("<II", view, 0)
    if magic != _LEVEL_RESOURCES_MAGIC:
        raise ValueError("[material] not a level-resources payload")
    if version != _LEVEL_RESOURCES_VERSION:
        raise ValueError("[material] level-resources version {0}; this host reads {1}".format(
            version, _LEVEL_RESOURCES_VERSION))
    cursor = 8

    def name():
        nonlocal cursor
        length = struct.unpack_from("<H", view, cursor)[0]
        text = bytes(view[cursor + 2:cursor + 2 + length]).decode("utf-8")
        cursor += 2 + length
        return text

    stated_globals = {}
    count = struct.unpack_from("<i", view, cursor)[0]
    cursor += 4
    for _ in range(count):
        key = name()
        stated_globals[key] = struct.unpack_from("<4f", view, cursor)
        cursor += 16
    blocks = {}
    count = struct.unpack_from("<i", view, cursor)[0]
    cursor += 4
    for _ in range(count):
        key = name()
        kind = _BLOCK_KINDS[view[cursor]]
        width, height, depth, mips, channels = struct.unpack_from("<5i", view, cursor + 1)
        texel_format = _TEXEL_FORMATS[view[cursor + 21]]
        cursor += 22
        if kind == "tiles":
            tiles = []
            for _tile in range(depth):
                tile_width, tile_height = struct.unpack_from("<2i", view, cursor)
                cursor += 8
                texels = numpy.frombuffer(view, dtype=texel_format, count=tile_width * tile_height * channels,
                                          offset=cursor)
                cursor += texels.nbytes
                texels = texels.astype(numpy.float32)
                if texel_format == "u1":
                    texels /= 255.0
                tiles.append(texels.reshape(tile_height, tile_width, channels))
            blocks[key] = (kind, tiles)
            continue
        levels = []
        for mip in range(mips):
            level_width, level_height = _level_size(width, mip), _level_size(height, mip)
            level_depth = _level_size(depth, mip) if kind == "volume" else depth
            total = level_width * level_height * level_depth * channels
            texels = numpy.frombuffer(view, dtype=texel_format, count=total, offset=cursor)
            cursor += texels.nbytes
            texels = texels.astype(numpy.float32)
            if texel_format == "u1":
                texels /= 255.0
            levels.append(texels.reshape(level_depth, level_height, level_width, channels))
        blocks[key] = (kind, levels)
    if cursor != len(view):
        raise ValueError("[material] level-resources payload has {0} trailing bytes".format(len(view) - cursor))
    return stated_globals, blocks


def volume_image(layout):
    """The one image a stack reads a level resource through, laid out as ``layout``
    says (a generated product's level-image row). The only place such an image is
    made: a stack asks for it when it builds a template, the level writes into it.
    A size that no longer matches is rebuilt -- the stack's coordinates are the
    layout's constants, and sampling another size would shift every tile.

    Every channel is data, the fourth included (the irradiance clipmaps keep sky
    visibility there), so the alpha is channel-packed: read as straight alpha, the
    colour channels are premultiplied on upload and come back zero wherever the
    fourth channel is. A data table holds the single-precision values the source
    reads as they are (positions, matrix rows), so it goes to the GPU at full
    precision; the atlases hold half and 8-bit texels and keep the half upload.

    A texture whose layout states no size takes the size of the texture a level states: it is made
    at one texel and the level scales it (the stack samples it through a plain image node, so no
    coordinate depends on that size).

    Made unfilled it is the plugin's own placeholder (runtime, never written); a level filling it
    makes it the document's content."""
    if layout["kind"] == "tiles":
        return _tiles_image(layout)
    size = layout["size"] if layout["kind"] == "table" else layout.get("atlas")
    image = bpy.data.images.get(layout["image"])
    if image is not None and size is not None and (int(image.size[0]), int(image.size[1])) != tuple(
            int(value) for value in size):
        bpy.data.images.remove(image)
        image = None
    if image is None:
        width, height = (int(value) for value in size) if size is not None else (1, 1)
        float_buffer = layout["format"] in ("HALF", "FLOAT")
        image = _plugin_data.born(bpy.data.images.new(layout["image"], width, height, alpha=True,
                                                      float_buffer=float_buffer))
        image.colorspace_settings.name = "Non-Color"
        image.file_format = "OPEN_EXR" if float_buffer else "PNG"
    if image.alpha_mode != "CHANNEL_PACKED":
        image.alpha_mode = "CHANNEL_PACKED"
    full_precision = layout["format"] == "FLOAT"
    if image.use_half_precision == full_precision:
        image.use_half_precision = not full_precision
    return image


def _tiles_image(layout):
    """The one image a stack reads a tile set through: a UDIM image whose tile 1001 + i holds
    texture i at its own size, sampled with the host's own mips and filtering. Its values are data
    (linear already), channel-packed like every level image."""
    image = bpy.data.images.get(layout["image"])
    if image is not None and image.source != "TILED":
        bpy.data.images.remove(image)
        image = None
    if image is None:
        float_buffer = layout["format"] in ("HALF", "FLOAT")
        image = _plugin_data.born(bpy.data.images.new(layout["image"], 1, 1, alpha=True, float_buffer=float_buffer,
                                                      tiled=True))
        image.colorspace_settings.name = "Non-Color"
    if image.alpha_mode != "CHANNEL_PACKED":
        image.alpha_mode = "CHANNEL_PACKED"
    return image


def _write_tiles(image, tiles):
    """Lay a tile set into its UDIM image: tile i as UDIM 1001 + i at its own size, its first
    stored row at the bottom (Blender's pixel order, and the order the source stores rows in).
    A tile takes its pixels only from a file, so each goes through a throwaway half-float OpenEXR
    -- written top row first, as the format stores it -- the image reloads them as its tiles and
    packs them, and the files go. Tiles packed by an earlier write are dropped first: a packed
    tile re-packs from the file it was packed from, and that file is already gone. A level with no
    tiles leaves the image as it is: nothing reads it."""
    import shutil
    import tempfile
    import numpy
    import OpenImageIO as oiio
    if not tiles:
        return
    directory = tempfile.mkdtemp()
    try:
        stem = os.path.join(directory, "tiles")
        for index, texels in enumerate(tiles):
            height, width, channels = texels.shape
            pixels = numpy.zeros((height, width, 4), dtype=numpy.float32)
            pixels[:, :, :channels] = texels
            path = "{0}.{1}.exr".format(stem, 1001 + index)
            output = oiio.ImageOutput.create(path)
            if output is None or not output.open(path, oiio.ImageSpec(width, height, 4, "half")):
                raise RuntimeError("[material] cannot write tile {0}: {1}".format(path, oiio.geterror()))
            output.write_image(numpy.ascontiguousarray(pixels[::-1]))
            output.close()
        if image.packed_files:
            image.unpack(method="REMOVE")
        wanted = {1001 + index for index in range(len(tiles))}
        for tile in list(image.tiles):
            if tile.number not in wanted:
                image.tiles.remove(tile)
        for number in sorted(wanted):
            if image.tiles.get(number) is None:
                image.tiles.new(tile_number=number)
        image.source = "TILED"
        image.filepath = stem + ".<UDIM>.exr"
        image.reload()
        image.pack()
        image.filepath_raw = "//textures/" + image.name + ".<UDIM>.exr"
        _plugin_data.content(image)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _address(index, count, mode):
    """One texel index under a sampler address mode (the same rule the stacks lower)."""
    if mode == "WRAP":
        return index % count
    if mode == "MIRROR":
        period = index % (2 * count)
        return period if period < count else 2 * count - 1 - period
    if mode == "MIRRORONCE":
        folded = -index - 1 if index < 0 else index
        return min(folded, count - 1)
    return min(max(index, 0), count - 1)


def _volume_pixels(levels, layout):
    """A 3D texture (one mip) laid out as the stack reads it: slice z at grid cell
    (z % columns, z // columns), row 0 at the bottom (Blender's own pixel order), each
    cell ringed by ``pad`` texels holding the neighbours the stack's in-slice address
    mode would reach."""
    import numpy
    if len(levels) != 1:
        raise ValueError("[material] volume {0}: {1} mips stated, the atlas holds one".format(
            layout["image"], len(levels)))
    texels = levels[0]
    width, height, depth = (int(value) for value in layout["size"])
    if texels.shape[:3] != (depth, height, width):
        raise ValueError("[material] volume {0}: texels {1} against a {2}x{3}x{4} layout".format(
            layout["image"], texels.shape, width, height, depth))
    columns, pad = int(layout["columns"]), int(layout["pad"])
    atlas_width, atlas_height = (int(value) for value in layout["atlas"])
    mode_u, mode_v = layout["address"]
    xs = [_address(i, width, mode_u) for i in range(-pad, width + pad)]
    ys = [_address(j, height, mode_v) for j in range(-pad, height + pad)]
    ringed = texels[:, ys][:, :, xs]
    channels = texels.shape[3]
    pixels = numpy.zeros((atlas_height, atlas_width, 4), dtype=numpy.float32)
    tile_width, tile_height = width + 2 * pad, height + 2 * pad
    for z in range(depth):
        row, column = divmod(z, columns)
        pixels[row * tile_height:(row + 1) * tile_height,
               column * tile_width:(column + 1) * tile_width, :channels] = ringed[z]
    return pixels


def _array_pixels(levels, layout):
    """A 2D texture array with its mips laid out as the stack reads it: slice s in the
    tile at (s % columns, s // columns), row 0 at the bottom; inside a tile mip 0 sits
    at the tile's origin and mip m >= 1 at (width, height - 2 * (height >> m)), each at
    its own resolution with its first stored row at the bottom. Slices past the ones
    stated stay zero."""
    import numpy
    width, height = (int(value) for value in layout["size"])
    slices, mips, columns = int(layout["slices"]), int(layout["mips"]), int(layout["columns"])
    tile_width, tile_height = (int(value) for value in layout["tile"])
    atlas_width, atlas_height = (int(value) for value in layout["atlas"])
    if len(levels) != mips:
        raise ValueError("[material] array {0}: {1} mips stated, the atlas holds {2}".format(
            layout["image"], len(levels), mips))
    stated = levels[0].shape[0]
    if stated > slices:
        raise ValueError("[material] array {0}: {1} slices stated, the atlas holds {2}".format(
            layout["image"], stated, slices))
    pixels = numpy.zeros((atlas_height, atlas_width, 4), dtype=numpy.float32)
    for mip, texels in enumerate(levels):
        level_width, level_height = width >> mip, height >> mip
        if texels.shape[:3] != (stated, level_height, level_width):
            raise ValueError("[material] array {0}: mip {1} is {2}, the layout says {3}x{4}x{5}".format(
                layout["image"], mip, texels.shape[:3], level_width, level_height, stated))
        origin_x = width if mip > 0 else 0
        origin_y = height - 2 * level_height if mip > 0 else 0
        channels = texels.shape[3]
        for index in range(stated):
            row, column = divmod(index, columns)
            x = column * tile_width + origin_x
            y = row * tile_height + origin_y
            pixels[y:y + level_height, x:x + level_width, :channels] = texels[index]
    return pixels


def _texture_pixels(levels, layout):
    """A 2D texture as the stack reads it: one image, row 0 at the bottom. It is stated
    as a one-slice, one-mip array, at the layout's size or, for a layout stating none, at
    its own."""
    import numpy
    width, height = ((int(value) for value in layout["size"]) if "size" in layout
                     else (int(levels[0].shape[2]), int(levels[0].shape[1])))
    if len(levels) != 1 or levels[0].shape[:3] != (1, height, width):
        raise ValueError("[material] texture {0}: stated {1}, the layout says one {2}x{3} image".format(
            layout["image"], [level.shape for level in levels], width, height))
    texels = levels[0][0]
    pixels = numpy.zeros((height, width, 4), dtype=numpy.float32)
    pixels[:, :, :texels.shape[2]] = texels
    return pixels


def _table_pixels(levels, layout):
    """A uniform struct array as the stack reads it: element i on row i from the
    bottom, its four-component fields left to right. A source states the elements it has;
    the rows past them stay zero (nothing indexes them), and more than the stack holds is
    refused."""
    import numpy
    fields, length = (int(value) for value in layout["size"])
    if (len(levels) != 1 or levels[0].ndim != 4 or levels[0].shape[0] != 1 or levels[0].shape[2:] != (fields, 4)
            or levels[0].shape[1] > length):
        raise ValueError("[material] table {0}: stated {1}, the layout holds {2} rows of {3} vectors".format(
            layout["image"], [level.shape for level in levels], length, fields))
    pixels = numpy.zeros((length, fields, 4), dtype=numpy.float32)
    pixels[:levels[0].shape[1]] = levels[0][0]
    return pixels


_LEVEL_PIXELS = {"texture": _texture_pixels, "volume": _volume_pixels, "array": _array_pixels,
                 "table": _table_pixels}


def _apply_level_images(scene, blocks):
    """Fill the image behind every stated block some registered stack reads. Two
    stacks laying one resource out differently cannot share its image, so that is
    refused. A texture array a stack reads one image per slice (``slice`` layouts)
    comes as a tile set, tile i being slice i; a level stating more slices than the
    stack reads is refused, since the stack clamps a slice index it has no image for.
    A texture made at its level's own size gets its ``_TexelSize`` global written
    alongside, when a stack reads one. Returns ``(filled, unread)``."""
    layouts = {}
    for provider in LEVEL_IMAGES:
        for name, layout in provider().items():
            known = layouts.setdefault(name, layout)
            if known != layout:
                raise ValueError("[material] level image {0} has two layouts".format(name))
    slices = {}
    for layout in layouts.values():
        if layout["kind"] == "slice":
            slices.setdefault(layout["array"], {})[int(layout["index"])] = layout
    filled = []
    images = []
    texel_sizes = {}
    for name, (kind, levels) in blocks.items():
        if name in slices:
            if kind != "tiles":
                raise ValueError("[material] level image {0}: stated as a {1}, read one image per slice".format(
                    name, kind))
            if len(levels) > len(slices[name]):
                raise ValueError("[material] level image {0}: {1} slices stated, the stacks read {2}".format(
                    name, len(levels), len(slices[name])))
            for index, layout in sorted(slices[name].items()):
                if index >= len(levels):
                    continue
                image = volume_image(layout)
                _fill_image(image, _tile_pixels(levels[index]))
                images.append(image)
            filled.append(name)
            continue
        layout = layouts.get(name)
        if layout is None:
            continue
        expected = layout["kind"] if layout["kind"] in ("volume", "tiles") else "array"
        if kind != expected:
            raise ValueError("[material] level image {0}: stated as a {1}, read as a {2}".format(
                name, kind, layout["kind"]))
        image = volume_image(layout)
        images.append(image)
        if kind == "tiles":
            _write_tiles(image, levels)
            filled.append(name)
            continue
        pixels = _LEVEL_PIXELS[layout["kind"]](levels, layout)
        _fill_image(image, pixels)
        base = _texel_size_base(name + "_TexelSize") if layout["kind"] == "texture" and "size" not in layout else None
        if base is not None:
            height, width = pixels.shape[:2]
            texel_sizes[name + "_TexelSize"] = [value - offset for value, offset in
                                                zip((1.0 / width, 1.0 / height, float(width), float(height)), base)]
        filled.append(name)
    if images or texel_sizes:
        world = level_world(scene)
        for key, row in texel_sizes.items():
            world[key] = row
        held = world.get(LEVEL_IMAGES_PROPERTY)
        bound = {key: held[key] for key in held.keys() if held[key] is not None} if held is not None else {}
        bound.update((image.name, image) for image in images)
        world[LEVEL_IMAGES_PROPERTY] = bound
        world.update_tag()
    return filled, sorted(name for name in blocks if name not in layouts and name not in slices)


def _fill_image(image, pixels):
    """Write a (height, width, 4) texel array into ``image``, first row at the bottom, scaling
    the image to it first when a level states its own size, and pack it: from here on it holds
    the level's data, the document's content."""
    if (int(image.size[0]), int(image.size[1])) != (pixels.shape[1], pixels.shape[0]):
        image.scale(pixels.shape[1], pixels.shape[0])
    image.pixels.foreach_set(pixels.ravel())
    image.pack()
    _plugin_data.content(image)


def _tile_pixels(texels):
    """One tile of a tile set, shaped (height, width, channels), as RGBA with its first stored
    row at the bottom (Blender's pixel order and the order the source stores rows in)."""
    import numpy
    height, width, channels = texels.shape
    pixels = numpy.zeros((height, width, 4), dtype=numpy.float32)
    pixels[:, :, :channels] = texels
    return pixels


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def _image_from_bytes(data, name, extension=None):
    """Load a stated texture's bytes through Blender's OWN image loader, via a
    throwaway temp file -- measured pixel-identical to decoding them in python
    and 30x faster.

    The image is PACKED into the file and the temp deleted, so nothing on disk
    outlives the call. A content-addressed cache directory instead traded away
    both things a texture has to keep: IDENTITY (the file referenced a hash where
    the game says a name) and PORTABILITY (a document whose textures live in a
    machine-local folder cannot be moved, handed over or archived).

    The name is stamped in three places because they are three different records:
    the datablock name (what the UI lists), ``filepath_raw`` (what a relink
    resolves) and the PACKED FILE's own path (what File > Unpack writes). Unpack
    reads the last of those, which is why setting only the first two used to
    leave every unpacked texture called tmpXXXXXXXX.img."""
    import tempfile

    temp = tempfile.NamedTemporaryFile(suffix=".img", delete=False)
    try:
        temp.write(data)
        temp.close()
        try:
            image = bpy.data.images.load(temp.name)
        except RuntimeError:
            return None
        image.name = name
        target = "//textures/" + name + (extension or _image_extension(image))
        # Pack FIRST -- it reads the file while it is still on disk -- and only
        # then rewrite both path records to the game's own name.
        image.pack()
        if image.packed_files:
            image.packed_files[0].filepath = target
        image.filepath_raw = target
    finally:
        os.unlink(temp.name)
    _disable_alpha_interpretation(image)
    return image


#: Extension per image FORMAT, not per image: see _image_extension.
_EXTENSION_BY_FORMAT = {}


def _image_extension(image):
    """The extension Blender itself uses for this image's DETECTED format, read
    out of Blender's own format table rather than a map here -- a container this
    importer has never seen still unpacks under a truthful name.

    Asked ONCE PER FORMAT. Reading that table means writing the scene's render
    settings twice, and a scene write runs Blender's update machinery: measured
    27ms per call and 4.5s over one level's 164 textures, for an answer that
    depends on nothing but the format string."""
    detected = image.file_format or ""
    remembered = _EXTENSION_BY_FORMAT.get(detected)
    if remembered is None:
        _EXTENSION_BY_FORMAT[detected] = remembered = _read_image_extension(image, detected)
    return remembered


def _read_image_extension(image, detected):
    fallback = "." + (detected or "img").lower()
    render = getattr(getattr(bpy.context, "scene", None), "render", None)
    if render is None:
        return fallback
    previous = render.image_settings.file_format
    try:
        render.image_settings.file_format = image.file_format
        return render.file_extension or fallback
    except (TypeError, ValueError):
        # Blender can READ formats it cannot render to; those have no entry.
        return fallback
    finally:
        render.image_settings.file_format = previous


def _disable_alpha_interpretation(image):
    """These shaders routinely repurpose a texture's fourth channel for something
    other than opacity (ambient occlusion, an emission mask, a packed channel).
    Blender's default treats it as real transparency regardless of whether the
    graph ever wires the Alpha output anywhere, which reads as an incorrectly
    see-through material."""
    try:
        image.alpha_mode = "NONE"
    except Exception:
        pass


def _enable_alpha_channel(image):
    """Give an image back its alpha, for the one case that asks for it: a
    material that WIRES that channel as opacity is the material saying it IS
    opacity there. While the interpretation is off the Alpha output reads 1.0
    everywhere, so a cutout keeps the whole card."""
    try:
        image.alpha_mode = "CHANNEL_PACKED"
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------
class MaterialBuilder:
    """Builds the materials of one statement, once each.

    A generated stack is handed this object and the stated material; the two
    names it reaches for -- ``options`` and ``_load_image`` -- are what its own
    manifest says its host provides."""

    def __init__(self, statement, options):
        self.statement = statement
        self.options = dict(options or {})
        self._by_key = {}
        self._images = {}

    def shader_display_name(self, props):
        """The shader a material names, as the shader asset calls itself. A stack
        claims by this and a report prints it."""
        return props.shader_name or None

    def build(self, key):
        """The Blender material for one stated material key, built once."""
        if key in self._by_key:
            return self._by_key[key]
        stated = self.statement.materials.get(key)
        if stated is None:
            made = bpy.data.materials.new(key or "Material")
            self._by_key[key] = made
            _announce(made)
            return made
        made = self._build(stated)
        made[SOURCE_KEY_PROPERTY] = str(key)
        made[CAPABILITY_STATE_PROPERTY] = capability_state(bpy.context.scene)
        self._by_key[key] = made
        _announce(made)
        return made

    def _load_image(self, guid, non_color=False):
        """The image behind one texture key, loaded once.

        Named ``_load_image`` because that is the name a generated product's
        manifest states for it; the key is called a guid for the same reason."""
        if not guid:
            return None
        cached = self._images.get(guid)
        if cached is None:
            texture = self.statement.textures.get(guid)
            if texture is None or not texture.bytes:
                return None
            cached = _image_from_bytes(texture.image, texture.name or guid,
                                       "." + texture.container if texture.container else None)
            if cached is None:
                return None
            self._images[guid] = cached
        # Colour space is the TEXTURE's own fact, never the slot's: one image is
        # routinely bound in a normal slot and an emission slot at once, and the
        # setting lives on the shared datablock where the last writer wins. Only
        # what the asset itself declares is honoured, and it is marked so nothing
        # downstream re-infers one from a slot.
        texture = self.statement.textures.get(guid)
        want = None
        if texture is not None:
            want = "sRGB" if texture.srgb else "Non-Color"
            cached[COLORSPACE_STATED_PROPERTY] = True
            # The texture's own sampler state is, like its colour space, the ASSET's
            # fact: a slot that samples it through the texture's own sampler reads it
            # back from here rather than assuming one.
            cached[SAMPLING_STATED_PROPERTY] = {
                "wrap_u": texture.wrap_u, "wrap_v": texture.wrap_v, "filter": texture.filter}
        elif non_color:
            want = "Non-Color"
        if want is not None and cached.colorspace_settings.name != want:
            try:
                cached.colorspace_settings.name = want
            except Exception:
                pass
        return cached

    def _build(self, props):
        name = props.name or "Material"
        # Generated stacks first, each declining with None. Every material is
        # asked, engine-standard ones included -- they simply resolve to a name
        # no stack claims, which is the intended route rather than a gap: the
        # Principled fallback below IS the faithful build for a standard shader.
        if self.options.get("game_shaders"):
            for provider in GRAPH_PROVIDERS:
                try:
                    claimed = provider(self, props)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    print("[material] !! provider {0} EXCEPTION on '{1}' -- falling back "
                          "to Principled, the graph is NOT the game shader".format(
                              getattr(provider, "__module__", provider), name))
                    claimed = None
                if claimed is not None:
                    return claimed
            print("[material] UNCLAIMED '{0}' shader={1} -- Principled fallback".format(
                name, props.shader_name or "<none>"))
        return self._principled(props)

    def _principled(self, props):
        roles = props.roles
        # Rewrite a same-named material in place: a mesh binds its material by
        # name, and a second datablock would take a .001 suffix and leave two.
        material = bpy.data.materials.get(name_of(props)) or bpy.data.materials.new(name_of(props))
        material.use_nodes = True
        tree = material.node_tree
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (600, 0)
        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (200, 0)
        tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
        claimed = set()

        base_node = self._wire_base_colour(tree, bsdf, roles, claimed)
        self._wire_opacity(material, tree, bsdf, roles, base_node)
        self._wire_normal(tree, bsdf, roles, claimed)
        self._wire_scalars(tree, bsdf, roles, claimed)
        self._wire_emission(tree, bsdf, roles, claimed)
        _orphan_textures(self, tree, props, claimed)
        return material

    def _wire_base_colour(self, tree, bsdf, roles, claimed):
        """The material's own tint MULTIPLIES the texture exactly as these
        shaders do; a neutral white tint adds no node."""
        tint = roles.colors.get("base_color")
        base = roles.first("base_color")
        if base is None:
            if tint is not None:
                bsdf.inputs["Base Color"].default_value = tuple(tint)
            return None
        claimed.add(base.name)
        image = self._load_image(base.guid)
        if image is None:
            return None
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-400, 100)
        node.label = base.name
        socket = node.outputs["Color"]
        if tint is not None and tuple(tint[:3]) != (1.0, 1.0, 1.0):
            mix = tree.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.clamp_factor = False
            mix.location = (-100, 100)
            mix.inputs["Factor"].default_value = 1.0
            tree.links.new(socket, mix.inputs["A"])
            mix.inputs["B"].default_value = (float(tint[0]), float(tint[1]), float(tint[2]), 1.0)
            socket = mix.outputs["Result"]
        tree.links.new(socket, bsdf.inputs["Base Color"])
        return node

    def _wire_opacity(self, material, tree, bsdf, roles, base_node):
        """The blend state is material DATA, and a material that states none is
        opaque -- as it is in every pipeline. Its base map's alpha is opacity only
        when the material says it blends or clips, or a layer names that channel
        opacity: an opaque material routinely packs something else there (a mask,
        a metallic channel, nothing at all), and reading that as opacity is what
        turned whole floors invisible.

        And an alpha TEST is not alpha blending: a material stating a cutoff asks
        for every texel to be all there or not there at all, and handing its soft
        alpha to a stochastic blend loses most of it to dithering noise. The test
        keeps a texel whose alpha REACHES the cutoff -- the engines' own
        clip(alpha - cutoff) -- so a zero cutoff keeps every texel. A cutoff a
        material merely carries while it does not clip is not a test."""
        transparent = roles.transparent
        clipped = roles.clipped
        opacity_texture, opacity_channel = roles.with_channel("opacity")
        base = roles.first("base_color")
        named = (opacity_texture is not None and opacity_texture is base
                 and opacity_channel == _ALPHA_CHANNEL)
        if base_node is not None and (transparent or clipped or named):
            cutoff = roles.floats.get("alpha_cutoff")
            _enable_alpha_channel(base_node.image)
            socket = base_node.outputs["Alpha"]
            if clipped and cutoff is not None:
                below = tree.nodes.new("ShaderNodeMath")
                below.operation = "LESS_THAN"
                below.location = (-250, -150)
                below.label = "alpha cutoff {0:.3f}".format(float(cutoff))
                below.inputs[1].default_value = float(cutoff)
                tree.links.new(socket, below.inputs[0])
                kept = tree.nodes.new("ShaderNodeMath")
                kept.operation = "SUBTRACT"
                kept.location = (-100, -150)
                kept.inputs[0].default_value = 1.0
                tree.links.new(below.outputs["Value"], kept.inputs[1])
                socket = kept.outputs["Value"]
            tree.links.new(socket, bsdf.inputs["Alpha"])
        try:
            material.surface_render_method = "BLENDED" if transparent else "DITHERED"
        except Exception:
            pass

    def _wire_normal(self, tree, bsdf, roles, claimed):
        normal = roles.first("normal")
        if normal is None:
            return
        claimed.add(normal.name)
        strength = float(roles.floats.get("normal_strength", 1.0) or 1.0)
        image = self._load_image(normal.guid, non_color=True)
        if image is None:
            return
        if normal.encoding == "hair_split":
            _wire_hair_split_normal(tree, bsdf, image, strength, (-400, -250))
            return
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-400, -250)
        node.label = normal.name
        mapping = tree.nodes.new("ShaderNodeNormalMap")
        mapping.location = (-100, -250)
        mapping.inputs["Strength"].default_value = strength
        tree.links.new(node.outputs["Color"], mapping.inputs["Color"])
        tree.links.new(mapping.outputs["Normal"], bsdf.inputs["Normal"])

    def _wire_scalars(self, tree, bsdf, roles, claimed):
        """Every texture the layers give a channel role to, each on its own
        separator; the first to state metallic or roughness feeds the BSDF. With
        no packed map, the material's own numbers ARE the truth."""
        metallic_wired = False
        roughness_wired = False
        for index, packed in enumerate(roles.packed()):
            claimed.add(packed.name)
            # ALPHA IS NOT COLOUR MANAGED. Reading channel 3 says nothing about
            # how the RGB is to be read, so a map used only for its alpha must
            # not drag the image to Non-Color: it is routinely the SAME picture
            # as the base colour (one title states the diffuse under two names in
            # 1500 of 1663 measured materials), the setting lives on the shared
            # datablock, and the last writer wins -- so the whole game renders
            # dark with nothing anywhere to show for it.
            reads_colour = any(channel != _ALPHA_CHANNEL
                               for channel in packed.channels.values())
            image = self._load_image(packed.guid, non_color=reads_colour)
            if image is None:
                continue
            channels = dict(packed.channels)
            if metallic_wired:
                channels.pop("metallic", None)
            if roughness_wired:
                channels.pop("roughness", None)
                channels.pop("smoothness", None)
            got_metallic, got_roughness = _wire_packed(
                tree, bsdf, image, packed.name, channels, (-400, -420 - index * 320))
            metallic_wired = metallic_wired or got_metallic
            roughness_wired = roughness_wired or got_roughness
        if not metallic_wired and roles.floats.get("metallic") is not None:
            bsdf.inputs["Metallic"].default_value = float(roles.floats.get("metallic"))
        if roughness_wired:
            return
        if roles.floats.get("roughness") is not None:
            bsdf.inputs["Roughness"].default_value = float(roles.floats.get("roughness"))
        elif roles.floats.get("smoothness") is not None:
            bsdf.inputs["Roughness"].default_value = 1.0 - float(roles.floats.get("smoothness"))

    def _wire_emission(self, tree, bsdf, roles, claimed):
        """Emission is the map times its colour factor: a BLACK emission colour
        means emission OFF even with a map bound, and skipping the multiply made
        every such material glow at full map brightness. A colour alone, when it
        is not black, is emission with no map."""
        emission = roles.first("emission")
        colour = roles.colors.get("emission")
        if "Emission Color" not in bsdf.inputs:
            return
        if emission is not None:
            claimed.add(emission.name)
            image = self._load_image(emission.guid)
            if image is None:
                return
            node = tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            node.location = (-400, -750)
            node.label = emission.name
            socket = node.outputs["Color"]
            if colour is not None and tuple(colour[:3]) != (1.0, 1.0, 1.0):
                mix = tree.nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MULTIPLY"
                mix.clamp_factor = False
                mix.location = (-100, -750)
                mix.inputs["Factor"].default_value = 1.0
                tree.links.new(socket, mix.inputs["A"])
                mix.inputs["B"].default_value = (float(colour[0]), float(colour[1]),
                                                 float(colour[2]), 1.0)
                socket = mix.outputs["Result"]
            tree.links.new(socket, bsdf.inputs["Emission Color"])
            bsdf.inputs["Emission Strength"].default_value = 1.0
            return
        if colour is not None and tuple(colour[:3]) != (0.0, 0.0, 0.0):
            bsdf.inputs["Emission Color"].default_value = (
                float(colour[0]), float(colour[1]), float(colour[2]), 1.0)
            bsdf.inputs["Emission Strength"].default_value = 1.0


def name_of(props):
    return props.name or "Material"


def _announce(*datablocks):
    """Tell the derived-state scheduler these datablocks just arrived. Imported
    inside the call: that module reads this one's points, and a module-level
    cycle would hand whichever loaded first a half-built other."""
    from . import derived_state
    derived_state.announce(*datablocks)


def _orphan_textures(builder, tree, props, claimed, origin=(-1100, 400)):
    """Put EVERY texture the material carries that nothing above claimed into the
    graph as an unconnected image node, inside a frame labelled with the shader.

    A shader's property names are whatever its author typed, and there is no rule
    that finds them all. Dropping the rest silently leaves a material that looks
    fully built while most of its content is missing, and the only clue -- the
    shader's name -- is a console line long since scrolled away. So they land as
    islands: the frame says which shader this was, each node is labelled with the
    property name the game gave it, and nothing is connected, because guessing a
    connection is how a normal map ends up in Base Color."""
    frame = tree.nodes.new("NodeFrame")
    frame.label = _ORPHAN_FRAME_LABEL.format(props.shader_name or "<none stated>")
    frame.shrink = True
    # An image a claimed slot already placed is not an orphan under another name:
    # a converter that states a slot's part beside the slot's own name gives the
    # same image two keys on purpose.
    claimed_keys = {props.textures.get(name) for name in claimed if name}
    left = [(name, key) for name, key in sorted(props.textures.items())
            if key and name not in claimed and key not in claimed_keys]
    for index, (name, key) in enumerate(left):
        image = builder._load_image(key)
        if image is None:
            continue
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.label = name
        node.parent = frame
        node.location = (origin[0], origin[1] - index * 300)


def _wire_packed(tree, bsdf, image, label, channels, location):
    """A texture whose channels carry scalar roles: metallic and roughness go
    straight to the BSDF, smoothness through 1 - x; occlusion, specular, opacity
    and height have no Principled socket and stay on the sheet, the node labelled
    with what they are."""
    x, y = location
    node = tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    node.location = (x, y)
    node.label = "{0}: {1}".format(label, ", ".join(
        "{0}={1}".format(role, _CHANNEL_NAMES[channel])
        for role, channel in sorted(channels.items()) if 0 <= channel < 4))
    separate = tree.nodes.new("ShaderNodeSeparateColor")
    separate.location = (x + 300, y)
    tree.links.new(node.outputs["Color"], separate.inputs["Color"])
    if _ALPHA_CHANNEL in channels.values():
        _enable_alpha_channel(image)
    outputs = {0: separate.outputs["Red"], 1: separate.outputs["Green"],
               2: separate.outputs["Blue"], 3: node.outputs["Alpha"]}
    metallic = channels.get("metallic")
    if metallic in outputs:
        tree.links.new(outputs[metallic], bsdf.inputs["Metallic"])
    roughness = channels.get("roughness")
    smoothness = channels.get("smoothness")
    if roughness in outputs:
        tree.links.new(outputs[roughness], bsdf.inputs["Roughness"])
    elif smoothness in outputs:
        invert = tree.nodes.new("ShaderNodeMath")
        invert.operation = "SUBTRACT"
        invert.inputs[0].default_value = 1.0
        invert.location = (x + 300, y - 180)
        tree.links.new(outputs[smoothness], invert.inputs[1])
        tree.links.new(invert.outputs["Value"], bsdf.inputs["Roughness"])
    return "metallic" in channels, "roughness" in channels or "smoothness" in channels


def _wire_hair_split_normal(tree, bsdf, image, bump_scale, location):
    """A split normal map that is NOT a standard two-channel normal map --
    ground-truthed instruction-by-instruction against the real compiled shader::

        _491 = R*2-1 ;  _493 = G*2-1                   (no alpha multiply: the
                                                        alpha here is the SPEC
                                                        normal's Y)
        _501 = max(sqrt(1 - min(dot(xy,xy), 1)), 1e-16) <- from the UNSCALED xy
        _507 = _491 * scale ;  _508 = _493 * scale      <- scale hits xy only
        N = normalize(_501*normal + _507*tangent + _508*bitangent)

    The hemisphere reconstruction uses the UNSCALED x/y and the scale is applied
    afterwards -- the same order the skin, fur and effect shaders use, with no
    per-part difference. Reconstructing Z from the SCALED x/y collapses Z to ~0
    once the scale exceeds 1, because 1 - scaled^2 goes negative and clamps; the
    two orders agree only at scale == 1, which is why it went unnoticed.

    B and A pack a SEPARATE specular-highlight normal, left unwired: the
    Principled BSDF has exactly one Normal input shared by diffuse and specular,
    and routing a genuinely different specular normal would need a fully custom
    anisotropic graph rather than a property-linking pass.

    Built from raw maths nodes, then fed back through a Normal Map node purely to
    reuse Blender's own tangent-space transform -- there is no node that accepts
    an already-tangent-space vector -- so the decoded vector is re-encoded to
    0..1 first and that node's own decode reconstructs exactly it."""
    x, y = location
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.location = (x, y)
    texture.label = "Split normal (RG diffuse, BA specular, specular unused)"

    separate = tree.nodes.new("ShaderNodeSeparateColor")
    separate.location = (x + 260, y)
    tree.links.new(texture.outputs["Color"], separate.inputs["Color"])

    raw = tree.nodes.new("ShaderNodeCombineXYZ")
    raw.location = (x + 460, y)
    tree.links.new(separate.outputs["Red"], raw.inputs["X"])
    tree.links.new(separate.outputs["Green"], raw.inputs["Y"])

    unit = tree.nodes.new("ShaderNodeVectorMath")
    unit.operation = "MULTIPLY_ADD"
    unit.location = (x + 660, y)
    unit.inputs[1].default_value = (2.0, 2.0, 0.0)
    unit.inputs[2].default_value = (-1.0, -1.0, 0.0)
    tree.links.new(raw.outputs["Vector"], unit.inputs[0])

    scaled = tree.nodes.new("ShaderNodeVectorMath")
    scaled.operation = "SCALE"
    scaled.location = (x + 860, y)
    scaled.inputs["Scale"].default_value = bump_scale
    tree.links.new(unit.outputs["Vector"], scaled.inputs[0])

    # The dot product is over the UNSCALED xy. Z is 0 in that vector, so a self
    # dot product is exactly x*x + y*y.
    squared = tree.nodes.new("ShaderNodeVectorMath")
    squared.operation = "DOT_PRODUCT"
    squared.location = (x + 1060, y)
    tree.links.new(unit.outputs["Vector"], squared.inputs[0])
    tree.links.new(unit.outputs["Vector"], squared.inputs[1])

    capped = tree.nodes.new("ShaderNodeMath")
    capped.operation = "MINIMUM"
    capped.location = (x + 1160, y)
    capped.inputs[1].default_value = 1.0
    tree.links.new(squared.outputs["Value"], capped.inputs[0])

    remainder = tree.nodes.new("ShaderNodeMath")
    remainder.operation = "SUBTRACT"
    remainder.location = (x + 1260, y)
    remainder.inputs[0].default_value = 1.0
    tree.links.new(capped.outputs["Value"], remainder.inputs[1])

    floored = tree.nodes.new("ShaderNodeMath")
    floored.operation = "MAXIMUM"
    floored.location = (x + 1420, y)
    floored.inputs[1].default_value = 1e-4
    tree.links.new(remainder.outputs["Value"], floored.inputs[0])

    depth = tree.nodes.new("ShaderNodeMath")
    depth.operation = "SQRT"
    depth.location = (x + 1580, y)
    tree.links.new(floored.outputs["Value"], depth.inputs[0])

    depth_vector = tree.nodes.new("ShaderNodeCombineXYZ")
    depth_vector.location = (x + 1580, y - 160)
    tree.links.new(depth.outputs["Value"], depth_vector.inputs["Z"])

    whole = tree.nodes.new("ShaderNodeVectorMath")
    whole.operation = "ADD"
    whole.location = (x + 1780, y)
    tree.links.new(scaled.outputs["Vector"], whole.inputs[0])
    tree.links.new(depth_vector.outputs["Vector"], whole.inputs[1])

    encoded = tree.nodes.new("ShaderNodeVectorMath")
    encoded.operation = "MULTIPLY_ADD"
    encoded.location = (x + 1980, y)
    encoded.inputs[1].default_value = (0.5, 0.5, 0.5)
    encoded.inputs[2].default_value = (0.5, 0.5, 0.5)
    tree.links.new(whole.outputs["Vector"], encoded.inputs[0])

    mapping = tree.nodes.new("ShaderNodeNormalMap")
    mapping.location = (x + 2180, y)
    mapping.inputs["Strength"].default_value = 1.0
    tree.links.new(encoded.outputs["Vector"], mapping.inputs["Color"])
    tree.links.new(mapping.outputs["Normal"], bsdf.inputs["Normal"])
