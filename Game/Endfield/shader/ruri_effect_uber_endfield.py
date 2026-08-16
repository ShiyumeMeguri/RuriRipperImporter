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
#  · **part 变体**:_EffectPartID 生成期折叠,每 part 一套入口 + 独立依赖闭包
#    (PARTS 表)。消费方按材质 part 选 PARTS[part],只建该闭包 —— 死支零节点;
#  · keyword 折叠: _ENDFIELD_EFFECT, _RURI_FORWARD_PASS(前向单趟);
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
    _img('_BaseTex', (1.0, 1.0, 1.0, 1.0), False)
    _img('_BlendTex', (0.0, 0.0, 0.0, 0.0), False)
    _img('_DissolveTex', (1.0, 1.0, 1.0, 1.0), True)
    _img('_MainTex', (1.0, 1.0, 1.0, 1.0), False)
    _img('_MaskTex', (1.0, 1.0, 1.0, 1.0), True)


def build_RCE_ExpBoost():
    t = _tree('RCE_ExpBoost')
    g = G(t)
    v0 = g.inp('rgb', True)
    v1 = g.inp('threshold', False)
    v2 = g.inp('intensity', False)
    v3 = g.bc(v1)
    v4 = g.vmath('SUBTRACT', v0, v3)
    v5 = g.vmath('MAXIMUM', v4, (0, 0, 0))
    v6 = g.bc(v2)
    v7 = g.vmath('MULTIPLY', v5, v6)
    v8 = g.vmath('ADD', v0, v7)
    v9 = g.vmath('MAXIMUM', v8, (0, 0, 0))
    v10 = g.vmath('MINIMUM', v9, (1000, 1000, 1000))
    g.out_('ret', v10, True)


def build_RCE_Fresnel():
    t = _tree('RCE_Fresnel')
    g = G(t)
    v0 = g.inp('normalWS', True)
    v1 = g.inp('viewDirectionWS', True)
    v2 = g.inp('bias', False)
    v3 = g.inp('power', False)
    v4 = g.inp('flip', False)
    v5 = g.inp('affectOpacity', False)
    v6 = g.vmath('NORMALIZE', v0)
    v7 = g.vmath('NORMALIZE', v1)
    v8 = g.vmath('DOT_PRODUCT', v6, v7)
    v9 = g.clampn(v8)
    v10 = g.math('SUBTRACT', 1, v9)
    v11 = g.clampn(v10)
    v12 = g.math('ADD', v11, v2)
    v13 = g.math('POWER', v12, v3)
    v14 = g.clampn(v13)
    v15 = g.math('SUBTRACT', 1, v14)
    v16 = g.clampn(v4)
    v17 = g.mixf(v16, v14, v15)
    v18 = g.math('MULTIPLY', v17, v5)
    g.out_('ret', v18, False)


def build_RCE_GridLine():
    t = _tree('RCE_GridLine')
    g = G(t)
    v0 = g.inp('uv', True)
    v1 = g.inp('width', False)
    v2 = g.inp('ddxUV', True)
    v3 = g.inp('ddyUV', True)
    v4 = g.vmath('ABSOLUTE', v2)
    v5 = g.vmath('ABSOLUTE', v3)
    v6 = g.vmath('MAXIMUM', v4, v5)
    v7 = g.comb(v1, v1, 0.0)
    v8 = g.vmath('MULTIPLY', v6, v7)
    v9 = g.vmath('SUBTRACT', v0, (0.5, 0.5, 0.0))
    v10 = g.vmath('FRACTION', v9)
    v11 = g.vmath('SUBTRACT', v10, (0.5, 0.5, 0.0))
    v12 = g.vmath('ABSOLUTE', v11)
    v13 = g.vmath('MAXIMUM', v8, (1E-06, 1E-06, 0.0))
    v14 = g.vmath('DIVIDE', v12, v13)
    v15 = g.sep(v14)
    v16 = g.math('MINIMUM', v15[0], v15[1])
    v17 = g.clampn(v16)
    v18 = g.math('SUBTRACT', 1, v17)
    g.out_('ret', v18, False)


def build_RCE_NearCameraFade():
    t = _tree('RCE_NearCameraFade')
    g = G(t)
    v0 = g.inp('viewDepth', False)
    v1 = g.inp('enable', False)
    v2 = g.inp('start', False)
    v3 = g.inp('end', False)
    v4 = g.inp('start2', False)
    v5 = g.inp('end2', False)
    v6 = g.math('COMPARE', v1, 0, 1e-05)
    v7 = g.mixf(v6, 0.0, 1)
    v8 = g.mixf(v6, 0.0, 1.0)
    v9 = g.math('SUBTRACT', 1.0, v8)
    v10 = g.math('SUBTRACT', v0, v2)
    v11 = g.math('SUBTRACT', v3, v2)
    v12 = g.math('DIVIDE', v10, v11)
    v13 = g.clampn(v12)
    v14 = g.math('SUBTRACT', v0, v4)
    v15 = g.math('SUBTRACT', v5, v4)
    v16 = g.math('DIVIDE', v14, v15)
    v17 = g.clampn(v16)
    v18 = g.math('MULTIPLY', v13, v17)
    v19 = g.mixf(v9, v7, v18)
    v20 = g.mixf(v9, v8, 1.0)
    g.out_('ret', v19, False)


def build_RCE_RotateUV():
    t = _tree('RCE_RotateUV')
    g = G(t)
    v0 = g.inp('uv', True)
    v1 = g.inp('degrees', False)
    v2 = g.math('MULTIPLY', v1, 0.01745329251)
    v3 = g.math('SINE', v2, 0.0)
    v4 = g.math('COSINE', v2, 0.0)
    v5 = g.vmath('SUBTRACT', v0, (0.5, 0.5, 0.0))
    v6 = g.sep(v5)
    v7 = g.math('MULTIPLY', v6[0], v4)
    v8 = g.math('MULTIPLY', v6[1], v3)
    v9 = g.math('SUBTRACT', v7, v8)
    v10 = g.math('MULTIPLY', v6[0], v3)
    v11 = g.math('MULTIPLY', v6[1], v4)
    v12 = g.math('ADD', v10, v11)
    v13 = g.comb(v9, v12, 0.0)
    v14 = g.vmath('ADD', v13, (0.5, 0.5, 0.0))
    g.out_('ret', v14, True)


def build_RCE_SelectUV():
    t = _tree('RCE_SelectUV')
    g = G(t)
    v0 = g.inp('uv', True)
    v1 = g.inp('screenUV', True)
    v2 = g.inp('switchMode', False)
    v3 = g.math('LESS_THAN', v2, 0.5)
    v4 = g.math('LESS_THAN', v2, 1.5)
    v5 = g.vmath('SUBTRACT', v0, (0.5, 0.5, 0.0))
    v6 = g.sep(v5)
    v7 = g.math('ARCTAN2', v6[1], v6[0])
    v8 = g.math('MULTIPLY', v7, 0.15915494)
    v9 = g.math('ADD', v8, 0.5)
    v10 = g.vmath('LENGTH', v5)
    v11 = g.math('MULTIPLY', v10, 2)
    v12 = g.comb(v9, v11, 0.0)
    v13 = g.mixv(v4, v12, v1)
    v14 = g.mixv(v3, v13, v0)
    g.out_('ret', v14, True)


