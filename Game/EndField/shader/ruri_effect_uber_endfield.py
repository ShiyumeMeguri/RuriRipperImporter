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
    raise RuntimeError('本脚本按 Blender 5.2 基准生成(shader 树原生 repeat zone),当前版本过旧')

def _tree(name):
    # 模板组恒固定名:同名旧组先删(ensure 已把在用的那份改名 .old 并在建成后 user_remap)。
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    return bpy.data.node_groups.new(name, 'ShaderNodeTree')


def _viewport_shading():
    """优先活动窗口的 3D 视图 shading(仅 Material Preview / Rendered 才谈得上环境);没有则 None。"""
    screen = getattr(bpy.context, 'screen', None)
    screens = ([screen] if screen is not None else []) + [s for s in bpy.data.screens if s is not screen]
    for scr in screens:
        for area in scr.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type == 'VIEW_3D' and space.shading.type in {'MATERIAL', 'RENDERED'}:
                    return space.shading
    return None


def _studiolight_image(name):
    """视口 studiolight 名 → HDRI image(自带的在 datafiles/studiolights/world/*.exr)。"""
    for sl in bpy.context.preferences.studio_lights:
        if sl.name == name and sl.type == 'WORLD' and sl.path:
            return bpy.data.images.load(sl.path, check_existing=True)
    return None


def _scene_world_env():
    """scene.world → (image, projection, mapping_spec, strength, 纯色 rgb)。
    只认最常见的两种世界:Background(Environment Texture) 与 Background(纯色);
    读不懂的(如 Sky Texture 驱动 Color)纯色返 None,调用方落引擎灰。"""
    scene = getattr(bpy.context, 'scene', None)
    world = getattr(scene, 'world', None) if scene is not None else None
    if world is None:
        return None, None, None, 1.0, None
    if not world.use_nodes or world.node_tree is None:
        return None, None, None, 1.0, tuple(world.color[:3])
    nodes = world.node_tree.nodes
    bg = next((n for n in nodes if n.type == 'BACKGROUND'), None)
    strength = bg.inputs['Strength'].default_value if bg is not None else 1.0
    tex = next((n for n in nodes if n.type == 'TEX_ENVIRONMENT' and n.image is not None), None)
    if tex is not None:
        links = tex.inputs['Vector'].links
        src = links[0].from_node if links else None
        spec = None
        if src is not None and src.type == 'MAPPING':
            spec = (src.vector_type, src.inputs['Location'].default_value[:],
                    src.inputs['Rotation'].default_value[:], src.inputs['Scale'].default_value[:])
        return tex.image, tex.projection, spec, strength, None
    if bg is not None and not bg.inputs['Color'].links:
        c = bg.inputs['Color'].default_value
        return None, None, None, strength, (c[0] * strength, c[1] * strength, c[2] * strength)
    return None, None, None, strength, None


