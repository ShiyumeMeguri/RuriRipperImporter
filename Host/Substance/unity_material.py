# -*- coding: utf-8 -*-
"""Unity ``Material`` -> EndField_Uber.glsl shader-instance uniforms + an
explicit texture job list.

This is the in-process, YAML-direct replacement for the old BuildSPInputs.py
pre-pass: instead of regexing a .mat on disk, writing a folder of split PNGs
and a params.json, and having the Painter plugin re-derive everything from
those filenames, the material document is read straight out of the resolved
closure and turned into

  * ``uniforms``      -- the exact uniform table alg.shaders.setParameters takes
                         (names identical to the GLSL uniforms, part-specialised
                         defaults applied), and
  * ``channel_jobs`` / ``param_jobs`` -- explicit (source texture guid, decode
                         operation, destination) triples.

Nothing round-trips through a filename convention any more, so the "which
suffix meant which channel" guessing layer is gone entirely; the destination is
stated at the point the source property is read.

Every table, default and inference rule below is a faithful port of
BuildSPInputs.build_endfield_entry / infer_chara_part and of the Painter
plugin's own channel/param maps -- the shader consuming them is unchanged.
"""

from __future__ import annotations


PART_NAMES = {
    0: "Standard", 1: "Face", 2: "Eyes", 3: "Hair",
    4: "Fur", 5: "Eyebrow", 6: "VFX", 7: "OverlayShadow",
    8: "ShadowReceiver",
}

# ---------------------------------------------------------------------------
# Texture decode operations (executed by texture_pipeline)
# ---------------------------------------------------------------------------
OP_COPY_RGB = "copy_rgb"                # RGB verbatim (drops alpha)
OP_COPY_RGBA = "copy_rgba"              # RGBA verbatim
OP_CHANNEL_R = "channel_r"
OP_CHANNEL_G = "channel_g"
OP_CHANNEL_B = "channel_b"
OP_CHANNEL_A = "channel_a"
OP_INVERT_A = "invert_a"                # 255 - A  (Smoothness -> Roughness)
OP_NORMAL_UNITY = "normal_unity"        # Unity RG/AG (X=R*A, Y=G) -> OpenGL RGB
OP_NORMAL_SPLIT_RG = "normal_split_rg"  # hair diffuse normal from R,G (no AG)
OP_NORMAL_SPLIT_BA = "normal_split_ba"  # hair specular normal from B,A (no AG)

# Painter channel identifiers (the JS alg.texturesets.addChannel token) and the
# Python ChannelType candidates for the same channel, plus the storage format.
CHANNELS = {
    #  key             (ChannelType candidates,             js token,            js format, srgb, label)
    "basecolor":       (("BaseColor",),                     "basecolor",         "sRGB8",   True,  None),
    "opacity":         (("Opacity",),                       "opacity",           "L8",      False, None),
    "metallic":        (("Metallic",),                      "metallic",          "L8",      False, None),
    "specularlevel":   (("SpecularLevel", "Specularlevel"), "specularlevel",     "L8",      False, None),
    "roughness":       (("Roughness",),                     "roughness",         "L8",      False, None),
    # Standard parallax height (Unity _ParallaxTex); raw sRGB bytes, the shader
    # still decodes them -- Height is a data channel, Painter does not colour-manage it.
    "height":          (("Height",),                        "height",            "L8",      False, None),
    "normal":          (("Normal",),                        "normal",            "RGB16F",  False, None),
    "emissive":        (("Emissive",),                      "emissive",          "sRGB8",   True,  None),
    # RMOS .b shadow mask -> AO channel (EndField_Uber [H1]: shadowMask = getAO).
    "ambientocclusion": (("AO", "AmbientOcclusion", "Ao"),  "ambientOcclusion",  "L8",      False, None),
    # ClearCoat mask -> User1 (paintable).
    "user1":           (("User1",),                         "user1",             "L8",      False, "ClearCoatMask"),
    # _EmissionMap.a -> Emission 呼吸遮罩(emissive 通道只有 RGB,装不下)。
    "user2":           (("User2",),                         "user2",             "L8",      False, "EmissionBreathMask"),
}

# Unity texture property -> (destination channel, decode op). These are the
# engine channels Painter can paint/bake.
CHANNEL_SOURCES = {
    "_BaseMap": (("basecolor", OP_COPY_RGB), ("opacity", OP_CHANNEL_A)),
    "_MetallicGlossMap": (("metallic", OP_CHANNEL_R),
                          ("specularlevel", OP_CHANNEL_G),
                          ("ambientocclusion", OP_CHANNEL_B),
                          ("roughness", OP_INVERT_A)),
    "_EmissionMap": (("emissive", OP_COPY_RGB), ("user2", OP_CHANNEL_A)),
    "_BumpMap": (("normal", OP_NORMAL_UNITY),),
    # Hair: RG = diffuse normal (paintable/bakeable), BA = specular normal
    # (a shader sampler -- see TEXTURE_PARAMS).
    "_SplitNormalMap": (("normal", OP_NORMAL_SPLIT_RG),),
    "_ParallaxTex": (("height", OP_CHANNEL_R),),
    "_ClearCoatMask": (("user1", OP_CHANNEL_R),),
}

# Unity texture property -> (EndField_Uber sampler uniform, decode op).
# Data/LUT/view-space/SDF/scrolling maps stay samplers: Painter force-colour-
# manages RGB channels, which would corrupt linear data, and several are simply
# not paintable.
TEXTURE_PARAMS = {
    "_DiffRampMap":        ("_RampMap",            OP_COPY_RGBA),
    "_SpecRampMap":        ("_SpecRampMap",        OP_COPY_RGBA),
    "_ShadowLutTex":       ("_ShadowLutTex",       OP_COPY_RGBA),
    "_SDFLightmap":        ("_SDFLightmap",        OP_COPY_RGBA),
    "_SDFMask":            ("_SDFMask",            OP_COPY_RGBA),
    "_EmotionMap":         ("_EmotionMap",         OP_COPY_RGBA),
    "_HighlightMap":       ("_HighlightMap",       OP_COPY_RGBA),
    "_MatcapTex":          ("_MatcapTex",          OP_COPY_RGBA),
    "_SplitNormalMap":     ("_SpecNormalMap",      OP_NORMAL_SPLIT_BA),
    # 丝袜遮罩:R 各向异性强度 / G 锐利度 / B 湿身光滑度 / A 透肉度 —— 四个通道
    # 都是数据,Painter 会对 RGB 强制色彩管理,所以走 sampler 而不是引擎通道。
    "_SilkStockingsMask":  ("_SilkStockingsMask",  OP_COPY_RGBA),
    "_StylizedFresnelNoiseMap": ("_StylizedFresnelNoiseMap", OP_COPY_RGBA),
    # UV1 的 Alpha(R)/Root(G)/Depth(B)/ID(A) —— 四通道全是数据。
    "_ExtraAlphaMask":     ("_ExtraAlphaMask",     OP_COPY_RGBA),
    # liquidag 拿 _OutlineColorMap 当液体附着的细节法线(RG)+ 高度(B),
    # 不是描边贴图 —— 见 shader 里 "液体附着 liquidag" 一段。
    "_OutlineColorMap":    ("_OutlineColorMap",    OP_COPY_RGBA),
    "_ErosionNormalSmoothnessMap": ("_ErosionNormalSmoothnessMap", OP_COPY_RGBA),
    "_ErosionPatternMap":  ("_ErosionPatternMap",  OP_COPY_RGBA),
    "_PuppetPatternMap":   ("_PuppetPatternMap",   OP_COPY_RGBA),
    "_DissolveTex":        ("_DissolveTex",        OP_COPY_RGBA),
    "_FaceDecalMap":       ("_FaceDecalMap",       OP_COPY_RGBA),
    "_StrokeMap":          ("_StrokeMap",          OP_COPY_RGBA),
    "_LineMap":            ("_LineMap",            OP_COPY_RGBA),
    "_FurMap":             ("_FurMap",             OP_COPY_RGBA),
    "_FurDirMap":          ("_FurDirMap",          OP_COPY_RGBA),
    "_FurDyeMap":          ("_FurDyeMap",          OP_COPY_RGBA),
    "_VFXSpecialMainTex":  ("_VFXSpecialMainTex",  OP_COPY_RGBA),
    "_VFXSpecialBlendTex": ("_VFXSpecialBlendTex", OP_COPY_RGBA),
    "_MainTex":            ("_VFXMainTex",         OP_COPY_RGBA),
    "_MaskTex":            ("_VFXMaskTex",         OP_COPY_RGBA),
    "_BlendTex":           ("_VFXBlendTex",        OP_COPY_RGBA),
    "_DisturbTex1":        ("_VFXDisturbTex",      OP_COPY_RGBA),
    "_NormalMap":          ("_VFXNormalMap",       OP_COPY_RGBA),
}

