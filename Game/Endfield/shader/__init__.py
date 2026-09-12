"""Endfield 材质栈 —— 本包**没有业务代码**,只是生成物的门面。

一个宿主一个子文件夹(``Blender/``、``Substance/``),名字就是
:attr:`Kernel.host.Host.name`,所以「哪一份是给我的」不需要任何映射表。里面的东西
按目标完全不同:Blender 拿到的是节点组 ``.blend`` 库 + 一个内联清单的运行时 ``.py``
(那份自己会说它有哪些栈);Painter 拿到的是 GLSL + 投影清单,纯资产、没有可注册的
东西,注册即空转。两边都是 ``Ruri.RenderPipelines.Generator`` 按各自配方写进来的产物,
**勿手改**。

按路径找资产的一方走 :mod:`Kernel.shaderstack`,不从这里 import。
"""

from __future__ import annotations

import importlib
import importlib.util

from ....Kernel import host as host_port

_loaded = []


def _host_stack():
    """The generated package for the host this process runs inside, or None
    when that host's stack ships assets only."""
    name = host_port.current().name
    if importlib.util.find_spec("." + name, __name__) is None:
        return None
    stack = importlib.import_module("." + name, __name__)
    return stack if hasattr(stack, "register") and hasattr(stack, "unregister") else None


def register():
    _loaded[:] = [stack for stack in (_host_stack(),) if stack is not None]
    for stack in _loaded:
        stack.register()


def unregister():
    for stack in reversed(_loaded):
        stack.unregister()
    _loaded[:] = []
