"""What a title of this family publishes, and the small amount of shape that is
genuinely the UI's.

Every list this add-on draws is a dataset the title's own hook reads
(``Ruri.RipperHook.Illusion``): the customization catalog, the cast, a build plan,
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

# What the hook publishes, named WITHOUT the game -- every title of this family
# publishes the same set, each under its own prefix (Ruri.RipperHook.Illusion's
# IllusionProfile.IdPrefix), so the id is completed here from whichever game the
# browser is on. Nothing in this package names a title.
CATALOG = "chara.catalog"
CAST = "chara.cast"
PLAN = "chara.plan"
PLACES = "scene.places"
ANIMATIONS = "anime.catalog"
EXPRESSIONS = "face.expressions"
FACE_PATTERNS = "face.patterns"
BUNDLE_CABS = "bundle.cabs"


def dataset_id(suffix):
    """One suffix under the current game's prefix. The prefix is the game's own name
    lowercased -- the same rule the hook builds its ids with -- so a title added
    upstream needs no entry here."""
    game = cabmap_state.active_game() or ""
    return "{0}.{1}".format(game.lower(), suffix) if game else suffix


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

# Why the last read produced nothing, in the reader's own words. A dataset this
# title does not publish is a REASON ("its hook is not decoding", "this title ships
# no such list"), and a panel that swallows it can only say "load a cabmap" -- which
# sends the user looking at the one thing that was never wrong.
_FAILURES = {}


def reset():
    _TABLES.clear()
    _FAILURES.clear()


def why_empty(suffix):
    """What stopped the last read of this dataset, or "" when nothing did."""
    return _FAILURES.get(dataset_id(suffix), "")


def table(suffix, refresh=False, **args):
    """One published dataset, by NAME-ed arguments. Returns None when there is no
    bridge yet (what a draw callback sees before a cabmap is loaded) or when the
    game on this tab publishes no such dataset -- an absence the hook states by not
    publishing it, which a panel reads as "this title has none"."""
    if cabmap_state.BRIDGE is None:
        return None
    resolved = dataset_id(suffix)
    key = (resolved,) + tuple(sorted((name, str(value)) for name, value in args.items()))
    if refresh:
        _TABLES.pop(key, None)
    if key not in _TABLES:
        try:
            _TABLES[key] = cabmap_state.BRIDGE.game_data(resolved, **args)
            _FAILURES.pop(resolved, None)
        except Exception as exc:
            _TABLES[key] = None
            _FAILURES[resolved] = _explain(resolved, exc)
            print("[RuriRipper] {0}: {1}".format(resolved, exc))
    return _TABLES[key]


def _explain(resolved, exc):
    """One line a user can act on. "Not published" is the common case and has exactly
    two causes -- this title ships no such list, or its hook is not the one decoding
    right now -- and which it is, is answerable: the id's own prefix says which game
    the dataset belongs to, and the session says which game is decoding."""
    if "no dataset" not in str(exc):
        return "{0}: {1}".format(type(exc).__name__, exc)
    wanted = resolved.split(".", 1)[0]
    decoding = cabmap_state.active_game() or ""
    if decoding.lower() != wanted:
        return ("This tab is {0} but {1} is decoding -- reload this tab's cabmap."
                .format(wanted, decoding or "no game hook"))
    return "{0} publishes no '{1}'.".format(decoding, resolved.split(".", 1)[1])


def rows(suffix, refresh=False, **args):
    """The dataset's rows as dicts -- for the handful of places that genuinely
    need every row (a build plan is 15 rows). A LIST does not use this: it draws
    through search_data_table over the table's own handle."""
    found = table(suffix, refresh=refresh, **args)
    if found is None:
        return []
    return [{name: found.cell(index, name) for name in found.names}
            for index in range(len(found))]


def search(suffix, args, query, filter_rules):
    """Row ids of one dataset matching a query and every enabled rule, on the C#
    engine, over the buffers the read already produced. ``table.handle`` is the
    search handle, so reading and searching are not two registrations that can
    disagree."""
    found = table(suffix, **args)
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
    found = table(BUNDLE_CABS, bundle=list(bundles))
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
