"""Graph-level regression for the generated uber: no game data, no cabmap, seconds.

    blender.exe --background --factory-startup --python Game/EndField/shader/selftest_graph.py

Builds every part's template, then renders a sphere per case and reads pixels.
Each check states the failure it exists to catch, because "it rendered" has
never once been evidence that the shading was right.
"""

from __future__ import annotations

import math
import os
import sys

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))))

from RuriRipperImporter.Game.EndField.shader import ruri_character_uber_endfield as gen  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print("[graph-selftest] {0} {1}{2}".format(
        "PASS" if ok else "FAIL", label, (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def scene_setup():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0, 0, 0))
    bpy.ops.object.shade_smooth()
    sphere = bpy.context.object
    bpy.ops.object.light_add(type="SUN", location=(0, -4, 4))
    sun = bpy.context.object
    sun.rotation_euler = (math.radians(50), 0, 0)
    sun.data.energy = 1.0
    bpy.ops.object.camera_add(location=(0, -4, 0), rotation=(math.pi / 2, 0, 0))
    cam = bpy.context.object
    scene = bpy.context.scene
    scene.camera = cam
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x = 128
    scene.render.resolution_y = 128
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    return sphere


def flat_image(name, rgba):
    """A real (non-generated) image of one colour, so a bound slot carries data
    the graph can actually read rather than the template's placeholder."""
    image = bpy.data.images.new(name, 8, 8, alpha=True)
    image.pixels = list(rgba) * (8 * 8)
    image.alpha_mode = "CHANNEL_PACKED"
    return image


def ramp_image(name):
    """Horizontal ramp in EVERY channel including ALPHA. Endfield's diffuse ramp
    carries the shadow WEIGHT in .a (min(ramp.a, castShadow) is what composes);
    RGB only feeds the saturation terms. A ramp painted with alpha=1 loses the
    directional signal entirely -- measured, not guessed."""
    image = bpy.data.images.new(name, 32, 32, alpha=True)
    pixels = []
    for _row in range(32):
        for column in range(32):
            u = column / 31.0
            pixels.extend((u, u, u, u))
    image.pixels = pixels
    image.alpha_mode = "CHANNEL_PACKED"
    return image


def render_mean(sphere, part, socket_values, tag, images=None):
    """Mean RGB over covered pixels for one part, with these socket overrides and
    these slots bound -- the same clone-and-repoint the real provider does."""
    group = gen.ensure(part)
    clone = group.copy()
    clone.name = "T " + tag
    for node in clone.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            real = (images or {}).get(node.image.name)
            if real is not None:
                node.image = real
    material = bpy.data.materials.new("T " + tag)
    node = gen.build_material(material, clone.name,
                              opaque=part not in ("Fur", "VFX", "OverlayShadow"))
    for name, value in socket_values.items():
        socket = node.inputs.get(name)
        if socket is None:
            failures.append("socket '{0}' missing on part {1}".format(name, part))
            continue
        socket.default_value = value if socket.type != "VECTOR" or isinstance(value, tuple) \
            else (value, value, value)
    sphere.data.materials.clear()
    sphere.data.materials.append(material)

    path = os.path.join(_HERE, "_graph_{0}.png".format(tag))
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path)
    pixels = list(image.pixels)
    bpy.data.images.remove(image)
    os.remove(path)
    return pixels


def mean_rgb(pixels):
    total = [0.0, 0.0, 0.0]
    covered = 0
    for i in range(0, len(pixels), 4):
        if pixels[i + 3] <= 0.5:
            continue
        covered += 1
        for c in range(3):
            total[c] += pixels[i + c]
    if covered == 0:
        return (0.0, 0.0, 0.0), 0
    return tuple(c / covered for c in total), covered


def peak_difference(a, b):
    """Largest per-pixel channel difference. A mean cannot see a left/right light
    flip on a sphere -- that swap is symmetric and averages out identically."""
    return max((abs(x - y) for x, y in zip(a, b)), default=0.0)


