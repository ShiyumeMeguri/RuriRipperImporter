# =============================================================================
# Ruri Endfield Post —— 合成器后处理(生成物,勿手改)
# 真源 = CharacterEndfield.BlenderTonemap_Endfield,经 Ruri.CodeGen.Blender 编成合成器节点组。
#
# 装配契约(Blender 5.2 真渲染实测):渲染结果只能由组内 CompositorNodeRLayers 取,
# 出口走 NodeGroupOutput;喂组输入 socket 拿到的是全 0(静默失效)。
# 视口实时预览 = 3D 视图着色弹窗的 Compositor 开关(DISABLED/CAMERA/ALWAYS)。
# =============================================================================

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


def _light_record(g, light):
    """一盏灯的四个叶子,全部原生取自灯物体本身(驱动器绑活值,转灯换色实时跟)。

    SUN 是平行光:方向恒为灯的 +Z 轴,无距离衰减。
    POINT/SPOT/AREA 的方向随着色点变,按 归一化(灯位 − 着色点) 算;距离衰减取
    **平方反比** —— 那正是 Blender 自己的灯遵循的落衰,不是自造公式(URP 那条额外的
    range 窗口在 Blender 没有对应物:灯没有「范围」这个属性,故不编一个出来)。"""
    tint = g._nd('ShaderNodeCombineXYZ')
    tint.label = 'RuriLightColor_' + light.name
    for i in range(3):
        _drive(tint.inputs[i], light, 'data.color[%d]' % i, expr='v * e', extra=('e', 'data.energy'))
    if light.data.type == 'SUN':
        to_light = _light_axis(g, light, 'RuriLightAxis_' + light.name)
        attenuation = 1.0
    else:
        to_light = g.vmath('SUBTRACT', _light_pos(g, light, 'RuriLightPos_' + light.name),
                           g.geo().outputs['Position'])
        distance2 = g.vmath('DOT_PRODUCT', to_light, to_light)
        attenuation = g.math('DIVIDE', 1.0, g.math('MAXIMUM', distance2, 1e-4))
        if light.data.type == 'SPOT':
            # 聚光锥 = Blender 自己的语义:spot_size 全角、spot_blend 内缘软化,
            # cos 经驱动器算在灯数据上(动灯即时跟),锥内外过渡用 smoothstep(与 Cycles 同式)。
            axis = g.vmath('NORMALIZE', _light_axis(g, light, 'RuriSpotAxis_' + light.name))
            cone = g._nd('ShaderNodeCombineXYZ')
            cone.label = 'RuriSpotCone_' + light.name
            _drive(cone.inputs[0], light, 'data.spot_size', expr='cos(v / 2)')
            _drive(cone.inputs[1], light, 'data.spot_size', expr='cos(v * (1 - e) / 2)',
                   extra=('e', 'data.spot_blend'))
            cos_outer, cos_inner, _cz = g.sep(cone.outputs[0])
            cone_cos = g.vmath('DOT_PRODUCT', g.vmath('NORMALIZE', to_light), axis)
            t = g.clampn(g.math('DIVIDE', g.math('SUBTRACT', cone_cos, cos_outer),
                                g.math('MAXIMUM', g.math('SUBTRACT', cos_inner, cos_outer), 1e-4)))
            smooth = g.math('MULTIPLY', g.math('MULTIPLY', t, t),
                            g.math('SUBTRACT', 3.0, g.math('MULTIPLY', 2.0, t)))
            attenuation = g.math('MULTIPLY', attenuation, smooth)
    return {
        'direction': g.b2u(g.vmath('NORMALIZE', to_light)),
        'color': tint.outputs[0],
        'distanceAttenuation': attenuation,
        'shadowAttenuation': 1.0,
        'layerMask': 1.0,
    }


def _cap_additional_light(g, query, ctx):
    """附加光的**元素列表**(集合能力,Arity 槽)。

    按什么下标选哪盏是**数学面**的事:选择链生成在共享树里,repeat zone 内逐迭代取对 ——
    这里只回答「第 k 盏是谁」,一句选择逻辑都没有。旧的兑现面 select 链已删:
    它在循环里必然全错(下标是 zone 内迭代变量,导出到组接口只剩最后一圈的值,
    每圈都会选到同一盏灯)。超出槽位的灯不发光,装配器如实打印。"""
    _ = query
    return [_light_record(g, light) for light in _additional_lights(ctx.get('material'))]


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

    def env(self, name, direction, default, mip=None):
        raise RuntimeError('几何树无环境采样(顶点腿不该走到 IBL)')

    def group(self, name, ins):
        raise RuntimeError('顶点树不引用共享子组(全内联编译)')

    def group_named(self, name, pairs):
        raise RuntimeError('顶点树不引用共享子组(全内联编译)')


