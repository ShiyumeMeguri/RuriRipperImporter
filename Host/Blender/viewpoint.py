"""The view a compositor tree solves its screen-space passes against, kept as one object the trees read.

A compositor tree runs for every frame it finishes -- each redraw of a 3D viewport as well as a final render -- yet it
has no node that reads the view it is finishing: its camera nodes name the scene camera. A screen-space pass
reconstructs positions from the depth it is handed, so solved against the scene camera it reconstructs them wrong in
every viewport not looked through that camera.

The viewpoint stands in the viewer's place: an empty linked into no scene, so nothing draws, selects or renders it,
while every tree that names it still evaluates it. It is scratch (:mod:`scratch`): never written to
a .blend, made again in each session that asks for it. Its object transform is the view's camera-to-world transform; the
view's projection is kept as a custom property on it, sixteen values row by row, which a tree reads through drivers.
A final render is drawn for the scene camera rather than for any viewport -- the trees tell the two apart with Is
Viewport. With an interface the viewpoint follows the 3D viewport the user last moved; without one it follows the
scene camera, so a background run evaluates what a render would see.

Only compositor trees read it. A geometry tree that named it would be evaluated again on every step of a viewport
orbit, and with it the deformation below it -- the whole vertex stage of every character on each redraw -- so the
vertex stage reads the scene camera instead. Every write re-evaluates each tree that reads the viewpoint, so a write
happens only when what it carries changed, and a file without a viewpoint costs the poll nothing: the object is found
once per file load or undo step, never by scanning on a tick.

A program that has to follow the view per vertex (an outline) is evaluated by the GPU in the material's
displacement, which already has the view matrix of every view it draws; what it lacks is the projection and the
size in pixels, stated on the objects that read them as the view window (:func:`sync_windows`). Those change with a
lens, a viewport size or a camera view, never with an orbit, so they cost the orbit nothing.
"""

from __future__ import annotations

import traceback

import bpy
import mathutils

#: Marker the viewpoint is found by; its name is the user's to change.
MARKER = "ruri_viewpoint"
NAME = "Ruri Viewpoint"
PROJECTION = "ruri_view_projection"
#: How often the interface is asked whether a 3D viewport moved.
POLL_SECONDS = 1.0 / 30.0

_objects = []
_views = {}
_driver = [None]
_written = [None]
#: The view window last stated on the readers: (projection, size). A render running states its camera's.
_windows = [None]
#: The objects the view window is stated on, as (scene pointer, objects); None until asked again.
_readers = [None]
_rendering = [False]


class Viewpoint:
    """The viewpoint object and the name of the custom property that carries the view's projection."""

    __slots__ = ("object", "projection_property")

    def __init__(self, obj):
        self.object = obj
        self.projection_property = PROJECTION


def viewpoint():
    """The viewpoint, made on first use and brought up to date before it is handed out."""
    if not _objects or not _alive():
        _rescan()
    if not _objects:
        _objects.append(_make())
    _write_current(bpy.context.scene)
    return Viewpoint(_objects[0])


def sync(scene=None):
    """Bring every viewpoint some tree still reads to the view it stands for: the 3D viewport the user last moved,
    else the scene camera. A viewpoint no tree reads any more (the chain that read it runs in the final render only)
    is left alone -- every write is a depsgraph update. Returns whether anything was written."""
    if _objects and not _alive():
        _rescan()
    if not any(obj.users for obj in _objects):
        return False
    return _write_current(scene if scene is not None else bpy.context.scene)


def _write_current(scene):
    if scene is None:
        return False
    state = _viewport_state()
    if state is None:
        state = _camera_state(scene)
    if state is None or state == _written[0]:
        return False
    for obj in _objects:
        _write(obj, state)
    _written[0] = state
    return True


def sync_footprint(scene):
    """Keep the world size of one render output pixel on the scene, under every name a loaded stack reads it by:
    (orthographic term, perspective term per unit of view depth), from the render camera's projection at the output
    size -- two over the vertical scale over the output height, the perspective term for a perspective camera and
    the orthographic one otherwise. A final render is what a pipeline's screen-space pass is sized against; a
    viewport has no shader-side switch to the viewport's own size. Returns how many values were written."""
    from . import material_builder

    names = material_builder.render_footprint_attributes()
    state = _camera_state(scene) if names else None
    if state is None:
        return 0
    projection = _matrix(state[1])
    size = 2.0 / (projection[1][1] * state[2][1])
    footprint = (0.0, size, 0.0) if projection[3][3] < 0.5 else (size, 0.0, 0.0)
    written = 0
    for name in names:
        if tuple(scene.get(name, ())) != footprint:
            scene[name] = footprint
            written += 1
    return written


