"""A material a file links from a library is drawn through a twin compiled in this session.

A library's material carries its record, and the record is content of the library file. The graph a stack
compiles from a record is plugin data (see :mod:`plugin_data`), and a linked material can hold nothing this
session writes: a file never writes the data it links. So a stack compiles the linked record into a twin that
is runtime data, and every user the session draws through is pointed at it:

- users inside linked data (the meshes of a level linked into a shot) take the twin directly; linked data is
  never written, so nothing more is owed;
- local users (a character's override mesh, a local object) are moved with ``user_remap``. They ARE written,
  so for the length of every save they go back to the library's material and come forward again after it:
  the file keeps saying what it links, never a twin that is not in it.

The load pass hands every user back before it drops plugin data (the twins with it), so reloading the stacks
finds the file as saved and compiles the twins anew. A file being opened frees the previous one whole; its
pairs are simply forgotten.

A twin can still leave its session: ``bpy.data.libraries.write`` -- the copy buffer behind copy and paste --
writes runtime data like any other, and a save made without this plugin has no guard at all. Every twin
therefore names the material it stands for (:data:`ORIGIN`: the library's absolute path and the material's
name). A stray twin -- one carrying that name that no pair of this session holds -- is handed back to its
material wherever it turns up: before a save (a material in this very file takes its users for good, a
library's material is paired with the stray so the session keeps drawing it), and in the load pass before
plugin data is dropped (every user goes back for good, and a stray a library carries is removed). A stray
whose material cannot be found is named, never silently dropped with its users' slots left empty."""

from __future__ import annotations

import os

import bpy

from . import plugin_data

ORIGIN = "ruri_twin_origin"

_PAIRS = []
_HELD = []


def _alive(pair):
    try:
        return bool(pair[0].name) and bool(pair[1].name)
    except ReferenceError:
        return False


def _normalized(path):
    return os.path.normcase(os.path.normpath(path))


def _library_path(library):
    return _normalized(bpy.path.abspath(library.filepath, library=library.parent))


def _this_file():
    return _normalized(bpy.data.filepath) if bpy.data.filepath else None


def twinned(original):
    """Whether this session already draws ``original`` (a linked material) through a twin."""
    return any(_alive(pair) and pair[0] == original for pair in _PAIRS)


def _repoint_linked(user, current, wanted):
    """Point a linked datablock's slots that hold ``current`` at ``wanted``. A kind of user that keeps a
    material anywhere else (a node socket, a grease pencil layer) is named: it keeps drawing ``current``."""
    moved = 0
    if isinstance(user, bpy.types.Object):
        for slot in user.material_slots:
            if slot.link == 'OBJECT' and slot.material == current:
                slot.material = wanted
                moved += 1
    else:
        materials = getattr(user, "materials", None)
        if materials is not None:
            for index, material in enumerate(materials):
                if material == current:
                    materials[index] = wanted
                    moved += 1
    if not moved:
        print("[linked-twins] !! {0} {1} keeps {2}: it holds the material outside a material slot".format(
            type(user).__name__, user.name_full, current.name_full), flush=True)
    return moved


def _move_users(current, wanted):
    """Every user of ``current`` draws ``wanted``: linked users slot by slot, local ones with ``user_remap``."""
    for user in bpy.data.user_map(subset=[current]).get(current, ()):
        if user.library is not None:
            _repoint_linked(user, current, wanted)
    current.user_remap(wanted)


def adopt(pairs):
    """``pairs`` of (linked material, its compiled twin): every user now draws the twin. Returns the linked
    slots repointed (local users move with ``user_remap``, which reports no count)."""
    if not pairs:
        return 0
    users = bpy.data.user_map(subset=[original for original, _twin in pairs])
    moved = 0
    for original, twin in pairs:
        twin[ORIGIN] = {"library": _library_path(original.library), "material": original.name}
        for user in users.get(original, ()):
            if user.library is not None:
                moved += _repoint_linked(user, original, twin)
        original.user_remap(twin)
        _PAIRS.append((original, twin))
    return moved


