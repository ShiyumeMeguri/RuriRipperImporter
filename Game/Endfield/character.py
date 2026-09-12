"""The Character tab: the cast, and what can be done to the one you loaded.

ONE tab, exactly as this game's panel has always had it. Splitting the cast browser
out into a tab of its own was a change to the layout nobody asked for -- the rule
for this whole port is that a host which cannot do something loses that SECTION, not
that the tab it lived in gets rearranged for every host including the one that could
do it all along.

So the composition is:

``Cast``   the game's own cast, its two rosters and the Story pane -- needs nothing
           but the game's tables, so every host draws it (``roster``).
``Story``  what the Story pane opens onto: the animations story playback uses. Needs
           an animation surface (``story``).
``Face``   the SkeletalMorph emotion/pose/lipsync library, bound to a rig and baked.
           Needs morph targets (``face``).

Each section asks for what it needs and is simply absent otherwise. Nothing here
imports a host.
"""

from __future__ import annotations

from .. import section
from . import SECTIONS, roster


def draw_tab(layout, context):
    """The tab body: the cast browser, then whatever this host can add to it.

    Both halves describe; neither renders. A tab is drawn inside the host panel's
    own description, so the layout handed here is the neutral one and a tab body
    that reached for a host toolkit would break the whole panel."""
    pane = roster.draw(layout.box(), context)

    if pane is roster.STORY_PANE:
        # The Story pane IS the story browser's own list; a host with no animation
        # surface has nothing to open under it.
        if section(SECTIONS, "story").available:
            from . import story
            story.draw_story_tab(layout, context)
        return

    if section(SECTIONS, "face").available:
        from . import face
        face.draw(layout, context)
