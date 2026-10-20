"""The port: everything the kernel asks of the application it is running inside.

One driver implements :class:`Host` and binds it. From then on the kernel talks
to that object and to nothing else application-shaped, which is what lets the
same import pipeline, the same material planning and the same browser serve
Blender and Substance Painter without either knowing the other exists.

WHAT A HOST CAN DO is the set of protocols its driver derives from, and nothing
else: a driver is a :class:`Host` and whichever of :class:`SceneGraph`,
:class:`Compositor`, :class:`Rig`, :class:`Timeline`, :class:`Retarget`,
:class:`MorphTargets`, :class:`NodeMaterials`, :class:`TextureCache`,
:class:`TextureSets`, :class:`DisplaySettings` and :class:`RegionPicking` it can actually answer.
:attr:`Host.capabilities` reads that back with ``isinstance``. There is no second list of capability names to
keep in step with the methods, and no method a host has to carry just to raise:
a host that cannot pose a skeleton is not a :class:`Rig`, and every option,
command, tab and section that names ``Rig`` is ABSENT there rather than broken.
No protocol is named after a host, and a kernel that branches on
``host.name == ...`` has quietly grown a per-host table of behaviour.
"""

from __future__ import annotations

import abc

#: The bound driver IS process state -- reloading this module during development
#: would unbind it while the application it names is still very much running, and
#: every module reloaded after this one would then fail asking for its host.
HOLDS_PROCESS_STATE = True

DEBUG = "debug"
INFO = "info"
WARNING = "warning"
ERROR = "error"


class Host(abc.ABC):
    """The application itself: where it puts things, how it says things, and the
    one thing every host does -- materialise a statement."""

    #: Folder name of this driver under ``Host/`` -- also the name of the
    #: per-host subfolder a generated shader stack is projected into (see
    #: :mod:`Kernel.shaderstack`). One string, two uses, no mapping table.
    name = ""

    #: The basis this application reads geometry and transforms in, by the name the reader
    #: converts to (``Kernel.statement.BLENDER``, ``GLTF``, ...). Stated by the driver, never
    #: defaulted: a statement asked in the wrong basis is geometry that is quietly turned.
    basis = ""

    @property
    def capabilities(self):
        """The protocols this application answers, read off the driver itself."""
        return frozenset(protocol for protocol in PROTOCOLS if isinstance(self, protocol))

    # -- diagnostics -------------------------------------------------------
    @abc.abstractmethod
    def log(self, level, message):
        """Write one line where this application's users look for messages."""

    # -- where things live -------------------------------------------------
    @abc.abstractmethod
    def workspace_dir(self):
        """Root for everything this toolchain generates, or None to let
        ``Kernel.bridge.workspace`` pick the per-user data folder.

        Never inside the checkout: a checkout is replaced wholesale by a pull.
        """

    @abc.abstractmethod
    def preset_dir(self):
        """Where THIS application keeps the preset files its users author, or
        None when it has no such place and the workspace is the only home.

        A generated runtime and a user's own choices are not the same kind of
        file: the first is an installed dependency this toolchain can rebuild at
        will, the second is the user's data and belongs where that application
        already shows them their presets."""

    @abc.abstractmethod
    def bin_dir(self):
        """The built folder that directly contains ``Ruri.RipperHook.dll``.
        The one genuinely machine-specific path, stored wherever this
        application stores its settings."""

    @abc.abstractmethod
    def bin_dir_hint(self):
        """One sentence telling THIS application's user where to set it. The
        shared bridge has no idea where this host keeps that setting and says
        so by quoting this."""

    # -- what it decodes and speaks ------------------------------------------
    @abc.abstractmethod
    def texture_containers(self):
        """Image containers this application can decode, as lowercase
        extensions, or () to take every texture in whatever container the game
        itself shipped.

        The bridge converts exactly the textures that fall outside this and
        hands the rest over byte-identical, so declaring more than the truth
        costs correctness and declaring less costs encode time."""

    @abc.abstractmethod
    def locale(self):
        """The language code this application's UI is running in, e.g.
        ``zh_CN``. A game's own localized names are joined through it, so
        switching the application's language switches every roster with nothing
        else reloaded."""

    # -- the application's own idioms --------------------------------------
    @abc.abstractmethod
    def absolute_path(self, path):
        """Resolve a path the user typed. Blender's fields accept its own
        ``//``-relative form, so "make this absolute" is not ``os.path.abspath``
        there and is exactly that everywhere else."""

    @abc.abstractmethod
    def schedule(self, seconds, call):
        """Run ``call`` once, ``seconds`` from now, on the main thread.

        What a debounced search box needs, and the one thing neither host can
        borrow from the other: Blender has ``bpy.app.timers``, Painter has Qt's.
        ``call`` returns a delay to run again, or None to stop."""

    @abc.abstractmethod
    def redraw(self):
        """Ask the application to repaint the plugin's surfaces. Immediate-mode
        in Blender (tag a redraw), a rebuild in Painter -- either way, what a
        panel says has changed and the screen has not caught up."""

    # -- panel state -------------------------------------------------------
    @abc.abstractmethod
    def register_state(self, name, schema, handlers, extra=None):
        """Materialise a panel's state declaration and file it under ``name``.

        Blender turns it into a PropertyGroup on the Scene, which is what gives
        the panel undo and per-.blend persistence; Painter into a plain value bag.
        A panel says WHICH fields it keeps (:mod:`Kernel.app.state`) and never
        where they are kept, which is the only reason one panel body can draw in
        both."""

    @abc.abstractmethod
    def unregister_state(self, name):
        """Drop what :meth:`register_state` filed under ``name``."""

    @abc.abstractmethod
    def panel_state(self, context, name):
        """The record filed under ``name``. The one way a panel body reaches its
        own state, and the reason it can be written without knowing whose."""

    # -- doing the work ----------------------------------------------------
    @abc.abstractmethod
    def materialise(self, context, statement, options, report=None):
        """Put what the reader stated into this document.

        ``statement`` is :class:`Kernel.statement.Statement` -- the nodes, meshes, skeletons,
        materials, textures and morphs of a selection, already in this host's basis. It is
        the ONE place the two hosts' import pipelines are named: every load reaches it through
        :func:`Kernel.app.loading.load`, whatever panel asked. Blender builds a rig, objects
        and node materials; Painter writes one self-contained mesh file and wires its texture
        sets. ``report`` collects lines for the user. Returns :class:`Kernel.statement.Built`."""


