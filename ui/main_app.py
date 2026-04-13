import customtkinter as ctk
import threading
import json
import os
import sys
import difflib
import tkinter.messagebox as mbox
from tkinter.simpledialog import askstring
from tkinter import ttk
from dotenv import load_dotenv

from core.controller import AnalysisController
from utils.highlighter import ABAPHighlighter
from utils.parser import ABAPParser
from utils import workspace
from utils import github_sync

# Import new modular panels
from ui.panels.sidebar import SidebarPanel
from ui.panels.editor import EditorPanel
from ui.panels.chat_panel import ChatPanel

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

def _find_dotenv() -> str:
    """Locate .env whether running as source or PyInstaller .exe"""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), ".env")
    return os.path.join(os.path.dirname(__file__), "..", ".env")

load_dotenv(_find_dotenv())

# User data goes to AppData — survives rebuilds, --clean, even uninstall/reinstall
_APP_DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ABAP_AI")
os.makedirs(_APP_DATA_DIR, exist_ok=True)
SYSTEMS_FILE = os.path.join(_APP_DATA_DIR, "systems.json")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ABAP AI IDE - Modular Edition")
        self.geometry("1600x900")
        
        self.controller = AnalysisController()
        self.systems_data = self.load_systems_file()
        self.tabs_dict = {} 
        self.current_main_program = ""

        self._setup_layout()
        
        # Instantiate Panels
        self.sidebar = SidebarPanel(self, self)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        
        self.editor = EditorPanel(self, self)
        self.editor.grid(row=0, column=1, sticky="nsew", padx=2)
        
        self.chat = ChatPanel(self, self)
        self.chat.grid(row=0, column=2, sticky="nsew", padx=(2, 0))
        
        # Initial logs tab creation
        logs_content = self.editor.add_tab("System Logs", is_closable=False)
        self.logs_text = ctk.CTkTextbox(logs_content, font=("Consolas", 12), wrap="word")
        self.logs_text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self.logs_text.configure(state="disabled")
        self.editor.set_active("System Logs")
        
        # Initial Profile Load
        names = list(self.systems_data.keys())
        if names: self.on_system_select(names[0])

        # Prefill AI
        self.initialize_ai_from_env()

        # Proposal file watcher — polls workspace/PROP every 2 seconds
        self._watched_proposals = set()
        self._poll_proposals()
        # Populate workspace explorer on startup
        self.after(500, self.refresh_workspace_tree)

    def _setup_layout(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0, minsize=320) 
        self.grid_columnconfigure(1, weight=1)              
        self.grid_columnconfigure(2, weight=0, minsize=380) 

    # -- System Profile Management --
    def load_systems_file(self):
        if os.path.exists(SYSTEMS_FILE):
            with open(SYSTEMS_FILE, "r") as f: return json.load(f)
        return {}

    def flush_systems_file(self):
        with open(SYSTEMS_FILE, "w") as f: json.dump(self.systems_data, f, indent=4)

    def on_system_select(self, name):
        if name in self.systems_data:
            sys = self.systems_data[name]
            for k in ["ashost", "sysnr", "client", "user", "passwd", "router"]:
                # Check for both 'router' and 'saprouter' for data persistence safety
                val = sys.get(k)
                if val is None and k == "router":
                    val = sys.get("saprouter", "")
                elif val is None:
                    val = ""
                
                field = getattr(self, "sap_"+k)
                field.delete(0, "end")
                field.insert(0, str(val))
        self.write_log(f"Switched to profile: {name}")

    def save_current_system(self):
        sys_name = self.sidebar.system_var.get()
        if sys_name == "New Profile":
            sys_name = askstring("Profile Name", "Enter a name for this profile:")
            if not sys_name: return
            self.sidebar.system_var.set(sys_name)
        
        data = {k: getattr(self, "sap_"+k).get() for k in ["ashost", "sysnr", "client", "user", "passwd", "router"]}
        # Duplicate router key to saprouter for backward/forward compatibility
        if data.get("router"):
            data["saprouter"] = data["router"]
            
        self.systems_data[sys_name] = data
        self.flush_systems_file()
        
        # Update dropdown
        names = list(self.systems_data.keys())
        self.sidebar.system_dropdown.configure(values=names)
        self.write_log(f"Profile '{sys_name}' saved.")

    def new_system_profile(self):
        self.sidebar.system_var.set("New Profile")
        for k in ["ashost", "sysnr", "client", "user", "passwd", "router"]:
            getattr(self, "sap_"+k).delete(0, "end")
        self.write_log("Ready for new profile.")

    def delete_current_system(self):
        sys_name = self.sidebar.system_var.get()
        if sys_name in self.systems_data:
            if mbox.askyesno("Confirm", f"Delete {sys_name}?"):
                del self.systems_data[sys_name]
                self.flush_systems_file()
                names = list(self.systems_data.keys())
                self.sidebar.system_dropdown.configure(values=names if names else ["New Profile"])
                self.on_system_select(names[0] if names else "New Profile")

    # -- Logic Glue --
    def get_current_conn(self):
        conn = {}
        for k in ["ashost", "sysnr", "client", "user", "passwd", "router"]:
            val = getattr(self, "sap_"+k).get().strip()
            if val:
                # IMPORTANT: pyrfc expects 'saprouter' parameter, not 'router'
                key = "saprouter" if k == "router" else k
                conn[key] = val
        return conn

    def fetch_program_flow(self):
        program = self.editor.name_entry.get().strip()
        if not program: return
        self.write_log(f"Fetching {program}...")
        self.fetch_btn.configure(state="disabled", text="Working...")
        conn = self.get_current_conn()
        ftype = self.editor.type_menu.get()
        threading.Thread(target=self.run_fetch, args=(conn, program, ftype), daemon=True).start()

    def run_fetch(self, conn, prog, ftype, force=False):
        try:
            profile = self.sidebar.system_var.get()

            # Track the main program so includes are saved under its project folder
            if ftype not in ("Table", "Structure"):
                self.current_main_program = prog.upper()

            # ── Workspace-first (skip on forced re-fetch) ─────────────────────
            if not force:
                if ftype in ("Table", "Structure"):
                    ws_fields = workspace.read_table_fields(profile, prog)
                    if ws_fields:
                        attrs = {"NAME": prog, "FIELDS": ws_fields}
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {prog}")
                        self.after(0, self.open_ddic_tab, f"{ftype}: {prog}", attrs, ftype)
                        return
                else:
                    ws_code = workspace.read_code(profile, ftype, prog)
                    if ws_code:
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {prog}")
                        self.after(0, self.open_code_tab, f"{ftype}: {prog}", ws_code,
                                   None, prog, ftype, profile)
                        return

            # ── Live RFC fetch ────────────────────────────────────────────────
            printable_conn = {k: (v if k != "passwd" else "********") for k, v in conn.items()}
            self.after(0, self.write_log, f"[DIAG] RFC Params: {printable_conn}")
            self.after(0, self.write_log, f"[RFC] Initiating Dial to {conn.get('ashost')}...")

            if ftype == "Table" or ftype == "Structure":
                code, attrs = self.controller.fetch_ddic_object(conn, prog)
            elif ftype == "Global Class":
                code, attrs = self.controller.fetch_class_source(conn, prog)
            elif ftype in ("Function Module", "Function Group"):
                code, attrs = self.controller.fetch_function_module(conn, prog)
            else:
                code, attrs = self.controller.fetch_program(conn, prog)

            if not code:
                err_msg = attrs if isinstance(attrs, str) else "Object not found."
                self.after(0, self.write_log, f"FAILED: {prog} - {err_msg}")
            else:
                self.after(0, self.write_log, f"SUCCESS: {prog} loaded.")
                if ftype in ("Table", "Structure"):
                    self.after(0, self.open_ddic_tab, f"{ftype}: {prog}", attrs, ftype)
                    saved = workspace.save_table(profile, prog, attrs.get("FIELDS", []), project=prog)
                else:
                    self.after(0, self.open_code_tab, f"{ftype}: {prog}", code, attrs,
                               prog, ftype, profile)
                    saved = workspace.save_code(profile, ftype, prog, code, project=prog)
                if saved:
                    self.after(0, self.write_log, f"[WS] Saved: {saved}")
                    self.after(0, self.refresh_workspace_tree)
                if ftype == "Program":
                    objs = ABAPParser.get_objects(code)
                    threading.Thread(target=self.run_proactive_check, args=(conn, objs, profile), daemon=True).start()
        except Exception as e:
            self.after(0, self.write_log, f"CONNECTION ERROR: {str(e)}")
        finally:
            self.after(0, self.reset_buttons)

    def run_proactive_check(self, conn, main_objs, profile=""):
        """Deep discovery: parse main program + all includes to find all referenced SAP objects."""
        # Merge helper
        def _merge(combined, seen, new_objs):
            for cat, items in new_objs.items():
                combined.setdefault(cat, [])
                seen.setdefault(cat, set())
                for obj in items:
                    name = obj["name"] if isinstance(obj, dict) else str(obj)
                    if name not in seen[cat]:
                        combined[cat].append(obj)
                        seen[cat].add(name)

        combined = {}
        seen = {}
        _merge(combined, seen, main_objs)

        # Fetch and parse each include found in the main program
        include_list = [o["name"] if isinstance(o, dict) else o for o in main_objs.get("INCLUDES", [])]
        for inc_name in include_list:
            self.after(0, self.write_log, f"[DEEP] Scanning: {inc_name}")
            try:
                inc_code, _ = self.controller.fetch_program(conn, inc_name)
                if inc_code:
                    _merge(combined, seen, ABAPParser.get_objects(inc_code))
                    if profile:
                        # Save includes under the main program's project folder
                        saved = workspace.save_code(profile, "PROG", inc_name, inc_code, project=self.current_main_program)
                        if saved:
                            self.after(0, self.write_log, f"[WS] Saved include: {inc_name}")
            except Exception as e:
                self.after(0, self.write_log, f"[DEEP] Skip {inc_name}: {e}")

        # Collect all unique names for a single TADIR batch check
        all_names = []
        for cat in ["DICT", "CLASS", "INCLUDES"]:
            for o in combined.get(cat, []):
                all_names.append(o["name"] if isinstance(o, dict) else o)

        self.after(0, self.write_log, f"[DISCOVERY] Checking {len(all_names)} objects in TADIR...")
        global_registry = self.controller.check_objects_batch(conn, all_names)
        self.after(0, self.write_log, f"[DISCOVERY] {len(global_registry)} SAP objects verified.")
        self.after(0, self.populate_tree, combined, global_registry)

    def populate_tree(self, objs_dict, registry):
        # Clear tree
        for root in self.tree_roots.values():
            for item in self.tree.get_children(root): self.tree.delete(item)
            
        ICON_MAP = {"TABL": "▦ ", "VIEW": "▦ ", "CLAS": "💎 ", "PROG": "📝 ", "MSAG": "💬 ", "FUGR": "⚙ ", "FUNC": "⚙ "}
        for cat, objects in objs_dict.items():
            if cat not in self.tree_roots: continue
            for obj in objects:
                if isinstance(obj, dict):
                    name = obj.get("name", "Unknown")
                    line = obj.get("line", 0)
                else:
                    name = str(obj)
                    line = 0

                tadir_type = registry.get(name.upper())

                # DICT: only show objects confirmed in TADIR — filters out ABAP keywords,
                # local vars, screen fields (SO_*, SSCRFIELDS, TEXT-xxx, etc.)
                if cat == "DICT" and not tadir_type:
                    continue

                icon = ICON_MAP.get(tadir_type, "📍 ")
                self.tree.insert(self.tree_roots[cat], "end", text=f"{icon} {name}", values=(tadir_type or "", line, tadir_type or ""))

    def on_tree_select(self, _event):
        sel = self.tree.selection()
        if not sel: return
        item = sel[0]
        parent = self.tree.parent(item)
        if not parent: return
        
        name = self.tree.item(item, "text").split(" ")[-1]
        vals = self.tree.item(item, "values")
        line = int(vals[1]) if vals and len(vals)>1 else 0
        ttype = vals[0]
        
        if ttype or line == 0:
            conn = self.get_current_conn()
            threading.Thread(target=self.run_sub_fetch, args=(conn, name, "DICT" if ttype in ["TABL","VIEW"] else "PROG"), daemon=True).start()
        else:
            self.jump_to_line(line)

    def run_sub_fetch(self, conn, name, category, force=False):
        try:
            profile = self.sidebar.system_var.get()

            # ── Workspace-first ───────────────────────────────────────────────
            if not force:
                if category == "DICT":
                    ws_fields = workspace.read_table_fields(profile, name)
                    if ws_fields:
                        attrs = {"NAME": name, "FIELDS": ws_fields}
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {name}")
                        self.after(0, self.open_ddic_tab, f"Table: {name}", attrs, "Table")
                        return
                else:
                    ws_code = workspace.read_code(profile, "PROG", name)
                    if ws_code:
                        self.after(0, self.write_log, f"[WS] Loaded from workspace: {name}")
                        self.after(0, self.open_code_tab, f"Object: {name}", ws_code,
                                   None, name, "Program", profile)
                        return

            # ── Live RFC fetch ────────────────────────────────────────────────
            if category == "DICT":
                code, attrs = self.controller.fetch_ddic_object(conn, name)
                dname = f"Table: {name}"
            else:
                code, attrs = self.controller.fetch_program(conn, name)
                dname = f"Object: {name}"

            if code:
                self.after(0, self.write_log, f"SUCCESS: {name} loaded.")
                if category == "DICT":
                    # Tables/structures → own project folder
                    self.after(0, self.open_ddic_tab, dname, attrs, "Table")
                    saved = workspace.save_table(profile, name, attrs.get("FIELDS", []),
                                                 project=name)
                else:
                    # Includes/programs → save under the active main program's folder
                    proj = self.current_main_program if self.current_main_program else name
                    self.after(0, self.open_code_tab, dname, code, attrs,
                               name, "Program", profile)
                    saved = workspace.save_code(profile, "PROG", name, code, project=proj)
                if saved:
                    self.after(0, self.write_log, f"[WS] Saved: {saved}")
                    self.after(0, self.refresh_workspace_tree)
            else:
                self.after(0, self.write_log, f"FAILED: {name}")
        except Exception as e:
            self.after(0, self.write_log, f"SUB-FETCH ERROR ({name}): {str(e)}")

    def open_code_tab(self, name, code, _attrs=None, prog=None, ftype=None,
                      source_profile=None):
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return
        content = self.editor.add_tab(name)
        toolbar = ctk.CTkFrame(content, height=30, fg_color="transparent")
        toolbar.pack(fill="x", padx=10, pady=2)

        txt = ctk.CTkTextbox(content, font=("Consolas", 14), wrap="none", fg_color="#1a1a1b")
        txt.insert("0.0", code); ABAPHighlighter.apply(txt); txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

        self.tabs_dict[name] = {
            "textbox": txt, "code": code,
            "prog": prog, "ftype": ftype,
            "source_profile": source_profile,   # which SAP system this came from
        }

        is_proposal = name.startswith("Proposal:")
        _editing = [False]  # mutable flag — avoids unreliable cget("state")

        # ── Save (hidden until edit mode) ─────────────────────────────────────
        def _save():
            current = txt.get("0.0", "end-1c")
            self.tabs_dict[name]["code"] = current
            profile = self.sidebar.system_var.get()
            if is_proposal and prog:
                workspace.write_proposal(profile, prog, current)
                self.write_log(f"[WS] Proposal updated: {prog}")
            elif prog and ftype:
                workspace.save_code(profile, ftype, prog, current)
                self.write_log(f"[WS] Saved: {prog}")
                self.after(0, self.refresh_workspace_tree)

        save_btn = ctk.CTkButton(toolbar, text="Save", width=70,
                                 fg_color="#2b5a2b", hover_color="#3a7a3a",
                                 command=_save)

        # ── Edit toggle ───────────────────────────────────────────────────────
        edit_btn = ctk.CTkButton(toolbar, text="Edit", width=70,
                                 fg_color="#3a3a3a", hover_color="#505050")

        def _toggle_edit():
            if not _editing[0]:
                txt.configure(state="normal")
                txt.focus_set()
                edit_btn.configure(text="Lock", fg_color="#6e2b28", hover_color="#8e3b38")
                save_btn.pack(side="right", padx=(0, 4))
                _editing[0] = True
            else:
                txt.configure(state="disabled")
                edit_btn.configure(text="Edit", fg_color="#3a3a3a", hover_color="#505050")
                save_btn.pack_forget()
                _editing[0] = False

        edit_btn.configure(command=_toggle_edit)

        # ── Show Diff (proposals only) ────────────────────────────────────────
        def _show_diff():
            if not prog:
                return
            profile = self.sidebar.system_var.get()
            original = ""
            for ft in ("Program", "Global Class", "Function Module"):
                original = workspace.read_code(profile, ft, prog)
                if original:
                    break
            if not original:
                self.write_log(f"[WS] Original not found for {prog} — cannot show diff.")
                return
            current = self.tabs_dict[name]["code"]
            diff_name = f"Diff: {prog}"
            if diff_name in self.editor.tabs_dict:
                self.editor.close_tab(diff_name)
            self.open_diff_tab(diff_name, original, current)

        # ── Pack toolbar (right-to-left) ──────────────────────────────────────
        ctk.CTkButton(toolbar, text="Copy", width=70,
                      command=lambda: self.copy_to_clipboard(
                          txt.get("0.0", "end-1c"))).pack(side="right")
        edit_btn.pack(side="right", padx=4)
        if prog:
            ctk.CTkButton(toolbar, text="Upload to SAP", width=120,
                          fg_color="#1a3a5a", hover_color="#2a5a8a",
                          command=lambda: self.open_transport_dialog(
                              prog, ftype,
                              lambda: txt.get("0.0", "end-1c"),
                              source_profile=source_profile or "")
                          ).pack(side="right", padx=4)
        if is_proposal:
            ctk.CTkButton(toolbar, text="Show Diff", width=90,
                          fg_color="#3a2a5a", hover_color="#5a4a8a",
                          command=_show_diff).pack(side="right", padx=4)
        if prog and ftype and not is_proposal:
            ctk.CTkButton(toolbar, text="Re-fetch from SAP", width=150,
                          fg_color="#3a3000", hover_color="#5a4a00",
                          command=lambda: self.refetch_object(name, prog, ftype)).pack(side="right", padx=4)

        self.editor.set_active(name)

    def refetch_object(self, tab_name, prog, ftype):
        """Close the current tab and force a live RFC fetch, overwriting workspace."""
        self.editor.close_tab(tab_name)
        self.write_log(f"[Re-fetch] Fetching {prog} from SAP...")
        if hasattr(self, "fetch_btn"):
            self.fetch_btn.configure(state="disabled", text="Working...")
        conn = self.get_current_conn()
        threading.Thread(target=self.run_fetch, args=(conn, prog, ftype, True), daemon=True).start()

    def open_ddic_tab(self, name, attrs, ftype="Table"):
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return
        content = self.editor.add_tab(name)
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # Header label + Re-fetch button
        hdr = ctk.CTkFrame(content, height=32, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        fields = attrs.get("FIELDS", [])
        prog_name = attrs.get("NAME", name.split(": ")[-1])
        ctk.CTkLabel(hdr, text=f"{prog_name}  —  {len(fields)} fields",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="Re-fetch from SAP", width=150,
                      fg_color="#3a3000", hover_color="#5a4a00",
                      command=lambda: self.refetch_object(name, prog_name, ftype)).pack(side="right")

        # Treeview container
        tree_frame = ctk.CTkFrame(content, fg_color="#1a1a1b")
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(2, 10))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("DDIC.Treeview",
                        background="#1a1a1b", foreground="#d4d4d4",
                        fieldbackground="#1a1a1b", rowheight=24,
                        font=("Consolas", 12))
        style.configure("DDIC.Treeview.Heading",
                        background="#2a2a2a", foreground="#569cd6",
                        font=("Consolas", 12, "bold"), relief="flat")
        style.map("DDIC.Treeview", background=[("selected", "#264f78")])

        cols = ("field", "type", "len", "description")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", style="DDIC.Treeview")
        tree.heading("field",       text="Field Name")
        tree.heading("type",        text="Type")
        tree.heading("len",         text="Length")
        tree.heading("description", text="Description")
        tree.column("field",       width=180, minwidth=100)
        tree.column("type",        width=80,  minwidth=60)
        tree.column("len",         width=70,  minwidth=50, anchor="center")
        tree.column("description", width=500, minwidth=200)

        tree.tag_configure("odd",  background="#1a1a1b")
        tree.tag_configure("even", background="#212123")

        sb_y = ttk.Scrollbar(tree_frame, orient="vertical",   command=tree.yview)
        sb_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        for i, f in enumerate(fields):
            tree.insert("", "end", tags=("even" if i % 2 == 0 else "odd",),
                        values=(f.get("Field", ""), f.get("Type", ""),
                                f.get("Len", ""), f.get("Description", "")))

        self.editor.set_active(name)

    def open_diff_tab(self, name, original_code, proposed_code):
        """Show a unified diff of original vs proposed code with green/red line highlighting."""
        if name in self.editor.tabs_dict:
            self.editor.set_active(name)
            return

        diff_lines = list(difflib.unified_diff(
            original_code.splitlines(keepends=True),
            proposed_code.splitlines(keepends=True),
            fromfile="original",
            tofile="proposed",
            lineterm="",
        ))

        content = self.editor.add_tab(name)
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # Toolbar
        toolbar = ctk.CTkFrame(content, height=30, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(4, 0))

        prog_name = name.replace("Proposal: ", "").replace("SAP update: ", "")
        ctk.CTkButton(
            toolbar, text="Open Full Code", width=130,
            fg_color="#2b5a2b", hover_color="#3a7a3a",
            command=lambda: self.open_code_tab(
                f"Full: {prog_name}", proposed_code,
                prog=prog_name, ftype="Program")
        ).pack(side="left")

        added   = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        ctk.CTkLabel(
            toolbar,
            text=f"+{added} added   -{removed} removed",
            font=ctk.CTkFont(size=12),
            text_color="#aaaaaa"
        ).pack(side="left", padx=12)

        txt = ctk.CTkTextbox(content, font=("Consolas", 13), wrap="none", fg_color="#1a1a1b")
        txt.tag_config("added",   foreground="#98c379", background="#1a3a1a")
        txt.tag_config("removed", foreground="#e06c75", background="#3a1a1a")
        txt.tag_config("header",  foreground="#569cd6")
        txt.tag_config("meta",    foreground="#666666")

        if not diff_lines:
            txt.insert("end", "No differences — proposed code is identical to original.", "meta")
        else:
            for line in diff_lines:
                if line.startswith("+++") or line.startswith("---"):
                    txt.insert("end", line + "\n", "meta")
                elif line.startswith("@@"):
                    txt.insert("end", line + "\n", "header")
                elif line.startswith("+"):
                    txt.insert("end", line + "\n", "added")
                elif line.startswith("-"):
                    txt.insert("end", line + "\n", "removed")
                else:
                    txt.insert("end", line + "\n")

        txt.configure(state="disabled")
        txt.grid(row=1, column=0, sticky="nsew", padx=1, pady=1)
        self.tabs_dict[name] = {"textbox": txt, "code": proposed_code}
        self.editor.set_active(name)

    def jump_to_line(self, line):
        if self.active_tab_name in self.tabs_dict:
            txt = self.tabs_dict[self.active_tab_name]["textbox"]
            txt.tag_remove("hl", "1.0", "end")
            pos = f"{line}.0"
            txt.see(pos); txt.tag_add("hl", pos, f"{line}.end")
            txt.tag_config("hl", background="#4a4a00")

    def send_chat(self):
        msg = self.chat_input.get().strip()
        if not msg: return
        self.chat_input.delete(0, "end")
        self.update_chat_log(f"You: {msg}")
        
        code = self.tabs_dict[self.active_tab_name]["code"] if self.active_tab_name in self.tabs_dict else ""
        context = f"--- ACTIVE: {self.active_tab_name} ---\n{code}\n\nUSER: {msg}"
        self.send_btn.configure(state="disabled")
        threading.Thread(target=self.run_ai, args=(context,), daemon=True).start()

    def run_ai(self, prompt):
        # Explicit routing to chat panel's response handler for FETCH and PROPOSAL parsing
        res = self.controller.send_chat(prompt)
        self.after(0, self.chat.on_chat_response, res)

    def open_suggestion_tab(self, name, scode):
        self.write_log(f"Suggestion for {name} generated.")
        self.open_code_tab(f"Proposal: {name}", scode)

    # ── Workspace Explorer ─────────────────────────────────────────────────────

    # Maps workspace subfolder name → (display label, ftype string)
    _WS_FOLDER_META = {
        "programs": ("📝  Programs",  "Program"),
        "prop":     ("📬  Proposals", "Program"),
    }

    def refresh_workspace_tree(self):
        """Reload the Workspace Explorer tree from disk."""
        if not hasattr(self, "ws_tree"):
            return
        tree = self.ws_tree
        for item in tree.get_children():
            tree.delete(item)

        profiles = workspace.list_profiles()
        if not profiles:
            tree.insert("", "end", text="(workspace is empty)")
            return

        for profile in sorted(profiles):
            projects = workspace.list_files(profile)
            if not projects:
                continue

            p_node = tree.insert("", "end", text=f"👤  {profile}", open=True)
            for proj_name in sorted(projects.keys()):
                proj_node = tree.insert(p_node, "end",
                                        text=f"📂  {proj_name}", open=True)
                subfolders = projects[proj_name]
                for folder in ("programs", "prop"):
                    fnames = subfolders.get(folder, [])
                    if not fnames:
                        continue
                    label, _ = self._WS_FOLDER_META[folder]
                    f_node = tree.insert(proj_node, "end",
                                         text=f"{label}  ({len(fnames)})",
                                         open=True)
                    for fname in fnames:
                        tree.insert(f_node, "end", text=fname,
                                    values=(profile, folder, fname, proj_name))

    def on_workspace_select(self, _event):
        """Open a workspace file when double-clicked in the Workspace Explorer."""
        if not hasattr(self, "ws_tree"):
            return
        sel = self.ws_tree.selection()
        if not sel:
            return
        vals = self.ws_tree.item(sel[0], "values")
        if not vals or len(vals) < 4:
            return   # clicked a group node, not a file

        profile, folder, filename, project = vals[0], vals[1], vals[2], vals[3]
        prog = os.path.splitext(filename)[0]   # e.g. ZPROGRAM

        if folder == "prop":
            code = workspace.read_file(profile, folder, filename, project=project)
            if code:
                self.open_code_tab(f"Proposal: {prog}", code, None, prog,
                                   "Program", source_profile=profile)
            else:
                self.write_log(f"[WS] Could not read {filename}")
        elif filename.endswith(".json"):
            fields = workspace.read_table_fields(profile, prog, project=project)
            if fields:
                attrs = {"NAME": prog, "FIELDS": fields}
                self.open_ddic_tab(f"Table: {prog}", attrs, "Table")
            else:
                self.write_log(f"[WS] Could not read {filename}")
        else:
            code = workspace.read_file(profile, folder, filename, project=project)
            if code:
                self.open_code_tab(f"Program: {prog}", code, None, prog,
                                   "Program", source_profile=profile)
            else:
                self.write_log(f"[WS] Could not read {filename}")

    def _poll_proposals(self):
        """Poll workspace PROP/ every 2 seconds. Opens new proposals as diff tabs."""
        profile = self.sidebar.system_var.get() if hasattr(self, "sidebar") else ""
        if profile:
            # Keep workspace tree in sync with disk (MCP server may have written new files)
            self.refresh_workspace_tree()

            for project, fname in workspace.scan_proposals(profile):
                key = f"{profile}/{project}/{fname}"
                if key not in self._watched_proposals:
                    self._watched_proposals.add(key)
                    name = os.path.splitext(fname)[0]
                    proposed = workspace.read_file(profile, "prop", fname, project=project)

                    # Find original: tabs_dict first (any tab prefix), then workspace
                    original = ""
                    for prefix in ("Program", "Object", "Global Class",
                                   "Function Module", "Function Group"):
                        original = self.tabs_dict.get(
                            f"{prefix}: {name}", {}).get("code", "")
                        if original:
                            break
                    if not original:
                        for ftype in ("Program", "Global Class", "Function Module"):
                            original = workspace.read_code(profile, ftype, name, project=project)
                            if original:
                                break

                    self.write_log(f"[WS] Proposal arrived: {project}/{fname}")
                    if original:
                        self.open_diff_tab(f"Proposal: {name}", original, proposed)
                    else:
                        self.open_code_tab(f"Proposal: {name}", proposed,
                                           prog=name, ftype="Program",
                                           source_profile=profile)
        self.after(2000, self._poll_proposals)

    def initialize_ai_from_env(self):
        key = os.getenv("GEMINI_API_KEY")
        if key: self.controller.initialize_ai(key)

    def write_log(self, text):
        self.logs_text.configure(state="normal")
        self.logs_text.insert("end", f">>> {text}\n")
        self.logs_text.see("end")
        self.logs_text.configure(state="disabled")
        self.update_idletasks() # Force UI refresh for synchronous feedback

    def update_chat_log(self, text):
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"\n{text}\n")
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    def copy_to_clipboard(self, text):
        self.clipboard_clear(); self.clipboard_append(text); self.write_log("Copied.")

    def reset_buttons(self):
        self.fetch_btn.configure(state="normal", text="Fetch")
        self.send_btn.configure(state="normal")

    # ── SAP Upload ─────────────────────────────────────────────────────────────

    def open_transport_dialog(self, prog: str, ftype: str, get_code_fn,
                              source_profile: str = ""):
        """
        Fetch open TRs in background, then show the transport selection dialog.
        get_code_fn: callable that returns the current code string (read from textbox).
        source_profile: the SAP profile the code was originally fetched from.
        """
        active_profile = self.sidebar.system_var.get()

        # ── System mismatch guard ─────────────────────────────────────────────
        if source_profile and source_profile != active_profile:
            answer = mbox.askyesno(
                "System Mismatch Warning",
                f"This code was fetched from:  {source_profile}\n"
                f"Active connection:             {active_profile}\n\n"
                f"Uploading to the wrong system can cause serious issues.\n\n"
                f"Are you sure you want to upload to '{active_profile}'?",
                icon="warning",
            )
            if not answer:
                return

        self.write_log(f"[Upload] Fetching open transport requests...")
        conn = self.get_current_conn()
        user = self.sap_user.get().strip()

        def _fetch_trs():
            trs = self.controller.list_transports(conn, user)
            self.after(0, self._show_transport_dialog, prog, ftype, get_code_fn, trs, conn)

        threading.Thread(target=_fetch_trs, daemon=True).start()

    def _show_transport_dialog(self, prog, ftype, get_code_fn, trs, conn):
        if not trs:
            mbox.showwarning("No open TRs",
                             "No open workbench transport requests found for this user.")
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title(f"Upload to SAP: {prog}")
        dlg.geometry("520x340")
        dlg.resizable(False, False)
        dlg.grab_set()

        ctk.CTkLabel(dlg, text="Select Transport Request:",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=20, pady=(16, 4))

        # Scrollable list of TRs
        list_frame = ctk.CTkScrollableFrame(dlg, height=180)
        list_frame.pack(fill="x", padx=20, pady=4)

        selected_tr = ctk.StringVar(value=trs[0]["TRKORR"] if trs else "")

        for tr in trs:
            label = f"{tr['TRKORR']}  —  {tr['AS4TEXT'] or '(no description)'}  [{tr['AS4USER']}]"
            ctk.CTkRadioButton(list_frame, text=label, variable=selected_tr,
                               value=tr["TRKORR"],
                               font=ctk.CTkFont(family="Consolas", size=11)).pack(
                anchor="w", padx=8, pady=3)

        # Buttons
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(12, 16))

        def _do_upload():
            trkorr = selected_tr.get()
            if not trkorr:
                mbox.showwarning("Select TR", "Please select a transport request.")
                return
            code = get_code_fn()
            dlg.destroy()
            self.write_log(f"[Upload] {prog} -> {trkorr} ...")
            threading.Thread(
                target=self._run_upload, args=(conn, prog, ftype, code, trkorr),
                daemon=True).start()

        def _do_local():
            """Write directly without TR — only works for $TMP (local) objects."""
            code = get_code_fn()
            dlg.destroy()
            self.write_log(f"[Upload] {prog} -> local (no TR) ...")
            threading.Thread(
                target=self._run_upload,
                args=(conn, prog, ftype, code, "", True),  # skip_tr=True
                daemon=True).start()

        ctk.CTkButton(btn_row, text="Cancel", width=90, fg_color="#4a4a4a",
                      command=dlg.destroy).pack(side="left")
        ctk.CTkButton(btn_row, text="Save Locally\n(no TR)", width=110,
                      fg_color="#3a3000", hover_color="#5a4a00",
                      command=_do_local).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Upload to SAP", width=130,
                      fg_color="#2b5a2b", hover_color="#3a7a3a",
                      command=_do_upload).pack(side="right")

    def _run_upload(self, conn, prog, _ftype, code, trkorr, skip_tr=False):
        # Step 1: syntax check
        chk, chk_msg = self.controller.check_syntax(conn, prog, code)
        if chk is False:
            # Hard errors — hand off to main thread to ask user, then stop this thread
            self.after(0, self.write_log, f"[Upload] Syntax errors in {prog}:\n{chk_msg}")
            self.after(0, self._ask_syntax_error,
                       conn, prog, _ftype, code, trkorr, skip_tr, chk_msg)
            return
        if chk is True and chk_msg:
            self.after(0, self.write_log, f"[Upload] Syntax warnings:\n{chk_msg}")

        # Step 2: write to SAP
        self._do_write(conn, prog, _ftype, code, trkorr, skip_tr)

    def _ask_syntax_error(self, conn, prog, ftype, code, trkorr, skip_tr, error_msg):
        answer = mbox.askyesno(
            "Syntax Errors Found",
            f"{prog} has syntax errors:\n\n{error_msg}\n\nUpload anyway?",
            icon="warning",
        )
        if answer:
            threading.Thread(
                target=self._do_write,
                args=(conn, prog, ftype, code, trkorr, skip_tr),
                daemon=True,
            ).start()

    def _do_write(self, conn, prog, _ftype, code, trkorr, skip_tr=False):
        ok, err = self.controller.upload_program(conn, prog, code, trkorr,
                                                  skip_tr_assign=skip_tr)
        if ok:
            self.after(0, self.write_log,
                       f"[Upload] SUCCESS: {prog} uploaded to {trkorr}")
            self.after(0, mbox.showinfo, "Upload OK",
                       f"{prog} successfully uploaded to SAP.\nTR: {trkorr}")
        elif err.startswith("TR_ASSIGN_FAILED:"):
            real_err = err[len("TR_ASSIGN_FAILED:"):]
            self.after(0, self.write_log, f"[Upload] TR assign failed: {real_err}")
            self.after(0, self._ask_skip_tr_assign, conn, prog, _ftype, code,
                       trkorr, real_err)
        else:
            self.after(0, self.write_log, f"[Upload] FAILED: {err}")
            self.after(0, mbox.showerror, "Upload Failed", err)

    def _ask_skip_tr_assign(self, conn, prog, ftype, code, trkorr, err):
        answer = mbox.askyesno(
            "TR Assignment Failed",
            f"Could not assign {prog} to transport {trkorr}:\n{err}\n\n"
            f"This can happen with includes or objects already locked.\n\n"
            f"Write the source code anyway without TR assignment?",
            icon="warning",
        )
        if answer:
            threading.Thread(
                target=self._do_write,
                args=(conn, prog, ftype, code, trkorr, True),
                daemon=True,
            ).start()

    # ── GitHub Sync ────────────────────────────────────────────────────────────

    def github_push(self):
        profile = self.sidebar.system_var.get()
        if not profile:
            mbox.showwarning("GitHub", "Select a profile first.")
            return
        self.write_log(f"[GitHub] Pushing {profile} to GitHub...")

        def _push():
            ok, msg = github_sync.push_workspace(profile)
            self.after(0, self.write_log, f"[GitHub] {msg}")
            if ok:
                self.after(0, mbox.showinfo,  "GitHub Push", msg)
            else:
                self.after(0, mbox.showerror, "GitHub Push Failed", msg)

        threading.Thread(target=_push, daemon=True).start()

    def github_pull(self):
        profile = self.sidebar.system_var.get()
        if not profile:
            mbox.showwarning("GitHub", "Select a profile first.")
            return
        self.write_log(f"[GitHub] Pulling {profile} from GitHub...")

        def _pull():
            ok, msg = github_sync.pull_workspace(profile)
            self.after(0, self.write_log, f"[GitHub] {msg}")
            if ok:
                self.after(0, self.refresh_workspace_tree)
                self.after(0, mbox.showinfo,  "GitHub Pull", msg)
            else:
                self.after(0, mbox.showerror, "GitHub Pull Failed", msg)

        threading.Thread(target=_pull, daemon=True).start()

if __name__ == "__main__":
    app = App()
    app.mainloop()
