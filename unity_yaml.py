"""Tolerant parser for Unity's serialized YAML subset.

Unity asset files (.prefab/.asset/.mat/.controller/.anim/.mask/.meta) are valid
YAML 1.1 except for the document headers, which carry a class tag and a file id:

    %YAML 1.1
    %TAG !u! tag:unity3d.com,2011:
    --- !u!137 &137163425435797775
    SkinnedMeshRenderer:
      m_Mesh: {fileID: 4300000, guid: d017..., type: 2}
      ...

Blender ships no PyYAML, and PyYAML would choke on the custom !u! tags and is far
too slow on the multi-megabyte hex blobs found in mesh assets.  This module is a
purpose-built, zero-dependency, single-pass indentation parser that handles only
the constructs Unity actually emits: block maps, block sequences, flow maps,
flow sequences and scalars.  Long hex blobs (vertex data, index buffers, bone
name hashes) are kept verbatim as strings.

Public entry points:
    parse_file(path)  -> UnityDocument list
    parse_text(text)  -> UnityDocument list
A parsed file is also wrapped by UnityFile for fileId-keyed access.
"""

from __future__ import annotations

import re

try:
    from . import class_registry
except ImportError:  # standalone (non-package) testing
    try:
        import class_registry
    except ImportError:
        class_registry = None

# A scalar token is treated as a number only when it matches this and is short
# enough that we cannot accidentally swallow a hex blob made of digits only.
_NUMBER_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
_DOC_HEADER_RE = re.compile(r"^---\s+!u!(\d+)\s+&(-?\d+)(\s+stripped)?\s*$")


class UnityDocument:
    """One `--- !u!CLASS &FILEID` document within a Unity YAML file."""

    __slots__ = ("class_id", "file_id", "stripped", "class_name", "raw_class_name", "data")

    def __init__(self, class_id, file_id, stripped, class_name, data, raw_class_name=None):
        self.class_id = class_id          # int, stable across versions (e.g. 137)
        self.file_id = file_id            # int local file id (anchor)
        self.stripped = stripped          # bool, prefab-instance stripped node
        self.class_name = class_name      # canonical name (registry) or file name
        self.raw_class_name = raw_class_name or class_name  # name as written in file
        self.data = data                  # dict, the body of the document

    def __repr__(self):
        return f"<UnityDocument {self.class_name} &{self.file_id}>"


class UnityFile:
    """A parsed Unity YAML file: ordered documents plus a fileId index."""

    __slots__ = ("path", "documents", "by_id")

    def __init__(self, path, documents):
        self.path = path
        self.documents = documents
        self.by_id = {d.file_id: d for d in documents}

    def first(self, class_name):
        cid = _resolve_class_id(class_name)
        for d in self.documents:
            if (cid is not None and d.class_id == cid) or d.class_name == class_name \
                    or d.raw_class_name == class_name:
                return d
        return None

    def all(self, class_name):
        cid = _resolve_class_id(class_name)
        return [d for d in self.documents
                if (cid is not None and d.class_id == cid)
                or d.class_name == class_name or d.raw_class_name == class_name]

    def get(self, file_id):
        return self.by_id.get(file_id)


def _resolve_class_id(class_name):
    """Map a query name to its stable numeric class id, if known."""
    if class_registry is None:
        return None
    return class_registry.id_for_name(class_name)


def _convert_scalar(token):
    """Convert a bare scalar token to int/float/bool, else leave it a string."""
    if token == "":
        return None
    if len(token) <= 20 and _NUMBER_RE.match(token):
        # Integers without a decimal point or exponent stay ints.
        if "." not in token and "e" not in token and "E" not in token:
            try:
                return int(token)
            except ValueError:
                pass
        try:
            return float(token)
        except ValueError:
            pass
    return token