# Reference features with no toggle property: the shader samples the map
# whenever the material binds one, so the GLSL bool is derived from presence.
TEXTURE_PRESENCE_BOOLS = {
    "_ExtraAlphaMask": "u_ExtraAlphaMask",
}

# Unity properties that carry no consumer on the Painter side at all -- each
# with the evidence, so the list stays honest rather than convenient.
IGNORED_TEXTURES = ("_OutlineMask", "_HairBrowMask")

#: Scalars the 1.4.4 reference declares but no ForwardLit fragment ever reads.
#: Verified across all 3184 compiled dumps, not assumed.
IGNORED_FLOATS = {
    "_FurColorEnable": "declared in characternpr.shader but referenced by ZERO compiled "
                       "dumps -- a dead property in 1.4.4",
    "_FurColor": "same as _FurColorEnable: zero references in any compiled dump",
    "_FurGravityStrength": "vertex stage only (shell extrusion); [H10] already states "
                           "Painter cannot do multi-shell fur",
    "_ResponsiveTransparency": "declared in characternpr.shader but never read by ANY "
                               "compiled shader (0 non-declaration uses in 3184 dumps)",
    "_HairBrowMaskThreshold": "only read by Pass1 CharacterOutline / Pass2 "
                              "DepthOnlyOutline / Pass3 PreGBuffer -- never by Pass0 "
                              "ForwardLit, which is the only pass Painter renders",
}

