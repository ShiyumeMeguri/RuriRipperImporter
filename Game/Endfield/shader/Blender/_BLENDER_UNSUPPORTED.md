# Blender 后端不可发射账目(逐函数落零值桩,签名保持,调用接线不受影响)

真源侧 `[ShaderCapability<T>]` 声明的、**不可能按计算等价移植**的渲染管线级差异:
割点 = 数学树只导出询问、由运行时接宿主原生等价物;折缺席值 = 本宿主答不出,
落**能力自己声明**的缺席值(不是后端挑的中性数)。

## 栈 ruri_character_uber_endfield

### 宿主答不出的环境询问(按缺席值折叠)

| 能力 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|
| GeometricDistortion | Pipeline | Identity | world-space vertex offset | 形变高度图、它的投影矩阵、作用球与方向都是管线每帧写的状态;编译器看见的只是一次矩阵变换加一次贴图读 |
| HairStylizedFill | Pipeline | Identity | weight of the source pipeline's stylized fill light in hair highlights (1 present, 0 absent) | 补光是源管线着色器里的风格化数学(视角补光向量与背光面底光),编译器看见的只是一串点积与 lerp,推不回「这是在给头发补一盏源管线的假灯」 |
| ScreenDepth | Pipeline | Declared | raw device depth | reversed-Z / 线性 / 对数深度三种约定并存,_ZBufferParams 的打包也是引擎私有;而材质节点图读不到深度缓冲 |
| ScreenSurfaceClass | Pipeline | Declared | the surface class flag the pre-pass wrote for the frontmost opaque surface at this pixel | 类别写在管线预趟的某张 RT 的某个通道里,编号与打包是管线私有的;编译器看见的只是一次按像素的 Load 加一次等值比较,而材质图读不到别的表面画过什么 |

### 不可发射函数

| 函数 | 原因 |
|---|---|
| OverlayShadow | 终点 ret_gBuffer0 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| OverlayShadow | 终点 ret_gBuffer0_w 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |

## 栈 ruri_scene_uber_endfield

### 宿主答不出的环境询问(按缺席值折叠)

| 能力 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|
| ContactShadow | Pipeline | Identity | [0,1] main-light contact shadow attenuation | 步进长度、厚度判定与双线性边界阈值都是管线私有参数,而且它是本帧的一张 RT;编译器看见的只是一次按屏幕坐标的纹理取值 |
| GeometricDistortion | Pipeline | Identity | world-space vertex offset | 形变高度图、它的投影矩阵、作用球与方向都是管线每帧写的状态;编译器看见的只是一次矩阵变换加一次贴图读 |
| ScreenColor | Pipeline | Declared | linear scene radiance | 有的管线是 RT 有的是 copy,分辨率/mip 链/色彩空间各不相同;而材质节点图**根本读不到**已绘制的帧缓冲 |
| ScreenDepth | Pipeline | Declared | raw device depth | reversed-Z / 线性 / 对数深度三种约定并存,_ZBufferParams 的打包也是引擎私有;而材质节点图读不到深度缓冲 |
| VolumetricFogScattering | Pipeline | Declared | linear in-scattered radiance (rgb) and transmittance (w) between the camera and this point | froxel 网格的分辨率、深度分布、抖动与时间累积各家自定(HG 是 _IntegratedLightScattering 一张 3D RT,按抖动后的屏幕坐标与对数深度切片取值);编译器看见的只是一次 3D 纹理取值 |

### 不可发射函数

| 函数 | 原因 |
|---|---|
| SampleNormalMap | uniform _UseBumpMap 挂 [ShaderProperty] 但 Default 缺失/不可解析——禁止发明默认值 |
| DitherClip | uniform _DitherMatrix 为结构/资源型(float4x4),无节点图等价 |
| Lit | 终点 __overlay__ScreenSpaceReflection__weight 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| LitForward | 终点 __overlay__ScreenSpaceReflection__weight 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| LitTransparent | 终点 __overlay__ScreenSpaceReflection__weight 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| LitEffect | 终点 __overlay__ScreenSpaceReflection__weight 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| LitEffectBlend | 终点 __overlay__ScreenSpaceReflection__weight 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| LitHLod | 终点 __overlay__ScreenSpaceReflection__weight 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| Trunk | 终点 __overlay__ScreenSpaceReflection__weight 依赖灯答案却接非 Light 口:宿主下沿污染路径按能力缺席值重算 |
| SceneMixedPassVertex[Lit] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[LitForward] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[LitTransparent] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[LitEffect] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[LitEffectBlend] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[LitHLod] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[Unlit] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[ContainerWater] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[Leaf] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[Grass] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |
| SceneMixedPassVertex[Trunk] | GpuClothPosition 降不下来:uniform _ClothSkeletonDataBuffer 为结构/资源型(StructuredBuffer),无节点图等价 |

## 栈 ruri_effect_uber_endfield

### 宿主答不出的环境询问(按缺席值折叠)

| 能力 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|
| ScreenColor | Pipeline | Declared | linear scene radiance | 有的管线是 RT 有的是 copy,分辨率/mip 链/色彩空间各不相同;而材质节点图**根本读不到**已绘制的帧缓冲 |
| ScreenDepth | Pipeline | Declared | raw device depth | reversed-Z / 线性 / 对数深度三种约定并存,_ZBufferParams 的打包也是引擎私有;而材质节点图读不到深度缓冲 |
| VolumetricFogScattering | Pipeline | Declared | linear in-scattered radiance (rgb) and transmittance (w) between the camera and this point | froxel 网格的分辨率、深度分布、抖动与时间累积各家自定(HG 是 _IntegratedLightScattering 一张 3D RT,按抖动后的屏幕坐标与对数深度切片取值);编译器看见的只是一次 3D 纹理取值 |

### 不可发射函数

| 函数 | 原因 |
|---|---|
| EffectMixedPassVertex[VFXBaseV2] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXDsWrite] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXDistanceField] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXDitherAlpha] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXAdvance] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXTransparentDepthOnly] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXIce] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXElectricWire] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXLine] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXCaptureMesh] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXSmokeVat] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXCharacterOutline] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXCharacterWallhack] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXCharacterGrowing] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXWaterDitherAlpha] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXRefract] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXRadialBlur] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXSmokeSixWay] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXWater] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXWaterRefract] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXDecal] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[VFXFakeVolumeFog] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[CutsceneEffect] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[SceneEffectRain] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |
| EffectMixedPassVertex[UiFrameEffect] | EffectSkinnedPosition 降不下来:uniform _VertexSkinMatrices 为结构/资源型(StructuredBuffer),无节点图等价 |

## 栈 ruri_water_endfield

### 宿主答不出的环境询问(按缺席值折叠)

| 能力 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|
| VolumetricFogScattering | Pipeline | Declared | linear in-scattered radiance (rgb) and transmittance (w) between the camera and this point | froxel 网格的分辨率、深度分布、抖动与时间累积各家自定(HG 是 _IntegratedLightScattering 一张 3D RT,按抖动后的屏幕坐标与对数深度切片取值);编译器看见的只是一次 3D 纹理取值 |


