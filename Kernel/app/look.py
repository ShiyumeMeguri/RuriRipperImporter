"""What a frame finally looks like -- as many sections as this host has answers for.

The knobs that decide the final image are not one feature. They are several, and
which of them EXIST is a property of the application: a host whose materials are
node graphs needs a panel for a generated graph's parameters (there is no native
one), while a host that shows those parameters itself needs no such panel and
would be getting a worse copy of a window it already has. A host whose scene is a
compositing graph can be handed a whole post chain; a host whose display is a
fixed set of choices can be handed exactly those choices, and the ported shader
states requirements for them.

So this is a REGISTRY, not a panel. Each driver registers the sections it can
answer for, saying which capability each needs; a tab body draws whatever is
registered for the host it is running in. That is why the tab crosses at all: it
is the same question in both applications, and only the answers differ.

A section is ``draw(layout, context)`` written against the neutral vocabulary,
exactly like every other panel body.
"""

from __future__ import annotations

from .. import host as host_port

#: The registered sections hold host state (a driver registers them at startup),
#: so reloading this module during development would empty the tab.
HOLDS_PROCESS_STATE = True


class Section:
    """One part of the answer, and the capability it needs to mean anything."""

    __slots__ = ("key", "label", "draw", "requires")

    def __init__(self, key, label, draw, requires=None):
        self.key = key
        self.label = label
        self.draw = draw
        self.requires = requires

    @property
    def available(self):
        return self.requires is None or self.requires in host_port.current().capabilities

    def __repr__(self):
        return "<Section {0}>".format(self.key)


SECTIONS = []


def register_section(key, label, draw, requires=None):
    """Add one section. Called by a DRIVER, because what the final image is made
    of is the application's answer, not the panel's."""
    drop_section(key)
    made = Section(key, label, draw, requires)
    SECTIONS.append(made)
    return made


def drop_section(key):
    SECTIONS[:] = [section for section in SECTIONS if section.key != key]


def clear():
    SECTIONS[:] = []


def sections():
    """Every section THIS host can answer for, in registration order."""
    return [section for section in SECTIONS if section.available]


def draw(layout, context):
    """The tab body: every section this host answers for, one after the other.

    A host that registered none says so once rather than showing an empty tab --
    which is the honest reading of "this application has no knob for any of it"."""
    offered = sections()
    if not offered:
        layout.label(text="This application exposes nothing about how the frame is "
                          "finally shown.", icon="INFO")
        return
    for section in offered:
        section.draw(layout, context)