def build_Ruri_Endfield_Effect_VFXBaseV2():
    t = _tree('Ruri Endfield Effect VFXBaseV2')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_color', True)
    v4 = g.inp('input_color_w', False)
    v5 = g.inp('input_positionCS', True)
    v6 = g.inp('input_positionCS_w', False)
    v7 = g.inp('facing', False)
    v8 = g.b2u(v1, point=True)
    v9 = g.b2u(v2, point=False)
    v10 = g.vmath('NORMALIZE', v9)
    v11 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v12 = g.vmath('SUBTRACT', v11, v8)
    v13 = g.vmath('NORMALIZE', v12)
    v14 = g.texco().outputs['Window']
    v15 = g.inp('_DisableVertColor', False, 0.0)
    v16 = g.math('COMPARE', v15, 0, 1e-05)
    v17 = g.math('SUBTRACT', 1.0, v16)
    v18 = g.mixv(v17, v3, (1, 1, 1))
    v19 = g.mixf(v17, v4, 1)
    v20 = g.inp('_TintColor', True, (1.0, 1.0, 1.0))
    v21 = g.inp('_TintColor_w', False, 1.0)
    v22 = g.vmath('MULTIPLY', v20, v18)
    v23 = g.math('MULTIPLY', v21, v19)
    v24 = g.inp('_TintColorIntensity', False, 1.0)
    v25 = g.bc(v24)
    v26 = g.vmath('MULTIPLY', v22, v25)
    v27 = g.inp('_ExposureWithMiscParams', True, (1.0, 1.0, 1.0))
    v28 = g.inp('_ExposureWithMiscParams_w', False, 1.0)
    v29 = g.sep(v27)
    v30 = g.inp('_IgnorePostExposure', False, 1.0)
    v31 = g.mixf(v30, 1, v29[1])
    v32 = g.bc(v31)
    v33 = g.vmath('MULTIPLY', v26, v32)
    v34 = g.inp('_TintColorAlpha', False, 1.0)
    v35 = g.math('MULTIPLY', v23, v34)
    v36 = g.inp('_MainSwitchUV', False, 0.0)
    v37 = g.group_named('RCE_SelectUV', [('uv', v0), ('screenUV', v14), ('switchMode', v36)])
    v38 = g.inp('_MainTexUVRotate', False, 0.0)
    v39 = g.group_named('RCE_RotateUV', [('uv', v37[0]), ('degrees', v38)])
    v40 = g.inp('_MainTexUVSpeed', True, (0.0, 0.0, 0.0))
    v41 = g.inp('_MainTexUVSpeed_w', False, 0.0)
    v42 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v43 = g.inp('_Time_w', False, 0.0)
    v44 = g.sep(v42)
    v45 = g.sep(v40)
    v46 = g.comb(v45[0], v45[1], 0.0)
    v47 = g.comb(v44[1], v44[1], 0.0)
    v48 = g.vmath('MULTIPLY', v46, v47)
    v49 = g.vmath('ADD', v39[0], v48)
    v50 = g.inp('_MainTex_ST', True, (1.0, 1.0, 0.0))
    v51 = g.inp('_MainTex_ST_w', False, 0.0)
    v52 = g.sep(v50)
    v53 = g.comb(v52[0], v52[1], 0.0)
    v54 = g.vmath('MULTIPLY', v49, v53)
    v55 = g.comb(v52[2], v51, 0.0)
    v56 = g.vmath('ADD', v54, v55)
    g.out_('F0_MainTex_uv', v56, True)
    v57 = g.inp('F0_MainTex', True, (1.0, 1.0, 1.0))
    v58 = g.inp('F0_MainTex_alpha', False, 1.0)
    v59 = g.sep(v57)
    v60 = g.inp('_UseMainTexAsAlpha', False, 1.0)
    v61 = g.mixv(v60, v57, (1, 1, 1))
    v62 = g.mixf(v60, v58, v59[0])
    v63 = g.mixv(0, v61, v57)
    v64 = g.mixf(0, v62, v58)
    v65 = g.vmath('MULTIPLY', v33, v63)
    v66 = g.math('MULTIPLY', v35, v64)
    v67 = g.inp('_UseBlend', False, 0.0)
    v68 = g.inp('_BlendTex_ST', True, (1.0, 1.0, 0.0))
    v69 = g.inp('_BlendTex_ST_w', False, 0.0)
    v70 = g.sep(v68)
    v71 = g.comb(v70[0], v70[1], 0.0)
    v72 = g.vmath('MULTIPLY', v49, v71)
    v73 = g.comb(v70[2], v69, 0.0)
    v74 = g.vmath('ADD', v72, v73)
    g.out_('F1_BlendTex_uv', v74, True)
    v75 = g.inp('F1_BlendTex', True, (0.0, 0.0, 0.0))
    v76 = g.inp('F1_BlendTex_alpha', False, 1.0)
    v77 = g.inp('_BlendTint', True, (1.0, 1.0, 1.0))
    v78 = g.inp('_BlendTint_w', False, 1.0)
    v79 = g.vmath('MULTIPLY', v75, v77)
    v80 = g.vmath('MULTIPLY', v65, v79)
    v81 = g.mixv(v67, v65, v80)
    v82 = g.inp('_UseMask', False, 0.0)
    v83 = g.inp('_MaskTex_ST', True, (1.0, 1.0, 0.0))
    v84 = g.inp('_MaskTex_ST_w', False, 0.0)
    v85 = g.sep(v83)
    v86 = g.comb(v85[0], v85[1], 0.0)
    v87 = g.vmath('MULTIPLY', v49, v86)
    v88 = g.comb(v85[2], v84, 0.0)
    v89 = g.vmath('ADD', v87, v88)
    g.out_('F2_MaskTex_uv', v89, True)
    v90 = g.inp('F2_MaskTex', True, (1.0, 1.0, 1.0))
    v91 = g.inp('F2_MaskTex_alpha', False, 1.0)
    v92 = g.sep(v90)
    v93 = g.math('MULTIPLY', v66, v92[0])
    v94 = g.mixf(v82, v66, v93)
    v95 = g.inp('_UseDissolve', False, 0.0)
    v96 = g.inp('_DissolveTex_ST', True, (1.0, 1.0, 0.0))
    v97 = g.inp('_DissolveTex_ST_w', False, 0.0)
    v98 = g.sep(v96)
    v99 = g.comb(v98[0], v98[1], 0.0)
    v100 = g.vmath('MULTIPLY', v49, v99)
    v101 = g.comb(v98[2], v97, 0.0)
    v102 = g.vmath('ADD', v100, v101)
    g.out_('F3_DissolveTex_uv', v102, True)
    v103 = g.inp('F3_DissolveTex', True, (1.0, 1.0, 1.0))
    v104 = g.inp('F3_DissolveTex_alpha', False, 1.0)
    v105 = g.sep(v103)
    v106 = g.inp('_DissolveAmount', False, 0.0)
    v107 = g.math('SUBTRACT', v105[0], v106)
    v108 = g.inp('_DissolveEdgeColor', True, (1.0, 1.0, 1.0))
    v109 = g.inp('_DissolveEdgeColor_w', False, 1.0)
    v110 = g.inp('_DissolveEdgeWidth', False, 0.1)
    v111 = g.math('MAXIMUM', v110, 0.0001)
    v112 = g.math('DIVIDE', v107, v111)
    v113 = g.clampn(v112)
    v114 = g.math('SUBTRACT', 1, v113)
    v115 = g.bc(v114)
    v116 = g.vmath('MULTIPLY', v108, v115)
    v117 = g.vmath('ADD', v81, v116)
    v118 = g.math('LESS_THAN', v107, 0)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MULTIPLY', v94, v119)
    v121 = g.mixv(v95, v81, v117)
    v122 = g.mixf(v95, v94, v120)
    v123 = g.inp('_ExpThreshold', False, 1.0)
    v124 = g.inp('_ExpIntensity', False, 0.0)
    v125 = g.group_named('RCE_ExpBoost', [('rgb', v121), ('threshold', v123), ('intensity', v124)])
    v126 = g.inp('_UseGridLine', False, 0.0)
    v127 = g.inp('_GridLineWidth', False, 1.0)
    v128 = g.group_named('RCE_GridLine', [('uv', v0), ('width', v127), ('ddxUV', (0.0, 0.0, 0.0)), ('ddyUV', (0.0, 0.0, 0.0))])
    v129 = g.math('MULTIPLY', v122, v128[0])
    v130 = g.mixf(v126, v122, v129)
    v131 = g.inp('_UseFresnel', False, 0.0)
    v132 = g.inp('_FresnelBias', False, 0.0)
    v133 = g.inp('_FresnelPower', False, 1.0)
    v134 = g.inp('_FresnelFlip', False, 0.001)
    v135 = g.inp('_FresnelAffectOpacity', False, 1.0)
    v136 = g.group_named('RCE_Fresnel', [('normalWS', v9), ('viewDirectionWS', v13), ('bias', v132), ('power', v133), ('flip', v134), ('affectOpacity', v135)])
    v137 = g.inp('_FresnelColor', True, (1.0, 1.0, 1.0))
    v138 = g.inp('_FresnelColor_w', False, 1.0)
    v139 = g.bc(v136[0])
    v140 = g.vmath('MULTIPLY', v137, v139)
    v141 = g.vmath('ADD', v125[0], v140)
    v142 = g.math('MULTIPLY', v136[0], v135)
    v143 = g.math('ADD', v130, v142)
    v144 = g.clampn(v143)
    v145 = g.mixv(v131, v125[0], v141)
    v146 = g.mixf(v131, v130, v144)
    v147 = g.vmath('SUBTRACT', v8, v11)
    v148 = g.vmath('LENGTH', v147)
    v149 = g.inp('_UseNearCameraFade', False, 0.0)
    v150 = g.inp('_NearCameraFadeDistanceStart', False, 0.001)
    v151 = g.inp('_NearCameraFadeDistanceEnd', False, 10.0)
    v152 = g.inp('_NearCameraFadeDistanceStart2', False, 120.0)
    v153 = g.inp('_NearCameraFadeDistanceEnd2', False, 100.0)
    v154 = g.group_named('RCE_NearCameraFade', [('viewDepth', v148), ('enable', v149), ('start', v150), ('end', v151), ('start2', v152), ('end2', v153)])
    v155 = g.math('MULTIPLY', v146, v154[0])
    v156 = g.clampn(v155)
    v157 = g.vmath('DOT_PRODUCT', v145, (0.2126729, 0.7151522, 0.072175))
    v158 = g.comb(v157, v157, v157)
    v159 = g.inp('_VFXParams1', True, (1.0, 1.0, 1.0))
    v160 = g.inp('_VFXParams1_w', False, 1.0)
    v161 = g.mixv(v160, v158, v145)
    v162 = g.vmath('MULTIPLY', v161, v159)
    v163 = g.vmath('NORMALIZE', v9)
    v164 = g.inp('_UseAlphaTest', False, 0.0)
    v165 = g.inp('_AlphaClipThreshold', False, 0.5)
    v166 = g.math('SUBTRACT', v156, v165)
    v167 = g.math('LESS_THAN', v166, 0.0)
    v168 = g.math('SUBTRACT', 1.0, v167)
    v169 = g.math('MULTIPLY', 1.0, v168)
    v170 = g.mixf(v164, 1.0, v169)
    g.out_('ret_gBuffer0', v162, True)
    g.out_('ret_gBuffer0_w', v156, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v162, True)
    g.out_('ret_color_w', v156, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v170, False)


