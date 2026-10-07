"""场景派生态的唯一调度器 —— 「什么时候重建 Ruri 生成的场景衍生物」只在这里回答一次。

派生态 = 不是从资产直接读出来、而是**由场景真值算出来**的东西:顶点腿的拓扑(壳层位移 /
反壳描边)、它那几格相机 uniform、脸部骨骼基座、材质的环境查询兑现节点、合成器后处理链。它们的共同点是「输入变了就必须重算,不重算画面静默错」。

## 拓扑只认导入,uniform 才认相机(2026-09-19 用户钦定)

顶点腿曾经是**一整段**:建树、灌相机基轴、接脸部基座全在 `apply_vertex_stage` 里,于是它
不得不同时挂在 CAMERA 与 RIG 上 —— 而挂上去就意味着**推一下镜头就全场重建**,重建又会按
材质**当下的值**重判一次「这张材质该不该有描边」。现场表现:用户手删掉的修改器过一会儿
自己长回来,画面上没有任何东西说明是谁加的。

拆开之后判据很干净:**建拓扑(= 建修改器)只认「有东西进场」**,而那两个事实只由导入路径
announce ⇒ 修改器只在从游戏导入时生成;相机动了、骨改名了各自只重灌自己那几格 uniform,
一个修改器都不创建、一次材质判断都不重做。

## 为什么不是各导入路径自己调 apply_xxx

那等于要求**每一个入口都记得手动收尾**,漏一个就是画面上毫无痕迹的缺失。实测漏了四条:

* NPC 装配路径从不跑顶点腿(壳毛/描边/脸基座全无);
* 浏览器直接导入角色不跑顶点腿(只有 roster 面板那一条补跑了);
* 场景窗口导入两样都不跑;
* 剧情单元建完过场相机后没人重算描边的视图基。

真源不该是「N 个调用点」,而是「一次事实变更 → 调度器决定跑哪些阶段」。
于是导入器**只管造东西**(造完 announce 一声),面板**一行收尾都不写**。

## 事实与阶段

事实(fact)= 派生态依赖的场景真值。阶段(stage)= 一段派生态,声明自己读哪些事实。
调度器把「本批脏了哪些事实」映射成「跑哪些阶段」:

* 新增一条派生态 = STAGES 表加一行;
* 新增一条导入路径 = **零行**(它造对象时已经经过 announce 了);
* 新增一个游戏 = 零行(阶段本体由各游戏生成物自注册进 material_builder 的注册表,
  这里只认注册表,不认任何游戏)。

阶段本体不在这里:生成的着色栈自己 `register_vertex_stage` / `register_capability_rewire`
/ `register_post_stage`(那是生成器拥有的契约)。本模块
只拥有**时机**:谁在什么变更下跑、跑在哪个范围上。

## 插件数据从不写进 .blend(见 plugin_data)

所以打开一个文件时派生态一样都不在:材质的树(模板组是插件数据)、顶点树、合成树、视点。
LOADED 这件事实由开文件与本调度器注册(着色栈刚载入)立起来,第一段阶段(plugin-data)先清光内存里的插件数据、
再让各栈按材质记录把材质全部现编,其余阶段照常按整场景现建 —— **顶点树除外**:它只在导入那一刻生成,
开文件不重填,修改器留空。存盘时文件里什么也不留,没有任何一份派生物会在文件里放旧。
从别的文件 append 进来的材质与对象(APPENDED)带着的树同样是死的,只编那些材质。

## 时机是空闲态,不是操作符结束

批量导入会造几百个对象,逐个收尾是 O(n²);而相机拖动每帧都在变。所以 announce 只打脏
标记,真正落地由一个去抖计时器在事件循环空闲时做一次 —— 一次导入 = 一次收敛。
"""

from __future__ import annotations

import time
import traceback

import bpy

from . import material_builder, shadow_casting


# ---- 事实 ----
OBJECTS = "objects"            # 有新对象进场
MATERIALS = "materials"        # 有新材质进场
CAMERA = "camera"              # 活动相机的身份/位姿/投影/输出分辨率
WORLD = "world"                # 世界被换或被改(环境采样是建组时快照,只有这件事还要重接兑现面)
RIG = "rig"                    # 骨架的骨骼名册变了(顶点腿的骨骼基座按名字接进几何节点)
LOADED = "loaded"              # 插件数据一样都不在:开了一个文件,或着色栈刚载入(插件数据从不写进 .blend)
APPENDED = "appended"          # 材质/对象不经导入进场(从别的文件 append):它们引用的插件数据是死的

