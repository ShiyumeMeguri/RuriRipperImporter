"""The application layer: what the panel remembers, what it can be told to do,
and how it is laid out -- all of it host-neutral.

The split against the layer below is that ``RuriRipperPyBridge`` answers
questions about the DATA (what is in this install, what does this .mat say),
while this package holds the state of a person looking at it: which install is
in front of them, what they typed in the search box, which rows they ticked,
which options they set.

Both hosts drive the same declarations from here. Blender generates its
PropertyGroups from them so the panel keeps Blender's undo and .blend
persistence; Painter builds a plain value bag. Neither restates a field.
"""
