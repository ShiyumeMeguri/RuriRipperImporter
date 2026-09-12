# RuriRipperImporter — 项目特化铁律

> 通用工程铁律继承 skill `ruri-engineering-discipline`,本文只放本仓特化。
> 条款与用户指令冲突或条款本身错 → 先改本文,再写代码。

## 🔴 1. 游戏特定逻辑一律不许写在 py 里

**判据:`Game/**/*.py` 只能有面板、operator、状态属性、以及对数据集/桥的调用。**
凡是「解析某游戏的表/名字/槽位/材质码/LOD 规则/闭包/资产发现」这类逻辑,一律实现在:

```
D:\Ruri\Git\FractalTools\Ruri-RipperHook\Source\Ruri.RipperHook\AssetRipperGameHook
```

py 侧只经 `cabmap_state.BRIDGE` / `datasets.*` 拿**已经算好的结果**。

**为什么**:python 逐资产解析是分钟级、单线程、还要跨 CLR 边界来回搬数据;同一套逻辑在
C# 侧是秒级且能并行。把逻辑放在 py 里等于给整条链装一个不可优化的天花板。
**顶级性能是硬要求,不是偏好。**

新游戏 = hook 侧新增数据集 + `Game/<游戏>/` 一个只画面板的文件夹。

## 🔴 2. 层界(判据可 grep)

| 目录 | 禁止出现 |
|---|---|
| `RuriRipperPyBridge/` | `bpy` / `mathutils` / `substance_painter` / `PySide` / 任何一个游戏的知识 |
| `Kernel/` | `bpy` / `mathutils` / `substance_painter` / `PySide` |
| `Host/<宿主>/` | 另一个宿主的 API;任何游戏名 |
| `Game/<游戏>/` | 别的游戏的知识 |

`RuriRipperPyBridge/` 是独立 git 子模块:改它单独提交再 bump 父仓。

**宿主差异一律声明成能力**(`Kernel/host.py` 的 `SKELETON`/`ANIMATION`/…),
**禁止 `if host.name == ...`** —— 那是一张按宿主分的行为表,判据是能力名里不许出现宿主名。

## 🔴 3. 一个包两个宿主,入口不许分叉

根 `__init__.py` 只做宿主检测 + 分派:Blender 走 `register/unregister`,
Painter 走 `start_plugin/close_plugin/reload_plugin`,两边都落到 `Host/<宿主>/`。

Painter 侧的插件目录是**指向本仓的目录联接**:

```
<Painter user resources>\python\plugins\RuriRipperImporter
  -> D:\Ruri\00.Model\Tools\BlenderProfile\RuriConfig\scripts\addons\RuriRipperImporter
```

看到两条路径指向看起来一样的东西,先假设是同一个目录(`Get-Item -Force | Select LinkType, Target`)。

## 🔴 4. 选项只有一张表

导入选项的唯一声明是 `Kernel/options.py`。两个宿主的控件都从它生成,**禁止手写第二份**。
判据:`grep -n '"import_\|"detail_level"' Host/` 只应命中 schema 的读取,不应命中新的字面量表。

## 🔴 5. 着色栈按宿主投影,路径不硬编码

生成物落在 `Game/<游戏>/shader/<宿主>/`,由生成器配方的 `destination` 决定
(`Ruri.RenderPipelines.Generator/Assets/Recipes/{Blender,Substance}.json`)。
消费方一律走 `Kernel/shaderstack.py` 按 (游戏, 宿主) 解析,**代码里不许出现着色器名或游戏名**。

改生成物里的宿主回调路径(`RegistryModule` / `RigIdentityModule`)= **改配方再重生成**,
不许手改产物 —— 产物是配方的投影。重生成+部署:

```bash
dotnet run --project Ruri.RenderPipelines/Ruri.Generator.Cli -c Release -- --deploy-shaders Assets/Recipes/Blender.json
```

## 6. 收工验证(两个宿主各一条)

Blender(headless 能验注册与导入图,验不了 `draw()`):

```bash
BLENDER_USER_SCRIPTS=D:/Ruri/00.Model/Tools/BlenderProfile/RuriConfig/scripts blender.exe --background --python <探针>
```

Painter(不能 headless):最接近的可跑检查是把 `substance_painter` / `PySide6` 打桩后
真 import 整棵驱动树 —— 模块体全部执行,宿主绑定、设置表按 schema 建键、选项控件按 schema 建出来。
真 API 语义仍然只能开 GUI 验。