# Scalar/toggle defaults (from the HGRP_*_Fix.shader Properties blocks); used
# when the .mat omits the property. Anything not listed defaults to 0.
FLOAT_DEFAULTS = {
    "_BumpScale": 1.0, "_Metallic": 0.0, "_Specular": 1.0, "_Smoothness": 0.5,
    "_ShadowColorBrightness": 0.5, "_ShadowColorSaturation": 1.0,
    "_SpecRampIridescentMode": 0.0, "_AlphaPremultiply": 0.0,
    "_EmissionBrightness": 1.0,
    "_SkinRimOffScale": 0.5, "_FaceRimOffScale": 1.0,
    "_EmotionIndex": 0.0, "_EmotionBlend": 1.0,
    "_MatcapNormalScale": 1.0, "_ParallaxScale": 0.3,
    "_ParallaxMarchNum": 2.0,
    "_SpecBumpScale": 1.0,
    # characternpr (Standard) 的 _ANISOTROPY_SPECULAR_ON 一套 —— 与下面 hair
    # 的 _AnisotropyValue/_AnisotropyDirX 是两个不同 shader 的不同功能。
    # 参考默认 2 = Unity Cull Back = 只画正面。
    "_Cull": 2.0,
    "_DitherSphereRadius": 0.0, "_DitherSphereSmoothness": 0.1,
    "_DepthFadeValue": 0.0, "_DepthFadeExp": 1.0,
    "_DisableRainEffectOnMaterial": 0.0,
    "_VFXMainUVSet": 0.0, "_VFXScreenUVUseDepth": 0.0, "_VFXFresnelUseNormalMap": 0.0,
    "_FaceDecalSize": 0.2, "_FaceDecalBrightnessMask": 0.7,
    "_FaceDecalMirrorSplit": 0.5, "_FaceDecalCenterX": 0.0, "_FaceDecalCenterY": 0.0,
    "_FaceDecalRotation": 0.0, "_FaceDecalMirrorMode": 0.0,
    "_FaceDecalInvertX": 0.0, "_FaceDecalInvertY": 0.0,
    "_HairBrowMaskThreshold": 0.5,
    "_AlphaClipThreshold": 0.5, "_ViewFade": 0.0,
    # Part 8 ShadowReceiver -- 参考 characternpr_shadowreceiver.shader 的原值
    "_CircleFadeDistance": 0.5, "_CircleFadeSmoothness": 0.0,
    # [H21] 描边片元程序 -- 参考 characternpr.shader 的原值
    "_OutlineColorBrightness": 0.5, "_OutlineColorSaturation": 1.5,
    "_DissolveEdgeSharp": 1.0, "_DissolveScheduleOffset": 0.0,
    "_DissolveEmissiveEdge": 0.0, "_CutOffPosY": 0.0,
    "_PuppetMaskLocationDown": 0.1, "_PuppetMaskLocationTop": 0.5,
    "_PuppetMaskSmooth": 0.1, "_PuppetPatternTintEdgeLocation": 1.0,
    "_PuppetMetallic": 0.0, "_PuppetRoughness": 1.0,
    "_PuppetPDCurveDistortSpeed": 0.5, "_PuppetPDCurveDistortPeriodSpeed": 0.5,
    "_PuppetPDCurveEdgeLocation": 0.3,
    "_ErosionMetallic": 0.5, "_ErosionSmoothnessBias": 0.0, "_ErosionNormalScale": 1.0,
    "_ErosionBaseRootColorLocation": 0.1, "_ErosionBaseRootColorSmooth": 0.1,
    "_ErosionBaseTopColorLocation": 0.7, "_ErosionBaseTopColorSmooth": 0.1,
    "_EmissionAlphaBrightBreathSpeed": 1.0,
    "_EmissionAlphaBrightBreathScaleMin": 0.0,
    "_EmissionAlphaBrightBreathScaleMax": 1.0,
    "_EnemyHitFlashInnerRadius": 0.0, "_EnemyHitFlashOuterRadius": 2.0,
    "_EnemyHitFlashFresnelBias": 0.0, "_EnemyHitFlashFresnelAffectOpacity": 1.0,
    "_EnemyHitFlashNormalScale": 1.0, "_EnemyHitFlashBrightColorAdjust": 1.0,
    "_EnemyHitFlashFresnelColorAdjust": 1.0,
    "_StylizedFresnelPow": 2.0, "_StylizedFresnelAmount": 2.0,
    "_StylizedFresnelNoiseSpeed": 0.0, "_StylizedNoiseContrast": 1.0,
    "_AnisotropyDirectionMain": 0.0,
    "_AnisotropyIntensityMultiplier": 1.0, "_AnisotropyDirectionAdditional": 0.0,
    "_AnisotropyOffsetAdditional": 0.0,
    "_AnisotropyValue": 0.7, "_AnisotropyValue2": 0.712, "_AnisotropyDirX": 0.0,
    "_AnisotropyIntensity": 2.0, "_AnisotropyEdgeFade": 3.0, "_AnisotropyRange2": 0.5,
    "_StrokeScale": 1.0, "_UseLineMap": 1.0, "_LineAmount": 300.0,
    "_LineValue": 0.58, "_LineRange": 0.93, "_LineIntensity": 0.3, "_LineSaturation": 1.7,
    "_FurLengthIntensity": 0.7, "_FurCutoffStart": 0.0, "_FurCutoffEnd": 1.0,
    "_FurAO": 1.0, "_FurEdgeFade": 0.4, "_FurTTIntensity": 0.0,
    "_FurDirMapEnable": 0.0, "_FurSharpen": 0.0, "_FurNoise": 0.0, "_FurDyeIntensity": 1.0,
    "_VFXColorIntensity": 1.0, "_VFXColorAlpha": 1.0, "_UseVFXMainTexAsAlpha": 0.0,
    "_VFXSpecialBlendTexRForDisturb": 1.0, "_VFXFresnelBias": 0.0,
    "_VFXFresnelAffectOpacity": 1.0, "_VFXFresnelPower": 1.0, "_VFXFresnelFlip": 0.0,
    "_SpecialDissolveScheduleOffset": 0.0,
    "_EnableVFXColorAdjustment": 0.0, "_ColorAdjustmentBrightness": 1.0,
    "_ColorAdjustmentSaturation": 1.0, "_ColorAdjustmentContrast": 1.0,
    "_ColorAdjustmentRimWidth": 0.35, "_ColorAdjustmentRimIntensity": 4.0,
    "_UseGrayAsAlpha": 1.0,
    "_ClearCoatSmoothness": 0.95, "_ClearCoatMetallic": 0.0, "_ClearCoatNormalMode": 0.0,
    # SilkStockings (1.4.4 characternpr.shader Properties 原值)
    "_SilkStockingsMinAffect": 0.05, "_SilkStockingsMaxAffect": 0.9,
    "_SilkStockingsAdvance": 0.0, "_SilkStockingsAnisoDirection": 0.0,
    "_SilkStockingsSpecularInt": 5.0, "_SilkStockingsSpecularMinAtMinWetness": 0.0,
    "_SilkStockingsSpecularFalloff": 0.8, "_SilkStockingsSpecularValue": 2.0,
    "_SilkStockingsRainWetMaskScale": 0.7, "_SilkStockingsAlbedoAffectType": 0.5,
    # VFX part (HGRP_CharacterNPR_VFX_Fix)
    "_BlendMode": 1.0, "_TintColorIntensity": 1.0, "_TintColorAlpha": 1.0,
    "_IgnorePostExposure": 1.0, "_UseMainTexAsAlpha": 1.0, "_MainTexUseDisturb": 1.0,
    "_MainTexUVRotate": 0.0, "_UseMaskTexAsAlpha": 1.0, "_MaskTexUseDisturb": 0.0,
    "_MaskTexUVRotate": 0.0, "_BlendTexUseDisturb": 0.0, "_BlendTexUVRotate": 0.0,
    "_DisturbUVRotate1": 0.0, "_Bi_Disturb": 0.0, "_DisturbTex1Normal": 0.0,
    "_DisturbUIntensity1": 0.0, "_DisturbVIntensity1": 0.0,
    "_NormalMapUVRotate": 0.0, "_NormalScale": 1.0, "_NormalMapUseDisturb": 1.0,
    "_FresnelBias": 0.0, "_FresnelAffectOpacity": 1.0, "_FresnelPower": 1.0,
    "_FresnelFlip": 0.001, "_UseNearCameraFade": 0.0,
    "_NearCameraFadeDistanceStart": 0.001, "_NearCameraFadeDistanceEnd": 10.0,
    "_NearCameraFadeDistanceEnd2": 100.0, "_NearCameraFadeDistanceStart2": 120.0,
}

# Part-specialised PBR scalars (the HGRP variants each declare their own).
# Fallbacks for when the .mat does not carry the scalar at all. These are the
# reference Properties defaults, read off each part's own .shader -- NOT tuned
# values. A wrong _Metallic fallback is not a small error: metallic drives both
# specColor = lerp(0.04, albedo, m) and diffColor = (1-m)*0.96*albedo, so a
# large default crushes the diffuse and blows up the specular, which reads as a
# glazed/ceramic surface.
#   characternpr / _skin / _hair / _liquidag: _Metallic 0, _Specular 1, _Smoothness 0.5
#   characternpr_eye:                         _Metallic 0, _Specular 0, _Smoothness 0.5
PART_PBR_DEFAULTS = {
    0: {"_Metallic": 0.0, "_Specular": 1.0, "_Smoothness": 0.5},   # characternpr
    1: {"_Metallic": 0.0, "_Specular": 1.0, "_Smoothness": 0.5},   # characternpr_skin
    2: {"_Metallic": 0.0, "_Specular": 0.0, "_Smoothness": 0.5},   # characternpr_eye
    3: {"_Metallic": 0.0, "_Specular": 1.0, "_Smoothness": 0.5},   # characternpr_hair
    4: {"_Metallic": 0.0, "_Specular": 1.0, "_Smoothness": 0.5},   # characternpr (Fur)
    5: {"_Metallic": 0.0, "_Specular": 0.0, "_Smoothness": 0.5},   # characternpr_eye
}

# Floats the reference declares with [Gamma]. Unity converts these through
# GammaToLinearSpace on upload in a linear-colour-space project, exactly as it
# does for [Gamma] colours -- passing the raw .mat number instead makes the
# value far too high (0.5 -> 0.214 is a 2.3x error on metallic).
GAMMA_FLOAT_PROPS = ("_Metallic",)

