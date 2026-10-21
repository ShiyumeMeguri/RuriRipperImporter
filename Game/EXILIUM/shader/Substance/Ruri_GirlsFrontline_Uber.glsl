//============================================================================
// Ruri_GirlsFrontline_Uber.glsl —— 生成物,勿手改(真源 = Ruri.RenderPipelines.Generator C# 材质模块)
// 风格 = GirlsFrontline;输入投影清单 = Ruri_GirlsFrontline_Uber.manifest.json(导入器与本文件同源同构)
//============================================================================

//----------------------------------------------------------------------region HLSL→GLSL 词法桥(生成物,勿手改)
#define float2 vec2
#define float3 vec3
#define float4 vec4
#define half float
#define half2 vec2
#define half3 vec3
#define half4 vec4
#define float2x2 mat2
#define float3x3 mat3
#define float4x4 mat4
#define half3x3 mat3
#define half4x4 mat4
#define uint2 uvec2
#define uint3 uvec3
#define uint4 uvec4
#define int2 ivec2
#define int3 ivec3
#define int4 ivec4
#define bool2 bvec2
#define bool3 bvec3
#define bool4 bvec4

#define frac fract
#define ddx dFdx
#define ddy dFdy
#define ddx_fine dFdxFine
#define ddy_fine dFdyFine
#define fmod mod
#define atan2(y, x) atan(y, x)
#define rsqrt inversesqrt
#define asuint floatBitsToUint
#define asfloat uintBitsToFloat
#define asint floatBitsToInt
#define UNITY_BRANCH
#define UNITY_LOOP
#define UNITY_FLATTEN

float  saturate(float v)  { return clamp(v, 0.0, 1.0); }
vec2   saturate(vec2 v)   { return clamp(v, vec2(0.0), vec2(1.0)); }
vec3   saturate(vec3 v)   { return clamp(v, vec3(0.0), vec3(1.0)); }
vec4   saturate(vec4 v)   { return clamp(v, vec4(0.0), vec4(1.0)); }

float  lerp(float a, float b, float t) { return mix(a, b, t); }
vec2   lerp(vec2 a, vec2 b, float t)   { return mix(a, b, t); }
vec3   lerp(vec3 a, vec3 b, float t)   { return mix(a, b, t); }
vec4   lerp(vec4 a, vec4 b, float t)   { return mix(a, b, t); }
vec2   lerp(vec2 a, vec2 b, vec2 t)    { return mix(a, b, t); }
vec3   lerp(vec3 a, vec3 b, vec3 t)    { return mix(a, b, t); }
vec4   lerp(vec4 a, vec4 b, vec4 t)    { return mix(a, b, t); }

float  mad(float a, float b, float c) { return a * b + c; }
vec2   mad(vec2 a, vec2 b, vec2 c)    { return a * b + c; }
vec3   mad(vec3 a, vec3 b, vec3 c)    { return a * b + c; }
vec4   mad(vec4 a, vec4 b, vec4 c)    { return a * b + c; }
vec2   mad(vec2 a, float b, vec2 c)   { return a * b + c; }
vec3   mad(vec3 a, float b, vec3 c)   { return a * b + c; }
vec4   mad(vec4 a, float b, vec4 c)   { return a * b + c; }

// HLSL mul 语义(矩阵按逻辑布局搬运:行构造经 ruriMatRows 转置,列语义两侧一致)。
// 本组是 mul 的**唯一供给**:同名 C# 镜像一律不编译(见 SubstanceDialect.BridgedNames 派生),
// 故镜像里出现过的每个重载都必须在这里齐备 —— 少一个 = 调用点无匹配重载。
vec3   mul(mat3 m, vec3 v)  { return m * v; }
vec4   mul(mat4 m, vec4 v)  { return m * v; }
vec3   mul(mat4 m, vec3 v)  { return mat3(m) * v; }
vec3   mul(vec3 v, mat3 m)  { return v * m; }
vec4   mul(vec4 v, mat4 m)  { return v * m; }
vec3   mul(vec3 v, mat4 m)  { return v * mat3(m); }
mat3   mul(mat3 a, mat3 b)  { return a * b; }
mat4   mul(mat4 a, mat4 b)  { return a * b; }

mat3   ruriMat3Rows(vec3 r0, vec3 r1, vec3 r2) { return transpose(mat3(r0, r1, r2)); }
mat4   ruriMat4Rows(vec4 r0, vec4 r1, vec4 r2, vec4 r3) { return transpose(mat4(r0, r1, r2, r3)); }

float  rcp(float x) { return 1.0 / x; }
vec2   rcp(vec2 x)  { return vec2(1.0) / x; }
vec3   rcp(vec3 x)  { return vec3(1.0) / x; }
vec4   rcp(vec4 x)  { return vec4(1.0) / x; }

void clip(float x) { if (x < 0.0) discard; }
void clip(vec4 x)  { if (any(lessThan(x, vec4(0.0)))) discard; }

// 附加光循环(URP 非聚簇形;灯数经能力兑现,缺席=0 → 死循环体被编译器消除)。
#define LIGHT_LOOP_BEGIN(count) for (uint lightIndex = 0u; lightIndex < count; ++lightIndex) {
#define LIGHT_LOOP_END }

// sRGB 解码(宿主原样槽位的颜色纹理:裸采样无硬件解码,按声明补;alpha 不解码)。
float ruriSrgbToLinear(float c) {
    return (c <= 0.04045) ? (c / 12.92) : pow(abs((c + 0.055) / 1.055), 2.4);
}
vec4 ruriSampleSrgb(sampler2D t, vec2 uv) {
    vec4 s = texture(t, uv);
    return vec4(ruriSrgbToLinear(s.r), ruriSrgbToLinear(s.g), ruriSrgbToLinear(s.b), s.a);
}
vec4 ruriSampleSrgbLod(sampler2D t, vec2 uv, float lod) {
    vec4 s = textureLod(t, uv, lod);
    return vec4(ruriSrgbToLinear(s.r), ruriSrgbToLinear(s.g), ruriSrgbToLinear(s.b), s.a);
}

// CLAMP 寻址复现(ramp/LUT 声明为 Clamp 的纹理:钳到 texel 中心,双线性不吃边框)。
vec2 ruriUvClamp(sampler2D t, vec2 uv) {
    vec2 size = vec2(textureSize(t, 0));
    vec2 halfTexel = 0.5 / max(size, vec2(1.0));
    return clamp(uv, halfTexel, vec2(1.0) - halfTexel);
}
//----------------------------------------------------------------------endregion

//----------------------------------------------------------------------region 面板参数(生成)
//: param custom { "default": 0, "label": "GirlsFrontline Part", "widget": "combobox", "values": { "0 Standard": 0, "1 Face": 1, "2 Eyes": 2, "3 EyeBlendAdd": 3, "4 EyeBlendMultiply": 4 }, "group": "0 部位" }
uniform_specialization int _CharaPartID;
//: param custom { "default": false, "label": "MAIN_LIGHT_CALCULATE_SHADOWS", "group": "1 变体开关" }
uniform_specialization bool MAIN_LIGHT_CALCULATE_SHADOWS;
//: param custom { "default": false, "label": "_NORMALMAP", "group": "1 变体开关" }
uniform_specialization bool _NORMALMAP;
const float INV_PI = 0.31830987;
//: param custom { "default": false, "label": "Adjust Shadow Bias", "group": "Stocking" }
uniform bool _AdjustShadowBias;
//: param custom { "default": 0, "label": "Anisotropic GGX", "min": -1, "max": 1, "group": "Stocking" }
uniform float _AnisotropicGXX;
//: param custom { "default": false, "label": "Use Anisotropic Specular", "group": "Stocking" }
uniform bool _AnisotropicSpecular;
//: param custom { "default": 1, "label": "Anisotropy", "min": 0, "max": 5, "group": "参数" }
uniform float _Anisotropy;
//: param custom { "default": 0.05, "label": "Anisotropy Shift", "min": 0, "max": 1, "group": "参数" }
uniform float _AnisotropyShift;
//: param custom { "default": [1, 1, 1, 1], "label": "Color", "widget": "color", "srgb": true, "group": "参数" }
uniform vec4 _BaseColor;
//: param custom { "default": [1, 1, 0, 0], "label": "_BaseMap_ST", "group": "R 引擎态" }
uniform vec4 _BaseMap_ST;
//: param custom { "default": 0.1, "label": "Blend Smoothness", "min": 0, "max": 1, "group": "Stocking" }
uniform float _BlendSmoothness;
//: param custom { "default": 1, "label": "Normal Scale", "group": "参数" }
uniform float _BumpScale;
//: param custom { "default": 0.3, "label": "Cornea Parallax", "min": 0, "max": 0.5, "group": "Character Effect" }
uniform float _CorneaParallax;
//: param custom { "default": 0.15, "label": "Alpha Cutoff", "min": 0, "max": 1, "group": "State" }
uniform float _Cutoff;
//: param custom { "default": 1, "label": "Emissive Intensity", "min": 1, "max": 20, "group": "参数" }
uniform float _EmissiveIntensity;
//: param custom { "default": 0, "label": "_WetFlowSize", "group": "Character Effect" }
uniform float _FaceLightDirAdjustment;
//: param custom { "default": [1, 1, 1, 1], "label": "Final Tint", "widget": "color", "srgb": true, "group": "Character Effect" }
uniform vec4 _FinalTint;
//: param custom { "default": [1, 1, 1, 0.85], "label": "Main Color", "widget": "color", "srgb": true, "group": "Character Effect" }
uniform vec4 _MainColor;
const uint _MainLightLayerMask = uint(0xFFFFFFFF);
//: param custom { "default": [1, 1, 1, 1], "label": "_MainLightOcclusionProbes", "group": "R 引擎态" }
uniform vec4 _MainLightOcclusionProbes;
//: param custom { "default": 1, "label": "Metallic Intensity", "group": "参数" }
uniform float _MetallicIntensity;
//: param custom { "default": [0.6, 0.6, 0.6, 0.1], "label": "Outline Color", "widget": "color", "srgb": true, "group": "State" }
uniform vec4 _OutlineColor;
//: param custom { "default": 1, "label": "Outline Intensity", "min": 1, "max": 30, "group": "Stocking" }
uniform float _OutlineIntensity;
//: param custom { "default": [0.6, 0.6, 0.6, 1], "label": "Outline Shadow Color", "widget": "color", "srgb": true, "group": "State" }
uniform vec4 _OutlineShadowColor;
//: param custom { "default": 1, "label": "Roughness Intensity", "group": "参数" }
uniform float _RoughnessIntensity;
//: param custom { "default": false, "label": "_RuriOutlineShellGate", "group": "R 引擎态" }
uniform bool _RuriOutlineShellGate;
//: param custom { "default": 0.1, "label": "Shadow Bias Distance", "min": 0, "max": 1, "group": "Stocking" }
uniform float _ShadowBiasDistance;
//: param custom { "default": 0.25, "label": "Shadow Intensity", "min": 0, "max": 1, "group": "Character Effect" }
uniform float _ShadowIntensity;
//: param custom { "default": 1, "label": "Specular Intensity", "group": "参数" }
uniform float _SpecularIntensity;
//: param custom { "default": 0.3, "label": "Specular Parallax", "min": 0, "max": 1, "group": "Character Effect" }
uniform float _SpecularParallax;
//: param custom { "default": [1, 1, 1, 1], "label": "Stocking Center Color", "widget": "color", "srgb": true, "group": "参数" }
uniform vec4 _StockingCenterColor;
//: param custom { "default": [0.1, 0, 0, 1], "label": "Stocking Falloff Color", "widget": "color", "srgb": true, "group": "参数" }
uniform vec4 _StockingFalloffColor;
//: param custom { "default": 1, "label": "Stocking Falloff Power", "min": 0.1, "max": 5, "group": "参数" }
uniform float _StockingFalloffPower;
//: param custom { "default": 0, "label": "Surface Type", "min": 0, "max": 1, "group": "参数" }
uniform int _SurfaceType;
//: param custom { "default": false, "label": "Use Alpha Test", "group": "State" }
uniform bool _UseAlphaTest;
//: param custom { "default": false, "label": "Use GI Flatten", "group": "Stocking" }
uniform bool _UseGIFlatten;
//: param custom { "default": false, "label": "Use RMOS Map", "group": "参数" }
uniform bool _UseRMOSMap;
//: param custom { "default": false, "label": "Use Ramp Map", "group": "参数" }
uniform bool _UseRampMap;
//: param custom { "default": false, "label": "Use UV2", "group": "Stocking" }
uniform bool _UseSpecularUV2;
//: param custom { "default": false, "label": "Use Stocking Falloff", "group": "Stocking" }
uniform bool _UseStockingFalloff;
const uint lightIndex = uint(0);
//: param custom { "default": [1, 1, 0, 0], "label": "unity_SpecCube0_HDR", "group": "R 引擎态" }
uniform vec4 unity_SpecCube0_HDR;
//----------------------------------------------------------------------endregion

