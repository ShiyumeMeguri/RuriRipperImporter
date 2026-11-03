"""Where each kind of layer lives on disk, and how one layer reaches another.

The tree follows the container paths of an engine whose content is addressed the same way:
scenes by name, the static pieces a scene is built from under the art folder, rigs under the
actors' folder. Every layer sits in a folder of its own with the textures it reads beside it, and
every reference from one layer to another, or to a texture, is relative to the layer that states it,
so the tree can be moved whole.
"""

from __future__ import annotations

import os

SCENES = "scenes"
STATIC_ASSETS = "arts/environment/sceneassets"
CHARACTERS = "dynamicassets/gameplay/actors/postmodels/characters"
CHARACTER_SUFFIX = "_postmodel"
TEXTURES = "textures"

#: The two file formats a layer is written in, by extension.
TEXT = "usda"
BINARY = "usdc"


class Layout:
    """The folders of one product's tree. ``root`` is the folder the product folder sits in,
    ``product`` the folder that holds the whole tree, ``extension`` the format every layer of this
    run is written in -- a layer refers to others of the same format, so each format is a complete tree
    of its own and the textures between them are shared."""

    def __init__(self, root, product, extension):
        self.base = os.path.join(root, "assets", product)
        self.extension = extension

    def scene(self, scene):
        folder = os.path.join(self.base, *SCENES.split("/"), scene)
        return os.path.join(folder, "{0}.{1}".format(scene, self.extension))

    def static(self, scene, asset):
        folder = os.path.join(self.base, *STATIC_ASSETS.split("/"), scene, asset)
        return os.path.join(folder, "{0}.{1}".format(asset, self.extension))

    def character(self, name):
        stem = name + CHARACTER_SUFFIX
        folder = os.path.join(self.base, *CHARACTERS.split("/"), stem)
        return os.path.join(folder, "{0}.{1}".format(stem, self.extension))

    @staticmethod
    def textures(layer_path):
        return os.path.join(os.path.dirname(layer_path), TEXTURES)

    @staticmethod
    def reference(from_layer, to_path):
        """``to_path`` as the layer ``from_layer`` states it: relative to that layer's folder, with the
        explicit leading ``./`` an anchored asset path needs and a search path does not have."""
        relative = os.path.relpath(to_path, os.path.dirname(from_layer)).replace("\\", "/")
        return relative if relative.startswith("../") else "./" + relative