class SceneGraph(abc.ABC):
    """A host that materialises a scene as a HIERARCHY of separate objects, each
    with its own transform, inside a world. A project that IS one mesh file is not
    the same thing under a different name -- it is a merge."""

    @abc.abstractmethod
    def clear_scene(self, context):
        """Empty the document before an import puts something new in it -- what
        the browser's "Reset Scene" means."""

    @abc.abstractmethod
    def apply_renderer(self, context):
        """Put the document's renderer in the configuration the shading stacks are verified under,
        before a level is stood up in it: a startup scene carries whatever settings it was saved
        with, and a level shaded under those shows what no stack was verified to show."""

    @abc.abstractmethod
    def apply_environment(self, context, environment):
        """Stand the environment a level puts its viewer under up in the document,
        replacing any stood up before. ``environment`` is ``{"label", "ambient",
        "light"}``: ``ambient`` is ``{"label", "coefficients"}``, the sky irradiance as
        the source's shading stack samples it, nine spherical-harmonic coefficients per
        channel, stood up as the document's world; ``light`` is the level's main light
        as ``[(target, value)]`` over the :mod:`Kernel.app.staging` LIGHT_* targets.
        Returns how many already-built materials were re-answered against the world.

        Called BEFORE the level's content is built: a material samples the world
        that exists when it is built, and one built against a default world keeps
        reflecting that default."""

    @abc.abstractmethod
    def apply_level_resources(self, context, values, payloads):
        """Hand the shading stacks everything a level states for every material: the
        engine globals in ``values`` (keyed by the engine's own names, in the source's
        own convention) plus the level-resources ``payloads`` -- blobs of further named
        globals and named 3D textures (a level's baked lighting around a camera). All
        of it is applied as ONE state, because a stack is refused a partial set of its
        globals and a level states them through more than one source. A stack reads it
        live, so it may be applied before or after the level's content is built.
        Returns ``(written, unclaimed)``: the names written and the supplied names no
        stack reads. What this state shades is distance, height and light through a
        level."""

    @abc.abstractmethod
    def apply_box_lists(self, context, boxes, range_attribute, list_table):
        """Hand every object the level boxes that reach it, for the shading stacks that walk them inside
        the material where the source culls them per pixel before shading (the decal pass, the reflection
        probe binning). ``boxes`` lists them in the order the shading walks them -- the order of the
        records in the payload they belong to -- each as the column-major matrix taking the unit cube
        onto its box, in the SOURCE's world. Each object's run of box indices goes into the data table
        ``list_table``, and where the run starts and how long it is into the per-object global
        ``range_attribute``. Returns the lines to report."""

    @abc.abstractmethod
    def apply_medium(self, context, medium):
        """Stand a level's participating medium up in the document, replacing any stood up
        before, so the host integrates it the way the source integrates its fog, and keep its
        statement with the level so a document that holds the level holds the fog. ``medium``
        None is a phase that integrates no fog: the one stood up before comes out. Otherwise it is
        in the source's own world and units:

        ``label``            the volume it came from;
        ``range``            ``(start, end)`` view depths it is integrated over;
        ``grid``             ``(slices, tile_pixels, distribution)``: the source's integration
                             grid, its slices at ``(2^(z/distribution) - O) / B`` over the range;
        ``albedo``           the scattered fraction, per channel;
        ``density_scale``,   extinction ``density_scale * sum(density * 2^-max(-127, falloff *
        ``layers``           (y - height)))`` over ``(height, density, falloff)`` layers, grey;
        ``anisotropy``       the Henyey-Greenstein phase ``g``;
        ``near_fade``        the medium scales by ``clamp(distance past the start * near_fade)``;
        ``emission``         radiance it emits per metre;
        ``ambient_scale``,   the sky it scatters: ``max(dot(L1, (g * ray, 1)) * ambient_scale, 0)``
        ``ambient``          per channel, one ``(x, y, z, w)`` L1 row each, times the scattering;
        ``light``            the main light it scatters -- its own copy: ``direction`` toward the
                             light, ``color``, ``intensity``, and ``scale`` on what it scatters;
        ``punctual_shadows`` whether the lamps it scatters are shadowed in it.

        A lamp's own share of what it scatters is the volume factor its statement carries.
        Returns the lines to report."""

    @abc.abstractmethod
    def source_view_position(self, context):
        """Where the document is being looked at from, as a point in the SOURCE's
        world, or None when the document holds nothing to look at. Camera-centred
        level state is built around it, as the source builds it around its camera."""

    @abc.abstractmethod
    def load_display_stage(self, context, stage, options):
        """Put one of a game's own display stages into the document, as the game
        stated it (:mod:`Kernel.app.staging`). Returns the lines to word.

        A stage is a sun, an ambient and a set of art loaded AROUND what is
        already there. The GAME resolves its assets into the shared targets; which
        of them this application has somewhere to put is answered here."""


