"""The Character tab: the cast, and what can be done to the one you loaded.

ONE tab, exactly as this game's panel has always had it. Splitting the cast browser
out into a tab of its own was a change to the layout nobody asked for -- the rule
for this whole port is that a host which cannot do something loses that SECTION, not
that the tab it lived in gets rearranged for every host including the one that could
do it all along.

So the composition is:

``Cast``   the game's own cast -- needs nothing but the game's tables, so every
           host draws it.
``Anim``   the animations the selected one plays: the engine's own answer (what its
           animator names, plus the library this game files elsewhere), and this
           game's story filing beside it. Needs an animation surface (``story``).
``Face``   the expressions the selected one was built with: the engine's own answer
           (the named blend shapes its meshes carry), and this game's SkeletalMorph
           emotion/pose/lipsync library beside it. Needs morph targets (``face``).

All three are the ONE cast panel's own panes (``Kernel.app.cast_panel``), declared
by the roster and filled by this game's two extra sources. Each asks for what it
needs and is simply absent otherwise. Nothing here imports a host.
"""

from __future__ import annotations

from . import roster


def draw_tab(layout, context):
    """The tab body.

    It describes; it does not render. A tab is drawn inside the host panel's own
    description, so the layout handed here is the neutral one and a tab body that
    reached for a host toolkit would break the whole panel."""
    roster.draw(layout.box(), context)
