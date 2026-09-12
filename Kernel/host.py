"""The port: everything the kernel asks of the application it is running inside.

One driver implements :class:`Host` and binds it. From then on the kernel talks
to that object and to nothing else application-shaped, which is what lets the
same import pipeline, the same material planning and the same browser serve
Blender and Substance Painter without either knowing the other exists.

The surface is deliberately small. Anything that turned out to be *two ways of
doing the same thing* -- where a texture cache lives, which image containers can
be decoded, how a long job is run without freezing the UI -- is here. Anything
that is a fact about the game data rather than about the host is NOT here; it
lives in the kernel or the data layer, stated once.

WHAT A HOST CAN DO is declared as capabilities rather than asked by name. A
kernel that branches on ``host.name == ...`` has quietly grown a per-host table
of behaviour; one that asks ``"animation" in host.capabilities`` has a
declaration each driver answers for itself, and a new host answers it by
existing. No capability name is a host name.
"""

from __future__ import annotations

import abc

#: The bound driver IS process state -- reloading this module during development
#: would unbind it while the application it names is still very much running, and
#: every module reloaded after this one would then fail asking for its host.
HOLDS_PROCESS_STATE = True

#: A host that materialises a skeleton as a first-class object the user can pose.
#: Painter bakes the bind pose into the geometry instead and has none, so a tab
#: that writes onto a rig (secondary motion, a retarget) is absent there.
SKELETON = "skeleton"
#: A host with an animation surface: actions, curves, a playhead. A tab that
#: plays or bakes a performance needs it.
ANIMATION = "animation"
#: A host with morph targets / blend shapes, which a facial expression library
#: drives.
MORPH_TARGETS = "morph_targets"
#: A host that materialises a scene as a HIERARCHY of many separate objects,
#: each with its own transform. Painter's project is one mesh file, so a scene
#: window there is not the same thing under a different name -- it is a merge.
SCENE_GRAPH = "scene_graph"
#: A host whose scene is a compositing/view-transform pipeline the plugin can
#: install a chain into. Painter's display settings are a fixed set of choices,
#: not a graph, so the two are not the same thing under different names.
COMPOSITOR = "compositor"
#: A host whose materials are NODE GRAPHS the plugin builds and can share as
#: reusable groups. Whether to rebuild a game's own shading stack at all, and
#: where its template groups live, are questions only such a host has: Painter's
#: material IS the ported shader, applied to every texture set, with nothing to
#: choose and nowhere to put a template.
NODE_MATERIALS = "node_materials"
#: A host whose texture inputs are files on disk, which makes a bake cache -- and
#: an option to invalidate it -- meaningful.
TEXTURE_CACHE = "texture_cache"
#: A host that groups surfaces into fixed-resolution texture sets.
TEXTURE_SETS = "texture_sets"
#: A host whose viewport display (environment map, tone mapping, colour LUT) is
#: settable by the plugin, and which the ported shader states requirements for.
DISPLAY_SETTINGS = "display_settings"

# A capability is declared here when something ASKS it. The set that used to sit
# beside these -- skeleton, animation, morph targets, node materials, mesh-file
# entry, stored tangents -- existed only to gate per-data-class import switches
# that have since been deleted (a model without its textures or its skeleton is
# not a lighter import, it is a broken one). With their questions gone they were
# constants nobody read, so they are gone too; whatever asks next declares what
# it needs in the same change.

DEBUG = "debug"
INFO = "info"
WARNING = "warning"
ERROR = "error"


