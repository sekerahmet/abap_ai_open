"""
MainWindow — the ABAP AI IDE (PySide6).

Layout:  toolbar (profile · ⚙ · type · name · Fetch · Open file · Paste code)
         left dock  = Claude (account, usage, sessions)
         centre     = tabs (System Logs, code, tables, data, diff, Claude chats)
         right dock = SAP Objects | Workspace
         status bar = profile/host · last log line · branch

Threading: every RFC / git / disk-heavy call runs in a daemon thread and
reports back through self.ui.call(fn, …) (queued Qt signal → GUI thread).
Nothing here ever writes to SAP.
"""

import os
import re
import json
import time
import difflib
import subprocess

from PySide6.QtCore import Qt, QTimer, QByteArray, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QGuiApplication
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QComboBox, QPushButton, QToolBar, QTabWidget, QDockWidget, QMessageBox,
                               QPlainTextEdit, QInputDialog, QFileDialog, QApplication, QSizePolicy,
                               QToolButton, QCheckBox)

from utils.env_loader import load_robust_env
load_robust_env()

from core.controller import AnalysisController
from core.claude_runner import ClaudeSession, find_claude, auth_info, transcript_path
from utils.parser import ABAPParser
from utils import workspace
from utils import github_sync
from ui.bridge import Bridge, run_bg
from ui.dialogs import ConnectionDialog, PasteCodeDialog, SetupCheckDialog, CONN_FIELDS
from core import env_check
from ui.widgets.code_editor import CodeView
from ui.widgets.tables import fields_table, data_table
from ui.panels.sap_tree import SapObjectsPanel, TADIR_META
from ui.panels.workspace_tree import WorkspacePanel
from ui.panels.claude_side import ClaudeSidePanel
from ui.panels.claude_chat import ClaudeChatTab

_APP_DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ABAP_AI")
os.makedirs(_APP_DATA_DIR, exist_ok=True)
SYSTEMS_FILE = os.path.join(_APP_DATA_DIR, "systems.json")
UI_STATE_FILE = os.path.join(_APP_DATA_DIR, "ui_state.json")
CLAUDE_SESSIONS_FILE = os.path.join(_APP_DATA_DIR, "claude_sessions.json")

LOCAL_PROFILE = workspace.LOCAL_PROFILE
_ABAP_EXTS = (".abap", ".prog", ".clas", ".fugr", ".incl")
_FILE_FILTER = "ABAP / text (*.abap *.txt *.prog *.clas *.fugr *.incl *.md *.json);;All files (*)"
OBJECT_TYPES = ["Program", "Table", "Structure", "Function Module", "Global Class", "Table Data"]
_CONN_KEYS = [k for k, _, _ in CONN_FIELDS]
_DDIC_TYPES = ("Table", "Structure")
_CODE_TYPES = ("Program", "Global Class", "Function Module")
_TAB_PREFIXES = ("Program", "Global Class", "Function Module", "Table", "Proposal", "Diff")
_CATEGORY_FTYPE = {"DICT": "Table", "CLASS": "Global Class", "PROG": "Program", "FUNC": "Function Module"}
_CONTEXT_INLINE_LIMIT = 20000
from ui.theme import BAD as T_BAD, WARN as T_WARN, GOOD as T_GOOD
_TAB_GLYPH = {"Program": "▤", "Global Class": "◆", "Function Module": "ƒ", "Table": "▦", "Data": "▥",
              "Proposal": "✉", "Diff": "±", "Claude": "✦", "System Logs": "≡", "File": "▢"}


