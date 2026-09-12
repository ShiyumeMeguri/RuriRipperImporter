"""An Unreal package straight into the forms the host's builders already take.

The other road turns an Unreal package into Unity assets, runs the AssetRipper
pipeline over them, serialises a project and hands the text back to be parsed;
every step of it exists to reach facts the engine already stated. This asks for
those facts: what a package places, the buffers of every mesh, the reference
skeleton a skinned one indexes, the parameters of every material, the pixels of
every texture.

Six datasets say everything an import needs and nothing it does not:

* ``unreal.placements`` -- every scene component one world places, parents before children;
* ``unreal.animations`` -- every sequence in a package, as the clip blob every producer writes;
* ``unreal.mesh.geometry`` -- every LOD of every mesh in one package as raw buffers, already
  in the host's basis, beside the object path of the material each slot names;
* ``unreal.mesh.skeleton`` -- the reference skeleton a skinned mesh's weights index;
* ``unreal.materials`` -- the parameter set every named material resolves to, flattened;
* ``unreal.textures`` -- the pixels of every named texture, in a container the host loads.

WHAT the columns mean is not about Unreal -- every non-Unity source states the same five
things the same way -- so the reading of them lives in ``..placements`` and this module is
the binding of Unreal's own dataset ids to it. Only ``animations`` is Unreal's alone.
"""

from __future__ import annotations

from .. import placements
from ..unity import clip_curves

ANIMATIONS = "unreal.animations"
PLACEMENTS = "unreal.placements"
MESH_GEOMETRY = "unreal.mesh.geometry"
MESH_SKELETON = "unreal.mesh.skeleton"
MATERIALS = "unreal.materials"
TEXTURES = "unreal.textures"

# Row kinds in the materials table (Ruri.FModelHook.Unreal.UnrealDatasets).
MATERIAL_ROW = placements.MATERIAL_ROW
KEYWORD_ROW = placements.KEYWORD_ROW
TEXTURE_ROW = placements.TEXTURE_ROW
SCALAR_ROW = placements.SCALAR_ROW
VECTOR_ROW = placements.VECTOR_ROW

# One slot's material paths inside a mesh row, joined the way the decoder joins its lists.
SLOT_SEPARATOR = placements.SLOT_SEPARATOR

SKIN_DTYPE = placements.SKIN_DTYPE

decoded_mesh = placements.decoded_mesh
mesh_bones = placements.mesh_bones
mesh_material_paths = placements.mesh_material_paths


def mesh_rows(bridge, package):
    """Every LOD of every mesh in one package, newest read each call (the kernel caches)."""
    return bridge.game_data(MESH_GEOMETRY, package=package)


def skeleton(bridge, package):
    """``{mesh name: [bone, ...]}`` for every skeletal mesh in one package, parents first."""
    return placements.skeleton_rows(bridge.game_data(MESH_SKELETON, package=package))


def animations(bridge, package):
    """``{sequence name: ClipCurves}`` for every animation sequence in one package.

    The decoder writes the same blob the bridge writes for a Unity clip -- one JSON index and one
    float32 payload -- so this is the reader that already exists, not a second one.
    """
    table = bridge.game_data(ANIMATIONS, package=package)
    return {table.cell(index, "name"): clip_curves.ClipCurves.from_blob(
        table.cell(index, "meta"), table.cell(index, "curves"))
        for index in range(len(table))}


def material_properties(bridge, paths):
    """``{object path: MaterialProperties}`` for every material path asked for."""
    wanted = [path for path in dict.fromkeys(paths) if path]
    if not wanted:
        return {}
    return placements.material_rows(bridge.game_data(MATERIALS, material=wanted))


class UnrealTextureSource(placements.TextureSource):
    """This engine's pixels, under the id its decoder publishes them as."""

    def __init__(self, bridge):
        super().__init__(bridge, TEXTURES)
