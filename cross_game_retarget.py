"""Cross-game animation retarget: play one game's animation on another game's rig.

A humanoid clip already retargets by itself -- muscle values are avatar-relative, and the
solve runs against whichever skeleton the clip is bound to (see prefab_importer.
_solve_humanoid_curves). A GENERIC clip cannot: its curves carry per-bone local rotations
in the SOURCE rig's own bone axes, so there is nothing avatar-shaped to re-decode. It needs
a real retarget -- world-rotation transfer through both rigs' rest poses -- and one table
saying which bone is which.

That is exactly what the AnimationRetarget add-on already is, so this module adds no maths
of its own -- and deliberately no second judgement of its own either. It contributes only
what AnimationRetarget cannot know: WHICH RIG the clips were authored on. That answer is
measured in two hops, neither of which reads a name, a folder or a game:

  clip 绑定 CRC ─(m_TOS 覆盖率)─> 源 Avatar ─(cabmap 反向依赖 + 根 Animator 身份)─> 宿主角色 prefab

and the host is then built by the ordinary prefab import path -- the same builder a user's
own character import runs. The tables are ordinary AnimationRetarget presets, so they stay
editable in its own panel and nothing here is a second config format.

🔴 骨架只允许有一个建造者
------------------------
重定向数学是过两副骨架的**静止姿势**做世界旋转传递的, 所以源骨架错一点, 结果就整个是
错的, 而且是"忠实地错" —— 数学没毛病, 传的是错的东西。这条路上死过两次, 两次都是源:

* 用 ``armature_builder.build_armature_from_avatar`` 从 Avatar 现搭源骨架 —— 实测同一个
  角色 415 骨 vs 真导入的 454 骨, 共有的 414 根里 380 根静止矩阵不同, 上臂差 0.7466。
  现场表现是跑步动画变成举手。Avatar 里那副骨架是给蒙皮用的绑定姿势, 不是泛型 clip 的
  曲线所参照的那副;
* 拿"用到这个角色的任意一个 prefab"当宿主 —— 过场/levelseq 里那份是**摆好位的实例**,
  带着场景的姿态, 实测 595/595 根骨全变, 最大差 612。

所以宿主必须是角色**自己的模型 prefab**, 且必须走 prefab 导入路径建。用户手动重定向之所以
完美, 就是因为他手动导的正是这一份 —— 同一个数学、同一张表, 只是源是对的。

🔴 这里不许再长出第二套判断
--------------------------
这个模块反复被写坏, 都是同一种错: 在"交给 AnimationRetarget"之外, 自己又加一层
"要不要交、交哪张"的推断。已经试错并否掉的:

* 按骨架上烙的游戏配对查 ``<GameA>To<GameB>.json`` —— 游戏不是骨架身份:
  一个游戏有多套互不兼容的骨架, 两个游戏可能共用一套, OC 骨架根本没有游戏;
* 按骨名覆盖率自动选表 —— 太松, 骨名碰巧撞上一半的无关表会被判成可用;
* 按骨架 path 集合的指纹精确选表 —— 太紧, 把一张表钉死在一副骨架上, 换外观、
  少几根骨、用别人的骨架全都失配, 而这些恰恰最该复用;
* 按路径匹配率决定"够像就直接绑定, 省掉重定向" —— **最隐蔽的一个**: 路径对得上
  只说明骨骼是同一批, 不说明骨轴一样。目标骨架为了 Blender 的 .L/.R 镜像重排过
  朝向/roll 时, 直接灌局部旋转就是一堆乱的, 而匹配率还接近 100%, 判据完全看不出来。

现在只剩一句话: **目标骨架指名了表就走重定向, 没指名就照旧直接绑定。**

* 用哪张表 —— 目标骨架上写着表名(``ruri_retarget_table``), 直接读那张。表是一份
  **通用的骨名对应关系**, 不绑定任何一副具体骨架: 两侧缺哪根骨就跳过哪一行, 所以同一张
  表能服务同一套骨架的各种外观变体, 也能手动指给别人的骨架用。一张表天然双向,
  方向按哪一侧更贴合来源骨架自动定, 永远不要再写镜像的那一份。

唯一还按游戏走的是**表情**: ``_retarget_faces`` 里的 ``Game.face_retarget_of``。
那是对的 —— 一套表情系统(blendshape 词汇)确实是每个游戏自己的, 和骨架身份是两回事。

There is no panel and no button here on purpose. A cross-game import is an ordinary clip
import onto a rig that names a table (see ``cabmap_panel._import_clips_standalone``); a
second, manual path would be a second way to express the same intent.
"""

from __future__ import annotations

import collections
import json

import bpy

try:
    from . import Game, armature_builder, prefab_importer
    from .RuriRipperPyBridge.session import cabmap_state
    from .RuriRipperPyBridge.unity import (avatar as avatar_module, bridge_asset_db,
                                           class_registry, clip_paths,
                                           hierarchy as unity_hierarchy)
except ImportError:  # standalone (non-package) testing
    import Game
    import armature_builder
    import prefab_importer
    from RuriRipperPyBridge.session import cabmap_state
    from RuriRipperPyBridge.unity import (avatar as avatar_module, bridge_asset_db,
                                          class_registry, clip_paths,
                                          hierarchy as unity_hierarchy)


