"""
App — glue class of the ABAP AI IDE.

Owns: connection profiles, background threads, tab routing, SAP-object tree,
workspace explorer, proposal watcher, GitHub sync, system log.

Threading rule: all RFC / git / disk-heavy work runs in daemon threads and
reports back with self.after(0, …).  Nothing here ever writes to SAP.
"""

import customtkinter as ctk
import threading
import json
import os
import re
import difflib
import subprocess
import tkinter as tk
import tkinter.messagebox as mbox
from tkinter.simpledialog import askstring
from tkinter import ttk

from utils.env_loader import load_robust_env
load_robust_env()

from core.controller import AnalysisController
from utils.highlighter import ABAPHighlighter
from utils.parser import ABAPParser
from utils import workspace
from utils import github_sync
from ui import theme as T
from ui.panels.topbar import TopBar, CONN_FIELDS
from ui.panels.editor import EditorPanel
from ui.panels.explorer_panel import ExplorerPanel
from ui.panels.claude_panel import ClaudePanel
from ui.widgets.codeview import CodeView
from core.claude_runner import ClaudeSession, find_claude

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# User data lives in AppData — survives rebuilds, --clean, uninstall/reinstall.
_APP_DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ABAP_AI")
os.makedirs(_APP_DATA_DIR, exist_ok=True)
SYSTEMS_FILE  = os.path.join(_APP_DATA_DIR, "systems.json")
UI_STATE_FILE = os.path.join(_APP_DATA_DIR, "ui_state.json")
CLAUDE_SESSIONS_FILE = os.path.join(_APP_DATA_DIR, "claude_sessions.json")

_CONN_KEYS   = [k for k, _, _ in CONN_FIELDS]
_DDIC_TYPES  = ("Table", "Structure")
_CODE_TYPES  = ("Program", "Global Class", "Function Module")
_TAB_PREFIXES = ("Program", "Global Class", "Function Module", "Table", "Proposal", "Diff")
_CONTEXT_INLINE_LIMIT = 20000      # chars of open-tab code sent inline to Claude

# TADIR OBJECT → (sub-fetch category, display icon)
_TADIR_META = {
    "TABL": ("DICT",  "▦ "), "VIEW": ("DICT",  "▦ "),
    "CLAS": ("CLASS", "💎 "), "INTF": (None,   "💠 "),
    "PROG": ("PROG",  "📝 "), "FUNC": ("FUNC",  "⚙ "), "FUGR": (None, "⚙ "),
    "MSAG": (None, "💬 "), "DTEL": (None, "🔤 "), "DOMA": (None, "🔤 "), "TTYP": (None, "▤ "),
}
_CATEGORY_FTYPE = {"DICT": "Table", "CLASS": "Global Class",
                   "PROG": "Program", "FUNC": "Function Module"}


