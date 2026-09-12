# -*- coding: utf-8 -*-
"""End-to-end import orchestration.

One entry point per source of truth:

  * :func:`import_from_cabs` -- the primary path. A cabmap row selection is
    resolved through the pythonnet bridge straight out of the game install:
    the dependency closure comes back as in-memory Unity YAML documents and
    raw texture bytes, and nothing is ever written to disk except the single
    .glb Painter has to be handed and the channel PNGs it has to import.
  * :func:`import_from_disk` -- the fallback for an already-extracted YAML
    folder (a RuriYamlDumper dump, or any AssetRipper text export).

Both converge on the same private pipeline: build the mesh, plan every
material, bake its textures, create the project, wire it.

Project creation is asynchronous on Painter's side, so the tail of the pipeline
runs from the ProjectEditionEntered event rather than inline.
"""

from __future__ import annotations

import os
import time

import substance_painter.event
import substance_painter.project

from . import settings, manifest_plan, model_builder, sp_apply, texture_pipeline, unity_material
from ...RuriRipperPyBridge.unity import asset_db
from ...RuriRipperPyBridge.unity import material as bridge_material


class ImportJob:
    """Everything one import needs, carried from the click to the
    project-ready callback.

    ``prefab_files`` is every root this project is made of -- a project is one
    mesh FILE, and a glTF file holds as many roots as were asked for."""

    def __init__(self, name, db, prefab_files, options):
        self.name = name
        self.db = db
        self.prefab_files = ([prefab_files] if not isinstance(prefab_files, (list, tuple))
                             else list(prefab_files))
        self.options = options
        #: Unity-space 4x4 per root, for a piece whose own transform tree is not
        #: where it belongs. None throughout for an ordinary import.
        self.placements = None
        self.cache_dir = os.path.join(settings.cache_dir(), _safe(name))
        self.build = None
        self.plans = {}
        self.baked_channels = {}
        self.baked_params = {}
        self.report = []
        self.seconds = 0.0

    def texture_dir(self):
        return os.path.join(self.cache_dir, "Textures")

    def glb_path(self):
        return os.path.join(self.cache_dir, _safe(self.name) + ".glb")


_UNSAFE = set('<>:"/\\|?*')


def _safe(name):
    return "".join("_" if c in _UNSAFE or ord(c) < 32 else c
                   for c in str(name)).strip() or "model"


# ---------------------------------------------------------------------------
# Stage 1-3: mesh, material plans, textures (all before Painter is involved)
# ---------------------------------------------------------------------------
def prepare(job, progress=None):
    """Decode the model and bake every texture. Returns the job, whose
    ``build`` is None when nothing importable was found."""
    started = time.time()
    force = bool(settings.get("force_rebuild", False))

    def step(message):
        if progress is not None:
            progress(message)

    step("Building mesh...")
    # A prefab carries its own renderers (mesh + material binding); a lone Mesh
    # asset has neither, so it becomes a single primitive named after itself.
    only = job.prefab_files[0] if len(job.prefab_files) == 1 else None
    if only is not None and only.first("Mesh") is not None \
            and only.first("GameObject") is None:
        job.build = model_builder.build_from_mesh(
            job.db, only, job.name, job.glb_path(), job.options)
    else:
        job.build = model_builder.build_from_prefabs(
            job.db, job.prefab_files, job.name, job.glb_path(), job.options,
            placements=job.placements)
    job.report.append("model: {0}".format(job.build.summary()))
    for warning in job.build.warnings:
        job.report.append("!! " + warning)
    if job.build.glb_path is None:
        job.seconds = time.time() - started
        return job
    finish_textures(job, progress)
    job.seconds = time.time() - started
    return job


def finish_textures(job, progress=None):
    """Plan every Texture Set the built model names and bake its channels.

    Split out because the MODEL half is not one thing: a Unity closure decodes
    prefabs, an engine that states its own placements writes them directly -- and
    both then want exactly this. Reached with ``job.build`` already filled."""
    force = bool(settings.get("force_rebuild", False))

    def step(message):
        if progress is not None:
            progress(message)

    def texture_exists(guid):
        if not guid:
            return False
        if job.db.resolve_guid(guid):
            return True
        texture_bytes = getattr(job.db, "texture_bytes", None)
        return bool(callable(texture_bytes) and texture_bytes(guid))

    shader_names = {}

    def shader_named(guid):
        """What a shader asset calls itself -- its own ``Shader "..."`` line,
        read out of the closure and remembered, since a character's materials
        point at a handful of shaders between them."""
        if not guid:
            return None
        if guid not in shader_names:
            shader_names[guid] = bridge_material.shader_identity(job.db.raw_text(guid))
        return shader_names[guid]

    step("Planning materials...")
    for set_name, entry in job.build.materials.items():
        plan = manifest_plan.build_plan(set_name, entry.guid, entry.props,
                                        texture_exists, shader_named,
                                        job.build.face_basis)
        job.plans[set_name] = plan
        for warning in plan.warnings:
            job.report.append("!! " + warning)

    step("Baking textures...")
    baker = texture_pipeline.TextureBaker(
        job.db, job.texture_dir(), flip_green=False, force=force)
    for set_name, plan in sorted(job.plans.items()):
        job.baked_channels[set_name] = baker.run(set_name, plan.channel_jobs)
        job.baked_params[set_name] = baker.run(set_name, plan.param_jobs)
    baker.flush()
    for warning in baker.warnings:
        job.report.append("!! " + warning)
    job.report.append("textures: {0} written, {1} reused, into {2}".format(
        baker.written, baker.reused, job.texture_dir()))
    return job


