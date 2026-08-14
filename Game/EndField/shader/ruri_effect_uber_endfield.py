# =============================================================================
# Ruri Endfield Uber → Blender shader nodes(生成物,勿手改)
# 真源 = Ruri.RenderPipelines.Generator 材质模块(CharacterEndfield/Lighting/MixedPass)
#        经 Ruri.CodeGen.Blender 后端转译;执行本脚本即重建全部 node group + 根材质。
#
# 等价性契约:
#  · **raw 材质方言**:输入 socket 名 = 游戏自己的属性名(_MetallicGlossMap/_Smoothness/…),
#    贴图键同名;RMOS 通道重排与 smoothness→roughness 取反已内建进图 —— 消费方零转换直灌 .mat;
#  · 贴图按名绑定(gen.IMG_MAP 或全局同名 image;缺席时用 4x4 中性占位);
#  · 引擎态经根组输入桩:Sun_Direction/Sun_Color(默认已按 Unity Y-up→Blender Z-up 换轴)、
#    Sun_ShadowAtten;
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

    def env_image(self, image, direction, mip=None):
        if mip is None:
            return (self._env_tap(image, 'EQUIRECTANGULAR', None, 1.0, direction), 1.0)
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


def _img(name, rgba, non_color):
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, 4, 4, alpha=True)
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
    v7 = g.bc(v1)
    v8 = g.vmath('MULTIPLY', v6, v7)
    v9 = g.vmath('SUBTRACT', v0, (0.5, 0.5, 0.5))
    v10 = g.vmath('FRACTION', v9)
    v11 = g.vmath('SUBTRACT', v10, (0.5, 0.5, 0.5))
    v12 = g.vmath('ABSOLUTE', v11)
    v13 = g.vmath('MAXIMUM', v8, (1E-06, 1E-06, 1E-06))
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
    v5 = g.vmath('SUBTRACT', v0, (0.5, 0.5, 0.5))
    v6 = g.sep(v5)
    v7 = g.math('MULTIPLY', v6[0], v4)
    v8 = g.math('MULTIPLY', v6[1], v3)
    v9 = g.math('SUBTRACT', v7, v8)
    v10 = g.math('MULTIPLY', v6[0], v3)
    v11 = g.math('MULTIPLY', v6[1], v4)
    v12 = g.math('ADD', v10, v11)
    v13 = g.comb(v9, v12, 0.0)
    v14 = g.vmath('ADD', v13, (0.5, 0.5, 0.5))
    g.out_('ret', v14, True)


def build_RCE_SelectUV():
    t = _tree('RCE_SelectUV')
    g = G(t)
    v0 = g.inp('uv', True)
    v1 = g.inp('screenUV', True)
    v2 = g.inp('switchMode', False)
    v3 = g.math('LESS_THAN', v2, 0.5)
    v4 = g.math('LESS_THAN', v2, 1.5)
    v5 = g.vmath('SUBTRACT', v0, (0.5, 0.5, 0.5))
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
    v47 = g.bc(v44[1])
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
    v47 = g.bc(v44[1])
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

DEFAULT_PART = 'VFXBaseV2'
STAMP = '7ecbf8a7b3cf2e66'
STAMP_KEY = 'ruri_uber_stamp'


VERTEX_PARTS = {
}

KNOWN_PARTS = {'VFXBaseV2', 'VFXDsWrite', 'VFXDistanceField'}
VTX_MODIFIER = 'Ruri Endfield Effect Vertex'
VTX_TREE_PREFIX = 'Ruri Endfield Effect Vertex '
OUTLINE_TEMPLATE = 'Ruri Endfield Effect Outline'
CLONE_V_PREFIX = 'Ruri Endfield Effect V '
CLONE_O_PREFIX = 'Ruri Endfield Effect O '

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


