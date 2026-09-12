# -*- coding: utf-8 -*-
"""Manifest-driven material planning for the GENERATED shader.

The generator (Ruri.RenderPipelines.Generator, Substance backend) writes two
files into shader/: the GLSL itself and a projection manifest. The manifest is
the single contract both sides share -- it says, for every Unity source
texture, which Painter inputs its channels were split into and by which named
operation. This module turns that contract plus one .mat document into the
same MaterialPlan shape the rest of the plugin already consumes (TextureBaker,
sp_apply), so the wiring machinery is untouched.

No per-texture or per-uniform tables live here: texture routing comes from
manifest inputs, uniform vocabulary comes from manifest panel (the generated
shader's parameter names ARE the Unity property names), and part numbering
comes from manifest panel.parts. Editing the C# truth source and re-running
codegen is the only way any of that changes.
"""

from __future__ import annotations

import json
import os

from . import shader, unity_material

_manifest_cache = {"path": None, "mtime": None, "data": None}


def manifest_path():
    return shader.manifest_path()


def load_manifest():
    """Load (and cache by mtime) the projection manifest. Missing manifest is a
    hard error: without the contract there is no honest way to bake anything."""
    path = manifest_path()
    mtime = os.path.getmtime(path)
    if _manifest_cache["path"] == path and _manifest_cache["mtime"] == mtime:
        return _manifest_cache["data"]
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    unknown = [op for op in data.get("operations", []) if op not in _BAKE_OPS]
    if unknown:
        raise RuntimeError(
            "manifest {0} uses bake operations this plugin does not implement: {1} "
            "-- update manifest_plan._BAKE_OPS in lockstep with the generator".format(
                os.path.basename(path), ", ".join(unknown)))
    _manifest_cache.update(path=path, mtime=mtime, data=data)
    return data


# ---------------------------------------------------------------------------
# Named bake operations (the manifest's `operations` vocabulary) -> the
# texture_pipeline op executed on the decoded source image. Channel segments
# with no operation are plain extracts.
# ---------------------------------------------------------------------------
def _op_for(operation, channels):
    if operation == "unpack_normal_alpha_green":
        return unity_material.OP_NORMAL_UNITY
    if operation == "unpack_normal_pair":
        return (unity_material.OP_NORMAL_SPLIT_RG if channels == "rg"
                else unity_material.OP_NORMAL_SPLIT_BA)
    if operation == "invert":
        if channels != "a":
            return "invert:" + channels
        return unity_material.OP_INVERT_A
    if operation == "":
        if channels == "rgba":
            return unity_material.OP_COPY_RGBA
        if channels == "rgb":
            return unity_material.OP_COPY_RGB
        if channels == "r":
            return unity_material.OP_CHANNEL_R
        if channels == "g":
            return unity_material.OP_CHANNEL_G
        if channels == "b":
            return unity_material.OP_CHANNEL_B
        if channels == "a":
            return unity_material.OP_CHANNEL_A
        return "extract:" + channels
    raise RuntimeError("unmapped bake operation {0!r} on channels {1!r}".format(
        operation, channels))


