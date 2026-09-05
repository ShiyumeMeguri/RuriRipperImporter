# Blender 后端不可发射账目(逐函数落零值桩,签名保持,调用接线不受影响)

真源侧 `[ShaderCapability<T>]` 声明的、**不可能按计算等价移植**的渲染管线级差异:
割点 = 数学树只导出询问、由运行时接宿主原生等价物;折缺席值 = 本宿主答不出,
落**能力自己声明**的缺席值(不是后端挑的中性数)。

## 栈 ruri_character_uber_endfield

### 环境询问(能力身份)

| 能力 | 处置 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|---|
| AdditionalLight | 割点(宿主兑现) | Scene | Identity | punctual light record (direction toward light, linear radiance) | 附加光的存储是管线私有的(聚簇灯表 / 瓦片索引 / Forward+ 的 z-bin),下标的含义每家都不同;编译器看见的只是几次带下标的 buffer 读 |
| AdditionalLightCount | 割点(宿主兑现) | Scene | Identity | light count | 可见灯数是**逐帧剔除的结果**:聚簇/瓦片/Forward+ 各家的剔除与排序都不一样,编译器只看得见一次 cbuffer 读 |
| MainLight | 割点(宿主兑现) | Scene | Identity | directional light record (direction toward light, linear radiance) | 主光从哪来是**管线的组织方式**:有的走 cbuffer 单槽(HG 的 type_LightDataBuffer c0/c1),有的走聚簇灯列表首项,有的按可见性每帧重排;编译器看见的只是几次 cbuffer 读 |
| ScreenDepth | 折缺席值 | Pipeline | Declared | raw device depth | reversed-Z / 线性 / 对数深度三种约定并存,_ZBufferParams 的打包也是引擎私有;而材质节点图读不到深度缓冲 |
| ScreenSpaceShadowMask | 折缺席值 | Pipeline | Identity | [0,1] screen-space resolved shadow mask | 解算通道数与语义各家自定(HG 是 .x 场景 / .y 角色图集),而且它是本帧的一张 RT;编译器看见的只是一次按屏幕坐标的纹理取值 |
| ShadowAttenuation | 折缺席值 | Pipeline | Identity | [0,1] attenuation | CSM / ASM / 接触阴影 / 屏幕空间阴影四条链各家自选,级联划分与滤波核全是私有实现;编译器看见的是一堆 shadowmap 比较采样 |

### 不可发射函数

| 函数 | 原因 |
|---|---|
| EndfieldCharaGBufferPassVertex[OverlayShadow] | 内建 mul 无节点图等价 |

## 栈 ruri_scene_uber_endfield

### 环境询问(能力身份)

| 能力 | 处置 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|---|
| AdditionalLight | 割点(宿主兑现) | Scene | Identity | punctual light record (direction toward light, linear radiance) | 附加光的存储是管线私有的(聚簇灯表 / 瓦片索引 / Forward+ 的 z-bin),下标的含义每家都不同;编译器看见的只是几次带下标的 buffer 读 |
| AdditionalLightCount | 割点(宿主兑现) | Scene | Identity | light count | 可见灯数是**逐帧剔除的结果**:聚簇/瓦片/Forward+ 各家的剔除与排序都不一样,编译器只看得见一次 cbuffer 读 |
| AmbientIrradiance | 割点(宿主兑现) | Scene | Identity | linear irradiance | SH 的阶数/编码/打包各家不同,HG 干脆换成辐照度体 clipmap(_IrradianceVolumeClipmapTexture*)——编译器只看得见几次 3D 图读加一串点积,推不回「这是环境辐照度」 |
| MainLight | 割点(宿主兑现) | Scene | Identity | directional light record (direction toward light, linear radiance) | 主光从哪来是**管线的组织方式**:有的走 cbuffer 单槽(HG 的 type_LightDataBuffer c0/c1),有的走聚簇灯列表首项,有的按可见性每帧重排;编译器看见的只是几次 cbuffer 读 |
| ScreenColor | 折缺席值 | Pipeline | Declared | linear scene radiance | 有的管线是 RT 有的是 copy,分辨率/mip 链/色彩空间各不相同;而材质节点图**根本读不到**已绘制的帧缓冲 |
| ScreenDepth | 折缺席值 | Pipeline | Declared | raw device depth | reversed-Z / 线性 / 对数深度三种约定并存,_ZBufferParams 的打包也是引擎私有;而材质节点图读不到深度缓冲 |
| ShadowAttenuation | 折缺席值 | Pipeline | Identity | [0,1] attenuation | CSM / ASM / 接触阴影 / 屏幕空间阴影四条链各家自选,级联划分与滤波核全是私有实现;编译器看见的是一堆 shadowmap 比较采样 |
| SpecularRadiance | 割点(宿主兑现) | Scene | Declared | linear radiance | 反射探针的存储各家不同(cube / 八面体图集 / 聚簇),粗糙度→mip 的映射也是各家自定;编译器看见的只是一次 cube 采样加一条经验曲线 |


## 栈 ruri_effect_uber_endfield

### 环境询问(能力身份)

| 能力 | 处置 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|---|
| AmbientIrradiance | 割点(宿主兑现) | Scene | Identity | linear irradiance | SH 的阶数/编码/打包各家不同,HG 干脆换成辐照度体 clipmap(_IrradianceVolumeClipmapTexture*)——编译器只看得见几次 3D 图读加一串点积,推不回「这是环境辐照度」 |
| MainLight | 割点(宿主兑现) | Scene | Identity | directional light record (direction toward light, linear radiance) | 主光从哪来是**管线的组织方式**:有的走 cbuffer 单槽(HG 的 type_LightDataBuffer c0/c1),有的走聚簇灯列表首项,有的按可见性每帧重排;编译器看见的只是几次 cbuffer 读 |
| ScreenColor | 折缺席值 | Pipeline | Declared | linear scene radiance | 有的管线是 RT 有的是 copy,分辨率/mip 链/色彩空间各不相同;而材质节点图**根本读不到**已绘制的帧缓冲 |
| ScreenDepth | 折缺席值 | Pipeline | Declared | raw device depth | reversed-Z / 线性 / 对数深度三种约定并存,_ZBufferParams 的打包也是引擎私有;而材质节点图读不到深度缓冲 |


## 栈 ruri_shadowreceiver_endfield

### 环境询问(能力身份)

| 能力 | 处置 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|---|
| MainLight | 割点(宿主兑现) | Scene | Identity | directional light record (direction toward light, linear radiance) | 主光从哪来是**管线的组织方式**:有的走 cbuffer 单槽(HG 的 type_LightDataBuffer c0/c1),有的走聚簇灯列表首项,有的按可见性每帧重排;编译器看见的只是几次 cbuffer 读 |


