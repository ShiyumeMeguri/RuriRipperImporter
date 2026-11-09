"""Host projection of already resolved static-lightmap and probe bindings."""

import json
import bpy
import numpy as np
from ....Kernel import statement as kernel_statement
from ....Host.Blender.shader_nodes import math_node, vector_node


def _input(tree, socket):
    if socket.is_linked:
        return socket.links[0].from_socket
    value = socket.default_value
    if isinstance(value, (int, float)):
        n = tree.nodes.new("ShaderNodeValue")
        n.outputs[0].default_value = value
    else:
        n = tree.nodes.new("ShaderNodeRGB")
        n.outputs[0].default_value = value
    return n.outputs[0]


def _lightmap(material, node, lm, builder, basis, probes=(), uv_layer="UV1", props=None):
    result = material.copy()
    result.name = material.name + " [baked " + str(node.index) + "]"
    tree = result.node_tree
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bpy.data.materials.remove(result)
        return None
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_layer
    uv.label = (
        "Source UV2 (zero-based channel 1), unchanged"
        if uv_layer == "UV1"
        else "Source UV0 fallback (UV2 absent), unchanged"
    )
    st = lm["st"]
    coord = vector_node(
        tree,
        "ADD",
        vector_node(tree, "MULTIPLY", uv.outputs[0], (st[0], st[1], 1)),
        (st[2], st[3], 0),
    )
    color = tree.nodes.new("ShaderNodeTexImage")
    color.image = builder.load_image(lm["color"])
    if color.image is None:
        raise RuntimeError("Missing lightmap color pixels")
    color.extension = "EXTEND"
    color.label = "Authored linear-HDR lightmap"
    tree.links.new(coord, color.inputs["Vector"])
    lighting = color.outputs["Color"]
    if lm.get("direction"):
        direction = tree.nodes.new("ShaderNodeTexImage")
        direction.image = builder.load_image(lm["direction"])
        if direction.image is None:
            raise RuntimeError("Missing directional lightmap pixels")
        # The fourth channel is directional rebalance DATA, not opacity. The
        # shared byte loader defaults to alpha NONE (Alpha socket then reads 1).
        # Keep the asset-declared colour space and shared image identity; packed
        # alpha exposes the stored scalar without premultiplying RGB or wiring
        # opacity into any material that merely shares this image.
        from ....Host.Blender.material_builder import enable_alpha_channel

        enable_alpha_channel(direction.image)
        direction.extension = "EXTEND"
        tree.links.new(coord, direction.inputs["Vector"])
        direction.label = "Directional lightmap; RGB direction, Alpha rebalance"
        # UnityCG DecodeDirectionalLightmap: dot(n,dir.rgb-.5)+.5,
        # divided by max(1e-4,dir.a). Rotate the normal back to source axes.
        n = (
            _input(tree, bsdf.inputs["Normal"])
            if bsdf.inputs["Normal"].is_linked
            else tree.nodes.new("ShaderNodeNewGeometry").outputs["Normal"]
        )
        combine = tree.nodes.new("ShaderNodeCombineXYZ")
        # Convert the world normal back through the basis declared by core.bases.
        for index, row in enumerate(np.linalg.inv(basis)):
            component = vector_node(tree, "DOT_PRODUCT", n, tuple(float(v) for v in row))
            tree.links.new(component, combine.inputs[index])
        half_lambert = math_node(
            tree,
            "ADD",
            vector_node(
                tree,
                "DOT_PRODUCT",
                combine.outputs[0],
                vector_node(
                    tree, "SUBTRACT", direction.outputs["Color"], (0.5, 0.5, 0.5)
                ),
            ),
            0.5,
        )
        factor = math_node(
            tree,
            "DIVIDE",
            half_lambert,
            math_node(tree, "MAXIMUM", direction.outputs["Alpha"], 1e-4),
        )
        scale = tree.nodes.new("ShaderNodeVectorMath")
        scale.operation = "SCALE"
        tree.links.new(lighting, scale.inputs[0])
        tree.links.new(factor, scale.inputs["Scale"])
        lighting = scale.outputs[0]
    base = _input(tree, bsdf.inputs["Base Color"])
    metallic = _input(tree, bsdf.inputs["Metallic"])
    roughness = _input(tree, bsdf.inputs["Roughness"])
    source_f0 = None
    if props is not None and props.roles.floats.get("surface_specular_workflow", 0):
        source_f0 = builder.specular_color(tree, props)
    diffuse = tree.nodes.new("ShaderNodeVectorMath")
    diffuse.operation = "SCALE"
    tree.links.new(vector_node(tree, "MULTIPLY", base, lighting), diffuse.inputs[0])
    # Unity Standard diffuse reflectivity: 1 - lerp(0.04,1,metallic).
    attenuation = math_node(tree, "MULTIPLY", math_node(tree, "SUBTRACT", 1, metallic), 0.96)
    if source_f0 is not None:
        split = tree.nodes.new("ShaderNodeSeparateXYZ")
        tree.links.new(source_f0, split.inputs[0])
        maximum = math_node(tree, "MAXIMUM", math_node(tree, "MAXIMUM", split.outputs[0], split.outputs[1]), split.outputs[2])
        attenuation = math_node(tree, "SUBTRACT", 1, maximum)
    tree.links.new(attenuation, diffuse.inputs["Scale"])
    # The map already contains bounced lighting. Display it to camera/glossy
    # rays without treating the baked surface as a new diffuse GI emitter.
    path = tree.nodes.new("ShaderNodeLightPath")
    visible_bake = tree.nodes.new("ShaderNodeVectorMath")
    visible_bake.operation = "SCALE"
    tree.links.new(diffuse.outputs[0], visible_bake.inputs[0])
    tree.links.new(
        math_node(tree, "SUBTRACT", 1, path.outputs["Is Diffuse Ray"]),
        visible_bake.inputs["Scale"],
    )
    prior_emission = _input(tree, bsdf.inputs["Emission Color"])
    emission_scale = tree.nodes.new("ShaderNodeVectorMath")
    emission_scale.operation = "SCALE"
    tree.links.new(prior_emission, emission_scale.inputs[0])
    tree.links.new(
        _input(tree, bsdf.inputs["Emission Strength"]), emission_scale.inputs["Scale"]
    )
    tree.links.new(
        vector_node(tree, "ADD", visible_bake.outputs[0], emission_scale.outputs[0]),
        bsdf.inputs["Emission Color"],
    )
    bsdf.inputs["Emission Strength"].default_value = 1
    # Eliminate a second diffuse lighting pass, retain metal colour and
    # dielectric specular response in the native BSDF.
    spec_base = tree.nodes.new("ShaderNodeVectorMath")
    spec_base.operation = "SCALE"
    tree.links.new(base, spec_base.inputs[0])
    tree.links.new(metallic, spec_base.inputs["Scale"])
    tree.links.new(spec_base.outputs[0], bsdf.inputs["Base Color"])
    if probes:
        from .static_reflections import specular

        normal = (
            _input(tree, bsdf.inputs["Normal"])
            if bsdf.inputs["Normal"].is_linked
            else tree.nodes.new("ShaderNodeNewGeometry").outputs["Normal"]
        )
        ibl = specular(tree, base, metallic, roughness, normal, probes, source_f0)
        gated = tree.nodes.new("ShaderNodeVectorMath")
        gated.operation = "SCALE"
        tree.links.new(ibl, gated.inputs[0])
        tree.links.new(
            math_node(tree, "SUBTRACT", 1, path.outputs["Is Diffuse Ray"]),
            gated.inputs["Scale"],
        )
        previous = bsdf.inputs["Emission Color"].links[0].from_socket
        tree.links.new(
            vector_node(tree, "ADD", previous, gated.outputs[0]),
            bsdf.inputs["Emission Color"],
        )
        # A source prefiltered probe replaces native ray-traced indirect specular,
        # not adds another reflection on top of it. These are opaque static draws.
        for socket in ("Metallic", "Base Color"):
            for link in list(bsdf.inputs[socket].links):
                tree.links.remove(link)
        bsdf.inputs["Metallic"].default_value = 0
        bsdf.inputs["Base Color"].default_value = (0, 0, 0, 1)
        bsdf.inputs["Specular IOR Level"].default_value = 0
        result["ruri_reflection_probes"] = json.dumps(
            [
                {"key": p["record"]["key"], "weight": w, "mips": len(p["levels"])}
                for p, w in probes
            ]
        )
        result["ruri_reflection_boundary"] = (
            "Source prefiltered mips and linear Unity BRDF1 IBL; renderer-bounds probe choice approximation."
        )
    result["ruri_lightmap_source"] = lm["color"]
    result["ruri_lightmap_uv_source"] = uv_layer
    result["ruri_lightmap_st"] = list(st)
    result["ruri_lightmap_node"] = node.index
    result["ruri_lightmap_boundary"] = (
        "Authored baked diffuse + native Blender specular; not original BRDF parity."
    )
    return result


