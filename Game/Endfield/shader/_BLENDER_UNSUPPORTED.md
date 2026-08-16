# Blender 后端不可发射账目(逐函数落零值桩,签名保持,调用接线不受影响)

## 环境询问(能力身份)

真源侧 `[ShaderCapability<T>]` 声明的、**不可能按计算等价移植**的渲染管线级差异。
割点 = 数学树只导出询问、由装配器接宿主原生等价物;折缺席值 = 本宿主答不出,
落**能力自己声明**的缺席值(不是后端挑的中性数)。

| 能力 | 处置 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|---|
| AdditionalLight | 割点(宿主兑现) | Scene | Identity | punctual light record (direction toward light, linear radiance) | 附加光的存储是管线私有的(聚簇灯表 / 瓦片索引 / Forward+ 的 z-bin),下标的含义每家都不同;编译器看见的只是几次带下标的 buffer 读 |
| AdditionalLightCount | 割点(宿主兑现) | Scene | Identity | light count | 可见灯数是**逐帧剔除的结果**:聚簇/瓦片/Forward+ 各家的剔除与排序都不一样,编译器只看得见一次 cbuffer 读 |
| MainLight | 割点(宿主兑现) | Scene | Identity | directional light record (direction toward light, linear radiance) | 主光从哪来是**管线的组织方式**:有的走 cbuffer 单槽(HG 的 type_LightDataBuffer c0/c1),有的走聚簇灯列表首项,有的按可见性每帧重排;编译器看见的只是几次 cbuffer 读 |
| ScreenDepth | 折缺席值 | Pipeline | Declared | raw device depth | reversed-Z / 线性 / 对数深度三种约定并存,_ZBufferParams 的打包也是引擎私有;而材质节点图读不到深度缓冲 |
| ShadowAttenuation | 折缺席值 | Pipeline | Subsumed | [0,1] attenuation | CSM / ASM / 接触阴影 / 屏幕空间阴影四条链各家自选,级联划分与滤波核全是私有实现;编译器看见的是一堆 shadowmap 比较采样 |

## 不可发射函数

| 函数 | 原因 |
|---|---|
| EndfieldCharaGBufferPassVertex[OverlayShadow] | 内建 mul 无节点图等价 |
| OutlinePassVertex[clipPos.z 偏置] | 光栅深度测试偏置无光追等价物;其买到的内侧遮挡由真实几何序提供,xy 外扩照发 |
