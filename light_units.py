"""Unity light quantities -> Blender light quantities. One statement of it.

Blender's light inputs are RADIOMETRIC and each type takes a different quantity;
Unity's ``m_Intensity`` is one number for every type. Copying the number across
is therefore wrong for everything except a directional light, and wrong by a
factor of 4*pi (a point/spot) or pi*area (an area light) -- large enough that a
copied point light reads as "the importer's lights are broken", which is exactly
what it is.

The conversions, each derived rather than tabulated:

``SUN``     Blender's Strength is IRRADIANCE on a surface facing the light
            (W/m^2). Unity's directional intensity enters its shading as the
            same quantity -- both sides then apply the Lambert ``1/pi`` -- so
            this one crosses 1:1.
``POINT``   Blender's Power is total RADIANT FLUX (W) radiated into the full
            sphere; the shader divides it back down by ``4*pi`` to get radiant
            intensity, then by ``d^2``. Unity's number IS that radiant
            intensity (``color * intensity / d^2``). So ``P = I * 4*pi``.
``SPOT``    Blender defines a spot's Power as the power the SAME bulb would
            radiate over the whole sphere -- narrowing the cone does not
            brighten it -- so the point conversion applies unchanged. (Which
            is also why a spot must NOT be scaled by its cone solid angle.)
``AREA``    Blender's Power is the flux emitted by the whole rectangle, and its
            emitted radiance is ``P / (area * pi)``. Unity's number behaves as
            that radiance, so ``P = L * area * pi``.

``pre_exposure`` is the separate, frame-level thing a modern pipeline does and
Blender does not: multiply all scene radiance by an exposure factor before tone
mapping. When a game ships its own tone mapper AND that tone mapper has been
ported into the compositor, the compositor expects the PRE-EXPOSED buffer as its
input -- so the exposure has to be in the light values themselves, not in
``view_settings.exposure`` (which Blender applies AFTER the compositor, i.e. to
the tone mapper's OUTPUT, where it cannot undo an already-clipped highlight).
A caller with no exposure to state passes 1.0 and nothing changes.

Nothing here knows a game: which field carries an intensity and which carries a
pre-exposure is the caller's answer.
"""

from __future__ import annotations

import math

# Unity's Light.type enum, as the light-building sites read it.
UNITY_SPOT = 0
UNITY_DIRECTIONAL = 1
UNITY_POINT = 2
UNITY_AREA = 3

BLENDER_TYPE = {UNITY_SPOT: "SPOT", UNITY_DIRECTIONAL: "SUN", UNITY_POINT: "POINT"}
BLENDER_TYPE_DEFAULT = "AREA"

_FOUR_PI = 4.0 * math.pi


def blender_type(unity_type):
    """The Blender light type a Unity Light.type stands for."""
    return BLENDER_TYPE.get(unity_type, BLENDER_TYPE_DEFAULT)


def directional_energy(intensity, pre_exposure=1.0):
    """Blender Sun Strength (W/m^2) for a Unity directional intensity."""
    return float(intensity) * float(pre_exposure)


def point_energy(intensity, pre_exposure=1.0):
    """Blender Point/Spot Power (W) for a Unity punctual intensity."""
    return float(intensity) * _FOUR_PI * float(pre_exposure)


def area_energy(intensity, size_x, size_y, pre_exposure=1.0):
    """Blender Area Power (W) for a Unity area intensity over an x*y rectangle.
    A degenerate rectangle emits nothing, which is what a zero area means."""
    area = float(size_x) * float(size_y)
    return float(intensity) * area * math.pi * float(pre_exposure)


def energy_for(unity_type, intensity, area_size=(1.0, 1.0), pre_exposure=1.0):
    """The Blender energy for ONE Unity light, dispatched on its own type --
    the single entry point every light-building site calls, so no site carries
    its own idea of what Unity's number means."""
    if unity_type == UNITY_DIRECTIONAL:
        return directional_energy(intensity, pre_exposure)
    if unity_type in (UNITY_POINT, UNITY_SPOT):
        return point_energy(intensity, pre_exposure)
    return area_energy(intensity, area_size[0], area_size[1], pre_exposure)
