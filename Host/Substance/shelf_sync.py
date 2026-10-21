"""Keeping the generated shaders Painter runs the ones the generator last deployed.

Painter never reads a shader from where the generator writes it: putting a shader on a shelf
copies it there, and a project keeps its own copy of every shader its instances run -- the
one it was given when it was last switched, carried inside the project file. A deploy
therefore reaches a project only when something copies it twice: onto the shelf, and from the
shelf into the project's instances. A shelf or a project left holding an older generation
shades differently from Blender without saying so.

So every game's stack for this host is copied over any shelf that already carries it -- the
shader and its manifest together, they are one product -- when the plugin starts, whenever a
shelf finishes reading the disk (Painter then notices the new file and recompiles it), and
before an import wires a shader, and whenever the generator deploys a new generation while Painter
runs: the plugin's own files are watched, and a change of what they hold -- their version hash,
the generation the manifest names, or their bytes -- is copied the moment it is seen. Nothing is
taken from what Painter has kept: the comparison is always between the plugin's file and the
shelf's file as they lie on the disk now. A shelf that does not carry a stack is left alone:
putting a shader on a shelf is the person's choice, keeping it current is not.

And every instance of the open project that runs one of those shaders is moved onto the
shelf's copy when the project is ready and whenever a shelf finishes reading the disk -- a
file copied at start is not a resource until that crawl ends, and a project opened before it
ends finds nothing to move to. A crawl that ends while a project is still opening or closing
leaves it to the project's own ready event, and one that ends while a save holds the project
-- from the moment it is announced until after it says it is done, a save answers no question
about the project's shaders -- has its move made once the save has returned. Painter keeps an
instance's values across the move, by name; a value the new generation no longer declares
goes with the old one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil

from PySide6 import QtCore

import substance_painter.event
import substance_painter.exception
import substance_painter.js
import substance_painter.project
import substance_painter.resource

from ...Kernel import host as host_port
from ...Kernel import shaderstack


def _products():
    """Every stack this host was given: its shader's name and the files that make it up."""
    for stack in shaderstack.stacks().values():
        name = stack.shader_name()
        yield name, [stack.asset(name + ".glsl"), stack.manifest_path()]


def _digest(path):
    """The version hash of one file as it lies on the disk now, or empty when there is none."""
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read()).hexdigest()


def _generation(manifest_path):
    """The generation a manifest names, or empty when it names none or cannot be read."""
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            return str(json.load(handle).get("identity") or "")
    except (OSError, ValueError):
        return ""


def sync(report):
    """Copy each stack's shader and manifest over the shelf copies that differ from them.
    Returns how many files were copied; what was copied, or failed to be, goes into ``report``."""
    copied = 0
    roots = [shelf.path() for shelf in substance_painter.resource.Shelves.all()]
    for name, product in _products():
        for root in roots:
            folder = os.path.join(root, "shaders")
            if not os.path.isfile(os.path.join(folder, name + ".glsl")):
                continue
            for source in product:
                target = os.path.join(folder, os.path.basename(source))
                wanted = _digest(source)
                held = _digest(target)
                if wanted == held:
                    continue
                before = _generation(target)
                try:
                    shutil.copyfile(source, target)
                except OSError as error:
                    report.append("!! shelf sync failed {0}: {1}".format(target, error))
                    continue
                copied += 1
                report.append("synced {0} into the shelf (version {1} -> {2}{3}): {4}".format(
                    os.path.basename(source), held[:8] or "none", wanted[:8],
                    ", generation {0} -> {1}".format(before or "unstamped", _generation(source) or "unstamped")
                    if source.endswith(".manifest.json") else "", target))
    return copied


def _shelf_shader(name, shelves):
    """The url of a shelf's shader of this name, or None when no shelf carries it. A search
    also answers with the copies projects carry; a shelf's own copy has the shelf as context."""
    for found in substance_painter.resource.search("u:shader " + name):
        identifier = found.identifier()
        if identifier.name == name and identifier.context in shelves:
            return identifier.url()
    return None