//----------------------------------------------------------------------region 宿主库
import lib-pbr.glsl
import lib-bent-normal.glsl
import lib-emissive.glsl
import lib-sss.glsl
import lib-utils.glsl
import lib-sparse.glsl
//----------------------------------------------------------------------endregion

//: state cull_face off
//: state blend over

//: param auto camera_view_matrix
uniform mat4 uniform_camera_view_matrix;
//: param auto environment_max_lod
uniform float environment_max_lod;
//: param auto facing
uniform int uniform_facing;
//: param auto main_light
uniform vec4 light_main;

//----------------------------------------------------------------------region 宿主输入(投影方案派生)
//: param auto channel_basecolor
uniform SamplerSparse basecolor_tex;
//: param auto channel_opacity
uniform SamplerSparse opacity_tex;
//: param custom { "default": "", "default_color": [0.2158605, 0.2158605, 0.2158605, 0.5], "label": "Blend Tex", "usage": "texture", "group": "2 贴图" }
uniform sampler2D _BlendTex;
//: param auto channel_user1
uniform SamplerSparse slot_user1_tex;
//: param custom { "default": "", "default_color": [1.0, 1.0, 1.0, 1.0], "label": "MainTex", "usage": "texture", "group": "2 贴图" }
uniform sampler2D _MainTex;
//: param auto channel_roughness
uniform SamplerSparse roughness_tex;
//: param auto channel_metallic
uniform SamplerSparse metallic_tex;
//: param auto channel_specularlevel
uniform SamplerSparse specularlevel_tex;
//: param custom { "default": "", "default_color": [1.0, 1.0, 1.0, 1.0], "label": "RMO Map (RGB)", "usage": "texture", "group": "2 贴图" }
uniform sampler2D _RMOTex;
//: param custom { "default": "", "default_color": [1.0, 1.0, 1.0, 1.0], "label": "Diffuse Ramp Map", "usage": "texture", "group": "2 贴图" }
uniform sampler2D _RampMap;
//: param custom { "default": "", "default_color": [0.0, 0.0, 0.0, 0.0], "label": "Specular Map", "usage": "texture", "group": "2 贴图" }
uniform sampler2D _Specularmap;
//: param custom { "default": "", "default_color": [1.0, 1.0, 0.0, 0.0], "label": "_BumpMap 余量(ba)", "usage": "texture", "group": "2 贴图" }
uniform sampler2D _BumpMap_ba;
//----------------------------------------------------------------------endregion

#ifndef RURI_PRELUDE_RURILENGTH
#define RURI_PRELUDE_RURILENGTH
float ruriLength(float2 v) { return sqrt(dot(v, v)); }
float ruriLength(float3 v) { return sqrt(dot(v, v)); }
float ruriLength(float4 v) { return sqrt(dot(v, v)); }
#endif

#ifndef RURI_PRELUDE_RURINORMALIZE
#define RURI_PRELUDE_RURINORMALIZE
float2 ruriNormalize(float2 v) { return v / sqrt(dot(v, v)); }
float3 ruriNormalize(float3 v) { return v / sqrt(dot(v, v)); }
float4 ruriNormalize(float4 v) { return v / sqrt(dot(v, v)); }
#endif

//----------------------------------------------------------------------region 结构体
struct CharaVaryings {
    vec2 uv;
    vec3 positionWS;
    vec3 normalWS;
    vec4 tangentWS;
    vec4 uv1;
    vec2 uv0zw;
    vec2 uv2;
    vec4 positionNDC;
    vec4 color;
    vec4 positionCS;
};

struct GBufferData {
    vec3 baseColor;
    float smoothness;
    vec3 specularColor;
    float occlusion;
    vec3 normalWS;
    uint materialFlags;
    float depth;
    vec4 shadowMask;
    uint meshRenderingLayers;
};

struct GBufferFragOutput {
    vec4 gBuffer0;
    vec4 gBuffer1;
    vec4 gBuffer2;
    vec4 color;
    float depth;
    vec4 shadowMask;
    uint meshRenderingLayers;
};

struct InputData {
    vec3 positionWS;
    vec4 positionCS;
    vec3 normalWS;
    vec3 viewDirectionWS;
    vec4 shadowCoord;
    float fogCoord;
    vec3 vertexLighting;
    vec3 bakedGI;
    vec2 normalizedScreenSpaceUV;
    vec4 shadowMask;
};

struct Light {
    vec3 direction;
    vec3 color;
    float distanceAttenuation;
    float shadowAttenuation;
    uint layerMask;
};

struct RuriData {
    float alpha;
    float overClearedBackdrop;
    vec3 albedo;
    float roughness;
    float metallic;
    float occlusion;
    float specular;
    vec3 normalTS;
    vec3 positionWS;
    vec4 positionCS;
    vec3 normalWS;
    vec3 viewDirectionWS;
    vec4 shadowCoord;
    vec3 bakedGI;
    vec2 normalizedScreenSpaceUV;
    vec4 shadowMask;
    float diffuse;
    vec4 baseSample;
    float baseAlpha;
    vec3 V;
    vec3 L;
    vec3 H;
    vec3 camFwd;
    Light mainLight;
    vec3 adjustedLightDir;
    float adjXZ_x;
    float adjXZ_z;
    float adjXZLen;
    float camLightDotRaw;
    float camLightDot;
    float camYSmooth;
    float exposure;
    float ambInt;
    float perObjectShadow;
    float useRampVal;
    float specScale;
    float base_weight;
    vec3 base_color;
    float base_diffuse_roughness;
    float base_metalness;
    float specular_weight;
    vec3 specular_color;
    float specular_roughness;
    float specular_ior;
    float specular_roughness_anisotropy;
    float subsurface_weight;
    vec3 subsurface_color;
    float subsurface_radius;
    vec3 subsurface_radius_scale;
    float subsurface_scatter_anisotropy;
    float fuzz_weight;
    vec3 fuzz_color;
    float fuzz_roughness;
    float coat_weight;
    vec3 coat_color;
    float coat_roughness;
    float coat_roughness_anisotropy;
    float coat_ior;
    float coat_darkening;
    float emission_luminance;
    vec3 emission_color;
    vec3 coat_normal;
};

struct RuriGBufferData {
    float alpha;
    vec3 baseColor;
    float roughness;
    float metallic;
    float occlusion;
    float specular;
    float lightBlock;
    float lightSky;
    uint materialFlags;
    uint shadingModel;
    vec4 customData;
    vec3 normalWS;
    float depth;
    vec4 shadowMask;
    uint meshRenderingLayers;
    vec4 globalIllumination;
};

struct SceneVaryings {
    vec2 uv;
    vec3 positionWS;
    vec3 positionOS;
    vec3 normalWS;
    vec4 tangentWS;
    vec2 voxelUV;
    vec3 voxelLitColor;
    vec2 staticLightmapUV;
    vec4 positionNDC;
    vec4 color;
    vec2 voxelSliceMaterial;
    vec2 voxelMaterialIris;
    vec2 uv1;
    vec2 uv2;
    vec2 voxelBlockLight;
    vec4 positionCS;
};

//----------------------------------------------------------------------endregion

//----------------------------------------------------------------------region 结构零值
CharaVaryings ruriZeroCharaVaryings() {
    CharaVaryings v;
    v.uv = vec2(0.0);
    v.positionWS = vec3(0.0);
    v.normalWS = vec3(0.0);
    v.tangentWS = vec4(0.0);
    v.uv1 = vec4(0.0);
    v.uv0zw = vec2(0.0);
    v.uv2 = vec2(0.0);
    v.positionNDC = vec4(0.0);
    v.color = vec4(0.0);
    v.positionCS = vec4(0.0);
    return v;
}

