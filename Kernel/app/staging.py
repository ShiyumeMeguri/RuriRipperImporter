"""What a game's DISPLAY STAGE is, in terms neither side owns.

An interface stands a model on a little lit stage -- a character screen, a weapon
screen -- and the game authors that stage: a directional light, an ambient level,
the volumes its camera renders under, the post-processing that camera applies, and
the stage's own art. Loading one means writing all of them somewhere, and where is
the host's business.

So a game RESOLVES its own assets into the targets below and the host WRITES them.
The target names are not invented here: the decoder publishes them beside each
value it states, so this module is where the Python side agrees with that one
table rather than a second opinion about it.

The statement is plain data, every value a number or a tuple of numbers::

    {"label":            what to call the stage,
     "exposure":         stops applied to every radiance in it, as ONE factor --
                         moving the sun without the sky would change the balance
                         the asset authored,
     "environment":      [(target, value)] for the LIGHT_* / WORLD_* targets,
     "volume":           {name: (x, y, z, w)} -- the engine globals the stage's own
                         volumes blend to, keyed by the engine's own names and
                         already packed the way the game packs them, for the host
                         to write the way it writes a level's globals,
     "post":             {input name: value} -- the post chain's own inputs, each
                         the difference from identity the chain reads, for the
                         host to drive its display chain with; empty when the
                         stage states none,
     "prefabs":          the container paths of the stage's own art, or ()}

A host writes whichever parts it has somewhere to put and reports what it did.
"""

from __future__ import annotations

#: The direction the light TRAVELS, as the game resolved it from its own pitch/yaw.
LIGHT_DIRECTION = "light.direction"
LIGHT_ENERGY = "light.energy"
#: The colour the light emits per unit of its energy, linear -- any colour
#: temperature already folded in by the game's own rule.
LIGHT_COLOR = "light.color"
#: Whether the light casts shadows.
LIGHT_SHADOWS = "light.shadows"
#: How much of the light scatters into a participating medium; zero keeps it out of
#: one the game lights with a copy of its own.
LIGHT_VOLUME = "light.volume"
#: The backdrop level: the constant term of the stage's own baked sky.
WORLD_COLOR = "world.color"
