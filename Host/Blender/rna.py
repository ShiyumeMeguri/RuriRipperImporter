"""Materialise a kernel state schema as real Blender RNA.

A generated ``PropertyGroup`` is not a lesser version of a hand-written one: it
carries the same ``bpy.props`` with the same names, so the panel keeps Blender's
undo, its per-.blend persistence and its redraw-on-change, and every existing
reader (``state.game_root``, ``state.window[i].cab``) works untouched. What
changes is only WHERE the field is declared -- once, in the kernel, where
Painter's dock reads the same declaration.

Two Blender specifics this handles so no schema has to know them:

* **A dynamic enum's items list must outlive the call.** Blender's callback
  hands its result to C, which keeps a pointer rather than a reference; a list
  that dies with the call leaves the enum pointing at freed strings. Every
  distinct catalog is therefore retained by its callback for that callback's
  entire lifetime, including catalogs superseded by a later refresh.
* **An enum with no default.** ``default=`` must name one of the items, which a
  dynamic list cannot promise, so a schema that states none gets none.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, StringProperty)

from ...Kernel.app import state as app_state

_SUBTYPES = {
    app_state.DIRECTORY: "DIR_PATH",
    app_state.FILE: "FILE_PATH",
    app_state.FACTOR: "FACTOR",
}

# Values behind transient RNA accessors, not values in RNA/ID properties. A
# getter/setter plus SKIP_SAVE is intentional: SKIP_SAVE alone still gives RNA
# a value that can leak into undo, copied scenes and document inspection.
_TRANSIENT = {}

def _enum_callback(handler):
    # Blender can still draw buttons pointing into a PREVIOUS callback result.
    # Keep every distinct catalog, not just the latest one. Equal catalogs reuse
    # the same list and strings, so ordinary redraws do not grow the cache.
    # The registered callback owns this cache: unregistering a different panel
    # cannot clear it, and releasing this callback releases its catalogs too.
    catalogs = {}

    def items(self, context):
        produced = handler(self, context) or ()
        signature = tuple(tuple(entry) for entry in produced) or (("", "", ""),)
        if signature not in catalogs:
            catalogs[signature] = list(signature)
        return catalogs[signature]

    return items


def _keywords(field, schema, handlers):
    keywords = {}
    if field.label:
        keywords["name"] = field.label
    if field.description:
        keywords["description"] = field.description
    if field.subtype:
        keywords["subtype"] = _SUBTYPES[field.subtype]
    if field.update:
        keywords["update"] = handlers.resolve(field.update, field, schema)
    if field.getter:
        keywords["get"] = handlers.resolve(field.getter, field, schema)
    if field.setter:
        keywords["set"] = handlers.resolve(field.setter, field, schema)
    if field.live:
        keywords["options"] = {"TEXTEDIT_UPDATE"}
    if field.transient:
        if field.kind not in (app_state.STRING, app_state.BOOL, app_state.INT, app_state.FLOAT):
            raise TypeError("transient fields require a scalar kind")
        if not field.getter and not field.setter:
            def slot(owner):
                return (schema.name, field.key, owner.as_pointer())
            keywords["get"] = lambda owner: _TRANSIENT.get(slot(owner), field.default)
            keywords["set"] = lambda owner, value: _TRANSIENT.__setitem__(slot(owner), value)
        keywords["options"] = set(keywords.get("options", ())) | {"SKIP_SAVE", "HIDDEN"}
    if field.sensitive:
        keywords["subtype"] = "PASSWORD"
    # A property with a getter stores nothing, so a default would be a value
    # nobody can ever read back -- Blender rejects the pair outright. A DYNAMIC
    # enum is rejected for the neighbouring reason: ``default=`` has to name one
    # of the items, and a list that depends on what the host can do cannot
    # promise to contain it. The schema's default still means something to a host
    # whose records are plain values; here the first item stands in, which is what
    # the schema names anyway.
    dynamic_enum = field.kind == app_state.ENUM and isinstance(field.items, str)
    if field.default is not None and not field.getter and not field.transient and not dynamic_enum:
        keywords["default"] = field.default
    return keywords


def property_for(field, schema, handlers, built):
    keywords = _keywords(field, schema, handlers)
    if field.kind == app_state.BOOL:
        return BoolProperty(**keywords)
    if field.kind == app_state.INT:
        for name, value in (("min", field.minimum), ("max", field.maximum),
                            ("soft_min", field.soft_minimum), ("soft_max", field.soft_maximum)):
            if value is not None:
                keywords[name] = value
        return IntProperty(**keywords)
    if field.kind == app_state.FLOAT:
        for name, value in (("min", field.minimum), ("max", field.maximum),
                            ("soft_min", field.soft_minimum), ("soft_max", field.soft_maximum)):
            if value is not None:
                keywords[name] = value
        return FloatProperty(**keywords)
    if field.kind == app_state.STRING:
        return StringProperty(**keywords)
    if field.kind == app_state.ENUM:
        if isinstance(field.items, str):
            keywords["items"] = _enum_callback(
                handlers.resolve(field.items, field, schema))
        else:
            keywords["items"] = [tuple(entry) for entry in field.items]
        return EnumProperty(**keywords)
    if field.kind == app_state.COLLECTION:
        return CollectionProperty(type=built[field.element.name])
    raise TypeError("no Blender property for field kind {0!r} ({1}.{2})".format(
        field.kind, schema.name, field.key))


class Registry:
    """Builds the PropertyGroup classes one schema tree needs, in the order
    Blender has to register them.

    ``prebuilt`` maps a SCHEMA NAME to a class someone else already made and
    registered -- the rule record belongs to the filter module and is contained
    by every list that can be filtered, so it is built once and handed round
    rather than duplicated per owner (Blender would reject the second
    registration under the same name anyway).

    By name, never by object identity: a development reload re-executes the
    schema module and every recorded id then matches nothing, which reads as
    "that schema was never built".
    """

    def __init__(self, handlers, prebuilt=None):
        self._handlers = handlers
        self._built = dict(prebuilt or {})
        self.classes = []

    def build(self, schema, names, extra=None):
        """Generate (and remember) the class for ``schema`` and everything it
        contains. ``names`` maps a schema name to the bpy class name to give it;
        a contained schema with no entry is an error rather than a guess, because
        a class name is what Blender registers and what a stale reference finds.

        Returns the class; ``self.classes`` is the registration order."""
        for contained in app_state.ordered(schema):
            if contained.name in self._built:
                continue
            class_name = names.get(contained.name)
            if not class_name:
                raise KeyError(
                    "no bpy class name given for schema {0!r}, which {1} contains".format(
                        contained.name, schema.name))
            self._built[contained.name] = self._make(
                contained, class_name, extra if contained is schema else None)
        return self._built[schema.name]

    def built(self, schema):
        """The class made for a schema this registry already built."""
        made = self._built.get(schema.name)
        if made is None:
            raise KeyError("nothing built for schema {0!r} yet".format(schema.name))
        return made

    def _make(self, schema, class_name, extra):
        annotations = {}
        for field in schema.fields:
            annotations[field.key] = property_for(field, schema, self._handlers, self._built)
        namespace = {"__annotations__": annotations, "__doc__": schema.doc,
                     "RURI_SCHEMA": schema}
        namespace.update(extra or {})
        made = type(class_name, (bpy.types.PropertyGroup,), namespace)
        self.classes.append(made)
        return made

    def register(self):
        for made in self.classes:
            bpy.utils.register_class(made)

    def unregister(self):
        for made in reversed(self.classes):
            bpy.utils.unregister_class(made)


# ---------------------------------------------------------------------------
# Panel state, filed by name
# ---------------------------------------------------------------------------
#: What each named panel state was built from, so unregistering can undo exactly
#: it. A schema's own name gives the generated classes theirs, so two panels
#: cannot collide without saying so in the declaration.
_STATES = {}

#: The records every panel state shares, by schema name, and the registry that
#: built them. The filter rule is contained by every filterable list, and Blender
#: registers a class under one name once.
_SHARED = {}
_SHARED_REGISTRY = []


def register_shared():
    from ...Kernel.app import filtering, schemas

    registry = Registry(filtering.HANDLERS)
    _SHARED[schemas.FILTER_RULE.name] = registry.build(
        schemas.FILTER_RULE, {schemas.FILTER_RULE.name: "RURI_PG_filter_rule"})
    registry.register()
    _SHARED_REGISTRY.append(registry)


def unregister_shared():
    for registry in reversed(_SHARED_REGISTRY):
        registry.unregister()
    _SHARED_REGISTRY.clear()
    _SHARED.clear()


def register_state(name, schema, handlers, extra=None):
    """Put a panel's state on the Scene under ``name``.

    On the Scene rather than in a module global on purpose: that is what makes it
    undoable and what saves it with the .blend, which is most of the reason to
    materialise a schema as RNA at all rather than as a plain bag."""
    registry = Registry(handlers, prebuilt=_SHARED)
    names = {contained.name: "RURI_PG_" + contained.name
             for contained in app_state.ordered(schema)}
    made = registry.build(schema, names, extra=extra)
    registry.register()
    setattr(bpy.types.Scene, name, bpy.props.PointerProperty(type=made))
    _STATES[name] = registry
    return made


def unregister_state(name):
    registry = _STATES.pop(name, None)
    if registry is None:
        return
    if hasattr(bpy.types.Scene, name):
        delattr(bpy.types.Scene, name)
    schema_names = {made.RURI_SCHEMA.name for made in registry.classes}
    for slot in tuple(_TRANSIENT):
        if slot[0] in schema_names:
            del _TRANSIENT[slot]
    registry.unregister()
