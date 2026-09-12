# RuriRipperImporter

> # 🟦🟧 一个目录,同时是 Blender 插件和 Substance Painter 插件
>
> **装的是同一份代码。** Blender 从 `scripts/addons` 加载它,Substance Painter 从
> `python/plugins` 加载**同一个目录**(做一个目录联接就行,见下)。
>
> 面板长得一样、按钮一样、流程一样 —— 在哪边点,做的都是同一件事,区别只在最后落成什么:
>
> | 你按下 Load Model 之后 | 得到 |
> |---|---|
> | **Blender** | 骨架 + 蒙皮网格 + 材质 + 形态键 + 动画,一整套在场景里 |
> | **Substance Painter** | 一个工程,模型已经在里面,**每个材质一个 Texture Set,并且接好了这个游戏的着色器** |
>
> 不用装两个插件、不用记两套操作,也不用先倒进 Blender 再手动搬去 Painter。

**指着游戏安装目录,把里面的角色和场景直接搬进你手上这个软件。**
不导 FBX,不转格式,不用先开 Unity。

基于 Blender **5.1**(4.2+ 可用)和 Substance Painter **12** 验证。

---

## 装它

### 两边都要的一步

工具 DLL 从 https://github.com/FractalTools/Ruri.RipperHook/actions 下载构建产物,解压到
任意目录 —— 面板里那个 **Bin Dir** 填的就是它。做一次,两个软件共用。

### Blender

**编辑 ▸ 偏好设置 ▸ 插件 ▸ 安装…** → 选 `RuriRipperImporter` 文件夹或 zip → 勾选启用。

面板在 **3D 视图 ▸ 按 N ▸ 侧栏的 `RuriRipper` 页签**。

> **OneDrive 注意**:`%APPDATA%\Blender` 被 OneDrive 同步的话,Blender 的「从磁盘安装」
> 可能静默解压失败。要么先暂停 OneDrive,要么设环境变量
> `BLENDER_USER_SCRIPTS=D:\某个不同步的路径`,把文件夹丢进它的 `addons\` 里。

### Substance Painter

**不要复制一份**,做个目录联接指到 Blender 那份(管理员 CMD 里跑一次):

```bash
mklink /J "%USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\python\plugins\RuriRipperImporter" "<你的 Blender addons 目录>\RuriRipperImporter"
```

然后开 Painter → 在 **Python 菜单里勾上 `RuriRipperImporter`** → 重启 Painter。

面板在**右侧面板条**上,图标是 **R**(关掉了也用它开回来)。

---

## 用它

面板从上往下就是流程,两边一模一样:

1. **Game Root** 指到游戏安装目录 —— 插件读安装自己发布的身份,页签立刻变成**那个游戏**的。
2. **Build** 建一次 cabmap(这个安装的资源索引),以后每次只要 **Load**。
3. 上面一排页签:
   - **VirtualAssetBundle** —— 整个安装的资源浏览器:搜名字、按类型过滤、进文件夹、多选、
     导入。想要什么就搜什么。
   - **游戏自己的页签** —— 比如终末地的 **Character**(名册里点一个人,Load Model 一键到位)、
     **StreamingScene**(整块场景)、**Look**(画面与着色旋钮)。
4. 选中一行 → 底下的导入选项 → **Load Model / Import**。

Blender 还多一个入口:**文件 ▸ 导入 ▸ Unity Asset**,直接选 Unity 工程里的
`.prefab` / `.asset` / `.anim` / `.controller`(不需要 cabmap)。先导模型,之后再导
clip 或 controller 会直接套到这副骨架上。

---

## 支持哪些游戏

| 页签来自 | 游戏 |
|---|---|
| `Game/Endfield/` | 明日方舟:终末地 |
| `Game/EXILIUM/` | 少女前线2:追放 |
| `Game/Illusion/` | Koikatu / KoikatsuSunshine / HoneyCome / SamabakeScramble |
| `Game/SBUE/` | **任何**虚幻引擎安装(按引擎家族认领,不是一个一个游戏加的) |

**认哪个游戏不用你选**:插件读安装自己写下的产品名(`app.info`),页签跟着改名。
没做过专属页签的游戏,照样能用 VirtualAssetBundle 浏览器翻它的资源。

加一个游戏 = 新建一个 `Game/<游戏>/` 文件夹,核心一行不用改。

---

## 有些东西 Painter 那边不出现,这是故意的

Painter 没有骨骼、没有时间轴、没有形态键 —— 这不是没做,是**它的 API 里根本没有那个面**
(Adobe 自己发布的 22 个 Python 模块里,`morph` / `blend shape` / `skeleton` / `bone` /
`animation` / `timeline` 全部零命中)。

规矩是:**能跨的全跨,跨不了的按「段」消失,不是整个页签消失。** 所以 Character 页签两边
都在、名册两边都有,只有「表情库」和「剧情动画」那两段在 Painter 上不出现。两边各 16 个
页签,一格不差。

---

## 只有 FBX 二进制怎么办

模型在 Unity 工程里只有二进制 `.fbx` 时,附带的
**`RuriYamlDumper/RuriYamlDumper.cs`** 能在 Unity 里把它转成可读的 YAML:

1. 把 `RuriYamlDumper.cs` 丢进 Unity 工程任意 `Editor/` 文件夹。
2. **Project Settings ▸ Editor ▸ Asset Serialization** 设为 **Force Text**。
3. Project 窗口右键模型 → **Ruri ▸ Dump Model to YAML (for Blender)**。
4. 导入生成的 `<model>_yaml/<model>.prefab`。

它会实例化模型、**完全解包** prefab 连接,抽出并重指向每个 Mesh / 内嵌 Material /
Avatar / AnimationClip,存成扁平 prefab。已端到端验证过:真实角色 FBX → dump → Blender,
骨架、贴图、被自身 clip 驱动全部正常。

> FBX 模型没有 `LODGroup`,所以会导入所有 LOD —— 只要 LOD0 的话,把 `*_lod1/2/3`
> 网格物体删掉即可。

---

## 已知边界

- **只导 LOD0**;LOD1+ 和 `ShadowsOnly` 阴影代理网格直接丢弃。
- **顶点法线**解不出可信结果时退回 Blender 自算 —— 宁可让 Blender 重算,也不会把垃圾法线
  塞给你。
- **humanoid 动画**完整还原(肌肉、重定向、根运动);前提是 Avatar 在作用域内,找不到时
  body 动作会丢弃并明确警告,不会静默给你一副不动的骨架。
- **材质**接 base / normal / emission 加实测过的打包 PBR 通道;**未知 shader 家族的打包
  贴图不猜通道序**(宁缺勿错)。要完整还原游戏画面靠的是生成的着色栈(终末地已经有),
  不是拿 Principled 凑。
- **Painter 侧**没有骨骼 / 动画 / 表情(见上一节)。

---

## 给要改代码的人

克隆时别忘了子模块:

```bash
git clone --recurse-submodules https://github.com/ShiyumeMeguri/RuriRipperImporter.git
```

```bash
git submodule update --init --recursive
```