def _env_source():
    """**当前真正在照亮视口的那份环境** → (image, projection, mapping_spec, strength, 纯色 rgb)。

    真源的 _CharMaxCubemap 声明在 space0(引擎全局描述符空间,与 _LightCookie/辐照度体同列),
    characternpr.shader 的 Properties 里零出现 —— 它是场景环境探针而非材质槽,不该让谁手动绑图。
    取值顺序按 Blender 的实际照明来源:Material Preview / Rendered 关着 Scene World 时,
    EEVEE 照的是**视口 studiolight**(那排 HDRI 小球)而不是 scene.world —— 只读后者就会
    "切了球纹丝不动";勾上 Scene World 或没有 3D 视图(headless)才回落 scene.world。"""
    shading = _viewport_shading()
    if shading is not None:
        use_world = (shading.use_scene_world_render if shading.type == 'RENDERED'
                     else shading.use_scene_world)
        if not use_world:
            image = _studiolight_image(shading.studio_light)
            if image is not None:
                rot = shading.studiolight_rotate_z
                # 转角按 Blender 自己 Mapping-on-world 的同一约定作用在方向上;
                # 万一反射与可见背景反向,要翻的符号只在这一处。
                spec = None if rot == 0.0 else ('VECTOR', (0.0, 0.0, 0.0), (0.0, 0.0, rot), (1.0, 1.0, 1.0))
                return image, 'EQUIRECTANGULAR', spec, shading.studiolight_intensity, None
    return _scene_world_env()


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
        if isinstance(v, (int, float)):
            if sock.type == 'RGBA':
                sock.default_value = (v, v, v, 1.0)
            elif sock.type == 'VECTOR':
                sock.default_value = (v, v, v)
            else:
                sock.default_value = float(v)
            return
        if sock.type == 'RGBA':
            sock.default_value = (v[0], v[1], v[2], 1.0)
        elif sock.type == 'VECTOR':
            sock.default_value = (v[0], v[1], v[2])
        else:
            sock.default_value = float(v[0])

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
    def tex(self, name, uv, non_color=False, clamp=False, interp='Linear'):
        # 模板里恒用**中性占位图**,且图名 == 逻辑槽名 —— 材质克隆模板后按这个名字认出槽位、
        # 换成自己的真贴图(Blender 无贴图 socket,这是唯一能把组做成可复用模板的办法)。
        # 同名同 uv 同参采样合并成一个节点:克隆重映射按图名找节点,少一份完全兼容。
        k = ('tx', name, self._ck(uv), non_color, clamp, interp)
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
        nd.extension = 'EXTEND' if clamp else 'REPEAT'
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
        c00, a00 = self.tex(name, texel_uv(x0, y0), non_color, clamp=True, interp='Closest')
        c10, _ = self.tex(name, texel_uv(x1, y0), non_color, clamp=True, interp='Closest')
        c01, _ = self.tex(name, texel_uv(x0, y1), non_color, clamp=True, interp='Closest')
        c11, _ = self.tex(name, texel_uv(x1, y1), non_color, clamp=True, interp='Closest')
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

    def env(self, name, direction, default):
        """环境反射按方向采样 = cubemap 的 Blender 等价物。源 = 当前在照亮视口的那份环境
        (视口 studiolight 或 scene.world,见 _env_source);存在同名 image 时优先,作手动覆盖。
        取不到图(纯色世界/读不懂)就退成常量色。

        为什么只能走图节点、只能是快照:方向在组内算,而节点图不许成环,组外的 BSDF 拿不到它 ——
        所以换球/换世界/转 HDRI 之后要重跑 ensure(rebuild=True) 才跟上。
        图节点无 LOD 口 → mip 丢弃,粗糙面反射未预滤。Environment Texture 只有 Color 一个输出。"""
        k = ('env', name, self._ck(direction))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        override = bpy.data.images.get(name)
        if override is not None:
            image, projection, spec, strength = override, 'EQUIRECTANGULAR', None, 1.0
        else:
            image, projection, spec, strength, flat = _env_source()
            if image is None:
                r = ((flat if flat is not None else default), 1.0)
                self._cse[k] = r
                return r
        if spec is not None:
            # 环境那侧把方向先过一道旋转,这边不跟就会和可见背景错位。
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
        r = (color, 1.0)
        self._cse[k] = r
        return r

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

    def tex(self, name, uv, non_color=False, clamp=False, interp='Linear'):
        k = ('tx', name, self._ck(uv), non_color, clamp, interp)
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
        nd.extension = 'EXTEND' if clamp else 'REPEAT'
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

    def env(self, name, direction, default):
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
    v57, v58 = g.tex('_MainTex', v56, non_color=False, clamp=False)
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
    v75, v76 = g.tex('_BlendTex', v74, non_color=False, clamp=False)
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
    v90, v91 = g.tex('_MaskTex', v89, non_color=True, clamp=False)
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
    v103, v104 = g.tex('_DissolveTex', v102, non_color=True, clamp=False)
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
    v57, v58 = g.tex('_BaseTex', v56, non_color=False, clamp=False)
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
    v75, v76 = g.tex('_BlendTex', v74, non_color=False, clamp=False)
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
    v90, v91 = g.tex('_MaskTex', v89, non_color=True, clamp=False)
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
    v103, v104 = g.tex('_DissolveTex', v102, non_color=True, clamp=False)
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

EXTERNAL_TEXTURES = {
    'VFXBaseV2': [],
    'VFXDsWrite': [],
    'VFXDistanceField': [],
}

DEFAULT_PART = 'VFXBaseV2'
STAMP = '3ce7ff6332cbd18d'
STAMP_KEY = 'ruri_uber_stamp'


VERTEX_PARTS = {
}

