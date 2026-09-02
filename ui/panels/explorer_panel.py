"""
ExplorerPanel — right-column Git-aware Explorer.

Two tabs:
  • SAP Objects  — objects discovered in the active program
                   single-click → jump to line, double-click → fetch from workspace/SAP
  • Workspace    — local cache files with live Git status
                   double-click → open, right-click → Open / Show in Explorer / Delete

Toolbar (Workspace tab):
  [⬆ Push]  [⬇ Pull]  [⟳ Refresh]  ──────  🌿 <branch>
"""
import customtkinter as ctk
from tkinter import ttk


def _apply_style(name: str, heading_color: str = "#9cdcfe"):
    s = ttk.Style()
    try:
        s.theme_use("clam")
    except Exception:
        pass
    s.configure(name,
                background="#1e1e1e", foreground="#cccccc",
                fieldbackground="#1e1e1e", rowheight=24,
                font=("Segoe UI", 10), borderwidth=0)
    s.configure(f"{name}.Heading",
                background="#252526", foreground=heading_color,
                font=("Segoe UI", 10, "bold"), relief="flat",
                padding=(6, 4))
    s.map(name,
          background=[("selected", "#094771")],
          foreground=[("selected", "#ffffff")])


# ── Icon factory ───────────────────────────────────────────────────────────────

