"""What Painter's viewport shows the ported shader through.

The generated shader carries its own requirements in its header: the reflection
cubemap it was captured against, the grading strip shipped beside it, and -- the
one that is a correctness matter rather than a preference -- that the display must
NOT tone map, because the shader already applies the game's own tonemap and doing
it twice is simply wrong.

An import applies all three. This is where they can be looked at and applied again
without one: a project opened from disk, a resource replaced by hand, or a colour
managed project that refused the tone mapping the first time all leave the display
saying something other than what the shader asked for, and until now nothing said
so anywhere.

Registered as a "look" section (:mod:`Kernel.app.look`), so the tab that asks what
the frame finally looks like gets this host's answer without naming this host.
"""

from __future__ import annotations

import os

from . import shader, sp_apply
from ...Kernel import host as host_port
from ...Kernel.app import browser as app_browser
from ...Kernel.app import command
from ...Kernel.app.state import Field, Schema
from ...Kernel.app import state as app_state

STATE = "ruri_display"

#: What each switch is called and what it turns on, keyed by the option the
#: browser already remembers -- so the words are said once and this section reads
#: the same values the import does.
_SWITCHES = ("apply_environment", "apply_color_lut", "force_linear_tonemap")

DISPLAY = Schema("Display", """The display section's own state: the last thing it
did.""", (
    Field("status", app_state.STRING, ""),
))

HANDLERS = app_state.Handlers("Substance.display")


def state_of(context):
    return host_port.current().panel_state(context, STATE)


def _identified(context):
    """A shader stack is resolved from the install in front of the panel, so
    until one is identified there is nothing to state requirements."""
    return bool(shader.game_name())


def _apply(context, arguments):
    """Set the environment, the LUT and the tone mapping the shader documents."""
    state = state_of(context)
    lines = []
    try:
        sp_apply.apply_display_settings(
            lines, app_browser.as_options(app_browser.state_of(context)))
    except Exception as exc:
        state.status = "{0}: {1}".format(type(exc).__name__, exc)
        return {"CANCELLED"}
    state.status = "  ·  ".join(line for line in lines if not line.startswith("!! ")) \
        or "nothing to apply -- every switch below is off."
    for line in lines:
        host_port.current().log(host_port.WARNING if line.startswith("!! ")
                                else host_port.INFO, line)
    return None


APPLY = command.COMMANDS.define(
    "ruri.display_apply", "Apply Display Settings", _apply,
    description="Set the reflection environment, the colour LUT and the tone mapping the "
                "ported shader documents as its requirements",
    icon="IMPORT", requires=host_port.DISPLAY_SETTINGS, poll=_identified)


def _requirement(box, label, path):
    """One requirement, and whether the file the shader ships it as is there. A
    requirement whose asset is missing is the difference between a wrong-looking
    viewport and a wrong-looking viewport you can explain."""
    line = box.row()
    present = bool(path) and os.path.isfile(path)
    line.alert = not present
    line.label(text="{0}: {1}".format(label, os.path.basename(path) if path else "none"),
               icon="CHECKMARK" if present else "ERROR")


def draw(layout, context):
    """The Display section."""
    box = layout.box()
    box.label(text="Display", icon="SHADING_RENDERED")
    if not _identified(context):
        box.label(text="No install has been identified yet, so no shader states a "
                       "requirement.", icon="INFO")
        return

    try:
        environment, lut = shader.environment_path(), shader.color_lut_path()
    except Exception as exc:
        alert = box.row()
        alert.alert = True
        alert.label(text="{0}: {1}".format(type(exc).__name__, exc), icon="ERROR")
        return

    _requirement(box, "Environment", environment)
    _requirement(box, "Colour LUT", lut)
    # 这条不是偏好而是对错:着色器自己已经做过一次 tonemap,显示端再做一次就是做了两遍。
    box.label(text="Tone mapping: Linear -- the shader tonemaps itself, and a second "
                   "pass is simply wrong.", icon="INFO")

    switches = box.column(align=True)
    browser = app_browser.state_of(context)
    for key in _SWITCHES:
        switches.prop(browser, key)
    box.operator(APPLY.id, icon="IMPORT")
    state = state_of(context)
    if state.status:
        box.label(text=state.status, icon="INFO")


def register():
    host_port.current().register_state(STATE, DISPLAY, HANDLERS)


def unregister():
    host_port.current().unregister_state(STATE)
