"""Reading a Unity ``Material`` document.

Only the READING is shared. What a host does with the result -- wire a
Principled BSDF graph, or fill an Endfield_Uber uniform table -- is entirely
its own business and stays on its own side. What is not its own business is
how Unity spells its property tables, which has changed across versions and
across writers, and which both importers previously got subtly differently:

* ``m_TexEnvs`` / ``m_Colors`` / ``m_Floats`` serialise as a LIST of single-key
  maps in a real Unity "Force Text" save, but as one nested map from
  AssetRipper's own YAML writer. Both are the same data model.
* Unity 2021+ moved integer-typed shader properties out of ``m_Floats`` into
  ``m_Ints``. A toggle that moved reads as absent -- and so defaults to off --
  for anything that only looks at ``m_Floats``.
* Shader keywords have had three serialisations: ``m_ShaderKeywords`` (one
  space-separated string, pre-2021) and the ``m_ValidKeywords`` /
  ``m_InvalidKeywords`` pair (2021+), of which only the valid list is enabled.

Which property is which surface input is not decided here: that is a mapping,
and a mapping is configuration -- see ``texture_roles`` for the layered table a
host resolves a material through.
"""

from __future__ import annotations


def shader_identity(shader_text):
    """The name a Shader asset calls itself, off its own ``Shader "..."`` head
    line. That string -- not a guid, not a file name -- is what a shader stack
    claims a material by, so it is also what a report has to say when nothing
    claims one."""
    if not shader_text:
        return None
    head = shader_text.lstrip()
    if not head.startswith("Shader"):
        return None
    first = head.split("\n", 1)[0]
    open_quote = first.find('"')
    if open_quote < 0:
        return None
    close_quote = first.find('"', open_quote + 1)
    if close_quote < 0:
        return None
    return first[open_quote + 1:close_quote]


def flatten(entries):
    """Normalize ``m_TexEnvs``/``m_Colors``/``m_Floats`` to ``{name: value}``,
    accepting either on-disk shape (see the module docstring)."""
    if isinstance(entries, dict):
        return entries
    out = {}
    for entry in entries or []:
        if isinstance(entry, dict):
            for key, value in entry.items():
                out[key] = value
    return out


def keywords(document):
    """The material's ACTIVE shader keywords, across all three serialisations."""
    if document is None:
        return set()
    data = document.data
    active = set()
    legacy = data.get("m_ShaderKeywords")
    if isinstance(legacy, str):
        active.update(k for k in legacy.split() if k)
    for entry in (data.get("m_ValidKeywords") or []):
        if isinstance(entry, str) and entry:
            active.add(entry)
    return active


class MaterialProperties:
    """A Unity Material's property tables, normalised.

    ``textures`` maps property name -> lowercase texture guid, in the order the
    file declares them.
    ``texture_st`` carries each property's tiling/offset as [sx, sy, ox, oy],
    ``floats`` merges m_Ints under m_Floats, ``colors`` is [r, g, b, a]."""

    __slots__ = ("name", "document", "shader_ref", "shader_name", "tex_envs", "textures",
                 "texture_st", "floats", "colors", "keywords", "disabled_passes")

    def __init__(self, name, document, shader_ref, tex_envs, textures,
                 texture_st, floats, colors, active_keywords, disabled_passes=(),
                 shader_name=None):
        self.name = name
        self.document = document
        self.shader_ref = shader_ref
        # The shader's own name, when the producer knows it outright. Unity does not:
        # a .mat names a shader ASSET, whose name lives in that asset's first line, so
        # this stays None there and the consumer resolves the reference. An engine
        # whose material states its base material by path knows the answer already.
        self.shader_name = shader_name
        self.tex_envs = tex_envs          # raw m_TexEnvs, flattened
        self.textures = textures
        self.texture_st = texture_st
        self.floats = floats
        self.colors = colors
        self.keywords = active_keywords
        # Passes the material itself switches off (Unity `disabledShaderPasses`,
        # pass-name strings). The only per-material truth for "this one draws no
        # outline" -- a float like _OutlineWidth is a historical key and proves
        # nothing (m_SavedProperties accumulates every key ever set).
        self.disabled_passes = list(disabled_passes)

    def __repr__(self):
        return "<MaterialProperties {0}>".format(self.name)

    def shader_guid(self):
        ref = self.shader_ref if isinstance(self.shader_ref, dict) else None
        guid = (ref or {}).get("guid")
        return str(guid).lower() if guid else None

    # -- lookups ------------------------------------------------------------

    def find_texture(self, names):
        """(property_name, guid) for the first populated slot among ``names``,
        in the given order, else (None, None)."""
        for name in names:
            guid = self.textures.get(name)
            if guid:
                return name, guid
        return None, None

    def find_color(self, names, default=None):
        for name in names:
            value = self.colors.get(name)
            if value is not None:
                return value
        return default

    def float(self, name, default=0.0):
        value = self.floats.get(name)
        return default if value is None else value


def parse_material(document):
    """Read a parsed Unity Material UnityDocument into MaterialProperties.

    Tolerates ``None`` (an unresolvable material reference) by returning empty
    tables, so callers do not need a separate branch for it."""
    data = document.data if document is not None else {}
    props = data.get("m_SavedProperties") or {}
    tex_envs = flatten(props.get("m_TexEnvs"))
    raw_colors = flatten(props.get("m_Colors"))
    # m_Floats wins a collision with m_Ints: it is the historical home of the
    # property, so a file carrying both was written by something that still
    # considers m_Floats authoritative.
    raw_floats = dict(flatten(props.get("m_Ints")))
    raw_floats.update(flatten(props.get("m_Floats")))

    textures = {}
    texture_st = {}
    for name, env in tex_envs.items():
        if not isinstance(env, dict):
            continue
        tex = env.get("m_Texture")
        if isinstance(tex, dict) and tex.get("guid"):
            textures[name] = str(tex["guid"]).lower()
        scale = env.get("m_Scale") or {}
        offset = env.get("m_Offset") or {}
        if scale or offset:
            texture_st[name] = [float(scale.get("x", 1.0)), float(scale.get("y", 1.0)),
                                float(offset.get("x", 0.0)), float(offset.get("y", 0.0))]

    floats = {}
    for name, value in raw_floats.items():
        try:
            floats[name] = float(value)
        except (TypeError, ValueError):
            continue

    colors = {}
    for name, value in raw_colors.items():
        if isinstance(value, dict):
            colors[name] = [float(value.get("r", 0.0)), float(value.get("g", 0.0)),
                            float(value.get("b", 0.0)), float(value.get("a", 0.0))]
        elif isinstance(value, (list, tuple)) and len(value) >= 4:
            colors[name] = [float(v) for v in value[:4]]

    disabled_passes = [str(p) for p in (data.get("disabledShaderPasses") or [])
                       if isinstance(p, str) and p]

    return MaterialProperties(
        name=str(data.get("m_Name", "")), document=document,
        shader_ref=data.get("m_Shader"), tex_envs=tex_envs, textures=textures,
        texture_st=texture_st, floats=floats, colors=colors,
        active_keywords=keywords(document), disabled_passes=disabled_passes)


def material_name(document, guid, fallback="Material"):
    """A stable display name for a material reference: its own ``m_Name`` when
    the document resolved, else a short guid-derived stand-in (never the empty
    string, which would collapse several materials into one nameless slot)."""
    if document is not None:
        name = document.data.get("m_Name")
        if name:
            return str(name)
    if guid:
        return "{0}_{1}".format(fallback, str(guid)[:8])
    return fallback
