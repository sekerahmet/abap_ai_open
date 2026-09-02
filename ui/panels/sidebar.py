import customtkinter as ctk

CONN_FIELDS = [
    ("ashost", "App Server", "10.x.x.x or host.domain"),
    ("sysnr",  "System Nr", "00"),
    ("client", "Client",    "100"),
    ("user",   "User",      "SAPUSER"),
    ("passwd", "Password",  "••••••••"),
    ("router", "SAP Router (optional)", "/H/host/S/3299"),
]


class SidebarPanel(ctk.CTkFrame):
    """Connection profiles: dropdown, New/Delete, entry fields, Save."""

    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0, fg_color="#1b1b1c")
        self.app = app_context
        self.grid_columnconfigure(0, weight=1)
        self._build()

    def _build(self):
        pad = dict(padx=12)

        ctk.CTkLabel(self, text="CONNECTION", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#8a8a8a", anchor="w").grid(row=0, column=0, sticky="ew",
                                                            pady=(14, 6), **pad)

        sys_names = list(self.app.systems_data.keys())
        self.system_var = ctk.StringVar(value=sys_names[0] if sys_names else "New Profile")
        self.system_dropdown = ctk.CTkOptionMenu(
            self, values=sys_names if sys_names else ["New Profile"],
            variable=self.system_var, command=self.app.on_system_select, height=34,
            dynamic_resizing=False)
        self.system_dropdown.grid(row=1, column=0, sticky="ew", **pad)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", pady=(6, 10), **pad)
        btn_row.grid_columnconfigure((0, 1), weight=1, uniform="b")
        ctk.CTkButton(btn_row, text="New", height=30, fg_color="#2b5a2b", hover_color="#3a7a3a",
                      command=self.app.new_system_profile).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(btn_row, text="Delete", height=30, fg_color="#6e2b28", hover_color="#8e3b38",
                      command=self.app.delete_current_system).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        row = 3
        for attr, label, ph in CONN_FIELDS:
            ctk.CTkLabel(self, text=label, font=ctk.CTkFont(size=11), text_color="#aaaaaa",
                         anchor="w").grid(row=row, column=0, sticky="ew", pady=(6, 1), **pad)
            entry = ctk.CTkEntry(self, placeholder_text=ph, height=32,
                                 show="*" if attr == "passwd" else None)
            entry.grid(row=row + 1, column=0, sticky="ew", **pad)
            setattr(self.app, "sap_" + attr, entry)
            row += 2

        ctk.CTkButton(self, text="Save Profile", height=36, font=ctk.CTkFont(weight="bold"),
                      command=self.app.save_current_system).grid(row=row, column=0, sticky="ew",
                                                                 pady=(16, 6), **pad)
        ctk.CTkLabel(self, text="Read-only connection — nothing is written to SAP.",
                     font=ctk.CTkFont(size=10), text_color="#6a6a6a", anchor="w",
                     wraplength=240, justify="left").grid(row=row + 1, column=0, sticky="ew", **pad)

    def set_profiles(self, names: list, select: str = None):
        values = names if names else ["New Profile"]
        self.system_dropdown.configure(values=values)
        self.system_var.set(select if select else values[0])
