# RuriRipperImporter

**Unity 原生 YAML 直进 Blender,无损。不走 FBX,不重导出,不绕弯。**

Unity 在「Force Text」序列化模式下,资源本身就是 YAML 文本 —— `.prefab` /
`.asset` / `.mat` / `.anim` / `.controller` 全是人类可读的明文。这个 Blender 插件
**直接读 Unity 这套原生 YAML**,把网格、真实骨架、带原始贴图的材质、以及全部动画
clip 原样搬进 Blender。

> FBX 那套工作流会给你重新绑骨、搞坏法线、丢顶点流、改骨骼名、再塞进一个瞎猜的坐标系。
> RuriRipperImporter 直接读「真相本身」—— Unity 自己的序列化文本 —— 忠实重建模型。
> **去他妈的 FBX 赶紧死。**

基于 Blender **5.1** 开发验证(4.2+ 可用)。

---

## 它能导入什么

| Unity 数据 | Blender 结果 |
|---|---|
| `.prefab` Transform 层级(class 1/4) | 骨架,每个 transform 一根骨,精确 rest 矩阵 |
| `SkinnedMeshRenderer`(137)+ Mesh `.asset`(43) | 蒙皮网格:坐标、全部 UV 通道、顶点色、蒙皮权重、blendshape |
| `LODGroup`(205) | 只导 **LOD0**;LOD1+ 和 `ShadowsOnly` 阴影代理网格直接丢弃 |
| `MeshRenderer`/`MeshFilter`(23/33) | 静态网格,放到对应节点变换上 |
| `.mat` 材质 | Principled BSDF;自动识别 base color / normal / emission 贴图 |
| 贴图(`.png`,经 `.meta` 的 GUID) | 加载并连线;法线贴图设为 Non-Color |
| Animator(95)→ `.controller` → `.anim`(74) | controller 引用的每个 clip 烘焙成一个 action;blendshape 曲线驱动 shape key |
| MonoBehaviour 等纯引擎数据 | 按设计跳过 |

**贴图识别**(按优先级,第一个有值的属性即采用):
- Base color:`_MainTex`、`_BaseMap`、`_BaseColorMap`、`_Albedo`、`_DiffuseMap` …
- Normal:`_BumpMap`、`_NormalMap`、`_NormalTex` …
- Emission:`_EmissionMap`、`_EmissiveMap` …

---

## 安装

1. 启用插件:Blender → **编辑 ▸ 偏好设置 ▸ 插件 ▸ 安装…** → 选 `RuriRipperImporter`
   文件夹/zip → 勾选启用 **RuriRipperImporter**。
2. 导入入口:**文件 ▸ 导入 ▸ Unity Asset (.prefab / .asset / .anim / .controller)**。
3. https://github.com/FractalTools/Ruri.RipperHook/actions 配置好构建的工具dll路径

插件会从你选的文件向上自动定位工程的 `Assets/` 根目录,并通过同名 `.meta` 里的 GUID
解析每一个贴图 / clip / avatar(只有引用未命中时才扩大扫描范围)。

