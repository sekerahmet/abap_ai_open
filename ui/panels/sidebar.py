import customtkinter as ctk
from tkinter import ttk

class SidebarPanel(ctk.CTkFrame):
    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0)
        self.app = app_context
        self.grid_rowconfigure(0, weight=0)  # connection settings
        self.grid_rowconfigure(1, weight=0)  # github buttons
        self.grid_rowconfigure(2, weight=1)  # explorer tabs
        self.grid_columnconfigure(0, weight=1)

        self._setup_settings()
        self._setup_github_bar()
        self._setup_explorer()

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

    def _setup_github_bar(self):
        gh_bar = ctk.CTkFrame(self, fg_color="transparent")
        gh_bar.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 4))
        gh_bar.grid_columnconfigure(0, weight=1)
        gh_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(gh_bar, text="Push to GitHub",
                      fg_color="#1a3a1a", hover_color="#2a5a2a",
                      command=self.app.github_push).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ctk.CTkButton(gh_bar, text="Pull from GitHub",
                      fg_color="#1a1a3a", hover_color="#2a2a5a",
                      command=self.app.github_pull).grid(row=0, column=1, sticky="ew", padx=(3, 0))

    # ── Explorer tabs ──────────────────────────────────────────────────────────

    def _setup_explorer(self):
        self.explorer_tabs = ctk.CTkTabview(self, corner_radius=0)
        self.explorer_tabs.grid(row=2, column=0, sticky="nsew", padx=5, pady=(0, 5))

        sap_tab = self.explorer_tabs.add("SAP Objects")
        ws_tab  = self.explorer_tabs.add("Workspace")

        sap_tab.grid_rowconfigure(0, weight=1)
        sap_tab.grid_columnconfigure(0, weight=1)
        ws_tab.grid_rowconfigure(1, weight=1)
        ws_tab.grid_columnconfigure(0, weight=1)

        self._setup_sap_tree(sap_tab)
        self._setup_workspace_tree(ws_tab)

    # ── SAP Object Explorer ────────────────────────────────────────────────────

    def _setup_sap_tree(self, parent):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("SAP.Treeview",
                        background="#1a1a1b", foreground="#d4d4d4",
                        fieldbackground="#1a1a1b", rowheight=22,
                        font=("Consolas", 11))
        style.configure("SAP.Treeview.Heading",
                        background="#2a2a2a", foreground="#569cd6",
                        font=("Consolas", 11, "bold"), relief="flat")
        style.map("SAP.Treeview", background=[("selected", "#264f78")])

        self.tree = ttk.Treeview(parent, columns=("Type",),
                                 show="tree headings", style="SAP.Treeview")
        self.tree.heading("#0",   text="SAP Object Explorer")
        self.tree.heading("Type", text="Type")
        self.tree.column("Type", width=60, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.tree.bind("<<TreeviewSelect>>", self.app.on_tree_select)

        self.tree_roots = {
            "DICT":     self.tree.insert("", "end", text="▦  Dictionary", open=True),
            "CLASS":    self.tree.insert("", "end", text="💎  Classes",   open=True),
            "INCLUDES": self.tree.insert("", "end", text="📎  Includes",  open=True),
            "FIELDS":   self.tree.insert("", "end", text="📍  Local Refs", open=True),
        }
        self.app.tree       = self.tree
        self.app.tree_roots = self.tree_roots

    # ── Workspace Explorer ─────────────────────────────────────────────────────

    def _setup_workspace_tree(self, parent):
        # Refresh button
        ctk.CTkButton(parent, text="Refresh", height=28,
                      command=self.app.refresh_workspace_tree).grid(
                          row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        style = ttk.Style()
        style.configure("WS.Treeview",
                        background="#1a1a1b", foreground="#d4d4d4",
                        fieldbackground="#1a1a1b", rowheight=22,
                        font=("Consolas", 11))
        style.configure("WS.Treeview.Heading",
                        background="#2a2a2a", foreground="#98c379",
                        font=("Consolas", 11, "bold"), relief="flat")
        style.map("WS.Treeview", background=[("selected", "#264f78")])

        self.ws_tree = ttk.Treeview(parent, show="tree", style="WS.Treeview")
        self.ws_tree.heading("#0", text="Workspace")
        self.ws_tree.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        self.ws_tree.bind("<Double-1>", self.app.on_workspace_select)

        self.app.ws_tree = self.ws_tree
