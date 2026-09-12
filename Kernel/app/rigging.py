"""The SHAPE of a model's own secondary motion. Not its vocabulary.

A game states hair/cloth/accessory chains on its model; a host writes them onto a
rig. Neither may import the other, so what crosses is declared here -- and what
crosses is deliberately only the shape, four role tokens and three attribute
tokens. Everything else is somebody's own words:

* WHICH fields a game states, and what its enum ordinals mean, is that GAME's --
  a second game states different fields, and a table here enumerating the first
  game's would have to be edited to add the second's, which is not "add on
  demand", it is "edit the kernel".
* WHAT a parameter is called on the other side is the SOLVER's, and the solver is
  the truth about its own field names. A path it does not carry has to fail at the
  assignment, loudly, rather than pass a copy of its field list kept here that
  nobody updates.

So a game maps its own fields onto the solver's paths and hands over ``(path,
value)``; the host assigns. Reading is per game (``GameModule.secondary_motion``);
a game that states no such settings declares none and the option that would ask
for them never appears.

The statement::

    {"configs":   [{"index", "name"}],
     "values":    [{"config", "path", "value", "kind"}],
     "curves":    [{"config", "path", "use", "value", "samples": [...]}],
     "bones":     [{"config", "role", "bone"}],
     "colliders": [{"index", "bone", "kind", "center", "size", "direction",
                    "aligned_on_center", "radius_separation", "reverse_direction",
                    "local_position", "local_rotation",
                    "bone_position", "bone_rotation"}],
     "config_colliders": [{"config", "collider"}],
     "attributes": [{"config", "bone", "attribute", "error"}],
     "unknown":   {path: value}}

``value`` arrives already in the solver's own terms -- a float, a bool, or the
identifier that solver names the choice by -- because turning an ordinal into a
choice needs the ordinal's meaning, and that is the game's.

A curve's ``samples`` is a LIST and the writer reads its length: how many samples
a game publishes is that game's business, never a constant two modules must agree
on. ``unknown`` names what the game stated and could not map, so a parameter that
goes nowhere is reported rather than silently dropped.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# What a value row is
# ---------------------------------------------------------------------------
#: An ordinary scalar, boolean or choice -- assigned as it arrives.
VALUE = "value"
#: A direction, carried as three components in the GAME's own axes. ONE row, not
#: three: the axes it is stated in are not the axes the host draws in, so three
#: independent scalars would each be individually right with the vector as a whole
#: wrong. The host converts it; nothing else in the statement needs converting.
DIRECTION = "direction"

# ---------------------------------------------------------------------------
# Bone roles
# ---------------------------------------------------------------------------
#: The chain's roots: the bones the simulation starts from.
ROOT_ROLE = "root"
#: Roots the model names and then excludes again.
IGNORE_ROLE = "ignore"
#: Bones the chain skins rather than simulates.
SKINNING_ROLE = "skinning"
#: Bones the chain collides against.
COLLISION_ROLE = "collision"

ROLES = (ROOT_ROLE, IGNORE_ROLE, SKINNING_ROLE, COLLISION_ROLE)

# ---------------------------------------------------------------------------
# Per-bone attributes
# ---------------------------------------------------------------------------
IGNORE_ATTRIBUTE = "IGNORE"
FIXED_ATTRIBUTE = "FIXED"
MOVE_ATTRIBUTE = "MOVE"

ATTRIBUTES = (IGNORE_ATTRIBUTE, FIXED_ATTRIBUTE, MOVE_ATTRIBUTE)