GBufferFragOutput ruriZeroGBufferFragOutput() {
    GBufferFragOutput v;
    v.gBuffer0 = vec4(0.0);
    v.gBuffer1 = vec4(0.0);
    v.gBuffer2 = vec4(0.0);
    v.color = vec4(0.0);
    v.depth = 0.0;
    v.shadowMask = vec4(0.0);
    v.meshRenderingLayers = uint(0);
    return v;
}

InputData ruriZeroInputData() {
    InputData v;
    v.positionWS = vec3(0.0);
    v.positionCS = vec4(0.0);
    v.normalWS = vec3(0.0);
    v.viewDirectionWS = vec3(0.0);
    v.shadowCoord = vec4(0.0);
    v.fogCoord = 0.0;
    v.vertexLighting = vec3(0.0);
    v.bakedGI = vec3(0.0);
    v.normalizedScreenSpaceUV = vec2(0.0);
    v.shadowMask = vec4(0.0);
    return v;
}

Light ruriZeroLight() {
    Light v;
    v.direction = vec3(0.0);
    v.color = vec3(0.0);
    v.distanceAttenuation = 0.0;
    v.shadowAttenuation = 0.0;
    v.layerMask = uint(0);
    return v;
}

RuriData ruriZeroRuriData() {
    RuriData v;
    v.alpha = 0.0;
    v.overClearedBackdrop = 0.0;
    v.albedo = vec3(0.0);
    v.roughness = 0.0;
    v.metallic = 0.0;
    v.occlusion = 0.0;
    v.specular = 0.0;
    v.normalTS = vec3(0.0);
    v.positionWS = vec3(0.0);
    v.positionCS = vec4(0.0);
    v.normalWS = vec3(0.0);
    v.viewDirectionWS = vec3(0.0);
    v.shadowCoord = vec4(0.0);
    v.bakedGI = vec3(0.0);
    v.normalizedScreenSpaceUV = vec2(0.0);
    v.shadowMask = vec4(0.0);
    v.diffuse = 0.0;
    v.baseSample = vec4(0.0);
    v.baseAlpha = 0.0;
    v.V = vec3(0.0);
    v.L = vec3(0.0);
    v.H = vec3(0.0);
    v.camFwd = vec3(0.0);
    v.mainLight = ruriZeroLight();
    v.adjustedLightDir = vec3(0.0);
    v.adjXZ_x = 0.0;
    v.adjXZ_z = 0.0;
    v.adjXZLen = 0.0;
    v.camLightDotRaw = 0.0;
    v.camLightDot = 0.0;
    v.camYSmooth = 0.0;
    v.exposure = 0.0;
    v.ambInt = 0.0;
    v.perObjectShadow = 0.0;
    v.useRampVal = 0.0;
    v.specScale = 0.0;
    v.base_weight = 0.0;
    v.base_color = vec3(0.0);
    v.base_diffuse_roughness = 0.0;
    v.base_metalness = 0.0;
    v.specular_weight = 0.0;
    v.specular_color = vec3(0.0);
    v.specular_roughness = 0.0;
    v.specular_ior = 0.0;
    v.specular_roughness_anisotropy = 0.0;
    v.subsurface_weight = 0.0;
    v.subsurface_color = vec3(0.0);
    v.subsurface_radius = 0.0;
    v.subsurface_radius_scale = vec3(0.0);
    v.subsurface_scatter_anisotropy = 0.0;
    v.fuzz_weight = 0.0;
    v.fuzz_color = vec3(0.0);
    v.fuzz_roughness = 0.0;
    v.coat_weight = 0.0;
    v.coat_color = vec3(0.0);
    v.coat_roughness = 0.0;
    v.coat_roughness_anisotropy = 0.0;
    v.coat_ior = 0.0;
    v.coat_darkening = 0.0;
    v.emission_luminance = 0.0;
    v.emission_color = vec3(0.0);
    v.coat_normal = vec3(0.0);
    return v;
}

RuriGBufferData ruriZeroRuriGBufferData() {
    RuriGBufferData v;
    v.alpha = 0.0;
    v.baseColor = vec3(0.0);
    v.roughness = 0.0;
    v.metallic = 0.0;
    v.occlusion = 0.0;
    v.specular = 0.0;
    v.lightBlock = 0.0;
    v.lightSky = 0.0;
    v.materialFlags = uint(0);
    v.shadingModel = uint(0);
    v.customData = vec4(0.0);
    v.normalWS = vec3(0.0);
    v.depth = 0.0;
    v.shadowMask = vec4(0.0);
    v.meshRenderingLayers = uint(0);
    v.globalIllumination = vec4(0.0);
    return v;
}

//----------------------------------------------------------------------endregion

SparseCoord ruriSparseCoord;

//----------------------------------------------------------------------region 宿主胶水(配方)
// 主光是本宿主自己的主光:环境的主光方向(世界系,随显示设置里的环境旋转一起转)、颜色恒白、不衰减不投影 —— 与宿主自带着色器照的是同一盏。
//: param custom { "default": 0.0, "label": "时间 Time", "min": 0.0, "max": 100.0, "group": "0 光照" }
uniform float f_RuriTime;
// 物体→世界。本宿主一棵着色器服务整个模型,没有「物体」可问,所以这三列由桥算好随材质行过来(它同时知道物体摆位、负载根节点为对齐 Y 轴带的旋转、以及两种物体空间约定之间的 Y/Z 互换——那是个反射不是旋转,所以假定单位阵不是「转错了」而是「左右反了」)。缺省是单位阵:没人告诉就跟从前一模一样。
//: param custom { "default": [1, 0, 0, 0], "label": "物体→世界 列0", "group": "R 引擎态" }
uniform vec4 i_ObjectToWorld0;
//: param custom { "default": [0, 1, 0, 0], "label": "物体→世界 列1", "group": "R 引擎态" }
uniform vec4 i_ObjectToWorld1;
//: param custom { "default": [0, 0, 1, 0], "label": "物体→世界 列2", "group": "R 引擎态" }
uniform vec4 i_ObjectToWorld2;
mat4 ruriObjectToWorld() {
    return mat4(i_ObjectToWorld0, i_ObjectToWorld1, i_ObjectToWorld2, vec4(0.0, 0.0, 0.0, 1.0));
}
// 单趟等价:F 腿回读的 gbuffer 就是本表面 G 腿写入的自身数据 —— 直接用本片元的表面态回声。
GBufferData ruriSelfGBuffer(RuriData rd) {
    GBufferData g;
    g.baseColor = rd.albedo;
    g.smoothness = 1.0 - rd.roughness;
    g.specularColor = vec3(rd.specular);
    g.occlusion = rd.occlusion;
    g.normalWS = rd.normalWS;
    g.materialFlags = 0u;
    g.depth = 0.0;
    g.shadowMask = vec4(1.0);
    g.meshRenderingLayers = 0u;
    return g;
}

// LinearToSRGB:URP 内建。其 C# 镜像的 float3 重载体是**向真身的委托** —— Unity 腿从不发射该函数
// (URP include 提供),故自递归在那边是不可见的潜伏项;本宿主内联镜像即 'Recursion detected'。
// 由宿主前导件兑现,名字进 HostProvides 后镜像不再编译。
float LinearToSRGB(float c) { return (c <= 0.0031308) ? (c * 12.9232102) : (1.055 * pow(abs(c), 1.0 / 2.4) - 0.055); }
vec3 LinearToSRGB(vec3 c) { return vec3(LinearToSRGB(c.r), LinearToSRGB(c.g), LinearToSRGB(c.b)); }
//----------------------------------------------------------------------endregion

//----------------------------------------------------------------------region 引擎态兑现(配方)
#define UNITY_MATRIX_I_M (inverse(ruriObjectToWorld()))
#define UNITY_MATRIX_I_V (inverse(uniform_camera_view_matrix))
#define UNITY_MATRIX_M (ruriObjectToWorld())
#define UNITY_MATRIX_V (uniform_camera_view_matrix)
#define _MainLightColor (vec4(1.0, 1.0, 1.0, 1.0))
#define _MainLightPosition (vec4(normalize(light_main.xyz), 0.0))
#define _ScaledScreenParams (vec4(1920.0, 1080.0, 1.0 + 1.0/1920.0, 1.0 + 1.0/1080.0))
#define _WorldSpaceCameraPos (camera_pos)
#define unity_LightData (vec4(0.0, 0.0, 1.0, 0.0))
#define unity_OrthoParams (vec4(0.0, 0.0, 0.0, is_perspective ? 0.0 : 1.0))
#define unity_WorldToObject (UNITY_MATRIX_I_M)
#define unity_WorldTransformParams (vec4(0.0, 0.0, 0.0, 1.0))
//----------------------------------------------------------------------endregion

//----------------------------------------------------------------------region 宿主原生取值(通道/烘焙网格贴图/环境预滤波)
vec3 ruriSparseColor(SamplerSparse smp, SparseCoord coord, vec3 absent)
{
    vec4 s = textureSparse(smp, coord);
    return s.rgb + absent * (1.0 - s.a);
}

float ruriSparseScalar(SamplerSparse smp, SparseCoord coord, float absent)
{
    vec4 s = textureSparse(smp, coord);
    return s.r + absent * (1.0 - s.g);
}

float ruriBakedOcclusion(SparseCoord coord, float absent)
{
    return (base_ao_tex.is_set || ao_tex.is_set) ? getAO(coord, true, false) : absent;
}

vec3 ruriBakedNormal(SparseCoord coord, vec3 absent)
{
    return (base_normal_texture.is_set || normal_texture.is_set || height_texture.is_set) ? getTSNormal(coord) : absent;
}

vec4 ruriTransparentFrame(vec3 factor, float coverage)
{
    vec3 clamped = clamp(factor, 0.0, 1.0);
    float opacity = 1.0 - min(min(clamped.r, clamped.g), clamped.b);
    vec3 color = opacity > 1e-5 ? (clamped - (1.0 - opacity)) / opacity : vec3(0.0);
    return vec4(color, opacity);
}
//----------------------------------------------------------------------endregion

//----------------------------------------------------------------------region 投影读函数(源纹理 → 宿主输入重建;与清单同源)
// 宿主可绘制通道按网格参数化取值,uv 实参不参与(源侧 ST 平铺对这些通道无效 —— 清单已披露)。
float4 ruriRead_BaseMap(float2 uv) {
    vec3 ruriInput0 = ruriSparseColor(basecolor_tex, ruriSparseCoord, vec3(1.0, 1.0, 1.0));
    float ruriInput1 = ruriSparseScalar(opacity_tex, ruriSparseCoord, 1.0);
    return float4(ruriInput0.x, ruriInput0.y, ruriInput0.z, ruriInput1);
}