TREE_KIND = 'CompositorNodeTree'


def build_Ruri_Endfield_Post():
    t = _tree('Ruri Endfield Post')
    g = G(t)
    v0 = g.inp('color', True)
    v1 = g.vmath('DOT_PRODUCT', v0, (0.6130973255536435, 0.3395228813214228, 0.0473793330068586))
    v2 = g.vmath('DOT_PRODUCT', v0, (0.0701942176296659, 0.9163555605787149, 0.013452343829894))
    v3 = g.vmath('DOT_PRODUCT', v0, (0.0206156004863253, 0.1095698373575739, 0.8698151534347436))
    v4 = g.comb(v1, v2, v3)
    v5 = g.vmath('DOT_PRODUCT', v4, (0.2722289860248566, 0.6740819811820984, 0.05368949845433235))
    v6 = g.math('SUBTRACT', v5, 0.5)
    v7 = g.math('MULTIPLY', v6, 0.6666666865348816)
    v8 = g.clampn(v7)
    v9 = g.vmath('MULTIPLY', v4, (2.7850849628448486, 2.7850849628448486, 2.7850849628448486))
    v10 = g.vmath('ADD', v9, (0.10777200013399124, 0.10777200013399124, 0.10777200013399124))
    v11 = g.vmath('MULTIPLY', v4, v10)
    v12 = g.vmath('MULTIPLY', v4, (2.936044931411743, 2.936044931411743, 2.936044931411743))
    v13 = g.vmath('ADD', v12, (0.8871219754219055, 0.8871219754219055, 0.8871219754219055))
    v14 = g.vmath('MULTIPLY', v4, v13)
    v15 = g.vmath('ADD', v14, (0.806888997554779, 0.806888997554779, 0.806888997554779))
    v16 = g.vmath('DIVIDE', (1, 1, 1), v15)
    v17 = g.vmath('MAXIMUM', v16, (9.999999747378752E-05, 9.999999747378752E-05, 9.999999747378752E-05))
    v18 = g.vmath('MULTIPLY', v17, v11)
    v19 = g.vmath('MINIMUM', v18, (1, 1, 1))
    v20 = g.vmath('DOT_PRODUCT', v19, (0.2722289860248566, 0.6740819811820984, 0.05368949845433235))
    v21 = g.comb(v20, v20, v20)
    v22 = g.mixv(0.9300000071525574, v21, v19)
    v23 = g.math('MULTIPLY', 0.6217907071113586, -1.0)
    v24 = g.math('MULTIPLY', 0.083258680999279, -1.0)
    v25 = g.comb(1.7050515413284302, v23, v24)
    v26 = g.vmath('DOT_PRODUCT', v22, v25)
    v27 = g.math('MULTIPLY', 0.1302571445703506, -1.0)
    v28 = g.math('MULTIPLY', 0.0105481902137399, -1.0)
    v29 = g.comb(v27, 1.1408028602600098, v28)
    v30 = g.vmath('DOT_PRODUCT', v22, v29)
    v31 = g.math('MULTIPLY', 0.0240032691508532, -1.0)
    v32 = g.math('MULTIPLY', 0.1289687752723694, -1.0)
    v33 = g.comb(v31, v32, 1.152971625328064)
    v34 = g.vmath('DOT_PRODUCT', v22, v33)
    v35 = g.comb(v26, v30, v34)
    v36 = g.sep(v35)
    v37 = g.math('MAXIMUM', v36[1], v36[2])
    v38 = g.math('MAXIMUM', v36[0], v37)
    v39 = g.math('MAXIMUM', v38, 9.999999747378752E-06)
    v40 = g.bc(v39)
    v41 = g.vmath('DIVIDE', v35, v40)
    v42 = g.vmath('MAXIMUM', v41, (0, 0, 0))
    v43 = g.vmath('MINIMUM', v42, (1, 1, 1))
    v44 = g.mixv(v8, v35, v43)
    v45 = g.vmath('MAXIMUM', v44, (0, 0, 0))
    v46 = g.vmath('MINIMUM', v45, (1, 1, 1))
    g.out_('ret', v46, True)


GROUP_NAME = 'Ruri Endfield Post'
SCENE_TREE_NAME = 'Ruri Endfield Post Scene'
COLOR_IN = 'color'
COLOR_OUT = 'ret'