_BAKE_OPS = ("invert", "repack_normal", "unpack_normal_alpha_green", "unpack_normal_pair")


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------
def build_plan(name, guid, props, texture_exists, shader_named, face_basis=None):
    """The manifest replaces every routing table. ``texture_exists`` and
    ``shader_named`` are the two things a .mat cannot answer alone -- whether a
    referenced texture is really in the closure, and what the shader asset it
    points at calls itself -- so the caller, which holds the closure, answers
    them. ``face_basis`` is accepted for call-site compatibility: the generated
    shader takes the face axes as plain uniforms from the material."""
    manifest = load_manifest()
    plan = unity_material.MaterialPlan(name, guid)

    textures, floats, colors = props.textures, props.floats, props.colors

    bound = {prop: g for prop, g in textures.items() if g and texture_exists(g)}

    # -- texture routing: every manifest input whose source is bound gets a job.
    for entry in manifest.get("inputs", []):
        input_id = entry.get("Id", "")
        kind = entry.get("Kind", "RawTexture")
        for source in entry.get("Sources", []):
            prop = source.get("Source", "")
            if prop not in bound:
                continue
            op = _op_for(source.get("Operation", ""), source.get("Channels", "rgba"))
            job_kind = "param" if kind == "RawTexture" else "channel"
            plan_jobs = plan.param_jobs if job_kind == "param" else plan.channel_jobs
            plan_jobs.append(unity_material.TextureJob(
                bound[prop], op, job_kind, input_id, prop))

    # -- uniforms: the generated shader's parameter names are the Unity property
    #    names, so .mat values pass through 1:1. Color-typed panel widgets get
    #    the same sRGB->linear conversion Unity applies when uploading color
    #    properties. WHICH ones is manifest.panel.uniforms[].srgb -- the
    #    generator's own IsGammaEncoded, the same evaluation it wrote into the
    #    GLSL annotations. The widget cannot answer it ([HDR] and [HDR][Gamma]
    #    are both HDRColor and disagree; [Gamma] _Metallic is not a colour at
    #    all), and neither can the property's name.
    panel = manifest.get("panel") or {}
    widget_of = {u.get("name", ""): u.get("widget", "")
                 for u in panel.get("uniforms", [])}
    gamma_named = {u.get("name", "") for u in panel.get("uniforms", []) if u.get("srgb")}
    keyword_names = set(panel.get("keywords", []))

    for prop, value in floats.items():
        if prop not in widget_of:
            continue
        plan.uniforms[prop] = (unity_material._srgb_to_linear(float(value))
                               if prop in gamma_named else value)
    for prop, rgba in colors.items():
        if prop not in widget_of:
            continue
        if prop in gamma_named:
            plan.uniforms[prop] = ([unity_material._srgb_to_linear(float(v)) for v in rgba[:3]]
                                   + [float(v) for v in rgba[3:]])
        else:
            plan.uniforms[prop] = list(rgba)

    for keyword in props.keywords:
        if keyword in keyword_names:
            plan.uniforms[keyword] = True

    # -- texture tiling/offset: <prop>_ST uniforms the shader actually declares.
    for tex_prop, st in props.texture_st.items():
        st_name = tex_prop + "_ST"
        if st_name in widget_of:
            plan.uniforms[st_name] = list(st)

    # -- part: explicit _CharaPartID wins; otherwise the existing inference,
    #    translated into the manifest's part numbering BY NAME (the two sides
    #    number their tails differently -- 8 is LiquidAg in the style module
    #    and ShadowReceiver in the legacy table).
    plan.part = _resolve_part(manifest, name, props, shader_named, plan)
    variant = panel.get("variantUniform", "")
    if variant:
        plan.uniforms[variant] = plan.part

    # -- face SDF axes: Painter world axes, pinned by the Unity 180-deg
    #    experiment (the .mat carries bind-pose values in the game's own
    #    convention; the measured landmark basis picked the wrong pair --
    #    see unity_material.build_plan's identical pin).
    if plan.part == _part_value(manifest, "Face"):
        if "_FaceForward" in widget_of:
            plan.uniforms["_FaceForward"] = list(unity_material.FACE_FORWARD_DEFAULT)
        if "_FaceRight" in widget_of:
            plan.uniforms["_FaceRight"] = list(unity_material.FACE_RIGHT_DEFAULT)

    plan.textures = textures
    plan.floats = floats
    plan.colors = colors
    plan.texture_st = props.texture_st
    return plan


def _part_value(manifest, part_name):
    for part in (manifest.get("panel") or {}).get("parts", []):
        if part.get("name") == part_name:
            return part.get("value", -1)
    return -1


def _resolve_part(manifest, name, props, shader_named, plan):
    """Which part this material is, read off the shader asset it points at --
    ``[StylePart(ShaderName=)]`` taken backwards, with ``Discriminator`` telling
    apart the parts that share one surface (Standard and Fur are both
    HGRP/CharacterNPR; ``_UseCharacterFur`` separates them).

    The material's NAME is not a criterion and never was: in one shipped
    character m_fx_actor_jsspsi_cloth_07_01 is named like an effect and points
    at HGRP/CharacterNPR, so guessing by prefix put it on the wrong branch."""
    parts = (manifest.get("panel") or {}).get("parts", [])
    claimed = props.shader_name or shader_named(props.shader_guid()) or ""
    wanted = [entry for entry in parts if claimed and (
        entry.get("shader") == claimed or claimed in (entry.get("aliases") or []))]
    for entry in wanted:
        gate = entry.get("discriminator") or ""
        if gate and float(props.floats.get(gate, 0.0) or 0.0) > 0.5:
            return entry.get("value", 0)
    for entry in wanted:
        if not (entry.get("discriminator") or ""):
            return entry.get("value", 0)
    plan.warnings.append(
        "{0}: shader {1!r} claims no part in the generated shader's part "
        "vocabulary ({2}) -- using Standard".format(
            name, claimed, ", ".join(entry.get("name", "") for entry in parts)))
    return next((entry.get("value", 0) for entry in parts
                 if entry.get("name") == "Standard"), 0)
