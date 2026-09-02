"""
Main-thread bridge for background work.

    ui = Bridge()                # created on the GUI thread
    ui.call(fn, *args)           # from ANY thread: run fn(*args) on the GUI thread
    run_bg(fn, *args)            # run fn in a daemon thread

Qt widgets must only be touched on the GUI thread; a queued signal carries the
callable across. This is the Qt equivalent of tkinter's `after(0, …)`.
"""

import threading
from PySide6.QtCore import QObject, Signal, Slot


class Bridge(QObject):
    _invoke = Signal(object)

    def __init__(self):
        super().__init__()
        self._invoke.connect(self._run)

    def call(self, fn, *args, **kwargs):
        self._invoke.emit(lambda: fn(*args, **kwargs))

    @Slot(object)
    def _run(self, thunk):
        thunk()


def run_bg(fn, *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
