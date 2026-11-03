"""A material as USD states it, and the pixels it reads.

What a layer says of a material is two things apart. The preview surface is the part any USD
reader can show: the base colour, as a texture times a tint or as a constant, and the opacity the
material blends or clips with. Everything the source says about it -- the shader it names, the
family that shader maps to, every number, vector, keyword and texture it carries -- is ``ruri:``
attributes of basic types on the material prim, so a reader that knows the source's shaders loses
nothing by there being a preview.
"""

from __future__ import annotations

import os
import shutil

from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

from .layout import TEXTURES
from .names import Namer, file_stem, identifier

#: A sampler's wrap word, as the preview surface says it. USD has no mirror-once; mirror is the nearest.
_WRAP = {"repeat": "repeat", "clamp": "clamp", "mirror": "mirror", "mirroronce": "mirror"}

#: The name of the primvar every material reads its first texture-coordinate set through.
PRIMVAR = "st"

#: The name of the prim a layer's materials sit under.
SCOPE = "Materials"

_NEUTRAL_TINT = (1.0, 1.0, 1.0, 1.0)
_NEUTRAL_TILING = (1.0, 1.0, 0.0, 0.0)


def family_of(shader_name, families):
    """The family a shader name maps to, or empty. ``families`` is data: rows of ``family`` with the
    shader names it takes whole (``exact``) and the beginnings of the names it takes (``prefix``)."""
    for row in families:
        if shader_name in row.get("exact", ()) or any(
                shader_name.startswith(prefix) for prefix in row.get("prefix", ())):
            return row["family"]
    return ""


class TextureStore:
    """Writes each texture a layer reads into the folder beside that layer, once per layer.

    The first time a texture is asked for anywhere its pixels are fetched from the reader and
    written; every later layer that reads it gets a copy of that file, so the reader is asked once and
    each layer still owns what it reads."""

    def __init__(self, statement):
        self._textures = statement.textures
        self._names = Namer()
        self._first = {}
        self._placed = {}
        self.written = 0
        self.linked = 0
        self.copied = 0
        self.total_bytes = 0
        self.missing = set()

    def get(self, key):
        """The statement's texture ``key``, or None."""
        return self._textures.get(key)

    def _duplicate(self, first, target):
        """The file ``target`` with the content of ``first``: a hard link where the volume has them, so a texture
        every layer owns is stored once, else a copy."""
        if os.path.exists(target) and os.path.samefile(first, target):
            self.linked += 1
            return
        try:
            os.link(first, target)
            self.linked += 1
        except OSError:
            shutil.copyfile(first, target)
            self.copied += 1

    def place(self, layer_path, key):
        """The texture ``key`` as the layer at ``layer_path`` reads it -- ``./textures/<file>`` -- or
        empty when the statement has no pixels for it."""
        folder = os.path.join(os.path.dirname(layer_path), TEXTURES)
        remembered = self._placed.get((folder, key))
        if remembered is not None:
            return remembered
        texture = self._textures.get(key)
        if texture is None or not texture.bytes:
            self.missing.add(key)
            self._placed[(folder, key)] = ""
            return ""
        os.makedirs(folder, exist_ok=True)
        stem = self._names.claim(folder, file_stem(texture.name or "Texture"))
        target = os.path.join(folder, "{0}.{1}".format(stem, texture.container or "png"))
        first = self._first.get(key)
        if first is None:
            with open(target, "wb") as handle:
                handle.write(texture.image)
            self._first[key] = target
            self.written += 1
        else:
            self._duplicate(first, target)
        self.total_bytes += os.path.getsize(target)
        placed = "./{0}/{1}".format(TEXTURES, os.path.basename(target))
        self._placed[(folder, key)] = placed
        return placed


def _text(prim, name, value):
    prim.CreateAttribute(name, Sdf.ValueTypeNames.String, custom=True).Set(value)


def _shader(stage, path, identity):
    shader = UsdShade.Shader.Define(stage, path)
    shader.CreateIdAttr(identity)
    return shader


def _reader(stage, material_path, made):
    """The node that reads the first texture-coordinate set, made once per material."""
    if "reader" not in made:
        reader = _shader(stage, material_path.AppendChild("StReader"), "UsdPrimvarReader_float2")
        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(PRIMVAR)
        reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)
        made["reader"] = reader
    return made["reader"]