KNOWN_PARTS = {'VFXBaseV2', 'VFXDsWrite', 'VFXDistanceField'}
VTX_MODIFIER = 'Ruri Endfield Effect Vertex'
VTX_TREE_PREFIX = 'Ruri Endfield Effect Vertex '
OUTLINE_TEMPLATE = 'Ruri Endfield Effect Outline'
CLONE_V_PREFIX = 'Ruri Endfield Effect V '
CLONE_O_PREFIX = 'Ruri Endfield Effect O '

_BUILT = set()   # 本会话已重建过的 part
_SHARED_READY = False


def _ensure_shared():
    # 共享子组:全会话建一次(九棵 part 树引用同一份;换血逻辑与 part 相同)。
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


def ensure(part=None, rebuild=False):
    """该 part 的模板组(固定名)。
    本 blend 里首次用到 → 直接换血覆盖同名组(不看指纹,所以插件更新后不会有
    过时组残留);之后再导入一律复用,零创建。
    Blender 没有贴图 socket ⇒ 组不能跨材质共享,故模板只是克隆源。"""
    part = part or DEFAULT_PART
    group_name, builder = PARTS.get(part, PARTS[DEFAULT_PART])
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None and part in _BUILT and not rebuild:
        return existing
    stale = existing
    if stale is not None:
        stale.name = group_name + '.old'
    _images()
    _ensure_shared()
    builder()
    built = bpy.data.node_groups[group_name]
    built.use_fake_user = True   # 零引用也随 blend 落盘
    built[STAMP_KEY] = STAMP     # 只作标记:哪次部署建的,不参与判定
    if stale is not None:
        stale.user_remap(built)  # 旧组的用户(上次导入的克隆源)改指新组后删除
        bpy.data.node_groups.remove(stale)
    _BUILT.add(part)
    return built


def _external_textures(g, grp, part, uv):
    """把原始 UV 直采的贴图建在**材质树上**(组外),Color/Alpha 接进组。
    这样换贴图不用进组:材质节点树上一眼看到,节点标签就是槽名。
    UV 是算出来的那些(影色 LUT 按 albedo 查、ramp 按 NdotL 查、matcap 按视
    空间法线查)提不出来 —— 组外拿不到组内中间量,节点图也不许成环。"""
    y = 320
    first = None
    for slot, clamp in EXTERNAL_TEXTURES.get(part, ()):
        color_in = grp.inputs.get(slot)
        if color_in is None:
            continue
        nd = g._nd('ShaderNodeTexImage')
        nd.label = slot
        nd.name = slot
        nd.location = (-620, y)
        nd.width = 260
        nd.extension = 'EXTEND' if clamp else 'REPEAT'
        y -= 300
        nd.image = bpy.data.images.get(slot)   # 占位图(色彩空间已由 _img 定死)
        g._set(nd.inputs[0], uv)
        g._set(color_in, nd.outputs['Color'])
        alpha_in = grp.inputs.get(slot + '_alpha')
        if alpha_in is not None:
            g._set(alpha_in, nd.outputs['Alpha'])
        if slot == '_BaseMap':
            first = nd
    # 活动节点决定 Solid/材质预览显示哪张图 —— 只认 _BaseMap(表里已排头)。
    # 提不出 _BaseMap 的 part(Eyes/Eyebrow 用视差偏移 UV 采样)宁可不设,
    # 也不能让法线图当活动节点 —— 那会把模型在视口里显示成紫蓝色。
    if first is not None:
        g.t.nodes.active = first
        first.select = True