def _tab_name(ftype: str, name: str) -> str:
    return f"{'Table' if ftype in _DDIC_TYPES else ftype}: {name.upper()}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ABAP AI IDE")
        self.ui = Bridge()
        self.controller = AnalysisController()
        self.systems_data = self._load_systems()
        self.tabs = {}                     # name → dict(widget, kind, view, code, prog, ftype, source_profile)
        self.current_main_program = ""
        self._watched_proposals = {}
        self._ws_snapshot = None
        self._ws_refreshing = False
        self._ws_refresh_pending = False
        self._ui_state = self._load_ui_state()

        self._build_toolbar()
        self._build_center()
        self._build_docks()
        self._build_statusbar()
        self._restore_ui_state()

        names = self.sap_profiles()
        self.on_profile_changed(names[0] if names else LOCAL_PROFILE)
        self._ws_snapshot = workspace.snapshot()
        self.refresh_workspace_tree()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_proposals)
        self._poll.start(2000)
        self.side.set_sessions(self.list_claude_sessions(self.active_profile()))
        if not names:
            self.write_log("No SAP profile yet — press ⚙ to create one, or work locally with Open file / Paste code.")
        self._setup_results = None
        self._setup_dlg = None
        self._setup_running = False
        if self.setup_startup_flag():
            QTimer.singleShot(1200, lambda: self.run_setup_check(show=False, auto=True))

    # ══════════════════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════════════════

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(190)
        self.profile_box.setStyleSheet("font-weight: bold;")
        self.profile_box.currentTextChanged.connect(self._profile_box_changed)
        tb.addWidget(self.profile_box)
        b_cfg = QPushButton("⚙"); b_cfg.setObjectName("flat"); b_cfg.setToolTip("Connection profiles")
        b_cfg.clicked.connect(lambda: ConnectionDialog(self, self).exec())
        tb.addWidget(b_cfg)
        self.host_lbl = QLabel(""); self.host_lbl.setObjectName("dim")
        tb.addWidget(self.host_lbl)
        tb.addSeparator()

        self.type_box = QComboBox(); self.type_box.addItems(OBJECT_TYPES)
        tb.addWidget(self.type_box)
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText("Object name…  (Enter = Fetch)")
        self.name_edit.setMinimumWidth(320)
        self.name_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.name_edit.returnPressed.connect(self.fetch_program_flow)
        tb.addWidget(self.name_edit)
        self.fetch_btn = QPushButton("Fetch"); self.fetch_btn.setObjectName("accent")
        self.fetch_btn.clicked.connect(self.fetch_program_flow)
        tb.addWidget(self.fetch_btn)
        tb.addSeparator()
        b_open = QPushButton("Open file…"); b_open.clicked.connect(self.open_local_file); tb.addWidget(b_open)
        b_paste = QPushButton("Paste code"); b_paste.clicked.connect(self.paste_code); tb.addWidget(b_paste)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.setup_btn = QPushButton("Setup …"); self.setup_btn.setObjectName("flat")
        self.setup_btn.setToolTip("Environment check: SAP RFC SDK, Claude Code CLI, Python/MCP, Git, .env")
        self.setup_btn.clicked.connect(self.open_setup_dialog)
        tb.addWidget(self.setup_btn)
        self._refill_profiles()

    def _build_center(self):
        self.tabw = QTabWidget()
        self.tabw.setTabsClosable(False)          # own ✕ buttons (see _add_tab) — the stock icon is invisible on dark themes
        self.tabw.setMovable(True)
        self.tabw.setDocumentMode(True)
        self.tabw.currentChanged.connect(lambda _i: None)
        self.setCentralWidget(self.tabw)
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setObjectName("code")
        self.logs.setMaximumBlockCount(5000)
        self._add_tab("System Logs", self.logs, closable=False, kind="logs")

    def _build_docks(self):
        self.sap_panel = SapObjectsPanel()
        self.sap_panel.jump.connect(self.jump_to_line)
        self.sap_panel.open_object.connect(self.open_from_tree)
        d1 = QDockWidget("SAP OBJECTS", self); d1.setObjectName("dock_sap"); d1.setWidget(self.sap_panel)
        self.ws_panel = WorkspacePanel()
        self.ws_panel.push.connect(self.github_push)
        self.ws_panel.pull.connect(self.github_pull)
        self.ws_panel.refresh.connect(self.refresh_workspace_tree)
        self.ws_panel.open_file.connect(self.open_workspace_file)
        self.ws_panel.reveal.connect(self.reveal_in_explorer)
        self.ws_panel.delete.connect(self.delete_workspace_node)
        self.ws_panel.open_root.connect(lambda: self.reveal_in_explorer(("_profile", self.active_profile(), "", "", "")))
        self.ws_panel.new_folder.connect(self.ws_new_folder)
        self.ws_panel.new_file.connect(self.ws_new_file)
        self.ws_panel.import_here.connect(self.ws_import_here)
        self.ws_panel.rename.connect(self.ws_rename)
        self.ws_panel.files_dropped.connect(lambda d, paths: self._import_local_files(paths, d[2]))
        d2 = QDockWidget("WORKSPACE", self); d2.setObjectName("dock_ws"); d2.setWidget(self.ws_panel)
        for d in (d1, d2):
            d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetClosable)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        self.tabifyDockWidget(d1, d2)
        d1.raise_()

        self.side = ClaudeSidePanel()
        self.side.new_session.connect(lambda: self.open_claude_tab())
        self.side.open_session.connect(lambda sid, title: self.open_claude_tab(sid, title))
        self.side.forget_session.connect(self.forget_claude_session)
        self.side.rename_session.connect(self.rename_claude_session)
        d3 = QDockWidget("CLAUDE", self); d3.setObjectName("dock_claude"); d3.setWidget(self.side)
        d3.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d3)
        self._docks = (d1, d2, d3)

        view = self.menuBar().addMenu("View")
        for d in self._docks:
            view.addAction(d.toggleViewAction())
        self.menuBar().setVisible(False)          # docks are toggled via the View shortcut below
        QShortcut(QKeySequence("Ctrl+B"), self, activated=lambda: d3.setVisible(not d3.isVisible()))
        QShortcut(QKeySequence("Ctrl+Shift+E"), self, activated=lambda: (d2.setVisible(True), d2.raise_()))
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=lambda: (d1.setVisible(True), d1.raise_()))
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self._close_tab_index(self.tabw.currentIndex()))

    def _build_statusbar(self):
        sb = self.statusBar()
        self.st_conn = QLabel("○  No profile"); self.st_msg = QLabel("Ready"); self.st_right = QLabel("read-only RFC")
        sb.addWidget(self.st_conn); sb.addWidget(self.st_msg, 1); sb.addPermanentWidget(self.st_right)

    # ── window state ──────────────────────────────────────────────────────────
    def _load_ui_state(self) -> dict:
        try:
            with open(UI_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _restore_ui_state(self):
        st = self._ui_state
        screen = QGuiApplication.primaryScreen().availableGeometry()
        w, h = min(1600, int(screen.width() * 0.92)), min(950, int(screen.height() * 0.9))
        self.resize(w, h)
        self.move(screen.x() + (screen.width() - w) // 2, screen.y() + (screen.height() - h) // 2)
        try:
            if st.get("geometry"):
                self.restoreGeometry(QByteArray.fromBase64(st["geometry"].encode()))
            if st.get("state"):
                self.restoreState(QByteArray.fromBase64(st["state"].encode()))
        except Exception:
            pass
        self.setMinimumSize(1000, 600)

    def closeEvent(self, e):
        try:
            st = {"geometry": bytes(self.saveGeometry().toBase64()).decode(),
                  "state": bytes(self.saveState().toBase64()).decode(),
                  "setup_check_startup": self.setup_startup_flag()}
            with open(UI_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except Exception:
            pass
        for name, entry in list(self.tabs.items()):
            if entry.get("kind") == "claude":
                entry["widget"].session.stop()
        super().closeEvent(e)

    # ── setup check ───────────────────────────────────────────────────────────
    def setup_startup_flag(self) -> bool:
        return bool(self._ui_state.get("setup_check_startup", True))

    def set_setup_startup_flag(self, on: bool):
        self._ui_state["setup_check_startup"] = bool(on)

    def run_setup_check(self, show=False, auto=False):
        if self._setup_running:
            return
        self._setup_running = True
        self.setup_btn.setText("Setup …")
        has_sap = bool(self.sap_profiles())
        run_bg(lambda: self.ui.call(self._setup_done, env_check.run_all(has_sap), show, auto))

    def _setup_done(self, results, show, auto):
        self._setup_running = False
        self._setup_results = results
        n_err, n_warn = env_check.summary(results)
        if n_err:
            self.setup_btn.setText(f"Setup ✗ {n_err}"); self.setup_btn.setStyleSheet(f"color: {T_BAD};")
        elif n_warn:
            self.setup_btn.setText(f"Setup ⚠ {n_warn}"); self.setup_btn.setStyleSheet(f"color: {T_WARN};")
        else:
            self.setup_btn.setText("Setup ✓"); self.setup_btn.setStyleSheet(f"color: {T_GOOD};")
        problems = [f"{r['label']}: {r['status']}" for r in results if r["level"] in ("warn", "error")]
        self.write_log("[Setup] " + ("all good" if not problems else " · ".join(problems)))
        if self._setup_dlg is not None:
            self._setup_dlg.set_results(results)
        if show or (auto and n_err):
            self.open_setup_dialog()

    def open_setup_dialog(self):
        if self._setup_dlg is None:
            self._setup_dlg = SetupCheckDialog(self, self._setup_results, self)
            self._setup_dlg.finished.connect(lambda _r: setattr(self, "_setup_dlg", None))
            if self._setup_results is None:
                self.run_setup_check(show=False)
        self._setup_dlg.show(); self._setup_dlg.raise_(); self._setup_dlg.activateWindow()

    # ══════════════════════════════════════════════════════════════════════════
    # Profiles
    # ══════════════════════════════════════════════════════════════════════════

    def _load_systems(self) -> dict:
        try:
            with open(SYSTEMS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _flush_systems(self):
        with open(SYSTEMS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.systems_data, f, indent=4, ensure_ascii=False)

    def sap_profiles(self) -> list:
        return list(self.systems_data.keys())

    def active_profile(self) -> str:
        return self.profile_box.currentText()

    def is_local(self) -> bool:
        return self.active_profile() == LOCAL_PROFILE

    def _refill_profiles(self, select: str = None):
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        self.profile_box.addItems(self.sap_profiles() + [LOCAL_PROFILE])
        if select:
            self.profile_box.setCurrentText(select)
        self.profile_box.blockSignals(False)

    def _profile_box_changed(self, name):
        if name:
            self.on_profile_changed(name)

    def on_profile_changed(self, name: str):
        if self.profile_box.currentText() != name:
            self.profile_box.blockSignals(True); self.profile_box.setCurrentText(name); self.profile_box.blockSignals(False)
        self.current_main_program = ""
        self.sap_panel.populate({}, {}, "Discovered Objects")
        self._seed_proposals(name)
        if name in self.systems_data:
            d = self.systems_data[name]
            self.st_conn.setText(f"●  {name}   {d.get('ashost', '')}")
            self.host_lbl.setText(f"{d.get('ashost', '')} · client {d.get('client', '')} · {d.get('user', '')}")
            self.fetch_btn.setEnabled(True); self.type_box.setEnabled(True); self.name_edit.setEnabled(True)
        else:
            self.st_conn.setText("○  Local — no SAP connection")
            self.host_lbl.setText("local files only")
            self.fetch_btn.setEnabled(False); self.type_box.setEnabled(False); self.name_edit.setEnabled(False)
        self.write_log(f"Switched to profile: {name}")
        self.refresh_workspace_tree()
        self.side.set_sessions(self.list_claude_sessions(name))

    def save_profile(self, name: str, data: dict):
        data = dict(data)
        if data.get("router"):
            data["saprouter"] = data["router"]
        self.systems_data[name] = data
        self._flush_systems()
        self._refill_profiles(select=name)
        self.on_profile_changed(name)
        self.write_log(f"Profile '{name}' saved.")

    def delete_profile(self, name: str):
        if name not in self.systems_data:
            return
        del self.systems_data[name]
        self._flush_systems()
        names = self.sap_profiles()
        self._refill_profiles(select=names[0] if names else LOCAL_PROFILE)
        self.on_profile_changed(names[0] if names else LOCAL_PROFILE)

    def get_current_conn(self) -> dict:
        data = self.systems_data.get(self.active_profile(), {})
        conn = {}
        for k in _CONN_KEYS:
            val = str(data.get(k) or "").strip()
            if val:
                conn["saprouter" if k == "router" else k] = val
        return conn

    # ══════════════════════════════════════════════════════════════════════════
    # Tabs
    # ══════════════════════════════════════════════════════════════════════════

    def _add_tab(self, name: str, widget: QWidget, closable=True, kind="code", **meta):
        glyph = _TAB_GLYPH.get(name.split(":", 1)[0], "")
        idx = self.tabw.addTab(widget, f"{glyph} {name}" if glyph else name)
        self.tabw.setTabToolTip(idx, name)
        if closable:
            btn = QToolButton(); btn.setObjectName("tabclose"); btn.setText("✕")
            btn.setFixedSize(18, 18); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip("Close tab  (Ctrl+W)")
            btn.clicked.connect(lambda _c=False, w=widget: self._close_tab_index(self.tabw.indexOf(w)))
            self.tabw.tabBar().setTabButton(idx, self.tabw.tabBar().ButtonPosition.RightSide, btn)
        self.tabs[name] = {"widget": widget, "kind": kind, **meta}
        self.tabw.setCurrentIndex(idx)
        return widget

    def _tab_index(self, name: str) -> int:
        entry = self.tabs.get(name)
        return self.tabw.indexOf(entry["widget"]) if entry else -1

    def has_tab(self, name: str) -> bool:
        return name in self.tabs

    def activate_tab(self, name: str) -> bool:
        i = self._tab_index(name)
        if i >= 0:
            self.tabw.setCurrentIndex(i)
            return True
        return False

    def close_tab(self, name: str):
        i = self._tab_index(name)
        if i >= 0:
            self._close_tab_index(i)

    def _close_tab_index(self, i: int):
        w = self.tabw.widget(i)
        name = next((n for n, e in self.tabs.items() if e["widget"] is w), None)
        if name is None or self.tabs[name].get("kind") == "logs":
            return
        if self.tabs[name].get("kind") == "claude":
            w.session.stop()
        self.tabw.removeTab(i)
        self.tabs.pop(name, None)
        w.deleteLater()

    def active_tab_name(self) -> str:
        w = self.tabw.currentWidget()
        return next((n for n, e in self.tabs.items() if e["widget"] is w), "")

    def _close_tabs_for_files(self, filenames):
        for fname in filenames:
            stem = os.path.splitext(fname)[0].upper()
            for prefix in _TAB_PREFIXES:
                self.close_tab(f"{prefix}: {stem}")

    # ── code tab ──────────────────────────────────────────────────────────────
    def open_code_tab(self, name, code, prog=None, ftype=None, source_profile=None, is_proposal=False,
                      rel=None, mode="abap"):
        """rel = profile-relative path of the file (free-form workspaces); for a proposal tab it is the
        path of the *target* file (the proposal itself lives in <dir>/proposals/)."""
        if self.activate_tab(name):
            return
        page = QWidget()
        lay = QVBoxLayout(page); lay.setContentsMargins(8, 6, 8, 8); lay.setSpacing(6)
        bar = QHBoxLayout()
        src = QLabel(f"{source_profile}  ·  {rel}" if rel else (source_profile or ""))
        src.setObjectName("dim"); bar.addWidget(src); bar.addStretch(1)
        view = CodeView(code, mode=mode)
        editing = {"on": False}

        def _save():
            current = view.get()
            self.tabs[name]["code"] = current
            profile = source_profile or self.active_profile()
            if not (profile and prog):
                self.write_log("[WS] Nothing to save (no profile / object name).")
                return
            if rel and is_proposal:
                path = workspace.write_proposal(profile, prog, current, path=rel)
                self._mark_proposal_seen(profile, path)
                self.close_tab(f"Diff: {rel}")
                self.write_log(f"[WS] Proposal updated: {path}")
            elif rel:
                path = workspace.write_rel(profile, rel, current)
                self.write_log(f"[WS] Saved: {path}")
            elif is_proposal:
                path = workspace.write_proposal(profile, prog, current)
                self._mark_proposal_seen(profile, path)
                self.close_tab(f"Diff: {prog}")
                self.write_log(f"[WS] Proposal updated: {path}")
            else:
                path = workspace.save_code(profile, ftype or "Program", prog, current)
                self.write_log(f"[WS] Saved: {path}" if path else f"[WS] {prog} is a standard object — not saved.")
            view.editor.document().setModified(False)

        b_save = QPushButton("Save"); b_save.setObjectName("ok"); b_save.clicked.connect(_save); b_save.hide()
        b_edit = QPushButton("Edit")

        def _toggle():
            editing["on"] = not editing["on"]
            view.set_editable(editing["on"])
            b_edit.setText("Lock" if editing["on"] else "Edit")
            b_save.setVisible(editing["on"])
        b_edit.clicked.connect(_toggle)

        if prog and ftype and not is_proposal and not rel and not self.is_local() and source_profile != LOCAL_PROFILE:
            b_ref = QPushButton("Re-fetch from SAP"); b_ref.clicked.connect(lambda: self.refetch_object(name, prog, ftype))
            bar.addWidget(b_ref)
        if is_proposal and prog:
            b_apply = QPushButton(f"Apply → {rel or prog}"); b_apply.setObjectName("ok")
            b_apply.setToolTip("Write this proposal over the workspace file (never to SAP)")
            b_apply.clicked.connect(lambda: self.apply_proposal(source_profile or self.active_profile(), prog, view.get(), rel))
            bar.addWidget(b_apply)
            b_diff = QPushButton("Show diff"); b_diff.setObjectName("claude")
            def _diff():
                profile = source_profile or self.active_profile()
                if rel:
                    original = workspace.read_rel(profile, rel)
                else:
                    original = workspace.read_code(profile, "Program", prog) if profile else ""
                if not original:
                    self.write_log(f"[WS] Original source for {rel or prog} not in workspace — cannot diff.")
                    return
                key = rel or prog
                self.close_tab(f"Diff: {key}")
                self.open_diff_tab(f"Diff: {key}", original, view.get(), prog, profile, rel=rel)
            b_diff.clicked.connect(_diff)
            bar.addWidget(b_diff)
        b_find = QPushButton("Find  Ctrl+F"); b_find.clicked.connect(view.show_find); bar.addWidget(b_find)
        bar.addWidget(b_edit); bar.addWidget(b_save)
        b_copy = QPushButton("Copy"); b_copy.clicked.connect(lambda: self.copy_to_clipboard(view.get())); bar.addWidget(b_copy)
        lay.addLayout(bar)
        lay.addWidget(view, 1)
        self._add_tab(name, page, kind="code", view=view, code=code, prog=prog, ftype=ftype,
                      source_profile=source_profile, rel=rel)

    def refetch_object(self, tab_name, prog, ftype):
        self.close_tab(tab_name)
        self.write_log(f"[Re-fetch] {ftype} {prog} from SAP...")
        self.fetch_btn.setEnabled(False); self.fetch_btn.setText("Working…")
        run_bg(self.run_fetch, self.get_current_conn(), prog, ftype, self.active_profile(), True, "", ftype == "Program")

    def open_ddic_tab(self, name, attrs, ftype="Table"):
        if self.activate_tab(name):
            return
        page = QWidget(); lay = QVBoxLayout(page); lay.setContentsMargins(8, 6, 8, 8)
        fields = attrs.get("FIELDS", [])
        obj = attrs.get("NAME", name.split(": ")[-1]).upper()
        bar = QHBoxLayout()
        lbl = QLabel(f"{obj}  —  {len(fields)} fields"); lbl.setStyleSheet("font-weight: bold;")
        bar.addWidget(lbl); bar.addStretch(1)
        if not self.is_local():
            b = QPushButton("Re-fetch from SAP"); b.clicked.connect(lambda: self.refetch_object(name, obj, ftype)); bar.addWidget(b)
        lay.addLayout(bar)
        lay.addWidget(fields_table(fields), 1)
        self._add_tab(name, page, kind="ddic")

    def open_data_tab(self, name, columns, rows):
        if self.activate_tab(name):
            return
        page = QWidget(); lay = QVBoxLayout(page); lay.setContentsMargins(8, 6, 8, 8)
        table_name = name.split(": ", 1)[-1].split(" [")[0]
        lbl = QLabel(f"{table_name}  —  {len(rows)} rows × {len(columns)} columns"); lbl.setStyleSheet("font-weight: bold;")
        lay.addWidget(lbl)
        lay.addWidget(data_table(columns, rows), 1)
        self._add_tab(name, page, kind="data")

    def open_diff_tab(self, name, original, proposed, prog, profile=None, rel=None):
        if self.activate_tab(name):
            return
        lines = list(difflib.unified_diff(original.splitlines(), proposed.splitlines(),
                                          fromfile="original", tofile="proposed", lineterm=""))
        page = QWidget(); lay = QVBoxLayout(page); lay.setContentsMargins(8, 6, 8, 8)
        bar = QHBoxLayout()
        b_open = QPushButton("Open proposal code"); b_open.setObjectName("claude")
        b_open.clicked.connect(lambda: self.open_code_tab(f"Proposal: {rel or prog}", proposed, prog, "Program", profile,
                                                          is_proposal=True, rel=rel))
        b_apply = QPushButton("Apply proposal"); b_apply.setObjectName("ok")
        b_apply.setToolTip("Overwrite the workspace file with the proposed code (never written to SAP)")
        b_apply.clicked.connect(lambda: self.apply_proposal(profile or self.active_profile(), prog, proposed, rel))
        bar.addWidget(b_apply)
        if rel:
            lbl = QLabel(rel); lbl.setObjectName("dim"); bar.addWidget(lbl)
        bar.addWidget(b_open)
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        s = QLabel(f"+{added} added   -{removed} removed"); s.setObjectName("muted"); bar.addWidget(s); bar.addStretch(1)
        lay.addLayout(bar)
        view = CodeView("\n".join(lines) if lines else "No differences — proposed code is identical to original.", mode="diff")
        lay.addWidget(view, 1)
        self._add_tab(name, page, kind="diff", view=view, code=proposed, prog=prog, ftype="Program",
                      source_profile=profile, rel=rel)

    def jump_to_line(self, line: int):
        target = f"Program: {self.current_main_program}" if self.current_main_program else ""
        if target not in self.tabs:
            target = self.active_tab_name()
        entry = self.tabs.get(target)
        if not entry or not entry.get("view"):
            return
        self.activate_tab(target)
        entry["view"].goto(line)

    # ══════════════════════════════════════════════════════════════════════════
    # Fetch flow
    # ══════════════════════════════════════════════════════════════════════════

    def fetch_program_flow(self):
        if self.is_local():
            return
        program = self.name_edit.text().strip().upper()
        if not program:
            return
        ftype = self.type_box.currentText()
        where = ""
        if ftype == "Table Data":
            where, ok = QInputDialog.getText(self, "WHERE clause",
                                             f"WHERE clause for {program} (empty = all rows, max 200):")
            if not ok:
                return
            where = where.strip()
        self.write_log(f"Fetching {ftype} {program}...")
        self.fetch_btn.setEnabled(False); self.fetch_btn.setText("Working…")
        run_bg(self.run_fetch, self.get_current_conn(), program, ftype, self.active_profile(), False, where, False)

    def reset_buttons(self):
        self.fetch_btn.setEnabled(not self.is_local()); self.fetch_btn.setText("Fetch")

    def _log_dial(self, conn):
        printable = {k: ("********" if k == "passwd" else v) for k, v in conn.items()}
        self.ui.call(self.write_log, f"[RFC] Connecting: {printable}")

    def run_fetch(self, conn, prog, ftype, profile, force=False, where_clause="", force_sub=False):
        prog = prog.upper()
        tab = _tab_name(ftype, prog)
        ui = self.ui
        try:
            if ftype == "Table Data":
                self._log_dial(conn)
                columns, rows = self.controller.fetch_table_data(conn, prog, where_clause)
                if columns is None:
                    ui.call(self.write_log, f"FAILED: {prog} - {rows}")
                    return
                title = f"Data: {prog}" + (f" [{where_clause[:30]}]" if where_clause else "")
                ui.call(self.write_log, f"SUCCESS: {prog} — {len(rows)} rows.")
                ui.call(self.open_data_tab, title, columns, rows)
                return
            if ftype == "Program":
                self.current_main_program = prog
            if not force and profile:
                if ftype in _DDIC_TYPES:
                    fields = workspace.read_table_fields(profile, prog)
                    if fields:
                        ui.call(self.write_log, f"[WS] Loaded from workspace: {prog}")
                        ui.call(self.open_ddic_tab, tab, {"NAME": prog, "FIELDS": fields}, ftype)
                        return
                else:
                    code = workspace.read_code(profile, ftype, prog)
                    if code:
                        ui.call(self.write_log, f"[WS] Loaded from workspace: {prog}")
                        ui.call(self.open_code_tab, tab, code, prog, ftype, profile)
                        if ftype == "Program":
                            ui.call(self._populate_tree_offline, profile, prog, ABAPParser.get_objects(code))
                        return
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
                ui.call(self.write_log, f"FAILED: {prog} - {attrs if isinstance(attrs, str) else 'Object not found.'}")
                return
            ui.call(self.write_log, f"SUCCESS: {prog} loaded from SAP.")
            if ftype in _DDIC_TYPES:
                ui.call(self.open_ddic_tab, tab, attrs, ftype)
                saved = workspace.save_table(profile, prog, attrs.get("FIELDS", [])) if profile else ""
            else:
                ui.call(self.open_code_tab, tab, code, prog, ftype, profile)
                saved = workspace.save_code(profile, ftype, prog, code) if profile else ""
            if saved:
                ui.call(self.write_log, f"[WS] Saved: {saved}")
            if ftype == "Program":
                run_bg(self.run_proactive_check, conn, prog, ABAPParser.get_objects(code), profile, force_sub)
        except Exception as e:
            ui.call(self.write_log, f"CONNECTION ERROR: {e}")
        finally:
            ui.call(self.reset_buttons)

    def run_proactive_check(self, conn, main_prog, main_objs, profile="", force=False):
        ui = self.ui

        def merge(combined, seen, new):
            for cat, items in new.items():
                combined.setdefault(cat, []); seen.setdefault(cat, set())
                for o in items:
                    if o["name"] not in seen[cat]:
                        combined[cat].append(o); seen[cat].add(o["name"])
        combined, seen = {}, {}
        merge(combined, seen, main_objs)
        try:
            to_fetch = []
            for inc in [o["name"] for o in main_objs.get("INCLUDES", [])]:
                cached = workspace.read_code(profile, "Program", inc) if (profile and not force) else ""
                if cached:
                    merge(combined, seen, ABAPParser.get_objects(cached))
                else:
                    to_fetch.append(inc)
            if to_fetch:
                ui.call(self.write_log, f"[DEEP] Reading {len(to_fetch)} include(s) from SAP...")
                for inc, (code, err) in self.controller.fetch_programs(conn, to_fetch).items():
                    if not code:
                        ui.call(self.write_log, f"[DEEP] Skip {inc}: {err}"); continue
                    merge(combined, seen, ABAPParser.get_objects(code))
                    if profile and workspace.save_code(profile, "Program", inc, code, project=main_prog):
                        ui.call(self.write_log, f"[WS] Saved include: {inc}")
            names = [o["name"] for cat in ("DICT", "CLASS", "INCLUDES") for o in combined.get(cat, [])]
            ui.call(self.write_log, f"[DISCOVERY] Checking {len(names)} names in TADIR...")
            registry = self.controller.check_objects_batch(conn, names)
            ui.call(self.write_log, f"[DISCOVERY] {len(registry)} SAP objects verified.")
            ui.call(self.sap_panel.populate, combined, registry, f"{main_prog}  (SAP)")
            if not profile:
                return
            tables = [n for n, t in registry.items() if t in ("TABL", "VIEW") and n.startswith(("Z", "Y"))
                      and (force or not workspace.find_project(profile, workspace.TABLE_FOLDER, f"{n}.json"))]
            if tables:
                ui.call(self.write_log, f"[WS] Caching {len(tables)} custom table(s)...")
                for name, (_, attrs) in self.controller.fetch_ddic_objects(conn, tables).items():
                    if isinstance(attrs, dict):
                        if workspace.save_table(profile, name, attrs.get("FIELDS", []), project=main_prog):
                            ui.call(self.write_log, f"[WS] Saved table: {name}")
                    else:
                        ui.call(self.write_log, f"[WS] Skip table {name}: {attrs}")
        except Exception as e:
            ui.call(self.write_log, f"[DEEP] Error: {e}")

    def _populate_tree_offline(self, profile, prog, objs):
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
        note = "workspace" if self.is_local() else "workspace — Re-fetch for TADIR check"
        self.sap_panel.populate(objs, registry, f"{prog}  ({note})")

    def open_from_tree(self, name: str, tadir: str):
        category = TADIR_META.get(tadir, (None, ""))[0]
        if not category:
            if tadir:
                self.write_log(f"[Tree] {name}: type {tadir} cannot be displayed.")
            return
        run_bg(self.run_sub_fetch, self.get_current_conn(), name, category, self.active_profile())

    def run_sub_fetch(self, conn, name, category, profile, force=False):
        name = name.upper()
        ftype = _CATEGORY_FTYPE.get(category, "Program")
        tab = _tab_name(ftype, name)
        ui = self.ui
        try:
            if not force and profile:
                if category == "DICT":
                    fields = workspace.read_table_fields(profile, name)
                    if fields:
                        ui.call(self.write_log, f"[WS] Loaded from workspace: {name}")
                        ui.call(self.open_ddic_tab, tab, {"NAME": name, "FIELDS": fields}, "Table"); return
                else:
                    code = workspace.read_code(profile, ftype, name)
                    if code:
                        ui.call(self.write_log, f"[WS] Loaded from workspace: {name}")
                        ui.call(self.open_code_tab, tab, code, name, ftype, profile); return
            if not conn:
                ui.call(self.write_log, f"[Local] {name} is not in the workspace and there is no SAP connection."); return
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
                ui.call(self.write_log, f"FAILED: {name} - {attrs}"); return
            ui.call(self.write_log, f"SUCCESS: {name} loaded from SAP.")
            project = self.current_main_program or None
            if category == "DICT":
                ui.call(self.open_ddic_tab, tab, attrs, "Table")
                saved = workspace.save_table(profile, name, attrs.get("FIELDS", []), project=project)
            else:
                ui.call(self.open_code_tab, tab, code, name, ftype, profile)
                saved = workspace.save_code(profile, ftype, name, code, project=project)
            if saved:
                ui.call(self.write_log, f"[WS] Saved: {saved}")
        except Exception as e:
            ui.call(self.write_log, f"SUB-FETCH ERROR ({name}): {e}")

    # ── local files ───────────────────────────────────────────────────────────
    def open_local_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Open ABAP source", "", _FILE_FILTER)
        if not paths:
            return
        if self.is_local():
            self._import_local_files(paths, self.ws_panel.selected_dir())
            return
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
            except OSError as e:
                self.write_log(f"[Local] Could not read {p}: {e}"); continue
            name = re.sub(r"[^A-Z0-9_/]", "_", os.path.splitext(os.path.basename(p))[0].upper())
            self._import_code(name, code, workspace.guess_ftype(code))

    def paste_code(self):
        default = "" if self.is_local() else self.current_main_program
        dlg = PasteCodeDialog(self, default_name=default)
        if dlg.exec() and dlg.result_name:
            if self.is_local():
                fname = dlg.result_name if os.path.splitext(dlg.result_name)[1] else dlg.result_name + ".abap"
                self._import_local_code(fname, dlg.result_code, self.ws_panel.selected_dir())
            else:
                self._import_code(dlg.result_name, dlg.result_code, dlg.result_type)

    # free-form (Local) workspace ------------------------------------------------
    def _import_local_files(self, paths, rel_dir: str):
        profile = LOCAL_PROFILE
        opened = None
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
            except OSError as e:
                self.write_log(f"[Local] Could not read {p}: {e}"); continue
            fname = os.path.basename(p)
            if not os.path.splitext(fname)[1]:
                fname += ".abap"
            if self._import_local_code(fname, code, rel_dir, open_after=False):
                opened = (rel_dir, fname)
        if opened:
            self.open_local_tree_file(profile, *opened)

    def _import_local_code(self, fname: str, code: str, rel_dir: str, open_after=True) -> bool:
        profile = LOCAL_PROFILE
        rel = f"{rel_dir}/{fname}" if rel_dir else fname
        if os.path.exists(workspace.abs_rel(profile, rel)):
            if QMessageBox.question(self, "Overwrite?", f"'{rel}' already exists in the workspace.\nOverwrite it?") \
                    != QMessageBox.StandardButton.Yes:
                return False
        path = workspace.write_rel(profile, rel, code)
        self.write_log(f"[Local] Imported → {path}")
        self._close_tabs_under(profile, rel)
        self.refresh_workspace_tree()
        if open_after:
            self.open_local_tree_file(profile, rel_dir, fname)
        return True

    def open_local_tree_file(self, profile: str, rel_dir: str, filename: str):
        rel = f"{rel_dir}/{filename}" if rel_dir else filename
        full = workspace.abs_rel(profile, rel)
        if not os.path.isfile(full):
            self.write_log(f"[WS] Not found: {rel}"); return
        content = workspace.read_rel(profile, rel)
        stem, ext = os.path.splitext(filename)
        ext = ext.lower()
        if os.path.basename(rel_dir) == workspace.PROP_FOLDER:            # a proposal → tab keyed by its target
            parent = os.path.dirname(rel_dir).replace("\\", "/")
            orig = workspace.find_original(profile, parent, filename)
            target = workspace.rel_of(profile, orig) if orig else (f"{parent}/{filename}" if parent else filename)
            self.open_code_tab(f"Proposal: {target}", content, stem.upper(), "Program", profile, is_proposal=True, rel=target)
            return
        if ext == ".json":
            try:
                data = json.loads(content)
            except ValueError:
                data = None
            if isinstance(data, list) and data and isinstance(data[0], dict) and "Field" in data[0]:
                self.open_ddic_tab(f"Table: {stem.upper()}", {"NAME": stem.upper(), "FIELDS": data}, "Table")
                return
        if ext in _ABAP_EXTS:
            ftype = workspace.guess_ftype(content)
            self.open_code_tab(f"{ftype}: {rel}", content, stem.upper(), ftype, profile, rel=rel)
            if ftype == "Program":
                self.current_main_program = rel                 # jump_to_line targets "Program: <rel>"
                self._populate_tree_offline(profile, stem.upper(), ABAPParser.get_objects(content))
        else:
            self.open_code_tab(f"File: {rel}", content, stem.upper(), "File", profile, rel=rel, mode="plain")

    def apply_proposal(self, profile: str, prog: str, code: str, rel=None):
        """Write proposal code over the workspace copy of the target (free-form: rel; SAP-style: programs/)."""
        if rel:
            target = workspace.abs_rel(profile, rel)
            shown = rel
        else:
            target = workspace.get_path(profile, "Program", prog)
            shown = workspace.rel_of(profile, target)
        exists = os.path.isfile(target)
        msg = (f"Overwrite '{shown}' with the proposed code?" if exists else f"Create '{shown}' from the proposed code?")
        if QMessageBox.question(self, "Apply proposal", msg + "\n\n(Only the local workspace changes — nothing is sent to SAP.)") \
                != QMessageBox.StandardButton.Yes:
            return
        if rel:
            path = workspace.write_rel(profile, rel, code)
        else:
            path = workspace.save_code(profile, "Program", prog, code)
            if not path:
                QMessageBox.warning(self, "Apply proposal", f"{prog} is not a custom (Z/Y) object — not saved."); return
        key = rel or prog
        self.close_tab(f"Diff: {key}"); self.close_tab(f"Proposal: {key}")
        self._close_tabs_under(profile, rel) if rel else self._close_tabs_for_files([f"{prog}.abap"])
        self.write_log(f"[WS] Proposal applied → {path}")
        self.refresh_workspace_tree()
        if rel:
            d, f = os.path.split(rel)
            self.open_local_tree_file(profile, d, f)
        else:
            ftype = workspace.guess_ftype(code)
            self.open_code_tab(_tab_name(ftype, prog), code, prog, ftype, profile)

    def _close_tabs_under(self, profile: str, rel_prefix: str):
        """Close every tab whose file is rel_prefix or lies inside that folder."""
        for tab in list(self.tabs):
            e = self.tabs[tab]
            r = e.get("rel")
            if r and (e.get("source_profile") or profile) == profile and (r == rel_prefix or r.startswith(rel_prefix + "/")):
                self.close_tab(tab)

    def ws_new_folder(self, vals):
        profile, rel_dir = vals[1], vals[2]
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        name = name.strip().strip("/\\") if ok else ""
        if not name:
            return
        if re.search(r'[<>:"|?*]', name):
            QMessageBox.warning(self, "New folder", "Folder names cannot contain  < > : \" | ? *"); return
        rel = f"{rel_dir}/{name}" if rel_dir else name
        os.makedirs(workspace.abs_rel(profile, rel), exist_ok=True)
        self.write_log(f"[WS] Folder created: {rel}")
        self.refresh_workspace_tree()

    def ws_new_file(self, vals):
        profile, rel_dir = vals[1], vals[2]
        name, ok = QInputDialog.getText(self, "New file", "File name (e.g. ZREPORT.abap):")
        name = name.strip().strip("/\\") if ok else ""
        if not name:
            return
        if re.search(r'[<>:"|?*/\\]', name):
            QMessageBox.warning(self, "New file", "Invalid file name."); return
        if not os.path.splitext(name)[1]:
            name += ".abap"
        rel = f"{rel_dir}/{name}" if rel_dir else name
        if os.path.exists(workspace.abs_rel(profile, rel)):
            QMessageBox.warning(self, "New file", f"'{rel}' already exists."); return
        stem, ext = os.path.splitext(name)
        template = f"REPORT {stem.lower()}.\n\n" if ext.lower() == ".abap" else ""
        workspace.write_rel(profile, rel, template)
        self.write_log(f"[WS] File created: {rel}")
        self.refresh_workspace_tree()
        self.open_local_tree_file(profile, rel_dir, name)

    def ws_import_here(self, vals):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import files into the workspace", "", _FILE_FILTER)
        if paths:
            self._import_local_files(paths, vals[2])

    def ws_rename(self, vals):
        kind, profile, rel_dir, fname, _ = vals
        old_rel = rel_dir if kind == "_dir" else (f"{rel_dir}/{fname}" if rel_dir else fname)
        old_name = os.path.basename(old_rel)
        new, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        new = new.strip() if ok else ""
        if not new or new == old_name:
            return
        if re.search(r'[<>:"|?*/\\]', new):
            QMessageBox.warning(self, "Rename", "Invalid name."); return
        parent = os.path.dirname(old_rel).replace("\\", "/")
        new_rel = f"{parent}/{new}" if parent else new
        src, dst = workspace.abs_rel(profile, old_rel), workspace.abs_rel(profile, new_rel)
        if os.path.exists(dst):
            QMessageBox.warning(self, "Rename", f"'{new_rel}' already exists."); return
        self._close_tabs_under(profile, old_rel)
        try:
            os.rename(src, dst)
        except OSError as e:
            QMessageBox.critical(self, "Rename", str(e)); return
        self.write_log(f"[WS] Renamed: {old_rel} → {new_rel}")
        self.refresh_workspace_tree()

    def _import_code(self, name: str, code: str, ftype: str):
        profile = self.active_profile()
        if not name.startswith(("Z", "Y")):
            name = "Z_" + name              # workspace only stores custom objects
        path = workspace.save_code(profile, ftype, name, code, project=name)
        self.write_log(f"[Local] Imported {ftype} {name} → {path}")
        self.open_code_tab(_tab_name(ftype, name), code, name, ftype, profile)
        if ftype == "Program":
            self.current_main_program = name
            self._populate_tree_offline(profile, name, ABAPParser.get_objects(code))

    # ══════════════════════════════════════════════════════════════════════════
    # Workspace explorer
    # ══════════════════════════════════════════════════════════════════════════

    def refresh_workspace_tree(self):
        if self._ws_refreshing:
            self._ws_refresh_pending = True; return
        self._ws_refreshing = True
        run_bg(self._ws_refresh_worker, self.active_profile())

    def _ws_refresh_worker(self, profile):
        data, git_st, branch, tree = {}, {}, "", None
        try:
            if profile == LOCAL_PROFILE:
                tree = workspace.list_tree(profile)
            else:
                data = {profile: workspace.list_files(profile)} if profile else {}
            git_st = github_sync.get_git_status()
            branch = github_sync.get_branch_name()
        except Exception as e:
            self.ui.call(self.write_log, f"[WS] Refresh error: {e}")
        self.ui.call(self._ws_apply, data, git_st, branch, profile, tree)

    def _ws_apply(self, data, git_st, branch, profile, tree=None):
        try:
            if tree is not None:
                self.ws_panel.build_free(tree, git_st, profile)
            else:
                self.ws_panel.build(data, git_st, profile)
            self.ws_panel.set_branch(branch)
            self.st_right.setText(f"⎇ {branch}   ·   read-only RFC" if branch else "read-only RFC")
        finally:
            self._ws_refreshing = False
            if self._ws_refresh_pending:
                self._ws_refresh_pending = False
                self.refresh_workspace_tree()

    def open_workspace_file(self, vals):
        kind, profile, folder, filename, project = vals
        if kind == "lfile":
            self.open_local_tree_file(profile, folder, filename)
            return
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

    @staticmethod
    def _ws_path(vals) -> str:
        kind, profile, folder, fname, proj = vals
        if kind == "_dir":
            return workspace.abs_rel(profile, folder)
        if kind == "lfile":
            return workspace.abs_rel(profile, f"{folder}/{fname}" if folder else fname)
        if kind == "_profile":
            return workspace.abs_path(profile)
        if kind == "_project":
            return workspace.abs_path(profile, proj)
        if kind == "_folder":
            return workspace.abs_path(profile, proj, folder)
        return workspace.abs_path(profile, proj, folder, fname)

    def reveal_in_explorer(self, vals):
        path = os.path.normpath(self._ws_path(vals))
        if not os.path.exists(path):
            self.write_log(f"[WS] Not found: {path}"); return
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                subprocess.Popen(["explorer", "/select,", path])
        except Exception as e:
            self.write_log(f"[WS] Could not open Explorer: {e}")

    def delete_workspace_node(self, vals):
        kind, profile, folder, fname, proj = vals
        path = self._ws_path(vals)
        label = {"_profile": f"Delete entire profile folder '{profile}'?", "_project": f"Delete project '{proj}'?",
                 "_folder": f"Delete '{proj} / {folder}' and its contents?",
                 "_dir": f"Delete folder '{folder}' and everything inside it?",
                 "lfile": f"Delete file '{folder + '/' if folder else ''}{fname}'?"}.get(kind, f"Delete file '{fname}'?")
        if not os.path.exists(path):
            self.write_log(f"[WS] Not found: {path}"); return
        if QMessageBox.question(self, "Delete", label) != QMessageBox.StandardButton.Yes:
            return
        affected = []
        if os.path.isdir(path):
            for _d, _ds, files in os.walk(path):
                affected.extend(files)
        else:
            affected.append(fname)
        try:
            workspace.delete_path(path)
            self.write_log(f"[WS] Deleted: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Delete error", str(e)); return
        self._close_tabs_for_files(affected)
        if kind in ("_dir", "lfile"):
            self._close_tabs_under(profile, workspace.rel_of(profile, path))
        self.refresh_workspace_tree()

    # ── proposals ─────────────────────────────────────────────────────────────
    def _seed_proposals(self, profile):
        for proj, fname, mtime in workspace.scan_proposals(profile):
            self._watched_proposals[f"{profile}/{proj}/{fname}"] = mtime

    def _mark_proposal_seen(self, profile, path):
        try:
            parent = workspace.rel_of(profile, os.path.dirname(os.path.dirname(path)))
            self._watched_proposals[f"{profile}/{parent}/{os.path.basename(path)}"] = os.stat(path).st_mtime_ns
        except OSError:
            pass

    def _poll_proposals(self):
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

    def _open_proposal(self, profile, parent, fname):
        """parent = folder owning the proposals/ dir ('ZFI_CO_003' SAP-style, 'sub/dir' or '' free-form)."""
        name = os.path.splitext(fname)[0].upper()
        prop_rel = "/".join(x for x in (parent, workspace.PROP_FOLDER, fname) if x)
        proposed = workspace.read_rel(profile, prop_rel)
        if not proposed:
            return
        orig_abs = workspace.find_original(profile, parent, fname)
        sap_style = (profile != LOCAL_PROFILE and orig_abs
                     and os.path.basename(os.path.dirname(orig_abs)) == workspace.SOURCE_FOLDER)
        original, rel = "", None
        if sap_style or (profile != LOCAL_PROFILE and not orig_abs):
            key = name
            for prefix in _CODE_TYPES:                       # prefer the open tab (may hold unsaved edits)
                e = self.tabs.get(f"{prefix}: {name}", {})
                original = e["view"].get() if e.get("view") and e.get("kind") == "code" else e.get("code", "")
                if original:
                    break
            if not original and orig_abs:
                with open(orig_abs, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
        else:
            rel = workspace.rel_of(profile, orig_abs) if orig_abs else "/".join(x for x in (parent, fname) if x)
            key = rel
            for tab, e in self.tabs.items():
                if e.get("rel") == rel and e.get("kind") == "code" and not tab.startswith(("Proposal:", "Diff:")):
                    original = e["view"].get(); break
            if not original and orig_abs:
                original = workspace.read_rel(profile, rel)
        self.write_log(f"[WS] Proposal arrived: {prop_rel}")
        self.close_tab(f"Diff: {key}"); self.close_tab(f"Proposal: {key}")
        if original:
            self.open_diff_tab(f"Diff: {key}", original, proposed, name, profile, rel=rel)
        else:
            self.open_code_tab(f"Proposal: {key}", proposed, name, "Program", profile, is_proposal=True, rel=rel)

    # ══════════════════════════════════════════════════════════════════════════
    # Claude
    # ══════════════════════════════════════════════════════════════════════════

    def _load_claude_sessions(self) -> list:
        try:
            with open(CLAUDE_SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_claude_sessions(self, items):
        try:
            with open(CLAUDE_SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(items[:100], f, indent=2)
        except OSError:
            pass

    def list_claude_sessions(self, profile: str) -> list:
        return [x for x in self._load_claude_sessions() if x.get("profile") == profile]

    def remember_claude_session(self, session, title: str, first_prompt: str = ""):
        if not session.session_id:
            return
        items = self._load_claude_sessions()
        old = next((x for x in items if x.get("id") == session.session_id), None)
        items = [x for x in items if x.get("id") != session.session_id]
        entry = {"id": session.session_id, "title": (old or {}).get("title") or title, "profile": session.profile,
                 "last": time.strftime("%Y-%m-%d %H:%M"), "cost": round(session.total_cost, 4)}
        if first_prompt and not old:
            entry["title"] = first_prompt.strip().splitlines()[0][:48]
        items.insert(0, entry)
        self._save_claude_sessions(items)
        self.side.set_sessions(self.list_claude_sessions(self.active_profile()))

    def forget_claude_session(self, session_id: str):
        items = self._load_claude_sessions()
        entry = next((x for x in items if x.get("id") == session_id), None) or {}
        title = entry.get("title") or session_id[:8]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question); box.setWindowTitle("Delete session")
        box.setText(f"Delete session '{title}' from the list?")
        cb = QCheckBox("Also delete Claude Code's transcript file (the chat can no longer be resumed)")
        box.setCheckBox(cb)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        for tab, e in list(self.tabs.items()):
            if e.get("kind") == "claude" and e["widget"].session.session_id == session_id:
                self.close_tab(tab)
        if cb.isChecked():
            cwd = workspace.abs_path(entry.get("profile") or self.active_profile())
            path = transcript_path(cwd, session_id)
            try:
                if path:
                    os.remove(path)
                    self.write_log(f"[Claude] Transcript deleted: {path}")
            except OSError as e:
                self.write_log(f"[Claude] Could not delete transcript: {e}")
        self._save_claude_sessions([x for x in items if x.get("id") != session_id])
        self.side.set_sessions(self.list_claude_sessions(self.active_profile()))
        self.write_log(f"[Claude] Session removed: {title}")

    def rename_claude_session(self, session_id: str, title: str):
        items = self._load_claude_sessions()
        for x in items:
            if x.get("id") == session_id:
                x["title"] = title
        self._save_claude_sessions(items)
        self.side.set_sessions(self.list_claude_sessions(self.active_profile()))
        for tab, e in list(self.tabs.items()):
            if e.get("kind") == "claude" and e["widget"].session.session_id == session_id:
                new_name = f"Claude: {title}"
                if new_name in self.tabs:
                    break
                self.tabs[new_name] = self.tabs.pop(tab)
                idx = self.tabw.indexOf(e["widget"])
                self.tabw.setTabText(idx, f"{_TAB_GLYPH['Claude']} {new_name}"); self.tabw.setTabToolTip(idx, new_name)
                e["widget"].title = title
                break

    def is_subscription(self) -> bool:
        return auth_info().get("authMethod") == "claude.ai"

    def usage_updated(self, session):
        self.side.set_usage(session.usage, session.usage_saved_at)

    def open_claude_tab(self, session_id: str = None, title: str = None):
        profile = self.active_profile()
        if not find_claude():
            QMessageBox.warning(self, "Claude Code not found",
                                "Claude Code CLI is not installed or not on PATH.\n\n"
                                "Install:  winget install Anthropic.ClaudeCode\nThen run  claude  once to log in.")
            return
        if not title:
            n = 1
            existing = {x.get("title") for x in self.list_claude_sessions(profile)}
            while f"Claude: #{n}" in self.tabs or f"#{n}" in existing:
                n += 1
            title = f"#{n}"
        name = f"Claude: {title}"
        if self.activate_tab(name):
            return
        cwd = workspace.abs_path(profile)
        os.makedirs(cwd, exist_ok=True)
        session = ClaudeSession(cwd=cwd, profile=profile, session_id=session_id)
        tab = ClaudeChatTab(self, session, title)
        tab.copy_requested.connect(self.copy_to_clipboard)
        tab.proposal_requested.connect(self.proposal_from_code)
        first = {"p": ""}
        def _updated(s):
            self.remember_claude_session(s, title, first["p"])
            self.side.set_usage(s.usage, s.usage_saved_at)
        tab.session_updated.connect(_updated)
        _orig_send = tab.send
        def _send():
            if not first["p"]:
                first["p"] = tab.input.toPlainText()
            _orig_send()
        tab.send = _send
        tab.input.send.disconnect(); tab.input.send.connect(_send)
        tab.send_btn.clicked.disconnect(); tab.send_btn.clicked.connect(_send)
        self._add_tab(name, tab, kind="claude")
        restored = tab.load_history() if session_id else 0
        self.write_log(f"[Claude] {'Resumed' if session_id else 'New'} session {title} "
                       f"(cwd={cwd}, mcp={'yes' if session.has_mcp else 'no'}"
                       + (f", {restored} messages restored" if restored else "") + ")")

    def get_active_code_context(self) -> str:
        name = self.active_tab_name()
        entry = self.tabs.get(name)
        if not entry or not entry.get("prog"):
            return ""
        prog, ftype = entry["prog"], entry.get("ftype") or "Program"
        profile = entry.get("source_profile") or self.active_profile()
        code = entry.get("code", "")
        if entry.get("view") and entry.get("kind") == "code":
            code = entry["view"].get()
        rel = entry.get("rel")
        if rel:
            shown = workspace.proposal_rel(rel) if name.startswith("Proposal:") else rel
            ctx = (f"[IDE context] The user has '{name}' open (profile {profile}). "
                   f"File relative to the working directory: {shown}. "
                   f"A proposal for it belongs to {workspace.proposal_rel(rel)} "
                   f"(write_proposal(..., path='{rel}')).")
        else:
            folder = workspace.PROP_FOLDER if name.startswith("Proposal:") else workspace.SOURCE_FOLDER
            proj = workspace.find_project(profile, folder, f"{prog}.abap") or prog
            ctx = (f"[IDE context] The user has '{name}' open ({ftype} {prog}, profile {profile}). "
                   f"Cached file relative to the working directory: {proj}/{folder}/{prog}.abap")
        if code and len(code) <= _CONTEXT_INLINE_LIMIT:
            ctx += f"\nCurrent content:\n```abap\n{code}\n```"
        elif code:
            ctx += f"\nThe file is large ({len(code.splitlines())} lines); read it with the Read tool."
        return ctx

    def proposal_from_code(self, code: str):
        profile = self.active_profile()
        name = ""
        for tab in reversed(list(self.tabs)):
            e = self.tabs[tab]
            if e.get("prog") and not tab.startswith(("Diff:", "Proposal:")):
                if e.get("rel"):                       # free-form file → proposal next to it
                    rel, ok = QInputDialog.getText(self, "Proposal", "Proposal for file (workspace-relative path):",
                                                   text=e["rel"])
                    if not ok or not rel.strip():
                        return
                    path = workspace.write_proposal(profile, e["prog"], code, path=rel.strip())
                    self.write_log(f"[Claude] Proposal written: {path}")
                    return
                name = e["prog"]; break
        name = name or self.current_main_program
        m = re.search(r"^\s*(?:REPORT|PROGRAM|FUNCTION)\s+([\w/]+)", code, re.I | re.M)
        if m:
            name = m.group(1).upper()
        name, ok = QInputDialog.getText(self, "Proposal", "Program name for this proposal:", text=name)
        if not ok or not name.strip():
            return
        path = workspace.write_proposal(profile, name.strip().upper(), code)
        self.write_log(f"[Claude] Proposal written: {path}")

    # ══════════════════════════════════════════════════════════════════════════
    # Misc
    # ══════════════════════════════════════════════════════════════════════════

    def write_log(self, text: str):
        self.logs.appendPlainText(f">>> {text}")
        first = text.strip().splitlines()[0] if text.strip() else ""
        self.st_msg.setText(first[:160])

    def copy_to_clipboard(self, text: str):
        QApplication.clipboard().setText(text)
        self.write_log("Copied.")

    # ── GitHub ────────────────────────────────────────────────────────────────
    def _github_op(self, label, fn):
        profile = self.active_profile()
        self.write_log(f"[GitHub] {label} {profile}...")

        def work():
            ok, msg = fn(profile)
            self.ui.call(self.write_log, f"[GitHub] {msg}")
            self.ui.call(self.refresh_workspace_tree)
            self.ui.call(lambda: (QMessageBox.information if ok else QMessageBox.warning)(self, f"GitHub {label}", msg))
        run_bg(work)

    def github_push(self):
        self._github_op("Push", github_sync.push_workspace)

    def github_pull(self):
        self._github_op("Pull", github_sync.pull_workspace)