class Compositor(abc.ABC):
    """A host whose scene is a compositing/view-transform pipeline the plugin can
    install a chain into. Display settings that are a fixed set of choices are not
    the same thing under a different name."""

    @abc.abstractmethod
    def apply_post_inputs(self, context, values):
        """Drive the display chain's host-side inputs from a level's own data --
        the colour grading a scene states, keyed by the chain's own input names: a
        value input's components, or an image input's texels (one row of them).
        Returns how many inputs were written."""


class Rig(abc.ABC):
    """A host that materialises a skeleton as a first-class object the user can
    pose, and that can remember things on it. Painter bakes the bind pose into the
    geometry instead and is not one."""

    @abc.abstractmethod
    def selected_rig(self, context):
        """The skeleton the user has in front of them right now, or None.

        Read two ways and both need the same answer: a control that writes ONTO
        a rig is greyed out with a reason while there is none, and the command
        behind it needs the rig itself."""

    @abc.abstractmethod
    def rig_named(self, rig_name, context=None):
        """The rig this document knows by that name, or None.

        A panel remembers WHICH rig it is pointed at, and a name is the only
        form of that a panel can hold: the object itself belongs to the
        application."""

    @abc.abstractmethod
    def rig_paths(self, rig):
        """The transform paths this rig's bones ARE, as the source stated them --
        what a performance's curves are re-anchored onto, whatever the bones are
        called in this document today."""

    @abc.abstractmethod
    def rig_seed(self, rig):
        """The seed this rig was built from, or "" -- what a later question about this character
        (the face it wears) is asked with. Carried verbatim from the reader back to the reader."""

    @abc.abstractmethod
    def rig_avatar(self, rig):
        """The avatar this rig was built with, as the reader stated it, or "" --
        what a muscle-encoded performance is solved against. Carried verbatim from
        the reader back to the reader; this side never reads into it.

        It is the rig itself that remembers both: a later session opens the
        document and the panel state is gone, while the character is still
        there."""

    @abc.abstractmethod
    def rig_rest(self, context, rig):
        """``[{"name", "rest"}]`` -- every bone of this rig that carries a
        source-space rest local, under the SOURCE's own bone names.

        What anything solving a performance elsewhere has to be told about the
        rig it is solving onto."""

    @abc.abstractmethod
    def write_secondary_motion(self, context, rig, reading):
        """Write a model's own hair/cloth/accessory chains onto ``rig``, as the
        game stated them (:mod:`Kernel.app.rigging`). Returns the lines to word,
        or None when this application has nothing that can hold them.

        The GAME reads -- which settings a model carries is its fact -- and the
        host writes, because which solver holds them and what it calls each
        parameter is a fact about the application. Neither side learns the
        other's words."""


