"""
Entry point.  With console=False in main.spec a startup crash would otherwise be
invisible, so any uncaught exception is written to %APPDATA%\\ABAP_AI\\crash.log
and shown in a message box.
"""
import os
import sys
import traceback
import datetime


def _crash_log_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ABAP_AI")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "crash.log")


def main():
    try:
        from ui.main_app import App
        App().mainloop()
    except Exception:
        tb = traceback.format_exc()
        path = _crash_log_path()
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{tb}")
        except OSError:
            pass
        try:
            import tkinter.messagebox as mbox
            mbox.showerror("ABAP AI IDE — crashed",
                           f"An unexpected error occurred.\nDetails were written to:\n{path}\n\n{tb[-1500:]}")
        except Exception:
            print(tb, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
