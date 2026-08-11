"""EndField CharacterNPR materials → generated Ruri Uber node groups, RAW feed.

``ruri_character_uber_endfield.py`` next door is GENERATED (AzureNihil C# shader
stack → ``Ruri.CodeGen.Blender``; regenerate with ``Ruri.App --codegen-only``,
never hand-edit). It speaks the **raw HGRP dialect**: every input socket and
every texture key is the game's own property name (_MetallicGlossMap /
_SDFLightmap / _Smoothness / _CharacterParams* / ...), with the pipeline-side
adaptations (RMOS channel order, smoothness↔roughness inversion, slot aliasing)
baked into the generated graphs themselves.

So this provider is a dumb pump, exactly as raw as the .mat:
  textures  → the cloned tree's texture nodes, matched by slot name
  floats    → group-instance socket ``property_name``
  colors    → sockets ``property_name`` (+ ``_w`` for the 4th component)
  tiling    → sockets ``property_name + '_ST'``

ONE node group per part, FULLY INLINED (no sub-groups), fixed-named, built once
by ``gen.ensure(part)`` and kept with a fake user. A material cannot share that
group -- Blender has no image socket, so textures are nodes inside the tree --
but it can CLONE it (a C-level datablock copy, 0.025s vs 1-3s to rebuild) and
swap the image datablocks, which is what this module does.

The only judgement calls here are the variant (which part) and opaque-vs-
transparent; both are read off the material's own data, and both are documented
at their decision site with the ground truth they came from.
"""

from __future__ import annotations

import bpy

from .... import material_builder
from . import ruri_character_uber_endfield as gen

# ============================================================================
# 变体判定 = shader 身份,不是属性指纹。
#
# .mat 的 m_Shader 引用着它真正用的 shader;闭包里必有该 shader 资产,其文本
# 第一行就是自称名(真源 E:\Temp\AllShader_1.4.4 逐字):
#   Shader "HGRP/CharacterNPR"                → cloth uber(_UseCharacterFur 值 1 ⇒ Fur,否则 Standard)
#   Shader "HGRP/CharacterNPR_Skin"           → Face
#   Shader "HGRP/CharacterNPR_Eye"            → Eyes(眉毛同此 shader)
#   Shader "HGRP/CharacterNPR_Hair"           → Hair
#   Shader "HGRP/CharacterNPR_VFX"            → VFX
#   Shader "HGRP/CharacterNPR_OverlayShadow"  → OverlayShadow
#   Shader "HGRP/CharacterNPR_LiquidAg"       → LiquidAg
#   Shader "HGRP/CharacterNPR_ProxyLod" / "_ShadowReceiver" → 非着色 part,不认领
#
# 🔴 属性指纹判变体已废除并禁止回退:Unity m_SavedProperties 会累积材质历史上
#   用过的全部键,「键存在」证明不了任何事(实锤:cloth 全带 _SkinRimOffScale,
#   整套布料被吃成 Face 跑脸部 SDF,全身发暗)。shader 引用是唯一真源;
#   解析不到 = 闭包丢了依赖,响亮报错,禁止猜。
# ============================================================================
_SHADER_PARTS = {
    "HGRP/CharacterNPR_Skin": ("Face", 1),
    "HGRP/CharacterNPR_Eye": ("Eyes", 2),
    "HGRP/CharacterNPR_Hair": ("Hair", 3),
    "HGRP/CharacterNPR_VFX": ("VFX", 6),
    "HGRP/CharacterNPR_OverlayShadow": ("OverlayShadow", 7),
    "HGRP/CharacterNPR_LiquidAg": ("LiquidAg", 8),
}
_NON_SHADING = ("HGRP/CharacterNPR_ProxyLod", "HGRP/CharacterNPR_ShadowReceiver")

_shader_name_cache = {}


