"""Reading a Sims 4 setup, host-side.

The twin of ``..unreal``: nothing here touches a host's API and nothing here converts one
of that game's resources into another engine's asset. Its decoder states geometry,
materials and pixels columnar (see ``Ruri.RipperHook.Sims4.Sims4Datasets``); the reading
of those columns is the shared one in ``..placements``, and this package is only the
binding of that game's own dataset ids to it.
"""