FLOAT_IDENTITY = [
    "_BumpScale", "_Metallic", "_Specular", "_Smoothness",
    "_ShadowColorBrightness", "_ShadowColorSaturation", "_SpecRampIridescentMode",
    "_AlphaPremultiply", "_EmissionBrightness", "_BackFaceNormalFlip",
    # _CubemapIntensity is deliberately absent: it is a Painter-side [H6]
    # compensation knob, not a reference property, so writing it on every
    # import would reset whatever the artist dialled in.
    "_SkinRimOffScale", "_FaceRimOffScale", "_EmotionBlend",
    "_MatcapNormalScale", "_SpecBumpScale",
    "_ParallaxMarchNum", "_ParallaxScale",
    "_Cull", "_DitherSphereRadius", "_DitherSphereSmoothness",
    "_DepthFadeValue", "_DepthFadeExp",
    "_DisableRainEffectOnMaterial",
    "_VFXMainUVSet", "_VFXScreenUVUseDepth", "_VFXFresnelUseNormalMap",
    "_FaceDecalSize", "_FaceDecalBrightnessMask", "_FaceDecalMirrorSplit",
    "_FaceDecalCenterX", "_FaceDecalCenterY", "_FaceDecalRotation",
    "_FaceDecalMirrorMode", "_FaceDecalInvertX", "_FaceDecalInvertY",
    "_AlphaClipThreshold", "_ViewFade",
    "_CircleFadeDistance", "_CircleFadeSmoothness",
    "_OutlineColorBrightness", "_OutlineColorSaturation",
    "_DissolveEdgeSharp", "_DissolveScheduleOffset", "_DissolveEmissiveEdge",
    "_CutOffPosY",
    "_PuppetMaskLocationDown", "_PuppetMaskLocationTop", "_PuppetMaskSmooth",
    "_PuppetPatternTintEdgeLocation", "_PuppetMetallic", "_PuppetRoughness",
    "_PuppetPDCurveDistortSpeed", "_PuppetPDCurveDistortPeriodSpeed",
    "_PuppetPDCurveEdgeLocation",
    "_ErosionMetallic", "_ErosionSmoothnessBias", "_ErosionNormalScale",
    "_ErosionBaseRootColorLocation", "_ErosionBaseRootColorSmooth",
    "_ErosionBaseTopColorLocation", "_ErosionBaseTopColorSmooth",
    "_EmissionAlphaBrightBreathSpeed", "_EmissionAlphaBrightBreathScaleMin",
    "_EmissionAlphaBrightBreathScaleMax",
    "_EnemyHitFlashInnerRadius", "_EnemyHitFlashOuterRadius",
    "_EnemyHitFlashFresnelBias", "_EnemyHitFlashFresnelAffectOpacity",
    "_EnemyHitFlashNormalScale", "_EnemyHitFlashBrightColorAdjust",
    "_EnemyHitFlashFresnelColorAdjust",
    "_StylizedFresnelPow", "_StylizedFresnelAmount",
    "_StylizedFresnelNoiseSpeed", "_StylizedNoiseContrast",
    "_AnisotropyDirectionMain", "_AnisotropyIntensityMultiplier",
    "_AnisotropyDirectionAdditional", "_AnisotropyOffsetAdditional",
    "_AnisotropyValue", "_AnisotropyValue2", "_AnisotropyDirX", "_AnisotropyIntensity",
    "_AnisotropyEdgeFade", "_AnisotropyRange2", "_StrokeScale",
    "_UseLineMap", "_LineAmount", "_LineValue", "_LineRange", "_LineIntensity",
    "_LineSaturation",
    "_FurLengthIntensity", "_FurCutoffStart", "_FurCutoffEnd", "_FurAO", "_FurEdgeFade",
    "_FurTTIntensity", "_FurDirMapEnable", "_FurSharpen", "_FurNoise", "_FurDyeIntensity",
    "_VFXColorIntensity", "_VFXColorAlpha", "_UseVFXMainTexAsAlpha",
    "_VFXSpecialBlendTexRForDisturb", "_VFXFresnelBias", "_VFXFresnelAffectOpacity",
    "_VFXFresnelPower", "_VFXFresnelFlip", "_SpecialDissolveScheduleOffset",
    "_EnableVFXColorAdjustment", "_ColorAdjustmentBrightness", "_ColorAdjustmentSaturation",
    "_ColorAdjustmentContrast", "_ColorAdjustmentRimWidth", "_ColorAdjustmentRimIntensity",
    "_UseGrayAsAlpha",
    "_ClearCoatSmoothness", "_ClearCoatMetallic", "_ClearCoatNormalMode",
    # _SilkStockingsAdvance 不在这里:它是 GLSL 的 bool,走 BOOL_MAP,
    # 同时留在 FLOAT_IDENTITY 会被 float 覆写成 1.0/0.0。
    "_SilkStockingsMinAffect", "_SilkStockingsMaxAffect",
    "_SilkStockingsAnisoDirection", "_SilkStockingsSpecularInt",
    "_SilkStockingsSpecularMinAtMinWetness", "_SilkStockingsSpecularFalloff",
    "_SilkStockingsSpecularValue", "_SilkStockingsRainWetMaskScale",
    "_SilkStockingsAlbedoAffectType",
    "_BlendMode", "_TintColorIntensity", "_TintColorAlpha", "_IgnorePostExposure",
    "_UseMainTexAsAlpha", "_MainTexUseDisturb", "_MainTexUVRotate",
    "_UseMaskTexAsAlpha", "_MaskTexUseDisturb", "_MaskTexUVRotate",
    "_BlendTexUseDisturb", "_BlendTexUVRotate",
    "_DisturbUVRotate1", "_Bi_Disturb", "_DisturbTex1Normal",
    "_DisturbUIntensity1", "_DisturbVIntensity1",
    "_NormalMapUVRotate", "_NormalScale", "_NormalMapUseDisturb",
    "_FresnelBias", "_FresnelAffectOpacity", "_FresnelPower", "_FresnelFlip",
    "_UseNearCameraFade", "_NearCameraFadeDistanceStart", "_NearCameraFadeDistanceEnd",
    "_NearCameraFadeDistanceEnd2", "_NearCameraFadeDistanceStart2",
]

