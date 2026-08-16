# RuriRipperImporter — 项目特化铁律

> 通用工程铁律继承 skill `ruri-engineering-discipline`,本文只放本仓特化。
> 条款与用户指令冲突或条款本身错 → 先改本文,再写代码。

## 🔴 1. 游戏特定逻辑一律不许写在 py 里

**判据:`Game/**/*.py` 只能有 bpy 面板、operator、状态属性、以及对数据集/桥的调用。**
凡是「解析某游戏的表/名字/槽位/材质码/LOD 规则/闭包/资产发现」这类逻辑,一律实现在:

```
D:\Ruri\Git\FractalTools\Ruri-RipperHook\Source\Ruri.RipperHook\AssetRipperGameHook
```

py 侧只经 `cabmap_state.BRIDGE` / `datasets.*` 拿**已经算好的结果**。

**为什么**:python 逐资产解析是分钟级、单线程、还要跨 CLR 边界来回搬数据;同一套逻辑在
C# 侧是秒级且能并行。把逻辑放在 py 里等于给整条链装一个不可优化的天花板。
**顶级性能是硬要求,不是偏好。**

新游戏 = hook 侧新增数据集 + `Game/<游戏>/` 一个只画面板的文件夹。

## 2. 共用层边界

`RuriRipperPyBridge/` 是与 Substance Painter 插件共享的 git 子模块:**禁止出现 bpy / mathutils**,
也禁止出现任何一个游戏的知识。改它单独提交再 bump 父仓。
