# Blender 后端不可发射账目(逐函数落零值桩,签名保持,调用接线不受影响)

| 函数 | 原因 |
|---|---|
| EndfieldCharaGBufferPassVertex[OverlayShadow] | 内建 mul 无节点图等价 |
| OutlinePassVertex[clipPos.z 偏置] | 光栅深度测试偏置无光追等价物;其买到的内侧遮挡由真实几何序提供,xy 外扩照发 |