class Host(abc.ABC):
    """One application, as the kernel sees it.

    Every member is abstract: a driver that forgets one cannot be instantiated,
    which is the difference between a missing capability being a startup error
    and it being a silent ``None`` three stages into an import.
    """

    #: Folder name of this driver under ``Host/`` -- also the name of the
    #: per-host subfolder a generated shader stack is projected into (see
    #: :mod:`Kernel.shaderstack`). One string, two uses, no mapping table.
    name = ""

    #: The set of :data:`SKELETON`-style constants this application supports.
    capabilities = frozenset()

    # -- diagnostics -------------------------------------------------------
    @abc.abstractmethod
    def log(self, level, message):
        """Write one line where this application's users look for messages."""

    # -- where things live -------------------------------------------------
    @abc.abstractmethod
    def workspace_dir(self):
        """Root for everything this toolchain generates, or None to let
        ``RuriRipperPyBridge.runtime.workspace`` pick the per-user data folder.

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

    # -- capability details ------------------------------------------------
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

    @abc.abstractmethod
    def selected_rig(self, context):
        """The skeleton the user has in front of them right now, or None.

        Read two ways and both need the same answer: a control that writes ONTO
        a rig is greyed out with a reason while there is none, and the command
        behind it needs the rig itself. A host with no rigs answers None, and
        those controls are absent there anyway."""

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
    def import_packages(self, context, packages, options=None, report=None,
                        resolved=None):
        """Materialise what a game said one thing is made of.

        This is the ONE place the two hosts' import pipelines are named, and it
        is deliberately the only one: a panel resolves a selection to
        :class:`Kernel.app.loading.Packages` -- the CABs to read, plus whatever
        the game stated is inside them -- and hands it over. Blender builds a
        skeleton, meshes and node materials; Painter writes one self-contained
        mesh file and wires its texture sets. Neither is a lesser version of the
        other, and neither is a branch in the panel that asked.

        ``resolved`` is an already-resolved dependency closure when the caller
        had one (a cast of fifty resolves ONE closure and materialises against
        it); the host resolves its own when given none. ``report`` collects
        lines for the user. Returns :class:`Kernel.app.loading.Built`."""

    @abc.abstractmethod
    def clear_scene(self, context):
        """Empty the document before an import puts something new in it -- what
        the browser's "Reset Scene" means.

        Only ever called on a host that answers :data:`SCENE_GRAPH`: a project
        that IS one mesh has nothing to clear, and the button that would ask for
        it is absent there rather than doing nothing."""

    @abc.abstractmethod
    def load_display_stage(self, context, stage, options):
        """Put one of a game's own display stages into the document, as the game
        stated it (:mod:`Kernel.app.staging`). Returns the lines to word.

        Only ever called on a host that answers :data:`SCENE_GRAPH`: a stage is a
        sun, an ambient and a set of art loaded AROUND what is already there, and
        a project that IS one mesh has no "already there" to load around. The GAME
        resolves its assets into the shared targets; which of them this application
        has somewhere to put is answered here."""

    @abc.abstractmethod
    def write_secondary_motion(self, context, rig, reading):
        """Write a model's own hair/cloth/accessory chains onto ``rig``, as the
        game stated them (:mod:`Kernel.app.rigging`). Returns the lines to word,
        or None when this application has nothing that can hold them.

        Only ever called on a host that answers :data:`SKELETON`. The GAME reads
        -- which settings a model carries is its fact -- and the host writes,
        because which solver holds them and what it calls each parameter is a
        fact about the application. Neither side learns the other's words."""

    @abc.abstractmethod
    def rig_memory(self, rig):
        """A mutable mapping of what the plugin remembers ON one rig -- which
        head a character was built with, which skeleton it is.

        Only ever reached on a host that answers :data:`SKELETON`. It is the
        rig itself that must remember: a later session opens the document and
        the panel state is gone, while the character is still there."""

    @abc.abstractmethod
    def rig_rest(self, context, rig):
        """``[{"name", "rest"}]`` -- every bone of this rig that carries a
        source-space rest local, under the SOURCE's own bone names.

        What anything solving a performance elsewhere has to be told about the
        rig it is solving onto. Only ever reached on a host that answers
        :data:`SKELETON`."""

    @abc.abstractmethod
    def bake_bone_poses(self, context, rig, source_names, frame_count, payload,
                        name, into=None):
        """Key a block of per-frame source-space locals onto this rig's bones.

        ``payload`` is float32, ``frame_count`` x ``len(source_names)`` x 10
        (position, quaternion, scale). Returns how many bones were posed. Only
        ever reached on a host that answers :data:`ANIMATION`."""

    @abc.abstractmethod
    def rig_named(self, rig_name, context=None):
        """The rig this document knows by that name, or None.

        A panel remembers WHICH rig it is pointed at, and a name is the only
        form of that a panel can hold: the object itself belongs to the
        application. Only ever reached on a host that answers
        :data:`SKELETON`."""

    @abc.abstractmethod
    def frame_rate(self, context):
        """Frames per second of the document's own timeline. Only ever reached
        on a host that answers :data:`ANIMATION` or :data:`MORPH_TARGETS`."""

    @abc.abstractmethod
    def set_frame_range(self, context, start, end):
        """Aim the playhead at what was just built -- importing a performance IS
        the request to see it. Only ever reached on a host with a timeline."""

    @abc.abstractmethod
    def face_bindings(self, context, rig, table):
        """What a ctrl-driven face TABLE actually reaches on this rig.

        Returns ``{"ctrls": {ctrl: label}, "via": [(count, how)],
        "missing_bones": [...], "rest_error": float or None, "reason": str}``.

        Only ever called on a host that answers :data:`MORPH_TARGETS`. The GAME
        states the table -- which ctrl moves which bone by how much, and the
        naming rule for a mesh that bakes a ctrl as a blend shape -- and this
        answers what of it lands, because whether a bone or a key exists is a
        fact about this document and nothing else."""

    @abc.abstractmethod
    def drive_face(self, context, rig, table, weights):
        """Drive every bound ctrl to its weight and every other one to zero, so a
        pose REPLACES the face rather than piling onto the last one.

        Only ever called on a host that answers :data:`MORPH_TARGETS`."""

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
        (action, slot) whose facial channels this replaces.

        Only ever called on a host that answers :data:`MORPH_TARGETS`."""

    @abc.abstractmethod
    def drive_blend_shapes(self, context, rig, weights):
        """Set blend shapes on the meshes ``rig`` drives, as
        ``{mesh name: {index: value}}``. Returns (meshes touched, warnings).

        Only ever called on a host that answers :data:`MORPH_TARGETS`. WHAT to
        set and to what is a game's arithmetic; which object carries a mesh and
        which of its keys is index N is this application's answer, and indices
        are stated in the SOURCE's own order."""

    @abc.abstractmethod
    def import_clips(self, context, clip_cab, clip_guids, database, options,
                     display_names=None, activate=False):
        """Build the named AnimationClips onto the rig the user has in front of
        them, out of an already-resolved closure. Returns (built, lines).

        Only ever called on a host that answers :data:`ANIMATION`. A clip is a
        performance measured against a rest pose, so a host with no rigs has
        nowhere to put one -- and the rows that hold only clips are reported as
        ``display_names`` renames the built performances -- a catalog row is the
        readable identity, while the clip is named after an internal controller
        state. ``activate`` puts the first one on the rig rather than only
        building it."""


_BOUND = []


def bind(host):
    """Install the driver for this process. Called once, by the driver itself,
    before anything else in the kernel runs."""
    if not isinstance(host, Host):
        raise TypeError("a host driver must derive from Kernel.host.Host, got "
                        + type(host).__name__)
    missing = sorted(name for name in ("name",) if not getattr(host, name, ""))
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


def supports(capability):
    return capability in current().capabilities


def log(message, level=INFO):
    current().log(level, message)
