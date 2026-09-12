"""What one Unreal package HOLDS, asked of the decoder directly.

The other road turns an Unreal package into Unity assets, runs the AssetRipper
pipeline over them, serialises a project and hands the text back to be parsed;
every step of it exists to reach facts the engine already stated. This asks for
those facts: what a package places, the buffers of every mesh, the reference
skeleton a skinned one indexes, the parameters of every material, the pixels of
every texture.

Nothing here builds anything, and nothing here imports a host. What comes out is
:class:`Kernel.app.loading.Packages` of kind ``PLACEMENTS`` -- the same normalised
forms both hosts' builders already take (``DecodedMesh``, ``MaterialProperties``,
a texture source), so an Unreal actor reaches Blender's mesh builder and Painter's
glTF writer through the one import entry every other package uses.
"""

from __future__ import annotations

from ...Kernel.app import loading
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unreal import direct


def package(name, label, options):
    """One package as placements, or None when it places nothing.

    A package that places nothing may still carry animation -- asking what a
    package HOLDS beats making the caller declare which kind it handed over -- and
    that is the host's business, so it is said as an empty placement list rather
    than answered here."""
    return packages([name], label or name, options, key=name)


def packages(names, label, options, key=""):
    """Several packages as ONE placement statement, or None when they place nothing.

    A streamed window is twenty cells of one world; importing them is ONE thing
    the user asked for, so it is read as one. That is not a convenience: a mesh
    two cells both stamp is decoded once, and a host whose project IS one file
    gets a window rather than twenty projects of which nineteen are gone. The
    ``parent`` column indexes back into the row list, so each package's rows are
    rebased onto the end of the ones before it as they are appended."""
    bridge = cabmap_state.BRIDGE
    names = list(dict.fromkeys(names))
    rows = []
    for name in names:
        base = len(rows)
        for row in _rows(bridge.game_data(direct.PLACEMENTS, package=name)):
            parent = int(row["parent"])
            rows.append(dict(row, parent=(parent + base) if parent >= 0 else parent,
                             materials=_slots(row)))
    if not rows:
        return None
    library = _mesh_library(bridge, rows, options)
    materials, textures = _materials(bridge, rows, library, options)
    return loading.Packages(key or label, label, loading.PLACEMENTS, [],
                            placements=rows, library=library,
                            materials=materials, textures=textures)


def _slots(row):
    """The decoder joins a row's slot materials into one cell; the row contract
    the hosts read is a list, so the split happens once, here."""
    joined = row["materials"]
    return joined.split(direct.SLOT_SEPARATOR) if joined else []


def _rows(table):
    """The placement table as plain rows, each column read once."""
    columns = {name: table.values(name) for name in table.names}
    return [{name: values[index] for name, values in columns.items()}
            for index in range(len(table))]


def _mesh_library(bridge, rows, options):
    """``{mesh object path: (DecodedMesh, own slot materials, bone names, skeleton rows)}``.

    One dataset call per mesh, not per placement. The detail level asked for picks
    among the LODs the mesh carries -- the nearest one it has, so a mesh with fewer
    levels than asked for still builds rather than vanishing.

    The skeleton rows stay as the decoder stated them (name/parent/position/
    rotation/path): turning them into a host's vector type is the host's step, and
    a host with no skeletons ignores them entirely."""
    detail = max(0, int(options.get("detail_level", 0) or 0))
    library = {}
    for path in sorted({row["mesh"] for row in rows if row["mesh"]}):
        table = direct.mesh_rows(bridge, path)
        export = path.rsplit(".", 1)[-1]
        chosen = None
        for index in range(len(table)):
            if table.cell(index, "name") != export:
                continue
            level = int(table.cell(index, "lod"))
            if chosen is None or abs(level - detail) < abs(chosen[1] - detail):
                chosen = (index, level)
        if chosen is None:
            continue
        index = chosen[0]
        bones = direct.mesh_bones(table, index)
        skeleton = direct.skeleton(bridge, path).get(export, []) if bones else []
        library[path] = (direct.decoded_mesh(table, index),
                         direct.mesh_material_paths(table, index), bones, skeleton)
    return library


def _materials(bridge, rows, library, options):
    """``({material path: MaterialProperties}, texture source)`` for every material
    any placement draws with, read once each."""
    if not options.get("import_materials", True):
        return {}, None
    wanted = []
    for row in rows:
        wanted.extend(loading.slot_paths(row, library))
    properties = direct.material_properties(bridge, wanted)
    source = direct.UnrealTextureSource(bridge)
    if options.get("import_textures", True):
        source.request([path for props in properties.values()
                        for path in props.textures.values()])
    return properties, source