def _shader_name(builder, props):
    """m_Shader 引用的 shader 自称名(闭包内 shader 资产文本的 Shader "..." 行)。"""
    ref = props.shader_ref if isinstance(props.shader_ref, dict) else None
    guid = (ref or {}).get("guid")
    if not guid:
        return None
    guid = guid.lower()
    if guid in _shader_name_cache:
        return _shader_name_cache[guid]
    name = None
    text = builder.db._text(guid) if hasattr(builder.db, "_text") else None
    if text:
        head = text.lstrip()
        if head.startswith("Shader"):
            first = head.split("\n", 1)[0]
            quote = first.find('"')
            if quote >= 0:
                name = first[quote + 1:first.find('"', quote + 1)]
    _shader_name_cache[guid] = name
    return name


def _variant(builder, props):
    """(part 名, part id),非角色着色 shader 返回 None(→ Principled)。"""
    name = _shader_name(builder, props)
    if name is None:
        ref = props.shader_ref if isinstance(props.shader_ref, dict) else {}
        print("[ruri-uber] !! 0DAY: material '{0}' 的 shader 引用 {1} 在闭包里解析不到 "
              "—— 闭包丢了 shader 依赖,拒绝按指纹猜,落 Principled".format(
                  props.name, ref.get("guid")), flush=True)
        return None
    if name == "HGRP/CharacterNPR":
        return ("Fur", 4) if props.floats.get("_UseCharacterFur") else ("Standard", 0)
    if name in _SHADER_PARTS:
        return _SHADER_PARTS[name]
    if name in _NON_SHADING:
        print("[ruri-uber] '{0}' 用 {1}(非着色 part),不认领".format(props.name, name), flush=True)
    return None

_TRANSPARENT_PARTS = {"Fur", "VFX", "OverlayShadow"}

# One uber group per part, fully inlined (no sub-groups), fixed-named, built
# once and kept with a fake user -- ``gen.ensure(part)`` rebuilds only when the
# generated code's fingerprint changes. Textures cannot be sockets in Blender,
# so a material cannot SHARE the group; it CLONES it (0.025s, a C-level
# datablock copy) and swaps the image datablocks. Building costs ~1-3s because
# Blender's nodes.new is O(n²) in tree size, so it must not happen per material.
def _clone_uber(part, material_name, images):
    """Clone this part's template group and point every texture node at this
    material's own image.

    No side table maps a texture node back to its logical slot: the template's
    placeholder image IS named after the slot (the generated ``g.tex`` creates
    ``bpy.data.images[slot]``), so each node carries its own identity. The
    placeholder's colorspace carries the generator's data-vs-colour decision for
    that sampling site, so the real image inherits it."""
    template = gen.ensure(part)
    clone = template.copy()
    # 重导幂等:同名旧克隆组直接换血删除(它唯一的用户就是本材质的旧树,马上会被整树重建)。
    stale = bpy.data.node_groups.get("Uber " + material_name)
    if stale is not None:
        stale.user_remap(clone)
        bpy.data.node_groups.remove(stale)
    clone.name = "Uber " + material_name
    for node in clone.nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        real = images.get(node.image.name)
        if real is None:
            continue
        non_color = node.image.colorspace_settings.name == "Non-Color"
        node.image = real
        if non_color:
            try:
                real.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
    return clone


def _drive_from_sun(node):
    """Drive the uber's Sun_* inputs off the scene's own sun lamp, so adding or
    rotating a light actually relights the character.

    A shader graph cannot read scene lights (there is no "light direction" node --
    that information only exists inside the render engine's own loop), so the NPR
    kernels take the sun as plain inputs. Leaving them at a baked default is what
    made lights do nothing. Drivers close the gap: the direction components and
    the colour follow the lamp live, and rotating it re-evaluates because each
    driver declares the lamp's own rotation as its variable."""
    sun = next((o for o in bpy.context.scene.objects
                if o.type == "LIGHT" and o.data.type == "SUN"), None)
    if sun is None:
        return False
    direction = node.inputs.get("Sun_Direction")
    color = node.inputs.get("Sun_Color")
    if direction is None:
        return False
    for index in range(3):
        fcurve = direction.driver_add("default_value", index)
        driver = fcurve.driver
        driver.type = "SCRIPTED"
        # +Z of the lamp = toward the light (Blender suns shine down their -Z).
        driver.expression = "ruri_sun_dir({0})".format(index)
        var = driver.variables.new()
        var.name = "rot"
        var.type = "TRANSFORMS"
        var.targets[0].id = sun
        var.targets[0].transform_type = "ROT_Z"
    if color is not None:
        for index in range(3):
            fcurve = color.driver_add("default_value", index)
            driver = fcurve.driver
            driver.type = "SCRIPTED"
            driver.expression = "ruri_sun_color({0})".format(index)
            var = driver.variables.new()
            var.name = "energy"
            var.type = "SINGLE_PROP"
            var.targets[0].id_type = "LIGHT"
            var.targets[0].id = sun.data
            var.targets[0].data_path = "energy"
    return True


