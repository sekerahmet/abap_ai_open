"""
Workspace panel: Push / Pull / Refresh, branch label, filter, git-aware file tree,
context menu.

Two tree modes:
  SAP profiles   → build(data, git_st, profile)      fixed layout PROJECT/{programs,tables,proposals}
  Local profile  → build_free(tree, git_st, profile)  free-form folder tree the user organises

Node payload (UserRole):
  SAP  : (kind, profile, folder, filename, project)   kind ∈ "_profile" | "_project" | "_folder" | "file"
  free : ("_dir",  profile, rel_dir, "",       "")     rel_dir = "" for the workspace root
         ("lfile", profile, rel_dir, filename, "")
"""

import os

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
_ABAP_EXTS = (".abap", ".prog", ".clas", ".fugr", ".incl")


class _DropTree(QTreeWidget):
    """Tree that accepts files dropped from Explorer (free-form mode only)."""
    files_dropped = Signal(object, list)      # item-or-None, [paths]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.accept_files = False

    def dragEnterEvent(self, e):
        if self.accept_files and e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self.accept_files and e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if self.accept_files and e.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(self.itemAt(e.position().toPoint()), paths)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class WorkspacePanel(QWidget):
    push = Signal()
    pull = Signal()
    refresh = Signal()
    open_root = Signal()
    open_file = Signal(tuple)
    reveal = Signal(tuple)
    delete = Signal(tuple)
    # free-form mode only
    new_folder = Signal(tuple)
    new_file = Signal(tuple)
    import_here = Signal(tuple)
    rename = Signal(tuple)
    files_dropped = Signal(tuple, list)       # (dir payload, [absolute source paths])

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = None
        self._mode = "sap"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        tb = QHBoxLayout()
        b_push = QPushButton("⬆ Push"); b_push.setObjectName("ok")
        b_pull = QPushButton("⬇ Pull")
        b_dir = QPushButton("📂"); b_dir.setFixedWidth(34); b_dir.setToolTip("Open the workspace folder in Windows Explorer")
        b_ref = QPushButton("⟳"); b_ref.setFixedWidth(34); b_ref.setToolTip("Refresh")
        b_push.clicked.connect(self.push); b_pull.clicked.connect(self.pull)
        b_dir.clicked.connect(self.open_root); b_ref.clicked.connect(self.refresh)
        tb.addWidget(b_push, 1); tb.addWidget(b_pull, 1); tb.addWidget(b_dir); tb.addWidget(b_ref)
        lay.addLayout(tb)

        self.branch = QLabel("")
        self.branch.setStyleSheet(f"color: {T.GOOD};")
        lay.addWidget(self.branch)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter files…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._refilter)
        lay.addWidget(self.filter)

        self.tree = _DropTree()
        self.tree.setHeaderLabels(["Name", "Kind"])
        self.tree.setColumnWidth(0, 280)
        self.tree.itemDoubleClicked.connect(self._dbl)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.files_dropped.connect(self._dropped)
        lay.addWidget(self.tree, 1)

        self.hint = QLabel(""); self.hint.setObjectName("dim"); self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

    def set_branch(self, branch: str):
        self.branch.setText(f"⎇  {branch}" if branch else "⎇  (no git repo yet — Push to create)")

    def _refilter(self, text):
        if self._last is None:
            return
        if self._mode == "free":
            self.build_free(*self._last, flt=text)
        else:
            self.build(*self._last, flt=text)

    # ── shared helpers ────────────────────────────────────────────────────────
    def _collect_state(self, key_fn) -> tuple:
        state = {}

        def _walk(item):
            d = item.data(0, Qt.ItemDataRole.UserRole)
            k = key_fn(d) if d else None
            if k is not None:
                state[k] = item.isExpanded()
            for i in range(item.childCount()):
                _walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            _walk(self.tree.topLevelItem(i))
        return state, self.tree.verticalScrollBar().value()

    @staticmethod
    def _mk(text, kind_txt, payload, st=""):
        it = QTreeWidgetItem([f"{_STATUS_PREFIX.get(st, '')}{text}", kind_txt])
        it.setData(0, Qt.ItemDataRole.UserRole, payload)
        if st in _STATUS_COLOR:
            it.setForeground(0, QColor(_STATUS_COLOR[st]))
        return it

    @staticmethod
    def _worst(a, b):
        pri = {"M": 3, "?": 2, "D": 1}
        return a if pri.get(a, 0) >= pri.get(b, 0) else b

    # ── SAP-style build ───────────────────────────────────────────────────────
    def build(self, data: dict, git_st: dict, profile: str = "", flt=None):
        if flt is None:
            self._last = (data, git_st, profile)
            self._mode = "sap"
            self.tree.accept_files = False
            self.hint.setText("")
            flt = self.filter.text()
        flt = (flt or "").strip().upper()
        tree = self.tree
        state, scroll = self._collect_state(lambda d: (d[0], d[1], d[4], d[2]) if d[0] != "file" else None)
        tree.clear()

        if not data or not any(data.values()):
            tree.addTopLevelItem(QTreeWidgetItem([f"(no cached objects yet for profile {profile})" if profile
                                                  else "(select a profile)", ""]))
            return

        proj_st, prof_st = {}, {}
        for path, st in git_st.items():
            parts = path.split("/")
            if len(parts) >= 2:
                pk = f"{parts[0]}/{parts[1]}"
                proj_st[pk] = self._worst(proj_st.get(pk, ""), st)
                prof_st[parts[0]] = self._worst(prof_st.get(parts[0], ""), st)
        mk = self._mk

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

    # ── free-form build (Local profile) ───────────────────────────────────────
    def build_free(self, tree_data: dict, git_st: dict, profile: str, flt=None):
        if flt is None:
            self._last = (tree_data, git_st, profile)
            self._mode = "free"
            self.tree.accept_files = True
            self.hint.setText("Organise files freely (also in Explorer 📂). Right-click: new folder / file, "
                              "import, rename. Proposals appear in <folder>/proposals/.")
            flt = self.filter.text()
        flt = (flt or "").strip().upper()
        tree = self.tree
        state, scroll = self._collect_state(lambda d: d[2] if d[0] == "_dir" else None)
        tree.clear()

        dir_st = {}
        pfx = profile + "/"
        for path, st in git_st.items():
            if not path.startswith(pfx):
                continue
            parts = path[len(pfx):].split("/")
            for i in range(len(parts)):
                key = "/".join(parts[:i])
                dir_st[key] = self._worst(dir_st.get(key, ""), st)
        mk = self._mk

        def kind_of(fname, rel_dir):
            if os.path.basename(rel_dir) == workspace.PROP_FOLDER:
                return "Proposal"
            ext = os.path.splitext(fname)[1].lower()
            if ext in _ABAP_EXTS:
                return "ABAP"
            return ext[1:].upper() if ext else ""

        def add(node, rel_dir, parent_item) -> int:
            n = 0
            for name, sub in node["dirs"].items():
                rel = f"{rel_dir}/{name}" if rel_dir else name
                glyph = "✉" if name == workspace.PROP_FOLDER else "📁"
                it = mk(f"{glyph}  {name}", "", ("_dir", profile, rel, "", ""), dir_st.get(rel, ""))
                c = add(sub, rel, it)
                if flt and c == 0:
                    continue
                parent_item.addChild(it)
                it.setExpanded(state.get(rel, True))
                n += c
            for f in node["files"]:
                if flt and flt not in f.upper() and flt not in rel_dir.upper():
                    continue
                rel = f"{rel_dir}/{f}" if rel_dir else f
                parent_item.addChild(mk(f, kind_of(f, rel_dir), ("lfile", profile, rel_dir, f, ""),
                                        git_st.get(f"{profile}/{rel}", "")))
                n += 1
            return n

        root = mk(f"🖥  {profile}", "", ("_dir", profile, "", "", ""), dir_st.get("", ""))
        tree.addTopLevelItem(root)
        count = add(tree_data or {"dirs": {}, "files": []}, "", root)
        root.setExpanded(True)
        if count == 0:
            root.addChild(QTreeWidgetItem(["(no files match)" if flt else
                                           "(empty — right-click for New folder / Import files, or drop files here)", ""]))
        tree.verticalScrollBar().setValue(scroll)

    # ── interaction ───────────────────────────────────────────────────────────
    def _payload(self, item):
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def selected_dir(self) -> str:
        """Free-form mode: profile-relative folder of the current selection ('' = root / none)."""
        d = self._payload(self.tree.currentItem())
        if self._mode != "free" or not d or d[0] not in ("_dir", "lfile"):
            return ""
        return d[2]

    def _dbl(self, item, _col):
        d = self._payload(item)
        if d and d[0] in ("file", "lfile"):
            self.open_file.emit(tuple(d))

    def _dropped(self, item, paths):
        d = self._payload(item)
        if not d or d[0] not in ("_dir", "lfile"):
            d = self._payload(self.tree.topLevelItem(0))
        if d:
            self.files_dropped.emit(("_dir", d[1], d[2], "", ""), paths)

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        d = self._payload(item)
        if not d:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)

        def act(label, fn):
            a = QAction(label, menu); a.triggered.connect(fn); menu.addAction(a)

        kind = d[0]
        if kind == "lfile":
            act("Open", lambda: self.open_file.emit(tuple(d)))
            act("Rename…", lambda: self.rename.emit(tuple(d)))
            act("Show in Windows Explorer", lambda: self.reveal.emit(tuple(d)))
            menu.addSeparator()
            act("Delete file…", lambda: self.delete.emit(tuple(d)))
        elif kind == "_dir":
            act("New folder…", lambda: self.new_folder.emit(tuple(d)))
            act("New file…", lambda: self.new_file.emit(tuple(d)))
            act("Import files here…", lambda: self.import_here.emit(tuple(d)))
            if d[2]:
                act("Rename…", lambda: self.rename.emit(tuple(d)))
            act("Show in Windows Explorer", lambda: self.reveal.emit(tuple(d)))
            if d[2]:
                menu.addSeparator()
                act("Delete folder…", lambda: self.delete.emit(tuple(d)))
        else:
            if kind == "file":
                act("Open", lambda: self.open_file.emit(tuple(d)))
            act("Show in Windows Explorer", lambda: self.reveal.emit(tuple(d)))
            menu.addSeparator()
            label = {"_profile": "Delete profile folder…", "_project": "Delete project…",
                     "_folder": "Delete folder contents…"}.get(kind, "Delete file…")
            act(label, lambda: self.delete.emit(tuple(d)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
