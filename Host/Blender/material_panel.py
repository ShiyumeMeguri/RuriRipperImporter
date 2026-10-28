"""选中网格所持有的材质的参数面板 —— 全场唯一一个。

## 为什么在宿主而不在生成物里

一个 Blender 会话同时装着 N 个生成着色栈(角色 / 特效 / 场景 / 接影…)。每栈各自注册一个
`bpy.types.Panel` 的结果是:属性页里 N 个「XXX 参数」面板并排堆着,每个都在画同一张活动材质,
而用户其实只想调**当前选中网格的那张材质**。所以面板本体收敛到这里,画一次。

## 界面是生成的

每个栈的生成物带一个 `<栈键>_ui` 模块:属性组的全部注解、每个 part 的绘制代码(分区、条件显示、
下拉、未实现功能折叠组)都是生成器从材质模式中间层发射的代码。这里只剩三件事:

* 控件库 —— 生成代码调用的 `group / prop / texture / choice / label / separator / writer ...`,
  与具体材质、具体游戏无关;
* 注册与镜子 —— 每个栈一个 PropertyGroup 挂在 `bpy.types.Material` 上,值随 .blend 存盘;
  PropertyGroup 只是**镜子**:改一格立刻经生成物写进图里,图由 `sync()` 回读;
* 基座骨绑定行 —— 不是着色参数,是一条绑定,生成物答「这张材质记的是哪根骨」。

生成物那一侧留的数据面经栈对象交上来:`PROPERTIES`(属性表)、`UI`(上面那个模块)、
`panel_claims / panel_read / panel_write / panel_write_image / panel_write_st / panel_rig`。
宿主既不知道也不该知道写路径要同时落级联实例、快照字典、顶点腿克隆输入。

## 回读

面板第一次画到一张本会话还没回读过的材质时,下一拍回读它这一张。开文件与导入不再把全场材质都回读一遍 ——
九千对象的关卡上那一遍要一分钟,而面板一次只画一张。

## 槽位(拓展点)

材质布局数据里可以写 `{"slot": "名字"}`,生成物在那个位置调 `kit.slot`,宿主把注册在这个名字下的逻辑画进去。
注册表就是 Kernel 的 `material panel slots` 拓展点:任何模块(别的插件也一样)往里放 `draw(layout, context, slot)`,
不改生成器也不改这里。带标题的条目由宿主包成分组,默认折叠,折叠状态与分组一样住在材质的面板镜子上。
"""

from __future__ import annotations

import sys
import traceback

import bpy

from ...Kernel import extensions

# PANEL_KEY -> {'stack': 生成物模块, 'property': Material 上的属性名, 'group': PropertyGroup 类, 'ui': 界面模块}
_STACKS = {}

# 回读期间关掉写路径:setattr 会触发 update 回调,不关就是回读一次写一次。
_GUARD = [False]

# 「这张材质挂在哪副骨架上」要扫对象,按材质缓存、在回读时作废。
# 值是骨架的**名字**("" = 扫过、没有骨架),不是对象引用:见 _rig_armature。
_RIGS = {}

# 本会话回读过的材质(session_uid),与排着下一拍回读的。材质照记录重编 = 一张新材质(新的 session_uid),
# 自然不在里面;换文件 / 撤销 / 重做整个换掉数据库,两份一起丢。
_SYNCED = set()
_PENDING = set()

# 基座骨这一行的镜子。它不是着色属性(图上没有它的 socket),所以名字由本模块拥有。
_RIG_ATTRIBUTE = "ruri_rig_basis_bone"

_TOGGLE_OPERATOR = "ruri.material_panel_toggle"


def _valid_icons():
    return {item.identifier for item in
            bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items}


_ICONS = _valid_icons()


def _accent_icon(accent):
    name = "COLORSET_%02d_VEC" % accent
    return name if accent > 0 and name in _ICONS else "NONE"


# ======================= 控件库(生成的界面代码调用) =======================

def writer(stack, name):
    row = stack.PROPERTIES[name]
    attribute = stack.UI.value_attribute(name)

    def update(self, _context):
        if not _GUARD[0]:
            stack.panel_write(self.id_data, row, getattr(self, attribute))
    return update


