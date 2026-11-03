"""The one way anything reaches a document: seeds in, the host's own objects out.

A seed is the payload of a published row. What it IS -- which archives, which parts at which
level, which window of a level -- is answered once, on the reader side, by the statement source
of the game that published it; the reader's own resolution answers an archive, a container path
or a container folder of the loaded map. This asks for that answer in the host's basis and hands
it to the host. There is no second road: a panel's row, a browser selection, a stage's art and a
retarget's host rig all load by handing over seeds, and nothing here names a game or a host.
"""

from __future__ import annotations

from .. import host as host_port
from .. import statement as kernel_statement
from . import texturing

#: How one argument carries several seeds: one per line. A seed never spans lines.
SEED_LINES = "\n"


def seeds_in(text):
    """The seeds a command argument carries."""
    return [seed for seed in str(text or "").split(SEED_LINES) if seed.strip()]


def seed_lines(seeds):
    """Several seeds as one command argument."""
    return SEED_LINES.join(seed for seed in seeds if seed)


def statement(seeds, options=None):
    """What these seeds are, as this host reads them: in its basis, with the image containers it
    decodes, through the texture-role layers the options state for this install, at the detail
    level and with the renderers the options ask for. The options that cross are the questions
    about the selection; the rest are about what the host then does with the answer."""
    host = host_port.current()
    values = dict(options or {})
    return kernel_statement.Statement.of(
        list(seeds), basis=host.basis,
        detail=int(values.get("detail_level", 0) or 0),
        inactive=bool(values.get("import_inactive", True)),
        shadow_proxies=bool(values.get("import_shadow_proxies", False)),
        colliders=bool(values.get("import_colliders", False)),
        containers=list(host.texture_containers() or ()),
        roles=list(values.get("role_layers") or ()))


def read(seeds, options=None):
    """The statement these seeds make, already flattened -- the half of a load that crosses to the
    reader and touches no host, which is what a stepped command runs off the main thread."""
    stated = statement(seeds, options)
    stated.roots
    return stated


def place(context, stated, options=None, report=None):
    """The other half: hand a read statement to the host, and file the texture properties no
    role layer names under the product the options name, for the panel that asks what each one
    is. Returns :class:`Kernel.statement.Built`."""
    values = dict(options or {})
    built = host_port.current().materialise(context, stated, values, report)
    unmapped = [(material, name) for material in stated.materials.values()
                for name in material.roles.unmapped]
    if unmapped:
        textures = stated.textures
        for material, name in unmapped:
            texture = textures.get(material.textures.get(name, ""))
            texturing.record(values.get("role_product", ""), name, material.name,
                             texture.name if texture is not None else "")
    return built


def load(context, seeds, options=None, report=None):
    """Put what these seeds are into the document: :func:`read`, then :func:`place`."""
    from ..bridge import cabmap_state

    seeds = [seed for seed in seeds if seed]
    if not seeds:
        return kernel_statement.Built(warnings=["Nothing to load: the selection states no seed."])
    if cabmap_state.BRIDGE is None:
        return kernel_statement.Built(warnings=["No install is open to load from."])
    with read(seeds, options) as stated:
        return place(context, stated, options, report)


#: Fewer bound transform paths than this share and a clip was written for another skeleton: a skeleton
#: either is the one a clip was written for (or a variant of it that still binds nearly every path) or
#: shares no more than the odd standard name with it.
FOREIGN_SHARE = 0.05


def _split_by_binding(clips, paths):
    own, foreign = [], []
    for clip in clips:
        wanted = clip.bindings
        landing = sum(1 for path in wanted if path in paths)
        (foreign if wanted and landing < FOREIGN_SHARE * len(wanted) else own).append(clip)
    return own, foreign


def perform(context, seeds, rig=None, options=None, activate=False):
    """Put the performances these seeds carry onto a rig -- the one the caller names, else the one
    in front of the user. The curves arrive re-anchored on that rig's own bones and solved against
    the avatar it carries, so what the host does is key them. A clip written for a skeleton the rig
    is not binds to none of it and goes through :func:`_carry` instead.

    A face baked into a clip's bone tracks means nothing on another character's rig, so when the
    options ask for it and the game that owns the clips restates faces
    (``Game.GameModule.face_retarget``), each clip that landed is handed to that game with where
    it landed, and the face is written into that same performance. Returns (built, lines)."""
    from ... import Game

    host = host_port.current()
    values = dict(options or {})
    seeds = [seed for seed in seeds if seed]
    target = host_port.selected_rig(context) if rig is None else rig
    if not seeds:
        return 0, ["Nothing to play: the selection states no seed."]
    if target is None:
        return 0, ["Select the rig to play onto first."]
    with statement(seeds, values) as stated:
        paths = host.rig_paths(target)
        clips = stated.clips(paths=paths, avatar=host.rig_avatar(target))
        own, foreign = _split_by_binding(clips, set(paths))
        landed, lines = host.play(context, target, own, values, activate) if own or not foreign else ({}, [])
        if foreign:
            carried, said = _carry(context, stated, target, foreign, values, activate and not landed)
            landed.update(carried)
            lines.extend(said)
    restate = Game.face_retarget_of(values.get("source_game", "")) if values.get("retarget_face") else None
    if restate is not None:
        for clip in clips:
            if clip.key in landed:
                said = restate(context, target, clip, values, landed[clip.key])
                if said:
                    lines.append(said)
    return len(landed), lines


def _carry(context, stated, target, clips, values, activate):
    """Clips written for another skeleton: keyed on a stand-in rig of the skeleton the reader measures
    each one was written for, carried across by the host, and the stand-in taken out again."""
    host = host_port.current()
    if not isinstance(host, host_port.Retarget):
        raise RuntimeError("{0} cannot play clips written for another skeleton.".format(host.name))
    sources = {}
    for clip in clips:
        seed = kernel_statement.source_host(clip.bindings, near=clip.archive)
        if not seed:
            raise LookupError("No prefab of this install is rooted on the skeleton {0} was written for.".format(clip.name))
        sources.setdefault(seed, []).append(clip)
    landed = {}
    lines = []
    for seed, group in sources.items():
        with statement([seed], values) as source:
            (key,) = source.skeletons
            stand_in = host.stand_in_rig(context, source, key, values)
        try:
            keys = {clip.key for clip in group}
            keyed = [clip for clip in stated.clips(paths=host.rig_paths(stand_in), avatar=host.rig_avatar(stand_in))
                     if clip.key in keys]
            built, played = host.play(context, stand_in, keyed, values, False)
            carried, said = host.retarget(context, stand_in, target, values.get("source_game", ""), built,
                                          activate and not landed)
        finally:
            host.discard_rig(context, stand_in)
        landed.update(carried)
        lines.extend(played + said)
    return landed, lines