def follow(report):
    """Move every instance of the open project that runs a generated shader onto the shelf's
    copy of it. Returns how many instances moved."""
    if not substance_painter.project.is_open() or not substance_painter.project.is_in_edition_state():
        return 0
    shelves = {shelf.name() for shelf in substance_painter.resource.Shelves.all()}
    shelved = {}
    for name, _product in _products():
        url = _shelf_shader(name, shelves)
        if url is not None:
            shelved[name] = url
    moved = {}
    for instance in substance_painter.js.evaluate("alg.shaders.instances()"):
        url = shelved.get(str(instance.get("shader") or ""))
        if url is None or instance.get("url") == url:
            continue
        substance_painter.js.evaluate("alg.shaders.updateShaderInstance({0}, {1})".format(
            int(instance["id"]), json.dumps(url)))
        moved.setdefault(instance["shader"], []).append(str(instance.get("label") or instance["id"]))
    for name, labels in sorted(moved.items()):
        report.append("moved {0} instance(s) of {1} onto the shelf's generation: {2}".format(
            len(labels), name, ", ".join(sorted(labels))))
    return sum(len(labels) for labels in moved.values())


def _announce(report):
    host = host_port.current()
    for line in report:
        host.log(host_port.ERROR if line.startswith("!!") else host_port.INFO, line)


_save = {"in_flight": False, "follow_due": False}

#: How often the plugin's own shader files are looked at while the host runs, in milliseconds.
WATCH_INTERVAL = 1500

_watch = {"timer": None, "seen": None}


def _signature():
    """What the plugin's own product files are, by size and time of writing: cheap to read
    every tick, and any deploy changes it."""
    found = []
    for _name, product in _products():
        for source in product:
            try:
                stat = os.stat(source)
            except OSError:
                found.append((source, 0, 0))
                continue
            found.append((source, stat.st_size, stat.st_mtime_ns))
    return tuple(found)


def _tick():
    """The generator deployed while Painter ran: copy the new generation onto the shelves now, so
    the next push finds the shelf current. The shelf then reads the disk and ends its crawl,
    which moves the project's instances."""
    try:
        signature = _signature()
        if signature == _watch["seen"]:
            return
        _watch["seen"] = signature
        report = []
        sync(report)
        _announce(report)
    except substance_painter.exception.ServiceNotFoundError:
        _watch["seen"] = None


def _follow_now():
    report = []
    follow(report)
    _announce(report)


def _on_shelf_read(_event):
    """A shelf finished reading the disk: the copy a sync made earlier -- at start, before this
    crawl -- is a resource only now, so the open project follows it now too, or once the save
    holding it is done."""
    report = []
    sync(report)
    if _save["in_flight"]:
        _save["follow_due"] = True
    else:
        follow(report)
    _announce(report)


def _on_project_ready(_event):
    _save["in_flight"] = False
    _follow_now()


def _on_save_announced(_event):
    _save["in_flight"] = True


def _on_saved(_event):
    """The save lets go of the project when control is back in the event loop."""
    QtCore.QTimer.singleShot(0, _save_released)


def _save_released():
    _save["in_flight"] = False
    if _save["follow_due"]:
        _save["follow_due"] = False
        _follow_now()


_EVENTS = (
    (substance_painter.event.ShelfCrawlingEnded, _on_shelf_read),
    (substance_painter.event.ProjectEditionEntered, _on_project_ready),
    (substance_painter.event.ProjectAboutToSave, _on_save_announced),
    (substance_painter.event.ProjectSaved, _on_saved),
)


def start():
    """Sync now if the shelves are up, and again each time one finishes reading the disk;
    follow the shelves with each project as it becomes ready."""
    for event, handler in _EVENTS:
        substance_painter.event.DISPATCHER.connect(event, handler)
    report = []
    try:
        sync(report)
    except substance_painter.exception.ServiceNotFoundError:
        report.append("the shelves are not up yet; the generated shaders are synced as each finishes reading")
    _announce(report)
    _watch["seen"] = _signature()
    timer = QtCore.QTimer()
    timer.setInterval(WATCH_INTERVAL)
    timer.timeout.connect(_tick)
    timer.start()
    _watch["timer"] = timer


def stop():
    timer = _watch["timer"]
    if timer is not None:
        timer.stop()
        _watch["timer"] = None
    _watch["seen"] = None
    for event, handler in _EVENTS:
        substance_painter.event.DISPATCHER.disconnect(event, handler)
