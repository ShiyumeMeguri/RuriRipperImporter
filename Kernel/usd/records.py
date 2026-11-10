"""A record -- the nested objects, lists and scalars a source states about one asset -- as one USD layer.

USD has no schema for a controller or a timeline, and this package does not make one up: a record is cut into prims
that carry nothing but ``ruri:`` attributes of basic types, the way every other thing the source says is carried.
Five rules are the whole format, and ``Ruri.Scenes.Editor``'s record reader (``UsdRecordTreeReader``) is their inverse:

* a scalar member (bool, int, float, str) is the attribute ``ruri:<member>``; an int and a float are different
  attribute types and are read back as the different kinds they are;
* a list of scalars is the array attribute of the same name (an empty list is not written: absent means empty);
* an object member is a child ``Scope`` prim of the member's name;
* a list of objects is a child ``Scope`` of the member's name, marked by its ``ruri:list:count`` attribute (the number of
  elements), holding one ``Scope`` per element, named ``i0``, ``i1``...: the marker is what tells a list from an object
  whose members happen to be named like elements, and a member name never holds a colon, so it cannot be one;
* ``None`` is not written: absent means none.

A list that mixes scalars and objects, or scalars of different kinds, is refused rather than guessed at.
"""

from __future__ import annotations

from pxr import Sdf, Vt

from .. import statement as kernel_statement
from . import stages
from .names import file_stem, identifier

LIST_COUNT = "ruri:list:count"
_INT32_LIMIT = 2 ** 31
_SCALAR_TYPES = {bool: (Sdf.ValueTypeNames.Bool, Sdf.ValueTypeNames.BoolArray, Vt.BoolArray),
                 float: (Sdf.ValueTypeNames.Double, Sdf.ValueTypeNames.DoubleArray, Vt.DoubleArray),
                 str: (Sdf.ValueTypeNames.String, Sdf.ValueTypeNames.StringArray, Vt.StringArray)}


def _kind(value):
    if isinstance(value, bool):
        return bool
    if isinstance(value, int):
        return int
    if isinstance(value, float):
        return float
    if isinstance(value, str):
        return str
    raise ValueError("a record member is a bool, int, float, str, list or object, not {0}.".format(type(value).__name__))


def _scalar_type(kind, values):
    if kind is int:
        wide = any(abs(value) >= _INT32_LIMIT for value in values)
        return ((Sdf.ValueTypeNames.Int64, Sdf.ValueTypeNames.Int64Array, Vt.Int64Array) if wide
                else (Sdf.ValueTypeNames.Int, Sdf.ValueTypeNames.IntArray, Vt.IntArray))
    return _SCALAR_TYPES[kind]


def _write_list(stage, prim, key, values, counts, path):
    if not values:
        return
    if all(isinstance(value, dict) for value in values):
        holder = stage.DefinePrim(prim.GetPath().AppendChild(key), "Scope")
        holder.CreateAttribute(LIST_COUNT, Sdf.ValueTypeNames.Int, custom=True).Set(len(values))
        counts["prims"] += 1
        counts["attributes"] += 1
        for index, value in enumerate(values):
            element = stage.DefinePrim(holder.GetPath().AppendChild("i{0}".format(index)), "Scope")
            counts["prims"] += 1
            _write_object(stage, element, value, counts, "{0}/{1}[{2}]".format(path, key, index))
        return
    kinds = {_kind(value) for value in values if not isinstance(value, (dict, list)) and value is not None}
    if len(kinds) != 1 or any(isinstance(value, (dict, list)) or value is None for value in values):
        raise ValueError("{0}/{1} mixes kinds in one list: {2}.".format(path, key, sorted(kind.__name__ for kind in kinds)))
    kind = kinds.pop()
    _scalar, array_type, array_class = _scalar_type(kind, values)
    prim.CreateAttribute("ruri:" + key, array_type, custom=True).Set(array_class(list(values)))
    counts["attributes"] += 1


def _write_object(stage, prim, mapping, counts, path):
    for key, value in mapping.items():
        if value is None:
            continue
        if isinstance(value, dict):
            child = stage.DefinePrim(prim.GetPath().AppendChild(key), "Scope")
            counts["prims"] += 1
            _write_object(stage, child, value, counts, "{0}/{1}".format(path, key))
        elif isinstance(value, list):
            _write_list(stage, prim, key, value, counts, path)
        else:
            kind = _kind(value)
            scalar_type, _array, _class = _scalar_type(kind, [value])
            prim.CreateAttribute("ruri:" + key, scalar_type, custom=True).Set(value)
            counts["attributes"] += 1


def attach(stage, parent, name, record, counts):
    """Write ``record`` (a dict) as the child ``Scope`` ``name`` of the prim ``parent``, by the same five rules, for a record
    that is one part of a layer rather than a layer of its own. ``counts`` tallies ``prims`` and ``attributes``. Returns
    the prim."""
    prim = stage.DefinePrim(parent.GetPath().AppendChild(name), "Scope")
    counts["prims"] += 1
    _write_object(stage, prim, record, counts, str(prim.GetPath()))
    return prim


def author(layer_path, record, description):
    """Write ``record`` (a dict) to ``layer_path``; the default prim is named after ``sourceName``. Returns the counts
    of prims and attributes written."""
    stage = stages.create(layer_path, kernel_statement.USD, description)
    root = stage.DefinePrim("/" + identifier(record.get("sourceName") or "Record"), "Scope")
    stage.SetDefaultPrim(root)
    counts = {"prims": 1, "attributes": 0}
    _write_object(stage, root, record, counts, "")
    stage.GetRootLayer().Save()
    return counts


def layer_stem(name, key):
    """The file name of a record's layer: its name for whoever reads the folder, and its key -- what tells two records
    of one name apart -- for everything else."""
    return "{0}__{1}".format(file_stem(name), identifier(key))