def build_material(mat, group_name=None, opaque=True, multiply_blend=False, part=None, cull=2.0):
    # cull = 材质 _Cull 真值(shader `Cull [_Cull]`:0=Off 双面 / 1=Front 渲背面 / 2=Back 渲正面)。
    # Cycles 无逐材质剔除,等价 = Backfacing→Transparent(实锤:ZWrite=0 的 fur 壳不剔背面时
    # 视线每层命中两面,alpha 厚度翻倍渲成闷壳)。
    # 入口组实例接进材质树;返回组实例节点(调用方按 socket 名填 uniform)。
    # opaque 的真源判据(characternpr_eye Sub0_Pass0_Fragment:1014 逐字):
    #     outColor.w = (_SurfaceType == 1.0) ? computedAlpha : 1.0
    # 即 Opaque 面的输出 alpha 恒 1 —— 那条 gBuffer0.w 在真管线是 materialFlags 位域,
    # 不是不透明度(skin 变体更直接:_3324.w = 1.0f 硬写)。alpha 裁剪掩码另算,恒生效。
    nt = mat.node_tree
    nt.nodes.clear()
    g = G(nt, is_group=False)
    grp = g._nd('ShaderNodeGroup')
    grp.node_tree = bpy.data.node_groups[group_name]
    geo = g.geo()
    tc = g.texco()
    # 切线读导入侧烘的 corner 属性:有游戏切线用游戏的,没有则是 Blender 自算的
    # UV 切线 —— 属性由 mesh_builder 恒建,故这里不需要存在性分支(Attribute
    # 节点没有可靠的存在判据,实测缺席与存在读到同一个值)。带手性 w 才对:
    # Blender 的 Tangent 节点不输出符号,镜像 UV 岛的副切线会整片反向。
    tan_attr = g.attr('ruri_tangent')
    tan_sign = g.attr('ruri_tangent_sign')
    tan_ws = g.vtrans(tan_attr.outputs['Vector'], 'OBJECT', 'WORLD', 'VECTOR')
    col = g.attr('Color')
    # UV1 varying(Fur 壳层 index 在 .x / 第二套 UV / VFX 粒子 custom data):
    # 不接线 socket 默认 0 → Fur 的 ceil(shellIdx)=0 → shellAlpha 恒 1,壳层 cutout
    # 整体失效(实锤:绒毛渲成一块实心岩石)。无 UV1 层的网格该节点输出 0,无害。
    uv1 = g._nd('ShaderNodeUVMap')
    uv1.uv_map = 'UV1'
    # varying uv 的真源语义 = uv0×_BaseMap_ST.xy+zw(顶点里统一变换;fur 的毛簇密度、
    # dye 的反解除法全指望它)。Mapping POINT = 先乘 Scale 后加 Location,provider 按
    # label 灌材质真值(实锤:漏变换让 furMap 平铺 4.5 倍降到 0.76,毛簇糊成岩石)。
    stmap = g._nd('ShaderNodeMapping')
    stmap.label = 'RuriBaseMapST'
    g._set(stmap.inputs['Vector'], tc.outputs['UV'])
    # 描边虚拟面的路由信号:GN 侧写的 'ruri_outline' 面属性(本体面缺省读 0)。
    # gate 走它 → 片元过游戏 outline 调色;剔除方向也按它翻(描边 pass 恒 Cull Front)。
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
    for s in grp.inputs:
        if s.name in wires:
            g._set(s, wires[s.name])
    _external_textures(g, grp, part, stmap.outputs['Vector'])
    # tonemap 已内联在入口组尾(ret_gBuffer0 即显示线性)——材质树零匿名节点,
    # 属性面板直接露出组的命名 socket。
    color_sock = grp.outputs.get('ret_gBuffer0')
    if color_sock is None:
        color_sock = next((s for s in grp.outputs if s.type == 'VECTOR'), None)
    alpha_sock = grp.outputs.get('ret_gBuffer0_w')
    clip_sock = grp.outputs.get('__clip')
    em = g._nd('ShaderNodeEmission')
    tr = g._nd('ShaderNodeBsdfTransparent')
    mixsh = g._nd('ShaderNodeMixShader')
    if multiply_blend:
        # 乘法混合(真源 OverlayShadow 的 `Blend Zero SrcColor` = dst*src)在 Blender 有
        # **精确**等价物:Transparent BSDF 的 Color 就是透射滤色,光穿过即乘以该色。
        # 不要拿自发光+alpha 去近似 —— 输出接近黑时 alpha→1,会变成一块不透明黑板。
        if color_sock is not None:
            g._set(tr.inputs['Color'], color_sock)
        g._set(mixsh.inputs[0], 0.0)   # 全走 transparent 一侧
        g._set(mixsh.inputs[1], tr.outputs[0])
        g._set(mixsh.inputs[2], em.outputs[0])
        outp = g._nd('ShaderNodeOutputMaterial')
        g._set(outp.inputs[0], _apply_cull(g, mixsh.outputs[0], cull, olattr.outputs['Fac']))
        return grp
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
    return grp