def image_writer(stack, name):
    row = stack.PROPERTIES[name]
    attribute = stack.UI.value_attribute(name)

    def update(self, _context):
        if not _GUARD[0]:
            stack.panel_write_image(self.id_data, row, getattr(self, attribute))
    return update


def st_writer(stack, name):
    row = stack.PROPERTIES[name]
    tiling = stack.UI.tiling_attribute(name)
    offset = stack.UI.offset_attribute(name)

    def update(self, _context):
        if not _GUARD[0]:
            stack.panel_write_st(self.id_data, row, getattr(self, tiling), getattr(self, offset))
    return update


def choice(storage, label, tip, options):
    """下拉:值住在 storage 那个浮点属性里(写路径、回读、条件显示都读它),下拉只是它的另一种看法。"""
    values = [value for value, _text in options]
    items = [(str(index), text, "", index) for index, (_value, text) in enumerate(options)]

    def get(self):
        current = float(getattr(self, storage))
        for index, value in enumerate(values):
            if abs(value - current) < 1e-6:
                return index
        return 0

    def set_index(self, index):
        setattr(self, storage, values[index])

    return bpy.props.EnumProperty(name=label, description=tip, items=items, get=get, set=set_index)


def group(layout, properties, key, title, accent, gate, section):
    """一个分区。返回装内容的列;折叠着返回 None(调用方据此跳过整段)。gate = 分区总开关属性,关着整段置灰。"""
    box = layout.box()
    header = box.row(align=True)
    opened = bool(properties.get("_open_" + key))
    arrow = header.operator(_TOGGLE_OPERATOR, text="", emboss=False, icon="TRIA_DOWN" if opened else "TRIA_RIGHT")
    arrow.group_key = key
    header.label(text=title, icon=_accent_icon(accent))
    gate_open = True
    if gate is not None:
        header.prop(properties, gate, text="")
        gate_open = bool(getattr(properties, gate))
    if not opened:
        return None
    column = box.column()
    column.active = gate_open
    return column


def prop(column, properties, attribute, label):
    column.prop(properties, attribute, text=label)


def texture(column, properties, attribute, label, tiling, offset):
    split = column.split(factor=0.4)
    split.label(text=label)
    split.template_ID(properties, attribute, open="image.open")
    if tiling is not None:
        sub = column.column(align=True)
        sub.prop(properties, tiling, text="平铺")
        sub.prop(properties, offset, text="偏移")


def label(column, text):
    column.label(text=text)


def separator(column):
    column.separator()


def notice(layout, text):
    layout.label(text=text, icon="ERROR")


# ======================= 槽位(拓展点) =======================

SLOTS = extensions.point(
    "material panel slots", "Layout slot name -> logic drawn at that place of a generated material panel.")

# 炸过的条目只在控制台报一次回溯,否则每次重绘刷一屏;画面上的红字每次都在。
_REPORTED = set()


class SlotContext:
    """被插入的逻辑画在哪:栈、槽位名、材质的部件、材质本身、面板镜子(参数的当前值)。"""

    __slots__ = ("stack", "slot", "part", "material", "properties")

    def __init__(self, stack, slot, material, properties):
        self.stack = stack
        self.slot = slot
        self.part = material.get("ruri_uber_part", "")
        self.material = material
        self.properties = properties


class SlotEntry:
    __slots__ = ("slot", "key", "title", "draw", "poll")

    def __init__(self, slot, key, title, draw, poll):
        self.slot = slot
        self.key = key
        self.title = title
        self.draw = draw
        self.poll = poll


def register_slot(slot_id, key, draw, title="", poll=None):
    """把 ``draw(layout, context, slot)`` 插进布局里名为 slot_id 的槽位。key 在同一槽位内标识这条注册
    (同一模块重复注册 = 开发重载,覆盖旧的)。title 非空时宿主把它包成默认折叠的分组;poll(slot) 返回假就不画。"""
    return SLOTS.add(SlotEntry(slot_id, key, title, draw, poll), key=slot_id + "/" + key, module=draw.__module__)


def drop_slot(slot_id, key):
    SLOTS.discard(slot_id + "/" + key)


def slot(stack_key, column, material, properties, slot_id):
    """生成的界面代码在布局槽位处调它:画注册在这个名字下的每一条逻辑,没有注册就什么也不画。"""
    place = SlotContext(stack_key, slot_id, material, properties)
    for entry in SLOTS:
        if entry.slot != slot_id:
            continue
        if entry.poll is not None and not entry.poll(place):
            continue
        _draw_slot_entry(column, properties, entry, place)


