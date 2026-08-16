# Blender 后端不可发射账目(逐函数落零值桩,签名保持,调用接线不受影响)

## 环境询问(能力身份)

真源侧 `[ShaderCapability<T>]` 声明的、**不可能按计算等价移植**的渲染管线级差异。
割点 = 数学树只导出询问、由装配器接宿主原生等价物;折缺席值 = 本宿主答不出,
落**能力自己声明**的缺席值(不是后端挑的中性数)。

| 能力 | 处置 | 答案来源 | 缺席契约 | 结果语义 | 编译器为什么认不出 |
|---|---|---|---|---|---|
| MainLight | 割点(宿主兑现) | Scene | Identity | directional light record (direction toward light, linear radiance) | 主光从哪来是**管线的组织方式**:有的走 cbuffer 单槽(HG 的 type_LightDataBuffer c0/c1),有的走聚簇灯列表首项,有的按可见性每帧重排;编译器看见的只是几次 cbuffer 读 |

## 不可发射函数

| 函数 | 原因 |
|---|---|
| OutlinePassVertex[clipPos.z 偏置] | 光栅深度测试偏置无光追等价物;其买到的内侧遮挡由真实几何序提供,xy 外扩照发 |