def _wire_zone(g, insts, zone, images, inst_sink, part):
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
        for item, is_vec in zone['states']:
            sock = b.inputs.get('s_' + item)
            if sock is not None:
                g._set(sock, zin.outputs[item])
        for rname, is_vec in zone['reads']:
            sock = b.inputs.get('r_' + rname)
            out = feed.outputs.get(zone['sock'] + '_r_' + rname)
            if sock is not None and out is not None:
                g._set(sock, out)
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
    wires = {
        '_RuriOutlineShellGate': olattr.outputs['Fac'],
        'input_uv': stmap.outputs['Vector'],
        'input_uv1': uv1.outputs['UV'],
        'input_normalWS': geo.outputs['Normal'],
        'input_positionWS': geo.outputs['Position'],
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
    anchor = None
    for f in FETCHES.get(part, ()):
        image = images.get(f['slot']) if images else None
        nd = _wire_fetch(g, insts, f, image)
        if nd is not None and nd.image is not None:
            if anchor is None or (f['slot'] in _ANCHOR_SLOTS and (anchor.label not in _ANCHOR_SLOTS)):
                anchor = nd
    for z in ZONES.get(part, ()):
        _wire_zone(g, insts, z, images, all_insts, part)
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


def apply_vertex_stage(objects=None, camera=None):
    """给 uber 材质网格装顶点腿 modifier:壳层位移(VERTEX_PARTS 的 part)+ 反壳描边。
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
        if not vert_slots and not outline_slots:
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
        print('[ruri-vertex] {0}: shell x{1} outline x{2}'.format(
            obj.name, len(vert_slots), len(outline_slots)), flush=True)
    return done


# ============================ 材质 provider ============================
# 认领判据 = m_Shader 身份(shader 资产文本首行的 Shader "..." 自称名)。
# 🔴 属性指纹判变体已废除并禁止回退:Unity m_SavedProperties 累积材质历史上用过的
#   全部键,「键存在」证明不了任何事(实锤:cloth 全带 _SkinRimOffScale,整套布料
#   被吃成 Face 跑脸部 SDF,全身发暗)。解析不到 = 闭包丢依赖,响亮报错,禁止猜。
PART_META = {
    'VFXBaseV2': {'id': 0, 'transparent': True, 'shader': 'HGRP/Effect/VFXBaseV2', 'discriminator': None},
    'VFXDsWrite': {'id': 1, 'transparent': True, 'shader': 'HGRP/Effect/VFXDsWrite', 'discriminator': None},
    'VFXDistanceField': {'id': 2, 'transparent': True, 'shader': 'HGRP/Effect/VFXDistanceField', 'discriminator': None},
}
NON_SHADING_SHADERS = ()

CULL_PROPERTY = '_CullMode'
CULL_FIXED = 2
_shader_name_cache = {}


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
    """m_Shader 引用的 shader 自称名(闭包内该资产文本的 Shader \"...\" 行)。"""
    ref = props.shader_ref if isinstance(props.shader_ref, dict) else None
    guid = (ref or {}).get('guid')
    if not guid:
        return None
    guid = guid.lower()
    if guid in _shader_name_cache:
        return _shader_name_cache[guid]
    name = None
    text = builder.db._text(guid)
    if text:
        head = text.lstrip()
        if head.startswith('Shader'):
            first = head.split('\n', 1)[0]
            q = first.find('"')
            if q >= 0:
                name = first[q + 1:first.find('"', q + 1)]
    _shader_name_cache[guid] = name
    return name


def _variant(builder, props):
    """(part 名, part id);非本风格/非着色 shader 返回 None(宿主落兜底材质)。
    同 shader 多 part 时按 discriminator 开关分流(如 Fur 的 _UseCharacterFur)。"""
    name = _shader_name(builder, props)
    if name is None:
        ref = props.shader_ref if isinstance(props.shader_ref, dict) else {}
        print('[ruri-uber] !! 0DAY: material {0} 的 shader 引用 {1} 在闭包里解析不到 '
              '—— 闭包丢了 shader 依赖,拒绝按指纹猜'.format(props.name, ref.get('guid')), flush=True)
        return None
    if name in NON_SHADING_SHADERS:
        print('[ruri-uber] {0} 用 {1}(非着色 part),不认领'.format(props.name, name), flush=True)
        return None
    fallback = None
    for part, meta in PART_META.items():
        if meta['shader'] != name:
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


def register():
    # 宿主注册表按**绝对路径**导入(配方给的名字):相对导入会绑死部署深度,
    # 而本文件必须能被脱包 spec_from_file_location 直接加载(建图/压测探针靠它)。
    # 材质图与顶点腿都在这里自注册 —— 消费方只调宿主注册表,门面不写一行逻辑。
    import importlib
    host = importlib.import_module('RuriRipperImporter.material_builder')
    host.register_graph_provider(provider)
    host.register_vertex_stage(apply_vertex_stage)


def unregister():
    import importlib
    host = importlib.import_module('RuriRipperImporter.material_builder')
    host.unregister_graph_provider(provider)
    host.unregister_vertex_stage(apply_vertex_stage)


# 导入即清理过时预设库(见 _prune_stale_libraries):要在 register() 之外,
# 因为脱包直接加载本文件的探针也该把目录扫干净。
_prune_stale_libraries()
if __name__ == '__main__':
    build_root()
