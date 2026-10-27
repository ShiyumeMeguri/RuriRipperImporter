"""世界太阳与灯物体里的太阳,谁来当角色的方向光。

EEVEE 把世界(HDRI)里超过 ``World.sun_threshold`` 的那部分抽成一盏原生太阳灯,它和灯物体一起进灯列表,
着色栈的光循环(原生 Light Info)里它与灯物体长得一样:节点给不出「这盏是世界太阳」的标志(它没有物体,资源号
恒 0,位置与自定义属性读到的是别的物体),所以「有太阳灯物体就不认世界太阳」在着色器里做不出来,只能管世界
太阳自己建不建。

策略只有一条:场景里有太阳灯物体,世界太阳让位(阈值暂切 0,EEVEE 不再建它);没有太阳灯物体(删光了、都改成
别的灯型),还原用户自己的阈值,世界太阳照常当方向光。隐藏的太阳灯物体也算有 —— 它在,世界太阳就让位,角色
这时没有方向光(与游戏里没有主光一致),删了才还原。

只管这一个开关,不碰灯、不碰材质、不重接任何东西:灯的位姿、颜色、强度怎么改都与这里无关,只有「场景里有没有
太阳灯物体」这件成员变化(加、删、改灯型)与世界被换被改才会再算一次(派生态的 SUNS、WORLD 事实)。只在场景里
真有插件着色的材质时才管;别的场景里世界太阳是用户自己的原生光照,不碰。

用户原来的阈值记在世界的自定义属性 ``SAVED_THRESHOLD`` 上;让位是会话里的策略不是文档的内容,所以存盘前还原、
存盘后再让位,不进 .blend。
"""

from __future__ import annotations

import bpy

SAVED_THRESHOLD = "ruri_sun_threshold"


def shades_with_plugin():
    """场景里有没有插件着色的材质实例(模板材质是插件自己的运行时数据,装着色栈就有,不算)。"""
    return any(material.get("ruri_uber_stack") and not material.get("ruri_uber_template")
               for material in bpy.data.materials)


def has_sun_object(scene):
    return any(obj.type == "LIGHT" and obj.data is not None and obj.data.type == "SUN"
               for obj in scene.objects)


def _release(world):
    world.sun_threshold = world[SAVED_THRESHOLD]
    del world[SAVED_THRESHOLD]


def apply(scene):
    """让场景当前世界的世界太阳按「有没有太阳灯物体」让位或还原;返回改动了几个世界。"""
    changed = 0
    for world in bpy.data.worlds:
        if world != scene.world and world.library is None and SAVED_THRESHOLD in world:
            _release(world)
            changed += 1
    world = scene.world
    if world is None or world.library is not None:
        return changed
    if shades_with_plugin() and has_sun_object(scene):
        current = world.sun_threshold
        if current == 0.0:
            return changed
        world[SAVED_THRESHOLD] = current
        world.sun_threshold = 0.0
        return changed + 1
    if SAVED_THRESHOLD in world:
        _release(world)
        changed += 1
    return changed


def release_all():
    """把所有让位还原成用户自己的阈值。"""
    released = 0
    for world in bpy.data.worlds:
        if world.library is None and SAVED_THRESHOLD in world:
            _release(world)
            released += 1
    return released


@bpy.app.handlers.persistent
def _before_save(*_args):
    release_all()


@bpy.app.handlers.persistent
def _after_save(*_args):
    scene = bpy.context.scene
    if scene is not None:
        apply(scene)


_HANDLERS = (("save_pre", _before_save), ("save_post", _after_save), ("save_post_fail", _after_save))


def register():
    for chain_name, handler in _HANDLERS:
        chain = getattr(bpy.app.handlers, chain_name)
        if handler not in chain:
            chain.append(handler)


def unregister():
    for chain_name, handler in _HANDLERS:
        chain = getattr(bpy.app.handlers, chain_name)
        if handler in chain:
            chain.remove(handler)
    release_all()