# ---------------------------------------------------------------------------
# Stage 4-5: Painter project + wiring (project creation is asynchronous)
# ---------------------------------------------------------------------------
_pending = [None]
_on_finished = [None]
_on_error = [None]


def _disconnect():
    try:
        substance_painter.event.DISPATCHER.disconnect(
            substance_painter.event.ProjectEditionEntered, _on_project_ready)
    except Exception:
        pass


def _on_project_ready(_event):
    job = _pending[0]
    if job is None:
        return
    _pending[0] = None
    _disconnect()
    _finish(job)


def _finish(job):
    job.report.append("")
    try:
        sp_apply.apply(job.plans, job.baked_channels, job.baked_params, job.report,
                       job.options)
    except Exception:
        import traceback
        job.report.append("!! wiring raised: " + traceback.format_exc(limit=4))
    callback = _on_finished[0]
    _on_finished[0] = None
    _on_error[0] = None
    if callback is not None:
        try:
            callback(job)
        except Exception:
            pass


def _fail(message):
    callback = _on_error[0]
    _on_finished[0] = None
    _on_error[0] = None
    _pending[0] = None
    _disconnect()
    if callback is not None:
        callback(message)


def launch(job, on_finished=None, on_error=None, reuse_open_project=False):
    """Create the Painter project (or reuse the open one) and wire everything
    up once it is ready.

    ``project.close()`` only POSTS the close (it queues Lock+Close actions), so
    creating the new project inline right after it would hit "another project is
    already opened". ``execute_when_not_busy`` is Painter's own way to sequence
    behind that, and project creation itself is asynchronous too -- hence the
    ProjectEditionEntered handler for the wiring stage.
    """
    _on_finished[0] = on_finished
    _on_error[0] = on_error
    if reuse_open_project and substance_painter.project.is_open():
        job.report.append("wiring the already-open project (mesh left untouched)")
        _finish(job)
        return

    _pending[0] = job
    substance_painter.event.DISPATCHER.connect(
        substance_painter.event.ProjectEditionEntered, _on_project_ready)

    def _create():
        try:
            sp_apply.create_project(job.build.glb_path,
                                    settings.get("texture_resolution", 2048))
        except Exception:
            import traceback
            _fail(traceback.format_exc())

    if substance_painter.project.is_open():
        try:
            substance_painter.project.close()
        except Exception:
            import traceback
            _fail(traceback.format_exc())
            return
        substance_painter.project.execute_when_not_busy(_create)
    else:
        _create()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def jobs_from_closure(cab_names, resolved):
    """Turn an ALREADY RESOLVED closure into ready-to-launch ImportJobs.

    The closure crossing itself is not done here and must not be: it is the one
    bridge call every import flow shares (``Kernel.app.loading.resolve_closure``),
    and the whole point of that shared function is that a step loader runs it on a
    worker thread and hands the result to the main thread to build from. Crossing
    again here would be a second truth AND a second wait -- measured at two
    seconds of frozen UI per character, for an answer already in hand.

    Root attribution goes through the closure's ``seed_roots`` -- the cabmap's own
    CAB/addressable-path identity -- never a display-name match. ``clips_by_cab``
    is the animation half of the same export and is deliberately unused here:
    this host has no animation surface.
    """
    db = resolved["db"]
    roots = resolved["roots"]
    seed_roots = resolved["seed_roots"]
    scene_roots = resolved["scene_roots"]
    options = settings.import_options()

    # A scene row imports exactly its OWN root (a level's closure drags in every
    # shared prefab the whole dependency graph exports); a plain bundled row
    # imports every root its closure exports, since one actor prefab routinely
    # pulls a portrait variant along as a second top-level asset.
    seeds = [seed_roots.get(cab) for cab in cab_names]
    restricted = [guid for guid in seeds if guid and guid in scene_roots]
    unrestricted = any(guid is None or guid not in scene_roots for guid in seeds)
    selected = list(roots) if unrestricted else restricted

    jobs = []
    seen = set()
    for guid in selected:
        if guid in seen:
            continue
        seen.add(guid)
        prefab_file = db.load_guid(guid)
        if prefab_file is None:
            continue
        jobs.append(ImportJob(_root_name(prefab_file, guid), db, prefab_file, options))
    return jobs


def one_job(jobs, name):
    """Fold every resolved root into ONE project.

    What the browser selected, or what a game said one character is made of, is
    ONE thing to put in front of the user -- Blender puts every root in the same
    scene and this is the same statement in a host whose document is a file."""
    if not jobs:
        return None
    if len(jobs) == 1:
        return jobs[0]
    first = jobs[0]
    made = ImportJob(name or first.name, first.db,
                     [job.prefab_files[0] for job in jobs], first.options)
    return made


def _root_name(prefab_file, guid):
    for document in prefab_file.documents:
        if document.class_name == "GameObject":
            name = document.data.get("m_Name")
            if name:
                return str(name)
    return "asset_" + guid[:8]


def job_from_disk(path, progress=None):
    """Build a job from an on-disk Unity YAML asset -- a .prefab (a
    RuriYamlDumper dump or any text-serialised prefab) or a lone Mesh .asset."""
    if progress is not None:
        progress("Reading {0}...".format(os.path.basename(path)))
    assets_dir = asset_db.find_assets_dir(path)
    db = asset_db.AssetDatabase(os.path.dirname(path), assets_dir)
    unity_file = db.load_file(path)
    name = os.path.splitext(os.path.basename(path))[0]
    return ImportJob(name, db, unity_file, settings.import_options())