ALL_FACTS = frozenset((OBJECTS, MATERIALS, CAMERA, WORLD, RIG, LOADED, APPENDED))

# 去抖窗口:批量导入的几百次 announce、相机拖动的每帧变更,都收敛成末尾的一次落地。
DEBOUNCE_SECONDS = 0.1

# 最近一次落地里炸掉的阶段,面板照着喊。派生态失败在画面上与「着色器本来就长这样」
# 完全无法区分,所以它必须留下能被看见的痕迹,而不是只在控制台滚过去。
LAST_ERROR = ""


class Change:
    """一次落地要处理的变更:脏了哪些事实、这批新造了什么、范围是不是整场景。

    ``whole_scene`` 为真时,已经存在的产物也失效了(相机动了 → 每个描边壳的视图基都
    过期;世界变了 → 每个材质的环境兑现都过期),所以阶段必须扫全场,而不是只看
    这批新对象。"""

    __slots__ = ("facts", "objects", "materials", "whole_scene", "scene", "force")

    def __init__(self, facts, objects, materials, whole_scene, scene, force):
        self.facts = frozenset(facts)
        self.objects = objects
        self.materials = materials
        self.whole_scene = whole_scene
        self.scene = scene
        self.force = force

    def __repr__(self):
        return "<Change {0} objects={1} materials={2}{3}{4}>".format(
            ",".join(sorted(self.facts)), len(self.objects), len(self.materials),
            " whole-scene" if self.whole_scene else "", " forced" if self.force else "")


class Stage:
    """一段派生态。``facts`` 是它读的事实集合,与本批脏事实有交集就跑。"""

    __slots__ = ("name", "facts", "run")

    def __init__(self, name, facts, run):
        self.name = name
        self.facts = frozenset(facts)
        self.run = run

    def __repr__(self):
        return "<Stage {0} reads={1}>".format(self.name, ",".join(sorted(self.facts)))


def _run_plugin_data(change):
    """load pass。开了文件 / 着色栈刚载入:先清光内存里的插件数据(本会话的,或旧文件存下的上一版),再让各栈按
    材质记录把本栈的材质全部现编;只是有东西从别的文件 append 进来:只编本会话还没编过的那些。"""
    _dropped, compiled = material_builder.rebuild_plugin_data(purge=LOADED in change.facts)
    return compiled


def _run_capabilities(change):
    """材质的环境查询兑现面重接。唯一的触发者是**世界被换或被改**:环境采样是建组时快照。

    灯**一概不进这条路**,也不进任何一条:材质树读的是宿主自己的原生灯节点(主光身份是原生
    is_sun),加灯/删灯/挪灯/转灯/换色由宿主的光循环当场吃掉,插件一个字节都不写。
    这条纪律的理由是量出来的:重接是 O(材质 × 树),单张 NPR 角色材质 ~0.8s、24 张 20 秒。
    摆灯是美术每秒都在做的事,绝不能和它挂钩。

    材质上记着它的答案按哪一份世界内容建的,没变的材质不重接 —— 导入先定世界再建材质,
    紧跟着的这一次重接原本是把刚建好的兑现面整场重做一遍。「重建派生态」强制,不看记号。"""
    scope = None if change.whole_scene else change.materials
    return material_builder.rewire_capabilities(scope, force=change.force)


def _run_shadow_sets(change):
    """平行光的投影排除集:声明自己的投影不进平行光级联的物体收在一个全排除的集合里(见 shadow_casting),它是每一盏
    平行光的遮挡集合 —— 着色栈把每一盏平行光都当主光照。东西进场时给还没有遮挡集合的平行光挂上,排除集与平行光
    谁先进场都一样;用户自己指了遮挡集合的灯不动。"""
    return shadow_casting.bind_directional_lights(change.scene)


def _run_vertex(change):
    """顶点腿的**拓扑**:壳层位移与反壳描边那棵几何节点树,以及挂着它的那个修改器。

    唯一的生成途径 = 导入从游戏读材质、造出对象的那一刻:范围永远是这批 announce 进来的对象,
    从不扫整场景。开文件、append、「重建派生态」都不建也不重填 —— 树是插件数据不进 .blend,
    重开文件后修改器是空的,要壳与描边就重新导入。相机与骨名各自只重灌已有树上那几格 uniform。"""
    return material_builder.apply_vertex_stages(objects=change.objects)