def build_Ruri_Endfield_Effect_VFXDistanceField():
    t = _tree('Ruri Endfield Effect VFXDistanceField')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_normalWS', True)
    v3 = g.inp('input_color', True)
    v4 = g.inp('input_color_w', False)
    v5 = g.inp('input_positionCS', True)
    v6 = g.inp('input_positionCS_w', False)
    v7 = g.inp('facing', False)
    v8 = g.b2u(v1, point=True)
    v9 = g.b2u(v2, point=False)
    v10 = g.vmath('NORMALIZE', v9)
    v11 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v12 = g.vmath('SUBTRACT', v11, v8)
    v13 = g.vmath('NORMALIZE', v12)
    v14 = g.texco().outputs['Window']
    v15 = g.inp('_DisableVertColor', False, 0.0)
    v16 = g.math('COMPARE', v15, 0, 1e-05)
    v17 = g.math('SUBTRACT', 1.0, v16)
    v18 = g.mixv(v17, v3, (1, 1, 1))
    v19 = g.mixf(v17, v4, 1)
    v20 = g.inp('_TintColor', True, (1.0, 1.0, 1.0))
    v21 = g.inp('_TintColor_w', False, 1.0)
    v22 = g.vmath('MULTIPLY', v20, v18)
    v23 = g.math('MULTIPLY', v21, v19)
    v24 = g.inp('_TintColorIntensity', False, 1.0)
    v25 = g.bc(v24)
    v26 = g.vmath('MULTIPLY', v22, v25)
    v27 = g.inp('_ExposureWithMiscParams', True, (1.0, 1.0, 1.0))
    v28 = g.inp('_ExposureWithMiscParams_w', False, 1.0)
    v29 = g.sep(v27)
    v30 = g.inp('_IgnorePostExposure', False, 1.0)
    v31 = g.mixf(v30, 1, v29[1])
    v32 = g.bc(v31)
    v33 = g.vmath('MULTIPLY', v26, v32)
    v34 = g.inp('_TintColorAlpha', False, 1.0)
    v35 = g.math('MULTIPLY', v23, v34)
    v36 = g.inp('_MainSwitchUV', False, 0.0)
    v37 = g.group_named('RCE_SelectUV', [('uv', v0), ('screenUV', v14), ('switchMode', v36)])
    v38 = g.inp('_MainTexUVRotate', False, 0.0)
    v39 = g.group_named('RCE_RotateUV', [('uv', v37[0]), ('degrees', v38)])
    v40 = g.inp('_MainTexUVSpeed', True, (0.0, 0.0, 0.0))
    v41 = g.inp('_MainTexUVSpeed_w', False, 0.0)
    v42 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v43 = g.inp('_Time_w', False, 0.0)
    v44 = g.sep(v42)
    v45 = g.sep(v40)
    v46 = g.comb(v45[0], v45[1], 0.0)
    v47 = g.comb(v44[1], v44[1], 0.0)
    v48 = g.vmath('MULTIPLY', v46, v47)
    v49 = g.vmath('ADD', v39[0], v48)
    v50 = g.inp('_BaseTex_ST', True, (1.0, 1.0, 0.0))
    v51 = g.inp('_BaseTex_ST_w', False, 0.0)
    v52 = g.sep(v50)
    v53 = g.comb(v52[0], v52[1], 0.0)
    v54 = g.vmath('MULTIPLY', v49, v53)
    v55 = g.comb(v52[2], v51, 0.0)
    v56 = g.vmath('ADD', v54, v55)
    g.out_('F0_BaseTex_uv', v56, True)
    v57 = g.inp('F0_BaseTex', True, (1.0, 1.0, 1.0))
    v58 = g.inp('F0_BaseTex_alpha', False, 1.0)
    v59 = g.sep(v57)
    v60 = g.inp('_UseMainTexAsAlpha', False, 1.0)
    v61 = g.mixv(v60, v57, (1, 1, 1))
    v62 = g.mixf(v60, v58, v59[0])
    v63 = g.mixv(1, v61, v57)
    v64 = g.mixf(1, v62, v58)
    v65 = g.vmath('MULTIPLY', v33, v63)
    v66 = g.math('MULTIPLY', v35, v64)
    v67 = g.inp('_UseBlend', False, 0.0)
    v68 = g.inp('_BlendTex_ST', True, (1.0, 1.0, 0.0))
    v69 = g.inp('_BlendTex_ST_w', False, 0.0)
    v70 = g.sep(v68)
    v71 = g.comb(v70[0], v70[1], 0.0)
    v72 = g.vmath('MULTIPLY', v49, v71)
    v73 = g.comb(v70[2], v69, 0.0)
    v74 = g.vmath('ADD', v72, v73)
    g.out_('F1_BlendTex_uv', v74, True)
    v75 = g.inp('F1_BlendTex', True, (0.0, 0.0, 0.0))
    v76 = g.inp('F1_BlendTex_alpha', False, 1.0)
    v77 = g.inp('_BlendTint', True, (1.0, 1.0, 1.0))
    v78 = g.inp('_BlendTint_w', False, 1.0)
    v79 = g.vmath('MULTIPLY', v75, v77)
    v80 = g.vmath('MULTIPLY', v65, v79)
    v81 = g.mixv(v67, v65, v80)
    v82 = g.inp('_UseMask', False, 0.0)
    v83 = g.inp('_MaskTex_ST', True, (1.0, 1.0, 0.0))
    v84 = g.inp('_MaskTex_ST_w', False, 0.0)
    v85 = g.sep(v83)
    v86 = g.comb(v85[0], v85[1], 0.0)
    v87 = g.vmath('MULTIPLY', v49, v86)
    v88 = g.comb(v85[2], v84, 0.0)
    v89 = g.vmath('ADD', v87, v88)
    g.out_('F2_MaskTex_uv', v89, True)
    v90 = g.inp('F2_MaskTex', True, (1.0, 1.0, 1.0))
    v91 = g.inp('F2_MaskTex_alpha', False, 1.0)
    v92 = g.sep(v90)
    v93 = g.math('MULTIPLY', v66, v92[0])
    v94 = g.mixf(v82, v66, v93)
    v95 = g.inp('_UseDissolve', False, 0.0)
    v96 = g.inp('_DissolveTex_ST', True, (1.0, 1.0, 0.0))
    v97 = g.inp('_DissolveTex_ST_w', False, 0.0)
    v98 = g.sep(v96)
    v99 = g.comb(v98[0], v98[1], 0.0)
    v100 = g.vmath('MULTIPLY', v49, v99)
    v101 = g.comb(v98[2], v97, 0.0)
    v102 = g.vmath('ADD', v100, v101)
    g.out_('F3_DissolveTex_uv', v102, True)
    v103 = g.inp('F3_DissolveTex', True, (1.0, 1.0, 1.0))
    v104 = g.inp('F3_DissolveTex_alpha', False, 1.0)
    v105 = g.sep(v103)
    v106 = g.inp('_DissolveAmount', False, 0.0)
    v107 = g.math('SUBTRACT', v105[0], v106)
    v108 = g.inp('_DissolveEdgeColor', True, (1.0, 1.0, 1.0))
    v109 = g.inp('_DissolveEdgeColor_w', False, 1.0)
    v110 = g.inp('_DissolveEdgeWidth', False, 0.1)
    v111 = g.math('MAXIMUM', v110, 0.0001)
    v112 = g.math('DIVIDE', v107, v111)
    v113 = g.clampn(v112)
    v114 = g.math('SUBTRACT', 1, v113)
    v115 = g.bc(v114)
    v116 = g.vmath('MULTIPLY', v108, v115)
    v117 = g.vmath('ADD', v81, v116)
    v118 = g.math('LESS_THAN', v107, 0)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MULTIPLY', v94, v119)
    v121 = g.mixv(v95, v81, v117)
    v122 = g.mixf(v95, v94, v120)
    v123 = g.inp('_ExpThreshold', False, 1.0)
    v124 = g.inp('_ExpIntensity', False, 0.0)
    v125 = g.group_named('RCE_ExpBoost', [('rgb', v121), ('threshold', v123), ('intensity', v124)])
    v126 = g.inp('_UseGridLine', False, 0.0)
    v127 = g.inp('_GridLineWidth', False, 1.0)
    v128 = g.group_named('RCE_GridLine', [('uv', v0), ('width', v127), ('ddxUV', (0.0, 0.0, 0.0)), ('ddyUV', (0.0, 0.0, 0.0))])
    v129 = g.math('MULTIPLY', v122, v128[0])
    v130 = g.mixf(v126, v122, v129)
    v131 = g.inp('_UseFresnel', False, 0.0)
    v132 = g.inp('_FresnelBias', False, 0.0)
    v133 = g.inp('_FresnelPower', False, 1.0)
    v134 = g.inp('_FresnelFlip', False, 0.001)
    v135 = g.inp('_FresnelAffectOpacity', False, 1.0)
    v136 = g.group_named('RCE_Fresnel', [('normalWS', v9), ('viewDirectionWS', v13), ('bias', v132), ('power', v133), ('flip', v134), ('affectOpacity', v135)])
    v137 = g.inp('_FresnelColor', True, (1.0, 1.0, 1.0))
    v138 = g.inp('_FresnelColor_w', False, 1.0)
    v139 = g.bc(v136[0])
    v140 = g.vmath('MULTIPLY', v137, v139)
    v141 = g.vmath('ADD', v125[0], v140)
    v142 = g.math('MULTIPLY', v136[0], v135)
    v143 = g.math('ADD', v130, v142)
    v144 = g.clampn(v143)
    v145 = g.mixv(v131, v125[0], v141)
    v146 = g.mixf(v131, v130, v144)
    v147 = g.vmath('SUBTRACT', v8, v11)
    v148 = g.vmath('LENGTH', v147)
    v149 = g.inp('_UseNearCameraFade', False, 0.0)
    v150 = g.inp('_NearCameraFadeDistanceStart', False, 0.001)
    v151 = g.inp('_NearCameraFadeDistanceEnd', False, 10.0)
    v152 = g.inp('_NearCameraFadeDistanceStart2', False, 120.0)
    v153 = g.inp('_NearCameraFadeDistanceEnd2', False, 100.0)
    v154 = g.group_named('RCE_NearCameraFade', [('viewDepth', v148), ('enable', v149), ('start', v150), ('end', v151), ('start2', v152), ('end2', v153)])
    v155 = g.math('MULTIPLY', v146, v154[0])
    v156 = g.clampn(v155)
    v157 = g.vmath('DOT_PRODUCT', v145, (0.2126729, 0.7151522, 0.072175))
    v158 = g.comb(v157, v157, v157)
    v159 = g.inp('_VFXParams1', True, (1.0, 1.0, 1.0))
    v160 = g.inp('_VFXParams1_w', False, 1.0)
    v161 = g.mixv(v160, v158, v145)
    v162 = g.vmath('MULTIPLY', v161, v159)
    v163 = g.vmath('NORMALIZE', v9)
    v164 = g.inp('_UseAlphaTest', False, 0.0)
    v165 = g.inp('_AlphaClipThreshold', False, 0.5)
    v166 = g.math('SUBTRACT', v156, v165)
    v167 = g.math('LESS_THAN', v166, 0.0)
    v168 = g.math('SUBTRACT', 1.0, v167)
    v169 = g.math('MULTIPLY', 1.0, v168)
    v170 = g.mixf(v164, 1.0, v169)
    g.out_('ret_gBuffer0', v162, True)
    g.out_('ret_gBuffer0_w', v156, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v162, True)
    g.out_('ret_color_w', v156, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v170, False)


