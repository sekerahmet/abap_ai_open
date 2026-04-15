import customtkinter as ctk

class SidebarPanel(ctk.CTkFrame):
    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0)
        self.app = app_context
        self.grid_rowconfigure(0, weight=0)  # connection settings
        self.grid_columnconfigure(0, weight=1)

        self._setup_settings()

    def _setup_settings(self):
        self.settings_pane = ctk.CTkScrollableFrame(self, label_text="Connection Settings", height=380)
        self.settings_pane.grid(row=0, column=0, sticky="ew", padx=5, pady=5)

        # Profile Select
        sys_row = ctk.CTkFrame(self.settings_pane, fg_color="transparent")
        sys_row.pack(fill="x", padx=10, pady=5)

        sys_names = list(self.app.systems_data.keys())
        self.system_var = ctk.StringVar(value=sys_names[0] if sys_names else "New Profile")
        self.system_dropdown = ctk.CTkOptionMenu(
            sys_row, values=sys_names if sys_names else ["New Profile"],
            variable=self.system_var, command=self.app.on_system_select)
        self.system_dropdown.pack(side="left", fill="x", expand=True, padx=(0, 5))

        ctk.CTkButton(sys_row, text="New", width=60, fg_color="#2b5a2b",
                      command=self.app.new_system_profile).pack(side="left", padx=(0, 5))
        ctk.CTkButton(sys_row, text="Del", width=60, fg_color="#6e2b28",
                      command=self.app.delete_current_system).pack(side="left")

        for attr, label, ph in [
            ("ashost", "App Server", "10.x.x.x"),
            ("sysnr",  "Sys Nr",    "00"),
            ("client", "Client",    "100"),
            ("user",   "User",      "SAPUSER"),
        ]:
            self._create_field(label, ph, attr)
        self._create_field("Password", "****", "passwd", show="*")
        self._create_field("SAP Router", "/H/...", "router")

        ctk.CTkButton(self.settings_pane, text="Save Profile",
                      font=ctk.CTkFont(weight="bold"),
                      command=self.app.save_current_system).pack(fill="x", padx=10, pady=(10, 8))

    def _create_field(self, label, ph, attr, show=None):
        f = ctk.CTkFrame(self.settings_pane, fg_color="transparent")
        f.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=11),
                     text_color="#aaaaaa").pack(anchor="w")
        entry = ctk.CTkEntry(f, placeholder_text=ph, show=show, height=30)
        entry.pack(fill="x")
        setattr(self.app, "sap_" + attr, entry)