def _run_camera_basis(change):
    """顶点腿那几格相机 uniform。描边宽度是按投影矩阵与真实 backbuffer 像素解的,相机
    一动就过期 —— 过期的是**值**不是拓扑,所以这里只把已有顶点树上的那几格重灌一遍,
    不建树、不建修改器、不重判任何一张材质。合成树读的视点物体也在这里跟上活动相机(没有界面时
    它就站在活动相机上)。"""
    from . import viewpoint
    viewpoint.sync(change.scene)
    viewpoint.sync_windows(change.scene)
    viewpoint.sync_footprint(change.scene)
    scope = None if change.whole_scene else change.objects
    return material_builder.push_camera_stages(objects=scope)


def _run_rig_basis(change):
    """脸部骨骼基座。基座是按**当下骨名**接进材质的,而名字是改得动的东西 —— 绑定的身份
    存在骨的印记上,这一条负责把那份身份重新翻成当下的名字。不重接就是 Exists=False、
    基座属性一个点都不写、SDF 悄悄回到绑定姿势。它只碰材质节点与对象属性,不碰修改器。"""
    scope = None if change.whole_scene else change.objects
    return material_builder.apply_rig_stages(objects=scope)


def _run_post(change):
    return len(material_builder.apply_post_stages(change.scene))


# 表就是调度策略的全部。顺序 = 注册顺序:兑现节点先接好,顶点腿再按材质真值建树,
# 后处理最后落在合成器上(三者互不读对方产物,顺序只为报告好读)。材质参数面板不在这里:
# 它是按需回读的镜子,面板画到哪张才回读哪张(见 material_panel)。
STAGES = (
    # 插件数据不在文件里:先按记录把材质编出来,后面各阶段才有东西可接。
    Stage("plugin-data", (LOADED, APPENDED), _run_plugin_data),
    Stage("capabilities", (WORLD,), _run_capabilities),
    Stage("shadow-sets", (OBJECTS,), _run_shadow_sets),
    Stage("vertex", (OBJECTS,), _run_vertex),
    Stage("camera-basis", (CAMERA, LOADED, APPENDED), _run_camera_basis),
    Stage("rig-basis", (OBJECTS, MATERIALS, RIG, LOADED, APPENDED), _run_rig_basis),
    # 后处理读的其实是「这个场景现在在放游戏内容了吗」:网格、材质、游戏自己的灯,
    # 任何一样进场都是证据(展示台可以只上太阳不上美术,那时也该有 tonemap)。
    # 装过就跳过,所以反复触发也只是一次 installed() 判断;相机事实含出图尺寸,
    # 泛光金字塔的级数与各级尺寸跟着它走,尺寸没变时重建图链也只是一次签名比较。
    Stage("post", (OBJECTS, MATERIALS, CAMERA, LOADED), _run_post),
)


# ---- 待落地队列 ----
_pending_facts = set()
_pending_objects = []
_pending_materials = []
_pending_whole_scene = False
_pending_force = False
_marked_at = 0.0
_flushing = False


def announce(*datablocks):
    """生产者报告自己刚造出来的数据块。**这是导入路径与派生态之间唯一的接口**:
    造东西的人不需要知道有哪些派生态,派生态也不需要知道有哪些导入路径。

    只认 Blender 数据块本身,所以它对游戏、对导入方式一无所知。"""
    facts = set()
    objects = []
    materials = []
    for block in datablocks:
        if isinstance(block, bpy.types.Object):
            facts.add(OBJECTS)
            objects.append(block)
        elif isinstance(block, bpy.types.Material):
            facts.add(MATERIALS)
            materials.append(block)
    if not facts:
        return
    _mark(facts, objects=objects, materials=materials)


def _mark(facts, objects=(), materials=(), whole_scene=False, force=False):
    global _pending_whole_scene, _pending_force, _marked_at
    _pending_facts.update(facts)
    _pending_objects.extend(objects)
    _pending_materials.extend(materials)
    _pending_whole_scene = _pending_whole_scene or whole_scene
    _pending_force = _pending_force or force
    _marked_at = time.monotonic()
    _arm_timer()


def _arm_timer():
    # 「有没有排着一次落地」直接问 Blender,不自己记标志位:同一个函数是可以被注册两遍的,
    # 标志位一旦与真实注册表失同步就是两个计时器在跑同一件事。
    if _flushing or bpy.app.timers.is_registered(_tick):
        return
    bpy.app.timers.register(_tick, first_interval=DEBOUNCE_SECONDS)