def apply(context, statement, built, builder, options):
    records = statement.scene_lighting
    if not records:
        return []
    from ....Host.Blender import world_builder
    bindings = {binding["source_object"]: binding
                for record in records for binding in record["execution"]["renderers"]}
    warnings = [warning for record in records for warning in record["execution"]["warnings"]]
    declared = kernel_statement.basis(statement.basis)
    basis = (declared["root"] @ declared["matrix"])[:3, :3]
    probe_images = {}
    for node in statement.nodes:
        obj = built.get(node.index)
        binding = bindings.get(node.source_object)
        if obj is None or obj.type != "MESH" or binding is None or binding["status"] != "ready":
            continue
        probes = []
        for selected in binding["probes"]:
            levels = []
            for index, keys in enumerate(selected["mips"]):
                identity = (selected["key"], index)
                image = probe_images.get(identity)
                if image is None:
                    face = builder.load_image(keys[0])
                    if face is None:
                        raise ValueError("Missing declared reflection probe image: " + keys[0])
                    image = world_builder.cube_image(keys, builder.load_image, basis,
                        "Authored local reflection mip " + str(index), width=max(16, face.size[0] * 2))
                    probe_images[identity] = image
                levels.append(image)
            probes.append((dict(levels=levels, record=dict(intensity=selected["strength"], key=selected["key"])),
                           selected["weight"]))
        uv_layer = "UVMap" if binding["uv_channel"] == 0 else "UV" + str(binding["uv_channel"])
        for index, slot in enumerate(obj.material_slots):
            key = node.materials[index] if index < len(node.materials) else ""
            if key not in binding["materials"] or slot.material is None:
                continue
            result = _lightmap(slot.material, node, binding, builder, basis, probes, uv_layer,
                               statement.materials[key])
            if result is not None:
                slot.link = "OBJECT"
                slot.material = result
    text = bpy.data.texts.new("RURI_STATIC_LIGHTING.json")
    text.write(json.dumps(records, ensure_ascii=False, indent=2))
    return warnings
