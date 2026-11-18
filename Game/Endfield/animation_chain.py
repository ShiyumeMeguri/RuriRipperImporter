"""Bind the cast's animation-chain panel to the reader's datasets."""

from ...Kernel.app import animation_chain
from . import datasets


def _owner(context):
    from . import roster
    entry = roster.BOUND.picked(roster.state_of(context))
    return entry.payload if entry is not None else ""


PANEL = animation_chain.Panel(
    "Endfield", "ruri_animation_chain", _owner,
    datasets.animation_chain, datasets.animation_sequence,
    notes=("No-buff baseline: continuously request the next checked attack.",
           "Offline preview uses native interruption timing and blends."))


def register():
    PANEL.register(__name__)


def unregister():
    PANEL.unregister(__name__)
