"""Host-agnostic session state: the cabmap browser model.

State lives in module-level globals, matching how the hosts already use it: one
host application is one process is one session. A model that is about ONE game
(a title's scene placements, its facial-morph library) is not host-agnostic --
it ships with the host that has that feature, not here.
"""