# Colour/vector parameters copied through by name (Unity's m_Colors holds both
# colours and Vector4s).
COLOR_IDENTITY = [
    "_BaseColor", "_EmissionColor", "_SDFRimColor", "_HighlightMapVector",
    "_MatcapColor", "_EyeHighLightColor", "_EyeScatteringColor",
    "_AnisotropyColor2", "_AnisotropyColorAdditional", "_StylizedFresnelColor",
    "_CustomizeBaseColor", "_CustomizeBaseTintColor", "_CustomizeAddTintColor",
    "_ExtraRootTintColor", "_ExtraDepthTintColor",
    "_DissolveEmissiveColor", "_CutOffDirection",
    "_HairBaseTintColor", "_HairAddTintColor", "_EyeTintColor",
    "_FaceDecalTintColor",
    "_ShadowColor", "_CapsuleAoColor", "_OutlineTintColor",
    "_PuppetBaseColor", "_PuppetPatternTintColor", "_PuppetPatternTintEdgeColor",
    "_PuppetPatternSpeed", "_PuppetPDCurveUVScaleSpeed", "_PuppetPDCurveBaseColor",
    "_PuppetPDCurveLightColor", "_PuppetPDCurveEdgeColor",
    "_ErosionBaseColor", "_ErosionBaseRootColor", "_ErosionBaseTopColor",
    "_ErosionPatternTintColor",
    "_BaseMapUVSpeed", "_EmissionMapUVSpeed",
    "_EnemyHitFlashBrightColor", "_EnemyHitFlashFresnelColor",
    "_EnemyHitFlashBrightCenter",
    "_HairDarkenParams",
    "_SilkStockingsDryColor", "_SilkStockingsWetColor", "_SilkStockingsColor",
    "_ClearCoatColor", "_ParallaxColor",
    "_VFXColor", "_VFXBlendTint", "_VFXSpecialParam", "_VFXFresnelColor",
    "_ColorAdjustmentColorBlend", "_ColorAdjustmentRimColor",
    "_TintColor", "_BlendTint", "_FresnelColor",
    "_MainTexUVSpeed", "_MaskTexUVSpeed", "_BlendTexUVSpeed",
    "_DisturbUVSpeed1", "_NormalMapUVSpeed",
    "_CharacterParams0", "_CharacterParams1", "_CharacterParams2", "_CharacterParams3",
    "_CharacterParams4", "_CharacterParams5", "_CharacterParams6", "_CharacterParams7",
    "_CharacterParams8", "_CharacterParams9", "_CharacterParams10", "_CharacterParams11",
    "_CharacterParams12", "_CharacterParams13", "_CharacterParams14", "_CharacterParams15",
    "_EnvironmentGlobalParams0", "_ExposureParams",
]

# Unity gamma-corrects COLOR properties on upload in a Linear-colour-space
# project; VECTOR properties go through untouched. A .mat stores both in
# m_Colors, so the split has to be reconstructed here.
#
# Verified against a real frame capture of M_actor_pelica_face_01 -- the .mat
# holds _SDFRimColor (0.6509804, 0.39607844, 0.41568628) and the constant buffer
# the GPU actually received holds (0.38133, 0.13014, 0.14413), which is that
# triple through the standard sRGB->linear transfer to five decimals:
#     0.6509804 -> 0.38124   0.39607844 -> 0.13013   0.41568628 -> 0.14414
# The same capture shows _CharacterParams7 as (0.15, 0.60, 1.00) on BOTH sides,
# so the Vector-typed ones are definitively NOT converted.
#
# The type lives in the shader's Properties block, which a .mat does not carry.
# The reliable proxy in this shader family is the name: HG declares every
# Color-typed property with a name ending in "Color". Anything else here --
# _HighlightMapVector, the UV speeds, every _CharacterParamsN, the exposure
# blocks -- is a Vector and must stay raw. Alpha is never gamma-corrected.
# ...with one explicit exception list for the Color-typed properties whose name
# does NOT end in "Color". Read off the reference Properties block, not guessed:
# 1.4.4's characternpr declares
#     _AnisotropyColorAdditional ("'第二层各向异性颜色' {}", Color) = (0.2, 0.2, 0.2, 1)
# so the name heuristic alone would have shipped it un-gamma-corrected.
_SRGB_COLOR_EXTRA = ("_AnisotropyColorAdditional",)

_SRGB_COLOR_PROPS = tuple(p for p in COLOR_IDENTITY
                          if p.endswith("Color") or p in _SRGB_COLOR_EXTRA)


def _srgb_to_linear(c):
    """Unity's exact GammaToLinearSpace for a colour channel.

    Above 1 the engine leaves the sRGB piece and uses a plain 2.2 exponent, and
    HDR colours live up there: extrapolating the piece instead reads 2.0 as
    4.954 where the engine reads 4.595."""
    if c <= 0.04045:
        return c / 12.92
    if c < 1.0:
        return ((c + 0.055) / 1.055) ** 2.4
    return c ** 2.2


def _color_to_linear(prop, rgba):
    """RGB through the transfer for Color properties; alpha and Vectors raw."""
    if prop not in _SRGB_COLOR_PROPS:
        return list(rgba)
    out = [_srgb_to_linear(float(v)) for v in rgba[:3]]
    out.extend(float(v) for v in rgba[3:])
    return out

# Shader bool <- Unity float (absent => off; the HGRP variant lacking the
# property has the feature compiled out anyway).
BOOL_MAP = {
    "u_UseBumpMap":          "_UseBumpMap",
    "u_UseMetallicGlossMap": "_UseMetallicGlossMap",
    "u_UseDiffRamp":         "_UseDiffRampMap",
    "u_UseSpecRamp":         "_UseSpecRampMap",
    "u_UseShadowLut":        "_UseShadowLutTex",
    "u_UseEmission":         "_UseEmission",
    "u_ClearCoat":           "_ClearCoat",
    "_PuppetUV2AreaMask":    "_PuppetUV2AreaMask",
    "_DisableCharacterSelfShadow": "_DisableCharacterSelfShadow",
    "_DisableSceneShadow":   "_DisableSceneShadow",
    "_CircleFade":           "_CircleFade",
    "u_EnableOutline":       "_EnableOutline",
    "_OutlineTintEnable":    "_OutlineTintEnable",
    "_ParallaxUseNormal":    "_ParallaxUseNormal",
    "_UseDissolve":          "_UseDissolve",
    "_UseCutOff":            "_UseCutOff",
    "_DissolveUseViewUV":    "_DissolveUseViewUV",
    "_PuppetPatternMapUseRGB": "_PuppetPatternMapUseRGB",
    "u_PuppetProceduralDCurve": "_PuppetProceduralDCurveEnable",
    "u_CharacterErosion":    "_UseCharacterErosion",
    "_ErosionUV2Tint":       "_ErosionUV2Tint",
    "_EmissionAlphaBrightBreath": "_EmissionAlphaBrightBreath",
    "u_CustomizeAvatar":     "_AvatarCustomizeEnable",
    "u_MatcapEnvReflection": "_UseMatcap",
    "u_EnemyHitFlash":       "_EnableEnemyHitFlash",
    "u_StylizedFresnel":     "_EnableStylizedFresnel",
    "u_UseAnisotropy":       "_UseAnisotropy",
    "_AnisotropyUseGeometryTangent": "_AnisotropyUseGeometryTangent",
    "u_SilkStockings":       "_SilkStockings",
    "_SilkStockingsAdvance": "_SilkStockingsAdvance",
    "u_UseParallax":         "_UseParallax",
    "u_UseSDFLightmap":      "_UseSDFLightmap",
    "u_UseEmotionMap":       "_UseEmotionMap",
    "u_FaceHighlightMap":    "_FaceHighlightMap",
    "u_UseMatcap":           "_UseMatcap",
    "u_EyeHighLight":        "_EyeHighLight",
    "u_UseSpecBumpMap":      "_UseSpecBumpMap",
    "u_StrokeOn":            "_StrokeOn",
    "u_SpecularLine":        "_SpecularLine",
    "u_FurDyeEnable":        "_FurDyeEnable",
    "u_EnableCharacterVFX":  "_EnableCharacterVFX",
    # _FBXRotationFix is deliberately NOT mapped: the game needs it at runtime
    # to fix the object axis, but Painter's mesh is already world-baked, and
    # enabling it rotates the hair anisotropy axis 90 degrees (killing the
    # fringe highlight ring). The shader has the switch removed entirely.
    "u_VFXUseMask":          "_UseMask",
    "u_VFXUseBlend":         "_UseBlend",
    "u_VFXUseDisturb":       "_UseDisturb",
    "u_VFXUseFresnel":       "_UseFresnel",
    "u_VFXEnableNormalMap":  "_EnableNormalMap",
}