def _strays():
    held = {pair[1] for pair in _PAIRS if _alive(pair)}
    return [material for material in bpy.data.materials
            if material.get(plugin_data.MARK) and material.get(ORIGIN) is not None and material not in held]


def _original_of(stray):
    """The material ``stray`` stands for, linked in from its library when this file does not hold it yet;
    ``None`` when neither this file nor the library has it."""
    origin = stray[ORIGIN]
    path = origin["library"]
    name = origin["material"]
    if path == _this_file():
        return bpy.data.materials.get((name, None))
    for library in bpy.data.libraries:
        if _library_path(library) == path:
            found = bpy.data.materials.get((name, library.filepath))
            if found is not None:
                return found
    if not os.path.isfile(path):
        return None
    with bpy.data.libraries.load(path, link=True) as (source, target):
        if name not in source.materials:
            return None
        target.materials = [name]
    return target.materials[0]


def _unfound(stray):
    origin = stray[ORIGIN]
    return "[linked-twins] {0} is a twin that left its session, and {1} is not in {2}: its users lose it".format(
        stray.name_full, origin["material"], origin["library"])


def return_strays():
    """The load pass, before plugin data is dropped or anything is compiled: every stray twin's users go back
    to the material it stands for and the stray is removed (the stacks then compile a twin of their own).
    Returns the strays whose material cannot be found, named."""
    unfound = []
    for stray in _strays():
        original = _original_of(stray)
        if original is None:
            unfound.append(_unfound(stray))
            continue
        _move_users(stray, original)
        print("[linked-twins] stray twin {0} handed back to {1}".format(stray.name_full, original.name_full), flush=True)
        bpy.data.materials.remove(stray)
    return unfound


def release():
    """Hand every user back to its linked material and forget the twins. The load pass calls this before it
    drops plugin data; pairs whose datablocks are already freed (a file was opened) are only forgotten."""
    live = [pair for pair in _PAIRS if _alive(pair)]
    if live:
        users = bpy.data.user_map(subset=[twin for _original, twin in live])
        for original, twin in live:
            for user in users.get(twin, ()):
                if user.library is not None:
                    _repoint_linked(user, twin, original)
            twin.user_remap(original)
    del _PAIRS[:]
    del _HELD[:]


def _settle_strays():
    for stray in [stray for stray in _strays() if stray.library is None]:
        original = _original_of(stray)
        if original is None:
            print("[linked-twins] !! " + _unfound(stray), flush=True)
        elif original.library is None:
            _move_users(stray, original)
            print("[linked-twins] stray twin {0} handed back to {1} for good".format(
                stray.name_full, original.name_full), flush=True)
        else:
            plugin_data.born(stray)
            adopt([(original, stray)])
            print("[linked-twins] stray twin {0} adopted as the twin of {1}".format(
                stray.name_full, original.name_full), flush=True)


@bpy.app.handlers.persistent
def _before_save(*_args):
    _settle_strays()
    live = [pair for pair in _PAIRS if _alive(pair)]
    if not live:
        return
    users = bpy.data.user_map(subset=[twin for _original, twin in live])
    for original, twin in live:
        if any(user.library is None for user in users.get(twin, ())):
            twin.user_remap(original)
            _HELD.append((original, twin))


@bpy.app.handlers.persistent
def _after_save(*_args):
    for original, twin in _HELD:
        if _alive((original, twin)):
            original.user_remap(twin)
    del _HELD[:]


_HANDLERS = (("save_pre", _before_save), ("save_post", _after_save), ("save_post_fail", _after_save))


def register():
    for chain_name, handler in _HANDLERS:
        chain = getattr(bpy.app.handlers, chain_name)
        if handler not in chain:
            chain.append(handler)


def unregister():
    for chain_name, handler in _HANDLERS:
        chain = getattr(bpy.app.handlers, chain_name)
        if handler in chain:
            chain.remove(handler)