float4 ruriRead_BumpMap(float2 uv) {
    vec3 ruriInput0 = ruriBakedNormal(ruriSparseCoord, vec3(0.0, 0.0, 0.0));
    vec4 ruriInput1 = texture(_BumpMap_ba, uv);
    return float4((ruriInput0.x * 0.5 + 0.5), (ruriInput0.y * 0.5 + 0.5), ruriInput1.x, ruriInput1.y);
}

float4 ruriRead_RMOSMap(float2 uv) {
    float ruriInput0 = ruriSparseScalar(roughness_tex, ruriSparseCoord, 0.0);
    float ruriInput1 = ruriSparseScalar(metallic_tex, ruriSparseCoord, 0.0);
    float ruriInput2 = ruriBakedOcclusion(ruriSparseCoord, 0.0);
    float ruriInput3 = ruriSparseScalar(specularlevel_tex, ruriSparseCoord, 0.0);
    return float4(ruriInput0, ruriInput1, ruriInput2, ruriInput3);
}

//----------------------------------------------------------------------endregion

vec3 UnpackNormalScale(vec4 packedNormal, float bumpScale)
{
    packedNormal.w *= packedNormal.x;
    vec3 normal;
    normal.x = packedNormal.w * 2.0 - 1.0;
    normal.y = packedNormal.y * 2.0 - 1.0;
    normal.z = max(1.0e-16, sqrt(1.0 - saturate(normal.x * normal.x + normal.y * normal.y)));
    normal.x *= bumpScale;
    normal.y *= bumpScale;
    return normal;
}

vec3 SampleNormal(vec2 uv, sampler2D bumpMap, float scale)
{
    if (_NORMALMAP)
    {
        vec4 n = half4(texture(bumpMap, uv));
        return half3(UnpackNormalScale(n, scale));
    }
    else
    {
        return half3(0.0, 0.0, 1.0);
    }
}

vec3 NormalizeNormalPerPixel(vec3 n)
{
    return ruriNormalize(n);
}

vec3 TransformTangentToWorld(vec3 directionTS, mat3 tangentToWorld)
{
    return mul(directionTS, tangentToWorld);
}

vec3 ResolveNormalWS(vec3 normalTS, vec3 positionWS, vec3 vertexNormalWS, vec4 tangentWS, vec2 uv)
{
    vec3 N = half3(NormalizeNormalPerPixel(vertexNormalWS));
    vec3 dp1 = ddx_fine(positionWS);
    vec3 dp2 = ddy_fine(positionWS);
    vec2 duv1 = ddx_fine(uv);
    vec2 duv2 = ddy_fine(uv);
    vec3 T;
    vec3 B;
    if (dot(tangentWS.xyz, tangentWS.xyz) > 1e-4)
    {
        T = tangentWS.xyz;
        B = tangentWS.w * cross(N, T);
    }
    else
    {
        vec3 dp2perp = cross(dp2, N);
        vec3 dp1perp = cross(N, dp1);
        vec3 Td = dp2perp * duv1.x + dp1perp * duv2.x;
        vec3 Bd = dp2perp * duv1.y + dp1perp * duv2.y;
        float invmax = rsqrt(max(dot(Td, Td), dot(Bd, Bd)) + 1e-8);
        T = half3(Td * invmax);
        B = half3(Bd * invmax);
    }
    return half3(NormalizeNormalPerPixel(TransformTangentToWorld(normalTS, ruriMat3Rows(T, B, N))));
}

bool IsPerspectiveProjection()
{
    return unity_OrthoParams.w == 0.0;
}

vec3 GetCameraPositionWS()
{
    return _WorldSpaceCameraPos;
}

vec3 GetCurrentViewPosition()
{
    return GetCameraPositionWS();
}

vec3 GetViewForwardDir()
{
    vec3 row2 = float3(UNITY_MATRIX_V[0].z, UNITY_MATRIX_V[1].z, UNITY_MATRIX_V[2].z);
    return -row2;
}

vec3 GetWorldSpaceNormalizeViewDir(vec3 positionWS)
{
    if (IsPerspectiveProjection())
    {
        vec3 V = GetCurrentViewPosition() - positionWS;
        return ruriNormalize(V);
    }
    return -GetViewForwardDir();
}

vec4 GetScaledScreenParams()
{
    return _ScaledScreenParams;
}

vec2 GetNormalizedScreenSpaceUV(vec2 positionCS)
{
    vec2 normalizedScreenSpaceUV = positionCS * (GetScaledScreenParams().zw - 1.0);
    return normalizedScreenSpaceUV;
}

vec2 GetNormalizedScreenSpaceUV(vec4 positionCS)
{
    return GetNormalizedScreenSpaceUV(positionCS.xy);
}

vec3 SampleNormal_BumpMap(vec2 uv, float scale)
{
    if (_NORMALMAP)
    {
        vec4 n = half4(ruriRead_BumpMap(uv));
        return half3(UnpackNormalScale(n, scale));
    }
    else
    {
        return half3(0.0, 0.0, 1.0);
    }
}

void RURI_INIT_COMMON(CharaVaryings input_, out RuriData outRuriData)
{
    outRuriData = ruriZeroRuriData();
    vec4 albedoAlpha = ruriRead_BaseMap(input_.uv);
    outRuriData.alpha = albedoAlpha.w;
    outRuriData.albedo = albedoAlpha.xyz;
    outRuriData.normalTS = SampleNormal_BumpMap(input_.uv, _BumpScale);
    outRuriData.positionCS = input_.positionCS;
    outRuriData.positionWS = input_.positionWS;
    outRuriData.normalWS = ResolveNormalWS(outRuriData.normalTS, input_.positionWS, input_.normalWS, input_.tangentWS, input_.uv);
    outRuriData.viewDirectionWS = GetWorldSpaceNormalizeViewDir(input_.positionWS);
    outRuriData.normalizedScreenSpaceUV = GetNormalizedScreenSpaceUV(input_.positionCS);
    outRuriData.shadowMask = float4(1.0, 1.0, 1.0, 1.0);
}

void RURI_INIT_COMMON(SceneVaryings input_, out RuriData outRuriData)
{
    outRuriData = ruriZeroRuriData();
    vec4 albedoAlpha = half4(ruriRead_BaseMap(input_.uv));
    outRuriData.alpha = albedoAlpha.a;
    outRuriData.albedo = albedoAlpha.rgb;
    outRuriData.normalTS = SampleNormal_BumpMap(input_.uv, _BumpScale);
    outRuriData.positionCS = input_.positionCS;
    outRuriData.positionWS = input_.positionWS;
    outRuriData.normalWS = ResolveNormalWS(outRuriData.normalTS, input_.positionWS, input_.normalWS, input_.tangentWS, input_.uv);
    outRuriData.viewDirectionWS = GetWorldSpaceNormalizeViewDir(input_.positionWS);
    outRuriData.normalizedScreenSpaceUV = GetNormalizedScreenSpaceUV(input_.positionCS);
    outRuriData.shadowMask = float4(1.0, 1.0, 1.0, 1.0);
}

void RURI_SHADOW_COORD(inout RuriData outRuriData)
{
    if (!(MAIN_LIGHT_CALCULATE_SHADOWS))
    {
        outRuriData.shadowCoord = float4(0.0, 0.0, 0.0, 0.0);
    }
    else
    {
        outRuriData.shadowCoord = vec4(0.0);
    }
}

void InitializeCharaData(CharaVaryings input_, out RuriData outRuriData)
{
    RURI_INIT_COMMON(input_, outRuriData);
    if (_UseRMOSMap)
    {
        vec4 rmosMap = ruriRead_RMOSMap(input_.uv);
        outRuriData.roughness = rmosMap.x;
        outRuriData.metallic = rmosMap.y;
        outRuriData.occlusion = rmosMap.z;
        outRuriData.specular = rmosMap.w;
    }
    RURI_SHADOW_COORD(outRuriData);
    outRuriData.bakedGI = half3(0, 0, 0);
}

// ---- 主光 ----

Light GetMainLight()
{
    Light light = ruriZeroLight();
    light.direction = _MainLightPosition.xyz;
    light.distanceAttenuation = unity_LightData.z;
    light.shadowAttenuation = 1.0;
    light.color = _MainLightColor.xyz;
    light.layerMask = _MainLightLayerMask;
    return light;
}

vec3 SafeNormalize(vec3 inVec)
{
    float dp3 = max(1.175494351e-38, dot(inVec, inVec));
    return inVec * rsqrt(dp3);
}

// b5194 片元与 b5190 第 214-248 行的顶点色:方位 = 物体空间里顶点水平方向与主光水平方向的
// 点积映到 [0, 1](光向先整体归一再取 xz),<c>lerp(_OutlineShadowColor, _OutlineColor, 方位) × _OutlineIntensity</c>,
// 再乘 <c>_FinalTint</c>、主光色与底色图;alpha 恒 1。
// 真源在顶点上算方位、光栅插值颜色;这里在着色点上按同一式求值(着色点位置换回物体空间)——
// 两者只在 <c>normalize(xz)</c> 的非线性上有插值差,描边只有一两个像素宽。
void GirlsFrontline_Outline(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData)
{
    Light mainLight = GetMainLight();
    vec3 lightOS = SafeNormalize(mul(mat3(unity_WorldToObject), mainLight.direction));
    vec2 radialOS = mul(unity_WorldToObject, float4(input_.positionWS, 1.0)).xz;
    vec2 radial = radialOS * rsqrt(max(dot(radialOS, radialOS), 1.175494351e-38));
    float azimuth = (dot(radial, lightOS.xz) + 1.0) * 0.5;
    vec4 tint = lerp(_OutlineShadowColor, _OutlineColor, azimuth) * _OutlineIntensity;
    vec3 baseMap = ruriRead_BaseMap(input_.uv * _BaseMap_ST.xy + _BaseMap_ST.zw).xyz;
    vec3 color = tint.xyz * _FinalTint.xyz * mainLight.color * baseMap;
    ruriData.alpha = 1.0;
    outputData.baseColor = baseMap;
    outputData.roughness = ruriData.roughness;
    outputData.metallic = ruriData.metallic;
    outputData.specular = ruriData.specular;
    outputData.normalWS = input_.normalWS;
    outputData.globalIllumination = float4(color, 1.0);
}

