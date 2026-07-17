"""Game-specific conversion handling.

Everything in this package compensates for ONE game's private engine-fork
conventions -- rig layouts, runtime-IK semantics, custom asset quirks -- as
opposed to the generic Unity machinery in the addon root. One module per
game (``endfield_ik`` for Arknights: Endfield's IK_* bone rig). Import via
``from .Game import <module>``; each module must stay import-light (stdlib +
mathutils only) so pulling one game's handling never drags in another's.
"""
