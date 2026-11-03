"""What an import can be told to do -- declared once, for every host.

Before this there were two tables: the Blender operator's property mixin and the
Painter plugin's settings defaults. They drifted, and the drift was invisible:
Painter offered a "LOD0 only" tick whose key nothing downstream read, because
the shared renderer filter had been generalised to ``detail_level`` on the
Blender side and the other table was never told. A checkbox that does nothing is
the exact failure mode two truths produce.

So the table lives here, the hosts' widgets are GENERATED from it, and both ends
of every option are the same string.

An option that only some applications can honour states the host PROTOCOL it
needs (``Kernel.host.Timeline`` and friends), never a host name. A host offers the
options the protocols it derives from cover and no others, so a host that gains a
protocol gains the options with it and a new host answers by deriving.
"""

from __future__ import annotations

from . import host as host_port

BOOL = "bool"
INT = "int"


class Option:
    """One switch: its key, what it is, and which capability it presumes."""

    __slots__ = ("key", "kind", "default", "label", "description", "requires",
                 "minimum", "soft_maximum", "choices")

    def __init__(self, key, kind, default, label, description,
                 requires=None, minimum=None, soft_maximum=None, choices=None):
        self.key = key
        self.kind = kind
        self.default = default
        self.label = label
        self.description = description
        #: Capability the host must declare, or None for one every host honours.
        self.requires = requires
        self.minimum = minimum
        self.soft_maximum = soft_maximum
        #: Values a host renders as a fixed list rather than a free field.
        self.choices = tuple(choices) if choices else ()

    def applies_to(self, capabilities):
        return self.requires is None or self.requires in capabilities

    def __repr__(self):
        return "<Option {0}={1!r}>".format(self.key, self.default)


# WHAT IS NOT HERE, and will not come back: a switch per data class -- import
# materials / textures / skeleton / normals / vertex colours / tangents /
# blendshapes, and a UV-flip override. A model imported without its textures or
# its skeleton is not a lighter import, it is a broken one, and nobody ever
# reached for those ticks. They are gone from every host's panel.

#: ORDER IS THE UI ORDER. What is in the model first, then per-host output.
SCHEMA = (
    Option("detail_level", INT, 0, "Detail Level",
           "Which detail level to build: 0 is the highest the game authored. Read off the "
           "LOD components the model itself carries, or off whatever this game states "
           "detail with instead. -1 builds every level at once, which is for inspecting "
           "a model, not rendering one",
           minimum=-1, soft_maximum=4),
    Option("import_shadow_proxies", BOOL, False, "Keep Shadow Proxies",
           "Keep renderers the game draws only into the shadow map. They arrive casting "
           "shadows and seen by nothing else; the renderers they stand in for often cast "
           "none of their own, so leaving them out takes those shadows away. Offered only "
           "by a game whose builds carry them"),
    Option("import_inactive", BOOL, True, "Import Inactive Renderers",
           "Renderers that are disabled, or sit on a deactivated GameObject, draw nothing "
           "in the game -- but they are usually runtime-toggled variants. Untick to import "
           "exactly what the game draws"),
    Option("import_colliders", BOOL, False, "Import Colliders",
           "Keep the collision volumes the game puts on its objects: boxes, spheres and capsules as empties "
           "of the same shape, mesh colliders as hidden meshes, gathered in one collection with what the "
           "game states of each. Nothing simulates",
           requires=host_port.SceneGraph),

    Option("game_shaders", BOOL, True, "Game Shaders",
           "Rebuild the game's own shading stack (NPR lighting, SDF face shadows, fur "
           "shells, outlines) instead of the host's built-in BSDF -- for every load, a "
           "scene window as much as a character",
           requires=host_port.NodeMaterials),
    Option("import_empties", BOOL, False, "Import Empties",
           "Keep every GameObject as an Empty. Off keeps only the empties that hold "
           "imported content in the hierarchy",
           requires=host_port.SceneGraph),
    Option("import_animations", BOOL, True, "Discover Animations",
           "List this character's animation clips in the Animations panel after import. "
           "Clips are NOT built until you check them there and click Import -- a single "
           "clip can be 100+MB, so nothing is loaded automatically",
           requires=host_port.Timeline),
    Option("retarget_face", BOOL, False, "Retarget Face",
           "For a clip whose facial animation is baked into its bone tracks (UI and "
           "cutscene clips are), work out WHICH library expressions that performance is "
           "-- measured on the character the clip was authored on -- and have the "
           "character in the scene play those same named expressions through its own face "
           "table. No geometry crosses between the two faces",
           requires=host_port.Timeline),
    Option("import_secondary_motion", BOOL, False, "Import Cloth",
           "Bring the model's own secondary motion across: the hair, cloth and accessory "
           "chains its author tuned on the model itself, plus the collision volumes they "
           "collide with, written onto the imported armature. Replaces whatever that "
           "armature already carried",
           requires=host_port.Rig),

    Option("force_rebuild", BOOL, False, "Rebuild Texture Cache",
           "Re-bake every channel image even when the cache already looks current",
           requires=host_port.TextureCache),
    Option("texture_resolution", INT, 2048, "Texture Resolution",
           "Working resolution of the texture sets the project is created with",
           requires=host_port.TextureSets, choices=(512, 1024, 2048, 4096)),
    Option("neutral_display", BOOL, True, "Neutral Display",
           "The ported shader writes the final picture -- it applies the game's own tonemap, "
           "as Blender's compositor does -- so the display keeps Linear tone mapping and no "
           "colour LUT; either one would change the picture a second time",
           requires=host_port.DisplaySettings),
)

_BY_KEY = {option.key: option for option in SCHEMA}


def option(key):
    found = _BY_KEY.get(key)
    if found is None:
        raise KeyError("no import option named {0!r} -- the schema is Kernel/options.py "
                       "and nothing outside it may invent a key".format(key))
    return found


def schema(capabilities=None):
    """The options that apply to a host with these capabilities (the bound
    host's, when none are given)."""
    if capabilities is None:
        capabilities = host_port.current().capabilities
    return tuple(entry for entry in SCHEMA if entry.applies_to(capabilities))


def defaults(capabilities=None):
    return {entry.key: entry.default for entry in schema(capabilities)}


def resolve(values, capabilities=None):
    """Merge caller-supplied values onto the defaults, rejecting keys this host
    has no option for.

    Rejecting rather than ignoring is the point: an option silently dropped
    because the key was misspelled, or because it belongs to another host's
    capability set, is a switch the user flipped and nothing acted on.
    """
    applicable = defaults(capabilities)
    if not values:
        return applicable
    unknown = sorted(set(values) - set(applicable))
    if unknown:
        raise KeyError(
            "import option(s) {0} do not apply here: {1}".format(
                ", ".join(unknown),
                "unknown to the schema" if set(unknown) - set(_BY_KEY)
                else "declared but this host lacks the capability they need"))
    applicable.update(values)
    return applicable
