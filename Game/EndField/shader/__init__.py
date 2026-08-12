"""EndField 材质栈 —— 本包**没有业务代码**,只是生成物的门面。

``ruri_*_endfield.py`` 全是生成物(AzureNihil C# 着色栈 → Ruri.CodeGen.Blender,
用 ``Ruri.App --codegen-only`` 重生成后定点拷入,**禁止手改**),各自自包含:
建图、克隆换图、按 m_Shader 身份认领 part、raw 直灌材质属性、顶点腿(壳层位移 +
反壳描边)、以及把材质图与顶点腿**自注册**进宿主注册表 —— 全在里面。

所以本文件只做两件事:发现生成物、把 register/unregister 转发过去。**一行业务逻辑都不写**
(顶点腿怎么施加、哪些 part 有位移、modifier 叫什么,全是生成物自己的知识;
在这里再写一遍就是第二真源)。消费方要跑顶点腿 → 调宿主的 ``apply_vertex_stages()``,
不按名字点任何一个模块。

一栈一配方一生成物:新增配方 = 多一个 ``ruri_*_endfield.py``,落盘即生效,零登记。
"""

from __future__ import annotations

import importlib
import pkgutil

_generated = [
    importlib.import_module("." + info.name, __name__)
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name)
    if info.name.startswith("ruri_") and info.name.endswith("_endfield")
]


def register():
    for module in _generated:
        module.register()


def unregister():
    for module in reversed(_generated):
        module.unregister()
