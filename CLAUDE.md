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

## 🔴 2. 着色器 .py 是生成物,禁止手改

`Game/*/shader/ruri_*.py` 全部由 `Ruri.RenderPipelines.Generator`
(`E:\SpeedProject\AzureNihil\RuriTools\Ruri.RenderPipelines.Generator`,后端
`Source/Ruri.CodeGen.Blender`)生成,`--codegen-only` 就地覆盖部署。**直接编辑下次生成就被冲掉。**
改行为 → 改生成器 → 重生成。

## 🔴 3. 派生态收尾不许在导入路径里写

顶点腿、材质环境查询兑现、灯表、后处理链的**重建时机**只有一个真源:`derived_state.py`。
导入路径只管造东西(`mesh_builder` / `MaterialBuilder` 造完自己 announce),
**任何面板/operator 都不许再调 `apply_vertex_stages` / `apply_post_stages` / `rewire_capabilities`**。
漏调一次 = 画面上毫无痕迹的静默缺失(NPC/浏览器/场景窗口/剧情四条路都栽过)。

## 4. 共用层边界

`RuriRipperPyBridge/` 是与 Substance Painter 插件共享的 git 子模块:**禁止出现 bpy / mathutils**,
也禁止出现任何一个游戏的知识。改它单独提交再 bump 父仓。

## 5. 验收

- 纯 python 部分:`RuriRipperPyBridge/run_tests.py`。
- 插件机制部分:headless 真插件跑
  `BLENDER_USER_SCRIPTS=<profile>/scripts blender.exe --background --python <探针>`
  (**不加 `--factory-startup`**,那会让 profile 的 addons 整个不加载却照样返回 FINISHED)。
  后台没有事件循环 ⇒ `bpy.app.timers` 不会被调用,计时器路径只能手动 tick;
  `depsgraph_update_post` 手动 `view_layer.update()` 即一拍。
- 🛑 不要写 headless 脚本去 cabmap 里全量导角色做诊断(用户 GUI 里 10 秒的事,headless 每次重付
  bootstrap;实测挂 20 分钟只涨 0.59s CPU)。