ADDON_MODULE = "AnimationRetarget"


def _addon():
    """The AnimationRetarget add-on's (core, presets, api) modules, or None when it is
    not installed -- imported lazily by name so this importer still loads without it."""
    try:
        core = __import__(ADDON_MODULE + ".core", fromlist=["core"])
        presets = __import__(ADDON_MODULE + ".presets", fromlist=["presets"])
        api = __import__(ADDON_MODULE + ".api", fromlist=["api"])
    except ImportError:
        return None
    return core, presets, api


def available():
    return _addon() is not None


RETARGET_TABLE_PROP = "ruri_retarget_table"


def table_name_of(arm_obj):
    """这副骨架指定用哪张对照表(AnimationRetarget 预设名), "" = 没指定。

    为什么是"骨架上写个表名"而不是任何自动推断:
      * 指纹把一张表钉死在**一副**骨架上 —— 换套外观、少几根骨、拿别人的骨架来用,
        指纹立刻不匹配, 而这些恰恰是最该能复用的情况;
      * 覆盖率打分又太松, 骨名碰巧撞上一半的无关表会被判成可用。
    表本来就是一份**通用的骨名对应关系**, 不属于任何一副具体骨架。所以"用哪张"是
    用户的一句声明, 写在骨架上, 之后每次导入动画自动读它 —— 透明、可复用、可手动
    改成别人的表。
    """
    if arm_obj is None:
        return ""
    return str(arm_obj.get(RETARGET_TABLE_PROP) or "")


def set_table_name(arm_obj, name):
    if arm_obj is not None:
        arm_obj[RETARGET_TABLE_PROP] = str(name or "")


def table_spec_of(arm_obj):
    """(这副骨架指名的那张表, 表名) —— **整份原样返回, 不过滤不定向不改设置**。

    🔴 这里刻意什么都不做, 因为 AnimationRetarget 自己已经做了:
    ``retarget_math.build_mappings`` 会把每一行编译成 SolvedMapping, 两侧任一根骨不存在
    就跳过该行并记一条警告("跳过映射 X -> Y: 骨骼不存在")。所以缺骨骼**本来就被容忍**,
    在这一层再滤一遍纯属第二套系统 —— 而且滤法还不一样(这边看 ``obj.data.bones``,
    那边看它自己建的 ``skel.index``), 两套一旦不一致就会出现"这边留下的行那边不认"。

    ``settings`` 同理整份直通(frame_step/interpolation/suffix/overwrite/bake_mode/
    fake_user 都在表里)。这里曾经自编过一份 ``{"suffix": "_"+目标名}`` 塞进去, 那就是
    第二套配置: 用户在 AnimationRetarget 面板里存的烘焙参数被无声顶掉, 同一张表手动跑
    和自动跑出来的产物不一样。表怎么写就怎么用。

    同理不在这里猜方向: 这条路的来源永远是动画的宿主角色, 目标永远是用户的骨架,
    方向是固定的; 表的双向性由 AnimationRetarget 面板那侧使用, 不该在这里再实现一遍。

    表名为空 / 插件缺失 / 表读不出来, 一律返回 ({}, 表名)。
    """
    addon = _addon()
    name = table_name_of(arm_obj)
    if addon is None or not name:
        return {}, name
    _core, presets, _api = addon
    try:
        return presets.load_preset(name), name
    except Exception:
        return {}, name


class CrossGameRetargetError(RuntimeError):
    """A direct cross-game clip import that cannot proceed: no source avatar covers the
    clips' bindings, or no bone table exists (the message then carries the fill-it-in
    prompt), or the AnimationRetarget add-on is missing."""


_AvatarScore = collections.namedtuple(
    "_AvatarScore", "cab name dependency_count score coverage tos_size")

_SOURCE_AVATAR_CACHE = {}


def _class_ids_at(rows, index):
    """The ClassIDs one cabmap row carries, decoded from the columnar
    class_starts/class_flat pair -- the one reader of that encoding here."""
    start = int(rows.class_starts[index])
    end = int(rows.class_starts[index + 1])
    return set(int(class_id) for class_id in rows.class_flat[start:end])


def _candidate_avatar_cabs(session_key):
    """Every CAB in the source install's loaded cabmap that carries an Avatar, cheapest
    (fewest cabmap dependencies) first -- the search order for the source rig."""
    session = cabmap_state.session_for(session_key)
    rows = session.ROWS
    avatar_id = class_registry.id_for_name("Avatar")
    if avatar_id is None:
        raise CrossGameRetargetError("The class registry has no 'Avatar' class id.")
    candidates = []
    for index in range(len(rows)):
        if avatar_id in _class_ids_at(rows, index):
            candidates.append((int(rows.deps[index]), rows.cab(index)))
    candidates.sort(key=lambda pair: pair[0])
    return candidates, avatar_id


def _avatar_documents(unity_file):
    for document in unity_file.documents:
        if document.class_name == "Avatar":
            yield document


_ScannedAvatar = collections.namedtuple(
    "_ScannedAvatar", "cab name dependency_count tos_size crcs")