SHARED_GROUPS = [
    ('RCE_ExpBoost', build_RCE_ExpBoost),
    ('RCE_Fresnel', build_RCE_Fresnel),
    ('RCE_GridLine', build_RCE_GridLine),
    ('RCE_NearCameraFade', build_RCE_NearCameraFade),
    ('RCE_RotateUV', build_RCE_RotateUV),
    ('RCE_SelectUV', build_RCE_SelectUV),
]

PARTS = {
    'VFXBaseV2': ('Ruri Endfield Effect VFXBaseV2', build_Ruri_Endfield_Effect_VFXBaseV2),
    'VFXDsWrite': ('Ruri Endfield Effect VFXBaseV2', build_Ruri_Endfield_Effect_VFXBaseV2),
    'VFXDistanceField': ('Ruri Endfield Effect VFXDistanceField', build_Ruri_Endfield_Effect_VFXDistanceField),
}

CASCADE = {
    'VFXBaseV2': 2,
    'VFXDsWrite': 2,
    'VFXDistanceField': 2,
}

FETCHES = {
    'VFXBaseV2': [
        {'sock': 'F0_MainTex', 'slot': '_MainTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BlendTex', 'slot': '_BlendTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_MaskTex', 'slot': '_MaskTex', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_DissolveTex', 'slot': '_DissolveTex', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'VFXDsWrite': [
        {'sock': 'F0_MainTex', 'slot': '_MainTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BlendTex', 'slot': '_BlendTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_MaskTex', 'slot': '_MaskTex', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_DissolveTex', 'slot': '_DissolveTex', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'VFXDistanceField': [
        {'sock': 'F0_BaseTex', 'slot': '_BaseTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_BlendTex', 'slot': '_BlendTex', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_MaskTex', 'slot': '_MaskTex', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_DissolveTex', 'slot': '_DissolveTex', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
}

ZONES = {
    'VFXBaseV2': [
    ],
    'VFXDsWrite': [
    ],
    'VFXDistanceField': [
    ],
}

CAPABILITIES = {
    'VFXBaseV2': [
    ],
    'VFXDsWrite': [
    ],
    'VFXDistanceField': [
    ],
}

DEFAULT_PART = 'VFXBaseV2'
STAMP = 'a29414fcb84b3fc8'
STAMP_KEY = 'ruri_uber_stamp'


VERTEX_PARTS = {
}

KNOWN_PARTS = {'VFXBaseV2', 'VFXDsWrite', 'VFXDistanceField'}
VTX_MODIFIER = 'Ruri Endfield Effect Vertex'
VTX_TREE_PREFIX = 'Ruri Endfield Effect Vertex '
OUTLINE_TEMPLATE = 'Ruri Endfield Effect Outline'
CLONE_V_PREFIX = 'Ruri Endfield Effect V '
CLONE_O_PREFIX = 'Ruri Endfield Effect O '
RIG_BASIS_BONE = ''
RIG_BASIS_ATTR = ''
RIG_BASIS_PARTS = set()

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
    mat = bpy.data.materials.get('Ruri_EndfieldEffect_Uber')
    if mat is None:
        mat = bpy.data.materials.new('Ruri_EndfieldEffect_Uber')
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
    'VFXBaseV2': {'id': 0, 'transparent': True, 'shader': 'HGRP/Effect/VFXBaseV2', 'aliases': ('HGRP/Effect/VFXBaseBatched', ), 'discriminator': None},
    'VFXDsWrite': {'id': 1, 'transparent': True, 'shader': 'HGRP/Effect/VFXDsWrite', 'aliases': (), 'discriminator': None},
    'VFXDistanceField': {'id': 2, 'transparent': True, 'shader': 'HGRP/Effect/VFXDistanceField', 'aliases': (), 'discriminator': None},
}
NON_SHADING_SHADERS = ()

CULL_PROPERTY = '_CullMode'
CULL_FIXED = 2
GLOBALS = {
    '_Time': {'role': 'time', 'type': 'VECTOR4', 'default': (0.0, 0.0, 0.0), 'default_w': 0.0},
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
    name = props.name or 'Ruri_EndfieldEffect_Uber'
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
    put('_EffectPartID', float(part_id))
    # 透明特效 part 的游戏 shader 靠 pass 状态透明、不声明 _SurfaceType,补上内核开关。
    if meta['transparent']:
        put('_SurfaceType', 1.0)
    try:
        mat.surface_render_method = 'BLENDED' if meta['transparent'] else 'DITHERED'
    except Exception:
        pass
    mat['ruri_uber_part'] = part_name
    # 栈身份:一个会话里同时装着 N 个生成栈,而 ruri_uber_part 每个栈都写 ——
    # 材质面板要认领得准(只画选中材质那一个栈的参数),判据只能是这一枚烙印。
    mat['ruri_uber_stack'] = PANEL_KEY
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


# ============================ 材质参数接口 ============================
# 参数面 = 从 C# 声明([ShaderProperty]/[MaterialTexture]/[ShaderPropertyHeader])反射派生的
# **接口**,不是对节点树的遍历——平坦、分组、带量程、带功能门。此表是它的逐字投影。
PANEL_KEY = 'ruri_effect_uber_endfield'
PANEL_TITLE = 'Ruri_EndfieldEffect_Uber 参数'
INTERFACE = [
    {'name': '基础', 'gate': None, 'rows': [
        {'name': '_MainTex', 'label': 'Main Tex', 'kind': 'TEXTURE'},
        {'name': '_BaseTex', 'label': 'Base Tex', 'kind': 'TEXTURE'},
        {'name': '_MaskTex', 'label': 'Mask Tex', 'kind': 'TEXTURE'},
        {'name': '_BlendTex', 'label': 'Blend Tex', 'kind': 'TEXTURE'},
        {'name': '_DissolveTex', 'label': 'Dissolve Tex', 'kind': 'TEXTURE'},
        {'name': '_BaseMap', 'label': 'Albedo', 'kind': 'TEXTURE', 'st_node': 'RuriBaseMapST'},
        {'name': '_BumpMap', 'label': 'Normal Map', 'kind': 'TEXTURE'},
        {'name': '_RampMap', 'label': 'Diffuse Ramp Map', 'kind': 'TEXTURE'},
        {'name': '_EmissionMap', 'label': 'Emission', 'kind': 'TEXTURE'},
        {'name': '_OutlineMask', 'label': 'Outline Mask', 'kind': 'TEXTURE'},
        {'name': '_HairBrowMask', 'label': 'Hair Brow Mask', 'kind': 'TEXTURE'},
        {'name': '_RMOSMap', 'label': 'RMOS Map (R=Rough G=Metal B=Occ A=Spec)', 'kind': 'TEXTURE'},
        {'name': '_RefractTex', 'label': '自定义折射贴图', 'kind': 'TEXTURE'},
        {'name': '_WaterNormalMap', 'label': '水面法线贴图', 'kind': 'TEXTURE'},
        {'name': '_WaterCausticMap', 'label': '水波纹贴图', 'kind': 'TEXTURE'},
        {'name': '_DisplacementTex', 'label': '置换贴图', 'kind': 'TEXTURE'},
        {'name': '_IceNormalMap', 'label': '冰块法线贴图', 'kind': 'TEXTURE'},
        {'name': '_IceOpacityMap', 'label': '冰块不透明度贴图', 'kind': 'TEXTURE'},
        {'name': '_BaseColorMap', 'label': 'Base Color Map', 'kind': 'TEXTURE'},
        {'name': '_MaskMap', 'label': 'Mask Map', 'kind': 'TEXTURE'},
        {'name': '_EmissiveMap', 'label': 'Emissive Map', 'kind': 'TEXTURE'},
        {'name': '_LayerBlendMaskMap', 'label': 'Layer Blend Mask (R)', 'kind': 'TEXTURE'},
        {'name': '_Layer1BaseMap', 'label': 'Layer1 Base (RGB)', 'kind': 'TEXTURE'},
        {'name': '_Layer1BumpMap', 'label': 'Layer1 NRO (RG=N B=Rough A=AO)', 'kind': 'TEXTURE'},
        {'name': '_BaseHeightMap', 'label': 'Base Height (R)', 'kind': 'TEXTURE'},
        {'name': '_MatcapMap', 'label': 'Matcap Map', 'kind': 'TEXTURE'},
        {'name': '_MacroNormalMap', 'label': 'Macro Normal', 'kind': 'TEXTURE'},
        {'name': '_DetailMap', 'label': 'Detail Map (RG=N B=Rough A)', 'kind': 'TEXTURE'},
        {'name': '_OffsetTex', 'label': 'Offset Tex (R)', 'kind': 'TEXTURE'},
        {'name': '_OffsetMaskTex', 'label': 'Offset Mask Tex (R)', 'kind': 'TEXTURE'},
        {'name': '_SubsurfaceMap', 'label': 'Subsurface Thickness (R)', 'kind': 'TEXTURE'},
        {'name': '_ParallaxMap', 'label': 'Parallax Map', 'kind': 'TEXTURE'},
        {'name': '_ILMMap', 'label': 'ILM Map', 'kind': 'TEXTURE'},
        {'name': '_MetalMatcap', 'label': 'Metal Matcap', 'kind': 'TEXTURE'},
        {'name': '_AddMatcapMap', 'label': 'Add Matcap', 'kind': 'TEXTURE'},
        {'name': '_BurstTex', 'label': '爆发效果贴图 Burst Texture', 'kind': 'TEXTURE'},
        {'name': '_ColorRamp', 'label': 'Color Ramp', 'kind': 'TEXTURE'},
        {'name': '_DenierMap', 'label': 'Denier Map', 'kind': 'TEXTURE'},
        {'name': '_DyeingTex', 'label': 'Dyeing Map', 'kind': 'TEXTURE'},
        {'name': '_HighLightTex1', 'label': 'HightLightTex 1', 'kind': 'TEXTURE'},
        {'name': '_HighLightTex2', 'label': 'HightLightTex 2', 'kind': 'TEXTURE'},
        {'name': '_HighLightTex3', 'label': 'HightLightTex 3', 'kind': 'TEXTURE'},
        {'name': '_IlmMap', 'label': 'ILM / Face SDF Map', 'kind': 'TEXTURE'},
        {'name': '_Colortop', 'label': 'Color Top', 'kind': 'COLOR', 'size': 4, 'default': [0.02, 0.04, 0.09, 1.0]},
        {'name': '_Colorbottom', 'label': 'Color Bottom', 'kind': 'COLOR', 'size': 4, 'default': [0.27, 0.16, 0.37, 1.0]},
        {'name': '_BottomPos', 'label': 'Gradient Offset', 'kind': 'SLIDER', 'size': 1, 'min': -2.0, 'max': 2.0, 'default': [0.0]},
        {'name': '_BottonWidth', 'label': 'Gradient Width', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_RimColor', 'label': 'Rim Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_RimPow', 'label': 'Rim Width (For Color)', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_RimPow2', 'label': 'Rim Width (For Alpha)', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_RimSterngth', 'label': 'Rim Strength', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_Opaqueness', 'label': 'Total Opaqueness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
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
        {'name': '_ScenePartID', 'label': 'Scene Part (0=Lit 1=Forward 2=Transparent 3=Effect 4=EffectBlend 5=HLod 6=Unlit)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_UseReceiveShadows', 'label': 'Allow Receive Shadows', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_UseMaskMap', 'label': 'Use Mask Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_TessellationFactor', 'label': 'Tessellation Factor', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 64.0, 'default': [16.0]},
        {'name': '_TessellationMinDist', 'label': 'Min Distance', 'kind': 'VALUE', 'size': 1, 'default': [10.0]},
        {'name': '_TessellationMaxDist', 'label': 'Max Distance', 'kind': 'VALUE', 'size': 1, 'default': [50.0]},
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
        {'name': '_BaseColorTintCover', 'label': 'Base Color Tint Cover', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_BaseColorBrighterScale', 'label': 'Base Color Brighter', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_RoughnessMin', 'label': 'Roughness Min', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_RoughnessMax', 'label': 'Roughness Max', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_OcclusionStrength', 'label': 'Occlusion Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_TwoSidedNormal', 'label': 'Two Sided Flip Backface Normal', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_BaseTextureMapCount', 'label': 'Base Texture Map Count (0=Three)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_UseCustomIBL', 'label': 'Use Custom IBL', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_CustomIBLIntensity', 'label': 'Custom IBL Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_UseEmissiveMap', 'label': 'Use Emissive Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmissiveColor', 'label': 'Emissive Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EmissiveMaskChannel', 'label': 'Emissive Mask Channel', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_AlbedoAffectEmissive', 'label': 'Albedo Not Affect Emissive', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_UseThinFilm', 'label': 'Use Thin Film', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ThinFilmIntensity', 'label': 'Thin Film Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_LayerBlend', 'label': 'Use Layer Blend', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_LayerBlendType', 'label': 'Blend Type (0=VtxColor 1=Mask 2=WorldTop)', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_LayerBlendMaskType', 'label': 'Mask Source (0=MaskR else=NROA)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_LayerBlendMaskUVType', 'label': 'Mask UV (0=UV1 1=UV0)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_LayerBlendUVType', 'label': 'Layer UV (0=UV0 1=WorldXZ 2=UV2)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_TopBlendThreshold', 'label': 'Top Blend Threshold', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_TopBlendSmoothness', 'label': 'Top Blend Smoothness', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [0.5]},
        {'name': '_TopBlendWithBumpMap', 'label': 'Top Blend With Bump', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_Layer1Tilling', 'label': 'Layer1 Tilling', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_Layer1TintColor', 'label': 'Layer1 Tint', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_Layer1BumpScale', 'label': 'Layer1 Normal Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_Layer1BaseNormalIntensity', 'label': 'Layer1 Base Normal Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_LayerMetallicType', 'label': 'Layer Metallic (0=Slider else=BaseA)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_Layer1Metallic', 'label': 'Layer1 Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_Layer1AOStrength', 'label': 'Layer1 AO Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_Layer1Saturation', 'label': 'Layer1 Saturation', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 0.0, 'default': [0.0]},
        {'name': '_Layer1ColorBrighterScale', 'label': 'Layer1 Brighter', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_LayerBlendHeight', 'label': 'Layer Height Blend', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_LayerBlendHeightTransition', 'label': 'Height Transition', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [1.0]},
        {'name': '_LayerBlendNoise', 'label': 'Layer Noise Blend', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_LayerBlendNoiseLevel', 'label': 'Noise Level', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_LayerBlendNoiseThreshold', 'label': 'Noise Threshold', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_LayerBlendNoiseNormalStrength', 'label': 'Noise Normal Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_LayerBlendNoiseNormalSmoothness', 'label': 'Noise Normal Smoothness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [1.0]},
        {'name': '_LayerBlendVerticalFlowThreshold', 'label': 'Vertical Flow Threshold', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableEmissiveAnimSweep', 'label': 'Emissive Anim Sweep', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmissiveSweepSpeed', 'label': 'Sweep Speed', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 20.0, 'default': [3.0]},
        {'name': '_EmissiveSweepInterval', 'label': 'Sweep Interval', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 20.0, 'default': [3.0]},
        {'name': '_EmissiveSweepWidth', 'label': 'Sweep Width', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 10.0, 'default': [0.8]},
        {'name': '_EmissiveSweepFalloff', 'label': 'Sweep Falloff', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_EmissiveSweepRandom', 'label': 'Sweep Random Per Position', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmissiveSweepAlbedoScale', 'label': 'Sweep Albedo Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [0.0]},
        {'name': '_PlanarReflection', 'label': 'Use Planar Reflection', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_PlanarReflectionTint', 'label': 'Planar Reflection Tint', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_EnableUVAnimation', 'label': 'Enable UV Animation', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UVAnimationSpeed', 'label': 'UV Anim Speed (xy=UV0 zw=UV1)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_EnableEmissiveAnim', 'label': 'Enable Emissive Anim', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmissiveAnimSpeed', 'label': 'Emissive Anim Speed', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 80.0, 'default': [0.0]},
        {'name': '_EmissiveAnimInterval', 'label': 'Emissive Anim Interval', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 10.0, 'default': [1.0]},
        {'name': '_EmissiveMinBrightness', 'label': 'Emissive Min Brightness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.5, 'default': [0.0]},
        {'name': '_EmissiveAnimRandom', 'label': 'Emissive Anim Random', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EnableEmissiveAnimFlicker', 'label': 'Enable Emissive Flicker', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_BrightDarkRatio', 'label': 'Flicker Bright/Dark Ratio', 'kind': 'SLIDER', 'size': 1, 'min': 0.001, 'max': 0.99, 'default': [0.15]},
        {'name': '_EnableRandomFlicker', 'label': 'Flicker Random Per Object', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_EnableMatcap', 'label': 'Enable Matcap', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_MatcapMapStrength', 'label': 'Matcap Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_UseMacroNormalMap', 'label': 'Use Macro Normal', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_MacroNormalMapScale', 'label': 'Macro Normal Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [1.0]},
        {'name': '_EnableDetailMap', 'label': 'Enable Detail Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DetailMode', 'label': 'Detail Mode (0=AlbedoTint+R 1=R+AO)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_DetailMaskMode', 'label': 'Detail Mask (0=All 1=DetailA 2=BaseA)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_DetailNormalIntensity', 'label': 'Detail Normal Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_DetailOverlayColor', 'label': 'Detail Overlay Color', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_DetailBaseColorBrighterScale', 'label': 'Detail Brighter', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_DetailPBRIntensity', 'label': 'Detail PBR Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_DetailFalloffStart', 'label': 'Detail Falloff Start', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 800.0, 'default': [750.0]},
        {'name': '_DetailFalloffEnd', 'label': 'Detail Falloff End', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 800.0, 'default': [800.0]},
        {'name': '_UseVertexOffset', 'label': 'Use Vertex Offset', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_OffsetSpeed', 'label': 'Offset Speed (xy=time zw=scroll)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_OffsetDir', 'label': 'Offset Dir (xyz axis)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_OffsetSwitchDir', 'label': 'Offset Space (0=Obj 1=World 2=Normal)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_OffsetIntensity', 'label': 'Offset Intensity', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_Bi_Offset', 'label': 'Bi-Directional Offset', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_OffsetUVSet', 'label': 'Offset UV (0=UV0 1=UV1)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_UseVertexColorMask', 'label': 'Use VertexColor.a Mask', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseVertexOffsetMask', 'label': 'Use Offset Mask Tex', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_OffsetMaskSpeed', 'label': 'Offset Mask Speed', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 0.0]},
        {'name': '_OffsetMaskPower', 'label': 'Offset Mask Power', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 5.0, 'default': [0.0]},
        {'name': '_EnableTriChannelMask', 'label': 'Enable Tri-Channel Mask (uses MaskMap RGB)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_MaskAlbedoR', 'label': 'Mask R Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 1.0]},
        {'name': '_MaskAlbedoG', 'label': 'Mask G Color', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 1.0, 0.0, 1.0]},
        {'name': '_MaskAlbedoB', 'label': 'Mask B Color', 'kind': 'COLOR', 'size': 4, 'default': [0.0, 0.0, 1.0, 1.0]},
        {'name': '_MaskRScale', 'label': 'Mask R Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [0.0]},
        {'name': '_MaskGScale', 'label': 'Mask G Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [0.0]},
        {'name': '_MaskBScale', 'label': 'Mask B Scale', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [0.0]},
        {'name': '_MaskROffset', 'label': 'Mask R Offset', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MaskGOffset', 'label': 'Mask G Offset', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MaskBOffset', 'label': 'Mask B Offset', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MaskRoghnessR', 'label': 'Mask R Roughness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.25]},
        {'name': '_MaskRoghnessG', 'label': 'Mask G Roughness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.25]},
        {'name': '_MaskRoghnessB', 'label': 'Mask B Roughness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.25]},
        {'name': '_MaskMetallicR', 'label': 'Mask R Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MaskMetallicG', 'label': 'Mask G Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MaskMetallicB', 'label': 'Mask B Metallic', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableSubsurface', 'label': 'Enable Subsurface', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SubsurfaceShadingMode', 'label': 'SSS Mode (0=Default 1=BaseColor)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_MinSubsurfaceThickness', 'label': 'Min Thickness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_MaxSubsurfaceThickness', 'label': 'Max Thickness', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_SubsurfaceWrapNoLBias', 'label': 'Wrap NoL Bias', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [0.0]},
        {'name': '_SubsurfaceIndirect', 'label': 'Subsurface Indirect', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_UseSubsurfaceThicknessMap', 'label': 'Use Thickness Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SubsurfaceThicknessMapChannel', 'label': 'Thickness Channel', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_EnableParallaxMap', 'label': 'Enable Parallax Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_ParallaxMappingType', 'label': 'Parallax Mode (0=Emissive 1=PBR)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_ParallaxStrength', 'label': 'Parallax Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_ParallaxTilling', 'label': 'Parallax Tilling', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 20.0, 'default': [1.0]},
        {'name': '_ParallaxColor', 'label': 'Parallax Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_ParallaxColorDark', 'label': 'Parallax Color Dark', 'kind': 'HDRCOLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_EmissionTint', 'label': 'Emission Tint (x Albedo)', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_SubsurfaceColor', 'label': 'Subsurface Color', 'kind': 'COLOR', 'size': 4, 'default': [0.8, 0.8, 0.8, 1.0]},
        {'name': '_ThinFilmWeight', 'label': 'Thin Film Weight', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_ThinFilmThickness', 'label': 'Thin Film Thickness (um)', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [0.5]},
        {'name': '_ThinFilmIOR', 'label': 'Thin Film IOR', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 3.0, 'default': [1.4]},
        {'name': '_PorosityFactorX', 'label': 'Porosity Factor X (x Roughness)', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_PorosityFactorY', 'label': 'Porosity Factor Y', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.4]},
        {'name': '_PorosityFactorZ', 'label': 'Porosity Factor Z (x Metallic)', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 0.0, 'default': [0.0]},
        {'name': '_DisableVerticalFlow', 'label': 'Disable Vertical Flow', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EffectIntensity', 'label': 'Effect Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_HLodFade', 'label': 'HLod Fade', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_EnableNormalMap', 'label': 'Foliage Normal Map Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_Reflectance', 'label': 'Reflectance', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_DiffuseUseVertexNormal', 'label': 'Diffuse Use Vertex Normal', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_BendNormalUpward', 'label': 'Bend Normal Upward', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_Transmission', 'label': 'Transmission', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_TransmissionDistanceFade', 'label': 'Transmission Distance Fade', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_SubsurfaceIntensity', 'label': 'Subsurface Intensity (Foliage)', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableBroadLeafTransmission', 'label': 'Broad Leaf Transmission', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_backfaceGiIntensity', 'label': 'Backface GI Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_FrontfaceIndirectDiffuse', 'label': 'Frontface Indirect Diffuse', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_BackfaceIndirectDiffuse', 'label': 'Backface Indirect Diffuse', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_AoAffectTransmissionStart', 'label': 'AO To Transmission Start', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.99, 'default': [0.0]},
        {'name': '_AoAffectTransmissionRange', 'label': 'AO To Transmission Range', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 0.9, 'default': [0.01]},
        {'name': '_AoAffectSubsurfaceStart', 'label': 'AO To Subsurface Start', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.99, 'default': [0.0]},
        {'name': '_AoAffectSubsurfaceRange', 'label': 'AO To Subsurface Range', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 0.9, 'default': [0.01]},
        {'name': '_FakeDirectionalShadowStrength', 'label': 'Fake Directional Shadow Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 2.0, 'default': [0.0]},
        {'name': '_FakeDirectionalShadowPow', 'label': 'Fake Directional Shadow Pow', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 5.0, 'default': [1.0]},
        {'name': '_OcclusionShadow', 'label': 'Occlusion Affects Shadow', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableVerticalNormalBoostAO', 'label': 'Vertical Normal Boost AO Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VerticalNormalThreshold', 'label': 'Vertical Normal Threshold', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_VerticalNormalBoostAO', 'label': 'Vertical Normal Boost AO', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_VerticalNormalAffectShadow', 'label': 'Vertical Normal Affect Shadow', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableCanopyColorRamp', 'label': 'Canopy Color Ramp Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_CanopyRampStartAtTop', 'label': 'Canopy Ramp Start At Top', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_CanopyRampColor', 'label': 'Canopy Ramp Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_CanopyRampColorBrighterScale', 'label': 'Canopy Ramp Brighter', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_CanopyRampIntensity', 'label': 'Canopy Ramp Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_CanopyRampRange', 'label': 'Canopy Ramp Range', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_CanopyRampTransitionRange', 'label': 'Canopy Ramp Transition', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [0.01]},
        {'name': '_CanopyRampColorCover', 'label': 'Canopy Ramp Cover', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableAoTuneColor', 'label': 'AO Mask Tune Color Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_FlipAoMask', 'label': 'Flip AO Mask', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_AoMaskTuneColor', 'label': 'AO Tune Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_AoMaskTuneColorBrighterScale', 'label': 'AO Tune Brighter', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 3.0, 'default': [1.0]},
        {'name': '_AoMaskTuneColorIntensity', 'label': 'AO Tune Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_AoMaskTuneColorRampStart', 'label': 'AO Tune Ramp Start', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 0.99, 'default': [0.0]},
        {'name': '_AoMaskTuneColorRampRange', 'label': 'AO Tune Ramp Range', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 0.9, 'default': [0.2]},
        {'name': '_AoMaskTuneColorCover', 'label': 'AO Tune Cover', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_EnableBlendColor', 'label': 'Blend Color Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_BlendColor', 'label': 'Blend Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_BlendNormalAdd', 'label': 'Blend Normal Add', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_BlendNormalPower', 'label': 'Blend Normal Power', 'kind': 'SLIDER', 'size': 1, 'min': 0.3, 'max': 15.0, 'default': [1.0]},
        {'name': '_BlendWithVertexNormal', 'label': 'Blend With Vertex Normal', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DirIntensity', 'label': 'Dir Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_DirContrast', 'label': 'Dir Contrast', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [0.2]},
        {'name': '_DirPosition', 'label': 'Dir Position', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_DirRadius', 'label': 'Dir Radius', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [0.1]},
        {'name': '_DirParams', 'label': 'Dir Params', 'kind': 'VECTOR', 'size': 4, 'default': [0.1, 1.0, 0.8, 99.0]},
        {'name': '_MaskOnDiffuse', 'label': 'Mask On Diffuse', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [1.0]},
        {'name': '_MaskOnTransmission', 'label': 'Mask On Transmission', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [1.0]},
        {'name': '_AoIntensity', 'label': 'Foliage AO Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.2]},
        {'name': '_AoContrast', 'label': 'Foliage AO Contrast', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 3.0, 'default': [0.2]},
        {'name': '_AoPosition', 'label': 'Foliage AO Position', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_AoRadius', 'label': 'Foliage AO Radius', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [0.1]},
        {'name': '_AoParams', 'label': 'Foliage AO Params', 'kind': 'VECTOR', 'size': 4, 'default': [0.1, 1.0, 0.8, 99.0]},
        {'name': '_TrunkVertexAoStrength', 'label': 'Trunk Vertex AO Strength', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_EnableTrunkRamp', 'label': 'Trunk Ramp Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_TrunkRampColor', 'label': 'Trunk Ramp Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_TrunkRampIntensity', 'label': 'Trunk Ramp Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_TrunkRampRange', 'label': 'Trunk Ramp Range', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_TrunkRampTransitionRange', 'label': 'Trunk Ramp Transition', 'kind': 'SLIDER', 'size': 1, 'min': 0.01, 'max': 1.0, 'default': [0.01]},
        {'name': '_EnableVertColorEmissive', 'label': 'Vertex Color Emissive Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VertColorEmissiveChannelVector', 'label': 'Vertex Color Emissive Channel Vector', 'kind': 'VECTOR', 'size': 4, 'default': [1.0, 0.0, 0.0, 0.0]},
        {'name': '_VertColorEmissiveBias', 'label': 'Vertex Color Emissive Bias', 'kind': 'SLIDER', 'size': 1, 'min': -1.0, 'max': 1.0, 'default': [0.0]},
        {'name': '_VertColorEmissiveFlip', 'label': 'Vertex Color Emissive Flip', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VertColorEmissiveColor', 'label': 'Vertex Color Emissive Color', 'kind': 'HDRCOLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_VertColorEmissiveAlbedoAffect', 'label': 'Vertex Color Emissive Albedo Affect', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_EnableEmissiveMap', 'label': 'Foliage Emissive Map Enable', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmissiveColorR', 'label': 'Emissive Color R', 'kind': 'HDRCOLOR', 'size': 4, 'default': [0.0, 0.0, 0.0, 1.0]},
        {'name': '_CrossCardViewCulling', 'label': 'Cross Card View Culling', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_CrossCardViewCullingThreshold', 'label': 'Cross Card Threshold', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.4]},
        {'name': '_CrossCardViewCullingFadeValue', 'label': 'Cross Card Fade Value', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
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
        {'name': '_TransmissionColor', 'label': 'Transmission Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 0.0]},
        {'name': '_UseVertexColor', 'label': 'Use Vertex Color (Voxel Albedo)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseVoxelAtlas', 'label': 'Use Voxel Atlas (Block Textures)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VoxelEmissionScale', 'label': 'Voxel Emission Scale', 'kind': 'VALUE', 'size': 1, 'default': [4.0]},
        {'name': '_RuriHeldRadiance', 'label': 'Held Item Radiance (Eye Lightmap)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_RuriHeldLightLevels', 'label': 'Held Eye Light Levels (Block, Sky)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 15.0, 0.0, 0.0]},
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


def panel_claims(mat):
    """这张材质是不是本栈建的。判据 = 建图时烙的**栈身份**,不是 ruri_uber_part:
    那个键每个生成栈都写,拿它当判据就是一张材质被 N 个栈同时认领(N 个面板并排堆着,
    实锤过)。没有烙印的是旧产物 —— 宿主会喊重新导入,这里不硬认。"""
    return mat is not None and mat.get('ruri_uber_stack') == PANEL_KEY


def panel_write(mat, row, value):
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


def panel_write_image(mat, row, image):
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


def panel_write_st(mat, row, tiling, offset):
    """平铺/偏移 → _ST socket 对 + 顶点期 uv 变换节点 + 描边 mask ST(同一真值三消费面)。"""
    name = row['name']
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


def panel_read(mat):
    """图 + 快照 → 当前值。读路径全在本栈这一侧:值住在级联实例 socket 与 ruri_uber_*
    快照里,那是本栈的形态。返回
    {'values': {名: 标量/元组}, 'images': {名: Image|None}, 'st': {名: (t,t,o,o)}}。"""
    values, images, st_out = {}, {}, {}
    insts = _panel_insts(mat)
    if not insts:
        return {'values': values, 'images': images, 'st': st_out}
    first = insts[0]
    floats = dict(mat.get('ruri_uber_floats') or {})
    st = {k: list(v) for k, v in dict(mat.get('ruri_uber_st') or {}).items()}
    colors = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()}
    bound = dict(mat.get('ruri_uber_images') or {})
    for row in _panel_rows():
        name = row['name']
        kind = row['kind']
        if kind == 'TEXTURE':
            img = bpy.data.images.get(bound.get(name, ''))
            if img is None:
                img = _panel_bound_image(mat, name)
            images[name] = img
            value = st.get(name)
            if value is None:
                sock = first.inputs.get(name + '_ST')
                if sock is not None and not sock.is_linked:
                    tail = first.inputs.get(name + '_ST_w')
                    raw = sock.default_value
                    value = [raw[0], raw[1], raw[2],
                             tail.default_value if tail is not None else 0.0]
            if value is not None:
                st_out[name] = (value[0], value[1], value[2], value[3])
            continue
        sock = first.inputs.get(name)
        if kind in ('SWITCH', 'VALUE', 'SLIDER', 'INT'):
            value = floats.get(name)
            if value is None and sock is not None and not sock.is_linked and sock.type == 'VALUE':
                value = sock.default_value
            if value is None:
                continue
            values[name] = (bool(value > 0.5) if kind == 'SWITCH'
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
            values[name] = tuple(float(x) for x in spread)
    return {'values': values, 'images': images, 'st': st_out}


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


def panel_rows(mat):
    """这张材质**真正有**的行,按 INTERFACE 分组给出:变体折叠掉的死支参数在图上没有
    socket,判据是图自己而不是另一张表。贴图行带 has_st(有没有平铺/偏移可调)。
    顶点腿节点要扫全部对象,所以宿主会缓存本函数结果、在换图/回读时作废 ——
    这里只负责答得准。"""
    insts = _panel_insts(mat)
    slots = _panel_slots(mat)
    out = []
    for group in INTERFACE:
        rows = []
        for row in group['rows']:
            if row['kind'] == 'TEXTURE':
                if row['name'] not in slots:
                    continue
                has_st = bool(row.get('st_node')) or any(
                    grp.inputs.get(row['name'] + '_ST') is not None for grp in insts)
                row = dict(row, has_st=has_st)
            elif not any(grp.inputs.get(row['name']) is not None for grp in insts):
                continue
            rows.append(row)
        if rows:
            out.append({'name': group['name'], 'gate': group['gate'], 'rows': rows})
    return out


def register():
    # 宿主注册表按**绝对路径**导入(配方给的名字):相对导入会绑死部署深度,
    # 而本文件必须能被脱包 spec_from_file_location 直接加载(建图/压测探针靠它)。
    # 材质图/顶点腿/兑现面重接都在这里自注册 —— 消费方只调宿主注册表,门面不写一行逻辑。
    import importlib
    import sys
    host = importlib.import_module('RuriRipperImporter.material_builder')
    host.register_graph_provider(provider)
    host.register_vertex_stage(apply_vertex_stage)
    host.register_capability_rewire(rewire_capabilities)
    host.register_light_table_refresh(refresh_light_tables)
    # 材质参数面板:本模块只交出读写路径与接口表,面板本体由宿主统一画一个
    # (每栈各画一个 = N 个面板并排堆在属性页里,而用户只关心选中网格的那张材质)。
    host.register_material_panel(sys.modules[__name__])


def unregister():
    import importlib
    import sys
    host = importlib.import_module('RuriRipperImporter.material_builder')
    host.unregister_material_panel(sys.modules[__name__])
    host.unregister_graph_provider(provider)
    host.unregister_vertex_stage(apply_vertex_stage)
    host.unregister_capability_rewire(rewire_capabilities)
    host.unregister_light_table_refresh(refresh_light_tables)


# 导入即清理过时预设库(见 _prune_stale_libraries):要在 register() 之外,
# 因为脱包直接加载本文件的探针也该把目录扫干净。
_prune_stale_libraries()
if __name__ == '__main__':
    build_root()
