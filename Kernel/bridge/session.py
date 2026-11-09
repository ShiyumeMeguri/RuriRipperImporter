"""Asking the open session for a published dataset.

ONE session exists per process: the panel opens it on an install, loads that
install's map, and holds it (:mod:`Kernel.bridge.cabmap_state`). Everything that
wants a dataset -- a statement, a roster, a diagnostic -- asks through here, so a
statement always reads the map that is actually open rather than one of its own.

Nothing is parsed on this side: a table arrives columnar over pinned buffers and
a blob arrives as bytes.
"""

from __future__ import annotations


def _reader():
    from . import cabmap_state

    if cabmap_state.BRIDGE is None:
        raise RuntimeError(
            "No session is open yet -- an install's map has to be loaded before a "
            "dataset can be read, because a dataset reads that map.")
    return cabmap_state.BRIDGE


def ensure_session():
    """Open a map-free session only if none exists; never replace an install."""
    from . import cabmap_state
    if cabmap_state.BRIDGE is None:
        cabmap_state.ensure_bridge("", "")
    return cabmap_state.BRIDGE


def table(dataset_id, cancellation=None, **args):
    """One published dataset, as a :class:`Kernel.table.ColumnTable`.

    Arguments go BY NAME and are validated against what the dataset declares, so
    a wrong name fails loudly here instead of silently shifting every later
    argument along."""
    return _reader().game_data(str(dataset_id), cancellation=cancellation, **args)


def blob(dataset_id, payload=None, cancellation=None, **args):
    """One published dataset whose answer is bytes. ``payload`` is what the
    caller brings that the reader cannot derive -- a performance to restate."""
    return _reader().game_data_blob(str(dataset_id), payload=payload,
                                    cancellation=cancellation, **args)


def release_statement(**args):
    """The selection a statement asked about with these arguments has been placed and nothing
    will ask about it again: the reader lets go of the flattening its tables were cut from.
    With no session open, nothing is held to let go."""
    from . import cabmap_state

    if cabmap_state.BRIDGE is not None:
        cabmap_state.BRIDGE.release_statement(**args)


def view(table_or_handle, facet="", query="", rules=None, note="", shipped_only=True,
         sort_column="", sort_direction=0, window=0, ordered=False, label_column="",
         group_column=""):
    """One drawn list, composed entirely on the reader side."""
    return _reader().open_view(table_or_handle, facet=facet, query=query, rules=rules,
                               note=note, shipped_only=shipped_only,
                               sort_column=sort_column, sort_direction=sort_direction,
                               window=window, ordered=ordered, label_column=label_column,
                               group_column=group_column)


def search(table_or_handle, query, rules=None):
    """The rows of one table that match, as row ids."""
    import numpy as np

    return np.frombuffer(bytes(_reader().search_data_table(table_or_handle, query, rules)),
                         dtype="<i4")