// 真源所有 uber 分支共用的开头:底色按 <c>_BaseMap_ST</c> 采样再乘 <c>_BaseColor</c>;
// b2593 第 126-129 行的裁剪 <c>if (baseMap.a * _BaseColor.a - _Cutoff &lt; 0) discard;</c>
// 是前向趟自己的(开关与阈值是这一作自己的两条名字)。
void GirlsFrontline_Setup(inout RuriData ruriData, CharaVaryings input_)
{
    vec4 baseMap = ruriRead_BaseMap(input_.uv * _BaseMap_ST.xy + _BaseMap_ST.zw);
    ruriData.albedo = baseMap.xyz * _BaseColor.xyz;
    ruriData.alpha = baseMap.w * _BaseColor.w;
    if (_UseAlphaTest)
    {
        clip(ruriData.alpha - _Cutoff);
    }
}

float MainLightShadow(vec4 shadowCoord, vec3 positionWS, vec4 shadowMask, vec4 occlusionProbes) {
    return 1.0;
}

// b3024 第 179-190 行:主光阴影在沿光向推过的位置上求。本体只在 <c>_AdjustShadowBias</c> 开时推
// <c>_ShadowBiasDistance</c>;各向异性与脸那两支关着时也推 0.1(b3034 第 174 行、b3035 第 164 行)。
// 级联的深度/法线偏置是阴影图自己的防粉刺量,宿主的阴影自带一份,不属于询问。
float GirlsFrontline_MainLightShadow(vec3 positionWS, vec3 lightDirection, vec4 shadowMask, float closedBias)
{
    float bias = (_AdjustShadowBias ? _ShadowBiasDistance : closedBias);
    vec3 samplePosition = positionWS + lightDirection * bias;
    return MainLightShadow(vec4(0.0), samplePosition, shadowMask, _MainLightOcclusionProbes);
}

// b3035 第 305-334 行:光向进物体空间取 xz 归一化(真源 <c>mul(unity_WorldToObject, L)</c>;
// 这里点乘 <c>UNITY_MATRIX_M</c> 的三列,刚体 + 等比缩放下同一方向),<c>_FaceLightDirAdjustment</c> 把它往 (0, 1) 掰。
// <paramref name="upFade"/> = <c>1 - |物体空间光向.z|</c>(三维单位化后的 z)。
vec2 GirlsFrontline_FaceLightXZ(vec3 lightDirection, out float upFade)
{
    vec3 objectLight = ruriNormalize(float3(dot(lightDirection, UNITY_MATRIX_M[0].xyz), dot(lightDirection, UNITY_MATRIX_M[1].xyz), dot(lightDirection, UNITY_MATRIX_M[2].xyz)));
    upFade = 1.0 - abs(objectLight.z);
    vec2 lightXZ = ruriNormalize(float2(objectLight.x, objectLight.z));
    if (_FaceLightDirAdjustment > 0.0)
    {
        lightXZ = ruriNormalize(float2(lightXZ.x + _FaceLightDirAdjustment * (0.0 - lightXZ.x), lightXZ.y + _FaceLightDirAdjustment * (1.0 - lightXZ.y)));
    }
    return lightXZ;
}

// b3035 第 335-368 行 —— 脸部 SDF。<c>_BlendTex.r</c> 采两次(原 uv、u 镜像),<c>1 - _BlendTex.a &gt; 0.5</c>
// 决定哪一份当近侧与门限落在 [0,1) 还是 [1,2),门限再按光向 x 的正负翻到 <c>2 - t</c>。
// 过渡带是环绕比较(<c>frac((t ± s/2 + 4)·0.5)·2</c>,t + 4 恒正,真源那两个正负分支恒取正的一支)。
float GirlsFrontline_FaceShadow(vec2 lightXZ, float nearSample, float mirroredSample, float faceSide)
{
    bool frontHalf = (1.0 - faceSide) > 0.5;
    float nearSide = (frontHalf ? nearSample : mirroredSample);
    float farSide = (frontHalf ? mirroredSample : nearSample);
    float rawThreshold = (frontHalf ? (lightXZ.y * 0.5 + 0.5) : (lightXZ.y * 0.5 + 1.5));
    float threshold = ((lightXZ.x < 0.0) ? (2.0 - rawThreshold) : rawThreshold);
    float smoothness = _BlendSmoothness;
    float lower = frac((threshold - smoothness * 0.5 + 4.0) * 0.5) * 2.0;
    float upper = frac((threshold + smoothness * 0.5 + 4.0) * 0.5) * 2.0;
    float wrapped = 1.0 - saturate(((2.0 - lower) - nearSide) / smoothness);
    float lowerTerm = ((lower < 1.0) ? saturate((farSide - lower) / smoothness) : wrapped);
    float upperTerm = 1.0 - saturate((upper - farSide) / smoothness);
    return ((upper < 1.0) ? min(upperTerm, lowerTerm) : max(wrapped, lowerTerm));
}

// b3024 第 313-338 行:斜坡图的横坐标是衰减三通道的均值(真源 <c>dot(a.xxx, 0.3333)</c>),
// 下限 2^-14;纵坐标是行 —— 主光 0.125、高光 0.375、环境高光偏置 0.625、附加光 0.875。
vec3 GirlsFrontline_RampLight(float attenuation, float row)
{
    float coordinate = max(dot(float3(attenuation, attenuation, attenuation), float3(0.3333, 0.3333, 0.3333)), 6.103515625e-05);
    vec3 ramp = textureLod(_RampMap, ruriUvClamp(_RampMap, float2(coordinate, row)), 0.0).xyz;
    return (coordinate > 6.103515625e-05 ? ramp * min(rcp(coordinate) * attenuation, 1.0) : ramp);
}

vec3 GirlsFrontline_Diffuse(float attenuation, float row)
{
    return (_UseRampMap ? GirlsFrontline_RampLight(attenuation, row) : float3(attenuation, attenuation, attenuation));
}

// b3035 第 398-423 行 —— 脸部高光。相机前向进物体空间,与(掰过的)光向 xz 按 0.85 混合决定采哪半边与阈值;
// 阈值式 <c>saturate(-dir.y - 0.70710677) · 3.4142134</c> 逐字;形由 <c>_BlendTex</c> 的 g/b 双阈值取交,
// 再乘 N·V、<c>_Anisotropy</c>、1/π、<c>saturate(4·(光向.y·0.5 + 0.5) - 3)</c> 与 SDF。
float GirlsFrontline_FaceSpecular(vec2 faceUV, vec2 lightXZ, vec3 viewDirectionWS, vec3 normalWS, float faceShadow)
{
    vec3 cameraForwardWS = -UNITY_MATRIX_I_V[2].xyz;
    vec3 objectForward = ruriNormalize(float3(dot(cameraForwardWS, UNITY_MATRIX_M[0].xyz), dot(cameraForwardWS, UNITY_MATRIX_M[1].xyz), dot(cameraForwardWS, UNITY_MATRIX_M[2].xyz)));
    vec2 steered = ruriNormalize(float2(lightXZ.x + (objectForward.x - lightXZ.x) * 0.85, lightXZ.y + (objectForward.z - lightXZ.y) * 0.85));
    vec2 shapeUV = float2((steered.x < 0.0 ? (1.0 - faceUV.x) : faceUV.x), faceUV.y + -viewDirectionWS.y * _AnisotropyShift);
    vec3 shape = texture(_BlendTex, shapeUV).xyz;
    float threshold = clamp(saturate(-steered.y - 0.70710677) * 3.4142134, 0.01, 0.99);
    float band = ((shape.y >= (1.0 - threshold) ? 1.0 : 0.0)) * ((shape.z >= threshold ? 1.0 : 0.0));
    float intensity = saturate(dot(viewDirectionWS, normalWS)) * band * _Anisotropy * 0.31830987;
    return intensity * saturate((lightXZ.y * 0.5 + 0.5) * 4.0 - 3.0) * faceShadow;
}

vec3 SampleSH(vec3 normalWS) {
    return envIrradiance(normalWS);
}

vec3 GirlsFrontline_IrradianceAlongAxis(vec3 axisWS) {
    return envIrradiance(axisWS);
}

// b3024 第 219-243 行 —— 环境漫反射。<c>_UseGIFlatten</c> 开时亮度换成 SH 在整个球面上的平均
// (真源 <c>dot(SHA.w + 0.3333·SHB.z, 亮度权重)</c>),色度仍取法线方向那一份。
// 宿主答不出 SH 的系数寄存器,只答「某个方向的辐照」;而二阶 SH 在六个轴向上的取值正好把这两个系数解回来:
// ±X 之和 = 2(A + C)、±Y 之和 = 2(A - C)、±Z 之和 = 2(A + Bz),于是 A = (X + Y)/4、Bz = Z/2 - A,
// 逐项代数恒等,不是近似。
vec3 GirlsFrontline_Ambient(vec3 normalWS, bool flatten)
{
    vec3 irradiance = SampleSH(normalWS);
    if (!flatten)
        return max(irradiance, 0.0);
    vec3 clamped = max(irradiance, 0.001);
    vec3 luminanceWeights = float3(0.2126729, 0.7151522, 0.072175);
    vec3 axisX = GirlsFrontline_IrradianceAlongAxis(float3(1.0, 0.0, 0.0)) + GirlsFrontline_IrradianceAlongAxis(float3(-1.0, 0.0, 0.0));
    vec3 axisY = GirlsFrontline_IrradianceAlongAxis(float3(0.0, 1.0, 0.0)) + GirlsFrontline_IrradianceAlongAxis(float3(0.0, -1.0, 0.0));
    vec3 axisZ = GirlsFrontline_IrradianceAlongAxis(float3(0.0, 0.0, 1.0)) + GirlsFrontline_IrradianceAlongAxis(float3(0.0, 0.0, -1.0));
    vec3 constantTerm = (axisX + axisY) * 0.25;
    vec3 zonalTerm = axisZ * 0.5 - constantTerm;
    float sphereLuminance = dot(constantTerm + zonalTerm * 0.3333, luminanceWeights);
    return sphereLuminance * (clamped / dot(clamped, luminanceWeights));
}

uint GetAdditionalLightsCount() {
    return 0;
}

Light GetAdditionalLight(uint index, vec3 positionWS, vec4 shadowMask) {
    return ruriZeroLight();
}

float GirlsFrontline_AdditionalLightKind(uint index) {
    return 0.0;
}

