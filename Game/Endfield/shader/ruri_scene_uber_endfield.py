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
#  · **part 变体**:_ScenePartID 生成期折叠,每 part 一套入口 + 独立依赖闭包
#    (PARTS 表)。消费方按材质 part 选 PARTS[part],只建该闭包 —— 死支零节点;
#  · keyword 折叠: _SCENE_ENDFIELD, _ADDITIONAL_LIGHTS, _RURI_FORWARD_PASS(前向单趟);
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
            _set_colorspace(img, 'Non-Color')
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


def _light_visible(obj):
    """灯算不算数。`visible_get()` 要走 view layer 求值 —— 刚 link 进场景、依赖图这一拍
    还没评估时它会答 False(实测:头一张材质因此按"无太阳"建,之后才跟上,同一场景先后
    两次渲染不一致)。求值不出就退回对象自己的隐藏旗标,那是不依赖任何求值的真值。"""
    try:
        return obj.visible_get()
    except (RuntimeError, ReferenceError):
        return not obj.hide_viewport


def _scene_sun():
    """场景里的主方向光 = 第一盏 SUN(按名字排序取定,免得场景枚举序变了图就跟着变)。"""
    suns = [o for o in bpy.context.scene.objects
            if o.type == 'LIGHT' and o.data.type == 'SUN' and _light_visible(o)]
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


LIGHT_TABLE = 'RuriLightTable'
LIGHT_TABLE_ROWS = 4
LIGHT_TABLE_COLS = 64


def _linear_to_srgb(c):
    """线性 → sRGB 显示值。`generated_color` 是按显示空间解释的,要它在 sRGB 图上
    生成缓冲值 c,就得给它 c 的 sRGB 编码(实测 gen=0.7354 → 缓冲 0.5000)。"""
    c = float(c)
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _image_stored(image):
    """把图的当前像素**存进 .blend**,并借此清掉 dirty 标记。

    🔴 写过 `pixels` 的图会被 Blender 记成「已修改未保存」,关文件/切场景时弹
    「N 张图片未保存」把用户拦住(实测 4 张参数表 + 1 张灯表 + 6 张中性图 = 11 张)。
    `is_dirty` 是只读的,`pack()` 是唯一能清掉它的合法手段 —— 而且顺带让表的内容
    真的进文件(重开时 _restore_projections 照样按真源重算,打包字节只是不再碍事)。
    表都极小(灯表 64×4、参数表 1024×H),实测 pack 0.2~0.8ms,落在去抖后的一次
    flush 上可忽略。平色图不走这里:它们用 generated_color,压根不脏。"""
    try:
        image.pack()
    except Exception:
        pass


def _set_colorspace(image, want):
    """给图定色彩空间 —— **只在真的不一样时才写**。

    🔴 赋值 `colorspace_settings.name` 是一次「重新解释这张图」的信号,Blender 会
    据此**丢弃并重建像素缓冲**,即使赋的是同一个值。后果按图的来源分两种,都很毒:

    * `source='GENERATED'` 的图(1x1 中性图、参数表、灯表)没有可回读的字节,
      重建 = 按 `generated_color` 重填 —— 而它恒是黑 (0,0,0,1)。于是**写进去的像素
      被静默抹成黑**。实测症状:没绑贴图的槽全采到黑,_BaseMap 中性白→黑,
      脸/发整片纯黑(而直建路缺图时压根不建采样节点,socket 停在接口缺省,是亮的)。
    * 真图(有文件/打包字节)重建 = 重新解码,值不丢,但**该图的所有使用者一起失效**。
      300 张材质共用几张贴图时就是 O(N²):实测 12.7ms/材质 退化成 1975ms/材质。

    所以「设色彩空间」这件事必须幂等,而唯一的幂等写法就是先比后写。
    每一处设色彩空间都走这里,不要就地写 —— 就地写过的地方全都踩过上面两条。"""
    if image is None:
        return
    try:
        if image.colorspace_settings.name != want:
            image.colorspace_settings.name = want
    except Exception:
        pass


def _image_uploaded(image):
    """像素写完之后让它真的到达 GPU。

    🔴 `update()` + `update_tag()` **都不够**:EEVEE 会继续用已上传的那份纹理,
    症状是「表里写对了、画面纹丝不动,下一次渲染才跟上」(实测:加一盏灯后
    peak|Δ|=0,再渲一次才变)。`gl_free()` 丢掉 GPU 端句柄,下次求值重新上传。
    表都是极小的图(灯表 64×4、参数表 1024×68),重传成本可以忽略;而这正是
    「数据一改、几百张材质立刻跟随且零重接」得以成立的最后一环。"""
    image.update()
    image.update_tag()
    try:
        image.gl_free()
    except Exception:
        pass


def _light_table_image():
    """场景全局灯表(全部生成栈共读同一张):64 列 × 4 行 fp32。
       列0 = 场景主光;列1.. = 附加光。行布局:
       行0 (x,y,z | 类型)   类型 0=SUN 1=POINT/AREA 2=SPOT
       行1 (r,g,b | -)      颜色 × 能量
       行2 (x,y,z | -)      灯的世界 +Z 轴(指向光源;SUN 的方向、SPOT 的锥轴)
       行3 (cosOuter, cosInner, hasMain* | count*)   *仅列0:主光在场旗标 + 附加光数
    列宽定死:图节点指针烙在几百张模板拷贝里,尺寸一变就得全场换指针 —— 表列数是
    协议不是容量优化;63 盏附加光之外的照明缺席会响亮报数,不静默截断。"""
    image = bpy.data.images.get(LIGHT_TABLE)
    if image is None or tuple(image.size) != (LIGHT_TABLE_COLS, LIGHT_TABLE_ROWS) or not image.is_float:
        stale = image
        image = bpy.data.images.new(LIGHT_TABLE, LIGHT_TABLE_COLS, LIGHT_TABLE_ROWS,
                                    float_buffer=True, alpha=True)
        _set_colorspace(image, 'Non-Color')
        # 🔴 同参数表:alpha 这里装的是类型位/count,不是不透明度。默认 STRAIGHT
        # 会拿它去关联 RGB(灯位/颜色/轴全被毁),必须 CHANNEL_PACKED。
        image.alpha_mode = 'CHANNEL_PACKED'
        image.use_fake_user = True
        if stale is not None:
            stale.user_remap(image)   # 旧尺寸的表被拷贝们的图节点攥着——换血不换指针语义
            bpy.data.images.remove(stale)
            image.name = LIGHT_TABLE
    return image


def _pack_light(light):
    """一盏灯的 4 texel 记录(列向,行序同上)。"""
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
    return ([matrix[0][3], matrix[1][3], matrix[2][3], flag],
            color + [1.0], axis + [1.0], cone + [0.0, 0.0])


def refresh_light_tables():
    """场景灯 → 灯表像素。加灯/删灯/动灯/换色/调锥全走这一条:**零节点、零重接**
    (实测机制:改像素不重建任何节点,渲染即时跟)。这正是模板复制架构的前提 ——
    拷出去的几百张材质不可能逐张重接,灯的一切变化都必须是数据。"""
    main = _scene_sun()
    others = [o for o in bpy.context.scene.objects
              if o.type == 'LIGHT' and _light_visible(o) and o is not main]
    others.sort(key=lambda o: o.name)
    if len(others) > LIGHT_TABLE_COLS - 1:
        print('[ruri-cap] !! 场景 {0} 盏附加光超过灯表 {1} 列,按名序靠后的不参与照明'.format(
            len(others), LIGHT_TABLE_COLS - 1), flush=True)
        others = others[:LIGHT_TABLE_COLS - 1]
    image = _light_table_image()
    rows = [[0.0] * (LIGHT_TABLE_COLS * 4) for _ in range(LIGHT_TABLE_ROWS)]
    for col, light in enumerate([main] + others if main is not None else others, start=0 if main is not None else 1):
        r0, r1, r2, r3 = _pack_light(light)
        for row, value in ((0, r0), (1, r1), (2, r2), (3, r3)):
            rows[row][col * 4:col * 4 + 4] = value
    rows[3][2] = 1.0 if main is not None else 0.0   # 列0 行3 .b = hasMain
    rows[3][3] = float(len(others))                 # 列0 行3 .a = 附加光数(灯循环迭代数)
    flat = []
    for row in rows:
        flat.extend(row)
    image.pixels = flat
    _image_uploaded(image)
    _image_stored(image)   # 清 dirty:否则关文件弹「N 张图片未保存」
    return 1


def _table_row(g, index, row):
    """按列下标取灯表的一行。Closest + EXTEND:纹素寻址不许再插一次值,
    否则相邻两盏灯会被硬件混成一盏不存在的灯。"""
    u = g.math('DIVIDE', g.math('ADD', index, 0.5), float(LIGHT_TABLE_COLS))
    node = g._nd('ShaderNodeTexImage')
    node.image = _light_table_image()
    node.interpolation = 'Closest'
    node.extension = 'EXTEND'
    node.label = 'RuriLightTable'
    g._set(node.inputs['Vector'], g.comb(u, (row + 0.5) / LIGHT_TABLE_ROWS, 0.0))
    return node.outputs['Color'], node.outputs['Alpha']


def _cap_main_light(g, query, ctx):
    """主方向光的兑现,**数据驱动**:方向/颜色从灯表列0读。加灯/删灯/动灯只改像素,
    图一个节点都不动。没有主光时的头灯兜底(方向 = Geometry 的 Incoming,表面指向观察者)
    以 hasMain 选择支**常驻图里** —— 0 盏灯 ↔ 有灯 的切换同样只是像素。

    逐角色覆盖灯(MAIN_LIGHT_OVERRIDE)是唯一的非表路径:那盏灯是这张材质自己的,
    走驱动器直连(转灯/换色实时跟),由设置它的算子对该材质单独 rewire —— 单材质一次,
    永不进批量路径。

    距离衰减恒 1(方向光无距离项);阴影衰减恒 1 是因为遮挡归 ShadowAttenuation 那条能力,
    而它在 Blender 上是 Subsumed —— EEVEE/Cycles 在图外自己算,图里再乘一次就是双计。"""
    _ = query
    light = _override_light(ctx.get('material'))
    if light is not None:
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
    refresh_light_tables()
    _pos, _flag = _table_row(g, 0.0, 0)
    color, _ca = _table_row(g, 0.0, 1)
    axis, _aa = _table_row(g, 0.0, 2)
    flags, _count = _table_row(g, 0.0, 3)
    _co, _ci, has_main = g.sep(flags)
    direction = g.mixv(has_main, g.geo().outputs['Incoming'], axis)
    tint = g.mixv(has_main, (1.0, 1.0, 1.0), color)
    return {
        'direction': g.b2u(g.vmath('NORMALIZE', direction)),
        'color': tint,
        'distanceAttenuation': 1.0,
        'shadowAttenuation': 1.0,
        'layerMask': 1.0,
    }


def _cap_additional_light_count(g, query, ctx):
    """附加光数从灯表列0行3的 .a 读 —— 给的是 socket 不是常量:加灯删灯改像素即生效,
    模板的几百张拷贝零重接。喂灯循环的迭代数 socket(float→int 隐式转换,值恒为精确整数)。"""
    _ = (query, ctx)
    refresh_light_tables()
    _flags, count = _table_row(g, 0.0, 3)
    return {'': count}


def _cap_additional_light(g, query, ctx):
    """第 index 盏附加光(表列 index+1;列0 是主光)。**逐迭代**按下标查灯表,
    所以下标是什么值都取得对 —— 循环体内的割点每圈自己采一次。

    类型差异不靠逐灯建节点,靠表里的类型位在图里选:
      SUN  方向 = 灯的 +Z 轴,无距离衰减;
      其余 方向 = 归一化(灯位 − 着色点),距离衰减 = 平方反比(Blender 自己的灯就这么落衰);
      SPOT 再乘一层原生锥(cos 半角 + spot_blend 软边,smoothstep,与 Cycles 同式)。
    灯不在场时对应列全零 ⇒ 颜色 0 ⇒ 贡献 0,且 count 门根本不让循环跑到那里。"""
    index = query.get('index')
    if index is None:
        return None
    refresh_light_tables()
    column = g.math('ADD', index, 1.0)
    position, type_flag = _table_row(g, column, 0)
    color, _ca = _table_row(g, column, 1)
    axis, _aa = _table_row(g, column, 2)
    cone, _oa = _table_row(g, column, 3)
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
            _set_colorspace(img, 'Non-Color')
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
    _set_colorspace(img, 'Non-Color' if non_color else 'sRGB')
    return img


def _images():
    _img('_BaseColorMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_BaseHeightMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_BaseMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_DetailMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_DisplacementTex', (0.5, 0.5, 0.5, 1.0), True)
    _img('_EmissiveMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_IceNormalMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_IceOpacityMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_Layer1BaseMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_Layer1BumpMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_LayerBlendMaskMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_MROMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_MacroNormalMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_MaskMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_MatcapMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_NormalMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_ParallaxMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_ParallaxMaskMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_ParallaxNoiseMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_PlanarReflectionTexture', (1.0, 1.0, 1.0, 1.0), False)
    _img('_RefractTex', (0.0, 0.0, 0.0, 0.0), True)
    _img('_SubsurfaceMap', (1.0, 1.0, 1.0, 1.0), False)
    _img('_VoxelAtlas', (1.0, 1.0, 1.0, 1.0), False)
    _img('_WaterCausticMap', (1.0, 1.0, 1.0, 1.0), True)
    _img('_WaterNormalMap', (0.5, 0.5, 1.0, 1.0), True)


def build_RCE_F_Schlick():
    t = _tree('RCE_F_Schlick')
    g = G(t)
    v0 = g.inp('f0', False)
    v1 = g.inp('u', False)
    v2 = g.math('SUBTRACT', 1, v1)
    v3 = g.math('MULTIPLY', v2, v2)
    v4 = g.math('MULTIPLY', v2, v3)
    v5 = g.math('MULTIPLY', v4, v3)
    v6 = g.math('SUBTRACT', 1, v0)
    v7 = g.math('MULTIPLY', v6, v5)
    v8 = g.math('ADD', v7, v0)
    g.out_('ret', v8, False)


def build_RCE_FoliageGradientBand():
    t = _tree('RCE_FoliageGradientBand')
    g = G(t)
    v0 = g.inp('coord', False)
    v1 = g.inp('position', False)
    v2 = g.inp('radius', False)
    v3 = g.inp('contrast', False)
    v4 = g.inp('intensity', False)
    v5 = g.math('SUBTRACT', v0, v1)
    v6 = g.math('ABSOLUTE', v5, 0.0)
    v7 = g.math('SUBTRACT', v6, v2)
    v8 = g.math('MAXIMUM', v2, 0.0001)
    v9 = g.math('DIVIDE', v7, v8)
    v10 = g.math('SUBTRACT', 1, v9)
    v11 = g.clampn(v10, 0, 1)
    v12 = g.math('MAXIMUM', v3, 0.001)
    v13 = g.math('POWER', v11, v12)
    v14 = g.math('MULTIPLY', v13, v4)
    v15 = g.math('SUBTRACT', 1, v14)
    g.out_('ret', v15, False)


def build_RCE_HgEnvBRDF():
    t = _tree('RCE_HgEnvBRDF')
    g = G(t)
    v0 = g.inp('roughness', False)
    v1 = g.inp('NoV', False)
    v2 = g.inp('f0', True)
    v3 = g.math('MULTIPLY', 1, -1.0)
    v4 = g.math('MULTIPLY_ADD', v0, v3, 1)
    v5 = g.math('MULTIPLY', v4, v4)
    v6 = g.math('MULTIPLY', 9.279999732971191, -1.0)
    v7 = g.math('MULTIPLY', v1, v6)
    v8 = g.math('POWER', 2.0, v7)
    v9 = g.math('MINIMUM', v5, v8)
    v10 = g.math('MULTIPLY', 0.027499999850988388, -1.0)
    v11 = g.math('MULTIPLY_ADD', v0, v10, 0.042500000447034836)
    v12 = g.math('MULTIPLY_ADD', v9, v4, v11)
    v13 = g.math('MULTIPLY', 1.0399999618530273, -1.0)
    v14 = g.math('MULTIPLY', 0.5720000267028809, -1.0)
    v15 = g.math('MULTIPLY_ADD', v0, v14, 1.0399999618530273)
    v16 = g.math('MULTIPLY_ADD', v12, v13, v15)
    v17 = g.math('MULTIPLY', 0.03999999910593033, -1.0)
    v18 = g.math('MULTIPLY_ADD', v0, 0.02199999988079071, v17)
    v19 = g.math('MULTIPLY_ADD', v12, 1.0399999618530273, v18)
    v20 = g.sep(v2)
    v21 = g.math('MULTIPLY', v20[1], 50)
    v22 = g.clampn(v21)
    v23 = g.math('MULTIPLY', v19, v22)
    g.out_('envScale', v16, False)
    g.out_('envBias', v23, False)


def build_RCE_HgEnvBRDFApproxDFG():
    t = _tree('RCE_HgEnvBRDFApproxDFG')
    g = G(t)
    v0 = g.inp('roughness', False)
    v1 = g.math('MULTIPLY', 1, -1.0)
    v2 = g.math('MULTIPLY_ADD', v0, v1, 1)
    v3 = g.math('MULTIPLY', v2, -1.0)
    v4 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v5 = g.math('MULTIPLY_ADD', v3, 0.38302600383758545, v4)
    v6 = g.math('MULTIPLY_ADD', v2, v5, 1.049970030784607)
    v7 = g.math('MULTIPLY_ADD', v2, v6, 0.4092549979686737)
    v8 = g.math('MINIMUM', v7, 0.9990000128746033)
    g.out_('ret', v8, False)


def build_RCE_HgDirectLightEnergy():
    t = _tree('RCE_HgDirectLightEnergy')
    g = G(t)
    v0 = g.inp('roughness', False)
    v1 = g.inp('f0', True)
    v2 = g.inp('NoL', False)
    v3 = g.inp('NoH', False)
    v4 = g.inp('NoV', False)
    v5 = g.inp('VoH', False)
    v6 = g.math('MULTIPLY', v0, v0)
    v7 = g.math('MULTIPLY', v0, v0)
    v8 = g.math('MULTIPLY', v6, v7)
    v9 = g.math('MINIMUM', v4, 1)
    v10 = g.math('MULTIPLY', v3, -1.0)
    v11 = g.math('MULTIPLY_ADD', v3, v8, v10)
    v12 = g.math('MULTIPLY_ADD', v11, v3, 1)
    v13 = g.math('MULTIPLY', v2, -1.0)
    v14 = g.math('MULTIPLY_ADD', v13, v8, v2)
    v15 = g.math('MULTIPLY_ADD', v14, v2, v8)
    v16 = g.math('SQRT', v15, 0.0)
    v17 = g.math('MULTIPLY', v9, v16)
    v18 = g.math('MULTIPLY', v9, -1.0)
    v19 = g.math('MULTIPLY_ADD', v18, v8, v9)
    v20 = g.math('MULTIPLY_ADD', v19, v9, v8)
    v21 = g.math('SQRT', v20, 0.0)
    v22 = g.math('MULTIPLY', v2, v21)
    v23 = g.math('ADD', v17, v22)
    v24 = g.math('ADD', v23, 0.0001)
    v25 = g.math('MULTIPLY', v12, v12)
    v26 = g.math('DIVIDE', v8, v25)
    v27 = g.math('DIVIDE', 0.5, v24)
    v28 = g.math('MULTIPLY', v26, v27)
    v29 = g.math('MULTIPLY', v5, -1.0)
    v30 = g.math('MULTIPLY_ADD', v29, 1, 1)
    v31 = g.math('MULTIPLY', v30, v30)
    v32 = g.math('MULTIPLY', v31, v31)
    v33 = g.math('MULTIPLY', v30, v32)
    v34 = g.math('MULTIPLY', v32, -1.0)
    v35 = g.math('MULTIPLY_ADD', v34, v30, 1)
    v36 = g.group_named('RCE_HgEnvBRDFApproxDFG', [('roughness', v0)])
    v37 = g.math('MULTIPLY', v36[0], -1.0)
    v38 = g.math('MULTIPLY_ADD', v37, 1, 1)
    v39 = g.math('DIVIDE', v36[0], v38)
    v40 = g.vmath('SUBTRACT', (1, 1, 1), v1)
    v41 = g.vmath('MULTIPLY', v40, (0.047619047619, 0.047619047619, 0.047619047619))
    v42 = g.vmath('ADD', v41, v1)
    v43 = g.vmath('MULTIPLY', v42, v42)
    v44 = g.bc(v39)
    v45 = g.vmath('MULTIPLY', v44, v43)
    v46 = g.vmath('SCALE', v42, s=-1.0)
    v47 = g.comb(v38, v38, v38)
    v48 = g.vmath('MULTIPLY', v46, v47)
    v49 = g.vmath('ADD', v48, (1, 1, 1))
    v50 = g.vmath('DIVIDE', v45, v49)
    v51 = g.comb(v35, v35, v35)
    v52 = g.comb(v33, v33, v33)
    v53 = g.vmath('MULTIPLY', v1, v51)
    v54 = g.vmath('ADD', v53, v52)
    v55 = g.bc(v28)
    v56 = g.vmath('MULTIPLY', v55, v54)
    v57 = g.vmath('MINIMUM', v56, (2048, 2048, 2048))
    v58 = g.vmath('ADD', v50, v57)
    v59 = g.vmath('MAXIMUM', v58, (0, 0, 0))
    v60 = g.vmath('MINIMUM', v59, (1000, 1000, 1000))
    g.out_('ret', v60, True)


def build_RCE_HgSssLobe():
    t = _tree('RCE_HgSssLobe')
    g = G(t)
    v0 = g.inp('amount', False)
    v1 = g.inp('rawNoL', False)
    v2 = g.inp('VdotL', False)
    v3 = g.inp('selfShadowBias', False)
    v4 = g.inp('enableSelfShadowBias', False)
    v5 = g.math('MULTIPLY_ADD', v1, 0.6666666865348816, 0.3333333432674408)
    v6 = g.clampn(v5, 0, 1)
    v7 = g.math('SQRT', v6, 0.0)
    v8 = g.math('MULTIPLY', v6, v7)
    v9 = g.math('MULTIPLY', 1, -1.0)
    v10 = g.math('MULTIPLY_ADD', v8, 1.6666666269302368, v9)
    v11 = g.math('MULTIPLY_ADD', v0, v10, 1)
    v12 = g.math('MULTIPLY', v2, -1.0)
    v13 = g.clampn(v12, 0, 1)
    v14 = g.math('LOGARITHM', v13, 2.0)
    v15 = g.math('MULTIPLY', v14, 12)
    v16 = g.math('POWER', 2.0, v15)
    v17 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v18 = g.math('MULTIPLY_ADD', v0, v17, 3)
    v19 = g.math('MULTIPLY', v16, v18)
    v20 = g.math('MULTIPLY', v11, -1.0)
    v21 = g.math('MULTIPLY_ADD', v20, 0.15915493667125702, 1)
    v22 = g.math('MULTIPLY', v11, 0.15915493667125702)
    v23 = g.math('MULTIPLY_ADD', v19, v21, v22)
    v24 = g.math('MULTIPLY', v3, v4)
    v25 = g.math('SUBTRACT', v1, v24)
    v26 = g.math('ADD', v25, 2)
    v27 = g.clampn(v26, 0, 1)
    v28 = g.math('MULTIPLY', v23, v27)
    g.out_('ret', v28, False)


def build_RCE_RuriEvalSensitivity():
    t = _tree('RCE_RuriEvalSensitivity')
    g = G(t)
    v0 = g.inp('opd', False)
    v1 = g.inp('shift', True)
    v2 = g.inp('M_PI', False, 0.0)
    v3 = g.math('MULTIPLY', 2, v2)
    v4 = g.math('MULTIPLY', v3, v0)
    v5 = g.math('MULTIPLY', v4, 1E-06)
    v6 = g.math('MULTIPLY', 2, v2)
    v7 = g.bc(v6)
    v8 = g.vmath('MULTIPLY', v7, (4327800000, 9304600000, 6612100000))
    v9 = g.sep(v8)
    v10 = g.math('SQRT', v9[0], 0.0)
    v11 = g.math('SQRT', v9[1], 0.0)
    v12 = g.math('SQRT', v9[2], 0.0)
    v13 = g.comb(v10, v11, v12)
    v14 = g.vmath('MULTIPLY', (5.4856E-13, 4.4201E-13, 5.2481E-13), v13)
    v15 = g.bc(v5)
    v16 = g.vmath('MULTIPLY', (1681000, 1795300, 2208400), v15)
    v17 = g.vmath('ADD', v16, v1)
    v18 = g.math('COSINE', v17, 0.0)
    v19 = g.bc(v18)
    v20 = g.vmath('MULTIPLY', v14, v19)
    v21 = g.vmath('SCALE', (4327800000, 9304600000, 6612100000), s=-1.0)
    v22 = g.bc(v5)
    v23 = g.vmath('MULTIPLY', v21, v22)
    v24 = g.bc(v5)
    v25 = g.vmath('MULTIPLY', v23, v24)
    v26 = g.math('EXPONENT', v25, 0.0)
    v27 = g.bc(v26)
    v28 = g.vmath('MULTIPLY', v20, v27)
    v29 = g.math('MULTIPLY', 2, v2)
    v30 = g.math('MULTIPLY', v29, 4528200000)
    v31 = g.math('SQRT', v30, 0.0)
    v32 = g.math('MULTIPLY', 9.747E-14, v31)
    v33 = g.math('MULTIPLY', 2239900, v5)
    v34 = g.sep(v1)
    v35 = g.math('ADD', v33, v34[0])
    v36 = g.math('COSINE', v35, 0.0)
    v37 = g.math('MULTIPLY', v32, v36)
    v38 = g.math('MULTIPLY', 4528200000, -1.0)
    v39 = g.math('MULTIPLY', v38, v5)
    v40 = g.math('MULTIPLY', v39, v5)
    v41 = g.math('EXPONENT', v40, 0.0)
    v42 = g.math('MULTIPLY', v37, v41)
    v43 = g.sep(v28)
    v44 = g.math('ADD', v43[0], v42)
    v45 = g.comb(v44, v43[1], v43[2])
    v46 = g.vmath('DIVIDE', v45, (1.0685E-07, 1.0685E-07, 1.0685E-07))
    v47 = g.math('MULTIPLY', 1.5371385, -1.0)
    v48 = g.math('MULTIPLY', 0.4985314, -1.0)
    v49 = g.comb(3.2404542, v47, v48)
    v50 = g.vmath('DOT_PRODUCT', v49, v46)
    v51 = g.math('MULTIPLY', 0.969266, -1.0)
    v52 = g.comb(v51, 1.8760108, 0.041556)
    v53 = g.vmath('DOT_PRODUCT', v52, v46)
    v54 = g.math('MULTIPLY', 0.2040259, -1.0)
    v55 = g.comb(0.0556434, v54, 1.0572252)
    v56 = g.vmath('DOT_PRODUCT', v55, v46)
    v57 = g.comb(v50, v53, v56)
    g.out_('ret', v57, True)


def build_RCE_RuriFresnel0ToIor():
    t = _tree('RCE_RuriFresnel0ToIor')
    g = G(t)
    v0 = g.inp('fresnel0', True)
    v1 = g.vmath('MAXIMUM', v0, (0, 0, 0))
    v2 = g.vmath('MINIMUM', v1, (0.9999, 0.9999, 0.9999))
    v3 = g.sep(v2)
    v4 = g.math('SQRT', v3[0], 0.0)
    v5 = g.math('SQRT', v3[1], 0.0)
    v6 = g.math('SQRT', v3[2], 0.0)
    v7 = g.comb(v4, v5, v6)
    v8 = g.vmath('ADD', (1, 1, 1), v7)
    v9 = g.vmath('SUBTRACT', (1, 1, 1), v7)
    v10 = g.vmath('DIVIDE', v8, v9)
    g.out_('ret', v10, True)


def build_RCE_RuriEvalIridescence():
    t = _tree('RCE_RuriEvalIridescence')
    g = G(t)
    v0 = g.inp('outsideIor', False)
    v1 = g.inp('eta2', False)
    v2 = g.inp('cosTheta1', False)
    v3 = g.inp('iridescenceThickness', False)
    v4 = g.inp('baseF0', True)
    v5 = g.math('SUBTRACT', v3, 0)
    v6 = g.math('DIVIDE', v5, 0.03)
    v7 = g.clampn(v6)
    v8 = g.math('MULTIPLY', v7, v7)
    v9 = g.math('MULTIPLY', 2.0, v7)
    v10 = g.math('SUBTRACT', 3.0, v9)
    v11 = g.math('MULTIPLY', v8, v10)
    v12 = g.mixf(v11, v0, v1)
    v13 = g.math('DIVIDE', v0, v12)
    v14 = g.math('MULTIPLY', v13, v13)
    v15 = g.math('MULTIPLY', v2, v2)
    v16 = g.math('SUBTRACT', 1, v15)
    v17 = g.math('MULTIPLY', v14, v16)
    v18 = g.math('SUBTRACT', 1, v17)
    v19 = g.math('LESS_THAN', v18, 0)
    v20 = g.mixv(v19, (0.0, 0.0, 0.0), v4)
    v21 = g.mixf(v19, 0.0, 1.0)
    v22 = g.math('SUBTRACT', 1.0, v21)
    v23 = g.math('SQRT', v18, 0.0)
    v24 = g.math('SUBTRACT', 1.0, v21)
    v25 = g.math('SUBTRACT', v12, v0)
    v26 = g.math('ADD', v12, v0)
    v27 = g.math('DIVIDE', v25, v26)
    v28 = g.math('MULTIPLY', v27, v27)
    v29 = g.math('SUBTRACT', 1.0, v21)
    v30 = g.group_named('RCE_F_Schlick', [('f0', v28), ('u', v2)])
    v31 = g.math('SUBTRACT', 1.0, v21)
    v32 = g.math('SUBTRACT', 1, v30[0])
    v33 = g.math('SUBTRACT', 1.0, v21)
    v34 = g.inp('M_PI', False, 0.0)
    v35 = g.math('SUBTRACT', 1.0, v21)
    v36 = g.vmath('MAXIMUM', v4, (0, 0, 0))
    v37 = g.vmath('MINIMUM', v36, (0.9999, 0.9999, 0.9999))
    v38 = g.group_named('RCE_RuriFresnel0ToIor', [('fresnel0', v37)])
    v39 = g.math('SUBTRACT', 1.0, v21)
    v40 = g.sep(v38[0])
    v41 = g.math('SUBTRACT', v40[0], v12)
    v42 = g.math('ADD', v40[0], v12)
    v43 = g.math('DIVIDE', v41, v42)
    v44 = g.math('MULTIPLY', v43, v43)
    v45 = g.bc(v44)
    v46 = g.math('SUBTRACT', 1.0, v21)
    v47 = g.sep(v45)
    v48 = g.group_named('RCE_F_Schlick', [('f0', v47[0]), ('u', v23)])
    v49 = g.bc(v48[0])
    v50 = g.math('SUBTRACT', 1.0, v21)
    v51 = g.math('LESS_THAN', v40[0], v12)
    v52 = g.mixf(v51, 0, v34)
    v53 = g.math('LESS_THAN', v40[1], v12)
    v54 = g.mixf(v53, 0, v34)
    v55 = g.math('LESS_THAN', v40[2], v12)
    v56 = g.mixf(v55, 0, v34)
    v57 = g.comb(v52, v54, v56)
    v58 = g.math('SUBTRACT', 1.0, v21)
    v59 = g.math('MULTIPLY', 2, v12)
    v60 = g.math('MULTIPLY', v59, v3)
    v61 = g.math('MULTIPLY', v60, v23)
    v62 = g.math('SUBTRACT', 1.0, v21)
    v63 = g.bc(v34)
    v64 = g.vmath('ADD', v63, v57)
    v65 = g.math('SUBTRACT', 1.0, v21)
    v66 = g.bc(v30[0])
    v67 = g.vmath('MULTIPLY', v66, v49)
    v68 = g.vmath('MAXIMUM', v67, (1E-05, 1E-05, 1E-05))
    v69 = g.vmath('MINIMUM', v68, (0.9999, 0.9999, 0.9999))
    v70 = g.math('SUBTRACT', 1.0, v21)
    v71 = g.sep(v69)
    v72 = g.math('SQRT', v71[0], 0.0)
    v73 = g.math('SQRT', v71[1], 0.0)
    v74 = g.math('SQRT', v71[2], 0.0)
    v75 = g.comb(v72, v73, v74)
    v76 = g.math('SUBTRACT', 1.0, v21)
    v77 = g.math('MULTIPLY', v32, v32)
    v78 = g.bc(v77)
    v79 = g.vmath('MULTIPLY', v78, v49)
    v80 = g.vmath('SUBTRACT', (1, 1, 1), v69)
    v81 = g.vmath('DIVIDE', v79, v80)
    v82 = g.math('SUBTRACT', 1.0, v21)
    v83 = g.bc(v30[0])
    v84 = g.vmath('ADD', v83, v81)
    v85 = g.math('SUBTRACT', 1.0, v21)
    v86 = g.bc(v32)
    v87 = g.vmath('SUBTRACT', v81, v86)
    v88 = g.math('SUBTRACT', 1.0, v21)
    v89 = g.math('SUBTRACT', 2, 1)
    v90 = g.math('ADD', v89, 1.0)
    v91 = g.math('CEIL', v90, 0.0)
    v92 = g.math('MAXIMUM', v91, 0.0)
    z93i, z93o = g.repeat_begin(v92, [('Cm', True, v87), ('I', True, v84), ('Lloop0', False, 1.0), ('m', False, 1)])
    v94 = g.math('GREATER_THAN', z93i.outputs['m'], 2)
    v95 = g.math('SUBTRACT', 1.0, v94)
    v96 = g.math('MULTIPLY', z93i.outputs['Lloop0'], v95)
    v97 = g.math('SUBTRACT', 1.0, v21)
    v98 = g.vmath('MULTIPLY', z93i.outputs['Cm'], v75)
    v99 = g.mixv(v97, z93i.outputs['Cm'], v98)
    v100 = g.math('SUBTRACT', 1.0, v21)
    v101 = g.math('MULTIPLY', z93i.outputs['m'], v61)
    v102 = g.bc(z93i.outputs['m'])
    v103 = g.vmath('MULTIPLY', v102, v64)
    v104 = g.group_named('RCE_RuriEvalSensitivity', [('opd', v101), ('shift', v103), ('M_PI', v34)])
    v105 = g.vmath('MULTIPLY', (2, 2, 2), v104[0])
    v106 = g.math('SUBTRACT', 1.0, v21)
    v107 = g.vmath('MULTIPLY', v99, v105)
    v108 = g.vmath('ADD', z93i.outputs['I'], v107)
    v109 = g.mixv(v106, z93i.outputs['I'], v108)
    v110 = g.mixv(v96, z93i.outputs['I'], v109)
    v111 = g.mixv(v96, z93i.outputs['Cm'], v99)
    v112 = g.math('ADD', z93i.outputs['m'], 1.0)
    g.repeat_end(z93o, {'Cm': v111, 'I': v110, 'Lloop0': v96, 'm': v112})
    v113 = g.mixv(v88, v84, z93o.outputs['I'])
    v114 = g.mixv(v88, v87, z93o.outputs['Cm'])
    v115 = g.math('SUBTRACT', 1.0, v21)
    v116 = g.vmath('MAXIMUM', v113, (0, 0, 0))
    v117 = g.mixv(v115, v20, v116)
    v118 = g.mixf(v115, v21, 1.0)
    g.out_('ret', v117, True)


def build_RCE_RuriLinearFogValue():
    t = _tree('RCE_RuriLinearFogValue')
    g = G(t)
    v0 = g.inp('vertexDistance', False)
    v1 = g.inp('fogStart', False)
    v2 = g.inp('fogEnd', False)
    v3 = g.math('GREATER_THAN', v0, v1)
    v4 = g.math('SUBTRACT', 1.0, v3)
    v5 = g.mixf(v4, 0.0, 0)
    v6 = g.mixf(v4, 0.0, 1.0)
    v7 = g.math('SUBTRACT', 1.0, v6)
    v8 = g.math('LESS_THAN', v0, v2)
    v9 = g.math('SUBTRACT', 1.0, v8)
    v10 = g.mixf(v9, v5, 1)
    v11 = g.mixf(v9, v6, 1.0)
    v12 = g.mixf(v7, v5, v10)
    v13 = g.mixf(v7, v6, v11)
    v14 = g.math('SUBTRACT', 1.0, v13)
    v15 = g.math('SUBTRACT', v0, v1)
    v16 = g.math('SUBTRACT', v2, v1)
    v17 = g.math('DIVIDE', v15, v16)
    v18 = g.mixf(v14, v12, v17)
    v19 = g.mixf(v14, v13, 1.0)
    g.out_('ret', v18, False)


def build_RCE_RuriRefract():
    t = _tree('RCE_RuriRefract')
    g = G(t)
    v0 = g.inp('incident', True)
    v1 = g.inp('normal', True)
    v2 = g.inp('eta', False)
    v3 = g.vmath('DOT_PRODUCT', v1, v0)
    v4 = g.math('MULTIPLY', v2, v2)
    v5 = g.math('MULTIPLY', v3, v3)
    v6 = g.math('SUBTRACT', 1, v5)
    v7 = g.math('MULTIPLY', v4, v6)
    v8 = g.math('SUBTRACT', 1, v7)
    v9 = g.math('LESS_THAN', v8, 0)
    v10 = g.mixv(v9, (0.0, 0.0, 0.0), (0, 0, 0))
    v11 = g.mixf(v9, 0.0, 1.0)
    v12 = g.math('SUBTRACT', 1.0, v11)
    v13 = g.bc(v2)
    v14 = g.vmath('MULTIPLY', v13, v0)
    v15 = g.math('MULTIPLY', v2, v3)
    v16 = g.math('SQRT', v8, 0.0)
    v17 = g.math('ADD', v15, v16)
    v18 = g.bc(v17)
    v19 = g.vmath('MULTIPLY', v18, v1)
    v20 = g.vmath('SUBTRACT', v14, v19)
    v21 = g.mixv(v12, v10, v20)
    v22 = g.mixf(v12, v11, 1.0)
    g.out_('ret', v21, True)


def build_RCE_RuriTotalFogValue():
    t = _tree('RCE_RuriTotalFogValue')
    g = G(t)
    v0 = g.inp('sphericalVertexDistance', False)
    v1 = g.inp('cylindricalVertexDistance', False)
    v2 = g.inp('environmentalStart', False)
    v3 = g.inp('environmentalEnd', False)
    v4 = g.inp('renderDistanceStart', False)
    v5 = g.inp('renderDistanceEnd', False)
    v6 = g.group_named('RCE_RuriLinearFogValue', [('vertexDistance', v0), ('fogStart', v2), ('fogEnd', v3)])
    v7 = g.group_named('RCE_RuriLinearFogValue', [('vertexDistance', v1), ('fogStart', v4), ('fogEnd', v5)])
    v8 = g.math('MAXIMUM', v6[0], v7[0])
    g.out_('ret', v8, False)


def build_RCE_RuriApplyFog():
    t = _tree('RCE_RuriApplyFog')
    g = G(t)
    v0 = g.inp('inColor', True)
    v1 = g.inp('inColor_w', False)
    v2 = g.inp('sphericalVertexDistance', False)
    v3 = g.inp('cylindricalVertexDistance', False)
    v4 = g.inp('environmentalStart', False)
    v5 = g.inp('environmentalEnd', False)
    v6 = g.inp('renderDistanceStart', False)
    v7 = g.inp('renderDistanceEnd', False)
    v8 = g.inp('fogColor', True)
    v9 = g.inp('fogColor_w', False)
    v10 = g.group_named('RCE_RuriTotalFogValue', [('sphericalVertexDistance', v2), ('cylindricalVertexDistance', v3), ('environmentalStart', v4), ('environmentalEnd', v5), ('renderDistanceStart', v6), ('renderDistanceEnd', v7)])
    v11 = g.math('MULTIPLY', v10[0], v9)
    v12 = g.mixv(v11, v0, v8)
    g.out_('ret', v12, True)
    g.out_('ret_w', v1, False)


def build_RCE_ViewMatrixRow0():
    t = _tree('RCE_ViewMatrixRow0')
    g = G(t)
    v0 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v1 = g.sep(v0)
    v2 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v3 = g.sep(v2)
    v4 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v5 = g.sep(v4)
    v6 = g.comb(v1[0], v3[0], v5[0])
    g.out_('ret', v6, True)


def build_RCE_ViewMatrixRow1():
    t = _tree('RCE_ViewMatrixRow1')
    g = G(t)
    v0 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v1 = g.sep(v0)
    v2 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v3 = g.sep(v2)
    v4 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v5 = g.sep(v4)
    v6 = g.comb(v1[1], v3[1], v5[1])
    g.out_('ret', v6, True)


def build_RCE_ViewMatrixRow2():
    t = _tree('RCE_ViewMatrixRow2')
    g = G(t)
    v0 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v1 = g.sep(v0)
    v2 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v3 = g.sep(v2)
    v4 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'WORLD', 'CAMERA', 'VECTOR'))
    v5 = g.sep(v4)
    v6 = g.comb(v1[2], v3[2], v5[2])
    g.out_('ret', v6, True)


def build_RCE_Z_Ruri_Endfield_Scene_Lit_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_Lit_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_height', False)
    v2 = g.inp('s_heightPrev', False)
    v3 = g.inp('s_iter', False)
    v4 = g.inp('s_offCur', True)
    v5 = g.inp('s_offPrev', True)
    v6 = g.inp('s_texHit', False)
    v7 = g.inp('s_texPrev', False)
    v8 = g.inp('s_uvP', True)
    v9 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v10 = g.math('ADD', g.inp('r_steps', False), 1)
    v11 = g.math('LESS_THAN', v3, v10)
    v12 = g.math('SUBTRACT', 1.0, v11)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.mixf(v13, v6, v7)
    v15 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v16 = g.mixf(v15, v0, 0.0)
    v17 = g.mixf(v12, v0, v16)
    v18 = g.mixf(v12, v6, v14)
    v19 = g.mixf(v9, v0, v17)
    v20 = g.mixf(v9, v6, v18)
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.inp('_ParallaxNoiseMapTilling', False, 0.0)
    v23 = g.comb(v22, v22, 0.0)
    v24 = g.vmath('MULTIPLY', v8, v23)
    v25 = g.vmath('ADD', v24, v4)
    g.out_('F0_ParallaxNoiseMap_uv', v25, True)
    v26 = g.inp('F0_ParallaxNoiseMap', True, (1.0, 1.0, 1.0))
    v27 = g.inp('F0_ParallaxNoiseMap_alpha', False, 1.0)
    v28 = g.sep(v26)
    v29 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v30 = g.math('LESS_THAN', v1, v28[0])
    v31 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v32 = g.mixf(v31, v20, v28[0])
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixf(v33, v19, 0.0)
    v35 = g.mixf(v30, v19, v34)
    v36 = g.mixf(v30, v20, v32)
    v37 = g.mixf(v29, v19, v35)
    v38 = g.mixf(v29, v20, v36)
    v39 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v40 = g.mixf(v39, v2, v1)
    v41 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v42 = g.math('SUBTRACT', v1, g.inp('r_stepH', False))
    v43 = g.mixf(v41, v1, v42)
    v44 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v45 = g.mixv(v44, v5, v4)
    v46 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v47 = g.vmath('ADD', v4, g.inp('r_stepUV', True))
    v48 = g.mixv(v46, v4, v47)
    v49 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v50 = g.mixf(v49, v7, v28[0])
    v51 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v52 = g.math('ADD', v3, 1)
    v53 = g.mixf(v51, v3, v52)
    v54 = g.mixf(v0, v0, v37)
    v55 = g.mixf(v0, v1, v43)
    v56 = g.mixf(v0, v2, v40)
    v57 = g.mixf(v0, v3, v53)
    v58 = g.mixv(v0, v4, v48)
    v59 = g.mixv(v0, v5, v45)
    v60 = g.mixf(v0, v6, v38)
    v61 = g.mixf(v0, v7, v50)
    g.out_('o_Lloop0', v54, False)
    g.out_('o_height', v55, False)
    g.out_('o_heightPrev', v56, False)
    g.out_('o_iter', v57, False)
    g.out_('o_offCur', v58, True)
    g.out_('o_offPrev', v59, True)
    g.out_('o_texHit', v60, False)
    g.out_('o_texPrev', v61, False)
    g.out_('o_uvP', v8, True)


def build_RCE_Z_Ruri_Endfield_Scene_Lit_1():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_Lit_1')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_LitForward_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitForward_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_height', False)
    v2 = g.inp('s_heightPrev', False)
    v3 = g.inp('s_iter', False)
    v4 = g.inp('s_offCur', True)
    v5 = g.inp('s_offPrev', True)
    v6 = g.inp('s_texHit', False)
    v7 = g.inp('s_texPrev', False)
    v8 = g.inp('s_uvP', True)
    v9 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v10 = g.math('ADD', g.inp('r_steps', False), 1)
    v11 = g.math('LESS_THAN', v3, v10)
    v12 = g.math('SUBTRACT', 1.0, v11)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.mixf(v13, v6, v7)
    v15 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v16 = g.mixf(v15, v0, 0.0)
    v17 = g.mixf(v12, v0, v16)
    v18 = g.mixf(v12, v6, v14)
    v19 = g.mixf(v9, v0, v17)
    v20 = g.mixf(v9, v6, v18)
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.inp('_ParallaxNoiseMapTilling', False, 0.0)
    v23 = g.comb(v22, v22, 0.0)
    v24 = g.vmath('MULTIPLY', v8, v23)
    v25 = g.vmath('ADD', v24, v4)
    g.out_('F0_ParallaxNoiseMap_uv', v25, True)
    v26 = g.inp('F0_ParallaxNoiseMap', True, (1.0, 1.0, 1.0))
    v27 = g.inp('F0_ParallaxNoiseMap_alpha', False, 1.0)
    v28 = g.sep(v26)
    v29 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v30 = g.math('LESS_THAN', v1, v28[0])
    v31 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v32 = g.mixf(v31, v20, v28[0])
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixf(v33, v19, 0.0)
    v35 = g.mixf(v30, v19, v34)
    v36 = g.mixf(v30, v20, v32)
    v37 = g.mixf(v29, v19, v35)
    v38 = g.mixf(v29, v20, v36)
    v39 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v40 = g.mixf(v39, v2, v1)
    v41 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v42 = g.math('SUBTRACT', v1, g.inp('r_stepH', False))
    v43 = g.mixf(v41, v1, v42)
    v44 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v45 = g.mixv(v44, v5, v4)
    v46 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v47 = g.vmath('ADD', v4, g.inp('r_stepUV', True))
    v48 = g.mixv(v46, v4, v47)
    v49 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v50 = g.mixf(v49, v7, v28[0])
    v51 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v52 = g.math('ADD', v3, 1)
    v53 = g.mixf(v51, v3, v52)
    v54 = g.mixf(v0, v0, v37)
    v55 = g.mixf(v0, v1, v43)
    v56 = g.mixf(v0, v2, v40)
    v57 = g.mixf(v0, v3, v53)
    v58 = g.mixv(v0, v4, v48)
    v59 = g.mixv(v0, v5, v45)
    v60 = g.mixf(v0, v6, v38)
    v61 = g.mixf(v0, v7, v50)
    g.out_('o_Lloop0', v54, False)
    g.out_('o_height', v55, False)
    g.out_('o_heightPrev', v56, False)
    g.out_('o_iter', v57, False)
    g.out_('o_offCur', v58, True)
    g.out_('o_offPrev', v59, True)
    g.out_('o_texHit', v60, False)
    g.out_('o_texPrev', v61, False)
    g.out_('o_uvP', v8, True)


def build_RCE_Z_Ruri_Endfield_Scene_LitForward_1():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitForward_1')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_LitTransparent_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitTransparent_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_height', False)
    v2 = g.inp('s_heightPrev', False)
    v3 = g.inp('s_iter', False)
    v4 = g.inp('s_offCur', True)
    v5 = g.inp('s_offPrev', True)
    v6 = g.inp('s_texHit', False)
    v7 = g.inp('s_texPrev', False)
    v8 = g.inp('s_uvP', True)
    v9 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v10 = g.math('ADD', g.inp('r_steps', False), 1)
    v11 = g.math('LESS_THAN', v3, v10)
    v12 = g.math('SUBTRACT', 1.0, v11)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.mixf(v13, v6, v7)
    v15 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v16 = g.mixf(v15, v0, 0.0)
    v17 = g.mixf(v12, v0, v16)
    v18 = g.mixf(v12, v6, v14)
    v19 = g.mixf(v9, v0, v17)
    v20 = g.mixf(v9, v6, v18)
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.inp('_ParallaxNoiseMapTilling', False, 0.0)
    v23 = g.comb(v22, v22, 0.0)
    v24 = g.vmath('MULTIPLY', v8, v23)
    v25 = g.vmath('ADD', v24, v4)
    g.out_('F0_ParallaxNoiseMap_uv', v25, True)
    v26 = g.inp('F0_ParallaxNoiseMap', True, (1.0, 1.0, 1.0))
    v27 = g.inp('F0_ParallaxNoiseMap_alpha', False, 1.0)
    v28 = g.sep(v26)
    v29 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v30 = g.math('LESS_THAN', v1, v28[0])
    v31 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v32 = g.mixf(v31, v20, v28[0])
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixf(v33, v19, 0.0)
    v35 = g.mixf(v30, v19, v34)
    v36 = g.mixf(v30, v20, v32)
    v37 = g.mixf(v29, v19, v35)
    v38 = g.mixf(v29, v20, v36)
    v39 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v40 = g.mixf(v39, v2, v1)
    v41 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v42 = g.math('SUBTRACT', v1, g.inp('r_stepH', False))
    v43 = g.mixf(v41, v1, v42)
    v44 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v45 = g.mixv(v44, v5, v4)
    v46 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v47 = g.vmath('ADD', v4, g.inp('r_stepUV', True))
    v48 = g.mixv(v46, v4, v47)
    v49 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v50 = g.mixf(v49, v7, v28[0])
    v51 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v52 = g.math('ADD', v3, 1)
    v53 = g.mixf(v51, v3, v52)
    v54 = g.mixf(v0, v0, v37)
    v55 = g.mixf(v0, v1, v43)
    v56 = g.mixf(v0, v2, v40)
    v57 = g.mixf(v0, v3, v53)
    v58 = g.mixv(v0, v4, v48)
    v59 = g.mixv(v0, v5, v45)
    v60 = g.mixf(v0, v6, v38)
    v61 = g.mixf(v0, v7, v50)
    g.out_('o_Lloop0', v54, False)
    g.out_('o_height', v55, False)
    g.out_('o_heightPrev', v56, False)
    g.out_('o_iter', v57, False)
    g.out_('o_offCur', v58, True)
    g.out_('o_offPrev', v59, True)
    g.out_('o_texHit', v60, False)
    g.out_('o_texPrev', v61, False)
    g.out_('o_uvP', v8, True)


def build_RCE_Z_Ruri_Endfield_Scene_LitTransparent_1():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitTransparent_1')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_LitEffect_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitEffect_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_height', False)
    v2 = g.inp('s_heightPrev', False)
    v3 = g.inp('s_iter', False)
    v4 = g.inp('s_offCur', True)
    v5 = g.inp('s_offPrev', True)
    v6 = g.inp('s_texHit', False)
    v7 = g.inp('s_texPrev', False)
    v8 = g.inp('s_uvP', True)
    v9 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v10 = g.math('ADD', g.inp('r_steps', False), 1)
    v11 = g.math('LESS_THAN', v3, v10)
    v12 = g.math('SUBTRACT', 1.0, v11)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.mixf(v13, v6, v7)
    v15 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v16 = g.mixf(v15, v0, 0.0)
    v17 = g.mixf(v12, v0, v16)
    v18 = g.mixf(v12, v6, v14)
    v19 = g.mixf(v9, v0, v17)
    v20 = g.mixf(v9, v6, v18)
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.inp('_ParallaxNoiseMapTilling', False, 0.0)
    v23 = g.comb(v22, v22, 0.0)
    v24 = g.vmath('MULTIPLY', v8, v23)
    v25 = g.vmath('ADD', v24, v4)
    g.out_('F0_ParallaxNoiseMap_uv', v25, True)
    v26 = g.inp('F0_ParallaxNoiseMap', True, (1.0, 1.0, 1.0))
    v27 = g.inp('F0_ParallaxNoiseMap_alpha', False, 1.0)
    v28 = g.sep(v26)
    v29 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v30 = g.math('LESS_THAN', v1, v28[0])
    v31 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v32 = g.mixf(v31, v20, v28[0])
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixf(v33, v19, 0.0)
    v35 = g.mixf(v30, v19, v34)
    v36 = g.mixf(v30, v20, v32)
    v37 = g.mixf(v29, v19, v35)
    v38 = g.mixf(v29, v20, v36)
    v39 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v40 = g.mixf(v39, v2, v1)
    v41 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v42 = g.math('SUBTRACT', v1, g.inp('r_stepH', False))
    v43 = g.mixf(v41, v1, v42)
    v44 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v45 = g.mixv(v44, v5, v4)
    v46 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v47 = g.vmath('ADD', v4, g.inp('r_stepUV', True))
    v48 = g.mixv(v46, v4, v47)
    v49 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v50 = g.mixf(v49, v7, v28[0])
    v51 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v52 = g.math('ADD', v3, 1)
    v53 = g.mixf(v51, v3, v52)
    v54 = g.mixf(v0, v0, v37)
    v55 = g.mixf(v0, v1, v43)
    v56 = g.mixf(v0, v2, v40)
    v57 = g.mixf(v0, v3, v53)
    v58 = g.mixv(v0, v4, v48)
    v59 = g.mixv(v0, v5, v45)
    v60 = g.mixf(v0, v6, v38)
    v61 = g.mixf(v0, v7, v50)
    g.out_('o_Lloop0', v54, False)
    g.out_('o_height', v55, False)
    g.out_('o_heightPrev', v56, False)
    g.out_('o_iter', v57, False)
    g.out_('o_offCur', v58, True)
    g.out_('o_offPrev', v59, True)
    g.out_('o_texHit', v60, False)
    g.out_('o_texPrev', v61, False)
    g.out_('o_uvP', v8, True)


def build_RCE_Z_Ruri_Endfield_Scene_LitEffect_1():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitEffect_1')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_height', False)
    v2 = g.inp('s_heightPrev', False)
    v3 = g.inp('s_iter', False)
    v4 = g.inp('s_offCur', True)
    v5 = g.inp('s_offPrev', True)
    v6 = g.inp('s_texHit', False)
    v7 = g.inp('s_texPrev', False)
    v8 = g.inp('s_uvP', True)
    v9 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v10 = g.math('ADD', g.inp('r_steps', False), 1)
    v11 = g.math('LESS_THAN', v3, v10)
    v12 = g.math('SUBTRACT', 1.0, v11)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.mixf(v13, v6, v7)
    v15 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v16 = g.mixf(v15, v0, 0.0)
    v17 = g.mixf(v12, v0, v16)
    v18 = g.mixf(v12, v6, v14)
    v19 = g.mixf(v9, v0, v17)
    v20 = g.mixf(v9, v6, v18)
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.inp('_ParallaxNoiseMapTilling', False, 0.0)
    v23 = g.comb(v22, v22, 0.0)
    v24 = g.vmath('MULTIPLY', v8, v23)
    v25 = g.vmath('ADD', v24, v4)
    g.out_('F0_ParallaxNoiseMap_uv', v25, True)
    v26 = g.inp('F0_ParallaxNoiseMap', True, (1.0, 1.0, 1.0))
    v27 = g.inp('F0_ParallaxNoiseMap_alpha', False, 1.0)
    v28 = g.sep(v26)
    v29 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v30 = g.math('LESS_THAN', v1, v28[0])
    v31 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v32 = g.mixf(v31, v20, v28[0])
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixf(v33, v19, 0.0)
    v35 = g.mixf(v30, v19, v34)
    v36 = g.mixf(v30, v20, v32)
    v37 = g.mixf(v29, v19, v35)
    v38 = g.mixf(v29, v20, v36)
    v39 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v40 = g.mixf(v39, v2, v1)
    v41 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v42 = g.math('SUBTRACT', v1, g.inp('r_stepH', False))
    v43 = g.mixf(v41, v1, v42)
    v44 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v45 = g.mixv(v44, v5, v4)
    v46 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v47 = g.vmath('ADD', v4, g.inp('r_stepUV', True))
    v48 = g.mixv(v46, v4, v47)
    v49 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v50 = g.mixf(v49, v7, v28[0])
    v51 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v52 = g.math('ADD', v3, 1)
    v53 = g.mixf(v51, v3, v52)
    v54 = g.mixf(v0, v0, v37)
    v55 = g.mixf(v0, v1, v43)
    v56 = g.mixf(v0, v2, v40)
    v57 = g.mixf(v0, v3, v53)
    v58 = g.mixv(v0, v4, v48)
    v59 = g.mixv(v0, v5, v45)
    v60 = g.mixf(v0, v6, v38)
    v61 = g.mixf(v0, v7, v50)
    g.out_('o_Lloop0', v54, False)
    g.out_('o_height', v55, False)
    g.out_('o_heightPrev', v56, False)
    g.out_('o_iter', v57, False)
    g.out_('o_offCur', v58, True)
    g.out_('o_offPrev', v59, True)
    g.out_('o_texHit', v60, False)
    g.out_('o_texPrev', v61, False)
    g.out_('o_uvP', v8, True)


def build_RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_1():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_1')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_LitHLod_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitHLod_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_height', False)
    v2 = g.inp('s_heightPrev', False)
    v3 = g.inp('s_iter', False)
    v4 = g.inp('s_offCur', True)
    v5 = g.inp('s_offPrev', True)
    v6 = g.inp('s_texHit', False)
    v7 = g.inp('s_texPrev', False)
    v8 = g.inp('s_uvP', True)
    v9 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v10 = g.math('ADD', g.inp('r_steps', False), 1)
    v11 = g.math('LESS_THAN', v3, v10)
    v12 = g.math('SUBTRACT', 1.0, v11)
    v13 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v14 = g.mixf(v13, v6, v7)
    v15 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v16 = g.mixf(v15, v0, 0.0)
    v17 = g.mixf(v12, v0, v16)
    v18 = g.mixf(v12, v6, v14)
    v19 = g.mixf(v9, v0, v17)
    v20 = g.mixf(v9, v6, v18)
    v21 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v22 = g.inp('_ParallaxNoiseMapTilling', False, 0.0)
    v23 = g.comb(v22, v22, 0.0)
    v24 = g.vmath('MULTIPLY', v8, v23)
    v25 = g.vmath('ADD', v24, v4)
    g.out_('F0_ParallaxNoiseMap_uv', v25, True)
    v26 = g.inp('F0_ParallaxNoiseMap', True, (1.0, 1.0, 1.0))
    v27 = g.inp('F0_ParallaxNoiseMap_alpha', False, 1.0)
    v28 = g.sep(v26)
    v29 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v30 = g.math('LESS_THAN', v1, v28[0])
    v31 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v32 = g.mixf(v31, v20, v28[0])
    v33 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v34 = g.mixf(v33, v19, 0.0)
    v35 = g.mixf(v30, v19, v34)
    v36 = g.mixf(v30, v20, v32)
    v37 = g.mixf(v29, v19, v35)
    v38 = g.mixf(v29, v20, v36)
    v39 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v40 = g.mixf(v39, v2, v1)
    v41 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v42 = g.math('SUBTRACT', v1, g.inp('r_stepH', False))
    v43 = g.mixf(v41, v1, v42)
    v44 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v45 = g.mixv(v44, v5, v4)
    v46 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v47 = g.vmath('ADD', v4, g.inp('r_stepUV', True))
    v48 = g.mixv(v46, v4, v47)
    v49 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v50 = g.mixf(v49, v7, v28[0])
    v51 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v52 = g.math('ADD', v3, 1)
    v53 = g.mixf(v51, v3, v52)
    v54 = g.mixf(v0, v0, v37)
    v55 = g.mixf(v0, v1, v43)
    v56 = g.mixf(v0, v2, v40)
    v57 = g.mixf(v0, v3, v53)
    v58 = g.mixv(v0, v4, v48)
    v59 = g.mixv(v0, v5, v45)
    v60 = g.mixf(v0, v6, v38)
    v61 = g.mixf(v0, v7, v50)
    g.out_('o_Lloop0', v54, False)
    g.out_('o_height', v55, False)
    g.out_('o_heightPrev', v56, False)
    g.out_('o_iter', v57, False)
    g.out_('o_offCur', v58, True)
    g.out_('o_offPrev', v59, True)
    g.out_('o_texHit', v60, False)
    g.out_('o_texPrev', v61, False)
    g.out_('o_uvP', v8, True)


def build_RCE_Z_Ruri_Endfield_Scene_LitHLod_1():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_LitHLod_1')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_ContainerWater_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_ContainerWater_0')
    g = G(t)
    v0 = g.inp('s_Lloop0', False)
    v1 = g.inp('s_height', False)
    v2 = g.inp('s_heightPrev', False)
    v3 = g.inp('s_iteration', False)
    v4 = g.inp('s_offset', True)
    v5 = g.inp('s_offsetPrev', True)
    v6 = g.inp('s_texHit', False)
    v7 = g.inp('s_texPrev', False)
    v8 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v9 = g.math('ADD', g.inp('r_steps', False), 1)
    v10 = g.math('LESS_THAN', v3, v9)
    v11 = g.math('SUBTRACT', 1.0, v10)
    v12 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v13 = g.mixf(v12, v6, v7)
    v14 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v15 = g.mixf(v14, v0, 0.0)
    v16 = g.mixf(v11, v0, v15)
    v17 = g.mixf(v11, v6, v13)
    v18 = g.mixf(v8, v0, v16)
    v19 = g.mixf(v8, v6, v17)
    v20 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v21 = g.vmath('ADD', g.inp('r_uvNoise', True), v4)
    g.out_('F0_ParallaxNoiseMap_uv', v21, True)
    v22 = g.inp('F0_ParallaxNoiseMap', True, (1.0, 1.0, 1.0))
    v23 = g.inp('F0_ParallaxNoiseMap_alpha', False, 1.0)
    v24 = g.sep(v22)
    v25 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v26 = g.math('GREATER_THAN', v24[0], v1)
    v27 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v28 = g.mixf(v27, v19, v24[0])
    v29 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v30 = g.mixf(v29, v18, 0.0)
    v31 = g.mixf(v26, v18, v30)
    v32 = g.mixf(v26, v19, v28)
    v33 = g.mixf(v25, v18, v31)
    v34 = g.mixf(v25, v19, v32)
    v35 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v36 = g.mixv(v35, v5, v4)
    v37 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v38 = g.mixf(v37, v7, v24[0])
    v39 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v40 = g.mixf(v39, v2, v1)
    v41 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v42 = g.math('SUBTRACT', v1, g.inp('r_stepSize', False))
    v43 = g.mixf(v41, v1, v42)
    v44 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v45 = g.vmath('ADD', v4, g.inp('r_stepOffset', True))
    v46 = g.mixv(v44, v4, v45)
    v47 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v48 = g.math('ADD', v3, 1)
    v49 = g.mixf(v47, v3, v48)
    v50 = g.mixf(v0, v0, v33)
    v51 = g.mixf(v0, v1, v43)
    v52 = g.mixf(v0, v2, v40)
    v53 = g.mixf(v0, v3, v49)
    v54 = g.mixv(v0, v4, v46)
    v55 = g.mixv(v0, v5, v36)
    v56 = g.mixf(v0, v6, v34)
    v57 = g.mixf(v0, v7, v38)
    g.out_('o_Lloop0', v50, False)
    g.out_('o_height', v51, False)
    g.out_('o_heightPrev', v52, False)
    g.out_('o_iteration', v53, False)
    g.out_('o_offset', v54, True)
    g.out_('o_offsetPrev', v55, True)
    g.out_('o_texHit', v56, False)
    g.out_('o_texPrev', v57, False)


def build_RCE_Z_Ruri_Endfield_Scene_Leaf_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_Leaf_0')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_Grass_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_Grass_0')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_RCE_Z_Ruri_Endfield_Scene_Trunk_0():
    t = _tree('RCE_Z_Ruri_Endfield_Scene_Trunk_0')
    g = G(t)
    v0 = g.inp('s_H', True)
    v1 = g.inp('s_L', True)
    v2 = g.inp('s_LV', True)
    v3 = g.inp('s_N', True)
    v4 = g.inp('s_NoH', False)
    v5 = g.inp('s_NoL', False)
    v6 = g.inp('s_NoV', False)
    v7 = g.inp('s_P', True)
    v8 = g.inp('s_V', True)
    v9 = g.inp('s_VoH', False)
    v10 = g.inp('s_Lloop0', False)
    v11 = g.inp('s_color', True)
    v12 = g.inp('s_energy', True)
    v13 = g.inp('s_f0', True)
    v14 = g.inp('s_inputData_bakedGI', True)
    v15 = g.inp('s_inputData_fogCoord', False)
    v16 = g.inp('s_inputData_normalWS', True)
    v17 = g.inp('s_inputData_normalizedScreenSpaceUV', True)
    v18 = g.inp('s_inputData_positionCS', True)
    v19 = g.inp('s_inputData_positionCS_w', False)
    v20 = g.inp('s_inputData_positionWS', True)
    v21 = g.inp('s_inputData_shadowCoord', True)
    v22 = g.inp('s_inputData_shadowCoord_w', False)
    v23 = g.inp('s_inputData_shadowMask', True)
    v24 = g.inp('s_inputData_shadowMask_w', False)
    v25 = g.inp('s_inputData_vertexLighting', True)
    v26 = g.inp('s_inputData_viewDirectionWS', True)
    v27 = g.inp('s_lightIndex', False)
    v28 = g.inp('s_roughness', False)
    v29 = g.inp('s_sssAmount', False)
    v30 = g.math('LESS_THAN', v27, g.inp('r_pixelLightCount', False))
    v31 = g.math('MULTIPLY', v10, v30)
    v32 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    g.out_('C0_AdditionalLight_index', v27, False)
    g.out_('C0_AdditionalLight_position', v7, True)
    v33 = g.inp('C0_AdditionalLight_direction', True, (0.0, 0.0, 0.0))
    v34 = g.inp('C0_AdditionalLight_color', True, (0.0, 0.0, 0.0))
    v35 = g.inp('C0_AdditionalLight_distanceAttenuation', False, 0.0)
    v36 = g.inp('C0_AdditionalLight_shadowAttenuation', False, 0.0)
    v37 = g.inp('C0_AdditionalLight_layerMask', False, 0.0)
    v38 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v39 = g.mixv(v38, v1, v33)
    v40 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v41 = g.vmath('ADD', v39, v8)
    v42 = g.mixv(v40, v2, v41)
    v43 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v44 = g.vmath('DOT_PRODUCT', v42, v42)
    v45 = g.math('MAXIMUM', v44, 1E-08)
    v46 = g.math('INVERSE_SQRT', v45, 0.0)
    v47 = g.bc(v46)
    v48 = g.vmath('MULTIPLY', v42, v47)
    v49 = g.mixv(v43, v0, v48)
    v50 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v51 = g.vmath('DOT_PRODUCT', v39, v3)
    v52 = g.clampn(v51)
    v53 = g.mixf(v50, v5, v52)
    v54 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v55 = g.vmath('DOT_PRODUCT', v3, v49)
    v56 = g.clampn(v55)
    v57 = g.mixf(v54, v4, v56)
    v58 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v59 = g.vmath('DOT_PRODUCT', v8, v49)
    v60 = g.clampn(v59)
    v61 = g.mixf(v58, v9, v60)
    v62 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v63 = g.math('MULTIPLY', v28, v28)
    v64 = g.math('MULTIPLY', v28, v28)
    v65 = g.math('MULTIPLY', v63, v64)
    v66 = g.math('MINIMUM', v6, 1)
    v67 = g.math('MULTIPLY', v57, -1.0)
    v68 = g.math('MULTIPLY_ADD', v57, v65, v67)
    v69 = g.math('MULTIPLY_ADD', v68, v57, 1)
    v70 = g.math('MULTIPLY', v53, -1.0)
    v71 = g.math('MULTIPLY_ADD', v70, v65, v53)
    v72 = g.math('MULTIPLY_ADD', v71, v53, v65)
    v73 = g.math('SQRT', v72, 0.0)
    v74 = g.math('MULTIPLY', v66, v73)
    v75 = g.math('MULTIPLY', v66, -1.0)
    v76 = g.math('MULTIPLY_ADD', v75, v65, v66)
    v77 = g.math('MULTIPLY_ADD', v76, v66, v65)
    v78 = g.math('SQRT', v77, 0.0)
    v79 = g.math('MULTIPLY', v53, v78)
    v80 = g.math('ADD', v74, v79)
    v81 = g.math('ADD', v80, 0.0001)
    v82 = g.math('MULTIPLY', v69, v69)
    v83 = g.math('DIVIDE', v65, v82)
    v84 = g.math('DIVIDE', 0.5, v81)
    v85 = g.math('MULTIPLY', v83, v84)
    v86 = g.math('MULTIPLY', v61, -1.0)
    v87 = g.math('MULTIPLY_ADD', v86, 1, 1)
    v88 = g.math('MULTIPLY', v87, v87)
    v89 = g.math('MULTIPLY', v88, v88)
    v90 = g.math('MULTIPLY', v87, v89)
    v91 = g.math('MULTIPLY', v89, -1.0)
    v92 = g.math('MULTIPLY_ADD', v91, v87, 1)
    v93 = g.math('MULTIPLY', 1, -1.0)
    v94 = g.math('MULTIPLY_ADD', v28, v93, 1)
    v95 = g.math('MULTIPLY', v94, -1.0)
    v96 = g.math('MULTIPLY', 0.07619470357894897, -1.0)
    v97 = g.math('MULTIPLY_ADD', v95, 0.38302600383758545, v96)
    v98 = g.math('MULTIPLY_ADD', v94, v97, 1.049970030784607)
    v99 = g.math('MULTIPLY_ADD', v94, v98, 0.4092549979686737)
    v100 = g.math('MINIMUM', v99, 0.9990000128746033)
    v101 = g.math('MULTIPLY', v100, -1.0)
    v102 = g.math('MULTIPLY_ADD', v101, 1, 1)
    v103 = g.math('DIVIDE', v100, v102)
    v104 = g.vmath('SUBTRACT', (1, 1, 1), v13)
    v105 = g.vmath('MULTIPLY', v104, (0.047619047619, 0.047619047619, 0.047619047619))
    v106 = g.vmath('ADD', v105, v13)
    v107 = g.vmath('MULTIPLY', v106, v106)
    v108 = g.bc(v103)
    v109 = g.vmath('MULTIPLY', v108, v107)
    v110 = g.vmath('SCALE', v106, s=-1.0)
    v111 = g.comb(v102, v102, v102)
    v112 = g.vmath('MULTIPLY', v110, v111)
    v113 = g.vmath('ADD', v112, (1, 1, 1))
    v114 = g.vmath('DIVIDE', v109, v113)
    v115 = g.comb(v92, v92, v92)
    v116 = g.comb(v90, v90, v90)
    v117 = g.vmath('MULTIPLY', v13, v115)
    v118 = g.vmath('ADD', v117, v116)
    v119 = g.bc(v85)
    v120 = g.vmath('MULTIPLY', v119, v118)
    v121 = g.vmath('MINIMUM', v120, (2048, 2048, 2048))
    v122 = g.vmath('ADD', v114, v121)
    v123 = g.vmath('MAXIMUM', v122, (0, 0, 0))
    v124 = g.vmath('MINIMUM', v123, (1000, 1000, 1000))
    v125 = g.mixv(v62, v12, v124)
    v126 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v127 = g.comb(v53, v53, v53)
    v128 = g.bc(v53)
    v129 = g.vmath('MULTIPLY', g.inp('r_diffuse', True), v128)
    v130 = g.vmath('MULTIPLY', v125, v127)
    v131 = g.vmath('ADD', v130, v129)
    v132 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v133 = g.inp('_EnableSubsurface', False, 0.0)
    v134 = g.math('GREATER_THAN', v133, 0.5)
    v135 = g.vmath('DOT_PRODUCT', v39, v3)
    v136 = g.vmath('DOT_PRODUCT', v8, v39)
    v137 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v138 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v139 = g.math('MULTIPLY_ADD', v135, 0.6666666865348816, 0.3333333432674408)
    v140 = g.clampn(v139, 0, 1)
    v141 = g.math('SQRT', v140, 0.0)
    v142 = g.math('MULTIPLY', v140, v141)
    v143 = g.math('MULTIPLY', 1, -1.0)
    v144 = g.math('MULTIPLY_ADD', v142, 1.6666666269302368, v143)
    v145 = g.math('MULTIPLY_ADD', v29, v144, 1)
    v146 = g.math('MULTIPLY', v136, -1.0)
    v147 = g.clampn(v146, 0, 1)
    v148 = g.math('LOGARITHM', v147, 2.0)
    v149 = g.math('MULTIPLY', v148, 12)
    v150 = g.math('POWER', 2.0, v149)
    v151 = g.math('MULTIPLY', 2.9000000953674316, -1.0)
    v152 = g.math('MULTIPLY_ADD', v29, v151, 3)
    v153 = g.math('MULTIPLY', v150, v152)
    v154 = g.math('MULTIPLY', v145, -1.0)
    v155 = g.math('MULTIPLY_ADD', v154, 0.15915493667125702, 1)
    v156 = g.math('MULTIPLY', v145, 0.15915493667125702)
    v157 = g.math('MULTIPLY_ADD', v153, v155, v156)
    v158 = g.math('MULTIPLY', v137, v138)
    v159 = g.math('SUBTRACT', v135, v158)
    v160 = g.math('ADD', v159, 2)
    v161 = g.clampn(v160, 0, 1)
    v162 = g.math('MULTIPLY', v157, v161)
    v163 = g.bc(v162)
    v164 = g.vmath('MULTIPLY', v163, g.inp('r_sssTint', True))
    v165 = g.vmath('ADD', v131, v164)
    v166 = g.mixv(v134, v131, v165)
    v167 = g.mixv(v132, v131, v166)
    v168 = g.math('SUBTRACT', 1.0, g.inp('r___done', False))
    v169 = g.math('MULTIPLY', v35, v36)
    v170 = g.bc(v169)
    v171 = g.vmath('MULTIPLY', v34, v170)
    v172 = g.vmath('MULTIPLY', v167, v171)
    v173 = g.vmath('ADD', v11, v172)
    v174 = g.mixv(v168, v11, v173)
    v175 = g.mixv(v31, v0, v49)
    v176 = g.mixv(v31, v1, v39)
    v177 = g.mixv(v31, v2, v42)
    v178 = g.mixf(v31, v4, v57)
    v179 = g.mixf(v31, v5, v53)
    v180 = g.mixf(v31, v9, v61)
    v181 = g.mixv(v31, v11, v174)
    v182 = g.mixv(v31, v12, v125)
    v183 = g.math('ADD', v27, 1)
    g.out_('o_H', v175, True)
    g.out_('o_L', v176, True)
    g.out_('o_LV', v177, True)
    g.out_('o_N', v3, True)
    g.out_('o_NoH', v178, False)
    g.out_('o_NoL', v179, False)
    g.out_('o_NoV', v6, False)
    g.out_('o_P', v7, True)
    g.out_('o_V', v8, True)
    g.out_('o_VoH', v180, False)
    g.out_('o_Lloop0', v31, False)
    g.out_('o_color', v181, True)
    g.out_('o_energy', v182, True)
    g.out_('o_f0', v13, True)
    g.out_('o_inputData_bakedGI', v14, True)
    g.out_('o_inputData_fogCoord', v15, False)
    g.out_('o_inputData_normalWS', v16, True)
    g.out_('o_inputData_normalizedScreenSpaceUV', v17, True)
    g.out_('o_inputData_positionCS', v18, True)
    g.out_('o_inputData_positionCS_w', v19, False)
    g.out_('o_inputData_positionWS', v20, True)
    g.out_('o_inputData_shadowCoord', v21, True)
    g.out_('o_inputData_shadowCoord_w', v22, False)
    g.out_('o_inputData_shadowMask', v23, True)
    g.out_('o_inputData_shadowMask_w', v24, False)
    g.out_('o_inputData_vertexLighting', v25, True)
    g.out_('o_inputData_viewDirectionWS', v26, True)
    g.out_('o_lightIndex', v183, False)
    g.out_('o_roughness', v28, False)
    g.out_('o_sssAmount', v29, False)


def build_Ruri_Endfield_Scene_Lit():
    t = _tree('Ruri Endfield Scene Lit')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.vmath('SUBTRACT', v14, v0)
    v133 = g.comb(v77, v77, 0.0)
    v134 = g.vmath('MULTIPLY', v133, v132)
    v135 = g.vmath('ADD', v134, v0)
    v136 = g.comb(v83[0], v83[1], 0.0)
    v137 = g.comb(v83[2], v82, 0.0)
    v138 = g.vmath('MULTIPLY', v135, v136)
    v139 = g.vmath('ADD', v138, v137)
    v140 = g.comb(v88, v88, 0.0)
    v141 = g.vmath('MULTIPLY', v140, v132)
    v142 = g.vmath('ADD', v141, v0)
    v143 = g.comb(v94[0], v94[1], 0.0)
    v144 = g.comb(v94[2], v93, 0.0)
    v145 = g.vmath('MULTIPLY', v142, v143)
    v146 = g.vmath('ADD', v145, v144)
    g.out_('F4_BaseColorMap_uv', v139, True)
    v147 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v148 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    g.out_('F5_NormalMap_uv', v146, True)
    v149 = g.inp('F5_NormalMap', True, (1.0, 1.0, 1.0))
    v150 = g.inp('F5_NormalMap_alpha', False, 1.0)
    v151 = g.math('MULTIPLY', v58, v148)
    g.out_('F6_MROMap_uv', v146, True)
    v152 = g.inp('F6_MROMap', True, (1.0, 1.0, 1.0))
    v153 = g.inp('F6_MROMap_alpha', False, 1.0)
    v154 = g.sep(v152)
    v155 = g.sep(v149)
    v156 = g.math('MULTIPLY', v155[0], v150)
    v157 = g.math('MULTIPLY_ADD', v156, 2, -1)
    v158 = g.math('MULTIPLY_ADD', v155[1], 2, -1)
    v159 = g.inp('_NormalScale', False, 0.0)
    v160 = g.math('MULTIPLY', v157, v159)
    v161 = g.math('MULTIPLY', v158, v159)
    v162 = g.vmath('MULTIPLY', v147, v37)
    v163 = g.inp('_BaseColorBrighterScale', False, 1.0)
    v164 = g.bc(v163)
    v165 = g.vmath('MULTIPLY', v162, v164)
    v166 = g.sep(v165)
    v167 = g.clampn(v166[0])
    v168 = g.clampn(v166[1])
    v169 = g.clampn(v166[2])
    v170 = g.comb(v167, v168, v169)
    v171 = g.inp('_BaseColorTintCover', False, 0.0)
    v172 = g.mixv(v171, v170, v37)
    v173 = g.inp('_RoughnessMin', False, 0.0)
    v174 = g.inp('_RoughnessMax', False, 1.0)
    v175 = g.mixf(v154[1], v173, v174)
    v176 = g.inp('_Metallic', False, 0.0)
    v177 = g.inp('_BaseTextureMapCount', False, 0.0)
    v178 = g.math('SUBTRACT', v177, 1)
    v179 = g.clampn(v178)
    v180 = g.mixf(v179, v154[0], v176)
    v181 = g.inp('_OcclusionStrength', False, 1.0)
    v182 = g.mixf(v181, 1, v154[2])
    v183 = g.inp('_PorosityFactorX', False, 0.2)
    v184 = g.math('MULTIPLY', v183, v175)
    v185 = g.inp('_PorosityFactorZ', False, 0.0)
    v186 = g.math('MULTIPLY', v185, v180)
    v187 = g.math('ADD', v184, v186)
    v188 = g.inp('_PorosityFactorY', False, 0.4)
    v189 = g.math('ADD', v187, v188)
    v190 = g.clampn(v189)
    v191 = g.math('MULTIPLY', v190, 0.95)
    v192 = g.math('ADD', v191, 0.05)
    v193 = g.inp('_DisableVerticalFlow', False, 0.0)
    v194 = g.math('SUBTRACT', 1, v193)
    v195 = g.math('MULTIPLY', v192, v194)
    v196 = g.vmath('DOT_PRODUCT', v36, v21)
    v197 = g.math('LESS_THAN', v196, 0)
    v198 = g.mixf(v197, 1, -1)
    v199 = g.comb(v157, v158, 0.0)
    v200 = g.comb(v157, v158, 0.0)
    v201 = g.vmath('DOT_PRODUCT', v199, v200)
    v202 = g.math('MINIMUM', v201, 1)
    v203 = g.math('SUBTRACT', 1, v202)
    v204 = g.math('SQRT', v203, 0.0)
    v205 = g.math('MAXIMUM', v204, 1.0000000168623835E-16)
    v206 = g.math('MULTIPLY', v205, v198)
    v207 = g.math('GREATER_THAN', v5, 0)
    v208 = g.mixf(v207, -1, 1)
    v209 = g.vmath('CROSS_PRODUCT', v21, v22)
    v210 = g.bc(v208)
    v211 = g.vmath('MULTIPLY', v210, v209)
    v212 = g.bc(v206)
    v213 = g.vmath('MULTIPLY', v21, v212)
    v214 = g.bc(v160)
    v215 = g.vmath('MULTIPLY', v22, v214)
    v216 = g.vmath('ADD', v213, v215)
    v217 = g.bc(v161)
    v218 = g.vmath('MULTIPLY', v211, v217)
    v219 = g.vmath('ADD', v216, v218)
    v220 = g.vmath('NORMALIZE', v219)
    v221 = g.mixf(v121, v58, v151)
    v222 = g.mixv(v121, v65, v172)
    v223 = g.mixf(v121, v114, v175)
    v224 = g.mixf(v121, v115, v180)
    v225 = g.mixf(v121, v116, v182)
    v226 = g.mixf(v121, v117, v195)
    v227 = g.mixv(v121, v36, v220)
    v228 = g.vmath('NORMALIZE', v21)
    v229 = g.vmath('NORMALIZE', v22)
    v230 = g.vmath('CROSS_PRODUCT', v228, v229)
    v231 = g.bc(v5)
    v232 = g.vmath('MULTIPLY', v230, v231)
    v233 = g.inp('_UseMacroNormalMap', False, 0.0)
    v234 = g.math('GREATER_THAN', v233, 0.5)
    g.out_('F7_MacroNormalMap_uv', v0, True)
    v235 = g.inp('F7_MacroNormalMap', True, (1.0, 1.0, 1.0))
    v236 = g.inp('F7_MacroNormalMap_alpha', False, 1.0)
    v237 = g.inp('_MacroNormalMapScale', False, 1.0)
    v238 = g.sep(v235)
    v239 = g.math('MULTIPLY', v236, v238[0])
    v240 = g.math('MULTIPLY', v239, 2.0)
    v241 = g.math('SUBTRACT', v240, 1.0)
    v242 = g.math('MULTIPLY', v238[1], 2.0)
    v243 = g.math('SUBTRACT', v242, 1.0)
    v244 = g.math('MULTIPLY', v241, v241)
    v245 = g.math('MULTIPLY', v243, v243)
    v246 = g.math('ADD', v244, v245)
    v247 = g.clampn(v246)
    v248 = g.math('SUBTRACT', 1.0, v247)
    v249 = g.math('SQRT', v248, 0.0)
    v250 = g.math('MAXIMUM', v249, 1e-16)
    v251 = g.math('MULTIPLY', v241, v237)
    v252 = g.math('MULTIPLY', v243, v237)
    v253 = g.comb(v251, v252, v250)
    v254 = g.sep(v253)
    v255 = g.bc(v254[0])
    v256 = g.vmath('MULTIPLY', v255, v229)
    v257 = g.bc(v254[1])
    v258 = g.vmath('MULTIPLY', v257, v232)
    v259 = g.vmath('ADD', v256, v258)
    v260 = g.bc(v254[2])
    v261 = g.vmath('MULTIPLY', v260, v228)
    v262 = g.vmath('ADD', v259, v261)
    v263 = g.vmath('NORMALIZE', v262)
    v264 = g.vmath('SUBTRACT', v263, v228)
    v265 = g.vmath('ADD', v227, v264)
    v266 = g.vmath('NORMALIZE', v265)
    v267 = g.mixv(v234, v227, v266)
    v268 = g.inp('_EnableDetailMap', False, 0.0)
    v269 = g.math('GREATER_THAN', v268, 0.5)
    v270 = g.vmath('DISTANCE', v20, v26)
    v271 = g.inp('_DetailFalloffStart', False, 750.0)
    v272 = g.math('SUBTRACT', v270, v271)
    v273 = g.inp('_DetailFalloffEnd', False, 800.0)
    v274 = g.math('SUBTRACT', v273, v271)
    v275 = g.math('MAXIMUM', v274, 0.001)
    v276 = g.math('DIVIDE', v272, v275)
    v277 = g.clampn(v276)
    v278 = g.math('SUBTRACT', 1, v277)
    g.out_('F8_DetailMap_uv', v0, True)
    v279 = g.inp('F8_DetailMap', True, (1.0, 1.0, 1.0))
    v280 = g.inp('F8_DetailMap_alpha', False, 1.0)
    v281 = g.inp('_DetailMaskMode', False, 0.0)
    v282 = g.math('LESS_THAN', v281, 0.5)
    v283 = g.math('LESS_THAN', v281, 1.5)
    v284 = g.math('LESS_THAN', v281, 2.5)
    v285 = g.math('LESS_THAN', v281, 3.5)
    g.out_('F9_NormalMap_uv', v0, True)
    v286 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v287 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v288 = g.sep(v286)
    v289 = g.math('LESS_THAN', v281, 4.5)
    v290 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v291 = g.inp('F9_NormalMap_alpha', False, 1.0)
    g.out_('F10_MROMap_uv', v0, True)
    v292 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v293 = g.inp('F10_MROMap_alpha', False, 1.0)
    v294 = g.mixf(v289, v293, v291)
    v295 = g.mixf(v285, v294, v288[2])
    v296 = g.mixf(v284, v295, v221)
    v297 = g.mixf(v283, v296, v280)
    v298 = g.mixf(v282, v297, 1)
    v299 = g.math('MULTIPLY', v278, v298)
    v300 = g.sep(v279)
    v301 = g.comb(v300[0], v300[1], 0.0)
    v302 = g.vmath('MULTIPLY', v301, (2, 2, 0.0))
    v303 = g.vmath('SUBTRACT', v302, (1, 1, 0.0))
    v304 = g.inp('_DetailNormalIntensity', False, 1.0)
    v305 = g.math('MULTIPLY', v304, v299)
    v306 = g.comb(v305, v305, 0.0)
    v307 = g.vmath('MULTIPLY', v303, v306)
    v308 = g.sep(v307)
    v309 = g.comb(v308[0], v308[1], 1)
    v310 = g.sep(v309)
    v311 = g.bc(v310[0])
    v312 = g.vmath('MULTIPLY', v311, v229)
    v313 = g.bc(v310[1])
    v314 = g.vmath('MULTIPLY', v313, v232)
    v315 = g.vmath('ADD', v312, v314)
    v316 = g.bc(v310[2])
    v317 = g.vmath('MULTIPLY', v316, v228)
    v318 = g.vmath('ADD', v315, v317)
    v319 = g.vmath('NORMALIZE', v318)
    v320 = g.vmath('SUBTRACT', v319, v228)
    v321 = g.vmath('ADD', v267, v320)
    v322 = g.vmath('NORMALIZE', v321)
    v323 = g.inp('_DetailMode', False, 0.0)
    v324 = g.math('LESS_THAN', v323, 0.5)
    v325 = g.inp('_DetailBaseColorBrighterScale', False, 1.0)
    v326 = g.mixf(v299, 1, v325)
    v327 = g.bc(v326)
    v328 = g.vmath('MULTIPLY', v222, v327)
    v329 = g.sep(v328)
    v330 = g.clampn(v329[0])
    v331 = g.clampn(v329[1])
    v332 = g.clampn(v329[2])
    v333 = g.comb(v330, v331, v332)
    v334 = g.inp('_DetailOverlayColor', True, (0.0, 0.0, 0.0))
    v335 = g.inp('_DetailOverlayColor_w', False, 0.0)
    v336 = g.vmath('MULTIPLY', v333, v334)
    v337 = g.math('MULTIPLY', v299, v335)
    v338 = g.mixv(v337, v333, v336)
    v339 = g.inp('_DetailPBRIntensity', False, 1.0)
    v340 = g.math('MULTIPLY', v299, v339)
    v341 = g.mixf(v340, v223, v300[2])
    v342 = g.math('MULTIPLY', v299, v339)
    v343 = g.mixf(v342, v223, v300[2])
    v344 = g.math('MULTIPLY', v225, v280)
    v345 = g.mixf(v299, v225, v344)
    v346 = g.mixv(v324, v222, v338)
    v347 = g.mixf(v324, v343, v341)
    v348 = g.mixf(v324, v345, v225)
    v349 = g.mixv(v269, v222, v346)
    v350 = g.mixf(v269, v223, v347)
    v351 = g.mixf(v269, v225, v348)
    v352 = g.mixv(v269, v267, v322)
    v353 = g.bc(v163)
    v354 = g.vmath('MULTIPLY', v349, v353)
    v355 = g.sep(v354)
    v356 = g.clampn(v355[0])
    v357 = g.clampn(v355[1])
    v358 = g.clampn(v355[2])
    v359 = g.comb(v356, v357, v358)
    v360 = g.mixv(v171, v359, v37)
    v361 = g.mixf(v350, v173, v174)
    v362 = g.clampn(v361, 0, 1)
    v363 = g.mixf(v181, 1, v351)
    v364 = g.math('LESS_THAN', v177, 0.5)
    v365 = g.math('SUBTRACT', 1.0, v364)
    v366 = g.mixf(v365, v224, v115)
    v367 = g.inp('_EnableTriChannelMask', False, 0.0)
    v368 = g.math('GREATER_THAN', v367, 0.5)
    v369 = g.vmath('SUBTRACT', v14, v0)
    v370 = g.inp('_MaskUVSet', False, 0.0)
    v371 = g.comb(v370, v370, 0.0)
    v372 = g.vmath('MULTIPLY', v371, v369)
    v373 = g.vmath('ADD', v372, v0)
    v374 = g.inp('_MaskMap_ST', True, (1.0, 1.0, 0.0))
    v375 = g.inp('_MaskMap_ST_w', False, 0.0)
    v376 = g.sep(v374)
    v377 = g.comb(v376[0], v376[1], 0.0)
    v378 = g.comb(v376[2], v375, 0.0)
    v379 = g.vmath('MULTIPLY', v373, v377)
    v380 = g.vmath('ADD', v379, v378)
    g.out_('F11_MaskMap_uv', v380, True)
    v381 = g.inp('F11_MaskMap', True, (1.0, 1.0, 1.0))
    v382 = g.inp('F11_MaskMap_alpha', False, 1.0)
    v383 = g.sep(v381)
    v384 = g.inp('_MaskBOffset', False, 0.0)
    v385 = g.math('ADD', v383[2], v384)
    v386 = g.inp('_MaskBScale', False, 0.0)
    v387 = g.math('ADD', v386, 1)
    v388 = g.math('MULTIPLY', v386, -0.5)
    v389 = g.math('MULTIPLY_ADD', v385, v387, v388)
    v390 = g.clampn(v389, 0, 1)
    v391 = g.inp('_MaskAlbedoB', True, (0.0, 0.0, 1.0))
    v392 = g.inp('_MaskAlbedoB_w', False, 1.0)
    v393 = g.math('MULTIPLY', v390, v392)
    v394 = g.inp('_MaskGOffset', False, 0.0)
    v395 = g.math('ADD', v383[1], v394)
    v396 = g.inp('_MaskGScale', False, 0.0)
    v397 = g.math('ADD', v396, 1)
    v398 = g.math('MULTIPLY', v396, -0.5)
    v399 = g.math('MULTIPLY_ADD', v395, v397, v398)
    v400 = g.clampn(v399, 0, 1)
    v401 = g.inp('_MaskAlbedoG', True, (0.0, 1.0, 0.0))
    v402 = g.inp('_MaskAlbedoG_w', False, 1.0)
    v403 = g.math('MULTIPLY', v400, v402)
    v404 = g.inp('_MaskROffset', False, 0.0)
    v405 = g.math('ADD', v383[0], v404)
    v406 = g.inp('_MaskRScale', False, 0.0)
    v407 = g.math('ADD', v406, 1)
    v408 = g.math('MULTIPLY', v406, -0.5)
    v409 = g.math('MULTIPLY_ADD', v405, v407, v408)
    v410 = g.clampn(v409, 0, 1)
    v411 = g.inp('_MaskAlbedoR', True, (1.0, 0.0, 0.0))
    v412 = g.inp('_MaskAlbedoR_w', False, 1.0)
    v413 = g.math('MULTIPLY', v410, v412)
    v414 = g.mixv(v393, v360, v391)
    v415 = g.mixv(v403, v414, v401)
    v416 = g.mixv(v413, v415, v411)
    v417 = g.inp('_MaskRoghnessB', False, 0.25)
    v418 = g.mixf(v393, v362, v417)
    v419 = g.inp('_MaskRoghnessG', False, 0.25)
    v420 = g.mixf(v403, v418, v419)
    v421 = g.inp('_MaskRoghnessR', False, 0.25)
    v422 = g.mixf(v413, v420, v421)
    v423 = g.inp('_MaskMetallicB', False, 0.0)
    v424 = g.mixf(v393, v366, v423)
    v425 = g.inp('_MaskMetallicG', False, 0.0)
    v426 = g.mixf(v403, v424, v425)
    v427 = g.inp('_MaskMetallicR', False, 0.0)
    v428 = g.mixf(v413, v426, v427)
    v429 = g.mixv(v368, v360, v416)
    v430 = g.mixf(v368, v362, v422)
    v431 = g.mixf(v368, v366, v428)
    v432 = g.inp('_LayerBlend', False, 0.0)
    v433 = g.math('GREATER_THAN', v432, 0.5)
    v434 = g.inp('_LayerBlendUVType', False, 0.0)
    v435 = g.math('GREATER_THAN', v434, 0.5)
    v436 = g.math('LESS_THAN', v434, 1.5)
    v437 = g.math('MULTIPLY', v435, v436)
    v438 = g.sep(v20)
    v439 = g.comb(v438[0], v438[2], 0.0)
    v440 = g.inp('_Layer1Tilling', False, 1.0)
    v441 = g.comb(v440, v440, 0.0)
    v442 = g.vmath('MULTIPLY', v439, v441)
    v443 = g.math('GREATER_THAN', v434, 1.5)
    v444 = g.comb(v440, v440, 0.0)
    v445 = g.vmath('MULTIPLY', v15, v444)
    v446 = g.comb(v440, v440, 0.0)
    v447 = g.vmath('MULTIPLY', v0, v446)
    v448 = g.mixv(v443, v447, v445)
    v449 = g.mixv(v437, v448, v442)
    g.out_('F12_Layer1BaseMap_uv', v449, True)
    v450 = g.inp('F12_Layer1BaseMap', True, (1.0, 1.0, 1.0))
    v451 = g.inp('F12_Layer1BaseMap_alpha', False, 1.0)
    g.out_('F13_Layer1BumpMap_uv', v449, True)
    v452 = g.inp('F13_Layer1BumpMap', True, (1.0, 1.0, 1.0))
    v453 = g.inp('F13_Layer1BumpMap_alpha', False, 1.0)
    g.out_('F14_BaseHeightMap_uv', v449, True)
    v454 = g.inp('F14_BaseHeightMap', True, (1.0, 1.0, 1.0))
    v455 = g.inp('F14_BaseHeightMap_alpha', False, 1.0)
    v456 = g.sep(v454)
    v457 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v458 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v459 = g.sep(v457)
    v460 = g.math('MULTIPLY_ADD', v459[0], 2, -1)
    v461 = g.math('MULTIPLY_ADD', v459[1], 2, -1)
    v462 = g.math('ABSOLUTE', v460, 0.0)
    v463 = g.math('LESS_THAN', v462, 0.012000000104308128)
    v464 = g.mixf(v463, v460, 0)
    v465 = g.math('ABSOLUTE', v461, 0.0)
    v466 = g.math('LESS_THAN', v465, 0.012000000104308128)
    v467 = g.mixf(v466, v461, 0)
    v468 = g.math('MULTIPLY', v464, v159)
    v469 = g.math('MULTIPLY', v467, v159)
    v470 = g.vmath('DOT_PRODUCT', v352, v21)
    v471 = g.math('LESS_THAN', v470, 0)
    v472 = g.mixf(v471, 1, -1)
    v473 = g.inp('_LayerBlendType', False, 1.0)
    v474 = g.math('LESS_THAN', v473, 0.5)
    v475 = g.sep(v11)
    v476 = g.math('SUBTRACT', 1, v475[0])
    v477 = g.clampn(v476, 0, 1)
    v478 = g.math('LESS_THAN', v473, 1.5)
    v479 = g.inp('_LayerBlendMaskUVType', False, 0.0)
    v480 = g.math('GREATER_THAN', v479, 0.5)
    v481 = g.mixv(v480, v14, v0)
    v482 = g.inp('_LayerBlendMaskType', False, 0.0)
    v483 = g.math('COMPARE', v482, 0, 1e-05)
    v484 = g.math('SUBTRACT', 1.0, v483)
    g.out_('F15_LayerBlendMaskMap_uv', v481, True)
    v485 = g.inp('F15_LayerBlendMaskMap', True, (1.0, 1.0, 1.0))
    v486 = g.inp('F15_LayerBlendMaskMap_alpha', False, 1.0)
    v487 = g.sep(v485)
    v488 = g.mixf(v484, v487[0], v458)
    v489 = g.clampn(v488, 0, 1)
    v490 = g.math('GREATER_THAN', v5, 0)
    v491 = g.mixf(v490, -1, 1)
    v492 = g.vmath('CROSS_PRODUCT', v21, v22)
    v493 = g.bc(v491)
    v494 = g.vmath('MULTIPLY', v493, v492)
    v495 = g.comb(v464, v467, 0.0)
    v496 = g.comb(v464, v467, 0.0)
    v497 = g.vmath('DOT_PRODUCT', v495, v496)
    v498 = g.math('MINIMUM', v497, 1)
    v499 = g.math('SUBTRACT', 1, v498)
    v500 = g.math('SQRT', v499, 0.0)
    v501 = g.bc(v500)
    v502 = g.vmath('MULTIPLY', v21, v501)
    v503 = g.bc(v468)
    v504 = g.vmath('MULTIPLY', v22, v503)
    v505 = g.vmath('ADD', v502, v504)
    v506 = g.bc(v469)
    v507 = g.vmath('MULTIPLY', v494, v506)
    v508 = g.vmath('ADD', v505, v507)
    v509 = g.inp('_TopBlendWithBumpMap', False, 0.0)
    v510 = g.sep(v508)
    v511 = g.vmath('DOT_PRODUCT', v508, v508)
    v512 = g.math('INVERSE_SQRT', v511, 0.0)
    v513 = g.math('MULTIPLY', v510[1], v512)
    v514 = g.sep(v21)
    v515 = g.math('SUBTRACT', v513, v514[1])
    v516 = g.math('MULTIPLY_ADD', v509, v515, v514[1])
    v517 = g.inp('_TopBlendThreshold', False, 0.5)
    v518 = g.math('SUBTRACT', v516, v517)
    v519 = g.inp('_TopBlendSmoothness', False, 0.5)
    v520 = g.math('MAXIMUM', 1.1754943508222875E-38, v519)
    v521 = g.math('DIVIDE', v518, v520)
    v522 = g.clampn(v521, 0, 1)
    v523 = g.mixf(v478, v522, v489)
    v524 = g.mixf(v474, v523, v477)
    v525 = g.math('SUBTRACT', 1, v524)
    v526 = g.math('MULTIPLY', v451, v524)
    v527 = g.math('MULTIPLY', v456[0], v525)
    v528 = g.math('MAXIMUM', v526, v527)
    v529 = g.math('MULTIPLY', v528, -1.0)
    v530 = g.inp('_LayerBlendHeightTransition', False, 1.0)
    v531 = g.math('MULTIPLY_ADD', v524, v451, v530)
    v532 = g.math('ADD', v529, v531)
    v533 = g.math('MAXIMUM', v532, 0)
    v534 = g.math('ADD', v533, 9.999999974752427E-07)
    v535 = g.math('MULTIPLY', v524, v534)
    v536 = g.math('MULTIPLY', v528, -1.0)
    v537 = g.math('MULTIPLY_ADD', v525, v456[0], v530)
    v538 = g.math('ADD', v536, v537)
    v539 = g.math('MAXIMUM', v538, 0)
    v540 = g.math('ADD', v539, 9.999999974752427E-07)
    v541 = g.math('MULTIPLY', v525, v540)
    v542 = g.inp('_LayerBlendHeight', False, 1.0)
    v543 = g.math('COMPARE', v542, 0, 1e-05)
    v544 = g.math('SUBTRACT', 1.0, v543)
    v545 = g.math('ADD', v535, v541)
    v546 = g.math('MAXIMUM', v545, 1.1754943508222875E-38)
    v547 = g.math('DIVIDE', v535, v546)
    v548 = g.mixf(v544, v524, v547)
    v549 = g.vmath('DOT_PRODUCT', v450, (0.2126729041337967, 0.7151522040367126, 0.07217500358819962))
    v550 = g.inp('_Layer1Saturation', False, 0.0)
    v551 = g.math('ADD', 1, v550)
    v552 = g.clampn(v551, 0, 1)
    v553 = g.math('MULTIPLY', v549, -1.0)
    v554 = g.sep(v450)
    v555 = g.math('ADD', v553, v554[0])
    v556 = g.math('MULTIPLY_ADD', v552, v555, v549)
    v557 = g.math('MULTIPLY', v549, -1.0)
    v558 = g.math('ADD', v557, v554[1])
    v559 = g.math('MULTIPLY_ADD', v552, v558, v549)
    v560 = g.math('MULTIPLY', v549, -1.0)
    v561 = g.math('ADD', v560, v554[2])
    v562 = g.math('MULTIPLY_ADD', v552, v561, v549)
    v563 = g.comb(v556, v559, v562)
    v564 = g.inp('_Layer1TintColor', True, (1.0, 1.0, 1.0))
    v565 = g.inp('_Layer1TintColor_w', False, 1.0)
    v566 = g.vmath('MULTIPLY', v563, v564)
    v567 = g.inp('_Layer1ColorBrighterScale', False, 1.0)
    v568 = g.bc(v567)
    v569 = g.vmath('MULTIPLY', v566, v568)
    v570 = g.mixv(v548, v429, v569)
    v571 = g.inp('_LayerMetallicType', False, 0.0)
    v572 = g.math('COMPARE', v571, 0, 1e-05)
    v573 = g.math('SUBTRACT', 1.0, v572)
    v574 = g.inp('_Layer1Metallic', False, 0.0)
    v575 = g.mixf(v573, v574, v451)
    v576 = g.mixf(v548, v431, v575)
    v577 = g.sep(v452)
    v578 = g.mixf(v548, v430, v577[2])
    v579 = g.inp('_Layer1AOStrength', False, 1.0)
    v580 = g.mixf(v579, 1, v453)
    v581 = g.mixf(v548, v363, v580)
    v582 = g.math('MULTIPLY_ADD', v577[0], 2, -1)
    v583 = g.math('MULTIPLY_ADD', v577[1], 2, -1)
    v584 = g.math('ABSOLUTE', v582, 0.0)
    v585 = g.math('LESS_THAN', v584, 0.012000000104308128)
    v586 = g.mixf(v585, v582, 0)
    v587 = g.math('ABSOLUTE', v583, 0.0)
    v588 = g.math('LESS_THAN', v587, 0.012000000104308128)
    v589 = g.mixf(v588, v583, 0)
    v590 = g.inp('_Layer1BumpScale', False, 1.0)
    v591 = g.math('MULTIPLY', v586, v590)
    v592 = g.math('MULTIPLY', v589, v590)
    v593 = g.comb(v586, v589, 0.0)
    v594 = g.comb(v586, v589, 0.0)
    v595 = g.vmath('DOT_PRODUCT', v593, v594)
    v596 = g.math('MINIMUM', v595, 1)
    v597 = g.math('SUBTRACT', 1, v596)
    v598 = g.math('SQRT', v597, 0.0)
    v599 = g.math('MAXIMUM', v598, 1.0000000168623835E-16)
    v600 = g.comb(v464, v467, 0.0)
    v601 = g.comb(v464, v467, 0.0)
    v602 = g.vmath('DOT_PRODUCT', v600, v601)
    v603 = g.math('SUBTRACT', 1, v602)
    v604 = g.math('MAXIMUM', v603, 0)
    v605 = g.math('SQRT', v604, 0.0)
    v606 = g.math('ADD', v605, 1)
    v607 = g.comb(v468, v469, v606)
    v608 = g.math('MULTIPLY', v591, -1.0)
    v609 = g.math('MULTIPLY', v592, -1.0)
    v610 = g.comb(v608, v609, v599)
    v611 = g.vmath('DOT_PRODUCT', v607, v610)
    v612 = g.inp('_Layer1BaseNormalIntensity', False, 0.0)
    v613 = g.math('MULTIPLY', v591, -1.0)
    v614 = g.math('MULTIPLY', v611, v468)
    v615 = g.math('DIVIDE', v614, v606)
    v616 = g.math('ADD', v591, v615)
    v617 = g.math('ADD', v613, v616)
    v618 = g.math('MULTIPLY_ADD', v612, v617, v591)
    v619 = g.math('MULTIPLY', v592, -1.0)
    v620 = g.math('MULTIPLY', v611, v469)
    v621 = g.math('DIVIDE', v620, v606)
    v622 = g.math('ADD', v592, v621)
    v623 = g.math('ADD', v619, v622)
    v624 = g.math('MULTIPLY_ADD', v612, v623, v592)
    v625 = g.math('MULTIPLY', v599, -1.0)
    v626 = g.math('SUBTRACT', v611, v599)
    v627 = g.math('ADD', v625, v626)
    v628 = g.math('MULTIPLY_ADD', v612, v627, v599)
    v629 = g.comb(v464, v467, 0.0)
    v630 = g.comb(v464, v467, 0.0)
    v631 = g.vmath('DOT_PRODUCT', v629, v630)
    v632 = g.math('SUBTRACT', 1, v631)
    v633 = g.math('MAXIMUM', v632, 0)
    v634 = g.math('SQRT', v633, 0.0)
    v635 = g.math('MULTIPLY', v468, -1.0)
    v636 = g.math('ADD', v635, v618)
    v637 = g.math('MULTIPLY_ADD', v548, v636, v468)
    v638 = g.math('MULTIPLY', v469, -1.0)
    v639 = g.math('ADD', v638, v624)
    v640 = g.math('MULTIPLY_ADD', v548, v639, v469)
    v641 = g.math('MULTIPLY', v634, -1.0)
    v642 = g.math('ADD', v641, v628)
    v643 = g.math('MULTIPLY_ADD', v548, v642, v634)
    v644 = g.math('MULTIPLY', v472, v643)
    v645 = g.math('GREATER_THAN', v5, 0)
    v646 = g.mixf(v645, -1, 1)
    v647 = g.vmath('CROSS_PRODUCT', v21, v22)
    v648 = g.bc(v646)
    v649 = g.vmath('MULTIPLY', v648, v647)
    v650 = g.bc(v644)
    v651 = g.vmath('MULTIPLY', v21, v650)
    v652 = g.bc(v637)
    v653 = g.vmath('MULTIPLY', v22, v652)
    v654 = g.vmath('ADD', v651, v653)
    v655 = g.bc(v640)
    v656 = g.vmath('MULTIPLY', v649, v655)
    v657 = g.vmath('ADD', v654, v656)
    v658 = g.vmath('NORMALIZE', v657)
    v659 = g.mixv(v433, v429, v570)
    v660 = g.mixf(v433, v430, v578)
    v661 = g.mixf(v433, v431, v576)
    v662 = g.mixf(v433, v363, v581)
    v663 = g.mixv(v433, v352, v658)
    v664 = g.mixf(v433, v157, v464)
    v665 = g.mixf(v433, v158, v467)
    v666 = g.mixf(v433, v160, v637)
    v667 = g.mixf(v433, v161, v640)
    v668 = g.mixf(v433, v198, v472)
    v669 = g.inp('_EmissionTint', True, (1.0, 1.0, 1.0))
    v670 = g.inp('_EmissionTint_w', False, 1.0)
    v671 = g.vmath('MULTIPLY', v659, v669)
    v672 = g.inp('_EmissiveIntensity', False, 0.0)
    v673 = g.bc(v672)
    v674 = g.vmath('MULTIPLY', v671, v673)
    v675 = g.inp('_UseEmissiveMap', False, 0.0)
    v676 = g.math('GREATER_THAN', v675, 0.5)
    v677 = g.inp('_AlbedoAffectEmissive', False, 1.0)
    v678 = g.mixv(v677, v659, (1, 1, 1))
    v679 = g.inp('_EnableEmissiveAnim', False, 0.0)
    v680 = g.math('GREATER_THAN', v679, 0.5)
    v681 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v682 = g.inp('_Time_w', False, 0.0)
    v683 = g.sep(v681)
    v684 = g.inp('_EmissiveAnimSpeed', False, 0.0)
    v685 = g.math('MULTIPLY', v683[1], v684)
    v686 = g.inp('_EmissiveAnimRandom', False, 0.0)
    v687 = g.math('MULTIPLY_ADD', v685, 0.15915493667125702, v686)
    v688 = g.math('FRACT', v687, 0.0)
    v689 = g.inp('_EmissiveAnimInterval', False, 1.0)
    v690 = g.math('MULTIPLY', v688, v689)
    v691 = g.clampn(v690, 0, 1)
    v692 = g.math('MULTIPLY_ADD', v691, 2, -1)
    v693 = g.math('MULTIPLY', v692, v692)
    v694 = g.inp('_EmissiveMinBrightness', False, 0.0)
    v695 = g.math('SUBTRACT', 1, v694)
    v696 = g.math('ADD', 1, v694)
    v697 = g.math('DIVIDE', v696, v695)
    v698 = g.math('MULTIPLY', v692, v693)
    v699 = g.math('ABSOLUTE', v698, 0.0)
    v700 = g.math('MULTIPLY', v699, 4)
    v701 = g.math('MULTIPLY_ADD', v693, -6, v700)
    v702 = g.math('ADD', v701, 1)
    v703 = g.math('ADD', v697, v702)
    v704 = g.math('MULTIPLY', v703, v695)
    v705 = g.math('MULTIPLY_ADD', v704, 0.5, -1)
    v706 = g.mixf(v680, 0, v705)
    v707 = g.inp('_EnableEmissiveAnimSweep', False, 0.0)
    v708 = g.math('GREATER_THAN', v707, 0.5)
    v709 = g.inp('_EmissiveMaskChannel', False, 0.0)
    v710 = g.math('GREATER_THAN', v709, 4.5)
    v711 = g.math('MAXIMUM', v708, v710)
    v712 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v713 = g.sep(v712)
    v714 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v715 = g.sep(v714)
    v716 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v717 = g.sep(v716)
    v718 = g.comb(v713[0], v715[1], v717[2])
    v719 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v720 = g.sep(v719)
    v721 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v722 = g.sep(v721)
    v723 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v724 = g.sep(v723)
    v725 = g.comb(v720[1], v722[1], v724[1])
    v726 = g.vmath('SUBTRACT', v20, v718)
    v727 = g.vmath('DOT_PRODUCT', v725, v726)
    v728 = g.inp('_EmissiveSweepRandom', False, 0.0)
    v729 = g.sep(v718)
    v730 = g.math('MULTIPLY_ADD', v728, v729[0], v683[1])
    v731 = g.inp('_EmissiveSweepInterval', False, 3.0)
    v732 = g.math('DIVIDE', v730, v731)
    v733 = g.math('ABSOLUTE', v732, 0.0)
    v734 = g.math('FRACT', v733, 0.0)
    v735 = g.math('MULTIPLY', v732, -1.0)
    v736 = g.math('LESS_THAN', v732, v735)
    v737 = g.math('SUBTRACT', 1.0, v736)
    v738 = g.math('MULTIPLY', v734, -1.0)
    v739 = g.mixf(v737, v738, v734)
    v740 = g.inp('_EmissiveSweepSpeed', False, 3.0)
    v741 = g.math('MULTIPLY', 0.30000001192092896, v731)
    v742 = g.math('MULTIPLY', v741, -1.0)
    v743 = g.math('MULTIPLY_ADD', v739, v731, v742)
    v744 = g.math('MULTIPLY', v740, v743)
    v745 = g.math('SUBTRACT', v727, v744)
    v746 = g.math('ABSOLUTE', v745, 0.0)
    v747 = g.inp('_EmissiveSweepWidth', False, 0.8)
    v748 = g.math('DIVIDE', v746, v747)
    v749 = g.clampn(v748, 0, 1)
    v750 = g.math('MULTIPLY', v749, -1.0)
    v751 = g.inp('_EmissiveSweepFalloff', False, 1.0)
    v752 = g.math('MULTIPLY_ADD', v750, v751, v751)
    v753 = g.clampn(v752, 0, 1)
    v754 = g.inp('_EmissiveSweepAlbedoScale', False, 0.0)
    v755 = g.vmath('DOT_PRODUCT', v659, (0.3330000042915344, 0.3330000042915344, 0.3330000042915344))
    v756 = g.math('ADD', v755, -0.20000000298023224)
    v757 = g.math('MULTIPLY_ADD', v754, v756, 0.20000000298023224)
    v758 = g.math('MULTIPLY', v757, 5)
    v759 = g.math('MAXIMUM', v758, 0)
    v760 = g.math('MULTIPLY', v753, v753)
    v761 = g.math('MULTIPLY', v759, v760)
    v762 = g.math('SUBTRACT', v761, 1)
    v763 = g.mixf(v711, v706, v762)
    v764 = g.mixf(v711, 0, v761)
    v765 = g.inp('_EmissiveColor', True, (1.0, 1.0, 1.0))
    v766 = g.inp('_EmissiveColor_w', False, 1.0)
    v767 = g.math('MULTIPLY_ADD', v766, v763, 1)
    v768 = g.inp('_EmissiveColorG', True, (0.0, 0.0, 0.0))
    v769 = g.inp('_EmissiveColorG_w', False, 0.0)
    v770 = g.math('MULTIPLY_ADD', v769, v763, 1)
    v771 = g.inp('_EmissiveColorB', True, (0.0, 0.0, 0.0))
    v772 = g.inp('_EmissiveColorB_w', False, 0.0)
    v773 = g.math('MULTIPLY_ADD', v772, v763, 1)
    v774 = g.inp('_EmissiveColorA', True, (0.0, 0.0, 0.0))
    v775 = g.inp('_EmissiveColorA_w', False, 0.0)
    v776 = g.math('MULTIPLY_ADD', v775, v763, 1)
    v777 = g.math('LESS_THAN', v709, 0.5)
    v778 = g.vmath('SUBTRACT', v14, v0)
    v779 = g.inp('_EmissiveSpeed', True, (0.0, 0.0, 0.0))
    v780 = g.inp('_EmissiveSpeed_w', False, 0.0)
    v781 = g.sep(v779)
    v782 = g.comb(v781[0], v781[1], 0.0)
    v783 = g.comb(v683[1], v683[1], 0.0)
    v784 = g.inp('_EmissiveUVSet', False, 0.0)
    v785 = g.comb(v784, v784, 0.0)
    v786 = g.vmath('MULTIPLY', v785, v778)
    v787 = g.vmath('ADD', v786, v0)
    v788 = g.inp('_EmissiveMap_ST', True, (1.0, 1.0, 0.0))
    v789 = g.inp('_EmissiveMap_ST_w', False, 0.0)
    v790 = g.sep(v788)
    v791 = g.comb(v790[0], v790[1], 0.0)
    v792 = g.comb(v790[2], v789, 0.0)
    v793 = g.vmath('MULTIPLY', v787, v791)
    v794 = g.vmath('ADD', v793, v792)
    v795 = g.vmath('MULTIPLY', v782, v783)
    v796 = g.vmath('ADD', v795, v794)
    v797 = g.math('MAXIMUM', v680, v708)
    v798 = g.inp('_EmissiveMapTilling', False, 0.0)
    v799 = g.comb(v798, v798, 0.0)
    v800 = g.vmath('MULTIPLY', v796, v799)
    v801 = g.mixv(v797, v796, v800)
    g.out_('F16_EmissiveMap_uv', v801, True)
    v802 = g.inp('F16_EmissiveMap', True, (1.0, 1.0, 1.0))
    v803 = g.inp('F16_EmissiveMap_alpha', False, 1.0)
    v804 = g.sep(v802)
    v805 = g.math('MULTIPLY', v804[0], v767)
    v806 = g.bc(v805)
    v807 = g.vmath('MULTIPLY', v765, v806)
    v808 = g.math('MULTIPLY', v804[1], v770)
    v809 = g.bc(v808)
    v810 = g.vmath('MULTIPLY', v768, v809)
    v811 = g.math('MULTIPLY', v804[2], v773)
    v812 = g.bc(v811)
    v813 = g.vmath('MULTIPLY', v771, v812)
    v814 = g.vmath('ADD', v810, v813)
    v815 = g.math('MULTIPLY', v803, v776)
    v816 = g.bc(v815)
    v817 = g.vmath('MULTIPLY', v774, v816)
    v818 = g.vmath('ADD', v814, v817)
    v819 = g.inp('_EmissiveType', False, 0.0)
    v820 = g.bc(v819)
    v821 = g.vmath('MULTIPLY', v818, v820)
    v822 = g.vmath('ADD', v807, v821)
    v823 = g.math('MAXIMUM', v680, v708)
    v824 = g.vmath('MAXIMUM', v822, (0, 0, 0))
    v825 = g.vmath('MINIMUM', v824, (1000, 1000, 1000))
    v826 = g.mixv(v823, v822, v825)
    v827 = g.vmath('MULTIPLY', v826, v678)
    v828 = g.bc(v672)
    v829 = g.vmath('MULTIPLY', v827, v828)
    v830 = g.vmath('ADD', v674, v829)
    v831 = g.math('SUBTRACT', 1.0, v710)
    v832 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v833 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v834 = g.sep(v832)
    v835 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v836 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v837 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v838 = g.inp('F10_MROMap_alpha', False, 1.0)
    v839 = g.math('SUBTRACT', v709, 1)
    v840 = g.clampn(v839, 0, 1)
    v841 = g.math('MULTIPLY', v221, -1.0)
    v842 = g.math('ADD', v841, v834[2])
    v843 = g.math('MULTIPLY_ADD', v840, v842, v221)
    v844 = g.math('SUBTRACT', v709, 2)
    v845 = g.clampn(v844, 0, 1)
    v846 = g.math('SUBTRACT', v836, v843)
    v847 = g.math('MULTIPLY_ADD', v845, v846, v843)
    v848 = g.math('SUBTRACT', v709, 3)
    v849 = g.clampn(v848, 0, 1)
    v850 = g.math('SUBTRACT', v838, v847)
    v851 = g.math('MULTIPLY_ADD', v849, v850, v847)
    v852 = g.clampn(v709, 0, 1)
    v853 = g.math('SUBTRACT', v851, 1)
    v854 = g.math('MULTIPLY_ADD', v852, v853, 1)
    v855 = g.math('MULTIPLY_ADD', v854, 1.1111111640930176, -0.055555559694767)
    v856 = g.clampn(v855, 0, 1)
    v857 = g.math('MULTIPLY', v856, v767)
    v858 = g.math('MULTIPLY', v857, v852)
    v859 = g.bc(v858)
    v860 = g.vmath('MULTIPLY', v765, v859)
    v861 = g.vmath('MULTIPLY', v860, v678)
    v862 = g.bc(v672)
    v863 = g.vmath('MULTIPLY', v861, v862)
    v864 = g.vmath('ADD', v674, v863)
    v865 = g.bc(v764)
    v866 = g.vmath('MULTIPLY', v765, v865)
    v867 = g.vmath('MULTIPLY', v866, v678)
    v868 = g.bc(v672)
    v869 = g.vmath('MULTIPLY', v867, v868)
    v870 = g.vmath('ADD', v674, v869)
    v871 = g.mixv(v831, v870, v864)
    v872 = g.mixv(v777, v871, v830)
    v873 = g.mixv(v676, v674, v872)
    v874 = g.bc(v130)
    v875 = g.vmath('MULTIPLY', v659, v874)
    v876 = g.vmath('ADD', v873, v875)
    v877 = g.inp('_EnableMatcap', False, 0.0)
    v878 = g.math('GREATER_THAN', v877, 0.5)
    v879 = g.vtrans(v663, 'WORLD', 'CAMERA', 'VECTOR')
    v880 = g.vmath('NORMALIZE', v879)
    v881 = g.sep(v880)
    v882 = g.comb(v881[0], v881[1], 0.0)
    v883 = g.vmath('MULTIPLY', v882, (0.5, 0.5, 0.0))
    v884 = g.vmath('ADD', v883, (0.5, 0.5, 0.0))
    g.out_('F17_MatcapMap_uv', v884, True)
    v885 = g.inp('F17_MatcapMap', True, (1.0, 1.0, 1.0))
    v886 = g.inp('F17_MatcapMap_alpha', False, 1.0)
    v887 = g.inp('_MatcapMapStrength', False, 0.2)
    v888 = g.bc(v887)
    v889 = g.vmath('MULTIPLY', v885, v888)
    v890 = g.vmath('ADD', v876, v889)
    v891 = g.mixv(v878, v876, v890)
    v892 = g.inp('_EnableParallaxMap', False, 0.0)
    v893 = g.math('GREATER_THAN', v892, 0.5)
    v894 = g.inp('_ParallaxMappingType', False, 0.0)
    v895 = g.math('LESS_THAN', v894, 0.5)
    v896 = g.math('MULTIPLY', v893, v895)
    v897 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v898 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v899 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v900 = g.inp('F10_MROMap_alpha', False, 1.0)
    v901 = g.sep(v897)
    v902 = g.inp('_UseParallaxMask', False, 0.0)
    v903 = g.math('COMPARE', v902, 0, 1e-05)
    v904 = g.math('SUBTRACT', 1.0, v903)
    v905 = g.math('MULTIPLY', v221, -1.0)
    v906 = g.math('ADD', v905, v898)
    v907 = g.math('MULTIPLY_ADD', v103, v906, v221)
    v908 = g.math('MULTIPLY', v907, -1.0)
    g.out_('F18_ParallaxMaskMap_uv', v14, True)
    v909 = g.inp('F18_ParallaxMaskMap', True, (1.0, 1.0, 1.0))
    v910 = g.inp('F18_ParallaxMaskMap_alpha', False, 1.0)
    v911 = g.sep(v909)
    v912 = g.math('MULTIPLY_ADD', v908, v38, v911[0])
    v913 = g.math('MULTIPLY', v907, v38)
    v914 = g.math('MULTIPLY_ADD', v902, v912, v913)
    v915 = g.inp('_ParallaxMaskChannel', False, 0.0)
    v916 = g.clampn(v915, 0, 1)
    v917 = g.math('MULTIPLY', v221, -1.0)
    v918 = g.math('ADD', v917, v901[2])
    v919 = g.math('MULTIPLY_ADD', v916, v918, v221)
    v920 = g.math('SUBTRACT', v915, 1)
    v921 = g.clampn(v920, 0, 1)
    v922 = g.math('MULTIPLY', v919, -1.0)
    v923 = g.math('ADD', v922, v898)
    v924 = g.math('MULTIPLY_ADD', v921, v923, v919)
    v925 = g.math('SUBTRACT', v915, 2)
    v926 = g.clampn(v925, 0, 1)
    v927 = g.math('MULTIPLY', v924, -1.0)
    v928 = g.math('ADD', v927, v900)
    v929 = g.math('MULTIPLY_ADD', v926, v928, v924)
    v930 = g.inp('_ParallaxMaskByLayerBlend', False, 0.0)
    v931 = g.math('MULTIPLY', v929, -1.0)
    v932 = g.math('MULTIPLY_ADD', v930, v931, v929)
    v933 = g.mixf(v904, v932, v914)
    v934 = g.math('LESS_THAN', 0.009999999776482582, v933)
    v935 = g.math('SUBTRACT', 1.0, v934)
    v936 = g.mixv(v935, (0.0, 0.0, 0.0), (0, 0, 0))
    v937 = g.mixf(v935, 0.0, 1.0)
    v938 = g.math('SUBTRACT', 1.0, v937)
    v939 = g.math('GREATER_THAN', v5, 0)
    v940 = g.mixf(v939, -1, 1)
    v941 = g.math('SUBTRACT', 1.0, v937)
    v942 = g.vmath('CROSS_PRODUCT', v21, v22)
    v943 = g.bc(v940)
    v944 = g.vmath('MULTIPLY', v943, v942)
    v945 = g.math('SUBTRACT', 1.0, v937)
    v946 = g.vmath('DOT_PRODUCT', v22, v28)
    v947 = g.math('SUBTRACT', 1.0, v937)
    v948 = g.vmath('DOT_PRODUCT', v944, v28)
    v949 = g.math('SUBTRACT', 1.0, v937)
    v950 = g.vmath('DOT_PRODUCT', v21, v28)
    v951 = g.math('SUBTRACT', 1.0, v937)
    v952 = g.comb(v946, v948, v950)
    v953 = g.comb(v946, v948, v950)
    v954 = g.vmath('DOT_PRODUCT', v952, v953)
    v955 = g.math('INVERSE_SQRT', v954, 0.0)
    v956 = g.math('SUBTRACT', 1.0, v937)
    v957 = g.inp('_ParallaxMapUVType', False, 0.0)
    v958 = g.comb(v957, v957, 0.0)
    v959 = g.vmath('SUBTRACT', v14, v0)
    v960 = g.vmath('MULTIPLY', v958, v959)
    v961 = g.vmath('ADD', v960, v0)
    v962 = g.math('SUBTRACT', 1.0, v937)
    v963 = g.inp('_GlobalMipBias', True, (0.0, 0.0, 0.0))
    v964 = g.sep(v963)
    v965 = g.comb(v964[1], v964[1], 0.0)
    v966 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), v965)
    v967 = g.math('SUBTRACT', 1.0, v937)
    v968 = g.comb(v964[1], v964[1], 0.0)
    v969 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), v968)
    v970 = g.math('SUBTRACT', 1.0, v937)
    v971 = g.inp('_ParallaxMarchNum', False, 3.0)
    v972 = g.math('MINIMUM', v971, 20)
    v973 = g.math('SUBTRACT', 1.0, v937)
    v974 = g.math('DIVIDE', 1, v972)
    v975 = g.math('SUBTRACT', 1.0, v937)
    v976 = g.math('MULTIPLY_ADD', v950, v955, 0.41999998688697815)
    v977 = g.math('SUBTRACT', 1.0, v937)
    v978 = g.math('MULTIPLY', v955, v950)
    v979 = g.math('MAXIMUM', v978, 0.0010000000474974513)
    v980 = g.math('SUBTRACT', 1.0, v937)
    v981 = g.math('MULTIPLY', v955, v946)
    v982 = g.math('DIVIDE', v981, v976)
    v983 = g.math('DIVIDE', v982, v979)
    v984 = g.inp('_ParallaxStrength', False, 0.0)
    v985 = g.math('MULTIPLY', v984, -1.0)
    v986 = g.math('MULTIPLY', v983, v985)
    v987 = g.math('MULTIPLY', v955, v948)
    v988 = g.math('DIVIDE', v987, v976)
    v989 = g.math('DIVIDE', v988, v979)
    v990 = g.math('MULTIPLY', v984, -1.0)
    v991 = g.math('MULTIPLY', v989, v990)
    v992 = g.comb(v986, v991, 0.0)
    v993 = g.math('SUBTRACT', 1.0, v937)
    v994 = g.comb(v974, v974, 0.0)
    v995 = g.vmath('MULTIPLY', v994, v992)
    v996 = g.math('SUBTRACT', 1.0, v937)
    v997 = g.math('SUBTRACT', 1, v974)
    v998 = g.math('SUBTRACT', 1.0, v937)
    v999 = g.math('SUBTRACT', 1.0, v937)
    v1000 = g.math('SUBTRACT', 1.0, v937)
    v1001 = g.math('SUBTRACT', 1.0, v937)
    v1002 = g.math('SUBTRACT', 1.0, v937)
    v1003 = g.math('SUBTRACT', 1.0, v937)
    v1004 = g.math('SUBTRACT', 1.0, v937)
    g.out_('Z0_it', 24, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_height', v997, False)
    g.out_('Z0_s_heightPrev', 1, False)
    g.out_('Z0_s_iter', 0, False)
    g.out_('Z0_s_offCur', v995, True)
    g.out_('Z0_s_offPrev', (0, 0, 0.0), True)
    g.out_('Z0_s_texHit', 0, False)
    g.out_('Z0_s_texPrev', 0, False)
    g.out_('Z0_s_uvP', v961, True)
    g.out_('Z0_r___done', v937, False)
    g.out_('Z0_r_steps', v972, False)
    g.out_('Z0_r_stepH', v974, False)
    g.out_('Z0_r_stepUV', v995, True)
    v1005 = g.inp('Z0_o_Lloop0', False)
    v1006 = g.inp('Z0_o_height', False)
    v1007 = g.inp('Z0_o_heightPrev', False)
    v1008 = g.inp('Z0_o_iter', False)
    v1009 = g.inp('Z0_o_offCur', True)
    v1010 = g.inp('Z0_o_offPrev', True)
    v1011 = g.inp('Z0_o_texHit', False)
    v1012 = g.inp('Z0_o_texPrev', False)
    v1013 = g.inp('Z0_o_uvP', True)
    v1014 = g.mixv(v1004, v961, v1013)
    v1015 = g.mixf(v1004, v997, v1006)
    v1016 = g.mixv(v1004, v995, v1009)
    v1017 = g.mixv(v1004, (0, 0, 0.0), v1010)
    v1018 = g.mixf(v1004, 0, v1012)
    v1019 = g.mixf(v1004, 1, v1007)
    v1020 = g.mixf(v1004, 0, v1011)
    v1021 = g.mixf(v1004, 0, v1008)
    v1022 = g.math('SUBTRACT', 1.0, v937)
    v1023 = g.math('SUBTRACT', v1018, v1019)
    v1024 = g.math('MULTIPLY', v1019, -1.0)
    v1025 = g.math('SUBTRACT', v1018, v1020)
    v1026 = g.math('ADD', v1015, v1025)
    v1027 = g.math('ADD', v1024, v1026)
    v1028 = g.math('DIVIDE', v1023, v1027)
    v1029 = g.math('SUBTRACT', 1.0, v937)
    v1030 = g.comb(v1028, v1028, 0.0)
    v1031 = g.vmath('MULTIPLY', v995, v1030)
    v1032 = g.vmath('ADD', v1031, v1017)
    v1033 = g.vmath('ADD', v1014, v1032)
    v1034 = g.inp('_ParallaxTilling', False, 1.0)
    v1035 = g.comb(v1034, v1034, 0.0)
    v1036 = g.vmath('MULTIPLY', v1033, v1035)
    v1037 = g.math('SUBTRACT', 1.0, v937)
    g.out_('F19_ParallaxMap_uv', v1036, True)
    v1038 = g.inp('F19_ParallaxMap', True, (1.0, 1.0, 1.0))
    v1039 = g.inp('F19_ParallaxMap_alpha', False, 1.0)
    v1040 = g.math('SUBTRACT', 1.0, v937)
    v1041 = g.sep(v1038)
    v1042 = g.inp('_ParallaxColor', True, (0.0, 0.0, 0.0))
    v1043 = g.inp('_ParallaxColor_w', False, 1.0)
    v1044 = g.sep(v1042)
    v1045 = g.inp('_ParallaxColorDark', True, (0.0, 0.0, 0.0))
    v1046 = g.inp('_ParallaxColorDark_w', False, 1.0)
    v1047 = g.sep(v1045)
    v1048 = g.math('SUBTRACT', v1044[0], v1047[0])
    v1049 = g.math('MULTIPLY_ADD', v1041[1], v1048, v1047[0])
    v1050 = g.math('SUBTRACT', v1044[1], v1047[1])
    v1051 = g.math('MULTIPLY_ADD', v1041[1], v1050, v1047[1])
    v1052 = g.math('SUBTRACT', v1044[2], v1047[2])
    v1053 = g.math('MULTIPLY_ADD', v1041[1], v1052, v1047[2])
    v1054 = g.comb(v1049, v1051, v1053)
    v1055 = g.math('SUBTRACT', 1.0, v937)
    v1056 = g.vmath('DOT_PRODUCT', v28, v663)
    v1057 = g.clampn(v1056, 0, 1)
    v1058 = g.math('MAXIMUM', v1057, 0.0010000000474974513)
    v1059 = g.math('LOGARITHM', v1058, 2.0)
    v1060 = g.inp('_ParallaxFresnelStrength', False, 0.0)
    v1061 = g.math('FLOOR', v1060, 0.0)
    v1062 = g.math('MULTIPLY', v1059, v1061)
    v1063 = g.math('POWER', 2.0, v1062)
    v1064 = g.math('MULTIPLY', v933, v933)
    v1065 = g.math('MULTIPLY', v1063, v1064)
    v1066 = g.math('SUBTRACT', 1.0, v937)
    v1067 = g.inp('_VFXParams0', True, (0.0, 0.0, 0.0))
    v1068 = g.inp('_VFXParams0_w', False, 0.0)
    v1069 = g.vmath('SUBTRACT', v20, v1067)
    v1070 = g.math('SUBTRACT', 1.0, v937)
    v1071 = g.vmath('DOT_PRODUCT', v1069, v1069)
    v1072 = g.math('SQRT', v1071, 0.0)
    v1073 = g.inp('_ParallaxBrightOuterRadius', False, 0.0)
    v1074 = g.math('SUBTRACT', v1072, v1073)
    v1075 = g.math('MULTIPLY', v1073, -1.0)
    v1076 = g.inp('_ParallaxBrightInnerRadius', False, 0.0)
    v1077 = g.math('ADD', v1075, v1076)
    v1078 = g.math('DIVIDE', 1, v1077)
    v1079 = g.math('MULTIPLY', v1074, v1078)
    v1080 = g.clampn(v1079, 0, 1)
    v1081 = g.math('SUBTRACT', 1.0, v937)
    v1082 = g.math('MULTIPLY', v1080, v1080)
    v1083 = g.math('MULTIPLY_ADD', v1080, -2, 3)
    v1084 = g.math('MULTIPLY', v1082, v1083)
    v1085 = g.inp('_ParallaxBrightStrength', False, 0.0)
    v1086 = g.math('MULTIPLY', v1084, v1085)
    v1087 = g.math('SUBTRACT', 1.0, v937)
    v1088 = g.inp('_ParallaxCharPos', False, 0.0)
    v1089 = g.math('COMPARE', v1088, 0, 1e-05)
    v1090 = g.math('SUBTRACT', 1.0, v1089)
    v1091 = g.mixf(v1090, 0, v1086)
    v1092 = g.mixf(v1087, v1086, v1091)
    v1093 = g.math('SUBTRACT', 1.0, v937)
    v1094 = g.inp('_VFXParams2', True, (0.0, 0.0, 0.0))
    v1095 = g.inp('_VFXParams2_w', False, 0.0)
    v1096 = g.sep(v1094)
    v1097 = g.math('SUBTRACT', v438[0], v1096[0])
    v1098 = g.math('SUBTRACT', v438[2], v1096[1])
    v1099 = g.comb(v1097, v1098, 0.0)
    v1100 = g.math('SUBTRACT', 1.0, v937)
    v1101 = g.math('MULTIPLY', v1096[2], -1.0)
    v1102 = g.math('DIVIDE', 1, v1101)
    v1103 = g.vmath('DOT_PRODUCT', v1099, v1099)
    v1104 = g.math('SQRT', v1103, 0.0)
    v1105 = g.math('SUBTRACT', v1104, v1096[2])
    v1106 = g.math('MULTIPLY', v1102, v1105)
    v1107 = g.clampn(v1106, 0, 1)
    v1108 = g.math('SUBTRACT', 1.0, v937)
    v1109 = g.inp('_ParallaxMinBrightness', False, 0.0)
    v1110 = g.math('SUBTRACT', 1, v1109)
    v1111 = g.math('SUBTRACT', 1.0, v937)
    v1112 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1113 = g.sep(v1112)
    v1114 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1115 = g.sep(v1114)
    v1116 = g.math('ADD', v1113[1], v1115[0])
    v1117 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1118 = g.sep(v1117)
    v1119 = g.math('ADD', v1116, v1118[2])
    v1120 = g.math('SUBTRACT', 1.0, v937)
    v1121 = g.math('MULTIPLY', v1107, v1107)
    v1122 = g.math('MULTIPLY_ADD', v1107, -2, 3)
    v1123 = g.math('MULTIPLY', v1121, v1122)
    v1124 = g.math('ADD', 1, v1109)
    v1125 = g.math('DIVIDE', v1124, v1110)
    v1126 = g.inp('_ParallaxAnimSpeed', False, 0.0)
    v1127 = g.math('MULTIPLY', v683[1], v1126)
    v1128 = g.inp('_ParallaxAnimRandom', False, 0.0)
    v1129 = g.math('MULTIPLY', v1119, v1128)
    v1130 = g.math('MULTIPLY_ADD', v1127, 0.05000000074505806, v1129)
    v1131 = g.math('COSINE', v1130, 0.0)
    v1132 = g.math('ADD', v1125, v1131)
    v1133 = g.math('MULTIPLY', v1110, v1132)
    v1134 = g.math('MULTIPLY_ADD', v1133, 0.5, v1092)
    v1135 = g.math('MULTIPLY_ADD', v1123, v1095, v1134)
    v1136 = g.math('SUBTRACT', 1.0, v937)
    v1137 = g.bc(v1065)
    v1138 = g.vmath('MULTIPLY', v1137, v1054)
    v1139 = g.bc(v1135)
    v1140 = g.vmath('MULTIPLY', v1139, v1138)
    v1141 = g.vmath('MAXIMUM', v1140, (0, 0, 0))
    v1142 = g.vmath('MINIMUM', v1141, (1000, 1000, 1000))
    v1143 = g.math('SUBTRACT', 1.0, v937)
    v1144 = g.inp('_UseWorldSpaceParallaxMask', False, 0.0)
    v1145 = g.math('GREATER_THAN', v1144, 0.5)
    v1146 = g.math('SUBTRACT', 1.0, v937)
    v1147 = g.inp('_MaskWorldPosParams', True, (0.0, 0.0, 0.0))
    v1148 = g.inp('_MaskWorldPosParams_w', False, 0.0)
    v1149 = g.sep(v1147)
    v1150 = g.math('SUBTRACT', v438[0], v1149[0])
    v1151 = g.math('SUBTRACT', v438[2], v1149[2])
    v1152 = g.comb(v1150, v1151, 0.0)
    v1153 = g.math('SUBTRACT', 1.0, v937)
    v1154 = g.math('MULTIPLY', 0.01745329238474369, v1149[1])
    v1155 = g.math('SUBTRACT', 1.0, v937)
    v1156 = g.math('SINE', v1154, 0.0)
    v1157 = g.math('SUBTRACT', 1.0, v937)
    v1158 = g.math('COSINE', v1154, 0.0)
    v1159 = g.math('SUBTRACT', 1.0, v937)
    v1160 = g.math('MAXIMUM', 0.10000000149011612, v1148)
    v1161 = g.math('SUBTRACT', 1.0, v937)
    v1162 = g.comb(v1160, v1160, 0.0)
    v1163 = g.vmath('DIVIDE', v1152, v1162)
    v1164 = g.math('SUBTRACT', 1.0, v937)
    v1165 = g.comb(v1158, v1156, 0.0)
    v1166 = g.vmath('DOT_PRODUCT', v1163, v1165)
    v1167 = g.math('ADD', v1166, 0.5)
    v1168 = g.math('MULTIPLY_ADD', v1041[0], v1109, v1167)
    v1169 = g.math('MULTIPLY', v1156, -1.0)
    v1170 = g.comb(v1169, v1158, 0.0)
    v1171 = g.vmath('DOT_PRODUCT', v1163, v1170)
    v1172 = g.math('ADD', v1171, 0.5)
    v1173 = g.math('MULTIPLY_ADD', v1041[1], v1109, v1172)
    v1174 = g.comb(v1168, v1173, 0.0)
    g.out_('F20_ParallaxMaskMap_uv', v1174, True)
    v1175 = g.inp('F20_ParallaxMaskMap', True, (1.0, 1.0, 1.0))
    v1176 = g.inp('F20_ParallaxMaskMap_alpha', False, 1.0)
    v1177 = g.math('SUBTRACT', 1.0, v937)
    v1178 = g.inp('_ParallaxMaskMapColorStrength', False, 0.0)
    v1179 = g.bc(v1178)
    v1180 = g.vmath('MULTIPLY', v1175, v1179)
    v1181 = g.vmath('MULTIPLY', v1142, v1180)
    v1182 = g.mixv(v1177, v1142, v1181)
    v1183 = g.math('SUBTRACT', 1.0, v937)
    v1184 = g.inp('_ParallaxSignControl', False, 0.0)
    v1185 = g.math('TRUNC', v1184, 0.0)
    v1186 = g.math('TRUNC', v1185, 0.0)
    v1187 = g.math('SUBTRACT', 1.0, v937)
    v1188 = g.clampn(v1176, 0, 1)
    v1189 = g.math('SUBTRACT', 1.0, v937)
    v1190 = g.math('SUBTRACT', v1176, 0.20000000298023224)
    v1191 = g.clampn(v1190, 0, 1)
    v1192 = g.math('SUBTRACT', 1.0, v937)
    v1193 = g.math('SUBTRACT', v1176, 0.4000000059604645)
    v1194 = g.clampn(v1193, 0, 1)
    v1195 = g.math('SUBTRACT', 1.0, v937)
    v1196 = g.math('SUBTRACT', v1176, 0.6000000238418579)
    v1197 = g.clampn(v1196, 0, 1)
    v1198 = g.math('SUBTRACT', 1.0, v937)
    v1199 = g.math('SUBTRACT', v1176, 0.800000011920929)
    v1200 = g.clampn(v1199, 0, 1)
    v1201 = g.math('SUBTRACT', 1.0, v937)
    v1202 = g.math('LESS_THAN', 0.18000000715255737, v1188)
    v1203 = g.math('SUBTRACT', 1.0, v1202)
    v1204 = g.math('MULTIPLY', v1188, 5)
    v1205 = g.mixf(v1203, 0, v1204)
    v1206 = g.math('MODULO', v1186, 2)
    v1207 = g.math('TRUNC', v1206, 0.0)
    v1208 = g.math('MULTIPLY', v1205, v1207)
    v1209 = g.inp('_ParallaxSignLerpFactor0', True, (0.0, 0.0, 0.0))
    v1210 = g.inp('_ParallaxSignLerpFactor0_w', False, 0.0)
    v1211 = g.sep(v1209)
    v1212 = g.math('MULTIPLY', v1208, v1211[0])
    v1213 = g.math('LESS_THAN', 0.18000000715255737, v1191)
    v1214 = g.math('SUBTRACT', 1.0, v1213)
    v1215 = g.math('MULTIPLY', v1191, 5)
    v1216 = g.mixf(v1214, 0, v1215)
    v1217 = g.math('DIVIDE', v1186, 2)
    v1218 = g.math('FLOOR', v1217, 0.0)
    v1219 = g.math('MODULO', v1218, 2)
    v1220 = g.math('TRUNC', v1219, 0.0)
    v1221 = g.math('MULTIPLY', v1216, v1220)
    v1222 = g.math('MULTIPLY', v1221, v1211[1])
    v1223 = g.math('ADD', v1212, v1222)
    v1224 = g.math('LESS_THAN', 0.18000000715255737, v1194)
    v1225 = g.math('SUBTRACT', 1.0, v1224)
    v1226 = g.math('MULTIPLY', v1194, 5)
    v1227 = g.mixf(v1225, 0, v1226)
    v1228 = g.math('DIVIDE', v1186, 4)
    v1229 = g.math('FLOOR', v1228, 0.0)
    v1230 = g.math('MODULO', v1229, 2)
    v1231 = g.math('TRUNC', v1230, 0.0)
    v1232 = g.math('MULTIPLY', v1227, v1231)
    v1233 = g.math('MULTIPLY', v1232, v1211[2])
    v1234 = g.math('ADD', v1223, v1233)
    v1235 = g.math('LESS_THAN', 0.18000000715255737, v1197)
    v1236 = g.math('SUBTRACT', 1.0, v1235)
    v1237 = g.math('MULTIPLY', v1197, 5)
    v1238 = g.mixf(v1236, 0, v1237)
    v1239 = g.math('DIVIDE', v1186, 8)
    v1240 = g.math('FLOOR', v1239, 0.0)
    v1241 = g.math('MODULO', v1240, 2)
    v1242 = g.math('TRUNC', v1241, 0.0)
    v1243 = g.math('MULTIPLY', v1238, v1242)
    v1244 = g.math('MULTIPLY', v1243, v1210)
    v1245 = g.math('ADD', v1234, v1244)
    v1246 = g.math('LESS_THAN', 0.18000000715255737, v1200)
    v1247 = g.math('SUBTRACT', 1.0, v1246)
    v1248 = g.math('MULTIPLY', v1200, 5)
    v1249 = g.mixf(v1247, 0, v1248)
    v1250 = g.math('DIVIDE', v1186, 16)
    v1251 = g.math('FLOOR', v1250, 0.0)
    v1252 = g.math('MODULO', v1251, 2)
    v1253 = g.math('TRUNC', v1252, 0.0)
    v1254 = g.math('MULTIPLY', v1249, v1253)
    v1255 = g.inp('_ParallaxSignLerpFactor2', False, 0.0)
    v1256 = g.math('MULTIPLY', v1254, v1255)
    v1257 = g.math('ADD', v1245, v1256)
    v1258 = g.math('SUBTRACT', 1.0, v937)
    v1259 = g.vmath('DOT_PRODUCT', v1152, v1152)
    v1260 = g.math('SQRT', v1259, 0.0)
    v1261 = g.math('MULTIPLY_ADD', v1041[0], 20, v1260)
    v1262 = g.inp('_ParallaxLerpSchedule', False, 0.0)
    v1263 = g.math('SUBTRACT', v1261, v1262)
    v1264 = g.clampn(v1263, 0, 1)
    v1265 = g.math('SUBTRACT', 1.0, v937)
    v1266 = g.math('MULTIPLY_ADD', v1257, v475[0], v1264)
    v1267 = g.clampn(v1266, 0, 1)
    v1268 = g.math('SUBTRACT', 1.0, v937)
    v1269 = g.inp('_ParallaxPatternColorDark', True, (0.0, 0.0, 0.0))
    v1270 = g.inp('_ParallaxPatternColorDark_w', False, 0.0)
    v1271 = g.vmath('MULTIPLY', v1182, v1269)
    v1272 = g.math('SUBTRACT', 1.0, v937)
    v1273 = g.inp('_ParallaxPatternColor', True, (0.0, 0.0, 0.0))
    v1274 = g.inp('_ParallaxPatternColor_w', False, 0.0)
    v1275 = g.vmath('MULTIPLY', v1182, v1273)
    v1276 = g.math('SUBTRACT', 1.0, v937)
    v1277 = g.mixv(v1267, v1271, v1275)
    v1278 = g.mixv(v1276, v1182, v1277)
    v1279 = g.math('SUBTRACT', 1.0, v937)
    v1280 = g.inp('_ParallaxSignLerpFactor1', True, (0.0, 0.0, 0.0))
    v1281 = g.inp('_ParallaxSignLerpFactor1_w', False, 0.0)
    v1282 = g.clampn(v1281, 0, 1)
    v1283 = g.math('MULTIPLY', v221, -1.0)
    v1284 = g.math('MULTIPLY', v438[1], -1.0)
    v1285 = g.math('ADD', v1284, v1281)
    v1286 = g.clampn(v1285, 0, 1)
    v1287 = g.math('ADD', v1283, v1286)
    v1288 = g.math('MULTIPLY_ADD', v1282, v1287, v221)
    v1289 = g.math('SUBTRACT', 1.0, v937)
    v1290 = g.inp('_WorldParallaxAdditionalLightMaskChannel', False, 0.0)
    v1291 = g.clampn(v1290, 0, 1)
    v1292 = g.math('MULTIPLY', v221, -1.0)
    v1293 = g.math('ADD', v1292, v901[2])
    v1294 = g.math('MULTIPLY_ADD', v1291, v1293, v221)
    v1295 = g.math('SUBTRACT', 1.0, v937)
    v1296 = g.math('SUBTRACT', v1290, 1)
    v1297 = g.clampn(v1296, 0, 1)
    v1298 = g.math('MULTIPLY', v1294, -1.0)
    v1299 = g.math('ADD', v1298, v898)
    v1300 = g.math('MULTIPLY_ADD', v1297, v1299, v1294)
    v1301 = g.mixf(v1295, v1294, v1300)
    v1302 = g.math('SUBTRACT', 1.0, v937)
    v1303 = g.math('SUBTRACT', v1290, 2)
    v1304 = g.clampn(v1303, 0, 1)
    v1305 = g.math('MULTIPLY', v1301, -1.0)
    v1306 = g.math('ADD', v1305, v900)
    v1307 = g.math('MULTIPLY_ADD', v1304, v1306, v1301)
    v1308 = g.mixf(v1302, v1301, v1307)
    v1309 = g.math('SUBTRACT', 1.0, v937)
    v1310 = g.sep(v1280)
    v1311 = g.math('SUBTRACT', v438[1], v1310[1])
    v1312 = g.clampn(v1311, 0, 1)
    v1313 = g.math('SUBTRACT', 1.0, v937)
    v1314 = g.vmath('ADD', v1278, (0.30000001192092896, 0.30000001192092896, 0.30000001192092896))
    v1315 = g.inp('_WorldParallaxAdditionalColor', True, (0.0, 0.0, 0.0))
    v1316 = g.inp('_WorldParallaxAdditionalColor_w', False, 0.0)
    v1317 = g.vmath('MULTIPLY', v1314, v1315)
    v1318 = g.bc(v1312)
    v1319 = g.vmath('MULTIPLY', v1318, v1317)
    v1320 = g.bc(v1308)
    v1321 = g.vmath('MULTIPLY', v1320, v1319)
    v1322 = g.math('SUBTRACT', 1.0, v937)
    v1323 = g.inp('_ParallaxIntensity', False, 0.0)
    v1324 = g.math('MULTIPLY', v1288, v1323)
    v1325 = g.math('MULTIPLY', v1288, v1323)
    v1326 = g.math('MULTIPLY', v1288, v1323)
    v1327 = g.comb(v1324, v1325, v1326)
    v1328 = g.vmath('MULTIPLY', v1278, v1327)
    v1329 = g.vmath('ADD', v1328, v1321)
    v1330 = g.mixv(v1322, v936, v1329)
    v1331 = g.mixf(v1322, v937, 1.0)
    v1332 = g.mixv(v1145, v936, v1330)
    v1333 = g.mixf(v1145, v937, v1331)
    v1334 = g.mixv(v1145, v1142, v1278)
    v1335 = g.mixv(v1143, v936, v1332)
    v1336 = g.mixf(v1143, v937, v1333)
    v1337 = g.mixv(v1143, v1142, v1334)
    v1338 = g.math('SUBTRACT', 1.0, v1336)
    v1339 = g.bc(v1323)
    v1340 = g.vmath('MULTIPLY', v1337, v1339)
    v1341 = g.mixv(v1338, v1335, v1340)
    v1342 = g.mixf(v1338, v1336, 1.0)
    v1343 = g.vmath('ADD', v891, v1341)
    v1344 = g.mixv(v896, v891, v1343)
    v1345 = g.comb(v226, v226, v226)
    v1346 = g.math('SUBTRACT', 1, v660)
    v1347 = g.vmath('NORMALIZE', v663)
    v1348 = g.vmath('DOT_PRODUCT', v1347, v28)
    v1349 = g.math('MAXIMUM', v1348, 0)
    v1350 = g.math('MULTIPLY', 0.08, v226)
    v1351 = g.math('MULTIPLY', 0.08, v226)
    v1352 = g.math('MULTIPLY', 0.08, v226)
    v1353 = g.comb(v1350, v1351, v1352)
    v1354 = g.mixv(v661, v1353, v659)
    v1355 = g.math('SUBTRACT', 1, v661)
    v1356 = g.bc(v1355)
    v1357 = g.vmath('MULTIPLY', v659, v1356)
    v1358 = g.inp('_UseThinFilm', False, 0.0)
    v1359 = g.math('GREATER_THAN', v1358, 0.5)
    v1360 = g.inp('_ThinFilmIOR', False, 1.4)
    v1361 = g.inp('_ThinFilmThickness', False, 0.5)
    v1362 = g.math('MULTIPLY', v1361, 1000)
    v1363 = g.inp('M_PI', False, 0.0)
    v1364 = g.group_named('RCE_RuriEvalIridescence', [('outsideIor', 1), ('eta2', v1360), ('cosTheta1', v1349), ('iridescenceThickness', v1362), ('baseF0', v1354), ('M_PI', v1363)])
    v1365 = g.inp('_ThinFilmWeight', False, 0.0)
    v1366 = g.inp('_ThinFilmIntensity', False, 1.0)
    v1367 = g.math('MULTIPLY', v1365, v1366)
    v1368 = g.clampn(v1367)
    v1369 = g.mixv(v1368, v1354, v1364[0])
    v1370 = g.mixv(v1359, v1354, v1369)
    v1371 = g.inp('_SubsurfaceShadingMode', False, 0.0)
    v1372 = g.math('LESS_THAN', v1371, 0.5)
    v1373 = g.inp('_SubsurfaceColor', True, (0.8, 0.8, 0.8))
    v1374 = g.inp('_SubsurfaceColor_w', False, 1.0)
    v1375 = g.vmath('MULTIPLY', v1373, v659)
    v1376 = g.mixv(v1372, v1375, v1373)
    v1377 = g.inp('_MaxSubsurfaceThickness', False, 1.0)
    v1378 = g.inp('_UseSubsurfaceThicknessMap', False, 0.0)
    v1379 = g.math('GREATER_THAN', v1378, 0.5)
    v1380 = g.inp('_MinSubsurfaceThickness', False, 0.0)
    g.out_('F21_SubsurfaceMap_uv', v0, True)
    v1381 = g.inp('F21_SubsurfaceMap', True, (1.0, 1.0, 1.0))
    v1382 = g.inp('F21_SubsurfaceMap_alpha', False, 1.0)
    v1383 = g.sep(v1381)
    v1384 = g.mixf(v1383[0], v1380, v1377)
    v1385 = g.mixf(v1379, v1377, v1384)
    v1386 = g.group_named('RCE_HgEnvBRDF', [('roughness', v660), ('NoV', v1349), ('f0', v1370)])
    v1387 = g.vmath('SCALE', v28, s=-1.0)
    v1388 = g.vmath('DOT_PRODUCT', v1347, v1387)
    v1389 = g.math('MULTIPLY', 2.0, v1388)
    v1390 = g.vmath('SCALE', v1347, s=v1389)
    v1391 = g.vmath('SUBTRACT', v1387, v1390)
    v1392 = g.inp('_UseCustomIBL', False, 0.0)
    v1393 = g.math('GREATER_THAN', v1392, 0.5)
    v1394 = g.math('MULTIPLY', 0.7, v660)
    v1395 = g.math('SUBTRACT', 1.7, v1394)
    v1396 = g.math('MULTIPLY', v660, v1395)
    v1397 = g.math('MULTIPLY', v1396, 6)
    v1398 = g.u2b(v1391)
    g.out_('F22_IBL_CustomIBL_dir', v1398, True)
    g.out_('F22_IBL_CustomIBL_mip', v1397, False)
    v1399 = g.inp('F22_IBL_CustomIBL', True, (0.2159, 0.2159, 0.2159))
    v1400 = g.inp('F22_IBL_CustomIBL_alpha', False, 1.0)
    v1401 = g.inp('_CustomIBLIntensity', False, 1.0)
    v1402 = g.bc(v1401)
    v1403 = g.vmath('MULTIPLY', v1399, v1402)
    g.out_('C1_SpecularRadiance_direction', v1391, True)
    g.out_('C1_SpecularRadiance_position', v20, True)
    g.out_('C1_SpecularRadiance_roughness', v660, False)
    v1404 = g.inp('C1_SpecularRadiance', True, (0.2159, 0.2159, 0.2159))
    v1405 = g.mixv(v1393, v1404, v1403)
    v1406 = g.inp('_PlanarReflection', False, 0.0)
    v1407 = g.math('GREATER_THAN', v1406, 0.5)
    g.out_('F23_PlanarReflectionTexture_uv', v29, True)
    v1408 = g.inp('F23_PlanarReflectionTexture', True, (1.0, 1.0, 1.0))
    v1409 = g.inp('F23_PlanarReflectionTexture_alpha', False, 1.0)
    v1410 = g.inp('_PlanarReflectionTint', True, (1.0, 1.0, 1.0))
    v1411 = g.inp('_PlanarReflectionTint_w', False, 1.0)
    v1412 = g.vmath('MULTIPLY', v1408, v1410)
    v1413 = g.mixv(v1411, v1405, v1412)
    v1414 = g.mixv(v1407, v1405, v1413)
    v1415 = g.inp('_EnableSubsurface', False, 0.0)
    v1416 = g.math('GREATER_THAN', v1415, 0.5)
    v1417 = g.inp('_SubsurfaceIndirect', False, 1.0)
    v1418 = g.comb(v1417, v1417, v1417)
    v1419 = g.vmath('MULTIPLY', v1376, v1418)
    v1420 = g.vmath('ADD', v1419, v1357)
    v1421 = g.mixv(v1416, v1357, v1420)
    v1422 = g.vmath('MULTIPLY', v1421, v30)
    v1423 = g.bc(v662)
    v1424 = g.vmath('MULTIPLY', v1422, v1423)
    v1425 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v1426 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v1427 = g.sep(v1425)
    v1428 = g.bc(v1427[0])
    v1429 = g.vmath('MULTIPLY', v1424, v1428)
    v1430 = g.comb(v1386[0], v1386[0], v1386[0])
    v1431 = g.comb(v1386[1], v1386[1], v1386[1])
    v1432 = g.vmath('MULTIPLY', v1370, v1430)
    v1433 = g.vmath('ADD', v1432, v1431)
    v1434 = g.vmath('MULTIPLY', v1433, v1414)
    v1435 = g.bc(v1427[1])
    v1436 = g.vmath('MULTIPLY', v1434, v1435)
    v1437 = g.vmath('ADD', v1429, v1436)
    v1438 = g.inp('C2_MainLight_direction', True, (0.0, 0.0, 0.0))
    v1439 = g.inp('C2_MainLight_color', True, (0.0, 0.0, 0.0))
    v1440 = g.inp('C2_MainLight_distanceAttenuation', False, 0.0)
    v1441 = g.inp('C2_MainLight_shadowAttenuation', False, 0.0)
    v1442 = g.inp('C2_MainLight_layerMask', False, 0.0)
    v1443 = g.inp('_MainLightOcclusionProbes', True, (0.0, 0.0, 0.0))
    v1444 = g.inp('_MainLightOcclusionProbes_w', False, 0.0)
    v1445 = g.vmath('ADD', v1438, v28)
    v1446 = g.vmath('DOT_PRODUCT', v1445, v1445)
    v1447 = g.math('MAXIMUM', v1446, 1E-08)
    v1448 = g.math('INVERSE_SQRT', v1447, 0.0)
    v1449 = g.bc(v1448)
    v1450 = g.vmath('MULTIPLY', v1445, v1449)
    v1451 = g.vmath('DOT_PRODUCT', v1438, v1347)
    v1452 = g.clampn(v1451)
    v1453 = g.vmath('DOT_PRODUCT', v1347, v1450)
    v1454 = g.clampn(v1453)
    v1455 = g.vmath('DOT_PRODUCT', v28, v1450)
    v1456 = g.clampn(v1455)
    v1457 = g.group_named('RCE_HgDirectLightEnergy', [('roughness', v660), ('f0', v1370), ('NoL', v1452), ('NoH', v1454), ('NoV', v1349), ('VoH', v1456)])
    v1458 = g.math('MULTIPLY', v1440, 1.0)
    v1459 = g.comb(v1452, v1452, v1452)
    v1460 = g.bc(v1452)
    v1461 = g.vmath('MULTIPLY', v1357, v1460)
    v1462 = g.vmath('MULTIPLY', v1457[0], v1459)
    v1463 = g.vmath('ADD', v1462, v1461)
    v1464 = g.math('GREATER_THAN', v1415, 0.5)
    v1465 = g.vmath('DOT_PRODUCT', v1438, v1347)
    v1466 = g.vmath('DOT_PRODUCT', v28, v1438)
    v1467 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v1468 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v1469 = g.group_named('RCE_HgSssLobe', [('amount', v1385), ('rawNoL', v1465), ('VdotL', v1466), ('selfShadowBias', v1467), ('enableSelfShadowBias', v1468)])
    v1470 = g.bc(v1469[0])
    v1471 = g.vmath('MULTIPLY', v1470, v1376)
    v1472 = g.vmath('ADD', v1463, v1471)
    v1473 = g.mixv(v1464, v1463, v1472)
    v1474 = g.bc(v1458)
    v1475 = g.vmath('MULTIPLY', v1439, v1474)
    v1476 = g.vmath('MULTIPLY', v1473, v1475)
    v1477 = g.vmath('ADD', v1437, v1476)
    v1478 = g.inp('C3_AdditionalLightCount', False, 0.0)
    v1479 = g.math('SUBTRACT', v1478, 0)
    v1480 = g.math('CEIL', v1479, 0.0)
    v1481 = g.math('MAXIMUM', v1480, 0.0)
    g.out_('Z1_it', v1481, False)
    g.out_('Z1_s_H', v1450, True)
    g.out_('Z1_s_L', v1438, True)
    g.out_('Z1_s_LV', v1445, True)
    g.out_('Z1_s_N', v1347, True)
    g.out_('Z1_s_NoH', v1454, False)
    g.out_('Z1_s_NoL', v1452, False)
    g.out_('Z1_s_NoV', v1349, False)
    g.out_('Z1_s_P', v20, True)
    g.out_('Z1_s_V', v28, True)
    g.out_('Z1_s_VoH', v1456, False)
    g.out_('Z1_s_Lloop0', 1.0, False)
    g.out_('Z1_s_color', v1477, True)
    g.out_('Z1_s_energy', v1457[0], True)
    g.out_('Z1_s_f0', v1370, True)
    g.out_('Z1_s_inputData_bakedGI', v30, True)
    g.out_('Z1_s_inputData_fogCoord', 0, False)
    g.out_('Z1_s_inputData_normalWS', v663, True)
    g.out_('Z1_s_inputData_normalizedScreenSpaceUV', v29, True)
    g.out_('Z1_s_inputData_positionCS', v17, True)
    g.out_('Z1_s_inputData_positionCS_w', v18, False)
    g.out_('Z1_s_inputData_positionWS', v20, True)
    g.out_('Z1_s_inputData_shadowCoord', (0.0, 0.0, 0.0), True)
    g.out_('Z1_s_inputData_shadowCoord_w', 0.0, False)
    g.out_('Z1_s_inputData_shadowMask', (1, 1, 1), True)
    g.out_('Z1_s_inputData_shadowMask_w', 1, False)
    g.out_('Z1_s_inputData_vertexLighting', (0, 0, 0), True)
    g.out_('Z1_s_inputData_viewDirectionWS', v28, True)
    g.out_('Z1_s_lightIndex', 0, False)
    g.out_('Z1_s_roughness', v660, False)
    g.out_('Z1_s_sssAmount', v1385, False)
    g.out_('Z1_r___done', 0.0, False)
    g.out_('Z1_r_diffuse', v1357, True)
    g.out_('Z1_r_sssTint', v1376, True)
    g.out_('Z1_r_pixelLightCount', v1478, False)
    v1482 = g.inp('Z1_o_H', True)
    v1483 = g.inp('Z1_o_L', True)
    v1484 = g.inp('Z1_o_LV', True)
    v1485 = g.inp('Z1_o_N', True)
    v1486 = g.inp('Z1_o_NoH', False)
    v1487 = g.inp('Z1_o_NoL', False)
    v1488 = g.inp('Z1_o_NoV', False)
    v1489 = g.inp('Z1_o_P', True)
    v1490 = g.inp('Z1_o_V', True)
    v1491 = g.inp('Z1_o_VoH', False)
    v1492 = g.inp('Z1_o_Lloop0', False)
    v1493 = g.inp('Z1_o_color', True)
    v1494 = g.inp('Z1_o_energy', True)
    v1495 = g.inp('Z1_o_f0', True)
    v1496 = g.inp('Z1_o_inputData_bakedGI', True)
    v1497 = g.inp('Z1_o_inputData_fogCoord', False)
    v1498 = g.inp('Z1_o_inputData_normalWS', True)
    v1499 = g.inp('Z1_o_inputData_normalizedScreenSpaceUV', True)
    v1500 = g.inp('Z1_o_inputData_positionCS', True)
    v1501 = g.inp('Z1_o_inputData_positionCS_w', False)
    v1502 = g.inp('Z1_o_inputData_positionWS', True)
    v1503 = g.inp('Z1_o_inputData_shadowCoord', True)
    v1504 = g.inp('Z1_o_inputData_shadowCoord_w', False)
    v1505 = g.inp('Z1_o_inputData_shadowMask', True)
    v1506 = g.inp('Z1_o_inputData_shadowMask_w', False)
    v1507 = g.inp('Z1_o_inputData_vertexLighting', True)
    v1508 = g.inp('Z1_o_inputData_viewDirectionWS', True)
    v1509 = g.inp('Z1_o_lightIndex', False)
    v1510 = g.inp('Z1_o_roughness', False)
    v1511 = g.inp('Z1_o_sssAmount', False)
    v1512 = g.vmath('ADD', v1493, v1344)
    v1513 = g.math('COMPARE', v60, 0, 1e-05)
    v1514 = g.math('SUBTRACT', 1.0, v1513)
    v1515 = g.vmath('SUBTRACT', v20, v26)
    v1516 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v1517 = g.bc(v1516)
    v1518 = g.vmath('DIVIDE', v1515, v1517)
    v1519 = g.vmath('ADD', v659, v1512)
    v1520 = g.vmath('LENGTH', v1518)
    v1521 = g.sep(v1518)
    v1522 = g.comb(v1521[0], v1521[2], 0.0)
    v1523 = g.vmath('LENGTH', v1522)
    v1524 = g.math('ABSOLUTE', v1521[1], 0.0)
    v1525 = g.math('MAXIMUM', v1523, v1524)
    v1526 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v1527 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v1528 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v1529 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v1530 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v1531 = g.inp('_RuriFogColor_w', False, 0.0)
    v1532 = g.group_named('RCE_RuriApplyFog', [('inColor', v1519), ('inColor_w', 1), ('sphericalVertexDistance', v1520), ('cylindricalVertexDistance', v1525), ('environmentalStart', v1526), ('environmentalEnd', v1527), ('renderDistanceStart', v1528), ('renderDistanceEnd', v1529), ('fogColor', v1530), ('fogColor_w', v1531)])
    v1533 = g.mixv(v1514, v1512, v1532[0])
    g.out_('ret_gBuffer0', v1533, True)
    g.out_('ret_gBuffer0_w', v221, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v1512, True)
    g.out_('ret_color_w', v221, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


def build_Ruri_Endfield_Scene_LitTransparent():
    t = _tree('Ruri Endfield Scene LitTransparent')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.vmath('SUBTRACT', v14, v0)
    v133 = g.comb(v77, v77, 0.0)
    v134 = g.vmath('MULTIPLY', v133, v132)
    v135 = g.vmath('ADD', v134, v0)
    v136 = g.comb(v83[0], v83[1], 0.0)
    v137 = g.comb(v83[2], v82, 0.0)
    v138 = g.vmath('MULTIPLY', v135, v136)
    v139 = g.vmath('ADD', v138, v137)
    v140 = g.comb(v88, v88, 0.0)
    v141 = g.vmath('MULTIPLY', v140, v132)
    v142 = g.vmath('ADD', v141, v0)
    v143 = g.comb(v94[0], v94[1], 0.0)
    v144 = g.comb(v94[2], v93, 0.0)
    v145 = g.vmath('MULTIPLY', v142, v143)
    v146 = g.vmath('ADD', v145, v144)
    g.out_('F4_BaseColorMap_uv', v139, True)
    v147 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v148 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    g.out_('F5_NormalMap_uv', v146, True)
    v149 = g.inp('F5_NormalMap', True, (1.0, 1.0, 1.0))
    v150 = g.inp('F5_NormalMap_alpha', False, 1.0)
    v151 = g.math('MULTIPLY', v58, v148)
    g.out_('F6_MROMap_uv', v146, True)
    v152 = g.inp('F6_MROMap', True, (1.0, 1.0, 1.0))
    v153 = g.inp('F6_MROMap_alpha', False, 1.0)
    v154 = g.sep(v152)
    v155 = g.sep(v149)
    v156 = g.math('MULTIPLY', v155[0], v150)
    v157 = g.math('MULTIPLY_ADD', v156, 2, -1)
    v158 = g.math('MULTIPLY_ADD', v155[1], 2, -1)
    v159 = g.inp('_NormalScale', False, 0.0)
    v160 = g.math('MULTIPLY', v157, v159)
    v161 = g.math('MULTIPLY', v158, v159)
    v162 = g.vmath('MULTIPLY', v147, v37)
    v163 = g.inp('_BaseColorBrighterScale', False, 1.0)
    v164 = g.bc(v163)
    v165 = g.vmath('MULTIPLY', v162, v164)
    v166 = g.sep(v165)
    v167 = g.clampn(v166[0])
    v168 = g.clampn(v166[1])
    v169 = g.clampn(v166[2])
    v170 = g.comb(v167, v168, v169)
    v171 = g.inp('_BaseColorTintCover', False, 0.0)
    v172 = g.mixv(v171, v170, v37)
    v173 = g.inp('_RoughnessMin', False, 0.0)
    v174 = g.inp('_RoughnessMax', False, 1.0)
    v175 = g.mixf(v154[1], v173, v174)
    v176 = g.inp('_Metallic', False, 0.0)
    v177 = g.inp('_BaseTextureMapCount', False, 0.0)
    v178 = g.math('SUBTRACT', v177, 1)
    v179 = g.clampn(v178)
    v180 = g.mixf(v179, v154[0], v176)
    v181 = g.inp('_OcclusionStrength', False, 1.0)
    v182 = g.mixf(v181, 1, v154[2])
    v183 = g.inp('_PorosityFactorX', False, 0.2)
    v184 = g.math('MULTIPLY', v183, v175)
    v185 = g.inp('_PorosityFactorZ', False, 0.0)
    v186 = g.math('MULTIPLY', v185, v180)
    v187 = g.math('ADD', v184, v186)
    v188 = g.inp('_PorosityFactorY', False, 0.4)
    v189 = g.math('ADD', v187, v188)
    v190 = g.clampn(v189)
    v191 = g.math('MULTIPLY', v190, 0.95)
    v192 = g.math('ADD', v191, 0.05)
    v193 = g.inp('_DisableVerticalFlow', False, 0.0)
    v194 = g.math('SUBTRACT', 1, v193)
    v195 = g.math('MULTIPLY', v192, v194)
    v196 = g.inp('_EffectIntensity', False, 1.0)
    v197 = g.math('MULTIPLY', v151, v196)
    v198 = g.vmath('DOT_PRODUCT', v36, v21)
    v199 = g.math('LESS_THAN', v198, 0)
    v200 = g.mixf(v199, 1, -1)
    v201 = g.comb(v157, v158, 0.0)
    v202 = g.comb(v157, v158, 0.0)
    v203 = g.vmath('DOT_PRODUCT', v201, v202)
    v204 = g.math('MINIMUM', v203, 1)
    v205 = g.math('SUBTRACT', 1, v204)
    v206 = g.math('SQRT', v205, 0.0)
    v207 = g.math('MAXIMUM', v206, 1.0000000168623835E-16)
    v208 = g.math('MULTIPLY', v207, v200)
    v209 = g.math('GREATER_THAN', v5, 0)
    v210 = g.mixf(v209, -1, 1)
    v211 = g.vmath('CROSS_PRODUCT', v21, v22)
    v212 = g.bc(v210)
    v213 = g.vmath('MULTIPLY', v212, v211)
    v214 = g.bc(v208)
    v215 = g.vmath('MULTIPLY', v21, v214)
    v216 = g.bc(v160)
    v217 = g.vmath('MULTIPLY', v22, v216)
    v218 = g.vmath('ADD', v215, v217)
    v219 = g.bc(v161)
    v220 = g.vmath('MULTIPLY', v213, v219)
    v221 = g.vmath('ADD', v218, v220)
    v222 = g.vmath('NORMALIZE', v221)
    v223 = g.mixf(v121, v58, v197)
    v224 = g.mixv(v121, v65, v172)
    v225 = g.mixf(v121, v114, v175)
    v226 = g.mixf(v121, v115, v180)
    v227 = g.mixf(v121, v116, v182)
    v228 = g.mixf(v121, v117, v195)
    v229 = g.mixv(v121, v36, v222)
    v230 = g.vmath('NORMALIZE', v21)
    v231 = g.vmath('NORMALIZE', v22)
    v232 = g.vmath('CROSS_PRODUCT', v230, v231)
    v233 = g.bc(v5)
    v234 = g.vmath('MULTIPLY', v232, v233)
    v235 = g.inp('_UseMacroNormalMap', False, 0.0)
    v236 = g.math('GREATER_THAN', v235, 0.5)
    g.out_('F7_MacroNormalMap_uv', v0, True)
    v237 = g.inp('F7_MacroNormalMap', True, (1.0, 1.0, 1.0))
    v238 = g.inp('F7_MacroNormalMap_alpha', False, 1.0)
    v239 = g.inp('_MacroNormalMapScale', False, 1.0)
    v240 = g.sep(v237)
    v241 = g.math('MULTIPLY', v238, v240[0])
    v242 = g.math('MULTIPLY', v241, 2.0)
    v243 = g.math('SUBTRACT', v242, 1.0)
    v244 = g.math('MULTIPLY', v240[1], 2.0)
    v245 = g.math('SUBTRACT', v244, 1.0)
    v246 = g.math('MULTIPLY', v243, v243)
    v247 = g.math('MULTIPLY', v245, v245)
    v248 = g.math('ADD', v246, v247)
    v249 = g.clampn(v248)
    v250 = g.math('SUBTRACT', 1.0, v249)
    v251 = g.math('SQRT', v250, 0.0)
    v252 = g.math('MAXIMUM', v251, 1e-16)
    v253 = g.math('MULTIPLY', v243, v239)
    v254 = g.math('MULTIPLY', v245, v239)
    v255 = g.comb(v253, v254, v252)
    v256 = g.sep(v255)
    v257 = g.bc(v256[0])
    v258 = g.vmath('MULTIPLY', v257, v231)
    v259 = g.bc(v256[1])
    v260 = g.vmath('MULTIPLY', v259, v234)
    v261 = g.vmath('ADD', v258, v260)
    v262 = g.bc(v256[2])
    v263 = g.vmath('MULTIPLY', v262, v230)
    v264 = g.vmath('ADD', v261, v263)
    v265 = g.vmath('NORMALIZE', v264)
    v266 = g.vmath('SUBTRACT', v265, v230)
    v267 = g.vmath('ADD', v229, v266)
    v268 = g.vmath('NORMALIZE', v267)
    v269 = g.mixv(v236, v229, v268)
    v270 = g.inp('_EnableDetailMap', False, 0.0)
    v271 = g.math('GREATER_THAN', v270, 0.5)
    v272 = g.vmath('DISTANCE', v20, v26)
    v273 = g.inp('_DetailFalloffStart', False, 750.0)
    v274 = g.math('SUBTRACT', v272, v273)
    v275 = g.inp('_DetailFalloffEnd', False, 800.0)
    v276 = g.math('SUBTRACT', v275, v273)
    v277 = g.math('MAXIMUM', v276, 0.001)
    v278 = g.math('DIVIDE', v274, v277)
    v279 = g.clampn(v278)
    v280 = g.math('SUBTRACT', 1, v279)
    g.out_('F8_DetailMap_uv', v0, True)
    v281 = g.inp('F8_DetailMap', True, (1.0, 1.0, 1.0))
    v282 = g.inp('F8_DetailMap_alpha', False, 1.0)
    v283 = g.inp('_DetailMaskMode', False, 0.0)
    v284 = g.math('LESS_THAN', v283, 0.5)
    v285 = g.math('LESS_THAN', v283, 1.5)
    v286 = g.math('LESS_THAN', v283, 2.5)
    v287 = g.math('LESS_THAN', v283, 3.5)
    g.out_('F9_NormalMap_uv', v0, True)
    v288 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v289 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v290 = g.sep(v288)
    v291 = g.math('LESS_THAN', v283, 4.5)
    v292 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v293 = g.inp('F9_NormalMap_alpha', False, 1.0)
    g.out_('F10_MROMap_uv', v0, True)
    v294 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v295 = g.inp('F10_MROMap_alpha', False, 1.0)
    v296 = g.mixf(v291, v295, v293)
    v297 = g.mixf(v287, v296, v290[2])
    v298 = g.mixf(v286, v297, v223)
    v299 = g.mixf(v285, v298, v282)
    v300 = g.mixf(v284, v299, 1)
    v301 = g.math('MULTIPLY', v280, v300)
    v302 = g.sep(v281)
    v303 = g.comb(v302[0], v302[1], 0.0)
    v304 = g.vmath('MULTIPLY', v303, (2, 2, 0.0))
    v305 = g.vmath('SUBTRACT', v304, (1, 1, 0.0))
    v306 = g.inp('_DetailNormalIntensity', False, 1.0)
    v307 = g.math('MULTIPLY', v306, v301)
    v308 = g.comb(v307, v307, 0.0)
    v309 = g.vmath('MULTIPLY', v305, v308)
    v310 = g.sep(v309)
    v311 = g.comb(v310[0], v310[1], 1)
    v312 = g.sep(v311)
    v313 = g.bc(v312[0])
    v314 = g.vmath('MULTIPLY', v313, v231)
    v315 = g.bc(v312[1])
    v316 = g.vmath('MULTIPLY', v315, v234)
    v317 = g.vmath('ADD', v314, v316)
    v318 = g.bc(v312[2])
    v319 = g.vmath('MULTIPLY', v318, v230)
    v320 = g.vmath('ADD', v317, v319)
    v321 = g.vmath('NORMALIZE', v320)
    v322 = g.vmath('SUBTRACT', v321, v230)
    v323 = g.vmath('ADD', v269, v322)
    v324 = g.vmath('NORMALIZE', v323)
    v325 = g.inp('_DetailMode', False, 0.0)
    v326 = g.math('LESS_THAN', v325, 0.5)
    v327 = g.inp('_DetailBaseColorBrighterScale', False, 1.0)
    v328 = g.mixf(v301, 1, v327)
    v329 = g.bc(v328)
    v330 = g.vmath('MULTIPLY', v224, v329)
    v331 = g.sep(v330)
    v332 = g.clampn(v331[0])
    v333 = g.clampn(v331[1])
    v334 = g.clampn(v331[2])
    v335 = g.comb(v332, v333, v334)
    v336 = g.inp('_DetailOverlayColor', True, (0.0, 0.0, 0.0))
    v337 = g.inp('_DetailOverlayColor_w', False, 0.0)
    v338 = g.vmath('MULTIPLY', v335, v336)
    v339 = g.math('MULTIPLY', v301, v337)
    v340 = g.mixv(v339, v335, v338)
    v341 = g.inp('_DetailPBRIntensity', False, 1.0)
    v342 = g.math('MULTIPLY', v301, v341)
    v343 = g.mixf(v342, v225, v302[2])
    v344 = g.math('MULTIPLY', v301, v341)
    v345 = g.mixf(v344, v225, v302[2])
    v346 = g.math('MULTIPLY', v227, v282)
    v347 = g.mixf(v301, v227, v346)
    v348 = g.mixv(v326, v224, v340)
    v349 = g.mixf(v326, v345, v343)
    v350 = g.mixf(v326, v347, v227)
    v351 = g.mixv(v271, v224, v348)
    v352 = g.mixf(v271, v225, v349)
    v353 = g.mixf(v271, v227, v350)
    v354 = g.mixv(v271, v269, v324)
    v355 = g.bc(v163)
    v356 = g.vmath('MULTIPLY', v351, v355)
    v357 = g.sep(v356)
    v358 = g.clampn(v357[0])
    v359 = g.clampn(v357[1])
    v360 = g.clampn(v357[2])
    v361 = g.comb(v358, v359, v360)
    v362 = g.mixv(v171, v361, v37)
    v363 = g.mixf(v352, v173, v174)
    v364 = g.clampn(v363, 0, 1)
    v365 = g.mixf(v181, 1, v353)
    v366 = g.math('LESS_THAN', v177, 0.5)
    v367 = g.math('SUBTRACT', 1.0, v366)
    v368 = g.mixf(v367, v226, v115)
    v369 = g.inp('_EnableTriChannelMask', False, 0.0)
    v370 = g.math('GREATER_THAN', v369, 0.5)
    v371 = g.vmath('SUBTRACT', v14, v0)
    v372 = g.inp('_MaskUVSet', False, 0.0)
    v373 = g.comb(v372, v372, 0.0)
    v374 = g.vmath('MULTIPLY', v373, v371)
    v375 = g.vmath('ADD', v374, v0)
    v376 = g.inp('_MaskMap_ST', True, (1.0, 1.0, 0.0))
    v377 = g.inp('_MaskMap_ST_w', False, 0.0)
    v378 = g.sep(v376)
    v379 = g.comb(v378[0], v378[1], 0.0)
    v380 = g.comb(v378[2], v377, 0.0)
    v381 = g.vmath('MULTIPLY', v375, v379)
    v382 = g.vmath('ADD', v381, v380)
    g.out_('F11_MaskMap_uv', v382, True)
    v383 = g.inp('F11_MaskMap', True, (1.0, 1.0, 1.0))
    v384 = g.inp('F11_MaskMap_alpha', False, 1.0)
    v385 = g.sep(v383)
    v386 = g.inp('_MaskBOffset', False, 0.0)
    v387 = g.math('ADD', v385[2], v386)
    v388 = g.inp('_MaskBScale', False, 0.0)
    v389 = g.math('ADD', v388, 1)
    v390 = g.math('MULTIPLY', v388, -0.5)
    v391 = g.math('MULTIPLY_ADD', v387, v389, v390)
    v392 = g.clampn(v391, 0, 1)
    v393 = g.inp('_MaskAlbedoB', True, (0.0, 0.0, 1.0))
    v394 = g.inp('_MaskAlbedoB_w', False, 1.0)
    v395 = g.math('MULTIPLY', v392, v394)
    v396 = g.inp('_MaskGOffset', False, 0.0)
    v397 = g.math('ADD', v385[1], v396)
    v398 = g.inp('_MaskGScale', False, 0.0)
    v399 = g.math('ADD', v398, 1)
    v400 = g.math('MULTIPLY', v398, -0.5)
    v401 = g.math('MULTIPLY_ADD', v397, v399, v400)
    v402 = g.clampn(v401, 0, 1)
    v403 = g.inp('_MaskAlbedoG', True, (0.0, 1.0, 0.0))
    v404 = g.inp('_MaskAlbedoG_w', False, 1.0)
    v405 = g.math('MULTIPLY', v402, v404)
    v406 = g.inp('_MaskROffset', False, 0.0)
    v407 = g.math('ADD', v385[0], v406)
    v408 = g.inp('_MaskRScale', False, 0.0)
    v409 = g.math('ADD', v408, 1)
    v410 = g.math('MULTIPLY', v408, -0.5)
    v411 = g.math('MULTIPLY_ADD', v407, v409, v410)
    v412 = g.clampn(v411, 0, 1)
    v413 = g.inp('_MaskAlbedoR', True, (1.0, 0.0, 0.0))
    v414 = g.inp('_MaskAlbedoR_w', False, 1.0)
    v415 = g.math('MULTIPLY', v412, v414)
    v416 = g.mixv(v395, v362, v393)
    v417 = g.mixv(v405, v416, v403)
    v418 = g.mixv(v415, v417, v413)
    v419 = g.inp('_MaskRoghnessB', False, 0.25)
    v420 = g.mixf(v395, v364, v419)
    v421 = g.inp('_MaskRoghnessG', False, 0.25)
    v422 = g.mixf(v405, v420, v421)
    v423 = g.inp('_MaskRoghnessR', False, 0.25)
    v424 = g.mixf(v415, v422, v423)
    v425 = g.inp('_MaskMetallicB', False, 0.0)
    v426 = g.mixf(v395, v368, v425)
    v427 = g.inp('_MaskMetallicG', False, 0.0)
    v428 = g.mixf(v405, v426, v427)
    v429 = g.inp('_MaskMetallicR', False, 0.0)
    v430 = g.mixf(v415, v428, v429)
    v431 = g.mixv(v370, v362, v418)
    v432 = g.mixf(v370, v364, v424)
    v433 = g.mixf(v370, v368, v430)
    v434 = g.inp('_LayerBlend', False, 0.0)
    v435 = g.math('GREATER_THAN', v434, 0.5)
    v436 = g.inp('_LayerBlendUVType', False, 0.0)
    v437 = g.math('GREATER_THAN', v436, 0.5)
    v438 = g.math('LESS_THAN', v436, 1.5)
    v439 = g.math('MULTIPLY', v437, v438)
    v440 = g.sep(v20)
    v441 = g.comb(v440[0], v440[2], 0.0)
    v442 = g.inp('_Layer1Tilling', False, 1.0)
    v443 = g.comb(v442, v442, 0.0)
    v444 = g.vmath('MULTIPLY', v441, v443)
    v445 = g.math('GREATER_THAN', v436, 1.5)
    v446 = g.comb(v442, v442, 0.0)
    v447 = g.vmath('MULTIPLY', v15, v446)
    v448 = g.comb(v442, v442, 0.0)
    v449 = g.vmath('MULTIPLY', v0, v448)
    v450 = g.mixv(v445, v449, v447)
    v451 = g.mixv(v439, v450, v444)
    g.out_('F12_Layer1BaseMap_uv', v451, True)
    v452 = g.inp('F12_Layer1BaseMap', True, (1.0, 1.0, 1.0))
    v453 = g.inp('F12_Layer1BaseMap_alpha', False, 1.0)
    g.out_('F13_Layer1BumpMap_uv', v451, True)
    v454 = g.inp('F13_Layer1BumpMap', True, (1.0, 1.0, 1.0))
    v455 = g.inp('F13_Layer1BumpMap_alpha', False, 1.0)
    g.out_('F14_BaseHeightMap_uv', v451, True)
    v456 = g.inp('F14_BaseHeightMap', True, (1.0, 1.0, 1.0))
    v457 = g.inp('F14_BaseHeightMap_alpha', False, 1.0)
    v458 = g.sep(v456)
    v459 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v460 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v461 = g.sep(v459)
    v462 = g.math('MULTIPLY_ADD', v461[0], 2, -1)
    v463 = g.math('MULTIPLY_ADD', v461[1], 2, -1)
    v464 = g.math('ABSOLUTE', v462, 0.0)
    v465 = g.math('LESS_THAN', v464, 0.012000000104308128)
    v466 = g.mixf(v465, v462, 0)
    v467 = g.math('ABSOLUTE', v463, 0.0)
    v468 = g.math('LESS_THAN', v467, 0.012000000104308128)
    v469 = g.mixf(v468, v463, 0)
    v470 = g.math('MULTIPLY', v466, v159)
    v471 = g.math('MULTIPLY', v469, v159)
    v472 = g.vmath('DOT_PRODUCT', v354, v21)
    v473 = g.math('LESS_THAN', v472, 0)
    v474 = g.mixf(v473, 1, -1)
    v475 = g.inp('_LayerBlendType', False, 1.0)
    v476 = g.math('LESS_THAN', v475, 0.5)
    v477 = g.sep(v11)
    v478 = g.math('SUBTRACT', 1, v477[0])
    v479 = g.clampn(v478, 0, 1)
    v480 = g.math('LESS_THAN', v475, 1.5)
    v481 = g.inp('_LayerBlendMaskUVType', False, 0.0)
    v482 = g.math('GREATER_THAN', v481, 0.5)
    v483 = g.mixv(v482, v14, v0)
    v484 = g.inp('_LayerBlendMaskType', False, 0.0)
    v485 = g.math('COMPARE', v484, 0, 1e-05)
    v486 = g.math('SUBTRACT', 1.0, v485)
    g.out_('F15_LayerBlendMaskMap_uv', v483, True)
    v487 = g.inp('F15_LayerBlendMaskMap', True, (1.0, 1.0, 1.0))
    v488 = g.inp('F15_LayerBlendMaskMap_alpha', False, 1.0)
    v489 = g.sep(v487)
    v490 = g.mixf(v486, v489[0], v460)
    v491 = g.clampn(v490, 0, 1)
    v492 = g.math('GREATER_THAN', v5, 0)
    v493 = g.mixf(v492, -1, 1)
    v494 = g.vmath('CROSS_PRODUCT', v21, v22)
    v495 = g.bc(v493)
    v496 = g.vmath('MULTIPLY', v495, v494)
    v497 = g.comb(v466, v469, 0.0)
    v498 = g.comb(v466, v469, 0.0)
    v499 = g.vmath('DOT_PRODUCT', v497, v498)
    v500 = g.math('MINIMUM', v499, 1)
    v501 = g.math('SUBTRACT', 1, v500)
    v502 = g.math('SQRT', v501, 0.0)
    v503 = g.bc(v502)
    v504 = g.vmath('MULTIPLY', v21, v503)
    v505 = g.bc(v470)
    v506 = g.vmath('MULTIPLY', v22, v505)
    v507 = g.vmath('ADD', v504, v506)
    v508 = g.bc(v471)
    v509 = g.vmath('MULTIPLY', v496, v508)
    v510 = g.vmath('ADD', v507, v509)
    v511 = g.inp('_TopBlendWithBumpMap', False, 0.0)
    v512 = g.sep(v510)
    v513 = g.vmath('DOT_PRODUCT', v510, v510)
    v514 = g.math('INVERSE_SQRT', v513, 0.0)
    v515 = g.math('MULTIPLY', v512[1], v514)
    v516 = g.sep(v21)
    v517 = g.math('SUBTRACT', v515, v516[1])
    v518 = g.math('MULTIPLY_ADD', v511, v517, v516[1])
    v519 = g.inp('_TopBlendThreshold', False, 0.5)
    v520 = g.math('SUBTRACT', v518, v519)
    v521 = g.inp('_TopBlendSmoothness', False, 0.5)
    v522 = g.math('MAXIMUM', 1.1754943508222875E-38, v521)
    v523 = g.math('DIVIDE', v520, v522)
    v524 = g.clampn(v523, 0, 1)
    v525 = g.mixf(v480, v524, v491)
    v526 = g.mixf(v476, v525, v479)
    v527 = g.math('SUBTRACT', 1, v526)
    v528 = g.math('MULTIPLY', v453, v526)
    v529 = g.math('MULTIPLY', v458[0], v527)
    v530 = g.math('MAXIMUM', v528, v529)
    v531 = g.math('MULTIPLY', v530, -1.0)
    v532 = g.inp('_LayerBlendHeightTransition', False, 1.0)
    v533 = g.math('MULTIPLY_ADD', v526, v453, v532)
    v534 = g.math('ADD', v531, v533)
    v535 = g.math('MAXIMUM', v534, 0)
    v536 = g.math('ADD', v535, 9.999999974752427E-07)
    v537 = g.math('MULTIPLY', v526, v536)
    v538 = g.math('MULTIPLY', v530, -1.0)
    v539 = g.math('MULTIPLY_ADD', v527, v458[0], v532)
    v540 = g.math('ADD', v538, v539)
    v541 = g.math('MAXIMUM', v540, 0)
    v542 = g.math('ADD', v541, 9.999999974752427E-07)
    v543 = g.math('MULTIPLY', v527, v542)
    v544 = g.inp('_LayerBlendHeight', False, 1.0)
    v545 = g.math('COMPARE', v544, 0, 1e-05)
    v546 = g.math('SUBTRACT', 1.0, v545)
    v547 = g.math('ADD', v537, v543)
    v548 = g.math('MAXIMUM', v547, 1.1754943508222875E-38)
    v549 = g.math('DIVIDE', v537, v548)
    v550 = g.mixf(v546, v526, v549)
    v551 = g.vmath('DOT_PRODUCT', v452, (0.2126729041337967, 0.7151522040367126, 0.07217500358819962))
    v552 = g.inp('_Layer1Saturation', False, 0.0)
    v553 = g.math('ADD', 1, v552)
    v554 = g.clampn(v553, 0, 1)
    v555 = g.math('MULTIPLY', v551, -1.0)
    v556 = g.sep(v452)
    v557 = g.math('ADD', v555, v556[0])
    v558 = g.math('MULTIPLY_ADD', v554, v557, v551)
    v559 = g.math('MULTIPLY', v551, -1.0)
    v560 = g.math('ADD', v559, v556[1])
    v561 = g.math('MULTIPLY_ADD', v554, v560, v551)
    v562 = g.math('MULTIPLY', v551, -1.0)
    v563 = g.math('ADD', v562, v556[2])
    v564 = g.math('MULTIPLY_ADD', v554, v563, v551)
    v565 = g.comb(v558, v561, v564)
    v566 = g.inp('_Layer1TintColor', True, (1.0, 1.0, 1.0))
    v567 = g.inp('_Layer1TintColor_w', False, 1.0)
    v568 = g.vmath('MULTIPLY', v565, v566)
    v569 = g.inp('_Layer1ColorBrighterScale', False, 1.0)
    v570 = g.bc(v569)
    v571 = g.vmath('MULTIPLY', v568, v570)
    v572 = g.mixv(v550, v431, v571)
    v573 = g.inp('_LayerMetallicType', False, 0.0)
    v574 = g.math('COMPARE', v573, 0, 1e-05)
    v575 = g.math('SUBTRACT', 1.0, v574)
    v576 = g.inp('_Layer1Metallic', False, 0.0)
    v577 = g.mixf(v575, v576, v453)
    v578 = g.mixf(v550, v433, v577)
    v579 = g.sep(v454)
    v580 = g.mixf(v550, v432, v579[2])
    v581 = g.inp('_Layer1AOStrength', False, 1.0)
    v582 = g.mixf(v581, 1, v455)
    v583 = g.mixf(v550, v365, v582)
    v584 = g.math('MULTIPLY_ADD', v579[0], 2, -1)
    v585 = g.math('MULTIPLY_ADD', v579[1], 2, -1)
    v586 = g.math('ABSOLUTE', v584, 0.0)
    v587 = g.math('LESS_THAN', v586, 0.012000000104308128)
    v588 = g.mixf(v587, v584, 0)
    v589 = g.math('ABSOLUTE', v585, 0.0)
    v590 = g.math('LESS_THAN', v589, 0.012000000104308128)
    v591 = g.mixf(v590, v585, 0)
    v592 = g.inp('_Layer1BumpScale', False, 1.0)
    v593 = g.math('MULTIPLY', v588, v592)
    v594 = g.math('MULTIPLY', v591, v592)
    v595 = g.comb(v588, v591, 0.0)
    v596 = g.comb(v588, v591, 0.0)
    v597 = g.vmath('DOT_PRODUCT', v595, v596)
    v598 = g.math('MINIMUM', v597, 1)
    v599 = g.math('SUBTRACT', 1, v598)
    v600 = g.math('SQRT', v599, 0.0)
    v601 = g.math('MAXIMUM', v600, 1.0000000168623835E-16)
    v602 = g.comb(v466, v469, 0.0)
    v603 = g.comb(v466, v469, 0.0)
    v604 = g.vmath('DOT_PRODUCT', v602, v603)
    v605 = g.math('SUBTRACT', 1, v604)
    v606 = g.math('MAXIMUM', v605, 0)
    v607 = g.math('SQRT', v606, 0.0)
    v608 = g.math('ADD', v607, 1)
    v609 = g.comb(v470, v471, v608)
    v610 = g.math('MULTIPLY', v593, -1.0)
    v611 = g.math('MULTIPLY', v594, -1.0)
    v612 = g.comb(v610, v611, v601)
    v613 = g.vmath('DOT_PRODUCT', v609, v612)
    v614 = g.inp('_Layer1BaseNormalIntensity', False, 0.0)
    v615 = g.math('MULTIPLY', v593, -1.0)
    v616 = g.math('MULTIPLY', v613, v470)
    v617 = g.math('DIVIDE', v616, v608)
    v618 = g.math('ADD', v593, v617)
    v619 = g.math('ADD', v615, v618)
    v620 = g.math('MULTIPLY_ADD', v614, v619, v593)
    v621 = g.math('MULTIPLY', v594, -1.0)
    v622 = g.math('MULTIPLY', v613, v471)
    v623 = g.math('DIVIDE', v622, v608)
    v624 = g.math('ADD', v594, v623)
    v625 = g.math('ADD', v621, v624)
    v626 = g.math('MULTIPLY_ADD', v614, v625, v594)
    v627 = g.math('MULTIPLY', v601, -1.0)
    v628 = g.math('SUBTRACT', v613, v601)
    v629 = g.math('ADD', v627, v628)
    v630 = g.math('MULTIPLY_ADD', v614, v629, v601)
    v631 = g.comb(v466, v469, 0.0)
    v632 = g.comb(v466, v469, 0.0)
    v633 = g.vmath('DOT_PRODUCT', v631, v632)
    v634 = g.math('SUBTRACT', 1, v633)
    v635 = g.math('MAXIMUM', v634, 0)
    v636 = g.math('SQRT', v635, 0.0)
    v637 = g.math('MULTIPLY', v470, -1.0)
    v638 = g.math('ADD', v637, v620)
    v639 = g.math('MULTIPLY_ADD', v550, v638, v470)
    v640 = g.math('MULTIPLY', v471, -1.0)
    v641 = g.math('ADD', v640, v626)
    v642 = g.math('MULTIPLY_ADD', v550, v641, v471)
    v643 = g.math('MULTIPLY', v636, -1.0)
    v644 = g.math('ADD', v643, v630)
    v645 = g.math('MULTIPLY_ADD', v550, v644, v636)
    v646 = g.math('MULTIPLY', v474, v645)
    v647 = g.math('GREATER_THAN', v5, 0)
    v648 = g.mixf(v647, -1, 1)
    v649 = g.vmath('CROSS_PRODUCT', v21, v22)
    v650 = g.bc(v648)
    v651 = g.vmath('MULTIPLY', v650, v649)
    v652 = g.bc(v646)
    v653 = g.vmath('MULTIPLY', v21, v652)
    v654 = g.bc(v639)
    v655 = g.vmath('MULTIPLY', v22, v654)
    v656 = g.vmath('ADD', v653, v655)
    v657 = g.bc(v642)
    v658 = g.vmath('MULTIPLY', v651, v657)
    v659 = g.vmath('ADD', v656, v658)
    v660 = g.vmath('NORMALIZE', v659)
    v661 = g.mixv(v435, v431, v572)
    v662 = g.mixf(v435, v432, v580)
    v663 = g.mixf(v435, v433, v578)
    v664 = g.mixf(v435, v365, v583)
    v665 = g.mixv(v435, v354, v660)
    v666 = g.mixf(v435, v157, v466)
    v667 = g.mixf(v435, v158, v469)
    v668 = g.mixf(v435, v160, v639)
    v669 = g.mixf(v435, v161, v642)
    v670 = g.mixf(v435, v200, v474)
    v671 = g.inp('_EmissionTint', True, (1.0, 1.0, 1.0))
    v672 = g.inp('_EmissionTint_w', False, 1.0)
    v673 = g.vmath('MULTIPLY', v661, v671)
    v674 = g.inp('_EmissiveIntensity', False, 0.0)
    v675 = g.bc(v674)
    v676 = g.vmath('MULTIPLY', v673, v675)
    v677 = g.inp('_UseEmissiveMap', False, 0.0)
    v678 = g.math('GREATER_THAN', v677, 0.5)
    v679 = g.inp('_AlbedoAffectEmissive', False, 1.0)
    v680 = g.mixv(v679, v661, (1, 1, 1))
    v681 = g.inp('_EnableEmissiveAnim', False, 0.0)
    v682 = g.math('GREATER_THAN', v681, 0.5)
    v683 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v684 = g.inp('_Time_w', False, 0.0)
    v685 = g.sep(v683)
    v686 = g.inp('_EmissiveAnimSpeed', False, 0.0)
    v687 = g.math('MULTIPLY', v685[1], v686)
    v688 = g.inp('_EmissiveAnimRandom', False, 0.0)
    v689 = g.math('MULTIPLY_ADD', v687, 0.15915493667125702, v688)
    v690 = g.math('FRACT', v689, 0.0)
    v691 = g.inp('_EmissiveAnimInterval', False, 1.0)
    v692 = g.math('MULTIPLY', v690, v691)
    v693 = g.clampn(v692, 0, 1)
    v694 = g.math('MULTIPLY_ADD', v693, 2, -1)
    v695 = g.math('MULTIPLY', v694, v694)
    v696 = g.inp('_EmissiveMinBrightness', False, 0.0)
    v697 = g.math('SUBTRACT', 1, v696)
    v698 = g.math('ADD', 1, v696)
    v699 = g.math('DIVIDE', v698, v697)
    v700 = g.math('MULTIPLY', v694, v695)
    v701 = g.math('ABSOLUTE', v700, 0.0)
    v702 = g.math('MULTIPLY', v701, 4)
    v703 = g.math('MULTIPLY_ADD', v695, -6, v702)
    v704 = g.math('ADD', v703, 1)
    v705 = g.math('ADD', v699, v704)
    v706 = g.math('MULTIPLY', v705, v697)
    v707 = g.math('MULTIPLY_ADD', v706, 0.5, -1)
    v708 = g.mixf(v682, 0, v707)
    v709 = g.inp('_EnableEmissiveAnimSweep', False, 0.0)
    v710 = g.math('GREATER_THAN', v709, 0.5)
    v711 = g.inp('_EmissiveMaskChannel', False, 0.0)
    v712 = g.math('GREATER_THAN', v711, 4.5)
    v713 = g.math('MAXIMUM', v710, v712)
    v714 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v715 = g.sep(v714)
    v716 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v717 = g.sep(v716)
    v718 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v719 = g.sep(v718)
    v720 = g.comb(v715[0], v717[1], v719[2])
    v721 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v722 = g.sep(v721)
    v723 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v724 = g.sep(v723)
    v725 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v726 = g.sep(v725)
    v727 = g.comb(v722[1], v724[1], v726[1])
    v728 = g.vmath('SUBTRACT', v20, v720)
    v729 = g.vmath('DOT_PRODUCT', v727, v728)
    v730 = g.inp('_EmissiveSweepRandom', False, 0.0)
    v731 = g.sep(v720)
    v732 = g.math('MULTIPLY_ADD', v730, v731[0], v685[1])
    v733 = g.inp('_EmissiveSweepInterval', False, 3.0)
    v734 = g.math('DIVIDE', v732, v733)
    v735 = g.math('ABSOLUTE', v734, 0.0)
    v736 = g.math('FRACT', v735, 0.0)
    v737 = g.math('MULTIPLY', v734, -1.0)
    v738 = g.math('LESS_THAN', v734, v737)
    v739 = g.math('SUBTRACT', 1.0, v738)
    v740 = g.math('MULTIPLY', v736, -1.0)
    v741 = g.mixf(v739, v740, v736)
    v742 = g.inp('_EmissiveSweepSpeed', False, 3.0)
    v743 = g.math('MULTIPLY', 0.30000001192092896, v733)
    v744 = g.math('MULTIPLY', v743, -1.0)
    v745 = g.math('MULTIPLY_ADD', v741, v733, v744)
    v746 = g.math('MULTIPLY', v742, v745)
    v747 = g.math('SUBTRACT', v729, v746)
    v748 = g.math('ABSOLUTE', v747, 0.0)
    v749 = g.inp('_EmissiveSweepWidth', False, 0.8)
    v750 = g.math('DIVIDE', v748, v749)
    v751 = g.clampn(v750, 0, 1)
    v752 = g.math('MULTIPLY', v751, -1.0)
    v753 = g.inp('_EmissiveSweepFalloff', False, 1.0)
    v754 = g.math('MULTIPLY_ADD', v752, v753, v753)
    v755 = g.clampn(v754, 0, 1)
    v756 = g.inp('_EmissiveSweepAlbedoScale', False, 0.0)
    v757 = g.vmath('DOT_PRODUCT', v661, (0.3330000042915344, 0.3330000042915344, 0.3330000042915344))
    v758 = g.math('ADD', v757, -0.20000000298023224)
    v759 = g.math('MULTIPLY_ADD', v756, v758, 0.20000000298023224)
    v760 = g.math('MULTIPLY', v759, 5)
    v761 = g.math('MAXIMUM', v760, 0)
    v762 = g.math('MULTIPLY', v755, v755)
    v763 = g.math('MULTIPLY', v761, v762)
    v764 = g.math('SUBTRACT', v763, 1)
    v765 = g.mixf(v713, v708, v764)
    v766 = g.mixf(v713, 0, v763)
    v767 = g.inp('_EmissiveColor', True, (1.0, 1.0, 1.0))
    v768 = g.inp('_EmissiveColor_w', False, 1.0)
    v769 = g.math('MULTIPLY_ADD', v768, v765, 1)
    v770 = g.inp('_EmissiveColorG', True, (0.0, 0.0, 0.0))
    v771 = g.inp('_EmissiveColorG_w', False, 0.0)
    v772 = g.math('MULTIPLY_ADD', v771, v765, 1)
    v773 = g.inp('_EmissiveColorB', True, (0.0, 0.0, 0.0))
    v774 = g.inp('_EmissiveColorB_w', False, 0.0)
    v775 = g.math('MULTIPLY_ADD', v774, v765, 1)
    v776 = g.inp('_EmissiveColorA', True, (0.0, 0.0, 0.0))
    v777 = g.inp('_EmissiveColorA_w', False, 0.0)
    v778 = g.math('MULTIPLY_ADD', v777, v765, 1)
    v779 = g.math('LESS_THAN', v711, 0.5)
    v780 = g.vmath('SUBTRACT', v14, v0)
    v781 = g.inp('_EmissiveSpeed', True, (0.0, 0.0, 0.0))
    v782 = g.inp('_EmissiveSpeed_w', False, 0.0)
    v783 = g.sep(v781)
    v784 = g.comb(v783[0], v783[1], 0.0)
    v785 = g.comb(v685[1], v685[1], 0.0)
    v786 = g.inp('_EmissiveUVSet', False, 0.0)
    v787 = g.comb(v786, v786, 0.0)
    v788 = g.vmath('MULTIPLY', v787, v780)
    v789 = g.vmath('ADD', v788, v0)
    v790 = g.inp('_EmissiveMap_ST', True, (1.0, 1.0, 0.0))
    v791 = g.inp('_EmissiveMap_ST_w', False, 0.0)
    v792 = g.sep(v790)
    v793 = g.comb(v792[0], v792[1], 0.0)
    v794 = g.comb(v792[2], v791, 0.0)
    v795 = g.vmath('MULTIPLY', v789, v793)
    v796 = g.vmath('ADD', v795, v794)
    v797 = g.vmath('MULTIPLY', v784, v785)
    v798 = g.vmath('ADD', v797, v796)
    v799 = g.math('MAXIMUM', v682, v710)
    v800 = g.inp('_EmissiveMapTilling', False, 0.0)
    v801 = g.comb(v800, v800, 0.0)
    v802 = g.vmath('MULTIPLY', v798, v801)
    v803 = g.mixv(v799, v798, v802)
    g.out_('F16_EmissiveMap_uv', v803, True)
    v804 = g.inp('F16_EmissiveMap', True, (1.0, 1.0, 1.0))
    v805 = g.inp('F16_EmissiveMap_alpha', False, 1.0)
    v806 = g.sep(v804)
    v807 = g.math('MULTIPLY', v806[0], v769)
    v808 = g.bc(v807)
    v809 = g.vmath('MULTIPLY', v767, v808)
    v810 = g.math('MULTIPLY', v806[1], v772)
    v811 = g.bc(v810)
    v812 = g.vmath('MULTIPLY', v770, v811)
    v813 = g.math('MULTIPLY', v806[2], v775)
    v814 = g.bc(v813)
    v815 = g.vmath('MULTIPLY', v773, v814)
    v816 = g.vmath('ADD', v812, v815)
    v817 = g.math('MULTIPLY', v805, v778)
    v818 = g.bc(v817)
    v819 = g.vmath('MULTIPLY', v776, v818)
    v820 = g.vmath('ADD', v816, v819)
    v821 = g.inp('_EmissiveType', False, 0.0)
    v822 = g.bc(v821)
    v823 = g.vmath('MULTIPLY', v820, v822)
    v824 = g.vmath('ADD', v809, v823)
    v825 = g.math('MAXIMUM', v682, v710)
    v826 = g.vmath('MAXIMUM', v824, (0, 0, 0))
    v827 = g.vmath('MINIMUM', v826, (1000, 1000, 1000))
    v828 = g.mixv(v825, v824, v827)
    v829 = g.vmath('MULTIPLY', v828, v680)
    v830 = g.bc(v674)
    v831 = g.vmath('MULTIPLY', v829, v830)
    v832 = g.vmath('ADD', v676, v831)
    v833 = g.math('SUBTRACT', 1.0, v712)
    v834 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v835 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v836 = g.sep(v834)
    v837 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v838 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v839 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v840 = g.inp('F10_MROMap_alpha', False, 1.0)
    v841 = g.math('SUBTRACT', v711, 1)
    v842 = g.clampn(v841, 0, 1)
    v843 = g.math('MULTIPLY', v223, -1.0)
    v844 = g.math('ADD', v843, v836[2])
    v845 = g.math('MULTIPLY_ADD', v842, v844, v223)
    v846 = g.math('SUBTRACT', v711, 2)
    v847 = g.clampn(v846, 0, 1)
    v848 = g.math('SUBTRACT', v838, v845)
    v849 = g.math('MULTIPLY_ADD', v847, v848, v845)
    v850 = g.math('SUBTRACT', v711, 3)
    v851 = g.clampn(v850, 0, 1)
    v852 = g.math('SUBTRACT', v840, v849)
    v853 = g.math('MULTIPLY_ADD', v851, v852, v849)
    v854 = g.clampn(v711, 0, 1)
    v855 = g.math('SUBTRACT', v853, 1)
    v856 = g.math('MULTIPLY_ADD', v854, v855, 1)
    v857 = g.math('MULTIPLY_ADD', v856, 1.1111111640930176, -0.055555559694767)
    v858 = g.clampn(v857, 0, 1)
    v859 = g.math('MULTIPLY', v858, v769)
    v860 = g.math('MULTIPLY', v859, v854)
    v861 = g.bc(v860)
    v862 = g.vmath('MULTIPLY', v767, v861)
    v863 = g.vmath('MULTIPLY', v862, v680)
    v864 = g.bc(v674)
    v865 = g.vmath('MULTIPLY', v863, v864)
    v866 = g.vmath('ADD', v676, v865)
    v867 = g.bc(v766)
    v868 = g.vmath('MULTIPLY', v767, v867)
    v869 = g.vmath('MULTIPLY', v868, v680)
    v870 = g.bc(v674)
    v871 = g.vmath('MULTIPLY', v869, v870)
    v872 = g.vmath('ADD', v676, v871)
    v873 = g.mixv(v833, v872, v866)
    v874 = g.mixv(v779, v873, v832)
    v875 = g.mixv(v678, v676, v874)
    v876 = g.bc(v130)
    v877 = g.vmath('MULTIPLY', v661, v876)
    v878 = g.vmath('ADD', v875, v877)
    v879 = g.inp('_EnableMatcap', False, 0.0)
    v880 = g.math('GREATER_THAN', v879, 0.5)
    v881 = g.vtrans(v665, 'WORLD', 'CAMERA', 'VECTOR')
    v882 = g.vmath('NORMALIZE', v881)
    v883 = g.sep(v882)
    v884 = g.comb(v883[0], v883[1], 0.0)
    v885 = g.vmath('MULTIPLY', v884, (0.5, 0.5, 0.0))
    v886 = g.vmath('ADD', v885, (0.5, 0.5, 0.0))
    g.out_('F17_MatcapMap_uv', v886, True)
    v887 = g.inp('F17_MatcapMap', True, (1.0, 1.0, 1.0))
    v888 = g.inp('F17_MatcapMap_alpha', False, 1.0)
    v889 = g.inp('_MatcapMapStrength', False, 0.2)
    v890 = g.bc(v889)
    v891 = g.vmath('MULTIPLY', v887, v890)
    v892 = g.vmath('ADD', v878, v891)
    v893 = g.mixv(v880, v878, v892)
    v894 = g.inp('_EnableParallaxMap', False, 0.0)
    v895 = g.math('GREATER_THAN', v894, 0.5)
    v896 = g.inp('_ParallaxMappingType', False, 0.0)
    v897 = g.math('LESS_THAN', v896, 0.5)
    v898 = g.math('MULTIPLY', v895, v897)
    v899 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v900 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v901 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v902 = g.inp('F10_MROMap_alpha', False, 1.0)
    v903 = g.sep(v899)
    v904 = g.inp('_UseParallaxMask', False, 0.0)
    v905 = g.math('COMPARE', v904, 0, 1e-05)
    v906 = g.math('SUBTRACT', 1.0, v905)
    v907 = g.math('MULTIPLY', v223, -1.0)
    v908 = g.math('ADD', v907, v900)
    v909 = g.math('MULTIPLY_ADD', v103, v908, v223)
    v910 = g.math('MULTIPLY', v909, -1.0)
    g.out_('F18_ParallaxMaskMap_uv', v14, True)
    v911 = g.inp('F18_ParallaxMaskMap', True, (1.0, 1.0, 1.0))
    v912 = g.inp('F18_ParallaxMaskMap_alpha', False, 1.0)
    v913 = g.sep(v911)
    v914 = g.math('MULTIPLY_ADD', v910, v38, v913[0])
    v915 = g.math('MULTIPLY', v909, v38)
    v916 = g.math('MULTIPLY_ADD', v904, v914, v915)
    v917 = g.inp('_ParallaxMaskChannel', False, 0.0)
    v918 = g.clampn(v917, 0, 1)
    v919 = g.math('MULTIPLY', v223, -1.0)
    v920 = g.math('ADD', v919, v903[2])
    v921 = g.math('MULTIPLY_ADD', v918, v920, v223)
    v922 = g.math('SUBTRACT', v917, 1)
    v923 = g.clampn(v922, 0, 1)
    v924 = g.math('MULTIPLY', v921, -1.0)
    v925 = g.math('ADD', v924, v900)
    v926 = g.math('MULTIPLY_ADD', v923, v925, v921)
    v927 = g.math('SUBTRACT', v917, 2)
    v928 = g.clampn(v927, 0, 1)
    v929 = g.math('MULTIPLY', v926, -1.0)
    v930 = g.math('ADD', v929, v902)
    v931 = g.math('MULTIPLY_ADD', v928, v930, v926)
    v932 = g.inp('_ParallaxMaskByLayerBlend', False, 0.0)
    v933 = g.math('MULTIPLY', v931, -1.0)
    v934 = g.math('MULTIPLY_ADD', v932, v933, v931)
    v935 = g.mixf(v906, v934, v916)
    v936 = g.math('LESS_THAN', 0.009999999776482582, v935)
    v937 = g.math('SUBTRACT', 1.0, v936)
    v938 = g.mixv(v937, (0.0, 0.0, 0.0), (0, 0, 0))
    v939 = g.mixf(v937, 0.0, 1.0)
    v940 = g.math('SUBTRACT', 1.0, v939)
    v941 = g.math('GREATER_THAN', v5, 0)
    v942 = g.mixf(v941, -1, 1)
    v943 = g.math('SUBTRACT', 1.0, v939)
    v944 = g.vmath('CROSS_PRODUCT', v21, v22)
    v945 = g.bc(v942)
    v946 = g.vmath('MULTIPLY', v945, v944)
    v947 = g.math('SUBTRACT', 1.0, v939)
    v948 = g.vmath('DOT_PRODUCT', v22, v28)
    v949 = g.math('SUBTRACT', 1.0, v939)
    v950 = g.vmath('DOT_PRODUCT', v946, v28)
    v951 = g.math('SUBTRACT', 1.0, v939)
    v952 = g.vmath('DOT_PRODUCT', v21, v28)
    v953 = g.math('SUBTRACT', 1.0, v939)
    v954 = g.comb(v948, v950, v952)
    v955 = g.comb(v948, v950, v952)
    v956 = g.vmath('DOT_PRODUCT', v954, v955)
    v957 = g.math('INVERSE_SQRT', v956, 0.0)
    v958 = g.math('SUBTRACT', 1.0, v939)
    v959 = g.inp('_ParallaxMapUVType', False, 0.0)
    v960 = g.comb(v959, v959, 0.0)
    v961 = g.vmath('SUBTRACT', v14, v0)
    v962 = g.vmath('MULTIPLY', v960, v961)
    v963 = g.vmath('ADD', v962, v0)
    v964 = g.math('SUBTRACT', 1.0, v939)
    v965 = g.inp('_GlobalMipBias', True, (0.0, 0.0, 0.0))
    v966 = g.sep(v965)
    v967 = g.comb(v966[1], v966[1], 0.0)
    v968 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), v967)
    v969 = g.math('SUBTRACT', 1.0, v939)
    v970 = g.comb(v966[1], v966[1], 0.0)
    v971 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), v970)
    v972 = g.math('SUBTRACT', 1.0, v939)
    v973 = g.inp('_ParallaxMarchNum', False, 3.0)
    v974 = g.math('MINIMUM', v973, 20)
    v975 = g.math('SUBTRACT', 1.0, v939)
    v976 = g.math('DIVIDE', 1, v974)
    v977 = g.math('SUBTRACT', 1.0, v939)
    v978 = g.math('MULTIPLY_ADD', v952, v957, 0.41999998688697815)
    v979 = g.math('SUBTRACT', 1.0, v939)
    v980 = g.math('MULTIPLY', v957, v952)
    v981 = g.math('MAXIMUM', v980, 0.0010000000474974513)
    v982 = g.math('SUBTRACT', 1.0, v939)
    v983 = g.math('MULTIPLY', v957, v948)
    v984 = g.math('DIVIDE', v983, v978)
    v985 = g.math('DIVIDE', v984, v981)
    v986 = g.inp('_ParallaxStrength', False, 0.0)
    v987 = g.math('MULTIPLY', v986, -1.0)
    v988 = g.math('MULTIPLY', v985, v987)
    v989 = g.math('MULTIPLY', v957, v950)
    v990 = g.math('DIVIDE', v989, v978)
    v991 = g.math('DIVIDE', v990, v981)
    v992 = g.math('MULTIPLY', v986, -1.0)
    v993 = g.math('MULTIPLY', v991, v992)
    v994 = g.comb(v988, v993, 0.0)
    v995 = g.math('SUBTRACT', 1.0, v939)
    v996 = g.comb(v976, v976, 0.0)
    v997 = g.vmath('MULTIPLY', v996, v994)
    v998 = g.math('SUBTRACT', 1.0, v939)
    v999 = g.math('SUBTRACT', 1, v976)
    v1000 = g.math('SUBTRACT', 1.0, v939)
    v1001 = g.math('SUBTRACT', 1.0, v939)
    v1002 = g.math('SUBTRACT', 1.0, v939)
    v1003 = g.math('SUBTRACT', 1.0, v939)
    v1004 = g.math('SUBTRACT', 1.0, v939)
    v1005 = g.math('SUBTRACT', 1.0, v939)
    v1006 = g.math('SUBTRACT', 1.0, v939)
    g.out_('Z0_it', 24, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_height', v999, False)
    g.out_('Z0_s_heightPrev', 1, False)
    g.out_('Z0_s_iter', 0, False)
    g.out_('Z0_s_offCur', v997, True)
    g.out_('Z0_s_offPrev', (0, 0, 0.0), True)
    g.out_('Z0_s_texHit', 0, False)
    g.out_('Z0_s_texPrev', 0, False)
    g.out_('Z0_s_uvP', v963, True)
    g.out_('Z0_r___done', v939, False)
    g.out_('Z0_r_steps', v974, False)
    g.out_('Z0_r_stepH', v976, False)
    g.out_('Z0_r_stepUV', v997, True)
    v1007 = g.inp('Z0_o_Lloop0', False)
    v1008 = g.inp('Z0_o_height', False)
    v1009 = g.inp('Z0_o_heightPrev', False)
    v1010 = g.inp('Z0_o_iter', False)
    v1011 = g.inp('Z0_o_offCur', True)
    v1012 = g.inp('Z0_o_offPrev', True)
    v1013 = g.inp('Z0_o_texHit', False)
    v1014 = g.inp('Z0_o_texPrev', False)
    v1015 = g.inp('Z0_o_uvP', True)
    v1016 = g.mixv(v1006, v963, v1015)
    v1017 = g.mixf(v1006, v999, v1008)
    v1018 = g.mixv(v1006, v997, v1011)
    v1019 = g.mixv(v1006, (0, 0, 0.0), v1012)
    v1020 = g.mixf(v1006, 0, v1014)
    v1021 = g.mixf(v1006, 1, v1009)
    v1022 = g.mixf(v1006, 0, v1013)
    v1023 = g.mixf(v1006, 0, v1010)
    v1024 = g.math('SUBTRACT', 1.0, v939)
    v1025 = g.math('SUBTRACT', v1020, v1021)
    v1026 = g.math('MULTIPLY', v1021, -1.0)
    v1027 = g.math('SUBTRACT', v1020, v1022)
    v1028 = g.math('ADD', v1017, v1027)
    v1029 = g.math('ADD', v1026, v1028)
    v1030 = g.math('DIVIDE', v1025, v1029)
    v1031 = g.math('SUBTRACT', 1.0, v939)
    v1032 = g.comb(v1030, v1030, 0.0)
    v1033 = g.vmath('MULTIPLY', v997, v1032)
    v1034 = g.vmath('ADD', v1033, v1019)
    v1035 = g.vmath('ADD', v1016, v1034)
    v1036 = g.inp('_ParallaxTilling', False, 1.0)
    v1037 = g.comb(v1036, v1036, 0.0)
    v1038 = g.vmath('MULTIPLY', v1035, v1037)
    v1039 = g.math('SUBTRACT', 1.0, v939)
    g.out_('F19_ParallaxMap_uv', v1038, True)
    v1040 = g.inp('F19_ParallaxMap', True, (1.0, 1.0, 1.0))
    v1041 = g.inp('F19_ParallaxMap_alpha', False, 1.0)
    v1042 = g.math('SUBTRACT', 1.0, v939)
    v1043 = g.sep(v1040)
    v1044 = g.inp('_ParallaxColor', True, (0.0, 0.0, 0.0))
    v1045 = g.inp('_ParallaxColor_w', False, 1.0)
    v1046 = g.sep(v1044)
    v1047 = g.inp('_ParallaxColorDark', True, (0.0, 0.0, 0.0))
    v1048 = g.inp('_ParallaxColorDark_w', False, 1.0)
    v1049 = g.sep(v1047)
    v1050 = g.math('SUBTRACT', v1046[0], v1049[0])
    v1051 = g.math('MULTIPLY_ADD', v1043[1], v1050, v1049[0])
    v1052 = g.math('SUBTRACT', v1046[1], v1049[1])
    v1053 = g.math('MULTIPLY_ADD', v1043[1], v1052, v1049[1])
    v1054 = g.math('SUBTRACT', v1046[2], v1049[2])
    v1055 = g.math('MULTIPLY_ADD', v1043[1], v1054, v1049[2])
    v1056 = g.comb(v1051, v1053, v1055)
    v1057 = g.math('SUBTRACT', 1.0, v939)
    v1058 = g.vmath('DOT_PRODUCT', v28, v665)
    v1059 = g.clampn(v1058, 0, 1)
    v1060 = g.math('MAXIMUM', v1059, 0.0010000000474974513)
    v1061 = g.math('LOGARITHM', v1060, 2.0)
    v1062 = g.inp('_ParallaxFresnelStrength', False, 0.0)
    v1063 = g.math('FLOOR', v1062, 0.0)
    v1064 = g.math('MULTIPLY', v1061, v1063)
    v1065 = g.math('POWER', 2.0, v1064)
    v1066 = g.math('MULTIPLY', v935, v935)
    v1067 = g.math('MULTIPLY', v1065, v1066)
    v1068 = g.math('SUBTRACT', 1.0, v939)
    v1069 = g.inp('_VFXParams0', True, (0.0, 0.0, 0.0))
    v1070 = g.inp('_VFXParams0_w', False, 0.0)
    v1071 = g.vmath('SUBTRACT', v20, v1069)
    v1072 = g.math('SUBTRACT', 1.0, v939)
    v1073 = g.vmath('DOT_PRODUCT', v1071, v1071)
    v1074 = g.math('SQRT', v1073, 0.0)
    v1075 = g.inp('_ParallaxBrightOuterRadius', False, 0.0)
    v1076 = g.math('SUBTRACT', v1074, v1075)
    v1077 = g.math('MULTIPLY', v1075, -1.0)
    v1078 = g.inp('_ParallaxBrightInnerRadius', False, 0.0)
    v1079 = g.math('ADD', v1077, v1078)
    v1080 = g.math('DIVIDE', 1, v1079)
    v1081 = g.math('MULTIPLY', v1076, v1080)
    v1082 = g.clampn(v1081, 0, 1)
    v1083 = g.math('SUBTRACT', 1.0, v939)
    v1084 = g.math('MULTIPLY', v1082, v1082)
    v1085 = g.math('MULTIPLY_ADD', v1082, -2, 3)
    v1086 = g.math('MULTIPLY', v1084, v1085)
    v1087 = g.inp('_ParallaxBrightStrength', False, 0.0)
    v1088 = g.math('MULTIPLY', v1086, v1087)
    v1089 = g.math('SUBTRACT', 1.0, v939)
    v1090 = g.inp('_ParallaxCharPos', False, 0.0)
    v1091 = g.math('COMPARE', v1090, 0, 1e-05)
    v1092 = g.math('SUBTRACT', 1.0, v1091)
    v1093 = g.mixf(v1092, 0, v1088)
    v1094 = g.mixf(v1089, v1088, v1093)
    v1095 = g.math('SUBTRACT', 1.0, v939)
    v1096 = g.inp('_VFXParams2', True, (0.0, 0.0, 0.0))
    v1097 = g.inp('_VFXParams2_w', False, 0.0)
    v1098 = g.sep(v1096)
    v1099 = g.math('SUBTRACT', v440[0], v1098[0])
    v1100 = g.math('SUBTRACT', v440[2], v1098[1])
    v1101 = g.comb(v1099, v1100, 0.0)
    v1102 = g.math('SUBTRACT', 1.0, v939)
    v1103 = g.math('MULTIPLY', v1098[2], -1.0)
    v1104 = g.math('DIVIDE', 1, v1103)
    v1105 = g.vmath('DOT_PRODUCT', v1101, v1101)
    v1106 = g.math('SQRT', v1105, 0.0)
    v1107 = g.math('SUBTRACT', v1106, v1098[2])
    v1108 = g.math('MULTIPLY', v1104, v1107)
    v1109 = g.clampn(v1108, 0, 1)
    v1110 = g.math('SUBTRACT', 1.0, v939)
    v1111 = g.inp('_ParallaxMinBrightness', False, 0.0)
    v1112 = g.math('SUBTRACT', 1, v1111)
    v1113 = g.math('SUBTRACT', 1.0, v939)
    v1114 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1115 = g.sep(v1114)
    v1116 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1117 = g.sep(v1116)
    v1118 = g.math('ADD', v1115[1], v1117[0])
    v1119 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1120 = g.sep(v1119)
    v1121 = g.math('ADD', v1118, v1120[2])
    v1122 = g.math('SUBTRACT', 1.0, v939)
    v1123 = g.math('MULTIPLY', v1109, v1109)
    v1124 = g.math('MULTIPLY_ADD', v1109, -2, 3)
    v1125 = g.math('MULTIPLY', v1123, v1124)
    v1126 = g.math('ADD', 1, v1111)
    v1127 = g.math('DIVIDE', v1126, v1112)
    v1128 = g.inp('_ParallaxAnimSpeed', False, 0.0)
    v1129 = g.math('MULTIPLY', v685[1], v1128)
    v1130 = g.inp('_ParallaxAnimRandom', False, 0.0)
    v1131 = g.math('MULTIPLY', v1121, v1130)
    v1132 = g.math('MULTIPLY_ADD', v1129, 0.05000000074505806, v1131)
    v1133 = g.math('COSINE', v1132, 0.0)
    v1134 = g.math('ADD', v1127, v1133)
    v1135 = g.math('MULTIPLY', v1112, v1134)
    v1136 = g.math('MULTIPLY_ADD', v1135, 0.5, v1094)
    v1137 = g.math('MULTIPLY_ADD', v1125, v1097, v1136)
    v1138 = g.math('SUBTRACT', 1.0, v939)
    v1139 = g.bc(v1067)
    v1140 = g.vmath('MULTIPLY', v1139, v1056)
    v1141 = g.bc(v1137)
    v1142 = g.vmath('MULTIPLY', v1141, v1140)
    v1143 = g.vmath('MAXIMUM', v1142, (0, 0, 0))
    v1144 = g.vmath('MINIMUM', v1143, (1000, 1000, 1000))
    v1145 = g.math('SUBTRACT', 1.0, v939)
    v1146 = g.inp('_UseWorldSpaceParallaxMask', False, 0.0)
    v1147 = g.math('GREATER_THAN', v1146, 0.5)
    v1148 = g.math('SUBTRACT', 1.0, v939)
    v1149 = g.inp('_MaskWorldPosParams', True, (0.0, 0.0, 0.0))
    v1150 = g.inp('_MaskWorldPosParams_w', False, 0.0)
    v1151 = g.sep(v1149)
    v1152 = g.math('SUBTRACT', v440[0], v1151[0])
    v1153 = g.math('SUBTRACT', v440[2], v1151[2])
    v1154 = g.comb(v1152, v1153, 0.0)
    v1155 = g.math('SUBTRACT', 1.0, v939)
    v1156 = g.math('MULTIPLY', 0.01745329238474369, v1151[1])
    v1157 = g.math('SUBTRACT', 1.0, v939)
    v1158 = g.math('SINE', v1156, 0.0)
    v1159 = g.math('SUBTRACT', 1.0, v939)
    v1160 = g.math('COSINE', v1156, 0.0)
    v1161 = g.math('SUBTRACT', 1.0, v939)
    v1162 = g.math('MAXIMUM', 0.10000000149011612, v1150)
    v1163 = g.math('SUBTRACT', 1.0, v939)
    v1164 = g.comb(v1162, v1162, 0.0)
    v1165 = g.vmath('DIVIDE', v1154, v1164)
    v1166 = g.math('SUBTRACT', 1.0, v939)
    v1167 = g.comb(v1160, v1158, 0.0)
    v1168 = g.vmath('DOT_PRODUCT', v1165, v1167)
    v1169 = g.math('ADD', v1168, 0.5)
    v1170 = g.math('MULTIPLY_ADD', v1043[0], v1111, v1169)
    v1171 = g.math('MULTIPLY', v1158, -1.0)
    v1172 = g.comb(v1171, v1160, 0.0)
    v1173 = g.vmath('DOT_PRODUCT', v1165, v1172)
    v1174 = g.math('ADD', v1173, 0.5)
    v1175 = g.math('MULTIPLY_ADD', v1043[1], v1111, v1174)
    v1176 = g.comb(v1170, v1175, 0.0)
    g.out_('F20_ParallaxMaskMap_uv', v1176, True)
    v1177 = g.inp('F20_ParallaxMaskMap', True, (1.0, 1.0, 1.0))
    v1178 = g.inp('F20_ParallaxMaskMap_alpha', False, 1.0)
    v1179 = g.math('SUBTRACT', 1.0, v939)
    v1180 = g.inp('_ParallaxMaskMapColorStrength', False, 0.0)
    v1181 = g.bc(v1180)
    v1182 = g.vmath('MULTIPLY', v1177, v1181)
    v1183 = g.vmath('MULTIPLY', v1144, v1182)
    v1184 = g.mixv(v1179, v1144, v1183)
    v1185 = g.math('SUBTRACT', 1.0, v939)
    v1186 = g.inp('_ParallaxSignControl', False, 0.0)
    v1187 = g.math('TRUNC', v1186, 0.0)
    v1188 = g.math('TRUNC', v1187, 0.0)
    v1189 = g.math('SUBTRACT', 1.0, v939)
    v1190 = g.clampn(v1178, 0, 1)
    v1191 = g.math('SUBTRACT', 1.0, v939)
    v1192 = g.math('SUBTRACT', v1178, 0.20000000298023224)
    v1193 = g.clampn(v1192, 0, 1)
    v1194 = g.math('SUBTRACT', 1.0, v939)
    v1195 = g.math('SUBTRACT', v1178, 0.4000000059604645)
    v1196 = g.clampn(v1195, 0, 1)
    v1197 = g.math('SUBTRACT', 1.0, v939)
    v1198 = g.math('SUBTRACT', v1178, 0.6000000238418579)
    v1199 = g.clampn(v1198, 0, 1)
    v1200 = g.math('SUBTRACT', 1.0, v939)
    v1201 = g.math('SUBTRACT', v1178, 0.800000011920929)
    v1202 = g.clampn(v1201, 0, 1)
    v1203 = g.math('SUBTRACT', 1.0, v939)
    v1204 = g.math('LESS_THAN', 0.18000000715255737, v1190)
    v1205 = g.math('SUBTRACT', 1.0, v1204)
    v1206 = g.math('MULTIPLY', v1190, 5)
    v1207 = g.mixf(v1205, 0, v1206)
    v1208 = g.math('MODULO', v1188, 2)
    v1209 = g.math('TRUNC', v1208, 0.0)
    v1210 = g.math('MULTIPLY', v1207, v1209)
    v1211 = g.inp('_ParallaxSignLerpFactor0', True, (0.0, 0.0, 0.0))
    v1212 = g.inp('_ParallaxSignLerpFactor0_w', False, 0.0)
    v1213 = g.sep(v1211)
    v1214 = g.math('MULTIPLY', v1210, v1213[0])
    v1215 = g.math('LESS_THAN', 0.18000000715255737, v1193)
    v1216 = g.math('SUBTRACT', 1.0, v1215)
    v1217 = g.math('MULTIPLY', v1193, 5)
    v1218 = g.mixf(v1216, 0, v1217)
    v1219 = g.math('DIVIDE', v1188, 2)
    v1220 = g.math('FLOOR', v1219, 0.0)
    v1221 = g.math('MODULO', v1220, 2)
    v1222 = g.math('TRUNC', v1221, 0.0)
    v1223 = g.math('MULTIPLY', v1218, v1222)
    v1224 = g.math('MULTIPLY', v1223, v1213[1])
    v1225 = g.math('ADD', v1214, v1224)
    v1226 = g.math('LESS_THAN', 0.18000000715255737, v1196)
    v1227 = g.math('SUBTRACT', 1.0, v1226)
    v1228 = g.math('MULTIPLY', v1196, 5)
    v1229 = g.mixf(v1227, 0, v1228)
    v1230 = g.math('DIVIDE', v1188, 4)
    v1231 = g.math('FLOOR', v1230, 0.0)
    v1232 = g.math('MODULO', v1231, 2)
    v1233 = g.math('TRUNC', v1232, 0.0)
    v1234 = g.math('MULTIPLY', v1229, v1233)
    v1235 = g.math('MULTIPLY', v1234, v1213[2])
    v1236 = g.math('ADD', v1225, v1235)
    v1237 = g.math('LESS_THAN', 0.18000000715255737, v1199)
    v1238 = g.math('SUBTRACT', 1.0, v1237)
    v1239 = g.math('MULTIPLY', v1199, 5)
    v1240 = g.mixf(v1238, 0, v1239)
    v1241 = g.math('DIVIDE', v1188, 8)
    v1242 = g.math('FLOOR', v1241, 0.0)
    v1243 = g.math('MODULO', v1242, 2)
    v1244 = g.math('TRUNC', v1243, 0.0)
    v1245 = g.math('MULTIPLY', v1240, v1244)
    v1246 = g.math('MULTIPLY', v1245, v1212)
    v1247 = g.math('ADD', v1236, v1246)
    v1248 = g.math('LESS_THAN', 0.18000000715255737, v1202)
    v1249 = g.math('SUBTRACT', 1.0, v1248)
    v1250 = g.math('MULTIPLY', v1202, 5)
    v1251 = g.mixf(v1249, 0, v1250)
    v1252 = g.math('DIVIDE', v1188, 16)
    v1253 = g.math('FLOOR', v1252, 0.0)
    v1254 = g.math('MODULO', v1253, 2)
    v1255 = g.math('TRUNC', v1254, 0.0)
    v1256 = g.math('MULTIPLY', v1251, v1255)
    v1257 = g.inp('_ParallaxSignLerpFactor2', False, 0.0)
    v1258 = g.math('MULTIPLY', v1256, v1257)
    v1259 = g.math('ADD', v1247, v1258)
    v1260 = g.math('SUBTRACT', 1.0, v939)
    v1261 = g.vmath('DOT_PRODUCT', v1154, v1154)
    v1262 = g.math('SQRT', v1261, 0.0)
    v1263 = g.math('MULTIPLY_ADD', v1043[0], 20, v1262)
    v1264 = g.inp('_ParallaxLerpSchedule', False, 0.0)
    v1265 = g.math('SUBTRACT', v1263, v1264)
    v1266 = g.clampn(v1265, 0, 1)
    v1267 = g.math('SUBTRACT', 1.0, v939)
    v1268 = g.math('MULTIPLY_ADD', v1259, v477[0], v1266)
    v1269 = g.clampn(v1268, 0, 1)
    v1270 = g.math('SUBTRACT', 1.0, v939)
    v1271 = g.inp('_ParallaxPatternColorDark', True, (0.0, 0.0, 0.0))
    v1272 = g.inp('_ParallaxPatternColorDark_w', False, 0.0)
    v1273 = g.vmath('MULTIPLY', v1184, v1271)
    v1274 = g.math('SUBTRACT', 1.0, v939)
    v1275 = g.inp('_ParallaxPatternColor', True, (0.0, 0.0, 0.0))
    v1276 = g.inp('_ParallaxPatternColor_w', False, 0.0)
    v1277 = g.vmath('MULTIPLY', v1184, v1275)
    v1278 = g.math('SUBTRACT', 1.0, v939)
    v1279 = g.mixv(v1269, v1273, v1277)
    v1280 = g.mixv(v1278, v1184, v1279)
    v1281 = g.math('SUBTRACT', 1.0, v939)
    v1282 = g.inp('_ParallaxSignLerpFactor1', True, (0.0, 0.0, 0.0))
    v1283 = g.inp('_ParallaxSignLerpFactor1_w', False, 0.0)
    v1284 = g.clampn(v1283, 0, 1)
    v1285 = g.math('MULTIPLY', v223, -1.0)
    v1286 = g.math('MULTIPLY', v440[1], -1.0)
    v1287 = g.math('ADD', v1286, v1283)
    v1288 = g.clampn(v1287, 0, 1)
    v1289 = g.math('ADD', v1285, v1288)
    v1290 = g.math('MULTIPLY_ADD', v1284, v1289, v223)
    v1291 = g.math('SUBTRACT', 1.0, v939)
    v1292 = g.inp('_WorldParallaxAdditionalLightMaskChannel', False, 0.0)
    v1293 = g.clampn(v1292, 0, 1)
    v1294 = g.math('MULTIPLY', v223, -1.0)
    v1295 = g.math('ADD', v1294, v903[2])
    v1296 = g.math('MULTIPLY_ADD', v1293, v1295, v223)
    v1297 = g.math('SUBTRACT', 1.0, v939)
    v1298 = g.math('SUBTRACT', v1292, 1)
    v1299 = g.clampn(v1298, 0, 1)
    v1300 = g.math('MULTIPLY', v1296, -1.0)
    v1301 = g.math('ADD', v1300, v900)
    v1302 = g.math('MULTIPLY_ADD', v1299, v1301, v1296)
    v1303 = g.mixf(v1297, v1296, v1302)
    v1304 = g.math('SUBTRACT', 1.0, v939)
    v1305 = g.math('SUBTRACT', v1292, 2)
    v1306 = g.clampn(v1305, 0, 1)
    v1307 = g.math('MULTIPLY', v1303, -1.0)
    v1308 = g.math('ADD', v1307, v902)
    v1309 = g.math('MULTIPLY_ADD', v1306, v1308, v1303)
    v1310 = g.mixf(v1304, v1303, v1309)
    v1311 = g.math('SUBTRACT', 1.0, v939)
    v1312 = g.sep(v1282)
    v1313 = g.math('SUBTRACT', v440[1], v1312[1])
    v1314 = g.clampn(v1313, 0, 1)
    v1315 = g.math('SUBTRACT', 1.0, v939)
    v1316 = g.vmath('ADD', v1280, (0.30000001192092896, 0.30000001192092896, 0.30000001192092896))
    v1317 = g.inp('_WorldParallaxAdditionalColor', True, (0.0, 0.0, 0.0))
    v1318 = g.inp('_WorldParallaxAdditionalColor_w', False, 0.0)
    v1319 = g.vmath('MULTIPLY', v1316, v1317)
    v1320 = g.bc(v1314)
    v1321 = g.vmath('MULTIPLY', v1320, v1319)
    v1322 = g.bc(v1310)
    v1323 = g.vmath('MULTIPLY', v1322, v1321)
    v1324 = g.math('SUBTRACT', 1.0, v939)
    v1325 = g.inp('_ParallaxIntensity', False, 0.0)
    v1326 = g.math('MULTIPLY', v1290, v1325)
    v1327 = g.math('MULTIPLY', v1290, v1325)
    v1328 = g.math('MULTIPLY', v1290, v1325)
    v1329 = g.comb(v1326, v1327, v1328)
    v1330 = g.vmath('MULTIPLY', v1280, v1329)
    v1331 = g.vmath('ADD', v1330, v1323)
    v1332 = g.mixv(v1324, v938, v1331)
    v1333 = g.mixf(v1324, v939, 1.0)
    v1334 = g.mixv(v1147, v938, v1332)
    v1335 = g.mixf(v1147, v939, v1333)
    v1336 = g.mixv(v1147, v1144, v1280)
    v1337 = g.mixv(v1145, v938, v1334)
    v1338 = g.mixf(v1145, v939, v1335)
    v1339 = g.mixv(v1145, v1144, v1336)
    v1340 = g.math('SUBTRACT', 1.0, v1338)
    v1341 = g.bc(v1325)
    v1342 = g.vmath('MULTIPLY', v1339, v1341)
    v1343 = g.mixv(v1340, v1337, v1342)
    v1344 = g.mixf(v1340, v1338, 1.0)
    v1345 = g.vmath('ADD', v893, v1343)
    v1346 = g.mixv(v898, v893, v1345)
    v1347 = g.comb(v228, v228, v228)
    v1348 = g.math('SUBTRACT', 1, v662)
    v1349 = g.vmath('NORMALIZE', v665)
    v1350 = g.vmath('DOT_PRODUCT', v1349, v28)
    v1351 = g.math('MAXIMUM', v1350, 0)
    v1352 = g.math('MULTIPLY', 0.08, v228)
    v1353 = g.math('MULTIPLY', 0.08, v228)
    v1354 = g.math('MULTIPLY', 0.08, v228)
    v1355 = g.comb(v1352, v1353, v1354)
    v1356 = g.mixv(v663, v1355, v661)
    v1357 = g.math('SUBTRACT', 1, v663)
    v1358 = g.bc(v1357)
    v1359 = g.vmath('MULTIPLY', v661, v1358)
    v1360 = g.inp('_UseThinFilm', False, 0.0)
    v1361 = g.math('GREATER_THAN', v1360, 0.5)
    v1362 = g.inp('_ThinFilmIOR', False, 1.4)
    v1363 = g.inp('_ThinFilmThickness', False, 0.5)
    v1364 = g.math('MULTIPLY', v1363, 1000)
    v1365 = g.inp('M_PI', False, 0.0)
    v1366 = g.group_named('RCE_RuriEvalIridescence', [('outsideIor', 1), ('eta2', v1362), ('cosTheta1', v1351), ('iridescenceThickness', v1364), ('baseF0', v1356), ('M_PI', v1365)])
    v1367 = g.inp('_ThinFilmWeight', False, 0.0)
    v1368 = g.inp('_ThinFilmIntensity', False, 1.0)
    v1369 = g.math('MULTIPLY', v1367, v1368)
    v1370 = g.clampn(v1369)
    v1371 = g.mixv(v1370, v1356, v1366[0])
    v1372 = g.mixv(v1361, v1356, v1371)
    v1373 = g.inp('_SubsurfaceShadingMode', False, 0.0)
    v1374 = g.math('LESS_THAN', v1373, 0.5)
    v1375 = g.inp('_SubsurfaceColor', True, (0.8, 0.8, 0.8))
    v1376 = g.inp('_SubsurfaceColor_w', False, 1.0)
    v1377 = g.vmath('MULTIPLY', v1375, v661)
    v1378 = g.mixv(v1374, v1377, v1375)
    v1379 = g.inp('_MaxSubsurfaceThickness', False, 1.0)
    v1380 = g.inp('_UseSubsurfaceThicknessMap', False, 0.0)
    v1381 = g.math('GREATER_THAN', v1380, 0.5)
    v1382 = g.inp('_MinSubsurfaceThickness', False, 0.0)
    g.out_('F21_SubsurfaceMap_uv', v0, True)
    v1383 = g.inp('F21_SubsurfaceMap', True, (1.0, 1.0, 1.0))
    v1384 = g.inp('F21_SubsurfaceMap_alpha', False, 1.0)
    v1385 = g.sep(v1383)
    v1386 = g.mixf(v1385[0], v1382, v1379)
    v1387 = g.mixf(v1381, v1379, v1386)
    v1388 = g.group_named('RCE_HgEnvBRDF', [('roughness', v662), ('NoV', v1351), ('f0', v1372)])
    v1389 = g.vmath('SCALE', v28, s=-1.0)
    v1390 = g.vmath('DOT_PRODUCT', v1349, v1389)
    v1391 = g.math('MULTIPLY', 2.0, v1390)
    v1392 = g.vmath('SCALE', v1349, s=v1391)
    v1393 = g.vmath('SUBTRACT', v1389, v1392)
    v1394 = g.inp('_UseCustomIBL', False, 0.0)
    v1395 = g.math('GREATER_THAN', v1394, 0.5)
    v1396 = g.math('MULTIPLY', 0.7, v662)
    v1397 = g.math('SUBTRACT', 1.7, v1396)
    v1398 = g.math('MULTIPLY', v662, v1397)
    v1399 = g.math('MULTIPLY', v1398, 6)
    v1400 = g.u2b(v1393)
    g.out_('F22_IBL_CustomIBL_dir', v1400, True)
    g.out_('F22_IBL_CustomIBL_mip', v1399, False)
    v1401 = g.inp('F22_IBL_CustomIBL', True, (0.2159, 0.2159, 0.2159))
    v1402 = g.inp('F22_IBL_CustomIBL_alpha', False, 1.0)
    v1403 = g.inp('_CustomIBLIntensity', False, 1.0)
    v1404 = g.bc(v1403)
    v1405 = g.vmath('MULTIPLY', v1401, v1404)
    g.out_('C1_SpecularRadiance_direction', v1393, True)
    g.out_('C1_SpecularRadiance_position', v20, True)
    g.out_('C1_SpecularRadiance_roughness', v662, False)
    v1406 = g.inp('C1_SpecularRadiance', True, (0.2159, 0.2159, 0.2159))
    v1407 = g.mixv(v1395, v1406, v1405)
    v1408 = g.inp('_PlanarReflection', False, 0.0)
    v1409 = g.math('GREATER_THAN', v1408, 0.5)
    g.out_('F23_PlanarReflectionTexture_uv', v29, True)
    v1410 = g.inp('F23_PlanarReflectionTexture', True, (1.0, 1.0, 1.0))
    v1411 = g.inp('F23_PlanarReflectionTexture_alpha', False, 1.0)
    v1412 = g.inp('_PlanarReflectionTint', True, (1.0, 1.0, 1.0))
    v1413 = g.inp('_PlanarReflectionTint_w', False, 1.0)
    v1414 = g.vmath('MULTIPLY', v1410, v1412)
    v1415 = g.mixv(v1413, v1407, v1414)
    v1416 = g.mixv(v1409, v1407, v1415)
    v1417 = g.inp('_EnableSubsurface', False, 0.0)
    v1418 = g.math('GREATER_THAN', v1417, 0.5)
    v1419 = g.inp('_SubsurfaceIndirect', False, 1.0)
    v1420 = g.comb(v1419, v1419, v1419)
    v1421 = g.vmath('MULTIPLY', v1378, v1420)
    v1422 = g.vmath('ADD', v1421, v1359)
    v1423 = g.mixv(v1418, v1359, v1422)
    v1424 = g.vmath('MULTIPLY', v1423, v30)
    v1425 = g.bc(v664)
    v1426 = g.vmath('MULTIPLY', v1424, v1425)
    v1427 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v1428 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v1429 = g.sep(v1427)
    v1430 = g.bc(v1429[0])
    v1431 = g.vmath('MULTIPLY', v1426, v1430)
    v1432 = g.comb(v1388[0], v1388[0], v1388[0])
    v1433 = g.comb(v1388[1], v1388[1], v1388[1])
    v1434 = g.vmath('MULTIPLY', v1372, v1432)
    v1435 = g.vmath('ADD', v1434, v1433)
    v1436 = g.vmath('MULTIPLY', v1435, v1416)
    v1437 = g.bc(v1429[1])
    v1438 = g.vmath('MULTIPLY', v1436, v1437)
    v1439 = g.vmath('ADD', v1431, v1438)
    v1440 = g.inp('C2_MainLight_direction', True, (0.0, 0.0, 0.0))
    v1441 = g.inp('C2_MainLight_color', True, (0.0, 0.0, 0.0))
    v1442 = g.inp('C2_MainLight_distanceAttenuation', False, 0.0)
    v1443 = g.inp('C2_MainLight_shadowAttenuation', False, 0.0)
    v1444 = g.inp('C2_MainLight_layerMask', False, 0.0)
    v1445 = g.inp('_MainLightOcclusionProbes', True, (0.0, 0.0, 0.0))
    v1446 = g.inp('_MainLightOcclusionProbes_w', False, 0.0)
    v1447 = g.vmath('ADD', v1440, v28)
    v1448 = g.vmath('DOT_PRODUCT', v1447, v1447)
    v1449 = g.math('MAXIMUM', v1448, 1E-08)
    v1450 = g.math('INVERSE_SQRT', v1449, 0.0)
    v1451 = g.bc(v1450)
    v1452 = g.vmath('MULTIPLY', v1447, v1451)
    v1453 = g.vmath('DOT_PRODUCT', v1440, v1349)
    v1454 = g.clampn(v1453)
    v1455 = g.vmath('DOT_PRODUCT', v1349, v1452)
    v1456 = g.clampn(v1455)
    v1457 = g.vmath('DOT_PRODUCT', v28, v1452)
    v1458 = g.clampn(v1457)
    v1459 = g.group_named('RCE_HgDirectLightEnergy', [('roughness', v662), ('f0', v1372), ('NoL', v1454), ('NoH', v1456), ('NoV', v1351), ('VoH', v1458)])
    v1460 = g.math('MULTIPLY', v1442, 1.0)
    v1461 = g.comb(v1454, v1454, v1454)
    v1462 = g.bc(v1454)
    v1463 = g.vmath('MULTIPLY', v1359, v1462)
    v1464 = g.vmath('MULTIPLY', v1459[0], v1461)
    v1465 = g.vmath('ADD', v1464, v1463)
    v1466 = g.math('GREATER_THAN', v1417, 0.5)
    v1467 = g.vmath('DOT_PRODUCT', v1440, v1349)
    v1468 = g.vmath('DOT_PRODUCT', v28, v1440)
    v1469 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v1470 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v1471 = g.group_named('RCE_HgSssLobe', [('amount', v1387), ('rawNoL', v1467), ('VdotL', v1468), ('selfShadowBias', v1469), ('enableSelfShadowBias', v1470)])
    v1472 = g.bc(v1471[0])
    v1473 = g.vmath('MULTIPLY', v1472, v1378)
    v1474 = g.vmath('ADD', v1465, v1473)
    v1475 = g.mixv(v1466, v1465, v1474)
    v1476 = g.bc(v1460)
    v1477 = g.vmath('MULTIPLY', v1441, v1476)
    v1478 = g.vmath('MULTIPLY', v1475, v1477)
    v1479 = g.vmath('ADD', v1439, v1478)
    v1480 = g.inp('C3_AdditionalLightCount', False, 0.0)
    v1481 = g.math('SUBTRACT', v1480, 0)
    v1482 = g.math('CEIL', v1481, 0.0)
    v1483 = g.math('MAXIMUM', v1482, 0.0)
    g.out_('Z1_it', v1483, False)
    g.out_('Z1_s_H', v1452, True)
    g.out_('Z1_s_L', v1440, True)
    g.out_('Z1_s_LV', v1447, True)
    g.out_('Z1_s_N', v1349, True)
    g.out_('Z1_s_NoH', v1456, False)
    g.out_('Z1_s_NoL', v1454, False)
    g.out_('Z1_s_NoV', v1351, False)
    g.out_('Z1_s_P', v20, True)
    g.out_('Z1_s_V', v28, True)
    g.out_('Z1_s_VoH', v1458, False)
    g.out_('Z1_s_Lloop0', 1.0, False)
    g.out_('Z1_s_color', v1479, True)
    g.out_('Z1_s_energy', v1459[0], True)
    g.out_('Z1_s_f0', v1372, True)
    g.out_('Z1_s_inputData_bakedGI', v30, True)
    g.out_('Z1_s_inputData_fogCoord', 0, False)
    g.out_('Z1_s_inputData_normalWS', v665, True)
    g.out_('Z1_s_inputData_normalizedScreenSpaceUV', v29, True)
    g.out_('Z1_s_inputData_positionCS', v17, True)
    g.out_('Z1_s_inputData_positionCS_w', v18, False)
    g.out_('Z1_s_inputData_positionWS', v20, True)
    g.out_('Z1_s_inputData_shadowCoord', (0.0, 0.0, 0.0), True)
    g.out_('Z1_s_inputData_shadowCoord_w', 0.0, False)
    g.out_('Z1_s_inputData_shadowMask', (1, 1, 1), True)
    g.out_('Z1_s_inputData_shadowMask_w', 1, False)
    g.out_('Z1_s_inputData_vertexLighting', (0, 0, 0), True)
    g.out_('Z1_s_inputData_viewDirectionWS', v28, True)
    g.out_('Z1_s_lightIndex', 0, False)
    g.out_('Z1_s_roughness', v662, False)
    g.out_('Z1_s_sssAmount', v1387, False)
    g.out_('Z1_r___done', 0.0, False)
    g.out_('Z1_r_diffuse', v1359, True)
    g.out_('Z1_r_sssTint', v1378, True)
    g.out_('Z1_r_pixelLightCount', v1480, False)
    v1484 = g.inp('Z1_o_H', True)
    v1485 = g.inp('Z1_o_L', True)
    v1486 = g.inp('Z1_o_LV', True)
    v1487 = g.inp('Z1_o_N', True)
    v1488 = g.inp('Z1_o_NoH', False)
    v1489 = g.inp('Z1_o_NoL', False)
    v1490 = g.inp('Z1_o_NoV', False)
    v1491 = g.inp('Z1_o_P', True)
    v1492 = g.inp('Z1_o_V', True)
    v1493 = g.inp('Z1_o_VoH', False)
    v1494 = g.inp('Z1_o_Lloop0', False)
    v1495 = g.inp('Z1_o_color', True)
    v1496 = g.inp('Z1_o_energy', True)
    v1497 = g.inp('Z1_o_f0', True)
    v1498 = g.inp('Z1_o_inputData_bakedGI', True)
    v1499 = g.inp('Z1_o_inputData_fogCoord', False)
    v1500 = g.inp('Z1_o_inputData_normalWS', True)
    v1501 = g.inp('Z1_o_inputData_normalizedScreenSpaceUV', True)
    v1502 = g.inp('Z1_o_inputData_positionCS', True)
    v1503 = g.inp('Z1_o_inputData_positionCS_w', False)
    v1504 = g.inp('Z1_o_inputData_positionWS', True)
    v1505 = g.inp('Z1_o_inputData_shadowCoord', True)
    v1506 = g.inp('Z1_o_inputData_shadowCoord_w', False)
    v1507 = g.inp('Z1_o_inputData_shadowMask', True)
    v1508 = g.inp('Z1_o_inputData_shadowMask_w', False)
    v1509 = g.inp('Z1_o_inputData_vertexLighting', True)
    v1510 = g.inp('Z1_o_inputData_viewDirectionWS', True)
    v1511 = g.inp('Z1_o_lightIndex', False)
    v1512 = g.inp('Z1_o_roughness', False)
    v1513 = g.inp('Z1_o_sssAmount', False)
    v1514 = g.vmath('ADD', v1495, v1346)
    v1515 = g.math('COMPARE', v60, 0, 1e-05)
    v1516 = g.math('SUBTRACT', 1.0, v1515)
    v1517 = g.vmath('SUBTRACT', v20, v26)
    v1518 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v1519 = g.bc(v1518)
    v1520 = g.vmath('DIVIDE', v1517, v1519)
    v1521 = g.vmath('ADD', v661, v1514)
    v1522 = g.vmath('LENGTH', v1520)
    v1523 = g.sep(v1520)
    v1524 = g.comb(v1523[0], v1523[2], 0.0)
    v1525 = g.vmath('LENGTH', v1524)
    v1526 = g.math('ABSOLUTE', v1523[1], 0.0)
    v1527 = g.math('MAXIMUM', v1525, v1526)
    v1528 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v1529 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v1530 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v1531 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v1532 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v1533 = g.inp('_RuriFogColor_w', False, 0.0)
    v1534 = g.group_named('RCE_RuriApplyFog', [('inColor', v1521), ('inColor_w', 1), ('sphericalVertexDistance', v1522), ('cylindricalVertexDistance', v1527), ('environmentalStart', v1528), ('environmentalEnd', v1529), ('renderDistanceStart', v1530), ('renderDistanceEnd', v1531), ('fogColor', v1532), ('fogColor_w', v1533)])
    v1535 = g.mixv(v1516, v1514, v1534[0])
    g.out_('ret_gBuffer0', v1535, True)
    g.out_('ret_gBuffer0_w', v223, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v1514, True)
    g.out_('ret_color_w', v223, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


def build_Ruri_Endfield_Scene_LitHLod():
    t = _tree('Ruri Endfield Scene LitHLod')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.vmath('SUBTRACT', v14, v0)
    v133 = g.comb(v77, v77, 0.0)
    v134 = g.vmath('MULTIPLY', v133, v132)
    v135 = g.vmath('ADD', v134, v0)
    v136 = g.comb(v83[0], v83[1], 0.0)
    v137 = g.comb(v83[2], v82, 0.0)
    v138 = g.vmath('MULTIPLY', v135, v136)
    v139 = g.vmath('ADD', v138, v137)
    v140 = g.comb(v88, v88, 0.0)
    v141 = g.vmath('MULTIPLY', v140, v132)
    v142 = g.vmath('ADD', v141, v0)
    v143 = g.comb(v94[0], v94[1], 0.0)
    v144 = g.comb(v94[2], v93, 0.0)
    v145 = g.vmath('MULTIPLY', v142, v143)
    v146 = g.vmath('ADD', v145, v144)
    g.out_('F4_BaseColorMap_uv', v139, True)
    v147 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v148 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    g.out_('F5_NormalMap_uv', v146, True)
    v149 = g.inp('F5_NormalMap', True, (1.0, 1.0, 1.0))
    v150 = g.inp('F5_NormalMap_alpha', False, 1.0)
    v151 = g.math('MULTIPLY', v58, v148)
    g.out_('F6_MROMap_uv', v146, True)
    v152 = g.inp('F6_MROMap', True, (1.0, 1.0, 1.0))
    v153 = g.inp('F6_MROMap_alpha', False, 1.0)
    v154 = g.sep(v152)
    v155 = g.sep(v149)
    v156 = g.math('MULTIPLY', v155[0], v150)
    v157 = g.math('MULTIPLY_ADD', v156, 2, -1)
    v158 = g.math('MULTIPLY_ADD', v155[1], 2, -1)
    v159 = g.inp('_NormalScale', False, 0.0)
    v160 = g.math('MULTIPLY', v157, v159)
    v161 = g.math('MULTIPLY', v158, v159)
    v162 = g.vmath('MULTIPLY', v147, v37)
    v163 = g.inp('_BaseColorBrighterScale', False, 1.0)
    v164 = g.bc(v163)
    v165 = g.vmath('MULTIPLY', v162, v164)
    v166 = g.sep(v165)
    v167 = g.clampn(v166[0])
    v168 = g.clampn(v166[1])
    v169 = g.clampn(v166[2])
    v170 = g.comb(v167, v168, v169)
    v171 = g.inp('_BaseColorTintCover', False, 0.0)
    v172 = g.mixv(v171, v170, v37)
    v173 = g.inp('_RoughnessMin', False, 0.0)
    v174 = g.inp('_RoughnessMax', False, 1.0)
    v175 = g.mixf(v154[1], v173, v174)
    v176 = g.inp('_Metallic', False, 0.0)
    v177 = g.inp('_BaseTextureMapCount', False, 0.0)
    v178 = g.math('SUBTRACT', v177, 1)
    v179 = g.clampn(v178)
    v180 = g.mixf(v179, v154[0], v176)
    v181 = g.inp('_OcclusionStrength', False, 1.0)
    v182 = g.mixf(v181, 1, v154[2])
    v183 = g.inp('_PorosityFactorX', False, 0.2)
    v184 = g.math('MULTIPLY', v183, v175)
    v185 = g.inp('_PorosityFactorZ', False, 0.0)
    v186 = g.math('MULTIPLY', v185, v180)
    v187 = g.math('ADD', v184, v186)
    v188 = g.inp('_PorosityFactorY', False, 0.4)
    v189 = g.math('ADD', v187, v188)
    v190 = g.clampn(v189)
    v191 = g.math('MULTIPLY', v190, 0.95)
    v192 = g.math('ADD', v191, 0.05)
    v193 = g.inp('_DisableVerticalFlow', False, 0.0)
    v194 = g.math('SUBTRACT', 1, v193)
    v195 = g.math('MULTIPLY', v192, v194)
    v196 = g.inp('_EffectIntensity', False, 1.0)
    v197 = g.math('MULTIPLY', v151, v196)
    v198 = g.inp('_HLodFade', False, 1.0)
    v199 = g.math('MULTIPLY', v197, v198)
    v200 = g.vmath('DOT_PRODUCT', v36, v21)
    v201 = g.math('LESS_THAN', v200, 0)
    v202 = g.mixf(v201, 1, -1)
    v203 = g.comb(v157, v158, 0.0)
    v204 = g.comb(v157, v158, 0.0)
    v205 = g.vmath('DOT_PRODUCT', v203, v204)
    v206 = g.math('MINIMUM', v205, 1)
    v207 = g.math('SUBTRACT', 1, v206)
    v208 = g.math('SQRT', v207, 0.0)
    v209 = g.math('MAXIMUM', v208, 1.0000000168623835E-16)
    v210 = g.math('MULTIPLY', v209, v202)
    v211 = g.math('GREATER_THAN', v5, 0)
    v212 = g.mixf(v211, -1, 1)
    v213 = g.vmath('CROSS_PRODUCT', v21, v22)
    v214 = g.bc(v212)
    v215 = g.vmath('MULTIPLY', v214, v213)
    v216 = g.bc(v210)
    v217 = g.vmath('MULTIPLY', v21, v216)
    v218 = g.bc(v160)
    v219 = g.vmath('MULTIPLY', v22, v218)
    v220 = g.vmath('ADD', v217, v219)
    v221 = g.bc(v161)
    v222 = g.vmath('MULTIPLY', v215, v221)
    v223 = g.vmath('ADD', v220, v222)
    v224 = g.vmath('NORMALIZE', v223)
    v225 = g.mixf(v121, v58, v199)
    v226 = g.mixv(v121, v65, v172)
    v227 = g.mixf(v121, v114, v175)
    v228 = g.mixf(v121, v115, v180)
    v229 = g.mixf(v121, v116, v182)
    v230 = g.mixf(v121, v117, v195)
    v231 = g.mixv(v121, v36, v224)
    v232 = g.vmath('NORMALIZE', v21)
    v233 = g.vmath('NORMALIZE', v22)
    v234 = g.vmath('CROSS_PRODUCT', v232, v233)
    v235 = g.bc(v5)
    v236 = g.vmath('MULTIPLY', v234, v235)
    v237 = g.inp('_UseMacroNormalMap', False, 0.0)
    v238 = g.math('GREATER_THAN', v237, 0.5)
    g.out_('F7_MacroNormalMap_uv', v0, True)
    v239 = g.inp('F7_MacroNormalMap', True, (1.0, 1.0, 1.0))
    v240 = g.inp('F7_MacroNormalMap_alpha', False, 1.0)
    v241 = g.inp('_MacroNormalMapScale', False, 1.0)
    v242 = g.sep(v239)
    v243 = g.math('MULTIPLY', v240, v242[0])
    v244 = g.math('MULTIPLY', v243, 2.0)
    v245 = g.math('SUBTRACT', v244, 1.0)
    v246 = g.math('MULTIPLY', v242[1], 2.0)
    v247 = g.math('SUBTRACT', v246, 1.0)
    v248 = g.math('MULTIPLY', v245, v245)
    v249 = g.math('MULTIPLY', v247, v247)
    v250 = g.math('ADD', v248, v249)
    v251 = g.clampn(v250)
    v252 = g.math('SUBTRACT', 1.0, v251)
    v253 = g.math('SQRT', v252, 0.0)
    v254 = g.math('MAXIMUM', v253, 1e-16)
    v255 = g.math('MULTIPLY', v245, v241)
    v256 = g.math('MULTIPLY', v247, v241)
    v257 = g.comb(v255, v256, v254)
    v258 = g.sep(v257)
    v259 = g.bc(v258[0])
    v260 = g.vmath('MULTIPLY', v259, v233)
    v261 = g.bc(v258[1])
    v262 = g.vmath('MULTIPLY', v261, v236)
    v263 = g.vmath('ADD', v260, v262)
    v264 = g.bc(v258[2])
    v265 = g.vmath('MULTIPLY', v264, v232)
    v266 = g.vmath('ADD', v263, v265)
    v267 = g.vmath('NORMALIZE', v266)
    v268 = g.vmath('SUBTRACT', v267, v232)
    v269 = g.vmath('ADD', v231, v268)
    v270 = g.vmath('NORMALIZE', v269)
    v271 = g.mixv(v238, v231, v270)
    v272 = g.inp('_EnableDetailMap', False, 0.0)
    v273 = g.math('GREATER_THAN', v272, 0.5)
    v274 = g.vmath('DISTANCE', v20, v26)
    v275 = g.inp('_DetailFalloffStart', False, 750.0)
    v276 = g.math('SUBTRACT', v274, v275)
    v277 = g.inp('_DetailFalloffEnd', False, 800.0)
    v278 = g.math('SUBTRACT', v277, v275)
    v279 = g.math('MAXIMUM', v278, 0.001)
    v280 = g.math('DIVIDE', v276, v279)
    v281 = g.clampn(v280)
    v282 = g.math('SUBTRACT', 1, v281)
    g.out_('F8_DetailMap_uv', v0, True)
    v283 = g.inp('F8_DetailMap', True, (1.0, 1.0, 1.0))
    v284 = g.inp('F8_DetailMap_alpha', False, 1.0)
    v285 = g.inp('_DetailMaskMode', False, 0.0)
    v286 = g.math('LESS_THAN', v285, 0.5)
    v287 = g.math('LESS_THAN', v285, 1.5)
    v288 = g.math('LESS_THAN', v285, 2.5)
    v289 = g.math('LESS_THAN', v285, 3.5)
    g.out_('F9_NormalMap_uv', v0, True)
    v290 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v291 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v292 = g.sep(v290)
    v293 = g.math('LESS_THAN', v285, 4.5)
    v294 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v295 = g.inp('F9_NormalMap_alpha', False, 1.0)
    g.out_('F10_MROMap_uv', v0, True)
    v296 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v297 = g.inp('F10_MROMap_alpha', False, 1.0)
    v298 = g.mixf(v293, v297, v295)
    v299 = g.mixf(v289, v298, v292[2])
    v300 = g.mixf(v288, v299, v225)
    v301 = g.mixf(v287, v300, v284)
    v302 = g.mixf(v286, v301, 1)
    v303 = g.math('MULTIPLY', v282, v302)
    v304 = g.sep(v283)
    v305 = g.comb(v304[0], v304[1], 0.0)
    v306 = g.vmath('MULTIPLY', v305, (2, 2, 0.0))
    v307 = g.vmath('SUBTRACT', v306, (1, 1, 0.0))
    v308 = g.inp('_DetailNormalIntensity', False, 1.0)
    v309 = g.math('MULTIPLY', v308, v303)
    v310 = g.comb(v309, v309, 0.0)
    v311 = g.vmath('MULTIPLY', v307, v310)
    v312 = g.sep(v311)
    v313 = g.comb(v312[0], v312[1], 1)
    v314 = g.sep(v313)
    v315 = g.bc(v314[0])
    v316 = g.vmath('MULTIPLY', v315, v233)
    v317 = g.bc(v314[1])
    v318 = g.vmath('MULTIPLY', v317, v236)
    v319 = g.vmath('ADD', v316, v318)
    v320 = g.bc(v314[2])
    v321 = g.vmath('MULTIPLY', v320, v232)
    v322 = g.vmath('ADD', v319, v321)
    v323 = g.vmath('NORMALIZE', v322)
    v324 = g.vmath('SUBTRACT', v323, v232)
    v325 = g.vmath('ADD', v271, v324)
    v326 = g.vmath('NORMALIZE', v325)
    v327 = g.inp('_DetailMode', False, 0.0)
    v328 = g.math('LESS_THAN', v327, 0.5)
    v329 = g.inp('_DetailBaseColorBrighterScale', False, 1.0)
    v330 = g.mixf(v303, 1, v329)
    v331 = g.bc(v330)
    v332 = g.vmath('MULTIPLY', v226, v331)
    v333 = g.sep(v332)
    v334 = g.clampn(v333[0])
    v335 = g.clampn(v333[1])
    v336 = g.clampn(v333[2])
    v337 = g.comb(v334, v335, v336)
    v338 = g.inp('_DetailOverlayColor', True, (0.0, 0.0, 0.0))
    v339 = g.inp('_DetailOverlayColor_w', False, 0.0)
    v340 = g.vmath('MULTIPLY', v337, v338)
    v341 = g.math('MULTIPLY', v303, v339)
    v342 = g.mixv(v341, v337, v340)
    v343 = g.inp('_DetailPBRIntensity', False, 1.0)
    v344 = g.math('MULTIPLY', v303, v343)
    v345 = g.mixf(v344, v227, v304[2])
    v346 = g.math('MULTIPLY', v303, v343)
    v347 = g.mixf(v346, v227, v304[2])
    v348 = g.math('MULTIPLY', v229, v284)
    v349 = g.mixf(v303, v229, v348)
    v350 = g.mixv(v328, v226, v342)
    v351 = g.mixf(v328, v347, v345)
    v352 = g.mixf(v328, v349, v229)
    v353 = g.mixv(v273, v226, v350)
    v354 = g.mixf(v273, v227, v351)
    v355 = g.mixf(v273, v229, v352)
    v356 = g.mixv(v273, v271, v326)
    v357 = g.bc(v163)
    v358 = g.vmath('MULTIPLY', v353, v357)
    v359 = g.sep(v358)
    v360 = g.clampn(v359[0])
    v361 = g.clampn(v359[1])
    v362 = g.clampn(v359[2])
    v363 = g.comb(v360, v361, v362)
    v364 = g.mixv(v171, v363, v37)
    v365 = g.mixf(v354, v173, v174)
    v366 = g.clampn(v365, 0, 1)
    v367 = g.mixf(v181, 1, v355)
    v368 = g.math('LESS_THAN', v177, 0.5)
    v369 = g.math('SUBTRACT', 1.0, v368)
    v370 = g.mixf(v369, v228, v115)
    v371 = g.inp('_EnableTriChannelMask', False, 0.0)
    v372 = g.math('GREATER_THAN', v371, 0.5)
    v373 = g.vmath('SUBTRACT', v14, v0)
    v374 = g.inp('_MaskUVSet', False, 0.0)
    v375 = g.comb(v374, v374, 0.0)
    v376 = g.vmath('MULTIPLY', v375, v373)
    v377 = g.vmath('ADD', v376, v0)
    v378 = g.inp('_MaskMap_ST', True, (1.0, 1.0, 0.0))
    v379 = g.inp('_MaskMap_ST_w', False, 0.0)
    v380 = g.sep(v378)
    v381 = g.comb(v380[0], v380[1], 0.0)
    v382 = g.comb(v380[2], v379, 0.0)
    v383 = g.vmath('MULTIPLY', v377, v381)
    v384 = g.vmath('ADD', v383, v382)
    g.out_('F11_MaskMap_uv', v384, True)
    v385 = g.inp('F11_MaskMap', True, (1.0, 1.0, 1.0))
    v386 = g.inp('F11_MaskMap_alpha', False, 1.0)
    v387 = g.sep(v385)
    v388 = g.inp('_MaskBOffset', False, 0.0)
    v389 = g.math('ADD', v387[2], v388)
    v390 = g.inp('_MaskBScale', False, 0.0)
    v391 = g.math('ADD', v390, 1)
    v392 = g.math('MULTIPLY', v390, -0.5)
    v393 = g.math('MULTIPLY_ADD', v389, v391, v392)
    v394 = g.clampn(v393, 0, 1)
    v395 = g.inp('_MaskAlbedoB', True, (0.0, 0.0, 1.0))
    v396 = g.inp('_MaskAlbedoB_w', False, 1.0)
    v397 = g.math('MULTIPLY', v394, v396)
    v398 = g.inp('_MaskGOffset', False, 0.0)
    v399 = g.math('ADD', v387[1], v398)
    v400 = g.inp('_MaskGScale', False, 0.0)
    v401 = g.math('ADD', v400, 1)
    v402 = g.math('MULTIPLY', v400, -0.5)
    v403 = g.math('MULTIPLY_ADD', v399, v401, v402)
    v404 = g.clampn(v403, 0, 1)
    v405 = g.inp('_MaskAlbedoG', True, (0.0, 1.0, 0.0))
    v406 = g.inp('_MaskAlbedoG_w', False, 1.0)
    v407 = g.math('MULTIPLY', v404, v406)
    v408 = g.inp('_MaskROffset', False, 0.0)
    v409 = g.math('ADD', v387[0], v408)
    v410 = g.inp('_MaskRScale', False, 0.0)
    v411 = g.math('ADD', v410, 1)
    v412 = g.math('MULTIPLY', v410, -0.5)
    v413 = g.math('MULTIPLY_ADD', v409, v411, v412)
    v414 = g.clampn(v413, 0, 1)
    v415 = g.inp('_MaskAlbedoR', True, (1.0, 0.0, 0.0))
    v416 = g.inp('_MaskAlbedoR_w', False, 1.0)
    v417 = g.math('MULTIPLY', v414, v416)
    v418 = g.mixv(v397, v364, v395)
    v419 = g.mixv(v407, v418, v405)
    v420 = g.mixv(v417, v419, v415)
    v421 = g.inp('_MaskRoghnessB', False, 0.25)
    v422 = g.mixf(v397, v366, v421)
    v423 = g.inp('_MaskRoghnessG', False, 0.25)
    v424 = g.mixf(v407, v422, v423)
    v425 = g.inp('_MaskRoghnessR', False, 0.25)
    v426 = g.mixf(v417, v424, v425)
    v427 = g.inp('_MaskMetallicB', False, 0.0)
    v428 = g.mixf(v397, v370, v427)
    v429 = g.inp('_MaskMetallicG', False, 0.0)
    v430 = g.mixf(v407, v428, v429)
    v431 = g.inp('_MaskMetallicR', False, 0.0)
    v432 = g.mixf(v417, v430, v431)
    v433 = g.mixv(v372, v364, v420)
    v434 = g.mixf(v372, v366, v426)
    v435 = g.mixf(v372, v370, v432)
    v436 = g.inp('_LayerBlend', False, 0.0)
    v437 = g.math('GREATER_THAN', v436, 0.5)
    v438 = g.inp('_LayerBlendUVType', False, 0.0)
    v439 = g.math('GREATER_THAN', v438, 0.5)
    v440 = g.math('LESS_THAN', v438, 1.5)
    v441 = g.math('MULTIPLY', v439, v440)
    v442 = g.sep(v20)
    v443 = g.comb(v442[0], v442[2], 0.0)
    v444 = g.inp('_Layer1Tilling', False, 1.0)
    v445 = g.comb(v444, v444, 0.0)
    v446 = g.vmath('MULTIPLY', v443, v445)
    v447 = g.math('GREATER_THAN', v438, 1.5)
    v448 = g.comb(v444, v444, 0.0)
    v449 = g.vmath('MULTIPLY', v15, v448)
    v450 = g.comb(v444, v444, 0.0)
    v451 = g.vmath('MULTIPLY', v0, v450)
    v452 = g.mixv(v447, v451, v449)
    v453 = g.mixv(v441, v452, v446)
    g.out_('F12_Layer1BaseMap_uv', v453, True)
    v454 = g.inp('F12_Layer1BaseMap', True, (1.0, 1.0, 1.0))
    v455 = g.inp('F12_Layer1BaseMap_alpha', False, 1.0)
    g.out_('F13_Layer1BumpMap_uv', v453, True)
    v456 = g.inp('F13_Layer1BumpMap', True, (1.0, 1.0, 1.0))
    v457 = g.inp('F13_Layer1BumpMap_alpha', False, 1.0)
    g.out_('F14_BaseHeightMap_uv', v453, True)
    v458 = g.inp('F14_BaseHeightMap', True, (1.0, 1.0, 1.0))
    v459 = g.inp('F14_BaseHeightMap_alpha', False, 1.0)
    v460 = g.sep(v458)
    v461 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v462 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v463 = g.sep(v461)
    v464 = g.math('MULTIPLY_ADD', v463[0], 2, -1)
    v465 = g.math('MULTIPLY_ADD', v463[1], 2, -1)
    v466 = g.math('ABSOLUTE', v464, 0.0)
    v467 = g.math('LESS_THAN', v466, 0.012000000104308128)
    v468 = g.mixf(v467, v464, 0)
    v469 = g.math('ABSOLUTE', v465, 0.0)
    v470 = g.math('LESS_THAN', v469, 0.012000000104308128)
    v471 = g.mixf(v470, v465, 0)
    v472 = g.math('MULTIPLY', v468, v159)
    v473 = g.math('MULTIPLY', v471, v159)
    v474 = g.vmath('DOT_PRODUCT', v356, v21)
    v475 = g.math('LESS_THAN', v474, 0)
    v476 = g.mixf(v475, 1, -1)
    v477 = g.inp('_LayerBlendType', False, 1.0)
    v478 = g.math('LESS_THAN', v477, 0.5)
    v479 = g.sep(v11)
    v480 = g.math('SUBTRACT', 1, v479[0])
    v481 = g.clampn(v480, 0, 1)
    v482 = g.math('LESS_THAN', v477, 1.5)
    v483 = g.inp('_LayerBlendMaskUVType', False, 0.0)
    v484 = g.math('GREATER_THAN', v483, 0.5)
    v485 = g.mixv(v484, v14, v0)
    v486 = g.inp('_LayerBlendMaskType', False, 0.0)
    v487 = g.math('COMPARE', v486, 0, 1e-05)
    v488 = g.math('SUBTRACT', 1.0, v487)
    g.out_('F15_LayerBlendMaskMap_uv', v485, True)
    v489 = g.inp('F15_LayerBlendMaskMap', True, (1.0, 1.0, 1.0))
    v490 = g.inp('F15_LayerBlendMaskMap_alpha', False, 1.0)
    v491 = g.sep(v489)
    v492 = g.mixf(v488, v491[0], v462)
    v493 = g.clampn(v492, 0, 1)
    v494 = g.math('GREATER_THAN', v5, 0)
    v495 = g.mixf(v494, -1, 1)
    v496 = g.vmath('CROSS_PRODUCT', v21, v22)
    v497 = g.bc(v495)
    v498 = g.vmath('MULTIPLY', v497, v496)
    v499 = g.comb(v468, v471, 0.0)
    v500 = g.comb(v468, v471, 0.0)
    v501 = g.vmath('DOT_PRODUCT', v499, v500)
    v502 = g.math('MINIMUM', v501, 1)
    v503 = g.math('SUBTRACT', 1, v502)
    v504 = g.math('SQRT', v503, 0.0)
    v505 = g.bc(v504)
    v506 = g.vmath('MULTIPLY', v21, v505)
    v507 = g.bc(v472)
    v508 = g.vmath('MULTIPLY', v22, v507)
    v509 = g.vmath('ADD', v506, v508)
    v510 = g.bc(v473)
    v511 = g.vmath('MULTIPLY', v498, v510)
    v512 = g.vmath('ADD', v509, v511)
    v513 = g.inp('_TopBlendWithBumpMap', False, 0.0)
    v514 = g.sep(v512)
    v515 = g.vmath('DOT_PRODUCT', v512, v512)
    v516 = g.math('INVERSE_SQRT', v515, 0.0)
    v517 = g.math('MULTIPLY', v514[1], v516)
    v518 = g.sep(v21)
    v519 = g.math('SUBTRACT', v517, v518[1])
    v520 = g.math('MULTIPLY_ADD', v513, v519, v518[1])
    v521 = g.inp('_TopBlendThreshold', False, 0.5)
    v522 = g.math('SUBTRACT', v520, v521)
    v523 = g.inp('_TopBlendSmoothness', False, 0.5)
    v524 = g.math('MAXIMUM', 1.1754943508222875E-38, v523)
    v525 = g.math('DIVIDE', v522, v524)
    v526 = g.clampn(v525, 0, 1)
    v527 = g.mixf(v482, v526, v493)
    v528 = g.mixf(v478, v527, v481)
    v529 = g.math('SUBTRACT', 1, v528)
    v530 = g.math('MULTIPLY', v455, v528)
    v531 = g.math('MULTIPLY', v460[0], v529)
    v532 = g.math('MAXIMUM', v530, v531)
    v533 = g.math('MULTIPLY', v532, -1.0)
    v534 = g.inp('_LayerBlendHeightTransition', False, 1.0)
    v535 = g.math('MULTIPLY_ADD', v528, v455, v534)
    v536 = g.math('ADD', v533, v535)
    v537 = g.math('MAXIMUM', v536, 0)
    v538 = g.math('ADD', v537, 9.999999974752427E-07)
    v539 = g.math('MULTIPLY', v528, v538)
    v540 = g.math('MULTIPLY', v532, -1.0)
    v541 = g.math('MULTIPLY_ADD', v529, v460[0], v534)
    v542 = g.math('ADD', v540, v541)
    v543 = g.math('MAXIMUM', v542, 0)
    v544 = g.math('ADD', v543, 9.999999974752427E-07)
    v545 = g.math('MULTIPLY', v529, v544)
    v546 = g.inp('_LayerBlendHeight', False, 1.0)
    v547 = g.math('COMPARE', v546, 0, 1e-05)
    v548 = g.math('SUBTRACT', 1.0, v547)
    v549 = g.math('ADD', v539, v545)
    v550 = g.math('MAXIMUM', v549, 1.1754943508222875E-38)
    v551 = g.math('DIVIDE', v539, v550)
    v552 = g.mixf(v548, v528, v551)
    v553 = g.vmath('DOT_PRODUCT', v454, (0.2126729041337967, 0.7151522040367126, 0.07217500358819962))
    v554 = g.inp('_Layer1Saturation', False, 0.0)
    v555 = g.math('ADD', 1, v554)
    v556 = g.clampn(v555, 0, 1)
    v557 = g.math('MULTIPLY', v553, -1.0)
    v558 = g.sep(v454)
    v559 = g.math('ADD', v557, v558[0])
    v560 = g.math('MULTIPLY_ADD', v556, v559, v553)
    v561 = g.math('MULTIPLY', v553, -1.0)
    v562 = g.math('ADD', v561, v558[1])
    v563 = g.math('MULTIPLY_ADD', v556, v562, v553)
    v564 = g.math('MULTIPLY', v553, -1.0)
    v565 = g.math('ADD', v564, v558[2])
    v566 = g.math('MULTIPLY_ADD', v556, v565, v553)
    v567 = g.comb(v560, v563, v566)
    v568 = g.inp('_Layer1TintColor', True, (1.0, 1.0, 1.0))
    v569 = g.inp('_Layer1TintColor_w', False, 1.0)
    v570 = g.vmath('MULTIPLY', v567, v568)
    v571 = g.inp('_Layer1ColorBrighterScale', False, 1.0)
    v572 = g.bc(v571)
    v573 = g.vmath('MULTIPLY', v570, v572)
    v574 = g.mixv(v552, v433, v573)
    v575 = g.inp('_LayerMetallicType', False, 0.0)
    v576 = g.math('COMPARE', v575, 0, 1e-05)
    v577 = g.math('SUBTRACT', 1.0, v576)
    v578 = g.inp('_Layer1Metallic', False, 0.0)
    v579 = g.mixf(v577, v578, v455)
    v580 = g.mixf(v552, v435, v579)
    v581 = g.sep(v456)
    v582 = g.mixf(v552, v434, v581[2])
    v583 = g.inp('_Layer1AOStrength', False, 1.0)
    v584 = g.mixf(v583, 1, v457)
    v585 = g.mixf(v552, v367, v584)
    v586 = g.math('MULTIPLY_ADD', v581[0], 2, -1)
    v587 = g.math('MULTIPLY_ADD', v581[1], 2, -1)
    v588 = g.math('ABSOLUTE', v586, 0.0)
    v589 = g.math('LESS_THAN', v588, 0.012000000104308128)
    v590 = g.mixf(v589, v586, 0)
    v591 = g.math('ABSOLUTE', v587, 0.0)
    v592 = g.math('LESS_THAN', v591, 0.012000000104308128)
    v593 = g.mixf(v592, v587, 0)
    v594 = g.inp('_Layer1BumpScale', False, 1.0)
    v595 = g.math('MULTIPLY', v590, v594)
    v596 = g.math('MULTIPLY', v593, v594)
    v597 = g.comb(v590, v593, 0.0)
    v598 = g.comb(v590, v593, 0.0)
    v599 = g.vmath('DOT_PRODUCT', v597, v598)
    v600 = g.math('MINIMUM', v599, 1)
    v601 = g.math('SUBTRACT', 1, v600)
    v602 = g.math('SQRT', v601, 0.0)
    v603 = g.math('MAXIMUM', v602, 1.0000000168623835E-16)
    v604 = g.comb(v468, v471, 0.0)
    v605 = g.comb(v468, v471, 0.0)
    v606 = g.vmath('DOT_PRODUCT', v604, v605)
    v607 = g.math('SUBTRACT', 1, v606)
    v608 = g.math('MAXIMUM', v607, 0)
    v609 = g.math('SQRT', v608, 0.0)
    v610 = g.math('ADD', v609, 1)
    v611 = g.comb(v472, v473, v610)
    v612 = g.math('MULTIPLY', v595, -1.0)
    v613 = g.math('MULTIPLY', v596, -1.0)
    v614 = g.comb(v612, v613, v603)
    v615 = g.vmath('DOT_PRODUCT', v611, v614)
    v616 = g.inp('_Layer1BaseNormalIntensity', False, 0.0)
    v617 = g.math('MULTIPLY', v595, -1.0)
    v618 = g.math('MULTIPLY', v615, v472)
    v619 = g.math('DIVIDE', v618, v610)
    v620 = g.math('ADD', v595, v619)
    v621 = g.math('ADD', v617, v620)
    v622 = g.math('MULTIPLY_ADD', v616, v621, v595)
    v623 = g.math('MULTIPLY', v596, -1.0)
    v624 = g.math('MULTIPLY', v615, v473)
    v625 = g.math('DIVIDE', v624, v610)
    v626 = g.math('ADD', v596, v625)
    v627 = g.math('ADD', v623, v626)
    v628 = g.math('MULTIPLY_ADD', v616, v627, v596)
    v629 = g.math('MULTIPLY', v603, -1.0)
    v630 = g.math('SUBTRACT', v615, v603)
    v631 = g.math('ADD', v629, v630)
    v632 = g.math('MULTIPLY_ADD', v616, v631, v603)
    v633 = g.comb(v468, v471, 0.0)
    v634 = g.comb(v468, v471, 0.0)
    v635 = g.vmath('DOT_PRODUCT', v633, v634)
    v636 = g.math('SUBTRACT', 1, v635)
    v637 = g.math('MAXIMUM', v636, 0)
    v638 = g.math('SQRT', v637, 0.0)
    v639 = g.math('MULTIPLY', v472, -1.0)
    v640 = g.math('ADD', v639, v622)
    v641 = g.math('MULTIPLY_ADD', v552, v640, v472)
    v642 = g.math('MULTIPLY', v473, -1.0)
    v643 = g.math('ADD', v642, v628)
    v644 = g.math('MULTIPLY_ADD', v552, v643, v473)
    v645 = g.math('MULTIPLY', v638, -1.0)
    v646 = g.math('ADD', v645, v632)
    v647 = g.math('MULTIPLY_ADD', v552, v646, v638)
    v648 = g.math('MULTIPLY', v476, v647)
    v649 = g.math('GREATER_THAN', v5, 0)
    v650 = g.mixf(v649, -1, 1)
    v651 = g.vmath('CROSS_PRODUCT', v21, v22)
    v652 = g.bc(v650)
    v653 = g.vmath('MULTIPLY', v652, v651)
    v654 = g.bc(v648)
    v655 = g.vmath('MULTIPLY', v21, v654)
    v656 = g.bc(v641)
    v657 = g.vmath('MULTIPLY', v22, v656)
    v658 = g.vmath('ADD', v655, v657)
    v659 = g.bc(v644)
    v660 = g.vmath('MULTIPLY', v653, v659)
    v661 = g.vmath('ADD', v658, v660)
    v662 = g.vmath('NORMALIZE', v661)
    v663 = g.mixv(v437, v433, v574)
    v664 = g.mixf(v437, v434, v582)
    v665 = g.mixf(v437, v435, v580)
    v666 = g.mixf(v437, v367, v585)
    v667 = g.mixv(v437, v356, v662)
    v668 = g.mixf(v437, v157, v468)
    v669 = g.mixf(v437, v158, v471)
    v670 = g.mixf(v437, v160, v641)
    v671 = g.mixf(v437, v161, v644)
    v672 = g.mixf(v437, v202, v476)
    v673 = g.inp('_EmissionTint', True, (1.0, 1.0, 1.0))
    v674 = g.inp('_EmissionTint_w', False, 1.0)
    v675 = g.vmath('MULTIPLY', v663, v673)
    v676 = g.inp('_EmissiveIntensity', False, 0.0)
    v677 = g.bc(v676)
    v678 = g.vmath('MULTIPLY', v675, v677)
    v679 = g.inp('_UseEmissiveMap', False, 0.0)
    v680 = g.math('GREATER_THAN', v679, 0.5)
    v681 = g.inp('_AlbedoAffectEmissive', False, 1.0)
    v682 = g.mixv(v681, v663, (1, 1, 1))
    v683 = g.inp('_EnableEmissiveAnim', False, 0.0)
    v684 = g.math('GREATER_THAN', v683, 0.5)
    v685 = g.inp('_Time', True, (0.0, 0.0, 0.0))
    v686 = g.inp('_Time_w', False, 0.0)
    v687 = g.sep(v685)
    v688 = g.inp('_EmissiveAnimSpeed', False, 0.0)
    v689 = g.math('MULTIPLY', v687[1], v688)
    v690 = g.inp('_EmissiveAnimRandom', False, 0.0)
    v691 = g.math('MULTIPLY_ADD', v689, 0.15915493667125702, v690)
    v692 = g.math('FRACT', v691, 0.0)
    v693 = g.inp('_EmissiveAnimInterval', False, 1.0)
    v694 = g.math('MULTIPLY', v692, v693)
    v695 = g.clampn(v694, 0, 1)
    v696 = g.math('MULTIPLY_ADD', v695, 2, -1)
    v697 = g.math('MULTIPLY', v696, v696)
    v698 = g.inp('_EmissiveMinBrightness', False, 0.0)
    v699 = g.math('SUBTRACT', 1, v698)
    v700 = g.math('ADD', 1, v698)
    v701 = g.math('DIVIDE', v700, v699)
    v702 = g.math('MULTIPLY', v696, v697)
    v703 = g.math('ABSOLUTE', v702, 0.0)
    v704 = g.math('MULTIPLY', v703, 4)
    v705 = g.math('MULTIPLY_ADD', v697, -6, v704)
    v706 = g.math('ADD', v705, 1)
    v707 = g.math('ADD', v701, v706)
    v708 = g.math('MULTIPLY', v707, v699)
    v709 = g.math('MULTIPLY_ADD', v708, 0.5, -1)
    v710 = g.mixf(v684, 0, v709)
    v711 = g.inp('_EnableEmissiveAnimSweep', False, 0.0)
    v712 = g.math('GREATER_THAN', v711, 0.5)
    v713 = g.inp('_EmissiveMaskChannel', False, 0.0)
    v714 = g.math('GREATER_THAN', v713, 4.5)
    v715 = g.math('MAXIMUM', v712, v714)
    v716 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v717 = g.sep(v716)
    v718 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v719 = g.sep(v718)
    v720 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v721 = g.sep(v720)
    v722 = g.comb(v717[0], v719[1], v721[2])
    v723 = g.b2u(g.vtrans((1.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v724 = g.sep(v723)
    v725 = g.b2u(g.vtrans((0.0, 0.0, 1.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v726 = g.sep(v725)
    v727 = g.b2u(g.vtrans((0.0, 1.0, 0.0), 'OBJECT', 'WORLD', 'VECTOR'))
    v728 = g.sep(v727)
    v729 = g.comb(v724[1], v726[1], v728[1])
    v730 = g.vmath('SUBTRACT', v20, v722)
    v731 = g.vmath('DOT_PRODUCT', v729, v730)
    v732 = g.inp('_EmissiveSweepRandom', False, 0.0)
    v733 = g.sep(v722)
    v734 = g.math('MULTIPLY_ADD', v732, v733[0], v687[1])
    v735 = g.inp('_EmissiveSweepInterval', False, 3.0)
    v736 = g.math('DIVIDE', v734, v735)
    v737 = g.math('ABSOLUTE', v736, 0.0)
    v738 = g.math('FRACT', v737, 0.0)
    v739 = g.math('MULTIPLY', v736, -1.0)
    v740 = g.math('LESS_THAN', v736, v739)
    v741 = g.math('SUBTRACT', 1.0, v740)
    v742 = g.math('MULTIPLY', v738, -1.0)
    v743 = g.mixf(v741, v742, v738)
    v744 = g.inp('_EmissiveSweepSpeed', False, 3.0)
    v745 = g.math('MULTIPLY', 0.30000001192092896, v735)
    v746 = g.math('MULTIPLY', v745, -1.0)
    v747 = g.math('MULTIPLY_ADD', v743, v735, v746)
    v748 = g.math('MULTIPLY', v744, v747)
    v749 = g.math('SUBTRACT', v731, v748)
    v750 = g.math('ABSOLUTE', v749, 0.0)
    v751 = g.inp('_EmissiveSweepWidth', False, 0.8)
    v752 = g.math('DIVIDE', v750, v751)
    v753 = g.clampn(v752, 0, 1)
    v754 = g.math('MULTIPLY', v753, -1.0)
    v755 = g.inp('_EmissiveSweepFalloff', False, 1.0)
    v756 = g.math('MULTIPLY_ADD', v754, v755, v755)
    v757 = g.clampn(v756, 0, 1)
    v758 = g.inp('_EmissiveSweepAlbedoScale', False, 0.0)
    v759 = g.vmath('DOT_PRODUCT', v663, (0.3330000042915344, 0.3330000042915344, 0.3330000042915344))
    v760 = g.math('ADD', v759, -0.20000000298023224)
    v761 = g.math('MULTIPLY_ADD', v758, v760, 0.20000000298023224)
    v762 = g.math('MULTIPLY', v761, 5)
    v763 = g.math('MAXIMUM', v762, 0)
    v764 = g.math('MULTIPLY', v757, v757)
    v765 = g.math('MULTIPLY', v763, v764)
    v766 = g.math('SUBTRACT', v765, 1)
    v767 = g.mixf(v715, v710, v766)
    v768 = g.mixf(v715, 0, v765)
    v769 = g.inp('_EmissiveColor', True, (1.0, 1.0, 1.0))
    v770 = g.inp('_EmissiveColor_w', False, 1.0)
    v771 = g.math('MULTIPLY_ADD', v770, v767, 1)
    v772 = g.inp('_EmissiveColorG', True, (0.0, 0.0, 0.0))
    v773 = g.inp('_EmissiveColorG_w', False, 0.0)
    v774 = g.math('MULTIPLY_ADD', v773, v767, 1)
    v775 = g.inp('_EmissiveColorB', True, (0.0, 0.0, 0.0))
    v776 = g.inp('_EmissiveColorB_w', False, 0.0)
    v777 = g.math('MULTIPLY_ADD', v776, v767, 1)
    v778 = g.inp('_EmissiveColorA', True, (0.0, 0.0, 0.0))
    v779 = g.inp('_EmissiveColorA_w', False, 0.0)
    v780 = g.math('MULTIPLY_ADD', v779, v767, 1)
    v781 = g.math('LESS_THAN', v713, 0.5)
    v782 = g.vmath('SUBTRACT', v14, v0)
    v783 = g.inp('_EmissiveSpeed', True, (0.0, 0.0, 0.0))
    v784 = g.inp('_EmissiveSpeed_w', False, 0.0)
    v785 = g.sep(v783)
    v786 = g.comb(v785[0], v785[1], 0.0)
    v787 = g.comb(v687[1], v687[1], 0.0)
    v788 = g.inp('_EmissiveUVSet', False, 0.0)
    v789 = g.comb(v788, v788, 0.0)
    v790 = g.vmath('MULTIPLY', v789, v782)
    v791 = g.vmath('ADD', v790, v0)
    v792 = g.inp('_EmissiveMap_ST', True, (1.0, 1.0, 0.0))
    v793 = g.inp('_EmissiveMap_ST_w', False, 0.0)
    v794 = g.sep(v792)
    v795 = g.comb(v794[0], v794[1], 0.0)
    v796 = g.comb(v794[2], v793, 0.0)
    v797 = g.vmath('MULTIPLY', v791, v795)
    v798 = g.vmath('ADD', v797, v796)
    v799 = g.vmath('MULTIPLY', v786, v787)
    v800 = g.vmath('ADD', v799, v798)
    v801 = g.math('MAXIMUM', v684, v712)
    v802 = g.inp('_EmissiveMapTilling', False, 0.0)
    v803 = g.comb(v802, v802, 0.0)
    v804 = g.vmath('MULTIPLY', v800, v803)
    v805 = g.mixv(v801, v800, v804)
    g.out_('F16_EmissiveMap_uv', v805, True)
    v806 = g.inp('F16_EmissiveMap', True, (1.0, 1.0, 1.0))
    v807 = g.inp('F16_EmissiveMap_alpha', False, 1.0)
    v808 = g.sep(v806)
    v809 = g.math('MULTIPLY', v808[0], v771)
    v810 = g.bc(v809)
    v811 = g.vmath('MULTIPLY', v769, v810)
    v812 = g.math('MULTIPLY', v808[1], v774)
    v813 = g.bc(v812)
    v814 = g.vmath('MULTIPLY', v772, v813)
    v815 = g.math('MULTIPLY', v808[2], v777)
    v816 = g.bc(v815)
    v817 = g.vmath('MULTIPLY', v775, v816)
    v818 = g.vmath('ADD', v814, v817)
    v819 = g.math('MULTIPLY', v807, v780)
    v820 = g.bc(v819)
    v821 = g.vmath('MULTIPLY', v778, v820)
    v822 = g.vmath('ADD', v818, v821)
    v823 = g.inp('_EmissiveType', False, 0.0)
    v824 = g.bc(v823)
    v825 = g.vmath('MULTIPLY', v822, v824)
    v826 = g.vmath('ADD', v811, v825)
    v827 = g.math('MAXIMUM', v684, v712)
    v828 = g.vmath('MAXIMUM', v826, (0, 0, 0))
    v829 = g.vmath('MINIMUM', v828, (1000, 1000, 1000))
    v830 = g.mixv(v827, v826, v829)
    v831 = g.vmath('MULTIPLY', v830, v682)
    v832 = g.bc(v676)
    v833 = g.vmath('MULTIPLY', v831, v832)
    v834 = g.vmath('ADD', v678, v833)
    v835 = g.math('SUBTRACT', 1.0, v714)
    v836 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v837 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v838 = g.sep(v836)
    v839 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v840 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v841 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v842 = g.inp('F10_MROMap_alpha', False, 1.0)
    v843 = g.math('SUBTRACT', v713, 1)
    v844 = g.clampn(v843, 0, 1)
    v845 = g.math('MULTIPLY', v225, -1.0)
    v846 = g.math('ADD', v845, v838[2])
    v847 = g.math('MULTIPLY_ADD', v844, v846, v225)
    v848 = g.math('SUBTRACT', v713, 2)
    v849 = g.clampn(v848, 0, 1)
    v850 = g.math('SUBTRACT', v840, v847)
    v851 = g.math('MULTIPLY_ADD', v849, v850, v847)
    v852 = g.math('SUBTRACT', v713, 3)
    v853 = g.clampn(v852, 0, 1)
    v854 = g.math('SUBTRACT', v842, v851)
    v855 = g.math('MULTIPLY_ADD', v853, v854, v851)
    v856 = g.clampn(v713, 0, 1)
    v857 = g.math('SUBTRACT', v855, 1)
    v858 = g.math('MULTIPLY_ADD', v856, v857, 1)
    v859 = g.math('MULTIPLY_ADD', v858, 1.1111111640930176, -0.055555559694767)
    v860 = g.clampn(v859, 0, 1)
    v861 = g.math('MULTIPLY', v860, v771)
    v862 = g.math('MULTIPLY', v861, v856)
    v863 = g.bc(v862)
    v864 = g.vmath('MULTIPLY', v769, v863)
    v865 = g.vmath('MULTIPLY', v864, v682)
    v866 = g.bc(v676)
    v867 = g.vmath('MULTIPLY', v865, v866)
    v868 = g.vmath('ADD', v678, v867)
    v869 = g.bc(v768)
    v870 = g.vmath('MULTIPLY', v769, v869)
    v871 = g.vmath('MULTIPLY', v870, v682)
    v872 = g.bc(v676)
    v873 = g.vmath('MULTIPLY', v871, v872)
    v874 = g.vmath('ADD', v678, v873)
    v875 = g.mixv(v835, v874, v868)
    v876 = g.mixv(v781, v875, v834)
    v877 = g.mixv(v680, v678, v876)
    v878 = g.bc(v130)
    v879 = g.vmath('MULTIPLY', v663, v878)
    v880 = g.vmath('ADD', v877, v879)
    v881 = g.inp('_EnableMatcap', False, 0.0)
    v882 = g.math('GREATER_THAN', v881, 0.5)
    v883 = g.vtrans(v667, 'WORLD', 'CAMERA', 'VECTOR')
    v884 = g.vmath('NORMALIZE', v883)
    v885 = g.sep(v884)
    v886 = g.comb(v885[0], v885[1], 0.0)
    v887 = g.vmath('MULTIPLY', v886, (0.5, 0.5, 0.0))
    v888 = g.vmath('ADD', v887, (0.5, 0.5, 0.0))
    g.out_('F17_MatcapMap_uv', v888, True)
    v889 = g.inp('F17_MatcapMap', True, (1.0, 1.0, 1.0))
    v890 = g.inp('F17_MatcapMap_alpha', False, 1.0)
    v891 = g.inp('_MatcapMapStrength', False, 0.2)
    v892 = g.bc(v891)
    v893 = g.vmath('MULTIPLY', v889, v892)
    v894 = g.vmath('ADD', v880, v893)
    v895 = g.mixv(v882, v880, v894)
    v896 = g.inp('_EnableParallaxMap', False, 0.0)
    v897 = g.math('GREATER_THAN', v896, 0.5)
    v898 = g.inp('_ParallaxMappingType', False, 0.0)
    v899 = g.math('LESS_THAN', v898, 0.5)
    v900 = g.math('MULTIPLY', v897, v899)
    v901 = g.inp('F9_NormalMap', True, (1.0, 1.0, 1.0))
    v902 = g.inp('F9_NormalMap_alpha', False, 1.0)
    v903 = g.inp('F10_MROMap', True, (1.0, 1.0, 1.0))
    v904 = g.inp('F10_MROMap_alpha', False, 1.0)
    v905 = g.sep(v901)
    v906 = g.inp('_UseParallaxMask', False, 0.0)
    v907 = g.math('COMPARE', v906, 0, 1e-05)
    v908 = g.math('SUBTRACT', 1.0, v907)
    v909 = g.math('MULTIPLY', v225, -1.0)
    v910 = g.math('ADD', v909, v902)
    v911 = g.math('MULTIPLY_ADD', v103, v910, v225)
    v912 = g.math('MULTIPLY', v911, -1.0)
    g.out_('F18_ParallaxMaskMap_uv', v14, True)
    v913 = g.inp('F18_ParallaxMaskMap', True, (1.0, 1.0, 1.0))
    v914 = g.inp('F18_ParallaxMaskMap_alpha', False, 1.0)
    v915 = g.sep(v913)
    v916 = g.math('MULTIPLY_ADD', v912, v38, v915[0])
    v917 = g.math('MULTIPLY', v911, v38)
    v918 = g.math('MULTIPLY_ADD', v906, v916, v917)
    v919 = g.inp('_ParallaxMaskChannel', False, 0.0)
    v920 = g.clampn(v919, 0, 1)
    v921 = g.math('MULTIPLY', v225, -1.0)
    v922 = g.math('ADD', v921, v905[2])
    v923 = g.math('MULTIPLY_ADD', v920, v922, v225)
    v924 = g.math('SUBTRACT', v919, 1)
    v925 = g.clampn(v924, 0, 1)
    v926 = g.math('MULTIPLY', v923, -1.0)
    v927 = g.math('ADD', v926, v902)
    v928 = g.math('MULTIPLY_ADD', v925, v927, v923)
    v929 = g.math('SUBTRACT', v919, 2)
    v930 = g.clampn(v929, 0, 1)
    v931 = g.math('MULTIPLY', v928, -1.0)
    v932 = g.math('ADD', v931, v904)
    v933 = g.math('MULTIPLY_ADD', v930, v932, v928)
    v934 = g.inp('_ParallaxMaskByLayerBlend', False, 0.0)
    v935 = g.math('MULTIPLY', v933, -1.0)
    v936 = g.math('MULTIPLY_ADD', v934, v935, v933)
    v937 = g.mixf(v908, v936, v918)
    v938 = g.math('LESS_THAN', 0.009999999776482582, v937)
    v939 = g.math('SUBTRACT', 1.0, v938)
    v940 = g.mixv(v939, (0.0, 0.0, 0.0), (0, 0, 0))
    v941 = g.mixf(v939, 0.0, 1.0)
    v942 = g.math('SUBTRACT', 1.0, v941)
    v943 = g.math('GREATER_THAN', v5, 0)
    v944 = g.mixf(v943, -1, 1)
    v945 = g.math('SUBTRACT', 1.0, v941)
    v946 = g.vmath('CROSS_PRODUCT', v21, v22)
    v947 = g.bc(v944)
    v948 = g.vmath('MULTIPLY', v947, v946)
    v949 = g.math('SUBTRACT', 1.0, v941)
    v950 = g.vmath('DOT_PRODUCT', v22, v28)
    v951 = g.math('SUBTRACT', 1.0, v941)
    v952 = g.vmath('DOT_PRODUCT', v948, v28)
    v953 = g.math('SUBTRACT', 1.0, v941)
    v954 = g.vmath('DOT_PRODUCT', v21, v28)
    v955 = g.math('SUBTRACT', 1.0, v941)
    v956 = g.comb(v950, v952, v954)
    v957 = g.comb(v950, v952, v954)
    v958 = g.vmath('DOT_PRODUCT', v956, v957)
    v959 = g.math('INVERSE_SQRT', v958, 0.0)
    v960 = g.math('SUBTRACT', 1.0, v941)
    v961 = g.inp('_ParallaxMapUVType', False, 0.0)
    v962 = g.comb(v961, v961, 0.0)
    v963 = g.vmath('SUBTRACT', v14, v0)
    v964 = g.vmath('MULTIPLY', v962, v963)
    v965 = g.vmath('ADD', v964, v0)
    v966 = g.math('SUBTRACT', 1.0, v941)
    v967 = g.inp('_GlobalMipBias', True, (0.0, 0.0, 0.0))
    v968 = g.sep(v967)
    v969 = g.comb(v968[1], v968[1], 0.0)
    v970 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), v969)
    v971 = g.math('SUBTRACT', 1.0, v941)
    v972 = g.comb(v968[1], v968[1], 0.0)
    v973 = g.vmath('MULTIPLY', (0.0, 0.0, 0.0), v972)
    v974 = g.math('SUBTRACT', 1.0, v941)
    v975 = g.inp('_ParallaxMarchNum', False, 3.0)
    v976 = g.math('MINIMUM', v975, 20)
    v977 = g.math('SUBTRACT', 1.0, v941)
    v978 = g.math('DIVIDE', 1, v976)
    v979 = g.math('SUBTRACT', 1.0, v941)
    v980 = g.math('MULTIPLY_ADD', v954, v959, 0.41999998688697815)
    v981 = g.math('SUBTRACT', 1.0, v941)
    v982 = g.math('MULTIPLY', v959, v954)
    v983 = g.math('MAXIMUM', v982, 0.0010000000474974513)
    v984 = g.math('SUBTRACT', 1.0, v941)
    v985 = g.math('MULTIPLY', v959, v950)
    v986 = g.math('DIVIDE', v985, v980)
    v987 = g.math('DIVIDE', v986, v983)
    v988 = g.inp('_ParallaxStrength', False, 0.0)
    v989 = g.math('MULTIPLY', v988, -1.0)
    v990 = g.math('MULTIPLY', v987, v989)
    v991 = g.math('MULTIPLY', v959, v952)
    v992 = g.math('DIVIDE', v991, v980)
    v993 = g.math('DIVIDE', v992, v983)
    v994 = g.math('MULTIPLY', v988, -1.0)
    v995 = g.math('MULTIPLY', v993, v994)
    v996 = g.comb(v990, v995, 0.0)
    v997 = g.math('SUBTRACT', 1.0, v941)
    v998 = g.comb(v978, v978, 0.0)
    v999 = g.vmath('MULTIPLY', v998, v996)
    v1000 = g.math('SUBTRACT', 1.0, v941)
    v1001 = g.math('SUBTRACT', 1, v978)
    v1002 = g.math('SUBTRACT', 1.0, v941)
    v1003 = g.math('SUBTRACT', 1.0, v941)
    v1004 = g.math('SUBTRACT', 1.0, v941)
    v1005 = g.math('SUBTRACT', 1.0, v941)
    v1006 = g.math('SUBTRACT', 1.0, v941)
    v1007 = g.math('SUBTRACT', 1.0, v941)
    v1008 = g.math('SUBTRACT', 1.0, v941)
    g.out_('Z0_it', 24, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_height', v1001, False)
    g.out_('Z0_s_heightPrev', 1, False)
    g.out_('Z0_s_iter', 0, False)
    g.out_('Z0_s_offCur', v999, True)
    g.out_('Z0_s_offPrev', (0, 0, 0.0), True)
    g.out_('Z0_s_texHit', 0, False)
    g.out_('Z0_s_texPrev', 0, False)
    g.out_('Z0_s_uvP', v965, True)
    g.out_('Z0_r___done', v941, False)
    g.out_('Z0_r_steps', v976, False)
    g.out_('Z0_r_stepH', v978, False)
    g.out_('Z0_r_stepUV', v999, True)
    v1009 = g.inp('Z0_o_Lloop0', False)
    v1010 = g.inp('Z0_o_height', False)
    v1011 = g.inp('Z0_o_heightPrev', False)
    v1012 = g.inp('Z0_o_iter', False)
    v1013 = g.inp('Z0_o_offCur', True)
    v1014 = g.inp('Z0_o_offPrev', True)
    v1015 = g.inp('Z0_o_texHit', False)
    v1016 = g.inp('Z0_o_texPrev', False)
    v1017 = g.inp('Z0_o_uvP', True)
    v1018 = g.mixv(v1008, v965, v1017)
    v1019 = g.mixf(v1008, v1001, v1010)
    v1020 = g.mixv(v1008, v999, v1013)
    v1021 = g.mixv(v1008, (0, 0, 0.0), v1014)
    v1022 = g.mixf(v1008, 0, v1016)
    v1023 = g.mixf(v1008, 1, v1011)
    v1024 = g.mixf(v1008, 0, v1015)
    v1025 = g.mixf(v1008, 0, v1012)
    v1026 = g.math('SUBTRACT', 1.0, v941)
    v1027 = g.math('SUBTRACT', v1022, v1023)
    v1028 = g.math('MULTIPLY', v1023, -1.0)
    v1029 = g.math('SUBTRACT', v1022, v1024)
    v1030 = g.math('ADD', v1019, v1029)
    v1031 = g.math('ADD', v1028, v1030)
    v1032 = g.math('DIVIDE', v1027, v1031)
    v1033 = g.math('SUBTRACT', 1.0, v941)
    v1034 = g.comb(v1032, v1032, 0.0)
    v1035 = g.vmath('MULTIPLY', v999, v1034)
    v1036 = g.vmath('ADD', v1035, v1021)
    v1037 = g.vmath('ADD', v1018, v1036)
    v1038 = g.inp('_ParallaxTilling', False, 1.0)
    v1039 = g.comb(v1038, v1038, 0.0)
    v1040 = g.vmath('MULTIPLY', v1037, v1039)
    v1041 = g.math('SUBTRACT', 1.0, v941)
    g.out_('F19_ParallaxMap_uv', v1040, True)
    v1042 = g.inp('F19_ParallaxMap', True, (1.0, 1.0, 1.0))
    v1043 = g.inp('F19_ParallaxMap_alpha', False, 1.0)
    v1044 = g.math('SUBTRACT', 1.0, v941)
    v1045 = g.sep(v1042)
    v1046 = g.inp('_ParallaxColor', True, (0.0, 0.0, 0.0))
    v1047 = g.inp('_ParallaxColor_w', False, 1.0)
    v1048 = g.sep(v1046)
    v1049 = g.inp('_ParallaxColorDark', True, (0.0, 0.0, 0.0))
    v1050 = g.inp('_ParallaxColorDark_w', False, 1.0)
    v1051 = g.sep(v1049)
    v1052 = g.math('SUBTRACT', v1048[0], v1051[0])
    v1053 = g.math('MULTIPLY_ADD', v1045[1], v1052, v1051[0])
    v1054 = g.math('SUBTRACT', v1048[1], v1051[1])
    v1055 = g.math('MULTIPLY_ADD', v1045[1], v1054, v1051[1])
    v1056 = g.math('SUBTRACT', v1048[2], v1051[2])
    v1057 = g.math('MULTIPLY_ADD', v1045[1], v1056, v1051[2])
    v1058 = g.comb(v1053, v1055, v1057)
    v1059 = g.math('SUBTRACT', 1.0, v941)
    v1060 = g.vmath('DOT_PRODUCT', v28, v667)
    v1061 = g.clampn(v1060, 0, 1)
    v1062 = g.math('MAXIMUM', v1061, 0.0010000000474974513)
    v1063 = g.math('LOGARITHM', v1062, 2.0)
    v1064 = g.inp('_ParallaxFresnelStrength', False, 0.0)
    v1065 = g.math('FLOOR', v1064, 0.0)
    v1066 = g.math('MULTIPLY', v1063, v1065)
    v1067 = g.math('POWER', 2.0, v1066)
    v1068 = g.math('MULTIPLY', v937, v937)
    v1069 = g.math('MULTIPLY', v1067, v1068)
    v1070 = g.math('SUBTRACT', 1.0, v941)
    v1071 = g.inp('_VFXParams0', True, (0.0, 0.0, 0.0))
    v1072 = g.inp('_VFXParams0_w', False, 0.0)
    v1073 = g.vmath('SUBTRACT', v20, v1071)
    v1074 = g.math('SUBTRACT', 1.0, v941)
    v1075 = g.vmath('DOT_PRODUCT', v1073, v1073)
    v1076 = g.math('SQRT', v1075, 0.0)
    v1077 = g.inp('_ParallaxBrightOuterRadius', False, 0.0)
    v1078 = g.math('SUBTRACT', v1076, v1077)
    v1079 = g.math('MULTIPLY', v1077, -1.0)
    v1080 = g.inp('_ParallaxBrightInnerRadius', False, 0.0)
    v1081 = g.math('ADD', v1079, v1080)
    v1082 = g.math('DIVIDE', 1, v1081)
    v1083 = g.math('MULTIPLY', v1078, v1082)
    v1084 = g.clampn(v1083, 0, 1)
    v1085 = g.math('SUBTRACT', 1.0, v941)
    v1086 = g.math('MULTIPLY', v1084, v1084)
    v1087 = g.math('MULTIPLY_ADD', v1084, -2, 3)
    v1088 = g.math('MULTIPLY', v1086, v1087)
    v1089 = g.inp('_ParallaxBrightStrength', False, 0.0)
    v1090 = g.math('MULTIPLY', v1088, v1089)
    v1091 = g.math('SUBTRACT', 1.0, v941)
    v1092 = g.inp('_ParallaxCharPos', False, 0.0)
    v1093 = g.math('COMPARE', v1092, 0, 1e-05)
    v1094 = g.math('SUBTRACT', 1.0, v1093)
    v1095 = g.mixf(v1094, 0, v1090)
    v1096 = g.mixf(v1091, v1090, v1095)
    v1097 = g.math('SUBTRACT', 1.0, v941)
    v1098 = g.inp('_VFXParams2', True, (0.0, 0.0, 0.0))
    v1099 = g.inp('_VFXParams2_w', False, 0.0)
    v1100 = g.sep(v1098)
    v1101 = g.math('SUBTRACT', v442[0], v1100[0])
    v1102 = g.math('SUBTRACT', v442[2], v1100[1])
    v1103 = g.comb(v1101, v1102, 0.0)
    v1104 = g.math('SUBTRACT', 1.0, v941)
    v1105 = g.math('MULTIPLY', v1100[2], -1.0)
    v1106 = g.math('DIVIDE', 1, v1105)
    v1107 = g.vmath('DOT_PRODUCT', v1103, v1103)
    v1108 = g.math('SQRT', v1107, 0.0)
    v1109 = g.math('SUBTRACT', v1108, v1100[2])
    v1110 = g.math('MULTIPLY', v1106, v1109)
    v1111 = g.clampn(v1110, 0, 1)
    v1112 = g.math('SUBTRACT', 1.0, v941)
    v1113 = g.inp('_ParallaxMinBrightness', False, 0.0)
    v1114 = g.math('SUBTRACT', 1, v1113)
    v1115 = g.math('SUBTRACT', 1.0, v941)
    v1116 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1117 = g.sep(v1116)
    v1118 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1119 = g.sep(v1118)
    v1120 = g.math('ADD', v1117[1], v1119[0])
    v1121 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v1122 = g.sep(v1121)
    v1123 = g.math('ADD', v1120, v1122[2])
    v1124 = g.math('SUBTRACT', 1.0, v941)
    v1125 = g.math('MULTIPLY', v1111, v1111)
    v1126 = g.math('MULTIPLY_ADD', v1111, -2, 3)
    v1127 = g.math('MULTIPLY', v1125, v1126)
    v1128 = g.math('ADD', 1, v1113)
    v1129 = g.math('DIVIDE', v1128, v1114)
    v1130 = g.inp('_ParallaxAnimSpeed', False, 0.0)
    v1131 = g.math('MULTIPLY', v687[1], v1130)
    v1132 = g.inp('_ParallaxAnimRandom', False, 0.0)
    v1133 = g.math('MULTIPLY', v1123, v1132)
    v1134 = g.math('MULTIPLY_ADD', v1131, 0.05000000074505806, v1133)
    v1135 = g.math('COSINE', v1134, 0.0)
    v1136 = g.math('ADD', v1129, v1135)
    v1137 = g.math('MULTIPLY', v1114, v1136)
    v1138 = g.math('MULTIPLY_ADD', v1137, 0.5, v1096)
    v1139 = g.math('MULTIPLY_ADD', v1127, v1099, v1138)
    v1140 = g.math('SUBTRACT', 1.0, v941)
    v1141 = g.bc(v1069)
    v1142 = g.vmath('MULTIPLY', v1141, v1058)
    v1143 = g.bc(v1139)
    v1144 = g.vmath('MULTIPLY', v1143, v1142)
    v1145 = g.vmath('MAXIMUM', v1144, (0, 0, 0))
    v1146 = g.vmath('MINIMUM', v1145, (1000, 1000, 1000))
    v1147 = g.math('SUBTRACT', 1.0, v941)
    v1148 = g.inp('_UseWorldSpaceParallaxMask', False, 0.0)
    v1149 = g.math('GREATER_THAN', v1148, 0.5)
    v1150 = g.math('SUBTRACT', 1.0, v941)
    v1151 = g.inp('_MaskWorldPosParams', True, (0.0, 0.0, 0.0))
    v1152 = g.inp('_MaskWorldPosParams_w', False, 0.0)
    v1153 = g.sep(v1151)
    v1154 = g.math('SUBTRACT', v442[0], v1153[0])
    v1155 = g.math('SUBTRACT', v442[2], v1153[2])
    v1156 = g.comb(v1154, v1155, 0.0)
    v1157 = g.math('SUBTRACT', 1.0, v941)
    v1158 = g.math('MULTIPLY', 0.01745329238474369, v1153[1])
    v1159 = g.math('SUBTRACT', 1.0, v941)
    v1160 = g.math('SINE', v1158, 0.0)
    v1161 = g.math('SUBTRACT', 1.0, v941)
    v1162 = g.math('COSINE', v1158, 0.0)
    v1163 = g.math('SUBTRACT', 1.0, v941)
    v1164 = g.math('MAXIMUM', 0.10000000149011612, v1152)
    v1165 = g.math('SUBTRACT', 1.0, v941)
    v1166 = g.comb(v1164, v1164, 0.0)
    v1167 = g.vmath('DIVIDE', v1156, v1166)
    v1168 = g.math('SUBTRACT', 1.0, v941)
    v1169 = g.comb(v1162, v1160, 0.0)
    v1170 = g.vmath('DOT_PRODUCT', v1167, v1169)
    v1171 = g.math('ADD', v1170, 0.5)
    v1172 = g.math('MULTIPLY_ADD', v1045[0], v1113, v1171)
    v1173 = g.math('MULTIPLY', v1160, -1.0)
    v1174 = g.comb(v1173, v1162, 0.0)
    v1175 = g.vmath('DOT_PRODUCT', v1167, v1174)
    v1176 = g.math('ADD', v1175, 0.5)
    v1177 = g.math('MULTIPLY_ADD', v1045[1], v1113, v1176)
    v1178 = g.comb(v1172, v1177, 0.0)
    g.out_('F20_ParallaxMaskMap_uv', v1178, True)
    v1179 = g.inp('F20_ParallaxMaskMap', True, (1.0, 1.0, 1.0))
    v1180 = g.inp('F20_ParallaxMaskMap_alpha', False, 1.0)
    v1181 = g.math('SUBTRACT', 1.0, v941)
    v1182 = g.inp('_ParallaxMaskMapColorStrength', False, 0.0)
    v1183 = g.bc(v1182)
    v1184 = g.vmath('MULTIPLY', v1179, v1183)
    v1185 = g.vmath('MULTIPLY', v1146, v1184)
    v1186 = g.mixv(v1181, v1146, v1185)
    v1187 = g.math('SUBTRACT', 1.0, v941)
    v1188 = g.inp('_ParallaxSignControl', False, 0.0)
    v1189 = g.math('TRUNC', v1188, 0.0)
    v1190 = g.math('TRUNC', v1189, 0.0)
    v1191 = g.math('SUBTRACT', 1.0, v941)
    v1192 = g.clampn(v1180, 0, 1)
    v1193 = g.math('SUBTRACT', 1.0, v941)
    v1194 = g.math('SUBTRACT', v1180, 0.20000000298023224)
    v1195 = g.clampn(v1194, 0, 1)
    v1196 = g.math('SUBTRACT', 1.0, v941)
    v1197 = g.math('SUBTRACT', v1180, 0.4000000059604645)
    v1198 = g.clampn(v1197, 0, 1)
    v1199 = g.math('SUBTRACT', 1.0, v941)
    v1200 = g.math('SUBTRACT', v1180, 0.6000000238418579)
    v1201 = g.clampn(v1200, 0, 1)
    v1202 = g.math('SUBTRACT', 1.0, v941)
    v1203 = g.math('SUBTRACT', v1180, 0.800000011920929)
    v1204 = g.clampn(v1203, 0, 1)
    v1205 = g.math('SUBTRACT', 1.0, v941)
    v1206 = g.math('LESS_THAN', 0.18000000715255737, v1192)
    v1207 = g.math('SUBTRACT', 1.0, v1206)
    v1208 = g.math('MULTIPLY', v1192, 5)
    v1209 = g.mixf(v1207, 0, v1208)
    v1210 = g.math('MODULO', v1190, 2)
    v1211 = g.math('TRUNC', v1210, 0.0)
    v1212 = g.math('MULTIPLY', v1209, v1211)
    v1213 = g.inp('_ParallaxSignLerpFactor0', True, (0.0, 0.0, 0.0))
    v1214 = g.inp('_ParallaxSignLerpFactor0_w', False, 0.0)
    v1215 = g.sep(v1213)
    v1216 = g.math('MULTIPLY', v1212, v1215[0])
    v1217 = g.math('LESS_THAN', 0.18000000715255737, v1195)
    v1218 = g.math('SUBTRACT', 1.0, v1217)
    v1219 = g.math('MULTIPLY', v1195, 5)
    v1220 = g.mixf(v1218, 0, v1219)
    v1221 = g.math('DIVIDE', v1190, 2)
    v1222 = g.math('FLOOR', v1221, 0.0)
    v1223 = g.math('MODULO', v1222, 2)
    v1224 = g.math('TRUNC', v1223, 0.0)
    v1225 = g.math('MULTIPLY', v1220, v1224)
    v1226 = g.math('MULTIPLY', v1225, v1215[1])
    v1227 = g.math('ADD', v1216, v1226)
    v1228 = g.math('LESS_THAN', 0.18000000715255737, v1198)
    v1229 = g.math('SUBTRACT', 1.0, v1228)
    v1230 = g.math('MULTIPLY', v1198, 5)
    v1231 = g.mixf(v1229, 0, v1230)
    v1232 = g.math('DIVIDE', v1190, 4)
    v1233 = g.math('FLOOR', v1232, 0.0)
    v1234 = g.math('MODULO', v1233, 2)
    v1235 = g.math('TRUNC', v1234, 0.0)
    v1236 = g.math('MULTIPLY', v1231, v1235)
    v1237 = g.math('MULTIPLY', v1236, v1215[2])
    v1238 = g.math('ADD', v1227, v1237)
    v1239 = g.math('LESS_THAN', 0.18000000715255737, v1201)
    v1240 = g.math('SUBTRACT', 1.0, v1239)
    v1241 = g.math('MULTIPLY', v1201, 5)
    v1242 = g.mixf(v1240, 0, v1241)
    v1243 = g.math('DIVIDE', v1190, 8)
    v1244 = g.math('FLOOR', v1243, 0.0)
    v1245 = g.math('MODULO', v1244, 2)
    v1246 = g.math('TRUNC', v1245, 0.0)
    v1247 = g.math('MULTIPLY', v1242, v1246)
    v1248 = g.math('MULTIPLY', v1247, v1214)
    v1249 = g.math('ADD', v1238, v1248)
    v1250 = g.math('LESS_THAN', 0.18000000715255737, v1204)
    v1251 = g.math('SUBTRACT', 1.0, v1250)
    v1252 = g.math('MULTIPLY', v1204, 5)
    v1253 = g.mixf(v1251, 0, v1252)
    v1254 = g.math('DIVIDE', v1190, 16)
    v1255 = g.math('FLOOR', v1254, 0.0)
    v1256 = g.math('MODULO', v1255, 2)
    v1257 = g.math('TRUNC', v1256, 0.0)
    v1258 = g.math('MULTIPLY', v1253, v1257)
    v1259 = g.inp('_ParallaxSignLerpFactor2', False, 0.0)
    v1260 = g.math('MULTIPLY', v1258, v1259)
    v1261 = g.math('ADD', v1249, v1260)
    v1262 = g.math('SUBTRACT', 1.0, v941)
    v1263 = g.vmath('DOT_PRODUCT', v1156, v1156)
    v1264 = g.math('SQRT', v1263, 0.0)
    v1265 = g.math('MULTIPLY_ADD', v1045[0], 20, v1264)
    v1266 = g.inp('_ParallaxLerpSchedule', False, 0.0)
    v1267 = g.math('SUBTRACT', v1265, v1266)
    v1268 = g.clampn(v1267, 0, 1)
    v1269 = g.math('SUBTRACT', 1.0, v941)
    v1270 = g.math('MULTIPLY_ADD', v1261, v479[0], v1268)
    v1271 = g.clampn(v1270, 0, 1)
    v1272 = g.math('SUBTRACT', 1.0, v941)
    v1273 = g.inp('_ParallaxPatternColorDark', True, (0.0, 0.0, 0.0))
    v1274 = g.inp('_ParallaxPatternColorDark_w', False, 0.0)
    v1275 = g.vmath('MULTIPLY', v1186, v1273)
    v1276 = g.math('SUBTRACT', 1.0, v941)
    v1277 = g.inp('_ParallaxPatternColor', True, (0.0, 0.0, 0.0))
    v1278 = g.inp('_ParallaxPatternColor_w', False, 0.0)
    v1279 = g.vmath('MULTIPLY', v1186, v1277)
    v1280 = g.math('SUBTRACT', 1.0, v941)
    v1281 = g.mixv(v1271, v1275, v1279)
    v1282 = g.mixv(v1280, v1186, v1281)
    v1283 = g.math('SUBTRACT', 1.0, v941)
    v1284 = g.inp('_ParallaxSignLerpFactor1', True, (0.0, 0.0, 0.0))
    v1285 = g.inp('_ParallaxSignLerpFactor1_w', False, 0.0)
    v1286 = g.clampn(v1285, 0, 1)
    v1287 = g.math('MULTIPLY', v225, -1.0)
    v1288 = g.math('MULTIPLY', v442[1], -1.0)
    v1289 = g.math('ADD', v1288, v1285)
    v1290 = g.clampn(v1289, 0, 1)
    v1291 = g.math('ADD', v1287, v1290)
    v1292 = g.math('MULTIPLY_ADD', v1286, v1291, v225)
    v1293 = g.math('SUBTRACT', 1.0, v941)
    v1294 = g.inp('_WorldParallaxAdditionalLightMaskChannel', False, 0.0)
    v1295 = g.clampn(v1294, 0, 1)
    v1296 = g.math('MULTIPLY', v225, -1.0)
    v1297 = g.math('ADD', v1296, v905[2])
    v1298 = g.math('MULTIPLY_ADD', v1295, v1297, v225)
    v1299 = g.math('SUBTRACT', 1.0, v941)
    v1300 = g.math('SUBTRACT', v1294, 1)
    v1301 = g.clampn(v1300, 0, 1)
    v1302 = g.math('MULTIPLY', v1298, -1.0)
    v1303 = g.math('ADD', v1302, v902)
    v1304 = g.math('MULTIPLY_ADD', v1301, v1303, v1298)
    v1305 = g.mixf(v1299, v1298, v1304)
    v1306 = g.math('SUBTRACT', 1.0, v941)
    v1307 = g.math('SUBTRACT', v1294, 2)
    v1308 = g.clampn(v1307, 0, 1)
    v1309 = g.math('MULTIPLY', v1305, -1.0)
    v1310 = g.math('ADD', v1309, v904)
    v1311 = g.math('MULTIPLY_ADD', v1308, v1310, v1305)
    v1312 = g.mixf(v1306, v1305, v1311)
    v1313 = g.math('SUBTRACT', 1.0, v941)
    v1314 = g.sep(v1284)
    v1315 = g.math('SUBTRACT', v442[1], v1314[1])
    v1316 = g.clampn(v1315, 0, 1)
    v1317 = g.math('SUBTRACT', 1.0, v941)
    v1318 = g.vmath('ADD', v1282, (0.30000001192092896, 0.30000001192092896, 0.30000001192092896))
    v1319 = g.inp('_WorldParallaxAdditionalColor', True, (0.0, 0.0, 0.0))
    v1320 = g.inp('_WorldParallaxAdditionalColor_w', False, 0.0)
    v1321 = g.vmath('MULTIPLY', v1318, v1319)
    v1322 = g.bc(v1316)
    v1323 = g.vmath('MULTIPLY', v1322, v1321)
    v1324 = g.bc(v1312)
    v1325 = g.vmath('MULTIPLY', v1324, v1323)
    v1326 = g.math('SUBTRACT', 1.0, v941)
    v1327 = g.inp('_ParallaxIntensity', False, 0.0)
    v1328 = g.math('MULTIPLY', v1292, v1327)
    v1329 = g.math('MULTIPLY', v1292, v1327)
    v1330 = g.math('MULTIPLY', v1292, v1327)
    v1331 = g.comb(v1328, v1329, v1330)
    v1332 = g.vmath('MULTIPLY', v1282, v1331)
    v1333 = g.vmath('ADD', v1332, v1325)
    v1334 = g.mixv(v1326, v940, v1333)
    v1335 = g.mixf(v1326, v941, 1.0)
    v1336 = g.mixv(v1149, v940, v1334)
    v1337 = g.mixf(v1149, v941, v1335)
    v1338 = g.mixv(v1149, v1146, v1282)
    v1339 = g.mixv(v1147, v940, v1336)
    v1340 = g.mixf(v1147, v941, v1337)
    v1341 = g.mixv(v1147, v1146, v1338)
    v1342 = g.math('SUBTRACT', 1.0, v1340)
    v1343 = g.bc(v1327)
    v1344 = g.vmath('MULTIPLY', v1341, v1343)
    v1345 = g.mixv(v1342, v1339, v1344)
    v1346 = g.mixf(v1342, v1340, 1.0)
    v1347 = g.vmath('ADD', v895, v1345)
    v1348 = g.mixv(v900, v895, v1347)
    v1349 = g.comb(v230, v230, v230)
    v1350 = g.math('SUBTRACT', 1, v664)
    v1351 = g.vmath('NORMALIZE', v667)
    v1352 = g.vmath('DOT_PRODUCT', v1351, v28)
    v1353 = g.math('MAXIMUM', v1352, 0)
    v1354 = g.math('MULTIPLY', 0.08, v230)
    v1355 = g.math('MULTIPLY', 0.08, v230)
    v1356 = g.math('MULTIPLY', 0.08, v230)
    v1357 = g.comb(v1354, v1355, v1356)
    v1358 = g.mixv(v665, v1357, v663)
    v1359 = g.math('SUBTRACT', 1, v665)
    v1360 = g.bc(v1359)
    v1361 = g.vmath('MULTIPLY', v663, v1360)
    v1362 = g.inp('_UseThinFilm', False, 0.0)
    v1363 = g.math('GREATER_THAN', v1362, 0.5)
    v1364 = g.inp('_ThinFilmIOR', False, 1.4)
    v1365 = g.inp('_ThinFilmThickness', False, 0.5)
    v1366 = g.math('MULTIPLY', v1365, 1000)
    v1367 = g.inp('M_PI', False, 0.0)
    v1368 = g.group_named('RCE_RuriEvalIridescence', [('outsideIor', 1), ('eta2', v1364), ('cosTheta1', v1353), ('iridescenceThickness', v1366), ('baseF0', v1358), ('M_PI', v1367)])
    v1369 = g.inp('_ThinFilmWeight', False, 0.0)
    v1370 = g.inp('_ThinFilmIntensity', False, 1.0)
    v1371 = g.math('MULTIPLY', v1369, v1370)
    v1372 = g.clampn(v1371)
    v1373 = g.mixv(v1372, v1358, v1368[0])
    v1374 = g.mixv(v1363, v1358, v1373)
    v1375 = g.inp('_SubsurfaceShadingMode', False, 0.0)
    v1376 = g.math('LESS_THAN', v1375, 0.5)
    v1377 = g.inp('_SubsurfaceColor', True, (0.8, 0.8, 0.8))
    v1378 = g.inp('_SubsurfaceColor_w', False, 1.0)
    v1379 = g.vmath('MULTIPLY', v1377, v663)
    v1380 = g.mixv(v1376, v1379, v1377)
    v1381 = g.inp('_MaxSubsurfaceThickness', False, 1.0)
    v1382 = g.inp('_UseSubsurfaceThicknessMap', False, 0.0)
    v1383 = g.math('GREATER_THAN', v1382, 0.5)
    v1384 = g.inp('_MinSubsurfaceThickness', False, 0.0)
    g.out_('F21_SubsurfaceMap_uv', v0, True)
    v1385 = g.inp('F21_SubsurfaceMap', True, (1.0, 1.0, 1.0))
    v1386 = g.inp('F21_SubsurfaceMap_alpha', False, 1.0)
    v1387 = g.sep(v1385)
    v1388 = g.mixf(v1387[0], v1384, v1381)
    v1389 = g.mixf(v1383, v1381, v1388)
    v1390 = g.group_named('RCE_HgEnvBRDF', [('roughness', v664), ('NoV', v1353), ('f0', v1374)])
    v1391 = g.vmath('SCALE', v28, s=-1.0)
    v1392 = g.vmath('DOT_PRODUCT', v1351, v1391)
    v1393 = g.math('MULTIPLY', 2.0, v1392)
    v1394 = g.vmath('SCALE', v1351, s=v1393)
    v1395 = g.vmath('SUBTRACT', v1391, v1394)
    v1396 = g.inp('_UseCustomIBL', False, 0.0)
    v1397 = g.math('GREATER_THAN', v1396, 0.5)
    v1398 = g.math('MULTIPLY', 0.7, v664)
    v1399 = g.math('SUBTRACT', 1.7, v1398)
    v1400 = g.math('MULTIPLY', v664, v1399)
    v1401 = g.math('MULTIPLY', v1400, 6)
    v1402 = g.u2b(v1395)
    g.out_('F22_IBL_CustomIBL_dir', v1402, True)
    g.out_('F22_IBL_CustomIBL_mip', v1401, False)
    v1403 = g.inp('F22_IBL_CustomIBL', True, (0.2159, 0.2159, 0.2159))
    v1404 = g.inp('F22_IBL_CustomIBL_alpha', False, 1.0)
    v1405 = g.inp('_CustomIBLIntensity', False, 1.0)
    v1406 = g.bc(v1405)
    v1407 = g.vmath('MULTIPLY', v1403, v1406)
    g.out_('C1_SpecularRadiance_direction', v1395, True)
    g.out_('C1_SpecularRadiance_position', v20, True)
    g.out_('C1_SpecularRadiance_roughness', v664, False)
    v1408 = g.inp('C1_SpecularRadiance', True, (0.2159, 0.2159, 0.2159))
    v1409 = g.mixv(v1397, v1408, v1407)
    v1410 = g.inp('_PlanarReflection', False, 0.0)
    v1411 = g.math('GREATER_THAN', v1410, 0.5)
    g.out_('F23_PlanarReflectionTexture_uv', v29, True)
    v1412 = g.inp('F23_PlanarReflectionTexture', True, (1.0, 1.0, 1.0))
    v1413 = g.inp('F23_PlanarReflectionTexture_alpha', False, 1.0)
    v1414 = g.inp('_PlanarReflectionTint', True, (1.0, 1.0, 1.0))
    v1415 = g.inp('_PlanarReflectionTint_w', False, 1.0)
    v1416 = g.vmath('MULTIPLY', v1412, v1414)
    v1417 = g.mixv(v1415, v1409, v1416)
    v1418 = g.mixv(v1411, v1409, v1417)
    v1419 = g.inp('_EnableSubsurface', False, 0.0)
    v1420 = g.math('GREATER_THAN', v1419, 0.5)
    v1421 = g.inp('_SubsurfaceIndirect', False, 1.0)
    v1422 = g.comb(v1421, v1421, v1421)
    v1423 = g.vmath('MULTIPLY', v1380, v1422)
    v1424 = g.vmath('ADD', v1423, v1361)
    v1425 = g.mixv(v1420, v1361, v1424)
    v1426 = g.vmath('MULTIPLY', v1425, v30)
    v1427 = g.bc(v666)
    v1428 = g.vmath('MULTIPLY', v1426, v1427)
    v1429 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v1430 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v1431 = g.sep(v1429)
    v1432 = g.bc(v1431[0])
    v1433 = g.vmath('MULTIPLY', v1428, v1432)
    v1434 = g.comb(v1390[0], v1390[0], v1390[0])
    v1435 = g.comb(v1390[1], v1390[1], v1390[1])
    v1436 = g.vmath('MULTIPLY', v1374, v1434)
    v1437 = g.vmath('ADD', v1436, v1435)
    v1438 = g.vmath('MULTIPLY', v1437, v1418)
    v1439 = g.bc(v1431[1])
    v1440 = g.vmath('MULTIPLY', v1438, v1439)
    v1441 = g.vmath('ADD', v1433, v1440)
    v1442 = g.inp('C2_MainLight_direction', True, (0.0, 0.0, 0.0))
    v1443 = g.inp('C2_MainLight_color', True, (0.0, 0.0, 0.0))
    v1444 = g.inp('C2_MainLight_distanceAttenuation', False, 0.0)
    v1445 = g.inp('C2_MainLight_shadowAttenuation', False, 0.0)
    v1446 = g.inp('C2_MainLight_layerMask', False, 0.0)
    v1447 = g.inp('_MainLightOcclusionProbes', True, (0.0, 0.0, 0.0))
    v1448 = g.inp('_MainLightOcclusionProbes_w', False, 0.0)
    v1449 = g.vmath('ADD', v1442, v28)
    v1450 = g.vmath('DOT_PRODUCT', v1449, v1449)
    v1451 = g.math('MAXIMUM', v1450, 1E-08)
    v1452 = g.math('INVERSE_SQRT', v1451, 0.0)
    v1453 = g.bc(v1452)
    v1454 = g.vmath('MULTIPLY', v1449, v1453)
    v1455 = g.vmath('DOT_PRODUCT', v1442, v1351)
    v1456 = g.clampn(v1455)
    v1457 = g.vmath('DOT_PRODUCT', v1351, v1454)
    v1458 = g.clampn(v1457)
    v1459 = g.vmath('DOT_PRODUCT', v28, v1454)
    v1460 = g.clampn(v1459)
    v1461 = g.group_named('RCE_HgDirectLightEnergy', [('roughness', v664), ('f0', v1374), ('NoL', v1456), ('NoH', v1458), ('NoV', v1353), ('VoH', v1460)])
    v1462 = g.math('MULTIPLY', v1444, 1.0)
    v1463 = g.comb(v1456, v1456, v1456)
    v1464 = g.bc(v1456)
    v1465 = g.vmath('MULTIPLY', v1361, v1464)
    v1466 = g.vmath('MULTIPLY', v1461[0], v1463)
    v1467 = g.vmath('ADD', v1466, v1465)
    v1468 = g.math('GREATER_THAN', v1419, 0.5)
    v1469 = g.vmath('DOT_PRODUCT', v1442, v1351)
    v1470 = g.vmath('DOT_PRODUCT', v28, v1442)
    v1471 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v1472 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v1473 = g.group_named('RCE_HgSssLobe', [('amount', v1389), ('rawNoL', v1469), ('VdotL', v1470), ('selfShadowBias', v1471), ('enableSelfShadowBias', v1472)])
    v1474 = g.bc(v1473[0])
    v1475 = g.vmath('MULTIPLY', v1474, v1380)
    v1476 = g.vmath('ADD', v1467, v1475)
    v1477 = g.mixv(v1468, v1467, v1476)
    v1478 = g.bc(v1462)
    v1479 = g.vmath('MULTIPLY', v1443, v1478)
    v1480 = g.vmath('MULTIPLY', v1477, v1479)
    v1481 = g.vmath('ADD', v1441, v1480)
    v1482 = g.inp('C3_AdditionalLightCount', False, 0.0)
    v1483 = g.math('SUBTRACT', v1482, 0)
    v1484 = g.math('CEIL', v1483, 0.0)
    v1485 = g.math('MAXIMUM', v1484, 0.0)
    g.out_('Z1_it', v1485, False)
    g.out_('Z1_s_H', v1454, True)
    g.out_('Z1_s_L', v1442, True)
    g.out_('Z1_s_LV', v1449, True)
    g.out_('Z1_s_N', v1351, True)
    g.out_('Z1_s_NoH', v1458, False)
    g.out_('Z1_s_NoL', v1456, False)
    g.out_('Z1_s_NoV', v1353, False)
    g.out_('Z1_s_P', v20, True)
    g.out_('Z1_s_V', v28, True)
    g.out_('Z1_s_VoH', v1460, False)
    g.out_('Z1_s_Lloop0', 1.0, False)
    g.out_('Z1_s_color', v1481, True)
    g.out_('Z1_s_energy', v1461[0], True)
    g.out_('Z1_s_f0', v1374, True)
    g.out_('Z1_s_inputData_bakedGI', v30, True)
    g.out_('Z1_s_inputData_fogCoord', 0, False)
    g.out_('Z1_s_inputData_normalWS', v667, True)
    g.out_('Z1_s_inputData_normalizedScreenSpaceUV', v29, True)
    g.out_('Z1_s_inputData_positionCS', v17, True)
    g.out_('Z1_s_inputData_positionCS_w', v18, False)
    g.out_('Z1_s_inputData_positionWS', v20, True)
    g.out_('Z1_s_inputData_shadowCoord', (0.0, 0.0, 0.0), True)
    g.out_('Z1_s_inputData_shadowCoord_w', 0.0, False)
    g.out_('Z1_s_inputData_shadowMask', (1, 1, 1), True)
    g.out_('Z1_s_inputData_shadowMask_w', 1, False)
    g.out_('Z1_s_inputData_vertexLighting', (0, 0, 0), True)
    g.out_('Z1_s_inputData_viewDirectionWS', v28, True)
    g.out_('Z1_s_lightIndex', 0, False)
    g.out_('Z1_s_roughness', v664, False)
    g.out_('Z1_s_sssAmount', v1389, False)
    g.out_('Z1_r___done', 0.0, False)
    g.out_('Z1_r_diffuse', v1361, True)
    g.out_('Z1_r_sssTint', v1380, True)
    g.out_('Z1_r_pixelLightCount', v1482, False)
    v1486 = g.inp('Z1_o_H', True)
    v1487 = g.inp('Z1_o_L', True)
    v1488 = g.inp('Z1_o_LV', True)
    v1489 = g.inp('Z1_o_N', True)
    v1490 = g.inp('Z1_o_NoH', False)
    v1491 = g.inp('Z1_o_NoL', False)
    v1492 = g.inp('Z1_o_NoV', False)
    v1493 = g.inp('Z1_o_P', True)
    v1494 = g.inp('Z1_o_V', True)
    v1495 = g.inp('Z1_o_VoH', False)
    v1496 = g.inp('Z1_o_Lloop0', False)
    v1497 = g.inp('Z1_o_color', True)
    v1498 = g.inp('Z1_o_energy', True)
    v1499 = g.inp('Z1_o_f0', True)
    v1500 = g.inp('Z1_o_inputData_bakedGI', True)
    v1501 = g.inp('Z1_o_inputData_fogCoord', False)
    v1502 = g.inp('Z1_o_inputData_normalWS', True)
    v1503 = g.inp('Z1_o_inputData_normalizedScreenSpaceUV', True)
    v1504 = g.inp('Z1_o_inputData_positionCS', True)
    v1505 = g.inp('Z1_o_inputData_positionCS_w', False)
    v1506 = g.inp('Z1_o_inputData_positionWS', True)
    v1507 = g.inp('Z1_o_inputData_shadowCoord', True)
    v1508 = g.inp('Z1_o_inputData_shadowCoord_w', False)
    v1509 = g.inp('Z1_o_inputData_shadowMask', True)
    v1510 = g.inp('Z1_o_inputData_shadowMask_w', False)
    v1511 = g.inp('Z1_o_inputData_vertexLighting', True)
    v1512 = g.inp('Z1_o_inputData_viewDirectionWS', True)
    v1513 = g.inp('Z1_o_lightIndex', False)
    v1514 = g.inp('Z1_o_roughness', False)
    v1515 = g.inp('Z1_o_sssAmount', False)
    v1516 = g.vmath('ADD', v1497, v1348)
    v1517 = g.math('COMPARE', v60, 0, 1e-05)
    v1518 = g.math('SUBTRACT', 1.0, v1517)
    v1519 = g.vmath('SUBTRACT', v20, v26)
    v1520 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v1521 = g.bc(v1520)
    v1522 = g.vmath('DIVIDE', v1519, v1521)
    v1523 = g.vmath('ADD', v663, v1516)
    v1524 = g.vmath('LENGTH', v1522)
    v1525 = g.sep(v1522)
    v1526 = g.comb(v1525[0], v1525[2], 0.0)
    v1527 = g.vmath('LENGTH', v1526)
    v1528 = g.math('ABSOLUTE', v1525[1], 0.0)
    v1529 = g.math('MAXIMUM', v1527, v1528)
    v1530 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v1531 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v1532 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v1533 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v1534 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v1535 = g.inp('_RuriFogColor_w', False, 0.0)
    v1536 = g.group_named('RCE_RuriApplyFog', [('inColor', v1523), ('inColor_w', 1), ('sphericalVertexDistance', v1524), ('cylindricalVertexDistance', v1529), ('environmentalStart', v1530), ('environmentalEnd', v1531), ('renderDistanceStart', v1532), ('renderDistanceEnd', v1533), ('fogColor', v1534), ('fogColor_w', v1535)])
    v1537 = g.mixv(v1518, v1516, v1536[0])
    g.out_('ret_gBuffer0', v1537, True)
    g.out_('ret_gBuffer0_w', v225, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v1516, True)
    g.out_('ret_color_w', v225, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


def build_Ruri_Endfield_Scene_Unlit():
    t = _tree('Ruri Endfield Scene Unlit')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.vmath('SUBTRACT', v14, v0)
    v133 = g.comb(v77, v77, 0.0)
    v134 = g.vmath('MULTIPLY', v133, v132)
    v135 = g.vmath('ADD', v134, v0)
    v136 = g.comb(v83[0], v83[1], 0.0)
    v137 = g.comb(v83[2], v82, 0.0)
    v138 = g.vmath('MULTIPLY', v135, v136)
    v139 = g.vmath('ADD', v138, v137)
    g.out_('F4_BaseColorMap_uv', v139, True)
    v140 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v141 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    v142 = g.vmath('MULTIPLY', v37, v140)
    v143 = g.math('MULTIPLY', v38, v141)
    v144 = g.bc(v143)
    v145 = g.vmath('MULTIPLY', v142, v144)
    v146 = g.sep(v145)
    v147 = g.mixf(v103, v143, v146[0])
    v148 = g.vmath('NORMALIZE', v21)
    v149 = g.math('COMPARE', v60, 0, 1e-05)
    v150 = g.math('SUBTRACT', 1.0, v149)
    v151 = g.vmath('SUBTRACT', v20, v26)
    v152 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v153 = g.bc(v152)
    v154 = g.vmath('DIVIDE', v151, v153)
    v155 = g.vmath('ADD', v145, v145)
    v156 = g.vmath('LENGTH', v154)
    v157 = g.sep(v154)
    v158 = g.comb(v157[0], v157[2], 0.0)
    v159 = g.vmath('LENGTH', v158)
    v160 = g.math('ABSOLUTE', v157[1], 0.0)
    v161 = g.math('MAXIMUM', v159, v160)
    v162 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v163 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v164 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v165 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v166 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v167 = g.inp('_RuriFogColor_w', False, 0.0)
    v168 = g.group_named('RCE_RuriApplyFog', [('inColor', v155), ('inColor_w', 1), ('sphericalVertexDistance', v156), ('cylindricalVertexDistance', v161), ('environmentalStart', v162), ('environmentalEnd', v163), ('renderDistanceStart', v164), ('renderDistanceEnd', v165), ('fogColor', v166), ('fogColor_w', v167)])
    v169 = g.mixv(v150, v145, v168[0])
    g.out_('ret_gBuffer0', v169, True)
    g.out_('ret_gBuffer0_w', v147, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v145, True)
    g.out_('ret_color_w', v147, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


def build_Ruri_Endfield_Scene_ContainerWater():
    t = _tree('Ruri Endfield Scene ContainerWater')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.inp('unity_OrthoParams', True, (0.0, 0.0, 0.0))
    v133 = g.inp('unity_OrthoParams_w', False, 0.0)
    v134 = g.math('COMPARE', v133, 0, 1e-05)
    v135 = g.group_named('RCE_ViewMatrixRow2', [])
    v136 = g.vmath('SUBTRACT', v26, v20)
    v137 = g.mixv(v134, v135[0], v136)
    v138 = g.vmath('DOT_PRODUCT', v137, v137)
    v139 = g.math('MAXIMUM', v138, 9.99999993922529E-09)
    v140 = g.math('INVERSE_SQRT', v139, 0.0)
    v141 = g.bc(v140)
    v142 = g.vmath('MULTIPLY', v137, v141)
    v143 = g.math('MULTIPLY', v138, v140)
    v144 = g.math('MAXIMUM', v18, 1E-08)
    v145 = g.math('DIVIDE', 1, v144)
    v146 = g.math('GREATER_THAN', v5, 0)
    v147 = g.math('MULTIPLY', 1, -1.0)
    v148 = g.mixf(v146, v147, 1)
    v149 = g.vmath('CROSS_PRODUCT', v21, v22)
    v150 = g.bc(v148)
    v151 = g.vmath('MULTIPLY', v149, v150)
    g.out_('F4_BaseColorMap_uv', v0, True)
    v152 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v153 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    v154 = g.math('MULTIPLY', v153, v38)
    g.out_('F5_NormalMap_uv', v0, True)
    v155 = g.inp('F5_NormalMap', True, (1.0, 1.0, 1.0))
    v156 = g.inp('F5_NormalMap_alpha', False, 1.0)
    v157 = g.sep(v155)
    v158 = g.comb(v157[0], v157[1], 0.0)
    v159 = g.vmath('MULTIPLY', v158, (2, 2, 0.0))
    v160 = g.vmath('SUBTRACT', v159, (1, 1, 0.0))
    v161 = g.sep(v160)
    v162 = g.math('ABSOLUTE', v161[0], 0.0)
    v163 = g.math('LESS_THAN', v162, 0.012000000104308128)
    v164 = g.mixf(v163, v161[0], 0)
    v165 = g.math('ABSOLUTE', v161[1], 0.0)
    v166 = g.math('LESS_THAN', v165, 0.012000000104308128)
    v167 = g.mixf(v166, v161[1], 0)
    v168 = g.comb(v164, v167, 0.0)
    v169 = g.inp('_NormalScale', False, 0.0)
    v170 = g.comb(v169, v169, 0.0)
    v171 = g.vmath('MULTIPLY', v168, v170)
    v172 = g.sep(v11)
    v173 = g.math('MULTIPLY', v154, v172[0])
    v174 = g.inp('_Use_VerexTexColorAsOpacity', False, 0.0)
    v175 = g.mixf(v174, v154, v173)
    v176 = g.math('MULTIPLY', v175, v38)
    v177 = g.vmath('DOT_PRODUCT', v36, v21)
    v178 = g.math('LESS_THAN', v177, 0)
    v179 = g.math('GREATER_THAN', v31, 0)
    v180 = g.math('MULTIPLY', 1, -1.0)
    v181 = g.mixf(v179, 1, v180)
    v182 = g.mixf(v178, 1, v181)
    v183 = g.sep(v171)
    v184 = g.bc(v183[0])
    v185 = g.vmath('MULTIPLY', v22, v184)
    v186 = g.bc(v183[1])
    v187 = g.vmath('MULTIPLY', v151, v186)
    v188 = g.vmath('ADD', v185, v187)
    v189 = g.vmath('DOT_PRODUCT', v168, v168)
    v190 = g.math('SUBTRACT', 1, v189)
    v191 = g.clampn(v190)
    v192 = g.math('SQRT', v191, 0.0)
    v193 = g.math('MULTIPLY', v192, v182)
    v194 = g.bc(v193)
    v195 = g.vmath('MULTIPLY', v21, v194)
    v196 = g.vmath('ADD', v188, v195)
    v197 = g.vmath('NORMALIZE', v196)
    v198 = g.group_named('RCE_ViewMatrixRow0', [])
    v199 = g.vmath('DOT_PRODUCT', v198[0], v197)
    v200 = g.group_named('RCE_ViewMatrixRow1', [])
    v201 = g.vmath('DOT_PRODUCT', v200[0], v197)
    v202 = g.comb(v199, v201, 0.0)
    v203 = g.vmath('ADD', v202, (1, 1, 0.0))
    v204 = g.vmath('MULTIPLY', v203, (0.5, 0.5, 0.0))
    g.out_('F6_MatcapMap_uv', v204, True)
    v205 = g.inp('F6_MatcapMap', True, (1.0, 1.0, 1.0))
    v206 = g.inp('F6_MatcapMap_alpha', False, 1.0)
    v207 = g.vmath('SCALE', v142, s=-1.0)
    v208 = g.inp('_RefractionIOR', False, 1.0)
    v209 = g.group_named('RCE_RuriRefract', [('incident', v207), ('normal', v197), ('eta', v208)])
    v210 = g.math('SUBTRACT', 1, v176)
    v211 = g.vmath('MULTIPLY', v152, v37)
    v212 = g.comb(v176, v176, v176)
    v213 = g.inp('_RefractionFresnelColor', True, (1.0, 1.0, 1.0))
    v214 = g.inp('_RefractionFresnelColor_w', False, 1.0)
    v215 = g.bc(v210)
    v216 = g.vmath('MULTIPLY', v213, v215)
    v217 = g.vmath('DOT_PRODUCT', v142, v197)
    v218 = g.clampn(v217)
    v219 = g.bc(v218)
    v220 = g.vmath('MULTIPLY', v216, v219)
    v221 = g.vmath('ADD', v212, v220)
    v222 = g.vmath('MULTIPLY', v211, v221)
    v223 = g.inp('_Roughness', False, 0.0)
    v224 = g.inp('_GraphicsFeaturesGlobalParam1', True, (0.0, 0.0, 0.0))
    v225 = g.inp('_GraphicsFeaturesGlobalParam1_w', False, 0.0)
    v226 = g.sep(v224)
    v227 = g.math('SUBTRACT', v226[0], 1)
    v228 = g.math('MAXIMUM', v223, 0.0010000000474974513)
    v229 = g.math('LOGARITHM', v228, 2.0)
    v230 = g.math('MULTIPLY', 1.2000000476837158, v229)
    v231 = g.math('SUBTRACT', 1, v230)
    v232 = g.math('SUBTRACT', v227, v231)
    v233 = g.u2b(v209[0])
    g.out_('F7_IBL_ReflectionProbeCube_dir', v233, True)
    g.out_('F7_IBL_ReflectionProbeCube_mip', v232, False)
    v234 = g.inp('F7_IBL_ReflectionProbeCube', True, (0.2159, 0.2159, 0.2159))
    v235 = g.inp('F7_IBL_ReflectionProbeCube_alpha', False, 1.0)
    v236 = g.inp('_EnableParallaxMap', False, 0.0)
    v237 = g.math('GREATER_THAN', v236, 0.5)
    v238 = g.inp('_UseParallaxMask', False, 0.0)
    v239 = g.math('COMPARE', v238, 0, 1e-05)
    v240 = g.math('SUBTRACT', 1.0, v239)
    g.out_('F8_ParallaxMaskMap_uv', v14, True)
    v241 = g.inp('F8_ParallaxMaskMap', True, (1.0, 1.0, 1.0))
    v242 = g.inp('F8_ParallaxMaskMap_alpha', False, 1.0)
    v243 = g.sep(v241)
    v244 = g.mixf(v238, v176, v243[0])
    v245 = g.inp('_ParallaxMaskChannel', False, 0.0)
    v246 = g.math('SUBTRACT', v245, 1)
    v247 = g.clampn(v246)
    v248 = g.mixf(v247, v153, 1)
    v249 = g.mixf(v240, v248, v244)
    v250 = g.math('GREATER_THAN', v249, 0.009999999776482582)
    v251 = g.math('SUBTRACT', 1.0, v250)
    v252 = g.mixv(v251, (0.0, 0.0, 0.0), (0, 0, 0))
    v253 = g.mixf(v251, 0.0, 1.0)
    v254 = g.math('SUBTRACT', 1.0, v253)
    v255 = g.vmath('DOT_PRODUCT', v22, v142)
    v256 = g.vmath('DOT_PRODUCT', v151, v142)
    v257 = g.vmath('DOT_PRODUCT', v21, v142)
    v258 = g.comb(v255, v256, v257)
    v259 = g.vmath('NORMALIZE', v258)
    v260 = g.math('SUBTRACT', 1.0, v253)
    v261 = g.sep(v259)
    v262 = g.math('SUBTRACT', 1.0, v253)
    v263 = g.inp('_ParallaxMapUVType', False, 0.0)
    v264 = g.mixv(v263, v0, v14)
    v265 = g.math('SUBTRACT', 1.0, v253)
    v266 = g.inp('_ParallaxNoiseMapTilling', False, 0.0)
    v267 = g.comb(v266, v266, 0.0)
    v268 = g.vmath('MULTIPLY', v264, v267)
    v269 = g.math('SUBTRACT', 1.0, v253)
    v270 = g.math('SUBTRACT', 1.0, v253)
    v271 = g.math('SUBTRACT', 1.0, v253)
    v272 = g.inp('_ParallaxMarchNum', False, 3.0)
    v273 = g.math('MINIMUM', v272, 20)
    v274 = g.math('SUBTRACT', 1.0, v253)
    v275 = g.math('DIVIDE', 1, v273)
    v276 = g.math('SUBTRACT', 1.0, v253)
    v277 = g.comb(v261[0], v261[1], 0.0)
    v278 = g.math('ADD', v261[2], 0.41999998688697815)
    v279 = g.comb(v278, v278, 0.0)
    v280 = g.vmath('DIVIDE', v277, v279)
    v281 = g.math('MAXIMUM', v261[2], 0.0010000000474974513)
    v282 = g.comb(v281, v281, 0.0)
    v283 = g.vmath('DIVIDE', v280, v282)
    v284 = g.math('MULTIPLY', 1, -1.0)
    v285 = g.inp('_ParallaxStrength', False, 0.0)
    v286 = g.math('MULTIPLY', v284, v285)
    v287 = g.comb(v286, v286, 0.0)
    v288 = g.vmath('MULTIPLY', v283, v287)
    v289 = g.comb(v275, v275, 0.0)
    v290 = g.vmath('MULTIPLY', v288, v289)
    v291 = g.math('SUBTRACT', 1.0, v253)
    v292 = g.math('SUBTRACT', 1, v275)
    v293 = g.math('SUBTRACT', 1.0, v253)
    v294 = g.math('SUBTRACT', 1.0, v253)
    v295 = g.math('SUBTRACT', 1.0, v253)
    v296 = g.math('SUBTRACT', 1.0, v253)
    v297 = g.math('SUBTRACT', 1.0, v253)
    v298 = g.math('SUBTRACT', 1.0, v253)
    v299 = g.math('SUBTRACT', 1.0, v253)
    g.out_('Z0_it', 24, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_height', v292, False)
    g.out_('Z0_s_heightPrev', 1, False)
    g.out_('Z0_s_iteration', 0, False)
    g.out_('Z0_s_offset', v290, True)
    g.out_('Z0_s_offsetPrev', (0, 0, 0.0), True)
    g.out_('Z0_s_texHit', 0, False)
    g.out_('Z0_s_texPrev', 0, False)
    g.out_('Z0_r___done', v253, False)
    g.out_('Z0_r_uvNoise', v268, True)
    g.out_('Z0_r_steps', v273, False)
    g.out_('Z0_r_stepSize', v275, False)
    g.out_('Z0_r_stepOffset', v290, True)
    v300 = g.inp('Z0_o_Lloop0', False)
    v301 = g.inp('Z0_o_height', False)
    v302 = g.inp('Z0_o_heightPrev', False)
    v303 = g.inp('Z0_o_iteration', False)
    v304 = g.inp('Z0_o_offset', True)
    v305 = g.inp('Z0_o_offsetPrev', True)
    v306 = g.inp('Z0_o_texHit', False)
    v307 = g.inp('Z0_o_texPrev', False)
    v308 = g.mixf(v299, v292, v301)
    v309 = g.mixv(v299, v290, v304)
    v310 = g.mixv(v299, (0, 0, 0.0), v305)
    v311 = g.mixf(v299, 0, v307)
    v312 = g.mixf(v299, 1, v302)
    v313 = g.mixf(v299, 0, v306)
    v314 = g.mixf(v299, 0, v303)
    v315 = g.math('SUBTRACT', 1.0, v253)
    v316 = g.math('SUBTRACT', v311, v312)
    v317 = g.math('SUBTRACT', v311, v313)
    v318 = g.math('ADD', v317, v308)
    v319 = g.math('SUBTRACT', v318, v312)
    v320 = g.math('DIVIDE', v316, v319)
    v321 = g.comb(v320, v320, 0.0)
    v322 = g.vmath('MULTIPLY', v290, v321)
    v323 = g.vmath('ADD', v310, v322)
    v324 = g.math('SUBTRACT', 1.0, v253)
    v325 = g.vmath('ADD', v264, v323)
    v326 = g.inp('_ParallaxTilling', False, 1.0)
    v327 = g.comb(v326, v326, 0.0)
    v328 = g.vmath('MULTIPLY', v325, v327)
    g.out_('F9_ParallaxMap_uv', v328, True)
    v329 = g.inp('F9_ParallaxMap', True, (1.0, 1.0, 1.0))
    v330 = g.inp('F9_ParallaxMap_alpha', False, 1.0)
    v331 = g.sep(v329)
    v332 = g.math('SUBTRACT', 1.0, v253)
    v333 = g.inp('_ParallaxMinBrightness', False, 0.0)
    v334 = g.math('SUBTRACT', 1, v333)
    v335 = g.math('SUBTRACT', 1.0, v253)
    v336 = g.vmath('DOT_PRODUCT', v142, v197)
    v337 = g.clampn(v336)
    v338 = g.math('MAXIMUM', v337, 0.0010000000474974513)
    v339 = g.inp('_ParallaxFresnelStrength', False, 0.0)
    v340 = g.math('FLOOR', v339, 0.0)
    v341 = g.math('POWER', v338, v340)
    v342 = g.math('MULTIPLY', v249, v341)
    v343 = g.math('MULTIPLY', v342, v249)
    v344 = g.math('SUBTRACT', 1.0, v253)
    v345 = g.inp('_VFXParams0', True, (0.0, 0.0, 0.0))
    v346 = g.inp('_VFXParams0_w', False, 0.0)
    v347 = g.math('MULTIPLY', v346, 0.05000000074505806)
    v348 = g.inp('_ParallaxAnimSpeed', False, 0.0)
    v349 = g.math('MULTIPLY', v347, v348)
    v350 = g.inp('_ParallaxAnimRandom', False, 0.0)
    v351 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v352 = g.sep(v351)
    v353 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v354 = g.sep(v353)
    v355 = g.math('ADD', v352[0], v354[1])
    v356 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'OBJECT', 'WORLD', 'POINT'), point=True)
    v357 = g.sep(v356)
    v358 = g.math('ADD', v355, v357[2])
    v359 = g.math('MULTIPLY', v350, v358)
    v360 = g.math('ADD', v349, v359)
    v361 = g.math('SUBTRACT', 1.0, v253)
    v362 = g.math('COSINE', v360, 0.0)
    v363 = g.math('ADD', v333, 1)
    v364 = g.math('DIVIDE', v363, v334)
    v365 = g.math('ADD', v362, v364)
    v366 = g.math('MULTIPLY', v334, 0.5)
    v367 = g.math('MULTIPLY', v365, v366)
    v368 = g.math('SUBTRACT', 1.0, v253)
    v369 = g.inp('_ParallaxCharPos', False, 0.0)
    v370 = g.math('COMPARE', v369, 0, 1e-05)
    v371 = g.math('SUBTRACT', 1.0, v370)
    v372 = g.inp('_ParallaxBrightOuterRadius', False, 0.0)
    v373 = g.inp('_ParallaxBrightInnerRadius', False, 0.0)
    v374 = g.vmath('DISTANCE', v20, v345)
    v375 = g.math('SUBTRACT', v374, v372)
    v376 = g.math('SUBTRACT', v373, v372)
    v377 = g.math('DIVIDE', v375, v376)
    v378 = g.clampn(v377)
    v379 = g.math('MULTIPLY', v378, v378)
    v380 = g.math('MULTIPLY', 2.0, v378)
    v381 = g.math('SUBTRACT', 3.0, v380)
    v382 = g.math('MULTIPLY', v379, v381)
    v383 = g.inp('_ParallaxBrightStrength', False, 0.0)
    v384 = g.math('MULTIPLY', v382, v383)
    v385 = g.mixf(v371, 0, v384)
    v386 = g.math('SUBTRACT', 1.0, v253)
    v387 = g.inp('_VFXParams2', True, (0.0, 0.0, 0.0))
    v388 = g.inp('_VFXParams2_w', False, 0.0)
    v389 = g.sep(v387)
    v390 = g.sep(v20)
    v391 = g.comb(v390[0], v390[2], 0.0)
    v392 = g.comb(v389[0], v389[1], 0.0)
    v393 = g.vmath('DISTANCE', v391, v392)
    v394 = g.math('SUBTRACT', v393, v389[2])
    v395 = g.math('SUBTRACT', 0, v389[2])
    v396 = g.math('DIVIDE', v394, v395)
    v397 = g.clampn(v396)
    v398 = g.math('MULTIPLY', v397, v397)
    v399 = g.math('MULTIPLY', 2.0, v397)
    v400 = g.math('SUBTRACT', 3.0, v399)
    v401 = g.math('MULTIPLY', v398, v400)
    v402 = g.math('MULTIPLY', v401, v388)
    v403 = g.math('SUBTRACT', 1.0, v253)
    v404 = g.inp('_ParallaxColorDark', True, (0.0, 0.0, 0.0))
    v405 = g.inp('_ParallaxColorDark_w', False, 1.0)
    v406 = g.inp('_ParallaxColor', True, (0.0, 0.0, 0.0))
    v407 = g.inp('_ParallaxColor_w', False, 1.0)
    v408 = g.mixv(v331[1], v404, v406)
    v409 = g.bc(v343)
    v410 = g.vmath('MULTIPLY', v408, v409)
    v411 = g.math('ADD', v367, v385)
    v412 = g.math('ADD', v411, v402)
    v413 = g.bc(v412)
    v414 = g.vmath('MULTIPLY', v410, v413)
    v415 = g.vmath('MAXIMUM', v414, (0, 0, 0))
    v416 = g.vmath('MINIMUM', v415, (1000, 1000, 1000))
    v417 = g.math('SUBTRACT', 1.0, v253)
    v418 = g.inp('_ParallaxIgnorePostExposure', False, 1.0)
    v419 = g.math('COMPARE', v418, 0, 1e-05)
    v420 = g.math('SUBTRACT', 1.0, v419)
    v421 = g.inp('_ExposureWithMiscParams', True, (1.0, 1.0, 1.0))
    v422 = g.inp('_ExposureWithMiscParams_w', False, 1.0)
    v423 = g.sep(v421)
    v424 = g.mixf(v420, 1, v423[1])
    v425 = g.bc(v424)
    v426 = g.vmath('MULTIPLY', v416, v425)
    v427 = g.mixv(v417, v252, v426)
    v428 = g.mixf(v417, v253, 1.0)
    v429 = g.mixv(v237, (0, 0, 0), v427)
    v430 = g.inp('_FresnelUseMeshNormal', False, 0.0)
    v431 = g.mixv(v430, v197, v21)
    v432 = g.math('GREATER_THAN', v182, 0)
    v433 = g.vmath('SCALE', v431, s=-1.0)
    v434 = g.mixv(v432, v433, v431)
    v435 = g.vmath('DOT_PRODUCT', v142, v434)
    v436 = g.inp('_FresnelBias', False, 0.0)
    v437 = g.math('ADD', v435, v436)
    v438 = g.clampn(v437)
    v439 = g.inp('_FresnelPower', False, 1.0)
    v440 = g.math('POWER', v438, v439)
    v441 = g.math('SUBTRACT', 1, v440)
    v442 = g.inp('_FresnelFlip', False, 0.001)
    v443 = g.mixf(v442, v441, v440)
    v444 = g.inp('_UseFresnel', False, 0.0)
    v445 = g.math('GREATER_THAN', v444, 0.5)
    v446 = g.inp('_FresnelAffectOpacity', False, 1.0)
    v447 = g.math('SUBTRACT', 1, v446)
    v448 = g.math('MULTIPLY', v443, v446)
    v449 = g.math('ADD', v447, v448)
    v450 = g.math('MULTIPLY', v176, v449)
    v451 = g.mixf(v445, v176, v450)
    v452 = g.vmath('DOT_PRODUCT', v197, v142)
    v453 = g.clampn(v452)
    v454 = g.math('SUBTRACT', 1, v453)
    v455 = g.inp('_EnableGlassRim', False, 0.0)
    v456 = g.math('GREATER_THAN', v455, 0.5)
    v457 = g.inp('_GlassRimPower', False, 1.0)
    v458 = g.math('POWER', v454, v457)
    v459 = g.inp('_GlassRimStrength', False, 1.0)
    v460 = g.math('MULTIPLY', v458, v459)
    v461 = g.inp('_GlassRimUseMask', False, 0.0)
    v462 = g.math('COMPARE', v461, 0, 1e-05)
    v463 = g.math('SUBTRACT', 1.0, v462)
    v464 = g.inp('_GlassRimMaskChannel', False, 0.0)
    v465 = g.math('SUBTRACT', v464, 1)
    v466 = g.clampn(v465)
    v467 = g.mixf(v466, v153, 1)
    v468 = g.math('MULTIPLY', v460, v467)
    v469 = g.mixf(v463, v460, v468)
    v470 = g.inp('_GlassRimColor', True, (1.0, 1.0, 1.0))
    v471 = g.inp('_GlassRimColor_w', False, 1.0)
    v472 = g.mixv(v469, v222, v470)
    v473 = g.inp('_GlassRimRoughnessScale', False, 1.0)
    v474 = g.math('MULTIPLY', v223, v473)
    v475 = g.mixf(v469, v223, v474)
    v476 = g.inp('_UseVertexColorAsRimMask', False, 0.0)
    v477 = g.math('COMPARE', v476, 0, 1e-05)
    v478 = g.math('SUBTRACT', 1.0, v477)
    v479 = g.inp('_GlassMaskOpacity', False, 0.995)
    v480 = g.mixf(v172[0], v479, v451)
    v481 = g.mixf(v478, v451, v480)
    v482 = g.mixv(v456, v222, v472)
    v483 = g.mixf(v456, v223, v475)
    v484 = g.mixf(v456, v451, v481)
    v485 = g.mixf(v456, 0, v469)
    v486 = g.inp('_EnableGlassRefraction', False, 0.0)
    v487 = g.math('GREATER_THAN', v486, 0.5)
    v488 = g.inp('_RefractThickness', False, 0.01)
    v489 = g.sep(v17)
    v490 = g.math('MULTIPLY', v489[2], v452)
    v491 = g.math('MAXIMUM', v490, 0.5)
    v492 = g.math('DIVIDE', v488, v491)
    v493 = g.inp('_IsShell', False, 1.0)
    v494 = g.mixf(v493, v488, v492)
    v495 = g.vmath('SCALE', v142, s=-1.0)
    v496 = g.inp('_IoR', False, 0.8)
    v497 = g.group_named('RCE_RuriRefract', [('incident', v495), ('normal', v197), ('eta', v496)])
    v498 = g.bc(v494)
    v499 = g.vmath('MULTIPLY', v497[0], v498)
    v500 = g.group_named('RCE_ViewMatrixRow0', [])
    v501 = g.vmath('DOT_PRODUCT', v500[0], v499)
    v502 = g.group_named('RCE_ViewMatrixRow1', [])
    v503 = g.vmath('DOT_PRODUCT', v502[0], v499)
    v504 = g.comb(v501, v503, 0.0)
    v505 = g.inp('_RefractTex_ST', True, (1.0, 1.0, 0.0))
    v506 = g.inp('_RefractTex_ST_w', False, 0.0)
    v507 = g.sep(v505)
    v508 = g.comb(v507[0], v507[1], 0.0)
    v509 = g.vmath('MULTIPLY', v0, v508)
    v510 = g.comb(v507[2], v506, 0.0)
    v511 = g.vmath('ADD', v509, v510)
    g.out_('F10_RefractTex_uv', v511, True)
    v512 = g.inp('F10_RefractTex', True, (0.0, 0.0, 0.0))
    v513 = g.inp('F10_RefractTex_alpha', False, 1.0)
    v514 = g.sep(v512)
    v515 = g.comb(v514[0], v514[1], 0.0)
    v516 = g.vmath('MULTIPLY', v515, (2, 2, 0.0))
    v517 = g.vmath('SUBTRACT', v516, (1, 1, 0.0))
    v518 = g.inp('_RefractTexIntensity', False, 0.01)
    v519 = g.comb(v518, v518, 0.0)
    v520 = g.vmath('MULTIPLY', v517, v519)
    v521 = g.inp('_UseCustomRefractTex', False, 0.0)
    v522 = g.mixv(v521, v504, v520)
    v523 = g.vmath('ADD', v29, v522)
    v524 = g.vmath('MULTIPLY', v522, (0.25, 0.25, 0.0))
    v525 = g.vmath('ADD', v29, v524)
    v526 = g.inp('_ZBufferParams', True, (9999.0, 1.0, 9.999))
    v527 = g.inp('_ZBufferParams_w', False, 0.001)
    v528 = g.sep(v526)
    v529 = g.math('MULTIPLY', v528[2], 0.0)
    v530 = g.math('ADD', v529, v527)
    v531 = g.math('DIVIDE', 1, v530)
    v532 = g.math('MULTIPLY', v528[2], 0.0)
    v533 = g.math('ADD', v532, v527)
    v534 = g.math('DIVIDE', 1, v533)
    v535 = g.math('LESS_THAN', v531, v145)
    v536 = g.math('SUBTRACT', 1.0, v535)
    v537 = g.mixv(v536, v29, v525)
    v538 = g.math('LESS_THAN', v534, v145)
    v539 = g.math('SUBTRACT', 1.0, v538)
    v540 = g.mixv(v539, v537, v523)
    v541 = g.inp('_RefractTint', True, (1.0, 1.0, 1.0))
    v542 = g.inp('_RefractTint_w', False, 1.0)
    v543 = g.inp('_RefractBrightness', False, 1.0)
    v544 = g.bc(v543)
    v545 = g.vmath('MULTIPLY', v541, v544)
    v546 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v545)
    v547 = g.math('SUBTRACT', 1, v484)
    v548 = g.inp('_RefractionContribution', False, 0.8)
    v549 = g.math('SUBTRACT', 1, v548)
    v550 = g.math('MAXIMUM', v549, 0.009999999776482582)
    v551 = g.math('DIVIDE', v547, v550)
    v552 = g.clampn(v551)
    v553 = g.inp('_GlassRimRefractionPower', False, 1.0)
    v554 = g.math('POWER', v454, v553)
    v555 = g.inp('_GlassRimRefractionStrength', False, 1.0)
    v556 = g.math('MULTIPLY', v554, v555)
    v557 = g.mixv(v556, v546, v470)
    v558 = g.mixf(v556, v552, v471)
    v559 = g.mixv(v487, (0, 0, 0), v557)
    v560 = g.mixf(v487, 0, v558)
    v561 = g.mixv(v487, (0, 0, 0), v557)
    v562 = g.inp('_EnableIce', False, 0.0)
    v563 = g.math('GREATER_THAN', v562, 0.5)
    g.out_('F11_IceNormalMap_uv', v0, True)
    v564 = g.inp('F11_IceNormalMap', True, (1.0, 1.0, 1.0))
    v565 = g.inp('F11_IceNormalMap_alpha', False, 1.0)
    v566 = g.sep(v564)
    v567 = g.comb(v566[0], v566[1], 0.0)
    v568 = g.inp('_IceRefractionStrength', False, 1.0)
    v569 = g.comb(v568, v568, 0.0)
    v570 = g.vmath('MULTIPLY', v202, v569)
    v571 = g.vmath('ADD', v567, v570)
    v572 = g.inp('_IceRefractionColor', True, (1.0, 1.0, 1.0))
    v573 = g.inp('_IceRefractionColor_w', False, 1.0)
    v574 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v572)
    v575 = g.inp('_IceRefractionBrightness', False, 1.0)
    v576 = g.bc(v575)
    v577 = g.vmath('MULTIPLY', v574, v576)
    v578 = g.math('SUBTRACT', 1, v451)
    v579 = g.bc(v578)
    v580 = g.vmath('MULTIPLY', v577, v579)
    v581 = g.inp('_IceOpacityMapTilling', False, 1.0)
    v582 = g.comb(v581, v581, 0.0)
    v583 = g.vmath('MULTIPLY', v0, v582)
    g.out_('F12_IceOpacityMap_uv', v583, True)
    v584 = g.inp('F12_IceOpacityMap', True, (1.0, 1.0, 1.0))
    v585 = g.inp('F12_IceOpacityMap_alpha', False, 1.0)
    v586 = g.sep(v584)
    v587 = g.inp('_IceOpacityThreshold', False, 0.0)
    v588 = g.math('GREATER_THAN', v586[0], v587)
    v589 = g.mixf(v588, v451, 1)
    v590 = g.mixf(v563, v484, v589)
    v591 = g.mixv(v563, (0, 0, 0), v580)
    v592 = g.math('MULTIPLY', v528[2], 0.0)
    v593 = g.math('ADD', v592, v527)
    v594 = g.math('DIVIDE', 1, v593)
    v595 = g.math('SUBTRACT', v594, v145)
    v596 = g.math('MULTIPLY', v595, 100)
    v597 = g.clampn(v596)
    v598 = g.inp('_EnableContainerWater', False, 0.0)
    v599 = g.math('GREATER_THAN', v598, 0.5)
    v600 = g.inp('_WaterNormalMap_ST', True, (1.0, 1.0, 0.0))
    v601 = g.inp('_WaterNormalMap_ST_w', False, 0.0)
    v602 = g.sep(v600)
    v603 = g.comb(v602[0], v602[1], 0.0)
    v604 = g.vmath('MULTIPLY', v0, v603)
    v605 = g.sep(v21)
    v606 = g.math('GREATER_THAN', v605[1], 0.8500000238418579)
    v607 = g.math('MULTIPLY', v346, 0.699999988079071)
    v608 = g.inp('_WaterNormalSpeed', False, 0.01)
    v609 = g.math('MULTIPLY', v607, v608)
    v610 = g.comb(v602[2], v601, 0.0)
    v611 = g.vmath('ADD', v604, v610)
    v612 = g.math('MULTIPLY', v609, 0.699999988079071)
    v613 = g.comb(v609, v612, 0.0)
    v614 = g.vmath('ADD', v611, v613)
    g.out_('F13_WaterNormalMap_uv', v614, True)
    v615 = g.inp('F13_WaterNormalMap', True, (0.5, 0.5, 1.0))
    v616 = g.inp('F13_WaterNormalMap_alpha', False, 1.0)
    v617 = g.inp('_WaterSurfaceNormalScale', False, 1.0)
    v618 = g.sep(v615)
    v619 = g.math('MULTIPLY', v616, v618[0])
    v620 = g.comb(v619, v618[1], 0.0)
    v621 = g.vmath('MULTIPLY', v620, (2, 2, 0.0))
    v622 = g.vmath('SUBTRACT', v621, (1, 1, 0.0))
    v623 = g.comb(v617, v617, 0.0)
    v624 = g.vmath('MULTIPLY', v622, v623)
    v625 = g.vmath('MULTIPLY', v624, (0.4000000059604645, 0.4000000059604645, 0.0))
    v626 = g.vmath('MULTIPLY', v604, (0.5, 0.5, 0.0))
    v627 = g.comb(v602[2], v601, 0.0)
    v628 = g.vmath('ADD', v626, v627)
    v629 = g.math('MULTIPLY', 0.6000000238418579, -1.0)
    v630 = g.math('MULTIPLY', v609, v629)
    v631 = g.math('MULTIPLY', 0.800000011920929, -1.0)
    v632 = g.math('MULTIPLY', v609, v631)
    v633 = g.comb(v630, v632, 0.0)
    v634 = g.vmath('ADD', v628, v633)
    g.out_('F14_WaterNormalMap_uv', v634, True)
    v635 = g.inp('F14_WaterNormalMap', True, (0.5, 0.5, 1.0))
    v636 = g.inp('F14_WaterNormalMap_alpha', False, 1.0)
    v637 = g.sep(v635)
    v638 = g.math('MULTIPLY', v636, v637[0])
    v639 = g.comb(v638, v637[1], 0.0)
    v640 = g.vmath('MULTIPLY', v639, (2, 2, 0.0))
    v641 = g.vmath('SUBTRACT', v640, (1, 1, 0.0))
    v642 = g.comb(v617, v617, 0.0)
    v643 = g.vmath('MULTIPLY', v641, v642)
    v644 = g.vmath('MULTIPLY', v643, (0.4000000059604645, 0.4000000059604645, 0.0))
    v645 = g.vmath('MULTIPLY', v604, (0.25, 0.25, 0.0))
    v646 = g.comb(v602[2], v601, 0.0)
    v647 = g.vmath('ADD', v645, v646)
    v648 = g.math('MULTIPLY', v609, 0.4000000059604645)
    v649 = g.math('MULTIPLY', 0.5, -1.0)
    v650 = g.math('MULTIPLY', v609, v649)
    v651 = g.comb(v648, v650, 0.0)
    v652 = g.vmath('ADD', v647, v651)
    g.out_('F15_WaterNormalMap_uv', v652, True)
    v653 = g.inp('F15_WaterNormalMap', True, (0.5, 0.5, 1.0))
    v654 = g.inp('F15_WaterNormalMap_alpha', False, 1.0)
    v655 = g.sep(v653)
    v656 = g.math('MULTIPLY', v654, v655[0])
    v657 = g.comb(v656, v655[1], 0.0)
    v658 = g.vmath('MULTIPLY', v657, (2, 2, 0.0))
    v659 = g.vmath('SUBTRACT', v658, (1, 1, 0.0))
    v660 = g.comb(v617, v617, 0.0)
    v661 = g.vmath('MULTIPLY', v659, v660)
    v662 = g.vmath('MULTIPLY', v661, (0.20000000298023224, 0.20000000298023224, 0.0))
    v663 = g.inp('_DisplacementTex_ST', True, (1.0, 1.0, 0.0))
    v664 = g.inp('_DisplacementTex_ST_w', False, 0.0)
    v665 = g.sep(v663)
    v666 = g.comb(v665[0], v665[1], 0.0)
    v667 = g.vmath('MULTIPLY', v0, v666)
    v668 = g.comb(v665[2], v664, 0.0)
    v669 = g.vmath('ADD', v667, v668)
    g.out_('F16_DisplacementTex_uv', v669, True)
    v670 = g.inp('F16_DisplacementTex', True, (0.5, 0.5, 0.5))
    v671 = g.inp('F16_DisplacementTex_alpha', False, 1.0)
    v672 = g.sep(v670)
    v673 = g.math('MULTIPLY', v672[1], 2)
    v674 = g.math('SUBTRACT', v673, 1)
    v675 = g.math('MULTIPLY', v672[2], 2)
    v676 = g.math('SUBTRACT', v675, 1)
    v677 = g.comb(v674, v676, 0.0)
    v678 = g.vmath('DOT_PRODUCT', v677, v677)
    v679 = g.clampn(v678)
    v680 = g.math('SUBTRACT', 1, v679)
    v681 = g.math('SQRT', v680, 0.0)
    v682 = g.math('MAXIMUM', 1.0000000168623835E-16, v681)
    v683 = g.comb(v674, v676, v682)
    v684 = g.vmath('NORMALIZE', v683)
    v685 = g.vmath('ADD', v625, v644)
    v686 = g.vmath('ADD', v685, v662)
    v687 = g.inp('_NormalMapBlendWeight', False, 0.5)
    v688 = g.comb(v687, v687, 0.0)
    v689 = g.vmath('MULTIPLY', v686, v688)
    v690 = g.inp('_DisplacementNormalStrength', False, 0.5)
    v691 = g.bc(v690)
    v692 = g.vmath('MULTIPLY', v684, v691)
    v693 = g.sep(v692)
    v694 = g.comb(v693[0], v693[1], 0.0)
    v695 = g.math('SUBTRACT', 1, v687)
    v696 = g.comb(v695, v695, 0.0)
    v697 = g.vmath('MULTIPLY', v694, v696)
    v698 = g.vmath('ADD', v689, v697)
    v699 = g.sep(v698)
    v700 = g.math('MULTIPLY', v699[0], v699[0])
    v701 = g.math('SUBTRACT', 1, v700)
    v702 = g.math('MULTIPLY', v699[1], v699[1])
    v703 = g.math('SUBTRACT', v701, v702)
    v704 = g.math('MAXIMUM', 0, v703)
    v705 = g.math('SQRT', v704, 0.0)
    v706 = g.comb(v699[0], v699[1], v705)
    v707 = g.vmath('NORMALIZE', v706)
    v708 = g.sep(v2)
    v709 = g.inp('_IcePosition', True, (0.0, 0.0, 0.0))
    v710 = g.inp('_IcePosition_w', False, 0.0)
    v711 = g.sep(v709)
    v712 = g.math('SUBTRACT', v708[0], v711[0])
    v713 = g.math('SUBTRACT', v708[2], v711[2])
    v714 = g.comb(v712, v713, 0.0)
    v715 = g.vmath('LENGTH', v714)
    v716 = g.inp('_WaterCupRadius', False, 1.0)
    v717 = g.math('SUBTRACT', v716, v715)
    v718 = g.inp('_IceballRadius', False, 1.0)
    v719 = g.math('GREATER_THAN', v718, 0)
    v720 = g.math('GREATER_THAN', v708[1], 0)
    v721 = g.math('SUBTRACT', v715, v718)
    v722 = g.inp('_IceballWaterlineWidth', False, 1.0)
    v723 = g.math('DIVIDE', v721, v722)
    v724 = g.clampn(v723)
    v725 = g.mixf(v720, 1, v724)
    v726 = g.mixf(v719, 1, v725)
    v727 = g.sep(v707)
    v728 = g.bc(v727[0])
    v729 = g.vmath('MULTIPLY', v22, v728)
    v730 = g.bc(v727[1])
    v731 = g.vmath('MULTIPLY', v151, v730)
    v732 = g.vmath('ADD', v729, v731)
    v733 = g.bc(v727[2])
    v734 = g.vmath('MULTIPLY', v21, v733)
    v735 = g.vmath('ADD', v732, v734)
    v736 = g.vmath('NORMALIZE', v735)
    v737 = g.mixv(v726, (0, 1, 0), v736)
    v738 = g.vmath('DOT_PRODUCT', v142, v737)
    v739 = g.clampn(v738)
    v740 = g.math('SUBTRACT', 1, v739)
    v741 = g.vmath('SCALE', v142, s=-1.0)
    v742 = g.vmath('DOT_PRODUCT', v737, v741)
    v743 = g.math('MULTIPLY', 2.0, v742)
    v744 = g.vmath('SCALE', v737, s=v743)
    v745 = g.vmath('SUBTRACT', v741, v744)
    v746 = g.math('SUBTRACT', v226[0], 1)
    v747 = g.math('MAXIMUM', 1, 0.0010000000474974513)
    v748 = g.math('LOGARITHM', v747, 2.0)
    v749 = g.math('MULTIPLY', 1.2000000476837158, v748)
    v750 = g.math('SUBTRACT', 1, v749)
    v751 = g.math('SUBTRACT', v746, v750)
    v752 = g.u2b(v745)
    g.out_('F17_IBL_ReflectionProbeCube_dir', v752, True)
    g.out_('F17_IBL_ReflectionProbeCube_mip', v751, False)
    v753 = g.inp('F17_IBL_ReflectionProbeCube', True, (0.2159, 0.2159, 0.2159))
    v754 = g.inp('F17_IBL_ReflectionProbeCube_alpha', False, 1.0)
    v755 = g.math('MULTIPLY', v597, 0.4000000059604645)
    v756 = g.inp('_WaterCausticSpeed', False, 1.0)
    v757 = g.math('MULTIPLY', v346, v756)
    v758 = g.inp('_MainLightPosition', True, (0.0, 0.0, 0.0))
    v759 = g.inp('_MainLightPosition_w', False, 0.0)
    v760 = g.vmath('SCALE', v758, s=-1.0)
    v761 = g.vmath('DOT_PRODUCT', v142, v760)
    v762 = g.clampn(v761)
    v763 = g.math('POWER', v762, 12)
    v764 = g.vmath('SCALE', v142, s=-1.0)
    v765 = g.vmath('SCALE', v758, s=-1.0)
    v766 = g.vmath('DOT_PRODUCT', v764, v765)
    v767 = g.clampn(v766)
    v768 = g.math('MULTIPLY', v767, 1.5)
    v769 = g.math('ADD', v763, v768)
    v770 = g.clampn(v769)
    v771 = g.inp('_WaterScatteringColor', True, (1.0, 1.0, 1.0))
    v772 = g.inp('_WaterScatteringColor_w', False, 1.0)
    v773 = g.math('MULTIPLY', 25, -1.0)
    v774 = g.math('MULTIPLY', v597, v773)
    v775 = g.math('EXPONENT', v774, 0.0)
    v776 = g.math('SUBTRACT', 1, v775)
    v777 = g.math('MULTIPLY', v772, v776)
    v778 = g.math('MULTIPLY', v770, v777)
    v779 = g.inp('_WaterRefractionColor', True, (1.0, 1.0, 1.0))
    v780 = g.inp('_WaterRefractionColor_w', False, 1.0)
    v781 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v779)
    v782 = g.inp('_WaterRefractionBrightness', False, 1.0)
    v783 = g.bc(v782)
    v784 = g.vmath('MULTIPLY', v781, v783)
    v785 = g.inp('_WaterCausticMap_ST', True, (1.0, 1.0, 0.0))
    v786 = g.inp('_WaterCausticMap_ST_w', False, 0.0)
    v787 = g.sep(v785)
    v788 = g.comb(v787[0], v787[1], 0.0)
    v789 = g.vmath('MULTIPLY', v0, v788)
    v790 = g.comb(v787[2], v786, 0.0)
    v791 = g.vmath('ADD', v789, v790)
    v792 = g.math('MULTIPLY', v757, 0.699999988079071)
    v793 = g.math('SINE', v792, 0.0)
    v794 = g.math('MULTIPLY', v757, 0.5)
    v795 = g.math('COSINE', v794, 0.0)
    v796 = g.comb(v793, v795, 0.0)
    v797 = g.vmath('MULTIPLY', v796, (0.019999999552965164, 0.019999999552965164, 0.0))
    v798 = g.vmath('ADD', v791, v797)
    g.out_('F18_WaterCausticMap_uv', v798, True)
    v799 = g.inp('F18_WaterCausticMap', True, (1.0, 1.0, 1.0))
    v800 = g.inp('F18_WaterCausticMap_alpha', False, 1.0)
    v801 = g.sep(v799)
    v802 = g.inp('_WaterShallowColor', True, (1.0, 1.0, 1.0))
    v803 = g.inp('_WaterShallowColor_w', False, 1.0)
    v804 = g.inp('_WaterCausticStrength', False, 1.0)
    v805 = g.math('MULTIPLY', v801[0], v804)
    v806 = g.bc(v805)
    v807 = g.vmath('MULTIPLY', v802, v806)
    v808 = g.vmath('ADD', v784, v807)
    v809 = g.inp('_WaterDeepColor', True, (1.0, 1.0, 1.0))
    v810 = g.inp('_WaterDeepColor_w', False, 1.0)
    v811 = g.math('MULTIPLY', v597, 2.5)
    v812 = g.clampn(v811)
    v813 = g.mixv(v812, v802, v809)
    v814 = g.mixv(v597, v802, v771)
    v815 = g.mixv(v778, v813, v814)
    v816 = g.math('MULTIPLY', 0.20000000298023224, v778)
    v817 = g.math('ADD', 1, v816)
    v818 = g.bc(v817)
    v819 = g.vmath('MULTIPLY', v815, v818)
    v820 = g.inp('_WaterAbsorptionColor', True, (1.0, 1.0, 1.0))
    v821 = g.inp('_WaterAbsorptionColor_w', False, 1.0)
    v822 = g.vmath('SCALE', v820, s=-1.0)
    v823 = g.math('MULTIPLY', v597, v716)
    v824 = g.bc(v823)
    v825 = g.vmath('MULTIPLY', v822, v824)
    v826 = g.bc(v821)
    v827 = g.vmath('MULTIPLY', v825, v826)
    v828 = g.math('EXPONENT', v827, 0.0)
    v829 = g.bc(v828)
    v830 = g.vmath('MULTIPLY', v819, v829)
    v831 = g.math('MULTIPLY', v597, 3.5)
    v832 = g.clampn(v831)
    v833 = g.mixv(v832, v808, v830)
    v834 = g.inp('_GraphicsFeaturesGlobalParam0', True, (0.0, 0.0, 0.0))
    v835 = g.inp('_GraphicsFeaturesGlobalParam0_w', False, 0.0)
    v836 = g.sep(v834)
    v837 = g.bc(v836[2])
    v838 = g.vmath('MULTIPLY', v753, v837)
    v839 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v840 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v841 = g.sep(v839)
    v842 = g.bc(v841[1])
    v843 = g.vmath('MULTIPLY', v838, v842)
    v844 = g.inp('_WaterFresnelPower', False, 1.0)
    v845 = g.math('MULTIPLY', v844, 1.2000000476837158)
    v846 = g.math('POWER', v740, v845)
    v847 = g.math('MULTIPLY', 0.9700000286102295, v846)
    v848 = g.math('ADD', 0.029999999329447746, v847)
    v849 = g.inp('_WaterReflectionStrength', False, 1.0)
    v850 = g.math('MULTIPLY', v848, v849)
    v851 = g.math('MULTIPLY', v850, 1.2000000476837158)
    v852 = g.mixv(v851, v833, v843)
    v853 = g.comb(v708[0], v708[2], 0.0)
    v854 = g.vmath('LENGTH', v853)
    v855 = g.inp('_WaterStrokeDistance', False, 0.0395)
    v856 = g.inp('_WaterStrokeWidth', False, 0.0352)
    v857 = g.math('ADD', v855, v856)
    v858 = g.inp('_WaterStrokeSoftness', False, 0.0025)
    v859 = g.math('SUBTRACT', v855, v858)
    v860 = g.math('SUBTRACT', v854, v859)
    v861 = g.math('SUBTRACT', v855, v859)
    v862 = g.math('DIVIDE', v860, v861)
    v863 = g.clampn(v862)
    v864 = g.math('MULTIPLY', v863, v863)
    v865 = g.math('MULTIPLY', 2.0, v863)
    v866 = g.math('SUBTRACT', 3.0, v865)
    v867 = g.math('MULTIPLY', v864, v866)
    v868 = g.math('ADD', v857, v858)
    v869 = g.math('SUBTRACT', v854, v857)
    v870 = g.math('SUBTRACT', v868, v857)
    v871 = g.math('DIVIDE', v869, v870)
    v872 = g.clampn(v871)
    v873 = g.math('MULTIPLY', v872, v872)
    v874 = g.math('MULTIPLY', 2.0, v872)
    v875 = g.math('SUBTRACT', 3.0, v874)
    v876 = g.math('MULTIPLY', v873, v875)
    v877 = g.math('SUBTRACT', 1, v876)
    v878 = g.math('MULTIPLY', v867, v877)
    v879 = g.inp('_WaterStrokeOpacity', False, 1.0)
    v880 = g.math('MULTIPLY', v878, v879)
    v881 = g.inp('_WaterMeniscusWidth', False, 1.0)
    v882 = g.math('SUBTRACT', v717, v881)
    v883 = g.clampn(v882)
    v884 = g.math('DIVIDE', v717, v881)
    v885 = g.math('POWER', v884, 2)
    v886 = g.math('SUBTRACT', 1, v885)
    v887 = g.clampn(v886)
    v888 = g.math('ADD', v883, v887)
    v889 = g.math('MULTIPLY', v346, 0.5)
    v890 = g.math('SINE', v889, 0.0)
    v891 = g.math('MULTIPLY', v890, 0.05000000074505806)
    v892 = g.math('ADD', v888, v891)
    v893 = g.clampn(v892)
    v894 = g.clampn(v893)
    v895 = g.math('MULTIPLY', v716, 0.20000000298023224)
    v896 = g.math('DIVIDE', v717, v895)
    v897 = g.clampn(v896)
    v898 = g.math('SUBTRACT', 1, v897)
    v899 = g.math('POWER', v898, 4)
    v900 = g.math('MULTIPLY', v899, 0.30000001192092896)
    v901 = g.bc(v900)
    v902 = g.vmath('MULTIPLY', (1, 1, 1), v901)
    v903 = g.vmath('ADD', v802, v902)
    v904 = g.vmath('MAXIMUM', v903, (0, 0, 0))
    v905 = g.vmath('MINIMUM', v904, (10, 10, 10))
    v906 = g.clampn(v755)
    v907 = g.mixv(v906, v852, v809)
    v908 = g.vmath('MAXIMUM', v907, (0, 0, 0))
    v909 = g.vmath('MINIMUM', v908, (10, 10, 10))
    v910 = g.mixv(v894, v909, v905)
    v911 = g.vmath('MAXIMUM', v910, (0, 0, 0))
    v912 = g.vmath('MINIMUM', v911, (10, 10, 10))
    v913 = g.inp('_WaterStrokeColor', True, (1.0, 1.0, 1.0))
    v914 = g.inp('_WaterStrokeColor_w', False, 1.0)
    v915 = g.math('MULTIPLY', v880, 0.5)
    v916 = g.mixv(v915, v912, v913)
    v917 = g.math('SUBTRACT', 1, v597)
    v918 = g.clampn(v917)
    v919 = g.mixf(v918, 0.8999999761581421, 1)
    v920 = g.clampn(v919)
    v921 = g.mixf(v880, v920, 1)
    v922 = g.math('MULTIPLY', v597, 0.5)
    v923 = g.clampn(v922)
    v924 = g.mixf(v923, 0.009999999776482582, 0.05000000074505806)
    v925 = g.bc(v878)
    v926 = g.vmath('MULTIPLY', v913, v925)
    v927 = g.bc(v879)
    v928 = g.vmath('MULTIPLY', v926, v927)
    v929 = g.inp('_WaterBaseOpacity', False, 1.0)
    v930 = g.inp('_WaterOpacityDepthFactor', False, 1.0)
    v931 = g.math('MULTIPLY', v597, v930)
    v932 = g.clampn(v931)
    v933 = g.mixf(v932, v929, 1)
    v934 = g.inp('_WaterOpacityFresnelFactor', False, 1.0)
    v935 = g.math('POWER', v740, v934)
    v936 = g.mixf(v935, v929, 1)
    v937 = g.math('MAXIMUM', v933, v936)
    v938 = g.inp('_WaterEdgeOpacity', False, 1.0)
    v939 = g.math('SUBTRACT', v716, v854)
    v940 = g.clampn(v939)
    v941 = g.mixf(v940, v938, 1)
    v942 = g.math('MULTIPLY', v937, v941)
    v943 = g.inp('_WaterTurbidity', False, 1.0)
    v944 = g.mixf(v943, 1, 0.699999988079071)
    v945 = g.math('MULTIPLY', v942, v944)
    v946 = g.math('MULTIPLY', v945, v726)
    v947 = g.inp('_WaterOpacityMinimum', False, 1.0)
    v948 = g.inp('_WaterOpacityMaximum', False, 1.0)
    v949 = g.clampn(v946, v947, v948)
    v950 = g.math('MULTIPLY', v949, 1.100000023841858)
    v951 = g.mixf(v880, v950, 1)
    v952 = g.math('MULTIPLY', v346, v608)
    v953 = g.comb(v602[2], v601, 0.0)
    v954 = g.vmath('ADD', v604, v953)
    v955 = g.math('MULTIPLY', v952, 0.5)
    v956 = g.math('MULTIPLY', v952, 0.30000001192092896)
    v957 = g.comb(v955, v956, 0.0)
    v958 = g.vmath('ADD', v954, v957)
    g.out_('F19_WaterNormalMap_uv', v958, True)
    v959 = g.inp('F19_WaterNormalMap', True, (0.5, 0.5, 1.0))
    v960 = g.inp('F19_WaterNormalMap_alpha', False, 1.0)
    v961 = g.sep(v959)
    v962 = g.math('MULTIPLY', v960, v961[0])
    v963 = g.comb(v962, v961[1], 0.0)
    v964 = g.vmath('MULTIPLY', v963, (2, 2, 0.0))
    v965 = g.vmath('SUBTRACT', v964, (1, 1, 0.0))
    v966 = g.vmath('MULTIPLY', v604, (0.5, 0.5, 0.0))
    v967 = g.comb(v602[2], v601, 0.0)
    v968 = g.vmath('ADD', v966, v967)
    v969 = g.math('MULTIPLY', 0.30000001192092896, -1.0)
    v970 = g.math('MULTIPLY', v952, v969)
    v971 = g.math('MULTIPLY', 0.4000000059604645, -1.0)
    v972 = g.math('MULTIPLY', v952, v971)
    v973 = g.comb(v970, v972, 0.0)
    v974 = g.vmath('ADD', v968, v973)
    g.out_('F20_WaterNormalMap_uv', v974, True)
    v975 = g.inp('F20_WaterNormalMap', True, (0.5, 0.5, 1.0))
    v976 = g.inp('F20_WaterNormalMap_alpha', False, 1.0)
    v977 = g.sep(v975)
    v978 = g.math('MULTIPLY', v976, v977[0])
    v979 = g.comb(v978, v977[1], 0.0)
    v980 = g.vmath('MULTIPLY', v979, (2, 2, 0.0))
    v981 = g.vmath('SUBTRACT', v980, (1, 1, 0.0))
    v982 = g.comb(v617, v617, 0.0)
    v983 = g.vmath('MULTIPLY', v965, v982)
    v984 = g.vmath('MULTIPLY', v983, (0.5, 0.5, 0.0))
    v985 = g.comb(v617, v617, 0.0)
    v986 = g.vmath('MULTIPLY', v981, v985)
    v987 = g.vmath('MULTIPLY', v986, (0.20000000298023224, 0.20000000298023224, 0.0))
    v988 = g.vmath('ADD', v984, v987)
    v989 = g.vmath('DOT_PRODUCT', v965, v965)
    v990 = g.clampn(v989)
    v991 = g.math('SUBTRACT', 1, v990)
    v992 = g.math('SQRT', v991, 0.0)
    v993 = g.math('MAXIMUM', 1.0000000168623835E-16, v992)
    v994 = g.vmath('DOT_PRODUCT', v981, v981)
    v995 = g.clampn(v994)
    v996 = g.math('SUBTRACT', 1, v995)
    v997 = g.math('SQRT', v996, 0.0)
    v998 = g.math('MAXIMUM', 1.0000000168623835E-16, v997)
    v999 = g.math('MULTIPLY', v993, v998)
    v1000 = g.sep(v988)
    v1001 = g.comb(v1000[0], v1000[1], v999)
    v1002 = g.vmath('NORMALIZE', v1001)
    v1003 = g.sep(v1002)
    v1004 = g.bc(v1003[0])
    v1005 = g.vmath('MULTIPLY', v22, v1004)
    v1006 = g.bc(v1003[1])
    v1007 = g.vmath('MULTIPLY', v151, v1006)
    v1008 = g.vmath('ADD', v1005, v1007)
    v1009 = g.bc(v1003[2])
    v1010 = g.vmath('MULTIPLY', v21, v1009)
    v1011 = g.vmath('ADD', v1008, v1010)
    v1012 = g.vmath('NORMALIZE', v1011)
    v1013 = g.vmath('DOT_PRODUCT', v142, v1012)
    v1014 = g.clampn(v1013)
    v1015 = g.math('SUBTRACT', 1, v1014)
    v1016 = g.vmath('SCALE', v142, s=-1.0)
    v1017 = g.vmath('DOT_PRODUCT', v1012, v1016)
    v1018 = g.math('MULTIPLY', 2.0, v1017)
    v1019 = g.vmath('SCALE', v1012, s=v1018)
    v1020 = g.vmath('SUBTRACT', v1016, v1019)
    v1021 = g.math('SUBTRACT', v226[0], 1)
    v1022 = g.math('MAXIMUM', 1, 0.0010000000474974513)
    v1023 = g.math('LOGARITHM', v1022, 2.0)
    v1024 = g.math('MULTIPLY', 1.2000000476837158, v1023)
    v1025 = g.math('SUBTRACT', 1, v1024)
    v1026 = g.math('SUBTRACT', v1021, v1025)
    v1027 = g.u2b(v1020)
    g.out_('F21_IBL_ReflectionProbeCube_dir', v1027, True)
    g.out_('F21_IBL_ReflectionProbeCube_mip', v1026, False)
    v1028 = g.inp('F21_IBL_ReflectionProbeCube', True, (0.2159, 0.2159, 0.2159))
    v1029 = g.inp('F21_IBL_ReflectionProbeCube_alpha', False, 1.0)
    v1030 = g.math('MULTIPLY', v597, 0.4000000059604645)
    v1031 = g.math('MULTIPLY', v346, v756)
    v1032 = g.vmath('SCALE', v758, s=-1.0)
    v1033 = g.vmath('DOT_PRODUCT', v142, v1032)
    v1034 = g.clampn(v1033)
    v1035 = g.math('POWER', v1034, 12)
    v1036 = g.vmath('SCALE', v142, s=-1.0)
    v1037 = g.vmath('SCALE', v758, s=-1.0)
    v1038 = g.vmath('DOT_PRODUCT', v1036, v1037)
    v1039 = g.clampn(v1038)
    v1040 = g.math('MULTIPLY', v1039, 1.5)
    v1041 = g.math('ADD', v1035, v1040)
    v1042 = g.clampn(v1041)
    v1043 = g.math('MULTIPLY', 25, -1.0)
    v1044 = g.math('MULTIPLY', v597, v1043)
    v1045 = g.math('EXPONENT', v1044, 0.0)
    v1046 = g.math('SUBTRACT', 1, v1045)
    v1047 = g.math('MULTIPLY', v772, v1046)
    v1048 = g.math('MULTIPLY', v1042, v1047)
    v1049 = g.vmath('MULTIPLY', (1.0, 1.0, 1.0), v779)
    v1050 = g.bc(v782)
    v1051 = g.vmath('MULTIPLY', v1049, v1050)
    v1052 = g.comb(v787[0], v787[1], 0.0)
    v1053 = g.vmath('MULTIPLY', v0, v1052)
    v1054 = g.comb(v787[2], v786, 0.0)
    v1055 = g.vmath('ADD', v1053, v1054)
    v1056 = g.math('MULTIPLY', v1031, 0.699999988079071)
    v1057 = g.math('SINE', v1056, 0.0)
    v1058 = g.math('MULTIPLY', v1031, 0.5)
    v1059 = g.math('COSINE', v1058, 0.0)
    v1060 = g.comb(v1057, v1059, 0.0)
    v1061 = g.vmath('MULTIPLY', v1060, (0.019999999552965164, 0.019999999552965164, 0.0))
    v1062 = g.vmath('ADD', v1055, v1061)
    g.out_('F22_WaterCausticMap_uv', v1062, True)
    v1063 = g.inp('F22_WaterCausticMap', True, (1.0, 1.0, 1.0))
    v1064 = g.inp('F22_WaterCausticMap_alpha', False, 1.0)
    v1065 = g.sep(v1063)
    v1066 = g.math('MULTIPLY', v1065[0], v804)
    v1067 = g.bc(v1066)
    v1068 = g.vmath('MULTIPLY', v802, v1067)
    v1069 = g.vmath('ADD', v1051, v1068)
    v1070 = g.math('MULTIPLY', v597, 2.5)
    v1071 = g.clampn(v1070)
    v1072 = g.mixv(v1071, v802, v809)
    v1073 = g.mixv(v597, v802, v771)
    v1074 = g.mixv(v1048, v1072, v1073)
    v1075 = g.math('MULTIPLY', 0.20000000298023224, v1048)
    v1076 = g.math('ADD', 1, v1075)
    v1077 = g.bc(v1076)
    v1078 = g.vmath('MULTIPLY', v1074, v1077)
    v1079 = g.vmath('SCALE', v820, s=-1.0)
    v1080 = g.math('MULTIPLY', v597, v716)
    v1081 = g.bc(v1080)
    v1082 = g.vmath('MULTIPLY', v1079, v1081)
    v1083 = g.bc(v821)
    v1084 = g.vmath('MULTIPLY', v1082, v1083)
    v1085 = g.math('EXPONENT', v1084, 0.0)
    v1086 = g.bc(v1085)
    v1087 = g.vmath('MULTIPLY', v1078, v1086)
    v1088 = g.comb(v708[0], v708[1], 0.0)
    v1089 = g.vmath('LENGTH', v1088)
    v1090 = g.math('MULTIPLY', v597, v930)
    v1091 = g.clampn(v1090)
    v1092 = g.mixf(v1091, v929, 1)
    v1093 = g.math('POWER', v1015, v934)
    v1094 = g.mixf(v1093, v929, 1)
    v1095 = g.math('MAXIMUM', v1092, v1094)
    v1096 = g.math('SUBTRACT', v716, v1089)
    v1097 = g.clampn(v1096)
    v1098 = g.mixf(v1097, v938, 1)
    v1099 = g.math('MULTIPLY', v1095, v1098)
    v1100 = g.mixf(v943, 1, 0.699999988079071)
    v1101 = g.math('MULTIPLY', v1099, v1100)
    v1102 = g.clampn(v1101, v947, v948)
    v1103 = g.math('MULTIPLY', v1102, 0.8999999761581421)
    v1104 = g.math('MULTIPLY', v597, 3.5)
    v1105 = g.clampn(v1104)
    v1106 = g.mixv(v1105, v1069, v1087)
    v1107 = g.bc(v836[2])
    v1108 = g.vmath('MULTIPLY', v1028, v1107)
    v1109 = g.bc(v841[1])
    v1110 = g.vmath('MULTIPLY', v1108, v1109)
    v1111 = g.math('MULTIPLY', v844, 1.2000000476837158)
    v1112 = g.math('POWER', v1015, v1111)
    v1113 = g.math('MULTIPLY', 0.9700000286102295, v1112)
    v1114 = g.math('ADD', 0.029999999329447746, v1113)
    v1115 = g.math('MULTIPLY', v1114, v849)
    v1116 = g.math('MULTIPLY', v1115, 1.2000000476837158)
    v1117 = g.mixv(v1116, v1106, v1110)
    v1118 = g.clampn(v1030)
    v1119 = g.mixv(v1118, v1117, v809)
    v1120 = g.math('MULTIPLY', v708[1], 0.5)
    v1121 = g.math('ADD', v1120, 0.5)
    v1122 = g.clampn(v1121)
    v1123 = g.math('SUBTRACT', 1, v1122)
    v1124 = g.mixv(v1123, v1119, v809)
    v1125 = g.math('SUBTRACT', 1, v1103)
    v1126 = g.bc(v1125)
    v1127 = g.vmath('MULTIPLY', v1051, v1126)
    v1128 = g.mixv(v606, v1124, v916)
    v1129 = g.mixv(v606, v1012, v737)
    v1130 = g.mixf(v606, 0.8999999761581421, v921)
    v1131 = g.mixf(v606, 0.019999999552965164, v924)
    v1132 = g.mixv(v606, v1127, v928)
    v1133 = g.mixf(v606, v1103, v951)
    v1134 = g.mixf(v606, v952, v609)
    v1135 = g.mixv(v606, v988, v698)
    v1136 = g.mixv(v606, v1002, v707)
    v1137 = g.mixv(v606, v1012, v737)
    v1138 = g.mixf(v606, v1015, v740)
    v1139 = g.mixv(v606, v1028, v753)
    v1140 = g.mixf(v606, v1030, v755)
    v1141 = g.mixf(v606, v1031, v757)
    v1142 = g.mixf(v606, v1048, v778)
    v1143 = g.mixv(v606, v1051, v784)
    v1144 = g.mixf(v606, v1065[0], v801[0])
    v1145 = g.mixv(v606, v1069, v808)
    v1146 = g.mixv(v606, v1087, v830)
    v1147 = g.mixv(v606, v1117, v852)
    v1148 = g.mixf(v606, v1089, v854)
    v1149 = g.mixv(v599, v197, v1129)
    v1150 = g.mixv(v599, v482, v1128)
    v1151 = g.mixf(v599, v483, v1130)
    v1152 = g.mixf(v599, v590, v1133)
    v1153 = g.mixv(v599, (0, 0, 0), v1128)
    v1154 = g.mixv(v599, (0, 0, 0), v1132)
    v1155 = g.inp('_Specular', False, 1.0)
    v1156 = g.math('MULTIPLY', 0.07999999821186066, v1155)
    v1157 = g.math('MULTIPLY', 0.07999999821186066, v1155)
    v1158 = g.math('MULTIPLY', 0.07999999821186066, v1155)
    v1159 = g.comb(v1156, v1157, v1158)
    v1160 = g.math('MULTIPLY', 1, -1.0)
    v1161 = g.math('MULTIPLY', 0.027499999850988388, -1.0)
    v1162 = g.math('MULTIPLY', 0.5720000267028809, -1.0)
    v1163 = g.comb(v1160, v1161, v1162)
    v1164 = g.bc(v1151)
    v1165 = g.vmath('MULTIPLY', v1163, v1164)
    v1166 = g.math('MULTIPLY', 0.02199999988079071, v1151)
    v1167 = g.math('MULTIPLY', 0.03999999910593033, -1.0)
    v1168 = g.vmath('ADD', v1165, (1, 0.042500000447034836, 1.0399999618530273))
    v1169 = g.math('ADD', v1166, v1167)
    v1170 = g.math('MULTIPLY', 1.0399999618530273, -1.0)
    v1171 = g.comb(v1170, 1.0399999618530273, 0.0)
    v1172 = g.sep(v1168)
    v1173 = g.math('MULTIPLY', v1172[0], v1172[0])
    v1174 = g.math('MULTIPLY', 9.279999732971191, -1.0)
    v1175 = g.math('MAXIMUM', 0, v452)
    v1176 = g.math('MULTIPLY', v1174, v1175)
    v1177 = g.math('POWER', 2.0, v1176)
    v1178 = g.math('MINIMUM', v1173, v1177)
    v1179 = g.math('MULTIPLY', v1178, v1172[0])
    v1180 = g.math('ADD', v1179, v1172[1])
    v1181 = g.comb(v1180, v1180, 0.0)
    v1182 = g.vmath('MULTIPLY', v1171, v1181)
    v1183 = g.comb(v1172[2], v1169, 0.0)
    v1184 = g.vmath('ADD', v1182, v1183)
    v1185 = g.vmath('SCALE', v142, s=-1.0)
    v1186 = g.vmath('DOT_PRODUCT', v1149, v1185)
    v1187 = g.math('MULTIPLY', 2.0, v1186)
    v1188 = g.vmath('SCALE', v1149, s=v1187)
    v1189 = g.vmath('SUBTRACT', v1185, v1188)
    v1190 = g.math('SUBTRACT', v226[0], 1)
    v1191 = g.math('MAXIMUM', v1151, 0.0010000000474974513)
    v1192 = g.math('LOGARITHM', v1191, 2.0)
    v1193 = g.math('MULTIPLY', 1.2000000476837158, v1192)
    v1194 = g.math('SUBTRACT', 1, v1193)
    v1195 = g.math('SUBTRACT', v1190, v1194)
    v1196 = g.u2b(v1189)
    g.out_('F23_IBL_ReflectionProbeCube_dir', v1196, True)
    g.out_('F23_IBL_ReflectionProbeCube_mip', v1195, False)
    v1197 = g.inp('F23_IBL_ReflectionProbeCube', True, (0.2159, 0.2159, 0.2159))
    v1198 = g.inp('F23_IBL_ReflectionProbeCube_alpha', False, 1.0)
    v1199 = g.math('GREATER_THAN', v562, 0.5)
    v1200 = g.mixv(v1199, v1150, v591)
    v1201 = g.vmath('MULTIPLY', v1200, v30)
    v1202 = g.bc(v841[0])
    v1203 = g.vmath('MULTIPLY', v1201, v1202)
    v1204 = g.bc(v836[2])
    v1205 = g.vmath('MULTIPLY', v1197, v1204)
    v1206 = g.bc(v841[1])
    v1207 = g.vmath('MULTIPLY', v1205, v1206)
    v1208 = g.sep(v1184)
    v1209 = g.bc(v1208[0])
    v1210 = g.vmath('MULTIPLY', v1159, v1209)
    v1211 = g.math('MULTIPLY', v1155, 4)
    v1212 = g.clampn(v1211)
    v1213 = g.math('MULTIPLY', v1212, v1208[1])
    v1214 = g.bc(v1213)
    v1215 = g.vmath('MULTIPLY', (1, 1, 1), v1214)
    v1216 = g.vmath('ADD', v1210, v1215)
    v1217 = g.vmath('MULTIPLY', v1207, v1216)
    v1218 = g.vmath('ADD', v1203, v1217)
    v1219 = g.inp('_MatcapMapStrength', False, 0.2)
    v1220 = g.bc(v1219)
    v1221 = g.vmath('MULTIPLY', v205, v1220)
    v1222 = g.inp('_MatCapIgnorePostExposure', False, 1.0)
    v1223 = g.math('SUBTRACT', 1, v1222)
    v1224 = g.math('MULTIPLY', v423[1], v1222)
    v1225 = g.math('ADD', v1223, v1224)
    v1226 = g.bc(v1225)
    v1227 = g.vmath('MULTIPLY', v1221, v1226)
    v1228 = g.inp('_EnableMatcap', False, 0.0)
    v1229 = g.math('GREATER_THAN', v1228, 0.5)
    v1230 = g.math('SUBTRACT', 1.0, v1229)
    v1231 = g.mixv(v1230, v1227, (0, 0, 0))
    v1232 = g.bc(v836[2])
    v1233 = g.vmath('MULTIPLY', v234, v1232)
    v1234 = g.bc(v841[1])
    v1235 = g.vmath('MULTIPLY', v1233, v1234)
    v1236 = g.inp('_RefractionColor', True, (1.0, 1.0, 1.0))
    v1237 = g.inp('_RefractionColor_w', False, 1.0)
    v1238 = g.vmath('MULTIPLY', v1235, v1236)
    v1239 = g.inp('_RefractionStrength', False, 1.0)
    v1240 = g.bc(v1239)
    v1241 = g.vmath('MULTIPLY', v1238, v1240)
    v1242 = g.bc(v210)
    v1243 = g.vmath('MULTIPLY', v1241, v1242)
    v1244 = g.inp('_FresnelColor', True, (1.0, 1.0, 1.0))
    v1245 = g.inp('_FresnelColor_w', False, 1.0)
    v1246 = g.math('MULTIPLY', v1245, v443)
    v1247 = g.mixv(v1246, v1150, v1244)
    v1248 = g.inp('_Use_VerexGAsFresnelOpacity', False, 0.0)
    v1249 = g.mixf(v1248, 1, v172[1])
    v1250 = g.bc(v1249)
    v1251 = g.vmath('MULTIPLY', v1247, v1250)
    v1252 = g.vmath('ADD', v1231, v1243)
    v1253 = g.vmath('ADD', v1252, v1251)
    v1254 = g.math('GREATER_THAN', v455, 0.5)
    v1255 = g.math('COMPARE', v476, 0, 1e-05)
    v1256 = g.math('SUBTRACT', 1.0, v1255)
    v1257 = g.math('MULTIPLY', v1254, v1256)
    v1258 = g.bc(v172[0])
    v1259 = g.vmath('MULTIPLY', v1253, v1258)
    v1260 = g.mixv(v1257, v1253, v1259)
    v1261 = g.vmath('ADD', v1218, v429)
    v1262 = g.vmath('ADD', v1261, v1260)
    v1263 = g.vmath('ADD', v1262, v1154)
    v1264 = g.math('GREATER_THAN', v486, 0.5)
    v1265 = g.math('SUBTRACT', 1, v1152)
    v1266 = g.bc(v1265)
    v1267 = g.vmath('MULTIPLY', v561, v1266)
    v1268 = g.vmath('ADD', v1263, v1267)
    v1269 = g.mixv(v1264, v1263, v1268)
    v1270 = g.bc(v1150)
    v1271 = g.bc(v1150)
    v1272 = g.math('COMPARE', v60, 0, 1e-05)
    v1273 = g.math('SUBTRACT', 1.0, v1272)
    v1274 = g.vmath('SUBTRACT', v20, v26)
    v1275 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v1276 = g.bc(v1275)
    v1277 = g.vmath('DIVIDE', v1274, v1276)
    v1278 = g.vmath('ADD', v1270, v1269)
    v1279 = g.vmath('LENGTH', v1277)
    v1280 = g.sep(v1277)
    v1281 = g.comb(v1280[0], v1280[2], 0.0)
    v1282 = g.vmath('LENGTH', v1281)
    v1283 = g.math('ABSOLUTE', v1280[1], 0.0)
    v1284 = g.math('MAXIMUM', v1282, v1283)
    v1285 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v1286 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v1287 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v1288 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v1289 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v1290 = g.inp('_RuriFogColor_w', False, 0.0)
    v1291 = g.group_named('RCE_RuriApplyFog', [('inColor', v1278), ('inColor_w', 1), ('sphericalVertexDistance', v1279), ('cylindricalVertexDistance', v1284), ('environmentalStart', v1285), ('environmentalEnd', v1286), ('renderDistanceStart', v1287), ('renderDistanceEnd', v1288), ('fogColor', v1289), ('fogColor_w', v1290)])
    v1292 = g.mixv(v1273, v1269, v1291[0])
    g.out_('ret_gBuffer0', v1292, True)
    g.out_('ret_gBuffer0_w', v1152, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v1269, True)
    g.out_('ret_color_w', v1152, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


def build_Ruri_Endfield_Scene_Leaf():
    t = _tree('Ruri Endfield Scene Leaf')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.vmath('NORMALIZE', v21)
    v133 = g.vmath('SUBTRACT', v14, v0)
    v134 = g.comb(v77, v77, 0.0)
    v135 = g.vmath('MULTIPLY', v134, v133)
    v136 = g.vmath('ADD', v135, v0)
    v137 = g.comb(v83[0], v83[1], 0.0)
    v138 = g.comb(v83[2], v82, 0.0)
    v139 = g.vmath('MULTIPLY', v136, v137)
    v140 = g.vmath('ADD', v139, v138)
    v141 = g.comb(v88, v88, 0.0)
    v142 = g.vmath('MULTIPLY', v141, v133)
    v143 = g.vmath('ADD', v142, v0)
    v144 = g.comb(v94[0], v94[1], 0.0)
    v145 = g.comb(v94[2], v93, 0.0)
    v146 = g.vmath('MULTIPLY', v143, v144)
    v147 = g.vmath('ADD', v146, v145)
    g.out_('F4_BaseColorMap_uv', v140, True)
    v148 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v149 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    g.out_('F5_NormalMap_uv', v147, True)
    v150 = g.inp('F5_NormalMap', True, (1.0, 1.0, 1.0))
    v151 = g.inp('F5_NormalMap_alpha', False, 1.0)
    v152 = g.math('MULTIPLY', v58, v149)
    v153 = g.sep(v11)
    v154 = g.clampn(v153[0], 0, 1)
    v155 = g.vmath('MULTIPLY', v148, v37)
    v156 = g.inp('_BaseColorBrighterScale', False, 1.0)
    v157 = g.bc(v156)
    v158 = g.vmath('MULTIPLY', v155, v157)
    v159 = g.vmath('MAXIMUM', v158, (0, 0, 0))
    v160 = g.vmath('MINIMUM', v159, (1, 1, 1))
    v161 = g.inp('_BaseColorTintCover', False, 0.0)
    v162 = g.mixv(v161, v160, v37)
    v163 = g.inp('_EnableNormalMap', False, 0.0)
    v164 = g.math('GREATER_THAN', v163, 0.5)
    v165 = g.math('MAXIMUM', 0, v164)
    v166 = g.sep(v150)
    v167 = g.math('MULTIPLY_ADD', v166[0], 2, -1)
    v168 = g.math('MULTIPLY_ADD', v166[1], 2, -1)
    v169 = g.mixf(v165, 0.5, v166[2])
    v170 = g.mixf(v165, 0, v167)
    v171 = g.mixf(v165, 0, v168)
    v172 = g.mixf(v165, 1, v151)
    v173 = g.inp('_NormalScale', False, 0.0)
    v174 = g.math('MULTIPLY', v170, v173)
    v175 = g.math('MULTIPLY', v171, v173)
    v176 = g.vmath('DOT_PRODUCT', v36, v21)
    v177 = g.math('LESS_THAN', v176, 0)
    v178 = g.mixf(v177, 1, -1)
    v179 = g.comb(v170, v171, 0.0)
    v180 = g.comb(v170, v171, 0.0)
    v181 = g.vmath('DOT_PRODUCT', v179, v180)
    v182 = g.math('MINIMUM', v181, 1)
    v183 = g.math('SUBTRACT', 1, v182)
    v184 = g.math('SQRT', v183, 0.0)
    v185 = g.math('MAXIMUM', v184, 1.0000000168623835E-16)
    v186 = g.math('MULTIPLY', v185, v178)
    v187 = g.math('GREATER_THAN', v5, 0)
    v188 = g.mixf(v187, -1, 1)
    v189 = g.vmath('CROSS_PRODUCT', v21, v22)
    v190 = g.bc(v188)
    v191 = g.vmath('MULTIPLY', v190, v189)
    v192 = g.bc(v186)
    v193 = g.vmath('MULTIPLY', v21, v192)
    v194 = g.bc(v174)
    v195 = g.vmath('MULTIPLY', v22, v194)
    v196 = g.vmath('ADD', v193, v195)
    v197 = g.bc(v175)
    v198 = g.vmath('MULTIPLY', v191, v197)
    v199 = g.vmath('ADD', v196, v198)
    v200 = g.vmath('NORMALIZE', v199)
    v201 = g.inp('_BendNormalUpward', False, 0.0)
    v202 = g.math('GREATER_THAN', v201, 0)
    v203 = g.math('MULTIPLY', 0, v202)
    v204 = g.mixv(v201, v200, (0, 1, 0))
    v205 = g.vmath('NORMALIZE', v204)
    v206 = g.mixv(v203, v200, v205)
    v207 = g.inp('_RoughnessMin', False, 0.0)
    v208 = g.inp('_RoughnessMax', False, 1.0)
    v209 = g.mixf(v169, v207, v208)
    v210 = g.clampn(v209, 0, 1)
    v211 = g.inp('_Metallic', False, 0.0)
    v212 = g.inp('_BaseTextureMapCount', False, 0.0)
    v213 = g.math('SUBTRACT', v212, 1)
    v214 = g.clampn(v213, 0, 1)
    v215 = g.mixf(v214, 0, v211)
    v216 = g.mixf(0, 0, v215)
    v217 = g.inp('_PorosityFactorX', False, 0.2)
    v218 = g.inp('_PorosityFactorZ', False, 0.0)
    v219 = g.inp('_PorosityFactorY', False, 0.4)
    v220 = g.math('MULTIPLY_ADD', v218, v216, v219)
    v221 = g.math('MULTIPLY_ADD', v217, v210, v220)
    v222 = g.clampn(v221, 0, 1)
    v223 = g.math('MULTIPLY', v222, 0.95)
    v224 = g.math('ADD', v223, 0.05)
    v225 = g.inp('_OcclusionStrength', False, 1.0)
    v226 = g.mixf(v225, 1, v154)
    v227 = g.clampn(v226, 0, 1)
    v228 = g.mixf(v225, 1, 1)
    v229 = g.inp('_TrunkVertexAoStrength', False, 1.0)
    v230 = g.mixf(v229, 1, v154)
    v231 = g.math('MULTIPLY', v228, v230)
    v232 = g.mixf(0, v227, v231)
    v233 = g.inp('_EnableVerticalNormalBoostAO', False, 0.0)
    v234 = g.math('GREATER_THAN', v233, 0.5)
    v235 = g.sep(v21)
    v236 = g.inp('_VerticalNormalThreshold', False, 0.0)
    v237 = g.math('SUBTRACT', v235[1], v236)
    v238 = g.math('SUBTRACT', 1, v236)
    v239 = g.math('MAXIMUM', v238, 0.0001)
    v240 = g.math('DIVIDE', v237, v239)
    v241 = g.clampn(v240, 0, 1)
    v242 = g.inp('_VerticalNormalBoostAO', False, 0.0)
    v243 = g.math('MULTIPLY', v241, v242)
    v244 = g.math('SUBTRACT', 1, v243)
    v245 = g.math('MULTIPLY', v232, v244)
    v246 = g.mixf(v234, v232, v245)
    v247 = g.clampn(v246, 0, 1)
    v248 = g.inp('_TransmissionDistanceFade', False, 0.0)
    v249 = g.math('GREATER_THAN', v248, 0.5)
    v250 = g.vmath('DISTANCE', v20, v26)
    v251 = g.math('SUBTRACT', 60, v250)
    v252 = g.math('DIVIDE', v251, 10)
    v253 = g.clampn(v252, 0, 1)
    v254 = g.mixf(v249, 1, v253)
    v255 = g.inp('_Transmission', False, 0.2)
    v256 = g.math('MULTIPLY', v255, v254)
    v257 = g.math('MULTIPLY', v256, v172)
    v258 = g.inp('_AoAffectTransmissionStart', False, 0.0)
    v259 = g.inp('_AoAffectTransmissionRange', False, 0.01)
    v260 = g.math('SUBTRACT', v246, v258)
    v261 = g.math('MAXIMUM', v259, 0.0001)
    v262 = g.math('DIVIDE', v260, v261)
    v263 = g.clampn(v262, 0, 1)
    v264 = g.math('MULTIPLY', v257, v263)
    v265 = g.inp('_SubsurfaceIntensity', False, 0.0)
    v266 = g.math('MULTIPLY', v265, v172)
    v267 = g.inp('_AoAffectSubsurfaceStart', False, 0.0)
    v268 = g.inp('_AoAffectSubsurfaceRange', False, 0.01)
    v269 = g.math('SUBTRACT', v246, v267)
    v270 = g.math('MAXIMUM', v268, 0.0001)
    v271 = g.math('DIVIDE', v269, v270)
    v272 = g.clampn(v271, 0, 1)
    v273 = g.math('MULTIPLY', v266, v272)
    v274 = g.math('MAXIMUM', v264, v273)
    v275 = g.clampn(v274, 0, 1)
    v276 = g.inp('_FakeDirectionalShadowStrength', False, 0.0)
    v277 = g.math('GREATER_THAN', v276, 0)
    v278 = g.vmath('NORMALIZE', v21)
    v279 = g.inp('_DiffuseUseVertexNormal', False, 1.0)
    v280 = g.mixv(v279, v206, v278)
    v281 = g.inp('_MainLightPosition', True, (0.0, 0.0, 0.0))
    v282 = g.inp('_MainLightPosition_w', False, 0.0)
    v283 = g.vmath('SCALE', v281, s=-1.0)
    v284 = g.vmath('DOT_PRODUCT', v280, v283)
    v285 = g.math('MULTIPLY_ADD', v284, 0.5, 0.5)
    v286 = g.inp('_FakeDirectionalShadowPow', False, 1.0)
    v287 = g.math('POWER', v285, v286)
    v288 = g.math('MULTIPLY', v287, v276)
    v289 = g.math('SUBTRACT', 1, v288)
    v290 = g.clampn(v289, 0, 1)
    v291 = g.inp('_OcclusionShadow', False, 0.0)
    v292 = g.mixf(v291, 1, v246)
    v293 = g.mixf(v292, 1, v290)
    v294 = g.bc(v293)
    v295 = g.vmath('MULTIPLY', v162, v294)
    v296 = g.mixv(v277, v162, v295)
    v297 = g.math('SUBTRACT', 1.0, 0)
    v298 = g.inp('_EnableCanopyColorRamp', False, 0.0)
    v299 = g.math('GREATER_THAN', v298, 0.5)
    v300 = g.math('MULTIPLY', v297, v299)
    v301 = g.sep(v2)
    v302 = g.clampn(v301[1], 0, 1)
    v303 = g.inp('_CanopyRampStartAtTop', False, 0.0)
    v304 = g.math('GREATER_THAN', v303, 0.5)
    v305 = g.math('SUBTRACT', 1, v302)
    v306 = g.mixf(v304, v302, v305)
    v307 = g.inp('_CanopyRampRange', False, 0.0)
    v308 = g.math('SUBTRACT', v306, v307)
    v309 = g.inp('_CanopyRampTransitionRange', False, 0.01)
    v310 = g.math('MAXIMUM', v309, 0.0001)
    v311 = g.math('DIVIDE', v308, v310)
    v312 = g.clampn(v311, 0, 1)
    v313 = g.inp('_CanopyRampIntensity', False, 1.0)
    v314 = g.math('MULTIPLY', v312, v313)
    v315 = g.inp('_CanopyRampColor', True, (1.0, 1.0, 1.0))
    v316 = g.inp('_CanopyRampColor_w', False, 1.0)
    v317 = g.inp('_CanopyRampColorBrighterScale', False, 1.0)
    v318 = g.bc(v317)
    v319 = g.vmath('MULTIPLY', v315, v318)
    v320 = g.vmath('MAXIMUM', v319, (0, 0, 0))
    v321 = g.vmath('MINIMUM', v320, (1, 1, 1))
    v322 = g.vmath('MULTIPLY', v296, v321)
    v323 = g.inp('_CanopyRampColorCover', False, 0.0)
    v324 = g.mixv(v323, v322, v321)
    v325 = g.mixv(v314, v296, v324)
    v326 = g.mixv(v300, v296, v325)
    v327 = g.math('SUBTRACT', 1.0, 0)
    v328 = g.inp('_EnableAoTuneColor', False, 0.0)
    v329 = g.math('GREATER_THAN', v328, 0.5)
    v330 = g.math('MULTIPLY', v327, v329)
    v331 = g.inp('_FlipAoMask', False, 0.0)
    v332 = g.math('GREATER_THAN', v331, 0.5)
    v333 = g.math('SUBTRACT', 1, v154)
    v334 = g.mixf(v332, v154, v333)
    v335 = g.inp('_AoMaskTuneColorRampStart', False, 0.0)
    v336 = g.math('SUBTRACT', v334, v335)
    v337 = g.inp('_AoMaskTuneColorRampRange', False, 0.2)
    v338 = g.math('MAXIMUM', v337, 0.0001)
    v339 = g.math('DIVIDE', v336, v338)
    v340 = g.clampn(v339, 0, 1)
    v341 = g.inp('_AoMaskTuneColorIntensity', False, 1.0)
    v342 = g.math('MULTIPLY', v340, v341)
    v343 = g.inp('_AoMaskTuneColor', True, (1.0, 1.0, 1.0))
    v344 = g.inp('_AoMaskTuneColor_w', False, 1.0)
    v345 = g.inp('_AoMaskTuneColorBrighterScale', False, 1.0)
    v346 = g.bc(v345)
    v347 = g.vmath('MULTIPLY', v343, v346)
    v348 = g.vmath('MAXIMUM', v347, (0, 0, 0))
    v349 = g.vmath('MINIMUM', v348, (1, 1, 1))
    v350 = g.vmath('MULTIPLY', v326, v349)
    v351 = g.inp('_AoMaskTuneColorCover', False, 0.0)
    v352 = g.mixv(v351, v350, v349)
    v353 = g.mixv(v342, v326, v352)
    v354 = g.mixv(v330, v326, v353)
    v355 = g.mixf(v330, v314, v342)
    v356 = g.math('SUBTRACT', 1.0, 0)
    v357 = g.inp('_EnableBlendColor', False, 0.0)
    v358 = g.math('GREATER_THAN', v357, 0.5)
    v359 = g.math('MULTIPLY', v356, v358)
    v360 = g.inp('_BlendWithVertexNormal', False, 0.0)
    v361 = g.math('GREATER_THAN', v360, 0.5)
    v362 = g.vmath('NORMALIZE', v21)
    v363 = g.mixv(v361, v206, v362)
    v364 = g.sep(v363)
    v365 = g.math('MULTIPLY_ADD', v364[1], 0.5, 0.5)
    v366 = g.inp('_BlendNormalAdd', False, 0.0)
    v367 = g.math('ADD', v365, v366)
    v368 = g.clampn(v367, 0, 1)
    v369 = g.inp('_BlendColor', True, (1.0, 1.0, 1.0))
    v370 = g.inp('_BlendColor_w', False, 1.0)
    v371 = g.vmath('MULTIPLY', v354, v369)
    v372 = g.inp('_BlendNormalPower', False, 1.0)
    v373 = g.math('MAXIMUM', v372, 0.001)
    v374 = g.math('POWER', v368, v373)
    v375 = g.mixv(v374, v354, v371)
    v376 = g.mixv(v359, v354, v375)
    v377 = g.mixf(v359, v355, v368)
    v378 = g.inp('_EnableTrunkRamp', False, 0.0)
    v379 = g.math('GREATER_THAN', v378, 0.5)
    v380 = g.math('MULTIPLY', 0, v379)
    v381 = g.clampn(v301[1], 0, 1)
    v382 = g.inp('_TrunkRampRange', False, 0.0)
    v383 = g.math('SUBTRACT', v382, v381)
    v384 = g.inp('_TrunkRampTransitionRange', False, 0.01)
    v385 = g.math('MAXIMUM', v384, 0.0001)
    v386 = g.math('DIVIDE', v383, v385)
    v387 = g.clampn(v386, 0, 1)
    v388 = g.inp('_TrunkRampIntensity', False, 1.0)
    v389 = g.math('MULTIPLY', v387, v388)
    v390 = g.inp('_TrunkRampColor', True, (1.0, 1.0, 1.0))
    v391 = g.inp('_TrunkRampColor_w', False, 1.0)
    v392 = g.vmath('MULTIPLY', v376, v390)
    v393 = g.mixv(v389, v376, v392)
    v394 = g.mixv(v380, v376, v393)
    v395 = g.mixf(v380, v306, v381)
    v396 = g.mixf(v380, v377, v389)
    v397 = g.inp('_EnableEmissiveMap', False, 0.0)
    v398 = g.math('GREATER_THAN', v397, 0.5)
    v399 = g.inp('_EmissiveUVSet', False, 0.0)
    v400 = g.math('GREATER_THAN', v399, 0.5)
    v401 = g.mixv(v400, v0, v14)
    v402 = g.inp('_EmissiveMap_ST', True, (1.0, 1.0, 0.0))
    v403 = g.inp('_EmissiveMap_ST_w', False, 0.0)
    v404 = g.sep(v402)
    v405 = g.comb(v404[0], v404[1], 0.0)
    v406 = g.comb(v404[2], v403, 0.0)
    v407 = g.vmath('MULTIPLY', v401, v405)
    v408 = g.vmath('ADD', v407, v406)
    g.out_('F6_EmissiveMap_uv', v408, True)
    v409 = g.inp('F6_EmissiveMap', True, (1.0, 1.0, 1.0))
    v410 = g.inp('F6_EmissiveMap_alpha', False, 1.0)
    v411 = g.inp('_EmissiveMaskChannel', False, 0.0)
    v412 = g.math('LESS_THAN', v411, 0.5)
    v413 = g.sep(v409)
    v414 = g.inp('_EmissiveColorR', True, (0.0, 0.0, 0.0))
    v415 = g.inp('_EmissiveColorR_w', False, 1.0)
    v416 = g.bc(v413[0])
    v417 = g.vmath('MULTIPLY', v416, v414)
    v418 = g.inp('_EmissiveColorG', True, (0.0, 0.0, 0.0))
    v419 = g.inp('_EmissiveColorG_w', False, 0.0)
    v420 = g.bc(v413[1])
    v421 = g.vmath('MULTIPLY', v420, v418)
    v422 = g.vmath('ADD', v417, v421)
    v423 = g.inp('_EmissiveColorB', True, (0.0, 0.0, 0.0))
    v424 = g.inp('_EmissiveColorB_w', False, 0.0)
    v425 = g.bc(v413[2])
    v426 = g.vmath('MULTIPLY', v425, v423)
    v427 = g.vmath('ADD', v422, v426)
    v428 = g.inp('_EmissiveColorA', True, (0.0, 0.0, 0.0))
    v429 = g.inp('_EmissiveColorA_w', False, 0.0)
    v430 = g.bc(v410)
    v431 = g.vmath('MULTIPLY', v430, v428)
    v432 = g.vmath('ADD', v427, v431)
    v433 = g.math('LESS_THAN', v411, 1.5)
    v434 = g.vmath('MULTIPLY', v409, v414)
    v435 = g.bc(v149)
    v436 = g.vmath('MULTIPLY', v435, v414)
    v437 = g.mixv(v433, v436, v434)
    v438 = g.mixv(v412, v437, v432)
    v439 = g.inp('_AlbedoAffectEmissive', False, 1.0)
    v440 = g.math('LESS_THAN', v439, 0.5)
    v441 = g.vmath('MULTIPLY', v438, v394)
    v442 = g.mixv(v440, v438, v441)
    v443 = g.mixv(v398, (0, 0, 0), v442)
    v444 = g.inp('_EnableVertColorEmissive', False, 0.0)
    v445 = g.math('GREATER_THAN', v444, 0.5)
    v446 = g.inp('_VertColorEmissiveChannelVector', True, (1.0, 0.0, 0.0))
    v447 = g.inp('_VertColorEmissiveChannelVector_w', False, 0.0)
    v448 = g.vmath('DOT_PRODUCT', v11, v446)
    v449 = g.inp('_VertColorEmissiveFlip', False, 0.0)
    v450 = g.math('GREATER_THAN', v449, 0.5)
    v451 = g.math('SUBTRACT', 1, v448)
    v452 = g.mixf(v450, v448, v451)
    v453 = g.inp('_VertColorEmissiveBias', False, 0.0)
    v454 = g.math('ADD', v452, v453)
    v455 = g.clampn(v454, 0, 1)
    v456 = g.inp('_VertColorEmissiveColor', True, (0.0, 0.0, 0.0))
    v457 = g.inp('_VertColorEmissiveColor_w', False, 1.0)
    v458 = g.bc(v455)
    v459 = g.vmath('MULTIPLY', v456, v458)
    v460 = g.inp('_VertColorEmissiveAlbedoAffect', False, 1.0)
    v461 = g.math('LESS_THAN', v460, 0.5)
    v462 = g.vmath('MULTIPLY', v459, v394)
    v463 = g.mixv(v461, v459, v462)
    v464 = g.vmath('ADD', v443, v463)
    v465 = g.mixv(v445, v443, v464)
    v466 = g.mixf(v445, v396, v455)
    v467 = g.inp('_CrossCardViewCulling', False, 0.0)
    v468 = g.math('GREATER_THAN', v467, 0.5)
    v469 = g.vmath('SUBTRACT', v26, v20)
    v470 = g.vmath('NORMALIZE', v469)
    v471 = g.vmath('NORMALIZE', v21)
    v472 = g.vmath('DOT_PRODUCT', v471, v470)
    v473 = g.math('ABSOLUTE', v472, 0.0)
    v474 = g.inp('_CrossCardViewCullingThreshold', False, 0.4)
    v475 = g.math('SUBTRACT', v473, v474)
    v476 = g.inp('_CrossCardViewCullingFadeValue', False, 0.5)
    v477 = g.math('MAXIMUM', v476, 0.0001)
    v478 = g.math('DIVIDE', v475, v477)
    v479 = g.clampn(v478, 0, 1)
    v480 = g.math('MULTIPLY', v152, v479)
    v481 = g.mixf(v468, v152, v480)
    v482 = g.mixf(v121, v58, v481)
    v483 = g.mixv(v121, v65, v394)
    v484 = g.mixf(v121, v114, v210)
    v485 = g.mixf(v121, v115, v216)
    v486 = g.mixf(v121, v116, v247)
    v487 = g.mixf(v121, v117, v224)
    v488 = g.mixv(v121, v36, v206)
    v489 = g.mixf(v121, 0.0, v275)
    v490 = g.mixv(v121, (0.0, 0.0, 0.0), v162)
    v491 = g.mixv(v121, (0, 0, 0), v465)
    v492 = g.mixv(v121, v132, v206)
    v493 = g.comb(v487, v487, v487)
    v494 = g.math('SUBTRACT', 1, v484)
    v495 = g.vmath('NORMALIZE', v488)
    v496 = g.vmath('DOT_PRODUCT', v495, v28)
    v497 = g.math('MAXIMUM', v496, 0)
    v498 = g.math('MULTIPLY', 0.08, v487)
    v499 = g.math('MULTIPLY', 0.08, v487)
    v500 = g.math('MULTIPLY', 0.08, v487)
    v501 = g.comb(v498, v499, v500)
    v502 = g.mixv(v485, v501, v483)
    v503 = g.math('SUBTRACT', 1, v485)
    v504 = g.bc(v503)
    v505 = g.vmath('MULTIPLY', v483, v504)
    v506 = g.inp('_UseThinFilm', False, 0.0)
    v507 = g.math('GREATER_THAN', v506, 0.5)
    v508 = g.inp('_ThinFilmIOR', False, 1.4)
    v509 = g.inp('_ThinFilmThickness', False, 0.5)
    v510 = g.math('MULTIPLY', v509, 1000)
    v511 = g.inp('M_PI', False, 0.0)
    v512 = g.group_named('RCE_RuriEvalIridescence', [('outsideIor', 1), ('eta2', v508), ('cosTheta1', v497), ('iridescenceThickness', v510), ('baseF0', v502), ('M_PI', v511)])
    v513 = g.inp('_ThinFilmWeight', False, 0.0)
    v514 = g.inp('_ThinFilmIntensity', False, 1.0)
    v515 = g.math('MULTIPLY', v513, v514)
    v516 = g.clampn(v515)
    v517 = g.mixv(v516, v502, v512[0])
    v518 = g.mixv(v507, v502, v517)
    v519 = g.inp('_SubsurfaceShadingMode', False, 0.0)
    v520 = g.math('LESS_THAN', v519, 0.5)
    v521 = g.inp('_SubsurfaceColor', True, (0.8, 0.8, 0.8))
    v522 = g.inp('_SubsurfaceColor_w', False, 1.0)
    v523 = g.vmath('MULTIPLY', v521, v483)
    v524 = g.mixv(v520, v523, v521)
    v525 = g.inp('_MaxSubsurfaceThickness', False, 1.0)
    v526 = g.inp('_UseSubsurfaceThicknessMap', False, 0.0)
    v527 = g.math('GREATER_THAN', v526, 0.5)
    v528 = g.inp('_MinSubsurfaceThickness', False, 0.0)
    g.out_('F7_SubsurfaceMap_uv', v0, True)
    v529 = g.inp('F7_SubsurfaceMap', True, (1.0, 1.0, 1.0))
    v530 = g.inp('F7_SubsurfaceMap_alpha', False, 1.0)
    v531 = g.sep(v529)
    v532 = g.mixf(v531[0], v528, v525)
    v533 = g.mixf(v527, v525, v532)
    v534 = g.group_named('RCE_HgEnvBRDF', [('roughness', v484), ('NoV', v497), ('f0', v518)])
    v535 = g.vmath('SCALE', v28, s=-1.0)
    v536 = g.vmath('DOT_PRODUCT', v495, v535)
    v537 = g.math('MULTIPLY', 2.0, v536)
    v538 = g.vmath('SCALE', v495, s=v537)
    v539 = g.vmath('SUBTRACT', v535, v538)
    v540 = g.inp('_UseCustomIBL', False, 0.0)
    v541 = g.math('GREATER_THAN', v540, 0.5)
    v542 = g.math('MULTIPLY', 0.7, v484)
    v543 = g.math('SUBTRACT', 1.7, v542)
    v544 = g.math('MULTIPLY', v484, v543)
    v545 = g.math('MULTIPLY', v544, 6)
    v546 = g.u2b(v539)
    g.out_('F8_IBL_CustomIBL_dir', v546, True)
    g.out_('F8_IBL_CustomIBL_mip', v545, False)
    v547 = g.inp('F8_IBL_CustomIBL', True, (0.2159, 0.2159, 0.2159))
    v548 = g.inp('F8_IBL_CustomIBL_alpha', False, 1.0)
    v549 = g.inp('_CustomIBLIntensity', False, 1.0)
    v550 = g.bc(v549)
    v551 = g.vmath('MULTIPLY', v547, v550)
    g.out_('C1_SpecularRadiance_direction', v539, True)
    g.out_('C1_SpecularRadiance_position', v20, True)
    g.out_('C1_SpecularRadiance_roughness', v484, False)
    v552 = g.inp('C1_SpecularRadiance', True, (0.2159, 0.2159, 0.2159))
    v553 = g.mixv(v541, v552, v551)
    v554 = g.inp('_PlanarReflection', False, 0.0)
    v555 = g.math('GREATER_THAN', v554, 0.5)
    g.out_('F9_PlanarReflectionTexture_uv', v29, True)
    v556 = g.inp('F9_PlanarReflectionTexture', True, (1.0, 1.0, 1.0))
    v557 = g.inp('F9_PlanarReflectionTexture_alpha', False, 1.0)
    v558 = g.inp('_PlanarReflectionTint', True, (1.0, 1.0, 1.0))
    v559 = g.inp('_PlanarReflectionTint_w', False, 1.0)
    v560 = g.vmath('MULTIPLY', v556, v558)
    v561 = g.mixv(v559, v553, v560)
    v562 = g.mixv(v555, v553, v561)
    v563 = g.inp('_EnableSubsurface', False, 0.0)
    v564 = g.math('GREATER_THAN', v563, 0.5)
    v565 = g.inp('_SubsurfaceIndirect', False, 1.0)
    v566 = g.comb(v565, v565, v565)
    v567 = g.vmath('MULTIPLY', v524, v566)
    v568 = g.vmath('ADD', v567, v505)
    v569 = g.mixv(v564, v505, v568)
    v570 = g.vmath('MULTIPLY', v569, v30)
    v571 = g.bc(v486)
    v572 = g.vmath('MULTIPLY', v570, v571)
    v573 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v574 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v575 = g.sep(v573)
    v576 = g.bc(v575[0])
    v577 = g.vmath('MULTIPLY', v572, v576)
    v578 = g.comb(v534[0], v534[0], v534[0])
    v579 = g.comb(v534[1], v534[1], v534[1])
    v580 = g.vmath('MULTIPLY', v518, v578)
    v581 = g.vmath('ADD', v580, v579)
    v582 = g.vmath('MULTIPLY', v581, v562)
    v583 = g.bc(v575[1])
    v584 = g.vmath('MULTIPLY', v582, v583)
    v585 = g.vmath('ADD', v577, v584)
    v586 = g.inp('C2_MainLight_direction', True, (0.0, 0.0, 0.0))
    v587 = g.inp('C2_MainLight_color', True, (0.0, 0.0, 0.0))
    v588 = g.inp('C2_MainLight_distanceAttenuation', False, 0.0)
    v589 = g.inp('C2_MainLight_shadowAttenuation', False, 0.0)
    v590 = g.inp('C2_MainLight_layerMask', False, 0.0)
    v591 = g.inp('_MainLightOcclusionProbes', True, (0.0, 0.0, 0.0))
    v592 = g.inp('_MainLightOcclusionProbes_w', False, 0.0)
    v593 = g.vmath('ADD', v586, v28)
    v594 = g.vmath('DOT_PRODUCT', v593, v593)
    v595 = g.math('MAXIMUM', v594, 1E-08)
    v596 = g.math('INVERSE_SQRT', v595, 0.0)
    v597 = g.bc(v596)
    v598 = g.vmath('MULTIPLY', v593, v597)
    v599 = g.vmath('DOT_PRODUCT', v586, v495)
    v600 = g.clampn(v599)
    v601 = g.vmath('DOT_PRODUCT', v495, v598)
    v602 = g.clampn(v601)
    v603 = g.vmath('DOT_PRODUCT', v28, v598)
    v604 = g.clampn(v603)
    v605 = g.group_named('RCE_HgDirectLightEnergy', [('roughness', v484), ('f0', v518), ('NoL', v600), ('NoH', v602), ('NoV', v497), ('VoH', v604)])
    v606 = g.math('MULTIPLY', v588, 1.0)
    v607 = g.comb(v600, v600, v600)
    v608 = g.bc(v600)
    v609 = g.vmath('MULTIPLY', v505, v608)
    v610 = g.vmath('MULTIPLY', v605[0], v607)
    v611 = g.vmath('ADD', v610, v609)
    v612 = g.math('GREATER_THAN', v563, 0.5)
    v613 = g.vmath('DOT_PRODUCT', v586, v495)
    v614 = g.vmath('DOT_PRODUCT', v28, v586)
    v615 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v616 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v617 = g.group_named('RCE_HgSssLobe', [('amount', v533), ('rawNoL', v613), ('VdotL', v614), ('selfShadowBias', v615), ('enableSelfShadowBias', v616)])
    v618 = g.bc(v617[0])
    v619 = g.vmath('MULTIPLY', v618, v524)
    v620 = g.vmath('ADD', v611, v619)
    v621 = g.mixv(v612, v611, v620)
    v622 = g.bc(v606)
    v623 = g.vmath('MULTIPLY', v587, v622)
    v624 = g.vmath('MULTIPLY', v621, v623)
    v625 = g.vmath('ADD', v585, v624)
    v626 = g.inp('C3_AdditionalLightCount', False, 0.0)
    v627 = g.math('SUBTRACT', v626, 0)
    v628 = g.math('CEIL', v627, 0.0)
    v629 = g.math('MAXIMUM', v628, 0.0)
    g.out_('Z0_it', v629, False)
    g.out_('Z0_s_H', v598, True)
    g.out_('Z0_s_L', v586, True)
    g.out_('Z0_s_LV', v593, True)
    g.out_('Z0_s_N', v495, True)
    g.out_('Z0_s_NoH', v602, False)
    g.out_('Z0_s_NoL', v600, False)
    g.out_('Z0_s_NoV', v497, False)
    g.out_('Z0_s_P', v20, True)
    g.out_('Z0_s_V', v28, True)
    g.out_('Z0_s_VoH', v604, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_color', v625, True)
    g.out_('Z0_s_energy', v605[0], True)
    g.out_('Z0_s_f0', v518, True)
    g.out_('Z0_s_inputData_bakedGI', v30, True)
    g.out_('Z0_s_inputData_fogCoord', 0, False)
    g.out_('Z0_s_inputData_normalWS', v488, True)
    g.out_('Z0_s_inputData_normalizedScreenSpaceUV', v29, True)
    g.out_('Z0_s_inputData_positionCS', v17, True)
    g.out_('Z0_s_inputData_positionCS_w', v18, False)
    g.out_('Z0_s_inputData_positionWS', v20, True)
    g.out_('Z0_s_inputData_shadowCoord', (0.0, 0.0, 0.0), True)
    g.out_('Z0_s_inputData_shadowCoord_w', 0.0, False)
    g.out_('Z0_s_inputData_shadowMask', (1, 1, 1), True)
    g.out_('Z0_s_inputData_shadowMask_w', 1, False)
    g.out_('Z0_s_inputData_vertexLighting', (0, 0, 0), True)
    g.out_('Z0_s_inputData_viewDirectionWS', v28, True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_roughness', v484, False)
    g.out_('Z0_s_sssAmount', v533, False)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_diffuse', v505, True)
    g.out_('Z0_r_sssTint', v524, True)
    g.out_('Z0_r_pixelLightCount', v626, False)
    v630 = g.inp('Z0_o_H', True)
    v631 = g.inp('Z0_o_L', True)
    v632 = g.inp('Z0_o_LV', True)
    v633 = g.inp('Z0_o_N', True)
    v634 = g.inp('Z0_o_NoH', False)
    v635 = g.inp('Z0_o_NoL', False)
    v636 = g.inp('Z0_o_NoV', False)
    v637 = g.inp('Z0_o_P', True)
    v638 = g.inp('Z0_o_V', True)
    v639 = g.inp('Z0_o_VoH', False)
    v640 = g.inp('Z0_o_Lloop0', False)
    v641 = g.inp('Z0_o_color', True)
    v642 = g.inp('Z0_o_energy', True)
    v643 = g.inp('Z0_o_f0', True)
    v644 = g.inp('Z0_o_inputData_bakedGI', True)
    v645 = g.inp('Z0_o_inputData_fogCoord', False)
    v646 = g.inp('Z0_o_inputData_normalWS', True)
    v647 = g.inp('Z0_o_inputData_normalizedScreenSpaceUV', True)
    v648 = g.inp('Z0_o_inputData_positionCS', True)
    v649 = g.inp('Z0_o_inputData_positionCS_w', False)
    v650 = g.inp('Z0_o_inputData_positionWS', True)
    v651 = g.inp('Z0_o_inputData_shadowCoord', True)
    v652 = g.inp('Z0_o_inputData_shadowCoord_w', False)
    v653 = g.inp('Z0_o_inputData_shadowMask', True)
    v654 = g.inp('Z0_o_inputData_shadowMask_w', False)
    v655 = g.inp('Z0_o_inputData_vertexLighting', True)
    v656 = g.inp('Z0_o_inputData_viewDirectionWS', True)
    v657 = g.inp('Z0_o_lightIndex', False)
    v658 = g.inp('Z0_o_roughness', False)
    v659 = g.inp('Z0_o_sssAmount', False)
    v660 = g.vmath('ADD', v641, v491)
    v661 = g.math('COMPARE', v60, 0, 1e-05)
    v662 = g.math('SUBTRACT', 1.0, v661)
    v663 = g.vmath('SUBTRACT', v20, v26)
    v664 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v665 = g.bc(v664)
    v666 = g.vmath('DIVIDE', v663, v665)
    v667 = g.vmath('ADD', v483, v660)
    v668 = g.vmath('LENGTH', v666)
    v669 = g.sep(v666)
    v670 = g.comb(v669[0], v669[2], 0.0)
    v671 = g.vmath('LENGTH', v670)
    v672 = g.math('ABSOLUTE', v669[1], 0.0)
    v673 = g.math('MAXIMUM', v671, v672)
    v674 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v675 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v676 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v677 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v678 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v679 = g.inp('_RuriFogColor_w', False, 0.0)
    v680 = g.group_named('RCE_RuriApplyFog', [('inColor', v667), ('inColor_w', 1), ('sphericalVertexDistance', v668), ('cylindricalVertexDistance', v673), ('environmentalStart', v674), ('environmentalEnd', v675), ('renderDistanceStart', v676), ('renderDistanceEnd', v677), ('fogColor', v678), ('fogColor_w', v679)])
    v681 = g.mixv(v662, v660, v680[0])
    g.out_('ret_gBuffer0', v681, True)
    g.out_('ret_gBuffer0_w', v482, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v660, True)
    g.out_('ret_color_w', v482, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


def build_Ruri_Endfield_Scene_Grass():
    t = _tree('Ruri Endfield Scene Grass')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.vmath('NORMALIZE', v21)
    v133 = g.vmath('SUBTRACT', v14, v0)
    v134 = g.comb(v77, v77, 0.0)
    v135 = g.vmath('MULTIPLY', v134, v133)
    v136 = g.vmath('ADD', v135, v0)
    v137 = g.comb(v83[0], v83[1], 0.0)
    v138 = g.comb(v83[2], v82, 0.0)
    v139 = g.vmath('MULTIPLY', v136, v137)
    v140 = g.vmath('ADD', v139, v138)
    v141 = g.comb(v88, v88, 0.0)
    v142 = g.vmath('MULTIPLY', v141, v133)
    v143 = g.vmath('ADD', v142, v0)
    v144 = g.comb(v94[0], v94[1], 0.0)
    v145 = g.comb(v94[2], v93, 0.0)
    v146 = g.vmath('MULTIPLY', v143, v144)
    v147 = g.vmath('ADD', v146, v145)
    g.out_('F4_BaseColorMap_uv', v140, True)
    v148 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v149 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    g.out_('F5_NormalMap_uv', v147, True)
    v150 = g.inp('F5_NormalMap', True, (1.0, 1.0, 1.0))
    v151 = g.inp('F5_NormalMap_alpha', False, 1.0)
    v152 = g.math('MULTIPLY', v58, v149)
    v153 = g.sep(v11)
    v154 = g.clampn(v153[0], 0, 1)
    v155 = g.vmath('MULTIPLY', v148, v37)
    v156 = g.inp('_BaseColorBrighterScale', False, 1.0)
    v157 = g.bc(v156)
    v158 = g.vmath('MULTIPLY', v155, v157)
    v159 = g.vmath('MAXIMUM', v158, (0, 0, 0))
    v160 = g.vmath('MINIMUM', v159, (1, 1, 1))
    v161 = g.inp('_BaseColorTintCover', False, 0.0)
    v162 = g.mixv(v161, v160, v37)
    v163 = g.inp('_EnableNormalMap', False, 0.0)
    v164 = g.math('GREATER_THAN', v163, 0.5)
    v165 = g.math('MAXIMUM', 0, v164)
    v166 = g.sep(v150)
    v167 = g.math('MULTIPLY_ADD', v166[0], 2, -1)
    v168 = g.math('MULTIPLY_ADD', v166[1], 2, -1)
    v169 = g.mixf(v165, 0.5, v166[2])
    v170 = g.mixf(v165, 0, v167)
    v171 = g.mixf(v165, 0, v168)
    v172 = g.mixf(v165, 1, v151)
    v173 = g.inp('_NormalScale', False, 0.0)
    v174 = g.math('MULTIPLY', v170, v173)
    v175 = g.math('MULTIPLY', v171, v173)
    v176 = g.vmath('DOT_PRODUCT', v36, v21)
    v177 = g.math('LESS_THAN', v176, 0)
    v178 = g.mixf(v177, 1, -1)
    v179 = g.comb(v170, v171, 0.0)
    v180 = g.comb(v170, v171, 0.0)
    v181 = g.vmath('DOT_PRODUCT', v179, v180)
    v182 = g.math('MINIMUM', v181, 1)
    v183 = g.math('SUBTRACT', 1, v182)
    v184 = g.math('SQRT', v183, 0.0)
    v185 = g.math('MAXIMUM', v184, 1.0000000168623835E-16)
    v186 = g.math('MULTIPLY', v185, v178)
    v187 = g.math('GREATER_THAN', v5, 0)
    v188 = g.mixf(v187, -1, 1)
    v189 = g.vmath('CROSS_PRODUCT', v21, v22)
    v190 = g.bc(v188)
    v191 = g.vmath('MULTIPLY', v190, v189)
    v192 = g.bc(v186)
    v193 = g.vmath('MULTIPLY', v21, v192)
    v194 = g.bc(v174)
    v195 = g.vmath('MULTIPLY', v22, v194)
    v196 = g.vmath('ADD', v193, v195)
    v197 = g.bc(v175)
    v198 = g.vmath('MULTIPLY', v191, v197)
    v199 = g.vmath('ADD', v196, v198)
    v200 = g.vmath('NORMALIZE', v199)
    v201 = g.inp('_BendNormalUpward', False, 0.0)
    v202 = g.math('GREATER_THAN', v201, 0)
    v203 = g.math('MULTIPLY', 1, v202)
    v204 = g.mixv(v201, v200, (0, 1, 0))
    v205 = g.vmath('NORMALIZE', v204)
    v206 = g.mixv(v203, v200, v205)
    v207 = g.inp('_RoughnessMin', False, 0.0)
    v208 = g.inp('_RoughnessMax', False, 1.0)
    v209 = g.mixf(v169, v207, v208)
    v210 = g.clampn(v209, 0, 1)
    v211 = g.inp('_Metallic', False, 0.0)
    v212 = g.inp('_BaseTextureMapCount', False, 0.0)
    v213 = g.math('SUBTRACT', v212, 1)
    v214 = g.clampn(v213, 0, 1)
    v215 = g.mixf(v214, 0, v211)
    v216 = g.mixf(0, 0, v215)
    v217 = g.inp('_PorosityFactorX', False, 0.2)
    v218 = g.inp('_PorosityFactorZ', False, 0.0)
    v219 = g.inp('_PorosityFactorY', False, 0.4)
    v220 = g.math('MULTIPLY_ADD', v218, v216, v219)
    v221 = g.math('MULTIPLY_ADD', v217, v210, v220)
    v222 = g.clampn(v221, 0, 1)
    v223 = g.math('MULTIPLY', v222, 0.95)
    v224 = g.math('ADD', v223, 0.05)
    v225 = g.inp('_OcclusionStrength', False, 1.0)
    v226 = g.mixf(v225, 1, v154)
    v227 = g.clampn(v226, 0, 1)
    v228 = g.mixf(v225, 1, 1)
    v229 = g.inp('_TrunkVertexAoStrength', False, 1.0)
    v230 = g.mixf(v229, 1, v154)
    v231 = g.math('MULTIPLY', v228, v230)
    v232 = g.mixf(0, v227, v231)
    v233 = g.inp('_EnableVerticalNormalBoostAO', False, 0.0)
    v234 = g.math('GREATER_THAN', v233, 0.5)
    v235 = g.sep(v21)
    v236 = g.inp('_VerticalNormalThreshold', False, 0.0)
    v237 = g.math('SUBTRACT', v235[1], v236)
    v238 = g.math('SUBTRACT', 1, v236)
    v239 = g.math('MAXIMUM', v238, 0.0001)
    v240 = g.math('DIVIDE', v237, v239)
    v241 = g.clampn(v240, 0, 1)
    v242 = g.inp('_VerticalNormalBoostAO', False, 0.0)
    v243 = g.math('MULTIPLY', v241, v242)
    v244 = g.math('SUBTRACT', 1, v243)
    v245 = g.math('MULTIPLY', v232, v244)
    v246 = g.mixf(v234, v232, v245)
    v247 = g.sep(v0)
    v248 = g.inp('_AoPosition', False, 0.0)
    v249 = g.inp('_AoRadius', False, 0.1)
    v250 = g.inp('_AoContrast', False, 0.2)
    v251 = g.inp('_AoIntensity', False, 0.2)
    v252 = g.group_named('RCE_FoliageGradientBand', [('coord', v247[1]), ('position', v248), ('radius', v249), ('contrast', v250), ('intensity', v251)])
    v253 = g.math('MULTIPLY', v246, v252[0])
    v254 = g.clampn(v253, 0, 1)
    v255 = g.inp('_TransmissionDistanceFade', False, 0.0)
    v256 = g.math('GREATER_THAN', v255, 0.5)
    v257 = g.vmath('DISTANCE', v20, v26)
    v258 = g.math('SUBTRACT', 60, v257)
    v259 = g.math('DIVIDE', v258, 10)
    v260 = g.clampn(v259, 0, 1)
    v261 = g.mixf(v256, 1, v260)
    v262 = g.inp('_Transmission', False, 0.2)
    v263 = g.math('MULTIPLY', v262, v261)
    v264 = g.math('MULTIPLY', v263, v172)
    v265 = g.inp('_AoAffectTransmissionStart', False, 0.0)
    v266 = g.inp('_AoAffectTransmissionRange', False, 0.01)
    v267 = g.math('SUBTRACT', v253, v265)
    v268 = g.math('MAXIMUM', v266, 0.0001)
    v269 = g.math('DIVIDE', v267, v268)
    v270 = g.clampn(v269, 0, 1)
    v271 = g.math('MULTIPLY', v264, v270)
    v272 = g.inp('_SubsurfaceIntensity', False, 0.0)
    v273 = g.math('MULTIPLY', v272, v172)
    v274 = g.inp('_AoAffectSubsurfaceStart', False, 0.0)
    v275 = g.inp('_AoAffectSubsurfaceRange', False, 0.01)
    v276 = g.math('SUBTRACT', v253, v274)
    v277 = g.math('MAXIMUM', v275, 0.0001)
    v278 = g.math('DIVIDE', v276, v277)
    v279 = g.clampn(v278, 0, 1)
    v280 = g.math('MULTIPLY', v273, v279)
    v281 = g.inp('_DirPosition', False, 0.0)
    v282 = g.inp('_DirRadius', False, 0.1)
    v283 = g.inp('_DirContrast', False, 0.2)
    v284 = g.inp('_MaskOnTransmission', False, 1.0)
    v285 = g.math('SUBTRACT', 1, v284)
    v286 = g.group_named('RCE_FoliageGradientBand', [('coord', v247[1]), ('position', v281), ('radius', v282), ('contrast', v283), ('intensity', v285)])
    v287 = g.math('MULTIPLY', v271, v286[0])
    v288 = g.math('MAXIMUM', v287, v280)
    v289 = g.clampn(v288, 0, 1)
    v290 = g.inp('_FakeDirectionalShadowStrength', False, 0.0)
    v291 = g.math('GREATER_THAN', v290, 0)
    v292 = g.vmath('NORMALIZE', v21)
    v293 = g.inp('_DiffuseUseVertexNormal', False, 1.0)
    v294 = g.mixv(v293, v206, v292)
    v295 = g.inp('_MainLightPosition', True, (0.0, 0.0, 0.0))
    v296 = g.inp('_MainLightPosition_w', False, 0.0)
    v297 = g.vmath('SCALE', v295, s=-1.0)
    v298 = g.vmath('DOT_PRODUCT', v294, v297)
    v299 = g.math('MULTIPLY_ADD', v298, 0.5, 0.5)
    v300 = g.inp('_FakeDirectionalShadowPow', False, 1.0)
    v301 = g.math('POWER', v299, v300)
    v302 = g.math('MULTIPLY', v301, v290)
    v303 = g.math('SUBTRACT', 1, v302)
    v304 = g.clampn(v303, 0, 1)
    v305 = g.inp('_OcclusionShadow', False, 0.0)
    v306 = g.mixf(v305, 1, v253)
    v307 = g.mixf(v306, 1, v304)
    v308 = g.bc(v307)
    v309 = g.vmath('MULTIPLY', v162, v308)
    v310 = g.mixv(v291, v162, v309)
    v311 = g.math('SUBTRACT', 1.0, 0)
    v312 = g.inp('_EnableCanopyColorRamp', False, 0.0)
    v313 = g.math('GREATER_THAN', v312, 0.5)
    v314 = g.math('MULTIPLY', v311, v313)
    v315 = g.sep(v2)
    v316 = g.clampn(v315[1], 0, 1)
    v317 = g.inp('_CanopyRampStartAtTop', False, 0.0)
    v318 = g.math('GREATER_THAN', v317, 0.5)
    v319 = g.math('SUBTRACT', 1, v316)
    v320 = g.mixf(v318, v316, v319)
    v321 = g.inp('_CanopyRampRange', False, 0.0)
    v322 = g.math('SUBTRACT', v320, v321)
    v323 = g.inp('_CanopyRampTransitionRange', False, 0.01)
    v324 = g.math('MAXIMUM', v323, 0.0001)
    v325 = g.math('DIVIDE', v322, v324)
    v326 = g.clampn(v325, 0, 1)
    v327 = g.inp('_CanopyRampIntensity', False, 1.0)
    v328 = g.math('MULTIPLY', v326, v327)
    v329 = g.inp('_CanopyRampColor', True, (1.0, 1.0, 1.0))
    v330 = g.inp('_CanopyRampColor_w', False, 1.0)
    v331 = g.inp('_CanopyRampColorBrighterScale', False, 1.0)
    v332 = g.bc(v331)
    v333 = g.vmath('MULTIPLY', v329, v332)
    v334 = g.vmath('MAXIMUM', v333, (0, 0, 0))
    v335 = g.vmath('MINIMUM', v334, (1, 1, 1))
    v336 = g.vmath('MULTIPLY', v310, v335)
    v337 = g.inp('_CanopyRampColorCover', False, 0.0)
    v338 = g.mixv(v337, v336, v335)
    v339 = g.mixv(v328, v310, v338)
    v340 = g.mixv(v314, v310, v339)
    v341 = g.math('SUBTRACT', 1.0, 0)
    v342 = g.inp('_EnableAoTuneColor', False, 0.0)
    v343 = g.math('GREATER_THAN', v342, 0.5)
    v344 = g.math('MULTIPLY', v341, v343)
    v345 = g.inp('_FlipAoMask', False, 0.0)
    v346 = g.math('GREATER_THAN', v345, 0.5)
    v347 = g.math('SUBTRACT', 1, v154)
    v348 = g.mixf(v346, v154, v347)
    v349 = g.inp('_AoMaskTuneColorRampStart', False, 0.0)
    v350 = g.math('SUBTRACT', v348, v349)
    v351 = g.inp('_AoMaskTuneColorRampRange', False, 0.2)
    v352 = g.math('MAXIMUM', v351, 0.0001)
    v353 = g.math('DIVIDE', v350, v352)
    v354 = g.clampn(v353, 0, 1)
    v355 = g.inp('_AoMaskTuneColorIntensity', False, 1.0)
    v356 = g.math('MULTIPLY', v354, v355)
    v357 = g.inp('_AoMaskTuneColor', True, (1.0, 1.0, 1.0))
    v358 = g.inp('_AoMaskTuneColor_w', False, 1.0)
    v359 = g.inp('_AoMaskTuneColorBrighterScale', False, 1.0)
    v360 = g.bc(v359)
    v361 = g.vmath('MULTIPLY', v357, v360)
    v362 = g.vmath('MAXIMUM', v361, (0, 0, 0))
    v363 = g.vmath('MINIMUM', v362, (1, 1, 1))
    v364 = g.vmath('MULTIPLY', v340, v363)
    v365 = g.inp('_AoMaskTuneColorCover', False, 0.0)
    v366 = g.mixv(v365, v364, v363)
    v367 = g.mixv(v356, v340, v366)
    v368 = g.mixv(v344, v340, v367)
    v369 = g.mixf(v344, v328, v356)
    v370 = g.math('SUBTRACT', 1.0, 0)
    v371 = g.inp('_EnableBlendColor', False, 0.0)
    v372 = g.math('GREATER_THAN', v371, 0.5)
    v373 = g.math('MULTIPLY', v370, v372)
    v374 = g.inp('_BlendWithVertexNormal', False, 0.0)
    v375 = g.math('GREATER_THAN', v374, 0.5)
    v376 = g.vmath('NORMALIZE', v21)
    v377 = g.mixv(v375, v206, v376)
    v378 = g.sep(v377)
    v379 = g.math('MULTIPLY_ADD', v378[1], 0.5, 0.5)
    v380 = g.inp('_BlendNormalAdd', False, 0.0)
    v381 = g.math('ADD', v379, v380)
    v382 = g.clampn(v381, 0, 1)
    v383 = g.inp('_BlendColor', True, (1.0, 1.0, 1.0))
    v384 = g.inp('_BlendColor_w', False, 1.0)
    v385 = g.vmath('MULTIPLY', v368, v383)
    v386 = g.inp('_BlendNormalPower', False, 1.0)
    v387 = g.math('MAXIMUM', v386, 0.001)
    v388 = g.math('POWER', v382, v387)
    v389 = g.mixv(v388, v368, v385)
    v390 = g.mixv(v373, v368, v389)
    v391 = g.mixf(v373, v369, v382)
    v392 = g.inp('_EnableTrunkRamp', False, 0.0)
    v393 = g.math('GREATER_THAN', v392, 0.5)
    v394 = g.math('MULTIPLY', 0, v393)
    v395 = g.clampn(v315[1], 0, 1)
    v396 = g.inp('_TrunkRampRange', False, 0.0)
    v397 = g.math('SUBTRACT', v396, v395)
    v398 = g.inp('_TrunkRampTransitionRange', False, 0.01)
    v399 = g.math('MAXIMUM', v398, 0.0001)
    v400 = g.math('DIVIDE', v397, v399)
    v401 = g.clampn(v400, 0, 1)
    v402 = g.inp('_TrunkRampIntensity', False, 1.0)
    v403 = g.math('MULTIPLY', v401, v402)
    v404 = g.inp('_TrunkRampColor', True, (1.0, 1.0, 1.0))
    v405 = g.inp('_TrunkRampColor_w', False, 1.0)
    v406 = g.vmath('MULTIPLY', v390, v404)
    v407 = g.mixv(v403, v390, v406)
    v408 = g.mixv(v394, v390, v407)
    v409 = g.mixf(v394, v320, v395)
    v410 = g.mixf(v394, v391, v403)
    v411 = g.inp('_DirIntensity', False, 0.2)
    v412 = g.inp('_MaskOnDiffuse', False, 1.0)
    v413 = g.math('MULTIPLY', v411, v412)
    v414 = g.group_named('RCE_FoliageGradientBand', [('coord', v247[1]), ('position', v281), ('radius', v282), ('contrast', v283), ('intensity', v413)])
    v415 = g.bc(v414[0])
    v416 = g.vmath('MULTIPLY', v408, v415)
    v417 = g.inp('_EnableEmissiveMap', False, 0.0)
    v418 = g.math('GREATER_THAN', v417, 0.5)
    v419 = g.inp('_EmissiveUVSet', False, 0.0)
    v420 = g.math('GREATER_THAN', v419, 0.5)
    v421 = g.mixv(v420, v0, v14)
    v422 = g.inp('_EmissiveMap_ST', True, (1.0, 1.0, 0.0))
    v423 = g.inp('_EmissiveMap_ST_w', False, 0.0)
    v424 = g.sep(v422)
    v425 = g.comb(v424[0], v424[1], 0.0)
    v426 = g.comb(v424[2], v423, 0.0)
    v427 = g.vmath('MULTIPLY', v421, v425)
    v428 = g.vmath('ADD', v427, v426)
    g.out_('F6_EmissiveMap_uv', v428, True)
    v429 = g.inp('F6_EmissiveMap', True, (1.0, 1.0, 1.0))
    v430 = g.inp('F6_EmissiveMap_alpha', False, 1.0)
    v431 = g.inp('_EmissiveMaskChannel', False, 0.0)
    v432 = g.math('LESS_THAN', v431, 0.5)
    v433 = g.sep(v429)
    v434 = g.inp('_EmissiveColorR', True, (0.0, 0.0, 0.0))
    v435 = g.inp('_EmissiveColorR_w', False, 1.0)
    v436 = g.bc(v433[0])
    v437 = g.vmath('MULTIPLY', v436, v434)
    v438 = g.inp('_EmissiveColorG', True, (0.0, 0.0, 0.0))
    v439 = g.inp('_EmissiveColorG_w', False, 0.0)
    v440 = g.bc(v433[1])
    v441 = g.vmath('MULTIPLY', v440, v438)
    v442 = g.vmath('ADD', v437, v441)
    v443 = g.inp('_EmissiveColorB', True, (0.0, 0.0, 0.0))
    v444 = g.inp('_EmissiveColorB_w', False, 0.0)
    v445 = g.bc(v433[2])
    v446 = g.vmath('MULTIPLY', v445, v443)
    v447 = g.vmath('ADD', v442, v446)
    v448 = g.inp('_EmissiveColorA', True, (0.0, 0.0, 0.0))
    v449 = g.inp('_EmissiveColorA_w', False, 0.0)
    v450 = g.bc(v430)
    v451 = g.vmath('MULTIPLY', v450, v448)
    v452 = g.vmath('ADD', v447, v451)
    v453 = g.math('LESS_THAN', v431, 1.5)
    v454 = g.vmath('MULTIPLY', v429, v434)
    v455 = g.bc(v149)
    v456 = g.vmath('MULTIPLY', v455, v434)
    v457 = g.mixv(v453, v456, v454)
    v458 = g.mixv(v432, v457, v452)
    v459 = g.inp('_AlbedoAffectEmissive', False, 1.0)
    v460 = g.math('LESS_THAN', v459, 0.5)
    v461 = g.vmath('MULTIPLY', v458, v416)
    v462 = g.mixv(v460, v458, v461)
    v463 = g.mixv(v418, (0, 0, 0), v462)
    v464 = g.inp('_EnableVertColorEmissive', False, 0.0)
    v465 = g.math('GREATER_THAN', v464, 0.5)
    v466 = g.inp('_VertColorEmissiveChannelVector', True, (1.0, 0.0, 0.0))
    v467 = g.inp('_VertColorEmissiveChannelVector_w', False, 0.0)
    v468 = g.vmath('DOT_PRODUCT', v11, v466)
    v469 = g.inp('_VertColorEmissiveFlip', False, 0.0)
    v470 = g.math('GREATER_THAN', v469, 0.5)
    v471 = g.math('SUBTRACT', 1, v468)
    v472 = g.mixf(v470, v468, v471)
    v473 = g.inp('_VertColorEmissiveBias', False, 0.0)
    v474 = g.math('ADD', v472, v473)
    v475 = g.clampn(v474, 0, 1)
    v476 = g.inp('_VertColorEmissiveColor', True, (0.0, 0.0, 0.0))
    v477 = g.inp('_VertColorEmissiveColor_w', False, 1.0)
    v478 = g.bc(v475)
    v479 = g.vmath('MULTIPLY', v476, v478)
    v480 = g.inp('_VertColorEmissiveAlbedoAffect', False, 1.0)
    v481 = g.math('LESS_THAN', v480, 0.5)
    v482 = g.vmath('MULTIPLY', v479, v416)
    v483 = g.mixv(v481, v479, v482)
    v484 = g.vmath('ADD', v463, v483)
    v485 = g.mixv(v465, v463, v484)
    v486 = g.mixf(v465, v410, v475)
    v487 = g.inp('_CrossCardViewCulling', False, 0.0)
    v488 = g.math('GREATER_THAN', v487, 0.5)
    v489 = g.vmath('SUBTRACT', v26, v20)
    v490 = g.vmath('NORMALIZE', v489)
    v491 = g.vmath('NORMALIZE', v21)
    v492 = g.vmath('DOT_PRODUCT', v491, v490)
    v493 = g.math('ABSOLUTE', v492, 0.0)
    v494 = g.inp('_CrossCardViewCullingThreshold', False, 0.4)
    v495 = g.math('SUBTRACT', v493, v494)
    v496 = g.inp('_CrossCardViewCullingFadeValue', False, 0.5)
    v497 = g.math('MAXIMUM', v496, 0.0001)
    v498 = g.math('DIVIDE', v495, v497)
    v499 = g.clampn(v498, 0, 1)
    v500 = g.math('MULTIPLY', v152, v499)
    v501 = g.mixf(v488, v152, v500)
    v502 = g.mixf(v121, v58, v501)
    v503 = g.mixv(v121, v65, v416)
    v504 = g.mixf(v121, v114, v210)
    v505 = g.mixf(v121, v115, v216)
    v506 = g.mixf(v121, v116, v254)
    v507 = g.mixf(v121, v117, v224)
    v508 = g.mixv(v121, v36, v206)
    v509 = g.mixf(v121, 0.0, v289)
    v510 = g.mixv(v121, (0.0, 0.0, 0.0), v162)
    v511 = g.mixv(v121, (0, 0, 0), v485)
    v512 = g.mixv(v121, v132, v206)
    v513 = g.comb(v507, v507, v507)
    v514 = g.math('SUBTRACT', 1, v504)
    v515 = g.vmath('NORMALIZE', v508)
    v516 = g.vmath('DOT_PRODUCT', v515, v28)
    v517 = g.math('MAXIMUM', v516, 0)
    v518 = g.math('MULTIPLY', 0.08, v507)
    v519 = g.math('MULTIPLY', 0.08, v507)
    v520 = g.math('MULTIPLY', 0.08, v507)
    v521 = g.comb(v518, v519, v520)
    v522 = g.mixv(v505, v521, v503)
    v523 = g.math('SUBTRACT', 1, v505)
    v524 = g.bc(v523)
    v525 = g.vmath('MULTIPLY', v503, v524)
    v526 = g.inp('_UseThinFilm', False, 0.0)
    v527 = g.math('GREATER_THAN', v526, 0.5)
    v528 = g.inp('_ThinFilmIOR', False, 1.4)
    v529 = g.inp('_ThinFilmThickness', False, 0.5)
    v530 = g.math('MULTIPLY', v529, 1000)
    v531 = g.inp('M_PI', False, 0.0)
    v532 = g.group_named('RCE_RuriEvalIridescence', [('outsideIor', 1), ('eta2', v528), ('cosTheta1', v517), ('iridescenceThickness', v530), ('baseF0', v522), ('M_PI', v531)])
    v533 = g.inp('_ThinFilmWeight', False, 0.0)
    v534 = g.inp('_ThinFilmIntensity', False, 1.0)
    v535 = g.math('MULTIPLY', v533, v534)
    v536 = g.clampn(v535)
    v537 = g.mixv(v536, v522, v532[0])
    v538 = g.mixv(v527, v522, v537)
    v539 = g.inp('_SubsurfaceShadingMode', False, 0.0)
    v540 = g.math('LESS_THAN', v539, 0.5)
    v541 = g.inp('_SubsurfaceColor', True, (0.8, 0.8, 0.8))
    v542 = g.inp('_SubsurfaceColor_w', False, 1.0)
    v543 = g.vmath('MULTIPLY', v541, v503)
    v544 = g.mixv(v540, v543, v541)
    v545 = g.inp('_MaxSubsurfaceThickness', False, 1.0)
    v546 = g.inp('_UseSubsurfaceThicknessMap', False, 0.0)
    v547 = g.math('GREATER_THAN', v546, 0.5)
    v548 = g.inp('_MinSubsurfaceThickness', False, 0.0)
    g.out_('F7_SubsurfaceMap_uv', v0, True)
    v549 = g.inp('F7_SubsurfaceMap', True, (1.0, 1.0, 1.0))
    v550 = g.inp('F7_SubsurfaceMap_alpha', False, 1.0)
    v551 = g.sep(v549)
    v552 = g.mixf(v551[0], v548, v545)
    v553 = g.mixf(v547, v545, v552)
    v554 = g.group_named('RCE_HgEnvBRDF', [('roughness', v504), ('NoV', v517), ('f0', v538)])
    v555 = g.vmath('SCALE', v28, s=-1.0)
    v556 = g.vmath('DOT_PRODUCT', v515, v555)
    v557 = g.math('MULTIPLY', 2.0, v556)
    v558 = g.vmath('SCALE', v515, s=v557)
    v559 = g.vmath('SUBTRACT', v555, v558)
    v560 = g.inp('_UseCustomIBL', False, 0.0)
    v561 = g.math('GREATER_THAN', v560, 0.5)
    v562 = g.math('MULTIPLY', 0.7, v504)
    v563 = g.math('SUBTRACT', 1.7, v562)
    v564 = g.math('MULTIPLY', v504, v563)
    v565 = g.math('MULTIPLY', v564, 6)
    v566 = g.u2b(v559)
    g.out_('F8_IBL_CustomIBL_dir', v566, True)
    g.out_('F8_IBL_CustomIBL_mip', v565, False)
    v567 = g.inp('F8_IBL_CustomIBL', True, (0.2159, 0.2159, 0.2159))
    v568 = g.inp('F8_IBL_CustomIBL_alpha', False, 1.0)
    v569 = g.inp('_CustomIBLIntensity', False, 1.0)
    v570 = g.bc(v569)
    v571 = g.vmath('MULTIPLY', v567, v570)
    g.out_('C1_SpecularRadiance_direction', v559, True)
    g.out_('C1_SpecularRadiance_position', v20, True)
    g.out_('C1_SpecularRadiance_roughness', v504, False)
    v572 = g.inp('C1_SpecularRadiance', True, (0.2159, 0.2159, 0.2159))
    v573 = g.mixv(v561, v572, v571)
    v574 = g.inp('_PlanarReflection', False, 0.0)
    v575 = g.math('GREATER_THAN', v574, 0.5)
    g.out_('F9_PlanarReflectionTexture_uv', v29, True)
    v576 = g.inp('F9_PlanarReflectionTexture', True, (1.0, 1.0, 1.0))
    v577 = g.inp('F9_PlanarReflectionTexture_alpha', False, 1.0)
    v578 = g.inp('_PlanarReflectionTint', True, (1.0, 1.0, 1.0))
    v579 = g.inp('_PlanarReflectionTint_w', False, 1.0)
    v580 = g.vmath('MULTIPLY', v576, v578)
    v581 = g.mixv(v579, v573, v580)
    v582 = g.mixv(v575, v573, v581)
    v583 = g.inp('_EnableSubsurface', False, 0.0)
    v584 = g.math('GREATER_THAN', v583, 0.5)
    v585 = g.inp('_SubsurfaceIndirect', False, 1.0)
    v586 = g.comb(v585, v585, v585)
    v587 = g.vmath('MULTIPLY', v544, v586)
    v588 = g.vmath('ADD', v587, v525)
    v589 = g.mixv(v584, v525, v588)
    v590 = g.vmath('MULTIPLY', v589, v30)
    v591 = g.bc(v506)
    v592 = g.vmath('MULTIPLY', v590, v591)
    v593 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v594 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v595 = g.sep(v593)
    v596 = g.bc(v595[0])
    v597 = g.vmath('MULTIPLY', v592, v596)
    v598 = g.comb(v554[0], v554[0], v554[0])
    v599 = g.comb(v554[1], v554[1], v554[1])
    v600 = g.vmath('MULTIPLY', v538, v598)
    v601 = g.vmath('ADD', v600, v599)
    v602 = g.vmath('MULTIPLY', v601, v582)
    v603 = g.bc(v595[1])
    v604 = g.vmath('MULTIPLY', v602, v603)
    v605 = g.vmath('ADD', v597, v604)
    v606 = g.inp('C2_MainLight_direction', True, (0.0, 0.0, 0.0))
    v607 = g.inp('C2_MainLight_color', True, (0.0, 0.0, 0.0))
    v608 = g.inp('C2_MainLight_distanceAttenuation', False, 0.0)
    v609 = g.inp('C2_MainLight_shadowAttenuation', False, 0.0)
    v610 = g.inp('C2_MainLight_layerMask', False, 0.0)
    v611 = g.inp('_MainLightOcclusionProbes', True, (0.0, 0.0, 0.0))
    v612 = g.inp('_MainLightOcclusionProbes_w', False, 0.0)
    v613 = g.vmath('ADD', v606, v28)
    v614 = g.vmath('DOT_PRODUCT', v613, v613)
    v615 = g.math('MAXIMUM', v614, 1E-08)
    v616 = g.math('INVERSE_SQRT', v615, 0.0)
    v617 = g.bc(v616)
    v618 = g.vmath('MULTIPLY', v613, v617)
    v619 = g.vmath('DOT_PRODUCT', v606, v515)
    v620 = g.clampn(v619)
    v621 = g.vmath('DOT_PRODUCT', v515, v618)
    v622 = g.clampn(v621)
    v623 = g.vmath('DOT_PRODUCT', v28, v618)
    v624 = g.clampn(v623)
    v625 = g.group_named('RCE_HgDirectLightEnergy', [('roughness', v504), ('f0', v538), ('NoL', v620), ('NoH', v622), ('NoV', v517), ('VoH', v624)])
    v626 = g.math('MULTIPLY', v608, 1.0)
    v627 = g.comb(v620, v620, v620)
    v628 = g.bc(v620)
    v629 = g.vmath('MULTIPLY', v525, v628)
    v630 = g.vmath('MULTIPLY', v625[0], v627)
    v631 = g.vmath('ADD', v630, v629)
    v632 = g.math('GREATER_THAN', v583, 0.5)
    v633 = g.vmath('DOT_PRODUCT', v606, v515)
    v634 = g.vmath('DOT_PRODUCT', v28, v606)
    v635 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v636 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v637 = g.group_named('RCE_HgSssLobe', [('amount', v553), ('rawNoL', v633), ('VdotL', v634), ('selfShadowBias', v635), ('enableSelfShadowBias', v636)])
    v638 = g.bc(v637[0])
    v639 = g.vmath('MULTIPLY', v638, v544)
    v640 = g.vmath('ADD', v631, v639)
    v641 = g.mixv(v632, v631, v640)
    v642 = g.bc(v626)
    v643 = g.vmath('MULTIPLY', v607, v642)
    v644 = g.vmath('MULTIPLY', v641, v643)
    v645 = g.vmath('ADD', v605, v644)
    v646 = g.inp('C3_AdditionalLightCount', False, 0.0)
    v647 = g.math('SUBTRACT', v646, 0)
    v648 = g.math('CEIL', v647, 0.0)
    v649 = g.math('MAXIMUM', v648, 0.0)
    g.out_('Z0_it', v649, False)
    g.out_('Z0_s_H', v618, True)
    g.out_('Z0_s_L', v606, True)
    g.out_('Z0_s_LV', v613, True)
    g.out_('Z0_s_N', v515, True)
    g.out_('Z0_s_NoH', v622, False)
    g.out_('Z0_s_NoL', v620, False)
    g.out_('Z0_s_NoV', v517, False)
    g.out_('Z0_s_P', v20, True)
    g.out_('Z0_s_V', v28, True)
    g.out_('Z0_s_VoH', v624, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_color', v645, True)
    g.out_('Z0_s_energy', v625[0], True)
    g.out_('Z0_s_f0', v538, True)
    g.out_('Z0_s_inputData_bakedGI', v30, True)
    g.out_('Z0_s_inputData_fogCoord', 0, False)
    g.out_('Z0_s_inputData_normalWS', v508, True)
    g.out_('Z0_s_inputData_normalizedScreenSpaceUV', v29, True)
    g.out_('Z0_s_inputData_positionCS', v17, True)
    g.out_('Z0_s_inputData_positionCS_w', v18, False)
    g.out_('Z0_s_inputData_positionWS', v20, True)
    g.out_('Z0_s_inputData_shadowCoord', (0.0, 0.0, 0.0), True)
    g.out_('Z0_s_inputData_shadowCoord_w', 0.0, False)
    g.out_('Z0_s_inputData_shadowMask', (1, 1, 1), True)
    g.out_('Z0_s_inputData_shadowMask_w', 1, False)
    g.out_('Z0_s_inputData_vertexLighting', (0, 0, 0), True)
    g.out_('Z0_s_inputData_viewDirectionWS', v28, True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_roughness', v504, False)
    g.out_('Z0_s_sssAmount', v553, False)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_diffuse', v525, True)
    g.out_('Z0_r_sssTint', v544, True)
    g.out_('Z0_r_pixelLightCount', v646, False)
    v650 = g.inp('Z0_o_H', True)
    v651 = g.inp('Z0_o_L', True)
    v652 = g.inp('Z0_o_LV', True)
    v653 = g.inp('Z0_o_N', True)
    v654 = g.inp('Z0_o_NoH', False)
    v655 = g.inp('Z0_o_NoL', False)
    v656 = g.inp('Z0_o_NoV', False)
    v657 = g.inp('Z0_o_P', True)
    v658 = g.inp('Z0_o_V', True)
    v659 = g.inp('Z0_o_VoH', False)
    v660 = g.inp('Z0_o_Lloop0', False)
    v661 = g.inp('Z0_o_color', True)
    v662 = g.inp('Z0_o_energy', True)
    v663 = g.inp('Z0_o_f0', True)
    v664 = g.inp('Z0_o_inputData_bakedGI', True)
    v665 = g.inp('Z0_o_inputData_fogCoord', False)
    v666 = g.inp('Z0_o_inputData_normalWS', True)
    v667 = g.inp('Z0_o_inputData_normalizedScreenSpaceUV', True)
    v668 = g.inp('Z0_o_inputData_positionCS', True)
    v669 = g.inp('Z0_o_inputData_positionCS_w', False)
    v670 = g.inp('Z0_o_inputData_positionWS', True)
    v671 = g.inp('Z0_o_inputData_shadowCoord', True)
    v672 = g.inp('Z0_o_inputData_shadowCoord_w', False)
    v673 = g.inp('Z0_o_inputData_shadowMask', True)
    v674 = g.inp('Z0_o_inputData_shadowMask_w', False)
    v675 = g.inp('Z0_o_inputData_vertexLighting', True)
    v676 = g.inp('Z0_o_inputData_viewDirectionWS', True)
    v677 = g.inp('Z0_o_lightIndex', False)
    v678 = g.inp('Z0_o_roughness', False)
    v679 = g.inp('Z0_o_sssAmount', False)
    v680 = g.vmath('ADD', v661, v511)
    v681 = g.math('COMPARE', v60, 0, 1e-05)
    v682 = g.math('SUBTRACT', 1.0, v681)
    v683 = g.vmath('SUBTRACT', v20, v26)
    v684 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v685 = g.bc(v684)
    v686 = g.vmath('DIVIDE', v683, v685)
    v687 = g.vmath('ADD', v503, v680)
    v688 = g.vmath('LENGTH', v686)
    v689 = g.sep(v686)
    v690 = g.comb(v689[0], v689[2], 0.0)
    v691 = g.vmath('LENGTH', v690)
    v692 = g.math('ABSOLUTE', v689[1], 0.0)
    v693 = g.math('MAXIMUM', v691, v692)
    v694 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v695 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v696 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v697 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v698 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v699 = g.inp('_RuriFogColor_w', False, 0.0)
    v700 = g.group_named('RCE_RuriApplyFog', [('inColor', v687), ('inColor_w', 1), ('sphericalVertexDistance', v688), ('cylindricalVertexDistance', v693), ('environmentalStart', v694), ('environmentalEnd', v695), ('renderDistanceStart', v696), ('renderDistanceEnd', v697), ('fogColor', v698), ('fogColor_w', v699)])
    v701 = g.mixv(v682, v680, v700[0])
    g.out_('ret_gBuffer0', v701, True)
    g.out_('ret_gBuffer0_w', v502, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v680, True)
    g.out_('ret_color_w', v502, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


def build_Ruri_Endfield_Scene_Trunk():
    t = _tree('Ruri Endfield Scene Trunk')
    g = G(t)
    v0 = g.inp('input_uv', True)
    v1 = g.inp('input_positionWS', True)
    v2 = g.inp('input_positionOS', True)
    v3 = g.inp('input_normalWS', True)
    v4 = g.inp('input_tangentWS', True)
    v5 = g.inp('input_tangentWS_w', False)
    v6 = g.inp('input_voxelUV', True)
    v7 = g.inp('input_voxelLitColor', True)
    v8 = g.inp('input_staticLightmapUV', True)
    v9 = g.inp('input_positionNDC', True)
    v10 = g.inp('input_positionNDC_w', False)
    v11 = g.inp('input_color', True)
    v12 = g.inp('input_color_w', False)
    v13 = g.inp('input_voxelSliceMaterial', True)
    v14 = g.inp('input_uv1', True)
    v15 = g.inp('input_uv2', True)
    v16 = g.inp('input_voxelBlockLight', True)
    v17 = g.inp('input_positionCS', True)
    v18 = g.inp('input_positionCS_w', False)
    v19 = g.inp('facing', False)
    v20 = g.b2u(v1, point=True)
    v21 = g.b2u(v3, point=False)
    v22 = g.b2u(v4, point=False)
    g.out_('F0_BaseMap_uv', v0, True)
    v23 = g.inp('F0_BaseMap', True, (1.0, 1.0, 1.0))
    v24 = g.inp('F0_BaseMap_alpha', False, 1.0)
    v25 = g.vmath('NORMALIZE', v21)
    v26 = g.b2u(g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT'), point=True)
    v27 = g.vmath('SUBTRACT', v26, v20)
    v28 = g.vmath('NORMALIZE', v27)
    v29 = g.texco().outputs['Window']
    g.out_('C0_AmbientIrradiance_normal', v25, True)
    v30 = g.inp('C0_AmbientIrradiance', True, (0.0, 0.0, 0.0))
    v31 = g.inp('_TwoSidedNormal', False, 1.0)
    v32 = g.math('GREATER_THAN', v31, 0.5)
    v33 = g.math('LESS_THAN', v19, 0)
    v34 = g.math('MULTIPLY', v32, v33)
    v35 = g.vmath('SCALE', v25, s=-1.0)
    v36 = g.mixv(v34, v25, v35)
    v37 = g.inp('_BaseColor', True, (1.0, 1.0, 1.0))
    v38 = g.inp('_BaseColor_w', False, 1.0)
    v39 = g.vmath('MULTIPLY', v23, v37)
    v40 = g.math('MULTIPLY', v24, v38)
    v41 = g.sep(v13)
    v42 = g.math('ROUND', v41[0], 0.0)
    v43 = g.math('TRUNC', v42, 0.0)
    v44 = g.math('COMPARE', v43, 65535, 1e-05)
    v45 = g.inp('_UseVoxelAtlas', False, 0.0)
    g.out_('F1_VoxelAtlas_uv', v6, True)
    v46 = g.inp('F1_VoxelAtlas', True, (1.0, 1.0, 1.0))
    v47 = g.inp('F1_VoxelAtlas_alpha', False, 1.0)
    v48 = g.vmath('MULTIPLY', v46, v11)
    v49 = g.mixv(v44, v48, v11)
    v50 = g.vmath('MULTIPLY', v39, v49)
    v51 = g.inp('_UseCutoff', False, 0.0)
    v52 = g.mixf(v44, v47, 1)
    v53 = g.math('MULTIPLY', v40, v52)
    v54 = g.mixf(v51, v40, v53)
    v55 = g.inp('_UseVertexColor', False, 0.0)
    v56 = g.vmath('MULTIPLY', v39, v11)
    v57 = g.mixv(v55, v39, v56)
    v58 = g.mixf(v45, v40, v54)
    v59 = g.mixv(v45, v57, v50)
    v60 = g.inp('_RuriVoxelLightVolumeOn', False, 0.0)
    v61 = g.math('COMPARE', v60, 0, 1e-05)
    v62 = g.math('SUBTRACT', 1.0, v61)
    v63 = g.vmath('MULTIPLY', v59, v7)
    v64 = g.bc(v63)
    v65 = g.mixv(v62, v59, v64)
    v66 = g.mixv(v62, (0, 0, 0), v59)
    v67 = g.inp('_UseDitherClip', False, 0.0)
    v68 = g.inp('_Cutoff', False, 0.5)
    v69 = g.math('SUBTRACT', v58, v68)
    v70 = g.math('LESS_THAN', v69, 0.0)
    v71 = g.math('SUBTRACT', 1.0, v70)
    v72 = g.math('MULTIPLY', 1.0, v71)
    v73 = g.mixf(v51, 1.0, v72)
    v74 = g.inp('_EnableAlphaTest', False, 0.0)
    v75 = g.math('GREATER_THAN', v74, 0.5)
    v76 = g.vmath('SUBTRACT', v14, v0)
    v77 = g.inp('_BaseUVSet', False, 0.0)
    v78 = g.comb(v77, v77, 0.0)
    v79 = g.vmath('MULTIPLY', v78, v76)
    v80 = g.vmath('ADD', v79, v0)
    v81 = g.inp('_BaseColorMap_ST', True, (1.0, 1.0, 0.0))
    v82 = g.inp('_BaseColorMap_ST_w', False, 0.0)
    v83 = g.sep(v81)
    v84 = g.comb(v83[0], v83[1], 0.0)
    v85 = g.comb(v83[2], v82, 0.0)
    v86 = g.vmath('MULTIPLY', v80, v84)
    v87 = g.vmath('ADD', v86, v85)
    v88 = g.inp('_BasePbrMapUVSet', False, 0.0)
    v89 = g.comb(v88, v88, 0.0)
    v90 = g.vmath('MULTIPLY', v89, v76)
    v91 = g.vmath('ADD', v90, v0)
    v92 = g.inp('_NormalMap_ST', True, (1.0, 1.0, 0.0))
    v93 = g.inp('_NormalMap_ST_w', False, 0.0)
    v94 = g.sep(v92)
    v95 = g.comb(v94[0], v94[1], 0.0)
    v96 = g.comb(v94[2], v93, 0.0)
    v97 = g.vmath('MULTIPLY', v91, v95)
    v98 = g.vmath('ADD', v97, v96)
    g.out_('F2_BaseColorMap_uv', v87, True)
    v99 = g.inp('F2_BaseColorMap', True, (1.0, 1.0, 1.0))
    v100 = g.inp('F2_BaseColorMap_alpha', False, 1.0)
    g.out_('F3_NormalMap_uv', v98, True)
    v101 = g.inp('F3_NormalMap', True, (1.0, 1.0, 1.0))
    v102 = g.inp('F3_NormalMap_alpha', False, 1.0)
    v103 = g.inp('_AlphaMaskChannel', False, 0.0)
    v104 = g.math('MULTIPLY', v100, -1.0)
    v105 = g.math('ADD', v104, v102)
    v106 = g.math('MULTIPLY_ADD', v103, v105, v100)
    v107 = g.math('MULTIPLY', v106, v38)
    v108 = g.inp('_AlphaClipThreshold', False, 0.5)
    v109 = g.math('SUBTRACT', v107, v108)
    v110 = g.math('LESS_THAN', v109, 0.0)
    v111 = g.math('SUBTRACT', 1.0, v110)
    v112 = g.math('MULTIPLY', v73, v111)
    v113 = g.mixf(v75, v73, v112)
    v114 = g.inp('_RoughnessIntensity', False, 0.5)
    v115 = g.inp('_MetallicIntensity', False, 0.0)
    v116 = g.inp('_OcclusionIntensity', False, 1.0)
    v117 = g.inp('_SpecularIntensity', False, 1.0)
    v118 = g.math('COMPARE', v60, 0, 1e-05)
    v119 = g.math('SUBTRACT', 1.0, v118)
    v120 = g.math('MAXIMUM', v45, v55)
    v121 = g.math('SUBTRACT', 1.0, v120)
    v122 = g.inp('_RuriRadianceMode', False, 0.0)
    v123 = g.math('COMPARE', v122, 0, 1e-05)
    v124 = g.math('MULTIPLY', v55, v123)
    v125 = g.inp('_VoxelEmissionScale', False, 4.0)
    v126 = g.math('MULTIPLY', v12, v125)
    v127 = g.mixf(v124, 0.0, v126)
    v128 = g.mixf(v124, 0.0, 1.0)
    v129 = g.math('SUBTRACT', 1.0, v128)
    v130 = g.mixf(v129, v127, 0)
    v131 = g.mixf(v129, v128, 1.0)
    v132 = g.vmath('NORMALIZE', v21)
    v133 = g.vmath('SUBTRACT', v14, v0)
    v134 = g.comb(v77, v77, 0.0)
    v135 = g.vmath('MULTIPLY', v134, v133)
    v136 = g.vmath('ADD', v135, v0)
    v137 = g.comb(v83[0], v83[1], 0.0)
    v138 = g.comb(v83[2], v82, 0.0)
    v139 = g.vmath('MULTIPLY', v136, v137)
    v140 = g.vmath('ADD', v139, v138)
    v141 = g.comb(v88, v88, 0.0)
    v142 = g.vmath('MULTIPLY', v141, v133)
    v143 = g.vmath('ADD', v142, v0)
    v144 = g.comb(v94[0], v94[1], 0.0)
    v145 = g.comb(v94[2], v93, 0.0)
    v146 = g.vmath('MULTIPLY', v143, v144)
    v147 = g.vmath('ADD', v146, v145)
    g.out_('F4_BaseColorMap_uv', v140, True)
    v148 = g.inp('F4_BaseColorMap', True, (1.0, 1.0, 1.0))
    v149 = g.inp('F4_BaseColorMap_alpha', False, 1.0)
    g.out_('F5_NormalMap_uv', v147, True)
    v150 = g.inp('F5_NormalMap', True, (1.0, 1.0, 1.0))
    v151 = g.inp('F5_NormalMap_alpha', False, 1.0)
    v152 = g.math('MULTIPLY', v58, v149)
    v153 = g.sep(v11)
    v154 = g.clampn(v153[0], 0, 1)
    v155 = g.vmath('MULTIPLY', v148, v37)
    v156 = g.inp('_BaseColorBrighterScale', False, 1.0)
    v157 = g.bc(v156)
    v158 = g.vmath('MULTIPLY', v155, v157)
    v159 = g.vmath('MAXIMUM', v158, (0, 0, 0))
    v160 = g.vmath('MINIMUM', v159, (1, 1, 1))
    v161 = g.inp('_BaseColorTintCover', False, 0.0)
    v162 = g.mixv(v161, v160, v37)
    v163 = g.inp('_EnableNormalMap', False, 0.0)
    v164 = g.math('GREATER_THAN', v163, 0.5)
    v165 = g.math('MAXIMUM', 1, v164)
    g.out_('F6_MROMap_uv', v147, True)
    v166 = g.inp('F6_MROMap', True, (1.0, 1.0, 1.0))
    v167 = g.inp('F6_MROMap_alpha', False, 1.0)
    v168 = g.sep(v166)
    v169 = g.sep(v150)
    v170 = g.math('MULTIPLY', v169[0], v151)
    v171 = g.math('MULTIPLY_ADD', v170, 2, -1)
    v172 = g.math('MULTIPLY_ADD', v169[1], 2, -1)
    v173 = g.inp('_NormalScale', False, 0.0)
    v174 = g.math('MULTIPLY', v171, v173)
    v175 = g.math('MULTIPLY', v172, v173)
    v176 = g.vmath('DOT_PRODUCT', v36, v21)
    v177 = g.math('LESS_THAN', v176, 0)
    v178 = g.mixf(v177, 1, -1)
    v179 = g.comb(v171, v172, 0.0)
    v180 = g.comb(v171, v172, 0.0)
    v181 = g.vmath('DOT_PRODUCT', v179, v180)
    v182 = g.math('MINIMUM', v181, 1)
    v183 = g.math('SUBTRACT', 1, v182)
    v184 = g.math('SQRT', v183, 0.0)
    v185 = g.math('MAXIMUM', v184, 1.0000000168623835E-16)
    v186 = g.math('MULTIPLY', v185, v178)
    v187 = g.math('GREATER_THAN', v5, 0)
    v188 = g.mixf(v187, -1, 1)
    v189 = g.vmath('CROSS_PRODUCT', v21, v22)
    v190 = g.bc(v188)
    v191 = g.vmath('MULTIPLY', v190, v189)
    v192 = g.bc(v186)
    v193 = g.vmath('MULTIPLY', v21, v192)
    v194 = g.bc(v174)
    v195 = g.vmath('MULTIPLY', v22, v194)
    v196 = g.vmath('ADD', v193, v195)
    v197 = g.bc(v175)
    v198 = g.vmath('MULTIPLY', v191, v197)
    v199 = g.vmath('ADD', v196, v198)
    v200 = g.vmath('NORMALIZE', v199)
    v201 = g.inp('_BendNormalUpward', False, 0.0)
    v202 = g.math('GREATER_THAN', v201, 0)
    v203 = g.math('MULTIPLY', 0, v202)
    v204 = g.mixv(v201, v200, (0, 1, 0))
    v205 = g.vmath('NORMALIZE', v204)
    v206 = g.mixv(v203, v200, v205)
    v207 = g.inp('_RoughnessMin', False, 0.0)
    v208 = g.inp('_RoughnessMax', False, 1.0)
    v209 = g.mixf(v168[1], v207, v208)
    v210 = g.clampn(v209, 0, 1)
    v211 = g.inp('_Metallic', False, 0.0)
    v212 = g.inp('_BaseTextureMapCount', False, 0.0)
    v213 = g.math('SUBTRACT', v212, 1)
    v214 = g.clampn(v213, 0, 1)
    v215 = g.mixf(v214, v168[0], v211)
    v216 = g.mixf(1, 0, v215)
    v217 = g.inp('_PorosityFactorX', False, 0.2)
    v218 = g.inp('_PorosityFactorZ', False, 0.0)
    v219 = g.inp('_PorosityFactorY', False, 0.4)
    v220 = g.math('MULTIPLY_ADD', v218, v216, v219)
    v221 = g.math('MULTIPLY_ADD', v217, v210, v220)
    v222 = g.clampn(v221, 0, 1)
    v223 = g.math('MULTIPLY', v222, 0.95)
    v224 = g.math('ADD', v223, 0.05)
    v225 = g.inp('_OcclusionStrength', False, 1.0)
    v226 = g.mixf(v225, 1, v154)
    v227 = g.clampn(v226, 0, 1)
    v228 = g.mixf(v225, 1, v168[2])
    v229 = g.inp('_TrunkVertexAoStrength', False, 1.0)
    v230 = g.mixf(v229, 1, v154)
    v231 = g.math('MULTIPLY', v228, v230)
    v232 = g.mixf(1, v227, v231)
    v233 = g.inp('_EnableVerticalNormalBoostAO', False, 0.0)
    v234 = g.math('GREATER_THAN', v233, 0.5)
    v235 = g.sep(v21)
    v236 = g.inp('_VerticalNormalThreshold', False, 0.0)
    v237 = g.math('SUBTRACT', v235[1], v236)
    v238 = g.math('SUBTRACT', 1, v236)
    v239 = g.math('MAXIMUM', v238, 0.0001)
    v240 = g.math('DIVIDE', v237, v239)
    v241 = g.clampn(v240, 0, 1)
    v242 = g.inp('_VerticalNormalBoostAO', False, 0.0)
    v243 = g.math('MULTIPLY', v241, v242)
    v244 = g.math('SUBTRACT', 1, v243)
    v245 = g.math('MULTIPLY', v232, v244)
    v246 = g.mixf(v234, v232, v245)
    v247 = g.clampn(v246, 0, 1)
    v248 = g.inp('_TransmissionDistanceFade', False, 0.0)
    v249 = g.math('GREATER_THAN', v248, 0.5)
    v250 = g.vmath('DISTANCE', v20, v26)
    v251 = g.math('SUBTRACT', 60, v250)
    v252 = g.math('DIVIDE', v251, 10)
    v253 = g.clampn(v252, 0, 1)
    v254 = g.mixf(v249, 1, v253)
    v255 = g.inp('_Transmission', False, 0.2)
    v256 = g.math('MULTIPLY', v255, v254)
    v257 = g.math('MULTIPLY', v256, 0)
    v258 = g.inp('_AoAffectTransmissionStart', False, 0.0)
    v259 = g.inp('_AoAffectTransmissionRange', False, 0.01)
    v260 = g.math('SUBTRACT', v246, v258)
    v261 = g.math('MAXIMUM', v259, 0.0001)
    v262 = g.math('DIVIDE', v260, v261)
    v263 = g.clampn(v262, 0, 1)
    v264 = g.math('MULTIPLY', v257, v263)
    v265 = g.inp('_SubsurfaceIntensity', False, 0.0)
    v266 = g.math('MULTIPLY', v265, 0)
    v267 = g.inp('_AoAffectSubsurfaceStart', False, 0.0)
    v268 = g.inp('_AoAffectSubsurfaceRange', False, 0.01)
    v269 = g.math('SUBTRACT', v246, v267)
    v270 = g.math('MAXIMUM', v268, 0.0001)
    v271 = g.math('DIVIDE', v269, v270)
    v272 = g.clampn(v271, 0, 1)
    v273 = g.math('MULTIPLY', v266, v272)
    v274 = g.math('MAXIMUM', v264, v273)
    v275 = g.clampn(v274, 0, 1)
    v276 = g.inp('_FakeDirectionalShadowStrength', False, 0.0)
    v277 = g.math('GREATER_THAN', v276, 0)
    v278 = g.vmath('NORMALIZE', v21)
    v279 = g.inp('_DiffuseUseVertexNormal', False, 1.0)
    v280 = g.mixv(v279, v206, v278)
    v281 = g.inp('_MainLightPosition', True, (0.0, 0.0, 0.0))
    v282 = g.inp('_MainLightPosition_w', False, 0.0)
    v283 = g.vmath('SCALE', v281, s=-1.0)
    v284 = g.vmath('DOT_PRODUCT', v280, v283)
    v285 = g.math('MULTIPLY_ADD', v284, 0.5, 0.5)
    v286 = g.inp('_FakeDirectionalShadowPow', False, 1.0)
    v287 = g.math('POWER', v285, v286)
    v288 = g.math('MULTIPLY', v287, v276)
    v289 = g.math('SUBTRACT', 1, v288)
    v290 = g.clampn(v289, 0, 1)
    v291 = g.inp('_OcclusionShadow', False, 0.0)
    v292 = g.mixf(v291, 1, v246)
    v293 = g.mixf(v292, 1, v290)
    v294 = g.bc(v293)
    v295 = g.vmath('MULTIPLY', v162, v294)
    v296 = g.mixv(v277, v162, v295)
    v297 = g.math('SUBTRACT', 1.0, 1)
    v298 = g.inp('_EnableCanopyColorRamp', False, 0.0)
    v299 = g.math('GREATER_THAN', v298, 0.5)
    v300 = g.math('MULTIPLY', v297, v299)
    v301 = g.sep(v2)
    v302 = g.clampn(v301[1], 0, 1)
    v303 = g.inp('_CanopyRampStartAtTop', False, 0.0)
    v304 = g.math('GREATER_THAN', v303, 0.5)
    v305 = g.math('SUBTRACT', 1, v302)
    v306 = g.mixf(v304, v302, v305)
    v307 = g.inp('_CanopyRampRange', False, 0.0)
    v308 = g.math('SUBTRACT', v306, v307)
    v309 = g.inp('_CanopyRampTransitionRange', False, 0.01)
    v310 = g.math('MAXIMUM', v309, 0.0001)
    v311 = g.math('DIVIDE', v308, v310)
    v312 = g.clampn(v311, 0, 1)
    v313 = g.inp('_CanopyRampIntensity', False, 1.0)
    v314 = g.math('MULTIPLY', v312, v313)
    v315 = g.inp('_CanopyRampColor', True, (1.0, 1.0, 1.0))
    v316 = g.inp('_CanopyRampColor_w', False, 1.0)
    v317 = g.inp('_CanopyRampColorBrighterScale', False, 1.0)
    v318 = g.bc(v317)
    v319 = g.vmath('MULTIPLY', v315, v318)
    v320 = g.vmath('MAXIMUM', v319, (0, 0, 0))
    v321 = g.vmath('MINIMUM', v320, (1, 1, 1))
    v322 = g.vmath('MULTIPLY', v296, v321)
    v323 = g.inp('_CanopyRampColorCover', False, 0.0)
    v324 = g.mixv(v323, v322, v321)
    v325 = g.mixv(v314, v296, v324)
    v326 = g.mixv(v300, v296, v325)
    v327 = g.math('SUBTRACT', 1.0, 1)
    v328 = g.inp('_EnableAoTuneColor', False, 0.0)
    v329 = g.math('GREATER_THAN', v328, 0.5)
    v330 = g.math('MULTIPLY', v327, v329)
    v331 = g.inp('_FlipAoMask', False, 0.0)
    v332 = g.math('GREATER_THAN', v331, 0.5)
    v333 = g.math('SUBTRACT', 1, v154)
    v334 = g.mixf(v332, v154, v333)
    v335 = g.inp('_AoMaskTuneColorRampStart', False, 0.0)
    v336 = g.math('SUBTRACT', v334, v335)
    v337 = g.inp('_AoMaskTuneColorRampRange', False, 0.2)
    v338 = g.math('MAXIMUM', v337, 0.0001)
    v339 = g.math('DIVIDE', v336, v338)
    v340 = g.clampn(v339, 0, 1)
    v341 = g.inp('_AoMaskTuneColorIntensity', False, 1.0)
    v342 = g.math('MULTIPLY', v340, v341)
    v343 = g.inp('_AoMaskTuneColor', True, (1.0, 1.0, 1.0))
    v344 = g.inp('_AoMaskTuneColor_w', False, 1.0)
    v345 = g.inp('_AoMaskTuneColorBrighterScale', False, 1.0)
    v346 = g.bc(v345)
    v347 = g.vmath('MULTIPLY', v343, v346)
    v348 = g.vmath('MAXIMUM', v347, (0, 0, 0))
    v349 = g.vmath('MINIMUM', v348, (1, 1, 1))
    v350 = g.vmath('MULTIPLY', v326, v349)
    v351 = g.inp('_AoMaskTuneColorCover', False, 0.0)
    v352 = g.mixv(v351, v350, v349)
    v353 = g.mixv(v342, v326, v352)
    v354 = g.mixv(v330, v326, v353)
    v355 = g.mixf(v330, v314, v342)
    v356 = g.math('SUBTRACT', 1.0, 1)
    v357 = g.inp('_EnableBlendColor', False, 0.0)
    v358 = g.math('GREATER_THAN', v357, 0.5)
    v359 = g.math('MULTIPLY', v356, v358)
    v360 = g.inp('_BlendWithVertexNormal', False, 0.0)
    v361 = g.math('GREATER_THAN', v360, 0.5)
    v362 = g.vmath('NORMALIZE', v21)
    v363 = g.mixv(v361, v206, v362)
    v364 = g.sep(v363)
    v365 = g.math('MULTIPLY_ADD', v364[1], 0.5, 0.5)
    v366 = g.inp('_BlendNormalAdd', False, 0.0)
    v367 = g.math('ADD', v365, v366)
    v368 = g.clampn(v367, 0, 1)
    v369 = g.inp('_BlendColor', True, (1.0, 1.0, 1.0))
    v370 = g.inp('_BlendColor_w', False, 1.0)
    v371 = g.vmath('MULTIPLY', v354, v369)
    v372 = g.inp('_BlendNormalPower', False, 1.0)
    v373 = g.math('MAXIMUM', v372, 0.001)
    v374 = g.math('POWER', v368, v373)
    v375 = g.mixv(v374, v354, v371)
    v376 = g.mixv(v359, v354, v375)
    v377 = g.mixf(v359, v355, v368)
    v378 = g.inp('_EnableTrunkRamp', False, 0.0)
    v379 = g.math('GREATER_THAN', v378, 0.5)
    v380 = g.math('MULTIPLY', 1, v379)
    v381 = g.clampn(v301[1], 0, 1)
    v382 = g.inp('_TrunkRampRange', False, 0.0)
    v383 = g.math('SUBTRACT', v382, v381)
    v384 = g.inp('_TrunkRampTransitionRange', False, 0.01)
    v385 = g.math('MAXIMUM', v384, 0.0001)
    v386 = g.math('DIVIDE', v383, v385)
    v387 = g.clampn(v386, 0, 1)
    v388 = g.inp('_TrunkRampIntensity', False, 1.0)
    v389 = g.math('MULTIPLY', v387, v388)
    v390 = g.inp('_TrunkRampColor', True, (1.0, 1.0, 1.0))
    v391 = g.inp('_TrunkRampColor_w', False, 1.0)
    v392 = g.vmath('MULTIPLY', v376, v390)
    v393 = g.mixv(v389, v376, v392)
    v394 = g.mixv(v380, v376, v393)
    v395 = g.mixf(v380, v306, v381)
    v396 = g.mixf(v380, v377, v389)
    v397 = g.inp('_EnableEmissiveMap', False, 0.0)
    v398 = g.math('GREATER_THAN', v397, 0.5)
    v399 = g.inp('_EmissiveUVSet', False, 0.0)
    v400 = g.math('GREATER_THAN', v399, 0.5)
    v401 = g.mixv(v400, v0, v14)
    v402 = g.inp('_EmissiveMap_ST', True, (1.0, 1.0, 0.0))
    v403 = g.inp('_EmissiveMap_ST_w', False, 0.0)
    v404 = g.sep(v402)
    v405 = g.comb(v404[0], v404[1], 0.0)
    v406 = g.comb(v404[2], v403, 0.0)
    v407 = g.vmath('MULTIPLY', v401, v405)
    v408 = g.vmath('ADD', v407, v406)
    g.out_('F7_EmissiveMap_uv', v408, True)
    v409 = g.inp('F7_EmissiveMap', True, (1.0, 1.0, 1.0))
    v410 = g.inp('F7_EmissiveMap_alpha', False, 1.0)
    v411 = g.inp('_EmissiveMaskChannel', False, 0.0)
    v412 = g.math('LESS_THAN', v411, 0.5)
    v413 = g.sep(v409)
    v414 = g.inp('_EmissiveColorR', True, (0.0, 0.0, 0.0))
    v415 = g.inp('_EmissiveColorR_w', False, 1.0)
    v416 = g.bc(v413[0])
    v417 = g.vmath('MULTIPLY', v416, v414)
    v418 = g.inp('_EmissiveColorG', True, (0.0, 0.0, 0.0))
    v419 = g.inp('_EmissiveColorG_w', False, 0.0)
    v420 = g.bc(v413[1])
    v421 = g.vmath('MULTIPLY', v420, v418)
    v422 = g.vmath('ADD', v417, v421)
    v423 = g.inp('_EmissiveColorB', True, (0.0, 0.0, 0.0))
    v424 = g.inp('_EmissiveColorB_w', False, 0.0)
    v425 = g.bc(v413[2])
    v426 = g.vmath('MULTIPLY', v425, v423)
    v427 = g.vmath('ADD', v422, v426)
    v428 = g.inp('_EmissiveColorA', True, (0.0, 0.0, 0.0))
    v429 = g.inp('_EmissiveColorA_w', False, 0.0)
    v430 = g.bc(v410)
    v431 = g.vmath('MULTIPLY', v430, v428)
    v432 = g.vmath('ADD', v427, v431)
    v433 = g.math('LESS_THAN', v411, 1.5)
    v434 = g.vmath('MULTIPLY', v409, v414)
    v435 = g.bc(v149)
    v436 = g.vmath('MULTIPLY', v435, v414)
    v437 = g.mixv(v433, v436, v434)
    v438 = g.mixv(v412, v437, v432)
    v439 = g.inp('_AlbedoAffectEmissive', False, 1.0)
    v440 = g.math('LESS_THAN', v439, 0.5)
    v441 = g.vmath('MULTIPLY', v438, v394)
    v442 = g.mixv(v440, v438, v441)
    v443 = g.mixv(v398, (0, 0, 0), v442)
    v444 = g.inp('_EnableVertColorEmissive', False, 0.0)
    v445 = g.math('GREATER_THAN', v444, 0.5)
    v446 = g.inp('_VertColorEmissiveChannelVector', True, (1.0, 0.0, 0.0))
    v447 = g.inp('_VertColorEmissiveChannelVector_w', False, 0.0)
    v448 = g.vmath('DOT_PRODUCT', v11, v446)
    v449 = g.inp('_VertColorEmissiveFlip', False, 0.0)
    v450 = g.math('GREATER_THAN', v449, 0.5)
    v451 = g.math('SUBTRACT', 1, v448)
    v452 = g.mixf(v450, v448, v451)
    v453 = g.inp('_VertColorEmissiveBias', False, 0.0)
    v454 = g.math('ADD', v452, v453)
    v455 = g.clampn(v454, 0, 1)
    v456 = g.inp('_VertColorEmissiveColor', True, (0.0, 0.0, 0.0))
    v457 = g.inp('_VertColorEmissiveColor_w', False, 1.0)
    v458 = g.bc(v455)
    v459 = g.vmath('MULTIPLY', v456, v458)
    v460 = g.inp('_VertColorEmissiveAlbedoAffect', False, 1.0)
    v461 = g.math('LESS_THAN', v460, 0.5)
    v462 = g.vmath('MULTIPLY', v459, v394)
    v463 = g.mixv(v461, v459, v462)
    v464 = g.vmath('ADD', v443, v463)
    v465 = g.mixv(v445, v443, v464)
    v466 = g.mixf(v445, v396, v455)
    v467 = g.inp('_CrossCardViewCulling', False, 0.0)
    v468 = g.math('GREATER_THAN', v467, 0.5)
    v469 = g.vmath('SUBTRACT', v26, v20)
    v470 = g.vmath('NORMALIZE', v469)
    v471 = g.vmath('NORMALIZE', v21)
    v472 = g.vmath('DOT_PRODUCT', v471, v470)
    v473 = g.math('ABSOLUTE', v472, 0.0)
    v474 = g.inp('_CrossCardViewCullingThreshold', False, 0.4)
    v475 = g.math('SUBTRACT', v473, v474)
    v476 = g.inp('_CrossCardViewCullingFadeValue', False, 0.5)
    v477 = g.math('MAXIMUM', v476, 0.0001)
    v478 = g.math('DIVIDE', v475, v477)
    v479 = g.clampn(v478, 0, 1)
    v480 = g.math('MULTIPLY', v152, v479)
    v481 = g.mixf(v468, v152, v480)
    v482 = g.mixf(v121, v58, v481)
    v483 = g.mixv(v121, v65, v394)
    v484 = g.mixf(v121, v114, v210)
    v485 = g.mixf(v121, v115, v216)
    v486 = g.mixf(v121, v116, v247)
    v487 = g.mixf(v121, v117, v224)
    v488 = g.mixv(v121, v36, v206)
    v489 = g.mixf(v121, 0.0, v275)
    v490 = g.mixv(v121, (0.0, 0.0, 0.0), v162)
    v491 = g.mixv(v121, (0, 0, 0), v465)
    v492 = g.mixv(v121, v132, v206)
    v493 = g.comb(v487, v487, v487)
    v494 = g.math('SUBTRACT', 1, v484)
    v495 = g.vmath('NORMALIZE', v488)
    v496 = g.vmath('DOT_PRODUCT', v495, v28)
    v497 = g.math('MAXIMUM', v496, 0)
    v498 = g.math('MULTIPLY', 0.08, v487)
    v499 = g.math('MULTIPLY', 0.08, v487)
    v500 = g.math('MULTIPLY', 0.08, v487)
    v501 = g.comb(v498, v499, v500)
    v502 = g.mixv(v485, v501, v483)
    v503 = g.math('SUBTRACT', 1, v485)
    v504 = g.bc(v503)
    v505 = g.vmath('MULTIPLY', v483, v504)
    v506 = g.inp('_UseThinFilm', False, 0.0)
    v507 = g.math('GREATER_THAN', v506, 0.5)
    v508 = g.inp('_ThinFilmIOR', False, 1.4)
    v509 = g.inp('_ThinFilmThickness', False, 0.5)
    v510 = g.math('MULTIPLY', v509, 1000)
    v511 = g.inp('M_PI', False, 0.0)
    v512 = g.group_named('RCE_RuriEvalIridescence', [('outsideIor', 1), ('eta2', v508), ('cosTheta1', v497), ('iridescenceThickness', v510), ('baseF0', v502), ('M_PI', v511)])
    v513 = g.inp('_ThinFilmWeight', False, 0.0)
    v514 = g.inp('_ThinFilmIntensity', False, 1.0)
    v515 = g.math('MULTIPLY', v513, v514)
    v516 = g.clampn(v515)
    v517 = g.mixv(v516, v502, v512[0])
    v518 = g.mixv(v507, v502, v517)
    v519 = g.inp('_SubsurfaceShadingMode', False, 0.0)
    v520 = g.math('LESS_THAN', v519, 0.5)
    v521 = g.inp('_SubsurfaceColor', True, (0.8, 0.8, 0.8))
    v522 = g.inp('_SubsurfaceColor_w', False, 1.0)
    v523 = g.vmath('MULTIPLY', v521, v483)
    v524 = g.mixv(v520, v523, v521)
    v525 = g.inp('_MaxSubsurfaceThickness', False, 1.0)
    v526 = g.inp('_UseSubsurfaceThicknessMap', False, 0.0)
    v527 = g.math('GREATER_THAN', v526, 0.5)
    v528 = g.inp('_MinSubsurfaceThickness', False, 0.0)
    g.out_('F8_SubsurfaceMap_uv', v0, True)
    v529 = g.inp('F8_SubsurfaceMap', True, (1.0, 1.0, 1.0))
    v530 = g.inp('F8_SubsurfaceMap_alpha', False, 1.0)
    v531 = g.sep(v529)
    v532 = g.mixf(v531[0], v528, v525)
    v533 = g.mixf(v527, v525, v532)
    v534 = g.group_named('RCE_HgEnvBRDF', [('roughness', v484), ('NoV', v497), ('f0', v518)])
    v535 = g.vmath('SCALE', v28, s=-1.0)
    v536 = g.vmath('DOT_PRODUCT', v495, v535)
    v537 = g.math('MULTIPLY', 2.0, v536)
    v538 = g.vmath('SCALE', v495, s=v537)
    v539 = g.vmath('SUBTRACT', v535, v538)
    v540 = g.inp('_UseCustomIBL', False, 0.0)
    v541 = g.math('GREATER_THAN', v540, 0.5)
    v542 = g.math('MULTIPLY', 0.7, v484)
    v543 = g.math('SUBTRACT', 1.7, v542)
    v544 = g.math('MULTIPLY', v484, v543)
    v545 = g.math('MULTIPLY', v544, 6)
    v546 = g.u2b(v539)
    g.out_('F9_IBL_CustomIBL_dir', v546, True)
    g.out_('F9_IBL_CustomIBL_mip', v545, False)
    v547 = g.inp('F9_IBL_CustomIBL', True, (0.2159, 0.2159, 0.2159))
    v548 = g.inp('F9_IBL_CustomIBL_alpha', False, 1.0)
    v549 = g.inp('_CustomIBLIntensity', False, 1.0)
    v550 = g.bc(v549)
    v551 = g.vmath('MULTIPLY', v547, v550)
    g.out_('C1_SpecularRadiance_direction', v539, True)
    g.out_('C1_SpecularRadiance_position', v20, True)
    g.out_('C1_SpecularRadiance_roughness', v484, False)
    v552 = g.inp('C1_SpecularRadiance', True, (0.2159, 0.2159, 0.2159))
    v553 = g.mixv(v541, v552, v551)
    v554 = g.inp('_PlanarReflection', False, 0.0)
    v555 = g.math('GREATER_THAN', v554, 0.5)
    g.out_('F10_PlanarReflectionTexture_uv', v29, True)
    v556 = g.inp('F10_PlanarReflectionTexture', True, (1.0, 1.0, 1.0))
    v557 = g.inp('F10_PlanarReflectionTexture_alpha', False, 1.0)
    v558 = g.inp('_PlanarReflectionTint', True, (1.0, 1.0, 1.0))
    v559 = g.inp('_PlanarReflectionTint_w', False, 1.0)
    v560 = g.vmath('MULTIPLY', v556, v558)
    v561 = g.mixv(v559, v553, v560)
    v562 = g.mixv(v555, v553, v561)
    v563 = g.inp('_EnableSubsurface', False, 0.0)
    v564 = g.math('GREATER_THAN', v563, 0.5)
    v565 = g.inp('_SubsurfaceIndirect', False, 1.0)
    v566 = g.comb(v565, v565, v565)
    v567 = g.vmath('MULTIPLY', v524, v566)
    v568 = g.vmath('ADD', v567, v505)
    v569 = g.mixv(v564, v505, v568)
    v570 = g.vmath('MULTIPLY', v569, v30)
    v571 = g.bc(v486)
    v572 = g.vmath('MULTIPLY', v570, v571)
    v573 = g.inp('_EnvironmentGlobalParams0', True, (1.67, 1.5, 1.0))
    v574 = g.inp('_EnvironmentGlobalParams0_w', False, 0.0)
    v575 = g.sep(v573)
    v576 = g.bc(v575[0])
    v577 = g.vmath('MULTIPLY', v572, v576)
    v578 = g.comb(v534[0], v534[0], v534[0])
    v579 = g.comb(v534[1], v534[1], v534[1])
    v580 = g.vmath('MULTIPLY', v518, v578)
    v581 = g.vmath('ADD', v580, v579)
    v582 = g.vmath('MULTIPLY', v581, v562)
    v583 = g.bc(v575[1])
    v584 = g.vmath('MULTIPLY', v582, v583)
    v585 = g.vmath('ADD', v577, v584)
    v586 = g.inp('C2_MainLight_direction', True, (0.0, 0.0, 0.0))
    v587 = g.inp('C2_MainLight_color', True, (0.0, 0.0, 0.0))
    v588 = g.inp('C2_MainLight_distanceAttenuation', False, 0.0)
    v589 = g.inp('C2_MainLight_shadowAttenuation', False, 0.0)
    v590 = g.inp('C2_MainLight_layerMask', False, 0.0)
    v591 = g.inp('_MainLightOcclusionProbes', True, (0.0, 0.0, 0.0))
    v592 = g.inp('_MainLightOcclusionProbes_w', False, 0.0)
    v593 = g.vmath('ADD', v586, v28)
    v594 = g.vmath('DOT_PRODUCT', v593, v593)
    v595 = g.math('MAXIMUM', v594, 1E-08)
    v596 = g.math('INVERSE_SQRT', v595, 0.0)
    v597 = g.bc(v596)
    v598 = g.vmath('MULTIPLY', v593, v597)
    v599 = g.vmath('DOT_PRODUCT', v586, v495)
    v600 = g.clampn(v599)
    v601 = g.vmath('DOT_PRODUCT', v495, v598)
    v602 = g.clampn(v601)
    v603 = g.vmath('DOT_PRODUCT', v28, v598)
    v604 = g.clampn(v603)
    v605 = g.group_named('RCE_HgDirectLightEnergy', [('roughness', v484), ('f0', v518), ('NoL', v600), ('NoH', v602), ('NoV', v497), ('VoH', v604)])
    v606 = g.math('MULTIPLY', v588, 1.0)
    v607 = g.comb(v600, v600, v600)
    v608 = g.bc(v600)
    v609 = g.vmath('MULTIPLY', v505, v608)
    v610 = g.vmath('MULTIPLY', v605[0], v607)
    v611 = g.vmath('ADD', v610, v609)
    v612 = g.math('GREATER_THAN', v563, 0.5)
    v613 = g.vmath('DOT_PRODUCT', v586, v495)
    v614 = g.vmath('DOT_PRODUCT', v28, v586)
    v615 = g.inp('_SubsurfaceSelfShadowBias', False, 0.0)
    v616 = g.inp('_SubsurfaceEnableSelfShadowBias', False, 0.0)
    v617 = g.group_named('RCE_HgSssLobe', [('amount', v533), ('rawNoL', v613), ('VdotL', v614), ('selfShadowBias', v615), ('enableSelfShadowBias', v616)])
    v618 = g.bc(v617[0])
    v619 = g.vmath('MULTIPLY', v618, v524)
    v620 = g.vmath('ADD', v611, v619)
    v621 = g.mixv(v612, v611, v620)
    v622 = g.bc(v606)
    v623 = g.vmath('MULTIPLY', v587, v622)
    v624 = g.vmath('MULTIPLY', v621, v623)
    v625 = g.vmath('ADD', v585, v624)
    v626 = g.inp('C3_AdditionalLightCount', False, 0.0)
    v627 = g.math('SUBTRACT', v626, 0)
    v628 = g.math('CEIL', v627, 0.0)
    v629 = g.math('MAXIMUM', v628, 0.0)
    g.out_('Z0_it', v629, False)
    g.out_('Z0_s_H', v598, True)
    g.out_('Z0_s_L', v586, True)
    g.out_('Z0_s_LV', v593, True)
    g.out_('Z0_s_N', v495, True)
    g.out_('Z0_s_NoH', v602, False)
    g.out_('Z0_s_NoL', v600, False)
    g.out_('Z0_s_NoV', v497, False)
    g.out_('Z0_s_P', v20, True)
    g.out_('Z0_s_V', v28, True)
    g.out_('Z0_s_VoH', v604, False)
    g.out_('Z0_s_Lloop0', 1.0, False)
    g.out_('Z0_s_color', v625, True)
    g.out_('Z0_s_energy', v605[0], True)
    g.out_('Z0_s_f0', v518, True)
    g.out_('Z0_s_inputData_bakedGI', v30, True)
    g.out_('Z0_s_inputData_fogCoord', 0, False)
    g.out_('Z0_s_inputData_normalWS', v488, True)
    g.out_('Z0_s_inputData_normalizedScreenSpaceUV', v29, True)
    g.out_('Z0_s_inputData_positionCS', v17, True)
    g.out_('Z0_s_inputData_positionCS_w', v18, False)
    g.out_('Z0_s_inputData_positionWS', v20, True)
    g.out_('Z0_s_inputData_shadowCoord', (0.0, 0.0, 0.0), True)
    g.out_('Z0_s_inputData_shadowCoord_w', 0.0, False)
    g.out_('Z0_s_inputData_shadowMask', (1, 1, 1), True)
    g.out_('Z0_s_inputData_shadowMask_w', 1, False)
    g.out_('Z0_s_inputData_vertexLighting', (0, 0, 0), True)
    g.out_('Z0_s_inputData_viewDirectionWS', v28, True)
    g.out_('Z0_s_lightIndex', 0, False)
    g.out_('Z0_s_roughness', v484, False)
    g.out_('Z0_s_sssAmount', v533, False)
    g.out_('Z0_r___done', 0.0, False)
    g.out_('Z0_r_diffuse', v505, True)
    g.out_('Z0_r_sssTint', v524, True)
    g.out_('Z0_r_pixelLightCount', v626, False)
    v630 = g.inp('Z0_o_H', True)
    v631 = g.inp('Z0_o_L', True)
    v632 = g.inp('Z0_o_LV', True)
    v633 = g.inp('Z0_o_N', True)
    v634 = g.inp('Z0_o_NoH', False)
    v635 = g.inp('Z0_o_NoL', False)
    v636 = g.inp('Z0_o_NoV', False)
    v637 = g.inp('Z0_o_P', True)
    v638 = g.inp('Z0_o_V', True)
    v639 = g.inp('Z0_o_VoH', False)
    v640 = g.inp('Z0_o_Lloop0', False)
    v641 = g.inp('Z0_o_color', True)
    v642 = g.inp('Z0_o_energy', True)
    v643 = g.inp('Z0_o_f0', True)
    v644 = g.inp('Z0_o_inputData_bakedGI', True)
    v645 = g.inp('Z0_o_inputData_fogCoord', False)
    v646 = g.inp('Z0_o_inputData_normalWS', True)
    v647 = g.inp('Z0_o_inputData_normalizedScreenSpaceUV', True)
    v648 = g.inp('Z0_o_inputData_positionCS', True)
    v649 = g.inp('Z0_o_inputData_positionCS_w', False)
    v650 = g.inp('Z0_o_inputData_positionWS', True)
    v651 = g.inp('Z0_o_inputData_shadowCoord', True)
    v652 = g.inp('Z0_o_inputData_shadowCoord_w', False)
    v653 = g.inp('Z0_o_inputData_shadowMask', True)
    v654 = g.inp('Z0_o_inputData_shadowMask_w', False)
    v655 = g.inp('Z0_o_inputData_vertexLighting', True)
    v656 = g.inp('Z0_o_inputData_viewDirectionWS', True)
    v657 = g.inp('Z0_o_lightIndex', False)
    v658 = g.inp('Z0_o_roughness', False)
    v659 = g.inp('Z0_o_sssAmount', False)
    v660 = g.vmath('ADD', v641, v491)
    v661 = g.math('COMPARE', v60, 0, 1e-05)
    v662 = g.math('SUBTRACT', 1.0, v661)
    v663 = g.vmath('SUBTRACT', v20, v26)
    v664 = g.inp('_RuriVoxelSizeMeters', False, 0.0)
    v665 = g.bc(v664)
    v666 = g.vmath('DIVIDE', v663, v665)
    v667 = g.vmath('ADD', v483, v660)
    v668 = g.vmath('LENGTH', v666)
    v669 = g.sep(v666)
    v670 = g.comb(v669[0], v669[2], 0.0)
    v671 = g.vmath('LENGTH', v670)
    v672 = g.math('ABSOLUTE', v669[1], 0.0)
    v673 = g.math('MAXIMUM', v671, v672)
    v674 = g.inp('_RuriFogEnvironmentalStart', False, 0.0)
    v675 = g.inp('_RuriFogEnvironmentalEnd', False, 0.0)
    v676 = g.inp('_RuriFogRenderDistanceStart', False, 0.0)
    v677 = g.inp('_RuriFogRenderDistanceEnd', False, 0.0)
    v678 = g.inp('_RuriFogColor', True, (0.0, 0.0, 0.0))
    v679 = g.inp('_RuriFogColor_w', False, 0.0)
    v680 = g.group_named('RCE_RuriApplyFog', [('inColor', v667), ('inColor_w', 1), ('sphericalVertexDistance', v668), ('cylindricalVertexDistance', v673), ('environmentalStart', v674), ('environmentalEnd', v675), ('renderDistanceStart', v676), ('renderDistanceEnd', v677), ('fogColor', v678), ('fogColor_w', v679)])
    v681 = g.mixv(v662, v660, v680[0])
    g.out_('ret_gBuffer0', v681, True)
    g.out_('ret_gBuffer0_w', v482, False)
    g.out_('ret_gBuffer1', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer1_w', 0.0, False)
    g.out_('ret_gBuffer2', (0.0, 0.0, 0.0), True)
    g.out_('ret_gBuffer2_w', 0.0, False)
    g.out_('ret_color', v660, True)
    g.out_('ret_color_w', v482, False)
    g.out_('ret_depth', 0.0, False)
    g.out_('ret_shadowMask', (0.0, 0.0, 0.0), True)
    g.out_('ret_shadowMask_w', 0.0, False)
    g.out_('ret_meshRenderingLayers', 0.0, False)
    g.out_('__clip', v113, False)


SHARED_GROUPS = [
    ('RCE_F_Schlick', build_RCE_F_Schlick),
    ('RCE_FoliageGradientBand', build_RCE_FoliageGradientBand),
    ('RCE_HgEnvBRDF', build_RCE_HgEnvBRDF),
    ('RCE_HgEnvBRDFApproxDFG', build_RCE_HgEnvBRDFApproxDFG),
    ('RCE_HgDirectLightEnergy', build_RCE_HgDirectLightEnergy),
    ('RCE_HgSssLobe', build_RCE_HgSssLobe),
    ('RCE_RuriEvalSensitivity', build_RCE_RuriEvalSensitivity),
    ('RCE_RuriFresnel0ToIor', build_RCE_RuriFresnel0ToIor),
    ('RCE_RuriEvalIridescence', build_RCE_RuriEvalIridescence),
    ('RCE_RuriLinearFogValue', build_RCE_RuriLinearFogValue),
    ('RCE_RuriRefract', build_RCE_RuriRefract),
    ('RCE_RuriTotalFogValue', build_RCE_RuriTotalFogValue),
    ('RCE_RuriApplyFog', build_RCE_RuriApplyFog),
    ('RCE_ViewMatrixRow0', build_RCE_ViewMatrixRow0),
    ('RCE_ViewMatrixRow1', build_RCE_ViewMatrixRow1),
    ('RCE_ViewMatrixRow2', build_RCE_ViewMatrixRow2),
    ('RCE_Z_Ruri_Endfield_Scene_Lit_0', build_RCE_Z_Ruri_Endfield_Scene_Lit_0),
    ('RCE_Z_Ruri_Endfield_Scene_Lit_1', build_RCE_Z_Ruri_Endfield_Scene_Lit_1),
    ('RCE_Z_Ruri_Endfield_Scene_LitForward_0', build_RCE_Z_Ruri_Endfield_Scene_LitForward_0),
    ('RCE_Z_Ruri_Endfield_Scene_LitForward_1', build_RCE_Z_Ruri_Endfield_Scene_LitForward_1),
    ('RCE_Z_Ruri_Endfield_Scene_LitTransparent_0', build_RCE_Z_Ruri_Endfield_Scene_LitTransparent_0),
    ('RCE_Z_Ruri_Endfield_Scene_LitTransparent_1', build_RCE_Z_Ruri_Endfield_Scene_LitTransparent_1),
    ('RCE_Z_Ruri_Endfield_Scene_LitEffect_0', build_RCE_Z_Ruri_Endfield_Scene_LitEffect_0),
    ('RCE_Z_Ruri_Endfield_Scene_LitEffect_1', build_RCE_Z_Ruri_Endfield_Scene_LitEffect_1),
    ('RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_0', build_RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_0),
    ('RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_1', build_RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_1),
    ('RCE_Z_Ruri_Endfield_Scene_LitHLod_0', build_RCE_Z_Ruri_Endfield_Scene_LitHLod_0),
    ('RCE_Z_Ruri_Endfield_Scene_LitHLod_1', build_RCE_Z_Ruri_Endfield_Scene_LitHLod_1),
    ('RCE_Z_Ruri_Endfield_Scene_ContainerWater_0', build_RCE_Z_Ruri_Endfield_Scene_ContainerWater_0),
    ('RCE_Z_Ruri_Endfield_Scene_Leaf_0', build_RCE_Z_Ruri_Endfield_Scene_Leaf_0),
    ('RCE_Z_Ruri_Endfield_Scene_Grass_0', build_RCE_Z_Ruri_Endfield_Scene_Grass_0),
    ('RCE_Z_Ruri_Endfield_Scene_Trunk_0', build_RCE_Z_Ruri_Endfield_Scene_Trunk_0),
]

PARTS = {
    'Lit': ('Ruri Endfield Scene Lit', build_Ruri_Endfield_Scene_Lit),
    'LitForward': ('Ruri Endfield Scene Lit', build_Ruri_Endfield_Scene_Lit),
    'LitTransparent': ('Ruri Endfield Scene LitTransparent', build_Ruri_Endfield_Scene_LitTransparent),
    'LitEffect': ('Ruri Endfield Scene LitTransparent', build_Ruri_Endfield_Scene_LitTransparent),
    'LitEffectBlend': ('Ruri Endfield Scene LitTransparent', build_Ruri_Endfield_Scene_LitTransparent),
    'LitHLod': ('Ruri Endfield Scene LitHLod', build_Ruri_Endfield_Scene_LitHLod),
    'Unlit': ('Ruri Endfield Scene Unlit', build_Ruri_Endfield_Scene_Unlit),
    'ContainerWater': ('Ruri Endfield Scene ContainerWater', build_Ruri_Endfield_Scene_ContainerWater),
    'Leaf': ('Ruri Endfield Scene Leaf', build_Ruri_Endfield_Scene_Leaf),
    'Grass': ('Ruri Endfield Scene Grass', build_Ruri_Endfield_Scene_Grass),
    'Trunk': ('Ruri Endfield Scene Trunk', build_Ruri_Endfield_Scene_Trunk),
}

CASCADE = {
    'Lit': 5,
    'LitForward': 5,
    'LitTransparent': 5,
    'LitEffect': 5,
    'LitEffectBlend': 5,
    'LitHLod': 5,
    'Unlit': 2,
    'ContainerWater': 4,
    'Leaf': 4,
    'Grass': 4,
    'Trunk': 4,
}

FETCHES = {
    'Lit': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_MacroNormalMap', 'slot': '_MacroNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DetailMap', 'slot': '_DetailMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_MaskMap', 'slot': '_MaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_Layer1BaseMap', 'slot': '_Layer1BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_Layer1BumpMap', 'slot': '_Layer1BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_BaseHeightMap', 'slot': '_BaseHeightMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_LayerBlendMaskMap', 'slot': '_LayerBlendMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F16_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F17_MatcapMap', 'slot': '_MatcapMap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F18_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F19_ParallaxMap', 'slot': '_ParallaxMap', 'depth': 2, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F20_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 3, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F21_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F22_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F23_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'LitForward': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_MacroNormalMap', 'slot': '_MacroNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DetailMap', 'slot': '_DetailMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_MaskMap', 'slot': '_MaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_Layer1BaseMap', 'slot': '_Layer1BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_Layer1BumpMap', 'slot': '_Layer1BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_BaseHeightMap', 'slot': '_BaseHeightMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_LayerBlendMaskMap', 'slot': '_LayerBlendMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F16_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F17_MatcapMap', 'slot': '_MatcapMap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F18_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F19_ParallaxMap', 'slot': '_ParallaxMap', 'depth': 2, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F20_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 3, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F21_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F22_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F23_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'LitTransparent': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_MacroNormalMap', 'slot': '_MacroNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DetailMap', 'slot': '_DetailMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_MaskMap', 'slot': '_MaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_Layer1BaseMap', 'slot': '_Layer1BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_Layer1BumpMap', 'slot': '_Layer1BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_BaseHeightMap', 'slot': '_BaseHeightMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_LayerBlendMaskMap', 'slot': '_LayerBlendMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F16_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F17_MatcapMap', 'slot': '_MatcapMap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F18_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F19_ParallaxMap', 'slot': '_ParallaxMap', 'depth': 2, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F20_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 3, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F21_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F22_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F23_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'LitEffect': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_MacroNormalMap', 'slot': '_MacroNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DetailMap', 'slot': '_DetailMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_MaskMap', 'slot': '_MaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_Layer1BaseMap', 'slot': '_Layer1BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_Layer1BumpMap', 'slot': '_Layer1BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_BaseHeightMap', 'slot': '_BaseHeightMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_LayerBlendMaskMap', 'slot': '_LayerBlendMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F16_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F17_MatcapMap', 'slot': '_MatcapMap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F18_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F19_ParallaxMap', 'slot': '_ParallaxMap', 'depth': 2, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F20_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 3, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F21_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F22_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F23_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'LitEffectBlend': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_MacroNormalMap', 'slot': '_MacroNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DetailMap', 'slot': '_DetailMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_MaskMap', 'slot': '_MaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_Layer1BaseMap', 'slot': '_Layer1BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_Layer1BumpMap', 'slot': '_Layer1BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_BaseHeightMap', 'slot': '_BaseHeightMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_LayerBlendMaskMap', 'slot': '_LayerBlendMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F16_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F17_MatcapMap', 'slot': '_MatcapMap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F18_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F19_ParallaxMap', 'slot': '_ParallaxMap', 'depth': 2, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F20_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 3, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F21_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F22_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F23_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'LitHLod': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_MacroNormalMap', 'slot': '_MacroNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_DetailMap', 'slot': '_DetailMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_MaskMap', 'slot': '_MaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_Layer1BaseMap', 'slot': '_Layer1BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_Layer1BumpMap', 'slot': '_Layer1BumpMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_BaseHeightMap', 'slot': '_BaseHeightMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_LayerBlendMaskMap', 'slot': '_LayerBlendMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F16_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F17_MatcapMap', 'slot': '_MatcapMap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F18_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F19_ParallaxMap', 'slot': '_ParallaxMap', 'depth': 2, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F20_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 3, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': False, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F21_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F22_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F23_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'Unlit': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'ContainerWater': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MatcapMap', 'slot': '_MatcapMap', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_IBL_ReflectionProbeCube', 'slot': 'IBL_ReflectionProbeCube', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F8_ParallaxMaskMap', 'slot': '_ParallaxMaskMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_ParallaxMap', 'slot': '_ParallaxMap', 'depth': 2, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F10_RefractTex', 'slot': '_RefractTex', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.0, 0.0, 0.0), 'neutral_alpha': 1.0},
        {'sock': 'F11_IceNormalMap', 'slot': '_IceNormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F12_IceOpacityMap', 'slot': '_IceOpacityMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F13_WaterNormalMap', 'slot': '_WaterNormalMap', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F14_WaterNormalMap', 'slot': '_WaterNormalMap', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F15_WaterNormalMap', 'slot': '_WaterNormalMap', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F16_DisplacementTex', 'slot': '_DisplacementTex', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 0.5), 'neutral_alpha': 1.0},
        {'sock': 'F17_IBL_ReflectionProbeCube', 'slot': 'IBL_ReflectionProbeCube', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F18_WaterCausticMap', 'slot': '_WaterCausticMap', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F19_WaterNormalMap', 'slot': '_WaterNormalMap', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F20_WaterNormalMap', 'slot': '_WaterNormalMap', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (0.5, 0.5, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F21_IBL_ReflectionProbeCube', 'slot': 'IBL_ReflectionProbeCube', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F22_WaterCausticMap', 'slot': '_WaterCausticMap', 'depth': 0, 'non_color': True, 'extension': 'MIRROR', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F23_IBL_ReflectionProbeCube', 'slot': 'IBL_ReflectionProbeCube', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
    ],
    'Leaf': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F9_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'Grass': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F9_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
    'Trunk': [
        {'sock': 'F0_BaseMap', 'slot': '_BaseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F1_VoxelAtlas', 'slot': '_VoxelAtlas', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F2_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F3_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F4_BaseColorMap', 'slot': '_BaseColorMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F5_NormalMap', 'slot': '_NormalMap', 'depth': 0, 'non_color': True, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F6_MROMap', 'slot': '_MROMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F7_EmissiveMap', 'slot': '_EmissiveMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F8_SubsurfaceMap', 'slot': '_SubsurfaceMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
        {'sock': 'F9_IBL_CustomIBL', 'slot': 'IBL_CustomIBL', 'depth': 1, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': True, 'mip': True, 'derivative_mip': True, 'neutral': (0.2159, 0.2159, 0.2159), 'neutral_alpha': 1.0},
        {'sock': 'F10_PlanarReflectionTexture', 'slot': '_PlanarReflectionTexture', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
    ],
}

ZONES = {
    'Lit': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_Lit_0', 'depth': 1, 'cascade': 2,
         'states': [('Lloop0', False), ('height', False), ('heightPrev', False), ('iter', False), ('offCur', True), ('offPrev', True), ('texHit', False), ('texPrev', False), ('uvP', True)],
         'reads': [('__done', False), ('steps', False), ('stepH', False), ('stepUV', True)],
         'uniforms': [('_ParallaxNoiseMapTilling', 0, 0.0, 0.0)],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxNoiseMap', 'slot': '_ParallaxNoiseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Scene_Lit_1', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'LitForward': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitForward_0', 'depth': 1, 'cascade': 2,
         'states': [('Lloop0', False), ('height', False), ('heightPrev', False), ('iter', False), ('offCur', True), ('offPrev', True), ('texHit', False), ('texPrev', False), ('uvP', True)],
         'reads': [('__done', False), ('steps', False), ('stepH', False), ('stepUV', True)],
         'uniforms': [('_ParallaxNoiseMapTilling', 0, 0.0, 0.0)],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxNoiseMap', 'slot': '_ParallaxNoiseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitForward_1', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'LitTransparent': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitTransparent_0', 'depth': 1, 'cascade': 2,
         'states': [('Lloop0', False), ('height', False), ('heightPrev', False), ('iter', False), ('offCur', True), ('offPrev', True), ('texHit', False), ('texPrev', False), ('uvP', True)],
         'reads': [('__done', False), ('steps', False), ('stepH', False), ('stepUV', True)],
         'uniforms': [('_ParallaxNoiseMapTilling', 0, 0.0, 0.0)],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxNoiseMap', 'slot': '_ParallaxNoiseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitTransparent_1', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'LitEffect': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitEffect_0', 'depth': 1, 'cascade': 2,
         'states': [('Lloop0', False), ('height', False), ('heightPrev', False), ('iter', False), ('offCur', True), ('offPrev', True), ('texHit', False), ('texPrev', False), ('uvP', True)],
         'reads': [('__done', False), ('steps', False), ('stepH', False), ('stepUV', True)],
         'uniforms': [('_ParallaxNoiseMapTilling', 0, 0.0, 0.0)],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxNoiseMap', 'slot': '_ParallaxNoiseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitEffect_1', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'LitEffectBlend': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_0', 'depth': 1, 'cascade': 2,
         'states': [('Lloop0', False), ('height', False), ('heightPrev', False), ('iter', False), ('offCur', True), ('offPrev', True), ('texHit', False), ('texPrev', False), ('uvP', True)],
         'reads': [('__done', False), ('steps', False), ('stepH', False), ('stepUV', True)],
         'uniforms': [('_ParallaxNoiseMapTilling', 0, 0.0, 0.0)],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxNoiseMap', 'slot': '_ParallaxNoiseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitEffectBlend_1', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'LitHLod': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitHLod_0', 'depth': 1, 'cascade': 2,
         'states': [('Lloop0', False), ('height', False), ('heightPrev', False), ('iter', False), ('offCur', True), ('offPrev', True), ('texHit', False), ('texPrev', False), ('uvP', True)],
         'reads': [('__done', False), ('steps', False), ('stepH', False), ('stepUV', True)],
         'uniforms': [('_ParallaxNoiseMapTilling', 0, 0.0, 0.0)],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxNoiseMap', 'slot': '_ParallaxNoiseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
        {'sock': 'Z1', 'body': 'RCE_Z_Ruri_Endfield_Scene_LitHLod_1', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Unlit': [
    ],
    'ContainerWater': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_ContainerWater_0', 'depth': 1, 'cascade': 2,
         'states': [('Lloop0', False), ('height', False), ('heightPrev', False), ('iteration', False), ('offset', True), ('offsetPrev', True), ('texHit', False), ('texPrev', False)],
         'reads': [('__done', False), ('uvNoise', True), ('steps', False), ('stepSize', False), ('stepOffset', True)],
         'uniforms': [],
         'capabilities': [
         ],
         'fetches': [
             {'sock': 'F0_ParallaxNoiseMap', 'slot': '_ParallaxNoiseMap', 'depth': 0, 'non_color': False, 'extension': 'REPEAT', 'point': False, 'env': False, 'mip': False, 'derivative_mip': True, 'neutral': (1.0, 1.0, 1.0), 'neutral_alpha': 1.0},
         ]},
    ],
    'Leaf': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_Leaf_0', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Grass': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_Grass_0', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
    'Trunk': [
        {'sock': 'Z0', 'body': 'RCE_Z_Ruri_Endfield_Scene_Trunk_0', 'depth': 2, 'cascade': 2,
         'states': [('H', True), ('L', True), ('LV', True), ('N', True), ('NoH', False), ('NoL', False), ('NoV', False), ('P', True), ('V', True), ('VoH', False), ('Lloop0', False), ('color', True), ('energy', True), ('f0', True), ('inputData_bakedGI', True), ('inputData_fogCoord', False), ('inputData_normalWS', True), ('inputData_normalizedScreenSpaceUV', True), ('inputData_positionCS', True), ('inputData_positionCS_w', False), ('inputData_positionWS', True), ('inputData_shadowCoord', True), ('inputData_shadowCoord_w', False), ('inputData_shadowMask', True), ('inputData_shadowMask_w', False), ('inputData_vertexLighting', True), ('inputData_viewDirectionWS', True), ('lightIndex', False), ('roughness', False), ('sssAmount', False)],
         'reads': [('__done', False), ('diffuse', True), ('sssTint', True), ('pixelLightCount', False)],
         'uniforms': [('_EnableSubsurface', 0, 0.0, 0.0), ('_SubsurfaceSelfShadowBias', 0, 0.0, 0.0), ('_SubsurfaceEnableSelfShadowBias', 0, 0.0, 0.0)],
         'capabilities': [
             {'sock': 'C0_AdditionalLight', 'cap': 'AdditionalLight', 'depth': 0, 'query': {'index': False, 'position': True}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'punctual light record (direction toward light, linear radiance)'},
         ],
         'fetches': [
         ]},
    ],
}

CAPABILITIES = {
    'Lit': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'LitForward': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'LitTransparent': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'LitEffect': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'LitEffectBlend': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'LitHLod': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Unlit': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
    ],
    'ContainerWater': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
    ],
    'Leaf': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Grass': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
    'Trunk': [
        {'sock': 'C0_AmbientIrradiance', 'cap': 'AmbientIrradiance', 'depth': 0, 'query': {'normal': True}, 'results': {'': True}, 'result': 'linear irradiance'},
        {'sock': 'C1_SpecularRadiance', 'cap': 'SpecularRadiance', 'depth': 1, 'query': {'direction': True, 'position': True, 'roughness': False}, 'results': {'': True}, 'result': 'linear radiance'},
        {'sock': 'C2_MainLight', 'cap': 'MainLight', 'depth': 0, 'query': {}, 'results': {'direction': True, 'color': True, 'distanceAttenuation': False, 'shadowAttenuation': False, 'layerMask': False}, 'result': 'directional light record (direction toward light, linear radiance)'},
        {'sock': 'C3_AdditionalLightCount', 'cap': 'AdditionalLightCount', 'depth': 0, 'query': {}, 'results': {'': False}, 'result': 'light count'},
    ],
}

PARAMS = {
    'Lit': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_NormalScale', 'F', 7, 2, 0.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 3, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 8, 0, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 1, 0.0, 0.0),
        ('_RoughnessMax', 'F', 8, 2, 1.0, 0.0),
        ('_Metallic', 'F', 8, 3, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 0, 0.0, 0.0),
        ('_OcclusionStrength', 'F', 9, 1, 1.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 2, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 9, 3, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 0, 0.4, 0.0),
        ('_DisableVerticalFlow', 'F', 10, 1, 0.0, 0.0),
        ('_UseMacroNormalMap', 'F', 10, 2, 0.0, 0.0),
        ('_MacroNormalMapScale', 'F', 10, 3, 1.0, 0.0),
        ('_EnableDetailMap', 'F', 11, 0, 0.0, 0.0),
        ('_DetailFalloffStart', 'F', 11, 1, 750.0, 0.0),
        ('_DetailFalloffEnd', 'F', 11, 2, 800.0, 0.0),
        ('_DetailMaskMode', 'F', 11, 3, 0.0, 0.0),
        ('_DetailNormalIntensity', 'F', 12, 0, 1.0, 0.0),
        ('_DetailMode', 'F', 12, 1, 0.0, 0.0),
        ('_DetailBaseColorBrighterScale', 'F', 12, 2, 1.0, 0.0),
        ('_DetailOverlayColor', 'V4', 13, 0, (0.0, 0.0, 0.0), 0.0),
        ('_DetailPBRIntensity', 'F', 12, 3, 1.0, 0.0),
        ('_EnableTriChannelMask', 'F', 14, 0, 0.0, 0.0),
        ('_MaskUVSet', 'F', 14, 1, 0.0, 0.0),
        ('_MaskMap_ST', 'V4', 15, 0, (1.0, 1.0, 0.0), 0.0),
        ('_MaskBOffset', 'F', 14, 2, 0.0, 0.0),
        ('_MaskBScale', 'F', 14, 3, 0.0, 0.0),
        ('_MaskAlbedoB', 'V4', 16, 0, (0.0, 0.0, 1.0), 1.0),
        ('_MaskGOffset', 'F', 17, 0, 0.0, 0.0),
        ('_MaskGScale', 'F', 17, 1, 0.0, 0.0),
        ('_MaskAlbedoG', 'V4', 18, 0, (0.0, 1.0, 0.0), 1.0),
        ('_MaskROffset', 'F', 17, 2, 0.0, 0.0),
        ('_MaskRScale', 'F', 17, 3, 0.0, 0.0),
        ('_MaskAlbedoR', 'V4', 19, 0, (1.0, 0.0, 0.0), 1.0),
        ('_MaskRoghnessB', 'F', 20, 0, 0.25, 0.0),
        ('_MaskRoghnessG', 'F', 20, 1, 0.25, 0.0),
        ('_MaskRoghnessR', 'F', 20, 2, 0.25, 0.0),
        ('_MaskMetallicB', 'F', 20, 3, 0.0, 0.0),
        ('_MaskMetallicG', 'F', 21, 0, 0.0, 0.0),
        ('_MaskMetallicR', 'F', 21, 1, 0.0, 0.0),
        ('_LayerBlend', 'F', 21, 2, 0.0, 0.0),
        ('_LayerBlendUVType', 'F', 21, 3, 0.0, 0.0),
        ('_Layer1Tilling', 'F', 22, 0, 1.0, 0.0),
        ('_LayerBlendType', 'F', 22, 1, 1.0, 0.0),
        ('_LayerBlendMaskUVType', 'F', 22, 2, 0.0, 0.0),
        ('_LayerBlendMaskType', 'F', 22, 3, 0.0, 0.0),
        ('_TopBlendWithBumpMap', 'F', 23, 0, 0.0, 0.0),
        ('_TopBlendThreshold', 'F', 23, 1, 0.5, 0.0),
        ('_TopBlendSmoothness', 'F', 23, 2, 0.5, 0.0),
        ('_LayerBlendHeightTransition', 'F', 23, 3, 1.0, 0.0),
        ('_LayerBlendHeight', 'F', 24, 0, 1.0, 0.0),
        ('_Layer1Saturation', 'F', 24, 1, 0.0, 0.0),
        ('_Layer1TintColor', 'V4', 25, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Layer1ColorBrighterScale', 'F', 24, 2, 1.0, 0.0),
        ('_LayerMetallicType', 'F', 24, 3, 0.0, 0.0),
        ('_Layer1Metallic', 'F', 26, 0, 0.0, 0.0),
        ('_Layer1AOStrength', 'F', 26, 1, 1.0, 0.0),
        ('_Layer1BumpScale', 'F', 26, 2, 1.0, 0.0),
        ('_Layer1BaseNormalIntensity', 'F', 26, 3, 0.0, 0.0),
        ('_EmissionTint', 'V4', 27, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveIntensity', 'F', 28, 0, 0.0, 0.0),
        ('_UseEmissiveMap', 'F', 28, 1, 0.0, 0.0),
        ('_AlbedoAffectEmissive', 'F', 28, 2, 1.0, 0.0),
        ('_EnableEmissiveAnim', 'F', 28, 3, 0.0, 0.0),
        ('_EmissiveAnimSpeed', 'F', 29, 0, 0.0, 0.0),
        ('_EmissiveAnimRandom', 'F', 29, 1, 0.0, 0.0),
        ('_EmissiveAnimInterval', 'F', 29, 2, 1.0, 0.0),
        ('_EmissiveMinBrightness', 'F', 29, 3, 0.0, 0.0),
        ('_EnableEmissiveAnimSweep', 'F', 30, 0, 0.0, 0.0),
        ('_EmissiveMaskChannel', 'F', 30, 1, 0.0, 0.0),
        ('_EmissiveSweepRandom', 'F', 30, 2, 0.0, 0.0),
        ('_EmissiveSweepInterval', 'F', 30, 3, 3.0, 0.0),
        ('_EmissiveSweepSpeed', 'F', 31, 0, 3.0, 0.0),
        ('_EmissiveSweepWidth', 'F', 31, 1, 0.8, 0.0),
        ('_EmissiveSweepFalloff', 'F', 31, 2, 1.0, 0.0),
        ('_EmissiveSweepAlbedoScale', 'F', 31, 3, 0.0, 0.0),
        ('_EmissiveColor', 'V4', 32, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveColorG', 'V4', 33, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 34, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 35, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveSpeed', 'V4', 36, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveUVSet', 'F', 37, 0, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 38, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMapTilling', 'F', 37, 1, 0.0, 0.0),
        ('_EmissiveType', 'F', 37, 2, 0.0, 0.0),
        ('_EnableMatcap', 'F', 37, 3, 0.0, 0.0),
        ('_MatcapMapStrength', 'F', 39, 0, 0.2, 0.0),
        ('_EnableParallaxMap', 'F', 39, 1, 0.0, 0.0),
        ('_ParallaxMappingType', 'F', 39, 2, 0.0, 0.0),
        ('_UseParallaxMask', 'F', 39, 3, 0.0, 0.0),
        ('_ParallaxMaskChannel', 'F', 40, 0, 0.0, 0.0),
        ('_ParallaxMaskByLayerBlend', 'F', 40, 1, 0.0, 0.0),
        ('_ParallaxMapUVType', 'F', 40, 2, 0.0, 0.0),
        ('_GlobalMipBias', 'V3', 41, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMarchNum', 'F', 40, 3, 3.0, 0.0),
        ('_ParallaxStrength', 'F', 42, 0, 0.0, 0.0),
        ('_ParallaxTilling', 'F', 42, 1, 1.0, 0.0),
        ('_ParallaxColor', 'V4', 43, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxColorDark', 'V4', 44, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxFresnelStrength', 'F', 42, 2, 0.0, 0.0),
        ('_VFXParams0', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxBrightOuterRadius', 'F', 42, 3, 0.0, 0.0),
        ('_ParallaxBrightInnerRadius', 'F', 46, 0, 0.0, 0.0),
        ('_ParallaxBrightStrength', 'F', 46, 1, 0.0, 0.0),
        ('_ParallaxCharPos', 'F', 46, 2, 0.0, 0.0),
        ('_VFXParams2', 'V4', 47, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMinBrightness', 'F', 46, 3, 0.0, 0.0),
        ('_ParallaxAnimSpeed', 'F', 48, 0, 0.0, 0.0),
        ('_ParallaxAnimRandom', 'F', 48, 1, 0.0, 0.0),
        ('_UseWorldSpaceParallaxMask', 'F', 48, 2, 0.0, 0.0),
        ('_MaskWorldPosParams', 'V4', 49, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMaskMapColorStrength', 'F', 48, 3, 0.0, 0.0),
        ('_ParallaxSignControl', 'F', 50, 0, 0.0, 0.0),
        ('_ParallaxSignLerpFactor0', 'V4', 51, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor2', 'F', 50, 1, 0.0, 0.0),
        ('_ParallaxLerpSchedule', 'F', 50, 2, 0.0, 0.0),
        ('_ParallaxPatternColorDark', 'V4', 52, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxPatternColor', 'V4', 53, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor1', 'V4', 54, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WorldParallaxAdditionalLightMaskChannel', 'F', 50, 3, 0.0, 0.0),
        ('_WorldParallaxAdditionalColor', 'V4', 55, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxIntensity', 'F', 56, 0, 0.0, 0.0),
        ('_UseThinFilm', 'F', 56, 1, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 56, 2, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 56, 3, 0.5, 0.0),
        ('M_PI', 'F', 57, 0, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 57, 1, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 57, 2, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 57, 3, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 58, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 59, 0, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 59, 1, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 59, 2, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 59, 3, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 60, 0, 1.0, 0.0),
        ('_PlanarReflection', 'F', 60, 1, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 61, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 60, 2, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 60, 3, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 62, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 63, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 64, 0, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 64, 1, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 64, 2, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 64, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 65, 0, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 65, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 65, 2, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 66, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxNoiseMapTilling', 'F', 65, 3, 0.0, 0.0),
        ('__size_ParallaxMaskMap', 'V3', 67, 0, (1.0, 1.0, 1.0), 0.0),
    ],
    'LitForward': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_NormalScale', 'F', 7, 2, 0.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 3, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 8, 0, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 1, 0.0, 0.0),
        ('_RoughnessMax', 'F', 8, 2, 1.0, 0.0),
        ('_Metallic', 'F', 8, 3, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 0, 0.0, 0.0),
        ('_OcclusionStrength', 'F', 9, 1, 1.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 2, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 9, 3, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 0, 0.4, 0.0),
        ('_DisableVerticalFlow', 'F', 10, 1, 0.0, 0.0),
        ('_UseMacroNormalMap', 'F', 10, 2, 0.0, 0.0),
        ('_MacroNormalMapScale', 'F', 10, 3, 1.0, 0.0),
        ('_EnableDetailMap', 'F', 11, 0, 0.0, 0.0),
        ('_DetailFalloffStart', 'F', 11, 1, 750.0, 0.0),
        ('_DetailFalloffEnd', 'F', 11, 2, 800.0, 0.0),
        ('_DetailMaskMode', 'F', 11, 3, 0.0, 0.0),
        ('_DetailNormalIntensity', 'F', 12, 0, 1.0, 0.0),
        ('_DetailMode', 'F', 12, 1, 0.0, 0.0),
        ('_DetailBaseColorBrighterScale', 'F', 12, 2, 1.0, 0.0),
        ('_DetailOverlayColor', 'V4', 13, 0, (0.0, 0.0, 0.0), 0.0),
        ('_DetailPBRIntensity', 'F', 12, 3, 1.0, 0.0),
        ('_EnableTriChannelMask', 'F', 14, 0, 0.0, 0.0),
        ('_MaskUVSet', 'F', 14, 1, 0.0, 0.0),
        ('_MaskMap_ST', 'V4', 15, 0, (1.0, 1.0, 0.0), 0.0),
        ('_MaskBOffset', 'F', 14, 2, 0.0, 0.0),
        ('_MaskBScale', 'F', 14, 3, 0.0, 0.0),
        ('_MaskAlbedoB', 'V4', 16, 0, (0.0, 0.0, 1.0), 1.0),
        ('_MaskGOffset', 'F', 17, 0, 0.0, 0.0),
        ('_MaskGScale', 'F', 17, 1, 0.0, 0.0),
        ('_MaskAlbedoG', 'V4', 18, 0, (0.0, 1.0, 0.0), 1.0),
        ('_MaskROffset', 'F', 17, 2, 0.0, 0.0),
        ('_MaskRScale', 'F', 17, 3, 0.0, 0.0),
        ('_MaskAlbedoR', 'V4', 19, 0, (1.0, 0.0, 0.0), 1.0),
        ('_MaskRoghnessB', 'F', 20, 0, 0.25, 0.0),
        ('_MaskRoghnessG', 'F', 20, 1, 0.25, 0.0),
        ('_MaskRoghnessR', 'F', 20, 2, 0.25, 0.0),
        ('_MaskMetallicB', 'F', 20, 3, 0.0, 0.0),
        ('_MaskMetallicG', 'F', 21, 0, 0.0, 0.0),
        ('_MaskMetallicR', 'F', 21, 1, 0.0, 0.0),
        ('_LayerBlend', 'F', 21, 2, 0.0, 0.0),
        ('_LayerBlendUVType', 'F', 21, 3, 0.0, 0.0),
        ('_Layer1Tilling', 'F', 22, 0, 1.0, 0.0),
        ('_LayerBlendType', 'F', 22, 1, 1.0, 0.0),
        ('_LayerBlendMaskUVType', 'F', 22, 2, 0.0, 0.0),
        ('_LayerBlendMaskType', 'F', 22, 3, 0.0, 0.0),
        ('_TopBlendWithBumpMap', 'F', 23, 0, 0.0, 0.0),
        ('_TopBlendThreshold', 'F', 23, 1, 0.5, 0.0),
        ('_TopBlendSmoothness', 'F', 23, 2, 0.5, 0.0),
        ('_LayerBlendHeightTransition', 'F', 23, 3, 1.0, 0.0),
        ('_LayerBlendHeight', 'F', 24, 0, 1.0, 0.0),
        ('_Layer1Saturation', 'F', 24, 1, 0.0, 0.0),
        ('_Layer1TintColor', 'V4', 25, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Layer1ColorBrighterScale', 'F', 24, 2, 1.0, 0.0),
        ('_LayerMetallicType', 'F', 24, 3, 0.0, 0.0),
        ('_Layer1Metallic', 'F', 26, 0, 0.0, 0.0),
        ('_Layer1AOStrength', 'F', 26, 1, 1.0, 0.0),
        ('_Layer1BumpScale', 'F', 26, 2, 1.0, 0.0),
        ('_Layer1BaseNormalIntensity', 'F', 26, 3, 0.0, 0.0),
        ('_EmissionTint', 'V4', 27, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveIntensity', 'F', 28, 0, 0.0, 0.0),
        ('_UseEmissiveMap', 'F', 28, 1, 0.0, 0.0),
        ('_AlbedoAffectEmissive', 'F', 28, 2, 1.0, 0.0),
        ('_EnableEmissiveAnim', 'F', 28, 3, 0.0, 0.0),
        ('_EmissiveAnimSpeed', 'F', 29, 0, 0.0, 0.0),
        ('_EmissiveAnimRandom', 'F', 29, 1, 0.0, 0.0),
        ('_EmissiveAnimInterval', 'F', 29, 2, 1.0, 0.0),
        ('_EmissiveMinBrightness', 'F', 29, 3, 0.0, 0.0),
        ('_EnableEmissiveAnimSweep', 'F', 30, 0, 0.0, 0.0),
        ('_EmissiveMaskChannel', 'F', 30, 1, 0.0, 0.0),
        ('_EmissiveSweepRandom', 'F', 30, 2, 0.0, 0.0),
        ('_EmissiveSweepInterval', 'F', 30, 3, 3.0, 0.0),
        ('_EmissiveSweepSpeed', 'F', 31, 0, 3.0, 0.0),
        ('_EmissiveSweepWidth', 'F', 31, 1, 0.8, 0.0),
        ('_EmissiveSweepFalloff', 'F', 31, 2, 1.0, 0.0),
        ('_EmissiveSweepAlbedoScale', 'F', 31, 3, 0.0, 0.0),
        ('_EmissiveColor', 'V4', 32, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveColorG', 'V4', 33, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 34, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 35, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveSpeed', 'V4', 36, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveUVSet', 'F', 37, 0, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 38, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMapTilling', 'F', 37, 1, 0.0, 0.0),
        ('_EmissiveType', 'F', 37, 2, 0.0, 0.0),
        ('_EnableMatcap', 'F', 37, 3, 0.0, 0.0),
        ('_MatcapMapStrength', 'F', 39, 0, 0.2, 0.0),
        ('_EnableParallaxMap', 'F', 39, 1, 0.0, 0.0),
        ('_ParallaxMappingType', 'F', 39, 2, 0.0, 0.0),
        ('_UseParallaxMask', 'F', 39, 3, 0.0, 0.0),
        ('_ParallaxMaskChannel', 'F', 40, 0, 0.0, 0.0),
        ('_ParallaxMaskByLayerBlend', 'F', 40, 1, 0.0, 0.0),
        ('_ParallaxMapUVType', 'F', 40, 2, 0.0, 0.0),
        ('_GlobalMipBias', 'V3', 41, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMarchNum', 'F', 40, 3, 3.0, 0.0),
        ('_ParallaxStrength', 'F', 42, 0, 0.0, 0.0),
        ('_ParallaxTilling', 'F', 42, 1, 1.0, 0.0),
        ('_ParallaxColor', 'V4', 43, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxColorDark', 'V4', 44, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxFresnelStrength', 'F', 42, 2, 0.0, 0.0),
        ('_VFXParams0', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxBrightOuterRadius', 'F', 42, 3, 0.0, 0.0),
        ('_ParallaxBrightInnerRadius', 'F', 46, 0, 0.0, 0.0),
        ('_ParallaxBrightStrength', 'F', 46, 1, 0.0, 0.0),
        ('_ParallaxCharPos', 'F', 46, 2, 0.0, 0.0),
        ('_VFXParams2', 'V4', 47, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMinBrightness', 'F', 46, 3, 0.0, 0.0),
        ('_ParallaxAnimSpeed', 'F', 48, 0, 0.0, 0.0),
        ('_ParallaxAnimRandom', 'F', 48, 1, 0.0, 0.0),
        ('_UseWorldSpaceParallaxMask', 'F', 48, 2, 0.0, 0.0),
        ('_MaskWorldPosParams', 'V4', 49, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMaskMapColorStrength', 'F', 48, 3, 0.0, 0.0),
        ('_ParallaxSignControl', 'F', 50, 0, 0.0, 0.0),
        ('_ParallaxSignLerpFactor0', 'V4', 51, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor2', 'F', 50, 1, 0.0, 0.0),
        ('_ParallaxLerpSchedule', 'F', 50, 2, 0.0, 0.0),
        ('_ParallaxPatternColorDark', 'V4', 52, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxPatternColor', 'V4', 53, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor1', 'V4', 54, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WorldParallaxAdditionalLightMaskChannel', 'F', 50, 3, 0.0, 0.0),
        ('_WorldParallaxAdditionalColor', 'V4', 55, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxIntensity', 'F', 56, 0, 0.0, 0.0),
        ('_UseThinFilm', 'F', 56, 1, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 56, 2, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 56, 3, 0.5, 0.0),
        ('M_PI', 'F', 57, 0, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 57, 1, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 57, 2, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 57, 3, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 58, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 59, 0, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 59, 1, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 59, 2, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 59, 3, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 60, 0, 1.0, 0.0),
        ('_PlanarReflection', 'F', 60, 1, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 61, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 60, 2, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 60, 3, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 62, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 63, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 64, 0, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 64, 1, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 64, 2, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 64, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 65, 0, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 65, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 65, 2, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 66, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxNoiseMapTilling', 'F', 65, 3, 0.0, 0.0),
        ('__size_ParallaxMaskMap', 'V3', 67, 0, (1.0, 1.0, 1.0), 0.0),
    ],
    'LitTransparent': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_NormalScale', 'F', 7, 2, 0.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 3, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 8, 0, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 1, 0.0, 0.0),
        ('_RoughnessMax', 'F', 8, 2, 1.0, 0.0),
        ('_Metallic', 'F', 8, 3, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 0, 0.0, 0.0),
        ('_OcclusionStrength', 'F', 9, 1, 1.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 2, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 9, 3, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 0, 0.4, 0.0),
        ('_DisableVerticalFlow', 'F', 10, 1, 0.0, 0.0),
        ('_EffectIntensity', 'F', 10, 2, 1.0, 0.0),
        ('_UseMacroNormalMap', 'F', 10, 3, 0.0, 0.0),
        ('_MacroNormalMapScale', 'F', 11, 0, 1.0, 0.0),
        ('_EnableDetailMap', 'F', 11, 1, 0.0, 0.0),
        ('_DetailFalloffStart', 'F', 11, 2, 750.0, 0.0),
        ('_DetailFalloffEnd', 'F', 11, 3, 800.0, 0.0),
        ('_DetailMaskMode', 'F', 12, 0, 0.0, 0.0),
        ('_DetailNormalIntensity', 'F', 12, 1, 1.0, 0.0),
        ('_DetailMode', 'F', 12, 2, 0.0, 0.0),
        ('_DetailBaseColorBrighterScale', 'F', 12, 3, 1.0, 0.0),
        ('_DetailOverlayColor', 'V4', 13, 0, (0.0, 0.0, 0.0), 0.0),
        ('_DetailPBRIntensity', 'F', 14, 0, 1.0, 0.0),
        ('_EnableTriChannelMask', 'F', 14, 1, 0.0, 0.0),
        ('_MaskUVSet', 'F', 14, 2, 0.0, 0.0),
        ('_MaskMap_ST', 'V4', 15, 0, (1.0, 1.0, 0.0), 0.0),
        ('_MaskBOffset', 'F', 14, 3, 0.0, 0.0),
        ('_MaskBScale', 'F', 16, 0, 0.0, 0.0),
        ('_MaskAlbedoB', 'V4', 17, 0, (0.0, 0.0, 1.0), 1.0),
        ('_MaskGOffset', 'F', 16, 1, 0.0, 0.0),
        ('_MaskGScale', 'F', 16, 2, 0.0, 0.0),
        ('_MaskAlbedoG', 'V4', 18, 0, (0.0, 1.0, 0.0), 1.0),
        ('_MaskROffset', 'F', 16, 3, 0.0, 0.0),
        ('_MaskRScale', 'F', 19, 0, 0.0, 0.0),
        ('_MaskAlbedoR', 'V4', 20, 0, (1.0, 0.0, 0.0), 1.0),
        ('_MaskRoghnessB', 'F', 19, 1, 0.25, 0.0),
        ('_MaskRoghnessG', 'F', 19, 2, 0.25, 0.0),
        ('_MaskRoghnessR', 'F', 19, 3, 0.25, 0.0),
        ('_MaskMetallicB', 'F', 21, 0, 0.0, 0.0),
        ('_MaskMetallicG', 'F', 21, 1, 0.0, 0.0),
        ('_MaskMetallicR', 'F', 21, 2, 0.0, 0.0),
        ('_LayerBlend', 'F', 21, 3, 0.0, 0.0),
        ('_LayerBlendUVType', 'F', 22, 0, 0.0, 0.0),
        ('_Layer1Tilling', 'F', 22, 1, 1.0, 0.0),
        ('_LayerBlendType', 'F', 22, 2, 1.0, 0.0),
        ('_LayerBlendMaskUVType', 'F', 22, 3, 0.0, 0.0),
        ('_LayerBlendMaskType', 'F', 23, 0, 0.0, 0.0),
        ('_TopBlendWithBumpMap', 'F', 23, 1, 0.0, 0.0),
        ('_TopBlendThreshold', 'F', 23, 2, 0.5, 0.0),
        ('_TopBlendSmoothness', 'F', 23, 3, 0.5, 0.0),
        ('_LayerBlendHeightTransition', 'F', 24, 0, 1.0, 0.0),
        ('_LayerBlendHeight', 'F', 24, 1, 1.0, 0.0),
        ('_Layer1Saturation', 'F', 24, 2, 0.0, 0.0),
        ('_Layer1TintColor', 'V4', 25, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Layer1ColorBrighterScale', 'F', 24, 3, 1.0, 0.0),
        ('_LayerMetallicType', 'F', 26, 0, 0.0, 0.0),
        ('_Layer1Metallic', 'F', 26, 1, 0.0, 0.0),
        ('_Layer1AOStrength', 'F', 26, 2, 1.0, 0.0),
        ('_Layer1BumpScale', 'F', 26, 3, 1.0, 0.0),
        ('_Layer1BaseNormalIntensity', 'F', 27, 0, 0.0, 0.0),
        ('_EmissionTint', 'V4', 28, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveIntensity', 'F', 27, 1, 0.0, 0.0),
        ('_UseEmissiveMap', 'F', 27, 2, 0.0, 0.0),
        ('_AlbedoAffectEmissive', 'F', 27, 3, 1.0, 0.0),
        ('_EnableEmissiveAnim', 'F', 29, 0, 0.0, 0.0),
        ('_EmissiveAnimSpeed', 'F', 29, 1, 0.0, 0.0),
        ('_EmissiveAnimRandom', 'F', 29, 2, 0.0, 0.0),
        ('_EmissiveAnimInterval', 'F', 29, 3, 1.0, 0.0),
        ('_EmissiveMinBrightness', 'F', 30, 0, 0.0, 0.0),
        ('_EnableEmissiveAnimSweep', 'F', 30, 1, 0.0, 0.0),
        ('_EmissiveMaskChannel', 'F', 30, 2, 0.0, 0.0),
        ('_EmissiveSweepRandom', 'F', 30, 3, 0.0, 0.0),
        ('_EmissiveSweepInterval', 'F', 31, 0, 3.0, 0.0),
        ('_EmissiveSweepSpeed', 'F', 31, 1, 3.0, 0.0),
        ('_EmissiveSweepWidth', 'F', 31, 2, 0.8, 0.0),
        ('_EmissiveSweepFalloff', 'F', 31, 3, 1.0, 0.0),
        ('_EmissiveSweepAlbedoScale', 'F', 32, 0, 0.0, 0.0),
        ('_EmissiveColor', 'V4', 33, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveColorG', 'V4', 34, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 35, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 36, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveSpeed', 'V4', 37, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveUVSet', 'F', 32, 1, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 38, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMapTilling', 'F', 32, 2, 0.0, 0.0),
        ('_EmissiveType', 'F', 32, 3, 0.0, 0.0),
        ('_EnableMatcap', 'F', 39, 0, 0.0, 0.0),
        ('_MatcapMapStrength', 'F', 39, 1, 0.2, 0.0),
        ('_EnableParallaxMap', 'F', 39, 2, 0.0, 0.0),
        ('_ParallaxMappingType', 'F', 39, 3, 0.0, 0.0),
        ('_UseParallaxMask', 'F', 40, 0, 0.0, 0.0),
        ('_ParallaxMaskChannel', 'F', 40, 1, 0.0, 0.0),
        ('_ParallaxMaskByLayerBlend', 'F', 40, 2, 0.0, 0.0),
        ('_ParallaxMapUVType', 'F', 40, 3, 0.0, 0.0),
        ('_GlobalMipBias', 'V3', 41, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMarchNum', 'F', 42, 0, 3.0, 0.0),
        ('_ParallaxStrength', 'F', 42, 1, 0.0, 0.0),
        ('_ParallaxTilling', 'F', 42, 2, 1.0, 0.0),
        ('_ParallaxColor', 'V4', 43, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxColorDark', 'V4', 44, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxFresnelStrength', 'F', 42, 3, 0.0, 0.0),
        ('_VFXParams0', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxBrightOuterRadius', 'F', 46, 0, 0.0, 0.0),
        ('_ParallaxBrightInnerRadius', 'F', 46, 1, 0.0, 0.0),
        ('_ParallaxBrightStrength', 'F', 46, 2, 0.0, 0.0),
        ('_ParallaxCharPos', 'F', 46, 3, 0.0, 0.0),
        ('_VFXParams2', 'V4', 47, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMinBrightness', 'F', 48, 0, 0.0, 0.0),
        ('_ParallaxAnimSpeed', 'F', 48, 1, 0.0, 0.0),
        ('_ParallaxAnimRandom', 'F', 48, 2, 0.0, 0.0),
        ('_UseWorldSpaceParallaxMask', 'F', 48, 3, 0.0, 0.0),
        ('_MaskWorldPosParams', 'V4', 49, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMaskMapColorStrength', 'F', 50, 0, 0.0, 0.0),
        ('_ParallaxSignControl', 'F', 50, 1, 0.0, 0.0),
        ('_ParallaxSignLerpFactor0', 'V4', 51, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor2', 'F', 50, 2, 0.0, 0.0),
        ('_ParallaxLerpSchedule', 'F', 50, 3, 0.0, 0.0),
        ('_ParallaxPatternColorDark', 'V4', 52, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxPatternColor', 'V4', 53, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor1', 'V4', 54, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WorldParallaxAdditionalLightMaskChannel', 'F', 55, 0, 0.0, 0.0),
        ('_WorldParallaxAdditionalColor', 'V4', 56, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxIntensity', 'F', 55, 1, 0.0, 0.0),
        ('_UseThinFilm', 'F', 55, 2, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 55, 3, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 57, 0, 0.5, 0.0),
        ('M_PI', 'F', 57, 1, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 57, 2, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 57, 3, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 58, 0, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 59, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 58, 1, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 58, 2, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 58, 3, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 60, 0, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 60, 1, 1.0, 0.0),
        ('_PlanarReflection', 'F', 60, 2, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 61, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 60, 3, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 62, 0, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 63, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 64, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 62, 1, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 62, 2, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 62, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 65, 0, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 65, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 65, 2, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 65, 3, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 66, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxNoiseMapTilling', 'F', 67, 0, 0.0, 0.0),
        ('__size_ParallaxMaskMap', 'V3', 68, 0, (1.0, 1.0, 1.0), 0.0),
    ],
    'LitEffect': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_NormalScale', 'F', 7, 2, 0.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 3, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 8, 0, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 1, 0.0, 0.0),
        ('_RoughnessMax', 'F', 8, 2, 1.0, 0.0),
        ('_Metallic', 'F', 8, 3, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 0, 0.0, 0.0),
        ('_OcclusionStrength', 'F', 9, 1, 1.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 2, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 9, 3, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 0, 0.4, 0.0),
        ('_DisableVerticalFlow', 'F', 10, 1, 0.0, 0.0),
        ('_EffectIntensity', 'F', 10, 2, 1.0, 0.0),
        ('_UseMacroNormalMap', 'F', 10, 3, 0.0, 0.0),
        ('_MacroNormalMapScale', 'F', 11, 0, 1.0, 0.0),
        ('_EnableDetailMap', 'F', 11, 1, 0.0, 0.0),
        ('_DetailFalloffStart', 'F', 11, 2, 750.0, 0.0),
        ('_DetailFalloffEnd', 'F', 11, 3, 800.0, 0.0),
        ('_DetailMaskMode', 'F', 12, 0, 0.0, 0.0),
        ('_DetailNormalIntensity', 'F', 12, 1, 1.0, 0.0),
        ('_DetailMode', 'F', 12, 2, 0.0, 0.0),
        ('_DetailBaseColorBrighterScale', 'F', 12, 3, 1.0, 0.0),
        ('_DetailOverlayColor', 'V4', 13, 0, (0.0, 0.0, 0.0), 0.0),
        ('_DetailPBRIntensity', 'F', 14, 0, 1.0, 0.0),
        ('_EnableTriChannelMask', 'F', 14, 1, 0.0, 0.0),
        ('_MaskUVSet', 'F', 14, 2, 0.0, 0.0),
        ('_MaskMap_ST', 'V4', 15, 0, (1.0, 1.0, 0.0), 0.0),
        ('_MaskBOffset', 'F', 14, 3, 0.0, 0.0),
        ('_MaskBScale', 'F', 16, 0, 0.0, 0.0),
        ('_MaskAlbedoB', 'V4', 17, 0, (0.0, 0.0, 1.0), 1.0),
        ('_MaskGOffset', 'F', 16, 1, 0.0, 0.0),
        ('_MaskGScale', 'F', 16, 2, 0.0, 0.0),
        ('_MaskAlbedoG', 'V4', 18, 0, (0.0, 1.0, 0.0), 1.0),
        ('_MaskROffset', 'F', 16, 3, 0.0, 0.0),
        ('_MaskRScale', 'F', 19, 0, 0.0, 0.0),
        ('_MaskAlbedoR', 'V4', 20, 0, (1.0, 0.0, 0.0), 1.0),
        ('_MaskRoghnessB', 'F', 19, 1, 0.25, 0.0),
        ('_MaskRoghnessG', 'F', 19, 2, 0.25, 0.0),
        ('_MaskRoghnessR', 'F', 19, 3, 0.25, 0.0),
        ('_MaskMetallicB', 'F', 21, 0, 0.0, 0.0),
        ('_MaskMetallicG', 'F', 21, 1, 0.0, 0.0),
        ('_MaskMetallicR', 'F', 21, 2, 0.0, 0.0),
        ('_LayerBlend', 'F', 21, 3, 0.0, 0.0),
        ('_LayerBlendUVType', 'F', 22, 0, 0.0, 0.0),
        ('_Layer1Tilling', 'F', 22, 1, 1.0, 0.0),
        ('_LayerBlendType', 'F', 22, 2, 1.0, 0.0),
        ('_LayerBlendMaskUVType', 'F', 22, 3, 0.0, 0.0),
        ('_LayerBlendMaskType', 'F', 23, 0, 0.0, 0.0),
        ('_TopBlendWithBumpMap', 'F', 23, 1, 0.0, 0.0),
        ('_TopBlendThreshold', 'F', 23, 2, 0.5, 0.0),
        ('_TopBlendSmoothness', 'F', 23, 3, 0.5, 0.0),
        ('_LayerBlendHeightTransition', 'F', 24, 0, 1.0, 0.0),
        ('_LayerBlendHeight', 'F', 24, 1, 1.0, 0.0),
        ('_Layer1Saturation', 'F', 24, 2, 0.0, 0.0),
        ('_Layer1TintColor', 'V4', 25, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Layer1ColorBrighterScale', 'F', 24, 3, 1.0, 0.0),
        ('_LayerMetallicType', 'F', 26, 0, 0.0, 0.0),
        ('_Layer1Metallic', 'F', 26, 1, 0.0, 0.0),
        ('_Layer1AOStrength', 'F', 26, 2, 1.0, 0.0),
        ('_Layer1BumpScale', 'F', 26, 3, 1.0, 0.0),
        ('_Layer1BaseNormalIntensity', 'F', 27, 0, 0.0, 0.0),
        ('_EmissionTint', 'V4', 28, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveIntensity', 'F', 27, 1, 0.0, 0.0),
        ('_UseEmissiveMap', 'F', 27, 2, 0.0, 0.0),
        ('_AlbedoAffectEmissive', 'F', 27, 3, 1.0, 0.0),
        ('_EnableEmissiveAnim', 'F', 29, 0, 0.0, 0.0),
        ('_EmissiveAnimSpeed', 'F', 29, 1, 0.0, 0.0),
        ('_EmissiveAnimRandom', 'F', 29, 2, 0.0, 0.0),
        ('_EmissiveAnimInterval', 'F', 29, 3, 1.0, 0.0),
        ('_EmissiveMinBrightness', 'F', 30, 0, 0.0, 0.0),
        ('_EnableEmissiveAnimSweep', 'F', 30, 1, 0.0, 0.0),
        ('_EmissiveMaskChannel', 'F', 30, 2, 0.0, 0.0),
        ('_EmissiveSweepRandom', 'F', 30, 3, 0.0, 0.0),
        ('_EmissiveSweepInterval', 'F', 31, 0, 3.0, 0.0),
        ('_EmissiveSweepSpeed', 'F', 31, 1, 3.0, 0.0),
        ('_EmissiveSweepWidth', 'F', 31, 2, 0.8, 0.0),
        ('_EmissiveSweepFalloff', 'F', 31, 3, 1.0, 0.0),
        ('_EmissiveSweepAlbedoScale', 'F', 32, 0, 0.0, 0.0),
        ('_EmissiveColor', 'V4', 33, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveColorG', 'V4', 34, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 35, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 36, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveSpeed', 'V4', 37, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveUVSet', 'F', 32, 1, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 38, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMapTilling', 'F', 32, 2, 0.0, 0.0),
        ('_EmissiveType', 'F', 32, 3, 0.0, 0.0),
        ('_EnableMatcap', 'F', 39, 0, 0.0, 0.0),
        ('_MatcapMapStrength', 'F', 39, 1, 0.2, 0.0),
        ('_EnableParallaxMap', 'F', 39, 2, 0.0, 0.0),
        ('_ParallaxMappingType', 'F', 39, 3, 0.0, 0.0),
        ('_UseParallaxMask', 'F', 40, 0, 0.0, 0.0),
        ('_ParallaxMaskChannel', 'F', 40, 1, 0.0, 0.0),
        ('_ParallaxMaskByLayerBlend', 'F', 40, 2, 0.0, 0.0),
        ('_ParallaxMapUVType', 'F', 40, 3, 0.0, 0.0),
        ('_GlobalMipBias', 'V3', 41, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMarchNum', 'F', 42, 0, 3.0, 0.0),
        ('_ParallaxStrength', 'F', 42, 1, 0.0, 0.0),
        ('_ParallaxTilling', 'F', 42, 2, 1.0, 0.0),
        ('_ParallaxColor', 'V4', 43, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxColorDark', 'V4', 44, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxFresnelStrength', 'F', 42, 3, 0.0, 0.0),
        ('_VFXParams0', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxBrightOuterRadius', 'F', 46, 0, 0.0, 0.0),
        ('_ParallaxBrightInnerRadius', 'F', 46, 1, 0.0, 0.0),
        ('_ParallaxBrightStrength', 'F', 46, 2, 0.0, 0.0),
        ('_ParallaxCharPos', 'F', 46, 3, 0.0, 0.0),
        ('_VFXParams2', 'V4', 47, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMinBrightness', 'F', 48, 0, 0.0, 0.0),
        ('_ParallaxAnimSpeed', 'F', 48, 1, 0.0, 0.0),
        ('_ParallaxAnimRandom', 'F', 48, 2, 0.0, 0.0),
        ('_UseWorldSpaceParallaxMask', 'F', 48, 3, 0.0, 0.0),
        ('_MaskWorldPosParams', 'V4', 49, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMaskMapColorStrength', 'F', 50, 0, 0.0, 0.0),
        ('_ParallaxSignControl', 'F', 50, 1, 0.0, 0.0),
        ('_ParallaxSignLerpFactor0', 'V4', 51, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor2', 'F', 50, 2, 0.0, 0.0),
        ('_ParallaxLerpSchedule', 'F', 50, 3, 0.0, 0.0),
        ('_ParallaxPatternColorDark', 'V4', 52, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxPatternColor', 'V4', 53, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor1', 'V4', 54, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WorldParallaxAdditionalLightMaskChannel', 'F', 55, 0, 0.0, 0.0),
        ('_WorldParallaxAdditionalColor', 'V4', 56, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxIntensity', 'F', 55, 1, 0.0, 0.0),
        ('_UseThinFilm', 'F', 55, 2, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 55, 3, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 57, 0, 0.5, 0.0),
        ('M_PI', 'F', 57, 1, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 57, 2, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 57, 3, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 58, 0, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 59, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 58, 1, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 58, 2, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 58, 3, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 60, 0, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 60, 1, 1.0, 0.0),
        ('_PlanarReflection', 'F', 60, 2, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 61, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 60, 3, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 62, 0, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 63, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 64, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 62, 1, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 62, 2, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 62, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 65, 0, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 65, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 65, 2, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 65, 3, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 66, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxNoiseMapTilling', 'F', 67, 0, 0.0, 0.0),
        ('__size_ParallaxMaskMap', 'V3', 68, 0, (1.0, 1.0, 1.0), 0.0),
    ],
    'LitEffectBlend': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_NormalScale', 'F', 7, 2, 0.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 3, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 8, 0, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 1, 0.0, 0.0),
        ('_RoughnessMax', 'F', 8, 2, 1.0, 0.0),
        ('_Metallic', 'F', 8, 3, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 0, 0.0, 0.0),
        ('_OcclusionStrength', 'F', 9, 1, 1.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 2, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 9, 3, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 0, 0.4, 0.0),
        ('_DisableVerticalFlow', 'F', 10, 1, 0.0, 0.0),
        ('_EffectIntensity', 'F', 10, 2, 1.0, 0.0),
        ('_UseMacroNormalMap', 'F', 10, 3, 0.0, 0.0),
        ('_MacroNormalMapScale', 'F', 11, 0, 1.0, 0.0),
        ('_EnableDetailMap', 'F', 11, 1, 0.0, 0.0),
        ('_DetailFalloffStart', 'F', 11, 2, 750.0, 0.0),
        ('_DetailFalloffEnd', 'F', 11, 3, 800.0, 0.0),
        ('_DetailMaskMode', 'F', 12, 0, 0.0, 0.0),
        ('_DetailNormalIntensity', 'F', 12, 1, 1.0, 0.0),
        ('_DetailMode', 'F', 12, 2, 0.0, 0.0),
        ('_DetailBaseColorBrighterScale', 'F', 12, 3, 1.0, 0.0),
        ('_DetailOverlayColor', 'V4', 13, 0, (0.0, 0.0, 0.0), 0.0),
        ('_DetailPBRIntensity', 'F', 14, 0, 1.0, 0.0),
        ('_EnableTriChannelMask', 'F', 14, 1, 0.0, 0.0),
        ('_MaskUVSet', 'F', 14, 2, 0.0, 0.0),
        ('_MaskMap_ST', 'V4', 15, 0, (1.0, 1.0, 0.0), 0.0),
        ('_MaskBOffset', 'F', 14, 3, 0.0, 0.0),
        ('_MaskBScale', 'F', 16, 0, 0.0, 0.0),
        ('_MaskAlbedoB', 'V4', 17, 0, (0.0, 0.0, 1.0), 1.0),
        ('_MaskGOffset', 'F', 16, 1, 0.0, 0.0),
        ('_MaskGScale', 'F', 16, 2, 0.0, 0.0),
        ('_MaskAlbedoG', 'V4', 18, 0, (0.0, 1.0, 0.0), 1.0),
        ('_MaskROffset', 'F', 16, 3, 0.0, 0.0),
        ('_MaskRScale', 'F', 19, 0, 0.0, 0.0),
        ('_MaskAlbedoR', 'V4', 20, 0, (1.0, 0.0, 0.0), 1.0),
        ('_MaskRoghnessB', 'F', 19, 1, 0.25, 0.0),
        ('_MaskRoghnessG', 'F', 19, 2, 0.25, 0.0),
        ('_MaskRoghnessR', 'F', 19, 3, 0.25, 0.0),
        ('_MaskMetallicB', 'F', 21, 0, 0.0, 0.0),
        ('_MaskMetallicG', 'F', 21, 1, 0.0, 0.0),
        ('_MaskMetallicR', 'F', 21, 2, 0.0, 0.0),
        ('_LayerBlend', 'F', 21, 3, 0.0, 0.0),
        ('_LayerBlendUVType', 'F', 22, 0, 0.0, 0.0),
        ('_Layer1Tilling', 'F', 22, 1, 1.0, 0.0),
        ('_LayerBlendType', 'F', 22, 2, 1.0, 0.0),
        ('_LayerBlendMaskUVType', 'F', 22, 3, 0.0, 0.0),
        ('_LayerBlendMaskType', 'F', 23, 0, 0.0, 0.0),
        ('_TopBlendWithBumpMap', 'F', 23, 1, 0.0, 0.0),
        ('_TopBlendThreshold', 'F', 23, 2, 0.5, 0.0),
        ('_TopBlendSmoothness', 'F', 23, 3, 0.5, 0.0),
        ('_LayerBlendHeightTransition', 'F', 24, 0, 1.0, 0.0),
        ('_LayerBlendHeight', 'F', 24, 1, 1.0, 0.0),
        ('_Layer1Saturation', 'F', 24, 2, 0.0, 0.0),
        ('_Layer1TintColor', 'V4', 25, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Layer1ColorBrighterScale', 'F', 24, 3, 1.0, 0.0),
        ('_LayerMetallicType', 'F', 26, 0, 0.0, 0.0),
        ('_Layer1Metallic', 'F', 26, 1, 0.0, 0.0),
        ('_Layer1AOStrength', 'F', 26, 2, 1.0, 0.0),
        ('_Layer1BumpScale', 'F', 26, 3, 1.0, 0.0),
        ('_Layer1BaseNormalIntensity', 'F', 27, 0, 0.0, 0.0),
        ('_EmissionTint', 'V4', 28, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveIntensity', 'F', 27, 1, 0.0, 0.0),
        ('_UseEmissiveMap', 'F', 27, 2, 0.0, 0.0),
        ('_AlbedoAffectEmissive', 'F', 27, 3, 1.0, 0.0),
        ('_EnableEmissiveAnim', 'F', 29, 0, 0.0, 0.0),
        ('_EmissiveAnimSpeed', 'F', 29, 1, 0.0, 0.0),
        ('_EmissiveAnimRandom', 'F', 29, 2, 0.0, 0.0),
        ('_EmissiveAnimInterval', 'F', 29, 3, 1.0, 0.0),
        ('_EmissiveMinBrightness', 'F', 30, 0, 0.0, 0.0),
        ('_EnableEmissiveAnimSweep', 'F', 30, 1, 0.0, 0.0),
        ('_EmissiveMaskChannel', 'F', 30, 2, 0.0, 0.0),
        ('_EmissiveSweepRandom', 'F', 30, 3, 0.0, 0.0),
        ('_EmissiveSweepInterval', 'F', 31, 0, 3.0, 0.0),
        ('_EmissiveSweepSpeed', 'F', 31, 1, 3.0, 0.0),
        ('_EmissiveSweepWidth', 'F', 31, 2, 0.8, 0.0),
        ('_EmissiveSweepFalloff', 'F', 31, 3, 1.0, 0.0),
        ('_EmissiveSweepAlbedoScale', 'F', 32, 0, 0.0, 0.0),
        ('_EmissiveColor', 'V4', 33, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveColorG', 'V4', 34, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 35, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 36, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveSpeed', 'V4', 37, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveUVSet', 'F', 32, 1, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 38, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMapTilling', 'F', 32, 2, 0.0, 0.0),
        ('_EmissiveType', 'F', 32, 3, 0.0, 0.0),
        ('_EnableMatcap', 'F', 39, 0, 0.0, 0.0),
        ('_MatcapMapStrength', 'F', 39, 1, 0.2, 0.0),
        ('_EnableParallaxMap', 'F', 39, 2, 0.0, 0.0),
        ('_ParallaxMappingType', 'F', 39, 3, 0.0, 0.0),
        ('_UseParallaxMask', 'F', 40, 0, 0.0, 0.0),
        ('_ParallaxMaskChannel', 'F', 40, 1, 0.0, 0.0),
        ('_ParallaxMaskByLayerBlend', 'F', 40, 2, 0.0, 0.0),
        ('_ParallaxMapUVType', 'F', 40, 3, 0.0, 0.0),
        ('_GlobalMipBias', 'V3', 41, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMarchNum', 'F', 42, 0, 3.0, 0.0),
        ('_ParallaxStrength', 'F', 42, 1, 0.0, 0.0),
        ('_ParallaxTilling', 'F', 42, 2, 1.0, 0.0),
        ('_ParallaxColor', 'V4', 43, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxColorDark', 'V4', 44, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxFresnelStrength', 'F', 42, 3, 0.0, 0.0),
        ('_VFXParams0', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxBrightOuterRadius', 'F', 46, 0, 0.0, 0.0),
        ('_ParallaxBrightInnerRadius', 'F', 46, 1, 0.0, 0.0),
        ('_ParallaxBrightStrength', 'F', 46, 2, 0.0, 0.0),
        ('_ParallaxCharPos', 'F', 46, 3, 0.0, 0.0),
        ('_VFXParams2', 'V4', 47, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMinBrightness', 'F', 48, 0, 0.0, 0.0),
        ('_ParallaxAnimSpeed', 'F', 48, 1, 0.0, 0.0),
        ('_ParallaxAnimRandom', 'F', 48, 2, 0.0, 0.0),
        ('_UseWorldSpaceParallaxMask', 'F', 48, 3, 0.0, 0.0),
        ('_MaskWorldPosParams', 'V4', 49, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMaskMapColorStrength', 'F', 50, 0, 0.0, 0.0),
        ('_ParallaxSignControl', 'F', 50, 1, 0.0, 0.0),
        ('_ParallaxSignLerpFactor0', 'V4', 51, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor2', 'F', 50, 2, 0.0, 0.0),
        ('_ParallaxLerpSchedule', 'F', 50, 3, 0.0, 0.0),
        ('_ParallaxPatternColorDark', 'V4', 52, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxPatternColor', 'V4', 53, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor1', 'V4', 54, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WorldParallaxAdditionalLightMaskChannel', 'F', 55, 0, 0.0, 0.0),
        ('_WorldParallaxAdditionalColor', 'V4', 56, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxIntensity', 'F', 55, 1, 0.0, 0.0),
        ('_UseThinFilm', 'F', 55, 2, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 55, 3, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 57, 0, 0.5, 0.0),
        ('M_PI', 'F', 57, 1, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 57, 2, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 57, 3, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 58, 0, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 59, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 58, 1, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 58, 2, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 58, 3, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 60, 0, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 60, 1, 1.0, 0.0),
        ('_PlanarReflection', 'F', 60, 2, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 61, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 60, 3, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 62, 0, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 63, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 64, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 62, 1, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 62, 2, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 62, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 65, 0, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 65, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 65, 2, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 65, 3, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 66, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxNoiseMapTilling', 'F', 67, 0, 0.0, 0.0),
        ('__size_ParallaxMaskMap', 'V3', 68, 0, (1.0, 1.0, 1.0), 0.0),
    ],
    'LitHLod': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_NormalScale', 'F', 7, 2, 0.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 3, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 8, 0, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 1, 0.0, 0.0),
        ('_RoughnessMax', 'F', 8, 2, 1.0, 0.0),
        ('_Metallic', 'F', 8, 3, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 0, 0.0, 0.0),
        ('_OcclusionStrength', 'F', 9, 1, 1.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 2, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 9, 3, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 0, 0.4, 0.0),
        ('_DisableVerticalFlow', 'F', 10, 1, 0.0, 0.0),
        ('_EffectIntensity', 'F', 10, 2, 1.0, 0.0),
        ('_HLodFade', 'F', 10, 3, 1.0, 0.0),
        ('_UseMacroNormalMap', 'F', 11, 0, 0.0, 0.0),
        ('_MacroNormalMapScale', 'F', 11, 1, 1.0, 0.0),
        ('_EnableDetailMap', 'F', 11, 2, 0.0, 0.0),
        ('_DetailFalloffStart', 'F', 11, 3, 750.0, 0.0),
        ('_DetailFalloffEnd', 'F', 12, 0, 800.0, 0.0),
        ('_DetailMaskMode', 'F', 12, 1, 0.0, 0.0),
        ('_DetailNormalIntensity', 'F', 12, 2, 1.0, 0.0),
        ('_DetailMode', 'F', 12, 3, 0.0, 0.0),
        ('_DetailBaseColorBrighterScale', 'F', 13, 0, 1.0, 0.0),
        ('_DetailOverlayColor', 'V4', 14, 0, (0.0, 0.0, 0.0), 0.0),
        ('_DetailPBRIntensity', 'F', 13, 1, 1.0, 0.0),
        ('_EnableTriChannelMask', 'F', 13, 2, 0.0, 0.0),
        ('_MaskUVSet', 'F', 13, 3, 0.0, 0.0),
        ('_MaskMap_ST', 'V4', 15, 0, (1.0, 1.0, 0.0), 0.0),
        ('_MaskBOffset', 'F', 16, 0, 0.0, 0.0),
        ('_MaskBScale', 'F', 16, 1, 0.0, 0.0),
        ('_MaskAlbedoB', 'V4', 17, 0, (0.0, 0.0, 1.0), 1.0),
        ('_MaskGOffset', 'F', 16, 2, 0.0, 0.0),
        ('_MaskGScale', 'F', 16, 3, 0.0, 0.0),
        ('_MaskAlbedoG', 'V4', 18, 0, (0.0, 1.0, 0.0), 1.0),
        ('_MaskROffset', 'F', 19, 0, 0.0, 0.0),
        ('_MaskRScale', 'F', 19, 1, 0.0, 0.0),
        ('_MaskAlbedoR', 'V4', 20, 0, (1.0, 0.0, 0.0), 1.0),
        ('_MaskRoghnessB', 'F', 19, 2, 0.25, 0.0),
        ('_MaskRoghnessG', 'F', 19, 3, 0.25, 0.0),
        ('_MaskRoghnessR', 'F', 21, 0, 0.25, 0.0),
        ('_MaskMetallicB', 'F', 21, 1, 0.0, 0.0),
        ('_MaskMetallicG', 'F', 21, 2, 0.0, 0.0),
        ('_MaskMetallicR', 'F', 21, 3, 0.0, 0.0),
        ('_LayerBlend', 'F', 22, 0, 0.0, 0.0),
        ('_LayerBlendUVType', 'F', 22, 1, 0.0, 0.0),
        ('_Layer1Tilling', 'F', 22, 2, 1.0, 0.0),
        ('_LayerBlendType', 'F', 22, 3, 1.0, 0.0),
        ('_LayerBlendMaskUVType', 'F', 23, 0, 0.0, 0.0),
        ('_LayerBlendMaskType', 'F', 23, 1, 0.0, 0.0),
        ('_TopBlendWithBumpMap', 'F', 23, 2, 0.0, 0.0),
        ('_TopBlendThreshold', 'F', 23, 3, 0.5, 0.0),
        ('_TopBlendSmoothness', 'F', 24, 0, 0.5, 0.0),
        ('_LayerBlendHeightTransition', 'F', 24, 1, 1.0, 0.0),
        ('_LayerBlendHeight', 'F', 24, 2, 1.0, 0.0),
        ('_Layer1Saturation', 'F', 24, 3, 0.0, 0.0),
        ('_Layer1TintColor', 'V4', 25, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Layer1ColorBrighterScale', 'F', 26, 0, 1.0, 0.0),
        ('_LayerMetallicType', 'F', 26, 1, 0.0, 0.0),
        ('_Layer1Metallic', 'F', 26, 2, 0.0, 0.0),
        ('_Layer1AOStrength', 'F', 26, 3, 1.0, 0.0),
        ('_Layer1BumpScale', 'F', 27, 0, 1.0, 0.0),
        ('_Layer1BaseNormalIntensity', 'F', 27, 1, 0.0, 0.0),
        ('_EmissionTint', 'V4', 28, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveIntensity', 'F', 27, 2, 0.0, 0.0),
        ('_UseEmissiveMap', 'F', 27, 3, 0.0, 0.0),
        ('_AlbedoAffectEmissive', 'F', 29, 0, 1.0, 0.0),
        ('_EnableEmissiveAnim', 'F', 29, 1, 0.0, 0.0),
        ('_EmissiveAnimSpeed', 'F', 29, 2, 0.0, 0.0),
        ('_EmissiveAnimRandom', 'F', 29, 3, 0.0, 0.0),
        ('_EmissiveAnimInterval', 'F', 30, 0, 1.0, 0.0),
        ('_EmissiveMinBrightness', 'F', 30, 1, 0.0, 0.0),
        ('_EnableEmissiveAnimSweep', 'F', 30, 2, 0.0, 0.0),
        ('_EmissiveMaskChannel', 'F', 30, 3, 0.0, 0.0),
        ('_EmissiveSweepRandom', 'F', 31, 0, 0.0, 0.0),
        ('_EmissiveSweepInterval', 'F', 31, 1, 3.0, 0.0),
        ('_EmissiveSweepSpeed', 'F', 31, 2, 3.0, 0.0),
        ('_EmissiveSweepWidth', 'F', 31, 3, 0.8, 0.0),
        ('_EmissiveSweepFalloff', 'F', 32, 0, 1.0, 0.0),
        ('_EmissiveSweepAlbedoScale', 'F', 32, 1, 0.0, 0.0),
        ('_EmissiveColor', 'V4', 33, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EmissiveColorG', 'V4', 34, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 35, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 36, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveSpeed', 'V4', 37, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveUVSet', 'F', 32, 2, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 38, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMapTilling', 'F', 32, 3, 0.0, 0.0),
        ('_EmissiveType', 'F', 39, 0, 0.0, 0.0),
        ('_EnableMatcap', 'F', 39, 1, 0.0, 0.0),
        ('_MatcapMapStrength', 'F', 39, 2, 0.2, 0.0),
        ('_EnableParallaxMap', 'F', 39, 3, 0.0, 0.0),
        ('_ParallaxMappingType', 'F', 40, 0, 0.0, 0.0),
        ('_UseParallaxMask', 'F', 40, 1, 0.0, 0.0),
        ('_ParallaxMaskChannel', 'F', 40, 2, 0.0, 0.0),
        ('_ParallaxMaskByLayerBlend', 'F', 40, 3, 0.0, 0.0),
        ('_ParallaxMapUVType', 'F', 41, 0, 0.0, 0.0),
        ('_GlobalMipBias', 'V3', 42, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMarchNum', 'F', 41, 1, 3.0, 0.0),
        ('_ParallaxStrength', 'F', 41, 2, 0.0, 0.0),
        ('_ParallaxTilling', 'F', 41, 3, 1.0, 0.0),
        ('_ParallaxColor', 'V4', 43, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxColorDark', 'V4', 44, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxFresnelStrength', 'F', 45, 0, 0.0, 0.0),
        ('_VFXParams0', 'V4', 46, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxBrightOuterRadius', 'F', 45, 1, 0.0, 0.0),
        ('_ParallaxBrightInnerRadius', 'F', 45, 2, 0.0, 0.0),
        ('_ParallaxBrightStrength', 'F', 45, 3, 0.0, 0.0),
        ('_ParallaxCharPos', 'F', 47, 0, 0.0, 0.0),
        ('_VFXParams2', 'V4', 48, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMinBrightness', 'F', 47, 1, 0.0, 0.0),
        ('_ParallaxAnimSpeed', 'F', 47, 2, 0.0, 0.0),
        ('_ParallaxAnimRandom', 'F', 47, 3, 0.0, 0.0),
        ('_UseWorldSpaceParallaxMask', 'F', 49, 0, 0.0, 0.0),
        ('_MaskWorldPosParams', 'V4', 50, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxMaskMapColorStrength', 'F', 49, 1, 0.0, 0.0),
        ('_ParallaxSignControl', 'F', 49, 2, 0.0, 0.0),
        ('_ParallaxSignLerpFactor0', 'V4', 51, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor2', 'F', 49, 3, 0.0, 0.0),
        ('_ParallaxLerpSchedule', 'F', 52, 0, 0.0, 0.0),
        ('_ParallaxPatternColorDark', 'V4', 53, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxPatternColor', 'V4', 54, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxSignLerpFactor1', 'V4', 55, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WorldParallaxAdditionalLightMaskChannel', 'F', 52, 1, 0.0, 0.0),
        ('_WorldParallaxAdditionalColor', 'V4', 56, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxIntensity', 'F', 52, 2, 0.0, 0.0),
        ('_UseThinFilm', 'F', 52, 3, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 57, 0, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 57, 1, 0.5, 0.0),
        ('M_PI', 'F', 57, 2, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 57, 3, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 58, 0, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 58, 1, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 59, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 58, 2, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 58, 3, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 60, 0, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 60, 1, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 60, 2, 1.0, 0.0),
        ('_PlanarReflection', 'F', 60, 3, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 61, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 62, 0, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 62, 1, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 63, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 64, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 62, 2, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 62, 3, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 65, 0, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 65, 1, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 65, 2, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 65, 3, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 66, 0, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 67, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxNoiseMapTilling', 'F', 66, 1, 0.0, 0.0),
        ('__size_ParallaxMaskMap', 'V3', 68, 0, (1.0, 1.0, 1.0), 0.0),
    ],
    'Unlit': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 7, 2, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 7, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 8, 0, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 8, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 8, 2, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 9, 0, (0.0, 0.0, 0.0), 0.0),
    ],
    'ContainerWater': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('unity_OrthoParams', 'V4', 8, 0, (0.0, 0.0, 0.0), 0.0),
        ('_NormalScale', 'F', 7, 2, 0.0, 0.0),
        ('_Use_VerexTexColorAsOpacity', 'F', 7, 3, 0.0, 0.0),
        ('_RefractionIOR', 'F', 9, 0, 1.0, 0.0),
        ('_RefractionFresnelColor', 'V4', 10, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Roughness', 'F', 9, 1, 0.0, 0.0),
        ('_GraphicsFeaturesGlobalParam1', 'V4', 11, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EnableParallaxMap', 'F', 9, 2, 0.0, 0.0),
        ('_UseParallaxMask', 'F', 9, 3, 0.0, 0.0),
        ('_ParallaxMaskChannel', 'F', 12, 0, 0.0, 0.0),
        ('_ParallaxMapUVType', 'F', 12, 1, 0.0, 0.0),
        ('_ParallaxNoiseMapTilling', 'F', 12, 2, 0.0, 0.0),
        ('_ParallaxMarchNum', 'F', 12, 3, 3.0, 0.0),
        ('_ParallaxStrength', 'F', 13, 0, 0.0, 0.0),
        ('_ParallaxTilling', 'F', 13, 1, 1.0, 0.0),
        ('_ParallaxMinBrightness', 'F', 13, 2, 0.0, 0.0),
        ('_ParallaxFresnelStrength', 'F', 13, 3, 0.0, 0.0),
        ('_VFXParams0', 'V4', 14, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxAnimSpeed', 'F', 15, 0, 0.0, 0.0),
        ('_ParallaxAnimRandom', 'F', 15, 1, 0.0, 0.0),
        ('_ParallaxCharPos', 'F', 15, 2, 0.0, 0.0),
        ('_ParallaxBrightOuterRadius', 'F', 15, 3, 0.0, 0.0),
        ('_ParallaxBrightInnerRadius', 'F', 16, 0, 0.0, 0.0),
        ('_ParallaxBrightStrength', 'F', 16, 1, 0.0, 0.0),
        ('_VFXParams2', 'V4', 17, 0, (0.0, 0.0, 0.0), 0.0),
        ('_ParallaxColorDark', 'V4', 18, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxColor', 'V4', 19, 0, (0.0, 0.0, 0.0), 1.0),
        ('_ParallaxIgnorePostExposure', 'F', 16, 2, 1.0, 0.0),
        ('_ExposureWithMiscParams', 'V4', 20, 0, (1.0, 1.0, 1.0), 1.0),
        ('_FresnelUseMeshNormal', 'F', 16, 3, 0.0, 0.0),
        ('_FresnelBias', 'F', 21, 0, 0.0, 0.0),
        ('_FresnelPower', 'F', 21, 1, 1.0, 0.0),
        ('_FresnelFlip', 'F', 21, 2, 0.001, 0.0),
        ('_UseFresnel', 'F', 21, 3, 0.0, 0.0),
        ('_FresnelAffectOpacity', 'F', 22, 0, 1.0, 0.0),
        ('_EnableGlassRim', 'F', 22, 1, 0.0, 0.0),
        ('_GlassRimPower', 'F', 22, 2, 1.0, 0.0),
        ('_GlassRimStrength', 'F', 22, 3, 1.0, 0.0),
        ('_GlassRimUseMask', 'F', 23, 0, 0.0, 0.0),
        ('_GlassRimMaskChannel', 'F', 23, 1, 0.0, 0.0),
        ('_GlassRimColor', 'V4', 24, 0, (1.0, 1.0, 1.0), 1.0),
        ('_GlassRimRoughnessScale', 'F', 23, 2, 1.0, 0.0),
        ('_UseVertexColorAsRimMask', 'F', 23, 3, 0.0, 0.0),
        ('_GlassMaskOpacity', 'F', 25, 0, 0.995, 0.0),
        ('_EnableGlassRefraction', 'F', 25, 1, 0.0, 0.0),
        ('_RefractThickness', 'F', 25, 2, 0.01, 0.0),
        ('_IsShell', 'F', 25, 3, 1.0, 0.0),
        ('_IoR', 'F', 26, 0, 0.8, 0.0),
        ('_RefractTex_ST', 'V4', 27, 0, (1.0, 1.0, 0.0), 0.0),
        ('_RefractTexIntensity', 'F', 26, 1, 0.01, 0.0),
        ('_UseCustomRefractTex', 'F', 26, 2, 0.0, 0.0),
        ('_RefractTint', 'V4', 28, 0, (1.0, 1.0, 1.0), 1.0),
        ('_RefractBrightness', 'F', 26, 3, 1.0, 0.0),
        ('_RefractionContribution', 'F', 29, 0, 0.8, 0.0),
        ('_GlassRimRefractionPower', 'F', 29, 1, 1.0, 0.0),
        ('_GlassRimRefractionStrength', 'F', 29, 2, 1.0, 0.0),
        ('_EnableIce', 'F', 29, 3, 0.0, 0.0),
        ('_IceRefractionStrength', 'F', 30, 0, 1.0, 0.0),
        ('_IceRefractionColor', 'V4', 31, 0, (1.0, 1.0, 1.0), 1.0),
        ('_IceRefractionBrightness', 'F', 30, 1, 1.0, 0.0),
        ('_IceOpacityMapTilling', 'F', 30, 2, 1.0, 0.0),
        ('_IceOpacityThreshold', 'F', 30, 3, 0.0, 0.0),
        ('_EnableContainerWater', 'F', 32, 0, 0.0, 0.0),
        ('_WaterNormalMap_ST', 'V4', 33, 0, (1.0, 1.0, 0.0), 0.0),
        ('_WaterNormalSpeed', 'F', 32, 1, 0.01, 0.0),
        ('_WaterSurfaceNormalScale', 'F', 32, 2, 1.0, 0.0),
        ('_DisplacementTex_ST', 'V4', 34, 0, (1.0, 1.0, 0.0), 0.0),
        ('_NormalMapBlendWeight', 'F', 32, 3, 0.5, 0.0),
        ('_DisplacementNormalStrength', 'F', 35, 0, 0.5, 0.0),
        ('_IcePosition', 'V4', 36, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WaterCupRadius', 'F', 35, 1, 1.0, 0.0),
        ('_IceballRadius', 'F', 35, 2, 1.0, 0.0),
        ('_IceballWaterlineWidth', 'F', 35, 3, 1.0, 0.0),
        ('_WaterCausticSpeed', 'F', 37, 0, 1.0, 0.0),
        ('_MainLightPosition', 'V4', 38, 0, (0.0, 0.0, 0.0), 0.0),
        ('_WaterScatteringColor', 'V4', 39, 0, (1.0, 1.0, 1.0), 1.0),
        ('_WaterRefractionColor', 'V4', 40, 0, (1.0, 1.0, 1.0), 1.0),
        ('_WaterRefractionBrightness', 'F', 37, 1, 1.0, 0.0),
        ('_WaterCausticMap_ST', 'V4', 41, 0, (1.0, 1.0, 0.0), 0.0),
        ('_WaterShallowColor', 'V4', 42, 0, (1.0, 1.0, 1.0), 1.0),
        ('_WaterCausticStrength', 'F', 37, 2, 1.0, 0.0),
        ('_WaterDeepColor', 'V4', 43, 0, (1.0, 1.0, 1.0), 1.0),
        ('_WaterAbsorptionColor', 'V4', 44, 0, (1.0, 1.0, 1.0), 1.0),
        ('_GraphicsFeaturesGlobalParam0', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 46, 0, (1.67, 1.5, 1.0), 0.0),
        ('_WaterFresnelPower', 'F', 37, 3, 1.0, 0.0),
        ('_WaterReflectionStrength', 'F', 47, 0, 1.0, 0.0),
        ('_WaterStrokeDistance', 'F', 47, 1, 0.0395, 0.0),
        ('_WaterStrokeWidth', 'F', 47, 2, 0.0352, 0.0),
        ('_WaterStrokeSoftness', 'F', 47, 3, 0.0025, 0.0),
        ('_WaterStrokeOpacity', 'F', 48, 0, 1.0, 0.0),
        ('_WaterMeniscusWidth', 'F', 48, 1, 1.0, 0.0),
        ('_WaterStrokeColor', 'V4', 49, 0, (1.0, 1.0, 1.0), 1.0),
        ('_WaterBaseOpacity', 'F', 48, 2, 1.0, 0.0),
        ('_WaterOpacityDepthFactor', 'F', 48, 3, 1.0, 0.0),
        ('_WaterOpacityFresnelFactor', 'F', 50, 0, 1.0, 0.0),
        ('_WaterEdgeOpacity', 'F', 50, 1, 1.0, 0.0),
        ('_WaterTurbidity', 'F', 50, 2, 1.0, 0.0),
        ('_WaterOpacityMinimum', 'F', 50, 3, 1.0, 0.0),
        ('_WaterOpacityMaximum', 'F', 51, 0, 1.0, 0.0),
        ('_Specular', 'F', 51, 1, 1.0, 0.0),
        ('_MatcapMapStrength', 'F', 51, 2, 0.2, 0.0),
        ('_MatCapIgnorePostExposure', 'F', 51, 3, 1.0, 0.0),
        ('_EnableMatcap', 'F', 52, 0, 0.0, 0.0),
        ('_RefractionColor', 'V4', 53, 0, (1.0, 1.0, 1.0), 1.0),
        ('_RefractionStrength', 'F', 52, 1, 1.0, 0.0),
        ('_FresnelColor', 'V4', 54, 0, (1.0, 1.0, 1.0), 1.0),
        ('_Use_VerexGAsFresnelOpacity', 'F', 52, 2, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 52, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 55, 0, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 55, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 55, 2, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 55, 3, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 56, 0, (0.0, 0.0, 0.0), 0.0),
    ],
    'Leaf': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 2, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 7, 3, 0.0, 0.0),
        ('_EnableNormalMap', 'F', 8, 0, 0.0, 0.0),
        ('_NormalScale', 'F', 8, 1, 0.0, 0.0),
        ('_BendNormalUpward', 'F', 8, 2, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 3, 0.0, 0.0),
        ('_RoughnessMax', 'F', 9, 0, 1.0, 0.0),
        ('_Metallic', 'F', 9, 1, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 2, 0.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 3, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 10, 0, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 1, 0.4, 0.0),
        ('_OcclusionStrength', 'F', 10, 2, 1.0, 0.0),
        ('_TrunkVertexAoStrength', 'F', 10, 3, 1.0, 0.0),
        ('_EnableVerticalNormalBoostAO', 'F', 11, 0, 0.0, 0.0),
        ('_VerticalNormalThreshold', 'F', 11, 1, 0.0, 0.0),
        ('_VerticalNormalBoostAO', 'F', 11, 2, 0.0, 0.0),
        ('_TransmissionDistanceFade', 'F', 11, 3, 0.0, 0.0),
        ('_Transmission', 'F', 12, 0, 0.2, 0.0),
        ('_AoAffectTransmissionStart', 'F', 12, 1, 0.0, 0.0),
        ('_AoAffectTransmissionRange', 'F', 12, 2, 0.01, 0.0),
        ('_SubsurfaceIntensity', 'F', 12, 3, 0.0, 0.0),
        ('_AoAffectSubsurfaceStart', 'F', 13, 0, 0.0, 0.0),
        ('_AoAffectSubsurfaceRange', 'F', 13, 1, 0.01, 0.0),
        ('_FakeDirectionalShadowStrength', 'F', 13, 2, 0.0, 0.0),
        ('_DiffuseUseVertexNormal', 'F', 13, 3, 1.0, 0.0),
        ('_MainLightPosition', 'V4', 14, 0, (0.0, 0.0, 0.0), 0.0),
        ('_FakeDirectionalShadowPow', 'F', 15, 0, 1.0, 0.0),
        ('_OcclusionShadow', 'F', 15, 1, 0.0, 0.0),
        ('_EnableCanopyColorRamp', 'F', 15, 2, 0.0, 0.0),
        ('_CanopyRampStartAtTop', 'F', 15, 3, 0.0, 0.0),
        ('_CanopyRampRange', 'F', 16, 0, 0.0, 0.0),
        ('_CanopyRampTransitionRange', 'F', 16, 1, 0.01, 0.0),
        ('_CanopyRampIntensity', 'F', 16, 2, 1.0, 0.0),
        ('_CanopyRampColor', 'V4', 17, 0, (1.0, 1.0, 1.0), 1.0),
        ('_CanopyRampColorBrighterScale', 'F', 16, 3, 1.0, 0.0),
        ('_CanopyRampColorCover', 'F', 18, 0, 0.0, 0.0),
        ('_EnableAoTuneColor', 'F', 18, 1, 0.0, 0.0),
        ('_FlipAoMask', 'F', 18, 2, 0.0, 0.0),
        ('_AoMaskTuneColorRampStart', 'F', 18, 3, 0.0, 0.0),
        ('_AoMaskTuneColorRampRange', 'F', 19, 0, 0.2, 0.0),
        ('_AoMaskTuneColorIntensity', 'F', 19, 1, 1.0, 0.0),
        ('_AoMaskTuneColor', 'V4', 20, 0, (1.0, 1.0, 1.0), 1.0),
        ('_AoMaskTuneColorBrighterScale', 'F', 19, 2, 1.0, 0.0),
        ('_AoMaskTuneColorCover', 'F', 19, 3, 0.0, 0.0),
        ('_EnableBlendColor', 'F', 21, 0, 0.0, 0.0),
        ('_BlendWithVertexNormal', 'F', 21, 1, 0.0, 0.0),
        ('_BlendNormalAdd', 'F', 21, 2, 0.0, 0.0),
        ('_BlendColor', 'V4', 22, 0, (1.0, 1.0, 1.0), 1.0),
        ('_BlendNormalPower', 'F', 21, 3, 1.0, 0.0),
        ('_EnableTrunkRamp', 'F', 23, 0, 0.0, 0.0),
        ('_TrunkRampRange', 'F', 23, 1, 0.0, 0.0),
        ('_TrunkRampTransitionRange', 'F', 23, 2, 0.01, 0.0),
        ('_TrunkRampIntensity', 'F', 23, 3, 1.0, 0.0),
        ('_TrunkRampColor', 'V4', 24, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableEmissiveMap', 'F', 25, 0, 0.0, 0.0),
        ('_EmissiveUVSet', 'F', 25, 1, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 26, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMaskChannel', 'F', 25, 2, 0.0, 0.0),
        ('_EmissiveColorR', 'V4', 27, 0, (0.0, 0.0, 0.0), 1.0),
        ('_EmissiveColorG', 'V4', 28, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 29, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 30, 0, (0.0, 0.0, 0.0), 0.0),
        ('_AlbedoAffectEmissive', 'F', 25, 3, 1.0, 0.0),
        ('_EnableVertColorEmissive', 'F', 31, 0, 0.0, 0.0),
        ('_VertColorEmissiveChannelVector', 'V4', 32, 0, (1.0, 0.0, 0.0), 0.0),
        ('_VertColorEmissiveFlip', 'F', 31, 1, 0.0, 0.0),
        ('_VertColorEmissiveBias', 'F', 31, 2, 0.0, 0.0),
        ('_VertColorEmissiveColor', 'V4', 33, 0, (0.0, 0.0, 0.0), 1.0),
        ('_VertColorEmissiveAlbedoAffect', 'F', 31, 3, 1.0, 0.0),
        ('_CrossCardViewCulling', 'F', 34, 0, 0.0, 0.0),
        ('_CrossCardViewCullingThreshold', 'F', 34, 1, 0.4, 0.0),
        ('_CrossCardViewCullingFadeValue', 'F', 34, 2, 0.5, 0.0),
        ('_UseThinFilm', 'F', 34, 3, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 35, 0, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 35, 1, 0.5, 0.0),
        ('M_PI', 'F', 35, 2, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 35, 3, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 36, 0, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 36, 1, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 37, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 36, 2, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 36, 3, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 38, 0, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 38, 1, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 38, 2, 1.0, 0.0),
        ('_PlanarReflection', 'F', 38, 3, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 39, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 40, 0, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 40, 1, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 41, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 42, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 40, 2, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 40, 3, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 43, 0, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 43, 1, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 43, 2, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 43, 3, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 44, 0, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
    ],
    'Grass': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 2, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 7, 3, 0.0, 0.0),
        ('_EnableNormalMap', 'F', 8, 0, 0.0, 0.0),
        ('_NormalScale', 'F', 8, 1, 0.0, 0.0),
        ('_BendNormalUpward', 'F', 8, 2, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 3, 0.0, 0.0),
        ('_RoughnessMax', 'F', 9, 0, 1.0, 0.0),
        ('_Metallic', 'F', 9, 1, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 2, 0.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 3, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 10, 0, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 1, 0.4, 0.0),
        ('_OcclusionStrength', 'F', 10, 2, 1.0, 0.0),
        ('_TrunkVertexAoStrength', 'F', 10, 3, 1.0, 0.0),
        ('_EnableVerticalNormalBoostAO', 'F', 11, 0, 0.0, 0.0),
        ('_VerticalNormalThreshold', 'F', 11, 1, 0.0, 0.0),
        ('_VerticalNormalBoostAO', 'F', 11, 2, 0.0, 0.0),
        ('_AoPosition', 'F', 11, 3, 0.0, 0.0),
        ('_AoRadius', 'F', 12, 0, 0.1, 0.0),
        ('_AoContrast', 'F', 12, 1, 0.2, 0.0),
        ('_AoIntensity', 'F', 12, 2, 0.2, 0.0),
        ('_TransmissionDistanceFade', 'F', 12, 3, 0.0, 0.0),
        ('_Transmission', 'F', 13, 0, 0.2, 0.0),
        ('_AoAffectTransmissionStart', 'F', 13, 1, 0.0, 0.0),
        ('_AoAffectTransmissionRange', 'F', 13, 2, 0.01, 0.0),
        ('_SubsurfaceIntensity', 'F', 13, 3, 0.0, 0.0),
        ('_AoAffectSubsurfaceStart', 'F', 14, 0, 0.0, 0.0),
        ('_AoAffectSubsurfaceRange', 'F', 14, 1, 0.01, 0.0),
        ('_DirPosition', 'F', 14, 2, 0.0, 0.0),
        ('_DirRadius', 'F', 14, 3, 0.1, 0.0),
        ('_DirContrast', 'F', 15, 0, 0.2, 0.0),
        ('_MaskOnTransmission', 'F', 15, 1, 1.0, 0.0),
        ('_FakeDirectionalShadowStrength', 'F', 15, 2, 0.0, 0.0),
        ('_DiffuseUseVertexNormal', 'F', 15, 3, 1.0, 0.0),
        ('_MainLightPosition', 'V4', 16, 0, (0.0, 0.0, 0.0), 0.0),
        ('_FakeDirectionalShadowPow', 'F', 17, 0, 1.0, 0.0),
        ('_OcclusionShadow', 'F', 17, 1, 0.0, 0.0),
        ('_EnableCanopyColorRamp', 'F', 17, 2, 0.0, 0.0),
        ('_CanopyRampStartAtTop', 'F', 17, 3, 0.0, 0.0),
        ('_CanopyRampRange', 'F', 18, 0, 0.0, 0.0),
        ('_CanopyRampTransitionRange', 'F', 18, 1, 0.01, 0.0),
        ('_CanopyRampIntensity', 'F', 18, 2, 1.0, 0.0),
        ('_CanopyRampColor', 'V4', 19, 0, (1.0, 1.0, 1.0), 1.0),
        ('_CanopyRampColorBrighterScale', 'F', 18, 3, 1.0, 0.0),
        ('_CanopyRampColorCover', 'F', 20, 0, 0.0, 0.0),
        ('_EnableAoTuneColor', 'F', 20, 1, 0.0, 0.0),
        ('_FlipAoMask', 'F', 20, 2, 0.0, 0.0),
        ('_AoMaskTuneColorRampStart', 'F', 20, 3, 0.0, 0.0),
        ('_AoMaskTuneColorRampRange', 'F', 21, 0, 0.2, 0.0),
        ('_AoMaskTuneColorIntensity', 'F', 21, 1, 1.0, 0.0),
        ('_AoMaskTuneColor', 'V4', 22, 0, (1.0, 1.0, 1.0), 1.0),
        ('_AoMaskTuneColorBrighterScale', 'F', 21, 2, 1.0, 0.0),
        ('_AoMaskTuneColorCover', 'F', 21, 3, 0.0, 0.0),
        ('_EnableBlendColor', 'F', 23, 0, 0.0, 0.0),
        ('_BlendWithVertexNormal', 'F', 23, 1, 0.0, 0.0),
        ('_BlendNormalAdd', 'F', 23, 2, 0.0, 0.0),
        ('_BlendColor', 'V4', 24, 0, (1.0, 1.0, 1.0), 1.0),
        ('_BlendNormalPower', 'F', 23, 3, 1.0, 0.0),
        ('_EnableTrunkRamp', 'F', 25, 0, 0.0, 0.0),
        ('_TrunkRampRange', 'F', 25, 1, 0.0, 0.0),
        ('_TrunkRampTransitionRange', 'F', 25, 2, 0.01, 0.0),
        ('_TrunkRampIntensity', 'F', 25, 3, 1.0, 0.0),
        ('_TrunkRampColor', 'V4', 26, 0, (1.0, 1.0, 1.0), 1.0),
        ('_DirIntensity', 'F', 27, 0, 0.2, 0.0),
        ('_MaskOnDiffuse', 'F', 27, 1, 1.0, 0.0),
        ('_EnableEmissiveMap', 'F', 27, 2, 0.0, 0.0),
        ('_EmissiveUVSet', 'F', 27, 3, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 28, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMaskChannel', 'F', 29, 0, 0.0, 0.0),
        ('_EmissiveColorR', 'V4', 30, 0, (0.0, 0.0, 0.0), 1.0),
        ('_EmissiveColorG', 'V4', 31, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 32, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 33, 0, (0.0, 0.0, 0.0), 0.0),
        ('_AlbedoAffectEmissive', 'F', 29, 1, 1.0, 0.0),
        ('_EnableVertColorEmissive', 'F', 29, 2, 0.0, 0.0),
        ('_VertColorEmissiveChannelVector', 'V4', 34, 0, (1.0, 0.0, 0.0), 0.0),
        ('_VertColorEmissiveFlip', 'F', 29, 3, 0.0, 0.0),
        ('_VertColorEmissiveBias', 'F', 35, 0, 0.0, 0.0),
        ('_VertColorEmissiveColor', 'V4', 36, 0, (0.0, 0.0, 0.0), 1.0),
        ('_VertColorEmissiveAlbedoAffect', 'F', 35, 1, 1.0, 0.0),
        ('_CrossCardViewCulling', 'F', 35, 2, 0.0, 0.0),
        ('_CrossCardViewCullingThreshold', 'F', 35, 3, 0.4, 0.0),
        ('_CrossCardViewCullingFadeValue', 'F', 37, 0, 0.5, 0.0),
        ('_UseThinFilm', 'F', 37, 1, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 37, 2, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 37, 3, 0.5, 0.0),
        ('M_PI', 'F', 38, 0, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 38, 1, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 38, 2, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 38, 3, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 39, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 40, 0, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 40, 1, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 40, 2, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 40, 3, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 41, 0, 1.0, 0.0),
        ('_PlanarReflection', 'F', 41, 1, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 42, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 41, 2, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 41, 3, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 43, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 44, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 45, 0, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 45, 1, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 45, 2, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 45, 3, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 46, 0, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 46, 1, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 46, 2, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 47, 0, (0.0, 0.0, 0.0), 0.0),
    ],
    'Trunk': [
        ('_TwoSidedNormal', 'F', 0, 0, 1.0, 0.0),
        ('_BaseColor', 'V4', 1, 0, (1.0, 1.0, 1.0), 1.0),
        ('_UseVoxelAtlas', 'F', 0, 1, 0.0, 0.0),
        ('_UseCutoff', 'F', 0, 2, 0.0, 0.0),
        ('_UseVertexColor', 'F', 0, 3, 0.0, 0.0),
        ('_RuriVoxelLightVolumeOn', 'F', 2, 0, 0.0, 0.0),
        ('_UseDitherClip', 'F', 2, 1, 0.0, 0.0),
        ('_Cutoff', 'F', 2, 2, 0.5, 0.0),
        ('_EnableAlphaTest', 'F', 2, 3, 0.0, 0.0),
        ('_BaseUVSet', 'F', 3, 0, 0.0, 0.0),
        ('_BaseColorMap_ST', 'V4', 4, 0, (1.0, 1.0, 0.0), 0.0),
        ('_BasePbrMapUVSet', 'F', 3, 1, 0.0, 0.0),
        ('_NormalMap_ST', 'V4', 5, 0, (1.0, 1.0, 0.0), 0.0),
        ('_AlphaMaskChannel', 'F', 3, 2, 0.0, 0.0),
        ('_AlphaClipThreshold', 'F', 3, 3, 0.5, 0.0),
        ('_RoughnessIntensity', 'F', 6, 0, 0.5, 0.0),
        ('_MetallicIntensity', 'F', 6, 1, 0.0, 0.0),
        ('_OcclusionIntensity', 'F', 6, 2, 1.0, 0.0),
        ('_SpecularIntensity', 'F', 6, 3, 1.0, 0.0),
        ('_RuriRadianceMode', 'F', 7, 0, 0.0, 0.0),
        ('_VoxelEmissionScale', 'F', 7, 1, 4.0, 0.0),
        ('_BaseColorBrighterScale', 'F', 7, 2, 1.0, 0.0),
        ('_BaseColorTintCover', 'F', 7, 3, 0.0, 0.0),
        ('_EnableNormalMap', 'F', 8, 0, 0.0, 0.0),
        ('_NormalScale', 'F', 8, 1, 0.0, 0.0),
        ('_BendNormalUpward', 'F', 8, 2, 0.0, 0.0),
        ('_RoughnessMin', 'F', 8, 3, 0.0, 0.0),
        ('_RoughnessMax', 'F', 9, 0, 1.0, 0.0),
        ('_Metallic', 'F', 9, 1, 0.0, 0.0),
        ('_BaseTextureMapCount', 'F', 9, 2, 0.0, 0.0),
        ('_PorosityFactorX', 'F', 9, 3, 0.2, 0.0),
        ('_PorosityFactorZ', 'F', 10, 0, 0.0, 0.0),
        ('_PorosityFactorY', 'F', 10, 1, 0.4, 0.0),
        ('_OcclusionStrength', 'F', 10, 2, 1.0, 0.0),
        ('_TrunkVertexAoStrength', 'F', 10, 3, 1.0, 0.0),
        ('_EnableVerticalNormalBoostAO', 'F', 11, 0, 0.0, 0.0),
        ('_VerticalNormalThreshold', 'F', 11, 1, 0.0, 0.0),
        ('_VerticalNormalBoostAO', 'F', 11, 2, 0.0, 0.0),
        ('_TransmissionDistanceFade', 'F', 11, 3, 0.0, 0.0),
        ('_Transmission', 'F', 12, 0, 0.2, 0.0),
        ('_AoAffectTransmissionStart', 'F', 12, 1, 0.0, 0.0),
        ('_AoAffectTransmissionRange', 'F', 12, 2, 0.01, 0.0),
        ('_SubsurfaceIntensity', 'F', 12, 3, 0.0, 0.0),
        ('_AoAffectSubsurfaceStart', 'F', 13, 0, 0.0, 0.0),
        ('_AoAffectSubsurfaceRange', 'F', 13, 1, 0.01, 0.0),
        ('_FakeDirectionalShadowStrength', 'F', 13, 2, 0.0, 0.0),
        ('_DiffuseUseVertexNormal', 'F', 13, 3, 1.0, 0.0),
        ('_MainLightPosition', 'V4', 14, 0, (0.0, 0.0, 0.0), 0.0),
        ('_FakeDirectionalShadowPow', 'F', 15, 0, 1.0, 0.0),
        ('_OcclusionShadow', 'F', 15, 1, 0.0, 0.0),
        ('_EnableCanopyColorRamp', 'F', 15, 2, 0.0, 0.0),
        ('_CanopyRampStartAtTop', 'F', 15, 3, 0.0, 0.0),
        ('_CanopyRampRange', 'F', 16, 0, 0.0, 0.0),
        ('_CanopyRampTransitionRange', 'F', 16, 1, 0.01, 0.0),
        ('_CanopyRampIntensity', 'F', 16, 2, 1.0, 0.0),
        ('_CanopyRampColor', 'V4', 17, 0, (1.0, 1.0, 1.0), 1.0),
        ('_CanopyRampColorBrighterScale', 'F', 16, 3, 1.0, 0.0),
        ('_CanopyRampColorCover', 'F', 18, 0, 0.0, 0.0),
        ('_EnableAoTuneColor', 'F', 18, 1, 0.0, 0.0),
        ('_FlipAoMask', 'F', 18, 2, 0.0, 0.0),
        ('_AoMaskTuneColorRampStart', 'F', 18, 3, 0.0, 0.0),
        ('_AoMaskTuneColorRampRange', 'F', 19, 0, 0.2, 0.0),
        ('_AoMaskTuneColorIntensity', 'F', 19, 1, 1.0, 0.0),
        ('_AoMaskTuneColor', 'V4', 20, 0, (1.0, 1.0, 1.0), 1.0),
        ('_AoMaskTuneColorBrighterScale', 'F', 19, 2, 1.0, 0.0),
        ('_AoMaskTuneColorCover', 'F', 19, 3, 0.0, 0.0),
        ('_EnableBlendColor', 'F', 21, 0, 0.0, 0.0),
        ('_BlendWithVertexNormal', 'F', 21, 1, 0.0, 0.0),
        ('_BlendNormalAdd', 'F', 21, 2, 0.0, 0.0),
        ('_BlendColor', 'V4', 22, 0, (1.0, 1.0, 1.0), 1.0),
        ('_BlendNormalPower', 'F', 21, 3, 1.0, 0.0),
        ('_EnableTrunkRamp', 'F', 23, 0, 0.0, 0.0),
        ('_TrunkRampRange', 'F', 23, 1, 0.0, 0.0),
        ('_TrunkRampTransitionRange', 'F', 23, 2, 0.01, 0.0),
        ('_TrunkRampIntensity', 'F', 23, 3, 1.0, 0.0),
        ('_TrunkRampColor', 'V4', 24, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableEmissiveMap', 'F', 25, 0, 0.0, 0.0),
        ('_EmissiveUVSet', 'F', 25, 1, 0.0, 0.0),
        ('_EmissiveMap_ST', 'V4', 26, 0, (1.0, 1.0, 0.0), 0.0),
        ('_EmissiveMaskChannel', 'F', 25, 2, 0.0, 0.0),
        ('_EmissiveColorR', 'V4', 27, 0, (0.0, 0.0, 0.0), 1.0),
        ('_EmissiveColorG', 'V4', 28, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorB', 'V4', 29, 0, (0.0, 0.0, 0.0), 0.0),
        ('_EmissiveColorA', 'V4', 30, 0, (0.0, 0.0, 0.0), 0.0),
        ('_AlbedoAffectEmissive', 'F', 25, 3, 1.0, 0.0),
        ('_EnableVertColorEmissive', 'F', 31, 0, 0.0, 0.0),
        ('_VertColorEmissiveChannelVector', 'V4', 32, 0, (1.0, 0.0, 0.0), 0.0),
        ('_VertColorEmissiveFlip', 'F', 31, 1, 0.0, 0.0),
        ('_VertColorEmissiveBias', 'F', 31, 2, 0.0, 0.0),
        ('_VertColorEmissiveColor', 'V4', 33, 0, (0.0, 0.0, 0.0), 1.0),
        ('_VertColorEmissiveAlbedoAffect', 'F', 31, 3, 1.0, 0.0),
        ('_CrossCardViewCulling', 'F', 34, 0, 0.0, 0.0),
        ('_CrossCardViewCullingThreshold', 'F', 34, 1, 0.4, 0.0),
        ('_CrossCardViewCullingFadeValue', 'F', 34, 2, 0.5, 0.0),
        ('_UseThinFilm', 'F', 34, 3, 0.0, 0.0),
        ('_ThinFilmIOR', 'F', 35, 0, 1.4, 0.0),
        ('_ThinFilmThickness', 'F', 35, 1, 0.5, 0.0),
        ('M_PI', 'F', 35, 2, 0.0, 0.0),
        ('_ThinFilmWeight', 'F', 35, 3, 0.0, 0.0),
        ('_ThinFilmIntensity', 'F', 36, 0, 1.0, 0.0),
        ('_SubsurfaceShadingMode', 'F', 36, 1, 0.0, 0.0),
        ('_SubsurfaceColor', 'V4', 37, 0, (0.8, 0.8, 0.8), 1.0),
        ('_MaxSubsurfaceThickness', 'F', 36, 2, 1.0, 0.0),
        ('_UseSubsurfaceThicknessMap', 'F', 36, 3, 0.0, 0.0),
        ('_MinSubsurfaceThickness', 'F', 38, 0, 0.0, 0.0),
        ('_UseCustomIBL', 'F', 38, 1, 0.0, 0.0),
        ('_CustomIBLIntensity', 'F', 38, 2, 1.0, 0.0),
        ('_PlanarReflection', 'F', 38, 3, 0.0, 0.0),
        ('_PlanarReflectionTint', 'V4', 39, 0, (1.0, 1.0, 1.0), 1.0),
        ('_EnableSubsurface', 'F', 40, 0, 0.0, 0.0),
        ('_SubsurfaceIndirect', 'F', 40, 1, 1.0, 0.0),
        ('_EnvironmentGlobalParams0', 'V4', 41, 0, (1.67, 1.5, 1.0), 0.0),
        ('_MainLightOcclusionProbes', 'V4', 42, 0, (0.0, 0.0, 0.0), 0.0),
        ('_SubsurfaceSelfShadowBias', 'F', 40, 2, 0.0, 0.0),
        ('_SubsurfaceEnableSelfShadowBias', 'F', 40, 3, 0.0, 0.0),
        ('_RuriVoxelSizeMeters', 'F', 43, 0, 0.0, 0.0),
        ('_RuriFogEnvironmentalStart', 'F', 43, 1, 0.0, 0.0),
        ('_RuriFogEnvironmentalEnd', 'F', 43, 2, 0.0, 0.0),
        ('_RuriFogRenderDistanceStart', 'F', 43, 3, 0.0, 0.0),
        ('_RuriFogRenderDistanceEnd', 'F', 44, 0, 0.0, 0.0),
        ('_RuriFogColor', 'V4', 45, 0, (0.0, 0.0, 0.0), 0.0),
    ],
}
MAT_TABLE_H = 69
MAT_TABLE_W = 1024

DEFAULT_PART = 'Lit'
STAMP = 'e2892d86d68c6881'
STAMP_KEY = 'ruri_uber_stamp'


VERTEX_PARTS = {
}

KNOWN_PARTS = {'Lit', 'LitForward', 'LitTransparent', 'LitEffect', 'LitEffectBlend', 'LitHLod', 'Unlit', 'ContainerWater', 'Leaf', 'Grass', 'Trunk'}
MAT_TABLE = 'Ruri Endfield Scene Params'
TEMPLATE_MAT = 'Ruri Endfield Scene Tpl '
VTX_MODIFIER = 'Ruri Endfield Scene Vertex'
VTX_TREE_PREFIX = 'Ruri Endfield Scene Vertex '
OUTLINE_TEMPLATE = 'Ruri Endfield Scene Outline'
CLONE_V_PREFIX = 'Ruri Endfield Scene V '
CLONE_O_PREFIX = 'Ruri Endfield Scene O '
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
        _set_colorspace(image, 'Non-Color' if fetch['non_color'] else 'sRGB')
        if fetch['non_color']:
            _fix_two_channel_layout(image)
    return nd


def _size_row(slot):
    return '__size' + slot


def _mat_size_socket(g, part, fetch, image):
    """显式 LOD 槽的纹素域尺寸,从**本 part 的表列**读(与别的材质参数同一条路)。
    找不到该行(旧表/非表化路径)才退回建图期常量 —— 那是直建路的正确行为,
    因为它拿到的本来就是真图。

    🔴 **texel 索引是逐 part 分配的**,所以必须按 part 查,不能在 PARAMS 里
    按名字撞见谁算谁。曾经写成遍历 PARAMS.values() 取第一个同名行:Face 的
    _ShadowLutTex 于是去读 Standard 的 texel 39(Face 自己在 32),读到空行
    ⇒ size=(0,0,0) ⇒ 手工双线性除以零 ⇒ **整张脸/头发/眉毛纯黑**;而字典里
    第一个 part(Standard)恰好自洽,布料看着一切正常,极具迷惑性。"""
    row = None
    want = _size_row(fetch['slot'])
    for entry in PARAMS.get(part, ()):
        if entry[0] == want:
            row = entry
            break
    if row is None:
        return (float(image.size[0]), float(image.size[1]), 1.0)
    nd = g._nd('ShaderNodeTexImage')
    nd.image = _mat_table_image()
    nd.interpolation = 'Closest'
    nd.extension = 'EXTEND'
    nd.label = 'RuriMatParam'
    g._set(nd.inputs['Vector'], g.comb(_mat_col_u(g), (row[2] + 0.5) / MAT_TABLE_H, 0.0))
    return nd.outputs['Color']


def _sample(g, part, fetch, image, uv):
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
    # 🔴 纹素域尺寸**必须是数据不是常量**:模板按中性占位图(4x4)建,实例化才换真图 ——
    # 把 image.size 烙成建图期常量,换上 2048² 的真图后 UV 纹素域整体错位,
    # 而走这条路的恰是 ramp/LUT 类槽(_DiffRampMap/_ShadowLutTex/_SpecRampMap/
    # _SDFLightmap)⇒ 症状是漫反射/影色整片错色(实测:角色漫反射变深绿)。
    # 尺寸随材质绑的图走,所以它和别的材质参数一样住表列(_mat_size_socket)。
    size = _mat_size_socket(g, part, fetch, image)
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


def _wire_fetch(g, part, insts, fetch, image):
    src = insts[fetch['depth']]
    heads = insts[fetch['depth'] + 1:]
    if fetch['env']:
        if image is None:
            return None
        mip = src.outputs[fetch['sock'] + '_mip'] if fetch['mip'] else None
        color, alpha = g.env_image(image, src.outputs[fetch['sock'] + '_dir'], mip)
        _feed(g, heads, fetch['sock'], color, alpha)
        return None
    color, alpha, anchor = _sample(g, part, fetch, image, src.outputs[fetch['sock'] + '_uv'])
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
        color, alpha, _anchor = _sample(g, part, f, image, src.outputs[f['sock'] + '_uv'])
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


# ==================== 材质参数表(材质 = 数据行) ====================
# 一张材质的全部自有参数 = 共享 fp32 表图的一列像素;真源恒为材质上的 ruri_uber_*
# 快照,列是它的投影(开旧文件/重载会话时逐材质重组,图本身不需要持久化)。
_MAT_MIRROR = None      # numpy (H, W, 4) float32 —— 表像素的权威镜像
_MAT_NEXT_COL = [1]     # 列0 = 缺省列(模板材质自己渲的就是它),材质从列1起
_MAT_FLUSH_QUEUED = [False]


def _mat_table_image():
    image = bpy.data.images.get(MAT_TABLE)
    if image is None or tuple(image.size) != (MAT_TABLE_W, MAT_TABLE_H) or not image.is_float:
        stale = image
        image = bpy.data.images.new(MAT_TABLE, MAT_TABLE_W, MAT_TABLE_H,
                                    float_buffer=True, alpha=True)
        _set_colorspace(image, 'Non-Color')
        # 🔴 必须 CHANNEL_PACKED:默认 STRAIGHT 会拿 alpha 去关联 RGB,而这里的
        # alpha 是**第四个参数值**不是不透明度 —— V4 行的 w(如 _CharacterParams6_w
        # 缺省 0)会把整个 rgb 毁掉,F 打包行更毒(第 4 个 F 污染前三个)。
        # 症状:角色漫反射整片变深绿(CP6 缺省恰是 (0,1,0))。
        image.alpha_mode = 'CHANNEL_PACKED'
        image.use_fake_user = True
        if stale is not None:
            stale.user_remap(image)   # 旧尺寸的表被拷贝们的图节点攥着——换血不换指针语义
            bpy.data.images.remove(stale)
            image.name = MAT_TABLE
    return image


def _mat_defaults(part):
    """一列声明缺省(生成期从真源 Properties 反射而来;列0 常驻这份)。"""
    import numpy as np
    column = np.zeros((MAT_TABLE_H, 4), dtype=np.float32)
    for name, kind, texel, comp, dvec, dw in PARAMS.get(part, ()):
        if kind == 'F':
            column[texel, comp] = dvec
        else:
            column[texel, 0:3] = dvec
            column[texel, 3] = dw
    return column


def _mat_compose(part, floats, st, colors):
    """材质快照 → 一列像素:缺省打底,声明值覆写。raw 直灌语义原样保留 ——
    行名 = 游戏属性名,_ST 后缀 = 平铺偏移,零映射表。"""
    column = _mat_defaults(part)
    merged = dict(floats or {})
    for prop, value in (st or {}).items():
        merged[prop + '_ST'] = value
    merged.update(colors or {})
    for name, kind, texel, comp, _dv, _dw in PARAMS.get(part, ()):
        value = merged.get(name)
        if value is None:
            continue
        if kind == 'F':
            try:
                column[texel, comp] = float(value)
            except (TypeError, ValueError):
                pass
        else:
            vec = [float(x) for x in value] + [0.0] * 4 if hasattr(value, '__len__') \
                else [float(value)] * 3 + [0.0]
            column[texel, 0:3] = vec[0:3]
            column[texel, 3] = vec[3]
    return column


def _mat_mirror():
    """镜像惰性建立:会话冷启/开旧文件时按材质快照逐列重组 —— 真源在材质,
    列号在 mat['ruri_param_col'],谁在场谁的列就有效。"""
    global _MAT_MIRROR
    if _MAT_MIRROR is not None:
        return _MAT_MIRROR
    import numpy as np
    _MAT_MIRROR = np.zeros((MAT_TABLE_H, MAT_TABLE_W, 4), dtype=np.float32)
    top = 0
    for mat in bpy.data.materials:
        if mat.get('ruri_uber_stack') != PANEL_KEY or mat.get('ruri_param_col') is None:
            continue
        col = int(mat['ruri_param_col'])
        if not (0 < col < MAT_TABLE_W):
            continue
        top = max(top, col)
        part = mat.get('ruri_uber_part', '')
        _MAT_MIRROR[:, col, :] = _mat_compose(
            part, dict(mat.get('ruri_uber_floats') or {}),
            {k: list(v) for k, v in dict(mat.get('ruri_uber_st') or {}).items()},
            {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()})
    _MAT_NEXT_COL[0] = max(_MAT_NEXT_COL[0], top + 1)
    for part in PARAMS:
        _MAT_MIRROR[:, 0, :] = _mat_defaults(part)   # 列0:任一 part 的缺省(模板渲染用)
        break
    return _MAT_MIRROR


def _param_flush():
    _MAT_FLUSH_QUEUED[0] = False
    image = _mat_table_image()
    image.pixels.foreach_set(_mat_mirror().ravel())
    _image_uploaded(image)   # 见 prelude:update/update_tag 都不够,要 gl_free
    _image_stored(image)     # 清 dirty:否则关文件弹「N 张图片未保存」
    return None      # timer 协议:None = 注销


def _param_flush_soon():
    """批量导入几百材质 = 几百次列写;整图 foreach_set 按帧去抖成一次。
    headless 没有事件循环(timers 永不触发)⇒ 直接刷。"""
    if bpy.app.background:
        _param_flush()
        return
    if not _MAT_FLUSH_QUEUED[0]:
        _MAT_FLUSH_QUEUED[0] = True
        bpy.app.timers.register(_param_flush, first_interval=0.1)


def _param_write(mat):
    """材质的 ruri_uber_* 快照 → 它的表列。分配列号(首次)并返回。"""
    part = mat.get('ruri_uber_part', '')
    mirror = _mat_mirror()
    col = mat.get('ruri_param_col')
    if col is None:
        col = _MAT_NEXT_COL[0]
        if col >= MAT_TABLE_W:
            raise RuntimeError('[ruri-uber] 材质表 {0} 列耗尽——同栈材质超容量'.format(MAT_TABLE_W))
        _MAT_NEXT_COL[0] = col + 1
        mat['ruri_param_col'] = col
    col = int(col)
    mirror[:, col, :] = _mat_compose(
        part, dict(mat.get('ruri_uber_floats') or {}),
        {k: list(v) for k, v in dict(mat.get('ruri_uber_st') or {}).items()},
        {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()})
    _param_flush_soon()
    return col


def _param_read(mat, name):
    """面板回读兜底:镜像列里的当前值(缺省已在列里)。F 返回标量,V 返回 [x,y,z,w]。"""
    col = mat.get('ruri_param_col')
    if col is None:
        return None
    mirror = _mat_mirror()
    for row_name, kind, texel, comp, _dv, _dw in PARAMS.get(mat.get('ruri_uber_part', ''), ()):
        if row_name != name:
            continue
        cell = mirror[texel, int(col)]
        if kind == 'F':
            return float(cell[comp])
        return [float(cell[0]), float(cell[1]), float(cell[2]), float(cell[3])]
    return None


def _mat_col_u(g):
    """本材质在参数表里的列 → U 坐标。列号住图里唯一的 RuriMatCol 值节点上
    (CSE 让全树共用同一个),实例化时改它一处即整棵图改读自己的列。"""
    hit = g._cse.get(('matcolu',))
    if hit is not None:
        return hit
    colv = g._nd('ShaderNodeValue')
    colv.label = 'RuriMatCol'
    colv.outputs[0].default_value = 0.0
    u = g.math('DIVIDE', g.math('ADD', colv.outputs[0], 0.5), float(MAT_TABLE_W))
    g._cse[('matcolu',)] = u
    return u


def _wire_params(g, insts, part):
    """把本 part 的全部材质 uniform 从表列接进级联/循环体实例 —— 模板建图时跑一次。
    从此「一张材质的参数」= 表里的一列像素:实例化零 socket 写,面板改参零树更新。
    列号住在 RuriMatCol 值节点上,是拷贝里唯一逐材质的图内状态。
    已链接的 socket(varying/fetch/能力答案)天然跳过 —— 顺序上本函数最后跑。"""
    rows = PARAMS.get(part, ())
    if not rows:
        return
    image = _mat_table_image()
    _param_flush_soon()          # 列0 缺省得先在像素里,模板渲染才不是全零
    u = _mat_col_u(g)
    texels = {}
    seps = {}
    for name, kind, texel, comp, _dv, _dw in rows:
        nd = texels.get(texel)
        if nd is None:
            nd = g._nd('ShaderNodeTexImage')
            nd.image = image
            nd.interpolation = 'Closest'
            nd.extension = 'EXTEND'
            nd.label = 'RuriMatParam'
            g._set(nd.inputs['Vector'], g.comb(u, (texel + 0.5) / MAT_TABLE_H, 0.0))
            texels[texel] = nd
        if kind == 'F' and comp < 3:
            parts3 = seps.get(texel)
            if parts3 is None:
                parts3 = seps[texel] = g.sep(nd.outputs['Color'])
            value = parts3[comp]
        elif kind == 'F':
            value = nd.outputs['Alpha']
        else:
            value = nd.outputs['Color']
        for inst in insts:
            sock = inst.inputs.get(name)
            if sock is not None and not sock.is_linked:
                g._set(sock, value)
            if kind == 'V4':
                tail = inst.inputs.get(name + '_w')
                if tail is not None and not tail.is_linked:
                    g._set(tail, nd.outputs['Alpha'])


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
        nd = _wire_fetch(g, part, insts, f, image)
        if nd is not None and nd.image is not None:
            if anchor is None or (f['slot'] in _ANCHOR_SLOTS and (anchor.label not in _ANCHOR_SLOTS)):
                anchor = nd
    for c in CAPABILITIES.get(part, ()):
        _wire_capability(g, insts, c, {'material': mat})
    for z in ZONES.get(part, ()):
        _wire_zone(g, insts, z, images, all_insts, part, {'material': mat})
    # 材质 uniform 最后接:此刻 varying/fetch/能力答案已全部占线,剩下的
    # 未链接 socket 恰是 PARAMS 的行 —— 全部改从表列读(材质 = 数据行)。
    _wire_params(g, all_insts, part)
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


# ==================== 模板材质 + 实例化(唯一的逐材质路径) ====================
_TEMPLATE_KEY = 'ruri_uber_template'


_NEUTRAL_IMAGES = {}


def _neutral_image(rgb, alpha, non_color):
    """一张 1x1 的图,颜色 = **该割点自己声明的 neutral**(缺图时的采样值)。
    按 (颜色, alpha, 色彩空间) 去重,全局就几张。"""
    key = (tuple(round(float(c), 6) for c in rgb), round(float(alpha), 6), bool(non_color))
    img = _NEUTRAL_IMAGES.get(key)
    if img is not None:
        try:
            img.name
            return img
        except ReferenceError:
            pass
    name = 'RuriNeutral_{0:.3f}_{1:.3f}_{2:.3f}_{3:.3f}_{4}'.format(
        key[0][0], key[0][1], key[0][2], key[1], 'nc' if non_color else 'srgb')
    img = bpy.data.images.get(name)
    if img is None or tuple(img.size) != (1, 1):
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(name, 1, 1, float_buffer=True, alpha=True)
    _set_colorspace(img, 'Non-Color' if non_color else 'sRGB')
    img.alpha_mode = 'CHANNEL_PACKED'
    img.generated_color = (
        key[0][0] if non_color else _linear_to_srgb(key[0][0]),
        key[0][1] if non_color else _linear_to_srgb(key[0][1]),
        key[0][2] if non_color else _linear_to_srgb(key[0][2]),
        key[1])
    img.update()
    img['ruri_placeholder'] = 1
    img.use_fake_user = True
    _NEUTRAL_IMAGES[key] = img
    return img


def _template_images(part):
    """模板必须建满全部采样槽(否则 instantiate 换图无处可换),而没绑真图的槽
    **必须与直建路逐位等价** —— 直建路在缺图时根本不建采样节点,socket 停在
    割点声明的 neutral 上。所以这里造的是「颜色 == 该割点 neutral」的 1x1 图,
    不是 _images() 的槽占位图。

    🔴 两套「中性」不是一回事:占位图的颜色是 [MaterialTexture(Default)] 的**槽语义**
    (_SDFLightmap 是纯黑 (0,0,0,0)),而割点的 neutral 是**缺图时该采到什么**。
    拿前者建模板,没绑 SDF 图的材质就真的采到 0 ⇒ 脸部 SDF 判定全黑,
    皮肤与头发整片变黑(实测症状)。

    1x1 也让 __size 行的缺省 (1,1,1) 天然自洽:没换真图时纹素域就是 1x1。"""
    rows = []
    for fetch in FETCHES.get(part, ()):
        if not fetch['env']:
            rows.append(fetch)
    for zone in ZONES.get(part, ()):
        for fetch in zone['fetches']:
            if not fetch['env']:
                rows.append(fetch)
    out = {}
    for fetch in rows:
        if fetch['slot'] in out:
            continue
        out[fetch['slot']] = _neutral_image(
            fetch['neutral'], fetch['neutral_alpha'], fetch['non_color'])
    return out


def _template(part, opaque, multiply_blend, cull):
    """每 (part, 透明形态, 剔除) 一张模板材质,整棵图只建这一次:级联、循环、割点、
    兑现面、参数表接线、**全部采样槽的占位图节点**全在这里付清 ——
    之后每张材质 = copy + 换图指针 + 一列像素。
    模板列号恒 0(缺省列),自己渲染出来就是声明缺省的样子。"""
    name = '{0}{1} {2}{3} c{4:g}'.format(TEMPLATE_MAT, part,
                                         int(bool(opaque)), int(bool(multiply_blend)), float(cull))
    tpl = bpy.data.materials.get(name)
    if tpl is not None and tpl.get(STAMP_KEY) == STAMP and tpl.node_tree is not None:
        return tpl
    stale = tpl
    if stale is not None:
        stale.name = name + '.old'
    tpl = bpy.data.materials.new(name)
    if tpl.node_tree is None:
        tpl.use_nodes = True
    build_material(tpl, part=part, opaque=opaque, multiply_blend=multiply_blend,
                   cull=cull, images=_template_images(part))
    tpl[STAMP_KEY] = STAMP
    tpl[_TEMPLATE_KEY] = 1
    tpl.use_fake_user = True
    if stale is not None:
        stale.user_remap(tpl)   # 旧模板的拷贝不受影响(各自独立树);只挪引用者
        bpy.data.materials.remove(stale)
        tpl.name = name
    return tpl


def instantiate(name, part, images=None, opaque=True, multiply_blend=False, cull=2.0):
    """一张材质 = 模板拷贝 + 贴图指针 + (调用方随后写的)一列像素。零建图、
    零逐 socket 灌参 —— build_material 从此只服务模板与探针。返回 (材质, 换图数)。"""
    tpl = _template(part, opaque, multiply_blend, cull)
    mat = tpl.copy()
    mat.name = name
    mat.use_fake_user = False
    if mat.get(_TEMPLATE_KEY) is not None:
        del mat[_TEMPLATE_KEY]
    swapped = 0
    if images and mat.node_tree is not None:
        for nd in mat.node_tree.nodes:
            if nd.type != 'TEX_IMAGE':
                continue
            real = images.get(_slot_of(nd.label or ''))
            if real is not None:
                _swap_image(nd, real)
                swapped += 1
    # 显式 LOD 槽的纹素域尺寸随真图走(模板是按占位图建的)——落进快照,
    # 由 _param_write 一并进列。漏了它 ramp/LUT 的 UV 就整体错位。
    sizes = {}
    for name, kind, _t, _c, _dv, _dw in PARAMS.get(part, ()):
        if not name.startswith('__size'):
            continue
        real = (images or {}).get(name[len('__size'):])
        if real is not None and real.size[0] and real.size[1]:
            sizes[name] = [float(real.size[0]), float(real.size[1]), 1.0, 0.0]
    if sizes:
        merged = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()}
        merged.update(sizes)
        mat['ruri_uber_colors'] = merged
    return mat, swapped


def build_root(part=None):
    mat = bpy.data.materials.get('Ruri_EndfieldScene_Uber')
    if mat is None:
        mat = bpy.data.materials.new('Ruri_EndfieldScene_Uber')
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
                _set_colorspace(real, 'Non-Color' if non_color else 'sRGB')
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
    'Lit': {'id': 0, 'transparent': False, 'shader': 'HGRP/Lit', 'aliases': (), 'discriminator': None},
    'LitForward': {'id': 1, 'transparent': False, 'shader': 'HGRP/LitForward', 'aliases': (), 'discriminator': None},
    'LitTransparent': {'id': 2, 'transparent': True, 'shader': 'HGRP/LitTransparent', 'aliases': (), 'discriminator': None},
    'LitEffect': {'id': 3, 'transparent': True, 'shader': 'HGRP/LitEffect', 'aliases': (), 'discriminator': None},
    'LitEffectBlend': {'id': 4, 'transparent': True, 'shader': 'Hidden/HGRP/LitEffectBlend', 'aliases': (), 'discriminator': None},
    'LitHLod': {'id': 5, 'transparent': False, 'shader': 'HGRP/LitHLOD', 'aliases': (), 'discriminator': None},
    'Unlit': {'id': 6, 'transparent': False, 'shader': 'HGRP/Unlit', 'aliases': ('HGRP/UnlitSubShaderLOD', ), 'discriminator': None},
    'ContainerWater': {'id': 7, 'transparent': True, 'shader': 'HGRP/Effect/VFXContainerWater', 'aliases': (), 'discriminator': None},
    'Leaf': {'id': 8, 'transparent': False, 'shader': 'HGRP/Foliage/Leaf', 'aliases': ('HGRP/Foliage/TreeFoliageCardMeshLod', 'HGRP/Foliage/TreeFoliageBillboardLod', ), 'discriminator': None},
    'Grass': {'id': 9, 'transparent': False, 'shader': 'HGRP/Foliage/Grass', 'aliases': ('HGRP/Foliage/Grass Cardmesh Lod', 'HGRP/Foliage/Grass Billboard Lod', ), 'discriminator': None},
    'Trunk': {'id': 10, 'transparent': False, 'shader': 'HGRP/Foliage/Trunk', 'aliases': (), 'discriminator': None},
}
NON_SHADING_SHADERS = ('HGRP/LitDepthOnly', 'HGRP/LitShadowCaster', 'HGRP/Foliage/FoliageOccluder', 'HGRP/Foliage/FoliageInteractiveCollider', )

CULL_PROPERTY = '_CullMode'
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
    是唯一真源)。双向赋值必须:只单向强制 Non-Color,老会话里被错标过的图永远弹不回 sRGB。

    🔴 **色彩空间必须先比较再写**:写它会让该 image 的全部使用者失效,而一张贴图
    被 N 张材质共用 —— 无条件写就是每换一张材质失效前面所有张,总体 O(N²)。
    实测 300 张材质的场景窗口:无条件写 1975ms/张,比较后写 …… 差两个数量级。
    (早先'同值重写只要 19µs'的实测是单材质场景,掩盖了使用者数量这一维。)"""
    non_color = node.image is not None and node.image.colorspace_settings.name == 'Non-Color'
    node.image = real
    _set_colorspace(real, 'Non-Color' if non_color else 'sRGB')
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
            if img.alpha_mode != 'CHANNEL_PACKED':   # 同 _swap_image:写它会失效全部使用者
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
    name = props.name or 'Ruri_EndfieldScene_Uber'
    images = _load_images(builder, props)
    if _standard_view_transform(bpy.context.scene):
        print('[ruri-uber] view transform -> Standard(图自带游戏 tonemap)', flush=True)
    # 不透明与否是**游戏自己的规则**:_SurfaceType==1 才吃 alpha,否则输出 alpha 恒 1
    #   (真源 characternpr_eye Fragment:1014;skin 更直接 _3324.w = 1.0 硬写)。
    #   那条 gBuffer0.w 在真管线是 materialFlags 不是不透明度 —— 接成不透明度会让
    #   皮肤/眼睛整个隐形。透明 part 由 [StylePart(Transparent)] 定,是风格自己的事实。
    opaque = props.floats.get('_SurfaceType', 0.0) < 0.5 and not meta['transparent']
    # 材质 = 数据行:模板拷贝 + 贴图指针 + 一列像素。逐 socket 灌参(整棵图从零建 +
    # 798 次 put)已整删 —— 那条路把 300 材质的场景窗口拖成 8 分钟,而一张材质的
    # 真实信息量只有十个图指针加几百字节参数。
    mat, swapped = instantiate(name, part_name, images=images, opaque=opaque,
                               multiply_blend=meta['transparent'] and part_name == 'OverlayShadow',
                               cull=_cull_mode(props))
    # 🔴 同名旧材质**只改名让位,不删**。删了会让宿主 MaterialBuilder 的两层缓存
    # (guid → mat、内容摘要 → mat)攥着已释放的数据块 —— 同一次导入里下一个
    # 命中缓存的网格拿到悬垂指针,`materials.append` 抛
    # `ReferenceError: StructRNA of type Material has been removed`。
    # 让位后没有使用者的旧材质会被 Blender 自己回收(不设 fake user),
    # 还在被别的网格用的那份则原样活着 —— 两种情况都对。
    stale = bpy.data.materials.get(name)
    if stale is not None and stale is not mat:
        stale.name = name + '.old'
        mat.name = name
    bst = props.texture_st.get('_BaseMap') or [1.0, 1.0, 0.0, 0.0]
    for node in mat.node_tree.nodes:
        if node.label == 'RuriBaseMapST':
            node.inputs['Scale'].default_value = (float(bst[0]), float(bst[1]), 1.0)
            node.inputs['Location'].default_value = (float(bst[2]), float(bst[3]), 0.0)
    try:
        mat.surface_render_method = 'BLENDED' if meta['transparent'] else 'DITHERED'
    except Exception:
        pass
    mat['ruri_uber_part'] = part_name
    # 栈身份:一个会话里同时装着 N 个生成栈,而 ruri_uber_part 每个栈都写 ——
    # 材质面板要认领得准(只画选中材质那一个栈的参数),判据只能是这一枚烙印。
    mat['ruri_uber_stack'] = PANEL_KEY
    # 快照 = 参数唯一真源(表列/顶点腿/面板全部从这里投影)。透明特效 part 的游戏
    # shader 靠 pass 状态透明、不声明 _SurfaceType —— 内核开关折进快照本身,
    # 冷启重组列时才不会丢(曾是 put() 的旁路补写,快照里没有)。
    snapshot_floats = {k: float(v) for k, v in props.floats.items()}
    if meta['transparent']:
        snapshot_floats['_SurfaceType'] = 1.0
    mat['ruri_uber_images'] = {k: v.name for k, v in images.items()}
    mat['ruri_uber_floats'] = snapshot_floats
    mat['ruri_uber_st'] = {k: [float(x) for x in v] for k, v in props.texture_st.items()}
    # instantiate 已把显式 LOD 槽的真图尺寸写进 colors 快照(__size*),
    # 这里是**合并**不是覆盖 —— 直接赋值会把它们连同 UV 纹素域一起抹掉。
    snapshot_colors = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()
                       if k.startswith('__size')}
    snapshot_colors.update({k: [float(x) for x in v] for k, v in props.colors.items()})
    mat['ruri_uber_colors'] = snapshot_colors
    mat['ruri_uber_disabled_passes'] = list(getattr(props, 'disabled_passes', ()))
    ref = props.shader_ref
    mat['ruri_uber_shader_guid'] = str(ref.get('guid', '')) if isinstance(ref, dict) else ''
    mat['ruri_uber_shader'] = _shader_name(builder, props) or ''
    col = _param_write(mat)
    for node in mat.node_tree.nodes:
        if node.label == 'RuriMatCol':
            node.outputs[0].default_value = float(col)
            break
    print('[ruri-uber] {0}: shader={1} part={2} images={3} col={4}'.format(
        name, mat['ruri_uber_shader'], part_name, swapped, col), flush=True)
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


# ============================ 材质参数接口 ============================
# 参数面 = 从 C# 声明([ShaderProperty]/[MaterialTexture]/[ShaderPropertyHeader])反射派生的
# **接口**,不是对节点树的遍历——平坦、分组、带量程、带功能门。此表是它的逐字投影。
PANEL_KEY = 'ruri_scene_uber_endfield'
PANEL_TITLE = 'Ruri_EndfieldScene_Uber 参数'
INTERFACE = [
    {'name': '基础', 'gate': None, 'rows': [
        {'name': '_RefractTex', 'label': '自定义折射贴图', 'kind': 'TEXTURE'},
        {'name': '_WaterNormalMap', 'label': '水面法线贴图', 'kind': 'TEXTURE'},
        {'name': '_WaterCausticMap', 'label': '水波纹贴图', 'kind': 'TEXTURE'},
        {'name': '_DisplacementTex', 'label': '置换贴图', 'kind': 'TEXTURE'},
        {'name': '_IceNormalMap', 'label': '冰块法线贴图', 'kind': 'TEXTURE'},
        {'name': '_IceOpacityMap', 'label': '冰块不透明度贴图', 'kind': 'TEXTURE'},
        {'name': '_BaseMap', 'label': 'Albedo', 'kind': 'TEXTURE', 'st_node': 'RuriBaseMapST'},
        {'name': '_BaseColorMap', 'label': 'Base Color Map', 'kind': 'TEXTURE'},
        {'name': '_BumpMap', 'label': 'Normal Map', 'kind': 'TEXTURE'},
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
        {'name': '_BaseTex', 'label': 'Base Tex', 'kind': 'TEXTURE'},
        {'name': '_DissolveTex', 'label': 'Dissolve Tex', 'kind': 'TEXTURE'},
        {'name': '_ScenePartID', 'label': 'Scene Part (0=Lit 1=Forward 2=Transparent 3=Effect 4=EffectBlend 5=HLod 6=Unlit)', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_BaseColor', 'label': 'Color', 'kind': 'COLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
        {'name': '_BumpScale', 'label': 'Normal Scale', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_RoughnessIntensity', 'label': 'Roughness Intensity', 'kind': 'VALUE', 'size': 1, 'default': [0.5]},
        {'name': '_MetallicIntensity', 'label': 'Metallic Intensity', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_OcclusionIntensity', 'label': 'Occlusion Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_SpecularIntensity', 'label': 'Specular Intensity', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
        {'name': '_UseCutoff', 'label': 'UseCutoff', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_Cutoff', 'label': 'Alpha Cutoff', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [0.5]},
        {'name': '_UseReceiveShadows', 'label': 'Allow Receive Shadows', 'kind': 'SWITCH', 'size': 1, 'default': [1.0]},
        {'name': '_UseMaskMap', 'label': 'Use Mask Map', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_EmissiveIntensity', 'label': 'Emissive Intensity', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 20.0, 'default': [0.0]},
        {'name': '_UseDitherClip', 'label': 'UseDitherClip', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_DitherAlpha', 'label': 'Dither Alpha Value', 'kind': 'SLIDER', 'size': 1, 'min': 0.0, 'max': 1.0, 'default': [1.0]},
        {'name': '_TessellationFactor', 'label': 'Tessellation Factor', 'kind': 'SLIDER', 'size': 1, 'min': 1.0, 'max': 64.0, 'default': [16.0]},
        {'name': '_TessellationMinDist', 'label': 'Min Distance', 'kind': 'VALUE', 'size': 1, 'default': [10.0]},
        {'name': '_TessellationMaxDist', 'label': 'Max Distance', 'kind': 'VALUE', 'size': 1, 'default': [50.0]},
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
        {'name': '_UseVertexColor', 'label': 'Use Vertex Color (Voxel Albedo)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_UseVoxelAtlas', 'label': 'Use Voxel Atlas (Block Textures)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_VoxelEmissionScale', 'label': 'Voxel Emission Scale', 'kind': 'VALUE', 'size': 1, 'default': [4.0]},
        {'name': '_RuriHeldRadiance', 'label': 'Held Item Radiance (Eye Lightmap)', 'kind': 'SWITCH', 'size': 1, 'default': [0.0]},
        {'name': '_RuriHeldLightLevels', 'label': 'Held Eye Light Levels (Block, Sky)', 'kind': 'VECTOR', 'size': 4, 'default': [0.0, 15.0, 0.0, 0.0]},
        {'name': '_EffectPartID', 'label': 'Effect Part ID', 'kind': 'VALUE', 'size': 1, 'default': [0.0]},
        {'name': '_SurfaceType', 'label': 'Surface Type', 'kind': 'VALUE', 'size': 1, 'default': [1.0]},
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
        {'name': '_FresnelColor', 'label': '菲涅尔颜色', 'kind': 'HDRCOLOR', 'size': 4, 'default': [1.0, 1.0, 1.0, 1.0]},
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
    """面板值 → 快照字典(真源)→ 表列像素 + 顶点腿克隆输入,一次到位。
    着色面的 socket 已被表列占线(is_linked 天然跳过)—— 改参 = 改像素,
    零树更新:这是拖杆不再卡顿的全部原因。"""
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
        _param_write(mat)
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
        _param_write(mat)
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
    # 显式 LOD 槽的纹素域尺寸随这张图走(见 _mat_size_socket):换图不写它,
    # ramp/LUT 的 UV 就停在上一张图的尺寸上,整片错色。
    size_row = _size_row(name)
    if any(r[0] == size_row for r in PARAMS.get(mat.get('ruri_uber_part', ''), ())):
        cols = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()}
        src = target if target is not None else None
        if src is not None and src.size[0] and src.size[1]:
            cols[size_row] = [float(src.size[0]), float(src.size[1]), 1.0, 0.0]
        else:
            cols.pop(size_row, None)
        mat['ruri_uber_colors'] = cols
        _param_write(mat)

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
                _set_colorspace(target, 'Non-Color' if non_color else 'sRGB')
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
    _param_write(mat)
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
                value = _param_read(mat, name + '_ST')   # 表列(缺省已在列里)
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
            if value is None:
                value = _param_read(mat, name)   # 表列(缺省已在列里)
                if isinstance(value, list):
                    value = value[0]
            if value is None and sock is not None and not sock.is_linked and sock.type == 'VALUE':
                value = sock.default_value
            if value is None:
                continue
            values[name] = (bool(value > 0.5) if kind == 'SWITCH'
                            else int(value) if kind == 'INT' else float(value))
        else:
            value = colors.get(name)
            if value is None:
                value = _param_read(mat, name)   # 表列(缺省已在列里)
                if isinstance(value, float):
                    value = None
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


@bpy.app.handlers.persistent
def _restore_projections(_path=None):
    """开文件后把所有生成图按真源重算。generated 图的像素不进 .blend,
    重开一律是 generated_color(黑)—— 不重放就是静默全黑/全零。"""
    global _MAT_MIRROR
    _NEUTRAL_IMAGES.clear()   # 旧会话的数据块已失效,按名重新认领并重写像素
    for part in PARTS:
        _template_images(part)
    _MAT_MIRROR = None        # 强制按在场材质的快照逐列重组
    _mat_mirror()
    _param_flush()
    refresh_light_tables()


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
    if _restore_projections not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_restore_projections)


def unregister():
    import importlib
    import sys
    host = importlib.import_module('RuriRipperImporter.material_builder')
    host.unregister_material_panel(sys.modules[__name__])
    host.unregister_graph_provider(provider)
    host.unregister_vertex_stage(apply_vertex_stage)
    host.unregister_capability_rewire(rewire_capabilities)
    host.unregister_light_table_refresh(refresh_light_tables)
    if _restore_projections in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restore_projections)


# 导入即清理过时预设库(见 _prune_stale_libraries):要在 register() 之外,
# 因为脱包直接加载本文件的探针也该把目录扫干净。
_prune_stale_libraries()
if __name__ == '__main__':
    build_root()
