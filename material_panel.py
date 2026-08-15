"""选中网格所持有的材质的参数面板 —— 全场唯一一个。

## 为什么在宿主而不在生成物里

一个 Blender 会话同时装着 N 个生成着色栈(角色 / 特效 / 场景 / 接影…)。每栈各自注册一个
`bpy.types.Panel` 的结果是:属性页里 N 个「XXX 参数」面板并排堆着,每个都在画同一张活动材质,
而用户其实只想调**当前选中网格的那张材质**。所以面板本体收敛到这里,画一次。

生成物那一侧留的是它真正独有的知识,经注册表交上来:

* `INTERFACE`  —— C# 声明([ShaderProperty]/[MaterialTexture]/[ShaderPropertyHeader])反射出的
  接口表(分组 / 量程 / 控件类型 / 组功能门),**不是**对节点树的遍历;
* `panel_claims(mat)` —— 这张材质是不是本栈建的(判据 = 建图时烙的 `ruri_uber_stack`);
* `panel_rows(mat)`   —— 这张材质**真正有**的行(变体折叠掉的死支在图上没有 socket);
* `panel_read(mat)`   —— 当前值(值住在级联组实例 socket 与 ruri_uber_* 快照里);
* `panel_write / panel_write_image / panel_write_st` —— 写路径要同时落级联实例、快照字典、
  顶点腿克隆输入,换图还要走导入期同一条建线链。宿主既不知道也不该知道这些。

## 状态住在哪

每个栈一个 PropertyGroup 类(按 INTERFACE 动态生成注解),挂在 `bpy.types.Material` 上 ——
所以面板值随 .blend 存盘,与材质同生命周期。PropertyGroup 只是**镜子**:改一格立刻经生成物
写进图里;图被重建后由 `sync()` 回读(导入后由 derived_state 统一叫一次,见那边的 stage)。
"""

from __future__ import annotations

import bpy

# PANEL_KEY -> {'stack': 生成物模块, 'property': Material 上的属性名, 'group': PropertyGroup 类}
_STACKS = {}

# 回读期间关掉写路径:setattr 会触发 update 回调,不关就是回读一次写一次。
_GUARD = [False]

# panel_rows 要扫顶点腿节点(遍历全部对象),每次重画都问一次在大场景里是灾难。
# 缓存按材质,换图 / 回读时作废 —— 那正是行集合唯一会变的两个时刻。
_ROWS = {}


def _value_attribute(name):
    return "p" + name


def _tiling_attribute(name):
    return "t" + name


def _offset_attribute(name):
    return "o" + name


def _rows_of(stack):
    for group in stack.INTERFACE:
        for row in group["rows"]:
            yield row


def _writer(stack, row):
    def update(self, _context):
        if not _GUARD[0]:
            stack.panel_write(self.id_data, row, getattr(self, _value_attribute(row["name"])))
    return update


def _image_writer(stack, row):
    def update(self, _context):
        if _GUARD[0]:
            return
        material = self.id_data
        stack.panel_write_image(material, row, getattr(self, _value_attribute(row["name"])))
        _ROWS.pop(material.name_full, None)
    return update


def _st_writer(stack, row):
    def update(self, _context):
        if not _GUARD[0]:
            stack.panel_write_st(self.id_data, row,
                                 getattr(self, _tiling_attribute(row["name"])),
                                 getattr(self, _offset_attribute(row["name"])))
    return update


def _annotations_of(stack):
    """INTERFACE → PropertyGroup 注解。控件类型/量程/缺省全部来自声明,面板不发明任何一项。"""
    annotations = {}
    for row in _rows_of(stack):
        attribute = _value_attribute(row["name"])
        kind = row["kind"]
        if kind == "TEXTURE":
            annotations[attribute] = bpy.props.PointerProperty(
                type=bpy.types.Image, name=row["label"], update=_image_writer(stack, row))
            annotations[_tiling_attribute(row["name"])] = bpy.props.FloatVectorProperty(
                name="平铺", size=2, default=(1.0, 1.0), update=_st_writer(stack, row))
            annotations[_offset_attribute(row["name"])] = bpy.props.FloatVectorProperty(
                name="偏移", size=2, default=(0.0, 0.0), update=_st_writer(stack, row))
            continue
        default = list(row.get("default") or [])
        if kind == "SWITCH":
            annotations[attribute] = bpy.props.BoolProperty(
                name=row["label"], default=bool(default and default[0] > 0.5),
                update=_writer(stack, row))
        elif kind == "INT":
            annotations[attribute] = bpy.props.IntProperty(
                name=row["label"], default=int(default[0]) if default else 0,
                update=_writer(stack, row))
        elif kind in ("VALUE", "SLIDER"):
            keywords = {"name": row["label"],
                        "default": float(default[0]) if default else 0.0,
                        "update": _writer(stack, row)}
            if kind == "SLIDER":
                keywords["min"] = row["min"]
                keywords["max"] = row["max"]
            annotations[attribute] = bpy.props.FloatProperty(**keywords)
        else:
            size = row["size"]
            fill = (default + [0.0] * 4)[:size]
            keywords = {"name": row["label"], "size": size, "update": _writer(stack, row)}
            if kind == "COLOR":
                keywords.update(subtype="COLOR", min=0.0, max=1.0)
                fill = [min(max(value, 0.0), 1.0) for value in fill]
            elif kind == "HDRCOLOR":
                keywords.update(subtype="COLOR", min=0.0, soft_max=1.0)
            keywords["default"] = tuple(fill)
            annotations[attribute] = bpy.props.FloatVectorProperty(**keywords)
    return annotations


