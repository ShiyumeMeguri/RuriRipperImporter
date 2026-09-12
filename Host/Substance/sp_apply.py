# -*- coding: utf-8 -*-
"""The Substance Painter side: create the project from the built mesh, wire the
engine channels into a fill layer, import the sampler textures, and configure
one EndField_Uber shader instance per Texture Set.

The channel/fill-layer/shader-instance mechanics here are ported from the
proven endfield_auto_textures plugin (including its runtime API probing, which
exists because the exact class names and signatures differ between Painter
versions). What changed is only where the data comes from: MaterialPlan objects
and freshly baked file paths handed over in memory, instead of a folder of
files whose names had to be re-parsed.

Every step is idempotent -- running it again on the same project tops up
whatever failed rather than duplicating anything.
"""

from __future__ import annotations

import json
import os
import shutil
import traceback

import substance_painter.event
import substance_painter.layerstack
import substance_painter.logging
import substance_painter.project
import substance_painter.resource
import substance_painter.source
import substance_painter.textureset
import substance_painter.ui

import substance_painter.display as _display

from . import settings, shader, unity_material

FILL_LAYER_NAME = "RuriAutoTex"
LOG_CHANNEL = "RuriRipper"

shader_path = shader.source_path
environment_path = shader.environment_path
color_lut_path = shader.color_lut_path


def log(message):
    substance_painter.logging.info("[{0}] {1}".format(LOG_CHANNEL, message))


def warn(message):
    substance_painter.logging.warning("[{0}] {1}".format(LOG_CHANNEL, message))


# ---------------------------------------------------------------------------
# Small runtime-probing helpers (Painter's API surface moves between versions)
# ---------------------------------------------------------------------------
def _resolve_enum(owner, candidates):
    for name in candidates:
        value = getattr(owner, name, None)
        if value is not None:
            return value
    return None


def _channel_type(candidates):
    return _resolve_enum(substance_painter.textureset.ChannelType, candidates)


def _channel_format(candidates):
    return _resolve_enum(substance_painter.textureset.ChannelFormat, candidates)


