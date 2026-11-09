"""A level's own sky, as the world every material's environment queries read.

The source states its sky irradiance as nine spherical-harmonic coefficients per
channel -- the quantity its shading stack's SampleSH evaluates directly, irradiance
over pi, in the same pre-exposed space as the directional light. A Blender world is
RADIANCE that the renderer convolves by itself, so every band goes in divided by its
share of the cosine lobe (1, 2/3 and 1/4 for l = 0, 1, 2): convolving the result back
lands on the source's own polynomial, and a white diffuse surface under this world
reads what SampleSH would have answered for its normal.

The world has to be in place BEFORE a material is built. A material's specular answer
samples whatever world exists at build time, and one built against a flat default
world reflects that constant for good -- every surface loses its sheen and the scene
reads as flat colour. Materials already in the file are re-answered after the world
changes, through the same rewire any environment change goes through.

Directions cross into the source's axes through the shared coordinate space (the
reflection plus the once-only top-level turn), so the sky is oriented exactly like the
statement's own geometry and lights.
"""

from __future__ import annotations

import bpy
import math
import numpy as np

from ...Kernel.math3d import coordinate
from . import material_builder

WIDTH = 512
HEIGHT = 256

_BAND_OF_COEFFICIENT = np.array([0, 1, 1, 1, 2, 2, 2, 2, 2])
_LOBE_SHARE = np.array([1.0, 2.0 / 3.0, 0.25])
_UNITY_TO_BLENDER = (coordinate.BLENDER.root_rotation @ coordinate.BLENDER.matrix)[:3, :3]


def _sample_sh(coefficients, x, y, z):
    """The source's SampleSH polynomial, per channel, in its own axes (Y up)."""
    c = coefficients[:, :, None]
    return (c[:, 0] - c[:, 6]
            + c[:, 1] * y + c[:, 2] * z + c[:, 3] * x
            + c[:, 4] * (x * y) + c[:, 5] * (y * z)
            + c[:, 6] * 3.0 * (z * z) + c[:, 7] * (z * x)
            + c[:, 8] * (x * x - y * y))


def radiance(coefficients):
    """(HEIGHT, WIDTH, 3) equirectangular radiance, bottom row first, in the texel
    layout Blender's environment lookup reads (u = -atan2(y, x) / 2pi + 1/2,
    v = atan2(z, |xy|) / pi + 1/2)."""
    stated = np.asarray(coefficients, dtype=np.float64).reshape(3, 9)
    unconvolved = stated / _LOBE_SHARE[_BAND_OF_COEFFICIENT][None, :]
    azimuth = (0.5 - (np.arange(WIDTH) + 0.5) / WIDTH) * 2.0 * np.pi
    latitude = ((np.arange(HEIGHT) + 0.5) / HEIGHT - 0.5) * np.pi
    azimuth, latitude = np.meshgrid(azimuth, latitude)
    blender = np.stack([np.cos(latitude) * np.cos(azimuth),
                        np.cos(latitude) * np.sin(azimuth),
                        np.sin(latitude)], axis=-1).reshape(-1, 3)
    source = blender @ _UNITY_TO_BLENDER
    values = _sample_sh(unconvolved, source[:, 0], source[:, 1], source[:, 2])
    return np.maximum(values.T, 0.0).reshape(HEIGHT, WIDTH, 3)


def _image(name, texels):
    stale = bpy.data.images.get(name)
    if stale is not None:
        bpy.data.images.remove(stale)
    image = bpy.data.images.new(name, WIDTH, HEIGHT, alpha=False, float_buffer=True)
    rgba = np.concatenate([texels, np.ones((HEIGHT, WIDTH, 1))], axis=-1)
    image.pixels.foreach_set(rgba.astype(np.float32).ravel())
    image.file_format = "OPEN_EXR"
    image.pack()
    return image


def build(context, coefficients, label):
    """Make the level's sky the scene's world and re-answer every built material's
    environment queries against it. Returns how many materials were re-answered."""
    name = "Ruri Sky " + label
    image = _image(name, radiance(coefficients))
    world = bpy.data.worlds.get(name) or bpy.data.worlds.new(name)
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    texture = tree.nodes.new("ShaderNodeTexEnvironment")
    texture.image = image
    texture.projection = "EQUIRECTANGULAR"
    background = tree.nodes.new("ShaderNodeBackground")
    background.inputs["Strength"].default_value = 1.0
    output = tree.nodes.new("ShaderNodeOutputWorld")
    tree.links.new(texture.outputs["Color"], background.inputs["Color"])
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    context.scene.world = world
    return material_builder.rewire_capabilities()

def source_directions(width, height, basis):
    azimuth = (0.5 - (np.arange(width) + 0.5) / width) * 2 * np.pi
    latitude = ((np.arange(height) + 0.5) / height - 0.5) * np.pi
    a, b = np.meshgrid(azimuth, latitude)
    directions = np.stack(
        (np.cos(b) * np.cos(a), np.cos(b) * np.sin(a), np.sin(b)), axis=-1
    )
    return directions @ np.linalg.inv(basis).T