def _tick():
    """去抖:窗口内又有新变更就继续等,安静下来才落地。返回 None 即注销自己。"""
    waited = time.monotonic() - _marked_at
    if waited < DEBOUNCE_SECONDS:
        return DEBOUNCE_SECONDS - waited
    flush()
    return DEBOUNCE_SECONDS if _pending_facts else None


def _live(datablocks):
    """还活着的数据块 —— 队列是在计时器窗口之前攒的,期间用户完全可能把东西删了,
    碰一下已释放的 StructRNA 就是 ReferenceError。"""
    alive = []
    for block in datablocks:
        try:
            block.name
        except ReferenceError:
            continue
        alive.append(block)
    return alive


def _in_scene(objects, scene):
    """只保留当下真在这个场景里的对象:reset_scene 那类流程会先删后建,
    队列里可能留着已被解链的产物。按身份比对,不按名字(名字会被复用)。
    场景成员先收成一个集合:scene.objects 按名字查是逐个走一遍全场景,一次导入九千个对象就是平方级。"""
    members = set(scene.objects)
    return [obj for obj in objects if obj in members]


def flush():
    """把攒下的变更落地。范围内跑一次,不重复、不递归。"""
    global _pending_whole_scene, _pending_force, _flushing, LAST_ERROR
    if _flushing or not _pending_facts:
        return None
    scene = bpy.context.scene
    if scene is None:
        return None

    # 先清队列再跑:阶段自己会造节点组/对象,由此引发的新变更属于下一批,
    # 而不是被本批吞掉或引起自激。
    change = Change(_pending_facts,
                    _in_scene(_live(_pending_objects), scene),
                    _live(_pending_materials),
                    _pending_whole_scene,
                    scene,
                    _pending_force)
    _pending_facts.clear()
    del _pending_objects[:]
    del _pending_materials[:]
    _pending_whole_scene = False
    _pending_force = False

    _flushing = True
    failures = []
    try:
        for stage in STAGES:
            if not (stage.facts & change.facts):
                continue
            try:
                stage.run(change)
            except Exception as error:
                traceback.print_exc()
                # 静默吞掉等于整条派生态消失而画面毫无痕迹(实锤:按名字点模块的老写法
                # 一改名就 AttributeError 被吞,壳层与描边一起没了还以为是生成器劣化)。
                print("[ruri-derived] !! 阶段 '{0}' 失败:{1}".format(stage.name, error), flush=True)
                failures.append("{0}: {1}".format(stage.name, error))
    finally:
        _flushing = False
        # 阶段自己动了灯/相机/场景(后处理会改 view transform,顶点腿会抬 Cycles 反弹数),
        # 重新采样一次基准,否则监视器把我们自己的回声当成用户改动,下一拍再跑一遍。
        _resnapshot(scene)
        # 落地期间进来的变更算下一批 —— 那期间 _arm_timer 是关着的,不在这里补一次
        # 就永远没人来收(阶段本身不 announce,所以这是防御,不是常规路径)。
        if _pending_facts:
            _arm_timer()

    LAST_ERROR = "; ".join(failures)
    return change


def rebuild_all():
    """把所有派生态按整场景重建一次 —— 撤销之后、或用户改了监视器看不见的东西
    (世界的节点树内部接线之类)时的唯一强制路径。"""
    _mark(ALL_FACTS, whole_scene=True, force=True)
    return flush()


# ---- 监视器:没有生产者的那半边 ----
# 加载器造东西会 announce;用户拖相机、换世界、改输出分辨率、改骨名没有生产者,只能看依赖图。
# 灯不在这里:着色读的是宿主自己的光循环,挪灯/加灯没有任何派生态要重算。

_camera = None
_world = None
_rig = None
_counts = None


def _world_signature(scene):
    """世界的身份。环境采样是各材质建组时的快照,换世界与改世界是仅有的两件还需要重接兑现面的事。"""
    return scene.world.name_full if scene.world is not None else None


def _world_edited(scene, depsgraph):
    """当前世界自己的数据块(或它的节点树)这一拍被改过。环境辐照与环境镜面的答案都是按世界的
    值建的快照,改了颜色、强度、环境图或它的 Mapping 而不重接,画面就停在旧世界上。"""
    world = scene.world
    if world is None:
        return False
    for update in depsgraph.updates:
        if not isinstance(update.id, (bpy.types.World, bpy.types.ShaderNodeTree)):
            continue
        block = update.id.original
        if block == world or (world.node_tree is not None and block == world.node_tree):
            return True
    return False