# install key -> {"order": [(dep count, cab)], "position": int, "seen": [_ScannedAvatar]}.
# 每个 avatar 的 m_TOS 只解析一次, 结果按 install 留在会话里。
#
# 为什么这件事值得: clip 和它的 avatar 在 cabmap 图上**没有连边**(实测 302 个 clip CAB,
# 97% 从 clip 反向依赖出发一个 Avatar 都够不到), 所以这一步只能拿覆盖率去量, 而量就得
# 逐个把 avatar 导进来解析。pelica 的 avatar 排在扫描序第 740 / 1386, 27.4s。过去这 740
# 个的解析结果转头就扔了, 下一个角色再从第 1 个重扫一遍 —— 现在扫过的就留着, 第二个角色
# 只从上次停下的地方往后走, 排在前面的直接命中。
_AVATAR_INDEX = {}


def _avatar_index(session_key):
    state = _AVATAR_INDEX.get(session_key)
    if state is None:
        candidates, _avatar_id = _candidate_avatar_cabs(session_key)
        state = {"order": candidates, "position": 0, "seen": [], "parsed": set()}
        _AVATAR_INDEX[session_key] = state
    return state


def _graph_avatar_cabs(session_key, clip_cab):
    """Avatar CABs the cabmap can actually REACH from this clip, cheapest first.

    Two reverse hops then forward: the clip's dependents (an AnimatorController, a
    timeline .playable, a montage/cpuanim .asset), then THEIR dependents (the prefab
    whose Animator names the controller), then those CABs' forward closures -- which is
    where an Avatar finally sits. Measured over 301 sampled clip CABs: one hop reaches an
    avatar for 2.7%, adding the second hop takes it to **33.6%**, candidate sets stay
    small (max 30) and the whole walk costs 1.8ms.

    It is not universal -- 46% of this game's AnimatorControllers have no dependent at
    all and 63% of character postmodels reference no controller (the controller is picked
    at runtime from gameplay config by entity id), which is exactly the case pelica's
    clips fall in: hop1 = 2 controllers, hop2 = 0. So this is a CANDIDATE SOURCE, not a
    judgement: whatever it returns still has to pass the same 100%-coverage test as every
    other candidate, and when it returns nothing the ordered walk runs as before. That is
    also why a wrong-but-reachable avatar (a weapon's, another actor's -- both common in
    the sample) costs nothing: it cannot cover a body clip's bindings, so it is rejected
    on measurement rather than on where it came from."""
    bridge = cabmap_state.BRIDGE
    if bridge is None or not clip_cab:
        return []
    rows = cabmap_state.session_for(session_key).ROWS
    index_of = rows.cab_to_index()
    avatar_id = class_registry.id_for_name("Avatar")
    hop1 = bridge.find_direct_dependents([clip_cab])
    if not hop1:
        return []
    hop2 = [cab for cab in bridge.find_direct_dependents(hop1) if cab not in set(hop1)]
    found = []
    for cab in bridge.resolve_closure_cab_names(hop1 + hop2):
        index = index_of.get(cab)
        if index is not None and avatar_id in _class_ids_at(rows, index):
            found.append((int(rows.deps[index]), cab))
    found.sort()
    return found


def _score_of(entry, clip_crcs, want):
    hits = len(entry.crcs & clip_crcs)
    return _AvatarScore(entry.cab, entry.name, entry.dependency_count, hits,
                        hits / want if want else 0.0, entry.tos_size)


def _scan_one_avatar_cab(bridge, avatar_id, dependency_count, cab):
    """Every Avatar in one CAB, as _ScannedAvatar rows (its m_TOS reduced to the CRC set
    -- the only thing coverage is measured against)."""
    assets = bridge.import_cabs([cab], export_class_ids=[avatar_id])[0]
    cab_db = bridge_asset_db.BridgeAssetDatabase(assets, asset_paths=bridge.asset_paths_by_guid)
    found = []
    for guid in list(cab_db.all_guids()):
        unity_file = cab_db.load_guid(guid)
        if unity_file is None:
            continue
        for document in _avatar_documents(unity_file):
            tos = avatar_module._parse_tos(document.data)
            found.append(_ScannedAvatar(cab, str(document.data.get("m_Name") or guid),
                                        dependency_count, len(tos), frozenset(tos.keys())))
    return found