# Unity shader KEYWORD -> the same shader bool. Unity's material inspector
# writes both a float property and a keyword for each feature toggle, and the
# keyword is what the runtime actually branches on. The float is used first
# (that is the reference pipeline's behaviour and it is verified byte-for-byte
# against it); the keyword is the fallback for a .mat that carries only the
# keyword, and a disagreement between the two is reported rather than silently
# resolved.
#
# Every name here is one EndField_Uber.glsl itself documents in its own
# parameter labels -- not inferred from the data.
KEYWORD_MAP = {
    "DITHER_SPHERE":          "u_DitherSphere",
    "_ALPHA_SCENE_DEPTH_FADE": "u_AlphaSceneDepthFade",
    "_NORMALMAP":             "u_UseBumpMap",
    "_METALLICSPECGLOSSMAP":  "u_UseMetallicGlossMap",
    "_DIFF_RAMP_ON":          "u_UseDiffRamp",
    "_SPEC_RAMP_ON":          "u_UseSpecRamp",
    "_SHADOW_LUT_TEX":        "u_UseShadowLut",
    "_EMISSION":              "u_UseEmission",
    "_CLEARCOAT":             "u_ClearCoat",
    "_ANISOTROPY_SPECULAR_ON": "u_UseAnisotropy",
    "_STYLIZED_FRESNEL":      "u_StylizedFresnel",
    "_ENEMY_HIT_FLASH":       "u_EnemyHitFlash",
    "_CUSTOMIZE_AVATAR":      "u_CustomizeAvatar",
    "_CHARACTER_EROSION":     "u_CharacterErosion",
    "_PUPPET":                "u_Puppet",
    "_REALISTIC_LIGHTING":    "u_RealisticLighting",
    "VFX_CHARACTER_DISSOLVE": "u_CharacterDissolve",
    "_PUPPET_PROCEDURAL_DCURVE": "u_PuppetProceduralDCurve",
    "_MATCAP_ENV_REFLECTION_ON": "u_MatcapEnvReflection",
    "_SILK_STOCKINGS":        "u_SilkStockings",
    "_PARALLAX_MAP":          "u_UseParallax",
    "_SDFLIGHTMAP":           "u_UseSDFLightmap",
    "_EMOTION_MAP":           "u_UseEmotionMap",
    "_HIGHLIGHT_MAP":         "u_FaceHighlightMap",
    "_MATCAP_ON":             "u_UseMatcap",
    "_EYE_HIGHLIGHT":         "u_EyeHighLight",
    "_SPECULAR_NORMALMAP":    "u_UseSpecBumpMap",
    "_STROKE_ON":             "u_StrokeOn",
    "_SPECULAR_LINE":         "u_SpecularLine",
    "_CHARACTER_FUR_DYE":     "u_FurDyeEnable",
    "_CHARACTER_VFX_SPECIAL": "u_EnableCharacterVFX",
    "_USE_MASK":              "u_VFXUseMask",
    "_USE_BLEND":             "u_VFXUseBlend",
    "_USE_DISTURB":           "u_VFXUseDisturb",
    "_USE_FRESNEL":           "u_VFXUseFresnel",
    "_NORMAL_MAP":            "u_VFXEnableNormalMap",
}
_UNIFORM_KEYWORD = {uniform: keyword for keyword, uniform in KEYWORD_MAP.items()}

# Keywords with no fragment-stage meaning in this port, each for a stated
# reason. Listing them is what keeps the "unhandled keyword" report honest.
IGNORED_KEYWORDS = {
    "_ALPHABLEND_ON": "surface type is taken from _SurfaceType (see u_AlphaBlend)",
    "_ALPHATEST_ON": "folded into u_AlphaClip together with _AlphaClip/_EnableAlphaTest",
    "_FBXROTATIONFIX_ON": "deliberately unmapped -- it rotates the hair anisotropy axis "
                          "90 degrees on Painter's world-baked mesh (see BOOL_MAP)",
    "_ADVANCEDOPTION_ON": "editor-only shader GUI toggle",
    "_OUTLINE_MASK": "the outline mask modulates the vertex extrusion width; the outline FRAGMENT program is ported ([H21]) but the extrusion is not",
    "_USE_ALCHEMY_AO": "engine AO variant; [H5] skips the sandbox custom AO",
    "_USE_GROUND_TRUTH_AO": "engine AO variant; [H5] skips the sandbox custom AO",
    "_CHARACTER_FUR": "the Fur path is selected by CharaPart == 4 (infer_chara_part), "
                      "so the keyword adds nothing on top",
    "TEXTURE_STREAMING_FEEDBACK_WAVE_OPS": "texture-streaming feedback pass only",
    "_DRAW_UNDER_BROW": "stencil/draw-order only, no fragment effect",
    "DISABLE_DRAW_UNDER_HAIR": "stencil/draw-order only, no fragment effect",
}

# Shader vec4 _*_ST <- the Tiling/Offset of a Unity texture slot.
ST_MAP = {
    "_BaseMap_ST":            "_BaseMap",
    "_StrokeMap_ST":          "_StrokeMap",
    "_LineMap_ST":            "_LineMap",
    "_ParallaxTex_ST":        "_ParallaxTex",
    "_ErosionNormalSmoothnessMap_ST": "_ErosionNormalSmoothnessMap",
    "_ErosionPatternMap_ST":  "_ErosionPatternMap",
    "_PuppetPatternMap_ST":   "_PuppetPatternMap",
    "_DissolveTex_ST":        "_DissolveTex",
    "_StylizedFresnelNoiseMap_ST": "_StylizedFresnelNoiseMap",
    "_FurMap_ST":             "_FurMap",
    "_FurDyeMap_ST":          "_FurDyeMap",
    "_VFXSpecialMainTex_ST":  "_VFXSpecialMainTex",
    "_VFXSpecialBlendTex_ST": "_VFXSpecialBlendTex",
    "_VFXMainTex_ST":         "_MainTex",
    "_VFXMaskTex_ST":         "_MaskTex",
    "_VFXBlendTex_ST":        "_BlendTex",
    "_VFXDisturbTex_ST":      "_DisturbTex1",
    "_VFXNormalMap_ST":       "_NormalMap",
}

