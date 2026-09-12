"""Where this game's characters keep their geometry.

A character prefab here ships the rig and the renderers, and every renderer's own
mesh reference is EMPTY: the game attaches the meshes at run time from a list it
keeps beside them, one (transform, asset path) pair per piece, each fetched by
hashing the path the way every address in this game is hashed. Import the prefab
without that step and you get a skeleton wearing nothing.

The list is read on the hook side (``exilium.model.meshes``); this side is the
registration that lets the ONE prefab-import path ask for it, and the join from a
mesh's own name to the document the closure carries for it. A renderer that does
carry its own mesh never reaches here, and neither does any other game's.
"""

from __future__ import annotations

from ...RuriRipperPyBridge.session import cabmap_state
from ...RuriRipperPyBridge.unity import discovery
from . import datasets

# prefab identity -> {renderer name: mesh name}. One prefab's list is read once
# however many renderers ask for it, and a closure holds one prefab per import.
_MANIFESTS = {}

# id(db) -> {mesh name (lowercased): guid}. The list names a mesh by the name the
# mesh itself carries, which is the only identity the two sides share: the
# catalog's container is the game's own asset guid while the closure is keyed by
# the exporter's, and those are different namespaces.
_MESH_NAMES = {}


def _manifest(db, prefab_file):
    """{renderer name: mesh name} for one prefab, or {} when it keeps no list --
    which is what every prefab of every other kind looks like."""
    key = getattr(prefab_file, "path", None) or id(prefab_file)
    if key in _MANIFESTS:
        return _MANIFESTS[key]
    manifest = {}
    text = _text(db, prefab_file)
    if text and "MeshResPath" in text:
        try:
            for row in datasets.role_meshes(text):
                manifest[str(row["transform"])] = (str(row["name"]), int(float(row["lod"] or -1)))
        except Exception:
            manifest = {}
    _MANIFESTS[key] = manifest
    return manifest


def _text(db, prefab_file):
    """The prefab's own serialized document. It is the closure that holds the bytes,
    keyed by the identity the file carries, so this asks the database rather than
    the parsed file -- the list lives in a component the parse does not surface."""
    identity = getattr(prefab_file, "path", None)
    if identity is None:
        return ""
    try:
        return db.raw_text(identity) or ""
    except Exception:
        return ""


def _mesh_guids(db):
    """{mesh name: guid} over a closure, built by peeking each document's class and
    name rather than parsing it -- the same bounded sniff the browser uses."""
    key = id(db)
    if key in _MESH_NAMES:
        return _MESH_NAMES[key]
    index = {}
    for guid in db.all_guids():
        text = db.raw_text(guid)
        if not text:
            continue
        class_name, name = discovery.peek_class_and_name(text)
        if class_name == "Mesh" and name:
            index.setdefault(name.lower(), guid)
    _MESH_NAMES[key] = index
    return index


def provide(db, prefab_file, renderer, options=None):
    """The mesh this renderer draws, as the prefab's own list states it. None when
    this prefab keeps no such list, or the closure carries no mesh by that name --
    the caller then reports a renderer with no geometry, as it always did.

    Which detail level a renderer draws is stated by this same list, but that is a
    different question and it is answered by ``detail`` below -- this one only says
    which mesh."""
    if prefab_file is None:
        return None
    declared = _manifest(db, prefab_file).get(renderer.name)
    if not declared:
        return None
    guid = _mesh_guids(db).get(declared[0].lower())
    return {"guid": guid} if guid else None


def detail(db, prefab_file, level, options=None):
    """Whether a renderer is at the wanted detail level, for a game that ships no
    LODGroups at all.

    This game states the level in its own per-prefab mesh list, so the host's pass
    over the engine's LOD components finds nothing to skip and a character arrives
    wearing every level at once. Returns None for a prefab that keeps no such list,
    which leaves the engine's own declaration deciding -- the right answer for the
    props and scenery that do carry LODGroups.

    A renderer the list does not mention is at no level and is always kept: it is
    not a detail variant of anything, it is simply something else."""
    if prefab_file is None:
        return None
    manifest = _manifest(db, prefab_file)
    if not manifest:
        return None
    stated = {lod for _name, lod in manifest.values() if lod >= 0}
    if not stated:
        return None
    # A level this prefab does not author contributes its nearest one instead of
    # nothing, exactly as a LODGroup that stops short of the wanted level does.
    wanted = min(stated, key=lambda candidate: (abs(candidate - level), candidate))

    def at_level(renderer):
        declared = manifest.get(renderer.name)
        return declared is None or declared[1] < 0 or declared[1] == wanted
    return at_level


def mesh_cabs(prefab_text):
    """The cabs holding the meshes one prefab's list names, so an import can seed
    them alongside the prefab: a mesh routinely lives in a different archive, and
    a closure that never loaded it has nothing for the resolver to find."""
    cabs = []
    for row in datasets.role_meshes(prefab_text):
        cab = str(row["cab"] or "")
        if cab and cab not in cabs:
            cabs.append(cab)
    return cabs


# address -> the cabs its meshes need. Reading the list costs one closure resolve,
# and this game's archives are pooled, so that resolve is the expensive part of a
# character load -- pay it once per character per session.
_SEEDS = {}


def seeds_for(address, cabs, asset_name):
    """Everything an import of ``address`` has to seed: the prefab's own cabs plus
    the ones holding the meshes its list names. Reads the list off a GameObject-only
    export -- the prefab document is all this needs, and the geometry it is about to
    ask for would be exported twice otherwise."""
    if address in _SEEDS:
        return _SEEDS[address]
    seeds = list(cabs)
    text = _prefab_text(cabs, asset_name)
    if text and "MeshResPath" in text:
        for cab in mesh_cabs(text):
            if cab not in seeds:
                seeds.append(cab)
    _SEEDS[address] = seeds
    return seeds


def _prefab_text(cabs, asset_name):
    """The prefab document itself, out of a closure exported for GameObjects only."""
    from ...RuriRipperPyBridge.unity import class_registry

    game_object = class_registry.id_for_name("GameObject")
    try:
        assets, roots, _seeds, _clips, _scenes = cabmap_state.BRIDGE.import_cabs(
            list(cabs), [game_object] if game_object is not None else None)
    except Exception:
        return ""
    paths = cabmap_state.BRIDGE.asset_paths_by_guid or {}
    wanted = (asset_name or "").lower() + ".prefab"
    for guid in roots:
        leaf = (paths.get(guid) or "").replace("\\", "/").rsplit("/", 1)[-1]
        if leaf.lower() == wanted:
            return assets[guid].decode("utf-8", "replace")
    return ""


def forget():
    _MANIFESTS.clear()
    _MESH_NAMES.clear()
    _SEEDS.clear()
