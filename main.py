"""
Entry point (PySide6).  Uncaught exceptions are written to
%APPDATA%\\ABAP_AI\\crash.log and shown in a message box.
"""
import os
import sys
import traceback
import datetime


def _crash_log_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ABAP_AI")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "crash.log")


def _report(tb: str):
    path = _crash_log_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{tb}")
    except OSError:
        pass
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "ABAP AI IDE — error",
                             f"An unexpected error occurred.\nDetails were written to:\n{path}\n\n{tb[-1500:]}")
    except Exception:
        print(tb, file=sys.stderr)


def _icon_path() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "abap_ai.ico")


def main():
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont, QIcon
        from ui import theme
        from ui.main_window import MainWindow

        if sys.platform == "win32":        # own taskbar identity (icon / grouping) instead of python.exe's
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("sekerahmet.ABAP_AI_IDE")
            except Exception:
                pass
        app = QApplication(sys.argv)
        app.setApplicationName("ABAP AI IDE")
        if os.path.isfile(_icon_path()):
            app.setWindowIcon(QIcon(_icon_path()))
        app.setStyle("Fusion")
        app.setFont(QFont(theme.UI_FONT, 9))
        app.setStyleSheet(theme.QSS)

        def _excepthook(etype, value, tb):
            _report("".join(traceback.format_exception(etype, value, tb)))
        sys.excepthook = _excepthook

        win = MainWindow()
        win.show()
        sys.exit(app.exec())
    except SystemExit:
        raise
    except Exception:
        _report(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