def _draw_slot_entry(column, properties, entry, place):
    target = column
    if entry.title:
        target = group(column, properties, "slot." + entry.slot + "." + entry.key, entry.title, 0, None, False)
        if target is None:
            return
    try:
        entry.draw(target, bpy.context, place)
    except Exception as error:
        # 外来逻辑炸了不许带走整个材质面板,但也不许静默:画面上喊出来。
        alert = target.row()
        alert.alert = True
        alert.label(text="{0}/{1}: {2}".format(entry.slot, entry.key, error), icon="ERROR")
        identity = (entry.slot, entry.key, type(error).__name__, str(error))
        if identity not in _REPORTED:
            _REPORTED.add(identity)
            traceback.print_exc()


# ======================= 基座骨绑定 =======================

def _rig_armature(material):
    """这张材质挂在哪副骨架上。材质自己不知道 —— 得问用它的网格,而「选中的 mesh 修改器
    指向谁」就是绑定本身(rig_identity.armature_of 是全插件唯一那条规则)。

    记住的是骨架的**名字**,活对象每次现解。Python 手上的数据块引用会被删除对象、撤销、
    换文件整个作废,再碰是 `ReferenceError: StructRNA has been removed` 而不是 None ——
    而这里唯一的读者是 draw,一炸就是整个面板消失。解不出来 = 记的那副已经不在了,
    当作没记过重扫一遍,所以这份缓存永远交不出一个死引用(Game/Endfield/Blender/stage._rig_for 同法)。"""
    key = material.name_full
    remembered = _RIGS.get(key)
    if remembered is not None:
        if not remembered:
            return None
        armature = bpy.data.objects.get(remembered)
        if armature is not None and armature.type == "ARMATURE":
            return armature
    from . import rig_identity

    found = None
    for obj in bpy.data.objects:
        if not any(slot.material is material for slot in obj.material_slots):
            continue
        found = rig_identity.armature_of(obj)
        if found is not None:
            break
    _RIGS[key] = found.name if found is not None else ""
    return found


def _rig_writer(stack):
    """面板选了一根活骨。生成物记的是这根骨的**身份**而不是它此刻的名字,所以拒收没有
    印记的骨;成功与否都把话留在镜子上,由 draw 说出来 —— 静默的绑定失败在画面上
    与「着色器本来就长这样」完全无法区分。"""
    def update(self, _context):
        if _GUARD[0]:
            return
        material = self.id_data
        picked = getattr(self, _RIG_ATTRIBUTE)
        armature = _rig_armature(material)
        accepted, message = stack.panel_write_rig(material, armature, picked)
        self["_rig_note"] = message
        if not accepted:
            _GUARD[0] = True
            try:
                state = stack.panel_rig(material, armature) or {}
                setattr(self, _RIG_ATTRIBUTE, state.get("bone", ""))
            finally:
                _GUARD[0] = False
    return update


def _draw_rig(box, stack, material, properties):
    """基座骨这一行。只在真用这座桥的 part 上出现(生成物答 None 就没有这一行)。

    这不是着色参数而是一条**绑定**:脸/发/眼的 SDF 按这根骨的当下朝向判明暗,接不上就
    静默停在绑定姿势 —— 角色一转身脸整片压黑,而同一张脸上的皮肤照亮。所以三种接不上的
    情形(没骨架 / 没记骨 / 记的骨这副骨架上没有)都在这里明写,不留给用户去猜。"""
    armature = _rig_armature(material)
    rig = stack.panel_rig(material, armature)
    if rig is None:
        return
    rig_box = box.box()
    if armature is None:
        rig_box.label(text="{0}:这张材质的网格没挂骨架,SDF 停在绑定姿势。".format(rig["label"]),
                      icon="ERROR")
        return
    row = rig_box.row(align=True)
    row.prop_search(properties, _RIG_ATTRIBUTE, armature.data, "bones", text=rig["label"])
    if not rig["unity"]:
        rig_box.label(text="没记基座骨:SDF 停在绑定姿势,选一根(通常是头骨)。", icon="ERROR")
    elif not rig["bone"]:
        rig_box.label(text="骨架 {0} 里没有 Unity 骨 {1} —— SDF 停在绑定姿势。".format(
            armature.name, rig["unity"]), icon="ERROR")
    else:
        rig_box.label(text="身份 {0} · 骨架 {1}".format(rig["unity"], armature.name), icon="BONE_DATA")
    note = properties.get("_rig_note")
    if note:
        rig_box.label(text=str(note), icon="INFO")