def _camera_signature(scene):
    """描边宽度是按投影矩阵与真实 backbuffer 像素解的(见生成物 apply_vertex_stage),
    所以「相机变了」包含镜头与输出设置,不只是位姿。"""
    camera = scene.camera
    if camera is None:
        return None
    data = camera.data
    render = scene.render
    return (
        camera.name_full,
        tuple(round(c, 5) for row in camera.matrix_world for c in row),
        getattr(data, "type", ""),
        round(getattr(data, "angle_y", 0.0), 6),
        round(getattr(data, "ortho_scale", 0.0), 6),
        round(getattr(data, "shift_x", 0.0), 6),
        round(getattr(data, "shift_y", 0.0), 6),
        render.resolution_x, render.resolution_y, render.resolution_percentage,
        round(render.pixel_aspect_x, 5), round(render.pixel_aspect_y, 5),
    )


def _rig_signature(scene):
    """骨骼名册。摆姿势不在其内 —— 那是每帧都在变的东西,而这里问的是「名字还是不是那些」。"""
    signature = []
    for obj in scene.objects:
        if obj.type != "ARMATURE" or obj.data is None:
            continue
        signature.append((obj.name_full, tuple(bone.name for bone in obj.data.bones)))
    signature.sort()
    return tuple(signature)


def _rig_touched(depsgraph):
    """只认 **Armature 数据块**:摆姿势 / 播放动画标记的是 Object,一帧一次;改名、加删骨、
    退出编辑模式标记的才是数据本身。判据下在这里,签名才不必每帧扫几百根骨。"""
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Armature):
            return True
    return False


def _camera_touched(depsgraph):
    for update in depsgraph.updates:
        block = update.id
        if isinstance(block, (bpy.types.Camera, bpy.types.Scene)):
            return True
        if isinstance(block, bpy.types.Object) and getattr(block, "type", "") == "CAMERA":
            return True
    return False


def _datablock_counts():
    """材质与对象的个数:涨了而没人 announce = 从别的文件 append 进来了(导入会 announce,也会让它涨;两边都判到
    只是多问一次「有没有还没编的」)。"""
    return len(bpy.data.materials), len(bpy.data.objects)


def _resnapshot(scene):
    global _camera, _world, _rig, _counts
    _counts = _datablock_counts()
    _camera = _camera_signature(scene)
    _world = _world_signature(scene)
    _rig = _rig_signature(scene)


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    global _camera, _world, _rig, _counts
    if _flushing:
        return
    counts = _datablock_counts()
    if _counts is not None and (counts[0] > _counts[0] or counts[1] > _counts[1]):
        _mark((APPENDED,), whole_scene=True)
    _counts = counts
    if _rig_touched(depsgraph):
        rig = _rig_signature(scene)
        if rig != _rig:
            _rig = rig
            _mark((RIG,), whole_scene=True)
    world = _world_signature(scene)
    if world != _world or _world_edited(scene, depsgraph):
        _world = world
        _mark((WORLD,), whole_scene=True)
    if _camera_touched(depsgraph):
        camera = _camera_signature(scene)
        if camera != _camera:
            _camera = camera
            _mark((CAMERA,), whole_scene=True)


@bpy.app.handlers.persistent
def _on_load_post(_path):
    """开了一个文件:插件数据从不写进 .blend,此刻一样都不在 —— 整场景的派生态照内容现建。有界面时交给去抖
    计时器;后台没有事件循环、计时器永远不响,求值一次当场落地。"""
    global _pending_whole_scene
    _pending_facts.clear()
    del _pending_objects[:]
    del _pending_materials[:]
    _pending_whole_scene = False
    scene = bpy.context.scene
    if scene is not None:
        _resnapshot(scene)
    _mark((LOADED,), whole_scene=True)
    if bpy.app.background and scene is not None:
        bpy.context.evaluated_depsgraph_get()
        flush()


def register():
    handlers = bpy.app.handlers
    if _on_depsgraph_update not in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_load_post not in handlers.load_post:
        handlers.load_post.append(_on_load_post)
    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        _resnapshot(scene)
    # 着色栈刚载入(启动、启用插件、重载脚本):会话里已经开着的文件同样一样插件数据都没有(或是上一次载入的那一份)。
    # 后台跑的文件由 load_post 当场落地;脚本自己导入的东西已经是本次载入编的,不再清一遍重编。
    if not bpy.app.background:
        _mark((LOADED,), whole_scene=True)


def unregister():
    handlers = bpy.app.handlers
    if _on_depsgraph_update in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if _on_load_post in handlers.load_post:
        handlers.load_post.remove(_on_load_post)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