def main():
    sphere = scene_setup()

    # ── every part builds at all ─────────────────────────────────────────────
    for part in gen.PARTS:
        try:
            group = gen.ensure(part)
            check("build '{0}'".format(part), len(group.nodes) > 0,
                  "{0} nodes".format(len(group.nodes)))
        except Exception as exc:
            check("build '{0}'".format(part), False, "{0}: {1}".format(type(exc).__name__, exc))
            return

    # ── the face SDF master switch exists natively under its GAME name ───────
    # Source code is raw now ([CharacterStyle] modules, StyleModules.md): the C#
    # declares _UseSDFLightmap itself, so this socket existing is structural --
    # the old slot-rename drift (_UseMaskMap02 hiding it, face SDF silently dead)
    # is impossible by construction. These checks pin that.
    face = gen.ensure("Face")
    face_inputs = {item.name for item in face.interface.items_tree if item.item_type == "SOCKET"}
    check("Face exposes _UseSDFLightmap", "_UseSDFLightmap" in face_inputs)
    check("Face has no slot-era socket (_UseMaskMap02)",
          "_UseMaskMap02" not in face_inputs)

    # ── raw sockets on Standard (the .mat pumps these names verbatim) ────────
    std = gen.ensure("Standard")
    std_inputs = {item.name for item in std.interface.items_tree if item.item_type == "SOCKET"}
    for raw in ("_Metallic", "_Smoothness", "_Specular", "_UseMetallicGlossMap"):
        check("Standard exposes raw {0}".format(raw), raw in std_inputs)

    # ── placeholder colour = the game's own Properties default ───────────────
    # _MetallicGlossMap defaults to "white" in the GAME shader, guarded by
    # _UseMetallicGlossMap = 0. Neutrality lives in the gate now, not the colour.
    mask = bpy.data.images.get("_MetallicGlossMap")
    if check("_MetallicGlossMap placeholder exists", mask is not None):
        color = tuple(round(c, 3) for c in mask.generated_color)
        check("_MetallicGlossMap placeholder is the game default (white)",
              color[:3] == (1.0, 1.0, 1.0), str(color))

    # ── behaviour: gate OFF (the game default) must leave cloth lit ──────────
    # This is the shipping configuration for an unbound map: scalars take over
    # (roughness = 1-_Smoothness etc.), no black-chrome failure mode.
    cloth = {"_BaseColor": (0.8, 0.75, 0.7)}
    neutral_px = render_mean(sphere, "Standard", cloth, "std")
    lit, covered = mean_rgb(neutral_px)
    check("Standard renders", covered > 500, "{0} px".format(covered))
    check("gate-off cloth is lit, not black metal", max(lit) > 0.05,
          "mean rgb {0}".format(tuple(round(c, 4) for c in lit)))

    # Gate ON + a real dielectric mask (metal=0, smooth mid) must also stay lit
    # and must differ from a full-metal mask -- proves raw channel order
    # (RGBA = Metal/Spec/Shadow/Smooth) survived into the graph.
    dielectric = flat_image("mg dielectric", (0.0, 1.0, 1.0, 0.5))
    metal = flat_image("mg metal", (1.0, 1.0, 1.0, 1.0))
    lit_d, _ = mean_rgb(render_mean(sphere, "Standard", dict(cloth, _UseMetallicGlossMap=1.0),
                                    "stdmg", images={"_MetallicGlossMap": dielectric}))
    lit_m, _ = mean_rgb(render_mean(sphere, "Standard", dict(cloth, _UseMetallicGlossMap=1.0),
                                    "stdmetal", images={"_MetallicGlossMap": metal}))
    check("dielectric mask stays lit", max(lit_d) > 0.05,
          "mean rgb {0}".format(tuple(round(c, 4) for c in lit_d)))
    check("metal mask differs from dielectric (raw channel order alive)",
          abs(sum(lit_m) - sum(lit_d)) > 0.05,
          "metal {0} vs dielectric {1}".format(
              tuple(round(c, 4) for c in lit_m), tuple(round(c, 4) for c in lit_d)))

    # ── behaviour: the sun socket must relight the character ────────────────
    # A shader tree cannot read scene lights, so the kernels take the sun as a
    # plain input; if that input does not move the image, nothing about the
    # lighting is actually connected and every NPR term is running on defaults.
    sun_a = render_mean(sphere, "Standard", dict(cloth, Sun_Direction=(0.95, 0.0, 0.3)), "sunA")
    sun_b = render_mean(sphere, "Standard", dict(cloth, Sun_Direction=(-0.95, 0.0, 0.3)), "sunB")
    check("Standard responds to Sun_Direction", peak_difference(sun_a, sun_b) > 1e-3,
          "peak delta {0:.4f}".format(peak_difference(sun_a, sun_b)))

    # ── behaviour: the face SDF master switch must actually change shading ───
    # The diffuse ramp must be ON with a real alpha ramp bound: the face's
    # directional shadow weight flows exclusively through _DiffRampMap.a.
    sdf = {"_SDFLightmap": ramp_image("sdf lightmap"),
           "_SDFMask": flat_image("sdf mask", (0.5, 0.5, 0.5, 1.0)),
           "_DiffRampMap": ramp_image("diff ramp")}
    # _Smoothness matters here: its socket default is 0 (the inverse of the
    # kernel's roughness default of 1), and at fully-rough the face's light
    # response collapses to ambient. Every real .mat ships a value, so feed one
    # rather than measuring the degenerate default.
    face_props = {"_BaseColor": (0.9, 0.8, 0.75), "Sun_Direction": (0.7, -0.5, 0.5),
                  "_Smoothness": 0.5, "_UseDiffRampMap": 1.0}
    off = render_mean(sphere, "Face", dict(face_props, _UseSDFLightmap=0.0), "faceoff", images=sdf)
    on = render_mean(sphere, "Face", dict(face_props, _UseSDFLightmap=1.0), "faceon", images=sdf)
    check("_UseSDFLightmap changes face shading", peak_difference(off, on) > 1e-3,
          "peak delta {0:.4f}".format(peak_difference(off, on)))

    # SDF is a LIGHT-DIRECTION effect: the graph takes the sun as a plain socket
    # (a shader tree cannot read scene lights), so drive it from opposite sides.
    # Identical results would mean the lightmap is sampled but never steered.
    left = render_mean(sphere, "Face",
                       dict(face_props, _UseSDFLightmap=1.0, Sun_Direction=(0.95, 0.0, 0.3)),
                       "faceleft", images=sdf)
    right = render_mean(sphere, "Face",
                        dict(face_props, _UseSDFLightmap=1.0, Sun_Direction=(-0.95, 0.0, 0.3)),
                        "faceright", images=sdf)
    check("face shading responds to light direction", peak_difference(left, right) > 1e-3,
          "peak delta {0:.4f}".format(peak_difference(left, right)))


main()
print("[graph-selftest] {0} failure(s): {1}".format(len(failures), ", ".join(failures) or "none"))
sys.exit(1 if failures else 0)