vec2 CalcEnvBRDFApprox(float Roughness, float NoV)
{
    vec4 c0 = half4(-1, -0.0275, -0.572, 0.022);
    vec4 c1 = half4(1, 0.0425, 1.04, -0.04);
    vec4 r = Roughness * c0 + c1;
    float a004 = min(r.x * r.x, exp2(-9.28 * NoV)) * r.x + r.y;
    vec2 AB = half2(-1.04, 1.04) * a004 + r.zw;
    return AB;
}

vec3 DecodeHDREnvironment(vec4 encodedIrradiance, vec4 decodeInstructions)
{
    float alpha = max(decodeInstructions.w * (encodedIrradiance.w - 1.0) + 1.0, 0.0);
    return (decodeInstructions.x * pow(alpha, decodeInstructions.y)) * encodedIrradiance.xyz;
}

// b3024 第 185-218 行 + 第 502-511 行:反射探针按 <c>roughness·6</c> 取 mip,环境 BRDF 是 Karis 的
// 移动端近似(<see cref="CalcEnvBRDFApprox"/> 逐字同式,NoV 用未加偏移的那一个)。斜坡开着时把偏置项 AB.y
// 乘上「朝光那一侧的相机右向」与法线的点积,再去斜坡第 0.625 行取 <c>.x</c>。
// 各向异性与脸那两支没有 RMO,真源把粗糙度恒定成 1、高光色恒定成 0.04(编译器折出 0.018096 与 -0.0024),
// 这里照原式传入,让它自己算出同一对数。
vec3 GirlsFrontline_EnvironmentSpecular(vec3 specularColor, float roughness, float NoV, vec3 normalWS, vec3 viewDirectionWS, vec3 lightDirection)
{
    vec2 environmentBRDF = CalcEnvBRDFApprox(roughness, NoV);
    float offset = environmentBRDF.y;
    if (_UseRampMap)
    {
        vec3 cameraRight = float3(UNITY_MATRIX_V[0].x, UNITY_MATRIX_V[1].x, UNITY_MATRIX_V[2].x);
        vec3 lightSide = ruriNormalize(sign(dot(cameraRight, lightDirection)) * UNITY_MATRIX_I_V[0].xyz);
        offset = textureLod(_RampMap, ruriUvClamp(_RampMap, float2(environmentBRDF.y * saturate(dot(normalWS, lightSide)), 0.625)), 0.0).x;
    }
    vec3 reflectDirection = reflect(-viewDirectionWS, normalWS);
    vec4 encodedIrradiance = vec4(0.2158605, 0.2158605, 0.2158605, 0.5);
    vec3 probeColor = DecodeHDREnvironment(encodedIrradiance, unity_SpecCube0_HDR);
    return probeColor * (specularColor * environmentBRDF.x + offset);
}

void GirlsFrontline_Face(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData, float facing)
{
    vec3 normalWS = ruriNormalize((facing > 0.0 ? input_.normalWS : -input_.normalWS));
    vec3 viewDirectionWS = ruriData.viewDirectionWS;
    vec3 albedo = ruriData.albedo;
    vec2 faceUV = (_UseSpecularUV2 ? input_.uv1.xy : input_.uv);
    vec4 nearSample = texture(_BlendTex, faceUV);
    float mirroredSample = texture(_BlendTex, float2(1.0 - faceUV.x, faceUV.y)).x;
    Light mainLight = GetMainLight();
    vec3 lightDirection = mainLight.direction;
    float shadow = GirlsFrontline_MainLightShadow(ruriData.positionWS, lightDirection, ruriData.shadowMask, 0.1);
    float upFade;
    vec2 lightXZ = GirlsFrontline_FaceLightXZ(lightDirection, upFade);
    float faceShadow = GirlsFrontline_FaceShadow(lightXZ, nearSample.x, mirroredSample, nearSample.w);
    vec3 diffuse = GirlsFrontline_Diffuse((upFade * 0.5 + 0.5) * (shadow * faceShadow), 0.125);
    float specular = GirlsFrontline_FaceSpecular(faceUV, lightXZ, viewDirectionWS, normalWS, faceShadow);
    float shadowedNoL = shadow * max(dot(normalWS, lightDirection), 0.0);
    float specularLight = specular * 0.1 + shadowedNoL * specular * 0.9;
    vec3 color = (diffuse * albedo + specularLight) * mainLight.color * mainLight.distanceAttenuation;
    color += albedo * GirlsFrontline_Ambient(normalWS, _UseGIFlatten);
    vec3 lightPosition = ruriData.positionWS + ((facing > 0.0 ? input_.normalWS : -input_.normalWS)) * 0.005;
    vec4 shadowMask = ruriData.shadowMask;
    InputData inputData = ruriZeroInputData();
    inputData.normalizedScreenSpaceUV = ruriData.normalizedScreenSpaceUV;
    inputData.positionWS = lightPosition;
    uint pixelLightCount = GetAdditionalLightsCount();
    LIGHT_LOOP_BEGIN(pixelLightCount)
        Light light = GetAdditionalLight(lightIndex, lightPosition, shadowMask);
        float additionalUpFade;
        vec2 additionalXZ = GirlsFrontline_FaceLightXZ(light.direction, additionalUpFade);
        float directional = (additionalUpFade * 0.5 + 0.5) * GirlsFrontline_FaceShadow(additionalXZ, nearSample.x, mirroredSample, nearSample.w);
        float punctual = max(dot(normalWS, light.direction), 0.0);
        float attenuation = (GirlsFrontline_AdditionalLightKind(lightIndex) < 1.0 ? directional : punctual);
        color += albedo * GirlsFrontline_Diffuse(attenuation, 0.875) * light.color * light.distanceAttenuation;
    LIGHT_LOOP_END
    color += GirlsFrontline_EnvironmentSpecular(float3(0.04, 0.04, 0.04), 1.0, max(dot(normalWS, viewDirectionWS), 0.0), normalWS, viewDirectionWS, lightDirection);
    color *= _FinalTint.xyz;
    outputData.baseColor = ruriData.albedo;
    outputData.roughness = 1.0;
    outputData.metallic = 0.0;
    outputData.specular = 0.0;
    outputData.normalWS = normalWS;
    outputData.globalIllumination = float4(color, ruriData.alpha);
}

// b384 —— 两张图各走自己的视差位移(顶点腿算的一维偏移:物体空间视线的 z × 顶点色 g × 切线手性
// × <c>unity_WorldTransformParams.w</c>,只加在 u 上),底色不乘 <c>_BaseColor</c>。
// 主光:<c>N·L × 阴影</c> 进 <c>lerp(_ShadowIntensity, 1, ·)</c> 当漫反射、乘高光图当高光,都乘主光色、不乘距离项;
// 环境漫反射**无条件**拍平(与 uber 的 <c>_UseGIFlatten</c> 同一式);附加光是 <c>(底色 + 高光图) × N·L</c>,
// 位置沿法线外推 0.02。出射 alpha 恒 0。
void GirlsFrontline_Eyes(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData)
{
    vec3 normalWS = ruriNormalize(input_.normalWS);
    vec3 viewDirectionWS = ruriData.viewDirectionWS;
    vec3 objectView = ruriNormalize(float3(dot(viewDirectionWS, UNITY_MATRIX_M[0].xyz), dot(viewDirectionWS, UNITY_MATRIX_M[1].xyz), dot(viewDirectionWS, UNITY_MATRIX_M[2].xyz)));
    float parallax = objectView.z * input_.color.y * input_.tangentWS.w * unity_WorldTransformParams.w;
    vec3 baseColor = ruriSampleSrgb(_MainTex, float2(input_.uv.x + parallax * _CorneaParallax, input_.uv.y)).xyz;
    vec3 specularMap = ruriSampleSrgb(_Specularmap, float2(input_.uv.x + parallax * _SpecularParallax, input_.uv.y)).xyz * _SpecularIntensity;
    Light mainLight = GetMainLight();
    vec3 lightDirection = mainLight.direction;
    vec3 samplePosition = ruriData.positionWS + lightDirection * _ShadowBiasDistance;
    float shadow = MainLightShadow(vec4(0.0), samplePosition, ruriData.shadowMask, _MainLightOcclusionProbes);
    float shadowedNoL = saturate(dot(lightDirection, normalWS)) * shadow;
    vec3 color = shadowedNoL * specularMap * mainLight.color + lerp(_ShadowIntensity, 1.0, shadowedNoL) * (baseColor * mainLight.color);
    color += GirlsFrontline_Ambient(normalWS, true) * baseColor;
    vec3 lightPosition = ruriData.positionWS + input_.normalWS * 0.02;
    vec3 lit = baseColor + specularMap;
    vec4 shadowMask = ruriData.shadowMask;
    InputData inputData = ruriZeroInputData();
    inputData.normalizedScreenSpaceUV = ruriData.normalizedScreenSpaceUV;
    inputData.positionWS = lightPosition;
    uint pixelLightCount = GetAdditionalLightsCount();
    LIGHT_LOOP_BEGIN(pixelLightCount)
        Light light = GetAdditionalLight(lightIndex, lightPosition, shadowMask);
        color += light.distanceAttenuation * (lit * light.color) * max(dot(input_.normalWS, light.direction), 0.0);
    LIGHT_LOOP_END
    color *= _FinalTint.xyz;
    outputData.baseColor = baseColor;
    outputData.roughness = ruriData.roughness;
    outputData.metallic = ruriData.metallic;
    outputData.specular = ruriData.specular;
    outputData.normalWS = normalWS;
    outputData.globalIllumination = float4(color, 0.0);
}

// b64(<c>Blend One One</c>):<c>rgb = _MainTex.rgb · _MainColor.rgb · _SpecularIntensity ·
// (saturate(N·L) · 阴影 · 0.8 + 0.2)</c>、<c>a = _MainTex.a · _MainColor.a</c>。
void GirlsFrontline_EyeBlendAdd(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData)
{
    vec4 mask = ruriSampleSrgb(_MainTex, input_.uv);
    Light mainLight = GetMainLight();
    vec3 lightDirection = mainLight.direction;
    vec3 samplePosition = ruriData.positionWS + lightDirection * _ShadowBiasDistance;
    float shadow = MainLightShadow(vec4(0.0), samplePosition, ruriData.shadowMask, _MainLightOcclusionProbes);
    float lightTerm = saturate(dot(lightDirection, input_.normalWS)) * shadow * 0.8 + 0.2;
    vec3 color = lightTerm * (mask.xyz * _MainColor.xyz * _SpecularIntensity);
    outputData.baseColor = mask.xyz;
    outputData.roughness = ruriData.roughness;
    outputData.metallic = ruriData.metallic;
    outputData.specular = ruriData.specular;
    outputData.normalWS = input_.normalWS;
    outputData.globalIllumination = float4(color, mask.w * _MainColor.w);
}