class Timeline(abc.ABC):
    """A host with an animation surface: actions, curves, a playhead."""

    @abc.abstractmethod
    def frame_rate(self, context):
        """Frames per second of the document's own timeline."""

    @abc.abstractmethod
    def set_frame_range(self, context, start, end):
        """Aim the playhead at what was just built -- importing a performance IS
        the request to see it."""

    @abc.abstractmethod
    def play(self, context, rig, clips, options, activate=False):
        """Key performances onto ``rig``. ``clips`` are what a statement carries, already
        re-anchored on this rig's own bones and solved against its avatar
        (:func:`Kernel.app.loading.perform`). ``activate`` puts the first one on the rig rather
        than only building it. Returns (landed, lines): by clip key, where that clip's
        performance landed -- a handle this host is handed back when more is written into the
        same performance -- and the lines to word."""

    @abc.abstractmethod
    def bake_bone_poses(self, context, rig, source_names, frame_count, payload,
                        name, into=None):
        """Key a block of per-frame source-space locals onto this rig's bones.

        ``payload`` is float32, ``frame_count`` x ``len(source_names)`` x 10
        (position, quaternion, scale). Returns how many bones were posed."""


class Retarget(abc.ABC):
    """A host that plays a clip written for another skeleton by carrying it across the bone tables its
    own add-ons keep. Also a :class:`Rig` and a :class:`Timeline`: the clip is keyed with the one
    ``play`` on a stand-in for the skeleton it was written for."""

    @abc.abstractmethod
    def stand_in_rig(self, context, statement, skeleton_key, options):
        """A rig for one skeleton of ``statement``, nothing drawn on it, until :meth:`discard_rig`."""

    @abc.abstractmethod
    def discard_rig(self, context, rig):
        """A stand-in rig out of the document."""

    @abc.abstractmethod
    def retarget(self, context, source_rig, rig, source_name, performances, activate=False):
        """The performances ``play`` landed on ``source_rig`` (by clip key), carried onto ``rig``;
        ``source_name`` is what the install the clips came from calls itself. Returns (landed, lines)."""


class MorphTargets(abc.ABC):
    """A host with morph targets, which a facial expression library drives."""

    @abc.abstractmethod
    def face_bindings(self, context, rig, table):
        """What a ctrl-driven face TABLE actually reaches on this rig.

        Returns ``{"ctrls": {ctrl: label}, "via": [(count, how)],
        "missing_bones": [...], "rest_error": float or None, "reason": str}``.

        The GAME states the table -- which ctrl moves which bone by how much, and
        the naming rule for a mesh that bakes a ctrl as a blend shape -- and this
        answers what of it lands, because whether a bone or a key exists is a
        fact about this document and nothing else."""

    @abc.abstractmethod
    def drive_face(self, context, rig, table, weights):
        """Drive every bound ctrl to its weight and every other one to zero, so a
        pose REPLACES the face rather than piling onto the last one."""

    @abc.abstractmethod
    def bake_face(self, context, rig, table, tracks, frames, fps, name,
                  into=None):
        """Bake one ctrl-driven animation as this host's own animation channels.

        ``tracks`` is ``{ctrl: [(time, value, in_slope, out_slope)]}`` -- the
        game's own keys, which a target that maps 1:1 onto one channel keeps
        unresampled. ``frames`` is that animation already SAMPLED per frame BY
        THE GAME, because several ctrls with independent key times sum into one
        bone and how a game evaluates its own curves between keys is its answer,
        never an interpolation the host invents. ``into`` is an existing
        (action, slot) whose facial channels this replaces."""

    @abc.abstractmethod
    def drive_blend_shapes(self, context, rig, weights):
        """Set blend shapes on the meshes ``rig`` drives, as
        ``{mesh name: {index: value}}``. Returns (meshes touched, warnings).

        WHAT to set and to what is a game's arithmetic; which object carries a
        mesh and which of its keys is index N is this application's answer, and
        indices are stated in the SOURCE's own order."""