def score_source_avatars(session_key, clip_crcs, stop_at_full=True, clip_cab=None):
    """Rank the source game's Avatars by how many of the clips' binding CRCs each one's
    ``m_TOS`` covers -- the measured answer to "which rig were these clips authored on",
    with no name guessing. A 100%-covering avatar short-circuits the walk; otherwise
    everything is scanned and the argmax wins. Ordered by _rank_key (coverage, then
    base-rig completeness). Returns [_AvatarScore].

    Candidates are tried in three tiers, and **the tiers are only an order** -- every one
    of them faces the identical coverage test, so which tier an avatar came from can never
    change whether it is accepted:

      1. what this install already scanned (free -- see _AVATAR_INDEX);
      2. what the cabmap can reach from the clip itself (1.8ms -- see _graph_avatar_cabs);
      3. every Avatar CAB in the map, cheapest first, resuming where it last stopped.

    Tier 3 is the one that always terminates the search, and it is why an install whose
    graph does not link clips to avatars still resolves correctly, just slower."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    avatar_id = class_registry.id_for_name("Avatar")
    state = _avatar_index(session_key)
    want = len(clip_crcs)
    ranked = []

    def absorb(entries):
        """Score freshly parsed avatars into the running rank; True on a full cover."""
        for entry in entries:
            state["seen"].append(entry)
            item = _score_of(entry, clip_crcs, want)
            ranked.append(item)
            if stop_at_full and want and item.score >= want:
                return True
        return False

    ranked.extend(_score_of(entry, clip_crcs, want) for entry in state["seen"])
    if stop_at_full and want and any(item.score >= want for item in ranked):
        ranked.sort(key=_rank_key)
        return ranked

    bridge.use_session(session_key)
    for dependency_count, cab in _graph_avatar_cabs(session_key, clip_cab):
        if cab in state["parsed"]:
            continue
        state["parsed"].add(cab)
        if absorb(_scan_one_avatar_cab(bridge, avatar_id, dependency_count, cab)):
            ranked.sort(key=_rank_key)
            return ranked

    order = state["order"]
    while state["position"] < len(order):
        dependency_count, cab = order[state["position"]]
        state["position"] += 1
        if cab in state["parsed"]:
            continue
        state["parsed"].add(cab)
        if absorb(_scan_one_avatar_cab(bridge, avatar_id, dependency_count, cab)):
            ranked.sort(key=_rank_key)
            return ranked
    ranked.sort(key=_rank_key)
    return ranked


def _rank_key(item):
    """Best coverage first; among ties the fewest-dependency, most-complete skeleton
    (a base body rig over a garment variant that merely embeds it), then name."""
    return (-item.score, item.dependency_count, -item.tos_size, item.name)


def _load_source_avatar_file(bridge, session_key, cab, avatar_name):
    """The exported Avatar UnityFile named ``avatar_name`` in ``cab`` -- matched by m_Name,
    never by guid: AssetRipper mints a fresh guid on every export, so the guid a scoring pass
    saw is meaningless in a later one, while the avatar's own name is stable."""
    bridge.use_session(session_key)
    avatar_id = class_registry.id_for_name("Avatar")
    assets = bridge.import_cabs([cab], export_class_ids=[avatar_id])[0]
    cab_db = bridge_asset_db.BridgeAssetDatabase(assets, asset_paths=bridge.asset_paths_by_guid)
    for guid in list(cab_db.all_guids()):
        unity_file = cab_db.load_guid(guid)
        if unity_file is None:
            continue
        document = unity_file.first("Avatar")
        if document is not None and str(document.data.get("m_Name") or "") == avatar_name:
            return unity_file
    return None