def sync_windows(scene=None):
    """Keep the projection of the view being drawn on every object that reads it, under every name a loaded stack
    reads it by (:func:`material_builder.view_window_attributes`): the window matrix column by column and its
    inverse, (near, far, orthographic) read off that matrix, and the view's size in pixels. The view is the 3D
    viewport the user last moved, else -- in a background run, or while a render runs -- the scene camera at the
    output size. The view matrix is not stated: the GPU has it for every view it draws. Orbiting, panning and zooming
    the viewport move only that matrix, so they write nothing here; a lens, a viewport size or a camera view change
    writes once.

    The values sit on the readers themselves, each followed by a shading-only tag -- writing the object's own
    colour is the one Python path to it (``rna_Object_internal_update_draw``). A scene property would need the
    scene tagged, and on a large level that re-evaluates everything that hangs off the scene (measured 330 ms on a
    9276-object level against 25 ms for twenty readers there, 1 ms on a lone character, nothing on a level with no
    reader). Returns whether anything was written."""
    from . import material_builder

    layouts = material_builder.view_window_attributes()
    scene = scene if scene is not None else bpy.context.scene
    if not layouts or scene is None:
        return False
    readers = _reader_objects(scene)
    if not readers:
        return False
    state = None if _rendering[0] else _viewport_state()
    if state is None:
        state = _camera_state(scene)
    if state is None:
        return False
    key = (state[1], state[2])
    if key == _windows[0]:
        return False
    columns, inverse_columns, clip, size = _window_values(state)
    try:
        for obj in readers:
            for layout in layouts:
                for name, column in zip(layout["columns"], columns):
                    obj[name] = column
                for name, column in zip(layout["inverse_columns"], inverse_columns):
                    obj[name] = column
                obj[layout["clip"]] = clip
                obj[layout["screen"]] = size
            obj.color = tuple(obj.color)
    except ReferenceError:
        invalidate_readers()
        return False
    _windows[0] = key
    return True


def invalidate_readers():
    """The objects reading the view window changed (an import built or removed outline shells): ask again, and
    state the window on the new set whatever it was before."""
    _readers[0] = None
    _windows[0] = None


def _reader_objects(scene):
    from . import material_builder

    cached = _readers[0]
    if cached is not None and cached[0] == scene.as_pointer():
        return cached[1]
    objects = material_builder.view_window_readers(scene)
    _readers[0] = (scene.as_pointer(), objects)
    _windows[0] = None
    return objects


def _window_values(state):
    """(columns, inverse columns, (near, far, orthographic), (width, height, 0)) of a view's window matrix, the
    clipping planes solved from the matrix itself so a camera view and a free viewport both answer from what is
    drawn."""
    _transform, projection, size = state
    window = _matrix(projection)
    inverse = window.inverted()
    columns = tuple(tuple(float(window[row][column]) for row in range(4)) for column in range(4))
    inverse_columns = tuple(tuple(float(inverse[row][column]) for row in range(4)) for column in range(4))
    if window[3][3] > 0.5:
        near = (window[2][3] + 1.0) / window[2][2]
        far = (window[2][3] - 1.0) / window[2][2]
        orthographic = 1.0
    else:
        near = window[2][3] / (window[2][2] - 1.0)
        far = window[2][3] / (window[2][2] + 1.0)
        orthographic = 0.0
    return columns, inverse_columns, (float(near), float(far), orthographic), (float(size[0]), float(size[1]), 0.0)


def _window_names():
    from . import material_builder

    names = []
    for layout in material_builder.view_window_attributes():
        names.extend(layout["columns"])
        names.extend(layout["inverse_columns"])
        names.extend((layout["clip"], layout["screen"]))
    return names


def _alive():
    try:
        return all(obj.get(MARKER) for obj in _objects)
    except ReferenceError:
        return False


def _rescan():
    from . import scratch
    _objects[:] = [scratch.mark(obj) for obj in bpy.data.objects if obj.get(MARKER)]
    _written[0] = None


