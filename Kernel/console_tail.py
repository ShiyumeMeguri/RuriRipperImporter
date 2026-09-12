"""Surface the hook's own console, one line at a time, without composing a word.

The importer's progress is already narrated -- by the C# hook, as it crosses the
bridge: an ``Import : [GameBundle]`` line as it reads files, an
``Export : [ExportCost]`` line naming the very ``.anim`` it is decoding, a
``[ruri-vertex]`` line the mesh it is finishing. Those lines are the true,
always-accurate account of what is loading right now, and the panel's one status
line is simply the latest of them. Nothing here writes that text; it only reads
what the hook already prints, so there is no per-content string to keep in step
with the game.

It reads them at the source: the hook logs through AssetRipper's static
``Logger``, which fans every line out to a list of ``ILogger`` sinks (see
AssetRipper.Import/Logging/Logger.cs). This registers ONE more sink beside the
console one, mirrored by ``Remove`` on stop, so every line the hook prints is
also handed here the instant it prints -- on whatever thread printed it, so the
sink only ever stores a string and never touches bpy.

Deliberately NOT a file-descriptor redirect: on Windows, redirecting fd 2 out
from under .NET invalidates its cached ``Console.Error`` handle, and the very
export we are narrating throws ``IOException`` instead of running. Reading the
logger's own fan-out has no such side effect -- the console keeps working exactly
as before, and this is simply a second reader of it.
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_LINE = [""]         # latest line, boxed so the sink's Log can rebind it
_SINK = [None]       # the live ILogger instance while registered (kept referenced)
_LOGGER = [None]     # AssetRipper's static Logger, resolved once the runtime is up
_SINK_TYPE = [None]  # the sink class, defined once the CLR interface is importable


def start():
    """Register the log sink. Idempotent; a no-op (leaving a blank line) when the
    CLR runtime is not up yet, so the panel simply shows nothing rather than the
    load failing over its status line."""
    if _SINK[0] is not None:
        return
    logger, sink_type = _resolve()
    if logger is None:
        return
    with _LOCK:
        _LINE[0] = ""
    sink = sink_type()
    logger.Add(sink)
    _SINK[0] = sink


def latest():
    """The newest line the hook has printed since start(), or ''."""
    with _LOCK:
        return _LINE[0]


def stop():
    """Unregister the sink. Safe to call when never started."""
    sink = _SINK[0]
    _SINK[0] = None
    if sink is not None and _LOGGER[0] is not None:
        try:
            _LOGGER[0].Remove(sink)
        except Exception:
            pass
    with _LOCK:
        _LINE[0] = ""


def _resolve():
    """AssetRipper's ``Logger`` and a sink class bound to its ``ILogger``, or
    (None, None) when the runtime is not up. The class is defined lazily and once:
    a class deriving from a .NET interface needs that interface to exist first, and
    redefining it would register a second CLR type for no reason."""
    if _SINK_TYPE[0] is not None:
        return _LOGGER[0], _SINK_TYPE[0]
    try:
        from AssetRipper.Import.Logging import ILogger, Logger, LogCategory
    except Exception:
        return None, None
    category_none = getattr(LogCategory, "None")   # 'None' is a Python keyword

    class _Sink(ILogger):
        __namespace__ = "RuriRipperImporter.ConsoleTail"

        def Log(self, log_type, category, message):
            # Runs under the Logger's own lock, on whatever thread logged -- so it
            # must be quick and must never throw (a throwing sink breaks the very
            # logging call it rode in on). Format matches BridgeLogger's console
            # line exactly: the category, then the message.
            try:
                text = str(message)
                if category != category_none:
                    text = "{0} : {1}".format(category, text)
                with _LOCK:
                    _LINE[0] = text
            except Exception:
                pass

        def BlankLine(self, num_lines):
            pass

    _LOGGER[0] = Logger
    _SINK_TYPE[0] = _Sink
    return Logger, _Sink