def _sun_dir(index):
    sun = next((o for o in bpy.context.scene.objects
                if o.type == "LIGHT" and o.data.type == "SUN"), None)
    if sun is None:
        return (0.0, 0.0, 1.0)[index]
    return sun.matrix_world.to_3x3().col[2].normalized()[index]


def _sun_color(index):
    sun = next((o for o in bpy.context.scene.objects
                if o.type == "LIGHT" and o.data.type == "SUN"), None)
    if sun is None:
        return 1.0
    # Sun "Strength" is irradiance in W/m²; the NPR kernels want a plain
    # multiplier, and the game's own default main light sits at ~1.
    return sun.data.color[index] * max(sun.data.energy, 0.0)


def register_drivers():
    bpy.app.driver_namespace["ruri_sun_dir"] = _sun_dir
    bpy.app.driver_namespace["ruri_sun_color"] = _sun_color


def _standard_view_transform(scene):
    """The uber graph ends on the game's OWN tonemap (HGRP lutbuilder2d's
    ACES_modified, inlined by the generator), so its output is already display
    linear. Blender's default AgX would then map an already-mapped image --
    the second pass crushes and desaturates everything, which reads as "dark
    and metallic" on cloth. Standard is the pass-through the graph expects."""
    if scene is None or scene.view_settings.view_transform == "Standard":
        return False
    scene.view_settings.view_transform = "Standard"
    try:
        scene.view_settings.look = "None"
    except Exception:
        pass
    return True


def _load_raw_images(builder, props):
    """Every texture the .mat binds, under its own property name. Colorspace is
    decided by the generated graphs per sampling slot (non_color baked at
    generation); alpha is re-enabled as data -- the host loader disables alpha
    interpretation for Principled use, but the uber kernels consume .a."""
    images = {}
    for name, guid in props.textures.items():
        img = builder._load_image(guid)
        if img is None:
            # .mat 明确引用却解不出图 = 信息丢失,必须大声——静默落占位曾把整脸 SDF 吞成黑。
            print("[ruri-uber] !! {0}: texture {1} guid={2} LOAD FAILED -- placeholder stays".format(
                props.name, name, guid), flush=True)
            continue
        try:
            img.alpha_mode = "CHANNEL_PACKED"
        except Exception:
            pass
        images[name] = img
    return images


