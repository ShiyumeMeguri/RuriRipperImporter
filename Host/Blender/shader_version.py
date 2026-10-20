"""Which generated shaders this session draws with are an older generation than the products installed beside the
add-on, and the update that brings them current while every input stays as the file keeps it.

A generated stack is a package of products (node-group libraries and a manifest) plus the runtime script that turns
a material's record into a node graph. The record is the file's content; the graph, the vertex stack and the
post chain are derived from it and are never written to a .blend (see :mod:`plugin_data`). So a shader can only be
an older generation than the products in this session: the products were replaced on disk after the package
loaded, or graphs were compiled by a generation that has since been replaced.

An update replaces the package's generation in place and compiles again only what the package made, the way the
load pass does after a file opens, but without dropping what is not the package's: the level's medium, its
images, the lights. No record is written except by a rename the user chose (see :mod:`shader_inputs`).
"""

from __future__ import annotations

import collections
import os

import bpy

from . import derived_state, linked_twins, material_builder, material_panel, shader_inputs
from ... import Game

#: The key every template node group of a product carries its generation's stamp under.
GROUP_STAMP = "ruri_stamp"
STACK = "ruri_uber_stack"
PART = "ruri_uber_part"
CURRENT = "current"
OLD = "old"
MISSING = "missing"


class StackState:
    """One stack of a package: the generation it runs, the one installed, and how its materials stand."""

    __slots__ = ("key", "loaded", "installed", "counts", "linked", "legacy")

    def __init__(self, key, loaded, installed):
        self.key = key
        self.loaded = loaded
        self.installed = installed
        self.counts = collections.Counter()
        self.linked = 0
        self.legacy = []

    @property
    def replaced(self):
        return self.installed != self.loaded


class PackageState:
    """One generated package (a game's stacks for this host) and what scanning found out about it."""

    __slots__ = ("package", "label", "stacks", "failure", "changes", "blockers")

    def __init__(self, package):
        self.package = package
        self.label = label_of(package)
        self.stacks = []
        self.failure = ""
        self.changes = []
        self.blockers = []

    @property
    def replaced(self):
        return any(stack.replaced for stack in self.stacks)

    @property
    def stale_materials(self):
        return sum(stack.counts[OLD] for stack in self.stacks)

    @property
    def uncompiled_materials(self):
        return sum(stack.counts[MISSING] for stack in self.stacks)

    @property
    def legacy_materials(self):
        return [name for stack in self.stacks for name in stack.legacy]

    @property
    def outdated(self):
        if self.failure:
            return False
        uncompiled = self.uncompiled_materials > 0 and not derived_state.pending()
        return self.replaced or self.stale_materials > 0 or uncompiled


class Result:
    """What an update did, in numbers the panel and the report line say."""

    __slots__ = ("packages", "recompiled", "identical", "differing", "renamed", "vertex_objects", "failures")

    def __init__(self):
        self.packages = []
        self.recompiled = 0
        self.identical = 0
        self.differing = []
        self.renamed = 0
        self.vertex_objects = 0
        self.failures = []

    def summary(self):
        if not self.packages:
            return "Shaders are current: nothing to update"
        text = "Updated {0}: {1} material(s) compiled again, records identical on {2}".format(
            ", ".join(self.packages), self.recompiled, self.identical)
        if self.renamed:
            text += ", {0} renamed by your mapping".format(self.renamed)
        if self.differing:
            text += ", {0} differ (see console)".format(len(self.differing))
        if self.vertex_objects:
            text += ", vertex stack rebuilt on {0} object(s)".format(self.vertex_objects)
        if self.failures:
            text += "; the derived state reports an error (see Derived state below)"
        return text


def label_of(package):
    parts = package.__name__.split(".")
    return parts[-3] if len(parts) >= 3 else package.__name__


def _group_state(material, stamp):
    tree = material.node_tree
    if tree is None:
        return MISSING
    groups = [node.node_tree for node in tree.nodes if node.bl_idname == "ShaderNodeGroup"]
    if not groups or any(group is None for group in groups):
        return MISSING
    if any(group.get(GROUP_STAMP) not in (None, stamp) for group in groups):
        return OLD
    return CURRENT


