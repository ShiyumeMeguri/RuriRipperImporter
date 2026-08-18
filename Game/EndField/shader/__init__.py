"""Endfield 材质栈 —— 本包**没有业务代码**,只是生成物的门面。

一个平台一份产物,只有两件(同批同 stamp,``Ruri.App --codegen-only`` 生成、``--deploy-recipe`` 落位,禁止手改):

* ``<平台>.blend`` —— 全部生成栈(角色/场景/特效/接影/后处理)的模板节点组。逐节点建图的
  O(全树) 代价在 codegen 机上一次付清,导入期只剩 link。
* ``<平台>.py``    —— 数据驱动运行时 + 全部栈清单(内联)。它按清单装配材质/顶点腿/兑现面/参数表,
  并把每个栈自注册进宿主注册表。这一个脚本是不可省的下限:.blend 没法把自己注册进宿主的
  provider 表,那道桥只能是 python。

所以本文件只做一件事:把 register/unregister 转发给它。**一行业务逻辑都不写**(哪些 part 有位移、
modifier 叫什么、材质怎么读写,全是清单自己的知识;在这里再写一遍就是第二真源)。
产物换名字也不用改这里 —— 按「有没有 register」认,不按名字认。
"""

from __future__ import annotations

import importlib
import pkgutil

_generated = [
    module for module in (
        importlib.import_module("." + info.name, __name__)
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name)
    )
    if hasattr(module, "register") and hasattr(module, "unregister")
]


def register():
    for module in _generated:
        module.register()


def unregister():
    for module in reversed(_generated):
        module.unregister()