# Face SDF basis axes in Painter world space.
#
# The shader wants unity_ObjectToWorld's column 0 (right) and column 2 (forward)
# -- verified instruction-by-instruction in the real shader (characternpr_skin
# b113: _502 = col0, _506 = col2). Painter's mesh carries no matrix ([H4]: the
# model IS the world, so O2W is the identity), which in Unity axes makes those
# columns exactly (1,0,0) and (0,0,1); this plugin's own conversion then negates
# X (coordinate.convert_points), giving:
#
#     faceRight = (-1, 0, 0)      faceForward = (0, 0, 1)
#
# Derived from the conversion this plugin performs, not measured by trial.
#
# The previous defaults -- forward (0,0,0), right (1,0,0) -- came from the older
# "hand the FBX to Painter" route, where the mesh's orientation was genuinely
# unknown: zeroing forward makes dot(lightDir, faceFwd) identically 0, which
# switches the SDF's front/back sweep off altogether and leaves only the
# left/right mirror. That is why the face shadow never moved with the light.
# Settled by a controlled Unity experiment: rotating the character root 180 deg
# about Y reproduces every symptom seen in Painter, so the exported mesh differs
# from what the SDF expects by exactly that rotation -- (x,y,z) -> (-x,y,-z).
# The .mat holds these in Unity space ((0,0,1) and (1,0,0)); the export mirrors
# X, so the same mirror applies to them -- one rule for geometry and directions
# alike. If the SDF's front/back or left/right comes out inverted, the sign to
# flip is HERE, not in the mesh conversion (which is what keeps the model facing
# the viewer).
FACE_FORWARD_DEFAULT = [0.0, 0.0, 1.0]
FACE_RIGHT_DEFAULT = [-1.0, 0.0, 0.0]


class TextureJob:
    """One source texture decoded into one destination."""

    __slots__ = ("guid", "op", "kind", "target", "source_property")

    def __init__(self, guid, op, kind, target, source_property):
        self.guid = guid            # Unity texture guid (lowercase)
        self.op = op                # OP_* decode operation
        self.kind = kind            # "channel" | "param"
        self.target = target        # channel key, or shader sampler uniform name
        self.source_property = source_property

    #: Bump whenever the BAKE RULES change (not when a source texture changes --
    #: the guid covers that). The cache is keyed by guid+op and reused verbatim
    #: unless force_rebuild is on, so a rules change would otherwise be masked by
    #: stale files and look like the fix simply did not work.
    #:   v2 -- one-pixel-tall strips grown to 8 rows. Did NOT help: the ramp still
    #:         read back as a single flat colour in-shader. Reverted.
    #:   v3 -- back to writing the source dimensions verbatim.
    #:   v4 -- one-row LUT strips squared up. Tested and NOT the cause: the ramp
    #:         still read flat at 256x256. The real fault was the sampler seam,
    #:         fixed in the shader (SampleRamp). Reverted in v5.
    #:   v5 -- strips written verbatim again.
    BAKE_VERSION = 5

    def cache_key(self):
        return "{0}_{1}_v{2}".format(self.guid[:12], self.op, self.BAKE_VERSION)


class MaterialPlan:
    __slots__ = ("name", "guid", "part", "uniforms", "channel_jobs", "param_jobs",
                 "textures", "floats", "colors", "texture_st", "warnings")

    def __init__(self, name, guid):
        self.name = name
        self.guid = guid
        self.part = 0
        self.uniforms = {}
        self.channel_jobs = []
        self.param_jobs = []
        self.textures = {}
        self.floats = {}
        self.colors = {}
        self.texture_st = {}
        self.warnings = []

    def part_name(self):
        return PART_NAMES.get(self.part, "?")


# ---------------------------------------------------------------------------
# Part inference (port of BuildSPInputs.infer_chara_part)
# ---------------------------------------------------------------------------
def infer_chara_part(mat_name, textures, floats, colors=()):
    n = (mat_name or "").lower()
    # 0) The retarget pipeline (Ruri_Character_Uber) writes an explicit
    #    _CharaPartID -- highest truth, enumerated identically to the GLSL.
    #    The GLSL splits "eye family without Matcap" out as Part 5 Eyebrow;
    #    Unity records both as 2.
    pid = floats.get("_CharaPartID")
    if pid is not None:
        try:
            value = int(pid)
        except (TypeError, ValueError):
            value = None
        if value is not None and 0 <= value <= 8:
            if value == 2 and "_MatcapTex" not in textures and floats.get("_UseMatcap", 0.0) <= 0.5:
                return 5
            return value

    # 1) Bound textures beat scalars: a Unity .mat keeps stale floats/colours
    #    from whatever shader it previously used, but a texture slot is only
    #    populated when that shader family actually consumes it.
    if "_FurMap" in textures or "_FurDirMap" in textures:
        return 4
    if "_SDFLightmap" in textures:
        return 1
    if "_MatcapTex" in textures:
        return 2
    if "_SplitNormalMap" in textures:
        return 3
    if "_MaskTex" in textures or "_DisturbTex1" in textures or "_BlendTex" in textures:
        return 6

    # 1b) ShadowReceiver: characternpr_shadowreceiver has no texture slot at all,
    #    so the only positive evidence is its own property set. _CapsuleAoColor
    #    and _CircleFadeDistance exist in no other variant, which makes them a
    #    clean signature -- checked before the "shadow" name rules below.
    if "shadowreceiver" in n or "_CapsuleAoColor" in colors or "_CircleFadeDistance" in floats:
        return 8

    # 2) OverlayShadow: the name is reliable (checked before the hair/eye name
    #    fallbacks -- "hairshadow" contains "hair"). Leftover floats only count
    #    when there is no lit-material evidence at all.
    if "overlayshadow" in n or "eyewhiteshadow" in n or "hairshadow" in n:
        return 7
    lit_evidence = ("_MetallicGlossMap" in textures or "_BumpMap" in textures
                    or "_EmissionMap" in textures)
    if "_UseGrayAsAlpha" in floats and not lit_evidence:
        return 7

    # 3) Name fallback.
    if "vfx" in n or n.startswith("m_fx_") or "_fx_" in n:
        return 6
    if "fur" in n:
        return 4
    if "brow" in n:
        return 5
    if "eye" in n or "iris" in n:
        return 2
    if "hair" in n:
        return 3
    if "face" in n or "skin" in n:
        return 1
    if "body" in n and "_MetallicGlossMap" not in textures:
        return 1  # Endfield body skin (no RMOS) shades through the skin path
    return 0


