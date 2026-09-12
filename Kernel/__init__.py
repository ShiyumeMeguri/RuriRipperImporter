"""The host-neutral application core.

Nothing under this package may import a host API -- no ``bpy``, no
``substance_painter``, no Qt. What a host can do is asked for through
:mod:`Kernel.host`, and what a host is asked to do is stated as data the host
then materialises. The rule is mechanically checkable: ``Kernel/**`` grepped for
those module names must come back empty.

The layer BELOW this one is ``RuriRipperPyBridge`` -- the data layer: the CLR
bridge into Ruri.RipperHook, Unity/Unreal decoding, the cabmap session. It knows
nothing about hosts either, and nothing about any one game.

The layer ABOVE is ``Host/<name>`` -- one driver per application, the only place
that application's API appears.
"""