def _saved_old_groups(material, stamp):
    tree = material.node_tree
    if tree is None:
        return False
    return any(node.node_tree is not None and node.node_tree.get(GROUP_STAMP) not in (None, stamp)
               for node in tree.nodes if node.bl_idname == "ShaderNodeGroup")


def _survey(by_key):
    """One pass over the file's materials: per stack how many it draws in each state, which it owns per part (the
    ones a rename can change), how many it links per part, and which linked materials are drawn as they were
    saved -- through shader data an older generation wrote into the library, which this file cannot replace."""
    owned = collections.defaultdict(list)
    linked = collections.defaultdict(list)
    for material in bpy.data.materials:
        entry = by_key.get(material.get(STACK))
        if entry is None or material.users == 0:
            continue
        stack, state = entry
        if material.get(stack.TEMPLATE_KEY) is not None:
            continue
        part = material.get(PART, "")
        if material.library is not None:
            linked[(stack.PANEL_KEY, part)].append(material)
            state.linked += 1
            if not material_builder.twinned(material) and _saved_old_groups(material, stack.STAMP):
                state.legacy.append(material.name_full)
            continue
        state.counts[_group_state(material, stack.STAMP)] += 1
        if linked_twins.ORIGIN not in material:
            owned[(stack.PANEL_KEY, part)].append(material)
    return owned, linked


def scan():
    """Every package this session registered, with what is outdated in it. Reads the manifests on disk and walks
    the file's materials once; draws nothing and writes nothing."""
    states = []
    installed_of = {}
    by_key = {}
    for package in Game.stack_modules():
        state = PackageState(package)
        states.append(state)
        try:
            installed_of[package] = shader_inputs.installed(package)
        except (OSError, ValueError, KeyError) as error:
            state.failure = "{0}: {1}".format(type(error).__name__, error)
            installed_of[package] = {}
        if installed_of[package] and not package.STACKS:
            state.failure = "the shader package is not registered (an update stopped half way): restart Blender"
        for stack in package.STACKS:
            now = installed_of[package].get(stack.PANEL_KEY)
            stack_state = StackState(stack.PANEL_KEY, stack.STAMP, now["stamp"] if now else None)
            state.stacks.append(stack_state)
            if stack.post is None:
                by_key[stack.PANEL_KEY] = (stack, stack_state)
        loaded_keys = {stack.PANEL_KEY for stack in package.STACKS}
        for key, now in installed_of[package].items():
            if key not in loaded_keys:
                state.stacks.append(StackState(key, None, now["stamp"]))
    owned, linked = _survey(by_key)
    for state in states:
        if state.failure:
            continue
        before = shader_inputs.loaded(state.package)
        after = installed_of[state.package]
        state.changes = shader_inputs.changes(
            before, after, lambda key, part: owned.get((key, part), ()), lambda key, part: linked.get((key, part), ()))
        for key, part in shader_inputs.removed_parts(before, after):
            held = len(owned.get((key, part), ())) + len(linked.get((key, part), ()))
            if held:
                state.blockers.append("{0}: part {1} is gone from the installed shaders, {2} material(s) use it".format(
                    key, part, held))
        folder = os.path.dirname(os.path.abspath(state.package.__file__))
        for key, now in after.items():
            if not os.path.isfile(os.path.join(folder, now["blend"])):
                state.blockers.append("{0}: product {1} is missing beside the package".format(key, now["blend"]))
    return states


_HELD = [None, None]


def _signature():
    disk = []
    for package in Game.stack_modules():
        try:
            status = os.stat(shader_inputs.manifest_path(package))
            disk.append((status.st_mtime_ns, status.st_size))
        except OSError:
            disk.append(None)
    return (tuple(disk), len(bpy.data.materials), len(bpy.data.node_groups), len(bpy.data.objects),
            derived_state.pending())


