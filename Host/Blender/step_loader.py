"""Loading without freezing Blender, for any loader that can say what its steps are.

A load is two kinds of work: crossing the bridge (slow, touches no bpy) and
building the scene (must be on the main thread). Written as one function, the
crossing blocks the UI and the user watches a frozen window with no idea whether
it is working. Written as a GENERATOR that yields its steps, the same sequence can
be driven two ways -- inline for a script, or modally with the reads on a worker
thread -- and the second one is what keeps the window alive.

So a loader here describes its work and does not schedule it:

    def load_steps(context, ...):
        thing = yield Read(lambda: cross_the_bridge(...))   # off the main thread
        yield Mark(0.5)                                     # a checkpoint to redraw at
        build(thing)                                        # main thread, between ticks
        return result

``run`` drives that inline; ``ModalSteps`` drives it in a window. Both walk the
SAME generator, so a headless build and an interactive one cannot diverge -- which
is the property a second, "async" implementation of the same load would destroy.
"""

from __future__ import annotations

import threading


from ...Kernel import console_tail
from ...Kernel.app import command

# The step vocabulary is the kernel's: a long load is DESCRIBED once and driven
# per host -- modally here, on a worker thread in Painter -- and both drivers walk
# the same generator, which is the property a second "async version" would destroy.
Read = command.Read
Mark = command.Mark


def run(steps):
    """Drive a step sequence inline and return what it returns.

    The non-interactive path -- a script, a headless build, a window-less call.
    Every read runs where it stands, so the result is byte-for-byte what the modal
    driver produces."""
    sent = None
    try:
        while True:
            step = steps.send(sent)
            sent = step.fn() if isinstance(step, Read) else None
    except StopIteration as stop:
        return stop.value
    finally:
        steps.close()


class ModalSteps:
    """Drive a step sequence in a window: reads on a worker, builds between ticks.

    Mix into an Operator that supplies:
      ``load_steps(context)``  the generator to drive
      ``settle(context, result)``  what to do with what it returned
      ``status(context)``  a property group carrying ``loading`` and ``load_line``,
                           or None for a loader whose panel shows neither
      ``failure``  how to word a failure, in this loader's own terms
    """

    failure = "Loading failed"

    def invoke(self, context, event):
        if context.window is None:
            return self.execute(context)
        self._steps = self.load_steps(context)
        self._thread = None
        self._result = None
        self._error = None
        self._committed = False
        self._timer = None
        self._cancel_read = None
        console_tail.start()
        context.window_manager.progress_begin(0.0, 1.0)
        state = self.status(context)
        if state is not None:
            state.loading = True
            state.load_line = ""
            state.progress = 0.0
        self._timer = context.window_manager.event_timer_add(0.08, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            return self._on_cancel(context)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        self._pump_line(context)
        if self._thread is not None:
            if self._thread.is_alive():
                return {"RUNNING_MODAL"}   # a bridge read is still crossing -- stay live
            self._thread = None
            self._cancel_read = None
            if self._error is not None:
                return self._on_error(context, self._error)
            sent, self._result = self._result, None
            return self._advance(context, sent)
        return self._advance(context, None)

    def _advance(self, context, sent):
        """Pull one step. A ``Read`` is offloaded to a worker; every send that
        follows a read runs a main-thread bpy chunk inside it, which is where the
        scene actually changes -- so a resume with a real result marks the load
        committed (an ESC after that leaves a partial scene, and says so)."""
        if sent is not None:
            self._committed = True
        try:
            step = self._steps.send(sent)
        except StopIteration as stop:
            try:
                self._teardown(context)
                return self.settle(context, stop.value)
            except Exception as exc:
                return self._on_error(context, exc)
        except Exception as exc:
            return self._on_error(context, exc)
        if isinstance(step, Read):
            self._cancel_read = step.cancel
            self._offload(step.fn)
        if step.progress is not None:
            context.window_manager.progress_update(step.progress)
            state = self.status(context)
            if state is not None:
                state.progress = step.progress
        self._redraw(context)
        return {"RUNNING_MODAL"}

    def _offload(self, fn):
        """Run one cross-boundary read on a worker thread. It touches no bpy, and
        the main thread only polls -- so the single-session bridge is never in two
        threads at once (the reason _teardown waits an in-flight read out)."""
        self._result = None
        self._error = None

        def work():
            try:
                self._result = fn()
            except BaseException as exc:   # surfaced on the main thread next tick
                self._error = exc

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def _pump_line(self, context):
        state = self.status(context)
        if state is None:
            return
        line = console_tail.latest()
        if line and line != state.load_line:
            state.load_line = line
            self._redraw(context)

    def _redraw(self, context):
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ("VIEW_3D", "PROPERTIES"):
                    area.tag_redraw()

    def _on_cancel(self, context):
        if getattr(self, "_cancel_read", None) is not None:
            self._cancel_read()
        try:
            self._teardown(context)
        except Exception as exc:
            return self._on_error(context, exc)
        if getattr(self, "cancel_message", ""):
            self.report({"INFO"}, self.cancel_message)
        elif self._committed:
            self.report({"WARNING"},
                        "Cancelled mid-load -- the scene holds a partial result. Clear it "
                        "and load again for a clean one.")
        else:
            self.report({"INFO"}, "Cancelled before anything was built.")
        return {"CANCELLED"}

    def _on_error(self, context, exc):
        import traceback
        try:
            self._teardown(context)
        except Exception as cleanup:
            traceback.print_exception(type(cleanup), cleanup, cleanup.__traceback__)
            self.report({"ERROR"}, "{0} cleanup: {1}: {2}".format(
                self.failure, type(cleanup).__name__, cleanup))
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        self.report({"ERROR"}, "{0}: {1}: {2}".format(self.failure, type(exc).__name__, exc))
        return {"CANCELLED"}

    def _teardown(self, context):
        # Cancellable reads have already signalled their token before joining;
        # non-cancellable legacy reads retain the old single-session guarantee.
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.is_alive():
            thread.join()
        self._thread = None
        self._cancel_read = None
        try:
            if getattr(self, "_timer", None) is not None:
                context.window_manager.event_timer_remove(self._timer)
                self._timer = None
            state = self.status(context)
            if state is not None:
                state.loading = False
            console_tail.stop()
            try:
                context.window_manager.progress_end()
            except Exception:
                pass
        finally:
            steps, self._steps = getattr(self, "_steps", None), None
            if steps is not None:
                steps.close()


# The bar a steps command in flight is shown with. Written against the neutral
# vocabulary, so it is the kernel's and both hosts draw the same one; named here
# because every panel that drives a load reaches for it through this module.
draw_progress = command.draw_progress