# ======================= 注册与镜子 =======================

def register_stack(stack):
    """一个生成着色栈进场:给它建一个 PropertyGroup 挂到 Material 上。幂等。没有界面模块的栈(后处理)不认领材质。"""
    unregister_stack(stack)
    ui = getattr(stack, "UI", None)
    if ui is None:
        return
    annotations = {_RIG_ATTRIBUTE: bpy.props.StringProperty(
        name="基座骨骼",
        description="脸部/头发/眼睛按这根骨的当下朝向判 SDF 明暗。记的是骨的身份印记,改名不丢",
        update=_rig_writer(stack))}
    annotations.update(ui.annotations(sys.modules[__name__], stack))
    property_name = "ruri_panel_" + stack.PANEL_KEY
    group_type = type("RURI_PG_" + stack.PANEL_KEY, (bpy.types.PropertyGroup,), {"__annotations__": annotations})
    bpy.utils.register_class(group_type)
    setattr(bpy.types.Material, property_name, bpy.props.PointerProperty(type=group_type))
    _STACKS[stack.PANEL_KEY] = {"stack": stack, "property": property_name, "group": group_type, "ui": ui}


def unregister_stack(stack):
    entry = _STACKS.pop(getattr(stack, "PANEL_KEY", ""), None)
    if entry is None:
        return
    if hasattr(bpy.types.Material, entry["property"]):
        delattr(bpy.types.Material, entry["property"])
    bpy.utils.unregister_class(entry["group"])


def stack_of(material):
    """认领这张材质的栈条目,或 None。谁认领谁画,顺序无关(判据互斥:栈身份烙印)。"""
    if material is None:
        return None
    for entry in _STACKS.values():
        if entry["stack"].panel_claims(material):
            return entry
    return None


def sync(material):
    """图 → 面板。生成物答当前值,这里回填镜子。守卫关掉写路径,否则回读一次就把图重写一遍。"""
    entry = stack_of(material)
    if entry is None:
        return False
    state = entry["stack"].panel_read(material)
    properties = getattr(material, entry["property"], None)
    if properties is None:
        return False
    ui = entry["ui"]
    _RIGS.pop(material.name_full, None)
    rig = entry["stack"].panel_rig(material, _rig_armature(material))
    _GUARD[0] = True
    try:
        for name, value in state["values"].items():
            setattr(properties, ui.value_attribute(name), value)
        for name, image in state["images"].items():
            setattr(properties, ui.value_attribute(name), image)
        for name, transform in state["st"].items():
            setattr(properties, ui.tiling_attribute(name), (transform[0], transform[1]))
            setattr(properties, ui.offset_attribute(name), (transform[2], transform[3]))
        # 镜子里放的是**当下的活骨名**(picker 要在活骨里搜),身份留在材质上。
        setattr(properties, _RIG_ATTRIBUTE, rig["bone"] if rig is not None else "")
    finally:
        _GUARD[0] = False
    properties["_rig_note"] = ""
    _SYNCED.add(material.session_uid)
    return True


def _sync_soon(material):
    """draw 里不许写数据:排到下一拍回读这一张,回读完重画界面。同一张只排一次。"""
    key = material.session_uid
    if key in _PENDING:
        return
    _PENDING.add(key)

    def run():
        _PENDING.discard(key)
        target = next((one for one in bpy.data.materials if one.session_uid == key), None)
        if target is not None and key not in _SYNCED:
            sync(target)
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
        return None

    bpy.app.timers.register(run, first_interval=0.0)


class RURI_OT_material_panel_toggle(bpy.types.Operator):
    bl_idname = _TOGGLE_OPERATOR
    bl_label = "展开/折叠分组"
    bl_options = {"INTERNAL"}

    group_key: bpy.props.StringProperty()

    def execute(self, context):
        entry = stack_of(context.object.active_material if context.object else None)
        if entry is None:
            return {"CANCELLED"}
        properties = getattr(context.object.active_material, entry["property"])
        state = "_open_" + self.group_key
        properties[state] = 0 if properties.get(state) else 1
        return {"FINISHED"}


