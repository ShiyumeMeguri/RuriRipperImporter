"""Static scene projection; geometry and material roles use the existing host builders."""

from ....Host.Blender import materialise
from . import scene_lighting


def project(context, statement, built, builder, options):
    return scene_lighting.apply(context, statement, built, builder, options)


def register():
    materialise.MATERIALISATION_STAGES.add(project)


def unregister():
    materialise.MATERIALISATION_STAGES.remove(project)