def provider(builder, props):
    """material_builder graph provider: claims materials by their m_Shader
    identity (the shader asset's own Shader "HGRP/CharacterNPR*" declaration),
    declines everything else (None → Principled)."""
    resolved = _variant(builder, props)
    if resolved is None:
        return None
    part_name, part_id = resolved
    name = props.name or "CharacterNPR"
    images = _load_raw_images(builder, props)
    if _standard_view_transform(bpy.context.scene):
        print("[ruri-uber] view transform -> Standard (graph carries the game's own tonemap)")

    # Opaque vs transparent is the GAME's own rule, read off the compiled shader:
    #   characternpr_eye  Sub0_Pass0_Fragment:1014  outColor.w = (_SurfaceType == 1.0) ? a : 1.0
    #   characternpr_skin Sub0_Pass0_Fragment:1431  _3324.w = 1.0f            (hard 1)
    # An Opaque face outputs alpha 1 no matter what the base map's alpha holds --
    # that channel feeds NPR shadow weighting (Endfield_Face reads it as
    # minShadow/combWeight), and the gBuffer0.w it lands in is materialFlags, not
    # opacity. Wiring it to opacity is what made skin/eyes render fully invisible.
    # Fur/VFX/OverlayShadow are forced transparent by the conversion config's
    # part post-actions, which is the same call the Unity port makes.
    opaque = (props.floats.get("_SurfaceType", 0.0) < 0.5
              and part_name not in _TRANSPARENT_PARTS)

    clone = _clone_uber(part_name, name, images)
    # 就地改写同名材质(网格按名字绑材质;另起新料会得 .001 后缀,原名材质带着兜底节点继续占插槽)。
    # build_material 整树清空重建,幂等。
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    grp = gen.build_material(mat, clone.name, opaque=opaque,
                             multiply_blend=(part_name == "OverlayShadow"))

    # ── raw direct feed ──────────────────────────────────────────────────────
    filled = 0
    def set_socket(sock_name, value):
        nonlocal filled
        sock = grp.inputs.get(sock_name)
        if sock is None:
            return
        if sock.type == "VECTOR":
            sock.default_value = value if isinstance(value, tuple) else (value, value, value)
        else:
            sock.default_value = value[0] if isinstance(value, tuple) else value
        filled += 1

    for prop, value in props.floats.items():
        set_socket(prop, float(value))
    for prop, value in props.colors.items():
        set_socket(prop, (float(value[0]), float(value[1]), float(value[2])))
        w = grp.inputs.get(prop + "_w")
        if w is not None:
            w.default_value = float(value[3])
    for prop, st in props.texture_st.items():
        set_socket(prop + "_ST", (float(st[0]), float(st[1]), float(st[2])))
        w = grp.inputs.get(prop + "_ST_w")
        if w is not None:
            w.default_value = float(st[3])

    # ── variant configuration (the one non-raw datum: the part dispatcher) ───
    set_socket("_CharaPartID", float(part_id))

    # Switches the HGRP side does not have: transparent effect parts don't
    # declare _SurfaceType (their game shaders ARE transparent by pass state),
    # so force the kernel flag; everything else arrives raw from the .mat.
    if part_name in _TRANSPARENT_PARTS:
        set_socket("_SurfaceType", 1.0)

    try:
        mat.surface_render_method = "BLENDED" if part_name in _TRANSPARENT_PARTS else "DITHERED"
    except Exception:
        pass

    _drive_from_sun(grp)

    mat["ruri_uber_part"] = part_name
    ref = props.shader_ref
    mat["ruri_uber_shader_guid"] = str(ref.get("guid", "")) if isinstance(ref, dict) else ""
    mat["ruri_uber_shader"] = _shader_name(builder, props) or ""
    # alpha 载体诊断(depth 24=RGB 无 alpha / 32=RGBA)。这三个槽的 alpha 都是**数据**不是不透明度:
    #   _BaseMap.a / _DiffRampMap.a → NPR 影权 minShadow=min(rampA,baseAlpha)、combWeight;
    #     两者恒 1 ⇒ 表面恒取阴影 LUT 色 = 均匀发暗。
    #   _BumpMap.a → Unity RG/AG 打包法线的 X 分量(内核 nx = s.x*s.w*2-1);
    #     alpha 缺失或为 0 ⇒ 切线法线整体错向,一切依赖法线的项(含 matcap)全废。
    alpha_info = " ".join("{0}:{1}bit".format(slot, images[slot].depth)
                          for slot in ("_BaseMap", "_DiffRampMap", "_BumpMap") if slot in images)
    print("[ruri-uber] {0}: shader={1} part={2} images={3} sockets={4} nodes={5} {6}".format(
        name, mat["ruri_uber_shader"], part_name, len(images), filled, len(clone.nodes), alpha_info))
    return mat


def register():
    register_drivers()
    material_builder.register_graph_provider(provider)


def unregister():
    material_builder.unregister_graph_provider(provider)
