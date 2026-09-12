"""What a game states about its own prefabs that the engine does not.

Two questions come up while reading any prefab, and for most games the engine's
own data answers both. A few games answer them their own way instead -- and that
is a fact about the GAME, not about which application is building the result, so
it is declared here and every host's importer consults the same declaration.

A module registers its answer and SELF-SELECTS by what the prefab itself carries,
returning None to decline. Nothing here learns which games do it, and a game that
does neither registers nothing.
"""

from __future__ import annotations

#: Renderers whose own ``m_Mesh`` is empty. In some games that is not a broken
#: prefab: the prefab ships the rig and the renderers, and the geometry is
#: attached at run time from a list the game keeps beside them. A resolver takes
#: ``(db, prefab_file, renderer, options)`` and returns a mesh ref, or None to
#: decline. First claimant wins; no claimant leaves the renderer with no geometry.
MESH_RESOLVERS = []

#: How a game states which renderers are at which detail level, when it does not
#: state it the way the engine does. The engine's own answer is a LODGroup
#: component listing renderers per level, and that is what a host reads; a game
#: that ships no LODGroups and encodes the level some other way (in the mesh's own
#: name, in a manifest beside the prefab) registers its answer here instead of
#: reaching into the import to special-case itself.
#:
#: A rule takes ``(db, prefab, level, options)`` and returns a PREDICATE over one
#: renderer -- true when that renderer is at the wanted level -- or None to say it
#: has no opinion about this prefab, which is what a game whose models do carry
#: LODGroups should say, so the engine's own declaration keeps deciding.
#:
#: Per renderer rather than as a set of fileIDs because that is the shape the data
#: has: a LODGroup lists fileIDs, but a game that states the level in the mesh's
#: own name has a name per renderer and no fileID index to answer with.
DETAIL_RULES = []


def register_mesh_resolver(resolver):
    if resolver not in MESH_RESOLVERS:
        MESH_RESOLVERS.append(resolver)


def unregister_mesh_resolver(resolver):
    if resolver in MESH_RESOLVERS:
        MESH_RESOLVERS.remove(resolver)


def register_detail_rule(rule):
    if rule not in DETAIL_RULES:
        DETAIL_RULES.append(rule)


def unregister_detail_rule(rule):
    if rule in DETAIL_RULES:
        DETAIL_RULES.remove(rule)


def mesh_ref(db, prefab_file, renderer, options):
    """Whatever the game that owns this prefab says belongs on a renderer with no
    mesh of its own, or None when nobody claims it."""
    for resolver in MESH_RESOLVERS:
        resolved = resolver(db, prefab_file, renderer, options)
        if resolved:
            return resolved
    return None


def detail_test(db, prefab, level, options):
    """The game's own verdict on which renderers are at ``level``, or None when it
    states none and the engine's own LOD declaration should decide."""
    for rule in DETAIL_RULES:
        stated = rule(db, prefab, level, options)
        if stated is not None:
            return stated
    return None