def _node_name(node):
    for attr in ("get_name", "name"):
        fn = getattr(node, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return ""


def _js():
    try:
        import substance_painter.js as js_mod
        return js_mod
    except Exception:
        return None


def _js_eval(js_mod, code):
    return js_mod.evaluate(code)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
def _rid(resource):
    """Resource.identifier -- a property on new builds, a method on old ones."""
    attr = getattr(resource, "identifier", None)
    if attr is None:
        return None
    try:
        return attr() if callable(attr) else attr
    except TypeError:
        return attr


def _rid_field(rid, name):
    attr = getattr(rid, name, None)
    if attr is None:
        return None
    try:
        return attr() if callable(attr) else attr
    except TypeError:
        return attr


def import_texture(path):
    """Import a bitmap into the PROJECT shelf, reusing an identically named
    resource on a repeat run instead of stacking duplicates."""
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        for resource in substance_painter.resource.search(stem):
            rid = _rid(resource)
            if _rid_field(rid, "name") == stem and _rid_field(rid, "context") == "project":
                return resource
    except Exception:
        pass
    return substance_painter.resource.import_project_resource(
        path, substance_painter.resource.Usage.TEXTURE)


def resource_url(resource):
    try:
        rid = _rid(resource)
        url = _rid_field(rid, "url")
        return url if url else str(rid)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fill-layer sources
# ---------------------------------------------------------------------------
_SOURCE_CLASS_CANDIDATES = (
    "SourceTexture", "SourceResource", "SourceBitmap", "SourceImage",
    "TextureSource", "ResourceSource",
)
_source_api_reported = [False]


def _set_fill_source(fill, channel_type, resource, report):
    """Probe for the fill-layer source class/signature this Painter build wants."""
    rid = None
    try:
        rid = resource.identifier()
    except Exception:
        pass

    attempts = []
    for class_name in _SOURCE_CLASS_CANDIDATES:
        cls = getattr(substance_painter.source, class_name, None)
        if cls is None:
            continue
        for arg in (rid, resource):
            if arg is None:
                continue
            attempts.append((class_name + ("(id)" if arg is rid else "(res)"),
                             lambda c=cls, a=arg: c(a)))
    attempts.append(("raw ResourceID", lambda: rid))
    attempts.append(("raw Resource", lambda: resource))

    last_error = None
    for label, make in attempts:
        try:
            source = make()
            if source is None:
                continue
            fill.set_source(channel_type, source)
            return True
        except Exception as exc:
            last_error = "{0}: {1}".format(label, exc)

    if not _source_api_reported[0]:
        _source_api_reported[0] = True
        api = [n for n in dir(substance_painter.source) if not n.startswith("_")]
        report.append("    !! substance_painter.source members: {0}".format(", ".join(api)))
        try:
            report.append("    !! FillLayer members: {0}".format(
                ", ".join(n for n in dir(fill) if not n.startswith("_"))))
        except Exception:
            pass
    if last_error:
        report.append("    !! last attempt: {0}".format(last_error))
    return False


# ---------------------------------------------------------------------------
# Texture set stacks / channels
# ---------------------------------------------------------------------------
def _get_stack(ts_obj, set_name):
    for attr in ("get_stack", "all_stacks"):
        fn = getattr(ts_obj, attr, None)
        if callable(fn):
            try:
                result = fn()
                if isinstance(result, (list, tuple)):
                    if result:
                        return result[0]
                elif result is not None:
                    return result
            except Exception:
                pass
    try:
        return substance_painter.textureset.Stack.from_name(set_name)
    except Exception:
        return None


def _existing_channels(stack):
    fn = getattr(stack, "all_channels", None)
    if callable(fn):
        try:
            return set(fn().keys())
        except Exception:
            pass
    return None


def ensure_channel(ts_obj, stack, set_name, channel_key, channel_type, report):
    existing = _existing_channels(stack)
    if existing is not None and channel_type in existing:
        return True

    _candidates, js_token, js_format, srgb, js_label = unity_material.CHANNELS[channel_key]

    # 1) Preferred: the JS API, which takes an explicit storage format (and a
    #    label for the user channels).
    js_mod = _js()
    if js_mod is not None:
        try:
            if js_label:
                code = "alg.texturesets.addChannel({0}, {1}, {2}, {3})".format(
                    json.dumps(set_name), json.dumps(js_token),
                    json.dumps(js_format), json.dumps(js_label))
            else:
                code = "alg.texturesets.addChannel({0}, {1}, {2})".format(
                    json.dumps(set_name), json.dumps(js_token), json.dumps(js_format))
            _js_eval(js_mod, code)
            return True
        except Exception:
            pass

    # 2) Python API fallback.
    fmt = _channel_format(("sRGB8",)) if srgb else _channel_format(("L8", "RGB8"))
    for owner in (stack, ts_obj):
        fn = getattr(owner, "add_channel", None)
        if callable(fn):
            try:
                fn(channel_type, fmt) if fmt is not None else fn(channel_type)
                return True
            except Exception as exc:
                warn("add_channel({0}) failed: {1}".format(channel_type, exc))

    if existing is not None:
        report.append("    !! channel {0} missing and the API refused to add it "
                      "-- add it by hand and re-run".format(channel_key))
        return False
    return True


def find_or_insert_fill(stack):
    try:
        for node in substance_painter.layerstack.get_root_layer_nodes(stack):
            if _node_name(node) == FILL_LAYER_NAME:
                return node
    except Exception:
        pass
    position = substance_painter.layerstack.InsertPosition.from_textureset_stack(stack)
    layer = substance_painter.layerstack.insert_fill(position)
    try:
        layer.set_name(FILL_LAYER_NAME)
    except Exception:
        pass
    return layer


# ---------------------------------------------------------------------------
# Shader resource
# ---------------------------------------------------------------------------
def _sync_shader_to_shelves(report):
    """The bundled shader source is the truth; Painter's shelf import COPIES a
    .glsl, so editing the bundled file would otherwise never take effect. A
    content-level sync on every run makes Painter notice the change and
    recompile (hot reload)."""
    source_path = shader_path()
    if not os.path.isfile(source_path):
        return
    try:
        with open(source_path, "rb") as handle:
            source = handle.read()
    except OSError:
        return
    shelves_cls = getattr(substance_painter.resource, "Shelves", None)
    if shelves_cls is None:
        return
    try:
        shelves = shelves_cls.all()
    except Exception:
        return
    for shelf in shelves:
        try:
            root = shelf.path() if callable(getattr(shelf, "path", None)) else None
        except Exception:
            continue
        if not root:
            continue
        candidate = os.path.join(root, "shaders", shader.name() + ".glsl")
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "rb") as handle:
                current = handle.read()
            if current != source:
                shutil.copyfile(source_path, candidate)
                report.append("synced the bundled shader into the shelf: {0}".format(candidate))
        except OSError as exc:
            report.append("!! shelf sync failed {0}: {1}".format(candidate, exc))