def cube_lookup(directions):
    """Unity/D3D faces +X,-X,+Y,-Y,+Z,-Z; returned UVs are bottom-left.
    The reader deliberately keeps cubemap rows unflipped (unlike Texture2D).
    """
    d = np.asarray(directions)
    axis = np.argmax(np.abs(d), axis=-1)
    x, y, z = (d[..., i] for i in range(3))
    major = np.maximum(np.max(np.abs(d), axis=-1), 1e-12)
    face = axis * 2 + (np.take_along_axis(d, axis[..., None], axis=-1)[..., 0] < 0)
    u = (
        np.select(
            (face == 0, face == 1, face == 2, face == 3, face == 4, face == 5),
            (-z, z, x, x, x, -x),
        )
        / major
    )
    v = (
        np.select(
            (face == 0, face == 1, face == 2, face == 3, face == 4, face == 5),
            (-y, -y, z, -z, -y, -y),
        )
        / major
    )
    return face.astype(int), (u + 1) * 0.5, (1 - v) * 0.5


def panorama(faces, width, height, basis, rotation=0):
    d = source_directions(width, height, basis)
    angle = math.radians(rotation)
    x, z = d[..., 0].copy(), d[..., 2].copy()
    d[..., 0], d[..., 2] = (
        math.cos(angle) * x - math.sin(angle) * z,
        math.sin(angle) * x + math.cos(angle) * z,
    )
    face, u, v = cube_lookup(d)
    result = np.zeros((height, width, 4), dtype=np.float32)
    for index, image in enumerate(faces):
        w, h = image.size
        pixels = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(h, w, 4)
        # EXR face pixels arrive bottom-first in Blender. Bilinear sampling is
        # clamped within a face; seam-aware cube filtering remains a limitation.
        px, py = np.clip(u * w - 0.5, 0, w - 1), np.clip(v * h - 0.5, 0, h - 1)
        ix, iy = px.astype(int), py.astype(int)
        jx, jy = np.minimum(ix + 1, w - 1), np.minimum(iy + 1, h - 1)
        fx, fy = (px - ix)[..., None], (py - iy)[..., None]
        sampled = (pixels[iy, ix] * (1 - fx) + pixels[iy, jx] * fx) * (1 - fy) + (
            pixels[jy, ix] * (1 - fx) + pixels[jy, jx] * fx
        ) * fy
        result[face == index] = sampled[face == index]
    result[..., 3] = 1
    return result


def float_image(name, pixels):
    h, w = pixels.shape[:2]
    image = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    image.file_format = "OPEN_EXR"
    image.pack()
    return image


def cube_image(keys, images, basis, name, rotation=0, multiplier=(1, 1, 1), width=None):
    """Project six declared +X,-X,+Y,-Y,+Z,-Z faces; no source-property interpretation."""
    faces = [images(key) for key in keys]
    if len(faces) != 6 or any(face is None for face in faces):
        raise ValueError("A declared cubemap is missing one of its six images.")
    width = width if width is not None else (2048 if faces[0].size[0] >= 512 else 512)
    pixels = panorama(faces, width, width // 2, basis, rotation)
    pixels[..., :3] *= np.asarray(multiplier, dtype=np.float32)
    return float_image(name, pixels)


def build_channels(context, environment, images):
    """One resolved environment, before any material is built. Exposure/look remain user-owned."""
    channels = environment["channels"]
    resolved_images = {}
    for value in channels.values():
        if value is None:
            continue
        keys = value["faces"] if "faces" in value else [value["image"]]
        if "faces" in value and len(keys) != 6:
            raise ValueError("A declared cubemap requires exactly six images.")
        for key in keys:
            if key not in resolved_images:
                resolved_images[key] = images(key)
            if resolved_images[key] is None:
                raise ValueError("A declared environment image is missing: " + key)
    world = bpy.data.worlds.new(environment["label"])
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    basis = np.asarray(material_builder.world_basis())
    projected = {}

    def channel(value, label):
        background = tree.nodes.new("ShaderNodeBackground")
        background.inputs["Color"].default_value = (0, 0, 0, 1)
        if value is None:
            return background.outputs[0]
        if "faces" in value:
            identity = (tuple(value["faces"]), value["rotation"], tuple(value["multiplier"]))
            image = projected.get(identity)
            if image is None:
                image = cube_image(value["faces"], resolved_images.__getitem__, basis, label,
                                   value["rotation"], value["multiplier"])
                projected[identity] = image
        else:
            image = resolved_images[value["image"]]
        texture = tree.nodes.new("ShaderNodeTexEnvironment")
        texture.image = image
        tree.links.new(texture.outputs["Color"], background.inputs["Color"])
        background.inputs["Strength"].default_value = value["strength"]
        return background.outputs[0]

    path = tree.nodes.new("ShaderNodeLightPath")
    glossy = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(path.outputs["Is Glossy Ray"], glossy.inputs[0])
    tree.links.new(channel(channels["diffuse"], "Authored diffuse environment"), glossy.inputs[1])
    tree.links.new(channel(channels["glossy"], "Authored reflection environment"), glossy.inputs[2])
    camera = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(path.outputs["Is Camera Ray"], camera.inputs[0])
    tree.links.new(glossy.outputs[0], camera.inputs[1])
    tree.links.new(channel(channels["camera"], "Authored visible environment"), camera.inputs[2])
    output = tree.nodes.new("ShaderNodeOutputWorld")
    tree.links.new(camera.outputs[0], output.inputs["Surface"])
    world["ruri_environment_source"] = environment["key"]
    context.scene.world = world
    return material_builder.rewire_capabilities()