def _unquote(token):
    """Strip YAML single/double quotes and unescape the doubled-quote form."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        inner = token[1:-1]
        if token[0] == "'":
            return inner.replace("''", "'")
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return token


# --- flow collection parsing: {a: 1, b: 2} and [x, y, {..}] ------------------

def _parse_flow(text, pos):
    """Parse a flow node starting at text[pos]; return (value, next_pos)."""
    pos = _skip_ws(text, pos)
    ch = text[pos]
    if ch == "{":
        return _parse_flow_map(text, pos)
    if ch == "[":
        return _parse_flow_seq(text, pos)
    return _parse_flow_scalar(text, pos)


def _skip_ws(text, pos):
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    return pos


def _parse_flow_map(text, pos):
    result = {}
    pos += 1  # consume '{'
    pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] == "}":
        return result, pos + 1
    while True:
        pos = _skip_ws(text, pos)
        key, pos = _parse_flow_scalar_until(text, pos, ":")
        pos = _skip_ws(text, pos)
        if pos < len(text) and text[pos] == ":":
            pos += 1
        value, pos = _parse_flow(text, pos)
        result[key.strip()] = value
        pos = _skip_ws(text, pos)
        if pos >= len(text):
            break
        if text[pos] == ",":
            pos += 1
            continue
        if text[pos] == "}":
            pos += 1
            break
        break
    return result, pos


def _parse_flow_seq(text, pos):
    result = []
    pos += 1  # consume '['
    pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] == "]":
        return result, pos + 1
    while True:
        value, pos = _parse_flow(text, pos)
        result.append(value)
        pos = _skip_ws(text, pos)
        if pos >= len(text):
            break
        if text[pos] == ",":
            pos += 1
            pos = _skip_ws(text, pos)
            continue
        if text[pos] == "]":
            pos += 1
            break
        break
    return result, pos


def _parse_flow_scalar(text, pos):
    """A scalar inside a flow collection, terminated by , } ] or end."""
    if pos < len(text) and text[pos] in ("'", '"'):
        return _parse_flow_quoted(text, pos)
    start = pos
    while pos < len(text) and text[pos] not in ",}]":
        pos += 1
    return _convert_scalar(text[start:pos].strip()), pos


def _parse_flow_scalar_until(text, pos, stop):
    if pos < len(text) and text[pos] in ("'", '"'):
        value, npos = _parse_flow_quoted(text, pos)
        return value, npos
    start = pos
    while pos < len(text) and text[pos] != stop and text[pos] not in ",}]":
        pos += 1
    return text[start:pos].strip(), pos


def _parse_flow_quoted(text, pos):
    quote = text[pos]
    pos += 1
    start = pos
    buf = []
    while pos < len(text):
        ch = text[pos]
        if ch == quote:
            if quote == "'" and pos + 1 < len(text) and text[pos + 1] == "'":
                buf.append("'")
                pos += 2
                continue
            pos += 1
            break
        buf.append(ch)
        pos += 1
    return "".join(buf), pos


def _parse_flow_value(token):
    """Parse a value that may be flow ({..}/[..]) or a bare scalar."""
    token = token.strip()
    if not token:
        return None
    if token[0] in "{[":
        value, _ = _parse_flow(token, 0)
        return value
    if token[0] in ("'", '"'):
        return _unquote(token)
    return _convert_scalar(token)


# --- block structure parsing -------------------------------------------------

class _Lines:
    """Pre-tokenised non-blank lines with cached indentation."""

    __slots__ = ("raw", "indent", "n")

    def __init__(self, text):
        self.raw = []
        self.indent = []
        for line in text.split("\n"):
            stripped = line.rstrip("\r")
            content = stripped.lstrip(" ")
            if content == "" or content.startswith("#"):
                continue
            self.raw.append(content)
            self.indent.append(len(stripped) - len(content))
        self.n = len(self.raw)


def _split_key_value(content):
    """Split `key: value` honouring quoted keys; return (key, value_str)."""
    if content[0] in ("'", '"'):
        key, npos = _parse_flow_quoted(content, 0)
        rest = content[npos:]
        rest = rest.lstrip()
        if rest.startswith(":"):
            return key, rest[1:].strip()
        return key, ""
    # Find the first ": " or trailing ":" that is the map separator.
    idx = content.find(":")
    while idx != -1:
        after = content[idx + 1:idx + 2]
        if after == "" or after == " ":
            return content[:idx].strip(), content[idx + 1:].strip()
        idx = content.find(":", idx + 1)
    return content.strip(), ""


def _is_dash(content):
    return content == "-" or content.startswith("- ")


def _parse_block(lines, start, indent):
    """Parse a block node (map or sequence) at the given indentation."""
    if start >= lines.n:
        return None, start
    if _is_dash(lines.raw[start]):
        return _parse_sequence(lines, start, indent)
    return _parse_map(lines, start, indent)


def _parse_map(lines, start, indent):
    """Parse a block mapping whose keys sit at exactly `indent`.

    Sequence values may be indented either deeper than the key or, in Unity's
    style, at the very same column as the key.
    """
    result = {}
    i = start
    while i < lines.n:
        ind = lines.indent[i]
        content = lines.raw[i]
        if ind < indent:
            break
        if ind > indent:
            i += 1  # defensive: skip stray over-indentation
            continue
        if _is_dash(content):
            break  # a sequence at this column belongs to an enclosing node
        key, value_str = _split_key_value(content)
        if value_str != "":
            result[key] = _parse_flow_value(value_str)
            i += 1
            continue
        # Empty inline value: the real value is the following block, if any.
        took = False
        if i + 1 < lines.n:
            ni = lines.indent[i + 1]
            nc = lines.raw[i + 1]
            if _is_dash(nc) and ni >= indent:
                result[key], i = _parse_sequence(lines, i + 1, ni)
                took = True
            elif (not _is_dash(nc)) and ni > indent:
                result[key], i = _parse_map(lines, i + 1, ni)
                took = True
        if not took:
            result[key] = None
            i += 1
    return result, i


def _parse_sequence(lines, start, indent):
    """Parse a block sequence whose `-` markers sit at exactly `indent`."""
    result = []
    i = start
    while i < lines.n and lines.indent[i] == indent and _is_dash(lines.raw[i]):
        content = lines.raw[i]
        body = content[2:] if content.startswith("- ") else ""
        if body.strip() == "":
            # Element body lives on the following, more-indented lines.
            if i + 1 < lines.n and lines.indent[i + 1] > indent:
                elem, i = _parse_block(lines, i + 1, lines.indent[i + 1])
            else:
                elem = None
                i += 1
            result.append(elem)
            continue
        stripped_body = body.lstrip()
        if stripped_body[0] in "{[":
            result.append(_parse_flow_value(body))
            i += 1
            continue
        key, value_str = _split_key_value(body)
        if value_str == "" and ":" not in body:
            result.append(_parse_flow_value(body))
            i += 1
            continue
        # Element is a mapping whose first key sits on the dash line.  Rewrite
        # the dash line into a plain map line and let _parse_map consume it
        # together with the element's remaining keys.
        extra = len(body) - len(stripped_body)
        virtual_indent = indent + 2 + extra
        lines.raw[i] = stripped_body
        lines.indent[i] = virtual_indent
        elem, i = _parse_map(lines, i, virtual_indent)
        result.append(elem)
    return result, i


def parse_text(text, path=None):
    """Parse a full Unity YAML file into a list of UnityDocument."""
    documents = []
    # Split on document headers while keeping the header lines.
    header_positions = []
    raw_lines = text.split("\n")
    for idx, line in enumerate(raw_lines):
        if line.startswith("--- "):
            m = _DOC_HEADER_RE.match(line.strip())
            if m:
                header_positions.append((idx, m))
    for h, (line_idx, m) in enumerate(header_positions):
        body_start = line_idx + 1
        body_end = header_positions[h + 1][0] if h + 1 < len(header_positions) else len(raw_lines)
        body_text = "\n".join(raw_lines[body_start:body_end])
        lines = _Lines(body_text)
        if lines.n == 0:
            data = {}
            class_name = ""
        else:
            # The body is a single-key map: ClassName: { ... }
            class_key, inline_val = _split_key_value(lines.raw[0])
            class_name = class_key
            if inline_val != "":
                data = _parse_flow_value(inline_val)
            elif lines.n > 1 and lines.indent[1] > lines.indent[0]:
                data, _ = _parse_block(lines, 1, lines.indent[1])
            else:
                data = {}
        class_id = int(m.group(1))
        # Prefer the canonical name from the all-version registry (keyed on the
        # stable numeric id); fall back to the name written in the file.
        canonical = class_registry.canonical_name(class_id) if class_registry else None
        documents.append(UnityDocument(
            class_id=class_id,
            file_id=int(m.group(2)),
            stripped=bool(m.group(3)),
            class_name=canonical or class_name,
            raw_class_name=class_name,
            data=data if isinstance(data, dict) else {"_value": data},
        ))
    return documents


def parse_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    return UnityFile(path, parse_text(text, path))
