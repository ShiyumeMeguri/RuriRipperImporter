"""A schema materialised as plain values, for a host with no property system.

Blender gets real RNA (``Host/Blender/rna.py``) and everything that comes with
it. Painter has nothing equivalent, so its side of the same declaration is this:
attributes that read and write like the RNA ones do, collections that add and
clear like RNA collections do, virtual fields that call the same named handlers,
and update callbacks that fire on assignment.

The point is that a panel body cannot tell which it is holding. ``state.search``,
``state.window[3].cab``, ``state.games.add()`` mean the same thing on both sides,
so the description that uses them is written once.
"""

from __future__ import annotations

from . import state as app_state


class _Notifier:
    """The ONE callback a whole state tree announces through.

    A tree, not a field: an install tab's folder is written on the entry, its
    list is written on the collection, and which tab is in front is written on
    the root -- all three are the same panel changing. Shared by every bag and
    collection under the root, so silencing it while a snapshot is read back in
    is one switch rather than a walk of everything that might announce."""

    __slots__ = ("call", "muted")

    def __init__(self, call):
        self.call = call
        self.muted = False

    def __call__(self):
        if not self.muted:
            self.call()


class Collection:
    """A schema-typed list, with the handful of RNA collection operations the
    panels actually use."""

    __slots__ = ("_schema", "_handlers", "_items", "_changed")

    def __init__(self, schema, handlers, changed=None):
        self._schema = schema
        self._handlers = handlers
        self._items = []
        #: Set only when the field holding this list is a remembered one, which
        #: is what makes every entry in it -- and every field of every entry --
        #: worth writing out. A list of the rows currently on screen has none.
        self._changed = changed

    def add(self):
        made = Bag(self._schema, self._handlers,
                   changed=self._changed, remembered=self._changed is not None)
        self._items.append(made)
        self._announce()
        return made

    def clear(self):
        self._items = []
        self._announce()

    def remove(self, index):
        del self._items[index]
        self._announce()

    def _announce(self):
        if self._changed is not None:
            self._changed()

    def values(self):
        return list(self._items)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, index):
        return self._items[index]


class Bag:
    """One record. Fields are real attributes; anything not declared is rejected
    rather than quietly stored, because a typo that silently becomes a new
    attribute is a value nothing will ever read back."""

    __slots__ = ("_schema", "_handlers", "_values", "_context", "_changed",
                 "_remembered")

    def __init__(self, schema, handlers, context=None, changed=None,
                 remembered=False):
        if changed is not None and not isinstance(changed, _Notifier):
            changed = _Notifier(changed)
        object.__setattr__(self, "_schema", schema)
        object.__setattr__(self, "_handlers", handlers)
        object.__setattr__(self, "_context", context)
        #: Called after a remembered value under here changes. A host whose panel
        #: state dies with the process uses it to write that state out; one whose
        #: state is a document property needs none and passes nothing.
        object.__setattr__(self, "_changed", changed)
        #: This record IS a remembered one -- an entry in a remembered collection,
        #: where every field of it counts rather than the ones marked.
        object.__setattr__(self, "_remembered", remembered)
        values = {}
        object.__setattr__(self, "_values", values)
        for field in schema.fields:
            if field.kind == app_state.COLLECTION:
                values[field.key] = Collection(
                    field.element, handlers,
                    changed if (remembered or field.remembered) else None)
            elif not field.virtual:
                values[field.key] = _initial(field)

    @property
    def schema(self):
        return self._schema

    def __getattr__(self, name):
        schema = object.__getattribute__(self, "_schema")
        try:
            field = schema.field(name)
        except KeyError:
            raise AttributeError("{0} has no {1!r}".format(schema.name, name))
        if field.getter:
            handlers = object.__getattribute__(self, "_handlers")
            return handlers.resolve(field.getter, field, schema)(self)
        value = object.__getattribute__(self, "_values")[name]
        if field.kind == app_state.ENUM and isinstance(field.items, str):
            return self._chosen(name, value)
        return value

    def _chosen(self, key, value):
        """A DYNAMIC enum reads as one of the choices it currently has.

        A list that depends on what was loaded cannot carry a default the schema
        could name, so the stored value starts empty and is stale the moment the
        list changes under it. Blender's own RNA answers the first item in both
        cases, and a bag that answered "" instead is a panel whose drop-down shows
        a world while every command it feeds is asked about no world at all."""
        items = self.enum_items(key)
        if any(value == identifier for identifier, *_rest in items):
            return value
        return items[0][0] if items else ""

    def __setattr__(self, name, value):
        schema = self._schema
        try:
            field = schema.field(name)
        except KeyError:
            raise AttributeError(
                "{0} has no {1!r} -- the schema is where a field is added".format(
                    schema.name, name))
        if field.kind == app_state.COLLECTION:
            raise AttributeError(
                "{0}.{1} is a collection; use .add()/.clear()".format(schema.name, name))
        if field.setter:
            self._handlers.resolve(field.setter, field, schema)(self, value)
        elif field.getter:
            raise AttributeError(
                "{0}.{1} is read-only -- it is an observation, not a stored value".format(
                    schema.name, name))
        else:
            self._values[name] = value
        if field.update:
            self._handlers.resolve(field.update, field, schema)(self, self._context)
        if self._changed is not None and (self._remembered or field.remembered):
            self._changed()

    def enum_items(self, key):
        """A field's choices, resolving a named handler when the list depends on
        what was actually loaded."""
        field = self._schema.field(key)
        if isinstance(field.items, str):
            return tuple(self._handlers.resolve(field.items, field, self._schema)(
                self, self._context) or ())
        return tuple(field.items or ())

    def __repr__(self):
        return "<Bag {0}>".format(self._schema.name)