def find_shader_resource(report):
    """The EndField_Uber shader resource, imported from the plugin's own shader
    folder when the shelf does not already carry it."""
    _sync_shader_to_shelves(report)
    for query in ("u:shader " + shader.name(), shader.name()):
        try:
            for resource in substance_painter.resource.search(query):
                rid = _rid(resource)
                name = _rid_field(rid, "name") or ""
                if shader.name().lower() not in str(name).lower():
                    continue
                usages_fn = getattr(resource, "usages", None)
                if callable(usages_fn):
                    try:
                        shader_usage = getattr(substance_painter.resource.Usage, "SHADER", None)
                        if shader_usage is not None and shader_usage not in usages_fn():
                            continue
                    except Exception:
                        pass
                return resource
        except Exception:
            pass

    source_path = shader_path()
    if os.path.isfile(source_path):
        shader_usage = getattr(substance_painter.resource.Usage, "SHADER", None)
        if shader_usage is not None:
            try:
                resource = substance_painter.resource.import_session_resource(
                    source_path, shader_usage)
                report.append("imported {0} from the plugin folder: {1}".format(
                    shader.name(), source_path))
                return resource
            except Exception as exc:
                report.append("!! importing the bundled shader failed: {0}".format(exc))
    else:
        report.append("!! bundled shader missing: {0}".format(source_path))
    return None