vec4 WriteMultiplyFrame(vec3 factor, float coverage) {
    return ruriTransparentFrame(factor, coverage);
}

// b16(<c>Blend DstColor Zero</c>):<c>rgb = 1 + _SpecularIntensity · (_MainTex.rgb · _MainColor.rgb - 1)</c>,
// 也就是「乘进帧缓冲的因子」;这个部位的 <c>[StylePart]</c> 照抄了那行 <c>Blend</c>,落帧交给宿主。
void GirlsFrontline_EyeBlendMultiply(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData)
{
    vec4 mask = ruriSampleSrgb(_MainTex, input_.uv);
    vec3 factor = 1.0 + _SpecularIntensity * (mask.xyz * _MainColor.xyz - 1.0);
    vec4 frame = WriteMultiplyFrame(factor, ruriData.alpha);
    ruriData.alpha = frame.w;
    outputData.baseColor = mask.xyz;
    outputData.roughness = ruriData.roughness;
    outputData.metallic = ruriData.metallic;
    outputData.specular = ruriData.specular;
    outputData.normalWS = input_.normalWS;
    outputData.globalIllumination = float4(frame.xyz, 1.0);
}

// b3024 第 137-177 行:法线图的 x 取 <c>r·a</c>、y 取 <c>1 - g</c>(绿通道朝下的约定),z 由单位长补出。
// 背面只翻顶点法线,切线与副切线不翻,三者都按插值原样参与合成。
vec3 GirlsFrontline_SurfaceNormal(CharaVaryings input_, float facing)
{
    vec4 packedNormal = ruriRead_BumpMap(input_.uv);
    float x = packedNormal.x * packedNormal.w * 2.0 - 1.0;
    float y = (1.0 - packedNormal.y) * 2.0 - 1.0;
    float z = sqrt(1.0 - min(x * x + y * y, 1.0));
    vec3 vertexNormal = (facing > 0.0 ? input_.normalWS : -input_.normalWS);
    vec3 bitangent = input_.tangentWS.w * cross(input_.normalWS, input_.tangentWS.xyz);
    return ruriNormalize(input_.tangentWS.xyz * x + bitangent * y + vertexNormal * z);
}

// b3034 第 348-362 行:高光带的形是 <c>_BlendTex</c> 在沿 v 推过的 uv 上的 rgb,乘 N·V 与 <c>_Anisotropy</c>;
// 出射 = <c>带 · 0.1/π + (遮罩后的 N·L · 带) · 0.9/π</c>(两个系数在反编译件里是 0.0318309888 与 0.2864788771)。
vec3 GirlsFrontline_AnisotropicBand(vec2 bandUV, float NoV, float maskedNoL)
{
    vec3 band = texture(_BlendTex, bandUV).xyz * NoV * _Anisotropy;
    return band * (INV_PI * 0.1) + maskedNoL * band * (INV_PI * 0.9);
}

void GirlsFrontline_Anisotropic(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData, float facing)
{
    vec3 normalWS = GirlsFrontline_SurfaceNormal(input_, facing);
    vec3 viewDirectionWS = ruriData.viewDirectionWS;
    vec3 albedo = ruriData.albedo;
    vec4 blend = texture(_BlendTex, input_.uv);
    Light mainLight = GetMainLight();
    vec3 lightDirection = mainLight.direction;
    float shadow = GirlsFrontline_MainLightShadow(ruriData.positionWS, lightDirection, ruriData.shadowMask, 0.1);
    float maskedNoL = max(dot(normalWS, lightDirection), 0.0) * (blend.w * shadow);
    vec3 diffuse = GirlsFrontline_Diffuse(maskedNoL, 0.125);
    vec2 bandUV = (_UseSpecularUV2 ? input_.uv1.xy : input_.uv);
    bandUV.y += -viewDirectionWS.y * _AnisotropyShift;
    float NoV = saturate(dot(viewDirectionWS, normalWS));
    vec3 band = GirlsFrontline_AnisotropicBand(bandUV, NoV, maskedNoL);
    vec3 color = (diffuse * albedo + band) * mainLight.color * mainLight.distanceAttenuation;
    color += albedo * GirlsFrontline_Ambient(normalWS, _UseGIFlatten);
    vec3 lightPosition = ruriData.positionWS + ((facing > 0.0 ? input_.normalWS : -input_.normalWS)) * 0.005;
    vec4 shadowMask = ruriData.shadowMask;
    InputData inputData = ruriZeroInputData();
    inputData.normalizedScreenSpaceUV = ruriData.normalizedScreenSpaceUV;
    inputData.positionWS = lightPosition;
    uint pixelLightCount = GetAdditionalLightsCount();
    LIGHT_LOOP_BEGIN(pixelLightCount)
        Light light = GetAdditionalLight(lightIndex, lightPosition, shadowMask);
        vec3 additional = GirlsFrontline_Diffuse(max(dot(normalWS, light.direction), 0.0), 0.875);
        color += albedo * additional * light.color * light.distanceAttenuation;
    LIGHT_LOOP_END
    color += GirlsFrontline_EnvironmentSpecular(float3(0.04, 0.04, 0.04), 1.0, max(dot(normalWS, viewDirectionWS), 0.0), normalWS, viewDirectionWS, lightDirection);
    color *= _FinalTint.xyz;
    outputData.baseColor = ruriData.albedo;
    outputData.roughness = 1.0;
    outputData.metallic = 0.0;
    outputData.specular = 0.0;
    outputData.normalWS = normalWS;
    outputData.globalIllumination = float4(color, ruriData.alpha);
}

// b3026 第 1048-1093 行:丝袜的各向异性 D。切向轴是 <c>normalize(副切线) × N</c>、副轴是 <c>N × 切向轴</c>,
// <c>αT = max(α(1 + g), 0.001)</c>、<c>αB = max(α(1 - g), 0.001)</c>(g = <c>_AnisotropicGXX</c>),
// <c>D = αTαB/π · (αTαB / |v|²)²</c>,这一支带 1/π、不设上限。
float GirlsFrontline_AnisotropicDistribution(float roughness, vec3 normalWS, vec3 bitangentWS, vec3 halfDirection, float NoH)
{
    float alpha = roughness * roughness;
    vec3 tangentAxis = cross(ruriNormalize(bitangentWS), normalWS);
    vec3 bitangentAxis = cross(normalWS, tangentAxis);
    float alphaT = max(alpha * (_AnisotropicGXX + 1.0), 0.001);
    float alphaB = max(alpha * (1.0 - _AnisotropicGXX), 0.001);
    float alphaProduct = alphaT * alphaB;
    vec3 v = float3(dot(tangentAxis, halfDirection) * alphaB, dot(bitangentAxis, halfDirection) * alphaT, NoH * alphaProduct);
    float w = alphaProduct / dot(v, v);
    return w * w * (alphaProduct * 0.31830987);
}

// b3024 第 339-345 行:各向同性的 D 是 Filament 那一式 <c>k = α/(NoH²α² + 1 - NoH²)</c>,
// <c>D = k²</c>,不带 1/π,上限 2048;α = 粗糙度²。
float GirlsFrontline_Distribution(float roughness, float NoH)
{
    float alpha = roughness * roughness;
    float scaled = NoH * alpha;
    float k = alpha / (scaled * scaled + (1.0 - NoH * NoH));
    return min(k * k, 2048.0);
}

// b3024 第 346-387 行 —— 直接光高光。V 是 Hammon 的相关 Smith 快速式,
// F = <c>1 + (saturate(50·F0.g) - 1)·(1 - VoH)^5</c>;斜坡开着时把 D·V 按「法线正对半程向量」那一刻的峰值
// 归一,去斜坡第 0.375 行查形,再乘回峰值。出射先夹到 [0, 10] 再乘高光色。
vec3 GirlsFrontline_Specular(vec3 specularColor, float roughness, float distribution, float NoL, float NoV, float VoH, float LoH)
{
    float alpha = roughness * roughness;
    float oneMinusAlpha = 1.0 - roughness * roughness;
    float visibility = min(rcp(max(NoL * (NoV * oneMinusAlpha + alpha) + (NoL * oneMinusAlpha + alpha) * NoV, 1e-4)) * 0.5, 1.0);
    float grazing = max(1.0 - VoH, 0.001);
    float grazingSquared = grazing * grazing;
    float fresnelWeight = grazing * (grazingSquared * grazingSquared);
    float fresnel = saturate(specularColor.y * 50.0) * fresnelWeight - fresnelWeight + 1.0;
    if (_UseRampMap)
    {
        float peakRatio = alpha / (alpha * alpha);
        float peakDistribution = min(peakRatio * peakRatio, 2048.0);
        float peakVisibility = min(rcp(max(LoH * (VoH * oneMinusAlpha + alpha) + VoH * (LoH * oneMinusAlpha + alpha), 1e-4)) * 0.5, 1.0);
        float coordinate = saturate(distribution * visibility * rcp(peakDistribution) * rcp(peakVisibility));
        vec3 ramp = textureLod(_RampMap, ruriUvClamp(_RampMap, float2(coordinate, 0.375)), 0.0).xyz;
        return specularColor * clamp(fresnel * (peakVisibility * (peakDistribution * ramp)), 0.0, 10.0);
    }
    float term = fresnel * (distribution * visibility);
    return specularColor * clamp(float3(term, term, term), 0.0, 10.0);
}