class RURI_OT_material_panel_sync(bpy.types.Operator):
    bl_idname = "ruri.material_panel_sync"
    bl_label = "从图同步参数"
    bl_description = "图被重建或被外部改动后,把这张材质的参数回读到当前值"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        material = context.object.active_material if context.object else None
        if not sync(material):
            self.report({"WARNING"}, "这张材质不是 Ruri 生成栈建的。")
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_materials(layout, context):
    """选中网格 → 它持有的材质槽 → 选中那张的参数。Blender 自己的槽列表控件,
    所以「哪张是当前材质」与属性页、与建模操作共用同一个真值(object.active_material_index)。"""
    obj = context.object
    box = layout.box()
    box.label(text="材质参数", icon="MATERIAL")
    slots = getattr(obj, "material_slots", None) if obj is not None else None
    if not slots:
        box.label(text="选中一个网格,这里出现它持有的材质。", icon="RESTRICT_SELECT_ON")
        return
    if len(slots) > 1:
        box.template_list("MATERIAL_UL_matslots", "", obj, "material_slots",
                          obj, "active_material_index", rows=min(4, len(slots)))
    material = obj.active_material
    if material is None:
        box.label(text="这个槽是空的。", icon="INFO")
        return

    entry = stack_of(material)
    if entry is None:
        if material.get("ruri_uber_part") is not None:
            # 旧产物没有栈烙印,认领判据落空。不硬猜:猜错就是拿另一个栈的接口写这张图。
            box.label(text="'{0}' 是旧产物(无栈身份),重新导入即可调参。".format(material.name),
                      icon="ERROR")
        else:
            box.label(text="'{0}' 不是 Ruri 生成栈建的材质。".format(material.name), icon="INFO")
        return

    stack = entry["stack"]
    properties = getattr(material, entry["property"], None)
    if properties is None:
        box.label(text="面板状态未注册 —— 重新启用插件。", icon="ERROR")
        return

    head = box.row(align=True)
    head.label(text="{0} · {1} · {2}".format(
        stack.PANEL_TITLE, material.get("ruri_uber_part", ""),
        material.get("ruri_uber_shader", "") or ""), icon="NODE_MATERIAL")
    head.operator(RURI_OT_material_panel_sync.bl_idname, text="", icon="FILE_REFRESH")
    if material.session_uid not in _SYNCED:
        _sync_soon(material)
        box.label(text="正在回读这张材质的参数……", icon="INFO")
    _draw_rig(box, stack, material, properties)

    body = box.column()
    body.use_property_split = True
    body.use_property_decorate = False
    entry["ui"].draw(sys.modules[__name__], body, material, properties)


_CLASSES = (RURI_OT_material_panel_toggle, RURI_OT_material_panel_sync)

# 缓存的键都是**材质名**,而换文件 / 撤销 / 重做会整个换掉数据库:同一个名字之后指着
# 另一张材质,缓存却照旧答上一副骨架 —— 不报错,只是画错。所以这三拍全丢。
_DATABASE_SWAPPED = ("load_post", "undo_post", "redo_post")


@bpy.app.handlers.persistent
def _drop_caches(*_arguments):
    _RIGS.clear()
    _SYNCED.clear()
    _PENDING.clear()


def forget():
    """Drop what the panel remembers about materials -- their rigs, which were read back -- for when
    materials were replaced by new compilations of their records."""
    _drop_caches()


def forget_material(material):
    """Read this one material back again on the next draw: its inputs changed some other way than this panel, and a
    mirror still holding the old values would write them back over the new ones the moment one widget is touched
    (a colour row writes all its channels)."""
    _SYNCED.discard(material.session_uid)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for name in _DATABASE_SWAPPED:
        handlers = getattr(bpy.app.handlers, name)
        if _drop_caches not in handlers:
            handlers.append(_drop_caches)


def unregister():
    for name in _DATABASE_SWAPPED:
        handlers = getattr(bpy.app.handlers, name)
        if _drop_caches in handlers:
            handlers.remove(_drop_caches)
    for key in list(_STACKS):
        unregister_stack(_STACKS[key]["stack"])
    _RIGS.clear()
    _SYNCED.clear()
    _PENDING.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