def _tab_name(ftype: str, name: str) -> str:
    """Canonical tab title — tables and structures share the 'Table:' prefix."""
    prefix = "Table" if ftype in _DDIC_TYPES else ftype
    return f"{prefix}: {name.upper()}"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ABAP AI IDE")

        self.controller = AnalysisController()
        self.systems_data = self.load_systems_file()
        self._ui_state = self._load_ui_state()
        self._apply_window_geometry()
        self.minsize(1100, 650)
        self.tabs_dict = {}
        self.active_tab_name = None
        self.current_main_program = ""

        self._watched_proposals = {}     # "profile/project/file" → mtime_ns
        self._ws_snapshot = None
        self._ws_refreshing = False
        self._ws_refresh_pending = False

        self._setup_layout()

        self.topbar = TopBar(self, self)
        self.topbar.grid(row=0, column=0, sticky="ew")
        self.editor = EditorPanel(self.paned, self)
        self.explorer_panel = ExplorerPanel(self.paned, self)
        self.paned.add(self.editor, minsize=520, stretch="always")
        self.paned.add(self.explorer_panel, minsize=320,
                       width=self._ui_state.get("explorer_w", 460), stretch="never")
        self._tree_last = ({}, {}, "Discovered Objects")
        self._ws_last = ({}, {}, "")

        logs_content = self.editor.add_tab("System Logs", is_closable=False)
        self.logs_text = ctk.CTkTextbox(logs_content, font=("Consolas", 12), wrap="word")
        self.logs_text.pack(fill="both", expand=True, padx=1, pady=1)
        self.logs_text.configure(state="disabled")
        self.editor.set_active("System Logs")

        names = list(self.systems_data.keys())
        if names:
            self.on_system_select(names[0])
        else:
            self.write_log("No connection profile yet — press ⚙ in the top bar to create one.")

        self._ws_snapshot = workspace.snapshot()
        self.refresh_workspace_tree()
        self.after(2000, self._poll_proposals)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Layout / window state ─────────────────────────────────────────────────

    def _setup_layout(self):
        self.configure(fg_color=T.BG)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        # Editor | Explorer separated by a draggable sash (top bar above, status bar below)
        self.paned = tk.PanedWindow(self, orient="horizontal", sashwidth=5, sashpad=0,
                                    sashrelief="flat", bg=T.BG, bd=0, opaqueresize=True)
        self.paned.grid(row=1, column=0, sticky="nsew")

        # Status bar
        bar = ctk.CTkFrame(self, height=26, corner_radius=0, fg_color=T.ACCENT)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        self._status_conn = ctk.StringVar(value="No profile")
        self._status_msg  = ctk.StringVar(value="Ready")
        self._status_right = ctk.StringVar(value="read-only RFC")
        f = ctk.CTkFont(family="Segoe UI", size=11)
        ctk.CTkLabel(bar, textvariable=self._status_conn, font=f, text_color="#ffffff",
                     anchor="w").grid(row=0, column=0, sticky="w", padx=(10, 20))
        ctk.CTkLabel(bar, textvariable=self._status_msg, font=f, text_color="#e8e8e8",
                     anchor="w").grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(bar, textvariable=self._status_right, font=f, text_color="#ffffff",
                     anchor="e").grid(row=0, column=2, sticky="e", padx=(20, 10))

    def _load_ui_state(self) -> dict:
        try:
            with open(UI_STATE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _apply_window_geometry(self):
        st = self._ui_state
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        geo = st.get("geometry")
        if geo:
            try:
                w, h, x, y = (int(v) for v in geo.replace("x", "+").split("+"))
                if w <= sw and h <= sh and -50 <= x < sw - 100 and -50 <= y < sh - 100:
                    self.geometry(geo)
                else:
                    geo = None
            except ValueError:
                geo = None
        if not geo:
            w, h = min(1600, int(sw * 0.92)), min(950, int(sh * 0.88))
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
        if st.get("zoomed"):
            try:
                self.state("zoomed")
            except Exception:
                pass

    def _on_close(self):
        try:
            zoomed = self.state() == "zoomed"
            st = {"zoomed": zoomed}
            if not zoomed:
                st["geometry"] = self.geometry()
            else:
                st["geometry"] = self._ui_state.get("geometry")
            st["explorer_w"] = self.explorer_panel.winfo_width()
            with open(UI_STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(st, fh)
        except Exception:
            pass
        self.destroy()

    # ══════════════════════════════════════════════════════════════════════════
    # Connection profiles
    # ══════════════════════════════════════════════════════════════════════════

    def load_systems_file(self):
        if os.path.exists(SYSTEMS_FILE):
            try:
                with open(SYSTEMS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def flush_systems_file(self):
        with open(SYSTEMS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.systems_data, f, indent=4, ensure_ascii=False)

    def active_profile(self) -> str:
        name = self.topbar.system_var.get()
        return name if name in self.systems_data else ""

    def on_system_select(self, name):
        if self.topbar.system_var.get() != name:
            self.topbar.system_var.set(name)
        self.current_main_program = ""
        self.populate_tree({}, {}, header="Discovered Objects")
        if name in self.systems_data:
            self._seed_proposals(name)
            data = self.systems_data[name]
            host = data.get("ashost", "")
            self._status_conn.set(f"●  {name}   {host}")
            self.topbar.set_host(f"{host}  ·  client {data.get('client', '')}  ·  {data.get('user', '')}")
        else:
            self._status_conn.set("○  No profile")
            self.topbar.set_host("")
        self.write_log(f"Switched to profile: {name}")
        self.refresh_workspace_tree()

    def save_profile(self, name: str, data: dict):
        data = dict(data)
        if data.get("router"):
            data["saprouter"] = data["router"]
        self.systems_data[name] = data
        self.flush_systems_file()
        self.topbar.set_profiles(list(self.systems_data.keys()), select=name)
        self.on_system_select(name)
        self.write_log(f"Profile '{name}' saved.")

    def delete_profile(self, name: str):
        if name not in self.systems_data:
            return
        del self.systems_data[name]
        self.flush_systems_file()
        names = list(self.systems_data.keys())
        self.topbar.set_profiles(names)
        self.on_system_select(names[0] if names else "New Profile")

    def get_current_conn(self) -> dict:
        data = self.systems_data.get(self.active_profile(), {})
        conn = {}
        for k in _CONN_KEYS:
            val = str(data.get(k) or "").strip()
            if val:
                conn["saprouter" if k == "router" else k] = val   # pyrfc expects 'saprouter'
        return conn

    # ══════════════════════════════════════════════════════════════════════════
    # Fetch flow
    # ══════════════════════════════════════════════════════════════════════════

    def fetch_program_flow(self):
        program = self.topbar.name_entry.get().strip().upper()
        if not program:
            return
        ftype = self.topbar.type_menu.get()
        where_clause = ""
        if ftype == "Table Data":
            where_clause = askstring(
                "WHERE Clause",
                f"Enter WHERE clause for {program}\n(leave empty for all rows, max 200):",
                initialvalue="")
            if where_clause is None:
                return
            where_clause = where_clause.strip()

        self.write_log(f"Fetching {ftype} {program}...")
        self.fetch_btn.configure(state="disabled", text="Working...")
        threading.Thread(target=self.run_fetch,
                         args=(self.get_current_conn(), program, ftype, self.active_profile()),
                         kwargs={"where_clause": where_clause}, daemon=True).start()

    def run_fetch(self, conn, prog, ftype, profile, force=False, where_clause="", force_sub=False):
        prog = prog.upper()
        tab = _tab_name(ftype, prog)
        try:
            if ftype == "Table Data":
                self._log_dial(conn)
                columns, rows = self.controller.fetch_table_data(conn, prog, where_clause)
                if columns is None:
                    self.after(0, self.write_log, f"FAILED: {prog} - {rows}")
                    return
                title = f"Data: {prog}" + (f" [{where_clause[:30]}]" if where_clause else "")
                self.after(0, self.write_log, f"SUCCESS: {prog} — {len(rows)} rows.")
                self.after(0, self.open_data_tab, title, columns, rows)
                return

            if ftype == "Program":
                self.current_main_program = prog

            # ── Workspace-first ───────────────────────────────────────────────
            if not force and profile:
                if ftype in _DDIC_TYPES:
                    fields = workspace.read_table_fields(profile, prog)
                    if fields:
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {prog}")
                        self.after(0, self.open_ddic_tab, tab,
                                   {"NAME": prog, "FIELDS": fields}, ftype)
                        return
                else:
                    code = workspace.read_code(profile, ftype, prog)
                    if code:
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {prog}")
                        self.after(0, self.open_code_tab, tab, code, prog, ftype, profile)
                        if ftype == "Program":
                            objs = ABAPParser.get_objects(code)
                            self.after(0, self._populate_tree_offline, profile, prog, objs)
                        return

            # ── Live RFC ──────────────────────────────────────────────────────
            self._log_dial(conn)
            if ftype in _DDIC_TYPES:
                code, attrs = self.controller.fetch_ddic_object(conn, prog)
            elif ftype == "Global Class":
                code, attrs = self.controller.fetch_class_source(conn, prog)
            elif ftype == "Function Module":
                code, attrs = self.controller.fetch_function_module(conn, prog)
            else:
                code, attrs = self.controller.fetch_program(conn, prog)

            if not code:
                err = attrs if isinstance(attrs, str) else "Object not found."
                self.after(0, self.write_log, f"FAILED: {prog} - {err}")
                return

            self.after(0, self.write_log, f"SUCCESS: {prog} loaded from SAP.")
            if ftype in _DDIC_TYPES:
                self.after(0, self.open_ddic_tab, tab, attrs, ftype)
                saved = workspace.save_table(profile, prog, attrs.get("FIELDS", [])) if profile else ""
            else:
                self.after(0, self.open_code_tab, tab, code, prog, ftype, profile)
                saved = workspace.save_code(profile, ftype, prog, code) if profile else ""
            if saved:
                self.after(0, self.write_log, f"[WS] Saved: {saved}")

            if ftype == "Program":
                objs = ABAPParser.get_objects(code)
                threading.Thread(target=self.run_proactive_check,
                                 args=(conn, prog, objs, profile),
                                 kwargs={"force": force_sub}, daemon=True).start()
        except Exception as e:
            self.after(0, self.write_log, f"CONNECTION ERROR: {e}")
        finally:
            self.after(0, self.reset_buttons)

    def _log_dial(self, conn):
        printable = {k: ("********" if k == "passwd" else v) for k, v in conn.items()}
        self.after(0, self.write_log, f"[RFC] Connecting: {printable}")

    # ── Deep discovery ────────────────────────────────────────────────────────

    def run_proactive_check(self, conn, main_prog, main_objs, profile="", force=False):
        """Parse main program + includes, verify names in TADIR, cache Z/Y tables."""
        def _merge(combined, seen, new_objs):
            for cat, items in new_objs.items():
                combined.setdefault(cat, [])
                seen.setdefault(cat, set())
                for obj in items:
                    if obj["name"] not in seen[cat]:
                        combined[cat].append(obj)
                        seen[cat].add(obj["name"])

        combined, seen = {}, {}
        _merge(combined, seen, main_objs)

        try:
            # Includes: workspace first (unless forced), then one RFC session for the rest
            include_names = [o["name"] for o in main_objs.get("INCLUDES", [])]
            to_fetch = []
            for inc in include_names:
                cached = workspace.read_code(profile, "Program", inc) if (profile and not force) else ""
                if cached:
                    _merge(combined, seen, ABAPParser.get_objects(cached))
                else:
                    to_fetch.append(inc)
            if to_fetch:
                self.after(0, self.write_log, f"[DEEP] Reading {len(to_fetch)} include(s) from SAP...")
                for inc, (code, err) in self.controller.fetch_programs(conn, to_fetch).items():
                    if not code:
                        self.after(0, self.write_log, f"[DEEP] Skip {inc}: {err}")
                        continue
                    _merge(combined, seen, ABAPParser.get_objects(code))
                    if profile:
                        saved = workspace.save_code(profile, "Program", inc, code, project=main_prog)
                        if saved:
                            self.after(0, self.write_log, f"[WS] Saved include: {inc}")

            names = [o["name"] for cat in ("DICT", "CLASS", "INCLUDES")
                     for o in combined.get(cat, [])]
            self.after(0, self.write_log, f"[DISCOVERY] Checking {len(names)} names in TADIR...")
            registry = self.controller.check_objects_batch(conn, names)
            self.after(0, self.write_log, f"[DISCOVERY] {len(registry)} SAP objects verified.")
            self.after(0, self.populate_tree, combined, registry, f"{main_prog}  (SAP)")

            if not profile:
                return
            tables = [n for n, t in registry.items()
                      if t in ("TABL", "VIEW") and n.startswith(("Z", "Y"))
                      and (force or not workspace.find_project(profile, workspace.TABLE_FOLDER, f"{n}.json"))]
            if tables:
                self.after(0, self.write_log, f"[WS] Caching {len(tables)} custom table(s)...")
                for name, (_, attrs) in self.controller.fetch_ddic_objects(conn, tables).items():
                    if isinstance(attrs, dict):
                        if workspace.save_table(profile, name, attrs.get("FIELDS", []), project=main_prog):
                            self.after(0, self.write_log, f"[WS] Saved table: {name}")
                    else:
                        self.after(0, self.write_log, f"[WS] Skip table {name}: {attrs}")
        except Exception as e:
            self.after(0, self.write_log, f"[DEEP] Error: {e}")

    def _populate_tree_offline(self, profile, prog, objs):
        """Build the object tree from workspace knowledge only (no RFC)."""
        registry = {}
        for o in objs.get("DICT", []):
            if workspace.find_project(profile, workspace.TABLE_FOLDER, f"{o['name']}.json"):
                registry[o["name"]] = "TABL"
        for o in objs.get("INCLUDES", []):
            if workspace.find_project(profile, workspace.SOURCE_FOLDER, f"{o['name']}.abap"):
                registry[o["name"]] = "PROG"
        for o in objs.get("CLASS", []):
            code = workspace.read_code(profile, "Program", o["name"])
            if code and workspace.guess_ftype(code) == "Global Class":
                registry[o["name"]] = "CLAS"
        self.populate_tree(objs, registry, f"{prog}  (workspace — Re-fetch for TADIR check)")

    def filter_sap_tree(self, text: str):
        objs, registry, header = self._tree_last
        self.populate_tree(objs, registry, header, flt=text)

    def populate_tree(self, objs_dict, registry, header="Discovered Objects", flt=None):
        if flt is None:
            self._tree_last = (objs_dict, registry, header)
            flt = self.explorer_panel.sap_filter.get() if hasattr(self.explorer_panel, "sap_filter") else ""
        flt = (flt or "").strip().upper()
        for root in self.tree_roots.values():
            for item in self.tree.get_children(root):
                self.tree.delete(item)
        self.explorer_panel.set_sap_header(header)

        for cat, objects in objs_dict.items():
            root = self.tree_roots.get(cat)
            if not root:
                continue
            for obj in objects:
                name, line = obj["name"], obj.get("line", 0)
                tadir = registry.get(name, "")
                if cat == "DICT" and not tadir:
                    continue          # keywords / local vars / screen fields
                if flt and flt not in name:
                    continue
                icon = _TADIR_META.get(tadir, (None, "📍 "))[1] if tadir else "📍 "
                self.tree.insert(root, "end", text=f"{icon}{name}", values=(tadir, line, name))

    # ── SAP tree interaction ──────────────────────────────────────────────────

    def _tree_item_info(self):
        sel = self.tree.selection()
        if not sel or not self.tree.parent(sel[0]):
            return None
        vals = self.tree.item(sel[0], "values")
        if not vals or len(vals) < 3:
            return None
        try:
            line = int(vals[1])
        except (TypeError, ValueError):
            line = 0
        return str(vals[0]), line, str(vals[2])

    def on_tree_select(self, _event):
        """Single click → jump to the line in the main program."""
        info = self._tree_item_info()
        if info and info[1] > 0:
            self.jump_to_line(info[1])

    def on_tree_open(self, _event):
        """Double click → open the object (workspace first, then SAP)."""
        info = self._tree_item_info()
        if not info:
            return
        tadir, line, name = info
        category = _TADIR_META.get(tadir, (None, ""))[0]
        if not category:
            if tadir:
                self.write_log(f"[Tree] {name}: type {tadir} cannot be displayed.")
            elif line > 0:
                self.jump_to_line(line)
            return
        threading.Thread(target=self.run_sub_fetch,
                         args=(self.get_current_conn(), name, category, self.active_profile()),
                         daemon=True).start()

    def run_sub_fetch(self, conn, name, category, profile, force=False):
        name = name.upper()
        ftype = _CATEGORY_FTYPE.get(category, "Program")
        tab = _tab_name(ftype, name)
        try:
            if not force and profile:
                if category == "DICT":
                    fields = workspace.read_table_fields(profile, name)
                    if fields:
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {name}")
                        self.after(0, self.open_ddic_tab, tab, {"NAME": name, "FIELDS": fields}, "Table")
                        return
                else:
                    code = workspace.read_code(profile, ftype, name)
                    if code:
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {name}")
                        self.after(0, self.open_code_tab, tab, code, name, ftype, profile)
                        return

            self._log_dial(conn)
            if category == "DICT":
                code, attrs = self.controller.fetch_ddic_object(conn, name)
            elif category == "CLASS":
                code, attrs = self.controller.fetch_class_source(conn, name)
            elif category == "FUNC":
                code, attrs = self.controller.fetch_function_module(conn, name)
            else:
                code, attrs = self.controller.fetch_program(conn, name)

            if not code:
                self.after(0, self.write_log, f"FAILED: {name} - {attrs}")
                return

            self.after(0, self.write_log, f"SUCCESS: {name} loaded from SAP.")
            project = self.current_main_program or None
            if category == "DICT":
                self.after(0, self.open_ddic_tab, tab, attrs, "Table")
                saved = workspace.save_table(profile, name, attrs.get("FIELDS", []), project=project) if profile else ""
            else:
                self.after(0, self.open_code_tab, tab, code, name, ftype, profile)
                saved = workspace.save_code(profile, ftype, name, code, project=project) if profile else ""
            if saved:
                self.after(0, self.write_log, f"[WS] Saved: {saved}")
        except Exception as e:
            self.after(0, self.write_log, f"SUB-FETCH ERROR ({name}): {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Tabs
    # ══════════════════════════════════════════════════════════════════════════

    def open_code_tab(self, name, code, prog=None, ftype=None, source_profile=None,
                      is_proposal=False):
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return
        content = self.editor.add_tab(name)
        toolbar = ctk.CTkFrame(content, height=30, fg_color="transparent")
        toolbar.pack(fill="x", padx=10, pady=2)

        view = CodeView(content, code)
        txt = view.text
        ABAPHighlighter.apply(txt)
        view.pack(fill="both", expand=True, padx=T.PAD, pady=(0, T.PAD))

        self.tabs_dict[name] = {"textbox": txt, "view": view, "code": code, "prog": prog,
                                "ftype": ftype, "source_profile": source_profile}
        editing = [False]

        def _save():
            current = view.get()
            self.tabs_dict[name]["code"] = current
            profile = source_profile or self.active_profile()
            if not (profile and prog):
                self.write_log("[WS] Nothing to save (no profile / object name).")
                return
            if is_proposal:
                path = workspace.write_proposal(profile, prog, current)
                self._mark_proposal_seen(profile, path)
                self.editor.close_tab(f"Diff: {prog}")          # stale diff
                self.write_log(f"[WS] Proposal updated: {path}")
            else:
                path = workspace.save_code(profile, ftype or "Program", prog, current)
                self.write_log(f"[WS] Saved: {path}" if path else
                               f"[WS] {prog} is a standard object — not saved.")
            ABAPHighlighter.apply(txt)

        save_btn = ctk.CTkButton(toolbar, text="Save", width=70,
                                 fg_color="#2b5a2b", hover_color="#3a7a3a", command=_save)
        edit_btn = ctk.CTkButton(toolbar, text="Edit", width=70,
                                 fg_color="#3a3a3a", hover_color="#505050")

        def _toggle_edit():
            if not editing[0]:
                view.set_editable(True)
                edit_btn.configure(text="Lock", fg_color="#6e2b28", hover_color="#8e3b38")
                save_btn.pack(side="right", padx=(0, 4))
                editing[0] = True
            else:
                view.set_editable(False)
                ABAPHighlighter.apply(txt)
                edit_btn.configure(text="Edit", fg_color="#3a3a3a", hover_color="#505050")
                save_btn.pack_forget()
                editing[0] = False
        edit_btn.configure(command=_toggle_edit)

        def _show_diff():
            profile = source_profile or self.active_profile()
            original = workspace.read_code(profile, "Program", prog) if (profile and prog) else ""
            if not original:
                self.write_log(f"[WS] Original source for {prog} not in workspace — cannot diff.")
                return
            current = view.get()
            self.editor.close_tab(f"Diff: {prog}")
            self.open_diff_tab(f"Diff: {prog}", original, current, prog, profile)

        ctk.CTkButton(toolbar, text="Copy", width=70,
                      command=lambda: self.copy_to_clipboard(view.get())).pack(side="right")
        ctk.CTkButton(toolbar, text="Find  Ctrl+F", width=100, fg_color=T.PANEL_ALT,
                      hover_color=T.BORDER, command=view.show_find).pack(side="right", padx=4)
        edit_btn.pack(side="right", padx=4)
        if is_proposal and prog:
            ctk.CTkButton(toolbar, text="Show Diff", width=90,
                          fg_color="#3a2a5a", hover_color="#5a4a8a",
                          command=_show_diff).pack(side="right", padx=4)
        if prog and ftype and not is_proposal:
            ctk.CTkButton(toolbar, text="Re-fetch from SAP", width=150,
                          fg_color="#3a3000", hover_color="#5a4a00",
                          command=lambda: self.refetch_object(name, prog, ftype)).pack(side="right", padx=4)
        if source_profile:
            ctk.CTkLabel(toolbar, text=f"{source_profile}", text_color="#777777",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=4)

        self.editor.set_active(name)

    def refetch_object(self, tab_name, prog, ftype):
        """Close the tab and force a live RFC fetch that overwrites the workspace copy."""
        self.editor.close_tab(tab_name)
        self.write_log(f"[Re-fetch] {ftype} {prog} from SAP...")
        self.fetch_btn.configure(state="disabled", text="Working...")
        threading.Thread(target=self.run_fetch,
                         args=(self.get_current_conn(), prog, ftype, self.active_profile(), True),
                         kwargs={"force_sub": ftype == "Program"}, daemon=True).start()

    def _make_grid(self, content, columns, headings, widths, rows, style_name):
        """Shared ttk.Treeview grid for DDIC / data tabs. Returns the tree."""
        tree_frame = ctk.CTkFrame(content, fg_color="#1a1a1b")
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(2, 10))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.configure(style_name, background="#1a1a1b", foreground="#d4d4d4",
                        fieldbackground="#1a1a1b", rowheight=24, font=("Consolas", 12))
        style.configure(f"{style_name}.Heading", background="#2a2a2a", foreground="#569cd6",
                        font=("Consolas", 12, "bold"), relief="flat")
        style.map(style_name, background=[("selected", "#264f78")])

        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", style=style_name)
        for col, head, (w, anchor, stretch) in zip(columns, headings, widths):
            tree.heading(col, text=head)
            tree.column(col, width=w, minwidth=min(w, 40), anchor=anchor, stretch=stretch)
        tree.tag_configure("odd",  background="#1a1a1b")
        tree.tag_configure("even", background="#212123")

        sb_y = ttk.Scrollbar(tree_frame, orient="vertical",   command=tree.yview)
        sb_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        for i, row in enumerate(rows):
            tree.insert("", "end", values=row, tags=("even" if i % 2 == 0 else "odd",))
        return tree

    def open_ddic_tab(self, name, attrs, ftype="Table"):
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return
        content = self.editor.add_tab(name)
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        fields = attrs.get("FIELDS", [])
        obj_name = attrs.get("NAME", name.split(": ")[-1]).upper()
        hdr = ctk.CTkFrame(content, height=32, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        ctk.CTkLabel(hdr, text=f"{obj_name}  —  {len(fields)} fields",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="Re-fetch from SAP", width=150,
                      fg_color="#3a3000", hover_color="#5a4a00",
                      command=lambda: self.refetch_object(name, obj_name, ftype)).pack(side="right")

        cols  = ("key", "field", "type", "len", "decimals", "dataelement", "domain", "description")
        heads = ("Key", "Field Name", "Type", "Len", "Dec", "Data Element", "Domain", "Description")
        widths = [(35, "center", False), (180, "w", False), (70, "w", False), (55, "center", False),
                  (45, "center", False), (160, "w", False), (140, "w", False), (340, "w", True)]
        rows = [(f.get("Key", ""), f.get("Field", ""), f.get("Type", ""), f.get("Len", ""),
                 f.get("Decimals", ""), f.get("DataElement", ""), f.get("Domain", ""),
                 f.get("Description", "")) for f in fields]
        self._make_grid(content, cols, heads, widths, rows, "DDIC.Treeview")
        self.editor.set_active(name)

    def open_data_tab(self, name, columns, rows):
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return
        content = self.editor.add_tab(name)
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(content, height=32, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        table_name = name.split(": ", 1)[-1].split(" [")[0]
        ctk.CTkLabel(hdr, text=f"{table_name}  —  {len(rows)} rows  ×  {len(columns)} columns",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        col_ids = [f"c{i}" for i in range(len(columns))]      # ids must be unique & non-empty
        widths  = [(120, "w", False)] * len(columns)
        self._make_grid(content, col_ids, columns, widths, rows, "Data.Treeview")
        self.editor.set_active(name)

    def open_diff_tab(self, name, original_code, proposed_code, prog, profile=None):
        """Unified diff of original vs proposed code with green/red line colouring."""
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return
        diff_lines = list(difflib.unified_diff(
            original_code.splitlines(), proposed_code.splitlines(),
            fromfile="original", tofile="proposed", lineterm=""))

        content = self.editor.add_tab(name)
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(content, height=30, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(4, 0))
        ctk.CTkButton(toolbar, text="Open Proposal Code", width=150,
                      fg_color="#2b5a2b", hover_color="#3a7a3a",
                      command=lambda: self.open_code_tab(f"Proposal: {prog}", proposed_code,
                                                         prog, "Program", profile, is_proposal=True)
                      ).pack(side="left")
        added   = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        ctk.CTkLabel(toolbar, text=f"+{added} added   -{removed} removed",
                     font=ctk.CTkFont(size=12), text_color="#aaaaaa").pack(side="left", padx=12)

        txt = ctk.CTkTextbox(content, font=("Consolas", 13), wrap="none", fg_color="#1a1a1b")
        txt.tag_config("added",   foreground="#98c379", background="#1a3a1a")
        txt.tag_config("removed", foreground="#e06c75", background="#3a1a1a")
        txt.tag_config("header",  foreground="#569cd6")
        txt.tag_config("meta",    foreground="#666666")
        if not diff_lines:
            txt.insert("end", "No differences — proposed code is identical to original.", "meta")
        for line in diff_lines:
            tag = ("meta" if line.startswith(("+++", "---")) else
                   "header" if line.startswith("@@") else
                   "added" if line.startswith("+") else
                   "removed" if line.startswith("-") else "")
            txt.insert("end", line + "\n", tag) if tag else txt.insert("end", line + "\n")
        txt.configure(state="disabled")
        txt.grid(row=1, column=0, sticky="nsew", padx=1, pady=1)

        self.tabs_dict[name] = {"textbox": txt, "code": proposed_code, "prog": prog,
                                "ftype": "Program", "source_profile": profile}
        self.editor.set_active(name)

    def jump_to_line(self, line):
        target = f"Program: {self.current_main_program}" if self.current_main_program else ""
        if target not in self.tabs_dict:
            target = self.active_tab_name
        entry = self.tabs_dict.get(target)
        if not entry:
            return
        self.editor.set_active(target)
        if entry.get("view"):
            entry["view"].goto(line)
            return
        txt = entry["textbox"]
        txt.tag_remove("hl", "1.0", "end")
        txt.tag_config("hl", background="#4a4a00")
        txt.tag_add("hl", f"{line}.0", f"{line}.end")
        txt.see(f"{line}.0")

    def _close_tabs_for_files(self, filenames):
        for fname in filenames:
            stem = os.path.splitext(fname)[0].upper()
            for prefix in _TAB_PREFIXES:
                self.editor.close_tab(f"{prefix}: {stem}")

    # ══════════════════════════════════════════════════════════════════════════
    # Workspace explorer
    # ══════════════════════════════════════════════════════════════════════════
    # Tree values: (display_kind, profile, folder, filename, project, node_kind)
    # node_kind is "_profile" | "_project" | "_folder" | "file"

    @staticmethod
    def _ws_kind(vals) -> str:
        if len(vals) > 5:
            return str(vals[5])
        return str(vals[0]) if str(vals[0]).startswith("_") else "file"

    _WS_FOLDER_LABEL = {"programs": "📝  Programs", "tables": "📊  Tables", "proposals": "📬  Proposals"}

    def refresh_workspace_tree(self):
        """Rebuild the Workspace tree. Disk + git work happens in a background thread."""
        if self._ws_refreshing:
            self._ws_refresh_pending = True
            return
        self._ws_refreshing = True
        profile = self.active_profile()
        threading.Thread(target=self._ws_refresh_worker, args=(profile,), daemon=True).start()

    def _ws_refresh_worker(self, profile):
        data, git_st, branch = {}, {}, ""
        try:
            data   = {profile: workspace.list_files(profile)} if profile else {}
            git_st = github_sync.get_git_status()
            branch = github_sync.get_branch_name()
        except Exception as e:
            self.after(0, self.write_log, f"[WS] Refresh error: {e}")
        self.after(0, self._ws_apply, data, git_st, branch, profile)

    def filter_workspace_tree(self, text: str):
        data, git_st, profile = self._ws_last
        self._ws_build(data, git_st, profile, flt=text)

    def _ws_apply(self, data, git_st, branch, profile=""):
        try:
            self._ws_last = (data, git_st, profile)
            self._ws_build(data, git_st, profile)
            self.explorer_panel.set_branch_label(branch)
            self._status_right.set(f"🌿 {branch}   ·   read-only RFC" if branch else "read-only RFC")
        finally:
            self._ws_refreshing = False
            if self._ws_refresh_pending:
                self._ws_refresh_pending = False
                self.refresh_workspace_tree()

    def _ws_build(self, data, git_st, profile="", flt=None):
        tree  = self.ws_tree
        icons = getattr(self, "ws_icons", {})
        if flt is None:
            flt = self.explorer_panel.ws_filter.get() if hasattr(self.explorer_panel, "ws_filter") else ""
        flt = (flt or "").strip().upper()

        # Remember collapsed/expanded state and scroll position
        open_state = {}
        def _collect(item):
            vals = tree.item(item, "values")
            if vals and len(vals) >= 5 and self._ws_kind(vals) != "file":
                open_state[(self._ws_kind(vals), vals[1], vals[4], vals[2])] = bool(tree.item(item, "open"))
            for child in tree.get_children(item):
                _collect(child)
        for root_item in tree.get_children():
            _collect(root_item)
        yview = tree.yview()

        tree.delete(*tree.get_children())
        if not data or not any(data.values()):
            empty = f"(no cached objects yet for profile {profile})" if profile else "(select a profile)"
            tree.insert("", "end", text=empty)
            return

        pri = {"M": 3, "?": 2, "D": 1}
        def _worst(a, b):
            return a if pri.get(a, 0) >= pri.get(b, 0) else b
        def _tag(st):
            return {"M": "ws_modified", "?": "ws_new", "D": "ws_deleted"}.get(st, "")
        def _prefix(st):
            return {"M": "● ", "?": "+ ", "D": "✗ "}.get(st, "")

        proj_st, prof_st = {}, {}
        for path, st in git_st.items():
            parts = path.split("/")
            if len(parts) >= 2:
                proj_st[f"{parts[0]}/{parts[1]}"] = _worst(proj_st.get(f"{parts[0]}/{parts[1]}", ""), st)
                prof_st[parts[0]] = _worst(prof_st.get(parts[0], ""), st)

        sub_icon = {"programs": icons.get("folder_prog"), "tables": icons.get("folder_tbl"),
                    "proposals": icons.get("folder_prop")}

        def _img(key):
            return {"image": icons[key]} if icons.get(key) else {}

        for profile in sorted(data):
            projects = data[profile]
            if not projects:
                continue
            pst = prof_st.get(profile, "")
            p_node = tree.insert("", "end", text=f"{_prefix(pst)}{profile}",
                                 open=open_state.get(("_profile", profile, "", ""), True),
                                 values=("", profile, "", "", "", "_profile"),
                                 tags=(_tag(pst),) if _tag(pst) else (), **_img("profile"))
            for proj in sorted(projects):
                if flt and not any(flt in f.upper() or flt in proj.upper()
                                   for fl in projects[proj].values() for f in fl):
                    continue
                prst = proj_st.get(f"{profile}/{proj}", "")
                proj_node = tree.insert(p_node, "end", text=f"{_prefix(prst)}{proj}",
                                        open=open_state.get(("_project", profile, proj, ""), True),
                                        values=("", profile, "", "", proj, "_project"),
                                        tags=(_tag(prst),) if _tag(prst) else (), **_img("folder"))
                for folder in workspace.FOLDERS:
                    fnames = projects[proj].get(folder, [])
                    if flt:
                        fnames = [f for f in fnames if flt in f.upper() or flt in proj.upper()]
                    if not fnames:
                        continue
                    kw = {"image": sub_icon[folder]} if sub_icon.get(folder) else {}
                    f_node = tree.insert(proj_node, "end",
                                         text=f"{self._WS_FOLDER_LABEL[folder]}  ({len(fnames)})",
                                         open=open_state.get(("_folder", profile, proj, folder), True),
                                         values=("", profile, folder, "", proj, "_folder"), **kw)
                    for fname in fnames:
                        is_abap, is_json = fname.endswith(".abap"), fname.endswith(".json")
                        kind = "ABAP" if is_abap else "Table" if is_json else ""
                        fst  = git_st.get(f"{profile}/{proj}/{folder}/{fname}", "")
                        kw   = _img("file_abap") if is_abap else _img("file_json") if is_json else {}
                        tree.insert(f_node, "end", text=f"{_prefix(fst)}{fname}",
                                    values=(kind, profile, folder, fname, proj, "file"),
                                    tags=(_tag(fst),) if _tag(fst) else (), **kw)
        try:
            tree.yview_moveto(yview[0])
        except Exception:
            pass

    def on_workspace_select(self, _event):
        sel = self.ws_tree.selection()
        if not sel:
            return
        vals = self.ws_tree.item(sel[0], "values")
        if vals and len(vals) >= 5 and self._ws_kind(vals) == "file":
            self._ws_open_vals(vals)

    def _ws_open_vals(self, vals):
        _kind, profile, folder, filename, project = (str(v) for v in vals[:5])
        prog = os.path.splitext(filename)[0].upper()

        if folder == workspace.PROP_FOLDER:
            code = workspace.read_file(profile, folder, filename, project=project)
            if code:
                self.open_code_tab(f"Proposal: {prog}", code, prog, "Program", profile, is_proposal=True)
        elif filename.endswith(".json"):
            fields = workspace.read_table_fields(profile, prog, project=project)
            if fields:
                self.open_ddic_tab(f"Table: {prog}", {"NAME": prog, "FIELDS": fields}, "Table")
        else:
            code = workspace.read_file(profile, folder, filename, project=project)
            if code:
                ftype = workspace.guess_ftype(code)
                self.open_code_tab(_tab_name(ftype, prog), code, prog, ftype, profile)
                if ftype == "Program" and project == prog:
                    self.current_main_program = prog
                    self._populate_tree_offline(profile, prog, ABAPParser.get_objects(code))
                return
        if not (code if folder != workspace.TABLE_FOLDER else fields):
            self.write_log(f"[WS] Could not read {filename}")

    # ── Context menu ──────────────────────────────────────────────────────────

    def on_ws_right_click(self, event):
        tree = self.ws_tree
        item = tree.identify_row(event.y)
        if not item:
            return
        tree.selection_set(item)
        vals = tree.item(item, "values")      # capture now — tree may be rebuilt later
        if not vals or len(vals) < 5:
            return
        kind = self._ws_kind(vals)

        menu = tk.Menu(self, tearoff=0, bg="#252526", fg="#cccccc",
                       activebackground="#094771", activeforeground="#ffffff", relief="flat", bd=1)
        if kind == "file":
            menu.add_command(label="  Open", command=lambda v=vals: self._ws_open_vals(v))
        menu.add_command(label="  Show in Windows Explorer", command=lambda v=vals: self._ws_reveal(v))
        menu.add_separator()
        delete_label = {"_profile": "  Delete Profile Folder...",
                        "_project": "  Delete Project...",
                        "_folder":  "  Delete Folder Contents..."}.get(kind, "  Delete File...")
        menu.add_command(label=delete_label, command=lambda v=vals: self._confirm_delete_ws(v))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    @classmethod
    def _ws_path_for(cls, vals):
        _d, profile, folder, fname, proj = (str(v) for v in vals[:5])
        kind = cls._ws_kind(vals)
        if kind == "_profile":
            return workspace.abs_path(profile)
        if kind == "_project":
            return workspace.abs_path(profile, proj)
        if kind == "_folder":
            return workspace.abs_path(profile, proj, folder)
        return workspace.abs_path(profile, proj, folder, fname)

    def _ws_reveal(self, vals):
        path = os.path.normpath(self._ws_path_for(vals))
        if not os.path.exists(path):
            self.write_log(f"[WS] Not found: {path}")
            return
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                subprocess.Popen(["explorer", "/select,", path])
        except Exception as e:
            self.write_log(f"[WS] Could not open Explorer: {e}")

    def _confirm_delete_ws(self, vals):
        _d, _profile, folder, fname, proj = (str(v) for v in vals[:5])
        kind = self._ws_kind(vals)
        path = self._ws_path_for(vals)
        label = {"_profile": f"Delete entire profile folder?\n\n📁  {_profile}",
                 "_project": f"Delete entire project folder?\n\n📁  {proj}",
                 "_folder":  f"Delete folder and all its contents?\n\n📁  {proj} / {folder}"
                 }.get(kind, f"Delete file?\n\n📄  {fname}")

        if not os.path.exists(path):
            self.write_log(f"[WS] Not found: {path}")
            return
        if not mbox.askyesno("Delete", label, icon="warning"):
            return

        affected = []
        if os.path.isdir(path):
            for _d, _dirs, files in os.walk(path):
                affected.extend(files)
        else:
            affected.append(fname)
        try:
            workspace.delete_path(path)
            self.write_log(f"[WS] Deleted: {path}")
        except Exception as e:
            mbox.showerror("Delete Error", str(e))
            return
        self._close_tabs_for_files(affected)
        self.refresh_workspace_tree()

    # ── Proposal watcher ──────────────────────────────────────────────────────

    def _seed_proposals(self, profile):
        """Remember existing proposals so they are not re-opened as 'new'."""
        for proj, fname, mtime in workspace.scan_proposals(profile):
            self._watched_proposals[f"{profile}/{proj}/{fname}"] = mtime

    def _mark_proposal_seen(self, profile, path):
        try:
            proj  = os.path.basename(os.path.dirname(os.path.dirname(path)))
            fname = os.path.basename(path)
            self._watched_proposals[f"{profile}/{proj}/{fname}"] = os.stat(path).st_mtime_ns
        except OSError:
            pass

    def _poll_proposals(self):
        """Every 2 s: refresh tree if the workspace changed; open new/updated proposals."""
        try:
            snap = workspace.snapshot()
            if snap != self._ws_snapshot:
                self._ws_snapshot = snap
                self.refresh_workspace_tree()

            profile = self.active_profile()
            if profile:
                for proj, fname, mtime in workspace.scan_proposals(profile):
                    key = f"{profile}/{proj}/{fname}"
                    if self._watched_proposals.get(key) == mtime:
                        continue
                    self._watched_proposals[key] = mtime
                    self._open_proposal(profile, proj, fname)
        except Exception as e:
            self.write_log(f"[WS] Poll error: {e}")
        finally:
            self.after(2000, self._poll_proposals)

    def _open_proposal(self, profile, project, fname):
        name = os.path.splitext(fname)[0].upper()
        proposed = workspace.read_file(profile, workspace.PROP_FOLDER, fname, project=project)
        if not proposed:
            return
        original = ""
        for prefix in _CODE_TYPES:
            original = self.tabs_dict.get(f"{prefix}: {name}", {}).get("code", "")
            if original:
                break
        if not original:
            original = (workspace.read_code(profile, "Program", name, project=project)
                        or workspace.read_code(profile, "Program", name))

        self.write_log(f"[WS] Proposal arrived: {project}/{fname}")
        self.editor.close_tab(f"Diff: {name}")
        self.editor.close_tab(f"Proposal: {name}")
        if original:
            self.open_diff_tab(f"Diff: {name}", original, proposed, name, profile)
        else:
            self.open_code_tab(f"Proposal: {name}", proposed, name, "Program", profile, is_proposal=True)

    # ══════════════════════════════════════════════════════════════════════════
    # Misc
    # ══════════════════════════════════════════════════════════════════════════

    def write_log(self, text):
        if not hasattr(self, "logs_text"):
            return
        self.logs_text.configure(state="normal")
        self.logs_text.insert("end", f">>> {text}\n")
        self.logs_text.see("end")
        self.logs_text.configure(state="disabled")

    def copy_to_clipboard(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.write_log("Copied.")

    def reset_buttons(self):
        self.fetch_btn.configure(state="normal", text="Fetch")

    # ── Claude Code sessions ──────────────────────────────────────────────────

    def _load_claude_sessions(self) -> list:
        try:
            with open(CLAUDE_SESSIONS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def list_claude_sessions(self, profile: str) -> list:
        """Most recent first: [{"id", "title", "profile", "last"}, …] for one profile."""
        return [x for x in self._load_claude_sessions() if x.get("profile") == profile]

    def remember_claude_session(self, session, title: str):
        if not session.session_id:
            return
        import time
        items = [x for x in self._load_claude_sessions() if x.get("id") != session.session_id]
        items.insert(0, {"id": session.session_id, "title": title, "profile": session.profile,
                         "last": time.strftime("%Y-%m-%d %H:%M"), "cost": round(session.total_cost, 4)})
        try:
            with open(CLAUDE_SESSIONS_FILE, "w", encoding="utf-8") as fh:
                json.dump(items[:50], fh, indent=2)
        except OSError:
            pass

    def open_claude_tab(self, session_id: str = None, title: str = None):
        """Open a Claude Code session tab (new session, or resume an existing one)."""
        profile = self.active_profile()
        if not profile:
            mbox.showwarning("Claude", "Select or save a connection profile first.")
            return
        if not find_claude():
            mbox.showwarning("Claude Code not found",
                             "Claude Code CLI is not installed or not on PATH.\n\n"
                             "Install:  winget install Anthropic.ClaudeCode\n"
                             "Then run  claude  once to log in with your subscription.")
            return
        if not title:
            n = 1
            while f"Claude: #{n}" in self.editor.tabs_dict or any(
                    x.get("title") == f"#{n}" for x in self.list_claude_sessions(profile)):
                n += 1
            title = f"#{n}"
        name = f"Claude: {title}"
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return
        cwd = workspace.abs_path(profile)
        session = ClaudeSession(cwd=cwd, profile=profile, session_id=session_id)
        content = self.editor.add_tab(name)
        panel = ClaudePanel(content, self, session, title)
        panel.pack(fill="both", expand=True)
        self.editor.set_active(name)
        self.write_log(f"[Claude] {'Resumed' if session_id else 'New'} session {title} "
                       f"(cwd={cwd}, mcp={'yes' if session.has_mcp else 'no'})")

    def proposal_from_code(self, code: str):
        """Save a code block from the Claude tab as a proposal → diff tab opens via the watcher."""
        profile = self.active_profile()
        if not profile:
            return
        name = ""
        for tab in reversed(list(self.tabs_dict)):
            entry = self.tabs_dict[tab]
            if entry.get("prog") and not str(tab).startswith(("Diff:", "Proposal:")):
                name = entry["prog"]
                break
        name = name or self.current_main_program
        m = re.search(r"^\s*(?:REPORT|PROGRAM|FUNCTION)\s+([\w/]+)", code, re.I | re.M)
        if m:
            name = m.group(1).upper()
        name = askstring("Proposal", "Program name for this proposal:", initialvalue=name)
        if not name:
            return
        path = workspace.write_proposal(profile, name.strip().upper(), code)
        self.write_log(f"[Claude] Proposal written: {path}")

    def get_active_code_context(self) -> str:
        """Describe the active code tab for Claude (file path + inline code when small)."""
        entry = self.tabs_dict.get(self.active_tab_name or "")
        if not entry or not entry.get("prog"):
            return ""
        prog, ftype = entry["prog"], entry.get("ftype") or "Program"
        profile = entry.get("source_profile") or self.active_profile()
        code = entry.get("code", "")
        folder = workspace.PROP_FOLDER if str(self.active_tab_name).startswith("Proposal:") else workspace.SOURCE_FOLDER
        proj = workspace.find_project(profile, folder, f"{prog}.abap") or prog
        rel = f"{proj}/{folder}/{prog}.abap"
        ctx = (f"[IDE context] The user has '{self.active_tab_name}' open ({ftype} {prog}, "
               f"SAP profile {profile}). Cached file relative to the working directory: {rel}")
        if code and len(code) <= _CONTEXT_INLINE_LIMIT:
            ctx += f"\nCurrent content:\n```abap\n{code}\n```"
        elif code:
            ctx += f"\nThe file is large ({len(code.splitlines())} lines); read it with the Read tool."
        return ctx

    # ── GitHub sync ───────────────────────────────────────────────────────────

    def _github_op(self, label, fn):
        profile = self.active_profile()
        if not profile:
            mbox.showwarning("GitHub", "Select a profile first.")
            return
        self.write_log(f"[GitHub] {label} {profile}...")

        def _work():
            ok, msg = fn(profile)
            self.after(0, self.write_log, f"[GitHub] {msg}")
            self.after(0, self.refresh_workspace_tree)
            if ok:
                self.after(0, mbox.showinfo, f"GitHub {label}", msg)
            else:
                self.after(0, mbox.showerror, f"GitHub {label} Failed", msg)
        threading.Thread(target=_work, daemon=True).start()

    def github_push(self):
        self._github_op("Push", github_sync.push_workspace)

    def github_pull(self):
        self._github_op("Pull", github_sync.pull_workspace)


if __name__ == "__main__":
    App().mainloop()
