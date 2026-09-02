"""
Workspace panel: Push / Pull / Refresh, branch label, filter, git-aware file tree,
context menu (Open / Show in Explorer / Delete).

Node payload (UserRole): (kind, profile, folder, filename, project)
kind ∈ "_profile" | "_project" | "_folder" | "file"
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QAction
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QMenu)

from ui import theme as T
from utils import workspace

_FOLDER_LABEL = {"programs": "Programs", "tables": "Tables", "proposals": "Proposals"}
_FOLDER_GLYPH = {"programs": "▤", "tables": "▦", "proposals": "✉"}
_STATUS_COLOR = {"M": T.WARN, "?": T.GOOD, "D": T.BAD}
_STATUS_PREFIX = {"M": "● ", "?": "+ ", "D": "✗ "}


class WorkspacePanel(QWidget):
    push = Signal()
    pull = Signal()
    refresh = Signal()
    open_file = Signal(tuple)
    reveal = Signal(tuple)
    delete = Signal(tuple)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = ({}, {}, "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        tb = QHBoxLayout()
        b_push = QPushButton("⬆ Push"); b_push.setObjectName("ok")
        b_pull = QPushButton("⬇ Pull")
        b_ref = QPushButton("⟳"); b_ref.setFixedWidth(34)
        b_push.clicked.connect(self.push); b_pull.clicked.connect(self.pull); b_ref.clicked.connect(self.refresh)
        tb.addWidget(b_push, 1); tb.addWidget(b_pull, 1); tb.addWidget(b_ref)
        lay.addLayout(tb)

        self.branch = QLabel("")
        self.branch.setStyleSheet(f"color: {T.GOOD};")
        lay.addWidget(self.branch)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter files…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda t: self.build(*self._last, flt=t))
        lay.addWidget(self.filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Kind"])
        self.tree.setColumnWidth(0, 280)
        self.tree.itemDoubleClicked.connect(self._dbl)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)

    def set_branch(self, branch: str):
        self.branch.setText(f"⎇  {branch}" if branch else "⎇  (no git repo yet — Push to create)")

    # ── build ─────────────────────────────────────────────────────────────────
    def build(self, data: dict, git_st: dict, profile: str = "", flt=None):
        if flt is None:
            self._last = (data, git_st, profile)
            flt = self.filter.text()
        flt = (flt or "").strip().upper()
        tree = self.tree

        # remember expansion state
        state = {}
        def _collect(item):
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] != "file":
                state[(d[0], d[1], d[4], d[2])] = item.isExpanded()
            for i in range(item.childCount()):
                _collect(item.child(i))
        for i in range(tree.topLevelItemCount()):
            _collect(tree.topLevelItem(i))
        scroll = tree.verticalScrollBar().value()
        tree.clear()

        if not data or not any(data.values()):
            tree.addTopLevelItem(QTreeWidgetItem([f"(no cached objects yet for profile {profile})" if profile
                                                  else "(select a profile)", ""]))
            return

        pri = {"M": 3, "?": 2, "D": 1}
        def worst(a, b):
            return a if pri.get(a, 0) >= pri.get(b, 0) else b
        proj_st, prof_st = {}, {}
        for path, st in git_st.items():
            parts = path.split("/")
            if len(parts) >= 2:
                pk = f"{parts[0]}/{parts[1]}"
                proj_st[pk] = worst(proj_st.get(pk, ""), st)
                prof_st[parts[0]] = worst(prof_st.get(parts[0], ""), st)

        def mk(text, kind_txt, payload, st=""):
            it = QTreeWidgetItem([f"{_STATUS_PREFIX.get(st, '')}{text}", kind_txt])
            it.setData(0, Qt.ItemDataRole.UserRole, payload)
            if st in _STATUS_COLOR:
                it.setForeground(0, QColor(_STATUS_COLOR[st]))
            return it

        for prof in sorted(data):
            projects = data[prof]
            if not projects:
                continue
            p_item = mk(f"🖥  {prof}", "", ("_profile", prof, "", "", ""), prof_st.get(prof, ""))
            tree.addTopLevelItem(p_item)
            p_item.setExpanded(state.get(("_profile", prof, "", ""), True))
            for proj in sorted(projects):
                if flt and not any(flt in f.upper() or flt in proj.upper()
                                   for fl in projects[proj].values() for f in fl):
                    continue
                pr_item = mk(f"📁  {proj}", "", ("_project", prof, "", "", proj), proj_st.get(f"{prof}/{proj}", ""))
                p_item.addChild(pr_item)
                pr_item.setExpanded(state.get(("_project", prof, proj, ""), True))
                for folder in workspace.FOLDERS:
                    fnames = projects[proj].get(folder, [])
                    if flt:
                        fnames = [f for f in fnames if flt in f.upper() or flt in proj.upper()]
                    if not fnames:
                        continue
                    f_item = mk(f"{_FOLDER_GLYPH[folder]}  {_FOLDER_LABEL[folder]}  ({len(fnames)})", "",
                                ("_folder", prof, folder, "", proj))
                    pr_item.addChild(f_item)
                    f_item.setExpanded(state.get(("_folder", prof, proj, folder), True))
                    for fname in fnames:
                        kind = "ABAP" if fname.endswith(".abap") else "Table" if fname.endswith(".json") else ""
                        st = git_st.get(f"{prof}/{proj}/{folder}/{fname}", "")
                        f_item.addChild(mk(fname, kind, ("file", prof, folder, fname, proj), st))
        tree.verticalScrollBar().setValue(scroll)

    # ── interaction ───────────────────────────────────────────────────────────
    def _payload(self, item):
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _dbl(self, item, _col):
        d = self._payload(item)
        if d and d[0] == "file":
            self.open_file.emit(tuple(d))

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        d = self._payload(item)
        if not d:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        if d[0] == "file":
            a = QAction("Open", menu); a.triggered.connect(lambda: self.open_file.emit(tuple(d))); menu.addAction(a)
        a = QAction("Show in Windows Explorer", menu); a.triggered.connect(lambda: self.reveal.emit(tuple(d))); menu.addAction(a)
        menu.addSeparator()
        label = {"_profile": "Delete profile folder…", "_project": "Delete project…",
                 "_folder": "Delete folder contents…"}.get(d[0], "Delete file…")
        a = QAction(label, menu); a.triggered.connect(lambda: self.delete.emit(tuple(d))); menu.addAction(a)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