def _resolve_source_avatar(session_key, clip_cab, clip_crcs):
    """The Avatar UnityFile the clips were authored on, plus its _AvatarScore. Cached per
    (install, clip CAB) so a second clip from the same pack skips the whole scan."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    key = (session_key, clip_cab)
    cached = _SOURCE_AVATAR_CACHE.get(key)
    if cached is not None:
        unity_file = _load_source_avatar_file(bridge, session_key, cached.cab, cached.name)
        if unity_file is not None:
            return unity_file, cached
    ranked = score_source_avatars(session_key, clip_crcs, clip_cab=clip_cab)
    if not ranked or ranked[0].score == 0:
        raise CrossGameRetargetError(
            "No {0} avatar's skeleton covers the selected clip's bindings -- there is nothing "
            "to retarget from.".format(session_key))
    best = ranked[0]
    _SOURCE_AVATAR_CACHE[key] = best
    unity_file = _load_source_avatar_file(bridge, session_key, best.cab, best.name)
    if unity_file is None:
        raise CrossGameRetargetError("The chosen source avatar CAB could not be re-read.")
    return unity_file, best


def _has_action(arm_obj):
    return (arm_obj.animation_data is not None
            and arm_obj.animation_data.action is not None)


HOST_COLLECTION = "RuriRetargetSources"

# (install key, avatar name) -> 宿主骨架对象名。同一副宿主服务这个角色的全部 clip:
# 宿主是一次**完整的角色导入**(秒级), 不是从前那种毫秒级脚手架, 每批 clip 重导一次
# 是纯浪费。缓存在会话里, 失效判据只有一条 —— 对象还在不在。
_HOST_RIG_CACHE = {}


def _discard_baked(baked_actions):
    """Remove the intermediate actions baked onto the host, leaving only the retarget
    products on the destination rig.

    🔴 宿主角色本身**不删**。它不是脚手架, 它就是这条路唯一正确的静止姿势来源
    (见 _resolve_host_cab), 重建一次等于重导一次角色。所以它被停在
    HOST_COLLECTION 这个隐藏 collection 里复用 —— 藏起来因为用户要的不是它,
    留着因为它是源, 放在一个具名 collection 里因为用户得看得见、删得掉。"""
    for action in baked_actions:
        try:
            bpy.data.actions.remove(action)
        except Exception:
            pass


def _host_collection(context):
    """The hidden collection every retarget source character is parked in."""
    collection = bpy.data.collections.get(HOST_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(HOST_COLLECTION)
    if not any(child is collection for child in context.scene.collection.children_recursive):
        context.scene.collection.children.link(collection)
    collection.hide_viewport = True
    collection.hide_render = True
    return collection


def _park_in_host_collection(context, objects):
    for obj in objects:
        for existing in list(obj.users_collection):
            existing.objects.unlink(obj)
    collection = _host_collection(context)
    for obj in objects:
        collection.objects.link(obj)


def _host_candidate_cabs(session_key, avatar_cab):
    """The CABs that could be "this character's own model prefab", cheapest first.

    Reverse dependency is CAB-granular, and a character's fbx CAB carries its Mesh
    alongside its Avatar -- so what comes back is everything that USES this character
    (measured: 183 CABs for one Endfield character -- cutscenes, dialogs, levelseqs,
    a UI model), not the host itself. **The order here only affects speed, never
    correctness**: which one is the host is answered by the hard test in
    _resolve_host_cab, and this merely puts the smallest closures first so the common
    case hits in one or two tries (measured: 2nd candidate for one character, 3rd for
    another). Carrying both a GameObject and an Animator is necessary, so that filter
    runs first and is free."""
    bridge = cabmap_state.BRIDGE
    session = cabmap_state.session_for(session_key)
    rows = session.ROWS
    index_of = rows.cab_to_index()
    gameobject_id = class_registry.id_for_name("GameObject")
    animator_id = class_registry.id_for_name("Animator")
    if gameobject_id is None or animator_id is None:
        raise CrossGameRetargetError("The class registry has no 'GameObject'/'Animator' class id.")
    ranked = []
    for cab in bridge.find_direct_dependents([avatar_cab]):
        index = index_of.get(cab)
        if index is None:
            continue
        classes = _class_ids_at(rows, index)
        if gameobject_id not in classes or animator_id not in classes:
            continue
        closure = bridge.resolve_closure_cab_names([cab])
        ranked.append((len(closure), int(rows.deps[index]), cab))
    ranked.sort()
    return [cab for _closure_size, _dependency_count, cab in ranked]


def _prefab_head(bridge, cab):
    """(this CAB's own root prefab, its db) with only the hierarchy/animator classes
    exported -- no mesh, no material, no texture. There can be a hundred-odd candidates
    and deciding whether one is the host reads nothing but its root Animator."""
    class_ids = [class_registry.id_for_name(name)
                 for name in ("GameObject", "Transform", "Animator", "Avatar")]
    if any(class_id is None for class_id in class_ids):
        raise CrossGameRetargetError("The class registry is missing a hierarchy class id.")
    assets, roots, seed_roots, _clips, _scenes = bridge.import_cabs(
        [cab], export_class_ids=class_ids)
    db = bridge_asset_db.BridgeAssetDatabase(assets, asset_paths=bridge.asset_paths_by_guid)
    guid = seed_roots.get(cab) or (roots[0] if roots else None)
    return (db.load_guid(guid) if guid else None), db


def _resolve_host_cab(session_key, source):
    """The CAB holding the character the clips were authored on -- the animation's HOST.

    One test, and it is hard: **the Animator on the prefab's ROOT GameObject names
    exactly this Avatar** (prefab_importer.root_animator / animator_avatar -- identity
    through the m_Avatar guid, then the avatar's own stable m_Name). It says "the
    animated thing IS this prefab", which is the difference that decides the rest pose:
    a levelseq/cutscene prefab that merely STAGES the character carries the staged pose,
    and building the source from one of those moves every bone (measured: 595/595 bones
    differ, worst 612 units). A UI model and another character's cutscene both pass the
    weaker "has a root animator" test and both name a different avatar, so the avatar
    identity is not optional either (measured: 4 root-animator prefabs among one
    character's 180 candidates, only 2 of them this character's own).

    Nothing here reads a name, a folder or a game. The cabmap's reverse dependency
    gives the candidates, the Avatar the clips measurably bind to picks among them."""
    bridge = cabmap_state.BRIDGE
    if bridge is None:
        raise CrossGameRetargetError("No cabmap bridge session for the source game.")
    bridge.use_session(session_key)
    for cab in _host_candidate_cabs(session_key, source.cab):
        prefab_file, db = _prefab_head(bridge, cab)
        if prefab_file is None:
            continue
        _nodes, roots = unity_hierarchy.build_hierarchy(prefab_file)
        animator = prefab_importer.root_animator(prefab_file, {"roots": roots})
        document = prefab_importer.animator_avatar(db, animator)
        if document is not None and str(document.data.get("m_Name") or "") == source.name:
            return cab
    raise CrossGameRetargetError(
        "No prefab in {0} is rooted on avatar '{1}' -- the character these clips were "
        "authored on is not in this install's cabmap, so there is no rig to retarget "
        "from.".format(session_key, source.name))


def _build_host_rig(context, session_key, host_cab, options):
    """Import the host character through the PREFAB IMPORT PATH -- the very builder a
    user's own character import runs, byte for byte the same call.

    🔴 骨架只允许有一个建造者。这里曾经用 armature_builder.build_armature_from_avatar
    从 Avatar 现搭一副源骨架, 那是第二个建造者, 而两个建造者建出来的东西不一样:
    实测同一个角色 415 骨 vs 454 骨, 共有的 414 根里 380 根静止矩阵不同, 上臂差 0.7466。
    重定向数学是过两副骨架的**静止姿势**做世界旋转传递的, 源的静止姿势错了, 数学再对也
    只是忠实地传错 —— 这就是"跑步动画变成举手"。所以源必须是真角色, 而真角色只有一种
    建法, 就是这一个。"""
    bridge = cabmap_state.BRIDGE
    bridge.use_session(session_key)
    assets, roots, seed_roots, _clips, _scenes = bridge.import_cabs([host_cab])
    db = bridge_asset_db.BridgeAssetDatabase(
        assets, clip_curve_blobs=bridge.clip_curves_by_guid,
        mesh_blobs=bridge.mesh_blobs_by_guid, asset_paths=bridge.asset_paths_by_guid)
    guid = seed_roots.get(host_cab) or (roots[0] if roots else None)
    prefab_file = db.load_guid(guid) if guid else None
    if prefab_file is None:
        raise CrossGameRetargetError("The host character's own asset could not be resolved.")
    # 关掉的三样都只关掉"看得见的部分", 一根骨头都不动:
    #   * clip 发现 —— 这条路要的 clip 是用户点的那些, 由 build_selected_animations 烘,
    #     宿主自己那一整柜子动画一条都不需要;
    #   * 材质与贴图 —— 骨架在 _import_prefab_core 里是**第一件事**, 只读 prefab 的
    #     transform 层级, 建完了才轮到网格/材质/贴图。实测同一个宿主开与关两种导法,
    #     454 根骨静止矩阵 max|delta| = 0.0000000000, 而耗时 7.2s -> 2.7s。宿主是停在
    #     隐藏 collection 里的源骨架, 没人看它的材质。
    # 其余选项原样透传: 凡是可能影响骨架的, 宿主必须和用户手动导入的那一份是同一份。
    host_options = dict(options or {})
    host_options["import_animations"] = False
    host_options["import_materials"] = False
    host_options["import_textures"] = False
    known = set(bpy.data.objects)
    report = prefab_importer.import_prefab_from_db(context, db, prefab_file, host_options)
    _park_in_host_collection(context, [obj for obj in bpy.data.objects if obj not in known])
    if report.armature is None:
        raise CrossGameRetargetError(
            "The host character built no skeleton to bake the clips onto.")
    return report.armature


def _stamped_avatar_name(arm_obj):
    """The name of the Avatar stamped on this armature at import time, "" for a rig
    carrying none -- the host's identity, read back off the host itself rather than
    trusted from a cache entry."""
    raw = armature_builder.read_avatar_json(arm_obj)
    if not raw:
        return ""
    try:
        return str(json.loads(raw).get("m_Name") or "")
    except ValueError:
        return ""


def _host_rig_for(context, session_key, clip_cab, clip_crcs, options):
    """(host armature, its rig maps) for the clips' own source character, built once
    per install+avatar and reused for every later batch.

    The cache holds a NAME, so reusing an entry re-reads the identity off the object it
    resolves to (its stamped Avatar) instead of believing the entry: a name can come to
    mean a different rig -- another .blend opened, the host renamed or replaced -- and a
    silently wrong source is precisely the failure this whole path exists to end."""
    _avatar_file, source = _resolve_source_avatar(session_key, clip_cab, clip_crcs)
    key = (session_key, source.name)
    cached = bpy.data.objects.get(_HOST_RIG_CACHE.get(key) or "")
    if (cached is None or cached.type != "ARMATURE"
            or _stamped_avatar_name(cached) != source.name):
        _HOST_RIG_CACHE.pop(key, None)
        cached = _build_host_rig(context, session_key,
                                 _resolve_host_cab(session_key, source), options)
        _HOST_RIG_CACHE[key] = cached.name
    maps = prefab_importer.maps_from_stamped_armature(cached)
    if maps is None:
        raise CrossGameRetargetError(
            "The host character '{0}' carries no Unity rig identity.".format(cached.name))
    return cached, maps


def retarget_clips_onto(context, session_key, clip_cab, clip_guids, db, dest_arm, options,
                        display_names=None, activate=False):
    """Import ``clip_guids`` (already resolved into ``db``, a source-game closure) onto
    ``dest_arm`` -- a rig that names a bone table -- by way of the clips' OWN host
    character. Resolves the avatar the clips measurably bind to, resolves and imports the
    character prefab rooted on that avatar, bakes the clips onto it, and hands the whole
    table (mappings AND settings) to AnimationRetarget. Returns (product_actions,
    warnings); raises CrossGameRetargetError when no avatar covers the clips, no host
    prefab is rooted on it, or the named table is unreadable/empty."""
    addon = _addon()
    if addon is None:
        raise CrossGameRetargetError(
            "The AnimationRetarget add-on is not enabled -- cross-game clip import needs its "
            "retarget maths.")
    core, _presets, api = addon

    clip_crcs = set()
    for guid in clip_guids:
        clip = db.clip_curves(guid)
        if clip is None:
            continue
        for channels in clip.transform_channel_lists():
            for channel in channels:
                if channel.path:
                    clip_crcs.add(clip_paths.entry_crc(channel.path))
    if not clip_crcs:
        raise CrossGameRetargetError("The selected clip(s) carry no transform bindings to retarget.")

    # 🔴 先验表, 再导宿主。
    # 顺序反了会出现最难看的失败: 先把一整个来源角色导进场景, 再发现表根本用不上, 然后
    # 抛错 —— 用户看到的是"导入动画结果多了个角色而且没有动画"。表能不能用只跟两侧骨名
    # 有关, 什么都不建就能先答一半: 表读不出来或是空的, 直接停手。
    spec, table_label = table_spec_of(dest_arm)
    mappings = list(spec.get("mappings") or [])
    if not mappings:
        raise CrossGameRetargetError(
            "Armature '{0}' names bone table {1!r}, but it could not be read or is empty."
            .format(dest_arm.name, table_label))

    host_arm, host_maps = _host_rig_for(context, session_key, clip_cab, clip_crcs, options)

    warnings = []
    products = []
    before = set(bpy.data.actions)
    baked_actions = []
    try:
        _built, build_warnings, _actions = prefab_importer.build_selected_animations(
            db, host_arm, host_maps, None, clip_guids, options, display_names)
        warnings.extend(build_warnings)
        baked_actions = [action for action in bpy.data.actions if action not in before]
        if not baked_actions:
            raise CrossGameRetargetError("No source action baked from the selected clip(s).")

        # 表整份原样交给 AnimationRetarget: mappings 与 settings 都是它的, 缺骨骼它自己
        # 跳过并警告, 数学也全是它的。这里一个字节都不改, 所以同一张表手动跑和自动跑
        # 出来的产物是同一个。
        results, errors = api.retarget_actions(
            host_arm, dest_arm, mappings, spec.get("settings") or {}, baked_actions)
        for action_name, message in errors:
            warnings.append("{0}: {1}".format(action_name, message))
        products = [dest_action for _source_action, dest_action, _info in results]
        if not products:
            raise CrossGameRetargetError(
                "Nothing retargeted onto '{0}' -- bone table '{1}' matched no shared bone.".format(
                    dest_arm.name, table_label))
        if activate or len(clip_guids) == 1 or not _has_action(dest_arm):
            core.assign_action(dest_arm, products[0])
    finally:
        _discard_baked(baked_actions)
    return products, warnings


def load_clips_onto(context, session_key, clip_cab, clip_guids, db, dest_arm, maps, options,
                    display_names=None, activate=False):
    """THE animation-loading entry point. Every panel calls this and nothing else.

    Whether a clip needs a cross-game retarget is one decision, made from one fact --
    the game stamped on the target armature versus the game whose session the clip
    came from -- so it is made here, once. A second copy of this branch in a game's
    own panel is how the two quietly stop agreeing.

    Returns (built, warnings): ``built`` counts the actions that ended up on
    ``dest_arm``.

    ``display_names`` ({guid: name}) names the products after something the caller
    knows better than the clip does -- see build_selected_animations. It rides both
    branches, so a retargeted product is the same name plus the destination suffix.

    ``activate`` makes the first product the armature's active action even when the
    batch holds several clips -- for a caller whose N clips are one user pick, not N.
    Rides both branches too, so which one it is never changes what the user sees.
    """
    if maps is None:
        maps = prefab_importer.maps_from_stamped_armature(dest_arm)

    # 走哪条路是**量出来的**, 不是标签判的: clip 的绑定路径有多少落在目标骨架的
    # 身份上。够高 = 就是这套骨架(哪怕骨骼全被改过名), 直接精确改绑写入, 零重定向
    # 误差; 不够 = 另一套骨架, 才需要表 + 重定向数学。
    # 这里刻意不问"哪个游戏": 游戏不是骨架的身份 —— 一个游戏有多套互不兼容的骨架,
    # 两个游戏可能共用一套, OC 骨架根本没有游戏, 拿游戏当判据对三者全部失效。
    # 🔴 指名了对照表 = 走重定向, 到此为止。这里**不再判断"要不要"**。
    #
    # 曾经在这里加过"路径匹配率够高就直接绑定, 省掉重定向"。那是错的, 而且错得很隐蔽:
    # 路径对得上只说明骨骼**是同一批**, 不说明骨轴一样。泛型 clip 的曲线是源骨架
    # 自己骨轴下的逐骨局部旋转, 目标骨架若为了 Blender 的 .L/.R 镜像重排过朝向/roll,
    # 直接把局部旋转灌进去就是一堆乱的 —— 而匹配率还是接近 100%, 判据完全看不出来。
    # 吸收轴向差异正是重定向(过两副骨架静止姿势的世界旋转传递)在做的事, 所以有表就用表,
    # 数学全交 AnimationRetarget, 不在这里另立一套判断。
    if table_name_of(dest_arm):
        products, warnings = retarget_clips_onto(
            context, session_key, clip_cab, clip_guids, db, dest_arm, options,
            display_names, activate)
        return len(products), warnings

    if maps is None:
        raise CrossGameRetargetError(
            "'{0}' carries no Unity rig identity -- import the character through this "
            "add-on once, then animations attach to it from then on.".format(dest_arm.name))
    ratio, checked = _binding_match(db, clip_guids, maps["path_to_bone"])
    if checked and ratio == 0.0:
        raise CrossGameRetargetError(
            "None of the clip's curve paths match armature '{0}', and it names no bone "
            "table to retarget through -- select the right skeleton, or set its {1} "
            "property to a preset name.".format(dest_arm.name, RETARGET_TABLE_PROP))

    built, warnings, actions = prefab_importer.build_selected_animations(
        db, dest_arm, maps, None, clip_guids, options, display_names, activate)
    if checked and ratio < 0.5:
        warnings.insert(0, "Only {0:.0%} of curve paths match armature '{1}' -- "
                           "imported anyway.".format(ratio, dest_arm.name))
    warnings.extend(_retarget_faces(context, session_key, clip_cab, clip_guids, db,
                                    dest_arm, options, actions))
    return built, warnings


def _retarget_faces(context, session_key, clip_cab, clip_guids, db, dest_arm, options,
                    actions):
    """Restate each clip's baked facial performance on this rig, when the user asked
    for it and the clip's game states what a face IS.

    Lives on the ONE clip-loading path for the same reason the cross-game branch does:
    a body clip and its face are one import, and a second copy of this decision in a
    game's own panel is how the two stop agreeing. The host stays game-blind -- it asks
    the registry whether this game contributed a facial restatement and passes the clip
    through; a game with no facial system contributes none and nothing happens.

    ``actions`` is that clip's own {guid: (action, slot)} from the body build, and the
    face goes INTO it. Two reasons, both measured, and both invisible without it:

    * an object plays ONE action, so a face on its own action replaces the body it
      arrived with -- whichever the user assigns, the other half stops playing;
    * the body build already keyed the SOURCE character's facial bones onto this rig
      (same standard bone names, so they bind), which is the untranslated geometry this
      whole feature exists to avoid. Writing the face into the same action lets it
      REPLACE those channels instead of losing a fight with them.
    """
    if not options.get("retarget_face"):
        return []
    provider = Game.face_retarget_of(cabmap_state.game_of(session_key)
                                     or armature_builder.read_game(dest_arm))
    if provider is None:
        return []
    reports = []
    for guid in clip_guids:
        try:
            clip = source_anchored_clip(session_key, clip_cab, guid, db)
        except Exception as exc:
            reports.append("Face retarget skipped for one clip: {0}".format(exc))
            continue
        if clip is None:
            continue
        try:
            report = provider(context, dest_arm, clip, options, actions.get(guid))
        except Exception as exc:
            # Never silent: the body animation DID land, so a face that did not is a
            # partial result the user has to be told about by name.
            reports.append("Face retarget skipped for '{0}': {1}".format(clip.name, exc))
            continue
        if report:
            reports.append(report)
    return reports


def source_anchored_clip(session_key, clip_cab, guid, db):
    """The clip with its curves anchored to the rig it was AUTHORED on, not to the
    one it is being played on.

    A clip imported on its own carries no readable bindings at all -- measured: all
    359 curve paths of a real UI clip arrive as ``path_0x<CRC32>_...`` placeholders,
    because AssetRipper can only restore a binding to a string when that skeleton is
    in the export scope. The normal import path then repairs them against the
    DESTINATION rig, which is exactly right for playing body motion on it.

    For a face it is not. Reading the performance means asking "where was this bone
    relative to ITS OWN rest", and the destination's skeleton answers a different
    question -- it happens not to explode only because these characters share a bone
    vocabulary. So the source skeleton is resolved and loaded here as a dependency
    (by CRC coverage of the clip's own bindings -- measurement, not the clip's name)
    and a FRESH copy of the curves is anchored to it; the destination-repaired clip
    the body import already produced is left alone.
    """
    blob = cabmap_state.BRIDGE.clip_curves_by_guid.get(guid) if cabmap_state.BRIDGE else None
    if blob is None:
        # No raw blob (a disk-mode session): the db's clip is all there is.
        return db.clip_curves(guid)

    from .RuriRipperPyBridge.unity import clip_curves as clip_curves_module
    clip = clip_curves_module.ClipCurves.from_blob(blob[0], blob[1])
    crcs = {clip_paths.entry_crc(channel.path)
            for channels in clip.transform_channel_lists()
            for channel in channels if channel.path}
    if not crcs:
        return clip

    unity_file, score = _resolve_source_avatar(session_key, clip_cab or guid, crcs)
    document = unity_file.first("Avatar") if unity_file is not None else None
    if document is None:
        raise CrossGameRetargetError(
            "the skeleton these curves were authored on could not be read back")
    paths = avatar_module.transform_paths(document.data)
    source_paths = {path: path.rsplit("/", 1)[-1] for path in paths if path}
    repaired, unmatched = clip_paths.repair_hashed_clip_paths(clip, source_paths)
    if repaired == 0:
        raise CrossGameRetargetError(
            "'{0}' covers none of this clip's bindings once loaded".format(score.name))
    print("[face] anchored '{0}' to source rig '{1}' ({2:.0%} binding coverage) · "
          "{3} curve(s) repaired, {4} unmatched".format(
              clip.name, score.name, score.coverage, repaired, unmatched), flush=True)
    return clip


def _binding_match(db, clip_guids, path_to_bone):
    """Best fraction of any clip's transform-curve paths that resolve to a bone of the
    target rig, and whether anything was checkable. A clip with no data, or one that
    fails to parse, is skipped -- the real per-clip complaint fires at build time."""
    best = 0.0
    checked = False
    for guid in clip_guids:
        try:
            clip = db.clip_curves(guid)
        except ValueError:
            continue
        if clip is None:
            continue
        ratio, total = clip_paths.clip_path_match_ratio(clip, path_to_bone)
        if total:
            checked = True
            best = max(best, ratio)
    return best, checked