def _texture_node(stage, material_path, made, name, file, texture, tiling):
    """A texture read through the coordinate set, tiled and offset as the material states."""
    node = _shader(stage, material_path.AppendChild(name), "UsdUVTexture")
    node.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(file))
    node.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB" if texture.srgb else "raw")
    node.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set(_WRAP.get(texture.wrap_u, "repeat"))
    node.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set(_WRAP.get(texture.wrap_v, "repeat"))
    coordinates = _reader(stage, material_path, made).ConnectableAPI()
    if tiling is not None and tuple(tiling) != _NEUTRAL_TILING:
        transform = _shader(stage, material_path.AppendChild(name + "Transform"), "UsdTransform2d")
        transform.CreateInput("in", Sdf.ValueTypeNames.Float2).ConnectToSource(coordinates, "result")
        transform.CreateInput("scale", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(tiling[0], tiling[1]))
        transform.CreateInput("translation", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(tiling[2], tiling[3]))
        transform.CreateOutput("result", Sdf.ValueTypeNames.Float2)
        coordinates = transform.ConnectableAPI()
    node.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(coordinates, "result")
    node.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
    node.CreateOutput("a", Sdf.ValueTypeNames.Float)
    return node


def _preview(stage, material_path, stated, base_file, texture):
    """The preview surface: base colour as texture times tint or as a constant, and the opacity."""
    roles = stated.roles
    surface = _shader(stage, material_path.AppendChild("Surface"), "UsdPreviewSurface")
    tint = roles.colors.get("base_color")
    base = roles.first("base_color")
    made = {}
    coverage = None
    if base is not None and base_file:
        node = _texture_node(stage, material_path, made, "BaseColor", base_file, texture,
                             stated.texture_st.get(base.name))
        if tint is not None and tuple(tint) != _NEUTRAL_TINT:
            node.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*(float(c) for c in tint)))
        surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(node.ConnectableAPI(), "rgb")
        coverage = node
    elif tint is not None:
        surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*(float(c) for c in tint[:3])))
    blends = roles.transparent or roles.clipped
    if blends and coverage is not None:
        surface.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(coverage.ConnectableAPI(), "a")
    elif blends and tint is not None:
        surface.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(tint[3]))
    cutoff = roles.floats.get("alpha_cutoff") if roles.clipped else None
    if cutoff is not None:
        surface.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(float(cutoff))
    return surface.CreateOutput("surface", Sdf.ValueTypeNames.Token)


def _source(prim, stated, families, place):
    """Everything the source states about the material, as ``ruri:`` attributes of basic types."""
    _text(prim, "ruri:sourceName", stated.name)
    _text(prim, "ruri:sourceKey", stated.key)
    _text(prim, "ruri:sourceShader", stated.shader_name)
    _text(prim, "ruri:family", family_of(stated.shader_name, families))
    if stated.keywords:
        prim.CreateAttribute("ruri:keywords", Sdf.ValueTypeNames.StringArray, custom=True).Set(
            Vt.StringArray(list(stated.keywords)))
    if stated.disabled_passes:
        prim.CreateAttribute("ruri:disabledPasses", Sdf.ValueTypeNames.StringArray, custom=True).Set(
            Vt.StringArray(list(stated.disabled_passes)))
    for name, value in sorted(stated.floats.items()):
        prim.CreateAttribute("ruri:properties:" + identifier(name), Sdf.ValueTypeNames.Float, custom=True).Set(
            float(value))
    for name, value in sorted(stated.colors.items()):
        prim.CreateAttribute("ruri:properties:" + identifier(name), Sdf.ValueTypeNames.FloatArray, custom=True).Set(
            Vt.FloatArray([float(component) for component in value]))
    for name, key in sorted(stated.textures.items()):
        file = place(key) if key else ""
        if file:
            prim.CreateAttribute("ruri:properties:" + identifier(name), Sdf.ValueTypeNames.Asset, custom=True).Set(
                Sdf.AssetPath(file))
        tiling = stated.texture_st.get(name)
        if tiling is not None:
            prim.CreateAttribute("ruri:properties:" + identifier(name) + "_ST", Sdf.ValueTypeNames.FloatArray,
                                 custom=True).Set(Vt.FloatArray([float(component) for component in tiling]))


def scope(stage, parent_path):
    """The folder prim a layer's materials sit under."""
    return UsdGeom.Scope.Define(stage, parent_path.AppendChild(SCOPE)).GetPath()


def author(stage, scope_path, stated, textures, layer_path, families, names):
    """One material in the scope at ``scope_path``: the preview surface, the source's properties, and
    the textures it reads written beside the layer. Returns the :class:`UsdShade.Material`."""
    name = names.claim(str(scope_path), identifier(stated.name or "Material"))
    material = UsdShade.Material.Define(stage, scope_path.AppendChild(name))

    def place(key):
        return textures.place(layer_path, key)

    base = stated.roles.first("base_color")
    base_file = place(base.guid) if base is not None else ""
    texture = textures.get(base.guid) if base is not None else None
    output = _preview(stage, material.GetPath(), stated, base_file, texture)
    material.CreateSurfaceOutput().ConnectToSource(output)
    _source(material.GetPrim(), stated, families, place)
    return material