> **OneDrive 注意**:若 `%APPDATA%\Blender` 被 OneDrive 同步,Blender 的「从磁盘安装」
> 可能静默解压失败。要么先暂停 OneDrive 再装,要么把 Blender 指到非同步目录:设环境变量
> `BLENDER_USER_SCRIPTS=D:\某非同步路径`,把 `RuriRipperImporter` 文件夹丢进其
> `addons\` 子目录。

---

## 用法 —— 一个菜单,四种文件

**文件 ▸ 导入 ▸ Unity Asset** 自动识别你给的文件类型:

| 你选的文件 | 行为 |
|---|---|
| **`.prefab`** | 完整模型:骨架 + LOD0 蒙皮网格 + 材质 + **Animator controller 引用的所有 clip**(作为 action) |
| **`.asset`** | 按 class 判定:Mesh(静态物体)/ AnimationClip / AnimatorController |
| **`.anim`** | 单个 clip,烘焙成 action 应用到**当前激活的骨架**上 |
| **`.controller`** | 它引用的**全部** clip,烘焙成 action 应用到当前骨架上 |

先导模型,之后导 clip 或 controller 会直接套到这个骨架上。

clip **只**来自 Animator controller —— 不靠遍历目录瞎猜。Unity 的负数 fileID(controller
里大量使用)也能正确解析,所以嵌套在状态机和 blend tree 里的 clip 引用全都找得到。

---

## 只有 FBX 二进制怎么办

如果某个模型在 Unity 工程里只有二进制 `.fbx`,附带的编辑器工具
**`RuriYamlDumper/RuriYamlDumper.cs`** 能在 Unity 内把它转成同款 YAML —— 等于把手动
「选中子资源 → Ctrl+D 抽出」对整个模型一次性自动化。

1. 把 `RuriYamlDumper.cs` 丢进 Unity 工程任意 `Editor/` 文件夹。
2. **Project Settings ▸ Editor ▸ Asset Serialization** 设为 **Force Text**。
3. 在 Project 窗口右键模型 → **Ruri ▸ Dump Model to YAML (for Blender)**。
4. 用本插件导入生成的 `<model>_yaml/<model>.prefab`。

它会实例化模型、**完全解包** prefab 连接(让层级内联展开,而不是只留一个指向 FBX 的瘦
引用),抽出并重指向每个 Mesh / 内嵌 Material / Avatar / AnimationClip,最后存成扁平
prefab。已端到端验证:真实角色 FBX → dump → Blender,骨架、贴图、被自身 clip 驱动全部正常。

> FBX 模型没有 `LODGroup`,所以会导入所有 LOD —— 只要 LOD0 的话,在 Blender 里把
> `*_lod1/2/3` 网格物体删掉即可。

---

## 全版本支持(不硬编码 class id)

派发是按 `!u!<id>` 头里那个**跨版本稳定的数字 class id** 来的,经 `class_registry.json`
解析 —— 这张表由 **1398** 份 Unity TypeTreeDump 类表合并而成(3.4 → 6000.x)。类名在版本
间会改(id 29 `Scene`→`SceneSettings`、id 1001 `DataTemplate`→`Prefab`);表里把每个历史
名字都映射到它的 id,所以任何版本的 YAML 都能正确识别类型。有新的 dump 时用
`python tools/build_class_registry.py` 重新生成。

---

## 原理(关键部分)

- **自研 YAML 解析器** —— 零依赖、单遍扫描,超大十六进制 blob 原样保留。560 KB 的 prefab
  约 50ms 解析完。负数 fileID、Unity 的同缩进块序列等怪写法全部吃下。
- **网格解码** —— 按通道表读交错的顶点流,处理打包的 `dimension` 字节、遵守 Unity 的
  16 字节流对齐、过滤某些 writer 末尾多吐的非法字符。按存储的 AABB 与权重和逐位校验。
- **坐标转换** —— Unity 左手 Y-up,Blender 右手 Z-up。转换用反射矩阵 `C = swap(Y,Z)` 做
  共轭 `M_blender = C · M_unity · C`,一次性搞定朝向和手性,无需逐四元数特判;三角形绕序
  反转以保证法线朝外。
- **bind-pose 烘焙** —— 顶点通过 `Σ wᵢ · (boneWorldᵢ · bindposeᵢ) · v_local` 变换到 bind
  姿势的世界空间,无论模型原本在什么坐标系下创作都能与骨架对齐(这些是 3ds Max `Bip001`
  绑定)。静止姿势下蒙皮变形为单位矩阵 → 精确 bind pose。
- **动画(全链路 numpy 向量化)** —— 曲线三次 Hermite 求值、TRS 合成、共轭、矩阵→四元数
  分解全部整通道数组化(分解逐分支复刻 Blender 自己的 Mike-Day 实现,与逐帧
  `decompose()` 数值等价到 fp32 噪声级);`foreach_set` 批量写 fcurve;Blender 4.4+
  slotted action。humanoid clip 走完整肌肉重定向(Avatar referential + TwistSolve +
  根运动轨迹语义),EndField 风格 rig 另有距离权重 IK 矫正。
- **三级 clip 数据通道,按可用性自动降级** ——
  ① 桥模式(cabmap):C# 侧导出时直接把曲线打成 float32 blob 跨 pythonnet 传来,
  Python 端 `numpy.frombuffer` 零解析(82.6MB YAML 的 clip = 9.2MB blob,导入
  **26s → 2s**);② 磁盘 `.anim`:编译 regex + numpy C 级字符串转换直取 m_Curve
  块(与全解析逐位一致,82MB 约 3s,带逐 entry 关键帧计数自校验,任何结构意外自动
  回退③);③ 通用 YAML 全解析(兜底,永远正确)。

---

## 按游戏解耦的 GUI

hook 是针对具体游戏写的,面板也该是。所以**一个游戏一个文件夹**:`Game/<游戏>/` 里放
这个游戏专属的 tab、面板和导入通道,`Game/__init__.py` 只是个注册表。

联结方式不是约定而是精确推导:hook id 就是 `{GameName}_{Version}`
(`Ruri.Hook.RuriHook.BuildHookId`,如 `EndField_1.4.4`),所以一个 hook 属于哪个游戏 =
最后一个下划线之前的部分,而一个游戏模块的 `game_name` 就是上游那个 `GameType` 成员。
于是:

- **勾上某个游戏的任意版本 hook → 它的 tab 才出现**,不需要维护版本清单;
- 取消勾选,tab 连同它的操作符一起消失,停留在该 tab 的界面自动退回 `VirtualAssetBundle`;
- `cabmap_panel.py`(微内核:hook 选择、cabmap 构建/加载、虚拟资源浏览、导入)
  **代码里出现不了任何游戏名**,加一个游戏 = 新建一个文件夹,核心零改动。

目前 `Game/EndField/` 提供 Scene(流式场景整图导入)与 Character(SkeletalMorph 表情
系统)两个 tab,覆盖 Endfield 全部已 hook 的版本。

---

## 文件清单

```
RuriRipperImporter/            ← 插件本体(装这个)
  __init__.py                  ← 单一「Unity Asset」导入算子 + 注册/重载
  coordinate.py hierarchy.py   ← 共用实现的 mathutils 边界(见下)
  armature_builder.py mesh_builder.py material_builder.py
  animation_builder.py prefab_importer.py
  cabmap_panel.py              ← 微内核面板:hook / cabmap / 浏览 / 导入,零游戏知识
  Game/                        ← 按游戏解耦的 GUI(见上)
    __init__.py                ← 注册表:GameModule / GameTab + hook id → 游戏
    EndField/                  ← Endfield 全版本专属
      __init__.py              ← GAME_MODULE 声明(两个 tab + 默认 hook id)
      scene_panel.py scene_importer.py character_panel.py
  ruri_pybridge/               ← git submodule:与 Painter 插件共用的那一半
    unity/    Unity YAML 解析、class id 表、GUID 解析、网格解码、
              Renderer 发现、材质属性、clip 曲线与重锚、Avatar 骨架
    runtime/  依赖 bootstrap、CoreCLR + RipperHook 桥、列式行表、设置/工作区
    session/  cabmap 浏览模型
    game/     一个游戏一个子包(endfield:寻址路径/LOD 规则、场景 placement、
              SkeletalMorph 表情系统)
    math3d/   Unity→Blender / Unity→glTF 坐标空间