def _make():
    from . import scratch
    obj = scratch.mark(bpy.data.objects.new(NAME, None))
    obj[MARKER] = 1
    _written[0] = None
    return obj


def _write(obj, state):
    transform, projection, _size = state
    obj.matrix_world = _matrix(transform)
    obj[PROJECTION] = [float(value) for value in projection]
    obj.update_tag()


def _flat(matrix):
    return tuple(float(value) for row in matrix for value in row)


def _matrix(flat):
    return mathutils.Matrix((flat[0:4], flat[4:8], flat[8:12], flat[12:16]))


def _render_screen(scene):
    render = scene.render
    scale = render.resolution_percentage / 100.0
    return float(render.resolution_x) * scale, float(render.resolution_y) * scale


def _viewport_state():
    """The state of the 3D viewport the user last moved: a viewport whose view changed since the last look takes
    over, and with none moving the one already followed stays, or the largest one when it is gone. A background run
    has none -- its window manager still holds the startup file's screens, but nothing is ever drawn through them."""
    manager = bpy.context.window_manager
    if bpy.app.background or manager is None:
        return None
    views = {}
    for window in manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((one for one in area.regions if one.type == "WINDOW"), None)
            view = getattr(area.spaces.active, "region_3d", None)
            if region is None or view is None or region.width <= 1 or region.height <= 1:
                continue
            views[area.as_pointer()] = (_flat(view.view_matrix.inverted()), _flat(view.window_matrix),
                                        (float(region.width), float(region.height)))
    if not views:
        _views.clear()
        _driver[0] = None
        return None
    moved = [key for key, state in views.items() if key in _views and _views[key] != state]
    if moved and _driver[0] not in moved:
        _driver[0] = moved[0]
    elif _driver[0] not in views:
        _driver[0] = max(views, key=lambda key: views[key][2][0] * views[key][2][1])
    _views.clear()
    _views.update(views)
    return views[_driver[0]]


def _camera_state(scene):
    camera = scene.camera
    if camera is None:
        return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = _render_screen(scene)
    render = scene.render
    projection = camera.calc_matrix_camera(depsgraph, x=int(round(width)), y=int(round(height)),
                                           scale_x=render.pixel_aspect_x, scale_y=render.pixel_aspect_y)
    return _flat(camera.evaluated_get(depsgraph).matrix_world), _flat(projection), (width, height)


def _poll():
    try:
        sync()
        sync_windows()
    except Exception:
        traceback.print_exc()
    return POLL_SECONDS


@bpy.app.handlers.persistent
def _on_data_replaced(*_args):
    # Opening a file or stepping undo replaces the data the cached objects and the written state describe.
    _rescan()
    invalidate_readers()


@bpy.app.handlers.persistent
def _on_render_start(scene, *_args):
    # A render is drawn for its camera at the output size, whatever viewport the user last moved.
    _rendering[0] = True
    sync_windows(scene)


@bpy.app.handlers.persistent
def _on_render_end(scene, *_args):
    _rendering[0] = False
    sync_windows(scene)


@bpy.app.handlers.persistent
def _on_save_pre(*_args):
    # The view window is this session's view, not the document's: it never goes into the file.
    names = _window_names()
    if names:
        for obj in bpy.data.objects:
            for name in names:
                if name in obj:
                    del obj[name]
    _windows[0] = None


@bpy.app.handlers.persistent
def _on_save_post(*_args):
    sync_windows()


_HANDLERS = (("load_post", _on_data_replaced), ("undo_post", _on_data_replaced), ("redo_post", _on_data_replaced),
             ("render_pre", _on_render_start), ("render_post", _on_render_end), ("render_cancel", _on_render_end),
             ("save_pre", _on_save_pre), ("save_post", _on_save_post))


def register():
    for chain_name, handler in _HANDLERS:
        chain = getattr(bpy.app.handlers, chain_name)
        if handler not in chain:
            chain.append(handler)
    if not bpy.app.background and not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=POLL_SECONDS, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    for chain_name, handler in _HANDLERS:
        chain = getattr(bpy.app.handlers, chain_name)
        if handler in chain:
            chain.remove(handler)
    _objects.clear()
    _views.clear()
    _driver[0] = None
    _written[0] = None
    _windows[0] = None
    _readers[0] = None
    _rendering[0] = False
