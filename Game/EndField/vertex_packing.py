"""本作自研的顶点打包布局 —— 一个游戏的事实,所以住在这里,不进 RuriRipperPyBridge。

内核只内置**真正标准**的格式(R10G10B10A2 的 snorm/unorm 读法),并开了注入点
``mesh_decoder.register_packed_channel_decoder``;私有布局由这里注册进去。

这不是标准八面体编码:通行的 oct 是每分量 8 或 16 位(oct16/oct32)独占一个通道。
本作把整个切线帧压进**一个** 32-bit word:

    bit31      切线手性 w(1 → +1,0 → -1)
    bit30      打包标志(为 0 表示这不是打包顶点,不认领)
    bits20-29  切平面内角 snorm10
    bits0-19   八面体法线对(每分量 10 位)

法线与切线共用这一个 word,所以两个通道都由本模块解;NORMAL 侧返回的候选还要过内核
的几何相关性判据(八面体解出来天然是单位长,长度门对它没有鉴别力)。
"""

from __future__ import annotations

import numpy as np

from ...RuriRipperPyBridge.unity import mesh_decoder

NORMAL = 1   # Unity VertexAttribute.Normal
TANGENT = 2  # Unity VertexAttribute.Tangent

_PACK_FLAG = np.uint32(1) << np.uint32(30)


def _sign_extend_10(bits):
    bits = bits.astype(np.int32)
    return np.where(bits >= 512, bits - 1024, bits)


def _normals(words):
    """bits0-19 的八面体对 → 单位法线。

    低两个 10-bit 字段符号扩展后除以 511 得 (x, y);z = 1 - |x| - |y|;z 为负时沿八面体
    对角折回 —— x' = (1-|y|)·sign(x),y' = (1-|x|)·sign(y),符号取自**原始**符号扩展整数、
    z 保持负值 —— 最后整体归一。
    """
    x_raw = _sign_extend_10(words & 0x3FF)
    y_raw = _sign_extend_10((words >> 10) & 0x3FF)

    x = x_raw.astype(np.float32) / 511.0
    y = y_raw.astype(np.float32) / 511.0
    z = 1.0 - np.abs(x) - np.abs(y)

    folded = z < 0.0
    if folded.any():
        sign_x = np.where(x_raw >= 0, 1.0, -1.0).astype(np.float32)
        sign_y = np.where(y_raw >= 0, 1.0, -1.0).astype(np.float32)
        new_x = (1.0 - np.abs(y)) * sign_x
        new_y = (1.0 - np.abs(x)) * sign_y
        x = np.where(folded, new_x, x)
        y = np.where(folded, new_y, y)

    out = np.stack([x, y, z], axis=1).astype(np.float32)
    return out / np.clip(np.linalg.norm(out, axis=1, keepdims=True), 1e-12, None)


def _tangents(words):
    """bits20-29 的切平面内角 + bit31 手性 → (n,4) 的 xyz + w。

    参考切线 = Gram-Schmidt(n.yzx - n.zxy) 对法线正交化;内角按对角编码
    dir2 = (1-2|a|, sign(a)·(1-|1-2|a||)) 归一后投到 [ref, cross(n, ref)] 基上。
    """
    normals = _normals(words)

    ang = _sign_extend_10((words >> 20) & 0x3FF).astype(np.float32) / 511.0

    ref = normals[:, [1, 2, 0]] - normals[:, [2, 0, 1]]
    ref = ref - (ref * normals).sum(axis=1, keepdims=True) * normals
    ref = ref / np.clip(np.linalg.norm(ref, axis=1, keepdims=True), 1e-12, None)
    bitangent = np.cross(normals, ref)
    bitangent = bitangent / np.clip(np.linalg.norm(bitangent, axis=1, keepdims=True), 1e-12, None)

    sign = np.where(ang < 0.0, np.float32(-1.0), np.float32(1.0))
    d0 = 1.0 - (ang * sign) * 2.0
    d1 = sign * (1.0 - np.abs(d0))
    norm = np.clip(np.sqrt(d0 * d0 + d1 * d1), 1e-12, None)
    tangent = (d0 / norm)[:, None] * ref + (d1 / norm)[:, None] * bitangent

    handedness = (((words >> 31) & 1).astype(np.float32) * 2.0) - 1.0
    return np.concatenate([tangent, handedness[:, None]], axis=1).astype(np.float32)


def decode(channel_index, words):
    """内核注入点的实现:认领得了返回数组,认领不了返回 None。"""
    if channel_index not in (NORMAL, TANGENT):
        return None
    words = words.astype(np.uint32)
    if float(np.mean((words & _PACK_FLAG) != 0)) <= 0.9:
        return None
    return _normals(words) if channel_index == NORMAL else _tangents(words)


def register():
    mesh_decoder.register_packed_channel_decoder(decode)


def unregister():
    mesh_decoder.unregister_packed_channel_decoder(decode)