# ---------------------------------------------------------------------------
# Shader instances
# ---------------------------------------------------------------------------
def setup_shader_instances(set_payloads, report):
    """Give every Texture Set its own same-named EndField_Uber instance and
    write its uniforms.

    alg.shaders.shaderInstancesToObject() returns
        {"format": {...},
         "shaders":     {"<instance>": {"shader": "<file>", "shaderInstance": "<instance>", ...}},
         "texturesets": {"<texture set>": {"shader": "<instance>"}}}

    Flow: snapshot -> add a same-named instance + binding for every set ->
    FromObject (Painter creates the missing instances and assigns them) ->
    updateShaderInstance to EndField_Uber -> setParameters -> re-snapshot and
    verify. Idempotent. Returns True when every set verified.
    """
    report.append("== shader stage ==")
    js_mod = _js()
    if js_mod is None:
        report.append("!! this Painter build has no substance_painter.js bridge -- "
                      "shader instances cannot be configured automatically.")
        return False

    shader_resource = find_shader_resource(report)
    if shader_resource is None:
        report.append("!! {0} is neither in the shelf nor importable -- import "
                      "{1} manually (usage = shader) and run again.".format(
                          shader.name(), shader_path()))
        return False
    shader_url = resource_url(shader_resource)
    report.append("shader resource: {0}".format(shader_url))

    def snapshot():
        obj = _js_eval(js_mod, "alg.shaders.shaderInstancesToObject()")
        if isinstance(obj, str):
            obj = json.loads(obj)
        if not isinstance(obj, dict):
            raise ValueError("shaderInstancesToObject returned {0}".format(type(obj).__name__))
        return obj

    all_ok = True

    # -- 1) snapshot, add missing instances, bind each texture set --
    try:
        obj = snapshot()
    except Exception as exc:
        report.append("!! shaderInstancesToObject failed: {0}".format(exc))
        return False

    shaders_map = obj.setdefault("shaders", {})
    texturesets_map = obj.setdefault("texturesets", {})
    created, rebound = [], []
    for set_name in sorted(set_payloads):
        if not isinstance(shaders_map.get(set_name), dict):
            shaders_map[set_name] = {"shader": shader.name(), "shaderInstance": set_name}
            created.append(set_name)
        binding = texturesets_map.get(set_name)
        if not isinstance(binding, dict):
            texturesets_map[set_name] = {"shader": set_name}
            rebound.append(set_name)
        elif binding.get("shader") != set_name:
            binding["shader"] = set_name
            rebound.append(set_name)

    if created or rebound:
        try:
            _js_eval(js_mod, "alg.shaders.shaderInstancesFromObject({0})".format(json.dumps(obj)))
            report.append("instance layout applied: {0} created, {1} rebound".format(
                len(created), len(rebound)))
        except Exception as exc:
            # Some builds refuse an unknown shader name in FromObject -- create
            # the new instances by cloning an existing instance's shader (the
            # binding is unaffected) and let step 3 switch them over.
            template = None
            for label, entry in shaders_map.items():
                if isinstance(entry, dict) and entry.get("shader") and label not in created:
                    template = entry["shader"]
                    break
            retried = False
            if template:
                for set_name in created:
                    shaders_map[set_name]["shader"] = template
                try:
                    _js_eval(js_mod, "alg.shaders.shaderInstancesFromObject({0})".format(
                        json.dumps(obj)))
                    report.append("instance layout applied (cloned {0}, switched later): "
                                  "{1} created, {2} rebound".format(
                                      template, len(created), len(rebound)))
                    retried = True
                except Exception as retry_exc:
                    exc = retry_exc
            if not retried:
                report.append("!! shaderInstancesFromObject failed: {0}".format(exc))
                log(json.dumps(obj)[:4000])
                return False
    else:
        report.append("instance layout already correct")

    # -- 2) instance ids --
    try:
        instances = _js_eval(js_mod, "alg.shaders.instances()")
        if isinstance(instances, str):
            instances = json.loads(instances)
    except Exception as exc:
        report.append("!! alg.shaders.instances() unavailable: {0}".format(exc))
        return False
    by_label = {}
    for instance in instances or []:
        if isinstance(instance, dict):
            by_label[str(instance.get("label", ""))] = instance

    # -- 3) switch shader + write uniforms --
    for set_name, payload in sorted(set_payloads.items()):
        instance = by_label.get(set_name)
        if instance is None:
            report.append("* {0}: !! no same-named instance after FromObject".format(set_name))
            all_ok = False
            continue
        instance_id = instance.get("id")

        # Switch when the instance is not on EndField_Uber, or is on an older
        # copy of it (the shader source changed) -- switching resets the
        # parameters, so it has to happen before setParameters.
        identity = "{0} {1}".format(instance.get("shader", ""), instance.get("url", ""))
        instance_url = str(instance.get("url", "") or "")
        if (shader.name().lower() not in identity.lower()
                or (shader_url and instance_url and instance_url != shader_url)):
            try:
                _js_eval(js_mod, "alg.shaders.updateShaderInstance({0}, {1})".format(
                    json.dumps(instance_id), json.dumps(shader_url)))
            except Exception as exc:
                report.append("* {0}: !! updateShaderInstance failed: {1}".format(set_name, exc))
                all_ok = False
                continue

        ok_keys, bad_keys = [], []
        try:
            _js_eval(js_mod, "alg.shaders.setParameters({0}, {1})".format(
                json.dumps(instance_id), json.dumps(payload)))
            ok_keys = list(payload.keys())
        except Exception:
            # One bad key rejects the whole batch -- isolate so the other
            # ~120 uniforms still land.
            for key, value in payload.items():
                try:
                    _js_eval(js_mod, "alg.shaders.setParameters({0}, {1})".format(
                        json.dumps(instance_id), json.dumps({key: value})))
                    ok_keys.append(key)
                except Exception:
                    bad_keys.append(key)
        if bad_keys:
            all_ok = False
        report.append("* {0}: instance#{1} u_CharaPart={2}({3}) wrote {4}/{5} params{6}".format(
            set_name, instance_id, payload.get("u_CharaPart"),
            unity_material.PART_NAMES.get(payload.get("u_CharaPart"), "?"),
            len(ok_keys), len(payload),
            "; failed: " + ", ".join(bad_keys) if bad_keys else ""))

    # -- 4) verify --
    try:
        final = snapshot()
        final_shaders = final.get("shaders", {}) or {}
        final_sets = final.get("texturesets", {}) or {}
        report.append("-- verify --")
        for set_name in sorted(set_payloads):
            bound = (final_sets.get(set_name) or {}).get("shader")
            shader_name = str((final_shaders.get(bound) or {}).get("shader", ""))
            good = (bound == set_name) and (shader.name().lower() in shader_name.lower())
            if not good:
                all_ok = False
            report.append("{0} {1}: instance '{2}' shader '{3}'".format(
                "OK" if good else "FAIL", set_name, bound, shader_name))
    except Exception as exc:
        report.append("!! verification failed: {0}".format(exc))
        all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# Display settings