class NodeMaterials(abc.ABC):
    """A host whose materials are NODE GRAPHS the plugin builds and can share as
    reusable groups -- which is what makes "rebuild the game's own shading stack"
    and "where do its templates live" questions that exist at all. Painter's
    material IS the ported shader, applied to every texture set."""


class TextureCache(abc.ABC):
    """A host whose texture inputs are files on disk, which makes a bake cache --
    and an option to invalidate it -- meaningful."""


class TextureSets(abc.ABC):
    """A host that groups surfaces into fixed-resolution texture sets."""


class DisplaySettings(abc.ABC):
    """A host whose viewport display -- environment map, tone mapping, colour LUT
    -- is a fixed set of choices the plugin sets, and which a ported shader states
    requirements for."""


class RegionPicking(abc.ABC):
    """A host whose user can drag a rectangle over a picture of the world: a map drawn in the panel,
    and a way to point into it that is larger than the panel itself. There is never more than one
    interaction: asking for another hands the one under way the new picture."""

    @abc.abstractmethod
    def pick_rectangle(self, context, picture, rectangle, done, snap=0.0):
        """Hand the user ``picture`` (:class:`Kernel.app.picture.Picture`) to drag a rectangle over,
        starting from ``rectangle`` -- world coordinates ``(min_x, min_z, max_x, max_z)``, or None --
        and, where the world is made of cells ``snap`` on a side, out to the cells it meets.
        ``done(context, rectangle)`` is called once with the rectangle they accepted and never when
        they cancel. Returns at once: the interaction outlives this call. An interaction already
        under way is handed all of this instead of a second one being made, and stays where it is
        unless ``context`` is a user's action in another place; ``context`` is None where the call
        answers a change of selection rather than a click."""

    @abc.abstractmethod
    def picking(self):
        """Whether an interaction is under way."""

    @abc.abstractmethod
    def stop_picking(self):
        """End the interaction under way, if there is one, as a cancel."""


#: Every protocol a driver can answer, in the order a diagnostics surface lists them.
PROTOCOLS = (SceneGraph, Compositor, Rig, Timeline, Retarget, MorphTargets, NodeMaterials,
             TextureCache, TextureSets, DisplaySettings, RegionPicking)

_BOUND = []


def bind(host):
    """Install the driver for this process. Called once, by the driver itself,
    before anything else in the kernel runs."""
    if not isinstance(host, Host):
        raise TypeError("a host driver must derive from Kernel.host.Host, got "
                        + type(host).__name__)
    missing = sorted(name for name in ("name", "basis") if not getattr(host, name, ""))
    if missing:
        raise ValueError("host driver declares no " + ", ".join(missing))
    _BOUND[:] = [host]
    return host


def current():
    """The bound driver. Raises rather than returning a stand-in: a kernel that
    can run with no host is a kernel with a second, invisible host."""
    if not _BOUND:
        raise RuntimeError(
            "no host driver is bound -- Host/<name>/__init__.py binds one at import, "
            "so reaching here means the kernel was imported outside a driver.")
    return _BOUND[0]


def bound():
    """True once a driver is installed -- for the handful of places that legitimately
    run before one is (a module body computing a constant, a reload guard)."""
    return bool(_BOUND)


def supports(protocol):
    return isinstance(current(), protocol)


def selected_rig(context):
    """The skeleton the user has in front of them, or None -- None as well on a host
    that is not a :class:`Rig`, where there is never one in front of anybody."""
    host = current()
    return host.selected_rig(context) if isinstance(host, Rig) else None


def log(message, level=INFO):
    current().log(level, message)
