"""One thing this game holds, straight into the forms the host's builders already take.

Five datasets say everything an import needs and nothing it does not:

* ``sims4.props`` -- every object the setup can place, with the model it draws with;
* ``sims4.placements`` -- what one of them places, parents before children;
* ``sims4.mesh.geometry`` -- every detail level of every mesh it draws, as raw buffers
  already in the host's basis, beside the material each states;
* ``sims4.materials`` -- the parameter set every named material resolves to, flattened;
* ``sims4.textures`` -- the pixels of every named texture, in a container the host loads.

The unit asked about is a RESOURCE KEY, because that is the only identity this game has:
an object definition's key names a prop the way a package path names an Unreal actor.

WHAT the columns mean is not about this game -- every non-Unity source states the same
things the same way -- so the reading of them is the shared one in ``..placements`` and
this module is only the binding of these ids to it.
"""

from __future__ import annotations

from .. import placements

PROPS = "sims4.props"
LOTS = "sims4.lots"
PLACEMENTS = "sims4.placements"
MESH_GEOMETRY = "sims4.mesh.geometry"
MATERIALS = "sims4.materials"
TEXTURES = "sims4.textures"

SLOT_SEPARATOR = placements.SLOT_SEPARATOR

decoded_mesh = placements.decoded_mesh
mesh_bones = placements.mesh_bones
mesh_material_paths = placements.mesh_material_paths


def props(bridge):
    """Every object the setup can place, as plain rows."""
    return placements.rows(bridge.game_data(PROPS))


def lots(bridge):
    """Every saved lot the setup carries -- a whole house each -- as plain rows."""
    return placements.rows(bridge.game_data(LOTS))


def placement_rows(bridge, key):
    """What one object places, as plain rows."""
    return placements.rows(bridge.game_data(PLACEMENTS, object=key))


def mesh_rows(bridge, key):
    """Every detail level of every mesh one object draws."""
    return bridge.game_data(MESH_GEOMETRY, object=key)


def material_properties(bridge, paths):
    """``{address: MaterialProperties}`` for every material address asked for."""
    wanted = [path for path in dict.fromkeys(paths) if path]
    if not wanted:
        return {}
    return placements.material_rows(bridge.game_data(MATERIALS, material=wanted))


class Sims4TextureSource(placements.TextureSource):
    """This game's pixels, under the id its decoder publishes them as."""

    def __init__(self, bridge):
        super().__init__(bridge, TEXTURES)