def remembered_values(bag):
    """What this panel is worth reopening with: the fields the schema marks
    remembered, as plain JSON-able values.

    Only the marked ones, and that is the whole point -- the rows the browser
    happens to be showing are a view of a cabmap that will not even be loaded
    next time, while WHICH INSTALL it was showing them from is the answer nobody
    should have to give twice."""
    return {field.key: _stored(bag, field)
            for field in bag.schema.fields if field.remembered}


def restore(bag, values):
    """Put a remembered snapshot back, read against the CURRENT schema: a key it
    no longer declares is dropped and a field the snapshot lacks keeps its
    default. The snapshot is a record of what the panel held, not a format with
    versions to reconcile -- so the schema decides, always, and a field that has
    since changed shape simply comes back at its default.

    Silent while it runs: a half-restored panel is not a state worth writing back
    over the one being read."""
    if not values:
        return
    notifier = bag._changed
    if notifier is None:
        _fill(bag, values, bag.schema.fields)
        return
    notifier.muted = True
    try:
        _fill(bag, values, bag.schema.fields)
    finally:
        notifier.muted = False
    # One announcement for the whole restore: what came back is what the panel
    # holds now, which is how a snapshot carrying a key the schema has since
    # dropped stops carrying it.
    notifier()


def _stored(bag, field):
    if field.kind == app_state.COLLECTION:
        return [{inner.key: _stored(entry, inner)
                 for inner in entry.schema.fields if not inner.virtual}
                for entry in getattr(bag, field.key)]
    return getattr(bag, field.key)


def _fill(bag, values, fields):
    for field in fields:
        if field.key not in values or field.virtual:
            continue
        value = values[field.key]
        if field.kind == app_state.COLLECTION:
            collection = getattr(bag, field.key)
            collection.clear()
            for record in value if isinstance(value, list) else ():
                _fill(collection.add(), record, field.element.fields)
            continue
        setattr(bag, field.key, value)


def _initial(field):
    if field.default is not None:
        return field.default
    if field.kind == app_state.BOOL:
        return False
    if field.kind == app_state.INT:
        return 0
    if field.kind == app_state.FLOAT:
        return 0.0
    if field.kind == app_state.ENUM:
        items = field.items
        return items[0][0] if items and not isinstance(items, str) else ""
    return ""