def current(force=False):
    """The scan, kept until the products on disk or the file's datablocks change."""
    signature = _signature()
    if force or _HELD[0] != signature:
        _HELD[0] = signature
        _HELD[1] = scan()
    return _HELD[1]


def _plain(value):
    if isinstance(value, bpy.types.ID):
        return value.name_full
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return _plain(value.to_dict())
    if hasattr(value, "to_list"):
        return _plain(value.to_list())
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _records(materials, stacks):
    return {material.name_full: {key: _plain(material[key]) for key in stacks[material[STACK]].RECORD_KEYS
                                 if key in material}
            for material in materials}


def _owned_materials(stacks):
    return [material for material in bpy.data.materials
            if material.library is None and material.get(STACK) in stacks and material.users > 0
            and material.get(stacks[material[STACK]].TEMPLATE_KEY) is None
            and linked_twins.ORIGIN not in material]


def _vertex_objects(stacks):
    found = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.library is not None:
            continue
        for key, stack in stacks.items():
            if stack.post is None and obj.modifiers.get(stack.VTX_MODIFIER) is not None:
                found.append((obj, key, stack.VTX_MODIFIER))
                break
    return found


def _rename_vertex_modifiers(vertex, fresh):
    for obj, key, was in vertex:
        modifier = obj.modifiers.get(was)
        now = fresh[key].VTX_MODIFIER if key in fresh else was
        if modifier is not None and now != was:
            modifier.name = now


def _rebuilt_vertex_objects(vertex, fresh):
    return sum(1 for obj, key, _was in vertex
               if key in fresh and getattr(obj.modifiers.get(fresh[key].VTX_MODIFIER), "node_group", None) is not None)


def update(renames=()):
    """Replace every outdated package's generation and compile what it made again, from the records as they are.
    ``renames`` are (panel key, part, kind, old name, new name): inputs the new generation spells differently,
    moved to the new spelling in the records of the materials this file owns before they are compiled. Returns a
    :class:`Result`; raises with the reason when nothing was started."""
    derived_state.flush()
    targets = [state for state in scan() if state.outdated]
    result = Result()
    if not targets:
        return result
    refused = [text for state in targets for text in ([state.failure] if state.failure else []) + state.blockers]
    if refused:
        raise RuntimeError("; ".join(refused))
    stacks = {stack.PANEL_KEY: stack for state in targets for stack in state.package.STACKS}
    owned = _owned_materials(stacks)
    before = _records(owned, stacks)
    vertex = _vertex_objects(stacks)
    linked_twins.release_matching(lambda original: original.get(STACK) in stacks)
    for state in targets:
        if state.replaced:
            Game.reload_stack(state.package)
        state.package.purge_plugin_data()
        result.packages.append(state.label)
    fresh = {stack.PANEL_KEY: stack for state in targets for stack in state.package.STACKS}
    _rename_vertex_modifiers(vertex, fresh)
    touched = set()
    for key, part, kind, old, new in renames:
        for material in owned:
            if material.get(STACK) == key and material.get(PART) == part \
                    and shader_inputs.rename(material, kind, old, new):
                touched.add(material.name_full)
    result.renamed = len(touched)
    material_panel.forget()
    derived_state.refresh_after_replacement([obj for obj, _key, _was in vertex])
    if derived_state.LAST_ERROR:
        result.failures.append(derived_state.LAST_ERROR)
    compiled = _owned_materials(fresh)
    after = _records(compiled, fresh)
    result.recompiled = len(compiled)
    for name, record in after.items():
        if name in touched:
            continue
        if before.get(name) == record:
            result.identical += 1
        else:
            result.differing.append(name)
    for name in result.differing:
        keys = set(before.get(name, {})) | set(after[name])
        print("[shader-update] record differs on {0}: {1}".format(
            name, sorted(key for key in keys if before.get(name, {}).get(key) != after[name].get(key))), flush=True)
    result.vertex_objects = _rebuilt_vertex_objects(vertex, fresh)
    current(force=True)
    print("[shader-update] " + result.summary(), flush=True)
    return result
