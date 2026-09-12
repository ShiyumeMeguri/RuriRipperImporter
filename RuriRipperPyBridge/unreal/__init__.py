"""Reading an Unreal install, host-side.

The twin of ``..unity``: nothing here touches a host's API, and nothing here converts an
Unreal asset into another engine's. The decoder states geometry, materials and pixels
columnar (see ``Ruri.FModelHook.Unreal.UnrealDatasets``); this turns those columns into
the same normalised forms the host's builders already take, so one set of builders serves
both engines.
"""