// b3024 第 294-342 行(主光)与第 363-501 行(附加光)共用的那一段:一盏灯的
// <c>斜坡漫反射 × 漫反射色 + 高光 × 斜坡漫反射</c>,灯色与衰减由调用方乘。
// <paramref name="shadow"/> 只进斜坡横坐标;附加光这一趟不带阴影,传 1。
vec3 GirlsFrontline_BodyLight(vec3 lightDirection, float shadow, float row, vec3 normalWS, vec3 viewDirectionWS, vec3 bitangentWS, vec3 diffuseColor, vec3 specularColor, float roughness, float NoV, bool anisotropicDistribution)
{
    vec3 halfDirection = ruriNormalize(viewDirectionWS + lightDirection);
    float NoL = max(dot(normalWS, lightDirection), 0.0);
    float NoH = max(dot(normalWS, halfDirection), 0.0);
    float VoH = max(dot(viewDirectionWS, halfDirection), 0.0);
    float LoH = max(dot(lightDirection, halfDirection), 0.0);
    float distribution = (anisotropicDistribution ? GirlsFrontline_AnisotropicDistribution(roughness, normalWS, bitangentWS, halfDirection, NoH) : GirlsFrontline_Distribution(roughness, NoH));
    vec3 diffuse = GirlsFrontline_Diffuse(NoL * shadow, row);
    vec3 specular = GirlsFrontline_Specular(specularColor, roughness, distribution, NoL, NoV, VoH, LoH);
    return diffuse * diffuseColor + specular * diffuse;
}

void GirlsFrontline_Body(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData, float facing)
{
    if (_AnisotropicSpecular)
    {
        GirlsFrontline_Anisotropic(ruriData, input_, outputData, facing);
        return;
    }
    vec3 normalWS = GirlsFrontline_SurfaceNormal(input_, facing);
    vec3 viewDirectionWS = ruriData.viewDirectionWS;
    vec3 bitangentWS = input_.tangentWS.w * cross(input_.normalWS, input_.tangentWS.xyz);
    vec3 albedo = ruriData.albedo;
    vec4 rmo = texture(_RMOTex, input_.uv);
    float roughness = rmo.x;
    float metallic = rmo.y;
    bool anisotropicDistribution = _UseStockingFalloff && abs(_AnisotropicGXX) > 0.001;
    float NoVRaw = max(dot(normalWS, viewDirectionWS), 0.0);
    float NoV = min(NoVRaw + 9.9999997e-06, 1.0);
    vec3 diffuseColor = albedo - albedo * metallic;
    vec3 specularColor;
    vec3 emission = albedo * _EmissiveIntensity * rmo.w;
    if (_UseStockingFalloff)
    {
        float stockingLevel = ((anisotropicDistribution ? rmo.w : 0.5)) * 0.08;
        specularColor = albedo * metallic + (stockingLevel - stockingLevel * metallic);
        vec3 falloff = lerp(_StockingFalloffColor.xyz, _StockingCenterColor.xyz, exp2(log2(NoV) * _StockingFalloffPower));
        diffuseColor *= falloff;
        if (anisotropicDistribution)
            emission = float3(0.0, 0.0, 0.0);
    }
    else
    {
        specularColor = albedo * metallic + (0.04 - metallic * 0.04);
    }
    Light mainLight = GetMainLight();
    vec3 lightDirection = mainLight.direction;
    float shadow = GirlsFrontline_MainLightShadow(ruriData.positionWS, lightDirection, ruriData.shadowMask, 0.0);
    vec3 lit = GirlsFrontline_BodyLight(lightDirection, shadow, 0.125, normalWS, viewDirectionWS, bitangentWS, diffuseColor, specularColor, roughness, NoV, anisotropicDistribution);
    vec3 color = lit * mainLight.color * mainLight.distanceAttenuation;
    color += GirlsFrontline_Ambient(normalWS, _UseGIFlatten) * diffuseColor * rmo.z;
    vec3 lightPosition = ruriData.positionWS + ((facing > 0.0 ? input_.normalWS : -input_.normalWS)) * 0.005;
    vec4 shadowMask = ruriData.shadowMask;
    InputData inputData = ruriZeroInputData();
    inputData.normalizedScreenSpaceUV = ruriData.normalizedScreenSpaceUV;
    inputData.positionWS = lightPosition;
    uint pixelLightCount = GetAdditionalLightsCount();
    LIGHT_LOOP_BEGIN(pixelLightCount)
        Light light = GetAdditionalLight(lightIndex, lightPosition, shadowMask);
        vec3 additional = GirlsFrontline_BodyLight(light.direction, 1.0, 0.875, normalWS, viewDirectionWS, bitangentWS, diffuseColor, specularColor, roughness, NoV, anisotropicDistribution);
        color += additional * light.color * light.distanceAttenuation;
    LIGHT_LOOP_END
    color += GirlsFrontline_EnvironmentSpecular(specularColor, roughness, NoVRaw, normalWS, viewDirectionWS, lightDirection);
    color += emission;
    color *= _FinalTint.xyz;
    outputData.baseColor = ruriData.albedo;
    outputData.roughness = roughness;
    outputData.metallic = metallic;
    outputData.specular = rmo.w;
    outputData.normalWS = normalWS;
    outputData.globalIllumination = float4(color, ruriData.alpha);
}

// 部位 = 材质指着哪份着色器(<c>ShaderName</c>),不是属性指纹。
// · <c>uber</c> 与 <c>ubertrans</c> 是**同一个表面**:两份反编译件的片元尾段逐项同构,差别全在 pass 状态与
// 写出的 alpha,所以收成 <c>Aliases</c> 而不是再开一个部位。
// · 脸与身体共用 <c>uber</c>,靠 <c>_UseBlendTex</c>(关键字 <c>_USE_BLEND_TEX</c>)区分。
// · 眼睛三份各是自己的着色器、自己的混合,分成三个部位。
// · 描边趟在 uber 里标作 GFOutline、在 ubertrans 里标作 GFCharTransOutline,两趟逐项同式。
void Fragment_GirlsFrontline(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData, float facing)
{
    if (_RuriOutlineShellGate)
    {
        GirlsFrontline_Outline(ruriData, input_, outputData);
    }
    else
    {
        GirlsFrontline_Setup(ruriData, input_);
        int partId = _CharaPartID;
        if (partId == 1)
            GirlsFrontline_Face(ruriData, input_, outputData, facing);
        else
            if (partId == 2)
                GirlsFrontline_Eyes(ruriData, input_, outputData);
            else
                if (partId == 3)
                    GirlsFrontline_EyeBlendAdd(ruriData, input_, outputData);
                else
                    if (partId == 4)
                        GirlsFrontline_EyeBlendMultiply(ruriData, input_, outputData);
                    else
                        GirlsFrontline_Body(ruriData, input_, outputData, facing);
    }
}

void CalcRuriNPR(inout RuriData ruriData, CharaVaryings input_, inout RuriGBufferData outputData, float facing)
{
    Fragment_GirlsFrontline(ruriData, input_, outputData, facing);
}

vec3 PackGBufferNormal(vec3 normalWS)
{
    float l1 = abs(normalWS.x) + abs(normalWS.y) + abs(normalWS.z);
    float inv = 1.0 / ((l1 > 1e-6 ? l1 : 1e-6));
    vec3 n = normalWS * inv;
    float t = saturate(-n.z);
    vec2 oct = float2(n.x + ((n.x >= 0.0 ? t : -t)), n.y + ((n.y >= 0.0 ? t : -t)));
    vec2 remapped = saturate(float2(oct.x * 0.5 + 0.5, oct.y * 0.5 + 0.5));
    uint2 i = uint2(uint((remapped.x * 4095.5)), uint((remapped.y * 4095.5)));
    uint2 hi = uint2(i.x >> 8, i.y >> 8);
    uint2 lo = uint2(i.x & 255, i.y & 255);
    return float3(lo.x / 255.0, lo.y / 255.0, (hi.x | (hi.y << 4)) / 255.0);
}

GBufferFragOutput RuriGBufferDataToCharaGbuffer(RuriData ruriData, RuriGBufferData outputData)
{
    vec3 packedNormalWS = PackGBufferNormal(outputData.normalWS);
    GBufferFragOutput output_ = ruriZeroGBufferFragOutput();
    float unused = 0;
    output_.gBuffer0 = float4(outputData.baseColor, outputData.alpha);
    output_.gBuffer1 = float4(unused, outputData.metallic, outputData.specular, outputData.occlusion);
    output_.gBuffer2 = float4(packedNormalWS, 1.0 - outputData.roughness);
    output_.color = half4(outputData.globalIllumination);
    return output_;
}

GBufferFragOutput CharaMixedPassFragment(CharaVaryings input_, float facing)
{
    RuriData ruriData;
    InitializeCharaData(input_, ruriData);
    GBufferData gBufferData = ruriSelfGBuffer(ruriData);
    if (_SurfaceType != 1)
    {
        ruriData.albedo = gBufferData.baseColor;
    }
    ruriData.albedo *= _BaseColor.rgb;
    ruriData.alpha *= _BaseColor.a;
    if (!_UseRMOSMap)
    {
        ruriData.roughness = _RoughnessIntensity;
        ruriData.metallic = _MetallicIntensity;
        ruriData.occlusion = 1.0;
        ruriData.specular = _SpecularIntensity;
    }
    RuriGBufferData outputData = ruriZeroRuriGBufferData();
    CalcRuriNPR(ruriData, input_, outputData, facing);
    outputData.baseColor = outputData.globalIllumination.xyz;
    outputData.alpha = ruriData.alpha;
    if (ruriData.overClearedBackdrop > 0.5)
    {
        outputData.baseColor = outputData.baseColor * outputData.alpha;
        outputData.alpha = 1.0;
    }
    return RuriGBufferDataToCharaGbuffer(ruriData, outputData);
}

//----------------------------------------------------------------------region Shader 入口
void shade(V2F inputs) {
    ruriSparseCoord = inputs.sparse_coord;
    CharaVaryings ruriInput = ruriZeroCharaVaryings();
    ruriInput.uv = inputs.sparse_coord.tex_coord;
    ruriInput.positionWS = inputs.position;
    ruriInput.normalWS = normalize(inputs.normal);
    ruriInput.tangentWS = float4(normalize(inputs.tangent), (dot(cross(normalize(inputs.normal), normalize(inputs.tangent)), normalize(inputs.bitangent)) < 0.0) ? -1.0 : 1.0);
    ruriInput.uv1 = float4(0.0, 0.0, 0.0, 0.0);
    ruriInput.uv0zw = float2(0.0, 0.0);
    ruriInput.positionNDC = float4(0.0, 0.0, 1.0, 1.0);
    ruriInput.color = float4(1.0, 1.0, 1.0, 1.0);
    ruriInput.positionCS = gl_FragCoord;
    GBufferFragOutput ruriOut = CharaMixedPassFragment(ruriInput, ((uniform_facing >= 0) ? 1.0 : -1.0));
    alphaOutput(ruriOut.gBuffer0.a);
    diffuseShadingOutput(ruriOut.gBuffer0.rgb);
}
//----------------------------------------------------------------------endregion
