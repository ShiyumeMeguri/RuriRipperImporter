"""Endfield 材质栈 —— 本包**没有业务代码**,只是生成物的门面。

产物 = 每栈一个 ``<栈>.blend``(模板组库)+ 全平台唯一的 ``<平台>.py``(运行时 + 内联清单)。
按「模块有没有 register」认,不按名字认 —— 产物换名不用改这里。
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
