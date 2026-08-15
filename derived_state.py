"""场景派生态的唯一调度器 —— 「什么时候重建 Ruri 生成的场景衍生物」只在这里回答一次。

派生态 = 不是从资产直接读出来、而是**由场景真值算出来**的东西:顶点腿(壳层位移 /
反壳描边 / 脸部骨骼基座)、材质的环境查询兑现节点、灯表像素、合成器后处理链。
它们的共同点是「输入变了就必须重算,不重算画面静默错」。

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
/ `register_light_table_refresh` / `register_post_stage`(那是生成器拥有的契约)。本模块
只拥有**时机**:谁在什么变更下跑、跑在哪个范围上。

## 时机是空闲态,不是操作符结束

批量导入会造几百个对象,逐个收尾是 O(n²);而相机拖动每帧都在变。所以 announce 只打脏
标记,真正落地由一个去抖计时器在事件循环空闲时做一次 —— 一次导入 = 一次收敛。
"""

from __future__ import annotations

import time
import traceback

import bpy

try:
    from . import material_builder
except ImportError:  # standalone (non-package) testing
    import material_builder


# ---- 事实 ----
OBJECTS = "objects"            # 有新对象进场
MATERIALS = "materials"        # 有新材质进场
CAMERA = "camera"              # 活动相机的身份/位姿/投影/输出分辨率
LIGHT_SET = "light_set"        # 灯的增删/类型/可见性/世界(兑现分支本身可能变)
LIGHT_VALUES = "light_values"  # 灯的位姿/颜色/强度/锥角(只有表像素要重写)

ALL_FACTS = frozenset((OBJECTS, MATERIALS, CAMERA, LIGHT_SET, LIGHT_VALUES))

# 去抖窗口:批量导入的几百次 announce、相机拖动的每帧变更,都收敛成末尾的一次落地。
DEBOUNCE_SECONDS = 0.1

# 最近一次落地里炸掉的阶段,面板照着喊。派生态失败在画面上与「着色器本来就长这样」
# 完全无法区分,所以它必须留下能被看见的痕迹,而不是只在控制台滚过去。
LAST_ERROR = ""


class Change:
    """一次落地要处理的变更:脏了哪些事实、这批新造了什么、范围是不是整场景。

    ``whole_scene`` 为真时,已经存在的产物也失效了(相机动了 → 每个描边壳的视图基都
    过期;灯集合变了 → 每个材质的兑现分支都可能变),所以阶段必须扫全场,而不是只看
    这批新对象。"""

    __slots__ = ("facts", "objects", "materials", "whole_scene", "scene")

    def __init__(self, facts, objects, materials, whole_scene, scene):
        self.facts = frozenset(facts)
        self.objects = objects
        self.materials = materials
        self.whole_scene = whole_scene
        self.scene = scene

    def __repr__(self):
        return "<Change {0} objects={1} materials={2}{3}>".format(
            ",".join(sorted(self.facts)), len(self.objects), len(self.materials),
            " whole-scene" if self.whole_scene else "")


class Stage:
    """一段派生态。``facts`` 是它读的事实集合,与本批脏事实有交集就跑。"""

    __slots__ = ("name", "facts", "run")

    def __init__(self, name, facts, run):
        self.name = name
        self.facts = frozenset(facts)
        self.run = run

    def __repr__(self):
        return "<Stage {0} reads={1}>".format(self.name, ",".join(sorted(self.facts)))


def _run_capabilities(change):
    """材质的环境查询兑现:新材质要接上,灯集合变了则全场重接。"""
    scope = None if change.whole_scene else change.materials
    return material_builder.rewire_capabilities(scope)


def _run_light_tables(_change):
    return material_builder.refresh_light_tables()


def _run_vertex(change):
    """顶点腿。相机基轴是逐对象烘进几何节点树的快照,所以相机一变就得全场重建;
    单纯多了几个对象时只处理这几个。"""
    scope = None if change.whole_scene else change.objects
    return material_builder.apply_vertex_stages(objects=scope)


def _run_post(change):
    return len(material_builder.apply_post_stages(change.scene))


def _run_material_panels(change):
    """材质参数面板是图的镜子,图刚建好就得照一次 —— 否则面板显示的是接口缺省值,
    用户一动某一格就把**没回读过的其它格**按缺省写进图里。"""
    from . import material_panel

    pool = list(bpy.data.materials) if change.whole_scene else change.materials
    return material_panel.sync_all(pool)


# 表就是调度策略的全部。顺序 = 注册顺序:兑现节点先接好,顶点腿再按材质真值建树,
# 后处理最后落在合成器上(三者互不读对方产物,顺序只为报告好读)。
STAGES = (
    Stage("capabilities", (MATERIALS, LIGHT_SET), _run_capabilities),
    Stage("light-tables", (LIGHT_VALUES,), _run_light_tables),
    Stage("vertex", (OBJECTS, MATERIALS, CAMERA), _run_vertex),
    # 后处理读的其实是「这个场景现在在放游戏内容了吗」:网格、材质、游戏自己的灯,
    # 任何一样进场都是证据(展示台可以只上太阳不上美术,那时也该有 tonemap)。
    # 装过就跳过,所以在灯上反复触发也只是一次 installed() 判断。
    Stage("post", (OBJECTS, MATERIALS, LIGHT_SET), _run_post),
    Stage("material-panels", (MATERIALS,), _run_material_panels),
)


