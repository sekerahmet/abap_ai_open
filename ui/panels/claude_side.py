"""
Claude side panel (left dock): account & usage, session manager.
"""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
                               QListWidget, QListWidgetItem, QLineEdit, QFrame, QMenu, QInputDialog)

from core.claude_runner import auth_info, load_usage, format_reset, USAGE_WINDOWS, find_claude


class UsageRow(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)
        top = QHBoxLayout()
        self.name = QLabel(label); self.name.setObjectName("muted")
        self.pct = QLabel("—"); self.pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(self.name); top.addStretch(1); top.addWidget(self.pct)
        lay.addLayout(top)
        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.setValue(0)
        self.bar.setTextVisible(False); self.bar.setFixedHeight(6)
        lay.addWidget(self.bar)
        self.reset = QLabel(""); self.reset.setObjectName("dim")
        lay.addWidget(self.reset)

    def set(self, util: float, resets):
        pct = int(round(max(0.0, min(1.0, util)) * 100))
        self.bar.setValue(pct)
        self.bar.setObjectName("bad" if pct >= 90 else "warn" if pct >= 70 else "")
        self.bar.style().unpolish(self.bar); self.bar.style().polish(self.bar)
        self.pct.setText(f"{pct}%")
        self.reset.setText(format_reset(resets))


class ClaudeSidePanel(QWidget):
    new_session = Signal()
    open_session = Signal(str, str)      # session_id, title
    forget_session = Signal(str)         # session_id  (delete from the list, with confirmation)
    rename_session = Signal(str, str)    # session_id, new title

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        h = QLabel("ACCOUNT & USAGE"); h.setObjectName("h")
        lay.addWidget(h)
        card = QFrame(); card.setObjectName("card")
        cl = QVBoxLayout(card); cl.setContentsMargins(10, 8, 10, 8); cl.setSpacing(4)
        self.email = QLabel("—"); self.plan = QLabel(""); self.plan.setObjectName("muted")
        cl.addWidget(self.email); cl.addWidget(self.plan)
        self.rows = {}
        for key, label in USAGE_WINDOWS:
            r = UsageRow(label); self.rows[key] = r; cl.addWidget(r)
        self.stamp = QLabel(""); self.stamp.setObjectName("dim"); cl.addWidget(self.stamp)
        lay.addWidget(card)

        h2 = QHBoxLayout()
        hs = QLabel("SESSIONS"); hs.setObjectName("h")
        b_new = QPushButton("+ New session"); b_new.setObjectName("claude")
        b_new.clicked.connect(self.new_session)
        h2.addWidget(hs); h2.addStretch(1); h2.addWidget(b_new)
        lay.addLayout(h2)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search sessions…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open)
        self.list.itemActivated.connect(self._open)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.setToolTip("Double-click: open · Right-click: rename / delete · Del: delete")
        QShortcut(QKeySequence.StandardKey.Delete, self.list, activated=self._forget)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        hint = QLabel("double-click opens · right-click for more"); hint.setObjectName("dim")
        b_forget = QPushButton("🗑  Delete"); b_forget.setObjectName("flat")
        b_forget.setToolTip("Delete the selected session from this list (Del)")
        b_forget.clicked.connect(self._forget)
        row.addWidget(hint); row.addStretch(1); row.addWidget(b_forget)
        lay.addLayout(row)
        self.refresh_account()

    # ── account / usage ───────────────────────────────────────────────────────
    def refresh_account(self):
        info = auth_info()
        if not find_claude():
            self.email.setText("Claude Code CLI not installed")
            self.plan.setText("winget install Anthropic.ClaudeCode")
        elif not info.get("loggedIn"):
            self.email.setText("Not logged in")
            self.plan.setText("run `claude` once to sign in")
        else:
            self.email.setText(info.get("email", "signed in"))
            if info.get("authMethod") == "claude.ai":
                self.plan.setText(f"{info.get('subscriptionType', 'subscription')} plan · not billed per request")
            else:
                self.plan.setText("API key · pay per token")
        usage, ts = load_usage()
        self.set_usage(usage, ts)

    def set_usage(self, usage: dict, saved_at=None):
        any_row = False
        for key, row in self.rows.items():
            info = usage.get(key)
            row.setVisible(bool(info))
            if info:
                row.set(info[0], info[1]); any_row = True
        if not any_row:
            self.stamp.setText("usage appears after the first message")
        elif saved_at:
            age = int(time.time()) - int(saved_at)
            self.stamp.setText("live" if age < 120 else f"as of {time.strftime('%H:%M', time.localtime(saved_at))}")
        else:
            self.stamp.setText("")

    # ── sessions ──────────────────────────────────────────────────────────────
    def set_sessions(self, sessions: list):
        self._sessions = sessions
        self._refill()

    def _refill(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for s in self._sessions:
            label = f"{s.get('title', '?')}    {s.get('last', '')}"
            if q and q not in label.lower():
                continue
            it = QListWidgetItem(f"●  {label}")
            it.setData(Qt.ItemDataRole.UserRole, s)
            it.setToolTip(s.get("id", ""))
            self.list.addItem(it)

    def _open(self, item):
        s = item.data(Qt.ItemDataRole.UserRole)
        if s:
            self.open_session.emit(s["id"], s.get("title", ""))

    def _forget(self):
        it = self.list.currentItem()
        s = it.data(Qt.ItemDataRole.UserRole) if it else None
        if s:
            self.forget_session.emit(s["id"])

    def _rename(self):
        it = self.list.currentItem()
        s = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not s:
            return
        title, ok = QInputDialog.getText(self, "Rename session", "Title:", text=s.get("title", ""))
        if ok and title.strip():
            self.rename_session.emit(s["id"], title.strip())

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        self.list.setCurrentItem(it)
        menu = QMenu(self)
        a = QAction("Open", menu); a.triggered.connect(lambda: self._open(it)); menu.addAction(a)
        a = QAction("Rename…", menu); a.triggered.connect(self._rename); menu.addAction(a)
        menu.addSeparator()
        a = QAction("Delete…", menu); a.triggered.connect(self._forget); menu.addAction(a)
        menu.exec(self.list.viewport().mapToGlobal(pos))
