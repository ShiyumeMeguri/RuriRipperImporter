"""What this game publishes, and the small amount of shape that is genuinely the UI's.

Every list this add-on draws for Koikatu is a dataset the game's own hook reads
(``Ruri.RipperHook.Koikatu``): the customization catalog, the cast, a build plan,
the places, the animation catalog, the named expressions, a head's pattern table.
None of it is parsed on this side -- the ids below name the tables, and
``bridge.game_data(id, *args)`` returns one already columnar, already searchable
under its own handle and already cached by (id, args).

That split is the whole architecture: the hook is the game's reading side, and
this add-on is a panel. So what remains here is the panel's own vocabulary --
which column a list shows, which slot families a build toggles -- and nothing
about how a byte becomes a row.
"""

from __future__ import annotations

from ...RuriRipperPyBridge.session import cabmap_state

# The datasets the hook publishes. Names, not paths: the hook owns where they come from.
CATALOG = "koikatu.chara.catalog"
CAST = "koikatu.chara.cast"
PLAN = "koikatu.chara.plan"
PLACES = "koikatu.scene.places"
ANIMATIONS = "koikatu.anime.catalog"
EXPRESSIONS = "koikatu.face.expressions"
FACE_PATTERNS = "koikatu.face.patterns"
BUNDLE_CABS = "koikatu.bundle.cabs"

# The slot families a build can be narrowed to, as the plan's own ``slot`` column
# spells them. The skeleton, body and tongue have no toggle: they are what the
# other parts bind onto.
HEAD = "head"
HAIR = "hair"
CLOTHES = "clothes"
SUB_CLOTHES = "sub_clothes"
ACCESSORY = "accessory"

# Read tables, by (id, args) -- the same key the hook caches under. Held so a
# redraw never crosses the bridge; a Refresh drops it.
_TABLES = {}


def reset():
    _TABLES.clear()


def table(dataset_id, *args, refresh=False):
    """One published dataset. Returns None when there is no bridge yet, which is
    what a draw callback sees before a cabmap is loaded."""
    if cabmap_state.BRIDGE is None:
        return None
    key = (dataset_id,) + tuple(str(arg) for arg in args)
    if refresh:
        _TABLES.pop(key, None)
    if key not in _TABLES:
        _TABLES[key] = cabmap_state.BRIDGE.game_data(dataset_id, *args)
    return _TABLES[key]


def rows(dataset_id, *args, refresh=False):
    """The dataset's rows as dicts -- for the handful of places that genuinely
    need every row (a build plan is 15 rows). A LIST does not use this: it draws
    through search_data_table over the table's own handle."""
    found = table(dataset_id, *args, refresh=refresh)
    if found is None:
        return []
    return [{name: found.cell(index, name) for name in found.names}
            for index in range(len(found))]


def search(dataset_id, args, query, filter_rules):
    """Row ids of one dataset matching a query and every enabled rule, on the C#
    engine, over the buffers the read already produced. ``table.handle`` is the
    search handle, so reading and searching are not two registrations that can
    disagree."""
    found = table(dataset_id, *args)
    if found is None:
        return [], None
    if cabmap_state.BRIDGE is None or not len(found):
        return list(range(len(found or ()))), found
    return list(cabmap_state.BRIDGE.search_data_table(found.handle, query, filter_rules)), found


def text(table_, row, column):
    """One cell as text. Numeric columns cross as numbers -- which is the point --
    so a whole number reads back without a trailing ``.0`` nobody wants in a label."""
    value = table_.cell(row, column)
    if isinstance(value, str):
        return value
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


def number(table_, row, column):
    value = table_.cell(row, column)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cabs_for(bundles):
    """The seed CABs a set of the game's own bundle paths resolves to -- asked of
    the hook, which owns the rule, rather than re-derived here."""
    found = table(BUNDLE_CABS, *bundles)
    if found is None:
        return []
    seen = set()
    ordered = []
    for index in range(len(found)):
        cab = found.cell(index, "cab")
        if cab not in seen:
            seen.add(cab)
            ordered.append(cab)
    return ordered
