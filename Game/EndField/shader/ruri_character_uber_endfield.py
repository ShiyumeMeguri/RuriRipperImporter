# =============================================================================
# Ruri Endfield Uber → Blender shader nodes(生成物,勿手改)
# 真源 = Ruri.RenderPipelines.Generator 材质模块(CharacterEndfield/Lighting/MixedPass)
#        经 Ruri.CodeGen.Blender 后端转译;执行本脚本即重建全部 node group + 根材质。
#
# 等价性契约:
#  · **raw 材质方言**:输入 socket 名 = 游戏自己的属性名(_MetallicGlossMap/_Smoothness/…),
#    贴图键同名;RMOS 通道重排与 smoothness→roughness 取反已内建进图 —— 消费方零转换直灌 .mat;
#  · 贴图按名绑定(gen.IMG_MAP 或全局同名 image;缺席时用 4x4 中性占位);
#  · 引擎态经根组输入桩,**名字一律真源自己的**:清单见本文件 GLOBALS 表(带 role);
#    方向光 = _DirectionalLightDirection/_DirectionalLightColor(反编译加的 <cbuffer>_ 前缀不是真名,已剥)
#    (方向 socket 收 Blender 世界方向,组内换轴回 Unity 语义);
#  · IBL:真源 _CharMaxCubemap 在 space0 = 引擎全局环境探针(非材质槽)→ 这里取**当前在照亮视口的环境**:
#    Material Preview/Rendered 关着 Scene World 时 = 视口 studiolight(那排 HDRI 小球,连 Rotation/Intensity
#    一起吃),勾上或无 3D 视图则 = scene.world;方向经 g.u2b 换回 Blender 世界系。
#    金属的漫反射项恒为 0,整身亮度几乎全来自这条 IBL —— 环境有多亮,金属就有多亮。
#    组内算的方向出不去(节点图不许成环)→ 只能取建组时的快照:换球/换世界后 ensure(rebuild=True);
#    mip 实参丢弃(图节点无 LOD 口),粗糙面反射未预滤;同名 image 存在时优先,作手动覆盖。
#  · **part 变体**:_CharaPartID 生成期折叠,每 part 一套入口 + 独立依赖闭包
#    (PARTS 表)。消费方按材质 part 选 PARTS[part],只建该闭包 —— 死支零节点;
#  · keyword 折叠: _ENDFIELD, _ADDITIONAL_LIGHTS, _RURI_FORWARD_PASS(前向单趟);
#  · uniform 折叠: _AlphaPremultiply=0(混合态等价桥:MixShader 恒 ×α,预乘量折 0 免得 α 乘两遍);
#  · 基线 Blender 5.2:循环 = 原生 repeat zone(体单实例,迭代数运行时驱动;5.2 shader 树
#    实渲证实真执行);分支 = 逐值 select(5.2 无运行时 bool switch,MenuSwitch 是静态菜单驱动,
#    逐值 select 与 GPU 发散执行同构,语义等价);
#  · 不可发射函数落零值桩,清单见同目录 _BLENDER_UNSUPPORTED.md。
# =============================================================================

import math
import os

import bpy

if bpy.app.version < (5, 2, 0):
    print('[Ruri] 本脚本按 Blender 5.2 基准生成(shader 树原生 repeat zone);当前 %d.%d 更旧,'
          '循环相关的节点可能建不出来。' % bpy.app.version[:2])

TREE_KIND = 'ShaderNodeTree'


def _tree(name):
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    return bpy.data.node_groups.new(name, TREE_KIND)


class G:
    """节点建图器:一个实例绑一棵 node tree。socket 或 Python 常量(float / 3 元组)皆可作输入。"""

    def __init__(self, tree, is_group=True):
        self.t = tree
        self.n = 0
        self.is_group = is_group
        self._sep_cache = {}
        # 运行时 CSE:相同 (运算, 上游 socket 身份, 常量值) 的节点只建一次。
        # 正确性:节点是纯函数(无隐藏态,Mix 的 clamp_factor 等模式量都进 key 所属方法固定),
        # 同输入 ⇒ 同输出;nodes.new + socket 写是建图耗时大头(实测 82%),命中即整节点免建。
        self._cse = {}
        self._geo = None
        self._texco = None
        self._iface = {}
        if is_group:
            self.gi = self._nd('NodeGroupInput')
            self.go = self._nd('NodeGroupOutput')

    def _ck(self, *parts):
        out = []
        for p in parts:
            if isinstance(p, bpy.types.NodeSocket):
                out.append(p.as_pointer())
            elif isinstance(p, (tuple, list)):
                out.append(tuple(round(float(x), 9) for x in p))
            elif isinstance(p, (int, float)):
                out.append(round(float(p), 9))
            else:
                out.append(p)
        return tuple(out)

    # ---- 基元 ----
    def _nd(self, typ):
        nd = self.t.nodes.new(typ)
        nd.location = ((self.n % 40) * 200.0, -(self.n // 40) * 240.0)
        self.n += 1
        return nd

    def _set(self, sock, v):
        if isinstance(v, bpy.types.NodeSocket):
            self.t.links.new(v, sock)
            return
        if not isinstance(v, (int, float)):
            v = v[0] if sock.type not in ('RGBA', 'VECTOR') else v
        if sock.type == 'RGBA':
            sock.default_value = (v, v, v, 1.0) if isinstance(v, (int, float)) else (v[0], v[1], v[2], 1.0)
        elif sock.type == 'VECTOR':
            sock.default_value = (v, v, v) if isinstance(v, (int, float)) else (v[0], v[1], v[2])
        elif sock.type == 'INT':
            sock.default_value = int(v)
        elif sock.type == 'BOOLEAN':
            sock.default_value = bool(v)
        else:
            sock.default_value = float(v)

    # ---- 标量/向量运算 ----
    def math(self, op, a, b=0.0, c=0.0):
        # 坑②:第三操作数默认 0.5——恒显式设满(MULTIPLY_ADD/COMPARE 等三操作数运算靠它)。
        k = ('m', op, self._ck(a, b, c))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeMath')
        nd.operation = op
        nd.use_clamp = False
        self._set(nd.inputs[0], a)
        self._set(nd.inputs[1], b)
        self._set(nd.inputs[2], c)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def vmath(self, op, a, b=(0.0, 0.0, 0.0), c=(0.0, 0.0, 0.0), s=1.0):
        k = ('v', op, self._ck(a, b, c, s))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeVectorMath')
        nd.operation = op
        self._set(nd.inputs[0], a)
        self._set(nd.inputs[1], b)
        self._set(nd.inputs[2], c)
        self._set(nd.inputs[3], s)
        # LENGTH/DOT_PRODUCT/DISTANCE 出 Value 口,其余出 Vector 口。
        out = nd.outputs[1] if op in ('LENGTH', 'DOT_PRODUCT', 'DISTANCE') else nd.outputs[0]
        self._cse[k] = out
        return out

    def mixf(self, fac, a, b):
        # 坑①:clamp_factor 默认 True 会钳 lerp 的 t——HLSL lerp 无钳语义,必须关。fac=0→a, 1→b。
        k = ('mf', self._ck(fac, a, b))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeMix')
        nd.data_type = 'FLOAT'
        nd.clamp_factor = False
        self._set(nd.inputs[0], fac)
        self._set(nd.inputs[2], a)
        self._set(nd.inputs[3], b)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def mixv(self, fac, a, b):
        k = ('mv', self._ck(fac, a, b))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeMix')
        nd.data_type = 'VECTOR'
        nd.factor_mode = 'UNIFORM'
        nd.clamp_factor = False
        self._set(nd.inputs[0], fac)
        self._set(nd.inputs[4], a)
        self._set(nd.inputs[5], b)
        self._cse[k] = nd.outputs[1]
        return nd.outputs[1]

    def clampn(self, x, mn=0.0, mx=1.0):
        k = ('cl', self._ck(x, mn, mx))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeClamp')
        self._set(nd.inputs[0], x)
        self._set(nd.inputs[1], mn)
        self._set(nd.inputs[2], mx)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def sep(self, v):
        key = v.as_pointer() if isinstance(v, bpy.types.NodeSocket) else None
        if key is not None and key in self._sep_cache:
            return self._sep_cache[key]
        nd = self._nd('ShaderNodeSeparateXYZ')
        self._set(nd.inputs[0], v)
        r = (nd.outputs[0], nd.outputs[1], nd.outputs[2])
        if key is not None:
            self._sep_cache[key] = r
        return r

    def comb(self, x, y, z):
        k = ('cb', self._ck(x, y, z))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeCombineXYZ')
        self._set(nd.inputs[0], x)
        self._set(nd.inputs[1], y)
        self._set(nd.inputs[2], z)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def bc(self, s):
        # 标量→向量广播(socket 标量无法直接喂需要三分量各异的向量口时用;常量走 _set 元组更省节点)。
        if isinstance(s, (int, float)):
            return (s, s, s)
        return self.comb(s, s, s)

    # ---- 纹理/几何/变换 ----
    def tex(self, name, uv, non_color=False, extension='REPEAT', interp='Linear'):
        # 模板里恒用**中性占位图**,且图名 == 逻辑槽名 —— 材质克隆模板后按这个名字认出槽位、
        # 换成自己的真贴图(Blender 无贴图 socket,这是唯一能把组做成可复用模板的办法)。
        # 同名同 uv 同参采样合并成一个节点:克隆重映射按图名找节点,少一份完全兼容。
        k = ('tx', name, self._ck(uv), non_color, extension, interp)
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeTexImage')
        img = bpy.data.images.get(name)
        if img is None:
            # 占位图(中性白):用户导入贴图后按同名替换即可全图生效。
            img = bpy.data.images.new(name, 4, 4)
            img.generated_color = (1.0, 1.0, 1.0, 1.0)
        nd.image = img
        nd.interpolation = interp
        # 查找表/屏幕派生 UV 类必须钳制:REPEAT 下 U→0/1 会与另一端插值,
        # 在阴影最深/最亮处炸出一条突变细线(见生成器 ClampedTextures 注)。
        nd.extension = extension
        # 数据类贴图(生成期按槽语义定)统一 Non-Color;克隆换图时按占位图的这个设定跟随。
        if non_color:
            img.colorspace_settings.name = 'Non-Color'
        self._set(nd.inputs[0], uv)
        r = (nd.outputs[0], nd.outputs[1])
        self._cse[k] = r
        return r

    def tex_lod0(self, name, uv, w, h, non_color=False):
        """SampleLevel(0) 双线性等价:4×Closest(EEVEE 走 texelFetch,永不掉 mip)+ 手工 bilerp。
        逐像素混沌 UV 的打包 LUT 必用 —— EEVEE 按屏幕导数选 mip,而打包 LUT 的 mip 是跨切片
        平均的语义垃圾(实锤:albedo 索引的 32³ 影色 LUT 满屏碎彩)。w/h = 贴图格式(texel)。"""
        ux, uy, _ = self.sep(uv)
        tx = self.math('SUBTRACT', self.math('MULTIPLY', ux, float(w)), 0.5)
        ty = self.math('SUBTRACT', self.math('MULTIPLY', uy, float(h)), 0.5)
        x0 = self.math('FLOOR', tx)
        y0 = self.math('FLOOR', ty)
        fx = self.math('SUBTRACT', tx, x0)
        fy = self.math('SUBTRACT', ty, y0)

        def texel_uv(xi, yi):
            u = self.math('DIVIDE', self.math('ADD', xi, 0.5), float(w))
            v = self.math('DIVIDE', self.math('ADD', yi, 0.5), float(h))
            return self.comb(u, v, 0.0)

        x1 = self.math('ADD', x0, 1.0)
        y1 = self.math('ADD', y0, 1.0)
        c00, a00 = self.tex(name, texel_uv(x0, y0), non_color, extension='EXTEND', interp='Closest')
        c10, _ = self.tex(name, texel_uv(x1, y0), non_color, extension='EXTEND', interp='Closest')
        c01, _ = self.tex(name, texel_uv(x0, y1), non_color, extension='EXTEND', interp='Closest')
        c11, _ = self.tex(name, texel_uv(x1, y1), non_color, extension='EXTEND', interp='Closest')
        top = self.mixv(fx, c00, c10)
        bot = self.mixv(fx, c01, c11)
        return self.mixv(fy, top, bot), a00

    def b2u(self, v, point=False):
        """Blender 世界系向量/点 → **Unity 语义**(内核所在的坐标系)。

        为什么必须:内核逐行是 Unity 约定(+Y 上、XZ 是水平面),满图都是
        `faceUp = cross(FaceForward, FaceRight)` / `float3(N.x, eps, N.z)` 压平 /
        `1-|camFwd.y|` / `rsqrt(x*x+z*z)` 这类把 .y 当上、.xz 当地面的写法;Blender 的上是 .z,
        直接喂进去 SDF 的脸朝向、各向异性切线、半球光照方向会全错(SDF 丢失/高光错位/过白)。
        1:1 移植纪律下内核不许改,于是在**边界**换轴。

        换轴 = 导入侧约定的逆(RuriRipperPyBridge/math3d/coordinate.BLENDER:`p' = (x, z, y)`,
        自反),故此处同为一次 Y/Z 互换。先转 OBJECT 空间再换轴:顶层导入对象带一次 180° yaw
        (coordinate._ROOT_YAW_180),对象空间里没有它,且角色被摆放/旋转后依然成立。"""
        local = self.vtrans(v, 'WORLD', 'OBJECT', 'POINT' if point else 'VECTOR')
        x, y, z = self.sep(local)
        return self.comb(x, z, y)

    def u2b(self, v):
        """**Unity 语义**方向 → Blender 世界方向(b2u 的逆:Y/Z 互换自反,故换轴在前、OBJECT→WORLD 在后)。

        环境采样必走这一步:反射向量算在 Unity 语义**对象**空间,而 Environment Texture 按
        **世界**方向取图 —— 直喂等于绕 X 转 90° 再叠一层对象变换(含导入根的 180° yaw)。"""
        x, y, z = self.sep(v)
        return self.vtrans(self.comb(x, z, y), 'OBJECT', 'WORLD', 'VECTOR')

    def _rig_basis_cols(self):
        """驱动骨骼相对绑定姿势的**增量旋转**三列(内核语义,已换轴),由顶点腿每帧写成点属性。
        属性缺席(网格没骨架 / 没跑顶点腿)读到零向量 ⇒ 长度 0 ⇒ 就地补回单位阵三列,
        于是 rig_basis 退化成恒等 = 与本桥不存在时逐位一致(禁静默黑脸)。"""
        hit = self._cse.get(('rigbcols',))
        if hit is not None:
            return hit
        raw = [self.attr(RIG_BASIS_ATTR + str(i)).outputs['Vector'] for i in range(3)]
        absent = self.math('MAXIMUM', self.math('SUBTRACT', 1.0, self.vmath('LENGTH', raw[0])), 0.0)
        unit = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        cols = [self.vmath('ADD', c, self.vmath('SCALE', e, s=absent)) for c, e in zip(raw, unit)]
        self._cse[('rigbcols',)] = cols
        return cols

    def rig_basis(self, v):
        """引擎每帧从角色骨骼注入的**世界基**(_FaceForward/_FaceRight 这类)的当下值。

        为什么必须:材质里存的是**绑定姿势**的那一份(引擎运行时才按骨骼覆写,导出的 .mat
        只剩静止值),而内核跑在对象空间 —— 骨骼一转,几何在对象空间里跟着转,这个常量却不转。
        脸转到背面时 SDF 仍按静止朝向判明暗,整张脸压黑而皮肤照亮(SDF 与几何 NdotL 的分歧)。
        真源语义 = 驱动骨骼当下的基 = **该骨骼相对绑定姿势的增量旋转** × 材质里的静止基,
        故此处逐列线性组合(增量恒为纯旋转,静止时是单位阵)。"""
        k = ('rigb', self._ck(v))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        cols = self._rig_basis_cols()
        x, y, z = self.sep(v)
        out = self.vmath('ADD',
                         self.vmath('ADD', self.vmath('SCALE', cols[0], s=x),
                                    self.vmath('SCALE', cols[1], s=y)),
                         self.vmath('SCALE', cols[2], s=z))
        self._cse[k] = out
        return out

    ENV_PREFILTER_TAPS = (
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 0.5), (-1.0, 0.0, 0.5), (0.0, 1.0, 0.5), (0.0, -1.0, 0.5),
        (0.7071, 0.7071, 0.35), (-0.7071, 0.7071, 0.35),
        (0.7071, -0.7071, 0.35), (-0.7071, -0.7071, 0.35),
    )

    def _env_tap(self, image, projection, spec, strength, direction):
        if spec is not None:
            vector_type, location, rotation, scale = spec
            md = self._nd('ShaderNodeMapping')
            md.vector_type = vector_type
            md.inputs['Location'].default_value = location
            md.inputs['Rotation'].default_value = rotation
            md.inputs['Scale'].default_value = scale
            self._set(md.inputs['Vector'], direction)
            direction = md.outputs[0]
        nd = self._nd('ShaderNodeTexEnvironment')
        nd.image = image
        nd.projection = projection
        nd.interpolation = 'Linear'
        self._set(nd.inputs[0], direction)
        color = nd.outputs[0]
        if strength != 1.0:
            color = self.vmath('SCALE', color, s=strength)
        return color

    def env(self, name, direction, default, mip=None):
        """环境反射按方向采样 = cubemap 的 Blender 等价物。源**只有**同名 image
        (游戏自己的探针贴图导进来后按 IBL_<槽名> 命名);没有就退引擎兜底常量。

        🔴 严禁拿视口 studiolight / scene.world 顶上:那是自造替身,不是游戏数据。
        真源的 _CharMaxCubemap 是角色反射探针,展示台没提供时引擎读到的就是未绑定 cube 的
        默认值 —— 拿天空 SH 或影棚 HDRI 冒充,会把布料照成漆皮(实测丝袜峰值 0.80 vs 兜底 0.17)。

        Environment Texture 没有 LOD 口,真源的 mip 只能**程序化**补:mip 是 cubemap 预滤波
        链上的位置,等价的角度锥半径由真源自己的 mip↔粗糙度式反解(mip = 1.2*log2(r) + 5 ⇒
        r = exp2((mip-5)/1.2)),再按 alpha = r*r 抖动反射向量做定点多抽样锥平均。
        r→0 时锥收敛回单抽样锐反射,与真源 mip0 同构;布料这种高粗糙度拿到的是宽锥均值而不是
        镜面 HDRI。**不是逐字的 GGX 预滤波**(那要离线烘 mip 链),是同一单调关系的程序化近似。"""
        k = ('env', name, self._ck(direction, mip))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        override = bpy.data.images.get(name)
        if override is None:
            r = (default, 1.0)
            self._cse[k] = r
            return r
        image, projection, spec, strength = override, 'EQUIRECTANGULAR', None, 1.0
        if mip is None:
            r = (self._env_tap(image, projection, spec, strength, direction), 1.0)
            self._cse[k] = r
            return r

        roughness = self.math('POWER', 2.0, self.math('DIVIDE', self.math('SUBTRACT', mip, 5.0), 1.2))
        spread = self.math('MINIMUM', self.math('MULTIPLY', roughness, roughness), 1.0)

        _dx, _dy, dz = self.sep(direction)
        polar = self.math('GREATER_THAN', self.math('ABSOLUTE', dz), 0.9)
        tangent = self.vmath('NORMALIZE', self.mixv(
            polar,
            self.vmath('CROSS_PRODUCT', direction, (0.0, 0.0, 1.0)),
            self.vmath('CROSS_PRODUCT', direction, (1.0, 0.0, 0.0))))
        bitangent = self.vmath('NORMALIZE', self.vmath('CROSS_PRODUCT', direction, tangent))

        total = None
        weight_sum = 0.0
        for offset_x, offset_y, weight in self.ENV_PREFILTER_TAPS:
            if offset_x or offset_y:
                offset = self.vmath('ADD',
                                    self.vmath('SCALE', tangent, s=offset_x),
                                    self.vmath('SCALE', bitangent, s=offset_y))
                tap = self.vmath('NORMALIZE',
                                 self.vmath('ADD', direction, self.vmath('SCALE', offset, s=spread)))
            else:
                tap = direction
            sample = self._env_tap(image, projection, spec, strength, tap)
            total = (self.vmath('SCALE', sample, s=weight) if total is None
                     else self.vmath('ADD', total, self.vmath('SCALE', sample, s=weight)))
            weight_sum += weight
        color = self.vmath('SCALE', total, s=1.0 / weight_sum)
        r = (color, 1.0)
        self._cse[k] = r
        return r

    def env_image(self, image, direction, mip=None, spread=None):
        """锥形预滤波的等距环境采样。锥宽两种给法,语义同一个:
           mip    —— 真源在 cubemap 预滤波链上的位置,按真源自己的 mip↔粗糙度式反解;
           spread —— 兑现面直接给的锥半径(能力割点走这条:询问给的是粗糙度,不是 mip)。
           两者皆无 = 锐反射单抽样。"""
        if mip is None and spread is None:
            return (self._env_tap(image, 'EQUIRECTANGULAR', None, 1.0, direction), 1.0)
        if spread is None:
            roughness = self.math('POWER', 2.0, self.math('DIVIDE', self.math('SUBTRACT', mip, 5.0), 1.2))
            spread = self.math('MINIMUM', self.math('MULTIPLY', roughness, roughness), 1.0)
        _dx, _dy, dz = self.sep(direction)
        polar = self.math('GREATER_THAN', self.math('ABSOLUTE', dz), 0.9)
        tangent = self.vmath('NORMALIZE', self.mixv(
            polar,
            self.vmath('CROSS_PRODUCT', direction, (0.0, 0.0, 1.0)),
            self.vmath('CROSS_PRODUCT', direction, (1.0, 0.0, 0.0))))
        bitangent = self.vmath('NORMALIZE', self.vmath('CROSS_PRODUCT', direction, tangent))
        total = None
        weight_sum = 0.0
        for offset_x, offset_y, weight in self.ENV_PREFILTER_TAPS:
            if offset_x or offset_y:
                offset = self.vmath('ADD',
                                    self.vmath('SCALE', tangent, s=offset_x),
                                    self.vmath('SCALE', bitangent, s=offset_y))
                tap = self.vmath('NORMALIZE',
                                 self.vmath('ADD', direction, self.vmath('SCALE', offset, s=spread)))
            else:
                tap = direction
            sample = self._env_tap(image, 'EQUIRECTANGULAR', None, 1.0, tap)
            total = (self.vmath('SCALE', sample, s=weight) if total is None
                     else self.vmath('ADD', total, self.vmath('SCALE', sample, s=weight)))
            weight_sum += weight
        return (self.vmath('SCALE', total, s=1.0 / weight_sum), 1.0)

    def vtrans(self, v, frm, to, kind='VECTOR'):
        k = ('vt', frm, to, kind, self._ck(v))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeVectorTransform')
        nd.vector_type = kind
        nd.convert_from = frm
        nd.convert_to = to
        self._set(nd.inputs[0], v)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def geo(self):
        if self._geo is None:
            self._geo = self._nd('ShaderNodeNewGeometry')
        return self._geo

    def texco(self):
        if self._texco is None:
            self._texco = self._nd('ShaderNodeTexCoord')
        return self._texco

    def attr(self, name):
        nd = self._nd('ShaderNodeAttribute')
        nd.attribute_name = name
        return nd

    # ---- 原生循环(5.2 shader 树 repeat zone;实测 EEVEE 真执行,见后端 probe2) ----
    def repeat_begin(self, iterations, states):
        # states: [(name, is_vec, init_value_or_socket), ...];返回 (rin, rout)。
        # 体内读 rin.outputs[name],体末 repeat_end 回填 rout.inputs[name];循环外读 rout.outputs[name]。
        rin = self._nd('GeometryNodeRepeatInput')
        rout = self._nd('GeometryNodeRepeatOutput')
        rin.pair_with_output(rout)
        rout.repeat_items.clear()
        for name, is_vec, _init in states:
            rout.repeat_items.new('VECTOR' if is_vec else 'FLOAT', name)
        self._set(rin.inputs['Iterations'], iterations)
        for name, _is_vec, init in states:
            self._set(rin.inputs[name], init)
        return rin, rout

    def repeat_end(self, rout, results):
        for name, v in results.items():
            self._set(rout.inputs[name], v)
        return rout.outputs

    # ---- 组接口 ----
    def inp(self, name, is_vec, default=None):
        if name in self._iface:
            return self.gi.outputs[name]
        item = self.t.interface.new_socket(
            name=name, in_out='INPUT',
            socket_type='NodeSocketVector' if is_vec else 'NodeSocketFloat')
        if default is not None:
            item.default_value = default
        self._iface[name] = item
        return self.gi.outputs[name]

    def out_(self, name, val, is_vec):
        self.t.interface.new_socket(
            name=name, in_out='OUTPUT',
            socket_type='NodeSocketVector' if is_vec else 'NodeSocketFloat')
        self._set(self.go.inputs[name], val)

    def group(self, name, ins):
        # 同组同输入 = 同输出:重复实例免建(纯函数组;共享子组的同参多调直接合一)。
        k = ('gp', name, self._ck(*ins))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeGroup')
        nd.node_tree = bpy.data.node_groups[name]
        for i, v in enumerate(ins):
            self._set(nd.inputs[i], v)
        self._cse[k] = nd.outputs
        return nd.outputs

    def group_named(self, name, pairs):
        # 子组实例按 **socket 名** 接线:组接口序 = 组体内 g.inp 首现序(uniform 与外提贴图
        # 交错),按 index 必错位;按名则序免疫。输出仍按 index 解包(输出统一在组尾发射,序稳定)。
        k = ('gpn', name, self._ck(*[x for p in pairs for x in p]))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeGroup')
        nd.node_tree = bpy.data.node_groups[name]
        for sname, v in pairs:
            self._set(nd.inputs[sname], v)
        self._cse[k] = nd.outputs
        return nd.outputs


# ===================== 环境询问的兑现面(Blender 数据平面) =====================
# 真源侧声明身份(Ruri.Shading 的 [ShaderCapability<T>]),编译器只负责把询问切成一对接口
# socket —— 拿什么原生物回答,唯一真源就是下面这张表。加一条能力 = 这里加一行,别处零改动。
#
# 🛑 表里查不到 = 本引擎**没有**原生等价物:什么都不接,socket 停在能力自声明的缺席值上并大声记账。
#    绝不允许"没匹配上就顺手落个中性数"——那正是上一版把整条间接光静默吞掉的病根。


def _world_background(scene):
    """当前世界的背景节点(没有世界 / 没有节点树 = 答不出)。"""
    world = getattr(scene, 'world', None)
    if world is None or not world.use_nodes or world.node_tree is None:
        return None
    for nd in world.node_tree.nodes:
        if nd.type == 'BACKGROUND':
            return nd
    return None


def _world_sample(g, direction, spread):
    """沿方向对**当前世界环境**取一次值 —— 这就是光追/EEVEE 下真正在照亮场景的那份数据。

    两种世界都答得出,且都是真值不是替身:
      · 背景色链到 Environment Texture → 复用同一张图、同一份 Mapping,按方向采样;
        spread 非 None 时套锥形预滤波(粗糙面拿宽锥均值,与真源 mip 链同一单调关系)。
      · 背景是平色 → 各向同性,任何方向的入射就是那个颜色 × Strength,直接给常量(精确,不是近似)。
    方向 socket 收的是**内核自己的空间语义**(Unity 世界系):换轴是宿主知识,在这里做。
    """
    bg = _world_background(bpy.context.scene)
    if bg is None:
        return None
    strength = float(bg.inputs['Strength'].default_value)
    color_in = bg.inputs['Color']
    src = color_in.links[0].from_node if color_in.is_linked else None
    if src is not None and src.type == 'TEX_ENVIRONMENT' and src.image is not None:
        d = g.u2b(direction)
        if src.inputs['Vector'].is_linked:
            up = src.inputs['Vector'].links[0].from_node
            if up.type == 'MAPPING':
                # 世界自己的 Mapping 逐值复刻:HDRI 转过多少度,采样也得转多少度。
                md = g._nd('ShaderNodeMapping')
                md.vector_type = up.vector_type
                for key in ('Location', 'Rotation', 'Scale'):
                    md.inputs[key].default_value = up.inputs[key].default_value[:]
                g._set(md.inputs['Vector'], d)
                d = md.outputs[0]
        color, _alpha = g.env_image(src.image, d, spread=spread)
        return g.vmath('SCALE', color, s=strength) if strength != 1.0 else color
    c = color_in.default_value
    return (float(c[0]) * strength, float(c[1]) * strength, float(c[2]) * strength)


def _cap_ambient_irradiance(g, query, ctx):
    """环境辐照度 = 沿法线取一次世界环境。锥开到最满(漫反射吃的是整个半球的积分),
    与真源辐照度体同一单调关系的程序化近似 —— 不是逐字的 SH 投影,也不假装是。"""
    _ = ctx
    normal = query.get('normal')
    if normal is None:
        return None
    answer = _world_sample(g, normal, spread=1.0)
    return None if answer is None else {'': answer}


def _cap_specular_radiance(g, query, ctx):
    """镜面环境 = 沿反射向量取一次世界环境;锥半径 = 感知粗糙度的平方(与 env_image
    从 mip 反解出的那条 spread 同式),粗糙面拿宽锥均值而不是镜面 HDRI。"""
    _ = ctx
    direction = query.get('direction')
    if direction is None:
        return None
    roughness = query.get('roughness')
    spread = None if roughness is None else g.math(
        'MINIMUM', g.math('MULTIPLY', roughness, roughness), 1.0)
    answer = _world_sample(g, direction, spread=spread)
    return None if answer is None else {'': answer}


MAIN_LIGHT_OVERRIDE = 'ruri_main_light'


def _scene_sun():
    """场景里的主方向光 = 第一盏 SUN(按名字排序取定,免得场景枚举序变了图就跟着变)。"""
    suns = [o for o in bpy.context.scene.objects
            if o.type == 'LIGHT' and o.data.type == 'SUN' and o.visible_get()]
    return sorted(suns, key=lambda o: o.name)[0] if suns else None


def _override_light(material):
    """逐角色自定义灯:材质自定义属性 `ruri_main_light` 指向一盏灯物体,覆盖场景主光。

    为什么挂在**材质**而不是对象上:节点图就是逐材质建的,材质才是这个覆盖的天然作用域。
    挂对象上得靠「反查哪些对象用了这个材质」,而真流程里材质是**先建好再往对象上挂**的 ——
    建图那一刻还没有任何对象引用它,反查必然落空(实测:覆盖被无声吃掉,退回场景太阳)。
    同一材质被两个角色共用时,「谁的覆盖算数」本身也答不出来。

    面板侧的操作仍是「选中对象 → 指定灯」:算子把选中对象的材质逐个写上这个属性再重建,
    用户看到的是对象,单一真源仍是材质上的这一处。"""
    if material is None:
        return None
    light = material.get(MAIN_LIGHT_OVERRIDE)
    return light if light is not None and getattr(light, 'type', '') == 'LIGHT' else None


def _drive(sock, obj, data_path, expr='v', extra=None):
    """把一个 socket 接到 **Blender 自己的数据**上 —— 驱动器是 Blender 原生的「跟着物体走」机制,
    每次依赖图更新自动重算。不是快照:转动/换色/调能量,着色实时跟,不用重建材质。"""
    fcurve = sock.driver_add('default_value')
    driver = fcurve.driver
    driver.type = 'SCRIPTED'
    for old in list(driver.variables):
        driver.variables.remove(old)
    var = driver.variables.new()
    var.name = 'v'
    var.type = 'SINGLE_PROP'
    var.targets[0].id_type = 'OBJECT'
    var.targets[0].id = obj
    var.targets[0].data_path = data_path
    if extra is not None:
        name, path = extra
        second = driver.variables.new()
        second.name = name
        second.type = 'SINGLE_PROP'
        second.targets[0].id_type = 'OBJECT'
        second.targets[0].id = obj
        second.targets[0].data_path = path
    driver.expression = expr


def _drive_transform(sock, obj, transform_type):
    """socket ← 物体变换通道,走驱动器的 **TRANSFORMS 变量** —— 这是 Blender 读物体变换的
    正规通道,依赖图按它建边、求值序有保证。曾用 SINGLE_PROP 直读 matrix_world[i][j]:
    headless 求值序下矩阵还没算好,读到的是单位阵(灯位=0,整条附加光方向全错,实锤)。"""
    fcurve = sock.driver_add('default_value')
    driver = fcurve.driver
    driver.type = 'SCRIPTED'
    for old in list(driver.variables):
        driver.variables.remove(old)
    var = driver.variables.new()
    var.name = 'v'
    var.type = 'TRANSFORMS'
    var.targets[0].id = obj
    var.targets[0].transform_type = transform_type
    var.targets[0].transform_space = 'WORLD_SPACE'
    driver.expression = 'v'


def _light_pos(g, light, label):
    """灯的世界位置(TRANSFORMS LOC_*,动灯实时跟)。"""
    node = g._nd('ShaderNodeCombineXYZ')
    node.label = label
    for i, tt in enumerate(('LOC_X', 'LOC_Y', 'LOC_Z')):
        _drive_transform(node.inputs[i], light, tt)
    return node.outputs[0]


def _light_axis(g, light, label):
    """灯的世界 +Z 轴(指向光源;灯沿本地 -Z 照射)。世界欧拉角经 TRANSFORMS 驱动,
    (0,0,1) 按 XYZ 序在图里用原生 VectorRotate 逐轴旋转 —— 全程 Blender 原生件。"""
    rot = g._nd('ShaderNodeCombineXYZ')
    rot.label = label
    for i, tt in enumerate(('ROT_X', 'ROT_Y', 'ROT_Z')):
        _drive_transform(rot.inputs[i], light, tt)
    rx, ry, rz = g.sep(rot.outputs[0])
    v = (0.0, 0.0, 1.0)
    for axis_vec, angle in (((1.0, 0.0, 0.0), rx), ((0.0, 1.0, 0.0), ry), ((0.0, 0.0, 1.0), rz)):
        nd = g._nd('ShaderNodeVectorRotate')
        nd.rotation_type = 'AXIS_ANGLE'
        g._set(nd.inputs['Vector'], v)
        g._set(nd.inputs['Axis'], axis_vec)
        g._set(nd.inputs['Angle'], angle)
        v = nd.outputs[0]
    return v


def _cap_main_light(g, query, ctx):
    """主方向光的兑现。**不自造光照系统** —— 方向与颜色全部原生取自 Blender 自己的灯物体,
    经**驱动器**绑到灯的 `matrix_world` / `data.color` / `data.energy`:转灯、换色、调能量
    实时跟随,材质不用重建(不是建图时刻的快照)。

    取谁,三级,单一真源逐级下落:
      ① **逐角色覆盖** —— 对象自定义属性 `ruri_main_light` 指向一盏灯(选中对象设一下即可);
      ② **场景太阳** —— 没有覆盖就取场景里的 SUN;
      ③ **前向光** —— 一盏灯都没有时,方向 = ShaderNodeNewGeometry 的 Incoming
         (表面指向观察者),也是 Blender 原生量,等价一盏永远跟着视角的头灯。
         这不是替身值:没有场景灯时,「光从看的方向来」是唯一不依赖任何未知量的定义。

    灯的类型也照 Blender 原生语义分:SUN 是平行光,方向恒为灯的 +Z 轴;POINT/SPOT/AREA 的方向
    随着色点变,故按 `归一化(灯位 − 着色点)` 算,着色点取 Geometry 的 Position(同样原生)。
    归一化交给节点(灯物体可能带缩放),不在 Python 里算死。

    方向最后经 g.b2u 换回内核的 Unity 语义(内核 +Y 是上)。距离衰减恒 1(方向光无距离项);
    阴影衰减恒 1 是因为遮挡归 ShadowAttenuation 那条能力,而它在 Blender 上是 Subsumed ——
    EEVEE/Cycles 在图外自己算,图里再乘一次就是双计。
    """
    _ = query
    light = _override_light(ctx.get('material')) or _scene_sun()
    if light is None:
        return {
            'direction': g.b2u(g.geo().outputs['Incoming']),
            'color': (1.0, 1.0, 1.0),
            'distanceAttenuation': 1.0,
            'shadowAttenuation': 1.0,
            'layerMask': 1.0,
        }
    if light.data.type == 'SUN':
        to_light = _light_axis(g, light, 'RuriMainLightAxis')
    else:
        to_light = g.vmath('SUBTRACT', _light_pos(g, light, 'RuriMainLightPos'),
                           g.geo().outputs['Position'])
    tint = g._nd('ShaderNodeCombineXYZ')
    tint.label = 'RuriMainLightColor'
    for i in range(3):
        _drive(tint.inputs[i], light, 'data.color[%d]' % i, expr='v * e', extra=('e', 'data.energy'))
    return {
        'direction': g.b2u(g.vmath('NORMALIZE', to_light)),
        'color': tint.outputs[0],
        'distanceAttenuation': 1.0,
        'shadowAttenuation': 1.0,
        'layerMask': 1.0,
    }


def _additional_lights(material):
    """附加光 = 场景里除主光以外的所有可见灯,按名字定序。

    「除主光以外」按对象取,不按类型:主光可能是场景 SUN,也可能是本材质自己覆盖的那盏。
    定序按名字而不是枚举序 —— 场景里加删别的对象不该让灯的下标跳位。"""
    main = _override_light(material) or _scene_sun()
    lights = [o for o in bpy.context.scene.objects
              if o.type == 'LIGHT' and o.visible_get() and o is not main]
    return sorted(lights, key=lambda o: o.name)


def _cap_additional_light_count(g, query, ctx):
    """有几盏附加光 —— 装配期数得出来,直接给常量。这个值喂的是灯循环的迭代数socket。"""
    _ = (g, query)
    return {'': float(len(_additional_lights(ctx.get('material'))))}


LIGHT_TABLE_ROWS = 4


def _light_table(material, lights):
    """灯表 = 一张 **N×4 浮点图**,一列一盏灯:
       行0 (x,y,z | 类型)   类型 0=SUN 1=POINT/AREA 2=SPOT
       行1 (r,g,b | -)      颜色 × 能量
       行2 (x,y,z | -)      灯的世界 +Z 轴(指向光源;SUN 的方向、SPOT 的锥轴)
       行3 (cosOuter, cosInner, 0 | -)  聚光锥

    **灯数没有上限**:图有多宽就装多少盏。图里按下标采样是固定几个节点,
    与灯数无关 —— 这正是不设槽位数的理由(槽位制的上限是实现漏进协议的产物)。
    灯动/换色只重写像素(实测:改像素不重建任何节点,渲染即时跟)。
    """
    name = 'RuriLightTable_' + (material.name if material is not None else 'Scene')
    width = max(len(lights), 1)
    image = bpy.data.images.get(name)
    if image is None or tuple(image.size) != (width, LIGHT_TABLE_ROWS) or not image.is_float:
        if image is not None:
            bpy.data.images.remove(image)
        image = bpy.data.images.new(name, width, LIGHT_TABLE_ROWS, float_buffer=True, alpha=True)
    image.colorspace_settings.name = 'Non-Color'
    rows = [[0.0] * (width * 4) for _ in range(LIGHT_TABLE_ROWS)]
    for k, light in enumerate(lights):
        matrix = light.matrix_world
        kind = light.data.type
        flag = 0.0 if kind == 'SUN' else (2.0 if kind == 'SPOT' else 1.0)
        axis = [matrix[0][2], matrix[1][2], matrix[2][2]]
        length = math.sqrt(sum(c * c for c in axis)) or 1.0
        axis = [c / length for c in axis]
        energy = float(light.data.energy)
        color = [float(c) * energy for c in light.data.color]
        if kind == 'SPOT':
            size = float(light.data.spot_size)
            blend = float(light.data.spot_blend)
            cone = [math.cos(size * 0.5), math.cos(size * (1.0 - blend) * 0.5)]
        else:
            cone = [-1.0, 1.0]
        for row, value in (
                (0, [matrix[0][3], matrix[1][3], matrix[2][3], flag]),
                (1, color + [1.0]),
                (2, axis + [1.0]),
                (3, cone + [0.0, 1.0])):
            rows[row][k * 4:k * 4 + 4] = value
    flat = []
    for row in rows:
        flat.extend(row)
    image.pixels = flat
    image.update()
    return image, width


def _table_row(g, image, index, row, width):
    """按下标取灯表的一行。Closest + EXTEND:纹素寻址不许再插一次值,
    否则相邻两盏灯会被硬件混成一盏不存在的灯。"""
    u = g.math('DIVIDE', g.math('ADD', index, 0.5), float(width))
    node = g._nd('ShaderNodeTexImage')
    node.image = image
    node.interpolation = 'Closest'
    node.extension = 'EXTEND'
    node.label = 'RuriLightTable'
    g._set(node.inputs['Vector'], g.comb(u, (row + 0.5) / LIGHT_TABLE_ROWS, 0.0))
    return node.outputs['Color'], node.outputs['Alpha']


def _cap_additional_light(g, query, ctx):
    """第 index 盏附加光。**逐迭代**按下标查灯表,所以下标是什么值都取得对 ——
    循环体内的割点每圈自己采一次,不存在「把下标导出去只剩最后一圈」的问题。

    类型差异不靠逐灯建节点,靠表里的类型位在图里选:
      SUN  方向 = 灯的 +Z 轴,无距离衰减;
      其余 方向 = 归一化(灯位 − 着色点),距离衰减 = 平方反比(Blender 自己的灯就这么落衰);
      SPOT 再乘一层原生锥(cos 半角 + spot_blend 软边,smoothstep,与 Cycles 同式)。
    """
    index = query.get('index')
    lights = _additional_lights(ctx.get('material'))
    if index is None or not lights:
        return None
    image, width = _light_table(ctx.get('material'), lights)
    position, type_flag = _table_row(g, image, index, 0, width)
    color, _ca = _table_row(g, image, index, 1, width)
    axis, _aa = _table_row(g, image, index, 2, width)
    cone, _oa = _table_row(g, image, index, 3, width)
    cos_outer, cos_inner, _unused = g.sep(cone)

    to_light = g.vmath('SUBTRACT', position, g.geo().outputs['Position'])
    is_sun = g.math('SUBTRACT', 1.0, g.math('MINIMUM', type_flag, 1.0))
    direction = g.mixv(is_sun, to_light, axis)

    distance2 = g.vmath('DOT_PRODUCT', to_light, to_light)
    attenuation = g.mixf(is_sun, g.math('DIVIDE', 1.0, g.math('MAXIMUM', distance2, 1e-4)), 1.0)

    is_spot = g.math('COMPARE', type_flag, 2.0, 0.5)
    cone_cos = g.vmath('DOT_PRODUCT', g.vmath('NORMALIZE', to_light), g.vmath('NORMALIZE', axis))
    edge = g.clampn(g.math('DIVIDE', g.math('SUBTRACT', cone_cos, cos_outer),
                           g.math('MAXIMUM', g.math('SUBTRACT', cos_inner, cos_outer), 1e-4)))
    smooth = g.math('MULTIPLY', g.math('MULTIPLY', edge, edge),
                    g.math('SUBTRACT', 3.0, g.math('MULTIPLY', 2.0, edge)))
    attenuation = g.math('MULTIPLY', attenuation, g.mixf(is_spot, 1.0, smooth))

    return {
        'direction': g.b2u(g.vmath('NORMALIZE', direction)),
        'color': color,
        'distanceAttenuation': attenuation,
        'shadowAttenuation': 1.0,
        'layerMask': 1.0,
    }


# 能力身份 → {引擎: 建图器}。建图器契约:答得出 → (值, w值) 二元组;答不出 → None。
# '*' = 所有引擎同一答案(世界环境对 EEVEE 与 Cycles 是同一份数据:
# 本腿的材质出口是 Emission + Transparent,两个引擎都不会给表面送任何接收光,间接光只能自取)。
CAP_BUILDERS = {
    'AmbientIrradiance': {'*': _cap_ambient_irradiance},
    'SpecularRadiance': {'*': _cap_specular_radiance},
    'MainLight': {'*': _cap_main_light},
    'AdditionalLightCount': {'*': _cap_additional_light_count},
    'AdditionalLight': {'*': _cap_additional_light},
}


def _gntree(name):
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    return bpy.data.node_groups.new(name, 'GeometryNodeTree')


class GV(G):
    """几何节点建图器:同一套 G 词汇落在 GeometryNodeTree(顶点腿)。差异单点治理:
    贴图 = GeometryNodeImageTexture(图挂 Image 输入 socket,无色彩空间口——空间随 image
    数据块走,与着色腿共用同名占位/真图);b2u/u2b = 纯 (x,z,y) 互换 —— 几何域在对象空间,
    换轴就是导入约定 coordinate.BLENDER 的逆,没有根 yaw 参与;方向数学(法线/重力/视向)
    对对象平移与绕竖轴旋转不变,故"对象空间当 Unity 世界用"精确成立(自由旋转对象时重力向
    随对象转,与游戏的世界重力有偏——文档级偏差,_FurGravityStrength 缺省 0)。
    相机位置经 vtrans(CAMERA→WORLD, POINT) 落 input_camPos socket,由 wrapper 喂对象空间值。"""

    def tex(self, name, uv, non_color=False, extension='REPEAT', interp='Linear'):
        k = ('tx', name, self._ck(uv), non_color, extension, interp)
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('GeometryNodeImageTexture')
        img = bpy.data.images.get(name)
        if img is None:
            img = bpy.data.images.new(name, 4, 4)
            img.generated_color = (1.0, 1.0, 1.0, 1.0)
        if non_color:
            img.colorspace_settings.name = 'Non-Color'
        nd.inputs['Image'].default_value = img
        nd.label = name
        nd.interpolation = interp
        nd.extension = extension
        self._set(nd.inputs['Vector'], uv)
        r = (nd.outputs['Color'], nd.outputs['Alpha'])
        self._cse[k] = r
        return r

    def b2u(self, v, point=False):
        x, y, z = self.sep(v)
        return self.comb(x, z, y)

    def u2b(self, v):
        x, y, z = self.sep(v)
        return self.comb(x, z, y)

    def vtrans(self, v, frm, to, kind='VECTOR'):
        if frm == 'CAMERA' and to == 'WORLD' and kind == 'POINT':
            return self.inp('input_camPos', True)
        raise RuntimeError('几何树无空间变换节点: %s->%s(%s)' % (frm, to, kind))

    def geo(self):
        raise RuntimeError('几何树无 ShaderNodeNewGeometry(位置/法线由 wrapper 经组输入喂)')

    def texco(self):
        raise RuntimeError('几何树无 ShaderNodeTexCoord(UV 由 wrapper 经组输入喂)')

    def attr(self, name):
        raise RuntimeError('几何树无 ShaderNodeAttribute(命名属性由 wrapper 读)')

    def rig_basis(self, v):
        # 增量三列是顶点腿自己写出去的点属性,顶点腿再回头读就是自指。真要在顶点腿用骨骼基座,
        # 得给克隆注入一个 per-角色的 ObjectInfo(材质克隆时才知道是哪个骨架)——现无消费者。
        raise RuntimeError('顶点腿无骨骼基座桥(要用先给克隆注入 per-角色 ObjectInfo)')

    def env(self, name, direction, default, mip=None):
        raise RuntimeError('几何树无环境采样(顶点腿不该走到 IBL)')

    def group(self, name, ins):
        raise RuntimeError('顶点树不引用共享子组(全内联编译)')

    def group_named(self, name, pairs):
        raise RuntimeError('顶点树不引用共享子组(全内联编译)')


def _img(name, rgba, non_color):
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, 4, 4, alpha=True)
    # 占位身份标记:参数面板回读时据此不把中性占位当成用户绑的贴图。
    img['ruri_placeholder'] = 1
    # 恒刷新:老会话同名旧占位(如白 _BumpMap)会被 get 命中,颜色必须跟上本次部署。
    img.generated_color = rgba
    # 色彩空间是**槽语义**,在这里定死 —— 曾经靠组内 g.tex 顺手设,贴图外提后那行
    # 不再执行,数据图就被当彩图按 sRGB 解码(法线全错)。占位图是唯一真源,内外同吃。
    img.colorspace_settings.name = 'Non-Color' if non_color else 'sRGB'
    return img


def _images():
    _img('_BaseMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_BlendTex', (0.0, 0.0, 0.0, 0.0), False)
    _img('_BumpMap', (0.5, 0.5, 1.0, 1.0), True)
    _img('_ClearCoatMask', (1.0, 1.0, 1.0, 1.0), True)
    _img('_DiffRampMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_DisturbTex1', (1.0, 1.0, 1.0, 1.0), True)
    _img('_EmissionMap', (0.0, 0.0, 0.0, 0.0), False)
    _img('_EmotionMap', (0.0, 0.0, 0.0, 0.0), False)
    _img('_FurDirMap', (0.5, 0.5, 1.0, 1.0), True)
    _img('_FurDyeMap', (0.0, 0.0, 0.0, 0.0), False)
    _img('_FurMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_HighlightMap', (0.0, 0.0, 0.0, 0.0), True)
    _img('_LineMap', (0.0, 0.0, 0.0, 0.0), True)
    _img('_MainTex', (1.0, 1.0, 1.0, 1.0), False)
    _img('_MaskTex', (1.0, 1.0, 1.0, 1.0), True)
    _img('_MatcapTex', (1.0, 1.0, 1.0, 1.0), False)
    _img('_MetallicGlossMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_NormalMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_ParallaxTex', (1.0, 1.0, 1.0, 1.0), True)
    _img('_RMOSMap', (0.0, 0.0, 0.0, 0.0), True)
    _img('_SDFLightmap', (0.0, 0.0, 0.0, 0.0), True)
    _img('_SDFMask', (0.0, 0.0, 0.0, 0.0), True)
    _img('_ShadowLutTex', (1.0, 1.0, 1.0, 1.0), False)
    _img('_SilkStockingsMask', (1.0, 1.0, 1.0, 1.0), True)
    _img('_SpecRampMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_SplitNormalMap', (0.5, 0.5, 0.5, 1.0), True)
    _img('_StrokeMap', (0.5, 0.5, 0.5, 1.0), True)
    _img('_VFXSpecialBlendTex', (0.0, 0.0, 0.0, 0.0), False)
    _img('_VFXSpecialMainTex', (1.0, 1.0, 1.0, 1.0), False)


def build_RCE_ApplyEndfieldOutlineAlbedo():
    t = _tree('RCE_ApplyEndfieldOutlineAlbedo')
    g = G(t)
    v0 = g.inp('albedo', True)
    v1 = g.inp('_OutlineTintEnable', False, 0.0)
    v2 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v3 = g.inp('_OutlineTintColor_w', False, 1.0)
    v4 = g.inp('_OutlineColorBrightness', False, 0.5)
    v5 = g.bc(v4)
    v6 = g.vmath('MULTIPLY', v0, v5)
    v7 = g.vmath('DOT_PRODUCT', v6, (0.2126729, 0.7152, 0.072175))
    v8 = g.inp('_OutlineColorSaturation', False, 1.5)
    v9 = g.bc(v7)
    v10 = g.vmath('SUBTRACT', v6, v9)
    v11 = g.bc(v8)
    v12 = g.vmath('MULTIPLY', v11, v10)
    v13 = g.bc(v7)
    v14 = g.vmath('ADD', v13, v12)
    v15 = g.mixv(v1, v14, v2)
    g.out_('albedo', v15, True)


def build_RCE_BRDF_AnisotropicNDF_SilkStockings_Endfield():
    t = _tree('RCE_BRDF_AnisotropicNDF_SilkStockings_Endfield')
    g = G(t)
    v0 = g.inp('N', True)
    v1 = g.inp('V', True)
    v2 = g.inp('H', True)
    v3 = g.inp('tangentDir', True)
    v4 = g.inp('tangentSign', False)
    v5 = g.inp('alpha2', False)
    v6 = g.inp('ph_aniso', False)
    v7 = g.vmath('DOT_PRODUCT', v3, v0)
    v8 = g.bc(v7)
    v9 = g.vmath('MULTIPLY', v0, v8)
    v10 = g.vmath('SUBTRACT', v3, v9)
    v11 = g.vmath('NORMALIZE', v10)
    v12 = g.vmath('CROSS_PRODUCT', v0, v11)
    v13 = g.bc(v4)
    v14 = g.vmath('MULTIPLY', v12, v13)
    v15 = g.inp('_SilkStockingsSpecularValue', False, 2.0)
    v16 = g.bc(v15)
    v17 = g.vmath('MULTIPLY', v1, v16)
    v18 = g.vmath('ADD', v2, v17)
    v19 = g.vmath('NORMALIZE', v18)
    v20 = g.math('ADD', v6, 1)
    v21 = g.math('MULTIPLY', v5, v20)
    v22 = g.math('SUBTRACT', 1, v6)
    v23 = g.math('MULTIPLY', v22, v5)
    v24 = g.math('MULTIPLY', v21, v23)
    v25 = g.vmath('DOT_PRODUCT', v11, v19)
    v26 = g.math('MULTIPLY', v23, v25)
    v27 = g.vmath('DOT_PRODUCT', v14, v19)
    v28 = g.math('MULTIPLY', v27, v21)
    v29 = g.vmath('DOT_PRODUCT', v0, v19)
    v30 = g.math('MULTIPLY', v29, v24)
    v31 = g.math('MULTIPLY', v26, v26)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('ADD', v31, v32)
    v34 = g.math('MULTIPLY', v30, v30)
    v35 = g.math('ADD', v33, v34)
    v36 = g.math('MULTIPLY', v24, v24)
    v37 = g.math('MULTIPLY', v36, v24)
    v38 = g.math('MULTIPLY', v35, v35)
    v39 = g.math('COMPARE', v38, v37, 1e-05)
    v40 = g.math('SUBTRACT', 1.0, v39)
    v41 = g.math('DIVIDE', v37, v38)
    v42 = g.mixf(v40, 1, v41)
    g.out_('ret', v42, False)


def build_RCE_BRDF_ClearCoat_Direct_Burley():
    t = _tree('RCE_BRDF_ClearCoat_Direct_Burley')
    g = G(t)
    v0 = g.inp('ccMask', False)
    v1 = g.inp('ccPercRough', False)
    v2 = g.inp('ccAlpha', False)
    v3 = g.inp('ccF0', True)
    v4 = g.inp('ccNdotH', False)
    v5 = g.inp('ccNdotV', False)
    v6 = g.inp('VdotH', False)
    v7 = g.math('SUBTRACT', 1, v6)
    v8 = g.math('MULTIPLY', v7, v7)
    v9 = g.math('MULTIPLY', v8, v8)
    v10 = g.math('MULTIPLY', v9, v7)
    v11 = g.math('SUBTRACT', 1, v10)
    v12 = g.bc(v11)
    v13 = g.vmath('MULTIPLY', v3, v12)
    v14 = g.bc(v10)
    v15 = g.vmath('ADD', v13, v14)
    v16 = g.bc(v0)
    v17 = g.vmath('MULTIPLY', v16, v15)
    v18 = g.vmath('SUBTRACT', (1, 1, 1), v17)
    v19 = g.bc(v0)
    v20 = g.vmath('MULTIPLY', v19, v17)
    v21 = g.vmath('SUBTRACT', (1, 1, 1), v20)
    v22 = g.math('MULTIPLY', v2, v2)
    v23 = g.math('MULTIPLY', v4, v22)
    v24 = g.math('SUBTRACT', v23, v4)
    v25 = g.math('MULTIPLY', v24, v4)
    v26 = g.math('ADD', v25, 1)
    v27 = g.math('MULTIPLY', v26, v26)
    v28 = g.math('COMPARE', v27, v22, 1e-05)
    v29 = g.math('SUBTRACT', 1.0, v28)
    v30 = g.math('DIVIDE', v22, v27)
    v31 = g.mixf(v29, 1, v30)
    v32 = g.math('MULTIPLY_ADD', v5, 2, v2)
    v33 = g.math('ADD', v32, 0.0001)
    v34 = g.math('DIVIDE', 0.5, v33)
    v35 = g.math('MULTIPLY', v34, v31)
    v36 = g.bc(v35)
    v37 = g.vmath('MULTIPLY', v36, v17)
    v38 = g.vmath('MAXIMUM', v37, (0, 0, 0))
    v39 = g.vmath('MINIMUM', v38, (20, 20, 20))
    g.out_('ret', v39, True)
    g.out_('ccBaseScale', v18, True)
    g.out_('ccDiffScale', v21, True)


def build_RCE_ComputeCamLightFactors():
    t = _tree('RCE_ComputeCamLightFactors')
    g = G(t)
    v0 = g.inp('camFwd', True)
    v1 = g.inp('adjXZ_x', False)
    v2 = g.inp('adjXZ_z', False)
    v3 = g.sep(v0)
    v4 = g.math('MULTIPLY', v3[0], v3[0])
    v5 = g.math('MULTIPLY', v3[2], v3[2])
    v6 = g.math('ADD', v4, v5)
    v7 = g.math('INVERSE_SQRT', v6, 0.0)
    v8 = g.math('MULTIPLY', v7, v3[0])
    v9 = g.math('MULTIPLY', v1, v8)
    v10 = g.math('MULTIPLY', v7, v3[2])
    v11 = g.math('MULTIPLY', v2, v10)
    v12 = g.math('ADD', v9, v11)
    v13 = g.math('MULTIPLY', v12, -1.0)
    v14 = g.math('ABSOLUTE', v3[1], 0.0)
    v15 = g.math('SUBTRACT', 0.75, v14)
    v16 = g.math('MULTIPLY', 2, v15)
    v17 = g.clampn(v16)
    v18 = g.math('MULTIPLY', v17, v17)
    v19 = g.math('MULTIPLY', 2, v17)
    v20 = g.math('SUBTRACT', 3, v19)
    v21 = g.math('MULTIPLY', v18, v20)
    g.out_('camLightDot', v13, False)
    g.out_('camYSmooth', v21, False)


def build_RCE_ComputeExposure():
    t = _tree('RCE_ComputeExposure')
    g = G(t)
    v0 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v1 = g.inp('_CharacterParams12_w', False, 0.0)
    v2 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v3 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v4 = g.sep(v2)
    v5 = g.math('SUBTRACT', 1, v4[0])
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('ADD', v6, v4[0])
    v8 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v9 = g.inp('_ExposureParams_w', False, 0.0)
    v10 = g.sep(v8)
    v11 = g.math('MULTIPLY', v7, v10[0])
    g.out_('ret', v11, False)


def build_RCE_ComputeNPRDiffuse():
    t = _tree('RCE_ComputeNPRDiffuse')
    g = G(t)
    v0 = g.inp('hemisphereN', True)
    v1 = g.inp('ambCol', True)
    v2 = g.inp('brightness', False)
    v3 = g.inp('blendedLightCol', True)
    v4 = g.inp('blendedLightInt', False)
    v5 = g.inp('minShadow', False)
    v6 = g.inp('combWeight', False)
    v7 = g.inp('albScaled', True)
    v8 = g.inp('diffColor', True)
    v9 = g.inp('rampCol', True)
    v10 = g.inp('rampChroma', False)
    v11 = g.inp('rampChromaInv', False)
    v12 = g.inp('_CharacterParams6', True, (0.0, 1.0, 4.371139E-08))
    v13 = g.inp('_CharacterParams6_w', False, 0.0)
    v14 = g.vmath('DOT_PRODUCT', v0, v12)
    v15 = g.inp('_CharacterParams7', True, (0.15, 1.5, 0.5))
    v16 = g.inp('_CharacterParams7_w', False, 0.0)
    v17 = g.sep(v15)
    v18 = g.math('ADD', v14, v17[0])
    v19 = g.clampn(v18)
    v20 = g.math('MULTIPLY', v19, v17[1])
    v21 = g.math('ADD', v20, v17[2])
    v22 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v23 = g.inp('_CharacterParams1_w', False, 0.0)
    v24 = g.sep(v22)
    v25 = g.math('MULTIPLY', v5, v24[1])
    v26 = g.vmath('SUBTRACT', (1, 1, 1), v1)
    v27 = g.bc(v25)
    v28 = g.vmath('MULTIPLY', v27, v26)
    v29 = g.vmath('ADD', v28, v1)
    v30 = g.bc(v21)
    v31 = g.vmath('MULTIPLY', v30, v29)
    v32 = g.bc(v4)
    v33 = g.vmath('MULTIPLY', v3, v32)
    v34 = g.vmath('DOT_PRODUCT', v33, (0.2126729, 0.7151522, 0.072175))
    v35 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v36 = g.inp('_CharacterParams12_w', False, 0.0)
    v37 = g.sep(v35)
    v38 = g.math('SUBTRACT', 1, v37[1])
    v39 = g.bc(v37[1])
    v40 = g.vmath('MULTIPLY', v3, v39)
    v41 = g.bc(v38)
    v42 = g.vmath('ADD', v40, v41)
    v43 = g.sep(v31)
    v44 = g.math('MULTIPLY', v43[0], v2)
    v45 = g.sep(v42)
    v46 = g.math('MULTIPLY', v44, v45[0])
    v47 = g.sep(v3)
    v48 = g.math('MULTIPLY', v47[0], v4)
    v49 = g.math('SUBTRACT', v48, v34)
    v50 = g.math('MULTIPLY', v5, v49)
    v51 = g.math('ADD', v46, v50)
    v52 = g.math('ADD', v51, v34)
    v53 = g.inp('_CharacterParams0', True, (0.0, 0.9, 0.8))
    v54 = g.inp('_CharacterParams0_w', False, 0.8)
    v55 = g.sep(v53)
    v56 = g.math('MULTIPLY', v52, v55[1])
    v57 = g.comb(v56, 0.0, 0.0)
    v58 = g.math('MULTIPLY', v43[1], v2)
    v59 = g.math('MULTIPLY', v58, v45[1])
    v60 = g.math('MULTIPLY', v47[1], v4)
    v61 = g.math('SUBTRACT', v60, v34)
    v62 = g.math('MULTIPLY', v5, v61)
    v63 = g.math('ADD', v59, v62)
    v64 = g.math('ADD', v63, v34)
    v65 = g.math('MULTIPLY', v64, v55[1])
    v66 = g.sep(v57)
    v67 = g.comb(v66[0], v65, v66[2])
    v68 = g.math('MULTIPLY', v43[2], v2)
    v69 = g.math('MULTIPLY', v68, v45[2])
    v70 = g.math('MULTIPLY', v47[2], v4)
    v71 = g.math('SUBTRACT', v70, v34)
    v72 = g.math('MULTIPLY', v5, v71)
    v73 = g.math('ADD', v69, v72)
    v74 = g.math('ADD', v73, v34)
    v75 = g.math('MULTIPLY', v74, v55[1])
    v76 = g.sep(v67)
    v77 = g.comb(v76[0], v76[1], v75)
    v78 = g.vmath('MULTIPLY', v7, (0.65, 0.65, 0.65))
    v79 = g.vmath('DOT_PRODUCT', v78, (0.2126729, 0.7151522, 0.072175))
    v80 = g.vmath('MULTIPLY', v7, (0.65, 0.65, 0.65))
    v81 = g.bc(v79)
    v82 = g.vmath('SUBTRACT', v80, v81)
    v83 = g.vmath('MULTIPLY', v82, (1.2, 1.2, 1.2))
    v84 = g.bc(v79)
    v85 = g.vmath('ADD', v83, v84)
    v86 = g.mixv(v6, v85, v7)
    v87 = g.mixv(v5, v86, v8)
    v88 = g.bc(v10)
    v89 = g.vmath('MULTIPLY', v9, v88)
    v90 = g.bc(v11)
    v91 = g.vmath('ADD', v89, v90)
    v92 = g.vmath('MULTIPLY', v87, v91)
    v93 = g.vmath('DOT_PRODUCT', v87, (0.2126729, 0.7151522, 0.072175))
    v94 = g.vmath('DOT_PRODUCT', v92, (0.2126729, 0.7151522, 0.072175))
    v95 = g.math('MAXIMUM', v94, 0.001)
    v96 = g.math('DIVIDE', v93, v95)
    v97 = g.clampn(v96, 0, 1.5)
    v98 = g.math('SUBTRACT', 1, v55[2])
    v99 = g.math('MULTIPLY', v5, v98)
    v100 = g.math('ADD', v99, v55[2])
    v101 = g.bc(v97)
    v102 = g.vmath('MULTIPLY', v92, v101)
    g.out_('ret', v102, True)
    g.out_('fullDiff', v77, True)
    g.out_('ambDiffInt', v100, False)


def build_RCE_ComputeSkinDir():
    t = _tree('RCE_ComputeSkinDir')
    g = G(t)
    v0 = g.inp('camFwd', True)
    v1 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v2 = g.inp('_CharacterParams9_w', False, 0.4)
    v3 = g.sep(v1)
    v4 = g.math('MULTIPLY', v3[1], -1.0)
    v5 = g.sep(v0)
    v6 = g.math('MULTIPLY', v4, v5[2])
    v7 = g.comb(v6, 0.0, 0.0)
    v8 = g.math('MULTIPLY', v5[2], v3[0])
    v9 = g.sep(v7)
    v10 = g.comb(v9[0], v8, v9[2])
    v11 = g.math('MULTIPLY', v5[0], v3[1])
    v12 = g.math('MULTIPLY', v3[0], v5[1])
    v13 = g.math('SUBTRACT', v11, v12)
    v14 = g.sep(v10)
    v15 = g.comb(v14[0], v14[1], v13)
    v16 = g.vmath('NORMALIZE', v15)
    g.out_('ret', v16, True)


def build_RCE_ComputeSkinSmoothFalloff():
    t = _tree('RCE_ComputeSkinSmoothFalloff')
    g = G(t)
    v0 = g.inp('NdotV', False)
    v1 = g.math('ABSOLUTE', v0, 0.0)
    v2 = g.math('SUBTRACT', 1, v1)
    v3 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v4 = g.inp('_CharacterParams9_w', False, 0.4)
    v5 = g.math('MULTIPLY', 0.6, -1.0)
    v6 = g.math('MULTIPLY', v4, v5)
    v7 = g.math('ADD', v6, 0.8)
    v8 = g.math('MULTIPLY', 0.4, -1.0)
    v9 = g.math('MULTIPLY', v4, v8)
    v10 = g.math('ADD', v9, 0.9)
    v11 = g.math('SUBTRACT', v2, v7)
    v12 = g.math('SUBTRACT', v10, v7)
    v13 = g.math('DIVIDE', v11, v12)
    v14 = g.clampn(v13)
    v15 = g.math('MULTIPLY', v14, v14)
    v16 = g.math('MULTIPLY', 2, v14)
    v17 = g.math('SUBTRACT', 3, v16)
    v18 = g.math('MULTIPLY', v15, v17)
    g.out_('ret', v18, False)


def build_RCE_ComputeSkinSpec():
    t = _tree('RCE_ComputeSkinSpec')
    g = G(t)
    v0 = g.inp('skinDir', True)
    v1 = g.inp('N', True)
    v2 = g.inp('diffColor', True)
    v3 = g.inp('skinShadow', False)
    v4 = g.inp('skinAmt', False)
    v5 = g.vmath('DOT_PRODUCT', v0, v1)
    v6 = g.clampn(v5)
    v7 = g.math('MULTIPLY', v3, v4)
    v8 = g.inp('_CharacterParams8', True, (0.0, 0.0, 0.0))
    v9 = g.inp('_CharacterParams8_w', False, 1.0)
    v10 = g.sep(v8)
    v11 = g.math('MULTIPLY', v7, v10[0])
    v12 = g.math('MULTIPLY', v11, v9)
    v13 = g.math('MULTIPLY', v12, v6)
    v14 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v15 = g.inp('_CharacterParams9_w', False, 0.4)
    v16 = g.sep(v14)
    v17 = g.sep(v2)
    v18 = g.math('SUBTRACT', v17[0], 0.25)
    v19 = g.math('MULTIPLY', v16[2], v18)
    v20 = g.math('ADD', v19, 0.25)
    v21 = g.math('MULTIPLY', v13, v20)
    v22 = g.comb(v21, 0.0, 0.0)
    v23 = g.math('MULTIPLY', v3, v4)
    v24 = g.math('MULTIPLY', v23, v10[1])
    v25 = g.math('MULTIPLY', v24, v9)
    v26 = g.math('MULTIPLY', v25, v6)
    v27 = g.math('SUBTRACT', v17[1], 0.25)
    v28 = g.math('MULTIPLY', v16[2], v27)
    v29 = g.math('ADD', v28, 0.25)
    v30 = g.math('MULTIPLY', v26, v29)
    v31 = g.sep(v22)
    v32 = g.comb(v31[0], v30, v31[2])
    v33 = g.math('MULTIPLY', v3, v4)
    v34 = g.math('MULTIPLY', v33, v10[2])
    v35 = g.math('MULTIPLY', v34, v9)
    v36 = g.math('MULTIPLY', v35, v6)
    v37 = g.math('SUBTRACT', v17[2], 0.25)
    v38 = g.math('MULTIPLY', v16[2], v37)
    v39 = g.math('ADD', v38, 0.25)
    v40 = g.math('MULTIPLY', v36, v39)
    v41 = g.sep(v32)
    v42 = g.comb(v41[0], v41[1], v40)
    g.out_('ret', v42, True)


def build_RCE_ComputeVFXUV_Endfield():
    t = _tree('RCE_ComputeVFXUV_Endfield')
    g = G(t)
    v0 = g.inp('uv0', True)
    v1 = g.inp('uv1', True)
    v2 = g.inp('weights', True)
    v3 = g.inp('weights_w', False)
    v4 = g.inp('speed', True)
    v5 = g.inp('speed_w', False)
    v6 = g.inp('time', False)
    v7 = g.inp('customData', False)
    v8 = g.inp('rotateMat', True)
    v9 = g.inp('rotateMat_w', False)
    v10 = g.inp('st', True)
    v11 = g.inp('st_w', False)
    v12 = g.inp('disturb', True)
    v13 = g.inp('useDisturb', False)
    v14 = g.sep(v2)
    v15 = g.comb(v14[0], v14[0], 0.0)
    v16 = g.vmath('MULTIPLY', v0, v15)
    v17 = g.comb(v14[1], v14[1], 0.0)
    v18 = g.vmath('MULTIPLY', v1, v17)
    v19 = g.vmath('ADD', v16, v18)
    v20 = g.sep(v4)
    v21 = g.comb(v20[0], v20[1], 0.0)
    v22 = g.comb(v6, v6, 0.0)
    v23 = g.vmath('MULTIPLY', v21, v22)
    v24 = g.comb(v20[2], v5, 0.0)
    v25 = g.comb(v7, v7, 0.0)
    v26 = g.vmath('MULTIPLY', v24, v25)
    v27 = g.vmath('ADD', v23, v26)
    v28 = g.vmath('ADD', v19, v27)
    v29 = g.vmath('SUBTRACT', v28, (0.5, 0.5, 0.0))
    v30 = g.sep(v29)
    v31 = g.sep(v8)
    v32 = g.math('MULTIPLY', v30[0], v31[0])
    v33 = g.math('MULTIPLY', v30[1], v31[2])
    v34 = g.math('ADD', v32, v33)
    v35 = g.math('ADD', v34, 0.5)
    v36 = g.sep(v28)
    v37 = g.comb(v35, v36[1], 0.0)
    v38 = g.math('MULTIPLY', v30[0], v31[1])
    v39 = g.math('MULTIPLY', v30[1], v9)
    v40 = g.math('ADD', v38, v39)
    v41 = g.math('ADD', v40, 0.5)
    v42 = g.sep(v37)
    v43 = g.comb(v42[0], v41, 0.0)
    v44 = g.sep(v10)
    v45 = g.comb(v44[0], v44[1], 0.0)
    v46 = g.vmath('MULTIPLY', v43, v45)
    v47 = g.comb(v44[2], v11, 0.0)
    v48 = g.vmath('ADD', v46, v47)
    v49 = g.comb(v13, v13, 0.0)
    v50 = g.vmath('MULTIPLY', v12, v49)
    v51 = g.vmath('ADD', v48, v50)
    g.out_('ret', v51, True)


def build_RCE_D_GGX_Float():
    t = _tree('RCE_D_GGX_Float')
    g = G(t)
    v0 = g.inp('NdotH', False)
    v1 = g.inp('alpha2', False)
    v2 = g.math('MULTIPLY', v0, v1)
    v3 = g.math('SUBTRACT', v2, v0)
    v4 = g.math('MULTIPLY', v3, v0)
    v5 = g.math('ADD', v4, 1)
    v6 = g.math('MULTIPLY', v5, v5)
    v7 = g.math('COMPARE', v6, v1, 1e-05)
    v8 = g.math('SUBTRACT', 1.0, v7)
    v9 = g.math('DIVIDE', v1, v6)
    v10 = g.mixf(v8, 1, v9)
    v11 = g.math('MINIMUM', v10, 2048)
    g.out_('ret', v11, False)


def build_RCE_BRDF_GGX_Stylized_Endfield():
    t = _tree('RCE_BRDF_GGX_Stylized_Endfield')
    g = G(t)
    v0 = g.inp('N', True)
    v1 = g.inp('V', True)
    v2 = g.inp('adjustedLightDir', True)
    v3 = g.inp('camFwd', True)
    v4 = g.inp('roughness', False)
    v5 = g.vmath('DOT_PRODUCT', v0, v1)
    v6 = g.clampn(v5)
    v7 = g.sep(v3)
    v8 = g.sep(v2)
    v9 = g.comb(v7[0], v8[1], v7[2])
    v10 = g.vmath('NORMALIZE', v9)
    v11 = g.vmath('MULTIPLY', v1, (3, 3, 3))
    v12 = g.vmath('ADD', v11, v2)
    v13 = g.vmath('MULTIPLY', v10, (2, 2, 2))
    v14 = g.vmath('ADD', v12, v13)
    v15 = g.vmath('NORMALIZE', v14)
    v16 = g.vmath('DOT_PRODUCT', v0, v15)
    v17 = g.math('MULTIPLY', v4, v4)
    v18 = g.group_named('RCE_D_GGX_Float', [('NdotH', v16), ('alpha2', v17)])
    v19 = g.math('MULTIPLY', v6, 2)
    v20 = g.math('ADD', v19, v4)
    v21 = g.math('ADD', v20, 0.0001)
    v22 = g.math('DIVIDE', 0.5, v21)
    v23 = g.math('MULTIPLY', v18[0], v22)
    v24 = g.math('SUBTRACT', v23, 6.103515625e-05)
    v25 = g.clampn(v24, 0, 20)
    g.out_('ret', v25, False)
    g.out_('D_raw', v18[0], False)
    g.out_('NdotV_spec', v6, False)
    g.out_('H', v15, True)
    g.out_('NdotH', v16, False)


def build_RCE_EnvBRDF_Endfield():
    t = _tree('RCE_EnvBRDF_Endfield')
    g = G(t)
    v0 = g.inp('NdotV', False)
    v1 = g.inp('roughSq', False)
    v2 = g.math('MULTIPLY', v0, v0)
    v3 = g.math('MULTIPLY', v0, v2)
    v4 = g.math('MULTIPLY', v1, v1)
    v5 = g.math('MULTIPLY', v4, v1)
    v6 = g.comb(v0, 0.0365463, 0.0)
    v7 = g.vmath('DOT_PRODUCT', (3.32707, 1, 0.0), v6)
    v8 = g.math('MULTIPLY', 9.04756, -1.0)
    v9 = g.comb(v8, 1, 0.0)
    v10 = g.comb(v0, 9.0632, 0.0)
    v11 = g.vmath('DOT_PRODUCT', v9, v10)
    v12 = g.comb(v7, v11, 0.0)
    v13 = g.math('MULTIPLY', 1.36772, -1.0)
    v14 = g.comb(3.59685, v13, 1)
    v15 = g.comb(v2, v3, 1)
    v16 = g.vmath('DOT_PRODUCT', v14, v15)
    v17 = g.math('MULTIPLY', 16.3174, -1.0)
    v18 = g.comb(v17, 1, 9.22949)
    v19 = g.comb(v2, 9.04401, v3)
    v20 = g.vmath('DOT_PRODUCT', v18, v19)
    v21 = g.math('MULTIPLY', 20.2123, -1.0)
    v22 = g.comb(1, 19.7886, v21)
    v23 = g.comb(5.56589, v2, v3)
    v24 = g.vmath('DOT_PRODUCT', v22, v23)
    v25 = g.comb(v16, v20, v24)
    v26 = g.comb(1, v1, 0.0)
    v27 = g.vmath('DOT_PRODUCT', v12, v26)
    v28 = g.comb(1, v1, v5)
    v29 = g.vmath('DOT_PRODUCT', v25, v28)
    v30 = g.math('DIVIDE', v27, v29)
    v31 = g.math('MULTIPLY', 1.28514, -1.0)
    v32 = g.comb(v31, 1, 0.0)
    v33 = g.comb(v0, 0.99044, 0.0)
    v34 = g.vmath('DOT_PRODUCT', v32, v33)
    v35 = g.math('MULTIPLY', 0.755907, -1.0)
    v36 = g.comb(1, v35, 0.0)
    v37 = g.comb(1.29678, v0, 0.0)
    v38 = g.vmath('DOT_PRODUCT', v36, v37)
    v39 = g.comb(v34, v38, 0.0)
    v40 = g.comb(v0, v3, 1)
    v41 = g.vmath('DOT_PRODUCT', (2.92338, 59.4188, 1), v40)
    v42 = g.math('MULTIPLY', 27.0302, -1.0)
    v43 = g.comb(1, v42, 222.592)
    v44 = g.comb(20.3225, v0, v3)
    v45 = g.vmath('DOT_PRODUCT', v43, v44)
    v46 = g.comb(v0, v3, 121.563)
    v47 = g.vmath('DOT_PRODUCT', (626.13, 316.627, 1), v46)
    v48 = g.comb(v41, v45, v47)
    v49 = g.comb(1, v1, 0.0)
    v50 = g.vmath('DOT_PRODUCT', v39, v49)
    v51 = g.comb(1, v1, v5)
    v52 = g.vmath('DOT_PRODUCT', v48, v51)
    v53 = g.math('DIVIDE', v50, v52)
    g.out_('dfgX', v30, False)
    g.out_('dfgY', v53, False)


def build_RCE_EnvironmentWaterSubmersion():
    t = _tree('RCE_EnvironmentWaterSubmersion')
    g = G(t)
    v0 = g.inp('positionWS', True)
    v1 = g.inp('_RuriCharacterEnvironmentWater', True, (0.0, 0.0, 0.0))
    v2 = g.inp('_RuriCharacterEnvironmentWater_w', False, 0.0)
    v3 = g.sep(v1)
    v4 = g.inp('_CharacterParams10', True, (0.0, 0.0, 0.0))
    v5 = g.inp('_CharacterParams10_w', False, 0.0)
    v6 = g.sep(v4)
    v7 = g.mixf(v6[0], v3[0], v5)
    v8 = g.math('MULTIPLY', 0.20000000298023224, -1.0)
    v9 = g.sep(v0)
    v10 = g.math('SUBTRACT', v7, v9[1])
    v11 = g.math('SUBTRACT', v10, v8)
    v12 = g.math('SUBTRACT', 0.15000000596046448, v8)
    v13 = g.math('DIVIDE', v11, v12)
    v14 = g.clampn(v13)
    v15 = g.math('MULTIPLY', v14, v14)
    v16 = g.math('MULTIPLY', 2.0, v14)
    v17 = g.math('SUBTRACT', 3.0, v16)
    v18 = g.math('MULTIPLY', v15, v17)
    v19 = g.inp('_RuriCharacterEnvironmentEffect', True, (0.0, 0.0, 0.0))
    v20 = g.inp('_RuriCharacterEnvironmentEffect_w', False, 0.0)
    v21 = g.sep(v19)
    v22 = g.math('MULTIPLY', v18, v21[1])
    g.out_('ret', v22, False)


def build_RCE_EnvironmentWetness():
    t = _tree('RCE_EnvironmentWetness')
    g = G(t)
    v0 = g.inp('positionWS', True)
    v1 = g.inp('_RuriCharacterEnvironmentEffect', True, (0.0, 0.0, 0.0))
    v2 = g.inp('_RuriCharacterEnvironmentEffect_w', False, 0.0)
    v3 = g.sep(v1)
    v4 = g.inp('_RuriCharacterEnvironmentWater', True, (0.0, 0.0, 0.0))
    v5 = g.inp('_RuriCharacterEnvironmentWater_w', False, 0.0)
    v6 = g.inp('_CharacterParams10', True, (0.0, 0.0, 0.0))
    v7 = g.inp('_CharacterParams10_w', False, 0.0)
    v8 = g.group_named('RCE_EnvironmentWaterSubmersion', [('positionWS', v0), ('_RuriCharacterEnvironmentWater', v4), ('_RuriCharacterEnvironmentWater_w', v5), ('_CharacterParams10', v6), ('_CharacterParams10_w', v7), ('_RuriCharacterEnvironmentEffect', v1), ('_RuriCharacterEnvironmentEffect_w', v2)])
    v9 = g.math('MAXIMUM', v3[2], v8[0])
    v10 = g.math('MAXIMUM', v3[0], v9)
    g.out_('ret', v10, False)


def build_RCE_GetObjectFlatDir():
    t = _tree('RCE_GetObjectFlatDir')
    g = G(t)
    v0 = g.inp('positionWS', True)
    v1 = g.sep(v0)
    v2 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v3 = g.sep(v2)
    v4 = g.math('SUBTRACT', v1[0], v3[0])
    v5 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v6 = g.sep(v5)
    v7 = g.math('SUBTRACT', v1[2], v6[2])
    v8 = g.math('MULTIPLY', v4, v4)
    v9 = g.math('ADD', v8, 3.725290298461914E-09)
    v10 = g.math('MULTIPLY', v7, v7)
    v11 = g.math('ADD', v9, v10)
    v12 = g.math('INVERSE_SQRT', v11, 0.0)
    v13 = g.math('MULTIPLY', v4, v12)
    v14 = g.math('MULTIPLY', 6.103515625e-05, v12)
    v15 = g.math('MULTIPLY', v7, v12)
    v16 = g.comb(v13, v14, v15)
    g.out_('ret', v16, True)


def build_RCE_IBL_SplitSumCombine():
    t = _tree('RCE_IBL_SplitSumCombine')
    g = G(t)
    v0 = g.inp('cubeSample', True)
    v1 = g.inp('NdotV_spec', False)
    v2 = g.inp('roughness', False)
    v3 = g.inp('specRampEnv', True)
    v4 = g.inp('ambIntensity', False)
    v5 = g.inp('ambCol', True)
    v6 = g.group_named('RCE_EnvBRDF_Endfield', [('NdotV', v1), ('roughSq', v2)])
    v7 = g.bc(v6[0])
    v8 = g.vmath('MULTIPLY', v3, v7)
    v9 = g.bc(v6[1])
    v10 = g.vmath('ADD', v8, v9)
    v11 = g.math('ADD', v6[0], v6[1])
    v12 = g.math('SUBTRACT', 1, v11)
    v13 = g.math('MAXIMUM', v11, 1E-06)
    v14 = g.math('DIVIDE', v12, v13)
    v15 = g.vmath('MULTIPLY', v0, v10)
    v16 = g.bc(v14)
    v17 = g.vmath('MULTIPLY', v16, v3)
    v18 = g.vmath('ADD', (1, 1, 1), v17)
    v19 = g.vmath('MULTIPLY', v15, v18)
    v20 = g.bc(v4)
    v21 = g.vmath('MULTIPLY', v20, v19)
    v22 = g.vmath('MULTIPLY', v21, v5)
    g.out_('ret', v22, True)


def build_RCE_ResolveAdjustedLight():
    t = _tree('RCE_ResolveAdjustedLight')
    g = G(t)
    v0 = g.inp('mainLightDir', True)
    v1 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v2 = g.inp('_CharacterParams11_w', False, -0.4)
    v3 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v4 = g.inp('_CharacterParams1_w', False, 0.0)
    v5 = g.mixv(v4, v0, v1)
    v6 = g.sep(v5)
    v7 = g.math('MULTIPLY', v6[0], v6[0])
    v8 = g.math('MULTIPLY', v6[2], v6[2])
    v9 = g.math('ADD', v7, v8)
    v10 = g.math('ADD', v9, 3.725290298461914E-09)
    v11 = g.math('INVERSE_SQRT', v10, 0.0)
    v12 = g.math('MULTIPLY', v11, v6[0])
    v13 = g.math('MULTIPLY', v11, v6[2])
    g.out_('adjustedLightDir', v5, True)
    g.out_('adjXZ_x', v12, False)
    g.out_('adjXZ_z', v13, False)
    g.out_('adjXZLen', v11, False)


def build_RCE_Shell_AOFromNormalZ():
    t = _tree('RCE_Shell_AOFromNormalZ')
    g = G(t)
    v0 = g.inp('shellIdx', False)
    v1 = g.inp('nrmZ_raw', False)
    v2 = g.math('MULTIPLY', v1, 2)
    v3 = g.math('MINIMUM', v2, 1)
    v4 = g.math('MULTIPLY', v3, v3)
    v5 = g.inp('_FurAO', False, 1.0)
    v6 = g.math('MULTIPLY', v4, v5)
    v7 = g.math('SUBTRACT', 1, v6)
    v8 = g.math('MULTIPLY', v0, v7)
    v9 = g.math('MULTIPLY', v4, v5)
    v10 = g.math('ADD', v8, v9)
    g.out_('ret', v10, False)


def build_RCE_Shell_TransmittedNdotL_Endfield():
    t = _tree('RCE_Shell_TransmittedNdotL_Endfield')
    g = G(t)
    v0 = g.inp('furSample', False)
    v1 = g.inp('shellIdx', False)
    v2 = g.inp('geomNdotL', False)
    v3 = g.inp('camLightDot', False)
    v4 = g.math('SUBTRACT', 1, v0)
    v5 = g.math('MULTIPLY', v4, 1.4286)
    v6 = g.clampn(v5)
    v7 = g.math('MULTIPLY', v6, v6)
    v8 = g.math('MULTIPLY', 2, v6)
    v9 = g.math('SUBTRACT', 3, v8)
    v10 = g.math('MULTIPLY', v7, v9)
    v11 = g.math('MULTIPLY', v10, v3)
    v12 = g.inp('_FurNoise', False, 0.0)
    v13 = g.math('MULTIPLY', v11, v12)
    v14 = g.inp('_FurTTIntensity', False, 0.5)
    v15 = g.math('SUBTRACT', 1.15, v14)
    v16 = g.math('MULTIPLY', v13, v15)
    v17 = g.math('ADD', v16, v14)
    v18 = g.math('MULTIPLY', v17, v1)
    v19 = g.math('ADD', v18, v2)
    v20 = g.math('MULTIPLY', 1, -1.0)
    v21 = g.clampn(v19, v20, 1)
    g.out_('ret', v21, False)


def build_RCE_Subsurf_EdgeGate():
    t = _tree('RCE_Subsurf_EdgeGate')
    g = G(t)
    v0 = g.inp('NdotV', False)
    v1 = g.inp('range', False)
    v2 = g.math('ABSOLUTE', v0, 0.0)
    v3 = g.math('MULTIPLY', v2, -1.0)
    v4 = g.math('ADD', v3, v1)
    v5 = g.math('MULTIPLY', v4, 5)
    v6 = g.clampn(v5)
    v7 = g.math('MULTIPLY', v6, v6)
    v8 = g.math('MULTIPLY', 2, v6)
    v9 = g.math('SUBTRACT', 3, v8)
    v10 = g.math('MULTIPLY', v7, v9)
    g.out_('ret', v10, False)


def build_RCE_BRDF_SubsurfaceSpec_Endfield():
    t = _tree('RCE_BRDF_SubsurfaceSpec_Endfield')
    g = G(t)
    v0 = g.inp('N', True)
    v1 = g.inp('V', True)
    v2 = g.inp('adjXZ_x', False)
    v3 = g.inp('adjXZ_z', False)
    v4 = g.inp('adjXZLen', False)
    v5 = g.inp('camLightFacing', False)
    v6 = g.inp('mask', False)
    v7 = g.inp('diffColorLum', False)
    v8 = g.inp('diffColor', True)
    v9 = g.inp('subsurfLight', True)
    v10 = g.math('MULTIPLY', v4, 6.103515625e-05)
    v11 = g.comb(v2, v10, v3)
    v12 = g.vmath('DOT_PRODUCT', v11, v0)
    v13 = g.math('ADD', 0.5, v12)
    v14 = g.math('MULTIPLY', 0.5, v12)
    v15 = g.math('MULTIPLY', v14, v12)
    v16 = g.math('SUBTRACT', v13, v15)
    v17 = g.clampn(v16)
    v18 = g.vmath('DOT_PRODUCT', v1, v0)
    v19 = g.group_named('RCE_Subsurf_EdgeGate', [('NdotV', v18), ('range', 0.4)])
    v20 = g.math('SUBTRACT', 0.1, v7)
    v21 = g.math('MULTIPLY', v20, 16.666)
    v22 = g.clampn(v21)
    v23 = g.math('MULTIPLY', v22, v22)
    v24 = g.math('MULTIPLY', 2, v22)
    v25 = g.math('SUBTRACT', 3, v24)
    v26 = g.math('MULTIPLY', v23, v25)
    v27 = g.math('MULTIPLY', v26, v6)
    v28 = g.math('MULTIPLY', v27, v19[0])
    v29 = g.math('MULTIPLY', v28, v5)
    v30 = g.math('MULTIPLY', v29, v17)
    v31 = g.bc(v30)
    v32 = g.vmath('MULTIPLY', v31, v9)
    v33 = g.vmath('MAXIMUM', v8, (0.15, 0.15, 0.15))
    v34 = g.vmath('MULTIPLY', v32, v33)
    g.out_('ret', v34, True)


def build_RCE_VFXColorAdjust():
    t = _tree('RCE_VFXColorAdjust')
    g = G(t)
    v0 = g.inp('litColor', True)
    v1 = g.inp('NdotV', False)
    v2 = g.inp('rimMod', False)
    v3 = g.vmath('DOT_PRODUCT', v0, (0.2126729, 0.7151522, 0.072175))
    v4 = g.inp('_ColorAdjustmentContrast', False, 1.0)
    v5 = g.sep(v0)
    v6 = g.inp('_ColorAdjustmentSaturation', False, 1.0)
    v7 = g.mixf(v6, v3, v5[0])
    v8 = g.math('SUBTRACT', v7, 0.5)
    v9 = g.math('MULTIPLY', v4, v8)
    v10 = g.math('ADD', v9, 0.5)
    v11 = g.comb(v10, 0.0, 0.0)
    v12 = g.mixf(v6, v3, v5[1])
    v13 = g.math('SUBTRACT', v12, 0.5)
    v14 = g.math('MULTIPLY', v4, v13)
    v15 = g.math('ADD', v14, 0.5)
    v16 = g.sep(v11)
    v17 = g.comb(v16[0], v15, v16[2])
    v18 = g.mixf(v6, v3, v5[2])
    v19 = g.math('SUBTRACT', v18, 0.5)
    v20 = g.math('MULTIPLY', v4, v19)
    v21 = g.math('ADD', v20, 0.5)
    v22 = g.sep(v17)
    v23 = g.comb(v22[0], v22[1], v21)
    v24 = g.inp('_ColorAdjustmentRimWidth', False, 0.35)
    v25 = g.math('SUBTRACT', v24, v1)
    v26 = g.math('MAXIMUM', v24, 1E-05)
    v27 = g.math('DIVIDE', v25, v26)
    v28 = g.clampn(v27)
    v29 = g.math('MULTIPLY', v28, v28)
    v30 = g.math('MULTIPLY', 2, v28)
    v31 = g.math('SUBTRACT', 3, v30)
    v32 = g.math('MULTIPLY', v29, v31)
    v33 = g.inp('_ColorAdjustmentBrightness', False, 1.0)
    v34 = g.bc(v33)
    v35 = g.vmath('MULTIPLY', v23, v34)
    v36 = g.inp('_ColorAdjustmentColorBlend', True, (1.0, 1.0, 1.0))
    v37 = g.inp('_ColorAdjustmentColorBlend_w', False, 0.0)
    v38 = g.mixv(v37, v35, v36)
    v39 = g.math('MULTIPLY', v2, v32)
    v40 = g.inp('_ColorAdjustmentRimColor', True, (1.0, 1.0, 1.0))
    v41 = g.inp('_ColorAdjustmentRimColor_w', False, 1.0)
    v42 = g.bc(v39)
    v43 = g.vmath('MULTIPLY', v42, v40)
    v44 = g.inp('_ColorAdjustmentRimIntensity', False, 4.0)
    v45 = g.bc(v44)
    v46 = g.vmath('MULTIPLY', v43, v45)
    v47 = g.vmath('ADD', v38, v46)
    g.out_('ret', v47, True)


def build_RCE_Z_Ruri_Endfield_Uber_Standard_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_Standard_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_pxAccum', True)
    v2 = g.inp('s_pxDxUV', True)
    v3 = g.inp('s_pxDyUV', True)
    v4 = g.inp('s_pxHit', False)
    v5 = g.inp('s_pxHitH', False)
    v6 = g.inp('s_pxLayerH', False)
    v7 = g.inp('s_pxPrevH', False)
    v8 = g.inp('s_pxPrevLayerH', False)
    v9 = g.inp('s_pxPrevOff', True)
    v10 = g.inp('s_pxi', False)
    v11 = g.math('ADD', g.inp('r_pxSteps', False), 1)
    v12 = g.math('LESS_THAN', v10, v11)
    v13 = g.math('MULTIPLY', v0, v12)
    v14 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v15 = g.vmath('ADD', g.inp('r_pxUV', True), v1)
    g.out_('F0_ParallaxTex_uv', v15, True)
    v16 = g.inp('F0_ParallaxTex', True, (1.0, 1.0, 1.0))
    v17 = g.inp('F0_ParallaxTex_alpha', False, 1.0)
    v18 = g.sep(v16)
    v19 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v20 = g.math('LESS_THAN', v6, v18[0])
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.mixf(v21, v5, v18[0])
    v23 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v24 = g.mixf(v23, v4, 1.0)
    v25 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v26 = g.mixf(v25, v13, 0.0)
    v27 = g.mixf(v20, v13, v26)
    v28 = g.mixf(v20, v4, v24)
    v29 = g.mixf(v20, v5, v22)
    v30 = g.mixf(v19, v13, v27)
    v31 = g.mixf(v19, v4, v28)
    v32 = g.mixf(v19, v5, v29)
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixv(v33, v9, v1)
    v35 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v36 = g.vmath('ADD', v1, g.inp('r_pxUVDelta', True))
    v37 = g.mixv(v35, v1, v36)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixf(v38, v7, v18[0])
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.mixf(v40, v8, v6)
    v42 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v43 = g.math('SUBTRACT', v6, g.inp('r_pxStepSz', False))
    v44 = g.mixf(v42, v6, v43)
    v45 = g.mixf(v13, v13, v30)
    v46 = g.mixv(v13, v1, v37)
    v47 = g.mixf(v13, v4, v31)
    v48 = g.mixf(v13, v5, v32)
    v49 = g.mixf(v13, v6, v44)
    v50 = g.mixf(v13, v7, v39)
    v51 = g.mixf(v13, v8, v41)
    v52 = g.mixv(v13, v9, v34)
    v53 = g.math('ADD', v10, 1)
    g.out_('o_Lloop0', v45, False)
    g.out_('o_pxAccum', v46, True)
    g.out_('o_pxDxUV', v2, True)
    g.out_('o_pxDyUV', v3, True)
    g.out_('o_pxHit', v47, False)
    g.out_('o_pxHitH', v48, False)
    g.out_('o_pxLayerH', v49, False)
    g.out_('o_pxPrevH', v50, False)
    g.out_('o_pxPrevLayerH', v51, False)
    g.out_('o_pxPrevOff', v52, True)
    g.out_('o_pxi', v53, False)


def build_RCE_Z_Ruri_Endfield_Uber_Standard_1():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_Standard_1')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_Face_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_Face_0')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_Eyes_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_Eyes_0')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_Hair_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_Hair_0')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_Fur_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_Fur_0')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_Eyebrow_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_Eyebrow_0')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_VFX_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_VFX_0')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_OverlayShadow_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_OverlayShadow_0')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_RCE_Z_Ruri_Endfield_Uber_LiquidAg_0():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_LiquidAg_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_pxAccum', True)
    v2 = g.inp('s_pxDxUV', True)
    v3 = g.inp('s_pxDyUV', True)
    v4 = g.inp('s_pxHit', False)
    v5 = g.inp('s_pxHitH', False)
    v6 = g.inp('s_pxLayerH', False)
    v7 = g.inp('s_pxPrevH', False)
    v8 = g.inp('s_pxPrevLayerH', False)
    v9 = g.inp('s_pxPrevOff', True)
    v10 = g.inp('s_pxi', False)
    v11 = g.math('ADD', g.inp('r_pxSteps', False), 1)
    v12 = g.math('LESS_THAN', v10, v11)
    v13 = g.math('MULTIPLY', v0, v12)
    v14 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v15 = g.vmath('ADD', g.inp('r_pxUV', True), v1)
    g.out_('F0_ParallaxTex_uv', v15, True)
    v16 = g.inp('F0_ParallaxTex', True, (1.0, 1.0, 1.0))
    v17 = g.inp('F0_ParallaxTex_alpha', False, 1.0)
    v18 = g.sep(v16)
    v19 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v20 = g.math('LESS_THAN', v6, v18[0])
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.mixf(v21, v5, v18[0])
    v23 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v24 = g.mixf(v23, v4, 1.0)
    v25 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v26 = g.mixf(v25, v13, 0.0)
    v27 = g.mixf(v20, v13, v26)
    v28 = g.mixf(v20, v4, v24)
    v29 = g.mixf(v20, v5, v22)
    v30 = g.mixf(v19, v13, v27)
    v31 = g.mixf(v19, v4, v28)
    v32 = g.mixf(v19, v5, v29)
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixv(v33, v9, v1)
    v35 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v36 = g.vmath('ADD', v1, g.inp('r_pxUVDelta', True))
    v37 = g.mixv(v35, v1, v36)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixf(v38, v7, v18[0])
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.mixf(v40, v8, v6)
    v42 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v43 = g.math('SUBTRACT', v6, g.inp('r_pxStepSz', False))
    v44 = g.mixf(v42, v6, v43)
    v45 = g.mixf(v13, v13, v30)
    v46 = g.mixv(v13, v1, v37)
    v47 = g.mixf(v13, v4, v31)
    v48 = g.mixf(v13, v5, v32)
    v49 = g.mixf(v13, v6, v44)
    v50 = g.mixf(v13, v7, v39)
    v51 = g.mixf(v13, v8, v41)
    v52 = g.mixv(v13, v9, v34)
    v53 = g.math('ADD', v10, 1)
    g.out_('o_Lloop0', v45, False)
    g.out_('o_pxAccum', v46, True)
    g.out_('o_pxDxUV', v2, True)
    g.out_('o_pxDyUV', v3, True)
    g.out_('o_pxHit', v47, False)
    g.out_('o_pxHitH', v48, False)
    g.out_('o_pxLayerH', v49, False)
    g.out_('o_pxPrevH', v50, False)
    g.out_('o_pxPrevLayerH', v51, False)
    g.out_('o_pxPrevOff', v52, True)
    g.out_('o_pxi', v53, False)


def build_RCE_Z_Ruri_Endfield_Uber_LiquidAg_1():
    t = _tree('RCE_Z_Ruri_Endfield_Uber_LiquidAg_1')
    g = G(t)
    v0 = g.inp('s_N', True)
    v1 = g.inp('s_Lloop0', False)
    v2 = g.inp('s_lightAccum', True)
    v3 = g.inp('s_lightIndex', False)
    v4 = g.inp('s_positionWS', True)
    v5 = g.math('LESS_THAN', v3, g.inp('r_pixelLightCount', False))
    v6 = g.math('MULTIPLY', v1, v5)
    v7 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v3, False)
    g.out_('C0_AdditionalLight_position', v4, True)
    v8 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v9 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v10 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v11 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v12 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.vmath('DOT_PRODUCT', v0, v8)
    v15 = g.math('MULTIPLY', v14, 0.5)
    v16 = g.math('ADD', v15, 0.5)
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v19 = g.math('MULTIPLY', v10, v11)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('MULTIPLY', g.inp('r_albedo', True), v9)
    v22 = g.math('MULTIPLY', v17, v17)
    v23 = g.math('MULTIPLY', v22, v19)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v21, v24)
    v26 = g.vmath('ADD', v2, v25)
    v27 = g.mixv(v20, v2, v26)
    v28 = g.mixv(v6, v2, v27)
    v29 = g.math('ADD', v3, 1.0)
    g.out_('o_N', v0, True)
    g.out_('o_Lloop0', v6, False)
    g.out_('o_lightAccum', v28, True)
    g.out_('o_lightIndex', v29, False)
    g.out_('o_positionWS', v4, True)


def build_Ruri_Endfield_Uber_Standard():
    t = _tree('Ruri Endfield Uber Standard')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.inp('_UseShadowLutTex', False, 0.0)
    v152 = g.sep(v150)
    v153 = g.math('MULTIPLY', v152[0], 12.92)
    v154 = g.math('POWER', v152[0], 0.4166667)
    v155 = g.math('MULTIPLY', 1.055, v154)
    v156 = g.math('SUBTRACT', v155, 0.055)
    v157 = g.math('LESS_THAN', v152[0], 0.0031308)
    v158 = g.math('SUBTRACT', 1.0, v157)
    v159 = g.mixf(v158, v153, v156)
    v160 = g.clampn(v159)
    v161 = g.math('MULTIPLY', v152[1], 12.92)
    v162 = g.math('POWER', v152[1], 0.4166667)
    v163 = g.math('MULTIPLY', 1.055, v162)
    v164 = g.math('SUBTRACT', v163, 0.055)
    v165 = g.math('LESS_THAN', v152[1], 0.0031308)
    v166 = g.math('SUBTRACT', 1.0, v165)
    v167 = g.mixf(v166, v161, v164)
    v168 = g.clampn(v167)
    v169 = g.math('MULTIPLY', v152[2], 12.92)
    v170 = g.math('POWER', v152[2], 0.4166667)
    v171 = g.math('MULTIPLY', 1.055, v170)
    v172 = g.math('SUBTRACT', v171, 0.055)
    v173 = g.math('LESS_THAN', v152[2], 0.0031308)
    v174 = g.math('SUBTRACT', 1.0, v173)
    v175 = g.mixf(v174, v169, v172)
    v176 = g.clampn(v175)
    v177 = g.math('MULTIPLY', v176, 31)
    v178 = g.math('FLOOR', v177, 0.0)
    v179 = g.math('MULTIPLY', v178, 0.03125)
    v180 = g.math('MULTIPLY', v160, 0.0302734375)
    v181 = g.math('ADD', v179, v180)
    v182 = g.math('ADD', v181, 0.00048828125)
    v183 = g.math('MULTIPLY', v168, 0.96875)
    v184 = g.math('ADD', v183, 0.015625)
    v185 = g.comb(v182, v184, 0.0)
    g.out_('F4_ShadowLutTex_uv', v185, True)
    v186 = g.inp('F4_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v187 = g.inp('F4_ShadowLutTex_alpha', False, 1.0)
    v188 = g.math('ADD', v182, 0.03125)
    v189 = g.comb(v188, v184, 0.0)
    g.out_('F5_ShadowLutTex_uv', v189, True)
    v190 = g.inp('F5_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v191 = g.inp('F5_ShadowLutTex_alpha', False, 1.0)
    v192 = g.math('MULTIPLY', v176, 31)
    v193 = g.math('SUBTRACT', v192, v178)
    v194 = g.mixv(v193, v186, v190)
    v195 = g.mixv(v151, (0.0, 0.0, 0.0), v194)
    v196 = g.mixf(v151, 0.0, 1.0)
    v197 = g.math('SUBTRACT', 1.0, v196)
    v198 = g.inp('_ShadowColorBrightness', False, 0.5)
    v199 = g.bc(v198)
    v200 = g.vmath('MULTIPLY', v150, v199)
    v201 = g.math('SUBTRACT', 1.0, v196)
    v202 = g.vmath('DOT_PRODUCT', v200, (0.2126729, 0.7151522, 0.072175))
    v203 = g.math('SUBTRACT', 1.0, v196)
    v204 = g.inp('_ShadowColorSaturation', False, 1.0)
    v205 = g.bc(v202)
    v206 = g.vmath('SUBTRACT', v200, v205)
    v207 = g.bc(v204)
    v208 = g.vmath('MULTIPLY', v207, v206)
    v209 = g.bc(v202)
    v210 = g.vmath('ADD', v208, v209)
    v211 = g.mixv(v203, v195, v210)
    v212 = g.mixf(v203, v196, 1.0)
    v213 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v214 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v215 = g.sep(v213)
    v216 = g.math('MULTIPLY', v215[0], v214)
    v217 = g.math('MULTIPLY', v216, 2)
    v218 = g.math('SUBTRACT', v217, 1)
    v219 = g.math('MULTIPLY', v218, v27)
    v220 = g.math('MULTIPLY', v215[1], 2)
    v221 = g.math('SUBTRACT', v220, 1)
    v222 = g.math('MULTIPLY', v221, v27)
    v223 = g.math('MULTIPLY', v219, v219)
    v224 = g.math('MULTIPLY', v222, v222)
    v225 = g.math('ADD', v223, v224)
    v226 = g.clampn(v225)
    v227 = g.math('SUBTRACT', 1, v226)
    v228 = g.math('SQRT', v227, 0.0)
    v229 = g.math('MAXIMUM', v228, 1E-16)
    v230 = g.vmath('NORMALIZE', v16)
    v231 = g.vmath('NORMALIZE', v17)
    v232 = g.vmath('CROSS_PRODUCT', v230, v231)
    v233 = g.bc(v4)
    v234 = g.vmath('MULTIPLY', v232, v233)
    v235 = g.bc(v219)
    v236 = g.vmath('MULTIPLY', v235, v231)
    v237 = g.bc(v222)
    v238 = g.vmath('MULTIPLY', v237, v234)
    v239 = g.vmath('ADD', v236, v238)
    v240 = g.bc(v229)
    v241 = g.vmath('MULTIPLY', v240, v230)
    v242 = g.vmath('ADD', v239, v241)
    v243 = g.vmath('NORMALIZE', v242)
    v244 = g.bc(v98)
    v245 = g.vmath('MULTIPLY', v244, v243)
    v246 = g.mixv(v20, (0.0, 0.0, 0.0), v245)
    v247 = g.mixf(v20, 0.0, 1.0)
    v248 = g.math('SUBTRACT', 1.0, v247)
    v249 = g.vmath('NORMALIZE', v16)
    v250 = g.bc(v98)
    v251 = g.vmath('MULTIPLY', v250, v249)
    v252 = g.mixv(v248, v246, v251)
    v253 = g.mixf(v248, v247, 1.0)
    v254 = g.inp('_ClearCoat', False, 0.0)
    g.out_('F6_ClearCoatMask_uv', v0, True)
    v255 = g.inp('F6_ClearCoatMask', True, (1.0, 1.0, 1.0))
    v256 = g.inp('F6_ClearCoatMask_alpha', False, 1.0)
    v257 = g.sep(v255)
    v258 = g.vmath('NORMALIZE', v16)
    v259 = g.bc(v98)
    v260 = g.vmath('MULTIPLY', v259, v258)
    v261 = g.inp('_ClearCoatNormalMode', False, 0.0)
    v262 = g.mixv(v261, v260, v252)
    v263 = g.inp('_ClearCoatSmoothness', False, 0.95)
    v264 = g.math('SUBTRACT', 1, v263)
    v265 = g.math('MULTIPLY', v264, v264)
    v266 = g.math('MAXIMUM', v265, 0.0078125)
    v267 = g.inp('_ClearCoatMetallic', False, 0.0)
    v268 = g.math('MULTIPLY_ADD', v267, 0.96, 0.04)
    v269 = g.inp('_ClearCoatColor', True, (1.0, 1.0, 1.0))
    v270 = g.inp('_ClearCoatColor_w', False, 1.0)
    v271 = g.bc(v268)
    v272 = g.vmath('MULTIPLY', v271, v269)
    v273 = g.math('GREATER_THAN', v257[0], 0.001)
    v274 = g.mixf(v254, 0, v257[0])
    v275 = g.mixv(v254, v252, v262)
    v276 = g.mixf(v254, 1, v264)
    v277 = g.mixf(v254, 0.0078125, v266)
    v278 = g.mixv(v254, (0, 0, 0), v272)
    v279 = g.mixf(v254, 0.0, v273)
    v280 = g.inp('_SilkStockings', False, 0.0)
    v281 = g.inp('_RuriCharacterEnvironmentEffect', True, (0.0, 0.0, 0.0))
    v282 = g.inp('_RuriCharacterEnvironmentEffect_w', False, 0.0)
    v283 = g.inp('_RuriCharacterEnvironmentWater', True, (0.0, 0.0, 0.0))
    v284 = g.inp('_RuriCharacterEnvironmentWater_w', False, 0.0)
    v285 = g.inp('_CharacterParams10', True, (0.0, 0.0, 0.0))
    v286 = g.inp('_CharacterParams10_w', False, 0.0)
    v287 = g.group_named('RCE_EnvironmentWetness', [('positionWS', v15), ('_RuriCharacterEnvironmentEffect', v281), ('_RuriCharacterEnvironmentEffect_w', v282), ('_RuriCharacterEnvironmentWater', v283), ('_RuriCharacterEnvironmentWater_w', v284), ('_CharacterParams10', v285), ('_CharacterParams10_w', v286)])
    v288 = g.math('MULTIPLY', v100, v76)
    v289 = g.inp('_SilkStockingsSpecularInt', False, 5.0)
    v290 = g.inp('_SilkStockingsSpecularMinAtMinWetness', False, 0.0)
    v291 = g.mixf(v287[0], v290, 1)
    v292 = g.math('MULTIPLY', v289, v291)
    v293 = g.inp('_SilkStockingsAdvance', False, 0.0)
    v294 = g.math('GREATER_THAN', v293, 0.5)
    g.out_('F7_SilkStockingsMask_uv', v0, True)
    v295 = g.inp('F7_SilkStockingsMask', True, (1.0, 1.0, 1.0))
    v296 = g.inp('F7_SilkStockingsMask_alpha', False, 1.0)
    v297 = g.sep(v295)
    v298 = g.math('SUBTRACT', 1, v297[2])
    v299 = g.mixf(v287[0], v126, v298)
    v300 = g.math('MULTIPLY', v292, v297[0])
    v301 = g.math('MULTIPLY', 1, -1.0)
    v302 = g.math('MULTIPLY_ADD', v297[1], 2, v301)
    v303 = g.math('MULTIPLY', 0.949999988079071, -1.0)
    v304 = g.clampn(v302, v303, 0.949999988079071)
    v305 = g.mixf(v287[0], v288, v296)
    v306 = g.math('ADD', v305, 1)
    v307 = g.inp('_SilkStockingsColor', True, (0.0, 0.0, 0.0))
    v308 = g.inp('_SilkStockingsColor_w', False, 1.0)
    v309 = g.math('SUBTRACT', v306, v308)
    v310 = g.clampn(v309)
    v311 = g.inp('_SilkStockingsAnisoDirection', False, 0.0)
    v312 = g.math('MULTIPLY', v288, 0.5)
    v313 = g.clampn(v312)
    v314 = g.mixf(v313, v311, 0.5)
    v315 = g.math('MULTIPLY', v314, -1.0)
    v316 = g.math('ADD', v288, 1)
    v317 = g.math('SUBTRACT', v316, v308)
    v318 = g.clampn(v317)
    v319 = g.mixf(v294, v292, v300)
    v320 = g.mixf(v294, v315, v304)
    v321 = g.mixf(v294, v318, v310)
    v322 = g.mixf(v294, v126, v299)
    v323 = g.inp('_SilkStockingsDryColor', True, (1.0, 1.0, 1.0))
    v324 = g.inp('_SilkStockingsDryColor_w', False, 1.0)
    v325 = g.inp('_SilkStockingsWetColor', True, (1.0, 1.0, 1.0))
    v326 = g.inp('_SilkStockingsWetColor_w', False, 1.0)
    v327 = g.mixv(v287[0], v323, v325)
    v328 = g.inp('_SilkStockingsMinAffect', False, 0.05)
    v329 = g.inp('_SilkStockingsMaxAffect', False, 0.9)
    v330 = g.vmath('DOT_PRODUCT', v252, v102)
    v331 = g.clampn(v330)
    v332 = g.math('SUBTRACT', 1.0499999523162842, v331)
    v333 = g.math('MULTIPLY', v321, 2)
    v334 = g.math('POWER', v332, v333)
    v335 = g.clampn(v334)
    v336 = g.mixf(v335, v328, v329)
    v337 = g.vmath('MULTIPLY', v150, v327)
    v338 = g.mixv(v336, v337, v307)
    v339 = g.vmath('MULTIPLY', v211, v327)
    v340 = g.mixv(v336, v339, v307)
    v341 = g.mixv(v280, v150, v338)
    v342 = g.mixf(v280, v126, v322)
    v343 = g.mixv(v280, v211, v340)
    v344 = g.mixf(v280, 0, v319)
    v345 = g.mixf(v280, 0, v320)
    v346 = g.mixf(v280, 0, v321)
    v347 = g.inp('_UseEmission', False, 1.0)
    g.out_('F8_EmissionMap_uv', v0, True)
    v348 = g.inp('F8_EmissionMap', True, (0.0, 0.0, 0.0))
    v349 = g.inp('F8_EmissionMap_alpha', False, 1.0)
    v350 = g.mixv(v347, (0, 0, 0), v348)
    v351 = g.inp('_UseParallax', False, 0.0)
    v352 = g.vmath('NORMALIZE', v16)
    v353 = g.vmath('NORMALIZE', v17)
    v354 = g.vmath('CROSS_PRODUCT', v352, v353)
    v355 = g.bc(v4)
    v356 = g.vmath('MULTIPLY', v354, v355)
    v357 = g.vmath('DOT_PRODUCT', v353, v102)
    v358 = g.vmath('DOT_PRODUCT', v356, v102)
    v359 = g.vmath('DOT_PRODUCT', v352, v102)
    v360 = g.comb(v357, v358, v359)
    v361 = g.vmath('DOT_PRODUCT', v360, v360)
    v362 = g.math('MAXIMUM', v361, 1.175E-38)
    v363 = g.math('INVERSE_SQRT', v362, 0.0)
    v364 = g.inp('_ParallaxTex_ST', True, (1.0, 1.0, 0.0))
    v365 = g.inp('_ParallaxTex_ST_w', False, 0.0)
    v366 = g.sep(v364)
    v367 = g.comb(v366[0], v366[1], 0.0)
    v368 = g.vmath('MULTIPLY', v0, v367)
    v369 = g.comb(v366[2], v365, 0.0)
    v370 = g.vmath('ADD', v368, v369)
    v371 = g.inp('_ParallaxMarchNum', False, 3.0)
    v372 = g.math('MINIMUM', 20, v371)
    v373 = g.math('DIVIDE', 1, v372)
    v374 = g.sep(v360)
    v375 = g.math('MULTIPLY', v363, v374[2])
    v376 = g.math('MAXIMUM', v375, 0.001)
    v377 = g.comb(v374[0], v374[1], 0.0)
    v378 = g.comb(v363, v363, 0.0)
    v379 = g.vmath('MULTIPLY', v378, v377)
    v380 = g.comb(v376, v376, 0.0)
    v381 = g.vmath('DIVIDE', v379, v380)
    v382 = g.inp('_ParallaxScale', False, 0.5)
    v383 = g.math('MULTIPLY', v382, -1.0)
    v384 = g.comb(v383, v383, 0.0)
    v385 = g.vmath('MULTIPLY', v381, v384)
    v386 = g.comb(v373, v373, 0.0)
    v387 = g.vmath('MULTIPLY', v386, v385)
    v388 = g.math('SUBTRACT', 1, v373)
    v389 = g.math('ADD', v372, 1)
    v390 = g.math('SUBTRACT', v389, 0)
    v391 = g.math('CEIL', v390, 0.0)
    v392 = g.math('MAXIMUM', v391, 0.0)
    g.out_('Z0_it', v392, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_pxAccum', v387, True)
    g.out_('Z0_s_pxDxUV', (0.0, 0.0, 0.0), True)
    g.out_('Z0_s_pxDyUV', (0.0, 0.0, 0.0), True)
    g.out_('Z0_s_pxHit', 0.0, False)
    g.out_('Z0_s_pxHitH', 0, False)
    g.out_('Z0_s_pxLayerH', v388, False)
    g.out_('Z0_s_pxPrevH', 0, False)
    g.out_('Z0_s_pxPrevLayerH', 1, False)
    g.out_('Z0_s_pxPrevOff', (0, 0, 0.0), True)
    g.out_('Z0_s_pxi', 0, False)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pxUV', v370, True)
    g.out_('Z0_r_pxSteps', v372, False)
    g.out_('Z0_r_pxStepSz', v373, False)
    g.out_('Z0_r_pxUVDelta', v387, True)
    v393 = g.inp('Z0_o_Lloop0', False)
    v394 = g.inp('Z0_o_pxAccum', True)
    v395 = g.inp('Z0_o_pxDxUV', True)
    v396 = g.inp('Z0_o_pxDyUV', True)
    v397 = g.inp('Z0_o_pxHit', False)
    v398 = g.inp('Z0_o_pxHitH', False)
    v399 = g.inp('Z0_o_pxLayerH', False)
    v400 = g.inp('Z0_o_pxPrevH', False)
    v401 = g.inp('Z0_o_pxPrevLayerH', False)
    v402 = g.inp('Z0_o_pxPrevOff', True)
    v403 = g.inp('Z0_o_pxi', False)
    v404 = g.math('SUBTRACT', 1.0, v397)
    v405 = g.mixf(v404, v398, v400)
    v406 = g.math('SUBTRACT', v400, v401)
    v407 = g.math('MULTIPLY', v401, -1.0)
    v408 = g.math('ADD', v407, v399)
    v409 = g.math('ADD', v408, v400)
    v410 = g.math('SUBTRACT', v409, v405)
    v411 = g.math('DIVIDE', v406, v410)
    v412 = g.comb(v411, v411, 0.0)
    v413 = g.vmath('MULTIPLY', v387, v412)
    v414 = g.vmath('ADD', v370, v413)
    v415 = g.vmath('ADD', v414, v402)
    g.out_('F9_ParallaxTex_uv', v415, True)
    v416 = g.inp('F9_ParallaxTex', True, (1.0, 1.0, 1.0))
    v417 = g.inp('F9_ParallaxTex_alpha', False, 1.0)
    v418 = g.sep(v416)
    v419 = g.mixf(v351, 0, v418[0])
    v420 = g.group_named('RCE_GetObjectFlatDir', [('positionWS', v15)])
    v421 = g.inp('_CharacterParams2', True, (0.7830188, 0.8293082, 1.0))
    v422 = g.inp('_CharacterParams2_w', False, 0.0)
    v423 = g.math('MULTIPLY', v129, 0.04)
    v424 = g.math('SUBTRACT', 1, v127)
    v425 = g.math('MULTIPLY', v424, 0.96)
    v426 = g.bc(v425)
    v427 = g.vmath('MULTIPLY', v426, v341)
    v428 = g.bc(v423)
    v429 = g.vmath('SUBTRACT', v341, v428)
    v430 = g.bc(v127)
    v431 = g.vmath('MULTIPLY', v430, v429)
    v432 = g.bc(v423)
    v433 = g.vmath('ADD', v431, v432)
    v434 = g.bc(v425)
    v435 = g.vmath('MULTIPLY', v434, v343)
    v436 = g.math('MULTIPLY', v342, v342)
    v437 = g.math('MAXIMUM', v436, 0.0078125)
    v438 = g.inp('_CharacterParams5', True, (1.0, 1.0, 1.0))
    v439 = g.inp('_CharacterParams5_w', False, 1.0)
    v440 = g.sep(v110)
    v441 = g.mixv(v440[1], v131, v438)
    v442 = g.vmath('DOT_PRODUCT', v252, v140[0])
    v443 = g.math('MULTIPLY', 0.5, v442)
    v444 = g.math('MULTIPLY', v443, v442)
    v445 = g.math('SUBTRACT', 0.5, v444)
    v446 = g.math('SUBTRACT', 1, v440[0])
    v447 = g.math('MULTIPLY', v142, v141[1])
    v448 = g.math('MULTIPLY', v446, v447)
    v449 = g.math('MULTIPLY', v448, v445)
    v450 = g.math('ADD', v449, v442)
    v451 = g.inp('_UseDiffRampMap', False, 0.0)
    v452 = g.math('SUBTRACT', 1.0, v451)
    v453 = g.math('MULTIPLY', v450, 0.5)
    v454 = g.math('ADD', v453, 0.5)
    v455 = g.clampn(v454)
    v456 = g.mixf(v452, 0.0, 0)
    v457 = g.mixf(v452, 0.0, 0)
    v458 = g.mixv(v452, (0.0, 0.0, 0.0), (1, 1, 1))
    v459 = g.mixf(v452, 0.0, v455)
    v460 = g.mixf(v452, 0.0, 1.0)
    v461 = g.math('SUBTRACT', 1.0, v460)
    v462 = g.math('MULTIPLY', v137, v440[0])
    v463 = g.math('ADD', v462, v450)
    v464 = g.math('MULTIPLY', 1, -1.0)
    v465 = g.clampn(v463, v464, 1)
    v466 = g.math('MULTIPLY', v465, 0.5)
    v467 = g.math('ADD', v466, 0.5)
    v468 = g.math('SUBTRACT', 1.0, v460)
    v469 = g.comb(v467, 0.5, 0.0)
    g.out_('F10_DiffRampMap_uv', v469, True)
    v470 = g.inp('F10_DiffRampMap', True, (1.0, 1.0, 1.0))
    v471 = g.inp('F10_DiffRampMap_alpha', False, 1.0)
    v472 = g.math('SUBTRACT', 1.0, v460)
    v473 = g.sep(v470)
    v474 = g.math('MAXIMUM', v473[1], v473[2])
    v475 = g.math('MAXIMUM', v473[0], v474)
    v476 = g.math('MINIMUM', v473[1], v473[2])
    v477 = g.math('MINIMUM', v473[0], v476)
    v478 = g.math('SUBTRACT', v475, v477)
    v479 = g.mixf(v472, v456, v478)
    v480 = g.math('SUBTRACT', 1.0, v460)
    v481 = g.vmath('DOT_PRODUCT', v252, v109)
    v482 = g.math('MULTIPLY', v481, 0.5)
    v483 = g.math('ADD', v482, 0.5)
    v484 = g.math('SUBTRACT', 1.0, v460)
    v485 = g.comb(v483, 0.5, 0.0)
    g.out_('F11_DiffRampMap_uv', v485, True)
    v486 = g.inp('F11_DiffRampMap', True, (1.0, 1.0, 1.0))
    v487 = g.inp('F11_DiffRampMap_alpha', False, 1.0)
    v488 = g.mixf(v484, v457, v487)
    v489 = g.math('SUBTRACT', 1.0, v460)
    v490 = g.mixv(v489, v458, v470)
    v491 = g.mixf(v489, v459, v471)
    v492 = g.mixf(v489, v460, 1.0)
    v493 = g.math('SUBTRACT', v135, 0)
    v494 = g.math('DIVIDE', v493, 1)
    v495 = g.clampn(v494)
    v496 = g.math('MULTIPLY', v495, v495)
    v497 = g.math('MULTIPLY', 2.0, v495)
    v498 = g.math('SUBTRACT', 3.0, v497)
    v499 = g.math('MULTIPLY', v496, v498)
    v500 = g.sep(v138)
    v501 = g.mixf(v500[2], v499, 1)
    v502 = g.math('MINIMUM', v491, v128)
    v503 = g.math('MULTIPLY', v502, v501)
    v504 = g.math('MULTIPLY', v488, v128)
    v505 = g.math('ADD', v504, v491)
    v506 = g.clampn(v505)
    v507 = g.inp('_CharacterParams0', True, (0.0, 0.9, 0.8))
    v508 = g.inp('_CharacterParams0_w', False, 0.8)
    v509 = g.sep(v507)
    v510 = g.bc(v509[2])
    v511 = g.vmath('MULTIPLY', v435, v510)
    v512 = g.vmath('DOT_PRODUCT', v427, (0.2126729, 0.7151522, 0.072175))
    v513 = g.clampn(v116[0], 0, 1.5)
    v514 = g.math('SUBTRACT', 1, v479)
    v515 = g.inp('_CharacterParams6', True, (0.0, 1.0, 4.371139E-08))
    v516 = g.inp('_CharacterParams6_w', False, 0.0)
    v517 = g.inp('_CharacterParams7', True, (0.15, 1.5, 0.5))
    v518 = g.inp('_CharacterParams7_w', False, 0.0)
    v519 = g.group_named('RCE_ComputeNPRDiffuse', [('hemisphereN', v252), ('ambCol', v421), ('brightness', v513), ('blendedLightCol', v441), ('blendedLightInt', 1), ('minShadow', v503), ('combWeight', v506), ('albScaled', v511), ('diffColor', v427), ('rampCol', v490), ('rampChroma', v479), ('rampChromaInv', v514), ('_CharacterParams6', v515), ('_CharacterParams6_w', v516), ('_CharacterParams7', v517), ('_CharacterParams7_w', v518), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139), ('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_CharacterParams0', v507), ('_CharacterParams0_w', v508)])
    v520 = g.math('MULTIPLY', v503, 0.5)
    v521 = g.math('ADD', v520, 0.5)
    v522 = g.math('MULTIPLY', v519[2], v521)
    v523 = g.math('MULTIPLY_ADD', v100, 0, 1)
    v524 = g.group_named('RCE_BRDF_GGX_Stylized_Endfield', [('N', v252), ('V', v102), ('adjustedLightDir', v140[0]), ('camFwd', v109), ('roughness', v437)])
    v525 = g.math('MULTIPLY', v437, v437)
    v526 = g.math('MULTIPLY', v524[1], 0.5)
    v527 = g.math('MULTIPLY', v524[2], 2)
    v528 = g.math('ADD', v527, v437)
    v529 = g.math('ADD', v528, 0.0001)
    v530 = g.math('DIVIDE', v526, v529)
    v531 = g.math('SUBTRACT', v530, 6.103515625e-05)
    v532 = g.clampn(v531, 0, 20)
    v533 = g.inp('_SilkStockingsSpecularFalloff', False, 0.8)
    v534 = g.math('MULTIPLY', v346, v533)
    v535 = g.clampn(v534)
    v536 = g.math('SUBTRACT', 1, v535)
    v537 = g.math('MULTIPLY', v345, v536)
    v538 = g.math('MULTIPLY', v537, -1.0)
    v539 = g.inp('_SilkStockingsSpecularValue', False, 2.0)
    v540 = g.group_named('RCE_BRDF_AnisotropicNDF_SilkStockings_Endfield', [('N', v252), ('V', v102), ('H', v524[3]), ('tangentDir', v17), ('tangentSign', v4), ('alpha2', v437), ('ph_aniso', v538), ('_SilkStockingsSpecularValue', v539)])
    v541 = g.clampn(v540[0], 0, 20)
    v542 = g.math('MULTIPLY', v344, v541)
    v543 = g.math('ADD', v532, v542)
    v544 = g.mixf(v280, v532, v543)
    v545 = g.inp('_UseSpecRampMap', False, 0.0)
    v546 = g.math('ADD', v525, 0.0001)
    v547 = g.math('MULTIPLY', v524[1], v546)
    v548 = g.math('MULTIPLY', v524[2], v524[2])
    v549 = g.inp('_SpecRampIridescentMode', False, 0.0)
    v550 = g.mixf(v549, v547, v548)
    v551 = g.math('SUBTRACT', 1, v127)
    v552 = g.math('MULTIPLY', v551, v342)
    v553 = g.comb(v550, v552, 0.0)
    g.out_('F12_SpecRampMap_uv', v553, True)
    v554 = g.inp('F12_SpecRampMap', True, (1.0, 1.0, 1.0))
    v555 = g.inp('F12_SpecRampMap_alpha', False, 1.0)
    v556 = g.vmath('MULTIPLY', v433, v554)
    v557 = g.mixv(v549, v433, v556)
    v558 = g.mixv(v545, v433, v556)
    v559 = g.mixv(v545, v433, v557)
    v560 = g.math('MULTIPLY', v254, v279)
    v561 = g.vmath('DOT_PRODUCT', v275, v524[3])
    v562 = g.vmath('DOT_PRODUCT', v275, v102)
    v563 = g.clampn(v562)
    v564 = g.vmath('DOT_PRODUCT', v102, v524[3])
    v565 = g.clampn(v564)
    v566 = g.group_named('RCE_BRDF_ClearCoat_Direct_Burley', [('ccMask', v274), ('ccPercRough', v276), ('ccAlpha', v277), ('ccF0', v278), ('ccNdotH', v561), ('ccNdotV', v563), ('VdotH', v565)])
    v567 = g.mixv(v560, (0, 0, 0), v566[0])
    v568 = g.mixv(v560, (1, 1, 1), v566[1])
    v569 = g.mixv(v560, (1, 1, 1), v566[2])
    v570 = g.vmath('MULTIPLY', v519[1], v519[0])
    v571 = g.bc(v523)
    v572 = g.vmath('MULTIPLY', v570, v571)
    v573 = g.bc(v522)
    v574 = g.vmath('MULTIPLY', v573, v519[1])
    v575 = g.bc(v544)
    v576 = g.vmath('MULTIPLY', v575, v558)
    v577 = g.vmath('MULTIPLY', v574, v576)
    v578 = g.inp('_CharacterParams13', True, (0.0, 0.0, 0.0))
    v579 = g.inp('_CharacterParams13_w', False, 1.0)
    v580 = g.bc(v579)
    v581 = g.vmath('MULTIPLY', v577, v580)
    v582 = g.vmath('ADD', v572, v581)
    v583 = g.vmath('MULTIPLY', v519[1], v519[0])
    v584 = g.bc(v523)
    v585 = g.vmath('MULTIPLY', v583, v584)
    v586 = g.vmath('MULTIPLY', v585, v569)
    v587 = g.bc(v522)
    v588 = g.vmath('MULTIPLY', v587, v519[1])
    v589 = g.bc(v544)
    v590 = g.vmath('MULTIPLY', v589, v558)
    v591 = g.vmath('MULTIPLY', v590, v568)
    v592 = g.vmath('MULTIPLY', v591, v568)
    v593 = g.vmath('ADD', v592, v567)
    v594 = g.vmath('MULTIPLY', v588, v593)
    v595 = g.bc(v579)
    v596 = g.vmath('MULTIPLY', v594, v595)
    v597 = g.vmath('ADD', v586, v596)
    v598 = g.mixv(v254, v582, v597)
    v599 = g.vmath('DOT_PRODUCT', v598, (0.2126729, 0.7151522, 0.072175))
    v600 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v601 = g.inp('_CharacterParams9_w', False, 0.4)
    v602 = g.group_named('RCE_ComputeSkinDir', [('camFwd', v109), ('_CharacterParams9', v600), ('_CharacterParams9_w', v601)])
    v603 = g.vmath('DOT_PRODUCT', v420[0], v602[0])
    v604 = g.math('ADD', v603, 1)
    v605 = g.clampn(v604)
    v606 = g.math('MINIMUM', v128, v605)
    v607 = g.vmath('DOT_PRODUCT', v102, v252)
    v608 = g.group_named('RCE_ComputeSkinSmoothFalloff', [('NdotV', v607), ('_CharacterParams9', v600), ('_CharacterParams9_w', v601)])
    v609 = g.inp('_CharacterParams8', True, (0.0, 0.0, 0.0))
    v610 = g.inp('_CharacterParams8_w', False, 1.0)
    v611 = g.group_named('RCE_ComputeSkinSpec', [('skinDir', v602[0]), ('N', v252), ('diffColor', v427), ('skinShadow', v606), ('skinAmt', v608[0]), ('_CharacterParams8', v609), ('_CharacterParams8_w', v610), ('_CharacterParams9', v600), ('_CharacterParams9_w', v601)])
    v612 = g.math('SUBTRACT', 1, v440[0])
    v613 = g.math('MULTIPLY', v612, v142)
    v614 = g.vmath('MULTIPLY', v441, (1, 1, 1))
    v615 = g.group_named('RCE_BRDF_SubsurfaceSpec_Endfield', [('N', v252), ('V', v102), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2]), ('adjXZLen', v140[3]), ('camLightFacing', v613), ('mask', v128), ('diffColorLum', v512), ('diffColor', v427), ('subsurfLight', v614)])
    v616 = g.clampn(v116[0], 0.5, 1.5)
    v617 = g.math('MULTIPLY', v616, v508)
    v618 = g.math('MULTIPLY', v519[2], v617)
    v619 = g.vmath('NORMALIZE', v16)
    v620 = g.bc(v98)
    v621 = g.vmath('MULTIPLY', v620, v619)
    v622 = g.vmath('DOT_PRODUCT', v621, v102)
    v623 = g.clampn(v622)
    v624 = g.math('MULTIPLY', v342, v342)
    v625 = g.vmath('SCALE', v102, s=-1.0)
    v626 = g.vmath('DOT_PRODUCT', v621, v625)
    v627 = g.math('MULTIPLY', 2.0, v626)
    v628 = g.vmath('SCALE', v621, s=v627)
    v629 = g.vmath('SUBTRACT', v625, v628)
    v630 = g.math('MAXIMUM', v342, 0.001)
    v631 = g.math('LOGARITHM', v630, 2.0)
    v632 = g.math('MULTIPLY', v631, 1.2)
    v633 = g.math('ADD', v632, 5)
    v634 = g.u2b(v629)
    g.out_('F13_IBL_CharMaxCubemap_dir', v634, True)
    g.out_('F13_IBL_CharMaxCubemap_mip', v633, False)
    v635 = g.inp('F13_IBL_CharMaxCubemap', True, (0.2159, 0.2159, 0.2159))
    v636 = g.inp('F13_IBL_CharMaxCubemap_alpha', False, 1.0)
    v637 = g.group_named('RCE_IBL_SplitSumCombine', [('cubeSample', v635), ('NdotV_spec', v623), ('roughness', v624), ('specRampEnv', v559), ('ambIntensity', v618), ('ambCol', v421)])
    v638 = g.inp('_CubemapIntensity', False, 1.0)
    v639 = g.bc(v638)
    v640 = g.vmath('MULTIPLY', v637[0], v639)
    v641 = g.math('MULTIPLY', v254, v279)
    v642 = g.vmath('SCALE', v102, s=-1.0)
    v643 = g.vmath('DOT_PRODUCT', v275, v642)
    v644 = g.math('MULTIPLY', 2.0, v643)
    v645 = g.vmath('SCALE', v275, s=v644)
    v646 = g.vmath('SUBTRACT', v642, v645)
    v647 = g.math('MAXIMUM', v276, 0.001)
    v648 = g.math('LOGARITHM', v647, 2.0)
    v649 = g.math('MULTIPLY', v648, 1.2)
    v650 = g.math('ADD', v649, 5)
    v651 = g.u2b(v646)
    g.out_('F14_IBL_CharMaxCubemap_dir', v651, True)
    g.out_('F14_IBL_CharMaxCubemap_mip', v650, False)
    v652 = g.inp('F14_IBL_CharMaxCubemap', True, (0.2159, 0.2159, 0.2159))
    v653 = g.inp('F14_IBL_CharMaxCubemap_alpha', False, 1.0)
    v654 = g.vmath('DOT_PRODUCT', v275, v102)
    v655 = g.clampn(v654)
    v656 = g.group_named('RCE_EnvBRDF_Endfield', [('NdotV', v655), ('roughSq', v277)])
    v657 = g.bc(v656[0])
    v658 = g.vmath('MULTIPLY', v278, v657)
    v659 = g.bc(v656[1])
    v660 = g.vmath('ADD', v658, v659)
    v661 = g.math('ADD', v656[0], v656[1])
    v662 = g.math('SUBTRACT', 1, v661)
    v663 = g.math('MAXIMUM', v661, 1E-06)
    v664 = g.math('DIVIDE', v662, v663)
    v665 = g.vmath('MULTIPLY', v652, v660)
    v666 = g.bc(v664)
    v667 = g.vmath('MULTIPLY', v666, v278)
    v668 = g.vmath('ADD', (1, 1, 1), v667)
    v669 = g.vmath('MULTIPLY', v665, v668)
    v670 = g.bc(v274)
    v671 = g.vmath('MULTIPLY', v670, v669)
    v672 = g.bc(v638)
    v673 = g.vmath('MULTIPLY', v671, v672)
    v674 = g.vmath('ADD', v640, v673)
    v675 = g.mixv(v641, v640, v674)
    v676 = g.inp('_EmissionColor', True, (0.0, 0.0, 0.0))
    v677 = g.inp('_EmissionColor_w', False, 1.0)
    v678 = g.vmath('MULTIPLY', v350, v676)
    v679 = g.inp('_EmissionBrightness', False, 1.0)
    v680 = g.bc(v679)
    v681 = g.vmath('MULTIPLY', v678, v680)
    v682 = g.bc(v523)
    v683 = g.vmath('MULTIPLY', v681, v682)
    v684 = g.mixv(v347, (0, 0, 0), v683)
    v685 = g.math('MULTIPLY', v100, v419)
    v686 = g.inp('_ParallaxColor', True, (0.0, 0.0, 0.0))
    v687 = g.inp('_ParallaxColor_w', False, 1.0)
    v688 = g.bc(v685)
    v689 = g.vmath('MULTIPLY', v688, v686)
    v690 = g.bc(v523)
    v691 = g.vmath('MULTIPLY', v689, v690)
    v692 = g.vmath('ADD', v684, v691)
    v693 = g.mixv(v351, v684, v692)
    v694 = g.math('SUBTRACT', v599, 0.5)
    v695 = g.clampn(v694, 0, 0.5)
    v696 = g.math('MULTIPLY', v695, v695)
    v697 = g.math('ADD', v696, 1)
    v698 = g.bc(v599)
    v699 = g.vmath('SUBTRACT', v598, v698)
    v700 = g.bc(v697)
    v701 = g.vmath('MULTIPLY', v700, v699)
    v702 = g.bc(v599)
    v703 = g.vmath('ADD', v701, v702)
    v704 = g.vmath('ADD', v703, v611[0])
    v705 = g.vmath('ADD', v704, v615[0])
    v706 = g.vmath('ADD', v705, v693)
    v707 = g.vmath('ADD', v706, v675)
    v708 = g.inp('_EnableVFXColorAdjustment', False, 0.0)
    v709 = g.math('GREATER_THAN', v708, 0.5)
    v710 = g.inp('_ColorAdjustmentContrast', False, 1.0)
    v711 = g.inp('_ColorAdjustmentSaturation', False, 1.0)
    v712 = g.inp('_ColorAdjustmentRimWidth', False, 0.35)
    v713 = g.inp('_ColorAdjustmentBrightness', False, 1.0)
    v714 = g.inp('_ColorAdjustmentColorBlend', True, (1.0, 1.0, 1.0))
    v715 = g.inp('_ColorAdjustmentColorBlend_w', False, 0.0)
    v716 = g.inp('_ColorAdjustmentRimColor', True, (1.0, 1.0, 1.0))
    v717 = g.inp('_ColorAdjustmentRimColor_w', False, 1.0)
    v718 = g.inp('_ColorAdjustmentRimIntensity', False, 4.0)
    v719 = g.group_named('RCE_VFXColorAdjust', [('litColor', v707), ('NdotV', v524[2]), ('rimMod', 1), ('_ColorAdjustmentContrast', v710), ('_ColorAdjustmentSaturation', v711), ('_ColorAdjustmentRimWidth', v712), ('_ColorAdjustmentBrightness', v713), ('_ColorAdjustmentColorBlend', v714), ('_ColorAdjustmentColorBlend_w', v715), ('_ColorAdjustmentRimColor', v716), ('_ColorAdjustmentRimColor_w', v717), ('_ColorAdjustmentRimIntensity', v718)])
    v720 = g.mixv(v709, v707, v719[0])
    v721 = g.sep(v114)
    v722 = g.bc(v721[0])
    v723 = g.vmath('DIVIDE', v720, v722)
    v724 = g.math('COMPARE', v78, 1, 1e-05)
    v725 = g.mixf(v724, 1, v100)
    v726 = g.math('MULTIPLY', v83, v725)
    v727 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v728 = g.math('SUBTRACT', v727, 0)
    v729 = g.math('CEIL', v728, 0.0)
    v730 = g.math('MAXIMUM', v729, 0.0)
    g.out_('Z1_it', v730, False)
    g.out_('Z1_s_N', v59, True)
    g.out_('Z1_s_Lloop0', 1.0, False)
    g.out_('Z1_s_lightAccum', (0, 0, 0), True)
    g.out_('Z1_s_lightIndex', 0, False)
    g.out_('Z1_s_positionWS', v15, True)
    g.out_('Z1_r_albedo', v341, True)
    g.out_('Z1_r___done', 0.0, False)
    g.out_('Z1_r_pixelLightCount', v727, False)
    v731 = g.inp('Z1_o_N', True)
    v732 = g.inp('Z1_o_Lloop0', False)
    v733 = g.inp('Z1_o_lightAccum', True)
    v734 = g.inp('Z1_o_lightIndex', False)
    v735 = g.inp('Z1_o_positionWS', True)
    v736 = g.vmath('ADD', v723, v733)
    v737 = g.sep(v723)
    v738 = g.sep(v736)
    v739 = g.comb(v738[0], v738[1], v738[2])
    g.out_('ret_gBuffer0', v739, True)
    g.out_('ret_gBuffer0_w', v726, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v723, True)
    g.out_('ret_color_w', v726, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)


def build_Ruri_Endfield_Uber_Face():
    t = _tree('Ruri Endfield Uber Face')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.inp('_UseEmotionMap', False, 0.0)
    v152 = g.inp('_EmotionIndex', False, 0.0)
    v153 = g.math('MULTIPLY', 0.5, v152)
    v154 = g.sep(v0)
    v155 = g.math('MULTIPLY', v154[0], 0.5)
    v156 = g.math('FRACT', v153, 0.0)
    v157 = g.math('ADD', v155, v156)
    v158 = g.math('MULTIPLY', v154[1], 0.5)
    v159 = g.math('FLOOR', v153, 0.0)
    v160 = g.math('MULTIPLY', v159, 0.5)
    v161 = g.math('ADD', v158, v160)
    v162 = g.comb(v157, v161, 0.0)
    g.out_('F4_EmotionMap_uv', v162, True)
    v163 = g.inp('F4_EmotionMap', True, (0.0, 0.0, 0.0))
    v164 = g.inp('F4_EmotionMap_alpha', False, 1.0)
    v165 = g.inp('_EmotionBlend', False, 1.0)
    v166 = g.math('MULTIPLY', v164, v165)
    v167 = g.sep(v163)
    v168 = g.sep(v99)
    v169 = g.sep(v75)
    v170 = g.math('MULTIPLY', v168[0], v169[0])
    v171 = g.math('SUBTRACT', v167[0], v170)
    v172 = g.math('MULTIPLY', v168[0], v169[0])
    v173 = g.math('MULTIPLY_ADD', v166, v171, v172)
    v174 = g.comb(v173, 0.0, 0.0)
    v175 = g.math('MULTIPLY', v168[1], v169[1])
    v176 = g.math('SUBTRACT', v167[1], v175)
    v177 = g.math('MULTIPLY', v168[1], v169[1])
    v178 = g.math('MULTIPLY_ADD', v166, v176, v177)
    v179 = g.sep(v174)
    v180 = g.comb(v179[0], v178, v179[2])
    v181 = g.math('MULTIPLY', v168[2], v169[2])
    v182 = g.math('SUBTRACT', v167[2], v181)
    v183 = g.math('MULTIPLY', v168[2], v169[2])
    v184 = g.math('MULTIPLY_ADD', v166, v182, v183)
    v185 = g.sep(v180)
    v186 = g.comb(v185[0], v185[1], v184)
    v187 = g.vmath('MULTIPLY', v99, v75)
    v188 = g.mixv(v151, v187, v186)
    v189 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v190 = g.sep(v189)
    v191 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v192 = g.sep(v191)
    v193 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v194 = g.sep(v193)
    v195 = g.comb(v190[0], v192[1], v194[2])
    v196 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v197 = g.sep(v196)
    v198 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v199 = g.sep(v198)
    v200 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v201 = g.sep(v200)
    v202 = g.comb(v197[0], v199[1], v201[2])
    v203 = g.inp('_FBXRotationFix', False, 0.0)
    v204 = g.math('GREATER_THAN', v203, 0.5)
    v205 = g.vmath('SCALE', v195, s=-1.0)
    v206 = g.mixv(v204, v195, v202)
    v207 = g.mixv(v204, v202, v205)
    v208 = g.inp('_FaceForward', True, (0.0, 0.0, 1.0))
    v209 = g.inp('_FaceForward_w', False, 0.0)
    v210 = g.rig_basis(v208)
    v211 = g.inp('_FaceRight', True, (1.0, 0.0, 0.0))
    v212 = g.inp('_FaceRight_w', False, 0.0)
    v213 = g.rig_basis(v211)
    v214 = g.vmath('CROSS_PRODUCT', v210, v213)
    v215 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v216 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v217 = g.sep(v215)
    v218 = g.math('MULTIPLY', v217[0], v216)
    v219 = g.math('MULTIPLY', v218, 2)
    v220 = g.math('SUBTRACT', v219, 1)
    v221 = g.math('MULTIPLY', v220, v27)
    v222 = g.math('MULTIPLY', v217[1], 2)
    v223 = g.math('SUBTRACT', v222, 1)
    v224 = g.math('MULTIPLY', v223, v27)
    v225 = g.math('MULTIPLY', v221, v221)
    v226 = g.math('MULTIPLY', v224, v224)
    v227 = g.math('ADD', v225, v226)
    v228 = g.clampn(v227)
    v229 = g.math('SUBTRACT', 1, v228)
    v230 = g.math('SQRT', v229, 0.0)
    v231 = g.math('MAXIMUM', v230, 1E-16)
    v232 = g.vmath('NORMALIZE', v16)
    v233 = g.vmath('NORMALIZE', v17)
    v234 = g.vmath('CROSS_PRODUCT', v232, v233)
    v235 = g.bc(v4)
    v236 = g.vmath('MULTIPLY', v234, v235)
    v237 = g.bc(v221)
    v238 = g.vmath('MULTIPLY', v237, v233)
    v239 = g.bc(v224)
    v240 = g.vmath('MULTIPLY', v239, v236)
    v241 = g.vmath('ADD', v238, v240)
    v242 = g.bc(v231)
    v243 = g.vmath('MULTIPLY', v242, v232)
    v244 = g.vmath('ADD', v241, v243)
    v245 = g.vmath('NORMALIZE', v244)
    v246 = g.bc(v98)
    v247 = g.vmath('MULTIPLY', v246, v245)
    v248 = g.mixv(v20, (0.0, 0.0, 0.0), v247)
    v249 = g.mixf(v20, 0.0, 1.0)
    v250 = g.math('SUBTRACT', 1.0, v249)
    v251 = g.vmath('NORMALIZE', v16)
    v252 = g.bc(v98)
    v253 = g.vmath('MULTIPLY', v252, v251)
    v254 = g.mixv(v250, v248, v253)
    v255 = g.mixf(v250, v249, 1.0)
    v256 = g.group_named('RCE_GetObjectFlatDir', [('positionWS', v15)])
    v257 = g.inp('_UseSDFLightmap', False, 0.0)
    g.out_('F5_SDFMask_uv', v0, True)
    v258 = g.inp('F5_SDFMask', True, (0.0, 0.0, 0.0))
    v259 = g.inp('F5_SDFMask_alpha', False, 1.0)
    v260 = g.mixv(v257, (1, 1, 0), v258)
    v261 = g.mixf(v257, 0, v259)
    v262 = g.vmath('DOT_PRODUCT', v109, v213)
    v263 = g.vmath('DOT_PRODUCT', v109, v214)
    v264 = g.vmath('DOT_PRODUCT', v109, v210)
    v265 = g.comb(v262, v263, v264)
    v266 = g.vmath('DOT_PRODUCT', v265, v265)
    v267 = g.math('MAXIMUM', v266, 1.175494E-38)
    v268 = g.math('INVERSE_SQRT', v267, 0.0)
    v269 = g.bc(v268)
    v270 = g.vmath('MULTIPLY', v265, v269)
    v271 = g.sep(v270)
    v272 = g.math('MULTIPLY', v271[0], v271[0])
    v273 = g.math('MULTIPLY', v271[2], v271[2])
    v274 = g.math('ADD', v272, v273)
    v275 = g.math('INVERSE_SQRT', v274, 0.0)
    v276 = g.sep(v254)
    v277 = g.comb(v276[0], 6.103515625e-05, v276[2])
    v278 = g.vmath('NORMALIZE', v277)
    v279 = g.sep(v260)
    v280 = g.mixv(v279[1], v256[0], v278)
    v281 = g.vmath('NORMALIZE', v280)
    v282 = g.mixv(v257, v278, v281)
    v283 = g.math('MULTIPLY', v271[2], v275)
    v284 = g.math('ADD', v283, 0.5)
    v285 = g.clampn(v284)
    v286 = g.mixf(v279[1], v285, 1)
    v287 = g.math('MULTIPLY', v286, v279[0])
    v288 = g.mixf(v257, 1, v287)
    v289 = g.inp('_SkinRimOffScale', False, 0.5)
    v290 = g.inp('_FaceRimOffScale', False, 1.0)
    v291 = g.mixf(v279[2], v290, v289)
    v292 = g.mixf(v257, v289, v291)
    v293 = g.vmath('DOT_PRODUCT', v254, v102)
    v294 = g.clampn(v293)
    v295 = g.math('MULTIPLY', v294, 0.85)
    v296 = g.math('ADD', v295, 0.15)
    v297 = g.math('SUBTRACT', 1, v296)
    v298 = g.math('MULTIPLY', v297, v288)
    v299 = g.math('MULTIPLY', v298, v292)
    v300 = g.clampn(v299)
    v301 = g.inp('_SDFRimColor', True, (1.0, 1.0, 1.0))
    v302 = g.inp('_SDFRimColor_w', False, 1.0)
    v303 = g.bc(v300)
    v304 = g.vmath('MULTIPLY', v301, v303)
    v305 = g.math('SUBTRACT', 1, v300)
    v306 = g.bc(v305)
    v307 = g.vmath('ADD', v304, v306)
    v308 = g.vmath('MULTIPLY', v188, v307)
    v309 = g.math('MULTIPLY', v279[1], v125)
    v310 = g.mixf(v257, v125, v309)
    v311 = g.math('SUBTRACT', 1, v122)
    v312 = g.math('MULTIPLY', v311, v311)
    v313 = g.math('MAXIMUM', v312, 0.0078125)
    v314 = g.math('SUBTRACT', 1, v124)
    v315 = g.math('MULTIPLY', v314, 0.96)
    v316 = g.bc(v315)
    v317 = g.vmath('MULTIPLY', v316, v308)
    v318 = g.math('MULTIPLY', v310, 0.04)
    v319 = g.bc(v318)
    v320 = g.vmath('SUBTRACT', v308, v319)
    v321 = g.bc(v124)
    v322 = g.vmath('MULTIPLY', v321, v320)
    v323 = g.math('MULTIPLY', v310, 0.04)
    v324 = g.bc(v323)
    v325 = g.vmath('ADD', v322, v324)
    v326 = g.inp('_UseShadowLutTex', False, 0.0)
    v327 = g.sep(v188)
    v328 = g.math('MULTIPLY', v327[0], 12.92)
    v329 = g.math('POWER', v327[0], 0.4166667)
    v330 = g.math('MULTIPLY', 1.055, v329)
    v331 = g.math('SUBTRACT', v330, 0.055)
    v332 = g.math('LESS_THAN', v327[0], 0.0031308)
    v333 = g.math('SUBTRACT', 1.0, v332)
    v334 = g.mixf(v333, v328, v331)
    v335 = g.clampn(v334)
    v336 = g.math('MULTIPLY', v327[1], 12.92)
    v337 = g.math('POWER', v327[1], 0.4166667)
    v338 = g.math('MULTIPLY', 1.055, v337)
    v339 = g.math('SUBTRACT', v338, 0.055)
    v340 = g.math('LESS_THAN', v327[1], 0.0031308)
    v341 = g.math('SUBTRACT', 1.0, v340)
    v342 = g.mixf(v341, v336, v339)
    v343 = g.clampn(v342)
    v344 = g.math('MULTIPLY', v327[2], 12.92)
    v345 = g.math('POWER', v327[2], 0.4166667)
    v346 = g.math('MULTIPLY', 1.055, v345)
    v347 = g.math('SUBTRACT', v346, 0.055)
    v348 = g.math('LESS_THAN', v327[2], 0.0031308)
    v349 = g.math('SUBTRACT', 1.0, v348)
    v350 = g.mixf(v349, v344, v347)
    v351 = g.clampn(v350)
    v352 = g.math('MULTIPLY', v351, 31)
    v353 = g.math('FLOOR', v352, 0.0)
    v354 = g.math('MULTIPLY', v353, 0.03125)
    v355 = g.math('MULTIPLY', v335, 0.0302734375)
    v356 = g.math('ADD', v354, v355)
    v357 = g.math('ADD', v356, 0.00048828125)
    v358 = g.math('MULTIPLY', v343, 0.96875)
    v359 = g.math('ADD', v358, 0.015625)
    v360 = g.comb(v357, v359, 0.0)
    g.out_('F6_ShadowLutTex_uv', v360, True)
    v361 = g.inp('F6_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v362 = g.inp('F6_ShadowLutTex_alpha', False, 1.0)
    v363 = g.math('ADD', v357, 0.03125)
    v364 = g.comb(v363, v359, 0.0)
    g.out_('F7_ShadowLutTex_uv', v364, True)
    v365 = g.inp('F7_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v366 = g.inp('F7_ShadowLutTex_alpha', False, 1.0)
    v367 = g.math('MULTIPLY', v351, 31)
    v368 = g.math('SUBTRACT', v367, v353)
    v369 = g.mixv(v368, v361, v365)
    v370 = g.mixv(v326, (0.0, 0.0, 0.0), v369)
    v371 = g.mixf(v326, 0.0, 1.0)
    v372 = g.math('SUBTRACT', 1.0, v371)
    v373 = g.inp('_ShadowColorBrightness', False, 0.5)
    v374 = g.bc(v373)
    v375 = g.vmath('MULTIPLY', v188, v374)
    v376 = g.math('SUBTRACT', 1.0, v371)
    v377 = g.vmath('DOT_PRODUCT', v375, (0.2126729, 0.7151522, 0.072175))
    v378 = g.math('SUBTRACT', 1.0, v371)
    v379 = g.inp('_ShadowColorSaturation', False, 1.0)
    v380 = g.bc(v377)
    v381 = g.vmath('SUBTRACT', v375, v380)
    v382 = g.bc(v379)
    v383 = g.vmath('MULTIPLY', v382, v381)
    v384 = g.bc(v377)
    v385 = g.vmath('ADD', v383, v384)
    v386 = g.mixv(v378, v370, v385)
    v387 = g.mixf(v378, v371, 1.0)
    v388 = g.bc(v315)
    v389 = g.vmath('MULTIPLY', v388, v386)
    v390 = g.math('MINIMUM', v135, 1)
    v391 = g.sep(v131)
    v392 = g.sep(v110)
    v393 = g.inp('_CharacterParams4', True, (1.0, 1.0, 1.0))
    v394 = g.inp('_CharacterParams4_w', False, 1.0)
    v395 = g.sep(v393)
    v396 = g.math('SUBTRACT', v395[0], v391[0])
    v397 = g.math('MULTIPLY', v392[1], v396)
    v398 = g.math('ADD', v391[0], v397)
    v399 = g.comb(v398, 0.0, 0.0)
    v400 = g.math('SUBTRACT', v395[1], v391[1])
    v401 = g.math('MULTIPLY', v392[1], v400)
    v402 = g.math('ADD', v391[1], v401)
    v403 = g.sep(v399)
    v404 = g.comb(v403[0], v402, v403[2])
    v405 = g.math('SUBTRACT', v395[2], v391[2])
    v406 = g.math('MULTIPLY', v392[1], v405)
    v407 = g.math('ADD', v391[2], v406)
    v408 = g.sep(v404)
    v409 = g.comb(v408[0], v408[1], v407)
    v410 = g.vmath('DOT_PRODUCT', v140[0], v213)
    v411 = g.vmath('DOT_PRODUCT', v140[0], v210)
    v412 = g.math('MULTIPLY', v410, v410)
    v413 = g.math('ADD', v412, 3.725290298461914E-09)
    v414 = g.math('MULTIPLY', v411, v411)
    v415 = g.math('ADD', v413, v414)
    v416 = g.math('INVERSE_SQRT', v415, 0.0)
    v417 = g.math('MULTIPLY', v416, v411)
    v418 = g.math('MULTIPLY', v416, v410)
    v419 = g.math('GREATER_THAN', v418, 0)
    v420 = g.mixf(v419, 0, 1)
    v421 = g.math('SUBTRACT', 1, v154[0])
    v422 = g.math('SUBTRACT', v154[0], v421)
    v423 = g.math('MULTIPLY_ADD', v420, v422, v421)
    v424 = g.comb(v423, v154[1], 0.0)
    g.out_('F8_SDFLightmap_uv', v424, True)
    v425 = g.inp('F8_SDFLightmap', True, (0.0, 0.0, 0.0))
    v426 = g.inp('F8_SDFLightmap_alpha', False, 1.0)
    v427 = g.sep(v425)
    v428 = g.math('ADD', v427[0], v427[1])
    v429 = g.math('MULTIPLY', 2, v427[2])
    v430 = g.math('SUBTRACT', 1, v429)
    v431 = g.math('MULTIPLY', 2, v427[2])
    v432 = g.math('SUBTRACT', v431, 1)
    v433 = g.math('SUBTRACT', v432, v430)
    v434 = g.math('MULTIPLY_ADD', v420, v433, v430)
    v435 = g.math('ABSOLUTE', v434, 0.0)
    v436 = g.math('SUBTRACT', 1, v435)
    v437 = g.comb(v434, 6.103515625e-05, v436)
    v438 = g.vmath('NORMALIZE', v437)
    v439 = g.sep(v438)
    v440 = g.bc(v439[0])
    v441 = g.vmath('MULTIPLY', v440, v213)
    v442 = g.bc(v439[1])
    v443 = g.vmath('MULTIPLY', v442, v214)
    v444 = g.vmath('ADD', v441, v443)
    v445 = g.bc(v439[2])
    v446 = g.vmath('MULTIPLY', v445, v210)
    v447 = g.vmath('ADD', v444, v446)
    v448 = g.vmath('NORMALIZE', v447)
    v449 = g.mixv(v279[1], v448, v254)
    v450 = g.vmath('NORMALIZE', v449)
    v451 = g.math('MULTIPLY', v417, -1.0)
    v452 = g.clampn(v451)
    v453 = g.math('MULTIPLY', v142, v452)
    v454 = g.math('SUBTRACT', 1, v392[0])
    v455 = g.math('MULTIPLY', v453, v454)
    v456 = g.math('MULTIPLY', v417, v417)
    v457 = g.math('SUBTRACT', 1, v456)
    v458 = g.math('MULTIPLY', 0.5, v457)
    v459 = g.math('MULTIPLY', v455, v458)
    v460 = g.math('ADD', v417, v459)
    v461 = g.math('MULTIPLY', v460, 0.5)
    v462 = g.math('SUBTRACT', 0.5, v461)
    v463 = g.clampn(v462, 0.001, 0.999)
    v464 = g.math('MULTIPLY', v428, 0.5)
    v465 = g.math('MULTIPLY', 2, v463)
    v466 = g.math('SUBTRACT', v465, 1)
    v467 = g.math('MAXIMUM', v466, 0)
    v468 = g.math('SUBTRACT', v464, v467)
    v469 = g.math('MULTIPLY', 2, v463)
    v470 = g.math('MINIMUM', v469, 1)
    v471 = g.math('MULTIPLY', 2, v463)
    v472 = g.math('SUBTRACT', v471, 1)
    v473 = g.math('MAXIMUM', v472, 0)
    v474 = g.math('SUBTRACT', v470, v473)
    v475 = g.math('DIVIDE', v468, v474)
    v476 = g.clampn(v475)
    v477 = g.math('MULTIPLY', v476, v476)
    v478 = g.math('MULTIPLY', 2, v476)
    v479 = g.math('SUBTRACT', 3, v478)
    v480 = g.math('MULTIPLY', v477, v479)
    v481 = g.math('CEIL', v461, 0.0)
    v482 = g.math('MULTIPLY', v481, v461)
    v483 = g.math('ADD', v480, v482)
    v484 = g.math('ABSOLUTE', v483, 0.0)
    v485 = g.math('MULTIPLY', v484, 2)
    v486 = g.math('SUBTRACT', v485, 1)
    v487 = g.mixv(v257, v254, v450)
    v488 = g.mixf(v257, 0, v428)
    v489 = g.mixf(v257, 0, v486)
    v490 = g.vmath('DOT_PRODUCT', v254, v140[0])
    v491 = g.math('MULTIPLY', v137, v392[0])
    v492 = g.math('ADD', v491, v490)
    v493 = g.math('MULTIPLY', 1, -1.0)
    v494 = g.clampn(v492, v493, 1)
    v495 = g.math('MULTIPLY', v494, 0.5)
    v496 = g.math('ADD', v495, 0.5)
    v497 = g.mixf(v279[1], v489, v494)
    v498 = g.math('MULTIPLY', v497, 0.5)
    v499 = g.math('ADD', v498, 0.5)
    v500 = g.mixf(v257, v496, v499)
    v501 = g.inp('_UseDiffRampMap', False, 0.0)
    v502 = g.comb(v500, 0.5, 0.0)
    g.out_('F9_DiffRampMap_uv', v502, True)
    v503 = g.inp('F9_DiffRampMap', True, (1.0, 1.0, 1.0))
    v504 = g.inp('F9_DiffRampMap_alpha', False, 1.0)
    v505 = g.sep(v503)
    v506 = g.math('MAXIMUM', v505[1], v505[2])
    v507 = g.math('MAXIMUM', v505[0], v506)
    v508 = g.math('MINIMUM', v505[1], v505[2])
    v509 = g.math('MINIMUM', v505[0], v508)
    v510 = g.math('SUBTRACT', v507, v509)
    v511 = g.math('SUBTRACT', 1, v510)
    v512 = g.mixv(v501, (1, 1, 1), v503)
    v513 = g.mixf(v501, 1, v504)
    v514 = g.mixf(v501, 0, v510)
    v515 = g.mixf(v501, 1, v511)
    v516 = g.math('SUBTRACT', v390, 0)
    v517 = g.math('DIVIDE', v516, 1)
    v518 = g.clampn(v517)
    v519 = g.math('MULTIPLY', v518, v518)
    v520 = g.math('MULTIPLY', 2.0, v518)
    v521 = g.math('SUBTRACT', 3.0, v520)
    v522 = g.math('MULTIPLY', v519, v521)
    v523 = g.sep(v138)
    v524 = g.mixf(v523[2], v522, 1)
    v525 = g.mixf(v257, v524, 1)
    v526 = g.math('MINIMUM', v513, v100)
    v527 = g.math('MULTIPLY', v526, v525)
    v528 = g.math('ADD', v100, v513)
    v529 = g.clampn(v528)
    v530 = g.clampn(v116[0], 0, 1.5)
    v531 = g.inp('_CharacterParams0', True, (0.0, 0.9, 0.8))
    v532 = g.inp('_CharacterParams0_w', False, 0.8)
    v533 = g.sep(v531)
    v534 = g.bc(v533[2])
    v535 = g.vmath('MULTIPLY', v389, v534)
    v536 = g.inp('_CharacterParams3', True, (1.0, 0.78114647, 0.68490565))
    v537 = g.inp('_CharacterParams3_w', False, 0.0)
    v538 = g.inp('_CharacterParams6', True, (0.0, 1.0, 4.371139E-08))
    v539 = g.inp('_CharacterParams6_w', False, 0.0)
    v540 = g.inp('_CharacterParams7', True, (0.15, 1.5, 0.5))
    v541 = g.inp('_CharacterParams7_w', False, 0.0)
    v542 = g.group_named('RCE_ComputeNPRDiffuse', [('hemisphereN', v282), ('ambCol', v536), ('brightness', v530), ('blendedLightCol', v409), ('blendedLightInt', 1), ('minShadow', v527), ('combWeight', v529), ('albScaled', v535), ('diffColor', v317), ('rampCol', v512), ('rampChroma', v514), ('rampChromaInv', v515), ('_CharacterParams6', v538), ('_CharacterParams6_w', v539), ('_CharacterParams7', v540), ('_CharacterParams7_w', v541), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139), ('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_CharacterParams0', v531), ('_CharacterParams0_w', v532)])
    v543 = g.math('MULTIPLY', v527, 0.5)
    v544 = g.math('ADD', v543, 0.5)
    v545 = g.math('MULTIPLY', v542[2], v544)
    v546 = g.bc(v545)
    v547 = g.vmath('MULTIPLY', v546, v542[1])
    v548 = g.group_named('RCE_BRDF_GGX_Stylized_Endfield', [('N', v254), ('V', v102), ('adjustedLightDir', v140[0]), ('camFwd', v109), ('roughness', v313)])
    v549 = g.inp('_FaceHighlightMap', False, 0.0)
    v550 = g.vmath('DOT_PRODUCT', v102, v206)
    v551 = g.inp('_HighlightMapVector', True, (0.04, -0.01, 0.0))
    v552 = g.inp('_HighlightMapVector_w', False, 0.0)
    v553 = g.sep(v551)
    v554 = g.math('MULTIPLY', v550, v553[0])
    v555 = g.vmath('DOT_PRODUCT', v102, v207)
    v556 = g.math('MULTIPLY', v555, v553[1])
    v557 = g.math('ADD', v154[0], v554)
    v558 = g.math('ADD', v154[1], v556)
    v559 = g.comb(v557, v558, 0.0)
    g.out_('F10_HighlightMap_uv', v559, True)
    v560 = g.inp('F10_HighlightMap', True, (0.0, 0.0, 0.0))
    v561 = g.inp('F10_HighlightMap_alpha', False, 1.0)
    v562 = g.mixv(v549, (0, 0, 0), v560)
    v563 = g.vmath('MULTIPLY', v542[1], v542[0])
    v564 = g.bc(v548[0])
    v565 = g.vmath('MULTIPLY', v325, v564)
    v566 = g.inp('_CharacterParams13', True, (0.0, 0.0, 0.0))
    v567 = g.inp('_CharacterParams13_w', False, 1.0)
    v568 = g.bc(v567)
    v569 = g.vmath('MULTIPLY', v565, v568)
    v570 = g.vmath('ADD', v569, v562)
    v571 = g.vmath('MULTIPLY', v547, v570)
    v572 = g.vmath('ADD', v563, v571)
    v573 = g.vmath('DOT_PRODUCT', v572, (0.2126729, 0.7151522, 0.072175))
    v574 = g.math('SUBTRACT', v573, 0.5)
    v575 = g.clampn(v574, 0, 0.5)
    v576 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v577 = g.inp('_CharacterParams9_w', False, 0.4)
    v578 = g.group_named('RCE_ComputeSkinDir', [('camFwd', v109), ('_CharacterParams9', v576), ('_CharacterParams9_w', v577)])
    v579 = g.vmath('DOT_PRODUCT', v102, v487)
    v580 = g.group_named('RCE_ComputeSkinSmoothFalloff', [('NdotV', v579), ('_CharacterParams9', v576), ('_CharacterParams9_w', v577)])
    v581 = g.math('MULTIPLY', v271[2], v275)
    v582 = g.math('ABSOLUTE', v581, 0.0)
    v583 = g.math('SUBTRACT', v582, 0.9)
    v584 = g.math('MULTIPLY', v583, 10)
    v585 = g.clampn(v584)
    v586 = g.math('MULTIPLY', v585, v585)
    v587 = g.math('MULTIPLY', 2, v585)
    v588 = g.math('SUBTRACT', 3, v587)
    v589 = g.math('MULTIPLY', v586, v588)
    v590 = g.math('MULTIPLY', v577, 10)
    v591 = g.math('SUBTRACT', v590, 3)
    v592 = g.clampn(v591)
    v593 = g.vmath('DOT_PRODUCT', v109, v578[0])
    v594 = g.math('MULTIPLY', 0.01, -1.0)
    v595 = g.math('LESS_THAN', v593, v594)
    v596 = g.mixf(v595, 0, 1)
    v597 = g.math('MULTIPLY', v589, v580[0])
    v598 = g.math('MAXIMUM', v589, v596)
    v599 = g.math('MULTIPLY', v598, v261)
    v600 = g.mixf(v592, v597, v599)
    v601 = g.mixf(v257, v580[0], v600)
    v602 = g.vmath('DOT_PRODUCT', v256[0], v578[0])
    v603 = g.math('ADD', v602, 1)
    v604 = g.clampn(v603)
    v605 = g.math('MINIMUM', v100, v604)
    v606 = g.inp('_CharacterParams8', True, (0.0, 0.0, 0.0))
    v607 = g.inp('_CharacterParams8_w', False, 1.0)
    v608 = g.group_named('RCE_ComputeSkinSpec', [('skinDir', v578[0]), ('N', v487), ('diffColor', v317), ('skinShadow', v605), ('skinAmt', v601), ('_CharacterParams8', v606), ('_CharacterParams8_w', v607), ('_CharacterParams9', v576), ('_CharacterParams9_w', v577)])
    v609 = g.vmath('DOT_PRODUCT', v317, (0.2126729, 0.7151522, 0.072175))
    v610 = g.math('SUBTRACT', 1, v392[0])
    v611 = g.math('MULTIPLY', v610, v142)
    v612 = g.vmath('MULTIPLY', v409, (1, 1, 1))
    v613 = g.group_named('RCE_BRDF_SubsurfaceSpec_Endfield', [('N', v254), ('V', v102), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2]), ('adjXZLen', v140[3]), ('camLightFacing', v611), ('mask', v100), ('diffColorLum', v609), ('diffColor', v317), ('subsurfLight', v612)])
    v614 = g.inp('_CharacterParams15', True, (0.0, 0.0, 0.0))
    v615 = g.inp('_CharacterParams15_w', False, 0.0)
    v616 = g.sep(v614)
    v617 = g.math('MULTIPLY', 0.5, v616[2])
    v618 = g.math('SUBTRACT', 0.5, v617)
    v619 = g.clampn(v618, 0.001, 0.999)
    v620 = g.math('MULTIPLY', 2, v619)
    v621 = g.math('SUBTRACT', v620, 1)
    v622 = g.math('MAXIMUM', v621, 0)
    v623 = g.math('MULTIPLY', 2, v619)
    v624 = g.math('MINIMUM', v623, 1)
    v625 = g.math('MULTIPLY', v488, 0.5)
    v626 = g.math('SUBTRACT', v625, v622)
    v627 = g.math('SUBTRACT', v624, v622)
    v628 = g.math('DIVIDE', v626, v627)
    v629 = g.clampn(v628)
    v630 = g.math('MULTIPLY', v629, v629)
    v631 = g.math('MULTIPLY', 2, v629)
    v632 = g.math('SUBTRACT', 3, v631)
    v633 = g.math('MULTIPLY', v630, v632)
    v634 = g.math('CEIL', v617, 0.0)
    v635 = g.math('MULTIPLY', v634, v617)
    v636 = g.math('ADD', v633, v635)
    v637 = g.math('ABSOLUTE', v636, 0.0)
    v638 = g.math('MULTIPLY', v637, 2)
    v639 = g.math('SUBTRACT', v638, 0.5)
    v640 = g.clampn(v639)
    v641 = g.math('SUBTRACT', 1, v279[1])
    v642 = g.math('MULTIPLY', v640, v640)
    v643 = g.math('MULTIPLY', 2, v640)
    v644 = g.math('SUBTRACT', 3, v643)
    v645 = g.math('MULTIPLY', v642, v644)
    v646 = g.math('MULTIPLY', v641, v645)
    v647 = g.bc(v646)
    v648 = g.vmath('MULTIPLY', v317, v647)
    v649 = g.inp('_CharacterParams14', True, (0.0, 0.0, 0.0))
    v650 = g.inp('_CharacterParams14_w', False, 0.0)
    v651 = g.vmath('MULTIPLY', v648, v649)
    v652 = g.bc(v650)
    v653 = g.vmath('MULTIPLY', v651, v652)
    v654 = g.mixv(v257, (0, 0, 0), v653)
    v655 = g.math('MULTIPLY', v575, v575)
    v656 = g.math('ADD', v655, 1)
    v657 = g.bc(v573)
    v658 = g.vmath('SUBTRACT', v572, v657)
    v659 = g.bc(v656)
    v660 = g.vmath('MULTIPLY', v659, v658)
    v661 = g.bc(v573)
    v662 = g.vmath('ADD', v660, v661)
    v663 = g.vmath('ADD', v662, v608[0])
    v664 = g.vmath('ADD', v663, v613[0])
    v665 = g.vmath('ADD', v664, v654)
    v666 = g.inp('_EnableVFXColorAdjustment', False, 0.0)
    v667 = g.math('GREATER_THAN', v666, 0.5)
    v668 = g.inp('_ColorAdjustmentContrast', False, 1.0)
    v669 = g.inp('_ColorAdjustmentSaturation', False, 1.0)
    v670 = g.inp('_ColorAdjustmentRimWidth', False, 0.35)
    v671 = g.inp('_ColorAdjustmentBrightness', False, 1.0)
    v672 = g.inp('_ColorAdjustmentColorBlend', True, (1.0, 1.0, 1.0))
    v673 = g.inp('_ColorAdjustmentColorBlend_w', False, 0.0)
    v674 = g.inp('_ColorAdjustmentRimColor', True, (1.0, 1.0, 1.0))
    v675 = g.inp('_ColorAdjustmentRimColor_w', False, 1.0)
    v676 = g.inp('_ColorAdjustmentRimIntensity', False, 4.0)
    v677 = g.group_named('RCE_VFXColorAdjust', [('litColor', v665), ('NdotV', v294), ('rimMod', v288), ('_ColorAdjustmentContrast', v668), ('_ColorAdjustmentSaturation', v669), ('_ColorAdjustmentRimWidth', v670), ('_ColorAdjustmentBrightness', v671), ('_ColorAdjustmentColorBlend', v672), ('_ColorAdjustmentColorBlend_w', v673), ('_ColorAdjustmentRimColor', v674), ('_ColorAdjustmentRimColor_w', v675), ('_ColorAdjustmentRimIntensity', v676)])
    v678 = g.mixv(v667, v665, v677[0])
    v679 = g.sep(v114)
    v680 = g.bc(v679[0])
    v681 = g.vmath('DIVIDE', v678, v680)
    v682 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v683 = g.math('SUBTRACT', v682, 0)
    v684 = g.math('CEIL', v683, 0.0)
    v685 = g.math('MAXIMUM', v684, 0.0)
    g.out_('Z0_it', v685, False)
    g.out_('Z0_s_N', v59, True)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_lightAccum', (0, 0, 0), True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_positionWS', v15, True)
    g.out_('Z0_r_albedo', v150, True)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pixelLightCount', v682, False)
    v686 = g.inp('Z0_o_N', True)
    v687 = g.inp('Z0_o_Lloop0', False)
    v688 = g.inp('Z0_o_lightAccum', True)
    v689 = g.inp('Z0_o_lightIndex', False)
    v690 = g.inp('Z0_o_positionWS', True)
    v691 = g.vmath('ADD', v681, v688)
    v692 = g.sep(v681)
    v693 = g.sep(v691)
    v694 = g.comb(v693[0], v693[1], v693[2])
    g.out_('ret_gBuffer0', v694, True)
    g.out_('ret_gBuffer0_w', v83, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v681, True)
    g.out_('ret_color_w', 1, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)


def build_Ruri_Endfield_Uber_Eyes():
    t = _tree('Ruri Endfield Uber Eyes')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.vmath('DOT_PRODUCT', v16, v16)
    v152 = g.math('MAXIMUM', v151, 1.175494E-38)
    v153 = g.math('INVERSE_SQRT', v152, 0.0)
    v154 = g.bc(v153)
    v155 = g.vmath('MULTIPLY', v154, v16)
    v156 = g.bc(v98)
    v157 = g.vmath('MULTIPLY', v156, v155)
    v158 = g.vmath('CROSS_PRODUCT', v16, v17)
    v159 = g.bc(v4)
    v160 = g.vmath('MULTIPLY', v158, v159)
    v161 = g.vmath('FRACTION', v0)
    v162 = g.vmath('SUBTRACT', v161, (0.5, 0.5, 0.0))
    v163 = g.vmath('DOT_PRODUCT', v162, v162)
    v164 = g.math('LESS_THAN', v163, 0.25)
    v165 = g.math('SUBTRACT', 1.0, v164)
    v166 = g.mixf(v165, 0, 1)
    v167 = g.sep(v157)
    v168 = g.comb(v167[0], 6.103515625e-05, v167[2])
    v169 = g.vmath('NORMALIZE', v168)
    v170 = g.inp('_UseMatcap', False, 0.0)
    v171 = g.bc(v153)
    v172 = g.vmath('MULTIPLY', v171, v17)
    v173 = g.vmath('DOT_PRODUCT', v172, v102)
    v174 = g.vmath('CROSS_PRODUCT', v16, v17)
    v175 = g.bc(v4)
    v176 = g.vmath('MULTIPLY', v175, v174)
    v177 = g.bc(v153)
    v178 = g.vmath('MULTIPLY', v177, v176)
    v179 = g.vmath('DOT_PRODUCT', v178, v102)
    v180 = g.bc(v153)
    v181 = g.vmath('MULTIPLY', v180, v16)
    v182 = g.vmath('DOT_PRODUCT', v181, v102)
    v183 = g.math('MULTIPLY', v173, v173)
    v184 = g.math('MULTIPLY', v179, v179)
    v185 = g.math('ADD', v183, v184)
    v186 = g.math('MULTIPLY', v182, v182)
    v187 = g.math('ADD', v185, v186)
    v188 = g.math('MAXIMUM', v187, 1.175494E-38)
    v189 = g.math('INVERSE_SQRT', v188, 0.0)
    v190 = g.math('SUBTRACT', v163, 0.25)
    v191 = g.math('MULTIPLY', 5, -1.0)
    v192 = g.math('MULTIPLY', v190, v191)
    v193 = g.clampn(v192)
    v194 = g.math('MULTIPLY', v193, v193)
    v195 = g.math('MULTIPLY', 2, v193)
    v196 = g.math('SUBTRACT', 3, v195)
    v197 = g.math('MULTIPLY', v194, v196)
    v198 = g.sep(v0)
    v199 = g.math('MULTIPLY', v189, v173)
    v200 = g.inp('_ParallaxScale', False, 0.5)
    v201 = g.math('MULTIPLY', v199, v200)
    v202 = g.math('MULTIPLY', v201, v197)
    v203 = g.math('SUBTRACT', v198[0], v202)
    v204 = g.math('MULTIPLY', v189, v179)
    v205 = g.math('MULTIPLY', v204, v200)
    v206 = g.math('MULTIPLY', v205, 0.25)
    v207 = g.math('MULTIPLY', v206, v197)
    v208 = g.math('SUBTRACT', v198[1], v207)
    v209 = g.comb(v203, v208, 0.0)
    v210 = g.sep(v161)
    v211 = g.math('MULTIPLY', v210[0], 2)
    v212 = g.math('SUBTRACT', v211, 1)
    v213 = g.math('MULTIPLY', v210[1], 2)
    v214 = g.math('SUBTRACT', v213, 1)
    v215 = g.comb(v212, v214, 0.0)
    v216 = g.comb(v212, v214, 0.0)
    v217 = g.vmath('DOT_PRODUCT', v215, v216)
    v218 = g.math('SUBTRACT', 1, v217)
    v219 = g.clampn(v218)
    v220 = g.math('SQRT', v219, 0.0)
    v221 = g.math('MAXIMUM', v220, 1E-16)
    v222 = g.inp('_MatcapNormalScale', False, 1.0)
    v223 = g.math('MULTIPLY', v222, -1.0)
    v224 = g.math('MULTIPLY', v212, v223)
    v225 = g.math('MULTIPLY', v222, -1.0)
    v226 = g.math('MULTIPLY', v214, v225)
    v227 = g.math('SUBTRACT', v166, 1)
    v228 = g.math('MULTIPLY', 0.125, v227)
    v229 = g.math('MULTIPLY', v224, v228)
    v230 = g.bc(v229)
    v231 = g.vmath('MULTIPLY', v17, v230)
    v232 = g.math('MULTIPLY', v226, v228)
    v233 = g.bc(v232)
    v234 = g.vmath('MULTIPLY', v160, v233)
    v235 = g.vmath('ADD', v231, v234)
    v236 = g.mixf(v166, v221, 1)
    v237 = g.bc(v236)
    v238 = g.vmath('MULTIPLY', v16, v237)
    v239 = g.vmath('ADD', v235, v238)
    v240 = g.vmath('NORMALIZE', v239)
    v241 = g.sep(v240)
    v242 = g.comb(v241[0], 6.103515625e-05, v241[2])
    v243 = g.vmath('NORMALIZE', v242)
    v244 = g.mixf(v170, 0, v224)
    v245 = g.mixf(v170, 0, v226)
    v246 = g.mixf(v170, 1, v221)
    v247 = g.mixv(v170, v0, v209)
    v248 = g.mixv(v170, v157, v240)
    v249 = g.mixv(v170, v169, v243)
    g.out_('F4_BaseMap_uv', v247, True)
    v250 = g.inp('F4_BaseMap', True, (1.0, 1.0, 1.0))
    v251 = g.inp('F4_BaseMap_alpha', False, 1.0)
    v252 = g.vmath('MULTIPLY', v250, v75)
    v253 = g.inp('_AvatarCustomizeEnable', False, 0.0)
    v254 = g.inp('_EyeTintColor', True, (1.0, 1.0, 1.0))
    v255 = g.inp('_EyeTintColor_w', False, 1.0)
    v256 = g.mixv(v166, v254, (1, 1, 1))
    v257 = g.vmath('MULTIPLY', v252, v256)
    v258 = g.mixv(v253, v252, v257)
    v259 = g.math('MULTIPLY', v251, v76)
    v260 = g.inp('_CharacterParams2', True, (0.7830188, 0.8293082, 1.0))
    v261 = g.inp('_CharacterParams2_w', False, 0.0)
    v262 = g.inp('_CharacterParams5', True, (1.0, 1.0, 1.0))
    v263 = g.inp('_CharacterParams5_w', False, 1.0)
    v264 = g.sep(v110)
    v265 = g.mixv(v264[1], v131, v262)
    v266 = g.math('SUBTRACT', 1, v124)
    v267 = g.math('MULTIPLY', v266, 0.96)
    v268 = g.bc(v267)
    v269 = g.vmath('MULTIPLY', v268, v258)
    v270 = g.inp('_UseShadowLutTex', False, 0.0)
    v271 = g.sep(v258)
    v272 = g.math('MULTIPLY', v271[0], 12.92)
    v273 = g.math('POWER', v271[0], 0.4166667)
    v274 = g.math('MULTIPLY', 1.055, v273)
    v275 = g.math('SUBTRACT', v274, 0.055)
    v276 = g.math('LESS_THAN', v271[0], 0.0031308)
    v277 = g.math('SUBTRACT', 1.0, v276)
    v278 = g.mixf(v277, v272, v275)
    v279 = g.clampn(v278)
    v280 = g.math('MULTIPLY', v271[1], 12.92)
    v281 = g.math('POWER', v271[1], 0.4166667)
    v282 = g.math('MULTIPLY', 1.055, v281)
    v283 = g.math('SUBTRACT', v282, 0.055)
    v284 = g.math('LESS_THAN', v271[1], 0.0031308)
    v285 = g.math('SUBTRACT', 1.0, v284)
    v286 = g.mixf(v285, v280, v283)
    v287 = g.clampn(v286)
    v288 = g.math('MULTIPLY', v271[2], 12.92)
    v289 = g.math('POWER', v271[2], 0.4166667)
    v290 = g.math('MULTIPLY', 1.055, v289)
    v291 = g.math('SUBTRACT', v290, 0.055)
    v292 = g.math('LESS_THAN', v271[2], 0.0031308)
    v293 = g.math('SUBTRACT', 1.0, v292)
    v294 = g.mixf(v293, v288, v291)
    v295 = g.clampn(v294)
    v296 = g.math('MULTIPLY', v295, 31)
    v297 = g.math('FLOOR', v296, 0.0)
    v298 = g.math('MULTIPLY', v297, 0.03125)
    v299 = g.math('MULTIPLY', v279, 0.0302734375)
    v300 = g.math('ADD', v298, v299)
    v301 = g.math('ADD', v300, 0.00048828125)
    v302 = g.math('MULTIPLY', v287, 0.96875)
    v303 = g.math('ADD', v302, 0.015625)
    v304 = g.comb(v301, v303, 0.0)
    g.out_('F5_ShadowLutTex_uv', v304, True)
    v305 = g.inp('F5_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v306 = g.inp('F5_ShadowLutTex_alpha', False, 1.0)
    v307 = g.math('ADD', v301, 0.03125)
    v308 = g.comb(v307, v303, 0.0)
    g.out_('F6_ShadowLutTex_uv', v308, True)
    v309 = g.inp('F6_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v310 = g.inp('F6_ShadowLutTex_alpha', False, 1.0)
    v311 = g.math('MULTIPLY', v295, 31)
    v312 = g.math('SUBTRACT', v311, v297)
    v313 = g.mixv(v312, v305, v309)
    v314 = g.mixv(v270, (0.0, 0.0, 0.0), v313)
    v315 = g.mixf(v270, 0.0, 1.0)
    v316 = g.math('SUBTRACT', 1.0, v315)
    v317 = g.inp('_ShadowColorBrightness', False, 0.5)
    v318 = g.bc(v317)
    v319 = g.vmath('MULTIPLY', v258, v318)
    v320 = g.math('SUBTRACT', 1.0, v315)
    v321 = g.vmath('DOT_PRODUCT', v319, (0.2126729, 0.7151522, 0.072175))
    v322 = g.math('SUBTRACT', 1.0, v315)
    v323 = g.inp('_ShadowColorSaturation', False, 1.0)
    v324 = g.bc(v321)
    v325 = g.vmath('SUBTRACT', v319, v324)
    v326 = g.bc(v323)
    v327 = g.vmath('MULTIPLY', v326, v325)
    v328 = g.bc(v321)
    v329 = g.vmath('ADD', v327, v328)
    v330 = g.mixv(v322, v314, v329)
    v331 = g.mixf(v322, v315, 1.0)
    v332 = g.bc(v267)
    v333 = g.vmath('MULTIPLY', v332, v330)
    v334 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v335 = g.sep(v334)
    v336 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v337 = g.sep(v336)
    v338 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v339 = g.sep(v338)
    v340 = g.comb(v335[0], v337[1], v339[2])
    v341 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v342 = g.sep(v341)
    v343 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v344 = g.sep(v343)
    v345 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v346 = g.sep(v345)
    v347 = g.comb(v342[0], v344[1], v346[2])
    v348 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v349 = g.sep(v348)
    v350 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v351 = g.sep(v350)
    v352 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v353 = g.sep(v352)
    v354 = g.comb(v349[0], v351[1], v353[2])
    v355 = g.inp('_FBXRotationFix', False, 0.0)
    v356 = g.math('GREATER_THAN', v355, 0.5)
    v357 = g.vmath('SCALE', v340, s=-1.0)
    v358 = g.mixv(v356, v340, v347)
    v359 = g.mixv(v356, v347, v357)
    v360 = g.sep(v358)
    v361 = g.sep(v359)
    v362 = g.sep(v354)
    v363 = g.comb(v360[0], v360[1], v360[2])
    v364 = g.comb(v361[0], v361[1], v361[2])
    v365 = g.comb(v362[0], v362[1], v362[2])
    v366 = g.vmath('DOT_PRODUCT', v140[0], v363)
    v367 = g.vmath('DOT_PRODUCT', v140[0], v364)
    v368 = g.vmath('DOT_PRODUCT', v140[0], v365)
    v369 = g.comb(v366, v367, v368)
    v370 = g.vmath('DOT_PRODUCT', v369, v369)
    v371 = g.math('MAXIMUM', v370, 1.175494E-38)
    v372 = g.math('INVERSE_SQRT', v371, 0.0)
    v373 = g.bc(v372)
    v374 = g.vmath('MULTIPLY', v369, v373)
    v375 = g.sep(v374)
    v376 = g.comb(v375[0], 0, v375[2])
    v377 = g.sep(v376)
    v378 = g.vmath('SCALE', v363, s=v377[0])
    v379 = g.vmath('SCALE', v364, s=v377[1])
    v380 = g.vmath('SCALE', v365, s=v377[2])
    v381 = g.vmath('ADD', v378, v379)
    v382 = g.vmath('ADD', v381, v380)
    v383 = g.vmath('DOT_PRODUCT', v382, v382)
    v384 = g.math('MAXIMUM', v383, 1.175494E-38)
    v385 = g.math('INVERSE_SQRT', v384, 0.0)
    v386 = g.bc(v385)
    v387 = g.vmath('MULTIPLY', v382, v386)
    v388 = g.inp('_EyeHighLight', False, 0.0)
    v389 = g.math('SUBTRACT', 1, v166)
    v390 = g.inp('_EyeHighLightColor', True, (2.0, 2.0, 2.0))
    v391 = g.inp('_EyeHighLightColor_w', False, 1.0)
    v392 = g.bc(v166)
    v393 = g.vmath('MULTIPLY', v390, v392)
    v394 = g.bc(v389)
    v395 = g.vmath('ADD', v393, v394)
    v396 = g.inp('_EyeScatteringColor', True, (1.0, 1.0, 1.0))
    v397 = g.inp('_EyeScatteringColor_w', False, 1.0)
    v398 = g.bc(v259)
    v399 = g.vmath('MULTIPLY', v396, v398)
    v400 = g.math('SUBTRACT', 1, v259)
    v401 = g.bc(v400)
    v402 = g.vmath('ADD', v399, v401)
    v403 = g.vmath('MULTIPLY', v395, v402)
    v404 = g.mixv(v388, (1, 1, 1), v403)
    v405 = g.inp('_UseDiffRampMap', False, 0.0)
    v406 = g.vmath('DOT_PRODUCT', v248, v387)
    v407 = g.math('MULTIPLY', v137, v264[0])
    v408 = g.math('ADD', v407, v406)
    v409 = g.math('MULTIPLY', 1, -1.0)
    v410 = g.clampn(v408, v409, 1)
    v411 = g.math('MULTIPLY', v410, 0.5)
    v412 = g.math('ADD', v411, 0.5)
    v413 = g.comb(v412, 0.5, 0.0)
    g.out_('F7_DiffRampMap_uv', v413, True)
    v414 = g.inp('F7_DiffRampMap', True, (1.0, 1.0, 1.0))
    v415 = g.inp('F7_DiffRampMap_alpha', False, 1.0)
    v416 = g.vmath('DOT_PRODUCT', v248, v109)
    v417 = g.math('MULTIPLY', v416, 0.5)
    v418 = g.math('ADD', v417, 0.5)
    v419 = g.comb(v418, 0.5, 0.0)
    g.out_('F8_DiffRampMap_uv', v419, True)
    v420 = g.inp('F8_DiffRampMap', True, (1.0, 1.0, 1.0))
    v421 = g.inp('F8_DiffRampMap_alpha', False, 1.0)
    v422 = g.mixv(v405, (1, 1, 1), v414)
    v423 = g.mixf(v405, 1, v415)
    v424 = g.mixf(v405, 0, v421)
    v425 = g.sep(v422)
    v426 = g.math('MAXIMUM', v425[1], v425[2])
    v427 = g.math('MAXIMUM', v425[0], v426)
    v428 = g.math('MINIMUM', v425[1], v425[2])
    v429 = g.math('MINIMUM', v425[0], v428)
    v430 = g.math('SUBTRACT', v427, v429)
    v431 = g.math('SUBTRACT', 1, v430)
    v432 = g.sep(v138)
    v433 = g.mixf(v432[2], v135, 1)
    v434 = g.math('MINIMUM', v423, 1)
    v435 = g.math('ADD', v424, v423)
    v436 = g.clampn(v435)
    v437 = g.inp('_CharacterParams0', True, (0.0, 0.9, 0.8))
    v438 = g.inp('_CharacterParams0_w', False, 0.8)
    v439 = g.sep(v437)
    v440 = g.bc(v439[2])
    v441 = g.vmath('MULTIPLY', v333, v440)
    v442 = g.clampn(v116[0], 0, 1.5)
    v443 = g.vmath('MULTIPLY', v269, v404)
    v444 = g.inp('_CharacterParams6', True, (0.0, 1.0, 4.371139E-08))
    v445 = g.inp('_CharacterParams6_w', False, 0.0)
    v446 = g.inp('_CharacterParams7', True, (0.15, 1.5, 0.5))
    v447 = g.inp('_CharacterParams7_w', False, 0.0)
    v448 = g.group_named('RCE_ComputeNPRDiffuse', [('hemisphereN', v249), ('ambCol', v260), ('brightness', v442), ('blendedLightCol', v265), ('blendedLightInt', 1), ('minShadow', v434), ('combWeight', v436), ('albScaled', v441), ('diffColor', v443), ('rampCol', v422), ('rampChroma', v430), ('rampChromaInv', v431), ('_CharacterParams6', v444), ('_CharacterParams6_w', v445), ('_CharacterParams7', v446), ('_CharacterParams7_w', v447), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139), ('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_CharacterParams0', v437), ('_CharacterParams0_w', v438)])
    v449 = g.vmath('DOT_PRODUCT', v249, v444)
    v450 = g.sep(v446)
    v451 = g.math('ADD', v449, v450[0])
    v452 = g.clampn(v451)
    v453 = g.math('MULTIPLY', v452, v450[1])
    v454 = g.math('ADD', v453, v450[2])
    v455 = g.math('MULTIPLY', v432[1], v434)
    v456 = g.vmath('SUBTRACT', (1, 1, 1), v260)
    v457 = g.bc(v455)
    v458 = g.vmath('MULTIPLY', v457, v456)
    v459 = g.vmath('ADD', v458, v260)
    v460 = g.bc(v454)
    v461 = g.vmath('MULTIPLY', v460, v459)
    v462 = g.mixf(v116[0], 0.65, 1)
    v463 = g.math('MINIMUM', v462, 1.5)
    v464 = g.clampn(v116[0], 1.25, 1.75)
    v465 = g.mixf(v432[0], v463, v464)
    v466 = g.bc(v465)
    v467 = g.vmath('MULTIPLY', v461, v466)
    v468 = g.bc(v438)
    v469 = g.vmath('MULTIPLY', v467, v468)
    v470 = g.vmath('MULTIPLY', v269, v404)
    v471 = g.mixv(v424, v441, v470)
    v472 = g.mixv(v433, v469, v448[1])
    v473 = g.mixv(v433, v471, v448[0])
    v474 = g.mixf(0, 1, v259)
    v475 = g.mixf(v433, v424, v434)
    v476 = g.math('SUBTRACT', 1, v439[2])
    v477 = g.math('MULTIPLY', v475, v476)
    v478 = g.math('ADD', v477, v439[2])
    v479 = g.math('MULTIPLY', v475, 0.5)
    v480 = g.math('ADD', v479, 0.5)
    v481 = g.math('MULTIPLY', v478, v480)
    v482 = g.bc(v244)
    v483 = g.vmath('MULTIPLY', v17, v482)
    v484 = g.bc(v245)
    v485 = g.vmath('MULTIPLY', v160, v484)
    v486 = g.vmath('ADD', v483, v485)
    v487 = g.bc(v246)
    v488 = g.vmath('MULTIPLY', v16, v487)
    v489 = g.vmath('ADD', v486, v488)
    v490 = g.vmath('NORMALIZE', v489)
    v491 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v492 = g.sep(v491)
    v493 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v494 = g.sep(v493)
    v495 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v496 = g.sep(v495)
    v497 = g.comb(v492[0], v494[0], v496[0])
    v498 = g.vmath('DOT_PRODUCT', v497, v490)
    v499 = g.comb(v498, 0.0, 0.0)
    v500 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v501 = g.sep(v500)
    v502 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v503 = g.sep(v502)
    v504 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v505 = g.sep(v504)
    v506 = g.comb(v501[1], v503[1], v505[1])
    v507 = g.vmath('DOT_PRODUCT', v506, v490)
    v508 = g.sep(v499)
    v509 = g.comb(v508[0], v507, v508[2])
    v510 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v511 = g.sep(v510)
    v512 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v513 = g.sep(v512)
    v514 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v515 = g.sep(v514)
    v516 = g.comb(v511[2], v513[2], v515[2])
    v517 = g.vmath('DOT_PRODUCT', v516, v490)
    v518 = g.sep(v509)
    v519 = g.comb(v518[0], v518[1], v517)
    v520 = g.vmath('DOT_PRODUCT', v519, v519)
    v521 = g.math('MAXIMUM', v520, 1.175494E-38)
    v522 = g.math('INVERSE_SQRT', v521, 0.0)
    v523 = g.sep(v519)
    v524 = g.math('MULTIPLY', v523[0], v522)
    v525 = g.math('MULTIPLY', v524, 0.5)
    v526 = g.math('ADD', v525, 0.5)
    v527 = g.math('MULTIPLY', v523[1], v522)
    v528 = g.math('MULTIPLY', v527, 0.5)
    v529 = g.math('ADD', v528, 0.5)
    v530 = g.comb(v526, v529, 0.0)
    g.out_('F9_MatcapTex_uv', v530, True)
    v531 = g.inp('F9_MatcapTex', True, (1.0, 1.0, 1.0))
    v532 = g.inp('F9_MatcapTex_alpha', False, 1.0)
    v533 = g.inp('_MatcapColor', True, (1.0, 1.0, 1.0))
    v534 = g.inp('_MatcapColor_w', False, 1.0)
    v535 = g.bc(v534)
    v536 = g.vmath('MULTIPLY', v531, v535)
    v537 = g.bc(v532)
    v538 = g.vmath('MULTIPLY', v537, v533)
    v539 = g.vmath('ADD', v536, v538)
    v540 = g.bc(v481)
    v541 = g.vmath('MULTIPLY', v540, v472)
    v542 = g.vmath('MULTIPLY', v539, v541)
    v543 = g.mixv(v170, (0, 0, 0), v542)
    v544 = g.vmath('MULTIPLY', v473, v472)
    v545 = g.bc(v474)
    v546 = g.vmath('MULTIPLY', v544, v545)
    v547 = g.vmath('ADD', v546, v543)
    v548 = g.vmath('DOT_PRODUCT', v547, (0.2126729, 0.7151522, 0.072175))
    v549 = g.math('SUBTRACT', v548, 0.5)
    v550 = g.clampn(v549, 0, 0.5)
    v551 = g.math('MULTIPLY', v550, v550)
    v552 = g.math('ADD', v551, 1)
    v553 = g.bc(v548)
    v554 = g.vmath('SUBTRACT', v547, v553)
    v555 = g.bc(v552)
    v556 = g.vmath('MULTIPLY', v555, v554)
    v557 = g.bc(v548)
    v558 = g.vmath('ADD', v556, v557)
    v559 = g.math('MULTIPLY', v140[3], 6.103515625e-05)
    v560 = g.comb(v140[1], v559, v140[2])
    v561 = g.vmath('DOT_PRODUCT', v560, v248)
    v562 = g.math('ADD', 0.5, v561)
    v563 = g.math('MULTIPLY', 0.5, v561)
    v564 = g.math('MULTIPLY', v563, v561)
    v565 = g.math('SUBTRACT', v562, v564)
    v566 = g.clampn(v565)
    v567 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v568 = g.sep(v567)
    v569 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v570 = g.sep(v569)
    v571 = g.math('MULTIPLY', v568[0], v570[0])
    v572 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v573 = g.sep(v572)
    v574 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v575 = g.sep(v574)
    v576 = g.math('MULTIPLY', v573[2], v575[2])
    v577 = g.math('ADD', v571, v576)
    v578 = g.math('INVERSE_SQRT', v577, 0.0)
    v579 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v580 = g.sep(v579)
    v581 = g.math('MULTIPLY', v578, v580[0])
    v582 = g.math('MULTIPLY', v140[1], v581)
    v583 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v584 = g.sep(v583)
    v585 = g.math('MULTIPLY', v578, v584[2])
    v586 = g.math('MULTIPLY', v140[2], v585)
    v587 = g.math('ADD', v582, v586)
    v588 = g.math('MULTIPLY', v587, -1.0)
    v589 = g.math('SUBTRACT', 1, v264[0])
    v590 = g.clampn(v588)
    v591 = g.math('MULTIPLY', v589, v590)
    v592 = g.vmath('DOT_PRODUCT', v102, v248)
    v593 = g.math('ABSOLUTE', v592, 0.0)
    v594 = g.math('MULTIPLY', v593, -1.0)
    v595 = g.math('ADD', v594, 0.4)
    v596 = g.math('MULTIPLY', v595, 5)
    v597 = g.clampn(v596)
    v598 = g.math('MULTIPLY', v597, v597)
    v599 = g.math('MULTIPLY', 2, v597)
    v600 = g.math('SUBTRACT', 3, v599)
    v601 = g.math('MULTIPLY', v598, v600)
    v602 = g.vmath('DOT_PRODUCT', v269, (0.2126729, 0.7151522, 0.072175))
    v603 = g.math('SUBTRACT', 0.1, v602)
    v604 = g.math('MULTIPLY', v603, 16.666)
    v605 = g.clampn(v604)
    v606 = g.math('MULTIPLY', v605, v605)
    v607 = g.math('MULTIPLY', 2, v605)
    v608 = g.math('SUBTRACT', 3, v607)
    v609 = g.math('MULTIPLY', v606, v608)
    v610 = g.math('MULTIPLY', v609, v601)
    v611 = g.math('MULTIPLY', v610, v591)
    v612 = g.math('MULTIPLY', v611, v566)
    v613 = g.vmath('MULTIPLY', v265, (1, 1, 1))
    v614 = g.bc(v612)
    v615 = g.vmath('MULTIPLY', v614, v613)
    v616 = g.vmath('MAXIMUM', v269, (0.15, 0.15, 0.15))
    v617 = g.vmath('MULTIPLY', v615, v616)
    v618 = g.inp('_UseEmission', False, 1.0)
    g.out_('F10_EmissionMap_uv', v247, True)
    v619 = g.inp('F10_EmissionMap', True, (0.0, 0.0, 0.0))
    v620 = g.inp('F10_EmissionMap_alpha', False, 1.0)
    v621 = g.inp('_EmissionColor', True, (0.0, 0.0, 0.0))
    v622 = g.inp('_EmissionColor_w', False, 1.0)
    v623 = g.vmath('MULTIPLY', v619, v621)
    v624 = g.inp('_EmissionBrightness', False, 1.0)
    v625 = g.bc(v624)
    v626 = g.vmath('MULTIPLY', v623, v625)
    v627 = g.mixv(v618, (0, 0, 0), v626)
    v628 = g.inp('_CharacterParams13', True, (0.0, 0.0, 0.0))
    v629 = g.inp('_CharacterParams13_w', False, 1.0)
    v630 = g.sep(v628)
    v631 = g.bc(v630[0])
    v632 = g.vmath('MULTIPLY', v258, v631)
    v633 = g.vmath('ADD', v627, v632)
    v634 = g.bc(v166)
    v635 = g.vmath('MULTIPLY', v634, v390)
    v636 = g.bc(v630[1])
    v637 = g.vmath('MULTIPLY', v635, v636)
    v638 = g.vmath('ADD', v633, v637)
    v639 = g.bc(v259)
    v640 = g.vmath('MULTIPLY', v639, v396)
    v641 = g.bc(v630[2])
    v642 = g.vmath('MULTIPLY', v640, v641)
    v643 = g.vmath('ADD', v638, v642)
    v644 = g.bc(v474)
    v645 = g.vmath('MULTIPLY', v643, v644)
    v646 = g.mixv(v388, (0, 0, 0), v645)
    v647 = g.vmath('ADD', v646, v617)
    v648 = g.vmath('ADD', v647, v558)
    v649 = g.sep(v114)
    v650 = g.bc(v649[0])
    v651 = g.vmath('DIVIDE', v648, v650)
    v652 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v653 = g.math('SUBTRACT', v652, 0)
    v654 = g.math('CEIL', v653, 0.0)
    v655 = g.math('MAXIMUM', v654, 0.0)
    g.out_('Z0_it', v655, False)
    g.out_('Z0_s_N', v59, True)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_lightAccum', (0, 0, 0), True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_positionWS', v15, True)
    g.out_('Z0_r_albedo', v150, True)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pixelLightCount', v652, False)
    v656 = g.inp('Z0_o_N', True)
    v657 = g.inp('Z0_o_Lloop0', False)
    v658 = g.inp('Z0_o_lightAccum', True)
    v659 = g.inp('Z0_o_lightIndex', False)
    v660 = g.inp('Z0_o_positionWS', True)
    v661 = g.vmath('ADD', v651, v658)
    v662 = g.sep(v651)
    v663 = g.sep(v661)
    v664 = g.comb(v663[0], v663[1], v663[2])
    g.out_('ret_gBuffer0', v664, True)
    g.out_('ret_gBuffer0_w', v83, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v651, True)
    g.out_('ret_color_w', 1, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)


def build_Ruri_Endfield_Uber_Hair():
    t = _tree('Ruri Endfield Uber Hair')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.math('MULTIPLY', v100, v76)
    v152 = g.inp('_UseCutoff', False, 0.0)
    v153 = g.inp('_Cutoff', False, 0.5)
    v154 = g.math('SUBTRACT', v151, v153)
    v155 = g.math('LESS_THAN', v154, 0.0)
    v156 = g.math('SUBTRACT', 1.0, v155)
    v157 = g.math('MULTIPLY', 1.0, v156)
    v158 = g.mixf(v152, 1.0, v157)
    v159 = g.inp('_UseShadowLutTex', False, 0.0)
    v160 = g.sep(v150)
    v161 = g.math('MULTIPLY', v160[0], 12.92)
    v162 = g.math('POWER', v160[0], 0.4166667)
    v163 = g.math('MULTIPLY', 1.055, v162)
    v164 = g.math('SUBTRACT', v163, 0.055)
    v165 = g.math('LESS_THAN', v160[0], 0.0031308)
    v166 = g.math('SUBTRACT', 1.0, v165)
    v167 = g.mixf(v166, v161, v164)
    v168 = g.clampn(v167)
    v169 = g.math('MULTIPLY', v160[1], 12.92)
    v170 = g.math('POWER', v160[1], 0.4166667)
    v171 = g.math('MULTIPLY', 1.055, v170)
    v172 = g.math('SUBTRACT', v171, 0.055)
    v173 = g.math('LESS_THAN', v160[1], 0.0031308)
    v174 = g.math('SUBTRACT', 1.0, v173)
    v175 = g.mixf(v174, v169, v172)
    v176 = g.clampn(v175)
    v177 = g.math('MULTIPLY', v160[2], 12.92)
    v178 = g.math('POWER', v160[2], 0.4166667)
    v179 = g.math('MULTIPLY', 1.055, v178)
    v180 = g.math('SUBTRACT', v179, 0.055)
    v181 = g.math('LESS_THAN', v160[2], 0.0031308)
    v182 = g.math('SUBTRACT', 1.0, v181)
    v183 = g.mixf(v182, v177, v180)
    v184 = g.clampn(v183)
    v185 = g.math('MULTIPLY', v184, 31)
    v186 = g.math('FLOOR', v185, 0.0)
    v187 = g.math('MULTIPLY', v186, 0.03125)
    v188 = g.math('MULTIPLY', v168, 0.0302734375)
    v189 = g.math('ADD', v187, v188)
    v190 = g.math('ADD', v189, 0.00048828125)
    v191 = g.math('MULTIPLY', v176, 0.96875)
    v192 = g.math('ADD', v191, 0.015625)
    v193 = g.comb(v190, v192, 0.0)
    g.out_('F4_ShadowLutTex_uv', v193, True)
    v194 = g.inp('F4_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v195 = g.inp('F4_ShadowLutTex_alpha', False, 1.0)
    v196 = g.math('ADD', v190, 0.03125)
    v197 = g.comb(v196, v192, 0.0)
    g.out_('F5_ShadowLutTex_uv', v197, True)
    v198 = g.inp('F5_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v199 = g.inp('F5_ShadowLutTex_alpha', False, 1.0)
    v200 = g.math('MULTIPLY', v184, 31)
    v201 = g.math('SUBTRACT', v200, v186)
    v202 = g.mixv(v201, v194, v198)
    v203 = g.mixv(v159, (0.0, 0.0, 0.0), v202)
    v204 = g.mixf(v159, 0.0, 1.0)
    v205 = g.math('SUBTRACT', 1.0, v204)
    v206 = g.inp('_ShadowColorBrightness', False, 0.5)
    v207 = g.bc(v206)
    v208 = g.vmath('MULTIPLY', v150, v207)
    v209 = g.math('SUBTRACT', 1.0, v204)
    v210 = g.vmath('DOT_PRODUCT', v208, (0.2126729, 0.7151522, 0.072175))
    v211 = g.math('SUBTRACT', 1.0, v204)
    v212 = g.inp('_ShadowColorSaturation', False, 1.0)
    v213 = g.bc(v210)
    v214 = g.vmath('SUBTRACT', v208, v213)
    v215 = g.bc(v212)
    v216 = g.vmath('MULTIPLY', v215, v214)
    v217 = g.bc(v210)
    v218 = g.vmath('ADD', v216, v217)
    v219 = g.mixv(v211, v203, v218)
    v220 = g.mixf(v211, v204, 1.0)
    v221 = g.vmath('NORMALIZE', v16)
    v222 = g.vmath('NORMALIZE', v17)
    v223 = g.vmath('CROSS_PRODUCT', v221, v222)
    v224 = g.bc(v4)
    v225 = g.vmath('MULTIPLY', v223, v224)
    v226 = g.inp('_UseSpecBumpMap', False, 0.0)
    v227 = g.math('MULTIPLY', v226, v20)
    g.out_('F6_SplitNormalMap_uv', v0, True)
    v228 = g.inp('F6_SplitNormalMap', True, (0.5, 0.5, 0.5))
    v229 = g.inp('F6_SplitNormalMap_alpha', False, 1.0)
    v230 = g.sep(v228)
    v231 = g.math('MULTIPLY', v230[0], 2)
    v232 = g.math('SUBTRACT', v231, 1)
    v233 = g.math('MULTIPLY', v232, v27)
    v234 = g.math('MULTIPLY', v230[1], 2)
    v235 = g.math('SUBTRACT', v234, 1)
    v236 = g.math('MULTIPLY', v235, v27)
    v237 = g.math('MULTIPLY', v233, v233)
    v238 = g.math('MULTIPLY', v236, v236)
    v239 = g.math('ADD', v237, v238)
    v240 = g.clampn(v239)
    v241 = g.math('SUBTRACT', 1, v240)
    v242 = g.math('SQRT', v241, 0.0)
    v243 = g.math('MAXIMUM', v242, 1E-16)
    v244 = g.bc(v233)
    v245 = g.vmath('MULTIPLY', v244, v222)
    v246 = g.bc(v236)
    v247 = g.vmath('MULTIPLY', v246, v225)
    v248 = g.vmath('ADD', v245, v247)
    v249 = g.bc(v243)
    v250 = g.vmath('MULTIPLY', v249, v221)
    v251 = g.vmath('ADD', v248, v250)
    v252 = g.vmath('NORMALIZE', v251)
    v253 = g.bc(v98)
    v254 = g.vmath('MULTIPLY', v253, v252)
    v255 = g.math('MULTIPLY', v230[2], 2)
    v256 = g.math('SUBTRACT', v255, 1)
    v257 = g.inp('_SpecBumpScale', False, 1.0)
    v258 = g.math('MULTIPLY', v256, v257)
    v259 = g.math('MULTIPLY', v229, 2)
    v260 = g.math('SUBTRACT', v259, 1)
    v261 = g.math('MULTIPLY', v260, v257)
    v262 = g.math('MULTIPLY', v258, v258)
    v263 = g.math('MULTIPLY', v261, v261)
    v264 = g.math('ADD', v262, v263)
    v265 = g.clampn(v264)
    v266 = g.math('SUBTRACT', 1, v265)
    v267 = g.math('SQRT', v266, 0.0)
    v268 = g.math('MAXIMUM', v267, 1E-16)
    v269 = g.bc(v258)
    v270 = g.vmath('MULTIPLY', v269, v222)
    v271 = g.bc(v261)
    v272 = g.vmath('MULTIPLY', v271, v225)
    v273 = g.vmath('ADD', v270, v272)
    v274 = g.bc(v268)
    v275 = g.vmath('MULTIPLY', v274, v221)
    v276 = g.vmath('ADD', v273, v275)
    v277 = g.vmath('NORMALIZE', v276)
    v278 = g.inp('F6_SplitNormalMap', True, (0.5, 0.5, 0.5))
    v279 = g.inp('F6_SplitNormalMap_alpha', False, 1.0)
    v280 = g.sep(v278)
    v281 = g.math('MULTIPLY', v280[0], 2)
    v282 = g.math('SUBTRACT', v281, 1)
    v283 = g.math('MULTIPLY', v282, v27)
    v284 = g.math('MULTIPLY', v280[1], 2)
    v285 = g.math('SUBTRACT', v284, 1)
    v286 = g.math('MULTIPLY', v285, v27)
    v287 = g.math('MULTIPLY', v283, v283)
    v288 = g.math('MULTIPLY', v286, v286)
    v289 = g.math('ADD', v287, v288)
    v290 = g.clampn(v289)
    v291 = g.math('SUBTRACT', 1, v290)
    v292 = g.math('SQRT', v291, 0.0)
    v293 = g.math('MAXIMUM', v292, 1E-16)
    v294 = g.bc(v283)
    v295 = g.vmath('MULTIPLY', v294, v222)
    v296 = g.bc(v286)
    v297 = g.vmath('MULTIPLY', v296, v225)
    v298 = g.vmath('ADD', v295, v297)
    v299 = g.bc(v293)
    v300 = g.vmath('MULTIPLY', v299, v221)
    v301 = g.vmath('ADD', v298, v300)
    v302 = g.vmath('NORMALIZE', v301)
    v303 = g.bc(v98)
    v304 = g.vmath('MULTIPLY', v303, v302)
    v305 = g.bc(v98)
    v306 = g.vmath('MULTIPLY', v305, v221)
    v307 = g.mixv(v20, v306, v304)
    v308 = g.mixv(v20, v306, v304)
    v309 = g.mixv(v227, v307, v254)
    v310 = g.mixv(v227, v308, v277)
    v311 = g.mixv(v227, v278, v228)
    v312 = g.mixf(v227, v279, v229)
    v313 = g.mixf(v227, v283, v233)
    v314 = g.mixf(v227, v286, v236)
    v315 = g.mixf(v227, v293, v243)
    v316 = g.group_named('RCE_GetObjectFlatDir', [('positionWS', v15)])
    v317 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v318 = g.sep(v317)
    v319 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v320 = g.sep(v319)
    v321 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v322 = g.sep(v321)
    v323 = g.comb(v318[0], v320[1], v322[2])
    v324 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v325 = g.sep(v324)
    v326 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v327 = g.sep(v326)
    v328 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v329 = g.sep(v328)
    v330 = g.comb(v325[0], v327[1], v329[2])
    v331 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v332 = g.sep(v331)
    v333 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v334 = g.sep(v333)
    v335 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v336 = g.sep(v335)
    v337 = g.comb(v332[0], v334[1], v336[2])
    v338 = g.inp('_FBXRotationFix', False, 0.0)
    v339 = g.math('GREATER_THAN', v338, 0.5)
    v340 = g.vmath('SCALE', v323, s=-1.0)
    v341 = g.mixv(v339, v323, v330)
    v342 = g.mixv(v339, v330, v340)
    v343 = g.inp('_AnisotropyDirX', False, 0.0)
    v344 = g.bc(v343)
    v345 = g.vmath('MULTIPLY', v341, v344)
    v346 = g.vmath('ADD', v345, v342)
    v347 = g.vmath('NORMALIZE', v346)
    v348 = g.vmath('CROSS_PRODUCT', v310, v347)
    v349 = g.mixv(v127, v348, v17)
    v350 = g.mixf(v127, 1, v4)
    v351 = g.vmath('CROSS_PRODUCT', v310, v349)
    v352 = g.bc(v350)
    v353 = g.vmath('MULTIPLY', v352, v351)
    v354 = g.vmath('DOT_PRODUCT', v102, v341)
    v355 = g.vmath('DOT_PRODUCT', v102, v337)
    v356 = g.vmath('DOT_PRODUCT', v310, v341)
    v357 = g.vmath('DOT_PRODUCT', v310, v337)
    v358 = g.math('MULTIPLY', v356, v356)
    v359 = g.math('MULTIPLY', v357, v357)
    v360 = g.math('ADD', v358, v359)
    v361 = g.math('INVERSE_SQRT', v360, 0.0)
    v362 = g.math('MULTIPLY', v354, v354)
    v363 = g.math('MULTIPLY', v355, v355)
    v364 = g.math('ADD', v362, v363)
    v365 = g.math('INVERSE_SQRT', v364, 0.0)
    v366 = g.math('MULTIPLY', v361, v356)
    v367 = g.math('MULTIPLY', v361, v357)
    v368 = g.comb(v366, v367, 0.0)
    v369 = g.math('MULTIPLY', v365, v354)
    v370 = g.math('MULTIPLY', v365, v355)
    v371 = g.comb(v369, v370, 0.0)
    v372 = g.vmath('DOT_PRODUCT', v368, v371)
    v373 = g.clampn(v372)
    v374 = g.math('LOGARITHM', v373, 2.0)
    v375 = g.inp('_AnisotropyEdgeFade', False, 1.0)
    v376 = g.math('MULTIPLY', v374, v375)
    v377 = g.math('POWER', 2.0, v376)
    v378 = g.inp('_HairDarkenParams', True, (0.0, 0.0, 0.0))
    v379 = g.inp('_HairDarkenParams_w', False, 0.0)
    v380 = g.sep(v378)
    v381 = g.inp('_CharacterParams10', True, (0.0, 0.0, 0.0))
    v382 = g.inp('_CharacterParams10_w', False, 0.0)
    v383 = g.sep(v381)
    v384 = g.mixf(v383[0], v380[0], v383[1])
    v385 = g.mixf(v383[0], v380[2], v382)
    v386 = g.mixf(v383[0], v380[1], 0)
    v387 = g.sep(v15)
    v388 = g.math('SUBTRACT', v385, v387[1])
    v389 = g.math('ADD', v388, 0.2)
    v390 = g.math('MULTIPLY', v389, 2.857143)
    v391 = g.clampn(v390)
    v392 = g.math('MULTIPLY', v391, v391)
    v393 = g.math('MULTIPLY', 2, v391)
    v394 = g.math('SUBTRACT', 3, v393)
    v395 = g.math('MULTIPLY', v392, v394)
    v396 = g.math('MULTIPLY', v395, v386)
    v397 = g.math('MAXIMUM', v396, v379)
    v398 = g.math('ADD', v397, v384)
    v399 = g.math('LESS_THAN', 0.01, v398)
    v400 = g.math('MAXIMUM', v397, v384)
    v401 = g.math('SUBTRACT', 1, v400)
    v402 = g.math('MULTIPLY', v400, 0.8)
    v403 = g.math('ADD', v402, v401)
    v404 = g.bc(v403)
    v405 = g.vmath('MULTIPLY', v150, v404)
    v406 = g.bc(v403)
    v407 = g.vmath('MULTIPLY', v219, v406)
    v408 = g.math('MULTIPLY', v400, 2)
    v409 = g.math('ADD', v408, v401)
    v410 = g.mixv(v399, v150, v405)
    v411 = g.mixv(v399, v219, v407)
    v412 = g.mixf(v399, 1, v409)
    v413 = g.vmath('MULTIPLY', v410, (0.96, 0.96, 0.96))
    v414 = g.math('MULTIPLY', v129, 0.04)
    v415 = g.vmath('MULTIPLY', v411, (0.96, 0.96, 0.96))
    v416 = g.vmath('DOT_PRODUCT', v413, (0.2126729, 0.7151522, 0.072175))
    v417 = g.math('MINIMUM', v135, 1)
    v418 = g.inp('_CharacterParams5', True, (1.0, 1.0, 1.0))
    v419 = g.inp('_CharacterParams5_w', False, 1.0)
    v420 = g.sep(v110)
    v421 = g.mixv(v420[1], v131, v418)
    v422 = g.vmath('DOT_PRODUCT', v309, v140[0])
    v423 = g.math('MULTIPLY', 0.5, v422)
    v424 = g.math('MULTIPLY', v423, v422)
    v425 = g.math('SUBTRACT', 0.5, v424)
    v426 = g.math('SUBTRACT', 1, v420[0])
    v427 = g.math('MULTIPLY', v142, v141[1])
    v428 = g.math('MULTIPLY', v426, v427)
    v429 = g.math('MULTIPLY', v428, v425)
    v430 = g.math('ADD', v429, v422)
    v431 = g.inp('_UseDiffRampMap', False, 0.0)
    v432 = g.math('SUBTRACT', 1.0, v431)
    v433 = g.math('MULTIPLY', v430, 0.5)
    v434 = g.math('ADD', v433, 0.5)
    v435 = g.clampn(v434)
    v436 = g.mixf(v432, 0.0, 0)
    v437 = g.mixf(v432, 0.0, 0)
    v438 = g.mixv(v432, (0.0, 0.0, 0.0), (1, 1, 1))
    v439 = g.mixf(v432, 0.0, v435)
    v440 = g.mixf(v432, 0.0, 1.0)
    v441 = g.math('SUBTRACT', 1.0, v440)
    v442 = g.math('MULTIPLY', v137, v420[0])
    v443 = g.math('ADD', v442, v430)
    v444 = g.math('MULTIPLY', 1, -1.0)
    v445 = g.clampn(v443, v444, 1)
    v446 = g.math('MULTIPLY', v445, 0.5)
    v447 = g.math('ADD', v446, 0.5)
    v448 = g.math('SUBTRACT', 1.0, v440)
    v449 = g.comb(v447, 0.5, 0.0)
    g.out_('F7_DiffRampMap_uv', v449, True)
    v450 = g.inp('F7_DiffRampMap', True, (1.0, 1.0, 1.0))
    v451 = g.inp('F7_DiffRampMap_alpha', False, 1.0)
    v452 = g.math('SUBTRACT', 1.0, v440)
    v453 = g.sep(v450)
    v454 = g.math('MAXIMUM', v453[1], v453[2])
    v455 = g.math('MAXIMUM', v453[0], v454)
    v456 = g.math('MINIMUM', v453[1], v453[2])
    v457 = g.math('MINIMUM', v453[0], v456)
    v458 = g.math('SUBTRACT', v455, v457)
    v459 = g.mixf(v452, v436, v458)
    v460 = g.math('SUBTRACT', 1.0, v440)
    v461 = g.vmath('DOT_PRODUCT', v309, v109)
    v462 = g.math('MULTIPLY', v461, 0.5)
    v463 = g.math('ADD', v462, 0.5)
    v464 = g.math('SUBTRACT', 1.0, v440)
    v465 = g.comb(v463, 0.5, 0.0)
    g.out_('F8_DiffRampMap_uv', v465, True)
    v466 = g.inp('F8_DiffRampMap', True, (1.0, 1.0, 1.0))
    v467 = g.inp('F8_DiffRampMap_alpha', False, 1.0)
    v468 = g.mixf(v464, v437, v467)
    v469 = g.math('SUBTRACT', 1.0, v440)
    v470 = g.mixv(v469, v438, v450)
    v471 = g.mixf(v469, v439, v451)
    v472 = g.mixf(v469, v440, 1.0)
    v473 = g.math('MULTIPLY', v468, v128)
    v474 = g.math('MINIMUM', v471, v128)
    v475 = g.math('ADD', v473, v471)
    v476 = g.clampn(v475)
    v477 = g.clampn(v116[0], 0, 1.5)
    v478 = g.inp('_CharacterParams0', True, (0.0, 0.9, 0.8))
    v479 = g.inp('_CharacterParams0_w', False, 0.8)
    v480 = g.sep(v478)
    v481 = g.bc(v480[2])
    v482 = g.vmath('MULTIPLY', v415, v481)
    v483 = g.inp('_CharacterParams2', True, (0.7830188, 0.8293082, 1.0))
    v484 = g.inp('_CharacterParams2_w', False, 0.0)
    v485 = g.math('SUBTRACT', 1, v459)
    v486 = g.inp('_CharacterParams6', True, (0.0, 1.0, 4.371139E-08))
    v487 = g.inp('_CharacterParams6_w', False, 0.0)
    v488 = g.inp('_CharacterParams7', True, (0.15, 1.5, 0.5))
    v489 = g.inp('_CharacterParams7_w', False, 0.0)
    v490 = g.group_named('RCE_ComputeNPRDiffuse', [('hemisphereN', v309), ('ambCol', v483), ('brightness', v477), ('blendedLightCol', v421), ('blendedLightInt', 1), ('minShadow', v474), ('combWeight', v476), ('albScaled', v482), ('diffColor', v413), ('rampCol', v470), ('rampChroma', v459), ('rampChromaInv', v485), ('_CharacterParams6', v486), ('_CharacterParams6_w', v487), ('_CharacterParams7', v488), ('_CharacterParams7_w', v489), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139), ('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_CharacterParams0', v478), ('_CharacterParams0_w', v479)])
    v491 = g.math('MULTIPLY', v474, 0.5)
    v492 = g.math('ADD', v491, 0.5)
    v493 = g.math('MULTIPLY', v490[2], v492)
    v494 = g.inp('_StrokeOn', False, 0.0)
    v495 = g.inp('_StrokeMap_ST', True, (1.0, 1.0, 0.0))
    v496 = g.inp('_StrokeMap_ST_w', False, 0.0)
    v497 = g.sep(v495)
    v498 = g.comb(v497[0], v497[1], 0.0)
    v499 = g.vmath('MULTIPLY', v0, v498)
    v500 = g.comb(v497[2], v496, 0.0)
    v501 = g.vmath('ADD', v499, v500)
    g.out_('F9_StrokeMap_uv', v501, True)
    v502 = g.inp('F9_StrokeMap', True, (0.5, 0.5, 0.5))
    v503 = g.inp('F9_StrokeMap_alpha', False, 1.0)
    v504 = g.sep(v502)
    v505 = g.math('MULTIPLY', v504[0], 2)
    v506 = g.math('SUBTRACT', v505, 1)
    v507 = g.inp('_StrokeScale', False, 1.0)
    v508 = g.math('MULTIPLY', v506, v507)
    v509 = g.inp('_AnisotropyValue', False, 0.35)
    v510 = g.math('MULTIPLY', v509, 2)
    v511 = g.math('ADD', v508, v510)
    v512 = g.math('SUBTRACT', v511, 1)
    v513 = g.math('MULTIPLY', v506, v507)
    v514 = g.inp('_AnisotropyValue2', False, 0.4)
    v515 = g.math('MULTIPLY', v514, 2)
    v516 = g.math('ADD', v513, v515)
    v517 = g.math('SUBTRACT', v516, 1)
    v518 = g.math('MULTIPLY', v509, 2)
    v519 = g.math('SUBTRACT', v518, 1)
    v520 = g.math('MULTIPLY', v514, 2)
    v521 = g.math('SUBTRACT', v520, 1)
    v522 = g.mixf(v494, v519, v512)
    v523 = g.mixf(v494, v521, v517)
    v524 = g.bc(v354)
    v525 = g.vmath('MULTIPLY', v341, v524)
    v526 = g.sep(v140[0])
    v527 = g.bc(v526[1])
    v528 = g.vmath('MULTIPLY', v342, v527)
    v529 = g.vmath('ADD', v525, v528)
    v530 = g.bc(v355)
    v531 = g.vmath('MULTIPLY', v337, v530)
    v532 = g.vmath('ADD', v529, v531)
    v533 = g.vmath('MULTIPLY', v532, (2, 2, 2))
    v534 = g.vmath('ADD', v140[0], v533)
    v535 = g.vmath('NORMALIZE', v534)
    v536 = g.vmath('ADD', v535, v102)
    v537 = g.vmath('NORMALIZE', v536)
    v538 = g.bc(v522)
    v539 = g.vmath('MULTIPLY', v310, v538)
    v540 = g.vmath('ADD', v539, v353)
    v541 = g.vmath('NORMALIZE', v540)
    v542 = g.vmath('DOT_PRODUCT', v541, v537)
    v543 = g.math('MULTIPLY', v542, v542)
    v544 = g.math('SUBTRACT', 1, v543)
    v545 = g.math('SQRT', v544, 0.0)
    v546 = g.math('MAXIMUM', v545, 0.0001)
    v547 = g.math('LOGARITHM', v546, 2.0)
    v548 = g.math('MULTIPLY', v547, 200)
    v549 = g.math('POWER', 2.0, v548)
    v550 = g.math('MULTIPLY', v129, v549)
    v551 = g.clampn(v550)
    v552 = g.inp('_UseSpecRampMap', False, 0.0)
    v553 = g.math('MULTIPLY', v377, v551)
    v554 = g.math('MULTIPLY', v377, v551)
    v555 = g.math('MULTIPLY', v377, v551)
    v556 = g.comb(v553, v554, v555)
    v557 = g.math('MULTIPLY', v377, v551)
    v558 = g.math('MULTIPLY', v377, v377)
    v559 = g.math('GREATER_THAN', v542, 0)
    v560 = g.mixf(v559, 0, 1)
    v561 = g.math('MULTIPLY', v558, v560)
    v562 = g.comb(v551, v561, 0.0)
    g.out_('F10_SpecRampMap_uv', v562, True)
    v563 = g.inp('F10_SpecRampMap', True, (1.0, 1.0, 1.0))
    v564 = g.inp('F10_SpecRampMap_alpha', False, 1.0)
    v565 = g.bc(v557)
    v566 = g.vmath('MULTIPLY', v565, v563)
    v567 = g.mixv(v552, v556, v566)
    v568 = g.sep(v567)
    v569 = g.math('MAXIMUM', v568[1], v568[2])
    v570 = g.math('MAXIMUM', v568[0], v569)
    v571 = g.bc(v523)
    v572 = g.vmath('MULTIPLY', v310, v571)
    v573 = g.vmath('ADD', v572, v353)
    v574 = g.vmath('NORMALIZE', v573)
    v575 = g.vmath('DOT_PRODUCT', v574, v537)
    v576 = g.math('MULTIPLY', v575, v575)
    v577 = g.math('SUBTRACT', 1, v576)
    v578 = g.math('SQRT', v577, 0.0)
    v579 = g.math('MAXIMUM', v578, 0.0001)
    v580 = g.inp('_AnisotropyRange2', False, 0.0)
    v581 = g.math('SUBTRACT', 1, v580)
    v582 = g.math('MAXIMUM', v581, 0)
    v583 = g.math('MULTIPLY', v582, 200)
    v584 = g.math('TRUNC', v583, 0.0)
    v585 = g.math('LOGARITHM', v579, 2.0)
    v586 = g.math('MULTIPLY', v585, v584)
    v587 = g.math('POWER', 2.0, v586)
    v588 = g.math('MULTIPLY', v377, v587)
    v589 = g.math('MULTIPLY', v412, v588)
    v590 = g.math('SUBTRACT', 1, v126)
    v591 = g.inp('_AnisotropyColor2', True, (0.0, 0.0, 0.0))
    v592 = g.inp('_AnisotropyColor2_w', False, 1.0)
    v593 = g.bc(v590)
    v594 = g.vmath('MULTIPLY', v593, v591)
    v595 = g.bc(v589)
    v596 = g.vmath('MULTIPLY', v595, v594)
    v597 = g.inp('_SpecularLine', False, 0.0)
    v598 = g.inp('_LineMap_ST', True, (1.0, 1.0, 0.0))
    v599 = g.inp('_LineMap_ST_w', False, 0.0)
    v600 = g.sep(v598)
    v601 = g.comb(v600[0], v600[1], 0.0)
    v602 = g.vmath('MULTIPLY', v0, v601)
    v603 = g.comb(v600[2], v599, 0.0)
    v604 = g.vmath('ADD', v602, v603)
    g.out_('F11_LineMap_uv', v604, True)
    v605 = g.inp('F11_LineMap', True, (0.0, 0.0, 0.0))
    v606 = g.inp('F11_LineMap_alpha', False, 1.0)
    v607 = g.sep(v605)
    v608 = g.inp('_LineValue', False, 0.0)
    v609 = g.math('MULTIPLY', v608, 2)
    v610 = g.math('SUBTRACT', v609, 1)
    v611 = g.bc(v610)
    v612 = g.vmath('MULTIPLY', v310, v611)
    v613 = g.vmath('ADD', v612, v353)
    v614 = g.vmath('NORMALIZE', v613)
    v615 = g.vmath('DOT_PRODUCT', v614, v537)
    v616 = g.math('MULTIPLY', v615, v615)
    v617 = g.math('SUBTRACT', 1, v616)
    v618 = g.math('SQRT', v617, 0.0)
    v619 = g.math('MAXIMUM', v618, 0.0001)
    v620 = g.sep(v0)
    v621 = g.inp('_LineAmount', False, 300.0)
    v622 = g.math('MULTIPLY', v620[0], v621)
    v623 = g.math('FRACT', v622, 0.0)
    v624 = g.math('SUBTRACT', v623, 0.5)
    v625 = g.math('MAXIMUM', v624, 0)
    v626 = g.math('CEIL', v625, 0.0)
    v627 = g.inp('_UseLineMap', False, 0.0)
    v628 = g.math('MULTIPLY', v626, -1.0)
    v629 = g.math('SUBTRACT', 1, v607[0])
    v630 = g.math('ADD', v628, v629)
    v631 = g.math('MULTIPLY', v627, v630)
    v632 = g.math('ADD', v631, v626)
    v633 = g.inp('_LineIntensity', False, 0.0)
    v634 = g.math('MULTIPLY', v632, v633)
    v635 = g.math('SUBTRACT', 1, v633)
    v636 = g.math('ADD', v634, v635)
    v637 = g.inp('_LineRange', False, 0.0)
    v638 = g.math('SUBTRACT', 1, v637)
    v639 = g.math('MAXIMUM', v638, 0)
    v640 = g.math('MULTIPLY', v639, 200)
    v641 = g.math('TRUNC', v640, 0.0)
    v642 = g.math('SUBTRACT', 1, v636)
    v643 = g.math('MULTIPLY', v642, v570)
    v644 = g.math('ADD', v636, v643)
    v645 = g.math('SUBTRACT', v644, 1)
    v646 = g.math('LOGARITHM', v619, 2.0)
    v647 = g.math('MULTIPLY', v646, v641)
    v648 = g.math('POWER', 2.0, v647)
    v649 = g.math('MULTIPLY', v645, v648)
    v650 = g.math('MULTIPLY', v129, v649)
    v651 = g.math('ADD', v650, 1)
    v652 = g.mixf(v597, 1, v651)
    v653 = g.math('MULTIPLY_ADD', v151, 0, 1)
    v654 = g.vmath('MULTIPLY', v490[1], v490[0])
    v655 = g.bc(v653)
    v656 = g.vmath('MULTIPLY', v654, v655)
    v657 = g.bc(v652)
    v658 = g.vmath('MULTIPLY', v657, v656)
    v659 = g.vmath('DOT_PRODUCT', v658, (0.2126729, 0.7151522, 0.072175))
    v660 = g.inp('_LineSaturation', False, 1.0)
    v661 = g.math('SUBTRACT', 1, v660)
    v662 = g.math('MULTIPLY', v652, v661)
    v663 = g.math('ADD', v662, v660)
    v664 = g.bc(v659)
    v665 = g.vmath('SUBTRACT', v658, v664)
    v666 = g.bc(v663)
    v667 = g.vmath('MULTIPLY', v666, v665)
    v668 = g.bc(v659)
    v669 = g.vmath('ADD', v667, v668)
    v670 = g.math('MULTIPLY', v412, v414)
    v671 = g.bc(v670)
    v672 = g.vmath('MULTIPLY', v671, v567)
    v673 = g.inp('_AnisotropyIntensity', False, 1.0)
    v674 = g.bc(v673)
    v675 = g.vmath('MULTIPLY', v672, v674)
    v676 = g.vmath('MULTIPLY', v675, (5, 5, 5))
    v677 = g.mixv(v570, v596, (0, 0, 0))
    v678 = g.vmath('ADD', v676, v677)
    v679 = g.bc(v493)
    v680 = g.vmath('MULTIPLY', v679, v490[1])
    v681 = g.vmath('MULTIPLY', v680, v678)
    v682 = g.inp('_CharacterParams13', True, (0.0, 0.0, 0.0))
    v683 = g.inp('_CharacterParams13_w', False, 1.0)
    v684 = g.bc(v683)
    v685 = g.vmath('MULTIPLY', v681, v684)
    v686 = g.vmath('ADD', v669, v685)
    v687 = g.vmath('DOT_PRODUCT', v686, (0.2126729, 0.7151522, 0.072175))
    v688 = g.math('SUBTRACT', v687, 0.5)
    v689 = g.clampn(v688, 0, 0.5)
    v690 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v691 = g.inp('_CharacterParams9_w', False, 0.4)
    v692 = g.group_named('RCE_ComputeSkinDir', [('camFwd', v109), ('_CharacterParams9', v690), ('_CharacterParams9_w', v691)])
    v693 = g.vtrans(v309, 'WORLD', 'CAMERA', 'VECTOR')
    v694 = g.sep(v693)
    v695 = g.math('MULTIPLY', v694[0], v694[0])
    v696 = g.math('MULTIPLY', v694[1], v694[1])
    v697 = g.math('ADD', v695, v696)
    v698 = g.math('INVERSE_SQRT', v697, 0.0)
    v699 = g.math('MULTIPLY', v694[0], v698)
    v700 = g.math('MULTIPLY', v694[1], v698)
    v701 = g.comb(v699, v700, 0.0)
    v702 = g.sep(v8)
    v703 = g.comb(v702[0], v702[1], 0.0)
    v704 = g.comb(v9, v9, 0.0)
    v705 = g.vmath('DIVIDE', v703, v704)
    v706 = g.inp('_ScreenParams', True, (1920.0, 1080.0, 1.0005208))
    v707 = g.inp('_ScreenParams_w', False, 1.0009259)
    v708 = g.sep(v706)
    v709 = g.math('DIVIDE', v708[1], v708[0])
    v710 = g.comb(v709, 1, 0.0)
    v711 = g.vmath('MULTIPLY', v701, v710)
    v712 = g.comb(v691, v691, 0.0)
    v713 = g.vmath('MULTIPLY', v711, v712)
    v714 = g.vmath('MULTIPLY', v713, (0.006, 0.006, 0.0))
    v715 = g.vmath('ADD', v705, v714)
    v716 = g.comb(v708[0], v708[1], 0.0)
    v717 = g.vmath('DIVIDE', (1, 1, 0.0), v716)
    v718 = g.comb(v708[0], v708[1], 0.0)
    v719 = g.vmath('DIVIDE', (1, 1, 0.0), v718)
    v720 = g.vmath('SUBTRACT', (1, 1, 0.0), v719)
    v721 = g.vmath('MAXIMUM', v715, v717)
    v722 = g.vmath('MINIMUM', v721, v720)
    v723 = g.inp('_ZBufferParams', True, (9999.0, 1.0, 9.999))
    v724 = g.inp('_ZBufferParams_w', False, 0.001)
    v725 = g.sep(v723)
    v726 = g.math('MULTIPLY', v725[2], 0.0)
    v727 = g.math('ADD', v726, v724)
    v728 = g.math('DIVIDE', 1, v727)
    v729 = g.math('SUBTRACT', v728, v9)
    v730 = g.math('SUBTRACT', v729, 0.1)
    v731 = g.math('MULTIPLY', v730, 10)
    v732 = g.clampn(v731)
    v733 = g.math('MULTIPLY', v732, v732)
    v734 = g.math('MULTIPLY', 2, v732)
    v735 = g.math('SUBTRACT', 3, v734)
    v736 = g.math('MULTIPLY', v733, v735)
    v737 = g.vmath('DOT_PRODUCT', v316[0], v692[0])
    v738 = g.math('ADD', v737, 1)
    v739 = g.clampn(v738)
    v740 = g.math('MINIMUM', v128, v739)
    v741 = g.math('MINIMUM', v128, v740)
    v742 = g.inp('_CharacterParams8', True, (0.0, 0.0, 0.0))
    v743 = g.inp('_CharacterParams8_w', False, 1.0)
    v744 = g.group_named('RCE_ComputeSkinSpec', [('skinDir', v692[0]), ('N', v309), ('diffColor', v413), ('skinShadow', v741), ('skinAmt', v736), ('_CharacterParams8', v742), ('_CharacterParams8_w', v743), ('_CharacterParams9', v690), ('_CharacterParams9_w', v691)])
    v745 = g.math('SUBTRACT', 1, v420[0])
    v746 = g.math('MULTIPLY', v745, v142)
    v747 = g.vmath('MULTIPLY', v421, (1, 1, 1))
    v748 = g.group_named('RCE_BRDF_SubsurfaceSpec_Endfield', [('N', v309), ('V', v102), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2]), ('adjXZLen', v140[3]), ('camLightFacing', v746), ('mask', v128), ('diffColorLum', v416), ('diffColor', v413), ('subsurfLight', v747)])
    v749 = g.math('MULTIPLY', v689, v689)
    v750 = g.math('ADD', v749, 1)
    v751 = g.bc(v687)
    v752 = g.vmath('SUBTRACT', v686, v751)
    v753 = g.bc(v750)
    v754 = g.vmath('MULTIPLY', v753, v752)
    v755 = g.bc(v687)
    v756 = g.vmath('ADD', v754, v755)
    v757 = g.vmath('ADD', v756, v744[0])
    v758 = g.vmath('ADD', v757, v748[0])
    v759 = g.inp('_EnableVFXColorAdjustment', False, 0.0)
    v760 = g.math('GREATER_THAN', v759, 0.5)
    v761 = g.vmath('DOT_PRODUCT', v309, v102)
    v762 = g.clampn(v761)
    v763 = g.inp('_ColorAdjustmentContrast', False, 1.0)
    v764 = g.inp('_ColorAdjustmentSaturation', False, 1.0)
    v765 = g.inp('_ColorAdjustmentRimWidth', False, 0.35)
    v766 = g.inp('_ColorAdjustmentBrightness', False, 1.0)
    v767 = g.inp('_ColorAdjustmentColorBlend', True, (1.0, 1.0, 1.0))
    v768 = g.inp('_ColorAdjustmentColorBlend_w', False, 0.0)
    v769 = g.inp('_ColorAdjustmentRimColor', True, (1.0, 1.0, 1.0))
    v770 = g.inp('_ColorAdjustmentRimColor_w', False, 1.0)
    v771 = g.inp('_ColorAdjustmentRimIntensity', False, 4.0)
    v772 = g.group_named('RCE_VFXColorAdjust', [('litColor', v758), ('NdotV', v762), ('rimMod', 1), ('_ColorAdjustmentContrast', v763), ('_ColorAdjustmentSaturation', v764), ('_ColorAdjustmentRimWidth', v765), ('_ColorAdjustmentBrightness', v766), ('_ColorAdjustmentColorBlend', v767), ('_ColorAdjustmentColorBlend_w', v768), ('_ColorAdjustmentRimColor', v769), ('_ColorAdjustmentRimColor_w', v770), ('_ColorAdjustmentRimIntensity', v771)])
    v773 = g.mixv(v760, v758, v772[0])
    v774 = g.sep(v114)
    v775 = g.bc(v774[0])
    v776 = g.vmath('DIVIDE', v773, v775)
    v777 = g.math('COMPARE', v78, 1, 1e-05)
    v778 = g.mixf(v777, 1, v100)
    v779 = g.math('MULTIPLY', v83, v778)
    v780 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v781 = g.math('SUBTRACT', v780, 0)
    v782 = g.math('CEIL', v781, 0.0)
    v783 = g.math('MAXIMUM', v782, 0.0)
    g.out_('Z0_it', v783, False)
    g.out_('Z0_s_N', v59, True)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_lightAccum', (0, 0, 0), True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_positionWS', v15, True)
    g.out_('Z0_r_albedo', v150, True)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pixelLightCount', v780, False)
    v784 = g.inp('Z0_o_N', True)
    v785 = g.inp('Z0_o_Lloop0', False)
    v786 = g.inp('Z0_o_lightAccum', True)
    v787 = g.inp('Z0_o_lightIndex', False)
    v788 = g.inp('Z0_o_positionWS', True)
    v789 = g.vmath('ADD', v776, v786)
    v790 = g.sep(v776)
    v791 = g.sep(v789)
    v792 = g.comb(v791[0], v791[1], v791[2])
    g.out_('ret_gBuffer0', v792, True)
    g.out_('ret_gBuffer0_w', v779, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v776, True)
    g.out_('ret_color_w', v778, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v158, False)


def build_Ruri_Endfield_Uber_Fur():
    t = _tree('Ruri Endfield Uber Fur')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.sep(v5)
    v152 = g.inp('_FurDyeEnable', False, 0.0)
    v153 = g.math('SUBTRACT', 1.0, v152)
    v154 = g.mixv(v153, (0.0, 0.0, 0.0), v150)
    v155 = g.mixf(v153, 0.0, 1.0)
    v156 = g.math('SUBTRACT', 1.0, v155)
    v157 = g.sep(v0)
    v158 = g.inp('_BaseMap_ST', True, (1.0, 1.0, 0.0))
    v159 = g.inp('_BaseMap_ST_w', False, 0.0)
    v160 = g.sep(v158)
    v161 = g.math('SUBTRACT', v157[0], v160[2])
    v162 = g.math('ABSOLUTE', v160[0], 0.0)
    v163 = g.math('MAXIMUM', 0.001, v162)
    v164 = g.math('DIVIDE', v161, v163)
    v165 = g.inp('_FurDyeMap_ST', True, (1.0, 1.0, 0.0))
    v166 = g.inp('_FurDyeMap_ST_w', False, 0.0)
    v167 = g.sep(v165)
    v168 = g.math('MULTIPLY_ADD', v164, v167[0], v167[2])
    v169 = g.math('SUBTRACT', v157[1], v159)
    v170 = g.math('ABSOLUTE', v160[1], 0.0)
    v171 = g.math('MAXIMUM', 0.001, v170)
    v172 = g.math('DIVIDE', v169, v171)
    v173 = g.math('MULTIPLY_ADD', v172, v167[1], v166)
    v174 = g.comb(v168, v173, 0.0)
    v175 = g.math('SUBTRACT', 1.0, v155)
    g.out_('F4_FurDyeMap_uv', v174, True)
    v176 = g.inp('F4_FurDyeMap', True, (0.0, 0.0, 0.0))
    v177 = g.inp('F4_FurDyeMap_alpha', False, 1.0)
    v178 = g.math('SUBTRACT', 1.0, v155)
    v179 = g.vmath('SUBTRACT', (1, 1, 1), v150)
    v180 = g.vmath('SUBTRACT', (1, 1, 1), v176)
    v181 = g.vmath('MULTIPLY', v179, v180)
    v182 = g.vmath('SUBTRACT', (1, 1, 1), v181)
    v183 = g.math('SUBTRACT', 1.0, v155)
    v184 = g.inp('_FurDyeIntensity', False, 1.0)
    v185 = g.mixv(v184, v150, v182)
    v186 = g.mixv(v183, v154, v185)
    v187 = g.mixf(v183, v155, 1.0)
    v188 = g.inp('_UseShadowLutTex', False, 0.0)
    v189 = g.sep(v186)
    v190 = g.math('MULTIPLY', v189[0], 12.92)
    v191 = g.math('POWER', v189[0], 0.4166667)
    v192 = g.math('MULTIPLY', 1.055, v191)
    v193 = g.math('SUBTRACT', v192, 0.055)
    v194 = g.math('LESS_THAN', v189[0], 0.0031308)
    v195 = g.math('SUBTRACT', 1.0, v194)
    v196 = g.mixf(v195, v190, v193)
    v197 = g.clampn(v196)
    v198 = g.math('MULTIPLY', v189[1], 12.92)
    v199 = g.math('POWER', v189[1], 0.4166667)
    v200 = g.math('MULTIPLY', 1.055, v199)
    v201 = g.math('SUBTRACT', v200, 0.055)
    v202 = g.math('LESS_THAN', v189[1], 0.0031308)
    v203 = g.math('SUBTRACT', 1.0, v202)
    v204 = g.mixf(v203, v198, v201)
    v205 = g.clampn(v204)
    v206 = g.math('MULTIPLY', v189[2], 12.92)
    v207 = g.math('POWER', v189[2], 0.4166667)
    v208 = g.math('MULTIPLY', 1.055, v207)
    v209 = g.math('SUBTRACT', v208, 0.055)
    v210 = g.math('LESS_THAN', v189[2], 0.0031308)
    v211 = g.math('SUBTRACT', 1.0, v210)
    v212 = g.mixf(v211, v206, v209)
    v213 = g.clampn(v212)
    v214 = g.math('MULTIPLY', v213, 31)
    v215 = g.math('FLOOR', v214, 0.0)
    v216 = g.math('MULTIPLY', v215, 0.03125)
    v217 = g.math('MULTIPLY', v197, 0.0302734375)
    v218 = g.math('ADD', v216, v217)
    v219 = g.math('ADD', v218, 0.00048828125)
    v220 = g.math('MULTIPLY', v205, 0.96875)
    v221 = g.math('ADD', v220, 0.015625)
    v222 = g.comb(v219, v221, 0.0)
    g.out_('F5_ShadowLutTex_uv', v222, True)
    v223 = g.inp('F5_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v224 = g.inp('F5_ShadowLutTex_alpha', False, 1.0)
    v225 = g.math('ADD', v219, 0.03125)
    v226 = g.comb(v225, v221, 0.0)
    g.out_('F6_ShadowLutTex_uv', v226, True)
    v227 = g.inp('F6_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v228 = g.inp('F6_ShadowLutTex_alpha', False, 1.0)
    v229 = g.math('MULTIPLY', v213, 31)
    v230 = g.math('SUBTRACT', v229, v215)
    v231 = g.mixv(v230, v223, v227)
    v232 = g.mixv(v188, (0.0, 0.0, 0.0), v231)
    v233 = g.mixf(v188, 0.0, 1.0)
    v234 = g.math('SUBTRACT', 1.0, v233)
    v235 = g.inp('_ShadowColorBrightness', False, 0.5)
    v236 = g.bc(v235)
    v237 = g.vmath('MULTIPLY', v186, v236)
    v238 = g.math('SUBTRACT', 1.0, v233)
    v239 = g.vmath('DOT_PRODUCT', v237, (0.2126729, 0.7151522, 0.072175))
    v240 = g.math('SUBTRACT', 1.0, v233)
    v241 = g.inp('_ShadowColorSaturation', False, 1.0)
    v242 = g.bc(v239)
    v243 = g.vmath('SUBTRACT', v237, v242)
    v244 = g.bc(v241)
    v245 = g.vmath('MULTIPLY', v244, v243)
    v246 = g.bc(v239)
    v247 = g.vmath('ADD', v245, v246)
    v248 = g.mixv(v240, v232, v247)
    v249 = g.mixf(v240, v233, 1.0)
    v250 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v251 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v252 = g.sep(v250)
    v253 = g.math('MULTIPLY', v252[0], v251)
    v254 = g.math('MULTIPLY', v253, 2)
    v255 = g.math('SUBTRACT', v254, 1)
    v256 = g.math('MULTIPLY', v252[1], 2)
    v257 = g.math('SUBTRACT', v256, 1)
    v258 = g.math('MULTIPLY', v255, v255)
    v259 = g.math('MULTIPLY', v257, v257)
    v260 = g.math('ADD', v258, v259)
    v261 = g.clampn(v260)
    v262 = g.math('SUBTRACT', 1, v261)
    v263 = g.math('SQRT', v262, 0.0)
    v264 = g.math('MAXIMUM', v263, 1E-16)
    v265 = g.vmath('NORMALIZE', v16)
    v266 = g.vmath('NORMALIZE', v17)
    v267 = g.vmath('CROSS_PRODUCT', v265, v266)
    v268 = g.bc(v4)
    v269 = g.vmath('MULTIPLY', v267, v268)
    v270 = g.math('MULTIPLY', v255, v27)
    v271 = g.bc(v270)
    v272 = g.vmath('MULTIPLY', v271, v266)
    v273 = g.math('MULTIPLY', v257, v27)
    v274 = g.bc(v273)
    v275 = g.vmath('MULTIPLY', v274, v269)
    v276 = g.vmath('ADD', v272, v275)
    v277 = g.bc(v264)
    v278 = g.vmath('MULTIPLY', v277, v265)
    v279 = g.vmath('ADD', v276, v278)
    v280 = g.vmath('NORMALIZE', v279)
    v281 = g.bc(v98)
    v282 = g.vmath('MULTIPLY', v281, v280)
    v283 = g.vmath('NORMALIZE', v16)
    v284 = g.bc(v98)
    v285 = g.vmath('MULTIPLY', v284, v283)
    v286 = g.mixf(v20, 1, v264)
    v287 = g.mixv(v20, v285, v282)
    g.out_('F7_FurDirMap_uv', v0, True)
    v288 = g.inp('F7_FurDirMap', True, (0.5, 0.5, 1.0))
    v289 = g.inp('F7_FurDirMap_alpha', False, 1.0)
    v290 = g.comb(v151[0], v151[0], 0.0)
    v291 = g.vmath('DOT_PRODUCT', v290, (12.9898, 78.233, 0.0))
    v292 = g.math('SINE', v291, 0.0)
    v293 = g.math('MULTIPLY', v292, 43758.5469)
    v294 = g.math('FRACT', v293, 0.0)
    v295 = g.math('MULTIPLY', v294, 2)
    v296 = g.math('SUBTRACT', v295, 1)
    v297 = g.inp('_FurNoise', False, 0.0)
    v298 = g.math('MULTIPLY', v296, v297)
    v299 = g.math('MULTIPLY', v298, 0.05)
    v300 = g.sep(v288)
    v301 = g.math('MULTIPLY', v300[0], 2)
    v302 = g.math('SUBTRACT', v301, 1)
    v303 = g.inp('_FurDirMapEnable', False, 0.0)
    v304 = g.math('MULTIPLY', v302, v303)
    v305 = g.math('MULTIPLY', v304, 0.005)
    v306 = g.math('ADD', v305, v299)
    v307 = g.math('MULTIPLY', v300[1], 2)
    v308 = g.math('SUBTRACT', v307, 1)
    v309 = g.math('MULTIPLY', v308, v303)
    v310 = g.math('MULTIPLY', v309, 0.005)
    v311 = g.math('ADD', v310, v299)
    v312 = g.comb(v306, v311, 0.0)
    v313 = g.sep(v312)
    v314 = g.math('MULTIPLY', v151[0], v313[0])
    v315 = g.math('SUBTRACT', v157[0], v314)
    v316 = g.inp('_FurMap_ST', True, (1.0, 1.0, 0.0))
    v317 = g.inp('_FurMap_ST_w', False, 0.0)
    v318 = g.sep(v316)
    v319 = g.math('MULTIPLY', v315, v318[0])
    v320 = g.math('ADD', v319, v318[2])
    v321 = g.math('MULTIPLY', v151[0], v313[1])
    v322 = g.math('SUBTRACT', v157[1], v321)
    v323 = g.math('MULTIPLY', v322, v318[0])
    v324 = g.math('ADD', v323, v317)
    v325 = g.comb(v320, v324, 0.0)
    g.out_('F8_FurMap_uv', v325, True)
    v326 = g.inp('F8_FurMap', True, (1.0, 1.0, 1.0))
    v327 = g.inp('F8_FurMap_alpha', False, 1.0)
    v328 = g.sep(v326)
    v329 = g.inp('_FurCutoffEnd', False, 1.0)
    v330 = g.inp('_FurCutoffStart', False, 0.0)
    v331 = g.math('SUBTRACT', v329, v330)
    v332 = g.math('MULTIPLY', v151[0], v331)
    v333 = g.math('ADD', v332, v330)
    v334 = g.math('SQRT', v333, 0.0)
    v335 = g.inp('_FurSharpen', False, 0.0)
    v336 = g.mixf(v335, v333, v334)
    v337 = g.math('SUBTRACT', v336, 0.25)
    v338 = g.math('MAXIMUM', v337, 0)
    v339 = g.math('ADD', v336, 0.25)
    v340 = g.math('MINIMUM', v339, 1)
    v341 = g.math('MULTIPLY', v300[2], v328[0])
    v342 = g.math('SUBTRACT', v341, v338)
    v343 = g.math('SUBTRACT', v340, v338)
    v344 = g.math('DIVIDE', v342, v343)
    v345 = g.clampn(v344)
    v346 = g.math('MULTIPLY', v345, v345)
    v347 = g.math('MULTIPLY', 2, v345)
    v348 = g.math('SUBTRACT', 3, v347)
    v349 = g.math('MULTIPLY', v346, v348)
    v350 = g.math('GREATER_THAN', v151[0], 0.01)
    v351 = g.math('SUBTRACT', 1.0, v350)
    v352 = g.mixf(v351, 0, 1)
    v353 = g.math('SUBTRACT', 1, v349)
    v354 = g.math('MULTIPLY', v352, v353)
    v355 = g.math('ADD', v354, v349)
    v356 = g.vmath('NORMALIZE', v16)
    v357 = g.math('MULTIPLY', v151[0], v151[0])
    v358 = g.math('MULTIPLY', v357, v151[0])
    v359 = g.math('SUBTRACT', 1, v358)
    v360 = g.vmath('DOT_PRODUCT', v356, v102)
    v361 = g.math('ADD', v359, v360)
    v362 = g.inp('_FurEdgeFade', False, 0.0)
    v363 = g.math('SUBTRACT', v361, v362)
    v364 = g.math('CEIL', v151[0], 0.0)
    v365 = g.math('MULTIPLY', v355, v363)
    v366 = g.clampn(v365)
    v367 = g.math('SUBTRACT', v366, 1)
    v368 = g.math('MULTIPLY', v364, v367)
    v369 = g.math('ADD', v368, 1)
    v370 = g.math('SUBTRACT', v369, 0.003)
    v371 = g.math('LESS_THAN', v370, 0.0)
    v372 = g.math('SUBTRACT', 1.0, v371)
    v373 = g.math('MULTIPLY', 1.0, v372)
    v374 = g.inp('_FurAO', False, 1.0)
    v375 = g.group_named('RCE_Shell_AOFromNormalZ', [('shellIdx', v151[0]), ('nrmZ_raw', v286), ('_FurAO', v374)])
    v376 = g.math('MULTIPLY', v375[0], v128)
    v377 = g.inp('_EnableCharacterVFX', False, 0.0)
    v378 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v379 = g.inp('_Time_w', False, 0.0)
    v380 = g.sep(v378)
    v381 = g.inp('_VFXSpecialParam', True, (0.0, 0.0, 0.0))
    v382 = g.inp('_VFXSpecialParam_w', False, 0.0)
    v383 = g.sep(v381)
    v384 = g.math('MULTIPLY_ADD', v383[2], v380[1], v157[0])
    v385 = g.inp('_VFXSpecialBlendTex_ST', True, (1.0, 1.0, 0.0))
    v386 = g.inp('_VFXSpecialBlendTex_ST_w', False, 0.0)
    v387 = g.sep(v385)
    v388 = g.math('MULTIPLY_ADD', v384, v387[0], v387[2])
    v389 = g.math('MULTIPLY_ADD', v382, v380[1], v157[1])
    v390 = g.math('MULTIPLY_ADD', v389, v387[1], v386)
    v391 = g.comb(v388, v390, 0.0)
    g.out_('F9_VFXSpecialBlendTex_uv', v391, True)
    v392 = g.inp('F9_VFXSpecialBlendTex', True, (0.0, 0.0, 0.0))
    v393 = g.inp('F9_VFXSpecialBlendTex_alpha', False, 1.0)
    v394 = g.sep(v392)
    v395 = g.inp('_VFXSpecialBlendTexRForDisturb', False, 1.0)
    v396 = g.math('MULTIPLY', v394[0], v395)
    v397 = g.comb(v396, v396, 0.0)
    v398 = g.vmath('ADD', v0, v397)
    v399 = g.sep(v398)
    v400 = g.math('MULTIPLY_ADD', v383[0], v380[1], v399[0])
    v401 = g.inp('_VFXSpecialMainTex_ST', True, (1.0, 1.0, 0.0))
    v402 = g.inp('_VFXSpecialMainTex_ST_w', False, 0.0)
    v403 = g.sep(v401)
    v404 = g.math('MULTIPLY_ADD', v400, v403[0], v403[2])
    v405 = g.math('MULTIPLY_ADD', v383[1], v380[1], v399[1])
    v406 = g.math('MULTIPLY_ADD', v405, v403[1], v402)
    v407 = g.comb(v404, v406, 0.0)
    g.out_('F10_VFXSpecialMainTex_uv', v407, True)
    v408 = g.inp('F10_VFXSpecialMainTex', True, (1.0, 1.0, 1.0))
    v409 = g.inp('F10_VFXSpecialMainTex_alpha', False, 1.0)
    v410 = g.sep(v408)
    v411 = g.inp('_UseVFXMainTexAsAlpha', False, 0.0)
    v412 = g.mixf(v411, v409, v410[0])
    v413 = g.mixv(v411, v408, (1, 1, 1))
    v414 = g.vmath('NORMALIZE', v16)
    v415 = g.vmath('DOT_PRODUCT', v102, v414)
    v416 = g.inp('_VFXFresnelBias', False, 0.0)
    v417 = g.math('ADD', v415, v416)
    v418 = g.clampn(v417)
    v419 = g.math('LOGARITHM', v418, 2.0)
    v420 = g.inp('_VFXFresnelPower', False, 1.0)
    v421 = g.math('MULTIPLY', v419, v420)
    v422 = g.math('POWER', 2.0, v421)
    v423 = g.math('SUBTRACT', 1, v422)
    v424 = g.inp('_VFXFresnelFlip', False, 0.001)
    v425 = g.mixf(v424, v423, v422)
    v426 = g.inp('_VFXColorAlpha', False, 1.0)
    v427 = g.inp('_VFXColor', True, (1.0, 1.0, 1.0))
    v428 = g.inp('_VFXColor_w', False, 1.0)
    v429 = g.math('MULTIPLY', v426, v428)
    v430 = g.inp('_SpecialDissolveScheduleOffset', False, 0.0)
    v431 = g.math('MULTIPLY', v430, 2.02)
    v432 = g.math('SUBTRACT', v431, 1.01)
    v433 = g.math('SUBTRACT', v394[0], v432)
    v434 = g.math('MULTIPLY', v433, -1.0)
    v435 = g.clampn(v434)
    v436 = g.mixv(v377, (0, 0, 0), v392)
    v437 = g.mixf(v377, 0, v393)
    v438 = g.mixf(v377, 0, v412)
    v439 = g.mixv(v377, (0, 0, 0), v413)
    v440 = g.mixf(v377, 0, v425)
    v441 = g.mixf(v377, 0, v429)
    v442 = g.mixf(v377, 0, v433)
    v443 = g.mixf(v377, 0, v435)
    v444 = g.group_named('RCE_GetObjectFlatDir', [('positionWS', v15)])
    v445 = g.inp('_CharacterParams2', True, (0.7830188, 0.8293082, 1.0))
    v446 = g.inp('_CharacterParams2_w', False, 0.0)
    v447 = g.inp('_CharacterParams5', True, (1.0, 1.0, 1.0))
    v448 = g.inp('_CharacterParams5_w', False, 1.0)
    v449 = g.sep(v110)
    v450 = g.mixv(v449[1], v131, v447)
    v451 = g.math('MULTIPLY', v129, 0.04)
    v452 = g.math('SUBTRACT', 1, v127)
    v453 = g.math('MULTIPLY', v452, 0.96)
    v454 = g.bc(v453)
    v455 = g.vmath('MULTIPLY', v454, v186)
    v456 = g.bc(v451)
    v457 = g.vmath('SUBTRACT', v186, v456)
    v458 = g.bc(v127)
    v459 = g.vmath('MULTIPLY', v458, v457)
    v460 = g.bc(v451)
    v461 = g.vmath('ADD', v459, v460)
    v462 = g.bc(v453)
    v463 = g.vmath('MULTIPLY', v462, v248)
    v464 = g.math('MULTIPLY', v126, v126)
    v465 = g.math('MAXIMUM', v464, 0.0078125)
    v466 = g.math('MULTIPLY', v465, v465)
    v467 = g.vmath('DOT_PRODUCT', v287, v140[0])
    v468 = g.inp('_FurTTIntensity', False, 0.5)
    v469 = g.group_named('RCE_Shell_TransmittedNdotL_Endfield', [('furSample', v328[0]), ('shellIdx', v151[0]), ('geomNdotL', v467), ('camLightDot', v142), ('_FurNoise', v297), ('_FurTTIntensity', v468)])
    v470 = g.math('MULTIPLY', 0.5, v469[0])
    v471 = g.math('MULTIPLY', v470, v469[0])
    v472 = g.math('SUBTRACT', 0.5, v471)
    v473 = g.math('SUBTRACT', 1, v449[0])
    v474 = g.math('MULTIPLY', v142, v141[1])
    v475 = g.math('MULTIPLY', v473, v474)
    v476 = g.math('MULTIPLY', v475, v472)
    v477 = g.math('ADD', v476, v469[0])
    v478 = g.inp('_UseDiffRampMap', False, 0.0)
    v479 = g.math('SUBTRACT', 1.0, v478)
    v480 = g.math('MULTIPLY', v477, 0.5)
    v481 = g.math('ADD', v480, 0.5)
    v482 = g.clampn(v481)
    v483 = g.mixf(v479, 0.0, 0)
    v484 = g.mixf(v479, 0.0, 0)
    v485 = g.mixv(v479, (0.0, 0.0, 0.0), (1, 1, 1))
    v486 = g.mixf(v479, 0.0, v482)
    v487 = g.mixf(v479, 0.0, 1.0)
    v488 = g.math('SUBTRACT', 1.0, v487)
    v489 = g.math('MULTIPLY', v137, v449[0])
    v490 = g.math('ADD', v489, v477)
    v491 = g.math('MULTIPLY', 1, -1.0)
    v492 = g.clampn(v490, v491, 1)
    v493 = g.math('MULTIPLY', v492, 0.5)
    v494 = g.math('ADD', v493, 0.5)
    v495 = g.math('SUBTRACT', 1.0, v487)
    v496 = g.comb(v494, 0.5, 0.0)
    g.out_('F11_DiffRampMap_uv', v496, True)
    v497 = g.inp('F11_DiffRampMap', True, (1.0, 1.0, 1.0))
    v498 = g.inp('F11_DiffRampMap_alpha', False, 1.0)
    v499 = g.math('SUBTRACT', 1.0, v487)
    v500 = g.sep(v497)
    v501 = g.math('MAXIMUM', v500[1], v500[2])
    v502 = g.math('MAXIMUM', v500[0], v501)
    v503 = g.math('MINIMUM', v500[1], v500[2])
    v504 = g.math('MINIMUM', v500[0], v503)
    v505 = g.math('SUBTRACT', v502, v504)
    v506 = g.mixf(v499, v483, v505)
    v507 = g.math('SUBTRACT', 1.0, v487)
    v508 = g.vmath('DOT_PRODUCT', v287, v109)
    v509 = g.math('MULTIPLY', v508, 0.5)
    v510 = g.math('ADD', v509, 0.5)
    v511 = g.math('SUBTRACT', 1.0, v487)
    v512 = g.comb(v510, 0.5, 0.0)
    g.out_('F12_DiffRampMap_uv', v512, True)
    v513 = g.inp('F12_DiffRampMap', True, (1.0, 1.0, 1.0))
    v514 = g.inp('F12_DiffRampMap_alpha', False, 1.0)
    v515 = g.mixf(v511, v484, v514)
    v516 = g.math('SUBTRACT', 1.0, v487)
    v517 = g.mixv(v516, v485, v497)
    v518 = g.mixf(v516, v486, v498)
    v519 = g.mixf(v516, v487, 1.0)
    v520 = g.math('SUBTRACT', v135, 0)
    v521 = g.math('DIVIDE', v520, 1)
    v522 = g.clampn(v521)
    v523 = g.math('MULTIPLY', v522, v522)
    v524 = g.math('MULTIPLY', 2.0, v522)
    v525 = g.math('SUBTRACT', 3.0, v524)
    v526 = g.math('MULTIPLY', v523, v525)
    v527 = g.sep(v138)
    v528 = g.mixf(v527[2], v526, 1)
    v529 = g.math('MINIMUM', v518, v376)
    v530 = g.math('MULTIPLY', v529, v528)
    v531 = g.math('MULTIPLY', v515, v376)
    v532 = g.math('ADD', v531, v518)
    v533 = g.clampn(v532)
    v534 = g.inp('_CharacterParams0', True, (0.0, 0.9, 0.8))
    v535 = g.inp('_CharacterParams0_w', False, 0.8)
    v536 = g.sep(v534)
    v537 = g.bc(v536[2])
    v538 = g.vmath('MULTIPLY', v463, v537)
    v539 = g.vmath('DOT_PRODUCT', v455, (0.2126729, 0.7151522, 0.072175))
    v540 = g.clampn(v116[0], 0, 1.5)
    v541 = g.math('SUBTRACT', 1, v506)
    v542 = g.inp('_CharacterParams6', True, (0.0, 1.0, 4.371139E-08))
    v543 = g.inp('_CharacterParams6_w', False, 0.0)
    v544 = g.inp('_CharacterParams7', True, (0.15, 1.5, 0.5))
    v545 = g.inp('_CharacterParams7_w', False, 0.0)
    v546 = g.group_named('RCE_ComputeNPRDiffuse', [('hemisphereN', v287), ('ambCol', v445), ('brightness', v540), ('blendedLightCol', v450), ('blendedLightInt', 1), ('minShadow', v530), ('combWeight', v533), ('albScaled', v538), ('diffColor', v455), ('rampCol', v517), ('rampChroma', v506), ('rampChromaInv', v541), ('_CharacterParams6', v542), ('_CharacterParams6_w', v543), ('_CharacterParams7', v544), ('_CharacterParams7_w', v545), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139), ('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_CharacterParams0', v534), ('_CharacterParams0_w', v535)])
    v547 = g.math('MULTIPLY', v530, 0.5)
    v548 = g.math('ADD', v547, 0.5)
    v549 = g.math('MULTIPLY', v546[2], v548)
    v550 = g.group_named('RCE_BRDF_GGX_Stylized_Endfield', [('N', v287), ('V', v102), ('adjustedLightDir', v140[0]), ('camFwd', v109), ('roughness', v465)])
    v551 = g.inp('_UseSpecRampMap', False, 0.0)
    v552 = g.math('ADD', v466, 0.0001)
    v553 = g.math('MULTIPLY', v550[1], v552)
    v554 = g.math('MULTIPLY', v550[2], v550[2])
    v555 = g.inp('_SpecRampIridescentMode', False, 0.0)
    v556 = g.mixf(v555, v553, v554)
    v557 = g.math('SUBTRACT', 1, v127)
    v558 = g.math('MULTIPLY', v557, v126)
    v559 = g.comb(v556, v558, 0.0)
    g.out_('F13_SpecRampMap_uv', v559, True)
    v560 = g.inp('F13_SpecRampMap', True, (1.0, 1.0, 1.0))
    v561 = g.inp('F13_SpecRampMap_alpha', False, 1.0)
    v562 = g.vmath('MULTIPLY', v461, v560)
    v563 = g.mixv(v555, v461, v562)
    v564 = g.mixv(v551, v461, v562)
    v565 = g.mixv(v551, v461, v563)
    v566 = g.math('MULTIPLY', v369, 0)
    v567 = g.math('ADD', v566, 1)
    v568 = g.vmath('MULTIPLY', v546[1], v546[0])
    v569 = g.bc(v567)
    v570 = g.vmath('MULTIPLY', v568, v569)
    v571 = g.bc(v549)
    v572 = g.vmath('MULTIPLY', v571, v546[1])
    v573 = g.bc(v550[0])
    v574 = g.vmath('MULTIPLY', v573, v564)
    v575 = g.vmath('MULTIPLY', v572, v574)
    v576 = g.inp('_CharacterParams13', True, (0.0, 0.0, 0.0))
    v577 = g.inp('_CharacterParams13_w', False, 1.0)
    v578 = g.bc(v577)
    v579 = g.vmath('MULTIPLY', v575, v578)
    v580 = g.vmath('ADD', v570, v579)
    v581 = g.vmath('DOT_PRODUCT', v580, (0.2126729, 0.7151522, 0.072175))
    v582 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v583 = g.inp('_CharacterParams9_w', False, 0.4)
    v584 = g.group_named('RCE_ComputeSkinDir', [('camFwd', v109), ('_CharacterParams9', v582), ('_CharacterParams9_w', v583)])
    v585 = g.vmath('DOT_PRODUCT', v444[0], v584[0])
    v586 = g.math('ADD', v585, 1)
    v587 = g.clampn(v586)
    v588 = g.math('MINIMUM', v376, v587)
    v589 = g.vmath('DOT_PRODUCT', v102, v287)
    v590 = g.group_named('RCE_ComputeSkinSmoothFalloff', [('NdotV', v589), ('_CharacterParams9', v582), ('_CharacterParams9_w', v583)])
    v591 = g.inp('_CharacterParams8', True, (0.0, 0.0, 0.0))
    v592 = g.inp('_CharacterParams8_w', False, 1.0)
    v593 = g.group_named('RCE_ComputeSkinSpec', [('skinDir', v584[0]), ('N', v287), ('diffColor', v455), ('skinShadow', v588), ('skinAmt', v590[0]), ('_CharacterParams8', v591), ('_CharacterParams8_w', v592), ('_CharacterParams9', v582), ('_CharacterParams9_w', v583)])
    v594 = g.math('SUBTRACT', 1, v449[0])
    v595 = g.math('MULTIPLY', v594, v142)
    v596 = g.vmath('MULTIPLY', v450, (1, 1, 1))
    v597 = g.group_named('RCE_BRDF_SubsurfaceSpec_Endfield', [('N', v287), ('V', v102), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2]), ('adjXZLen', v140[3]), ('camLightFacing', v595), ('mask', v376), ('diffColorLum', v539), ('diffColor', v455), ('subsurfLight', v596)])
    v598 = g.clampn(v116[0], 0.5, 1.5)
    v599 = g.math('MULTIPLY', v598, v535)
    v600 = g.math('MULTIPLY', v546[2], v599)
    v601 = g.vmath('SCALE', v102, s=-1.0)
    v602 = g.vmath('DOT_PRODUCT', v287, v601)
    v603 = g.math('MULTIPLY', 2.0, v602)
    v604 = g.vmath('SCALE', v287, s=v603)
    v605 = g.vmath('SUBTRACT', v601, v604)
    v606 = g.math('MAXIMUM', v126, 0.001)
    v607 = g.math('LOGARITHM', v606, 2.0)
    v608 = g.math('MULTIPLY', v607, 1.2)
    v609 = g.math('ADD', v608, 5)
    v610 = g.u2b(v605)
    g.out_('F14_IBL_unity_SpecCube0_dir', v610, True)
    g.out_('F14_IBL_unity_SpecCube0_mip', v609, False)
    v611 = g.inp('F14_IBL_unity_SpecCube0', True, (0.2159, 0.2159, 0.2159))
    v612 = g.inp('F14_IBL_unity_SpecCube0_alpha', False, 1.0)
    v613 = g.group_named('RCE_IBL_SplitSumCombine', [('cubeSample', v611), ('NdotV_spec', v550[2]), ('roughness', v465), ('specRampEnv', v565), ('ambIntensity', v600), ('ambCol', v445)])
    v614 = g.math('SUBTRACT', v581, 0.5)
    v615 = g.clampn(v614, 0, 0.5)
    v616 = g.math('MULTIPLY', v615, v615)
    v617 = g.math('ADD', v616, 1)
    v618 = g.bc(v581)
    v619 = g.vmath('SUBTRACT', v580, v618)
    v620 = g.bc(v617)
    v621 = g.vmath('MULTIPLY', v620, v619)
    v622 = g.bc(v581)
    v623 = g.vmath('ADD', v621, v622)
    v624 = g.vmath('ADD', v623, v593[0])
    v625 = g.vmath('ADD', v624, v597[0])
    v626 = g.vmath('ADD', v625, v613[0])
    v627 = g.math('MULTIPLY', v441, v438)
    v628 = g.math('ADD', v627, v437)
    v629 = g.inp('_VFXBlendTint', True, (1.0, 1.0, 1.0))
    v630 = g.inp('_VFXBlendTint_w', False, 1.0)
    v631 = g.math('MULTIPLY', v628, v630)
    v632 = g.clampn(v631)
    v633 = g.inp('_VFXColorIntensity', False, 1.0)
    v634 = g.bc(v633)
    v635 = g.vmath('MULTIPLY', v634, v427)
    v636 = g.vmath('MULTIPLY', v635, v439)
    v637 = g.bc(v632)
    v638 = g.vmath('MULTIPLY', v436, v637)
    v639 = g.vmath('MULTIPLY', v638, v629)
    v640 = g.vmath('ADD', v639, v636)
    v641 = g.inp('_VFXFresnelColor', True, (1.0, 1.0, 1.0))
    v642 = g.inp('_VFXFresnelColor_w', False, 1.0)
    v643 = g.bc(v443)
    v644 = g.vmath('MULTIPLY', v643, v641)
    v645 = g.bc(v633)
    v646 = g.vmath('MULTIPLY', v644, v645)
    v647 = g.mixv(v443, v640, v646)
    v648 = g.math('MULTIPLY', v440, v642)
    v649 = g.clampn(v442)
    v650 = g.math('MULTIPLY', v649, v441)
    v651 = g.math('MULTIPLY', v650, v438)
    v652 = g.clampn(v651)
    v653 = g.inp('_VFXFresnelAffectOpacity', False, 1.0)
    v654 = g.mixf(v653, 1, v440)
    v655 = g.math('MULTIPLY', v652, v654)
    v656 = g.mixv(v648, v647, v641)
    v657 = g.bc(v655)
    v658 = g.vmath('MULTIPLY', v657, v656)
    v659 = g.bc(v567)
    v660 = g.vmath('MULTIPLY', v658, v659)
    v661 = g.vmath('ADD', v626, v660)
    v662 = g.mixv(v377, v626, v661)
    v663 = g.sep(v114)
    v664 = g.bc(v663[0])
    v665 = g.vmath('DIVIDE', v662, v664)
    v666 = g.math('COMPARE', v78, 1, 1e-05)
    v667 = g.mixf(v666, 1, v369)
    v668 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v669 = g.math('SUBTRACT', v668, 0)
    v670 = g.math('CEIL', v669, 0.0)
    v671 = g.math('MAXIMUM', v670, 0.0)
    g.out_('Z0_it', v671, False)
    g.out_('Z0_s_N', v59, True)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_lightAccum', (0, 0, 0), True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_positionWS', v15, True)
    g.out_('Z0_r_albedo', v150, True)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pixelLightCount', v668, False)
    v672 = g.inp('Z0_o_N', True)
    v673 = g.inp('Z0_o_Lloop0', False)
    v674 = g.inp('Z0_o_lightAccum', True)
    v675 = g.inp('Z0_o_lightIndex', False)
    v676 = g.inp('Z0_o_positionWS', True)
    v677 = g.vmath('ADD', v665, v674)
    v678 = g.sep(v665)
    v679 = g.sep(v677)
    v680 = g.comb(v679[0], v679[1], v679[2])
    g.out_('ret_gBuffer0', v680, True)
    g.out_('ret_gBuffer0_w', v667, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v665, True)
    g.out_('ret_color_w', v369, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v373, False)


def build_Ruri_Endfield_Uber_VFX():
    t = _tree('Ruri Endfield Uber VFX')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v152 = g.inp('_Time_w', False, 0.0)
    v153 = g.sep(v151)
    v154 = g.sep(v5)
    v155 = g.inp('_InParticle', False, 1.0)
    v156 = g.math('MULTIPLY', v154[0], v155)
    v157 = g.math('MULTIPLY', v154[1], v155)
    v158 = g.sep(v7)
    v159 = g.math('MULTIPLY', v156, -1.0)
    v160 = g.math('MULTIPLY_ADD', v158[0], v155, v159)
    v161 = g.math('ADD', v160, v154[0])
    v162 = g.math('MULTIPLY', v157, -1.0)
    v163 = g.math('MULTIPLY_ADD', v158[1], v155, v162)
    v164 = g.math('ADD', v163, v154[1])
    v165 = g.comb(v161, v164, 0.0)
    v166 = g.inp('_UseDisturb', False, 0.0)
    v167 = g.inp('_DisturbUVWeights1', True, (1.0, 0.0, 0.0))
    v168 = g.inp('_DisturbUVWeights1_w', False, 0.0)
    v169 = g.inp('_DisturbUVSpeed1', True, (0.0, 0.0, 0.0))
    v170 = g.inp('_DisturbUVSpeed1_w', False, 0.0)
    v171 = g.inp('_DisturbUVRotateMat1', True, (1.0, 0.0, 0.0))
    v172 = g.inp('_DisturbUVRotateMat1_w', False, 1.0)
    v173 = g.inp('_DisturbTex1_ST', True, (1.0, 1.0, 0.0))
    v174 = g.inp('_DisturbTex1_ST_w', False, 0.0)
    v175 = g.group_named('RCE_ComputeVFXUV_Endfield', [('uv0', v0), ('uv1', v165), ('weights', v167), ('weights_w', v168), ('speed', v169), ('speed_w', v170), ('time', v153[1]), ('customData', v157), ('rotateMat', v171), ('rotateMat_w', v172), ('st', v173), ('st_w', v174), ('disturb', (0, 0, 0.0)), ('useDisturb', 0)])
    g.out_('F4_DisturbTex1_uv', v175[0], True)
    v176 = g.inp('F4_DisturbTex1', True, (1.0, 1.0, 1.0))
    v177 = g.inp('F4_DisturbTex1_alpha', False, 1.0)
    v178 = g.sep(v176)
    v179 = g.inp('_Bi_Disturb', False, 0.0)
    v180 = g.math('ADD', 1, v179)
    v181 = g.math('MULTIPLY', v179, -1.0)
    v182 = g.math('MULTIPLY_ADD', v178[0], v180, v181)
    v183 = g.inp('_DisturbTex1Normal', False, 0.0)
    v184 = g.math('COMPARE', 0, v183, 1e-05)
    v185 = g.math('SUBTRACT', 1.0, v184)
    v186 = g.inp('_DisturbUIntensity1', False, 0.0)
    v187 = g.math('MULTIPLY', v182, v186)
    v188 = g.math('MULTIPLY', v182, v177)
    v189 = g.math('MULTIPLY', 1, -1.0)
    v190 = g.math('MULTIPLY_ADD', v188, 2, v189)
    v191 = g.math('MULTIPLY', v190, v186)
    v192 = g.mixf(v185, v187, v191)
    v193 = g.comb(v192, 0, 0.0)
    v194 = g.inp('_DisturbVIntensity1', False, 0.0)
    v195 = g.math('MULTIPLY', v182, v194)
    v196 = g.math('MULTIPLY', 1, -1.0)
    v197 = g.math('MULTIPLY_ADD', v178[1], 2, v196)
    v198 = g.math('MULTIPLY', v197, v186)
    v199 = g.mixf(v185, v195, v198)
    v200 = g.sep(v193)
    v201 = g.comb(v200[0], v199, 0.0)
    v202 = g.mixv(v166, (0, 0, 0.0), v201)
    v203 = g.inp('_MainTexUVWeights', True, (1.0, 0.0, 0.0))
    v204 = g.inp('_MainTexUVWeights_w', False, 0.0)
    v205 = g.inp('_MainTexUVSpeed', True, (0.0, 0.0, 0.0))
    v206 = g.inp('_MainTexUVSpeed_w', False, 0.0)
    v207 = g.inp('_MainTexUVRotateMat', True, (1.0, 0.0, 0.0))
    v208 = g.inp('_MainTexUVRotateMat_w', False, 1.0)
    v209 = g.inp('_MainTex_ST', True, (1.0, 1.0, 0.0))
    v210 = g.inp('_MainTex_ST_w', False, 0.0)
    v211 = g.inp('_MainTexUseDisturb', False, 1.0)
    v212 = g.group_named('RCE_ComputeVFXUV_Endfield', [('uv0', v0), ('uv1', v165), ('weights', v203), ('weights_w', v204), ('speed', v205), ('speed_w', v206), ('time', v153[1]), ('customData', v156), ('rotateMat', v207), ('rotateMat_w', v208), ('st', v209), ('st_w', v210), ('disturb', v202), ('useDisturb', v211)])
    g.out_('F5_MainTex_uv', v212[0], True)
    v213 = g.inp('F5_MainTex', True, (1.0, 1.0, 1.0))
    v214 = g.inp('F5_MainTex_alpha', False, 1.0)
    v215 = g.sep(v213)
    v216 = g.inp('_UseMainTexAsAlpha', False, 1.0)
    v217 = g.mixf(v216, v214, v215[0])
    v218 = g.inp('_DisableVertColor', False, 0.0)
    v219 = g.mixf(v218, v11, 1)
    v220 = g.inp('_TintColor', True, (1.0, 1.0, 1.0))
    v221 = g.inp('_TintColor_w', False, 1.0)
    v222 = g.math('MULTIPLY', v219, v221)
    v223 = g.inp('_TintColorAlpha', False, 1.0)
    v224 = g.math('MULTIPLY', v222, v223)
    v225 = g.math('MULTIPLY', v224, v217)
    v226 = g.inp('_UseMask', False, 0.0)
    v227 = g.inp('_MaskTexUVWeights', True, (1.0, 0.0, 0.0))
    v228 = g.inp('_MaskTexUVWeights_w', False, 0.0)
    v229 = g.inp('_MaskTexUVSpeed', True, (0.0, 0.0, 0.0))
    v230 = g.inp('_MaskTexUVSpeed_w', False, 0.0)
    v231 = g.inp('_MaskTexUVRotateMat', True, (1.0, 0.0, 0.0))
    v232 = g.inp('_MaskTexUVRotateMat_w', False, 1.0)
    v233 = g.inp('_MaskTex_ST', True, (1.0, 1.0, 0.0))
    v234 = g.inp('_MaskTex_ST_w', False, 0.0)
    v235 = g.inp('_MaskTexUseDisturb', False, 0.0)
    v236 = g.group_named('RCE_ComputeVFXUV_Endfield', [('uv0', v0), ('uv1', v165), ('weights', v227), ('weights_w', v228), ('speed', v229), ('speed_w', v230), ('time', v153[1]), ('customData', v157), ('rotateMat', v231), ('rotateMat_w', v232), ('st', v233), ('st_w', v234), ('disturb', v202), ('useDisturb', v235)])
    g.out_('F6_MaskTex_uv', v236[0], True)
    v237 = g.inp('F6_MaskTex', True, (1.0, 1.0, 1.0))
    v238 = g.inp('F6_MaskTex_alpha', False, 1.0)
    v239 = g.sep(v237)
    v240 = g.inp('_UseMaskTexAsAlpha', False, 1.0)
    v241 = g.mixf(v240, v238, v239[0])
    v242 = g.mixv(v240, v237, (1, 1, 1))
    v243 = g.mixf(v226, 1, v241)
    v244 = g.mixv(v226, (1, 1, 1), v242)
    v245 = g.mixv(v218, v10, (1, 1, 1))
    v246 = g.mixv(v216, v213, (1, 1, 1))
    v247 = g.vmath('MULTIPLY', v245, v220)
    v248 = g.inp('_TintColorIntensity', False, 1.0)
    v249 = g.bc(v248)
    v250 = g.vmath('MULTIPLY', v247, v249)
    v251 = g.vmath('MULTIPLY', v250, v246)
    v252 = g.vmath('MULTIPLY', v251, v244)
    v253 = g.math('MULTIPLY', v225, v243)
    v254 = g.inp('_UseBlend', False, 0.0)
    v255 = g.inp('_BlendTexUVWeights', True, (1.0, 0.0, 0.0))
    v256 = g.inp('_BlendTexUVWeights_w', False, 0.0)
    v257 = g.inp('_BlendTexUVSpeed', True, (0.0, 0.0, 0.0))
    v258 = g.inp('_BlendTexUVSpeed_w', False, 0.0)
    v259 = g.inp('_BlendTexUVRotateMat', True, (1.0, 0.0, 0.0))
    v260 = g.inp('_BlendTexUVRotateMat_w', False, 1.0)
    v261 = g.inp('_BlendTex_ST', True, (1.0, 1.0, 0.0))
    v262 = g.inp('_BlendTex_ST_w', False, 0.0)
    v263 = g.inp('_BlendTexUseDisturb', False, 0.0)
    v264 = g.group_named('RCE_ComputeVFXUV_Endfield', [('uv0', v0), ('uv1', v165), ('weights', v255), ('weights_w', v256), ('speed', v257), ('speed_w', v258), ('time', v153[1]), ('customData', v157), ('rotateMat', v259), ('rotateMat_w', v260), ('st', v261), ('st_w', v262), ('disturb', v202), ('useDisturb', v263)])
    g.out_('F7_BlendTex_uv', v264[0], True)
    v265 = g.inp('F7_BlendTex', True, (0.0, 0.0, 0.0))
    v266 = g.inp('F7_BlendTex_alpha', False, 1.0)
    v267 = g.math('ADD', v253, v266)
    v268 = g.math('MULTIPLY', v267, v11)
    v269 = g.inp('_BlendTint', True, (1.0, 1.0, 1.0))
    v270 = g.inp('_BlendTint_w', False, 1.0)
    v271 = g.math('MULTIPLY', v268, v270)
    v272 = g.clampn(v271)
    v273 = g.bc(v272)
    v274 = g.vmath('MULTIPLY', v273, v265)
    v275 = g.vmath('MULTIPLY', v274, v10)
    v276 = g.vmath('MULTIPLY', v275, v269)
    v277 = g.vmath('ADD', v252, v276)
    v278 = g.mixv(v254, v252, v277)
    v279 = g.vmath('NORMALIZE', v16)
    v280 = g.inp('_EnableNormalMap', False, 0.0)
    v281 = g.math('COMPARE', v280, 0, 1e-05)
    v282 = g.math('SUBTRACT', 1.0, v281)
    v283 = g.inp('_NormalMapUVWeights', True, (1.0, 0.0, 0.0))
    v284 = g.inp('_NormalMapUVWeights_w', False, 0.0)
    v285 = g.inp('_NormalMapUVSpeed', True, (0.0, 0.0, 0.0))
    v286 = g.inp('_NormalMapUVSpeed_w', False, 0.0)
    v287 = g.inp('_NormalMapUVRotateMat', True, (1.0, 0.0, 0.0))
    v288 = g.inp('_NormalMapUVRotateMat_w', False, 1.0)
    v289 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v290 = g.inp('_NormalMap_ST_w', False, 0.0)
    v291 = g.inp('_NormalMapUseDisturb', False, 0.0)
    v292 = g.group_named('RCE_ComputeVFXUV_Endfield', [('uv0', v0), ('uv1', v165), ('weights', v283), ('weights_w', v284), ('speed', v285), ('speed_w', v286), ('time', v153[1]), ('customData', v157), ('rotateMat', v287), ('rotateMat_w', v288), ('st', v289), ('st_w', v290), ('disturb', v202), ('useDisturb', v291)])
    g.out_('F8_NormalMap_uv', v292[0], True)
    v293 = g.inp('F8_NormalMap', True, (1.0, 1.0, 1.0))
    v294 = g.inp('F8_NormalMap_alpha', False, 1.0)
    v295 = g.sep(v293)
    v296 = g.math('MULTIPLY', v295[0], v294)
    v297 = g.math('MULTIPLY', v296, 2)
    v298 = g.math('SUBTRACT', v297, 1)
    v299 = g.comb(v298, 0, 0)
    v300 = g.math('MULTIPLY', v295[1], 2)
    v301 = g.math('SUBTRACT', v300, 1)
    v302 = g.sep(v299)
    v303 = g.comb(v302[0], v301, v302[2])
    v304 = g.sep(v303)
    v305 = g.comb(v304[0], v304[1], 0.0)
    v306 = g.comb(v304[0], v304[1], 0.0)
    v307 = g.vmath('DOT_PRODUCT', v305, v306)
    v308 = g.math('MINIMUM', v307, 1)
    v309 = g.math('SUBTRACT', 1, v308)
    v310 = g.math('SQRT', v309, 0.0)
    v311 = g.math('MAXIMUM', v310, 1E-16)
    v312 = g.comb(v304[0], v304[1], v311)
    v313 = g.inp('_NormalScale', False, 1.0)
    v314 = g.sep(v312)
    v315 = g.comb(v314[0], v314[1], 0.0)
    v316 = g.comb(v313, v313, 0.0)
    v317 = g.vmath('MULTIPLY', v315, v316)
    v318 = g.sep(v317)
    v319 = g.comb(v318[0], v318[1], v314[2])
    v320 = g.vmath('NORMALIZE', v319)
    v321 = g.vmath('NORMALIZE', v17)
    v322 = g.math('GREATER_THAN', v4, 0)
    v323 = g.math('MULTIPLY', 1, -1.0)
    v324 = g.mixf(v322, v323, 1)
    v325 = g.vmath('CROSS_PRODUCT', v279, v321)
    v326 = g.bc(v324)
    v327 = g.vmath('MULTIPLY', v326, v325)
    v328 = g.sep(v320)
    v329 = g.bc(v328[0])
    v330 = g.vmath('MULTIPLY', v329, v321)
    v331 = g.bc(v328[1])
    v332 = g.vmath('MULTIPLY', v331, v327)
    v333 = g.vmath('ADD', v330, v332)
    v334 = g.bc(v328[2])
    v335 = g.vmath('MULTIPLY', v334, v279)
    v336 = g.vmath('ADD', v333, v335)
    v337 = g.vmath('NORMALIZE', v336)
    v338 = g.mixv(v282, v279, v337)
    v339 = g.math('LESS_THAN', v14, 0)
    v340 = g.math('SUBTRACT', 1.0, v339)
    v341 = g.vmath('SCALE', v338, s=-1.0)
    v342 = g.mixv(v340, v341, v338)
    v343 = g.inp('_UseFresnel', False, 0.0)
    v344 = g.vmath('SUBTRACT', v61, v15)
    v345 = g.vmath('NORMALIZE', v344)
    v346 = g.vmath('DOT_PRODUCT', v345, v342)
    v347 = g.inp('_FresnelBias', False, 0.0)
    v348 = g.math('ADD', v346, v347)
    v349 = g.clampn(v348)
    v350 = g.inp('_FresnelPower', False, 1.0)
    v351 = g.math('POWER', v349, v350)
    v352 = g.math('SUBTRACT', 1, v351)
    v353 = g.inp('_FresnelFlip', False, 0.001)
    v354 = g.math('SUBTRACT', v351, v352)
    v355 = g.math('MULTIPLY_ADD', v353, v354, v352)
    v356 = g.inp('_FresnelColor', True, (1.0, 1.0, 1.0))
    v357 = g.inp('_FresnelColor_w', False, 1.0)
    v358 = g.math('MULTIPLY', v355, v357)
    v359 = g.mixv(v358, v278, v356)
    v360 = g.mixv(v343, v278, v359)
    v361 = g.mixf(v343, 1, v355)
    v362 = g.sep(v114)
    v363 = g.inp('_IgnorePostExposure', False, 1.0)
    v364 = g.math('SUBTRACT', 1, v363)
    v365 = g.math('MULTIPLY_ADD', v362[0], v363, v364)
    v366 = g.bc(v365)
    v367 = g.vmath('DIVIDE', v360, v366)
    v368 = g.vmath('MAXIMUM', v367, (0, 0, 0))
    v369 = g.vmath('MINIMUM', v368, (1000, 1000, 1000))
    v370 = g.inp('_UseNearCameraFade', False, 0.0)
    v371 = g.math('COMPARE', v370, 0, 1e-05)
    v372 = g.math('SUBTRACT', 1.0, v371)
    v373 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v374 = g.sep(v373)
    v375 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v376 = g.sep(v375)
    v377 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v378 = g.sep(v377)
    v379 = g.comb(v374[2], v376[2], v378[2])
    v380 = g.vmath('DOT_PRODUCT', v379, v15)
    v381 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'POINT'), point=True)
    v382 = g.sep(v381)
    v383 = g.math('ADD', v380, v382[2])
    v384 = g.math('ABSOLUTE', v383, 0.0)
    v385 = g.inp('_NearCameraFadeDistanceStart2', False, 120.0)
    v386 = g.math('SUBTRACT', v384, v385)
    v387 = g.inp('_NearCameraFadeDistanceEnd2', False, 100.0)
    v388 = g.math('SUBTRACT', v387, v385)
    v389 = g.math('DIVIDE', v386, v388)
    v390 = g.clampn(v389)
    v391 = g.inp('_NearCameraFadeDistanceStart', False, 0.001)
    v392 = g.math('SUBTRACT', v384, v391)
    v393 = g.inp('_NearCameraFadeDistanceEnd', False, 10.0)
    v394 = g.math('SUBTRACT', v393, v391)
    v395 = g.math('DIVIDE', v392, v394)
    v396 = g.clampn(v395)
    v397 = g.math('MULTIPLY', v390, v396)
    v398 = g.mixf(v372, 1, v397)
    v399 = g.inp('_FresnelAffectOpacity', False, 1.0)
    v400 = g.mixf(v399, 1, v361)
    v401 = g.clampn(v253)
    v402 = g.math('MULTIPLY', v401, v400)
    v403 = g.math('MULTIPLY', v402, v398)
    v404 = g.clampn(v403)
    v405 = g.inp('_BlendMode', False, 0.0)
    v406 = g.math('SUBTRACT', 1, v405)
    v407 = g.math('MULTIPLY', v406, v404)
    v408 = g.bc(v404)
    v409 = g.vmath('MULTIPLY', v408, v369)
    v410 = g.bc(v404)
    v411 = g.vmath('MULTIPLY', v410, v369)
    v412 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v413 = g.math('SUBTRACT', v412, 0)
    v414 = g.math('CEIL', v413, 0.0)
    v415 = g.math('MAXIMUM', v414, 0.0)
    g.out_('Z0_it', v415, False)
    g.out_('Z0_s_N', v59, True)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_lightAccum', (0, 0, 0), True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_positionWS', v15, True)
    g.out_('Z0_r_albedo', v150, True)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pixelLightCount', v412, False)
    v416 = g.inp('Z0_o_N', True)
    v417 = g.inp('Z0_o_Lloop0', False)
    v418 = g.inp('Z0_o_lightAccum', True)
    v419 = g.inp('Z0_o_lightIndex', False)
    v420 = g.inp('Z0_o_positionWS', True)
    v421 = g.vmath('ADD', v411, v418)
    v422 = g.sep(v411)
    v423 = g.sep(v421)
    v424 = g.comb(v423[0], v423[1], v423[2])
    g.out_('ret_gBuffer0', v424, True)
    g.out_('ret_gBuffer0_w', v407, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v411, True)
    g.out_('ret_color_w', v407, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)


def build_Ruri_Endfield_Uber_OverlayShadow():
    t = _tree('Ruri Endfield Uber OverlayShadow')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.inp('_UseGrayAsAlpha', False, 0.0)
    v152 = g.mixv(v151, v99, (1, 1, 1))
    v153 = g.sep(v99)
    v154 = g.mixf(v151, v100, v153[0])
    v155 = g.math('MULTIPLY', v154, v76)
    v156 = g.math('MULTIPLY', v155, v76)
    v157 = g.vmath('MULTIPLY', v152, v75)
    v158 = g.vmath('SUBTRACT', v157, (1, 1, 1))
    v159 = g.bc(v156)
    v160 = g.vmath('MULTIPLY', v159, v158)
    v161 = g.vmath('ADD', (1, 1, 1), v160)
    v162 = g.vmath('NORMALIZE', v16)
    v163 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v164 = g.math('SUBTRACT', v163, 0)
    v165 = g.math('CEIL', v164, 0.0)
    v166 = g.math('MAXIMUM', v165, 0.0)
    g.out_('Z0_it', v166, False)
    g.out_('Z0_s_N', v59, True)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_lightAccum', (0, 0, 0), True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_positionWS', v15, True)
    g.out_('Z0_r_albedo', v150, True)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pixelLightCount', v163, False)
    v167 = g.inp('Z0_o_N', True)
    v168 = g.inp('Z0_o_Lloop0', False)
    v169 = g.inp('Z0_o_lightAccum', True)
    v170 = g.inp('Z0_o_lightIndex', False)
    v171 = g.inp('Z0_o_positionWS', True)
    v172 = g.vmath('ADD', v161, v169)
    v173 = g.sep(v161)
    v174 = g.sep(v172)
    v175 = g.comb(v174[0], v174[1], v174[2])
    g.out_('ret_gBuffer0', v175, True)
    g.out_('ret_gBuffer0_w', v155, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v161, True)
    g.out_('ret_color_w', v155, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)


def build_Ruri_Endfield_Uber_LiquidAg():
    t = _tree('Ruri Endfield Uber LiquidAg')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_tangentWS', True)
    v4 = g.inp('input_tangentWS_w', False)
    v5 = g.inp('input_uv1', True)
    v6 = g.inp('input_uv1_w', False)
    v7 = g.inp('input_uv0zw', True)
    v8 = g.inp('input_positionNDC', True)
    v9 = g.inp('input_positionNDC_w', False)
    v10 = g.inp('input_color', True)
    v11 = g.inp('input_color_w', False)
    v12 = g.inp('input_positionCS', True)
    v13 = g.inp('input_positionCS_w', False)
    v14 = g.inp('facing', False)
    v15 = g.b2u(v1, point=True)
    v16 = g.b2u(v2, point=False)
    v17 = g.b2u(v3, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v18 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v19 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v20 = g.inp('_UseBumpMap', False, 0.0)
    g.out_('F1_BumpMap_uv', v0, True)
    v21 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v22 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v23 = g.sep(v21)
    v24 = g.math('MULTIPLY', v23[0], v22)
    v25 = g.math('MULTIPLY', v24, 2)
    v26 = g.math('SUBTRACT', v25, 1)
    v27 = g.inp('_BumpScale', False, 1.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v23[1], 2)
    v30 = g.math('SUBTRACT', v29, 1)
    v31 = g.math('MULTIPLY', v30, v27)
    v32 = g.math('MULTIPLY', v28, v28)
    v33 = g.math('MULTIPLY', v31, v31)
    v34 = g.math('ADD', v32, v33)
    v35 = g.clampn(v34)
    v36 = g.math('SUBTRACT', 1, v35)
    v37 = g.math('SQRT', v36, 0.0)
    v38 = g.math('MAXIMUM', v37, 1E-16)
    v39 = g.vmath('NORMALIZE', v16)
    v40 = g.vmath('NORMALIZE', v17)
    v41 = g.vmath('CROSS_PRODUCT', v39, v40)
    v42 = g.bc(v4)
    v43 = g.vmath('MULTIPLY', v41, v42)
    v44 = g.bc(v28)
    v45 = g.vmath('MULTIPLY', v44, v40)
    v46 = g.bc(v31)
    v47 = g.vmath('MULTIPLY', v46, v43)
    v48 = g.vmath('ADD', v45, v47)
    v49 = g.bc(v38)
    v50 = g.vmath('MULTIPLY', v49, v39)
    v51 = g.vmath('ADD', v48, v50)
    v52 = g.vmath('NORMALIZE', v51)
    v53 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v52)
    v54 = g.mixv(v20, (0.0, 0.0, 0.0), v53)
    v55 = g.mixf(v20, 0.0, 1.0)
    v56 = g.math('SUBTRACT', 1.0, v55)
    v57 = g.vmath('NORMALIZE', v16)
    v58 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v57)
    v59 = g.mixv(v56, v54, v58)
    v60 = g.mixf(v56, v55, 1.0)
    v61 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v62 = g.vmath('SUBTRACT', v61, v15)
    v63 = g.vmath('NORMALIZE', v62)
    v64 = g.texco().outputs['Window']
    v65 = g.inp('_UseRMOSMap', False, 0.0)
    g.out_('F2_RMOSMap_uv', v0, True)
    v66 = g.inp('F2_RMOSMap', True, (0.0, 0.0, 0.0))
    v67 = g.inp('F2_RMOSMap_alpha', False, 1.0)
    v68 = g.sep(v66)
    v69 = g.mixf(v65, 0.0, v68[0])
    v70 = g.mixf(v65, 0.0, v68[1])
    v71 = g.mixf(v65, 0.0, v68[2])
    v72 = g.mixf(v65, 0.0, v67)
    v73 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v74 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v75 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v76 = g.inp('_BaseColor_w', False, 1.0)
    v77 = g.vmath('MULTIPLY', v73, v75)
    v78 = g.inp('_SurfaceType', False, 0.0)
    v79 = g.math('COMPARE', v78, 1, 1e-05)
    v80 = g.math('SUBTRACT', 1.0, v79)
    v81 = g.mixv(v80, v18, v77)
    v82 = g.vmath('MULTIPLY', v81, v75)
    v83 = g.math('MULTIPLY', v19, v76)
    v84 = g.math('SUBTRACT', 1.0, v65)
    v85 = g.inp('_RoughnessIntensity', False, 1.0)
    v86 = g.inp('_MetallicIntensity', False, 1.0)
    v87 = g.inp('_OcclusionIntensity', False, 1.0)
    v88 = g.inp('_SpecularIntensity', False, 1.0)
    v89 = g.mixf(v84, v69, v85)
    v90 = g.mixf(v84, v70, v86)
    v91 = g.mixf(v84, v71, v87)
    v92 = g.mixf(v84, v72, v88)
    v93 = g.math('LESS_THAN', v14, 0)
    v94 = g.math('SUBTRACT', 1.0, v93)
    v95 = g.inp('_BackFaceNormalFlip', False, 0.0)
    v96 = g.math('MULTIPLY', v95, 2)
    v97 = g.math('SUBTRACT', v96, 1)
    v98 = g.mixf(v94, v97, 1)
    v99 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v101 = g.vmath('SUBTRACT', v61, v15)
    v102 = g.vmath('NORMALIZE', v101)
    v103 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v104 = g.sep(v103)
    v105 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v106 = g.sep(v105)
    v107 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD', 'VECTOR'))
    v108 = g.sep(v107)
    v109 = g.comb(v104[0], v106[1], v108[2])
    v110 = g.inp('_CharacterParams12', True, (1.0, 0.0, 0.0))
    v111 = g.inp('_CharacterParams12_w', False, 0.0)
    v112 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v113 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v114 = g.inp('_ExposureParams', True, (1.0, 0.0, 0.0))
    v115 = g.inp('_ExposureParams_w', False, 0.0)
    v116 = g.group_named('RCE_ComputeExposure', [('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_EnvironmentGlobalParams0', v112), ('_EnvironmentGlobalParams0_w', v113), ('_ExposureParams', v114), ('_ExposureParams_w', v115)])
    v117 = g.inp('_UseMetallicGlossMap', False, 0.0)
    g.out_('F3_MetallicGlossMap_uv', v0, True)
    v118 = g.inp('F3_MetallicGlossMap', True, (1.0, 1.0, 1.0))
    v119 = g.inp('F3_MetallicGlossMap_alpha', False, 1.0)
    v120 = g.math('SUBTRACT', 1, v119)
    v121 = g.sep(v118)
    v122 = g.inp('_Smoothness', False, 0.5)
    v123 = g.math('SUBTRACT', 1, v122)
    v124 = g.inp('_Metallic', False, 0.0)
    v125 = g.inp('_Specular', False, 1.0)
    v126 = g.mixf(v117, v123, v120)
    v127 = g.mixf(v117, v124, v121[0])
    v128 = g.mixf(v117, v87, v121[2])
    v129 = g.mixf(v117, v125, v121[1])
    v130 = g.inp('C0_MainLight_direction', True, (0.0, 0.0, 0.0))
    v131 = g.inp('C0_MainLight_color', True, (0.0, 0.0, 0.0))
    v132 = g.inp('C0_MainLight_distanceAttenuation', False, 0.0)
    v133 = g.inp('C0_MainLight_shadowAttenuation', False, 0.0)
    v134 = g.inp('C0_MainLight_layerMask', False, 0.0)
    v135 = g.math('MINIMUM', 1.0, 1)
    v136 = g.inp('_CharacterParams11', True, (-0.433, 0.5, 0.75))
    v137 = g.inp('_CharacterParams11_w', False, -0.4)
    v138 = g.inp('_CharacterParams1', True, (0.0, 0.0, 1.0))
    v139 = g.inp('_CharacterParams1_w', False, 0.0)
    v140 = g.group_named('RCE_ResolveAdjustedLight', [('mainLightDir', v130), ('_CharacterParams11', v136), ('_CharacterParams11_w', v137), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139)])
    v141 = g.group_named('RCE_ComputeCamLightFactors', [('camFwd', v109), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2])])
    v142 = g.clampn(v141[0])
    v143 = g.inp('_RuriOutlineShellGate', False, 0.0)
    v144 = g.inp('_OutlineTintEnable', False, 0.0)
    v145 = g.inp('_OutlineTintColor', True, (1.0, 1.0, 1.0))
    v146 = g.inp('_OutlineTintColor_w', False, 1.0)
    v147 = g.inp('_OutlineColorBrightness', False, 0.5)
    v148 = g.inp('_OutlineColorSaturation', False, 1.5)
    v149 = g.group_named('RCE_ApplyEndfieldOutlineAlbedo', [('albedo', v82), ('_OutlineTintEnable', v144), ('_OutlineTintColor', v145), ('_OutlineTintColor_w', v146), ('_OutlineColorBrightness', v147), ('_OutlineColorSaturation', v148)])
    v150 = g.mixv(v143, v82, v149[0])
    v151 = g.inp('_UseShadowLutTex', False, 0.0)
    v152 = g.sep(v150)
    v153 = g.math('MULTIPLY', v152[0], 12.92)
    v154 = g.math('POWER', v152[0], 0.4166667)
    v155 = g.math('MULTIPLY', 1.055, v154)
    v156 = g.math('SUBTRACT', v155, 0.055)
    v157 = g.math('LESS_THAN', v152[0], 0.0031308)
    v158 = g.math('SUBTRACT', 1.0, v157)
    v159 = g.mixf(v158, v153, v156)
    v160 = g.clampn(v159)
    v161 = g.math('MULTIPLY', v152[1], 12.92)
    v162 = g.math('POWER', v152[1], 0.4166667)
    v163 = g.math('MULTIPLY', 1.055, v162)
    v164 = g.math('SUBTRACT', v163, 0.055)
    v165 = g.math('LESS_THAN', v152[1], 0.0031308)
    v166 = g.math('SUBTRACT', 1.0, v165)
    v167 = g.mixf(v166, v161, v164)
    v168 = g.clampn(v167)
    v169 = g.math('MULTIPLY', v152[2], 12.92)
    v170 = g.math('POWER', v152[2], 0.4166667)
    v171 = g.math('MULTIPLY', 1.055, v170)
    v172 = g.math('SUBTRACT', v171, 0.055)
    v173 = g.math('LESS_THAN', v152[2], 0.0031308)
    v174 = g.math('SUBTRACT', 1.0, v173)
    v175 = g.mixf(v174, v169, v172)
    v176 = g.clampn(v175)
    v177 = g.math('MULTIPLY', v176, 31)
    v178 = g.math('FLOOR', v177, 0.0)
    v179 = g.math('MULTIPLY', v178, 0.03125)
    v180 = g.math('MULTIPLY', v160, 0.0302734375)
    v181 = g.math('ADD', v179, v180)
    v182 = g.math('ADD', v181, 0.00048828125)
    v183 = g.math('MULTIPLY', v168, 0.96875)
    v184 = g.math('ADD', v183, 0.015625)
    v185 = g.comb(v182, v184, 0.0)
    g.out_('F4_ShadowLutTex_uv', v185, True)
    v186 = g.inp('F4_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v187 = g.inp('F4_ShadowLutTex_alpha', False, 1.0)
    v188 = g.math('ADD', v182, 0.03125)
    v189 = g.comb(v188, v184, 0.0)
    g.out_('F5_ShadowLutTex_uv', v189, True)
    v190 = g.inp('F5_ShadowLutTex', True, (1.0, 1.0, 1.0))
    v191 = g.inp('F5_ShadowLutTex_alpha', False, 1.0)
    v192 = g.math('MULTIPLY', v176, 31)
    v193 = g.math('SUBTRACT', v192, v178)
    v194 = g.mixv(v193, v186, v190)
    v195 = g.mixv(v151, (0.0, 0.0, 0.0), v194)
    v196 = g.mixf(v151, 0.0, 1.0)
    v197 = g.math('SUBTRACT', 1.0, v196)
    v198 = g.inp('_ShadowColorBrightness', False, 0.5)
    v199 = g.bc(v198)
    v200 = g.vmath('MULTIPLY', v150, v199)
    v201 = g.math('SUBTRACT', 1.0, v196)
    v202 = g.vmath('DOT_PRODUCT', v200, (0.2126729, 0.7151522, 0.072175))
    v203 = g.math('SUBTRACT', 1.0, v196)
    v204 = g.inp('_ShadowColorSaturation', False, 1.0)
    v205 = g.bc(v202)
    v206 = g.vmath('SUBTRACT', v200, v205)
    v207 = g.bc(v204)
    v208 = g.vmath('MULTIPLY', v207, v206)
    v209 = g.bc(v202)
    v210 = g.vmath('ADD', v208, v209)
    v211 = g.mixv(v203, v195, v210)
    v212 = g.mixf(v203, v196, 1.0)
    v213 = g.inp('F1_BumpMap', True, (0.5, 0.5, 1.0))
    v214 = g.inp('F1_BumpMap_alpha', False, 1.0)
    v215 = g.sep(v213)
    v216 = g.math('MULTIPLY', v215[0], v214)
    v217 = g.math('MULTIPLY', v216, 2)
    v218 = g.math('SUBTRACT', v217, 1)
    v219 = g.math('MULTIPLY', v218, v27)
    v220 = g.math('MULTIPLY', v215[1], 2)
    v221 = g.math('SUBTRACT', v220, 1)
    v222 = g.math('MULTIPLY', v221, v27)
    v223 = g.math('MULTIPLY', v219, v219)
    v224 = g.math('MULTIPLY', v222, v222)
    v225 = g.math('ADD', v223, v224)
    v226 = g.clampn(v225)
    v227 = g.math('SUBTRACT', 1, v226)
    v228 = g.math('SQRT', v227, 0.0)
    v229 = g.math('MAXIMUM', v228, 1E-16)
    v230 = g.vmath('NORMALIZE', v16)
    v231 = g.vmath('NORMALIZE', v17)
    v232 = g.vmath('CROSS_PRODUCT', v230, v231)
    v233 = g.bc(v4)
    v234 = g.vmath('MULTIPLY', v232, v233)
    v235 = g.bc(v219)
    v236 = g.vmath('MULTIPLY', v235, v231)
    v237 = g.bc(v222)
    v238 = g.vmath('MULTIPLY', v237, v234)
    v239 = g.vmath('ADD', v236, v238)
    v240 = g.bc(v229)
    v241 = g.vmath('MULTIPLY', v240, v230)
    v242 = g.vmath('ADD', v239, v241)
    v243 = g.vmath('NORMALIZE', v242)
    v244 = g.bc(v98)
    v245 = g.vmath('MULTIPLY', v244, v243)
    v246 = g.mixv(v20, (0.0, 0.0, 0.0), v245)
    v247 = g.mixf(v20, 0.0, 1.0)
    v248 = g.math('SUBTRACT', 1.0, v247)
    v249 = g.vmath('NORMALIZE', v16)
    v250 = g.bc(v98)
    v251 = g.vmath('MULTIPLY', v250, v249)
    v252 = g.mixv(v248, v246, v251)
    v253 = g.mixf(v248, v247, 1.0)
    v254 = g.inp('_ClearCoat', False, 0.0)
    g.out_('F6_ClearCoatMask_uv', v0, True)
    v255 = g.inp('F6_ClearCoatMask', True, (1.0, 1.0, 1.0))
    v256 = g.inp('F6_ClearCoatMask_alpha', False, 1.0)
    v257 = g.sep(v255)
    v258 = g.vmath('NORMALIZE', v16)
    v259 = g.bc(v98)
    v260 = g.vmath('MULTIPLY', v259, v258)
    v261 = g.inp('_ClearCoatNormalMode', False, 0.0)
    v262 = g.mixv(v261, v260, v252)
    v263 = g.inp('_ClearCoatSmoothness', False, 0.95)
    v264 = g.math('SUBTRACT', 1, v263)
    v265 = g.math('MULTIPLY', v264, v264)
    v266 = g.math('MAXIMUM', v265, 0.0078125)
    v267 = g.inp('_ClearCoatMetallic', False, 0.0)
    v268 = g.math('MULTIPLY_ADD', v267, 0.96, 0.04)
    v269 = g.inp('_ClearCoatColor', True, (1.0, 1.0, 1.0))
    v270 = g.inp('_ClearCoatColor_w', False, 1.0)
    v271 = g.bc(v268)
    v272 = g.vmath('MULTIPLY', v271, v269)
    v273 = g.math('GREATER_THAN', v257[0], 0.001)
    v274 = g.mixf(v254, 0, v257[0])
    v275 = g.mixv(v254, v252, v262)
    v276 = g.mixf(v254, 1, v264)
    v277 = g.mixf(v254, 0.0078125, v266)
    v278 = g.mixv(v254, (0, 0, 0), v272)
    v279 = g.mixf(v254, 0.0, v273)
    v280 = g.inp('_SilkStockings', False, 0.0)
    v281 = g.inp('_RuriCharacterEnvironmentEffect', True, (0.0, 0.0, 0.0))
    v282 = g.inp('_RuriCharacterEnvironmentEffect_w', False, 0.0)
    v283 = g.inp('_RuriCharacterEnvironmentWater', True, (0.0, 0.0, 0.0))
    v284 = g.inp('_RuriCharacterEnvironmentWater_w', False, 0.0)
    v285 = g.inp('_CharacterParams10', True, (0.0, 0.0, 0.0))
    v286 = g.inp('_CharacterParams10_w', False, 0.0)
    v287 = g.group_named('RCE_EnvironmentWetness', [('positionWS', v15), ('_RuriCharacterEnvironmentEffect', v281), ('_RuriCharacterEnvironmentEffect_w', v282), ('_RuriCharacterEnvironmentWater', v283), ('_RuriCharacterEnvironmentWater_w', v284), ('_CharacterParams10', v285), ('_CharacterParams10_w', v286)])
    v288 = g.math('MULTIPLY', v100, v76)
    v289 = g.inp('_SilkStockingsSpecularInt', False, 5.0)
    v290 = g.inp('_SilkStockingsSpecularMinAtMinWetness', False, 0.0)
    v291 = g.mixf(v287[0], v290, 1)
    v292 = g.math('MULTIPLY', v289, v291)
    v293 = g.inp('_SilkStockingsAdvance', False, 0.0)
    v294 = g.math('GREATER_THAN', v293, 0.5)
    g.out_('F7_SilkStockingsMask_uv', v0, True)
    v295 = g.inp('F7_SilkStockingsMask', True, (1.0, 1.0, 1.0))
    v296 = g.inp('F7_SilkStockingsMask_alpha', False, 1.0)
    v297 = g.sep(v295)
    v298 = g.math('SUBTRACT', 1, v297[2])
    v299 = g.mixf(v287[0], v126, v298)
    v300 = g.math('MULTIPLY', v292, v297[0])
    v301 = g.math('MULTIPLY', 1, -1.0)
    v302 = g.math('MULTIPLY_ADD', v297[1], 2, v301)
    v303 = g.math('MULTIPLY', 0.949999988079071, -1.0)
    v304 = g.clampn(v302, v303, 0.949999988079071)
    v305 = g.mixf(v287[0], v288, v296)
    v306 = g.math('ADD', v305, 1)
    v307 = g.inp('_SilkStockingsColor', True, (0.0, 0.0, 0.0))
    v308 = g.inp('_SilkStockingsColor_w', False, 1.0)
    v309 = g.math('SUBTRACT', v306, v308)
    v310 = g.clampn(v309)
    v311 = g.inp('_SilkStockingsAnisoDirection', False, 0.0)
    v312 = g.math('MULTIPLY', v288, 0.5)
    v313 = g.clampn(v312)
    v314 = g.mixf(v313, v311, 0.5)
    v315 = g.math('MULTIPLY', v314, -1.0)
    v316 = g.math('ADD', v288, 1)
    v317 = g.math('SUBTRACT', v316, v308)
    v318 = g.clampn(v317)
    v319 = g.mixf(v294, v292, v300)
    v320 = g.mixf(v294, v315, v304)
    v321 = g.mixf(v294, v318, v310)
    v322 = g.mixf(v294, v126, v299)
    v323 = g.inp('_SilkStockingsDryColor', True, (1.0, 1.0, 1.0))
    v324 = g.inp('_SilkStockingsDryColor_w', False, 1.0)
    v325 = g.inp('_SilkStockingsWetColor', True, (1.0, 1.0, 1.0))
    v326 = g.inp('_SilkStockingsWetColor_w', False, 1.0)
    v327 = g.mixv(v287[0], v323, v325)
    v328 = g.inp('_SilkStockingsMinAffect', False, 0.05)
    v329 = g.inp('_SilkStockingsMaxAffect', False, 0.9)
    v330 = g.vmath('DOT_PRODUCT', v252, v102)
    v331 = g.clampn(v330)
    v332 = g.math('SUBTRACT', 1.0499999523162842, v331)
    v333 = g.math('MULTIPLY', v321, 2)
    v334 = g.math('POWER', v332, v333)
    v335 = g.clampn(v334)
    v336 = g.mixf(v335, v328, v329)
    v337 = g.vmath('MULTIPLY', v150, v327)
    v338 = g.mixv(v336, v337, v307)
    v339 = g.vmath('MULTIPLY', v211, v327)
    v340 = g.mixv(v336, v339, v307)
    v341 = g.mixv(v280, v150, v338)
    v342 = g.mixf(v280, v126, v322)
    v343 = g.mixv(v280, v211, v340)
    v344 = g.mixf(v280, 0, v319)
    v345 = g.mixf(v280, 0, v320)
    v346 = g.mixf(v280, 0, v321)
    v347 = g.inp('_UseEmission', False, 1.0)
    g.out_('F8_EmissionMap_uv', v0, True)
    v348 = g.inp('F8_EmissionMap', True, (0.0, 0.0, 0.0))
    v349 = g.inp('F8_EmissionMap_alpha', False, 1.0)
    v350 = g.mixv(v347, (0, 0, 0), v348)
    v351 = g.inp('_UseParallax', False, 0.0)
    v352 = g.vmath('NORMALIZE', v16)
    v353 = g.vmath('NORMALIZE', v17)
    v354 = g.vmath('CROSS_PRODUCT', v352, v353)
    v355 = g.bc(v4)
    v356 = g.vmath('MULTIPLY', v354, v355)
    v357 = g.vmath('DOT_PRODUCT', v353, v102)
    v358 = g.vmath('DOT_PRODUCT', v356, v102)
    v359 = g.vmath('DOT_PRODUCT', v352, v102)
    v360 = g.comb(v357, v358, v359)
    v361 = g.vmath('DOT_PRODUCT', v360, v360)
    v362 = g.math('MAXIMUM', v361, 1.175E-38)
    v363 = g.math('INVERSE_SQRT', v362, 0.0)
    v364 = g.inp('_ParallaxTex_ST', True, (1.0, 1.0, 0.0))
    v365 = g.inp('_ParallaxTex_ST_w', False, 0.0)
    v366 = g.sep(v364)
    v367 = g.comb(v366[0], v366[1], 0.0)
    v368 = g.vmath('MULTIPLY', v0, v367)
    v369 = g.comb(v366[2], v365, 0.0)
    v370 = g.vmath('ADD', v368, v369)
    v371 = g.inp('_ParallaxMarchNum', False, 3.0)
    v372 = g.math('MINIMUM', 20, v371)
    v373 = g.math('DIVIDE', 1, v372)
    v374 = g.sep(v360)
    v375 = g.math('MULTIPLY', v363, v374[2])
    v376 = g.math('MAXIMUM', v375, 0.001)
    v377 = g.comb(v374[0], v374[1], 0.0)
    v378 = g.comb(v363, v363, 0.0)
    v379 = g.vmath('MULTIPLY', v378, v377)
    v380 = g.comb(v376, v376, 0.0)
    v381 = g.vmath('DIVIDE', v379, v380)
    v382 = g.inp('_ParallaxScale', False, 0.5)
    v383 = g.math('MULTIPLY', v382, -1.0)
    v384 = g.comb(v383, v383, 0.0)
    v385 = g.vmath('MULTIPLY', v381, v384)
    v386 = g.comb(v373, v373, 0.0)
    v387 = g.vmath('MULTIPLY', v386, v385)
    v388 = g.math('SUBTRACT', 1, v373)
    v389 = g.math('ADD', v372, 1)
    v390 = g.math('SUBTRACT', v389, 0)
    v391 = g.math('CEIL', v390, 0.0)
    v392 = g.math('MAXIMUM', v391, 0.0)
    g.out_('Z0_it', v392, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_pxAccum', v387, True)
    g.out_('Z0_s_pxDxUV', (0.0, 0.0, 0.0), True)
    g.out_('Z0_s_pxDyUV', (0.0, 0.0, 0.0), True)
    g.out_('Z0_s_pxHit', 0.0, False)
    g.out_('Z0_s_pxHitH', 0, False)
    g.out_('Z0_s_pxLayerH', v388, False)
    g.out_('Z0_s_pxPrevH', 0, False)
    g.out_('Z0_s_pxPrevLayerH', 1, False)
    g.out_('Z0_s_pxPrevOff', (0, 0, 0.0), True)
    g.out_('Z0_s_pxi', 0, False)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_pxUV', v370, True)
    g.out_('Z0_r_pxSteps', v372, False)
    g.out_('Z0_r_pxStepSz', v373, False)
    g.out_('Z0_r_pxUVDelta', v387, True)
    v393 = g.inp('Z0_o_Lloop0', False)
    v394 = g.inp('Z0_o_pxAccum', True)
    v395 = g.inp('Z0_o_pxDxUV', True)
    v396 = g.inp('Z0_o_pxDyUV', True)
    v397 = g.inp('Z0_o_pxHit', False)
    v398 = g.inp('Z0_o_pxHitH', False)
    v399 = g.inp('Z0_o_pxLayerH', False)
    v400 = g.inp('Z0_o_pxPrevH', False)
    v401 = g.inp('Z0_o_pxPrevLayerH', False)
    v402 = g.inp('Z0_o_pxPrevOff', True)
    v403 = g.inp('Z0_o_pxi', False)
    v404 = g.math('SUBTRACT', 1.0, v397)
    v405 = g.mixf(v404, v398, v400)
    v406 = g.math('SUBTRACT', v400, v401)
    v407 = g.math('MULTIPLY', v401, -1.0)
    v408 = g.math('ADD', v407, v399)
    v409 = g.math('ADD', v408, v400)
    v410 = g.math('SUBTRACT', v409, v405)
    v411 = g.math('DIVIDE', v406, v410)
    v412 = g.comb(v411, v411, 0.0)
    v413 = g.vmath('MULTIPLY', v387, v412)
    v414 = g.vmath('ADD', v370, v413)
    v415 = g.vmath('ADD', v414, v402)
    g.out_('F9_ParallaxTex_uv', v415, True)
    v416 = g.inp('F9_ParallaxTex', True, (1.0, 1.0, 1.0))
    v417 = g.inp('F9_ParallaxTex_alpha', False, 1.0)
    v418 = g.sep(v416)
    v419 = g.mixf(v351, 0, v418[0])
    v420 = g.inp('_EnableCharacterVFX', False, 0.0)
    v421 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v422 = g.inp('_Time_w', False, 0.0)
    v423 = g.sep(v421)
    v424 = g.inp('_VFXSpecialParam', True, (0.0, 0.0, 0.0))
    v425 = g.inp('_VFXSpecialParam_w', False, 0.0)
    v426 = g.sep(v424)
    v427 = g.sep(v0)
    v428 = g.math('MULTIPLY_ADD', v426[2], v423[1], v427[0])
    v429 = g.inp('_VFXSpecialBlendTex_ST', True, (1.0, 1.0, 0.0))
    v430 = g.inp('_VFXSpecialBlendTex_ST_w', False, 0.0)
    v431 = g.sep(v429)
    v432 = g.math('MULTIPLY_ADD', v428, v431[0], v431[2])
    v433 = g.math('MULTIPLY_ADD', v425, v423[1], v427[1])
    v434 = g.math('MULTIPLY_ADD', v433, v431[1], v430)
    v435 = g.comb(v432, v434, 0.0)
    g.out_('F10_VFXSpecialBlendTex_uv', v435, True)
    v436 = g.inp('F10_VFXSpecialBlendTex', True, (0.0, 0.0, 0.0))
    v437 = g.inp('F10_VFXSpecialBlendTex_alpha', False, 1.0)
    v438 = g.sep(v436)
    v439 = g.inp('_VFXSpecialBlendTexRForDisturb', False, 1.0)
    v440 = g.math('MULTIPLY', v438[0], v439)
    v441 = g.comb(v440, v440, 0.0)
    v442 = g.vmath('ADD', v0, v441)
    v443 = g.sep(v442)
    v444 = g.math('MULTIPLY_ADD', v426[0], v423[1], v443[0])
    v445 = g.inp('_VFXSpecialMainTex_ST', True, (1.0, 1.0, 0.0))
    v446 = g.inp('_VFXSpecialMainTex_ST_w', False, 0.0)
    v447 = g.sep(v445)
    v448 = g.math('MULTIPLY_ADD', v444, v447[0], v447[2])
    v449 = g.math('MULTIPLY_ADD', v426[1], v423[1], v443[1])
    v450 = g.math('MULTIPLY_ADD', v449, v447[1], v446)
    v451 = g.comb(v448, v450, 0.0)
    g.out_('F11_VFXSpecialMainTex_uv', v451, True)
    v452 = g.inp('F11_VFXSpecialMainTex', True, (1.0, 1.0, 1.0))
    v453 = g.inp('F11_VFXSpecialMainTex_alpha', False, 1.0)
    v454 = g.sep(v452)
    v455 = g.inp('_UseVFXMainTexAsAlpha', False, 0.0)
    v456 = g.mixf(v455, v453, v454[0])
    v457 = g.mixv(v455, v452, (1, 1, 1))
    v458 = g.vmath('NORMALIZE', v16)
    v459 = g.vmath('DOT_PRODUCT', v102, v458)
    v460 = g.inp('_VFXFresnelBias', False, 0.0)
    v461 = g.math('ADD', v459, v460)
    v462 = g.clampn(v461)
    v463 = g.math('LOGARITHM', v462, 2.0)
    v464 = g.inp('_VFXFresnelPower', False, 1.0)
    v465 = g.math('MULTIPLY', v463, v464)
    v466 = g.math('POWER', 2.0, v465)
    v467 = g.math('SUBTRACT', 1, v466)
    v468 = g.inp('_VFXFresnelFlip', False, 0.001)
    v469 = g.mixf(v468, v467, v466)
    v470 = g.inp('_VFXColorAlpha', False, 1.0)
    v471 = g.inp('_VFXColor', True, (1.0, 1.0, 1.0))
    v472 = g.inp('_VFXColor_w', False, 1.0)
    v473 = g.math('MULTIPLY', v470, v472)
    v474 = g.inp('_SpecialDissolveScheduleOffset', False, 0.0)
    v475 = g.math('MULTIPLY', v474, 2.02)
    v476 = g.math('SUBTRACT', v475, 1.01)
    v477 = g.math('SUBTRACT', v438[0], v476)
    v478 = g.math('MULTIPLY', v477, -1.0)
    v479 = g.clampn(v478)
    v480 = g.mixv(v420, (0, 0, 0), v436)
    v481 = g.mixf(v420, 0, v437)
    v482 = g.mixf(v420, 0, v456)
    v483 = g.mixv(v420, (0, 0, 0), v457)
    v484 = g.mixf(v420, 0, v469)
    v485 = g.mixf(v420, 0, v473)
    v486 = g.mixf(v420, 0, v477)
    v487 = g.mixf(v420, 0, v479)
    v488 = g.group_named('RCE_GetObjectFlatDir', [('positionWS', v15)])
    v489 = g.inp('_CharacterParams2', True, (0.7830188, 0.8293082, 1.0))
    v490 = g.inp('_CharacterParams2_w', False, 0.0)
    v491 = g.math('MULTIPLY', v129, 0.04)
    v492 = g.math('SUBTRACT', 1, v127)
    v493 = g.math('MULTIPLY', v492, 0.96)
    v494 = g.bc(v493)
    v495 = g.vmath('MULTIPLY', v494, v341)
    v496 = g.bc(v491)
    v497 = g.vmath('SUBTRACT', v341, v496)
    v498 = g.bc(v127)
    v499 = g.vmath('MULTIPLY', v498, v497)
    v500 = g.bc(v491)
    v501 = g.vmath('ADD', v499, v500)
    v502 = g.bc(v493)
    v503 = g.vmath('MULTIPLY', v502, v343)
    v504 = g.math('MULTIPLY', v342, v342)
    v505 = g.math('MAXIMUM', v504, 0.0078125)
    v506 = g.inp('_CharacterParams5', True, (1.0, 1.0, 1.0))
    v507 = g.inp('_CharacterParams5_w', False, 1.0)
    v508 = g.sep(v110)
    v509 = g.mixv(v508[1], v131, v506)
    v510 = g.vmath('DOT_PRODUCT', v252, v140[0])
    v511 = g.math('MULTIPLY', 0.5, v510)
    v512 = g.math('MULTIPLY', v511, v510)
    v513 = g.math('SUBTRACT', 0.5, v512)
    v514 = g.math('SUBTRACT', 1, v508[0])
    v515 = g.math('MULTIPLY', v142, v141[1])
    v516 = g.math('MULTIPLY', v514, v515)
    v517 = g.math('MULTIPLY', v516, v513)
    v518 = g.math('ADD', v517, v510)
    v519 = g.inp('_UseDiffRampMap', False, 0.0)
    v520 = g.math('SUBTRACT', 1.0, v519)
    v521 = g.math('MULTIPLY', v518, 0.5)
    v522 = g.math('ADD', v521, 0.5)
    v523 = g.clampn(v522)
    v524 = g.mixf(v520, 0.0, 0)
    v525 = g.mixf(v520, 0.0, 0)
    v526 = g.mixv(v520, (0.0, 0.0, 0.0), (1, 1, 1))
    v527 = g.mixf(v520, 0.0, v523)
    v528 = g.mixf(v520, 0.0, 1.0)
    v529 = g.math('SUBTRACT', 1.0, v528)
    v530 = g.math('MULTIPLY', v137, v508[0])
    v531 = g.math('ADD', v530, v518)
    v532 = g.math('MULTIPLY', 1, -1.0)
    v533 = g.clampn(v531, v532, 1)
    v534 = g.math('MULTIPLY', v533, 0.5)
    v535 = g.math('ADD', v534, 0.5)
    v536 = g.math('SUBTRACT', 1.0, v528)
    v537 = g.comb(v535, 0.5, 0.0)
    g.out_('F12_DiffRampMap_uv', v537, True)
    v538 = g.inp('F12_DiffRampMap', True, (1.0, 1.0, 1.0))
    v539 = g.inp('F12_DiffRampMap_alpha', False, 1.0)
    v540 = g.math('SUBTRACT', 1.0, v528)
    v541 = g.sep(v538)
    v542 = g.math('MAXIMUM', v541[1], v541[2])
    v543 = g.math('MAXIMUM', v541[0], v542)
    v544 = g.math('MINIMUM', v541[1], v541[2])
    v545 = g.math('MINIMUM', v541[0], v544)
    v546 = g.math('SUBTRACT', v543, v545)
    v547 = g.mixf(v540, v524, v546)
    v548 = g.math('SUBTRACT', 1.0, v528)
    v549 = g.vmath('DOT_PRODUCT', v252, v109)
    v550 = g.math('MULTIPLY', v549, 0.5)
    v551 = g.math('ADD', v550, 0.5)
    v552 = g.math('SUBTRACT', 1.0, v528)
    v553 = g.comb(v551, 0.5, 0.0)
    g.out_('F13_DiffRampMap_uv', v553, True)
    v554 = g.inp('F13_DiffRampMap', True, (1.0, 1.0, 1.0))
    v555 = g.inp('F13_DiffRampMap_alpha', False, 1.0)
    v556 = g.mixf(v552, v525, v555)
    v557 = g.math('SUBTRACT', 1.0, v528)
    v558 = g.mixv(v557, v526, v538)
    v559 = g.mixf(v557, v527, v539)
    v560 = g.mixf(v557, v528, 1.0)
    v561 = g.math('SUBTRACT', v135, 0)
    v562 = g.math('DIVIDE', v561, 1)
    v563 = g.clampn(v562)
    v564 = g.math('MULTIPLY', v563, v563)
    v565 = g.math('MULTIPLY', 2.0, v563)
    v566 = g.math('SUBTRACT', 3.0, v565)
    v567 = g.math('MULTIPLY', v564, v566)
    v568 = g.sep(v138)
    v569 = g.mixf(v568[2], v567, 1)
    v570 = g.math('MINIMUM', v559, v128)
    v571 = g.math('MULTIPLY', v570, v569)
    v572 = g.math('MULTIPLY', v556, v128)
    v573 = g.math('ADD', v572, v559)
    v574 = g.clampn(v573)
    v575 = g.inp('_CharacterParams0', True, (0.0, 0.9, 0.8))
    v576 = g.inp('_CharacterParams0_w', False, 0.8)
    v577 = g.sep(v575)
    v578 = g.bc(v577[2])
    v579 = g.vmath('MULTIPLY', v503, v578)
    v580 = g.vmath('DOT_PRODUCT', v495, (0.2126729, 0.7151522, 0.072175))
    v581 = g.clampn(v116[0], 0, 1.5)
    v582 = g.math('SUBTRACT', 1, v547)
    v583 = g.inp('_CharacterParams6', True, (0.0, 1.0, 4.371139E-08))
    v584 = g.inp('_CharacterParams6_w', False, 0.0)
    v585 = g.inp('_CharacterParams7', True, (0.15, 1.5, 0.5))
    v586 = g.inp('_CharacterParams7_w', False, 0.0)
    v587 = g.group_named('RCE_ComputeNPRDiffuse', [('hemisphereN', v252), ('ambCol', v489), ('brightness', v581), ('blendedLightCol', v509), ('blendedLightInt', 1), ('minShadow', v571), ('combWeight', v574), ('albScaled', v579), ('diffColor', v495), ('rampCol', v558), ('rampChroma', v547), ('rampChromaInv', v582), ('_CharacterParams6', v583), ('_CharacterParams6_w', v584), ('_CharacterParams7', v585), ('_CharacterParams7_w', v586), ('_CharacterParams1', v138), ('_CharacterParams1_w', v139), ('_CharacterParams12', v110), ('_CharacterParams12_w', v111), ('_CharacterParams0', v575), ('_CharacterParams0_w', v576)])
    v588 = g.math('MULTIPLY', v571, 0.5)
    v589 = g.math('ADD', v588, 0.5)
    v590 = g.math('MULTIPLY', v587[2], v589)
    v591 = g.math('MULTIPLY_ADD', v100, 0, 1)
    v592 = g.group_named('RCE_BRDF_GGX_Stylized_Endfield', [('N', v252), ('V', v102), ('adjustedLightDir', v140[0]), ('camFwd', v109), ('roughness', v505)])
    v593 = g.math('MULTIPLY', v505, v505)
    v594 = g.math('MULTIPLY', v592[1], 0.5)
    v595 = g.math('MULTIPLY', v592[2], 2)
    v596 = g.math('ADD', v595, v505)
    v597 = g.math('ADD', v596, 0.0001)
    v598 = g.math('DIVIDE', v594, v597)
    v599 = g.math('SUBTRACT', v598, 6.103515625e-05)
    v600 = g.clampn(v599, 0, 20)
    v601 = g.inp('_SilkStockingsSpecularFalloff', False, 0.8)
    v602 = g.math('MULTIPLY', v346, v601)
    v603 = g.clampn(v602)
    v604 = g.math('SUBTRACT', 1, v603)
    v605 = g.math('MULTIPLY', v345, v604)
    v606 = g.math('MULTIPLY', v605, -1.0)
    v607 = g.inp('_SilkStockingsSpecularValue', False, 2.0)
    v608 = g.group_named('RCE_BRDF_AnisotropicNDF_SilkStockings_Endfield', [('N', v252), ('V', v102), ('H', v592[3]), ('tangentDir', v17), ('tangentSign', v4), ('alpha2', v505), ('ph_aniso', v606), ('_SilkStockingsSpecularValue', v607)])
    v609 = g.clampn(v608[0], 0, 20)
    v610 = g.math('MULTIPLY', v344, v609)
    v611 = g.math('ADD', v600, v610)
    v612 = g.mixf(v280, v600, v611)
    v613 = g.inp('_UseSpecRampMap', False, 0.0)
    v614 = g.math('ADD', v593, 0.0001)
    v615 = g.math('MULTIPLY', v592[1], v614)
    v616 = g.math('MULTIPLY', v592[2], v592[2])
    v617 = g.inp('_SpecRampIridescentMode', False, 0.0)
    v618 = g.mixf(v617, v615, v616)
    v619 = g.math('SUBTRACT', 1, v127)
    v620 = g.math('MULTIPLY', v619, v342)
    v621 = g.comb(v618, v620, 0.0)
    g.out_('F14_SpecRampMap_uv', v621, True)
    v622 = g.inp('F14_SpecRampMap', True, (1.0, 1.0, 1.0))
    v623 = g.inp('F14_SpecRampMap_alpha', False, 1.0)
    v624 = g.vmath('MULTIPLY', v501, v622)
    v625 = g.mixv(v617, v501, v624)
    v626 = g.mixv(v613, v501, v624)
    v627 = g.mixv(v613, v501, v625)
    v628 = g.math('MULTIPLY', v254, v279)
    v629 = g.vmath('DOT_PRODUCT', v275, v592[3])
    v630 = g.vmath('DOT_PRODUCT', v275, v102)
    v631 = g.clampn(v630)
    v632 = g.vmath('DOT_PRODUCT', v102, v592[3])
    v633 = g.clampn(v632)
    v634 = g.group_named('RCE_BRDF_ClearCoat_Direct_Burley', [('ccMask', v274), ('ccPercRough', v276), ('ccAlpha', v277), ('ccF0', v278), ('ccNdotH', v629), ('ccNdotV', v631), ('VdotH', v633)])
    v635 = g.mixv(v628, (0, 0, 0), v634[0])
    v636 = g.mixv(v628, (1, 1, 1), v634[1])
    v637 = g.mixv(v628, (1, 1, 1), v634[2])
    v638 = g.vmath('MULTIPLY', v587[1], v587[0])
    v639 = g.bc(v591)
    v640 = g.vmath('MULTIPLY', v638, v639)
    v641 = g.bc(v590)
    v642 = g.vmath('MULTIPLY', v641, v587[1])
    v643 = g.bc(v612)
    v644 = g.vmath('MULTIPLY', v643, v626)
    v645 = g.vmath('MULTIPLY', v642, v644)
    v646 = g.inp('_CharacterParams13', True, (0.0, 0.0, 0.0))
    v647 = g.inp('_CharacterParams13_w', False, 1.0)
    v648 = g.bc(v647)
    v649 = g.vmath('MULTIPLY', v645, v648)
    v650 = g.vmath('ADD', v640, v649)
    v651 = g.vmath('MULTIPLY', v587[1], v587[0])
    v652 = g.bc(v591)
    v653 = g.vmath('MULTIPLY', v651, v652)
    v654 = g.vmath('MULTIPLY', v653, v637)
    v655 = g.bc(v590)
    v656 = g.vmath('MULTIPLY', v655, v587[1])
    v657 = g.bc(v612)
    v658 = g.vmath('MULTIPLY', v657, v626)
    v659 = g.vmath('MULTIPLY', v658, v636)
    v660 = g.vmath('MULTIPLY', v659, v636)
    v661 = g.vmath('ADD', v660, v635)
    v662 = g.vmath('MULTIPLY', v656, v661)
    v663 = g.bc(v647)
    v664 = g.vmath('MULTIPLY', v662, v663)
    v665 = g.vmath('ADD', v654, v664)
    v666 = g.mixv(v254, v650, v665)
    v667 = g.vmath('DOT_PRODUCT', v666, (0.2126729, 0.7151522, 0.072175))
    v668 = g.inp('_CharacterParams9', True, (0.0, 1.0, 0.0))
    v669 = g.inp('_CharacterParams9_w', False, 0.4)
    v670 = g.group_named('RCE_ComputeSkinDir', [('camFwd', v109), ('_CharacterParams9', v668), ('_CharacterParams9_w', v669)])
    v671 = g.vmath('DOT_PRODUCT', v488[0], v670[0])
    v672 = g.math('ADD', v671, 1)
    v673 = g.clampn(v672)
    v674 = g.math('MINIMUM', v128, v673)
    v675 = g.vmath('DOT_PRODUCT', v102, v252)
    v676 = g.group_named('RCE_ComputeSkinSmoothFalloff', [('NdotV', v675), ('_CharacterParams9', v668), ('_CharacterParams9_w', v669)])
    v677 = g.inp('_CharacterParams8', True, (0.0, 0.0, 0.0))
    v678 = g.inp('_CharacterParams8_w', False, 1.0)
    v679 = g.group_named('RCE_ComputeSkinSpec', [('skinDir', v670[0]), ('N', v252), ('diffColor', v495), ('skinShadow', v674), ('skinAmt', v676[0]), ('_CharacterParams8', v677), ('_CharacterParams8_w', v678), ('_CharacterParams9', v668), ('_CharacterParams9_w', v669)])
    v680 = g.math('SUBTRACT', 1, v508[0])
    v681 = g.math('MULTIPLY', v680, v142)
    v682 = g.vmath('MULTIPLY', v509, (1, 1, 1))
    v683 = g.group_named('RCE_BRDF_SubsurfaceSpec_Endfield', [('N', v252), ('V', v102), ('adjXZ_x', v140[1]), ('adjXZ_z', v140[2]), ('adjXZLen', v140[3]), ('camLightFacing', v681), ('mask', v128), ('diffColorLum', v580), ('diffColor', v495), ('subsurfLight', v682)])
    v684 = g.clampn(v116[0], 0.5, 1.5)
    v685 = g.math('MULTIPLY', v684, v576)
    v686 = g.math('MULTIPLY', v587[2], v685)
    v687 = g.vmath('NORMALIZE', v16)
    v688 = g.bc(v98)
    v689 = g.vmath('MULTIPLY', v688, v687)
    v690 = g.vmath('DOT_PRODUCT', v689, v102)
    v691 = g.clampn(v690)
    v692 = g.math('MULTIPLY', v342, v342)
    v693 = g.vmath('SCALE', v102, s=-1.0)
    v694 = g.vmath('DOT_PRODUCT', v689, v693)
    v695 = g.math('MULTIPLY', 2.0, v694)
    v696 = g.vmath('SCALE', v689, s=v695)
    v697 = g.vmath('SUBTRACT', v693, v696)
    v698 = g.math('MAXIMUM', v342, 0.001)
    v699 = g.math('LOGARITHM', v698, 2.0)
    v700 = g.math('MULTIPLY', v699, 1.2)
    v701 = g.math('ADD', v700, 5)
    v702 = g.u2b(v697)
    g.out_('F15_IBL_CharMaxCubemap_dir', v702, True)
    g.out_('F15_IBL_CharMaxCubemap_mip', v701, False)
    v703 = g.inp('F15_IBL_CharMaxCubemap', True, (0.2159, 0.2159, 0.2159))
    v704 = g.inp('F15_IBL_CharMaxCubemap_alpha', False, 1.0)
    v705 = g.group_named('RCE_IBL_SplitSumCombine', [('cubeSample', v703), ('NdotV_spec', v691), ('roughness', v692), ('specRampEnv', v627), ('ambIntensity', v686), ('ambCol', v489)])
    v706 = g.inp('_CubemapIntensity', False, 1.0)
    v707 = g.bc(v706)
    v708 = g.vmath('MULTIPLY', v705[0], v707)
    v709 = g.math('MULTIPLY', v254, v279)
    v710 = g.vmath('SCALE', v102, s=-1.0)
    v711 = g.vmath('DOT_PRODUCT', v275, v710)
    v712 = g.math('MULTIPLY', 2.0, v711)
    v713 = g.vmath('SCALE', v275, s=v712)
    v714 = g.vmath('SUBTRACT', v710, v713)
    v715 = g.math('MAXIMUM', v276, 0.001)
    v716 = g.math('LOGARITHM', v715, 2.0)
    v717 = g.math('MULTIPLY', v716, 1.2)
    v718 = g.math('ADD', v717, 5)
    v719 = g.u2b(v714)
    g.out_('F16_IBL_CharMaxCubemap_dir', v719, True)
    g.out_('F16_IBL_CharMaxCubemap_mip', v718, False)
    v720 = g.inp('F16_IBL_CharMaxCubemap', True, (0.2159, 0.2159, 0.2159))
    v721 = g.inp('F16_IBL_CharMaxCubemap_alpha', False, 1.0)
    v722 = g.vmath('DOT_PRODUCT', v275, v102)
    v723 = g.clampn(v722)
    v724 = g.group_named('RCE_EnvBRDF_Endfield', [('NdotV', v723), ('roughSq', v277)])
    v725 = g.bc(v724[0])
    v726 = g.vmath('MULTIPLY', v278, v725)
    v727 = g.bc(v724[1])
    v728 = g.vmath('ADD', v726, v727)
    v729 = g.math('ADD', v724[0], v724[1])
    v730 = g.math('SUBTRACT', 1, v729)
    v731 = g.math('MAXIMUM', v729, 1E-06)
    v732 = g.math('DIVIDE', v730, v731)
    v733 = g.vmath('MULTIPLY', v720, v728)
    v734 = g.bc(v732)
    v735 = g.vmath('MULTIPLY', v734, v278)
    v736 = g.vmath('ADD', (1, 1, 1), v735)
    v737 = g.vmath('MULTIPLY', v733, v736)
    v738 = g.bc(v274)
    v739 = g.vmath('MULTIPLY', v738, v737)
    v740 = g.bc(v706)
    v741 = g.vmath('MULTIPLY', v739, v740)
    v742 = g.vmath('ADD', v708, v741)
    v743 = g.mixv(v709, v708, v742)
    v744 = g.inp('_EmissionColor', True, (0.0, 0.0, 0.0))
    v745 = g.inp('_EmissionColor_w', False, 1.0)
    v746 = g.vmath('MULTIPLY', v350, v744)
    v747 = g.inp('_EmissionBrightness', False, 1.0)
    v748 = g.bc(v747)
    v749 = g.vmath('MULTIPLY', v746, v748)
    v750 = g.bc(v591)
    v751 = g.vmath('MULTIPLY', v749, v750)
    v752 = g.mixv(v347, (0, 0, 0), v751)
    v753 = g.math('MULTIPLY', v100, v419)
    v754 = g.inp('_ParallaxColor', True, (0.0, 0.0, 0.0))
    v755 = g.inp('_ParallaxColor_w', False, 1.0)
    v756 = g.bc(v753)
    v757 = g.vmath('MULTIPLY', v756, v754)
    v758 = g.bc(v591)
    v759 = g.vmath('MULTIPLY', v757, v758)
    v760 = g.vmath('ADD', v752, v759)
    v761 = g.mixv(v351, v752, v760)
    v762 = g.math('SUBTRACT', v667, 0.5)
    v763 = g.clampn(v762, 0, 0.5)
    v764 = g.math('MULTIPLY', v763, v763)
    v765 = g.math('ADD', v764, 1)
    v766 = g.bc(v667)
    v767 = g.vmath('SUBTRACT', v666, v766)
    v768 = g.bc(v765)
    v769 = g.vmath('MULTIPLY', v768, v767)
    v770 = g.bc(v667)
    v771 = g.vmath('ADD', v769, v770)
    v772 = g.vmath('ADD', v771, v679[0])
    v773 = g.vmath('ADD', v772, v683[0])
    v774 = g.vmath('ADD', v773, v761)
    v775 = g.vmath('ADD', v774, v743)
    v776 = g.math('MULTIPLY', v485, v482)
    v777 = g.math('ADD', v776, v481)
    v778 = g.inp('_VFXBlendTint', True, (1.0, 1.0, 1.0))
    v779 = g.inp('_VFXBlendTint_w', False, 1.0)
    v780 = g.math('MULTIPLY', v777, v779)
    v781 = g.clampn(v780)
    v782 = g.inp('_VFXColorIntensity', False, 1.0)
    v783 = g.bc(v782)
    v784 = g.vmath('MULTIPLY', v783, v471)
    v785 = g.vmath('MULTIPLY', v784, v483)
    v786 = g.bc(v781)
    v787 = g.vmath('MULTIPLY', v480, v786)
    v788 = g.vmath('MULTIPLY', v787, v778)
    v789 = g.vmath('ADD', v788, v785)
    v790 = g.inp('_VFXFresnelColor', True, (1.0, 1.0, 1.0))
    v791 = g.inp('_VFXFresnelColor_w', False, 1.0)
    v792 = g.bc(v487)
    v793 = g.vmath('MULTIPLY', v792, v790)
    v794 = g.bc(v782)
    v795 = g.vmath('MULTIPLY', v793, v794)
    v796 = g.mixv(v487, v789, v795)
    v797 = g.math('MULTIPLY', v484, v791)
    v798 = g.clampn(v486)
    v799 = g.math('MULTIPLY', v798, v485)
    v800 = g.math('MULTIPLY', v799, v482)
    v801 = g.clampn(v800)
    v802 = g.inp('_VFXFresnelAffectOpacity', False, 1.0)
    v803 = g.mixf(v802, 1, v484)
    v804 = g.math('MULTIPLY', v801, v803)
    v805 = g.mixv(v797, v796, v790)
    v806 = g.bc(v804)
    v807 = g.vmath('MULTIPLY', v806, v805)
    v808 = g.bc(v591)
    v809 = g.vmath('MULTIPLY', v807, v808)
    v810 = g.vmath('ADD', v775, v809)
    v811 = g.mixv(v420, v775, v810)
    v812 = g.inp('_EnableVFXColorAdjustment', False, 0.0)
    v813 = g.math('GREATER_THAN', v812, 0.5)
    v814 = g.inp('_ColorAdjustmentContrast', False, 1.0)
    v815 = g.inp('_ColorAdjustmentSaturation', False, 1.0)
    v816 = g.inp('_ColorAdjustmentRimWidth', False, 0.35)
    v817 = g.inp('_ColorAdjustmentBrightness', False, 1.0)
    v818 = g.inp('_ColorAdjustmentColorBlend', True, (1.0, 1.0, 1.0))
    v819 = g.inp('_ColorAdjustmentColorBlend_w', False, 0.0)
    v820 = g.inp('_ColorAdjustmentRimColor', True, (1.0, 1.0, 1.0))
    v821 = g.inp('_ColorAdjustmentRimColor_w', False, 1.0)
    v822 = g.inp('_ColorAdjustmentRimIntensity', False, 4.0)
    v823 = g.group_named('RCE_VFXColorAdjust', [('litColor', v811), ('NdotV', v592[2]), ('rimMod', 1), ('_ColorAdjustmentContrast', v814), ('_ColorAdjustmentSaturation', v815), ('_ColorAdjustmentRimWidth', v816), ('_ColorAdjustmentBrightness', v817), ('_ColorAdjustmentColorBlend', v818), ('_ColorAdjustmentColorBlend_w', v819), ('_ColorAdjustmentRimColor', v820), ('_ColorAdjustmentRimColor_w', v821), ('_ColorAdjustmentRimIntensity', v822)])
    v824 = g.mixv(v813, v811, v823[0])
    v825 = g.sep(v114)
    v826 = g.bc(v825[0])
    v827 = g.vmath('DIVIDE', v824, v826)
    v828 = g.math('COMPARE', v78, 1, 1e-05)
    v829 = g.mixf(v828, 1, v100)
    v830 = g.math('MULTIPLY', v83, v829)
    v831 = g.inp('C1_AdditionalLightCount', False, 0.0)
    v832 = g.math('SUBTRACT', v831, 0)
    v833 = g.math('CEIL', v832, 0.0)
    v834 = g.math('MAXIMUM', v833, 0.0)
    g.out_('Z1_it', v834, False)
    g.out_('Z1_s_N', v59, True)
    g.out_('Z1_s_Lloop0', 1.0, False)
    g.out_('Z1_s_lightAccum', (0, 0, 0), True)
    g.out_('Z1_s_lightIndex', 0, False)
    g.out_('Z1_s_positionWS', v15, True)
    g.out_('Z1_r_albedo', v341, True)
    g.out_('Z1_r___done', 0.0, False)
    g.out_('Z1_r_pixelLightCount', v831, False)
    v835 = g.inp('Z1_o_N', True)
    v836 = g.inp('Z1_o_Lloop0', False)
    v837 = g.inp('Z1_o_lightAccum', True)
    v838 = g.inp('Z1_o_lightIndex', False)
    v839 = g.inp('Z1_o_positionWS', True)
    v840 = g.vmath('ADD', v827, v837)
    v841 = g.sep(v827)
    v842 = g.sep(v840)
    v843 = g.comb(v842[0], v842[1], v842[2])
    g.out_('ret_gBuffer0', v843, True)
    g.out_('ret_gBuffer0_w', v830, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v827, True)
    g.out_('ret_color_w', v830, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)


SHARED_GROUPS = [
    ('RCE_ApplyEndfieldOutlineAlbedo', build_RCE_ApplyEndfieldOutlineAlbedo),
    ('RCE_BRDF_AnisotropicNDF_SilkStockings_Endfield', build_RCE_BRDF_AnisotropicNDF_SilkStockings_Endfield),
    ('RCE_BRDF_ClearCoat_Direct_Burley', build_RCE_BRDF_ClearCoat_Direct_Burley),
    ('RCE_ComputeCamLightFactors', build_RCE_ComputeCamLightFactors),
    ('RCE_ComputeExposure', build_RCE_ComputeExposure),
    ('RCE_ComputeNPRDiffuse', build_RCE_ComputeNPRDiffuse),
    ('RCE_ComputeSkinDir', build_RCE_ComputeSkinDir),
    ('RCE_ComputeSkinSmoothFalloff', build_RCE_ComputeSkinSmoothFalloff),
    ('RCE_ComputeSkinSpec', build_RCE_ComputeSkinSpec),
    ('RCE_ComputeVFXUV_Endfield', build_RCE_ComputeVFXUV_Endfield),
    ('RCE_D_GGX_Float', build_RCE_D_GGX_Float),
    ('RCE_BRDF_GGX_Stylized_Endfield', build_RCE_BRDF_GGX_Stylized_Endfield),
    ('RCE_EnvBRDF_Endfield', build_RCE_EnvBRDF_Endfield),
    ('RCE_EnvironmentWaterSubmersion', build_RCE_EnvironmentWaterSubmersion),
    ('RCE_EnvironmentWetness', build_RCE_EnvironmentWetness),
    ('RCE_GetObjectFlatDir', build_RCE_GetObjectFlatDir),
    ('RCE_IBL_SplitSumCombine', build_RCE_IBL_SplitSumCombine),
    ('RCE_ResolveAdjustedLight', build_RCE_ResolveAdjustedLight),
    ('RCE_Shell_AOFromNormalZ', build_RCE_Shell_AOFromNormalZ),
    ('RCE_Shell_TransmittedNdotL_Endfield', build_RCE_Shell_TransmittedNdotL_Endfield),
    ('RCE_Subsurf_EdgeGate', build_RCE_Subsurf_EdgeGate),
    ('RCE_BRDF_SubsurfaceSpec_Endfield', build_RCE_BRDF_SubsurfaceSpec_Endfield),
    ('RCE_VFXColorAdjust', build_RCE_VFXColorAdjust),
    ('RCE_Z_Ruri_Endfield_Uber_Standard_0', build_RCE_Z_Ruri_Endfield_Uber_Standard_0),
    ('RCE_Z_Ruri_Endfield_Uber_Standard_1', build_RCE_Z_Ruri_Endfield_Uber_Standard_1),
    ('RCE_Z_Ruri_Endfield_Uber_Face_0', build_RCE_Z_Ruri_Endfield_Uber_Face_0),
    ('RCE_Z_Ruri_Endfield_Uber_Eyes_0', build_RCE_Z_Ruri_Endfield_Uber_Eyes_0),
    ('RCE_Z_Ruri_Endfield_Uber_Hair_0', build_RCE_Z_Ruri_Endfield_Uber_Hair_0),
    ('RCE_Z_Ruri_Endfield_Uber_Fur_0', build_RCE_Z_Ruri_Endfield_Uber_Fur_0),
    ('RCE_Z_Ruri_Endfield_Uber_Eyebrow_0', build_RCE_Z_Ruri_Endfield_Uber_Eyebrow_0),
    ('RCE_Z_Ruri_Endfield_Uber_VFX_0', build_RCE_Z_Ruri_Endfield_Uber_VFX_0),
    ('RCE_Z_Ruri_Endfield_Uber_OverlayShadow_0', build_RCE_Z_Ruri_Endfield_Uber_OverlayShadow_0),
    ('RCE_Z_Ruri_Endfield_Uber_LiquidAg_0', build_RCE_Z_Ruri_Endfield_Uber_LiquidAg_0),
    ('RCE_Z_Ruri_Endfield_Uber_LiquidAg_1', build_RCE_Z_Ruri_Endfield_Uber_LiquidAg_1),
]

PARTS = {
    'Standard': ('Ruri Endfield Uber Standard', build_Ruri_Endfield_Uber_Standard),
    'Face': ('Ruri Endfield Uber Face', build_Ruri_Endfield_Uber_Face),
    'Eyes': ('Ruri Endfield Uber Eyes', build_Ruri_Endfield_Uber_Eyes),
    'Hair': ('Ruri Endfield Uber Hair', build_Ruri_Endfield_Uber_Hair),
    'Fur': ('Ruri Endfield Uber Fur', build_Ruri_Endfield_Uber_Fur),
    'Eyebrow': ('Ruri Endfield Uber Eyes', build_Ruri_Endfield_Uber_Eyes),
    'VFX': ('Ruri Endfield Uber VFX', build_Ruri_Endfield_Uber_VFX),
    'OverlayShadow': ('Ruri Endfield Uber OverlayShadow', build_Ruri_Endfield_Uber_OverlayShadow),
    'LiquidAg': ('Ruri Endfield Uber LiquidAg', build_Ruri_Endfield_Uber_LiquidAg),
}

CASCADE = {
    'Standard': 3,
    'Face': 4,
    'Eyes': 3,
    'Hair': 3,
    'Fur': 4,
    'Eyebrow': 3,
    'VFX': 3,
    'OverlayShadow': 3,
    'LiquidAg': 3,
}

FETCHES = {
    'Standard': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_ClearCoatMask', 'slot': '_ClearCoatMask', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_SilkStockingsMask', 'slot': '_SilkStockingsMask', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_EmissionMap', 'slot': '_EmissionMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_ParallaxTex', 'slot': '_ParallaxTex', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': True, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_SpecRampMap', 'slot': '_SpecRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_IBL_CharMaxCubemap', 'slot': 'IBL_CharMaxCubemap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F14_IBL_CharMaxCubemap', 'slot': 'IBL_CharMaxCubemap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
    ],
    'Face': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_EmotionMap', 'slot': '_EmotionMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_SDFMask', 'slot': '_SDFMask', 'depth': 0, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_SDFLightmap', 'slot': '_SDFLightmap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 2, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_HighlightMap', 'slot': '_HighlightMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
    ],
    'Eyes': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 0, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_MatcapTex', 'slot': '_MatcapTex', 'depth': 0, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_EmissionMap', 'slot': '_EmissionMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
    ],
    'Hair': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_SplitNormalMap', 'slot': '_SplitNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 0.5), 'neutral_alpha': 1.0},
        {'sock': 'F7_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_StrokeMap', 'slot': '_StrokeMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 0.5), 'neutral_alpha': 1.0},
        {'sock': 'F10_SpecRampMap', 'slot': '_SpecRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_LineMap', 'slot': '_LineMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
    ],
    'Fur': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_FurDyeMap', 'slot': '_FurDyeMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_FurDirMap', 'slot': '_FurDirMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_FurMap', 'slot': '_FurMap', 'depth': 1, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_VFXSpecialBlendTex', 'slot': '_VFXSpecialBlendTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_VFXSpecialMainTex', 'slot': '_VFXSpecialMainTex', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 2, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_SpecRampMap', 'slot': '_SpecRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_IBL_unity_SpecCube0', 'slot': 'IBL_unity_SpecCube0', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
    ],
    'Eyebrow': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 0, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_MatcapTex', 'slot': '_MatcapTex', 'depth': 0, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_EmissionMap', 'slot': '_EmissionMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
    ],
    'VFX': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_DisturbTex1', 'slot': '_DisturbTex1', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_MainTex', 'slot': '_MainTex', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MaskTex', 'slot': '_MaskTex', 'depth': 1, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_BlendTex', 'slot': '_BlendTex', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_NormalMap', 'slot': '_NormalMap', 'depth': 1, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'OverlayShadow': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'LiquidAg': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BumpMap', 'slot': '_BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_RMOSMap', 'slot': '_RMOSMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_MetallicGlossMap', 'slot': '_MetallicGlossMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_ShadowLutTex', 'slot': '_ShadowLutTex', 'depth': 1, 'non_color': False, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_ClearCoatMask', 'slot': '_ClearCoatMask', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_SilkStockingsMask', 'slot': '_SilkStockingsMask', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_EmissionMap', 'slot': '_EmissionMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_ParallaxTex', 'slot': '_ParallaxTex', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': True, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_VFXSpecialBlendTex', 'slot': '_VFXSpecialBlendTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_VFXSpecialMainTex', 'slot': '_VFXSpecialMainTex', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_DiffRampMap', 'slot': '_DiffRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_SpecRampMap', 'slot': '_SpecRampMap', 'depth': 1, 'non_color': True, 'extension': 'EXTEND', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_IBL_CharMaxCubemap', 'slot': 'IBL_CharMaxCubemap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F16_IBL_CharMaxCubemap', 'slot': 'IBL_CharMaxCubemap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
    ],
}

ZONES = {
    'Standard': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_Standard_0', 'depth': 0, 'cascade': 2,
         'states': [('Lloop0', False), ('pxAccum', True), ('pxDxUV', True), ('pxDyUV', True), ('pxHit', False), ('pxHitH', False), ('pxLayerH', False), ('pxPrevH', False), ('pxPrevLayerH', False), ('pxPrevOff', True), ('pxi', False)],
         'reads': [('__done', False), ('pxUV', True), ('pxSteps', False), ('pxStepSz', False), ('pxUVDelta', True)],
         'uniforms': [],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxTex', 'slot': '_ParallaxTex', 'depth': 0, 'non_color': True, 'extension': 'EXTEND', 'point': True, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Uber_Standard_1', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Face': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_Face_0', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Eyes': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_Eyes_0', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Hair': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_Hair_0', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Fur': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_Fur_0', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Eyebrow': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_Eyebrow_0', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'VFX': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_VFX_0', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'OverlayShadow': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_OverlayShadow_0', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'LiquidAg': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Uber_LiquidAg_0', 'depth': 0, 'cascade': 2,
         'states': [('Lloop0', False), ('pxAccum', True), ('pxDxUV', True), ('pxDyUV', True), ('pxHit', False), ('pxHitH', False), ('pxLayerH', False), ('pxPrevH', False), ('pxPrevLayerH', False), ('pxPrevOff', True), ('pxi', False)],
         'reads': [('__done', False), ('pxUV', True), ('pxSteps', False), ('pxStepSz', False), ('pxUVDelta', True)],
         'uniforms': [],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxTex', 'slot': '_ParallaxTex', 'depth': 0, 'non_color': True, 'extension': 'EXTEND', 'point': True, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Uber_LiquidAg_1', 'depth': 1, 'cascade': 2,
         'states': [('N', True), ('Lloop0', False), ('lightAccum', True), ('lightIndex', False), ('positionWS', True)],
         'reads': [('albedo', True), ('__done', False), ('pixelLightCount', False)],
         'uniforms': [],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
}

CAPABILITIES = {
    'Standard': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Face': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Eyes': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Hair': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Fur': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Eyebrow': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'VFX': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'OverlayShadow': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'LiquidAg': [
        {'sock': 'C0_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C1_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
}

DEFAULT_PART = 'Standard'
STAMP = '13c144af1b658f33'
STAMP_KEY = 'ruri_uber_stamp'


def build_Ruri_Endfield_Uber_Vertex_Fur():
    t = _gntree('Ruri Endfield Uber Vertex Fur')
    g = GV(t)
    v0 = g.inp('input_positionOS', True)
    v1 = g.inp('input_positionOS_w', False)
    v2 = g.inp('input_normalOS', True)
    v3 = g.inp('input_tangentOS', True)
    v4 = g.inp('input_tangentOS_w', False)
    v5 = g.inp('input_texcoord', True)
    v6 = g.inp('input_texcoord_w', False)
    v7 = g.inp('input_texcoord1', True)
    v8 = g.inp('input_texcoord1_w', False)
    v9 = g.inp('input_texcoord2', True)
    v10 = g.inp('input_texcoord2_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.b2u(v0, point=True)
    v14 = g.b2u(v2, point=False)
    v15 = g.b2u(v3, point=False)
    v16 = g.vmath('NORMALIZE', v14)
    v17 = g.vmath('NORMALIZE', v15)
    v18 = g.vmath('CROSS_PRODUCT', v16, v17)
    v19 = g.vmath('SCALE', v18, s=v4)
    v20 = g.sep(v7)
    v21 = g.sep(v5)
    v22 = g.comb(v21[0], v21[1], 0.0)
    v23 = g.sep(v16)
    v24 = g.math('MULTIPLY', 0.5, v23[1])
    v25 = g.math('SUBTRACT', 0.5, v24)
    v26 = g.math('MULTIPLY', v25, v20[0])
    v27 = g.inp('_FurGravityStrength', False, 0.0)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('SUBTRACT', 1, v28)
    v30 = g.bc(v29)
    v31 = g.vmath('MULTIPLY', v16, v30)
    v32 = g.math('MULTIPLY', 1, -1.0)
    v33 = g.comb(0, v32, 0)
    v34 = g.bc(v28)
    v35 = g.vmath('MULTIPLY', v33, v34)
    v36 = g.vmath('ADD', v31, v35)
    v37, v38 = g.tex('_FurDirMap', v22, non_color=True, extension='REPEAT')
    v39 = g.inp('_FurLengthIntensity', False, 1.0)
    v40 = g.math('MULTIPLY', v20[0], v39)
    v41 = g.math('MULTIPLY', v40, 0.01)
    v42 = g.math('MULTIPLY', v41, v38)
    v43 = g.bc(v42)
    v44 = g.vmath('MULTIPLY', v36, v43)
    v45 = g.vmath('ADD', v13, v44)
    v46 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), (0.5, 0.5, 0.5))
    v47 = g.sep(v46)
    v48 = g.inp('_ProjectionParams', True, (1.0, 0.1, 1000.0))
    v49 = g.inp('_ProjectionParams_w', False, 0.001)
    v50 = g.sep(v48)
    v51 = g.math('MULTIPLY', v47[1], v50[0])
    v52 = g.comb(v47[0], v51, 0.0)
    v53 = g.vmath('ADD', v52, (0.5, 0.5, 0.0))
    v54 = g.sep(v53)
    v55 = g.comb(v54[0], v54[1], 0.0)
    v56 = g.sep(v55)
    v57 = g.comb(v56[0], v56[1], 0.0)
    v58 = g.comb(v21[0], v21[1], 0.0)
    v59 = g.inp('_BaseMap_ST', True, (1.0, 1.0, 0.0))
    v60 = g.inp('_BaseMap_ST_w', False, 0.0)
    v61 = g.sep(v59)
    v62 = g.comb(v61[0], v61[1], 0.0)
    v63 = g.vmath('MULTIPLY', v58, v62)
    v64 = g.comb(v61[2], v60, 0.0)
    v65 = g.vmath('ADD', v63, v64)
    v66 = g.math('MULTIPLY', v4, 1.0)
    v67 = g.comb(v21[2], v6, 0.0)
    g.out_('ret_uv', v65, True)
    g.out_('ret_positionWS', v45, True)
    g.out_('ret_normalWS', v16, True)
    g.out_('ret_tangentWS', v17, True)
    g.out_('ret_tangentWS_w', v66, False)
    g.out_('ret_uv1', v7, True)
    g.out_('ret_uv1_w', v8, False)
    g.out_('ret_uv0zw', v67, True)
    g.out_('ret_positionNDC', v57, True)
    g.out_('ret_positionNDC_w', 1.0, False)
    g.out_('ret_color', v11, True)
    g.out_('ret_color_w', v12, False)
    g.out_('ret_positionCS', (0.0, 0.0, 0.0), True)
    g.out_('ret_positionCS_w', 1.0, False)


def build_Ruri_Endfield_Uber_Vertex_VFX():
    t = _gntree('Ruri Endfield Uber Vertex VFX')
    g = GV(t)
    v0 = g.inp('input_positionOS', True)
    v1 = g.inp('input_positionOS_w', False)
    v2 = g.inp('input_normalOS', True)
    v3 = g.inp('input_tangentOS', True)
    v4 = g.inp('input_tangentOS_w', False)
    v5 = g.inp('input_texcoord', True)
    v6 = g.inp('input_texcoord_w', False)
    v7 = g.inp('input_texcoord1', True)
    v8 = g.inp('input_texcoord1_w', False)
    v9 = g.inp('input_texcoord2', True)
    v10 = g.inp('input_texcoord2_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.b2u(v0, point=True)
    v14 = g.b2u(v2, point=False)
    v15 = g.b2u(v3, point=False)
    v16 = g.vmath('NORMALIZE', v14)
    v17 = g.vmath('NORMALIZE', v15)
    v18 = g.vmath('CROSS_PRODUCT', v16, v17)
    v19 = g.vmath('SCALE', v18, s=v4)
    v20 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v21 = g.vmath('SUBTRACT', v20, v13)
    v22 = g.vmath('NORMALIZE', v21)
    v23 = g.inp('_VertCameraOffset', False, 0.0)
    v24 = g.bc(v23)
    v25 = g.vmath('MULTIPLY', v22, v24)
    v26 = g.vmath('ADD', v13, v25)
    v27 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), (0.5, 0.5, 0.5))
    v28 = g.sep(v27)
    v29 = g.inp('_ProjectionParams', True, (1.0, 0.1, 1000.0))
    v30 = g.inp('_ProjectionParams_w', False, 0.001)
    v31 = g.sep(v29)
    v32 = g.math('MULTIPLY', v28[1], v31[0])
    v33 = g.comb(v28[0], v32, 0.0)
    v34 = g.vmath('ADD', v33, (0.5, 0.5, 0.0))
    v35 = g.sep(v34)
    v36 = g.comb(v35[0], v35[1], 0.0)
    v37 = g.sep(v36)
    v38 = g.comb(v37[0], v37[1], 0.0)
    v39 = g.sep(v5)
    v40 = g.comb(v39[0], v39[1], 0.0)
    v41 = g.inp('_BaseMap_ST', True, (1.0, 1.0, 0.0))
    v42 = g.inp('_BaseMap_ST_w', False, 0.0)
    v43 = g.sep(v41)
    v44 = g.comb(v43[0], v43[1], 0.0)
    v45 = g.vmath('MULTIPLY', v40, v44)
    v46 = g.comb(v43[2], v42, 0.0)
    v47 = g.vmath('ADD', v45, v46)
    v48 = g.math('MULTIPLY', v4, 1.0)
    v49 = g.comb(v39[2], v6, 0.0)
    g.out_('ret_uv', v47, True)
    g.out_('ret_positionWS', v26, True)
    g.out_('ret_normalWS', v16, True)
    g.out_('ret_tangentWS', v17, True)
    g.out_('ret_tangentWS_w', v48, False)
    g.out_('ret_uv1', v7, True)
    g.out_('ret_uv1_w', v8, False)
    g.out_('ret_uv0zw', v49, True)
    g.out_('ret_positionNDC', v38, True)
    g.out_('ret_positionNDC_w', 1.0, False)
    g.out_('ret_color', v11, True)
    g.out_('ret_color_w', v12, False)
    g.out_('ret_positionCS', (0.0, 0.0, 0.0), True)
    g.out_('ret_positionCS_w', 1.0, False)


VERTEX_PARTS = {
    'Fur': ('Ruri Endfield Uber Vertex Fur', build_Ruri_Endfield_Uber_Vertex_Fur),
    'VFX': ('Ruri Endfield Uber Vertex VFX', build_Ruri_Endfield_Uber_Vertex_VFX),
}

KNOWN_PARTS = {'Standard', 'Face', 'Eyes', 'Hair', 'Fur', 'Eyebrow', 'VFX', 'OverlayShadow', 'LiquidAg'}
VTX_MODIFIER = 'Ruri Endfield Uber Vertex'
VTX_TREE_PREFIX = 'Ruri Endfield Uber Vertex '
OUTLINE_TEMPLATE = 'Ruri Endfield Uber Outline'
CLONE_V_PREFIX = 'Ruri Endfield Uber V '
CLONE_O_PREFIX = 'Ruri Endfield Uber O '
RIG_BASIS_BONE = 'Bip001_Head'
RIG_BASIS_ATTR = 'ruri_face_basis'
RIG_BASIS_PARTS = {'Face'}

_BUILT = {}      # part -> 本会话在用的模板组(link 来的或现建的)
_SHARED_READY = False
_LIBRARY = None       # {组名: 已 link 的组}
_LIBRARY_TRIED = False
_LIBRARY_WRITTEN = False


def _ensure_shared():
    # 共享子组:全会话建一次(各 part 树引用同一份;换血逻辑与 part 相同)。
    global _SHARED_READY
    if _SHARED_READY:
        return
    for sgn, sbuild in SHARED_GROUPS:
        stale = bpy.data.node_groups.get(sgn)
        if stale is not None:
            stale.name = sgn + '.old'
        sbuild()
        built = bpy.data.node_groups[sgn]
        built.use_fake_user = True
        if stale is not None:
            stale.user_remap(built)
            bpy.data.node_groups.remove(stale)
    _SHARED_READY = True


def _library_path(create=False):
    """<用户脚本目录>/presets/<插件包名>/<本模块>.<STAMP>.blend。
    指纹进文件名而不是文件内容:'这份缓存配不配得上当前生成物' 就是一次
    存在性判断,不必先把库读出来再比对,也不存在读到过时组的窗口。"""
    package = (__package__ or '').split('.')[0] or 'RuriShaders'
    folder = bpy.utils.user_resource('SCRIPTS',
                                     path=os.path.join('presets', package),
                                     create=create)
    if not folder:
        return None
    return os.path.join(folder, '%s.%s.blend' % (__name__.rsplit('.', 1)[-1], STAMP))


def _alive(block):
    """这个数据块引用还活着吗。
    **必须问**:link 进来的模板组按 Blender 规矩是只读的,设不了 fake user,
    所以用它的材质一被删除(删角色再导一次就是这个流程),它就没有用户、被释放,
    而缓存字典还攥着那个指针 —— 再碰它是 ReferenceError,不是 None。
    缓存数据块引用就得配这个判活,否则等于缓存了一个悬垂指针。"""
    if block is None:
        return False
    try:
        block.name
    except ReferenceError:
        return False
    return True


def _prune_stale_libraries():
    """删掉本模块留在预设目录里的旧指纹库。**在模块导入时跑**,不是写库时:
    重新生成之后,一个这次没被用到的模块永远走不到写库那一步,它上一版的库就会
    一直堆在目录里。加载插件即清,一次 listdir。"""
    path = _library_path()
    if not path:
        return
    folder = os.path.dirname(path)
    keep = os.path.basename(path)
    prefix = __name__.rsplit('.', 1)[-1] + '.'
    try:
        stale_names = os.listdir(folder)
    except OSError:
        return
    for stale in stale_names:
        if stale == keep or not stale.startswith(prefix) or not stale.endswith('.blend'):
            continue
        try:
            os.remove(os.path.join(folder, stale))
            print('[Ruri] 删除过时着色器预设库:%s' % stale)
        except OSError as exc:
            print('[Ruri] 过时预设库删不掉(%s):%s' % (stale, exc))


def _library_groups():
    """预设库里的全部模板 + 共享组,LINK 进来(不是 append):图只在库里存一份,
    不复制进每个导入过东西的 .blend。库不在或读不出就返回 None,调用方现建。"""
    global _LIBRARY, _LIBRARY_TRIED
    if _LIBRARY is not None and not all(_alive(g) for g in _LIBRARY.values()):
        _LIBRARY = None       # 上一批 link 的组被释放了(见 _alive)——重连
        _LIBRARY_TRIED = False
    if _LIBRARY_TRIED:
        return _LIBRARY
    _LIBRARY_TRIED = True
    path = _library_path()
    if not path or not os.path.isfile(path):
        return None
    wanted = [n for n, _b in SHARED_GROUPS] + [n for n, _b in PARTS.values()]
    try:
        with bpy.data.libraries.load(path, link=True) as (src, dst):
            dst.node_groups = [n for n in dict.fromkeys(wanted) if n in src.node_groups]
        _LIBRARY = {g.name: g for g in dst.node_groups if g is not None}
    except Exception as exc:
        print('[Ruri] 着色器预设库读不出(%s):%s —— 改为现建' % (path, exc))
        _LIBRARY = None
    return _LIBRARY


def _write_library():
    """把本模块全部模板写成预设库,每会话至多一次。整套一起写:库要么完整要么
    不存在 —— 只写用过的 part 会让没写进去的那些每个会话都重建一次,而文件已在
    所以永远不会被补全。建出来的每个 part 同时登记进 _BUILT,本会话后面再要它就是
    字典命中(否则同一棵树会被换血重建一遍,实测每棵 9s)。同目录下别的指纹是上一版
    生成物留下的,顺手清掉。"""
    global _LIBRARY_WRITTEN
    if _LIBRARY_WRITTEN:
        return
    _LIBRARY_WRITTEN = True
    path = _library_path(create=True)
    if not path:
        return
    try:
        _ensure_shared()
        blocks = {bpy.data.node_groups[n] for n, _b in SHARED_GROUPS}
        for part_name, (group_name, builder) in PARTS.items():
            group = bpy.data.node_groups.get(group_name)
            if group is None:
                builder()
                group = bpy.data.node_groups[group_name]
                group.use_fake_user = True
                group[STAMP_KEY] = STAMP
            if not _alive(_BUILT.get(part_name)):
                _BUILT[part_name] = group
            blocks.add(group)
        bpy.data.libraries.write(path, blocks, fake_user=True, compress=True)
        print('[Ruri] 着色器预设库已写出:%s(%d 组)' % (path, len(blocks)))
    except Exception as exc:
        print('[Ruri] 着色器预设库写不出(%s):%s —— 下次仍会现建' % (path, exc))


def ensure(part=None, rebuild=False):
    """该 part 的模板组(纯数学,零资源节点)。命中预设库就 link 回来(零建图);
    库不在才现建并写库。贴图/循环 zone 全在本地材质树(见 build_material)。"""
    part = part or DEFAULT_PART
    group_name, builder = PARTS.get(part, PARTS[DEFAULT_PART])
    if not rebuild:
        ready = _BUILT.get(part)
        if _alive(ready):
            return ready
        library = _library_groups()
        linked = library.get(group_name) if library else None
        if linked is not None:
            _BUILT[part] = linked
            return linked
    stale = bpy.data.node_groups.get(group_name)
    if stale is not None and stale.library is None:
        stale.name = group_name + '.old'
    else:
        stale = None
    _ensure_shared()
    builder()
    built = bpy.data.node_groups[group_name]
    built.use_fake_user = True   # 零引用也随 blend 落盘
    built[STAMP_KEY] = STAMP     # 只作标记:哪次部署建的,不参与判定
    if stale is not None:
        stale.user_remap(built)  # 旧组的用户(上次导入的克隆源)改指新组后删除
        bpy.data.node_groups.remove(stale)
    _BUILT[part] = built
    if not rebuild:
        _write_library()
    return built


def _teximage(g, fetch, image, force_closest=False):
    nd = g._nd('ShaderNodeTexImage')
    nd.label = fetch['slot']
    nd.width = 260
    nd.extension = fetch['extension']
    if force_closest or fetch['point']:
        nd.interpolation = 'Closest'
    nd.image = image
    if image is not None:
        try:
            image.colorspace_settings.name = 'Non-Color' if fetch['non_color'] else 'sRGB'
        except Exception:
            pass
        if fetch['non_color']:
            _fix_two_channel_layout(image)
    return nd


def _sample(g, fetch, image, uv):
    """把割点声明的**采样语义**在宿主上兑现,返回 (Color, Alpha, 锚点节点)。
    内核只说 derivative_mip(真源写的是隐式采样形还是显式 LOD 形),不含任何滤波数学:
      · 吃屏幕导数 → 一个 TexImage,mip 交给 EEVEE;
      · 不吃导数(显式 LOD)→ Blender 的 TexImage 没有 LOD 口,只能在这里兑现:
        按**真图 image.size** 算纹素域,四角各取一次 Closest,手工双线性。
        四角是纹素寻址,恒 Closest —— 再让硬件插一次值,打包 LUT 就会跨切片
        平均掉(症状:暗部等高线状阈值线)。尺寸取自真图,不是手打元数据。"""
    if image is None:
        return None, None, None
    if fetch['derivative_mip'] or not image.size[0] or not image.size[1]:
        nd = _teximage(g, fetch, image)
        g._set(nd.inputs['Vector'], uv)
        return nd.outputs['Color'], nd.outputs['Alpha'], nd
    size = (float(image.size[0]), float(image.size[1]), 1.0)
    texel = g.vmath('SUBTRACT', g.vmath('MULTIPLY', uv, size), (0.5, 0.5, 0.0))
    base = g.vmath('FLOOR', texel)
    frac = g.vmath('SUBTRACT', texel, base)
    fx, fy, _fz = g.sep(frac)
    taps = []
    for dy in (0.5, 1.5):
        for dx in (0.5, 1.5):
            nd = _teximage(g, fetch, image, force_closest=True)
            g._set(nd.inputs['Vector'],
                   g.vmath('DIVIDE', g.vmath('ADD', base, (dx, dy, 0.0)), size))
            taps.append(nd)
    top = g.mixv(fx, taps[0].outputs['Color'], taps[1].outputs['Color'])
    bot = g.mixv(fx, taps[2].outputs['Color'], taps[3].outputs['Color'])
    top_a = g.mixf(fx, taps[0].outputs['Alpha'], taps[1].outputs['Alpha'])
    bot_a = g.mixf(fx, taps[2].outputs['Alpha'], taps[3].outputs['Alpha'])
    return g.mixv(fy, top, bot), g.mixf(fy, top_a, bot_a), taps[0]


def _feed(g, heads, sock, color, alpha):
    if color is None:
        return
    for c in heads:
        g._set(c.inputs[sock], color)
        a = c.inputs.get(sock + '_alpha')
        if a is not None and alpha is not None:
            g._set(a, alpha)


def _wire_fetch(g, insts, fetch, image):
    src = insts[fetch['depth']]
    heads = insts[fetch['depth'] + 1:]
    if fetch['env']:
        if image is None:
            return None
        mip = src.outputs[fetch['sock'] + '_mip'] if fetch['mip'] else None
        color, alpha = g.env_image(image, src.outputs[fetch['sock'] + '_dir'], mip)
        _feed(g, heads, fetch['sock'], color, alpha)
        return None
    color, alpha, anchor = _sample(g, fetch, image, src.outputs[fetch['sock'] + '_uv'])
    _feed(g, heads, fetch['sock'], color, alpha)
    return anchor


def _wire_capability(g, insts, cap, ctx):
    """环境询问的**兑现面**:数学树只切了一对接口 —— 询问从组输出逐语义导出、
    答案从组输入回灌。这里按 (能力身份 × 当前渲染引擎) 查 CAP_BUILDERS 拿宿主原生等价物。
    建图器契约:答得出返回 **{结果叶名: 值}** 字典,答不出返回 None。
    单值能力的叶名是 '' (V4 另有 'w'),结构化能力逐字段一叶(Light 的 direction/color/…)——
    恒字典是为了让「平色世界给的三分量常量」与「多叶结果」不可能撞形。
    查不到就什么都不接:socket 停在**能力自己声明的缺席值**上(生成期已写进接口默认),
    并大声记一笔 —— 绝不静默拿个中性数冒充兑现。"""
    src = insts[cap['depth']]
    heads = insts[cap['depth'] + 1:]
    engine = getattr(bpy.context.scene.render, 'engine', '')
    builder = CAP_BUILDERS.get(cap['cap'], {}).get(engine) or CAP_BUILDERS.get(cap['cap'], {}).get('*')
    if builder is None:
        print('[ruri-cap] {0}: {1} 无原生等价物 → 缺席值(见 _BLENDER_UNSUPPORTED.md)'.format(
            engine, cap['cap']), flush=True)
        return
    query = {}
    for name in cap['query']:
        out = src.outputs.get(cap['sock'] + '_' + name)
        if out is None:
            # 表里有这条询问、组上却没有对应导出 socket = 生成期割点表与发射文本脱节。
            # 大声炸掉,别拿半份询问去兑现(半份询问接出来的图是错的,不是缺的)。
            raise RuntimeError('[ruri-cap] {0}: 询问 socket {1}_{2} 不在组接口上'.format(
                cap['cap'], cap['sock'], name))
        query[name] = out
    before = set(n.as_pointer() for n in g.t.nodes)
    answer = builder(g, query, ctx)
    if answer is None:
        print('[ruri-cap] {0}: {1} 本场景答不出 → 缺席值'.format(engine, cap['cap']), flush=True)
        return
    for nd in g.t.nodes:
        if nd.as_pointer() not in before:
            nd['ruri_cap'] = cap['cap']   # 重接时按这个标记精确回收
    def _feed_record(record, prefix):
        missing = [leaf for leaf in cap['results'] if leaf not in record]
        if missing:
            # 半份答案比没有答案更坏:接上去的图看着对、算出来是错的。
            raise RuntimeError('[ruri-cap] {0}: 建图器少答了结果叶 {1}'.format(cap['cap'], missing))
        for leaf, value in record.items():
            name = prefix + ('_' + leaf if leaf else '')
            for c in heads:
                s = c.inputs.get(name)
                if s is not None:
                    g._set(s, value)
    _feed_record(answer, cap['sock'])


def _wire_zone(g, insts, zone, images, inst_sink, part, ctx):
    feed = insts[zone['depth']]
    heads = insts[zone['depth'] + 1:]
    body_tpl = bpy.data.node_groups[zone['body']]
    zin = g._nd('GeometryNodeRepeatInput')
    zout = g._nd('GeometryNodeRepeatOutput')
    zin.pair_with_output(zout)
    zout.repeat_items.clear()
    for item, is_vec in zone['states']:
        zout.repeat_items.new('VECTOR' if is_vec else 'FLOAT', item)
    g._set(zin.inputs['Iterations'], feed.outputs[zone['sock'] + '_it'])
    for item, is_vec in zone['states']:
        out = feed.outputs.get(zone['sock'] + '_s_' + item)
        if out is not None:
            g._set(zin.inputs[item], out)
    binsts = []
    for _i in range(zone['cascade']):
        b = g._nd('ShaderNodeGroup')
        b.node_tree = body_tpl
        binsts.append(b)
        inst_sink.append(b)
        b['ruri_zone'] = zone['sock']   # 体实例的身份:重接体内割点要按 (zone, 序) 找回
        b['ruri_binst'] = _i
        for item, is_vec in zone['states']:
            sock = b.inputs.get('s_' + item)
            if sock is not None:
                g._set(sock, zin.outputs[item])
        for rname, is_vec in zone['reads']:
            sock = b.inputs.get('r_' + rname)
            out = feed.outputs.get(zone['sock'] + '_r_' + rname)
            if sock is not None and out is not None:
                g._set(sock, out)
    for c in zone['capabilities']:
        _wire_capability(g, binsts, c, ctx)
    for f in zone['fetches']:
        src = binsts[f['depth']]
        heads = binsts[f['depth'] + 1:]
        image = images.get(f['slot']) if images else None
        if f['env']:
            if image is None:
                continue
            mip = src.outputs[f['sock'] + '_mip'] if f['mip'] else None
            color, alpha = g.env_image(image, src.outputs[f['sock'] + '_dir'], mip)
            _feed(g, heads, f['sock'], color, alpha)
            continue
        color, alpha, _anchor = _sample(g, f, image, src.outputs[f['sock'] + '_uv'])
        _feed(g, heads, f['sock'], color, alpha)
    last = binsts[-1]
    for item, is_vec in zone['states']:
        out = last.outputs.get('o_' + item)
        if out is not None:
            g._set(zout.inputs[item], out)
    for c in heads:
        for item, is_vec in zone['states']:
            sock = c.inputs.get(zone['sock'] + '_o_' + item)
            if sock is not None:
                g._set(sock, zout.outputs[item])


_ANCHOR_SLOTS = ('_BaseMap', '_BaseColorMap', '_MainTex')


def build_material(mat, part=None, opaque=True, multiply_blend=False, cull=2.0, images=None):
    part = part or DEFAULT_PART
    template = ensure(part)
    nt = mat.node_tree
    nt.nodes.clear()
    g = G(nt, is_group=False)
    insts = []
    for _i in range(CASCADE.get(part, 1)):
        grp = g._nd('ShaderNodeGroup')
        grp.node_tree = template
        grp.width = 320
        insts.append(grp)
    geo = g.geo()
    tc = g.texco()
    tan_attr = g.attr('ruri_tangent')
    tan_sign = g.attr('ruri_tangent_sign')
    tan_ws = g.vtrans(tan_attr.outputs['Vector'], 'OBJECT', 'WORLD', 'VECTOR')
    col = g.attr('Color')
    uv1 = g._nd('ShaderNodeUVMap')
    uv1.uv_map = 'UV1'
    stmap = g._nd('ShaderNodeMapping')
    stmap.label = 'RuriBaseMapST'
    g._set(stmap.inputs['Vector'], tc.outputs['UV'])
    olattr = g._nd('ShaderNodeAttribute')
    olattr.attribute_name = 'ruri_outline'
    # 对象空间位置 varying(树冠/树干高度渐变消费):texco Object 已是对象空间,
    # 只差导入换轴的逆(Unity(x,y,z)=Blender(x,z,y))——入口 b2u 只管 WORLD varying,
    # OS 量在接线处换,免得混进世界系换轴的 WORLD→OBJECT 一步。
    os_sep = g._nd('ShaderNodeSeparateXYZ')
    g._set(os_sep.inputs['Vector'], tc.outputs['Object'])
    os_comb = g._nd('ShaderNodeCombineXYZ')
    g._set(os_comb.inputs['X'], os_sep.outputs['X'])
    g._set(os_comb.inputs['Y'], os_sep.outputs['Z'])
    g._set(os_comb.inputs['Z'], os_sep.outputs['Y'])
    wires = {
        '_RuriOutlineShellGate': olattr.outputs['Fac'],
        'input_uv': stmap.outputs['Vector'],
        'input_uv1': uv1.outputs['UV'],
        'input_normalWS': geo.outputs['Normal'],
        'input_positionWS': geo.outputs['Position'],
        'input_positionOS': os_comb.outputs['Vector'],
        'input_tangentWS': tan_ws,
        'input_tangentWS_w': tan_sign.outputs['Fac'],
        'input_color': col.outputs['Color'],
        'input_color_w': col.outputs['Alpha'],
        'facing': 1.0,
    }
    for grp in insts:
        for s in grp.inputs:
            if s.name in wires:
                g._set(s, wires[s.name])
    all_insts = list(insts)
    # 级联实例记序 + part 记名:重接兑现面(换灯/换世界)按 depth 取实例,
    # 而建图序事后从节点表里**恢复不出来** —— 只能建的时候就写下。
    for _i, _grp in enumerate(insts):
        _grp['ruri_inst'] = _i
    mat['ruri_uber_part'] = part
    anchor = None
    for f in FETCHES.get(part, ()):
        image = images.get(f['slot']) if images else None
        nd = _wire_fetch(g, insts, f, image)
        if nd is not None and nd.image is not None:
            if anchor is None or (f['slot'] in _ANCHOR_SLOTS and (anchor.label not in _ANCHOR_SLOTS)):
                anchor = nd
    for c in CAPABILITIES.get(part, ()):
        _wire_capability(g, insts, c, {'material': mat})
    for z in ZONES.get(part, ()):
        _wire_zone(g, insts, z, images, all_insts, part, {'material': mat})
    if anchor is not None:
        nt.nodes.active = anchor
        anchor.select = True
    last = insts[-1]
    color_sock = last.outputs.get('ret_gBuffer0')
    if color_sock is None:
        color_sock = next((s for s in last.outputs if s.type == 'VECTOR'), None)
    alpha_sock = last.outputs.get('ret_gBuffer0_w')
    clip_sock = last.outputs.get('__clip')
    em = g._nd('ShaderNodeEmission')
    tr = g._nd('ShaderNodeBsdfTransparent')
    mixsh = g._nd('ShaderNodeMixShader')
    if multiply_blend:
        if color_sock is not None:
            g._set(tr.inputs['Color'], color_sock)
        g._set(mixsh.inputs[0], 0.0)
        g._set(mixsh.inputs[1], tr.outputs[0])
        g._set(mixsh.inputs[2], em.outputs[0])
        outp = g._nd('ShaderNodeOutputMaterial')
        g._set(outp.inputs[0], _apply_cull(g, mixsh.outputs[0], cull, olattr.outputs['Fac']))
        return all_insts
    else:
        if color_sock is not None:
            g._set(em.inputs[0], color_sock)
        if opaque or alpha_sock is None:
            alpha = 1.0
        else:
            alpha = alpha_sock
    if clip_sock is not None:
        alpha = g.math('MULTIPLY', alpha, clip_sock)
    g._set(mixsh.inputs[0], alpha)
    g._set(mixsh.inputs[1], tr.outputs[0])
    g._set(mixsh.inputs[2], em.outputs[0])
    outp = g._nd('ShaderNodeOutputMaterial')
    g._set(outp.inputs[0], _apply_cull(g, mixsh.outputs[0], cull, olattr.outputs['Fac']))
    return all_insts


def _apply_cull(g, shader_sock, cull, outline_fac):
    cgeo = g._nd('ShaderNodeNewGeometry')
    bf = cgeo.outputs['Backfacing']
    if cull == 1.0:
        tr_fac = g.math('SUBTRACT', 1.0, bf)
    elif cull == 2.0:
        tr_fac = g.math('ABSOLUTE', g.math('SUBTRACT', bf, outline_fac))
    else:
        tr_fac = g.math('MULTIPLY', outline_fac, g.math('SUBTRACT', 1.0, bf))
    ctr = g._nd('ShaderNodeBsdfTransparent')
    cmix = g._nd('ShaderNodeMixShader')
    cmix.label = 'RuriCullMix'
    g._set(cmix.inputs[0], tr_fac)
    g._set(cmix.inputs[1], shader_sock)
    g._set(cmix.inputs[2], ctr.outputs[0])
    return cmix.outputs[0]


def build_root(part=None):
    mat = bpy.data.materials.get('Ruri_Endfield_Uber')
    if mat is None:
        mat = bpy.data.materials.new('Ruri_Endfield_Uber')
    if mat.node_tree is None:
        mat.use_nodes = True  # 5.2 新材质默认带树;此行仅旧数据兜底。
    group = ensure(part)
    build_material(mat, part=part)
    print('[ruri-blender] ' + group.name + ':  ' + str(len(group.nodes)) + ' 节点')
    return mat


# ============================ 顶点腿(几何节点) ============================
_VTX_BUILT = set()


def build_vtx_outline():
    t = _gntree(OUTLINE_TEMPLATE)
    g = GV(t)
    def nattr(name, dtype):
        nd = g._nd('GeometryNodeInputNamedAttribute')
        nd.data_type = dtype
        nd.inputs['Name'].default_value = name
        return nd
    p = g._nd('GeometryNodeInputPosition').outputs['Position']
    n_raw = g._nd('GeometryNodeInputNormal').outputs['Normal']
    cam_right = g.inp('cam_right', True, (1.0, 0.0, 0.0))
    cam_up = g.inp('cam_up', True, (0.0, 0.0, 1.0))
    cam_look = g.inp('cam_look', True, (0.0, 1.0, 0.0))
    cam_pos = g.inp('cam_pos', True, (0.0, -5.0, 1.0))
    half_fov = g.inp('half_fov', False, 0.2)
    screen_x = g.inp('screen_x', False, 1920.0)
    screen_y = g.inp('screen_y', False, 1080.0)
    width_in = g.inp('_OutlineWidth', False, 0.0)
    offset_z = g.inp('_OutlineOffsetZ', False, 0.0)
    use_mask = g.inp('_EnableOutlineMask', False, 0.0)
    avg_normal = g.inp('_OutlineAverageNormal', False, 0.0)
    st_scale = g.inp('mask_st_scale', True, (1.0, 1.0, 0.0))
    st_off = g.inp('mask_st_offset', True, (0.0, 0.0, 0.0))
    # smooth normal(源 L167-177):UV2 切空间 xy + Z 重建 + TBN;无 UV2 落几何法线。
    uv2 = nattr('UV2', 'FLOAT_VECTOR')
    tan_a = nattr('ruri_tangent', 'FLOAT_VECTOR').outputs['Attribute']
    sign_a = nattr('ruri_tangent_sign', 'FLOAT').outputs['Attribute']
    ts = uv2.outputs['Attribute']
    z2 = g.math('SQRT', g.math('MAXIMUM', g.math('SUBTRACT', 1.0, g.vmath('DOT_PRODUCT', ts, ts)), 0.0))
    t_n = g.vmath('NORMALIZE', tan_a)
    bit = g.vmath('SCALE', g.vmath('CROSS_PRODUCT', n_raw, t_n), s=sign_a)
    tsx, tsy, _tsz = g.sep(ts)
    n_smooth = g.vmath('NORMALIZE', g.vmath('ADD', g.vmath('ADD',
        g.vmath('SCALE', t_n, s=tsx), g.vmath('SCALE', bit, s=tsy)), g.vmath('SCALE', n_raw, s=z2)))
    use_smooth = g.math('MULTIPLY', avg_normal, uv2.outputs['Exists'])
    n = g.mixv(use_smooth, g.vmath('NORMALIZE', n_raw), n_smooth)
    # 屏幕方向 + 宽度(源 L179-207 逐常量)。
    w = g.vmath('DOT_PRODUCT', cam_look, g.vmath('SUBTRACT', p, cam_pos))
    nvx = g.vmath('DOT_PRODUCT', cam_right, n)
    nvy = g.vmath('DOT_PRODUCT', cam_up, n)
    aspect = g.math('DIVIDE', screen_y, screen_x)
    nsx = g.math('MULTIPLY', nvx, aspect)
    inv_len = g.math('INVERSE_SQRT', g.math('ADD', g.math('ADD',
        g.math('MULTIPLY', nsx, nsx), g.math('MULTIPLY', nvy, nvy)), 1e-08)
        )
    width = g.math('MULTIPLY', g.math('DIVIDE', 0.3926990, half_fov), width_in)
    dist_atten = g.clampn(g.math('MULTIPLY', g.math('MULTIPLY', w, half_fov), 4.5837))
    offx = g.math('MULTIPLY', g.math('MULTIPLY', dist_atten, width),
                  g.math('MULTIPLY', g.math('MULTIPLY', inv_len, nsx), g.math('MULTIPLY', aspect, 0.005)))
    offy = g.math('MULTIPLY', g.math('MULTIPLY', dist_atten, width),
                  g.math('MULTIPLY', g.math('MULTIPLY', inv_len, nvy), 0.005))
    max_extent = g.math('MINIMUM', g.math('DIVIDE', 1.5707964, half_fov), g.math('MAXIMUM', w, 0.0))
    mpx = g.math('DIVIDE', max_extent, screen_x)
    mpy = g.math('DIVIDE', max_extent, screen_y)
    small_x = g.math('LESS_THAN', g.math('ABSOLUTE', offx), mpx)
    small_y = g.math('LESS_THAN', g.math('ABSOLUTE', offy), mpy)
    offx = g.mixf(small_x, offx, g.math('MULTIPLY', g.math('SIGN', offx), mpx))
    offy = g.mixf(small_y, offy, g.math('MULTIPLY', g.math('SIGN', offy), mpy))
    # 宽度遮罩(源 GetEndfieldOutlineMask):r=宽度,g=Z 推移系数;钳制后再乘,与源同序。
    uv0 = nattr('UVMap', 'FLOAT_VECTOR').outputs['Attribute']
    mask_uv = g.vmath('ADD', g.vmath('MULTIPLY', uv0, st_scale), st_off)
    mc, _ma = g.tex('_OutlineMask', mask_uv, non_color=True, extension='EXTEND')
    mr, mg, _mb = g.sep(mc)
    width_mask = g.mixf(use_mask, 1.0, mr)
    z_val = g.mixf(use_mask, offset_z, g.math('MULTIPLY', mg, offset_z))
    offx = g.math('MULTIPLY', offx, width_mask)
    offy = g.math('MULTIPLY', offy, width_mask)
    # NDC → 对象空间(介质桥):Δview = off·tan(halfFov)(w 抵消;x 轴除回 aspect)。
    tan_h = g.math('TANGENT', half_fov)
    dvx = g.math('DIVIDE', g.math('MULTIPLY', offx, tan_h), aspect)
    dvy = g.math('MULTIPLY', offy, tan_h)
    # 真源末段 `clipPos.z = ...(viewZ = -w - 0.1*zOffsetVal)` 只改 clipPos.z、xy/w 全不动 =
    # **光栅深度测试偏置**,不是几何位移。Cycles 是光线求交、没有可独立偏置的深度值,
    # 而它买到的「壳的内侧被本体挡住」在光追里由真实几何序天然提供 → 落账目不发射。
    # (曾按 camLook·0.1·z_val 桥成世界平移:face/cloth 的壳被整体推走 3cm,1px 描边全埋。)
    world = g.vmath('ADD', g.vmath('SCALE', cam_right, s=dvx), g.vmath('SCALE', cam_up, s=dvy))
    g.out_('offset', world, True)


def _ensure_vtx(name, builder):
    if name in _VTX_BUILT and bpy.data.node_groups.get(name) is not None:
        return bpy.data.node_groups[name]
    _images()
    builder()
    built = bpy.data.node_groups[name]
    built.use_fake_user = True
    _VTX_BUILT.add(name)
    return built


def _material_images(mat):
    """顶点树换图的真源,两路合并:①provider 落的全量图名映射(.mat 绑定的每张图,
    含 _OutlineMask 这种只有顶点腿消费、材质树上没有节点的);②材质树(含 RCE 克隆
    子组)里 label=槽名的贴图节点。①在前:它才是 .mat 全集,②只是老场景兜底。"""
    images = {}
    for slot, img_name in dict(mat.get('ruri_uber_images') or {}).items():
        img = bpy.data.images.get(img_name)
        if img is not None:
            images[slot] = img
    def walk(tree, depth=0):
        if depth > 4 or tree is None:
            return
        for nd in tree.nodes:
            if nd.type == 'TEX_IMAGE' and nd.image is not None:
                images.setdefault(_slot_of(nd.label or nd.name or nd.image.name), nd.image)
            elif nd.type == 'GROUP':
                walk(nd.node_tree, depth + 1)
    if mat.node_tree is not None:
        walk(mat.node_tree)
    return images


def _clone_vtx(template_name, builder, clone_name, mat):
    template = _ensure_vtx(template_name, builder)
    clone = template.copy()
    stale = bpy.data.node_groups.get(clone_name)
    if stale is not None:
        stale.user_remap(clone)
        bpy.data.node_groups.remove(stale)
    clone.name = clone_name
    images = _material_images(mat)
    for nd in clone.nodes:
        if nd.bl_idname == 'GeometryNodeImageTexture':
            real = images.get(_slot_of(nd.label or ''))
            if real is not None:
                ph = nd.inputs['Image'].default_value
                non_color = ph is not None and ph.colorspace_settings.name == 'Non-Color'
                nd.inputs['Image'].default_value = real
                try:
                    real.colorspace_settings.name = 'Non-Color' if non_color else 'sRGB'
                except Exception:
                    pass
                if non_color:
                    _fix_two_channel_layout(real)
    return clone


def _mat_meta(mat):
    floats = dict(mat.get('ruri_uber_floats') or {})
    st = {k: list(v) for k, v in dict(mat.get('ruri_uber_st') or {}).items()}
    colors = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()}
    return floats, st, colors


def _fill_uniform_sockets(node, floats, st, colors):
    """raw 直灌顶点组实例:socket 名 = 游戏属性名(V4 = vec+_w 对),与材质面同一约定。"""
    for sock in node.inputs:
        name = sock.name
        if name.endswith('_w'):
            base = name[:-2]
            if base.endswith('_ST') and base[:-3] in st:
                sock.default_value = float(st[base[:-3]][3])
            elif base in colors:
                sock.default_value = float(colors[base][3])
            continue
        if name.endswith('_ST') and name[:-3] in st:
            v = st[name[:-3]]
            sock.default_value = (float(v[0]), float(v[1]), float(v[2]))
        elif name in colors:
            v = colors[name]
            sock.default_value = (float(v[0]), float(v[1]), float(v[2]))
        elif name in floats:
            try:
                sock.default_value = float(floats[name])
            except (TypeError, ValueError):
                pass


def _rig_basis_armature(obj):
    """网格挂在哪个骨架上 —— Bone Info 节点要的就是骨架**对象**加骨名,
    骨骼在不在是它自己的 Exists 口,这里不替它判。"""
    if not RIG_BASIS_BONE:
        return None
    arm = next((m.object for m in obj.modifiers
                if m.type == 'ARMATURE' and m.object is not None), None)
    if arm is None and obj.parent is not None and obj.parent.type == 'ARMATURE':
        arm = obj.parent
    return arm


def apply_vertex_stage(objects=None, camera=None):
    """给 uber 材质网格装顶点腿 modifier:壳层位移(VERTEX_PARTS 的 part)+ 反壳描边
    + 骨骼基座增量(RIG_BASIS_PARTS 的 part,逐帧由 depsgraph 自己算)。
    幂等:同名 modifier/树/克隆整体换血。相机基轴按当下快照 —— 相机动了重跑本函数。"""
    import mathutils
    scene = bpy.context.scene
    cam = camera or scene.camera
    if cam is None:
        print('[ruri-vertex] 场景无活动相机:描边壳照建,视图基(cam_right/up/half_fov)\n'
              '              留在组默认值 —— 设好 scene.camera 后重跑 apply_vertex_stage() 刷新。', flush=True)
    # 描边的 halfFov / 屏幕尺寸必须与真源同源:真源取 `-1 / ProjMatrix._m11`(投影矩阵反求
    # 垂直半 FOV)+ 真实 backbuffer 像素。`cam.data.angle_y` 只在渲染宽高比 == 传感器宽高比
    # 时才等于它;它同时喂 width / distAtten / minPixel 楼层三处,错一次三处一起错,
    # 楼层抬高就把全部部件钳到 1px、_OutlineWidth 的差异整体消失。
    half_fov_val, screen_px, screen_py = 0.2, 1920.0, 1080.0
    if cam is not None:
        import math
        rp = scene.render.resolution_percentage / 100.0
        screen_px = float(scene.render.resolution_x) * rp
        screen_py = float(scene.render.resolution_y) * rp
        proj = cam.calc_matrix_camera(
            bpy.context.evaluated_depsgraph_get(),
            x=scene.render.resolution_x, y=scene.render.resolution_y,
            scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y)
        half_fov_val = math.atan(1.0 / abs(proj[1][1]))
    done = 0
    # 快照必须**先剔掉** RuriOL:重跑时本函数会删同名旧描边对象,留在快照里的
    # 已删对象一旦被后续迭代碰到就是 ReferenceError(StructRNA has been removed)。
    for obj in [o for o in (objects if objects is not None else scene.objects)
                if not o.name.startswith('RuriOL ')]:
        # RuriOL* 是本函数自建的描边壳对象(共享本体 data,材质同样带 ruri_uber_part)
        # —— 不排除会给描边对象再建描边,无限套娃。
        if obj.type != 'MESH' or obj.data is None or obj.name.startswith('RuriOL '):
            continue
        slots = [(i, m) for i, m in enumerate(obj.data.materials)
                 if m is not None and m.get('ruri_uber_part') in KNOWN_PARTS
                 and not m.get('ruri_outline_clone')]   # 描边克隆继承 id props,不算 uber 槽
        vert_slots = [(i, m) for i, m in slots if m['ruri_uber_part'] in VERTEX_PARTS]
        basis_slots = [(i, m) for i, m in slots if m['ruri_uber_part'] in RIG_BASIS_PARTS]
        def _outline_on(mat):
            # 真判据三连:①材质自禁的 pass 列表(disabledShaderPasses,唯一的
            # per-材质关描边真值 —— _OutlineWidth 只是历史键,证明不了在用);
            # ②宽度>0;③_BaseColor.a>0(游戏 RefreshCharaMaterial 的关 pass 条件)。
            disabled = {str(p).lower() for p in (mat.get('ruri_uber_disabled_passes') or [])}
            if any('outline' in p for p in disabled):
                return False
            floats, _s, colors = _mat_meta(mat)
            base_a = colors.get('_BaseColor', [1, 1, 1, 1])[3]
            return float(floats.get('_OutlineWidth', 0.0)) > 0.0 and float(base_a) > 0.0
        # 描边发不发只看材质真值;相机只提供视图基(组的输入 socket,契约本来就是
        # 「相机动了重跑本函数」)。曾把 `cam is not None` 并进这个判据 —— 场景没设
        # 活动相机就一个描边对象都不建,而壳位移照装,静默得毫无痕迹。
        outline_slots = [(i, m) for i, m in slots if _outline_on(m)]
        if not vert_slots and not outline_slots and not basis_slots:
            continue
        tree_name = VTX_TREE_PREFIX + obj.name
        old = bpy.data.node_groups.get(tree_name)
        if old is not None:
            bpy.data.node_groups.remove(old)
        mt = bpy.data.node_groups.new(tree_name, 'GeometryNodeTree')
        mt.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        mt.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        gin = mt.nodes.new('NodeGroupInput')
        gout = mt.nodes.new('NodeGroupOutput')
        def nd(t_):
            return mt.nodes.new(t_)
        def nattr(name, dtype):
            a = nd('GeometryNodeInputNamedAttribute')
            a.data_type = dtype
            a.inputs['Name'].default_value = name
            return a.outputs['Attribute']
        def slot_sel(slot):
            eq = nd('ShaderNodeMath')
            eq.operation = 'COMPARE'
            mt.links.new(mat_idx, eq.inputs[0])
            eq.inputs[1].default_value = float(slot)
            eq.inputs[2].default_value = 0.5
            return eq.outputs[0]
        def swap_yz(sock):
            sep = nd('ShaderNodeSeparateXYZ')
            mt.links.new(sock, sep.inputs[0])
            comb = nd('ShaderNodeCombineXYZ')
            mt.links.new(sep.outputs[0], comb.inputs[0])
            mt.links.new(sep.outputs[2], comb.inputs[1])
            mt.links.new(sep.outputs[1], comb.inputs[2])
            return comb.outputs[0]
        mat_idx = nattr('material_index', 'INT')
        geo = gin.outputs[0]
        inv = obj.matrix_world.inverted()
        inv3 = inv.to_3x3()
        cam_vec = {}
        if cam is not None:
            cam3 = cam.matrix_world.to_3x3()
            cam_vec = {'cam_right': inv3 @ cam3.col[0], 'cam_up': inv3 @ cam3.col[1],
                       'cam_look': inv3 @ (cam3 @ mathutils.Vector((0.0, 0.0, -1.0))),
                       'cam_pos': inv @ cam.matrix_world.translation}
        # ① 壳层位移(part 的顶点树按材质克隆换图,uniform raw 直灌,选区 = 材质槽)。
        for slot, mat in vert_slots:
            part = mat['ruri_uber_part']
            vt_name, vt_builder = VERTEX_PARTS[part]
            clone = _clone_vtx(vt_name, vt_builder, CLONE_V_PREFIX + mat.name, mat)
            gn = nd('GeometryNodeGroup')
            gn.node_tree = clone
            floats, st, colors = _mat_meta(mat)
            _fill_uniform_sockets(gn, floats, st, colors)
            wires = {'input_positionOS': nd('GeometryNodeInputPosition').outputs['Position'],
                     'input_normalOS': nd('GeometryNodeInputNormal').outputs['Normal'],
                     'input_tangentOS': nattr('ruri_tangent', 'FLOAT_VECTOR'),
                     'input_tangentOS_w': nattr('ruri_tangent_sign', 'FLOAT'),
                     'input_texcoord': nattr('UVMap', 'FLOAT_VECTOR'),
                     'input_texcoord1': nattr('UV1', 'FLOAT_VECTOR'),
                     'input_texcoord2': nattr('UV2', 'FLOAT_VECTOR')}
        # 顶点色:网格缺席时读 0 —— 与游戏顶点色缺省 (1,1,1,1) 不同,但顶点位移路径不消费 color。
            for sock in gn.inputs:
                src = wires.get(sock.name)
                if src is not None:
                    mt.links.new(src, sock)
                elif sock.name == 'input_color':
                    sock.default_value = (1.0, 1.0, 1.0)
                elif sock.name == 'input_color_w':
                    sock.default_value = 1.0
                elif sock.name == 'input_camPos' and cam_vec:
                    sock.default_value = tuple(cam_vec['cam_pos'])
            out_sock = gn.outputs.get('ret_positionWS')
            if out_sock is None:
                continue
            sp = nd('GeometryNodeSetPosition')
            mt.links.new(geo, sp.inputs['Geometry'])
            mt.links.new(slot_sel(slot), sp.inputs['Selection'])
            mt.links.new(swap_yz(out_sock), sp.inputs['Position'])
            geo = sp.outputs['Geometry']
        # ② 反壳描边 = **同一棵树里的虚拟几何**:原始几何分叉一条描边支,逐槽外扩、
        #    删无描边槽的面、写 'ruri_outline' 面属性,Join 回本体链尾。零场景对象、
        #    零材质节点(GN SetMaterial/SetMaterialIndex 在 Cycles 渲染求值下输出空
        #    几何,实锤两次)—— 虚拟面保留原 material_index = 同槽同材质,材质树按
        #    ruri_outline 面属性路由 gate 调色与剔除方向(描边 pass 恒 Cull Front)。
        stale_ol = bpy.data.objects.get('RuriOL ' + obj.name)   # 老架构遗留,见即清
        if stale_ol is not None:
            bpy.data.objects.remove(stale_ol, do_unlink=True)
        if outline_slots:
            branch = gin.outputs[0]
            for slot, mat in outline_slots:
                floats, st, colors = _mat_meta(mat)
                oc_tree = _clone_vtx(OUTLINE_TEMPLATE, build_vtx_outline,
                                     CLONE_O_PREFIX + mat.name, mat)
                og = nd('GeometryNodeGroup')
                og.node_tree = oc_tree
                for key, value in cam_vec.items():
                    og.inputs[key].default_value = tuple(value)
                og.inputs['half_fov'].default_value = half_fov_val
                og.inputs['screen_x'].default_value = screen_px
                og.inputs['screen_y'].default_value = screen_py
                og.inputs['_OutlineWidth'].default_value = float(floats.get('_OutlineWidth', 0.0))
                og.inputs['_OutlineOffsetZ'].default_value = float(floats.get('_OutlineOffsetZ', 0.0))
                og.inputs['_EnableOutlineMask'].default_value = float(floats.get('_EnableOutlineMask', 0.0))
                og.inputs['_OutlineAverageNormal'].default_value = float(floats.get('_OutlineAverageNormal', 0.0))
                # mask 采样 UV = uv0×_BaseMap_ST+zw(真源 b1089:_453 与片元 varying 同源),
                # 不是 _OutlineMask 自己的 ST(那个恒 (1,1,0,0),历史键)。
                bst = st.get('_BaseMap', [1.0, 1.0, 0.0, 0.0])
                og.inputs['mask_st_scale'].default_value = (float(bst[0]), float(bst[1]), 0.0)
                og.inputs['mask_st_offset'].default_value = (float(bst[2]), float(bst[3]), 0.0)
                sp = nd('GeometryNodeSetPosition')
                mt.links.new(branch, sp.inputs['Geometry'])
                mt.links.new(slot_sel(slot), sp.inputs['Selection'])
                mt.links.new(og.outputs['offset'], sp.inputs['Offset'])
                branch = sp.outputs['Geometry']
            # 删无描边槽的面(选区先于属性写入,field 惰性求值安全)。
            keep = None
            for slot, _mat in outline_slots:
                sel = slot_sel(slot)
                if keep is None:
                    keep = sel
                else:
                    mx = nd('ShaderNodeMath')
                    mx.operation = 'MAXIMUM'
                    mt.links.new(keep, mx.inputs[0])
                    mt.links.new(sel, mx.inputs[1])
                    keep = mx.outputs[0]
            drop = nd('ShaderNodeMath')
            drop.operation = 'SUBTRACT'
            drop.inputs[0].default_value = 1.0
            mt.links.new(keep, drop.inputs[1])
            dg = nd('GeometryNodeDeleteGeometry')
            dg.domain = 'FACE'
            mt.links.new(branch, dg.inputs['Geometry'])
            mt.links.new(drop.outputs[0], dg.inputs['Selection'])
            sa = nd('GeometryNodeStoreNamedAttribute')
            sa.data_type = 'FLOAT'
            sa.domain = 'FACE'
            sa.inputs['Name'].default_value = 'ruri_outline'
            sa.inputs['Value'].default_value = 1.0
            mt.links.new(dg.outputs['Geometry'], sa.inputs['Geometry'])
            jn = nd('GeometryNodeJoinGeometry')
            mt.links.new(geo, jn.inputs[0])
            mt.links.new(sa.outputs['Geometry'], jn.inputs[0])
            geo = jn.outputs['Geometry']
        # ③ 骨骼基座增量(RIG_BASIS_BONE 的增量旋转三列 → 点属性,材质树 g.rig_basis 读):
        #    材质里的 _FaceForward/_FaceRight 是**绑定姿势**的静止值(引擎运行时才按头骨
        #    每帧覆写,导出的 .mat 带不走),而内核跑在对象空间 —— 骨骼一转,几何在对象空间里
        #    跟着转、这个常量不转,SDF 脸影就按静止朝向判明暗(转身 180° 整脸压黑、皮肤照亮)。
        #    增量 = Pose × RestPose⁻¹,两个矩阵都由 Bone Info 现出(**节点直接读骨骼**,
        #    不经空物体也不经 driver/handler);transform_space=RELATIVE 出的已是**本对象的
        #    对象空间**,连对象自己的世界矩阵都是活的(快照下来就会在用户挪动网格后过期)。
        #    再补一次 Y/Z 互换即内核的 Unity 语义;静止时 Pose == RestPose ⇒ 三列恒为单位阵,
        #    画面与本桥不存在时一致。骨骼名/属性名全来自配方。
        #    存放点在 Join 之后:描边虚拟面与本体同材质,漏掉它们那壳的脸影会各判各的。
        if basis_slots:
            arm = _rig_basis_armature(obj)
            if arm is None:
                print('[ruri-vertex] {0}: 没挂骨架,脸部基座留在绑定姿势值'.format(
                    obj.name), flush=True)
            else:
                bone = nd('GeometryNodeBoneInfo')
                bone.transform_space = 'RELATIVE'
                bone.inputs['Armature'].default_value = arm
                bone.inputs['Bone Name'].default_value = RIG_BASIS_BONE
                rest_inv = nd('FunctionNodeInvertMatrix')
                mt.links.new(bone.outputs['Rest Pose'], rest_inv.inputs['Matrix'])
                delta = nd('FunctionNodeMatrixMultiply')
                mt.links.new(bone.outputs['Pose'], delta.inputs[0])
                mt.links.new(rest_inv.outputs['Matrix'], delta.inputs[1])
                # 内核三根基轴按导入换轴取对象空间的 (x, z, y) —— 与 swap_yz 对称。
                for idx, axis in enumerate(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))):
                    td = nd('FunctionNodeTransformDirection')
                    td.inputs['Direction'].default_value = axis
                    mt.links.new(delta.outputs['Matrix'], td.inputs['Transform'])
                    # 基是**方向**不是长度:骨架/骨骼带缩放时矩阵会把它拉长,归一化掐死。
                    unit = nd('ShaderNodeVectorMath')
                    unit.operation = 'NORMALIZE'
                    mt.links.new(td.outputs['Direction'], unit.inputs[0])
                    sa = nd('GeometryNodeStoreNamedAttribute')
                    sa.data_type = 'FLOAT_VECTOR'
                    sa.domain = 'POINT'
                    sa.inputs['Name'].default_value = RIG_BASIS_ATTR + str(idx)
                    # 骨架里没这根骨头 = 一个点都不写 ⇒ 属性恒零 ⇒ 材质端回退单位阵。
                    mt.links.new(bone.outputs['Exists'], sa.inputs['Selection'])
                    mt.links.new(geo, sa.inputs['Geometry'])
                    mt.links.new(swap_yz(unit.outputs[0]), sa.inputs['Value'])
                    geo = sa.outputs['Geometry']
        mt.links.new(geo, gout.inputs[0])
        mod = obj.modifiers.get(VTX_MODIFIER)
        if mod is None:
            mod = obj.modifiers.new(VTX_MODIFIER, 'NODES')
        mod.node_group = mt
        if vert_slots:
            # 20 层透明壳叠深 > Cycles 默认 transparent_max_bounces=8,穿透壳堆的
            # 光线会提前截断发黑 —— 只升不降的保底(view_transform 接管的同款先例)。
            try:
                if scene.cycles.transparent_max_bounces < 32:
                    scene.cycles.transparent_max_bounces = 32
            except AttributeError:
                pass
        done += 1
        print('[ruri-vertex] {0}: shell x{1} outline x{2} basis x{3}'.format(
            obj.name, len(vert_slots), len(outline_slots), len(basis_slots)), flush=True)
    return done


# ============================ 材质 provider ============================
# 认领判据 = m_Shader 身份(shader 资产文本首行的 Shader "..." 自称名)。
# 🔴 属性指纹判变体已废除并禁止回退:Unity m_SavedProperties 累积材质历史上用过的
#   全部键,「键存在」证明不了任何事(实锤:cloth 全带 _SkinRimOffScale,整套布料
#   被吃成 Face 跑脸部 SDF,全身发暗)。解析不到 = 闭包丢依赖,响亮报错,禁止猜。
PART_META = {
    'Standard': {'id': 0, 'transparent': False, 'shader': 'HGRP/CharacterNPR', 'aliases': (), 'discriminator': None},
    'Face': {'id': 1, 'transparent': False, 'shader': 'HGRP/CharacterNPR_Skin', 'aliases': (), 'discriminator': None},
    'Eyes': {'id': 2, 'transparent': False, 'shader': 'HGRP/CharacterNPR_Eye', 'aliases': (), 'discriminator': None},
    'Hair': {'id': 3, 'transparent': False, 'shader': 'HGRP/CharacterNPR_Hair', 'aliases': (), 'discriminator': None},
    'Fur': {'id': 4, 'transparent': True, 'shader': 'HGRP/CharacterNPR', 'aliases': (), 'discriminator': '_UseCharacterFur'},
    'Eyebrow': {'id': 5, 'transparent': False, 'shader': None, 'aliases': (), 'discriminator': None},
    'VFX': {'id': 6, 'transparent': True, 'shader': 'HGRP/CharacterNPR_VFX', 'aliases': (), 'discriminator': None},
    'OverlayShadow': {'id': 7, 'transparent': True, 'shader': 'HGRP/CharacterNPR_OverlayShadow', 'aliases': (), 'discriminator': None},
    'LiquidAg': {'id': 8, 'transparent': False, 'shader': 'HGRP/CharacterNPR_LiquidAg', 'aliases': (), 'discriminator': None},
}
NON_SHADING_SHADERS = ('HGRP/CharacterNPR_ProxyLod', 'HGRP/CharacterNPR_ShadowReceiver', )

CULL_PROPERTY = '_Cull'
CULL_FIXED = 2
GLOBALS = {
    '_Time': {'role': 'time', 'type': 'VECTOR4', 'default': (0.0, 0.0, 0.0), 'default_w': 0.0},
    '_ZBufferParams': {'role': 'zbuffer', 'type': 'VECTOR4', 'default': (9999.0, 1.0, 9.999), 'default_w': 0.001},
}

def _cull_mode(props):
    """材质的 Unity Cull 值,按**本家族真源自己的属性名**读 —— `Cull [<名>]` 逐 shader
    家族不同(characternpr 系 _Cull,lit/unlit/effect 系 _CullMode),真源没有 `Cull` 行的
    家族则是固定态(CULL_FIXED)。
    名字对不上就取不到值,而静默落「只渲正面」会让站在球壳内部的背景壳整个消失
    (实测:白房间 alpha 恒 0,画面只剩世界背景),所以这里响亮报错并按双面渲。"""
    if not CULL_PROPERTY:
        return CULL_FIXED
    value = props.floats.get(CULL_PROPERTY)
    if value is None:
        print('[ruri-uber] !! {0} 未声明 {1},按双面渲染'.format(
            props.name, CULL_PROPERTY), flush=True)
        return 0.0
    return float(value)


def _shader_name(builder, props):
    """m_Shader 的自称名 —— 唯一解析面在宿主(builder.shader_display_name):
    闭包内 shader 资产的 Shader \"...\" 行。本模块自己不解析、不缓存 ——
    两个生成栈各解析一遍就是同一语义两处真源。"""
    return builder.shader_display_name(props)


def _variant(builder, props):
    """(part 名, part id);非本风格/非着色 shader 返回 None(宿主落兜底材质)。
    同 shader 多 part 时按 discriminator 开关分流(如 Fur 的 _UseCharacterFur);
    aliases = 与本 part 共用同一表面的其它 shader 自称名(游戏按 LOD/批次切出的
    同表面资产,如 Grass ↔ Grass Cardmesh Lod),认领等价、共用同一棵树。"""
    name = _shader_name(builder, props)
    if name is None:
        ref = props.shader_ref if isinstance(props.shader_ref, dict) else {}
        print('[ruri-uber] !! 0DAY: material {0} 的 m_Shader {1}/{2} 解析不出自称名 '
              '(闭包无此资产,引擎内置注册表也不认识)—— 拒绝按指纹猜,交宿主兜底'
              .format(props.name, ref.get('guid'), ref.get('fileID')), flush=True)
        return None
    if name in NON_SHADING_SHADERS:
        print('[ruri-uber] {0} 用 {1}(非着色 part),不认领'.format(props.name, name), flush=True)
        return None
    fallback = None
    for part, meta in PART_META.items():
        if meta['shader'] != name and name not in meta['aliases']:
            continue
        disc = meta['discriminator']
        if disc is None:
            fallback = (part, meta['id'])
        elif props.floats.get(disc):
            return (part, meta['id'])
    return fallback


def _slot_of(image_name):
    # 占位图名 → 逻辑槽名:剥数字后缀(同名数据块重载会产生 .001)。
    base, dot, tail = image_name.rpartition('.')
    return base if dot and tail.isdigit() else image_name


def _swap_image(node, real):
    """占位图换真图,色彩空间跟着**占位图**走(生成期按真源 .meta sRGBTexture 定死,
    是唯一真源)。双向赋值必须:只单向强制 Non-Color,老会话里被错标过的图永远弹不回 sRGB。"""
    non_color = node.image is not None and node.image.colorspace_settings.name == 'Non-Color'
    node.image = real
    try:
        real.colorspace_settings.name = 'Non-Color' if non_color else 'sRGB'
    except Exception:
        pass
    if non_color:
        _fix_two_channel_layout(real)


def _fix_two_channel_layout(real):
    """双通道压缩纹理(BC5 类)被导成 R恒白/G=B=X/A=Y 的容器,shader 读 .x 只会
    吃到填充白(ardelia _FurMap 实锤:毛噪声全灭成壳石头)——命中判据就地还原 R<-G。
    只对数据图(Non-Color)调用;材质树与顶点树换图共用;prop 记账防重复。"""
    if real.get('ruri_rg_layout_fixed'):
        return
    real['ruri_rg_layout_fixed'] = 1
    try:
        import numpy as _np
        w, h = real.size
        if w and h:
            buf = _np.empty(w * h * 4, dtype=_np.float32)
            real.pixels.foreach_get(buf)
            px = buf.reshape(-1, 4)
            # 统计判据(DXT 块伪影下 min/max 不可靠):R 恒白 + G 有数据 + G≈B
            if (float(px[:, 0].mean()) > 0.99 and float(px[:, 0].std()) < 0.02
                    and float(px[:, 1].std()) > 1e-3
                    and float(_np.abs(px[:, 1] - px[:, 2]).mean()) < 0.01):
                px[:, 0] = px[:, 1]
                real.pixels.foreach_set(buf)
                real.update()
                if real.packed_file is not None:
                    real.pack()   # 重打包当前缓冲,否则 save 后修正回退到原字节
                print('[ruri-uber] {0}: 双通道导出布局已恢复 R<-G'.format(real.name), flush=True)
    except Exception as exc:
        print('[ruri-uber] {0} 通道布局检测失败: {1}'.format(real.name, exc), flush=True)


def _standard_view_transform(scene):
    """材质组输出恒为场景线性 HDR:tonemap 是后处理链的一级,住合成器,
    由后处理级按需装配。材质侧不碰用户的 view transform。"""
    return False


def _load_images(builder, props):
    """.mat 绑的每张图,按属性名收。色彩空间由占位图承载(生成期定),这里只管取;
    alpha 重新按数据解释 —— 宿主 loader 为 Principled 关掉了它,而内核吃 .a。"""
    images = {}
    for name, guid in props.textures.items():
        img = builder._load_image(guid)
        if img is None:
            # .mat 明确引用却解不出 = 信息丢失,必须大声(静默落占位曾把整脸 SDF 吞成黑)。
            print('[ruri-uber] !! {0}: texture {1} guid={2} LOAD FAILED'.format(
                props.name, name, guid), flush=True)
            continue
        try:
            img.alpha_mode = 'CHANNEL_PACKED'
        except Exception:
            pass
        images[name] = img
    return images


def provider(builder, props):
    """宿主图 provider:按 m_Shader 身份认领,其余返回 None 交给宿主兜底。"""
    resolved = _variant(builder, props)
    if resolved is None:
        return None
    part_name, part_id = resolved
    meta = PART_META[part_name]
    name = props.name or 'Ruri_Endfield_Uber'
    images = _load_images(builder, props)
    if _standard_view_transform(bpy.context.scene):
        print('[ruri-uber] view transform -> Standard(图自带游戏 tonemap)', flush=True)
    # 不透明与否是**游戏自己的规则**:_SurfaceType==1 才吃 alpha,否则输出 alpha 恒 1
    #   (真源 characternpr_eye Fragment:1014;skin 更直接 _3324.w = 1.0 硬写)。
    #   那条 gBuffer0.w 在真管线是 materialFlags 不是不透明度 —— 接成不透明度会让
    #   皮肤/眼睛整个隐形。透明 part 由 [StylePart(Transparent)] 定,是风格自己的事实。
    opaque = props.floats.get('_SurfaceType', 0.0) < 0.5 and not meta['transparent']
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    insts = build_material(mat, part=part_name, opaque=opaque,
                           multiply_blend=meta['transparent'] and part_name == 'OverlayShadow',
                           cull=_cull_mode(props), images=images)
    filled = [0]
    bst = props.texture_st.get('_BaseMap') or [1.0, 1.0, 0.0, 0.0]
    for node in mat.node_tree.nodes:
        if node.label == 'RuriBaseMapST':
            node.inputs['Scale'].default_value = (float(bst[0]), float(bst[1]), 1.0)
            node.inputs['Location'].default_value = (float(bst[2]), float(bst[3]), 0.0)

    def put(sock_name, value):
        for grp in insts:
            sock = grp.inputs.get(sock_name)
            if sock is None or sock.is_linked:
                continue
            if sock.type == 'VECTOR':
                sock.default_value = value if isinstance(value, tuple) else (value, value, value)
            else:
                sock.default_value = value[0] if isinstance(value, tuple) else value
            filled[0] += 1

    # raw 直灌:socket 名 = 游戏属性名(生成期就是这么命名的),零映射表。
    for prop, value in props.floats.items():
        put(prop, float(value))
    for prop, value in props.colors.items():
        put(prop, (float(value[0]), float(value[1]), float(value[2])))
        put(prop + '_w', float(value[3]))
    for prop, st in props.texture_st.items():
        put(prop + '_ST', (float(st[0]), float(st[1]), float(st[2])))
        put(prop + '_ST_w', float(st[3]))
    put('_CharaPartID', float(part_id))
    # 透明特效 part 的游戏 shader 靠 pass 状态透明、不声明 _SurfaceType,补上内核开关。
    if meta['transparent']:
        put('_SurfaceType', 1.0)
    try:
        mat.surface_render_method = 'BLENDED' if meta['transparent'] else 'DITHERED'
    except Exception:
        pass
    mat['ruri_uber_part'] = part_name
    # 顶点腿参数真源:只有顶点消费的属性(_FurLengthIntensity/_OutlineWidth…)在着色组上
    # 没有 socket,不落这里就永远丢 —— apply_vertex_stage 按名回收。
    mat['ruri_uber_images'] = {k: v.name for k, v in images.items()}
    mat['ruri_uber_floats'] = {k: float(v) for k, v in props.floats.items()}
    mat['ruri_uber_st'] = {k: [float(x) for x in v] for k, v in props.texture_st.items()}
    mat['ruri_uber_colors'] = {k: [float(x) for x in v] for k, v in props.colors.items()}
    mat['ruri_uber_disabled_passes'] = list(getattr(props, 'disabled_passes', ()))
    ref = props.shader_ref
    mat['ruri_uber_shader_guid'] = str(ref.get('guid', '')) if isinstance(ref, dict) else ''
    mat['ruri_uber_shader'] = _shader_name(builder, props) or ''
    print('[ruri-uber] {0}: shader={1} part={2} images={3} sockets={4} insts={5}'.format(
        name, mat['ruri_uber_shader'], part_name, len(images), filled[0], len(insts)), flush=True)
    panel_sync(mat)
    return mat


def rewire_capabilities(mat):
    """重接本材质的**环境询问兑现面** —— 场景灯换了、覆盖灯改了、世界换了之后叫一次。

    只回收兑现面自己建的节点(建图时打了 ruri_cap 标记),图本体与材质参数一概不碰:
    所以不必重建整张图,也就不会把用户调过的旋钮清回缺省。
    不是本栈建的材质返回 False —— 宿主逐个问,谁认领谁重接。"""
    part = mat.get('ruri_uber_part')
    rows = CAPABILITIES.get(part) if part else None
    if not rows or mat.node_tree is None:
        return False
    nt = mat.node_tree
    for stale in [n for n in nt.nodes if n.get('ruri_cap') is not None]:
        nt.nodes.remove(stale)
    ordered = sorted((n for n in nt.nodes if n.get('ruri_inst') is not None),
                     key=lambda n: int(n['ruri_inst']))
    if not ordered:
        # 没有实例序标记 = 这张材质是旧产物,按 depth 取实例必错位。响亮说,不硬接。
        raise RuntimeError('[ruri-cap] 材质 {0} 无实例序标记(旧产物),请重新导入'.format(mat.name))
    g = G(nt, is_group=False)
    ctx = {'material': mat}
    for row in rows:
        _wire_capability(g, ordered, row, ctx)
    # 体内割点(灯就住在这儿):按 (zone, 序) 找回体实例链再接。
    # 漏掉这段 = 上面刚把带 ruri_cap 标记的节点全删了、却只重建顶层那批,
    # 循环里的兑现面被抹掉且永不回来(实测:第一次依赖图更新后灯全灭)。
    for zone in ZONES.get(part, ()):
        if not zone['capabilities']:
            continue
        binsts = sorted((n for n in nt.nodes if n.get('ruri_zone') == zone['sock']),
                        key=lambda n: int(n['ruri_binst']))
        if not binsts:
            raise RuntimeError('[ruri-cap] 材质 {0} 的循环 {1} 无体实例标记(旧产物),请重新导入'.format(
                mat.name, zone['sock']))
        for row in zone['capabilities']:
            _wire_capability(g, binsts, row, ctx)
    # 从 Python 改完节点树必须自己打脏标记:建材质那条路是 bpy 算子驱动的、
    # 依赖图顺手就重编了;这里是纯数据写,不打标记 EEVEE 会继续用旧的编译结果 ——
    # 症状是「图明明换了支、像素逐位不动」(实测换灯后三次渲染完全相同)。
    nt.update_tag()
    mat.update_tag()
    return True


def refresh_light_tables():
    """灯只是动了 / 换了色 / 调了能量 —— **重写灯表像素即可,一个节点都不用碰**。
    灯集合本身没变时走这条:代价是几十个浮点写,与重建图不在一个量级。
    (集合变了才需要重接:0 盏↔有盏会换整条兑现分支。)"""
    touched = 0
    for mat in bpy.data.materials:
        if mat.get('ruri_uber_part') is None:
            continue
        lights = _additional_lights(mat)
        if not lights:
            continue
        _light_table(mat, lights)
        touched += 1
    return touched


# ============================ 材质参数面板 ============================
# 参数面 = 从 C# 声明([ShaderProperty]/[MaterialTexture]/[ShaderPropertyHeader])反射派生的
# **接口**,不是对节点树的遍历——平坦、分组、带量程、带功能门。此表是它的逐字投影。
PANEL_KEY = 'ruri_character_uber_endfield'
PANEL_TITLE = 'Ruri_Endfield_Uber 参数'
_PANEL_PROP = 'ruri_panel_' + PANEL_KEY
INTERFACE = [
    {'name': '基础', 'gate': None, 'rows': [
        {'name': '_BaseMap', 'label': 'Albedo', 'kind': 'TEXTURE', 'st_node': 'RuriBaseMapST'},
        {'name': '_BumpMap', 'label': 'Normal Map', 'kind': 'TEXTURE'},
        {'name': '_RampMap', 'label': 'Diffuse Ramp Map', 'kind': 'TEXTURE'},
        {'name': '_EmissionMap', 'label': 'Emission', 'kind': 'TEXTURE'},
        {'name': '_OutlineMask', 'label': 'Outline Mask', 'kind': 'TEXTURE'},
        {'name': '_HairBrowMask', 'label': 'Hair Brow Mask', 'kind': 'TEXTURE'},
        {'name': '_RMOSMap', 'label': 'RMOS Map (R=Rough G=Metal B=Occ A=Spec)', 'kind': 'TEXTURE'},
        {'name': '_IlmMap', 'label': 'ILM / Face SDF Map', 'kind': 'TEXTURE'},
        {'name': '_RefractTex', 'label': '自定义折射贴图', 'kind': 'TEXTURE'},
        {'name': '_WaterNormalMap', 'label': '水面法线贴图', 'kind': 'TEXTURE'},
        {'name': '_WaterCausticMap', 'label': '水波纹贴图', 'kind': 'TEXTURE'},
        {'name': '_DisplacementTex', 'label': '置换贴图', 'kind': 'TEXTURE'},
        {'name': '_IceNormalMap', 'label': '冰块法线贴图', 'kind': 'TEXTURE'},
        {'name': '_IceOpacityMap', 'label': '冰块不透明度贴图', 'kind': 'TEXTURE'},
        {'name': '_ILMMap', 'label': 'ILM Map', 'kind': 'TEXTURE'},
        {'name': '_MatcapMap', 'label': 'Matcap', 'kind': 'TEXTURE'},
        {'name': '_MetalMatcap', 'label': 'Metal Matcap', 'kind': 'TEXTURE'},
        {'name': '_AddMatcapMap', 'label': 'Add Matcap', 'kind': 'TEXTURE'},
        {'name': '_BurstTex', 'label': '爆发效果贴图 Burst Texture', 'kind': 'TEXTURE'},
        {'name': '_ColorRamp', 'label': 'Color Ramp', 'kind': 'TEXTURE'},
        {'name': '_DenierMap', 'label': 'Denier Map', 'kind': 'TEXTURE'},
        {'name': '_DyeingTex', 'label': 'Dyeing Map', 'kind': 'TEXTURE'},
        {'name': '_HighLightTex1', 'label': 'HightLightTex 1', 'kind': 'TEXTURE'},
        {'name': '_HighLightTex2', 'label': 'HightLightTex 2', 'kind': 'TEXTURE'},
        {'name': '_HighLightTex3', 'label': 'HightLightTex 3', 'kind': 'TEXTURE'},
        {'name': '_BaseTex', 'label': 'Base Tex', 'kind': 'TEXTURE'},
        {'name': '_DissolveTex', 'label': 'Dissolve Tex', 'kind': 'TEXTURE'},
        {'name': '_BaseColor', 'label': 'Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_BumpScale', 'label': 'Normal Scale', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_ShadowSoft', 'label': 'Shadow Soft', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_RoughnessIntensity', 'label': 'Roughness Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_MetallicIntensity', 'label': 'Metallic Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_OcclusionIntensity', 'label': 'Occlusion Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_SpecularIntensity', 'label': 'Specular Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_EmissiveIntensity', 'label': 'Emissive Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 20.0, 'default': [1.0]},
        {'name': '_UseCutoff', 'label': 'Use Alpha Cutoff', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_Cutoff', 'label': 'Alpha Cutoff', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_UseDitherClip', 'label': 'Use Dither Clip', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DitherAlpha', 'label': 'Dither Alpha Value', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_SurfaceType', 'label': 'Surface Type', 'kind': 'INT', 'size': 2, 'default': [0.0]},
        {'name': '_HairBrowMaskThreshold', 'label': 'Hair Brow Mask Threshold', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_UseRMOSMap', 'label': 'Use RMOS Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseRampMap', 'label': 'Use Ramp Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseBumpMap', 'label': 'Use Normal Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseEmission', 'label': 'Use Emission', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_UseMaskUV2', 'label': 'Use Mask UV2', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_GameRenderStyle', 'label': 'Render Style', 'kind': 'VALUE', 'size': 2, 'default': [0.0]},
        {'name': '_CharaPartID', 'label': 'Character Part', 'kind': 'INT', 'size': 2, 'default': [0.0]},
        {'name': '_UseHairShadow', 'label': 'Use Hair Shadow', 'kind': 'INT', 'size': 1, 'default': [0.0]},
        {'name': '_EyeShadowIntensity', 'label': 'Eye Shadow Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_UseAnisotropicSpecular', 'label': 'Use Anisotropic Specular', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_AnisotropicGGX', 'label': 'Anisotropic GGX', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_Anisotropy', 'label': 'Anisotropy', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_AnisotropyShift', 'label': 'Anisotropy Shift', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.05]},
        {'name': '_UseStocking', 'label': 'Use Stocking Falloff', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_StockingCenterColor', 'label': 'Stocking Center Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_StockingFalloffColor', 'label': 'Stocking Falloff Color', 'kind': 'COLOR', 'size': 4, 'default': [0.1, 0.0, 0.0, 1.0]},
        {'name': '_StockingFalloffPower', 'label': 'Stocking Falloff Power', 'kind': 'SLIDER', 'size': 1, 'min': 0.1, 'max': 5.0, 'default': [1.0]},
        {'name': '_CubemapIntensity', 'label': 'Cubemap Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_EmissionColor', 'label': 'Emission Color', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_OutlineWidth', 'label': 'Outline Width', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [0.5]},
        {'name': '_OutlineOffsetZ', 'label': 'Outline Offset Z', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_OutlineAverageNormal', 'label': 'Use Smooth Normal (UV2)', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_OutlineTintEnable', 'label': 'Outline Tint Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_OutlineTintColor', 'label': 'Outline Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EnableOutlineMask', 'label': 'Outline Mask Enable', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_UseVertexColorOutline', 'label': 'Use VertexColor Outline', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_BackFaceNormalFlip', 'label': 'Back Face Normal Flip', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_AlphaPremultiply', 'label': 'Alpha Premultiply', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_FBXRotationFix', 'label': 'FBX -90 Z Rotation Fix (OTW col0/col1 swap)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_NormalScale', 'label': 'Normal Scale', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_FresnelColor', 'label': 'Fresnel Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EffectPartID', 'label': 'Effect Part ID', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_AlphaClipThreshold', 'label': 'Alpha Clip Threshold', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_UseAlphaTest', 'label': 'Use Alpha Test', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_IgnorePostExposure', 'label': 'Ignore Post Exposure', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_CullMode', 'label': 'Cull Mode', 'kind': 'VALUE', 'size': 1, 'default': [2.0]},
        {'name': '_ExposureWithMiscParams', 'label': 'Exposure (y = post exposure)', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_VFXParams1', 'label': 'VFX Grade (rgb = tint, w = saturation)', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_Use_VerexTexColorAsOpacity', 'label': '用顶点色控制Opacity', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_Specular', 'label': 'Specular (Default 0.5)', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_Roughness', 'label': '粗糙度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MatCapIgnorePostExposure', 'label': 'Matcap 不受自动曝光影响', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_RefractionIOR', 'label': '散射IOR', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_RefractionColor', 'label': '折射颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RefractionFresnelColor', 'label': '折射菲涅尔颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RefractionStrength', 'label': '折射强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_UseFresnel', 'label': 'Use Fresnel', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FresnelBias', 'label': '菲涅尔偏移', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [0.0]},
        {'name': '_FresnelAffectOpacity', 'label': '菲涅尔影响透明度系数', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_FresnelPower', 'label': 'Fresnel Power', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 100.0, 'default': [1.0]},
        {'name': '_Use_VerexGAsFresnelOpacity', 'label': '使用顶点色G通道控制菲涅尔强度', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FresnelUseMeshNormal', 'label': '菲涅尔效果使用模型法线', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FresnelFlip', 'label': '翻转菲涅尔区域', 'kind': 'SWITCH', 'size': 1, 'default': [0.001]},
        {'name': '_EnableGlassRefraction', 'label': '玻璃折射', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseCustomRefractTex', 'label': '折射类型 (0=折射率 1=自定义贴图)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_RefractTint', 'label': '折射染色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RefractionContribution', 'label': '折射贡献', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.8]},
        {'name': '_RefractThickness', 'label': '厚度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.01]},
        {'name': '_IsShell', 'label': '玻璃类型 (0=实心 1=壳)', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_IoR', 'label': '折射率 IoR', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.8]},
        {'name': '_RefractTexIntensity', 'label': '折射贴图强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.01]},
        {'name': '_RefractBrightness', 'label': '折射亮度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_EnableGlassRim', 'label': '玻璃边缘高光', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_GlassRimColor', 'label': '玻璃边缘颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_GlassRimPower', 'label': '玻璃边缘幂次', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 50.0, 'default': [1.0]},
        {'name': '_GlassRimStrength', 'label': '玻璃边缘强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_GlassRimRoughnessScale', 'label': '玻璃边缘粗糙度缩放', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_GlassRimRefractionPower', 'label': '玻璃边缘折射幂次', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_GlassRimRefractionStrength', 'label': '玻璃边缘折射亮度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_GlassRimUseMask', 'label': '使用边缘遮罩', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_GlassRimMaskChannel', 'label': '边缘遮罩通道', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_UseVertexColorAsRimMask', 'label': '使用顶点色作为边缘遮罩', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_GlassMaskOpacity', 'label': '玻璃遮罩不透明度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.995]},
        {'name': '_EnableIce', 'label': '冰块效果', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_IceRefractionColor', 'label': '冰块折射颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_IceRefractionBrightness', 'label': '冰块折射亮度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_IceRefractionStrength', 'label': '冰块折射强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_IceRefractionMipBias', 'label': '冰块折射采样Mip偏移', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_IceOpacityMapTilling', 'label': '冰块不透明度贴图平铺', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 20.0, 'default': [1.0]},
        {'name': '_IceOpacityThreshold', 'label': '冰块不透明度阈值', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableContainerWater', 'label': '容器液体效果', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_WaterShallowColor', 'label': '浅水颜色', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_WaterDeepColor', 'label': '深水颜色', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_WaterRefractionColor', 'label': '水折射颜色', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_WaterScatteringColor', 'label': '散射颜色', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_WaterAbsorptionColor', 'label': '吸收颜色', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_WaterSurfaceNormalScale', 'label': '水面法线强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_WaterNormalSpeed', 'label': '水面波动速度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.01]},
        {'name': '_WaterFresnelPower', 'label': '水面菲涅尔强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_WaterReflectionStrength', 'label': '水面反射强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_WaterRefractionStrength', 'label': '水面折射强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_WaterRefractionBrightness', 'label': '水面折射亮度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterCupRadius', 'label': '杯体半径', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterMeniscusWidth', 'label': 'Meniscus宽度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterBaseOpacity', 'label': '基础不透明度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterOpacityDepthFactor', 'label': '深度不透明度系数', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterOpacityFresnelFactor', 'label': '菲涅尔不透明度系数', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterOpacityMinimum', 'label': '最小不透明度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterOpacityMaximum', 'label': '最大不透明度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterEdgeOpacity', 'label': '边缘不透明度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterTurbidity', 'label': '浑浊度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterCausticStrength', 'label': '水波纹强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterCausticSpeed', 'label': '水波纹速度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_IceballRadius', 'label': '冰球半径', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_IceballWaterlineWidth', 'label': '冰球水线宽度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_IcePosition', 'label': '冰块位置', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_DisplacementNormalStrength', 'label': '置换法线强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_NormalMapBlendWeight', 'label': '法线贴图混合权重', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_WaterStrokeDistance', 'label': '描边距离', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0395]},
        {'name': '_WaterStrokeWidth', 'label': '描边宽度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0001, 'max': 0.1, 'default': [0.0352]},
        {'name': '_WaterStrokeColor', 'label': '描边颜色', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_WaterStrokeOpacity', 'label': '描边不透明度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_WaterStrokeSoftness', 'label': '描边边缘柔和度', 'kind': 'SLIDER', 'size': 1, 'min': 0.001, 'max': 0.1, 'default': [0.0025]},
        {'name': '_ParallaxIgnorePostExposure', 'label': 'Parallax Ignore Post Exposure', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_ShadowReceiverPartID', 'label': 'Shadow Receiver Part ID', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_VertexStretchNoiseOffset', 'label': 'Vertex Stretch Noise Offset', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_VertexStretchMinMax', 'label': 'Vertex Stretch MinMax', 'kind': 'VECTOR', 'size': 4, 'default': [0.8, 1.0, 0.0, 0.0]},
        {'name': '_VertexStretchNoiseScale', 'label': 'Vertex Stretch Noise Scale', 'kind': 'VALUE', 'size': 1, 'default': [3.0]},
        {'name': '_VertexStretchDirection', 'label': 'Vertex Stretch Direction', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 1.0, 0.0, 0.0]},
        {'name': '_VertexStretchIntensity', 'label': 'Vertex Stretch Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.4, 'default': [0.0]},
        {'name': '_CatchZOffset', 'label': 'ZOffset', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_SwitchToMultiply', 'label': '切换正片叠底叠加模式', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_MainLightIntensity', 'label': '灯光强度', 'kind': 'VALUE', 'size': 1, 'default': [0.2]},
        {'name': '_UseTimelineEffect', 'label': '使用时间线效果 Use Timeline Effect', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_TimelineSaturation', 'label': '时间线饱和度 Timeline Saturation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_TimelineContrast', 'label': '时间线对比度 Timeline Contrast', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_TimelineBrightness', 'label': '时间线亮度 Timeline Brightness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_TimelineColorTint', 'label': '时间线颜色色调 Timeline Color Tint', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_UseCharacterWeatherAdjust', 'label': '使用角色天气调整 Use Weather Adjust', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_Weather_lightColorCh', 'label': '天气光照颜色 Weather Light Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_Weather_DarkSideValueCh', 'label': '天气暗面值 Weather Dark Side Value', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_UseScriptColorAdjust', 'label': '使用脚本颜色调整 Use Script Color Adjust', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DayNightValue', 'label': '夜晚渐变', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MainLightColorLimit', 'label': 'Main Light Color Limit', 'kind': 'VALUE', 'size': 1, 'default': [1.29688]},
        {'name': '_DebugSaturation', 'label': '调试饱和度 Debug Saturation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_DebugContrast', 'label': '调试对比度 Debug Contrast', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_DebugBrightness', 'label': '调试亮度 Debug Brightness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_AdditionalSpecularIntensity', 'label': 'Additional Light Specular Intensity', 'kind': 'VALUE', 'size': 1, 'default': [0.29688]},
        {'name': '_BackRimLightIntensityClamp', 'label': 'Back Rim Light Intensity Clamp', 'kind': 'VALUE', 'size': 1, 'default': [4.0]},
        {'name': '_WetIntensity', 'label': 'Wet Intensity', 'kind': 'VALUE', 'size': 1, 'default': [0.39844]},
        {'name': '_WetSpecularIntensity', 'label': 'Wet Specular Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.1875]},
        {'name': '_PhotoMode', 'label': '照片模式 Photo Mode', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_CharLightIntensity', 'label': '角色光照强度 Character Light Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_UseFakeLightDir', 'label': '使用假光照方向 Use Fake Light Direction', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FakeLightDir', 'label': '假光照方向 Fake Light Direction', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 1.0, 0.0, 0.0]},
        {'name': '_UseFakeLightColor', 'label': '使用假光照颜色 Use Fake Light Color', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FakeLightColor', 'label': '假光照颜色 Fake Light Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_DarkFaceSmoothness', 'label': '暗面平滑度 Dark Face Smoothness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.1]},
        {'name': '_DarkFaceThreshold', 'label': '暗面阈值 Dark Face Threshold', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_DarkFaceColor', 'label': '暗面颜色 Dark Face Color', 'kind': 'COLOR', 'size': 4, 'default': [0.5, 0.5, 0.5, 1.0]},
        {'name': '_BrightFaceColor', 'label': '亮面颜色 Bright Face Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ScriptBackRimIntensity', 'label': '脚本背面边缘光强度 Script Back Rim Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_ScriptBackFresnelMin', 'label': '脚本背面菲涅尔最小值 Script Back Fresnel Min', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [0.5]},
        {'name': '_ScriptBackFresnelMax', 'label': '脚本背面菲涅尔最大值 Script Back Fresnel Max', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_BurstFrePow', 'label': '爆发菲涅尔幂 Burst Fresnel Power', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_BurstFreColor', 'label': '爆发菲涅尔颜色 Burst Fresnel Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_BurstPoint', 'label': '爆发点 Burst Point', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_BurstRadius', 'label': '爆发半径 Burst Radius', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_BurstHardness', 'label': '爆发硬度 Burst Hardness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_ForwardWS', 'label': '脸正面朝向（需要脚本控制）', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, -1.0, 0.0]},
        {'name': '_LeftWS', 'label': '脸左侧朝向（需要脚本控制）', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_AnisotropyBias', 'label': 'Anisotropy Bias', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.5, 'default': [0.0]},
        {'name': '_AnisotropyHueColor', 'label': 'Anisotropy Hue', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_SpecularExponent', 'label': '高光宽窄', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 500.0, 'default': [50.0]},
        {'name': '_SpecularColor', 'label': '高光颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_SiwaFresnelMax', 'label': '丝袜菲涅尔最大值', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_SiwaFresnelMin', 'label': '丝袜菲涅尔最小值', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_SiwaColor', 'label': '丝袜颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_SiwaBlackWhiteExchange', 'label': '黑白丝袜质感转换', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_AnisotropySmoothness', 'label': 'Anisotropy Smoothness', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 0.5, 'default': [0.2]},
        {'name': '_DiffuseColorInfluence', 'label': '固有色影响自发光颜色', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [0.0]},
        {'name': '_EmissiveGetRampEffect', 'label': '自发光区域受Ramp影响', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_EmissiveRampMode', 'label': '  自发光区域Ramp跟随区域', 'kind': 'VALUE', 'size': 1, 'default': [3.0]},
        {'name': '_RimColor', 'label': '边缘光颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RimIntensity', 'label': '边缘光强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [0.0]},
        {'name': '_RimMainLightRatio', 'label': '主光源颜色影响强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_FresnelMin', 'label': '边缘光最小值', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [0.45]},
        {'name': '_FresnelMax', 'label': '边缘光最大值', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [0.55]},
        {'name': '_BackRimIntensity', 'label': '边缘光（背光）强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_BackFresnelMin', 'label': '背面菲涅尔最小值 Back Fresnel Min', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [0.5]},
        {'name': '_BackFresnelMax', 'label': '背面菲涅尔最大值 Back Fresnel Max', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_AddMatCapOn', 'label': '启用附加Matcap', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_MetalMapOff', 'label': '关闭ILM金属度(仅附加Matcap部分)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_AddMatcapBrightness', 'label': '附加Matcap亮度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 50.0, 'default': [1.0]},
        {'name': '_SkinColor', 'label': '皮肤透色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_AOIntensity', 'label': '环境光遮蔽强度 AO Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_AOColorSaturation', 'label': 'AO颜色饱和度 AO Color Saturation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_DebugDiffseLayer', 'label': 'Debug 漫反射层', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_DebugSpecularLayer', 'label': 'Debug 高光层', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_ReverseRoughness', 'label': '使用光滑度（替换ILM粗糙度）', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_RimLightColor', 'label': '边缘光颜色 Rim Light Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RimLightStrength', 'label': '边缘光强度 Rim Light Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_FresnelPow', 'label': '菲涅尔幂 Fresnel Power', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_ColorMinR', 'label': '头发过渡颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ColorMaxR', 'label': '头发颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_PartColor', 'label': '挑染颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RampColor', 'label': 'Ramp颜色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RimLightColorRatio', 'label': '固有色颜色影响强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_RimMaskVal', 'label': '边缘光遮罩值 Rim Mask Value', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_PaintHighlightDayColor', 'label': '高光颜色(G通道)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_HorizontalAmount2', 'label': 'Horizontal Amount', 'kind': 'VALUE', 'size': 1, 'default': [4.0]},
        {'name': '_VerticalAmount2', 'label': 'Vertical Amount', 'kind': 'VALUE', 'size': 1, 'default': [2.0]},
        {'name': '_ColorMin', 'label': 'Color Min', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ColorMax', 'label': 'Color Max', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_Min', 'label': 'Min', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_Max', 'label': 'Max', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_ColorG', 'label': 'Color(G)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_GLerpIntensity', 'label': 'Color(G) Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_ColorB', 'label': 'Color(B)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_BLerpIntensity', 'label': 'Color(B) Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_ColorA', 'label': 'Color(A)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ALerpIntensity', 'label': 'Color(A) Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_RotateAngle1', 'label': 'Anim Rotate Angle 1', 'kind': 'SLIDER', 'size': 1, 'min': -180.0, 'max': 180.0, 'default': [0.0]},
        {'name': '_RotateAngle2', 'label': 'Anim Rotate Angle 2', 'kind': 'SLIDER', 'size': 1, 'min': -180.0, 'max': 180.0, 'default': [0.0]},
        {'name': '_HighLightColor1', 'label': 'Hight Light Color 1', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_HighLightColor2', 'label': 'Hight Light Color 2', 'kind': 'COLOR', 'size': 4, 'default': [0.4392, 0.8352, 1.0, 1.0]},
        {'name': '_ScaleX1', 'label': 'Scale X 1', 'kind': 'SLIDER', 'size': 1, 'min': 0.2, 'max': 2.0, 'default': [1.0]},
        {'name': '_ScaleY1', 'label': 'Scale Y 1', 'kind': 'SLIDER', 'size': 1, 'min': 0.2, 'max': 2.0, 'default': [1.0]},
        {'name': '_ScaleX2', 'label': 'Scale X 2', 'kind': 'SLIDER', 'size': 1, 'min': 0.2, 'max': 2.0, 'default': [1.0]},
        {'name': '_ScaleY2', 'label': 'Scale Y 2', 'kind': 'SLIDER', 'size': 1, 'min': 0.2, 'max': 2.0, 'default': [1.0]},
        {'name': '_HighlightIntensity1', 'label': 'Intensity 1', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [2.0]},
        {'name': '_HighlightIntensity2', 'label': 'Intensity 2', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [0.06]},
        {'name': '_HighlightIntensity3', 'label': 'Intensity 3', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_Range', 'label': 'Range', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_SpecularFlipHorizontal', 'label': 'Flip Horizontal', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_DarkColor', 'label': 'Dark Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_DayColor', 'label': 'Day Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_DyeingColorR', 'label': '眼影 (R)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_DyeingColorG', 'label': '腮红 (G)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_DyeingColorB', 'label': '唇彩 (B)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_DyeingColorA', 'label': '睫毛反光 (A)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_MaskColor02', 'label': '下睫毛 (0.2)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_MaskColor03', 'label': '眼白牙齿 (0.4)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_MaskColor04', 'label': '舌头 (0.3)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_MaskColor05', 'label': '口腔牙床 (0.5)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_MaskColor06', 'label': '睫毛描边 (0.6)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_MaskColor07', 'label': 'NPC眼睛 (0.7)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_MaskColor08', 'label': 'NPC眉毛 (0.8)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_AMin', 'label': 'Min', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_AMax', 'label': 'Max', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_DyeingColorAMin', 'label': '上睫毛 Min', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_DyeingColorAMax', 'label': '上睫毛 Max', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_HighLightMoveDistance2', 'label': 'High Light Move Distance', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.02, 'default': [0.02]},
        {'name': '_UseTestLightDir', 'label': 'Use Test Light Direction ?', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_TestAngle', 'label': 'Test Light Angle', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 360.0, 'default': [0.0]},
        {'name': '_ExpThreshold', 'label': 'Exp Threshold', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_ExpIntensity', 'label': 'Exp Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 100.0, 'default': [0.0]},
        {'name': '_MainUVSet', 'label': 'Main UV Set', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_MainSwitchUV', 'label': 'Main UV Switcher', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_MainTexUVRotate', 'label': 'MainTex UV Rotate', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_UseGridLine', 'label': 'Use Grid Line', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_GridLineWidth', 'label': 'Grid Line Width', 'kind': 'SLIDER', 'size': 1, 'min': 0.1, 'max': 15.0, 'default': [1.0]},
        {'name': '_UseDissolve', 'label': 'Use Dissolve', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DissolveAmount', 'label': 'Dissolve Amount', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_DissolveEdgeWidth', 'label': 'Dissolve Edge Width', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.1]},
        {'name': '_DissolveEdgeColor', 'label': 'Dissolve Edge Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ShadowColor', 'label': 'Shadow Color', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_CircleFade', 'label': 'Circle Fade', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_CircleFadeDistance', 'label': 'Circle Fade Distance', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_CircleFadeSmoothness', 'label': 'Circle Fade Smoothness', 'kind': 'VALUE', 'size': 1, 'default': [0.2]},
        {'name': '_DisableSceneShadow', 'label': 'Disable Scene Shadow', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DisableCharacterSelfShadow', 'label': 'Disable Character Self Shadow', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_CapsuleAoColor', 'label': 'Capsule AO Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
    ]},
    {'name': '引擎全局 CP', 'gate': None, 'rows': [
        {'name': '_CharacterParams0', 'label': 'CP0 (.y=主光系数 .z=环境阴影系数 .w=环境光系数)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.9, 0.8, 0.8]},
        {'name': '_CharacterParams1', 'label': 'CP1 (.x=brightMix .y=shadowStr .z=忽略主光阴影 .w=方向覆写量)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 1.0, 0.0]},
        {'name': '_CharacterParams2', 'label': 'CP2 (阴影色倾向 rgb，皮肤以外)', 'kind': 'VECTOR', 'size': 4, 'default': [0.7830188, 0.8293082, 1.0, 0.0]},
        {'name': '_CharacterParams3', 'label': 'CP3 (阴影色倾向 rgb，皮肤)', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.78114647, 0.68490565, 0.0]},
        {'name': '_CharacterParams4', 'label': 'CP4 (主光自定义颜色 rgb，皮肤)', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_CharacterParams5', 'label': 'CP5 (主光自定义颜色 rgb，皮肤以外)', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_CharacterParams6', 'label': 'CP6 (环境光方向 = charGlobalAmbientParam0)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 1.0, 4.371139E-08, 0.0]},
        {'name': '_CharacterParams7', 'label': 'CP7 (环境光系数 = charGlobalAmbientParam1)', 'kind': 'VECTOR', 'size': 4, 'default': [0.15, 1.5, 0.5, 0.0]},
        {'name': '_CharacterParams8', 'label': 'CP8 (skin spec color rgb + .w=intensity)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_CharacterParams9', 'label': 'CP9 (skin spec .xy=dir .z=tint .w=width)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 1.0, 0.0, 0.4]},
        {'name': '_CharacterParams10', 'label': 'CP10 (height darken control)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_RuriCharacterEnvironmentEffect', 'label': '环境效果量 (.x=雨 .y=水位量 .z=浸润 .w=雪)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_RuriCharacterEnvironmentWater', 'label': '环境效果水面 (.x=世界高度)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_CharacterParams11', 'label': 'CP11 (方向覆写 xyz + .w=明暗交界线偏移)', 'kind': 'VECTOR', 'size': 4, 'default': [-0.433, 0.5, 0.75, -0.4]},
        {'name': '_CharacterParams12', 'label': 'CP12 (.x=灯光手动控制 .y=主光色覆写量 .z=shadowGate .w=exposureBlend)', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_CharacterParams13', 'label': 'CP13 (.w=GGX specular toggle)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_CharacterParams14', 'label': 'CP14 (secondary spec color rgb + .w=intensity)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_CharacterParams15', 'label': 'CP15 (.z=SDF secondary threshold)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_EnvironmentGlobalParams0', 'label': 'EnvGlobalParams0', 'kind': 'VECTOR', 'size': 4, 'default': [1.67, 1.5, 1.0, 0.0]},
        {'name': '_ExposureParams', 'label': 'ExposureParams', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
    ]},
    {'name': '朝向与压暗', 'gate': None, 'rows': [
        {'name': '_FaceForward', 'label': 'Face Forward (World)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 1.0, 0.0]},
        {'name': '_FaceRight', 'label': 'Face Right (World)', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_HairDarkenParams', 'label': 'Hair Darken (x=offsetX y=darken z=offsetZ w=minDarken)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
    ]},
    {'name': 'PBR 基础', 'gate': None, 'rows': [
        {'name': '_MetallicGlossMap', 'label': 'RGBA:Metal,Spec,Shadow,Smooth', 'kind': 'TEXTURE'},
        {'name': '_UseMetallicGlossMap', 'label': 'Use MetallicGlossMap', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_Metallic', 'label': 'Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_Smoothness', 'label': 'Smoothness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
    ]},
    {'name': '自发光', 'gate': None, 'rows': [
        {'name': '_EmissionBrightness', 'label': 'Emission Brightness', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
    ]},
    {'name': 'Ramp', 'gate': None, 'rows': [
        {'name': '_DiffRampMap', 'label': 'Diffuse Ramp', 'kind': 'TEXTURE'},
        {'name': '_SpecRampMap', 'label': 'Specular Ramp', 'kind': 'TEXTURE'},
        {'name': '_UseDiffRampMap', 'label': 'Diffuse Ramp', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseSpecRampMap', 'label': 'Specular Ramp', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SpecRampIridescentMode', 'label': '彩虹色模式(镭射塑料请勾选)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
    ]},
    {'name': '阴影色', 'gate': None, 'rows': [
        {'name': '_ShadowLutTex', 'label': 'Shadow Color Lut', 'kind': 'TEXTURE'},
        {'name': '_UseShadowLutTex', 'label': 'Use Shadow Color LUT Tex', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ShadowColorBrightness', 'label': 'Shadow Color Brightness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_ShadowColorSaturation', 'label': 'Shadow Color Saturation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
    ]},
    {'name': '脸部 SDF/表情', 'gate': None, 'rows': [
        {'name': '_SDFMask', 'label': 'RimMask/SDFMask/FlatSHMask', 'kind': 'TEXTURE'},
        {'name': '_SDFLightmap', 'label': 'SDF Lightmap', 'kind': 'TEXTURE'},
        {'name': '_EmotionMap', 'label': 'Emotion Map', 'kind': 'TEXTURE'},
        {'name': '_HighlightMap', 'label': 'HighlightMap', 'kind': 'TEXTURE'},
        {'name': '_UseSDFLightmap', 'label': 'Use SDF Lightmap', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseEmotionMap', 'label': 'Use Emotion Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmotionIndex', 'label': 'Emotion Index', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [0.0]},
        {'name': '_EmotionBlend', 'label': 'Emotion Blend', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_SDFRimColor', 'label': 'Skin Rim Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_SkinRimOffScale', 'label': 'Skin Rim Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.5, 'default': [0.5]},
        {'name': '_FaceRimOffScale', 'label': 'Face Rim Scale (SDF Area)', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.5, 'default': [1.0]},
        {'name': '_FaceHighlightMap', 'label': 'Use Face Highlight Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_HighlightMapVector', 'label': 'HighlightMap Vector', 'kind': 'VECTOR', 'size': 4, 'default': [0.04, -0.01, 0.0, 0.0]},
    ]},
    {'name': '脸部贴花', 'gate': None, 'rows': [
        {'name': '_FaceDecalTintColor', 'label': 'Face Decal Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_FaceDecalCenterX', 'label': 'Face Decal Center X', 'kind': 'SLIDER', 'size': 1, 'min': -0.5, 'max': 0.5, 'default': [0.0]},
        {'name': '_FaceDecalCenterY', 'label': 'Face Decal Center Y', 'kind': 'SLIDER', 'size': 1, 'min': -0.5, 'max': 0.5, 'default': [0.0]},
        {'name': '_FaceDecalInvertX', 'label': 'Face Decal Invert X', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FaceDecalInvertY', 'label': 'Face Decal Invert Y', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FaceDecalSize', 'label': 'Face Decal Size', 'kind': 'SLIDER', 'size': 1, 'min': 0.05, 'max': 2.0, 'default': [0.2]},
        {'name': '_FaceDecalRotation', 'label': 'Face Decal Rotation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_FaceDecalMirrorMode', 'label': 'Face Decal Mirror Mode', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_FaceDecalMirrorSplit', 'label': 'Face Decal Mirror Split', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_FaceDecalBrightnessMask', 'label': 'Face Decal Brightness Mask', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.7]},
    ]},
    {'name': '眼睛 Matcap', 'gate': None, 'rows': [
        {'name': '_MatcapTex', 'label': 'Matcap', 'kind': 'TEXTURE'},
        {'name': '_UseMatcap', 'label': 'Use Matcap', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EyeHighLight', 'label': 'Eye High Light', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_MatcapNormalScale', 'label': 'Matcap Normal Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.5, 'default': [1.0]},
        {'name': '_MatcapColor', 'label': 'Matcap Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EyeHighLightColor', 'label': 'High Light Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [2.0, 2.0, 2.0, 1.0]},
        {'name': '_EyeScatteringColor', 'label': 'Scattering Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EyeTintColor', 'label': 'Eye Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
    ]},
    {'name': '头发高光/描线', 'gate': None, 'rows': [
        {'name': '_SplitNormalMap', 'label': 'Hair Normal Map', 'kind': 'TEXTURE'},
        {'name': '_StrokeMap', 'label': 'Stroke Map(R:anisotropy G:specular offset)', 'kind': 'TEXTURE'},
        {'name': '_LineMap', 'label': 'Line Map', 'kind': 'TEXTURE'},
        {'name': '_UseSpecBumpMap', 'label': 'Split Diffuse / Specular Normal', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SpecBumpScale', 'label': 'Spec Scale', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_StrokeOn', 'label': 'Use Stroke Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SpecularLine', 'label': 'SpecularLine', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DrawUnderBrow', 'label': 'Draw Under Brow', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_AnisotropyValue', 'label': 'Anisotropy Value', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.35]},
        {'name': '_AnisotropyValue2', 'label': 'Anisotropy Value2', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.4]},
        {'name': '_AnisotropyDirX', 'label': 'Anisotropy Direction X', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_AnisotropyIntensity', 'label': 'Anisotropy Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_AnisotropyEdgeFade', 'label': 'Anisotropy Edge Fade', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 10.0, 'default': [1.0]},
        {'name': '_AnisotropyRange2', 'label': 'Anisotropy Range2', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_AnisotropyColor2', 'label': 'Anisotropy Color2', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_StrokeScale', 'label': 'Stroke Scale', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_UseLineMap', 'label': 'Use Line Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_LineAmount', 'label': 'Line Amount', 'kind': 'VALUE', 'size': 1, 'default': [300.0]},
        {'name': '_LineValue', 'label': 'Line Value', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_LineRange', 'label': 'Line Range', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_LineIntensity', 'label': 'Line Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_LineSaturation', 'label': 'Line Saturation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_HairBaseTintColor', 'label': 'Hair Base Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_HairAddTintColor', 'label': 'Hair Add Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
    ]},
    {'name': '皮毛', 'gate': '_UseCharacterFur', 'rows': [
        {'name': '_UseCharacterFur', 'label': 'Use CharacterFur', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FurMap', 'label': 'Fur Noise', 'kind': 'TEXTURE'},
        {'name': '_FurDirMap', 'label': '毛发方向(RG)疏密(B)长短(A)', 'kind': 'TEXTURE'},
        {'name': '_FurDyeMap', 'label': '皮毛染色', 'kind': 'TEXTURE'},
        {'name': '_FurDyeEnable', 'label': '使用皮毛染色功能', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FurDyeIntensity', 'label': '染色强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_FurLengthIntensity', 'label': '毛发长度', 'kind': 'SLIDER', 'size': 1, 'min': 0.001, 'max': 6.0, 'default': [1.0]},
        {'name': '_FurCutoffStart', 'label': '发根CutOff', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_FurCutoffEnd', 'label': '发尾CutOff', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_FurAO', 'label': '发根AO', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_FurEdgeFade', 'label': '边缘平滑过度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_FurGravityStrength', 'label': '重力强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_FurTTIntensity', 'label': '直射光透光强度', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_FurDirMapEnable', 'label': '使用毛发方向贴图(RG)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FurColorEnable', 'label': '使用尖端调色', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FurColor', 'label': '尖端调色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_FurSharpen', 'label': '皮毛尖锐', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FurNoise', 'label': '皮毛叠加噪声', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
    ]},
    {'name': '清漆', 'gate': '_ClearCoat', 'rows': [
        {'name': '_ClearCoat', 'label': 'ClearCoat Effect', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ClearCoatMask', 'label': 'ClearCoat Mask', 'kind': 'TEXTURE'},
        {'name': '_ClearCoatColor', 'label': 'ClearCoat Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ClearCoatSmoothness', 'label': 'ClearCoat Smoothness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.95]},
        {'name': '_ClearCoatMetallic', 'label': 'ClearCoat Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_ClearCoatNormalMode', 'label': 'ClearCoat Normal', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
    ]},
    {'name': '视差', 'gate': '_UseParallax', 'rows': [
        {'name': '_UseParallax', 'label': 'Use Parallax', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ParallaxTex', 'label': 'Parallax Tex', 'kind': 'TEXTURE'},
        {'name': '_ParallaxUseNormal', 'label': 'Parallax Use Normal Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ParallaxMarchNum', 'label': 'Parallax March Num', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 5.0, 'default': [3.0]},
        {'name': '_ParallaxScale', 'label': 'Parallax Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_ParallaxColor', 'label': 'Parallax Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
    ]},
    {'name': '丝袜', 'gate': '_SilkStockings', 'rows': [
        {'name': '_SilkStockings', 'label': 'Silk Stockings', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SilkStockingsMask', 'label': '丝袜遮罩', 'kind': 'TEXTURE'},
        {'name': '_SilkStockingsColor', 'label': '丝袜边缘颜色', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_SilkStockingsSpecularInt', 'label': '丝袜高光强度Remap', 'kind': 'VALUE', 'size': 1, 'default': [5.0]},
        {'name': '_SilkStockingsSpecularValue', 'label': '丝袜高光位置偏移', 'kind': 'SLIDER', 'size': 1, 'min': -2.0, 'max': 2.0, 'default': [2.0]},
        {'name': '_SilkStockingsAnisoDirection', 'label': '丝袜锐利度G', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_SilkStockingsDryColor', 'label': '丝袜常态偏色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_SilkStockingsWetColor', 'label': '丝袜湿润偏色', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_SilkStockingsMinAffect', 'label': '丝袜最浅覆盖', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.49, 'default': [0.05]},
        {'name': '_SilkStockingsMaxAffect', 'label': '丝袜最深覆盖', 'kind': 'SLIDER', 'size': 1, 'min': 0.5, 'max': 0.9, 'default': [0.9]},
        {'name': '_SilkStockingsAdvance', 'label': '丝袜高级模式(使用贴图)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SilkStockingsSpecularMinAtMinWetness', 'label': '丝袜高光干燥态最小值', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_SilkStockingsSpecularFalloff', 'label': '丝袜高光透肉衰减值', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.8]},
        {'name': '_SilkStockingsRainWetMaskScale', 'label': '丝袜浸润内置遮罩影响', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.7]},
        {'name': '_SilkStockingsAlbedoAffectType', 'label': '浸润或水下时透肉or压暗', 'kind': 'SLIDER', 'size': 1, 'min': -0.9, 'max': 0.5, 'default': [0.5]},
    ]},
    {'name': '各向异性', 'gate': '_UseAnisotropy', 'rows': [
        {'name': '_UseAnisotropy', 'label': 'Use Anisotropy', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_AnisotropyUseGeometryTangent', 'label': '使用模型切线', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_AnisotropyDirectionMain', 'label': '基础各项异性高光方向', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_AnisotropyIntensityMultiplier', 'label': '基础各项异性高光强度系数', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_AnisotropyDirectionAdditional', 'label': '第二层各向异性方向', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_AnisotropyOffsetAdditional', 'label': '第二层各向异性位置偏移', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_AnisotropyColorAdditional', 'label': '第二层各向异性颜色', 'kind': 'COLOR', 'size': 4, 'default': [0.2, 0.2, 0.2, 1.0]},
    ]},
    {'name': 'UV2 染色', 'gate': None, 'rows': [
        {'name': '_UseUV2Color', 'label': 'UV2 Color', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ExtraRootTintColor', 'label': 'Root Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ExtraDepthTintColor', 'label': 'Depth Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_ViewFade', 'label': 'View Fade', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.5, 'default': [0.0]},
    ]},
    {'name': '捏人染色', 'gate': '_AvatarCustomizeEnable', 'rows': [
        {'name': '_AvatarCustomizeEnable', 'label': 'Avatar System Input', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_CustomizeBaseColor', 'label': 'Customize Base Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_CustomizeBaseTintColor', 'label': 'Customize Base Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_CustomizeAddTintColor', 'label': 'Customize Add Tint Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
    ]},
    {'name': '深度淡出', 'gate': '_UseDepthFade', 'rows': [
        {'name': '_UseDepthFade', 'label': 'Use Depth Fade', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DepthFadeValue', 'label': 'Depth Fade Value', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_DepthFadeExp', 'label': 'Depth Fade Exp', 'kind': 'SLIDER', 'size': 1, 'min': 0.001, 'max': 50.0, 'default': [10.0]},
    ]},
    {'name': '侵蚀', 'gate': '_UseCharacterErosion', 'rows': [
        {'name': '_UseCharacterErosion', 'label': 'Use Character Erosion', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ErosionMetallic', 'label': 'Erosion Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_ErosionSmoothnessBias', 'label': 'Erosion Smoothness Bias', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_ErosionNormalScale', 'label': 'Erosion Normal Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 4.0, 'default': [1.0]},
        {'name': '_ErosionBaseColor', 'label': 'Erosion Base Color', 'kind': 'COLOR', 'size': 4, 'default': [0.8, 0.4, 0.5, 1.0]},
        {'name': '_ErosionUV2Tint', 'label': 'Erosion Tint UV2 Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ErosionBaseRootColor', 'label': 'Erosion Base Root Color', 'kind': 'COLOR', 'size': 4, 'default': [0.1, 0.1, 0.1, 1.0]},
        {'name': '_ErosionBaseRootColorLocation', 'label': 'Erosion Root Color Location', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.9, 'default': [0.1]},
        {'name': '_ErosionBaseRootColorSmooth', 'label': 'Erosion Root Color Smooth', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.25, 'default': [0.1]},
        {'name': '_ErosionBaseTopColor', 'label': 'Erosion Top Color', 'kind': 'COLOR', 'size': 4, 'default': [0.75, 0.75, 0.75, 1.0]},
        {'name': '_ErosionBaseTopColorLocation', 'label': 'Erosion Top Color Location', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.9, 'default': [0.7]},
        {'name': '_ErosionBaseTopColorSmooth', 'label': 'Erosion Top Color Smooth', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.25, 'default': [0.1]},
        {'name': '_ErosionPatternTintColor', 'label': 'Erosion Pattern Tint Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
    ]},
    {'name': '傀儡', 'gate': '_UsePuppet', 'rows': [
        {'name': '_UsePuppet', 'label': 'Use Puppet Effect', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_PuppetUV2AreaMask', 'label': 'Puppet UV2 Area Mask', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_PuppetMaskLocationDown', 'label': 'Puppet Mask Location Down', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.9, 'default': [0.1]},
        {'name': '_PuppetMaskLocationTop', 'label': 'Puppet Mask Location Top', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.9, 'default': [0.5]},
        {'name': '_PuppetMaskSmooth', 'label': 'Puppet Mask Smooth', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 0.25, 'default': [0.1]},
        {'name': '_PuppetProceduralDCurveEnable', 'label': 'Puppet Procedural DCurve Color', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_PuppetPDCurveUVScaleSpeed', 'label': 'Puppet DCurve UV2 Scale(XY) Speed(ZW)', 'kind': 'VECTOR', 'size': 4, 'default': [120.0, 12.0, 0.0, -0.06]},
        {'name': '_PuppetPDCurveDistortSpeed', 'label': 'Puppet DCurve Distort Speed', 'kind': 'VALUE', 'size': 1, 'default': [0.5]},
        {'name': '_PuppetPDCurveDistortPeriodSpeed', 'label': 'Puppet DCurve Period Speed', 'kind': 'VALUE', 'size': 1, 'default': [0.5]},
        {'name': '_PuppetPDCurveBaseColor', 'label': 'Puppet DCurve Base Color', 'kind': 'COLOR', 'size': 4, 'default': [0.39, 0.58, 0.7, 1.0]},
        {'name': '_PuppetPDCurveLightColor', 'label': 'Puppet DCurve Light Color', 'kind': 'COLOR', 'size': 4, 'default': [0.57, 0.3, 0.83, 0.5]},
        {'name': '_PuppetPDCurveEdgeColor', 'label': 'Puppet DCurve Edge Color', 'kind': 'COLOR', 'size': 4, 'default': [0.45, 0.38, 0.73, 0.5]},
        {'name': '_PuppetPDCurveEdgeLocation', 'label': 'Puppet DCurve Edge Location(1 = Unuse)', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [0.3]},
        {'name': '_PuppetPatternSpeed', 'label': 'Puppet Pattern Speed', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_PuppetPatternMapUseRGB', 'label': 'Puppet Pattern Map Use RGB', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_PuppetBaseColor', 'label': 'Puppet Base Color', 'kind': 'COLOR', 'size': 4, 'default': [0.5, 0.65, 0.8, 1.0]},
        {'name': '_PuppetPatternTintColor', 'label': 'Puppet Pattern Tint Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [0.4, 0.2, 0.94, 1.0]},
        {'name': '_PuppetPatternTintEdgeColor', 'label': 'Puppet Pattern Tint Edge Color', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_PuppetPatternTintEdgeLocation', 'label': 'Puppet Pattern Tint Edge Location(1 = Unuse)', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [1.0]},
        {'name': '_PuppetMetallic', 'label': 'Puppet Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_PuppetRoughness', 'label': 'Puppet Roughness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
    ]},
    {'name': '风格化菲涅尔', 'gate': '_EnableStylizedFresnel', 'rows': [
        {'name': '_EnableStylizedFresnel', 'label': 'Stylized Fresnel', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_StylizedFresnelColor', 'label': 'Color(A = Emission)', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_StylizedFresnelPow', 'label': 'Pow', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [2.0]},
        {'name': '_StylizedFresnelAmount', 'label': 'Amount', 'kind': 'VALUE', 'size': 1, 'default': [2.0]},
        {'name': '_StylizedFresnelNoiseSpeed', 'label': 'Noise Speed', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_StylizedNoiseContrast', 'label': 'Noise Contrast', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
    ]},
    {'name': '受击闪光', 'gate': '_EnableEnemyHitFlash', 'rows': [
        {'name': '_EnableEnemyHitFlash', 'label': 'Enemy Hit Flash', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EnemyHitFlashBrightColor', 'label': 'Bright(Scanline) Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EnemyHitFlashInnerRadius', 'label': '羽化内半径', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [0.0]},
        {'name': '_EnemyHitFlashOuterRadius', 'label': '羽化外半径', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [2.0]},
        {'name': '_EnemyHitFlashBrightCenter', 'label': '覆盖中心坐标,0为默认主角位置', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_EnemyHitFlashFresnelColor', 'label': 'Fresnel Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EnemyHitFlashFresnelBias', 'label': 'Fresnel Bias(Default:0)', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [0.0]},
        {'name': '_EnemyHitFlashFresnelAffectOpacity', 'label': 'Fresnel Affect Opacity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_EnemyHitFlashNormalScale', 'label': 'Normal Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_EnemyHitFlashBrightColorAdjust', 'label': 'Bright Color Adjust', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_EnemyHitFlashFresnelColorAdjust', 'label': 'Fresnel Color Adjust', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
    ]},
    {'name': '球形抖动消隐', 'gate': '_EnableDitherSphere', 'rows': [
        {'name': '_EnableDitherSphere', 'label': 'Enable Sphere Dither', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DitherSphereRadius', 'label': 'Dither Sphere Radius', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.3, 'default': [0.05]},
        {'name': '_DitherSphereSmoothness', 'label': 'Dither Sphere Smoothness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.5, 'default': [0.2]},
    ]},
    {'name': 'VAT 动画', 'gate': '_UseVATMap', 'rows': [
        {'name': '_UseVATMap', 'label': 'UseVATMap', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DebugVATFrameIndex', 'label': 'DebugVATFrame', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VATFrameIndex', 'label': 'VAT Frame Index', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
    ]},
    {'name': 'UV 流动/呼吸', 'gate': None, 'rows': [
        {'name': '_BaseMapUVSpeed', 'label': 'BaseMap UV Speed', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_EmissionMapUVSpeed', 'label': 'EmissionMap UV Speed', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_EmissionAlphaBrightBreath', 'label': 'Emission呼吸（A）', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmissionAlphaBrightBreathSpeed', 'label': 'Emission呼吸速度', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_EmissionAlphaBrightBreathScaleMin', 'label': 'Emission呼吸最小亮度', 'kind': 'VALUE', 'size': 1, 'default': [0.5]},
        {'name': '_EmissionAlphaBrightBreathScaleMax', 'label': 'Emission呼吸最大亮度', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
    ]},
    {'name': '顶点动画', 'gate': '_VertexAnimationEnable', 'rows': [
        {'name': '_VertexAnimationEnable', 'label': 'Vertex Animation', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VertexAnimationIntensity', 'label': 'Vertex Animation Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.1]},
        {'name': '_VertexAnimationFrequency', 'label': 'Vertex Animation Frequency', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 30.0, 'default': [0.5]},
        {'name': '_VertexAnimationWaveLength', 'label': 'Vertex Animation WaveLength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 20.0, 'default': [0.0]},
        {'name': '_VertexAnimationFalloff', 'label': 'Vertex Animation Falloff', 'kind': 'SLIDER', 'size': 1, 'min': 0.1, 'max': 10.0, 'default': [1.0]},
        {'name': '_VertexAnimationExpandOnly', 'label': 'Vertex Animation Expand Only', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VertexAnimationDirection', 'label': 'Vertex Animation Direction', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 1.0, 0.0]},
        {'name': '_VertexAnimationNoiseIntensity', 'label': 'Vertex Animation Noise Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_VertexAnimationNoiseTiling', 'label': 'Vertex Animation Noise Tiling', 'kind': 'SLIDER', 'size': 1, 'min': 0.5, 'max': 4.0, 'default': [1.0]},
        {'name': '_VertexAnimationNoiseFrequency', 'label': 'Vertex Animation Noise Frequency', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
    ]},
    {'name': '自阴影', 'gate': None, 'rows': [
        {'name': '_DisableSelfShadow', 'label': 'Disable Self Shadow', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
    ]},
    {'name': '角色 VFX', 'gate': None, 'rows': [
        {'name': '_EnableCharacterVFX', 'label': 'Character VFX', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
    ]},
    {'name': 'VFX 合成', 'gate': None, 'rows': [
        {'name': '_VFXSpecialMainTex', 'label': 'VFX Special Main Tex', 'kind': 'TEXTURE'},
        {'name': '_VFXSpecialBlendTex', 'label': 'VFX Special Blend Tex', 'kind': 'TEXTURE'},
        {'name': '_UseMask', 'label': 'Use Mask (只影响Alpha)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseBlend', 'label': 'Use Blend', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseDisturb', 'label': 'Use Disturb', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VFXColor', 'label': 'VFX Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_VFXColorIntensity', 'label': 'VFX Color Intensity (Default 1)', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 100.0, 'default': [1.0]},
        {'name': '_VFXColorAlpha', 'label': 'VFX Color Alpha (Default 1)', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_UseVFXMainTexAsAlpha', 'label': 'UseMainTexAsAlpha', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VFXSpecialBlendTexRForDisturb', 'label': 'Use Blend Tex R For Disturb', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_VFXBlendTint', 'label': 'BlendTint', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_VFXSpecialParam', 'label': 'VFX Special Param(XY: MainTex, ZW: BlendTex)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_VFXFresnelColor', 'label': 'Fresnel Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_VFXFresnelBias', 'label': 'Fresnel Bias(Default:0)', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 2.0, 'default': [0.0]},
        {'name': '_VFXFresnelAffectOpacity', 'label': 'Fresnel Affect Opacity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_VFXFresnelPower', 'label': 'Fresnel Power(Default:1)', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 100.0, 'default': [1.0]},
        {'name': '_VFXFresnelFlip', 'label': 'Fresnel Flip', 'kind': 'SWITCH', 'size': 1, 'default': [0.001]},
        {'name': '_SpecialDissolveScheduleOffset', 'label': 'Dissolve Schedule Offset', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [0.0]},
    ]},
    {'name': '特效贴图/流动', 'gate': None, 'rows': [
        {'name': '_MainTex', 'label': 'Main Tex', 'kind': 'TEXTURE'},
        {'name': '_BlendTex', 'label': 'Blend Tex', 'kind': 'TEXTURE'},
        {'name': '_MaskTex', 'label': 'Mask Tex', 'kind': 'TEXTURE'},
        {'name': '_DisturbTex1', 'label': 'Disturb Tex 1', 'kind': 'TEXTURE'},
        {'name': '_NormalMap', 'label': '法线图', 'kind': 'TEXTURE'},
        {'name': '_BlendMode', 'label': 'Blend Type', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_DisableVertColor', 'label': 'Disable VertColor', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_InParticle', 'label': 'Use In Particle', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_VertCameraOffset', 'label': '顶点向相机偏移(单位米)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_TintColor', 'label': 'TintColor', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_TintColorIntensity', 'label': 'Tint Color Intensity (Default 1)', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 100.0, 'default': [1.0]},
        {'name': '_TintColorAlpha', 'label': 'Tint Color Alpha (Default 1)', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_UseMainTexAsAlpha', 'label': 'UseMainTexAsAlpha', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_MainTexUseDisturb', 'label': 'Main Tex Use Disturb', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_MainTexUVSpeed', 'label': 'MainTexUVSpeed(XY:By Time,ZW:By Custom1.X)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_MainTexUVRotateMat', 'label': 'MainTexUVRotateMat', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 1.0]},
        {'name': '_MainTexUVWeights', 'label': '\'_MainTexUVWeights\'', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_UseMaskTexAsAlpha', 'label': 'UseMaskTexAsAlpha', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_MaskTexUseDisturb', 'label': 'Mask Tex Use Disturb', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MaskTexUVSpeed', 'label': 'MaskTaexUVSpeed(XY:By Time,ZW:By Custom1.Y)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_MaskTexUVRotateMat', 'label': 'MaskTexUVRotateMat', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 1.0]},
        {'name': '_MaskTexUVWeights', 'label': '\'_MaskTexUVWeights\'', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_BlendTexUseDisturb', 'label': 'Blend Tex Use Disturb', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_BlendTexUVSpeed', 'label': 'BlendTexUVSpeed(XY:By Time,ZW:By Custom1.Y)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_BlendTexUVRotateMat', 'label': 'BlendTexUVRotateMat', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 1.0]},
        {'name': '_BlendTexUVWeights', 'label': '\'_BlendTexUVWeights\'', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_BlendTint', 'label': 'BlendTint', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_Bi_Disturb', 'label': 'Disturbe in 2 Direction', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DisturbTex1Normal', 'label': 'Disturb Tex1 is Normal', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DisturbUIntensity1', 'label': 'UIntensity1', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_DisturbVIntensity1', 'label': 'VIntensity1(Unused In Normal)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_DisturbUVSpeed1', 'label': 'DisturbUVSpeed(XY:By Time,ZW:By Custom1.Y)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_DisturbUVRotateMat1', 'label': 'DisturbUVRotateMat', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 1.0]},
        {'name': '_DisturbUVWeights1', 'label': '\'_DisturbTexUVWeights\'', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_EnableNormalMap', 'label': 'Normal Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_NormalMapUVSpeed', 'label': 'NormalMapUVSpeed(XY:By Time,ZW:By Custom1.Y)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_NormalMapUVRotateMat', 'label': 'NormalMapUVRotateMat', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 1.0]},
        {'name': '_NormalMapUVWeights', 'label': '\'_NormalMapUVWeights\'', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
    ]},
    {'name': '特效菲涅尔/近淡出', 'gate': None, 'rows': [
        {'name': '_UseNearCameraFade', 'label': 'Use Near Camera Fade', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_NearCameraFadeDistanceStart', 'label': '消失距离1', 'kind': 'SLIDER', 'size': 1, 'min': 0.001, 'max': 3000.0, 'default': [0.001]},
        {'name': '_NearCameraFadeDistanceEnd', 'label': '出现距离1', 'kind': 'SLIDER', 'size': 1, 'min': 0.001, 'max': 3000.0, 'default': [10.0]},
        {'name': '_NearCameraFadeDistanceEnd2', 'label': '出现距离2', 'kind': 'SLIDER', 'size': 1, 'min': 0.002, 'max': 3000.0, 'default': [100.0]},
        {'name': '_NearCameraFadeDistanceStart2', 'label': '消失距离2', 'kind': 'SLIDER', 'size': 1, 'min': 0.001, 'max': 3000.0, 'default': [120.0]},
    ]},
    {'name': '特效杂项', 'gate': None, 'rows': [
        {'name': '_UseGrayAsAlpha', 'label': 'Use Gray As Alpha', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ShadowAngleRange', 'label': 'Shadow Angle Range', 'kind': 'SLIDER', 'size': 1, 'min': -0.01, 'max': 0.01, 'default': [0.0]},
    ]},
    {'name': '特效调色', 'gate': '_EnableVFXColorAdjustment', 'rows': [
        {'name': '_EnableVFXColorAdjustment', 'label': 'VFX Color Adjustment', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ColorAdjustmentContrast', 'label': 'Color Adjustment Contrast', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_ColorAdjustmentSaturation', 'label': 'Color Adjustment Saturation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_ColorAdjustmentBrightness', 'label': 'Color Adjustment Brightness', 'kind': 'SLIDER', 'size': 1, 'min': 0.5, 'max': 1.5, 'default': [1.0]},
        {'name': '_ColorAdjustmentRimWidth', 'label': 'Color Adjustment Rim Width', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.35]},
        {'name': '_ColorAdjustmentRimIntensity', 'label': 'Color Adjustment Rim Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 10.0, 'default': [4.0]},
        {'name': '_ColorAdjustmentColorBlend', 'label': 'Color Adjustment Color Blend', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_ColorAdjustmentRimColor', 'label': 'Color Adjustment Rim Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
    ]},
    {'name': '描边', 'gate': None, 'rows': [
        {'name': '_OutlineColorBrightness', 'label': 'Outline Color Brightness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_OutlineColorSaturation', 'label': 'Outline Color Saturation', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.5]},
    ]},
]


_PANEL_GUARD = [False]
_PANEL_REGISTERED = []


def _panel_attr(name):
    return 'p' + name


def _panel_rows():
    for group in INTERFACE:
        for row in group['rows']:
            yield row


def _panel_insts(mat):
    """级联组实例按建图序(ruri_inst 标记;与 rewire_capabilities 同一判据)。"""
    nt = mat.node_tree
    if nt is None:
        return []
    return sorted((n for n in nt.nodes if n.get('ruri_inst') is not None),
                  key=lambda n: int(n['ruri_inst']))


def _panel_vertex_nodes(mat):
    """本材质的顶点腿克隆组实例(壳位移 + 描边)。没有顶点腿的目标返回空——
    globals() 探测的是目标形态(该产物有没有发射顶点腿),不是运行时兜底。"""
    prefix_v = globals().get('CLONE_V_PREFIX')
    prefix_o = globals().get('CLONE_O_PREFIX')
    modifier = globals().get('VTX_MODIFIER')
    if not modifier:
        return []
    names = {p + mat.name for p in (prefix_v, prefix_o) if p}
    nodes = []
    for obj in bpy.data.objects:
        mod = obj.modifiers.get(modifier)
        tree = getattr(mod, 'node_group', None) if mod is not None else None
        if tree is None:
            continue
        for node in tree.nodes:
            sub = getattr(node, 'node_tree', None)
            if sub is not None and sub.name in names:
                nodes.append(node)
    return nodes


def _panel_touch(mat):
    # 纯数据写不触发依赖图,必须自己打脏标记(rewire_capabilities 同款教训)。
    if mat.node_tree is not None:
        mat.node_tree.update_tag()
    mat.update_tag()


def _panel_write(mat, row, value):
    """面板值 → 全部级联实例 socket + 快照字典 + 顶点腿克隆输入,一次到位。"""
    name = row['name']
    kind = row['kind']
    insts = _panel_insts(mat)
    if kind in ('SWITCH', 'VALUE', 'SLIDER', 'INT'):
        scalar = (1.0 if value else 0.0) if kind == 'SWITCH' else float(value)
        for grp in insts:
            sock = grp.inputs.get(name)
            if sock is not None and not sock.is_linked:
                sock.default_value = scalar
        for node in _panel_vertex_nodes(mat):
            sock = node.inputs.get(name)
            if sock is not None:
                sock.default_value = scalar
        floats = dict(mat.get('ruri_uber_floats') or {})
        floats[name] = scalar
        mat['ruri_uber_floats'] = floats
    else:
        vec4 = [float(v) for v in value] + [0.0] * 4
        vec4 = vec4[:4]
        xyz = (vec4[0], vec4[1], vec4[2])
        for grp in insts:
            sock = grp.inputs.get(name)
            if sock is not None and not sock.is_linked:
                sock.default_value = xyz
            tail = grp.inputs.get(name + '_w')
            if tail is not None and not tail.is_linked and row['size'] >= 4:
                tail.default_value = vec4[3]
        for node in _panel_vertex_nodes(mat):
            sock = node.inputs.get(name)
            if sock is not None:
                try:
                    sock.default_value = xyz
                except (TypeError, ValueError):
                    pass
        colors = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()}
        colors[name] = vec4
        mat['ruri_uber_colors'] = colors
    _panel_touch(mat)


def _panel_write_image(mat, row, image):
    """贴图槽换图。清空 = 回落到该槽的中性占位图(槽语义中性,不是黑)。
    只写本材质自有数据:链接库模板(library 非空)只读且跨材质共享,跳过。"""
    name = row['name']
    _images()
    target = image if image is not None else bpy.data.images.get(name)
    imgs = dict(mat.get('ruri_uber_images') or {})
    if image is None:
        imgs.pop(name, None)
    else:
        imgs[name] = image.name
    mat['ruri_uber_images'] = imgs

    def swap(tree, depth=0):
        if tree is None or depth > 4 or tree.library is not None:
            return
        for node in tree.nodes:
            if node.type == 'TEX_IMAGE' and _slot_of(node.label or '') == name:
                if target is not None:
                    _swap_image(node, target)
            elif node.type == 'GROUP':
                swap(node.node_tree, depth + 1)

    swapped = [0]
    if mat.node_tree is not None:
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and _slot_of(node.label or '') == name:
                if target is not None:
                    _swap_image(node, target)
                    swapped[0] += 1
            elif node.type == 'GROUP':
                swap(node.node_tree, 1)
    if not swapped[0] and target is not None and image is not None and mat.node_tree is not None:
        # 建线,不只换图:导入期没绑图的槽根本没有采样节点。走导入期同一条
        # _wire_fetch/_sample 链(FETCHES/ZONES 是唯一采样语义真源,面板不自己发明)。
        part = mat.get('ruri_uber_part', '')
        g = G(mat.node_tree, is_group=False)
        ordered = _panel_insts(mat)
        for fetch in FETCHES.get(part, ()):
            if fetch['slot'] == name and not fetch['env']:
                _wire_fetch(g, ordered, fetch, target)
        for zone in ZONES.get(part, ()):
            zone_rows = [f for f in zone['fetches'] if f['slot'] == name and not f['env']]
            if not zone_rows:
                continue
            binsts = sorted((n for n in mat.node_tree.nodes if n.get('ruri_zone') == zone['sock']),
                            key=lambda n: int(n['ruri_binst']))
            if not binsts:
                continue
            for fetch in zone_rows:
                src = binsts[fetch['depth']]
                heads = binsts[fetch['depth'] + 1:]
                color, alpha, _anchor = _sample(g, fetch, target, src.outputs[fetch['sock'] + '_uv'])
                _feed(g, heads, fetch['sock'], color, alpha)
    for group_node in _panel_vertex_nodes(mat):
        sub = group_node.node_tree
        if sub is None or target is None:
            continue
        for node in sub.nodes:
            if node.bl_idname == 'GeometryNodeImageTexture' and _slot_of(node.label or '') == name:
                holder = node.inputs['Image'].default_value
                non_color = holder is not None and holder.colorspace_settings.name == 'Non-Color'
                node.inputs['Image'].default_value = target
                try:
                    target.colorspace_settings.name = 'Non-Color' if non_color else 'sRGB'
                except Exception:
                    pass
                if non_color:
                    _fix_two_channel_layout(target)
    _panel_touch(mat)


def _panel_write_st(mat, row):
    """平铺/偏移 → _ST socket 对 + 顶点期 uv 变换节点 + 描边 mask ST(同一真值三消费面)。"""
    name = row['name']
    pg = getattr(mat, _PANEL_PROP)
    tiling = getattr(pg, 't' + name)
    offset = getattr(pg, 'o' + name)
    st_value = [float(tiling[0]), float(tiling[1]), float(offset[0]), float(offset[1])]
    st = {k: list(v) for k, v in dict(mat.get('ruri_uber_st') or {}).items()}
    st[name] = st_value
    mat['ruri_uber_st'] = st
    for grp in _panel_insts(mat):
        sock = grp.inputs.get(name + '_ST')
        if sock is not None and not sock.is_linked:
            sock.default_value = (st_value[0], st_value[1], st_value[2])
        tail = grp.inputs.get(name + '_ST_w')
        if tail is not None and not tail.is_linked:
            tail.default_value = st_value[3]
    if row.get('st_node') and mat.node_tree is not None:
        for node in mat.node_tree.nodes:
            if node.label == row['st_node']:
                node.inputs['Scale'].default_value = (st_value[0], st_value[1], 1.0)
                node.inputs['Location'].default_value = (st_value[2], st_value[3], 0.0)
        for group_node in _panel_vertex_nodes(mat):
            scale_sock = group_node.inputs.get('mask_st_scale')
            offset_sock = group_node.inputs.get('mask_st_offset')
            if scale_sock is not None:
                scale_sock.default_value = (st_value[0], st_value[1], 0.0)
            if offset_sock is not None:
                offset_sock.default_value = (st_value[2], st_value[3], 0.0)
    _panel_touch(mat)


def _panel_updater(row):
    def update(self, _context):
        if not _PANEL_GUARD[0]:
            _panel_write(self.id_data, row, getattr(self, _panel_attr(row['name'])))
    return update


def _panel_image_updater(row):
    def update(self, _context):
        if not _PANEL_GUARD[0]:
            _panel_write_image(self.id_data, row, getattr(self, _panel_attr(row['name'])))
    return update


def _panel_st_updater(row):
    def update(self, _context):
        if not _PANEL_GUARD[0]:
            _panel_write_st(self.id_data, row)
    return update


def _panel_bound_image(mat, slot):
    def walk(tree, depth=0):
        if tree is None or depth > 4:
            return None
        for node in tree.nodes:
            if node.type == 'TEX_IMAGE' and _slot_of(node.label or '') == slot:
                img = node.image
                if img is not None and not img.get('ruri_placeholder'):
                    return img
            elif node.type == 'GROUP':
                hit = walk(node.node_tree, depth + 1)
                if hit is not None:
                    return hit
        return None
    return walk(mat.node_tree)


def panel_sync(mat):
    """图/快照 → 面板回读(守卫防触发写路径)。provider 每次建完自动调;
    图被外部改动后由面板刷新按钮手动触发。"""
    pg = getattr(mat, _PANEL_PROP, None)
    if pg is None:
        return False
    insts = _panel_insts(mat)
    if not insts:
        return False
    first = insts[0]
    floats = dict(mat.get('ruri_uber_floats') or {})
    st = {k: list(v) for k, v in dict(mat.get('ruri_uber_st') or {}).items()}
    colors = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()}
    images = dict(mat.get('ruri_uber_images') or {})
    _PANEL_GUARD[0] = True
    try:
        for row in _panel_rows():
            name = row['name']
            attr = _panel_attr(name)
            kind = row['kind']
            if kind == 'TEXTURE':
                img = bpy.data.images.get(images.get(name, ''))
                if img is None:
                    img = _panel_bound_image(mat, name)
                try:
                    setattr(pg, attr, img)
                except Exception:
                    pass
                value = st.get(name)
                if value is None:
                    sock = first.inputs.get(name + '_ST')
                    if sock is not None and not sock.is_linked:
                        tail = first.inputs.get(name + '_ST_w')
                        raw = sock.default_value
                        value = [raw[0], raw[1], raw[2],
                                 tail.default_value if tail is not None else 0.0]
                if value is not None:
                    setattr(pg, 't' + name, (value[0], value[1]))
                    setattr(pg, 'o' + name, (value[2], value[3]))
                continue
            sock = first.inputs.get(name)
            if kind in ('SWITCH', 'VALUE', 'SLIDER', 'INT'):
                value = floats.get(name)
                if value is None and sock is not None and not sock.is_linked and sock.type == 'VALUE':
                    value = sock.default_value
                if value is None:
                    continue
                setattr(pg, attr, bool(value > 0.5) if kind == 'SWITCH'
                        else int(value) if kind == 'INT' else float(value))
            else:
                value = colors.get(name)
                if value is None and sock is not None and not sock.is_linked and sock.type == 'VECTOR':
                    tail = first.inputs.get(name + '_w')
                    raw = sock.default_value
                    value = [raw[0], raw[1], raw[2],
                             tail.default_value if tail is not None and not tail.is_linked else 1.0]
                if value is None:
                    continue
                spread = (list(value) + [0.0] * 4)[:row['size']]
                setattr(pg, attr, tuple(float(x) for x in spread))
    finally:
        _PANEL_GUARD[0] = False
    return True


def _panel_slots(mat):
    """本材质有意义的贴图槽 = .mat 绑定 ∪ 本 part 的割点表(顶层/循环体)∪ 顶点腿图节点。
    全部按既有表判定,零平行清单。"""
    part = mat.get('ruri_uber_part', '')
    slots = set(dict(mat.get('ruri_uber_images') or {}).keys())
    for fetch in FETCHES.get(part, ()):
        slots.add(fetch['slot'])
    for zone in ZONES.get(part, ()):
        for fetch in zone['fetches']:
            slots.add(fetch['slot'])
    for group_node in _panel_vertex_nodes(mat):
        sub = group_node.node_tree
        if sub is None:
            continue
        for node in sub.nodes:
            if node.bl_idname == 'GeometryNodeImageTexture' and node.label:
                slots.add(_slot_of(node.label))
    return slots


def _panel_visible(insts, slots, row):
    """变体折叠掉的死支参数没有 socket → 不画。判据 = 图自己,不是另一张表。"""
    if row['kind'] == 'TEXTURE':
        return row['name'] in slots
    name = row['name']
    for grp in insts:
        if grp.inputs.get(name) is not None:
            return True
    return False


def _panel_classes():
    toggle_idname = PANEL_KEY + '.toggle_group'
    sync_idname = PANEL_KEY + '.sync_panel'

    class Toggle(bpy.types.Operator):
        bl_idname = toggle_idname
        bl_label = '展开/折叠分组'
        bl_options = {'INTERNAL'}
        group: bpy.props.IntProperty()

        def execute(self, context):
            pg = getattr(context.material, _PANEL_PROP)
            key = '_open_%d' % self.group
            pg[key] = 0 if pg.get(key) else 1
            return {'FINISHED'}

    class Sync(bpy.types.Operator):
        bl_idname = sync_idname
        bl_label = '从图同步面板'
        bl_description = '图被重建或外部改动后,把面板回读到当前值'
        bl_options = {'INTERNAL'}

        def execute(self, context):
            panel_sync(context.material)
            return {'FINISHED'}

    class Panel(bpy.types.Panel):
        bl_idname = 'RURI_PT_' + PANEL_KEY
        bl_label = PANEL_TITLE
        bl_space_type = 'PROPERTIES'
        bl_region_type = 'WINDOW'
        bl_context = 'material'

        @classmethod
        def poll(cls, context):
            mat = getattr(context, 'material', None)
            return (mat is not None and mat.get('ruri_uber_part') is not None
                    and getattr(mat, _PANEL_PROP, None) is not None)

        def draw(self, context):
            mat = context.material
            pg = getattr(mat, _PANEL_PROP)
            layout = self.layout
            layout.use_property_split = True
            layout.use_property_decorate = False
            head = layout.row(align=True)
            head.label(text='{0} · {1}'.format(mat.get('ruri_uber_part', ''),
                                               mat.get('ruri_uber_shader', '') or ''), icon='MATERIAL')
            head.operator(sync_idname, text='', icon='FILE_REFRESH')
            insts = _panel_insts(mat)
            slots = _panel_slots(mat)
            for index, group in enumerate(INTERFACE):
                rows = [r for r in group['rows'] if _panel_visible(insts, slots, r)]
                if not rows:
                    continue
                gate = group['gate']
                box = layout.box()
                header = box.row(align=True)
                opened = bool(pg.get('_open_%d' % index))
                arrow = header.operator(toggle_idname, text='', emboss=False,
                                        icon='TRIA_DOWN' if opened else 'TRIA_RIGHT')
                arrow.group = index
                header.label(text=group['name'])
                gate_open = True
                gate_attr = _panel_attr(gate) if gate else None
                if gate_attr is not None and hasattr(pg, gate_attr):
                    header.prop(pg, gate_attr, text='')
                    gate_open = bool(getattr(pg, gate_attr))
                if not opened:
                    continue
                column = box.column()
                column.active = gate_open
                for row in rows:
                    if gate is not None and row['name'] == gate:
                        continue
                    self.draw_row(column, pg, insts, row)

        def draw_row(self, column, pg, insts, row):
            attr = _panel_attr(row['name'])
            if row['kind'] != 'TEXTURE':
                column.prop(pg, attr)
                return
            split = column.split(factor=0.4)
            split.label(text=row['label'])
            split.template_ID(pg, attr, open='image.open')
            has_st = bool(row.get('st_node')) or any(
                grp.inputs.get(row['name'] + '_ST') is not None for grp in insts)
            if has_st:
                sub = column.column(align=True)
                sub.prop(pg, 't' + row['name'], text='平铺')
                sub.prop(pg, 'o' + row['name'], text='偏移')

    return Toggle, Sync, Panel


def _panel_register():
    """INTERFACE → PropertyGroup(动态注解)+ 面板 + 操作符。幂等:先卸旧再注册。"""
    _panel_unregister()
    annotations = {}
    for row in _panel_rows():
        attr = _panel_attr(row['name'])
        kind = row['kind']
        if kind == 'TEXTURE':
            annotations[attr] = bpy.props.PointerProperty(
                type=bpy.types.Image, name=row['label'], update=_panel_image_updater(row))
            annotations['t' + row['name']] = bpy.props.FloatVectorProperty(
                name='平铺', size=2, default=(1.0, 1.0), update=_panel_st_updater(row))
            annotations['o' + row['name']] = bpy.props.FloatVectorProperty(
                name='偏移', size=2, default=(0.0, 0.0), update=_panel_st_updater(row))
            continue
        default = list(row.get('default') or [])
        if kind == 'SWITCH':
            annotations[attr] = bpy.props.BoolProperty(
                name=row['label'], default=bool(default and default[0] > 0.5),
                update=_panel_updater(row))
        elif kind == 'INT':
            annotations[attr] = bpy.props.IntProperty(
                name=row['label'], default=int(default[0]) if default else 0,
                update=_panel_updater(row))
        elif kind in ('VALUE', 'SLIDER'):
            keywords = {'name': row['label'],
                        'default': float(default[0]) if default else 0.0,
                        'update': _panel_updater(row)}
            if kind == 'SLIDER':
                keywords['min'] = row['min']
                keywords['max'] = row['max']
            annotations[attr] = bpy.props.FloatProperty(**keywords)
        else:
            size = row['size']
            fill = (default + [0.0] * 4)[:size]
            keywords = {'name': row['label'], 'size': size,
                        'update': _panel_updater(row)}
            if kind == 'COLOR':
                keywords.update(subtype='COLOR', min=0.0, max=1.0)
                fill = [min(max(v, 0.0), 1.0) for v in fill]
            elif kind == 'HDRCOLOR':
                keywords.update(subtype='COLOR', min=0.0, soft_max=1.0)
            keywords['default'] = tuple(fill)
            annotations[attr] = bpy.props.FloatVectorProperty(**keywords)
    group_cls = type('RURI_PG_' + PANEL_KEY, (bpy.types.PropertyGroup,),
                     {'__annotations__': annotations})
    bpy.utils.register_class(group_cls)
    _PANEL_REGISTERED.append(group_cls)
    setattr(bpy.types.Material, _PANEL_PROP, bpy.props.PointerProperty(type=group_cls))
    for cls in _panel_classes():
        bpy.utils.register_class(cls)
        _PANEL_REGISTERED.append(cls)


def _panel_unregister():
    if hasattr(bpy.types.Material, _PANEL_PROP):
        try:
            delattr(bpy.types.Material, _PANEL_PROP)
        except Exception:
            pass
    while _PANEL_REGISTERED:
        cls = _PANEL_REGISTERED.pop()
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def register():
    # 宿主注册表按**绝对路径**导入(配方给的名字):相对导入会绑死部署深度,
    # 而本文件必须能被脱包 spec_from_file_location 直接加载(建图/压测探针靠它)。
    # 材质图/顶点腿/兑现面重接都在这里自注册 —— 消费方只调宿主注册表,门面不写一行逻辑。
    import importlib
    host = importlib.import_module('RuriRipperImporter.material_builder')
    host.register_graph_provider(provider)
    host.register_vertex_stage(apply_vertex_stage)
    host.register_capability_rewire(rewire_capabilities)
    host.register_light_table_refresh(refresh_light_tables)
    _panel_register()


def unregister():
    _panel_unregister()
    import importlib
    host = importlib.import_module('RuriRipperImporter.material_builder')
    host.unregister_graph_provider(provider)
    host.unregister_vertex_stage(apply_vertex_stage)
    host.unregister_capability_rewire(rewire_capabilities)
    host.unregister_light_table_refresh(refresh_light_tables)


# 导入即清理过时预设库(见 _prune_stale_libraries):要在 register() 之外,
# 因为脱包直接加载本文件的探针也该把目录扫干净。
_prune_stale_libraries()
if __name__ == '__main__':
    build_root()