def _unity_dir_to_sp(vector, default):
    """A Unity-space direction from the .mat -> Painter world space.

    Negate X, exactly as coordinate.convert_points does to every position and
    normal in the mesh -- the handedness conversion has to be the SAME one, or a
    direction parameter ends up in a different space than the geometry it is
    meant to describe. Missing/zero falls back to the Painter-space default.
    """
    if not vector:
        return list(default)
    try:
        x, y, z = float(vector[0]), float(vector[1]), float(vector[2])
    except (TypeError, ValueError, IndexError):
        return list(default)
    if abs(x) < 1e-8 and abs(y) < 1e-8 and abs(z) < 1e-8:
        return list(default)
    return [-x, y, z]


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------
def build_plan(name, guid, props, texture_exists, face_basis=None):
    """Turn one material's normalised PROPERTIES into a MaterialPlan.

    ``props`` is :class:`RuriRipperPyBridge.unity.material.MaterialProperties` --
    the shared normalised form, not a document. Reading a Unity ``.mat`` into it
    is the caller's (``unity.material.parse_material``); an engine that states
    those properties outright hands one over with no document at all, which is
    the only reason this used to be unreachable from anything but Unity.

    ``face_basis`` is the (right, forward) pair measured off the exported model
    (model_builder._measure_face_basis). When present it replaces the static
    defaults, because Painter documents no world-space handedness and a measured
    axis beats a guessed convention. None keeps the defaults.

    ``texture_exists(guid) -> bool`` reports whether a texture guid actually
    resolves in the current closure/project; part inference only trusts slots
    whose texture is really there, matching BuildSPInputs' exported_props rule
    (a .mat routinely keeps a stale GUID from a previous shader assignment).
    """
    plan = MaterialPlan(name, guid)
    # Everything below is EndField_Uber-specific; getting HERE is not.
    textures, floats, colors, texture_st = (
        props.textures, props.floats, props.colors, props.texture_st)
    plan.textures, plan.floats, plan.colors, plan.texture_st = \
        textures, floats, colors, texture_st

    resolved = {prop: tex for prop, tex in textures.items() if texture_exists(tex)}
    for prop, tex in textures.items():
        if prop not in resolved and prop not in IGNORED_TEXTURES:
            plan.warnings.append("{0}: texture {1} ({2}) is not in the resolved closure".format(
                name, prop, tex[:8]))

    part = infer_chara_part(name, resolved, floats, colors)
    plan.part = part

    def scalar(prop):
        default = PART_PBR_DEFAULTS.get(part, {}).get(prop)
        if default is None:
            default = FLOAT_DEFAULTS.get(prop, 0.0)
        value = float(floats.get(prop, default))
        if prop in GAMMA_FLOAT_PROPS:
            value = _srgb_to_linear(value)
        return value

    uniforms = {"u_CharaPart": part}

    # -- toggles --
    # The float property is authoritative (it is what the reference pipeline
    # read, and the port is verified against it); the shader keyword covers a
    # material that only carries the keyword, and any disagreement is reported.
    active_keywords = props.keywords
    # Some keywords have no property in the reference Properties block at all
    # (_PUPPET is one): the game enables them, the inspector never does. Those
    # uniforms are keyword-only -- give them an entry so they still resolve.
    keyword_only = {uniform: None for uniform in KEYWORD_MAP.values()
                    if uniform not in BOOL_MAP}
    for shader_name, unity_name in list(BOOL_MAP.items()) + list(keyword_only.items()):
        keyword = _UNIFORM_KEYWORD.get(shader_name)
        keyword_on = keyword in active_keywords if keyword else None
        if unity_name is not None and unity_name in floats:
            value = bool(floats[unity_name] > 0.5)
            if keyword_on is not None and keyword_on != value:
                plan.warnings.append(
                    "{0}: {1}={2} but the keyword {3} is {4} -- following the property, "
                    "as the reference pipeline does.".format(
                        name, unity_name, floats[unity_name], keyword,
                        "on" if keyword_on else "off"))
        else:
            value = bool(keyword_on)
        uniforms[shader_name] = value
    # Transparency: Fur is always blended; everything else follows _SurfaceType,
    # falling back to the _ALPHABLEND_ON keyword when that property is absent.
    uniforms["u_AlphaBlend"] = (
        (part == 4)
        or (floats.get("_SurfaceType", 0.0) > 0.5)
        or ("_SurfaceType" not in floats and "_ALPHABLEND_ON" in active_keywords))

    # -- scalars --
    for prop in FLOAT_IDENTITY:
        uniforms[prop] = scalar(prop)
    # Alpha clip only bites when one of the alpha-test switches is on -- the
    # reference gates every Pass0 clip behind _ALPHATEST_ON. Without this gate
    # the 0.5 default threshold would clip parts whose _BaseMap.a is a data
    # mask rather than opacity (eye scattering, face), punching holes in them.
    uniforms["u_AlphaClip"] = (floats.get("_AlphaClip", 0.0) > 0.5
                               or floats.get("_EnableAlphaTest", 0.0) > 0.5
                               or "_ALPHATEST_ON" in active_keywords)
    # Eyes carry their own parallax strength uniform (domain 0..0.15, unlike
    # Standard's _ParallaxScale, which stays in the table -- the shader simply
    # does not read it on these parts).
    if part in (2, 5):
        uniforms["_EyeParallaxScale"] = float(floats.get("_ParallaxScale", 0.03))
    uniforms["_EmotionIndex"] = int(scalar("_EmotionIndex"))

    # -- colours / vectors --
    for prop in COLOR_IDENTITY:
        if prop in colors:
            uniforms[prop] = _color_to_linear(prop, colors[prop])
    if part == 1:
        # The landmark measurement is NOT used: it returned right=(-1,-0,0)
        # forward=(0,-1,0) on a real model, i.e. it picked the wrong axis pair.
        # The constants above are pinned by the Unity 180-deg experiment instead.
        uniforms["_FaceForward"] = list(FACE_FORWARD_DEFAULT)
        uniforms["_FaceRight"] = list(FACE_RIGHT_DEFAULT)

    # -- texture tiling/offset --
    for shader_name, unity_tex in ST_MAP.items():
        if unity_tex in texture_st:
            uniforms[shader_name] = list(texture_st[unity_tex])

    plan.uniforms = uniforms

    # -- texture jobs --
    for prop, destinations in CHANNEL_SOURCES.items():
        tex = resolved.get(prop)
        if not tex:
            continue
        for channel, op in destinations:
            plan.channel_jobs.append(TextureJob(tex, op, "channel", channel, prop))
    for prop, (sampler, op) in TEXTURE_PARAMS.items():
        tex = resolved.get(prop)
        if not tex:
            continue
        plan.param_jobs.append(TextureJob(tex, op, "param", sampler, prop))

    # A few reference features have no toggle property at all -- the shader just
    # samples the map when the material binds one. The GLSL needs a bool for
    # that, so it is derived from whether the texture actually resolved.
    for prop, uniform in TEXTURE_PRESENCE_BOOLS.items():
        plan.uniforms[uniform] = bool(resolved.get(prop))

    # Any remaining bound texture with no destination at all: report it rather
    # than dropping it silently, so a new shader property surfaces instead of
    # quietly vanishing from the port.
    known = set(CHANNEL_SOURCES) | set(TEXTURE_PARAMS) | set(IGNORED_TEXTURES)
    for prop in sorted(resolved):
        if prop not in known:
            plan.warnings.append("{0}: texture property {1} has no Painter destination "
                                 "(ignored)".format(name, prop))
    # Same for keywords: anything the material enables that neither drives a
    # uniform nor has a stated reason to be ignored is a real gap in the port
    # and is named, not swallowed.
    for keyword in sorted(active_keywords):
        if keyword in KEYWORD_MAP or keyword in IGNORED_KEYWORDS:
            continue
        plan.warnings.append(
            "{0}: shader keyword {1} is enabled but has no equivalent in "
            "EndField_Uber (ignored)".format(name, keyword))
    return plan
