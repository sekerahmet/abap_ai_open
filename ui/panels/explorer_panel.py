"""
ExplorerPanel — right-column Git-aware Explorer.

Two tabs:
  • SAP Objects  — discovered objects in the active program (DICT / CLASS / INCLUDES)
  • Workspace    — local cache files with live Git status (modified / new / clean)

Toolbar (Workspace tab):
  [⬆ Push]  [⬇ Pull]  [⟳ Refresh]  ──────  🌿 <branch>
"""
import customtkinter as ctk
from tkinter import ttk
from utils import github_sync


# ── Status helpers ─────────────────────────────────────────────────────────────

_STATUS_ICON = {"M": "● ", "+": "+ ", "D": "✗ "}   # prefix added to text
_STATUS_TAG  = {"M": "ws_modified", "?": "ws_new", "D": "ws_deleted"}

def _worst(a: str, b: str) -> str:
    """Return the higher-priority status (M > ? > D > None)."""
    pri = {"M": 3, "?": 2, "D": 1}
    return a if pri.get(a, 0) >= pri.get(b, 0) else b


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
        # Body
        d.rounded_rectangle([0, 4, S - 1, S - 2], radius=2, fill=body_rgba)
        # Tab (top-left bump)
        d.rounded_rectangle([0, 2, 6, 5], radius=1, fill=tab_rgba)
        # Merge tab bottom into body top seamlessly
        d.rectangle([0, 4, 6, 5], fill=body_rgba)
        # Subtle top highlight
        d.line([(1, 4), (S - 2, 4)], fill=(255, 255, 255, 55))
        return ImageTk.PhotoImage(img)

    def filei(fold_rgba, body_rgba, border_rgba):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fold = 4
        # Main body (clipped top-right corner)
        d.polygon([(1, 1), (S - fold - 1, 1), (S - 1, fold + 1),
                   (S - 1, S - 2), (1, S - 2)], fill=body_rgba)
        # Folded corner triangle
        d.polygon([(S - fold - 1, 1), (S - 1, fold + 1),
                   (S - fold - 1, fold + 1)], fill=fold_rgba)
        # Border outline
        d.line([(1, 1), (S - fold - 1, 1)], fill=border_rgba)
        d.line([(S - fold - 1, 1), (S - 1, fold + 1), (S - 1, S - 2),
                (1, S - 2), (1, 1)], fill=border_rgba)
        return ImageTk.PhotoImage(img)

    def profile_icon():
        """Simple monitor/screen icon."""
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # Screen body
        d.rounded_rectangle([0, 1, S - 1, S - 4], radius=2,
                             fill=(70, 120, 195, 255))
        # Screen inner (dark area)
        d.rectangle([2, 3, S - 3, S - 6], fill=(20, 50, 110, 255))
        # Stand
        d.rectangle([6, S - 4, 9, S - 2], fill=(70, 120, 195, 255))
        # Base
        d.rectangle([3, S - 2, S - 4, S - 1], fill=(70, 120, 195, 255))
        return ImageTk.PhotoImage(img)

    return {
        "profile":      profile_icon(),
        # Project folder — classic Windows yellow
        "folder":       folder((195, 145,   0, 255), (240, 190,  20, 255)),
        # Programs subfolder — blue
        "folder_prog":  folder(( 50, 100, 190, 255), ( 75, 135, 225, 255)),
        # Tables subfolder — teal
        "folder_tbl":   folder((  0, 135, 145, 255), ( 20, 170, 182, 255)),
        # Proposals subfolder — purple
        "folder_prop":  folder((130,  55, 175, 255), (160,  85, 210, 255)),
        # ABAP source file — light blue
        "file_abap":    filei((120, 170, 220, 255), (205, 230, 255, 255),
                              ( 85, 135, 185, 255)),
        # JSON / table file — amber
        "file_json":    filei((195, 148,  50, 255), (255, 222, 150, 255),
                              (170, 115,  25, 255)),
    }


# ── Panel ──────────────────────────────────────────────────────────────────────

