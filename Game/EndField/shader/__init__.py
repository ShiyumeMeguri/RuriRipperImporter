"""EndField CharacterNPR 材质 —— 本包**没有业务代码**,只是生成物的门面。

``ruri_character_uber_endfield.py`` 是生成物(AzureNihil C# 着色栈 → Ruri.CodeGen.Blender,
用 ``Ruri.App --codegen-only`` + ``--deploy-shaders`` 重生成,**禁止手改**),它自包含:
建图、克隆换图、按 m_Shader 身份认领 part、raw 直灌材质属性、自注册进宿主 provider 表,
全在里面。part 元数据(名/值/透明/shader 身份/区分开关)由 C# 的 ``[StylePart]`` 派生,
宿主 API 的名字由配方 ``options.Host`` 给 —— 两边都是单一真源,这里再抄一遍就是漂移面。
"""

from __future__ import annotations

from . import ruri_character_uber_endfield as gen

register = gen.register
unregister = gen.unregister