# ---------------------------------------------------------------------------
def apply_display_settings(report, options=None):
    """Hang the shader's companion environment/LUT and force the tone mapping
    the port requires.

    ``options`` is the same dict every other stage is handed -- the switches
    live on the panel and travel with the job. Reading the settings store here
    instead meant three switches nothing ever wrote and therefore three
    switches that did nothing.

    EndField_Uber's own header states both: [H6] the reflection cubemap becomes
    Painter's environment (CharCubemap.exr is the matching capture), and [H9]
    the HG post tonemap is built INTO the shader, so Painter's display tone
    mapping must be Linear or it is applied twice."""
    options = settings.import_options() if options is None else options
    if _display is None or getattr(_display, "set_environment_resource", None) is None:
        report.append("note: this Painter build has no display API -- set the environment "
                      "to shader/CharCubemap.exr and the tone mapping to Linear by hand.")
        return

    def _import(path, usage_name):
        usage = getattr(substance_painter.resource.Usage, usage_name, None)
        if usage is None or not os.path.isfile(path):
            return None
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            for resource in substance_painter.resource.search(stem):
                rid = _rid(resource)
                if _rid_field(rid, "name") == stem:
                    return rid
        except Exception:
            pass
        try:
            return _rid(substance_painter.resource.import_session_resource(path, usage))
        except Exception as exc:
            report.append("!! could not import {0}: {1}".format(os.path.basename(path), exc))
            return None

    if options.get("apply_environment", True):
        rid = _import(environment_path(), "ENVIRONMENT")
        if rid is not None:
            try:
                _display.set_environment_resource(rid)
                report.append("environment map set to CharCubemap.exr")
            except Exception as exc:
                report.append("!! set_environment_resource failed: {0}".format(exc))

    if options.get("apply_color_lut", True):
        rid = _import(color_lut_path(), "COLOR_LUT")
        if rid is not None:
            try:
                _display.set_color_lut_resource(rid)
                report.append("colour LUT set to CharShowLut3D.tga")
            except Exception as exc:
                report.append("!! set_color_lut_resource failed: {0}".format(exc))

    # EndField_Uber runs its own Endfield ACES tonemap ([H9], u_UseEndfieldTonemap),
    # so Painter must NOT tonemap again -- Linear is the correct display setting.
    if options.get("force_linear_tonemap", True):
        try:
            linear = getattr(_display.ToneMappingFunction, "Linear", None)
            if linear is not None:
                _display.set_tone_mapping(linear)
                report.append("display tone mapping forced to Linear "
                              "(EndField_Uber does the tonemap itself -- see [H9])")
        except RuntimeError:
            report.append("note: the project is colour managed, so the display tone mapping "
                          "cannot be set -- disable colour management to avoid a double tonemap.")
        except Exception as exc:
            report.append("!! set_tone_mapping failed: {0}".format(exc))