def _build_icons():
    """Create 16×16 PIL-based PhotoImage icons.  Returns {} if PIL unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageTk
    except ImportError:
        return {}

    S = 16

    def folder(tab_rgba, body_rgba):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 4, S - 1, S - 2], radius=2, fill=body_rgba)
        d.rounded_rectangle([0, 2, 6, 5], radius=1, fill=tab_rgba)
        d.rectangle([0, 4, 6, 5], fill=body_rgba)
        d.line([(1, 4), (S - 2, 4)], fill=(255, 255, 255, 55))
        return ImageTk.PhotoImage(img)

    def filei(fold_rgba, body_rgba, border_rgba):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fold = 4
        d.polygon([(1, 1), (S - fold - 1, 1), (S - 1, fold + 1),
                   (S - 1, S - 2), (1, S - 2)], fill=body_rgba)
        d.polygon([(S - fold - 1, 1), (S - 1, fold + 1),
                   (S - fold - 1, fold + 1)], fill=fold_rgba)
        d.line([(1, 1), (S - fold - 1, 1)], fill=border_rgba)
        d.line([(S - fold - 1, 1), (S - 1, fold + 1), (S - 1, S - 2),
                (1, S - 2), (1, 1)], fill=border_rgba)
        return ImageTk.PhotoImage(img)

    def profile_icon():
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 1, S - 1, S - 4], radius=2, fill=(70, 120, 195, 255))
        d.rectangle([2, 3, S - 3, S - 6], fill=(20, 50, 110, 255))
        d.rectangle([6, S - 4, 9, S - 2], fill=(70, 120, 195, 255))
        d.rectangle([3, S - 2, S - 4, S - 1], fill=(70, 120, 195, 255))
        return ImageTk.PhotoImage(img)

    return {
        "profile":     profile_icon(),
        "folder":      folder((195, 145,   0, 255), (240, 190,  20, 255)),
        "folder_prog": folder(( 50, 100, 190, 255), ( 75, 135, 225, 255)),
        "folder_tbl":  folder((  0, 135, 145, 255), ( 20, 170, 182, 255)),
        "folder_prop": folder((130,  55, 175, 255), (160,  85, 210, 255)),
        "file_abap":   filei((120, 170, 220, 255), (205, 230, 255, 255), ( 85, 135, 185, 255)),
        "file_json":   filei((195, 148,  50, 255), (255, 222, 150, 255), (170, 115,  25, 255)),
    }


# ── Panel ──────────────────────────────────────────────────────────────────────

class ExplorerPanel(ctk.CTkFrame):
    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0)
        self.app = app_context
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._setup_tabs()

    def _setup_tabs(self):
        self.tabs = ctk.CTkTabview(self, corner_radius=0)
        self.tabs.grid(row=0, column=0, sticky="nsew")

        sap_tab = self.tabs.add("SAP Objects")
        ws_tab  = self.tabs.add("Workspace")

        for t in (sap_tab, ws_tab):
            t.grid_rowconfigure(1, weight=1)
            t.grid_columnconfigure(0, weight=1)

        self._setup_sap_tree(sap_tab)
        self._setup_workspace_tree(ws_tab)

    # ── SAP Objects ───────────────────────────────────────────────────────────

    def _setup_sap_tree(self, parent):
        _apply_style("SAP.Treeview")

        hdr = ctk.CTkFrame(parent, height=30, fg_color="#252526")
        hdr.grid(row=0, column=0, sticky="ew")
        self._sap_hdr_var = ctk.StringVar(value="  Discovered Objects")
        ctk.CTkLabel(hdr, textvariable=self._sap_hdr_var,
                     font=ctk.CTkFont(family="Segoe UI", size=11),
                     text_color="#aaaaaa").pack(side="left", padx=6, pady=5)

        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(frame, columns=("type", "line", "name"), displaycolumns=("type",),
                            show="tree headings", style="SAP.Treeview")
        tree.heading("#0",   text="Name (click: go to line · double-click: open)", anchor="w")
        tree.heading("type", text="Type", anchor="center")
        tree.column("#0",   minwidth=140, stretch=True)
        tree.column("type", width=60, anchor="center", stretch=False)

        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=tree.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        tree.bind("<<TreeviewSelect>>", self.app.on_tree_select)
        tree.bind("<Double-1>",         self.app.on_tree_open)

        self.app.tree = tree
        self.app.tree_roots = {
            "DICT":     tree.insert("", "end", text="📦  Dictionary",  open=True),
            "CLASS":    tree.insert("", "end", text="💠  Classes",     open=True),
            "INCLUDES": tree.insert("", "end", text="📎  Includes",    open=True),
            "FORMS":    tree.insert("", "end", text="🧩  Forms / Modules", open=False),
            "FIELDS":   tree.insert("", "end", text="🔗  Local Refs",  open=False),
        }

    def set_sap_header(self, text: str):
        self._sap_hdr_var.set(f"  {text}")

    # ── Workspace ─────────────────────────────────────────────────────────────

    def _setup_workspace_tree(self, parent):
        _apply_style("WS.Treeview")

        self._ws_icons = _build_icons()          # keep a reference → prevents GC
        self.app.ws_icons = self._ws_icons

        toolbar = ctk.CTkFrame(parent, fg_color="#252526", corner_radius=0)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.grid_columnconfigure((0, 1), weight=1, uniform="tb")

        _btn = dict(height=26, border_width=1, border_color="#444",
                    font=ctk.CTkFont(family="Segoe UI", size=11))
        ctk.CTkButton(toolbar, text="⬆  Push", fg_color="#1a3a1a", hover_color="#2a5a2a",
                      command=self.app.github_push, **_btn).grid(row=0, column=0, sticky="ew",
                                                                 padx=(6, 3), pady=(6, 3))
        ctk.CTkButton(toolbar, text="⬇  Pull", fg_color="#1a1a3a", hover_color="#2a2a5a",
                      command=self.app.github_pull, **_btn).grid(row=0, column=1, sticky="ew",
                                                                 padx=(3, 3), pady=(6, 3))
        ctk.CTkButton(toolbar, text="⟳", fg_color="#2a2a2a", hover_color="#3c3c3c", width=34,
                      font=ctk.CTkFont(family="Segoe UI", size=13),
                      border_width=1, border_color="#444", height=26,
                      command=self.app.refresh_workspace_tree).grid(row=0, column=2, padx=(3, 6),
                                                                    pady=(6, 3))

        self._branch_var = ctk.StringVar(value="")
        ctk.CTkLabel(toolbar, textvariable=self._branch_var, anchor="w",
                     font=ctk.CTkFont(family="Segoe UI", size=11),
                     text_color="#6a9955").grid(row=1, column=0, columnspan=3, sticky="ew",
                                                padx=8, pady=(0, 4))

        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # columns: "kind" visible | _p _fo _fn _proj _k are hidden metadata
        tree = ttk.Treeview(frame, columns=("kind", "_p", "_fo", "_fn", "_proj", "_k"),
                            displaycolumns=("kind",), show="tree headings", style="WS.Treeview")
        tree.heading("#0",   text="Name", anchor="w")
        tree.heading("kind", text="Kind", anchor="center")
        tree.column("#0",   minwidth=180, stretch=True)
        tree.column("kind", width=80, anchor="center", stretch=False)

        tree.tag_configure("ws_modified", foreground="#e5c07b")
        tree.tag_configure("ws_new",      foreground="#98c379")
        tree.tag_configure("ws_deleted",  foreground="#e06c75")

        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=tree.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        tree.bind("<Double-1>", self.app.on_workspace_select)
        tree.bind("<Button-3>", self.app.on_ws_right_click)

        self.app.ws_tree = tree

    # ── Public API ────────────────────────────────────────────────────────────

    def set_branch_label(self, branch: str):
        self._branch_var.set(f"🌿  {branch}" if branch else "🌿  (no git repo yet — Push to create)")
