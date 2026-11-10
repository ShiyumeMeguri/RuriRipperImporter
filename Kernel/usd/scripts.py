"""The script records of one set of seeds as one layer: the components its nodes carry and the scriptable objects its archives hold.

The statement states every record as a JSON object (``Statement.scripts``); the layer is one record by the five rules of
``records``: ``components`` and ``assets`` are lists of the records the statement gave, in its order, so that a reader that
sorts the elements by their index reads them in the statement's order. Nothing here decides what a member means: a member is
what the game's own scripts call it.
"""

from __future__ import annotations

from . import records

#: The members of the record of the layer.
SOURCE_KEY = "sourceKey"
SOURCE_NAME = "sourceName"
COMPONENTS = "components"
ASSETS = "assets"

#: The kinds of row the statement states.
COMPONENT = "component"
ASSET = "asset"


def record_of(label, source_key, scripts):
    """The record of the layer named ``label`` from the rows ``scripts`` of a statement."""
    kinds = {script.kind for script in scripts}
    unknown = kinds - {COMPONENT, ASSET}
    if unknown:
        raise ValueError("a script row is a component or an asset, not {0}.".format(sorted(unknown)))
    return {
        SOURCE_KEY: source_key,
        SOURCE_NAME: label,
        COMPONENTS: [script.record for script in scripts if script.kind == COMPONENT],
        ASSETS: [script.record for script in scripts if script.kind == ASSET],
    }


def author(layer_path, label, source_key, scripts):
    """Write the ``scripts`` of one statement as the layer ``layer_path``. Returns the record written and the counts of prims and
    attributes."""
    record = record_of(label, source_key, scripts)
    counts = records.author(layer_path, record, "Scripts {0}".format(label))
    return record, counts