# ---------------------------------------------------------------------------
# Per texture set wiring
# ---------------------------------------------------------------------------
def wire_texture_set(ts_obj, set_name, plan, channel_files, param_files, report,
                     set_payloads):
    report.append("* {0}  [{1} {2}]".format(set_name, plan.part, plan.part_name()))

    stack = _get_stack(ts_obj, set_name)
    if stack is None:
        report.append("    !! no Stack for this texture set -- skipped")
        return

    # -- engine channels -> fill layer --
    wanted = {}
    for channel_key, path in sorted(channel_files.items()):
        spec = unity_material.CHANNELS.get(channel_key)
        if spec is None:
            continue
        channel_type = _channel_type(spec[0])
        if channel_type is None:
            report.append("    !! no ChannelType enum for {0} -- {1} skipped".format(
                spec[0], os.path.basename(path)))
            continue
        if not ensure_channel(ts_obj, stack, set_name, channel_key, channel_type, report):
            continue
        wanted[channel_type] = (channel_key, path)

    if wanted:
        fill = find_or_insert_fill(stack)
        try:
            current = set(fill.active_channels)
        except Exception:
            current = set()
        try:
            fill.active_channels = current | set(wanted.keys())
        except Exception as exc:
            warn("active_channels assignment failed: {0}".format(exc))

        for channel_type, (channel_key, path) in wanted.items():
            try:
                resource = import_texture(path)
            except Exception as exc:
                report.append("    !! import failed {0}: {1}".format(os.path.basename(path), exc))
                continue
            if _set_fill_source(fill, channel_type, resource, report):
                report.append("    {0:<16} <- {1}".format(channel_key, os.path.basename(path)))
            else:
                report.append("    !! could not wire {0} (see the API dump above)".format(
                    os.path.basename(path)))

    # -- sampler textures -> shader uniforms --
    payload = dict(plan.uniforms)
    for sampler, path in sorted(param_files.items()):
        try:
            resource = import_texture(path)
        except Exception as exc:
            report.append("    !! import failed {0}: {1}".format(os.path.basename(path), exc))
            continue
        url = resource_url(resource)
        if url:
            payload[sampler] = url
            report.append("    {0:<22} <- {1} (shader param)".format(
                sampler, os.path.basename(path)))

    set_payloads[set_name] = payload



def apply(plans, baked_channels, baked_params, report, options=None):
    """Wire every Texture Set of the OPEN project. ``plans`` maps texture set
    name -> MaterialPlan; the two baked dicts map the same name -> {target: path}."""
    set_payloads = {}
    try:
        texture_sets = substance_painter.textureset.all_texture_sets()
    except Exception as exc:
        report.append("!! all_texture_sets failed: {0}".format(exc))
        return False

    for ts_obj in texture_sets:
        try:
            set_name = ts_obj.name() if callable(getattr(ts_obj, "name", None)) else str(ts_obj)
        except Exception:
            set_name = str(ts_obj)
        plan = plans.get(set_name)
        if plan is None:
            report.append("* {0}".format(set_name))
            report.append("    !! no Unity material of this name in the import -- skipped")
            report.append("")
            continue
        try:
            wire_texture_set(ts_obj, set_name, plan,
                             baked_channels.get(set_name, {}),
                             baked_params.get(set_name, {}),
                             report, set_payloads)
        except Exception:
            report.append("    !! exception: " + traceback.format_exc(limit=3))
        report.append("")

    shader_ok = False
    if set_payloads:
        try:
            shader_ok = setup_shader_instances(set_payloads, report)
        except Exception:
            report.append("!! shader stage raised: " + traceback.format_exc(limit=3))
        report.append("")
        if shader_ok:
            report.append("== result: {0} texture set(s) bound to their own EndField_Uber "
                          "instance with textures and parameters written ==".format(
                              len(set_payloads)))
        else:
            report.append("== result: some steps failed (see !!/FAIL above) -- the whole pass "
                          "is idempotent, run it again to top up ==")
    else:
        report.append("== result: no texture set matched a Unity material ==")

    try:
        apply_display_settings(report, options)
    except Exception:
        report.append("!! display settings raised: " + traceback.format_exc(limit=3))

    for line in report:
        log(line)
    return shader_ok


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------
def create_project(mesh_path, texture_resolution=2048):
    """Create a Painter project on the built mesh with the settings this port
    needs (OpenGL normals -- the whole texture pipeline decodes to OpenGL
    tangent space -- and no imported cameras)."""
    kwargs = {}
    try:
        settings_kwargs = {
            "import_cameras": False,
            "default_texture_resolution": int(texture_resolution),
        }
        normal_format = getattr(substance_painter.project, "NormalMapFormat", None)
        if normal_format is not None:
            settings_kwargs["normal_map_format"] = normal_format.OpenGL
        kwargs["settings"] = substance_painter.project.Settings(**settings_kwargs)
    except Exception as exc:
        warn("project.Settings construction failed, using defaults: {0}".format(exc))
    substance_painter.project.create(mesh_file_path=mesh_path, **kwargs)