def ensure_group():
    stale = bpy.data.node_groups.get(GROUP_NAME)
    if stale is not None:
        stale.name = GROUP_NAME + '.old'
    build_Ruri_Endfield_Post()
    built = bpy.data.node_groups[GROUP_NAME]
    built.use_fake_user = True
    if stale is not None:
        stale.user_remap(built)
        bpy.data.node_groups.remove(stale)
    return built


SAVED_KEY = 'ruri_post_saved_state'


def _remember(scene):
    """什么被这一级改掉了,在改之前记下来。不记 = 用户装了就回不去,
    只能手动猜 view transform 原来是什么。已记过就不覆盖(重复安装是幂等的)。"""
    if SAVED_KEY in scene:
        return
    previous = scene.compositing_node_group
    scene[SAVED_KEY] = {
        'group': previous.name if previous is not None else '',
        'use_compositing': scene.render.use_compositing,
        'view_transform': scene.view_settings.view_transform,
        'look': scene.view_settings.look,
    }


def uninstall(scene):
    """把这一级装上之前的场景状态放回去,并删掉它自己建的树。"""
    saved = scene.get(SAVED_KEY)
    scene_tree = bpy.data.node_groups.get(SCENE_TREE_NAME)
    if scene.compositing_node_group is scene_tree:
        scene.compositing_node_group = None
    if scene_tree is not None:
        bpy.data.node_groups.remove(scene_tree)
    if saved is None:
        return False
    restored = bpy.data.node_groups.get(saved.get('group') or '')
    if restored is not None:
        scene.compositing_node_group = restored
    scene.render.use_compositing = bool(saved.get('use_compositing', True))
    scene.view_settings.view_transform = saved.get('view_transform', 'Standard')
    scene.view_settings.look = saved.get('look', 'None')
    del scene[SAVED_KEY]
    return True


def installed(scene):
    tree = bpy.data.node_groups.get(SCENE_TREE_NAME)
    return tree is not None and scene.compositing_node_group is tree


def stage_node(scene):
    """装在场景树里的那个组实例 —— 它的输入 socket 就是这条链的可调参数,
    面板照着画即可,不必知道链里有什么。"""
    tree = scene.compositing_node_group
    if tree is None:
        return None
    for node in tree.nodes:
        if node.bl_idname == 'CompositorNodeGroup' and node.node_tree is not None \
                and node.node_tree.name == GROUP_NAME:
            return node
    return None


def install(scene):
    _remember(scene)
    group = ensure_group()
    stale = bpy.data.node_groups.get(SCENE_TREE_NAME)
    if stale is not None:
        bpy.data.node_groups.remove(stale)
    tree = bpy.data.node_groups.new(SCENE_TREE_NAME, 'CompositorNodeTree')
    tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    render = tree.nodes.new('CompositorNodeRLayers')
    split = tree.nodes.new('CompositorNodeSeparateColor')
    pack = tree.nodes.new('ShaderNodeCombineXYZ')
    stage = tree.nodes.new('CompositorNodeGroup')
    stage.node_tree = group
    unpack = tree.nodes.new('ShaderNodeSeparateXYZ')
    join = tree.nodes.new('CompositorNodeCombineColor')
    output = tree.nodes.new('NodeGroupOutput')
    tree.links.new(render.outputs['Image'], split.inputs['Image'])
    for channel, axis in (('Red', 'X'), ('Green', 'Y'), ('Blue', 'Z')):
        tree.links.new(split.outputs[channel], pack.inputs[axis])
    tree.links.new(pack.outputs['Vector'], stage.inputs[COLOR_IN])
    tree.links.new(stage.outputs[COLOR_OUT], unpack.inputs['Vector'])
    for channel, axis in (('Red', 'X'), ('Green', 'Y'), ('Blue', 'Z')):
        tree.links.new(unpack.outputs[axis], join.inputs[channel])
    tree.links.new(render.outputs['Alpha'], join.inputs['Alpha'])
    tree.links.new(join.outputs['Image'], output.inputs['Image'])
    scene.compositing_node_group = tree
    scene.render.use_compositing = True
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.use_compositor = 'ALWAYS'
    return tree


import importlib
import sys

_host = importlib.import_module('RuriRipperImporter.material_builder')


def register():
    _host.register_post_stage(sys.modules[__name__])


def unregister():
    _host.unregister_post_stage(sys.modules[__name__])


if __name__ == '__main__':
    install(bpy.context.scene)
