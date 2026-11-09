"""Node projection of stated prefiltered probe images and per-renderer weights."""

def specular(tree, base, metallic, roughness, normal, probes, source_f0=None):
    """Unity Standard BRDF1 indirect specular, linear-working branch.
    The data are source convolution mips, not an artist-blurred approximation.
    """
    from .scene_lighting import math_node as math, vector_node as vector

    def scale(color, factor):
        n = tree.nodes.new("ShaderNodeVectorMath")
        n.operation = "SCALE"
        tree.links.new(color, n.inputs[0])
        tree.links.new(factor, n.inputs["Scale"])
        return n.outputs[0]

    geometry = tree.nodes.new("ShaderNodeNewGeometry")
    # Incoming points from the shaded point towards the viewer in Blender.
    incident = scale(geometry.outputs["Incoming"], math(tree, "MULTIPLY", -1, 1))
    reflected = vector(tree, "REFLECT", incident, normal)
    nv = math(
        tree,
        "MINIMUM",
        math(
            tree,
            "ABSOLUTE",
            vector(tree, "DOT_PRODUCT", normal, geometry.outputs["Incoming"]),
        ),
        1,
    )
    fresnel = math(tree, "POWER", math(tree, "SUBTRACT", 1, nv), 5)
    one_minus_metal = math(tree, "SUBTRACT", 1, metallic)
    dielectric = tree.nodes.new("ShaderNodeRGB")
    dielectric.outputs[0].default_value = (0.04, 0.04, 0.04, 1)
    f0 = vector(
        tree,
        "ADD",
        scale(base, metallic),
        scale(dielectric.outputs[0], one_minus_metal),
    )
    reflectivity = math(
        tree, "SUBTRACT", 1, math(tree, "MULTIPLY", one_minus_metal, 0.96)
    )
    if source_f0 is not None:
        f0 = source_f0
        separate = tree.nodes.new("ShaderNodeSeparateXYZ")
        tree.links.new(f0, separate.inputs[0])
        reflectivity = math(tree, "MAXIMUM", math(tree, "MAXIMUM", separate.outputs[0], separate.outputs[1]), separate.outputs[2])
    grazing = math(
        tree,
        "MINIMUM",
        math(tree, "ADD", math(tree, "SUBTRACT", 1, roughness), reflectivity),
        1,
    )
    white = tree.nodes.new("ShaderNodeRGB")
    white.outputs[0].default_value = (1, 1, 1, 1)
    fresnel_color = vector(
        tree,
        "ADD",
        scale(f0, math(tree, "SUBTRACT", 1, fresnel)),
        scale(white.outputs[0], math(tree, "MULTIPLY", grazing, fresnel)),
    )
    reduction = math(
        tree, "DIVIDE", 1, math(tree, "ADD", math(tree, "POWER", roughness, 4), 1)
    )
    mip = math(
        tree,
        "MULTIPLY",
        math(
            tree,
            "MULTIPLY",
            roughness,
            math(tree, "SUBTRACT", 1.7, math(tree, "MULTIPLY", roughness, 0.7)),
        ),
        6,
    )
    value = None
    for probe, weight in probes:
        max_mip = len(probe["levels"]) - 1
        sample_mip = math(tree, "MINIMUM", mip, float(max_mip))
        for index, image in enumerate(probe["levels"]):
            texture = tree.nodes.new("ShaderNodeTexEnvironment")
            texture.image = image
            texture.label = "Authored prefiltered probe mip " + str(index)
            tree.links.new(reflected, texture.inputs["Vector"])
            blend = math(
                tree,
                "MAXIMUM",
                math(
                    tree,
                    "SUBTRACT",
                    1,
                    math(
                        tree,
                        "ABSOLUTE",
                        math(tree, "SUBTRACT", sample_mip, float(index)),
                    ),
                ),
                0,
            )
            factor = math(
                tree, "MULTIPLY", blend, float(weight * probe["record"]["intensity"])
            )
            color = scale(texture.outputs["Color"], factor)
            value = color if value is None else vector(tree, "ADD", value, color)
    return scale(vector(tree, "MULTIPLY", value, fresnel_color), reduction)
