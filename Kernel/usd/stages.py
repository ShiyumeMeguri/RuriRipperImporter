"""The stage every layer of a tree starts from, so a reader can assert what it is reading.

Each layer states, in its own metadata, the axis it is up on and the size of a unit, and in its
custom layer data the basis its numbers are in and the version of this schema. A reader that
does not find them as it expects refuses the layer rather than reading it wrong.
"""

from __future__ import annotations

import os

from pxr import Usd, UsdGeom

SCHEMA_VERSION = 2


def create(path, basis, description):
    """A new stage whose root layer is the file at ``path`` (written when the stage is saved)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stage = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    layer = stage.GetRootLayer()
    layer.customLayerData = {"ruri:basis": basis, "ruri:schemaVersion": SCHEMA_VERSION}
    layer.documentation = description
    return stage
