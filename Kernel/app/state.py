"""Declaring panel state once, for hosts whose state systems have nothing in
common.

Blender's panel state is RNA: properties on a ``PropertyGroup``, which is what
gives the panel undo, per-.blend persistence, and redraw-on-change for free.
Painter has none of that -- its state is whatever the widgets hold, plus a JSON
file. Neither can be made to use the other's, and a panel declared twice is a
panel that drifts.

So the DECLARATION lives here and each host materialises it: the Blender driver
generates real PropertyGroups (see ``Host/Blender/rna.py``), the Painter driver
a plain value bag. A field exists in exactly one place, and adding one is one
line that both hosts pick up.

WHAT THIS DELIBERATELY IS NOT is a general property framework. The kinds below
are exactly the ones this panel uses and no more: bool, int, float, string,
enum, and a collection of sub-records. A kind is added when something declares
it, never in advance.

Behaviour -- a field's update callback, a virtual field's getter/setter -- is
named rather than referenced. A declaration that held the function itself would
have to import the layer that defines it, and the whole point is that this layer
is under both hosts. Names are resolved when a host materialises the schema, and
a name with no handler fails there rather than at the first click.
"""

from __future__ import annotations

BOOL = "bool"
INT = "int"
FLOAT = "float"
STRING = "string"
ENUM = "enum"
COLLECTION = "collection"

#: A string field that names a directory / a file, so a host can offer its own
#: browse button rather than a bare text box.
DIRECTORY = "directory"
FILE = "file"
#: A float in 0..1 that a host may draw as a slider/factor rather than a number.
FACTOR = "factor"


class Field:
    """One remembered value."""

    __slots__ = ("key", "kind", "default", "label", "description", "subtype",
                 "minimum", "maximum", "soft_minimum", "soft_maximum",
                 "items", "element", "update", "getter", "setter", "live",
                 "remembered")

    def __init__(self, key, kind, default=None, label="", description="",
                 subtype="", minimum=None, maximum=None,
                 soft_minimum=None, soft_maximum=None,
                 items=None, element=None, update="", getter="", setter="",
                 live=False, remembered=False):
        self.key = key
        self.kind = kind
        self.default = default
        self.label = label
        self.description = description
        self.subtype = subtype
        self.minimum = minimum
        self.maximum = maximum
        self.soft_minimum = soft_minimum
        self.soft_maximum = soft_maximum
        #: ENUM only. Either a tuple of (id, label, description), or the NAME of
        #: a handler returning one -- a list whose contents depend on what was
        #: actually loaded cannot be a constant.
        self.items = items
        #: COLLECTION only: the :class:`Schema` of one element.
        self.element = element
        #: Handler name called after this field changes.
        self.update = update
        #: Handler names making this a VIRTUAL field -- it stores nothing and
        #: reads/writes somewhere else (the per-install entry the browser is
        #: currently on, say). Both or neither.
        self.getter = getter
        self.setter = setter
        #: Fire ``update`` on every keystroke rather than when editing finishes.
        self.live = live
        #: Whether this value is worth carrying into the NEXT session -- which
        #: install the browser is on, not the rows it happens to be showing.
        #:
        #: A statement about the VALUE, so it is made once here rather than by
        #: each host: Blender's panel state is RNA on the Scene and comes back
        #: with the .blend whatever this says, while Painter's is a plain object
        #: that dies with the process, so its driver writes exactly the fields
        #: marked here into its own settings file and puts them back at startup.
        self.remembered = remembered

    @property
    def virtual(self):
        return bool(self.getter or self.setter)

    def __repr__(self):
        return "<Field {0}:{1}>".format(self.key, self.kind)


class Schema:
    """One record's worth of fields, in the order a host should present them."""

    __slots__ = ("name", "doc", "fields", "_by_key")

    def __init__(self, name, doc, fields, include=()):
        self.name = name
        self.doc = doc
        merged = []
        for shared in include:
            merged.extend(shared.fields)
        merged.extend(fields)
        self.fields = tuple(merged)
        self._by_key = {field.key: field for field in self.fields}
        if len(self._by_key) != len(self.fields):
            seen = set()
            duplicated = sorted({field.key for field in self.fields
                                 if field.key in seen or seen.add(field.key)})
            raise ValueError("{0} declares {1} twice".format(name, ", ".join(duplicated)))

    def field(self, key):
        found = self._by_key.get(key)
        if found is None:
            raise KeyError("{0} has no field {1!r}".format(self.name, key))
        return found

    def keys(self):
        return tuple(field.key for field in self.fields)

    def collections(self):
        """The sub-schemas this one contains, which a host must materialise
        before it can materialise this."""
        return tuple(field.element for field in self.fields
                     if field.kind == COLLECTION and field.element is not None)

    def __repr__(self):
        return "<Schema {0} ({1} fields)>".format(self.name, len(self.fields))


class Handlers:
    """The behaviour a host binds to a schema's named callbacks.

    Resolution is strict on purpose: a schema naming a handler nobody supplied
    is a field whose update would silently never fire, which is the exact class
    of bug -- "you must remember to also wire X" -- that a declaration is
    supposed to make impossible."""

    __slots__ = ("_table", "_owner")

    def __init__(self, owner, base=None, **table):
        self._owner = owner
        self._table = dict(base._table) if base is not None else {}
        self._table.update(table)

    def resolve(self, name, field, schema):
        found = self._table.get(name)
        if found is None:
            raise KeyError(
                "{0} supplies no handler {1!r}, named by {2}.{3}".format(
                    self._owner, name, schema.name, field.key))
        return found

    def __contains__(self, name):
        return name in self._table


def ordered(schema, seen=None):
    """A schema and everything it contains, deepest first -- the order a host
    has to materialise them in, since a collection field cannot be built before
    the record it holds."""
    seen = set() if seen is None else seen
    out = []
    for element in schema.collections():
        if element.name in seen:
            continue
        out.extend(ordered(element, seen))
    if schema.name not in seen:
        seen.add(schema.name)
        out.append(schema)
    return out