class ExplorerPanel(ctk.CTkFrame):
    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0)
        self.app = app_context
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._setup_tabs()

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _setup_tabs(self):
        self.tabs = ctk.CTkTabview(self, corner_radius=0)
        self.tabs.grid(row=0, column=0, sticky="nsew")

        sap_tab = self.tabs.add("SAP Objects")
        ws_tab  = self.tabs.add("Workspace")

        sap_tab.grid_rowconfigure(1, weight=1)
        sap_tab.grid_columnconfigure(0, weight=1)
        ws_tab.grid_rowconfigure(1, weight=1)
        ws_tab.grid_columnconfigure(0, weight=1)

        self._setup_sap_tree(sap_tab)
        self._setup_workspace_tree(ws_tab)

    # ── SAP Objects ───────────────────────────────────────────────────────────

    def _setup_sap_tree(self, parent):
        _apply_style("SAP.Treeview", heading_color="#9cdcfe")

        hdr = ctk.CTkFrame(parent, height=30, fg_color="#252526")
        hdr.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hdr, text="  Discovered Objects",
                     font=ctk.CTkFont(family="Segoe UI", size=11),
                     text_color="#aaaaaa").pack(side="left", padx=6, pady=5)

        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(frame, columns=("type",),
                            show="tree headings", style="SAP.Treeview")
        tree.heading("#0",   text="Name",  anchor="w")
        tree.heading("type", text="Type",  anchor="center")
        tree.column("#0",   minwidth=140, stretch=True)
        tree.column("type", width=60, anchor="center", stretch=False)

        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=tree.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        tree.bind("<<TreeviewSelect>>", self.app.on_tree_select)

        self.app.tree = tree
        self.app.tree_roots = {
            "DICT":     tree.insert("", "end", text="📦  Dictionary",  open=True),
            "CLASS":    tree.insert("", "end", text="💠  Classes",     open=True),
            "INCLUDES": tree.insert("", "end", text="📎  Includes",    open=True),
            "FIELDS":   tree.insert("", "end", text="🔗  Local Refs",  open=True),
        }

    # ── Workspace ─────────────────────────────────────────────────────────────

    def _setup_workspace_tree(self, parent):
        _apply_style("WS.Treeview", heading_color="#9cdcfe")

        # Build icons — store on self to prevent GC, also expose on app
        self._ws_icons = _build_icons()
        self.app.ws_icons = self._ws_icons

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = ctk.CTkFrame(parent, height=34, fg_color="#252526")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.grid_columnconfigure(3, weight=1)   # spacer column

        _btn = dict(height=24, width=80, border_width=1, border_color="#444",
                    font=ctk.CTkFont(family="Segoe UI", size=11))

        ctk.CTkButton(toolbar, text="⬆  Push",
                      fg_color="#1a3a1a", hover_color="#2a5a2a",
                      command=self.app.github_push,
                      **_btn).grid(row=0, column=0, padx=(6, 3), pady=5)

        ctk.CTkButton(toolbar, text="⬇  Pull",
                      fg_color="#1a1a3a", hover_color="#2a2a5a",
                      command=self.app.github_pull,
                      **_btn).grid(row=0, column=1, padx=3, pady=5)

        ctk.CTkButton(toolbar, text="⟳",
                      fg_color="#2a2a2a", hover_color="#3c3c3c", width=34,
                      font=ctk.CTkFont(family="Segoe UI", size=13),
                      border_width=1, border_color="#444", height=24,
                      command=self.app.refresh_workspace_tree
                      ).grid(row=0, column=2, padx=3, pady=5)

        # Branch label — right-aligned
        self._branch_var = ctk.StringVar(value="")
        self._branch_lbl = ctk.CTkLabel(toolbar, textvariable=self._branch_var,
                                        font=ctk.CTkFont(family="Segoe UI", size=11),
                                        text_color="#6a9955")
        self._branch_lbl.grid(row=0, column=4, padx=(0, 10), sticky="e")

        # ── Tree ──────────────────────────────────────────────────────────────
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # columns: "kind" visible | _p _fo _fn _proj are hidden metadata
        tree = ttk.Treeview(
            frame,
            columns=("kind", "_p", "_fo", "_fn", "_proj"),
            displaycolumns=("kind",),
            show="tree headings",
            style="WS.Treeview",
        )
        tree.heading("#0",   text="Name", anchor="w")
        tree.heading("kind", text="Kind", anchor="center")
        tree.column("#0",   minwidth=180, stretch=True)
        tree.column("kind", width=80, anchor="center", stretch=False)

        # Git status color tags
        tree.tag_configure("ws_modified", foreground="#e5c07b")   # amber — modified
        tree.tag_configure("ws_new",      foreground="#98c379")   # green — untracked
        tree.tag_configure("ws_deleted",  foreground="#e06c75")   # red   — deleted

        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=tree.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        tree.bind("<Double-1>",  self.app.on_workspace_select)
        tree.bind("<Button-3>",  self.app.on_ws_right_click)

        self.app.ws_tree = tree

    # ── Public API ────────────────────────────────────────────────────────────

    def update_branch_label(self):
        """Read branch name from git and update the toolbar label."""
        branch = github_sync.get_branch_name()
        self._branch_var.set(f"🌿  {branch}" if branch else "")