# ---- 待落地队列 ----
_pending_facts = set()
_pending_objects = []
_pending_materials = []
_pending_whole_scene = False
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
            # 灯是自己进场的:兑现分支按「场上有哪些灯」建,多一盏就得重接。
            if block.type == "LIGHT":
                facts.add(LIGHT_SET)
        elif isinstance(block, bpy.types.Material):
            facts.add(MATERIALS)
            materials.append(block)
    if not facts:
        return
    _mark(facts, objects=objects, materials=materials)


def _mark(facts, objects=(), materials=(), whole_scene=False):
    global _pending_whole_scene, _marked_at
    _pending_facts.update(facts)
    _pending_objects.extend(objects)
    _pending_materials.extend(materials)
    _pending_whole_scene = _pending_whole_scene or whole_scene
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
    队列里可能留着已被解链的产物。按身份比对,不按名字(名字会被复用)。"""
    return [obj for obj in objects if scene.objects.get(obj.name) is obj]


def flush():
    """把攒下的变更落地。范围内跑一次,不重复、不递归。"""
    global _pending_whole_scene, _flushing, LAST_ERROR
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
                    scene)
    _pending_facts.clear()
    del _pending_objects[:]
    del _pending_materials[:]
    _pending_whole_scene = False

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
    _mark(ALL_FACTS, whole_scene=True)
    return flush()


# ---- 监视器:没有生产者的那半边 ----
# 加载器造东西会 announce;用户拖相机、挪灯、改输出分辨率没有生产者,只能看依赖图。
# 两条闸分开判,因为代价差一个数量级:相机签名是 O(1),灯签名要扫全场对象。

_light_set = None
_light_values = None
_camera = None


def _light_set_signature(scene):
    signature = []
    for obj in scene.objects:
        if obj.type != "LIGHT":
            continue
        try:
            visible = obj.visible_get()
        except Exception:
            visible = not obj.hide_viewport
        signature.append((obj.name, obj.data.type, visible))
    signature.sort()
    return (tuple(signature), scene.world.name_full if scene.world is not None else None)


def _light_values_signature(scene):
    signature = []
    for obj in scene.objects:
        if obj.type != "LIGHT":
            continue
        light = obj.data
        signature.append((
            obj.name,
            tuple(round(c, 5) for row in obj.matrix_world for c in row),
            tuple(round(c, 5) for c in light.color),
            round(light.energy, 5),
            round(getattr(light, "spot_size", 0.0), 5),
            round(getattr(light, "spot_blend", 0.0), 5),
        ))
    signature.sort()
    return tuple(signature)


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


def _lights_touched(depsgraph):
    for update in depsgraph.updates:
        block = update.id
        if isinstance(block, (bpy.types.Light, bpy.types.World, bpy.types.Collection)):
            return True
        if isinstance(block, bpy.types.Object) and getattr(block, "type", "") == "LIGHT":
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


def _resnapshot(scene):
    global _light_set, _light_values, _camera
    _light_set = _light_set_signature(scene)
    _light_values = _light_values_signature(scene)
    _camera = _camera_signature(scene)


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    global _light_set, _light_values, _camera
    if _flushing:
        return
    if _lights_touched(depsgraph):
        light_set = _light_set_signature(scene)
        light_values = _light_values_signature(scene)
        if light_set != _light_set:
            # 集合变了就只报集合:兑现节点整体重建,顺带把表也重写了,再单独刷一遍是白干。
            _light_set, _light_values = light_set, light_values
            _mark((LIGHT_SET,), whole_scene=True)
        elif light_values != _light_values:
            _light_values = light_values
            _mark((LIGHT_VALUES,), whole_scene=True)
    if _camera_touched(depsgraph):
        camera = _camera_signature(scene)
        if camera != _camera:
            _camera = camera
            _mark((CAMERA,), whole_scene=True)


@bpy.app.handlers.persistent
def _on_load_post(_path):
    # 打开文件时只采基准、不动手:存盘时它就是按这些灯这些相机接好的,每次开都重建是白干。
    global _pending_whole_scene
    _pending_facts.clear()
    del _pending_objects[:]
    del _pending_materials[:]
    _pending_whole_scene = False
    scene = bpy.context.scene
    if scene is not None:
        _resnapshot(scene)


def register():
    handlers = bpy.app.handlers
    if _on_depsgraph_update not in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_load_post not in handlers.load_post:
        handlers.load_post.append(_on_load_post)
    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        _resnapshot(scene)


def unregister():
    handlers = bpy.app.handlers
    if _on_depsgraph_update in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if _on_load_post in handlers.load_post:
        handlers.load_post.remove(_on_load_post)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