def register_stack(stack):
    """一个生成着色栈进场:给它建一个 PropertyGroup 挂到 Material 上。幂等。"""
    unregister_stack(stack)
    property_name = "ruri_panel_" + stack.PANEL_KEY
    group = type("RURI_PG_" + stack.PANEL_KEY, (bpy.types.PropertyGroup,),
                 {"__annotations__": _annotations_of(stack)})
    bpy.utils.register_class(group)
    setattr(bpy.types.Material, property_name, bpy.props.PointerProperty(type=group))
    _STACKS[stack.PANEL_KEY] = {"stack": stack, "property": property_name, "group": group}


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
    _GUARD[0] = True
    try:
        for name, value in state["values"].items():
            setattr(properties, _value_attribute(name), value)
        for name, image in state["images"].items():
            setattr(properties, _value_attribute(name), image)
        for name, transform in state["st"].items():
            setattr(properties, _tiling_attribute(name), (transform[0], transform[1]))
            setattr(properties, _offset_attribute(name), (transform[2], transform[3]))
    finally:
        _GUARD[0] = False
    _ROWS.pop(material.name_full, None)
    properties["_synced"] = 1
    return True


def sync_all(materials):
    return sum(1 for material in materials if sync(material))


def rows_of(material, entry):
    cached = _ROWS.get(material.name_full)
    if cached is None:
        cached = entry["stack"].panel_rows(material)
        _ROWS[material.name_full] = cached
    return cached


class RURI_OT_material_panel_toggle(bpy.types.Operator):
    bl_idname = "ruri.material_panel_toggle"
    bl_label = "展开/折叠分组"
    bl_options = {"INTERNAL"}

    group: bpy.props.IntProperty()

    def execute(self, context):
        entry = stack_of(context.object.active_material if context.object else None)
        if entry is None:
            return {"CANCELLED"}
        properties = getattr(context.object.active_material, entry["property"])
        key = "_open_%d" % self.group
        properties[key] = 0 if properties.get(key) else 1
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


def _draw_row(column, properties, row):
    attribute = _value_attribute(row["name"])
    if row["kind"] != "TEXTURE":
        column.prop(properties, attribute)
        return
    split = column.split(factor=0.4)
    split.label(text=row["label"])
    split.template_ID(properties, attribute, open="image.open")
    if row.get("has_st"):
        sub = column.column(align=True)
        sub.prop(properties, _tiling_attribute(row["name"]), text="平铺")
        sub.prop(properties, _offset_attribute(row["name"]), text="偏移")


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
    if not properties.get("_synced"):
        # 回读只能由 operator / derived_state 做:draw 期间禁止写数据。
        box.label(text="这张材质还没回读过参数,点上面的刷新。", icon="INFO")

    body = box.column()
    body.use_property_split = True
    body.use_property_decorate = False
    for index, group in enumerate(rows_of(material, entry)):
        gate = group["gate"]
        panel_box = body.box()
        header = panel_box.row(align=True)
        opened = bool(properties.get("_open_%d" % index))
        arrow = header.operator(RURI_OT_material_panel_toggle.bl_idname, text="", emboss=False,
                                icon="TRIA_DOWN" if opened else "TRIA_RIGHT")
        arrow.group = index
        header.label(text=group["name"])
        gate_open = True
        gate_attribute = _value_attribute(gate) if gate else None
        if gate_attribute is not None and hasattr(properties, gate_attribute):
            header.prop(properties, gate_attribute, text="")
            gate_open = bool(getattr(properties, gate_attribute))
        if not opened:
            continue
        column = panel_box.column()
        column.active = gate_open
        for row in group["rows"]:
            if gate is not None and row["name"] == gate:
                continue
            _draw_row(column, properties, row)


_CLASSES = (RURI_OT_material_panel_toggle, RURI_OT_material_panel_sync)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for key in list(_STACKS):
        unregister_stack(_STACKS[key]["stack"])
    _ROWS.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
