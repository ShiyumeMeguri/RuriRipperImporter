"""What one thing in this game HOLDS, asked of the decoder directly.

The other road would turn a package resource into Unity assets, run the AssetRipper
pipeline over them, serialise a project and hand the text back to be parsed; every step
of it exists to reach facts the game already stated. This asks for those facts: what an
object places, the buffers of every mesh, the parameters of every material, the pixels of
every texture.

Nothing here builds anything, and nothing here imports a host. What comes out is
:class:`Kernel.app.loading.Packages` of kind ``PLACEMENTS`` -- the same normalised forms
both hosts' builders already take, so an object of this game reaches Blender's mesh
builder through the one import entry every other package uses.
"""

from __future__ import annotations

from ...Kernel.app import loading
from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge import placements as shared
from ...RuriRipperPyBridge.sims4 import direct


def package(name, label, options):
    """One object as placements, or None when it places nothing."""
    return packages([name], label or name, options, key=name)


def packages(names, label, options, key=""):
    """Several objects as ONE placement statement, or None when they place nothing.

    Several is not a convenience: a room of this game's furniture is many objects that
    share meshes, and reading them as one decodes a shared mesh once. The ``parent``
    column indexes back into the row list, so each object's rows are rebased onto the end
    of the ones before it as they are appended."""
    bridge = cabmap_state.BRIDGE
    names = list(dict.fromkeys(names))
    rows = []
    for name in names:
        base = len(rows)
        for row in direct.placement_rows(bridge, name):
            parent = int(row["parent"])
            rows.append(dict(row, parent=(parent + base) if parent >= 0 else parent,
                             materials=_slots(row)))
    if not rows:
        return None
    library = _mesh_library(bridge, names, rows, options)
    materials, textures = _materials(bridge, rows, library, options)
    return loading.Packages(key or label, label, loading.PLACEMENTS, [],
                            placements=rows, library=library,
                            materials=materials, textures=textures)


def _slots(row):
    """The decoder joins a row's slot materials into one cell; the row contract the hosts
    read is a list, so the split happens once, here."""
    joined = row["materials"]
    return joined.split(shared.SLOT_SEPARATOR) if joined else []


def _mesh_library(bridge, names, rows, options):
    """``{mesh address: (DecodedMesh, own slot materials, bone names, skeleton rows)}``.

    One dataset call per OBJECT, not per mesh: this game states every mesh of an object in
    one table, and a mesh address is that object's key with the mesh's own name after it.
    The detail level asked for picks among the levels each mesh carries -- the nearest one
    it has, so a mesh with fewer levels than asked for still builds rather than vanishing.

    This game rigs an object to a single bone; it ships no reference skeleton for one, so
    the skeleton rows are empty and the host builds no armature."""
    detail = max(0, int(options.get("detail_level", 0) or 0))
    wanted = {row["mesh"] for row in rows if row["mesh"]}
    library = {}
    for name in names:
        table = direct.mesh_rows(bridge, name)
        for index in range(len(table)):
            address = "{0}.{1}".format(name, table.cell(index, "name"))
            if address not in wanted:
                continue
            level = int(table.cell(index, "lod"))
            chosen = library.get(address)
            if chosen is not None and abs(chosen[0] - detail) <= abs(level - detail):
                continue
            library[address] = (level, index, table)
    return {address: _entry(table, index)
            for address, (_level, index, table) in library.items()}


def _entry(table, index):
    return (direct.decoded_mesh(table, index),
            direct.mesh_material_paths(table, index),
            direct.mesh_bones(table, index),
            [])


def _materials(bridge, rows, library, options):
    """``({material address: MaterialProperties}, texture source)`` for every material any
    placement draws with, read once each."""
    if not options.get("import_materials", True):
        return {}, None
    wanted = []
    for row in rows:
        wanted.extend(loading.slot_paths(row, library))
    properties = direct.material_properties(bridge, wanted)
    source = direct.Sims4TextureSource(bridge)
    if options.get("import_textures", True):
        source.request([path for props in properties.values()
                        for path in props.textures.values()])
    return properties, source
