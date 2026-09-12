"""What a game's DISPLAY STAGE is, in terms neither side owns.

An interface stands a model on a little lit stage -- a character screen, a weapon
screen -- and the game authors that stage: a directional light, an ambient level,
a set of per-material parameters, and the stage's own art. Loading one means
writing all four somewhere, and where is the host's business.

So a game RESOLVES its own assets into the targets below and the host WRITES them.
The target names are not invented here: the decoder publishes them beside each
binding it states, so this module is where the Python side agrees with that one
table rather than a second opinion about it.

The statement is plain data::

    {"label":            what to call the stage,
     "exposure":         stops applied to every radiance in it, as ONE factor --
                         moving the sun without the sky would change the balance
                         the asset authored,
     "environment":      [(target, value)] for the LIGHT_* / WORLD_* targets,
     "character_params": [(slot, components, value)] to push onto the game's own
                         materials -- ``components`` says which of a slot's four
                         the value fills ("rgb", "xyzw", "w", "x", "y", "z"),
     "prefabs":          the container paths of the stage's own art, or ()}

A host writes whichever parts it has somewhere to put and reports what it did.
"""

from __future__ import annotations

#: The direction the light TRAVELS, as the game resolved it from its own pitch/yaw.
LIGHT_DIRECTION = "light.direction"
LIGHT_ENERGY = "light.energy"
LIGHT_ANGLE = "light.angle"
LIGHT_COLOR = "light.color"
LIGHT_TEMPERATURE = "light.temperature"
LIGHT_USE_TEMPERATURE = "light.useTemperature"
#: The backdrop level: the constant term of the stage's own baked sky.
WORLD_COLOR = "world.color"
#: A per-material parameter of the game's own shading stack.
CHARACTER_PARAMS = "material.characterParams"

LIGHT_TARGETS = (LIGHT_DIRECTION, LIGHT_ENERGY, LIGHT_ANGLE, LIGHT_COLOR,
                 LIGHT_TEMPERATURE, LIGHT_USE_TEMPERATURE)

#: The targets an exposure change scales. A radiance moves with the stops; a
#: direction, an angle and a temperature do not.
SCALED_TARGETS = (LIGHT_ENERGY, WORLD_COLOR)