def _apply_cull(g, shader_sock, cull, outline_fac):
    """剔除面 → Transparent。本体面按材质 _Cull(1=Front 渲背面 / 2=Back 渲正面);
    描边虚拟面(ruri_outline=1)恒 Cull Front(游戏 outline pass 写死)。
    透明权重:cull=2 → XOR(Backfacing, outline);cull=1 → 1-Backfacing(两类同向);
    cull=0 → outline×(1-Backfacing)(本体双面,描边仍剔正面)。"""
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
    build_material(mat, group.name, part=part)
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
    mc, _ma = g.tex('_OutlineMask', mask_uv, non_color=True, clamp=True)
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

_shader_name_cache = {}


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


def _subtree_has_tex(tree, memo):
    hit = memo.get(tree.name)
    if hit is not None:
        return hit
    memo[tree.name] = False   # 先占位防环
    found = False
    for node in tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image is not None:
            found = True
            break
        if (node.type == 'GROUP' and node.node_tree is not None
                and node.node_tree.name.startswith('RCE_')
                and _subtree_has_tex(node.node_tree, memo)):
            found = True
            break
    memo[tree.name] = found
    return found


def _retexture(tree, images, material_name, cloned, memo):
    """树上贴图节点指向本材质真图;遇到**含贴图的 RCE 子组**先按材质克隆再递归 ——
    模板保持共享,分裂只发生在克隆时;纯数学子组不含贴图,全材质共享不动。"""
    for node in tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image is not None:
            slot = _slot_of(node.image.name)
            node.label = slot   # 槽名恒留节点上:换图后占位名消失,绑定错位否则无从审计
            real = images.get(slot)
            if real is not None:
                _swap_image(node, real)
        elif (node.type == 'GROUP' and node.node_tree is not None
                and node.node_tree.name.startswith('RCE_')
                and _subtree_has_tex(node.node_tree, memo)):
            src = node.node_tree
            got = cloned.get(src.name)
            if got is None:
                got = src.copy()
                new_name = 'Uber {0}/{1}'.format(material_name, src.name)
                old = bpy.data.node_groups.get(new_name)
                if old is not None:
                    old.user_remap(got)
                    bpy.data.node_groups.remove(old)
                got.name = new_name
                cloned[src.name] = got
                _retexture(got, images, material_name, cloned, memo)
            node.node_tree = got


def _clone_uber(part, material_name, images):
    template = ensure(part)
    clone = template.copy()
    # 重导幂等:同名旧克隆换血删除(唯一用户就是本材质旧树,马上整树重建)。
    stale = bpy.data.node_groups.get('Uber ' + material_name)
    if stale is not None:
        stale.user_remap(clone)
        bpy.data.node_groups.remove(stale)
    clone.name = 'Uber ' + material_name
    _retexture(clone, images, material_name, {}, {})
    return clone


def _standard_view_transform(scene):
    # 本次生成**未内联** tonemap:组输出是场景线性 HDR,显示变换归用户
    #(Color Management 的 View Transform / 合成器)。这里不越界改场景设置。
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
    clone = _clone_uber(part_name, name, images)
    # 就地改写同名材质:网格按名绑材质,另起新料会得 .001 后缀,原名材质带着兜底节点占槽。
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    grp = build_material(mat, clone.name, opaque=opaque,
                         multiply_blend=meta['transparent'] and part_name == 'OverlayShadow',
                         part=part_name, cull=float(props.floats.get('_Cull', 2.0)))
    # 组外贴图节点(原始 UV 直采的那些建在材质树上):换真图,节点标签 = 槽名。
    for node in mat.node_tree.nodes:
        if node.type != 'TEX_IMAGE':
            continue
        real = images.get(_slot_of(node.label or node.name))
        if real is not None:
            _swap_image(node, real)
    filled = [0]
    # varying uv 的 _BaseMap_ST 变换(build_material 里的 Mapping 按 label 灌真值)
    bst = props.texture_st.get('_BaseMap') or [1.0, 1.0, 0.0, 0.0]
    for node in mat.node_tree.nodes:
        if node.label == 'RuriBaseMapST':
            node.inputs['Scale'].default_value = (float(bst[0]), float(bst[1]), 1.0)
            node.inputs['Location'].default_value = (float(bst[2]), float(bst[3]), 0.0)

    def put(sock_name, value):
        sock = grp.inputs.get(sock_name)
        if sock is None:
            return
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
    print('[ruri-uber] {0}: shader={1} part={2} images={3} sockets={4} nodes={5}'.format(
        name, mat['ruri_uber_shader'], part_name, len(images), filled[0], len(clone.nodes)), flush=True)
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


if __name__ == '__main__':
    build_root()