RuriYamlDumper/RuriYamlDumper.cs ← 可选:Unity 端 FBX→YAML 导出工具
```

克隆时别忘了子模块:

```bash
git clone --recurse-submodules https://github.com/ShiyumeMeguri/RuriRipperImporter.git
# 已经克隆过的:
git submodule update --init --recursive
```

`ruri_pybridge` 里**不许出现 bpy/mathutils**(它同时要在 Substance Painter 里跑,那边
没有这两个东西)。所以 `coordinate.py` / `hierarchy.py` 留在插件里,只做一件事:把共用层
的 numpy 4x4 在边界上转成 `mathutils.Matrix`。这条规矩由自测本身把关(`test_no_host_imports`
按 AST 扫全包);共用层自带 145 个自测:

```bash
python ruri_pybridge/run_tests.py
```

---

## 限制

- 一个只「引用」二进制 `.fbx` 的 prefab 里没有 YAML 几何数据 —— 导入器会**主动识别**这种
  thin-PrefabInstance 形状并在警告里直接给出 dumper 操作指引(见上文);数据本身仍需先转一次。
- 顶点法线:全部标准 `VertexAttributeFormat`(Float32/16、S/UNorm 8/16)+ 打包
  R10G10B10A2(SNorm/UNorm 双解释试探)都能解码;**任何**解码结果都要过单位向量场信任门,
  过不了才退回 Blender 自算(私有魔改编码仍会触发退回 —— 永远不会把垃圾法线塞给你)。
- humanoid 肌肉/重定向:**完整应用**(Avatar referential、swing-twist、TwistSolve、根运动
  轨迹、EndField 扩展肌肉枚举重映射与 IK 矫正)。前提是 Avatar 在作用域内(桥模式闭包
  co-seed / 角色导入时盖章到骨架);找不到 Avatar 时 body 动作丢弃并明确警告。
- 材质:Principled BSDF 上接 base/normal/emission,外加实测过 HLSL 的打包 PBR 通道
  (`_MROMap` R=金属 G=粗糙 B=AO、`_MetallicGlossMap` R=金属 A=光滑度、发丝分离法线);
  未知 shader 家族的打包贴图**不猜通道序**(宁缺勿错)。完整 NPR shader graph 复刻不在范围内。
- 超大 clip 性能见「原理」一节的三级通道:桥模式 ~2s、磁盘 regex 快路径 ~3s(82MB 实测),
  纯 Python 全解析仅作兜底。
