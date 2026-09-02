"""
TopBar — one row across the window:

  [● profile ▾] [⚙]   [Program ▾] [Object name…] [Fetch] [✦ Claude]

The connection form lives in ConnectionDialog (opened with ⚙), so the main
window no longer spends a column on it.
"""

import customtkinter as ctk
import tkinter.messagebox as mbox

from ui import theme as T

CONN_FIELDS = [
    ("ashost", "App Server", "10.x.x.x or host.domain"),
    ("sysnr",  "System Nr", "00"),
    ("client", "Client",    "100"),
    ("user",   "User",      "SAPUSER"),
    ("passwd", "Password",  "••••••••"),
    ("router", "SAP Router (optional)", "/H/host/S/3299"),
]
OBJECT_TYPES = ["Program", "Table", "Structure", "Function Module", "Global Class", "Table Data"]


class TopBar(ctk.CTkFrame):
    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0, fg_color=T.PANEL, height=52)
        self.app = app_context
        self.grid_columnconfigure(4, weight=1)
        self._build()

    def _build(self):
        names = list(self.app.systems_data.keys())
        self.system_var = ctk.StringVar(value=names[0] if names else "New Profile")
        self.system_dropdown = ctk.CTkOptionMenu(
            self, values=names if names else ["New Profile"], variable=self.system_var,
            command=self.app.on_system_select, width=210, height=34, dynamic_resizing=False,
            fg_color=T.PANEL_ALT, button_color=T.PANEL_ALT, button_hover_color=T.BORDER,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        self.system_dropdown.grid(row=0, column=0, padx=(10, 4), pady=9)

        ctk.CTkButton(self, text="⚙", width=36, height=34, fg_color=T.PANEL_ALT,
                      hover_color=T.BORDER, font=ctk.CTkFont(size=15),
                      command=self.open_dialog).grid(row=0, column=1, padx=(0, 4))

        self.host_var = ctk.StringVar(value="")
        ctk.CTkLabel(self, textvariable=self.host_var, text_color=T.MUTED,
                     font=ctk.CTkFont(family="Segoe UI", size=11)).grid(row=0, column=2, padx=(4, 18))

        self.type_menu = ctk.CTkOptionMenu(self, values=OBJECT_TYPES, width=150, height=34,
                                           fg_color=T.ACCENT, button_color=T.ACCENT,
                                           button_hover_color=T.ACCENT_HOVER)
        self.type_menu.grid(row=0, column=3, padx=(0, 6))

        self.name_entry = ctk.CTkEntry(self, placeholder_text="Object name…  (Enter = Fetch)", height=34)
        self.name_entry.grid(row=0, column=4, sticky="ew", padx=(0, 6))
        self.name_entry.bind("<Return>", lambda _e: self.app.fetch_program_flow())

        self.fetch_btn = ctk.CTkButton(self, text="Fetch", width=90, height=34,
                                       fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                       command=self.app.fetch_program_flow)
        self.fetch_btn.grid(row=0, column=5, padx=(0, 6))
        self.app.fetch_btn = self.fetch_btn

        ctk.CTkButton(self, text="✦ Claude", width=100, height=34, fg_color=T.CLAUDE,
                      hover_color=T.CLAUDE_HOVER, command=self.app.open_claude_tab
                      ).grid(row=0, column=6, padx=(0, 10))

    # ── API used by App ───────────────────────────────────────────────────────

    def set_profiles(self, names: list, select: str = None):
        values = names if names else ["New Profile"]
        self.system_dropdown.configure(values=values)
        self.system_var.set(select if select else values[0])

    def set_host(self, text: str):
        self.host_var.set(text)

    def open_dialog(self):
        ConnectionDialog(self.app)


class ConnectionDialog(ctk.CTkToplevel):
    """Edit / create / delete connection profiles."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Connection profiles")
        self.resizable(False, False)
        self.configure(fg_color=T.PANEL)
        self._entries = {}
        self._build()
        self._load(app.active_profile())
        self.after(120, self._modal)

    def _modal(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def _build(self):
        pad = dict(padx=16)
        ctk.CTkLabel(self, text="Profile", font=ctk.CTkFont(size=11), text_color=T.MUTED,
                     anchor="w").grid(row=0, column=0, sticky="ew", pady=(14, 1), **pad)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", **pad)
        row.grid_columnconfigure(0, weight=1)
        names = list(self.app.systems_data.keys())
        self.pick_var = ctk.StringVar(value="")
        self.pick = ctk.CTkOptionMenu(row, values=names or ["(none)"], variable=self.pick_var,
                                      command=self._load, width=220, dynamic_resizing=False)
        self.pick.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(row, text="New", width=64, fg_color=T.OK, hover_color=T.OK_HOVER,
                      command=self._new).grid(row=0, column=1, padx=(6, 0))

        ctk.CTkLabel(self, text="Profile name", font=ctk.CTkFont(size=11), text_color=T.MUTED,
                     anchor="w").grid(row=2, column=0, sticky="ew", pady=(10, 1), **pad)
        self.name_entry = ctk.CTkEntry(self, width=320, height=32)
        self.name_entry.grid(row=3, column=0, sticky="ew", **pad)

        r = 4
        for attr, label, ph in CONN_FIELDS:
            ctk.CTkLabel(self, text=label, font=ctk.CTkFont(size=11), text_color=T.MUTED,
                         anchor="w").grid(row=r, column=0, sticky="ew", pady=(8, 1), **pad)
            e = ctk.CTkEntry(self, placeholder_text=ph, height=32,
                             show="*" if attr == "passwd" else None)
            e.grid(row=r + 1, column=0, sticky="ew", **pad)
            self._entries[attr] = e
            r += 2

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=r, column=0, sticky="ew", pady=(16, 6), **pad)
        btns.grid_columnconfigure((0, 1, 2), weight=1, uniform="b")
        ctk.CTkButton(btns, text="Delete", fg_color=T.DANGER, hover_color=T.DANGER_HOVER,
                      command=self._delete).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(btns, text="Close", fg_color=T.PANEL_ALT, hover_color=T.BORDER,
                      command=self.destroy).grid(row=0, column=1, sticky="ew", padx=4)
        ctk.CTkButton(btns, text="Save & use", fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      font=ctk.CTkFont(weight="bold"),
                      command=self._save).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        ctk.CTkLabel(self, text="Read-only connection — nothing is written to SAP.",
                     font=ctk.CTkFont(size=10), text_color=T.DIM).grid(row=r + 1, column=0,
                                                                        pady=(0, 12), **pad)

    def _fill(self, name: str, data: dict):
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, name)
        for attr, e in self._entries.items():
            val = data.get(attr)
            if val is None and attr == "router":
                val = data.get("saprouter", "")
            e.delete(0, "end")
            e.insert(0, str(val or ""))

    def _load(self, name):
        if name in self.app.systems_data:
            self.pick_var.set(name)
            self._fill(name, self.app.systems_data[name])
        else:
            self._new()

    def _new(self):
        self.pick_var.set("(new)")
        self._fill("", {})
        self.name_entry.focus_set()

    def _save(self):
        name = self.name_entry.get().strip()
        if not name or any(c in name for c in '\\/:*?"<>|'):
            mbox.showwarning("Profile name", "Enter a profile name without \\ / : * ? \" < > |", parent=self)
            return
        data = {attr: e.get().strip() for attr, e in self._entries.items()}
        if not data.get("ashost"):
            mbox.showwarning("App Server", "App Server is required.", parent=self)
            return
        self.app.save_profile(name, data)
        self.destroy()

    def _delete(self):
        name = self.pick_var.get()
        if name not in self.app.systems_data:
            return
        if not mbox.askyesno("Delete profile", f"Delete profile '{name}'?", parent=self):
            return
        self.app.delete_profile(name)
        names = list(self.app.systems_data.keys())
        self.pick.configure(values=names or ["(none)"])
        self._load(names[0] if names else "")
