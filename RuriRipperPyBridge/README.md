# RuriRipperPyBridge

**两个 RuriRipper 导入器共用的那一半：Unity 资产域 + Ruri.RipperHook 桥。**

[RuriRipperImporter](https://github.com/ShiyumeMeguri/RuriRipperImporter)（Blender
插件）和
[RuriRipperImporterSubstance](https://github.com/ShiyumeMeguri/RuriRipperImporterSubstance)
（Substance 3D Painter 插件）读的是同一份真相、走的是同一条路：pythonnet 在宿主进程里
起 CoreCLR → 进 `Ruri.RipperHook.dll` → cabmap 依赖闭包直接吐 Unity YAML 文本和贴图
字节。两边真正不同的只有**最后一步**（建 Blender 物体 / 写 glTF + Painter 通道），前面
全是同一件事。

这个仓库就是"前面那部分"。作为 **git submodule** 挂进两个插件。

## 唯一一条规则

**这里的代码不许 import 任何宿主 API。** 没有 `bpy`、没有 `mathutils`、没有
`substance_painter`、没有 Qt。只有 stdlib + numpy。

一个模块该不该进来，只看这一条；进不来的（节点树连线、Texture Set、操作符/面板）留在各自
插件里。宿主差异靠**注入**解决，不靠 `if host ==`：

| 差异点 | 注入方式 |
|---|---|
| 坐标系（Blender 换 YZ / glTF 翻 X） | `math3d.coordinate.BLENDER` / `.GLTF` —— 选一个 `Space` |
| bin 目录从哪来（AddonPreferences / JSON） | `pythonnet_bridge.set_bin_dir()` 或 `set_bin_dir_provider()` |
| 工作区放哪 | `runtime.workspace.configure()` |
| 矩阵类型（`mathutils.Matrix`） | 宿主边界上转换，共用层永远是 numpy 4x4 |
| 搜索防抖定时器（`bpy.app.timers` / `QTimer`） | 宿主自己起，调 `cabmap_state.reapply_filter()` |

## 分层

严格单向依赖，`math3d` ← `unity` ← `session`、`runtime` ← `session`：

### `math3d/` —— 坐标空间

`coordinate` 把 Unity → 目标系的整套换算做成**数据**：反射矩阵、绕序、UV 原点、切线手性
各是 `Space` 的一个属性，不是散在各处的代码。两个空间：

* `BLENDER` —— 交换 Y/Z（`p' = (x, z, y)`），UV 不动 → **切线 w 翻转**；
* `GLTF` —— 取反 X（`p' = (-x, y, z)`），UV 翻 V → **切线 w 不动**（反射翻一次、翻 V
  再翻回来，正好抵消）。

切线 w 的符号是 `det < 0` 和 `flip_v` **推导**出来的，不是查表写死的 —— 这也是当初两边
各写一份最容易写反的地方。

### `unity/` —— Unity 资产域

| 模块 | 内容 |
|---|---|
| `unity_yaml` | Unity 序列化 YAML 子集解析器（零依赖、单遍缩进解析，hex blob 原样留字符串） |
| `class_registry` | 全版本 class id ↔ 名字（1398 份 TypeTreeDump 合并），按稳定数字 id 派发 |
| `asset_db` / `bridge_asset_db` | guid 解析：磁盘 `.meta` 扫描 / 桥内存闭包。**同一套鸭子接口**，上层不知道自己在哪种模式 |
| `hierarchy` | Transform 树 → 节点、Unity 世界矩阵、root 相对路径 |
| `mesh_decoder` | 顶点流解码（含 Endfield 八面体法线）+ `diagnose_empty`（空网格到底空在哪） |
| `skinning` | bind-pose 烘焙（法线走逆转置，切线走线性部分） |
| `prefab` | Renderer 发现：LODGroup、ShadowsOnly 代理、禁用/inactive、静态合批窗口 |
| `material` | `m_SavedProperties` 三种序列化形态归一 + 逻辑槽位候选名表 |
| `asset_paths` | 可寻址路径规则、LOD 兄弟选优（`select_best_lod`） |
| `discovery` | 闭包扫描（`peek_class_and_name` 定界嗅探，不全解析）、名字索引、clip 发现 |
| `clip_curves` | 桥的零解析曲线负载（JSON 索引 + float32 payload） |
| `clip_paths` | 曲线绑定重锚：后缀 CRC32 表修 `path_0x...` 占位符与不同嵌套深度 |
| `muscles` | Unity humanoid muscle 分类表（95 muscle / BoneType 枚举序 / twist-solve 对 / 质心公式）+ `is_muscle`/`is_root` |

> `humanoid_retarget`（muscle → 骨骼旋转的实际求解）**留在 Blender 插件里**：它整套数学
> 是拿 `mathutils.Quaternion/Matrix` 写的，进不来。但它依赖的那些表是 Unity 自己的定义、
> 与宿主无关，所以拆出来放在 `muscles` —— 只需要"认出这是不是 humanoid clip"的调用方
> （`clip_paths.clip_is_humanoid`）因此不必拖进一个矩阵库。

### `runtime/` —— 进程管线

| 模块 | 内容 |
|---|---|
| `bootstrap` | ABI 锁定的私有依赖安装（**只装缺的**、显式 `--abi/--platform`、进程内 pip 兜底） |
| `pythonnet_bridge` | CoreCLR 认领（一进程一次）+ `RipperBridge` 全部 API |
| `row_table` | 列式 cabmap 行表（26 万行零 materialize） |
| `workspace` | 生成物落盘位置（**永远不在 checkout 里**） |
| `settings` | JSON 设置存储：默认值、未知键拒绝、版本化修复、**原子写** |

### `session/` —— 会话状态

`cabmap_state`（行表 / 虚拟目录树 / 过滤 / 排序 / 选择 / 动画构建上下文）、
`scene_state`（地图发现 → placement → CAB 解析）。都是模块级全局，因为一个宿主进程就是
一个会话。

## 作为子模块接入

```bash
git submodule add https://github.com/ShiyumeMeguri/RuriRipperPyBridge.git ruri_pybridge
git submodule update --init --recursive
```

包内全是相对 import，所以**签出目录叫什么都行**（两个宿主都用 `ruri_pybridge`）：

```python
from .ruri_pybridge.unity import unity_yaml, prefab, mesh_decoder
from .ruri_pybridge.math3d.coordinate import GLTF as SPACE
from .ruri_pybridge.runtime import bootstrap, pythonnet_bridge
from .ruri_pybridge.session import cabmap_state
```

宿主启动时要做的三件事：

```python
workspace.configure(<插件自己的工作区>)          # 可选，不设就用 OS 用户数据目录
bootstrap.activate()                            # 必须在 import numpy 之前
pythonnet_bridge.set_bin_dir(<Ruri.RipperHook.dll 所在目录>)
pythonnet_bridge.set_bin_dir_hint("在 XXX 里设置")  # 报错文案指向本宿主的 UI
pythonnet_bridge.claim_runtime_early()          # 抢进程唯一的 CLR runtime
bootstrap.ensure_async(log, on_ready=pythonnet_bridge.claim_runtime_early)
```

⚠️ `runtime/bootstrap.py` 和 `runtime/pythonnet_bridge.py` **在装 numpy 之前就要能
import**，所以所有 `__init__.py` 都不做任何 eager import，行表的 numpy 依赖也推迟到函数
体内。别往包初始化里加东西。

⚠️ 重载（Blender 的 Reload Scripts / Painter 的 `reload_plugin`）**不要**重载
`bootstrap` / `pythonnet_bridge` / `cabmap_state` / `scene_state`：它们跟踪的是进程级
真实状态（一旦认领就不能再改的 CLR runtime、几秒才载完的 cabmap），重载只会把"已经做过"
的标记清成源码默认值，而它们跟踪的东西还活着。

## 自测

不需要 Blender、不需要 Painter、不需要 .NET：

```bash
python run_tests.py -v
```

覆盖的是这次抽取里最容易悄悄错的部分：坐标空间**逐元素**对拍原来两份手写实现、YAML 解析
器、Renderer 过滤与跳过计数、材质三种序列化形态、LOD 选优、CRC 重锚（含真机验证过的
`crc32(b"Root") == 0xB6C65665`）、bind-pose 烘焙的逆转置、设置迁移、过滤规则引擎。
