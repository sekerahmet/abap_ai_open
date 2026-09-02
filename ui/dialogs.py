"""Connection-profile dialog and paste-code dialog."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
                               QComboBox, QPushButton, QMessageBox, QPlainTextEdit, QDialogButtonBox,
                               QScrollArea, QWidget, QFrame, QCheckBox, QApplication)

from ui import theme as T


CONN_FIELDS = [
    ("ashost", "App Server", "10.x.x.x or host.domain"),
    ("sysnr",  "System Nr", "00"),
    ("client", "Client", "100"),
    ("user",   "User", "SAPUSER"),
    ("passwd", "Password", ""),
    ("router", "SAP Router (optional)", "/H/host/S/3299"),
]
_BAD = '\\/:*?"<>|'


class ConnectionDialog(QDialog):
    """Create / edit / delete SAP connection profiles. Talks to app.save_profile / delete_profile."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Connection profiles")
        self.setMinimumWidth(420)
        self._entries = {}
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        row = QHBoxLayout()
        self.pick = QComboBox()
        self.pick.addItems(app.sap_profiles() or [])
        self.pick.currentTextChanged.connect(self._load)
        b_new = QPushButton("New"); b_new.setObjectName("ok"); b_new.clicked.connect(self._new)
        row.addWidget(QLabel("Profile")); row.addWidget(self.pick, 1); row.addWidget(b_new)
        lay.addLayout(row)

        form = QFormLayout()
        form.setSpacing(8)
        self.name_edit = QLineEdit()
        form.addRow("Profile name", self.name_edit)
        for attr, label, ph in CONN_FIELDS:
            e = QLineEdit(); e.setPlaceholderText(ph)
            if attr == "passwd":
                e.setEchoMode(QLineEdit.EchoMode.Password)
            self._entries[attr] = e
            form.addRow(label, e)
        lay.addLayout(form)

        note = QLabel("Read-only connection — nothing is written to SAP."); note.setObjectName("dim")
        lay.addWidget(note)

        btns = QHBoxLayout()
        b_del = QPushButton("Delete"); b_del.setObjectName("danger"); b_del.clicked.connect(self._delete)
        b_close = QPushButton("Close"); b_close.clicked.connect(self.reject)
        b_save = QPushButton("Save & use"); b_save.setObjectName("accent"); b_save.clicked.connect(self._save)
        btns.addWidget(b_del); btns.addStretch(1); btns.addWidget(b_close); btns.addWidget(b_save)
        lay.addLayout(btns)

        cur = app.active_profile()
        if cur in app.sap_profiles():
            self.pick.setCurrentText(cur)
            self._load(cur)
        elif self.pick.count():
            self._load(self.pick.currentText())
        else:
            self._new()

    def _fill(self, name, data):
        self.name_edit.setText(name)
        for attr, e in self._entries.items():
            val = data.get(attr)
            if val is None and attr == "router":
                val = data.get("saprouter", "")
            e.setText(str(val or ""))

    def _load(self, name):
        if name in self.app.systems_data:
            self._fill(name, self.app.systems_data[name])

    def _new(self):
        self._fill("", {})
        self.name_edit.setFocus()

    def _save(self):
        name = self.name_edit.text().strip()
        if not name or any(c in name for c in _BAD):
            QMessageBox.warning(self, "Profile name", f"Enter a profile name without {_BAD}")
            return
        data = {attr: e.text().strip() for attr, e in self._entries.items()}
        if not data.get("ashost"):
            QMessageBox.warning(self, "App Server", "App Server is required.")
            return
        self.app.save_profile(name, data)
        self.accept()

    def _delete(self):
        name = self.pick.currentText()
        if name not in self.app.systems_data:
            return
        if QMessageBox.question(self, "Delete profile", f"Delete profile '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.app.delete_profile(name)
        self.pick.clear()
        self.pick.addItems(self.app.sap_profiles())
        if self.pick.count():
            self._load(self.pick.currentText())
        else:
            self._new()


class PasteCodeDialog(QDialog):
    """Paste ABAP source and give it a name → saved to the workspace and opened as a tab."""

    def __init__(self, parent=None, default_name=""):
        super().__init__(parent)
        self.setWindowTitle("Paste code")
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Object name"))
        self.name_edit = QLineEdit(default_name); self.name_edit.setPlaceholderText("ZPROGRAM_NAME")
        row.addWidget(self.name_edit, 1)
        row.addWidget(QLabel("Type"))
        self.type_box = QComboBox(); self.type_box.addItems(["Program", "Global Class", "Function Module"])
        row.addWidget(self.type_box)
        lay.addLayout(row)
        self.text = QPlainTextEdit(); self.text.setObjectName("code")
        self.text.setPlaceholderText("Paste ABAP source here…")
        lay.addWidget(self.text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.result_name = self.result_code = self.result_type = None

    def _ok(self):
        name = self.name_edit.text().strip().upper()
        code = self.text.toPlainText()
        if not name or any(c in name for c in _BAD + " "):
            QMessageBox.warning(self, "Name", "Enter a valid object name (no spaces or special characters).")
            return
        if not code.strip():
            QMessageBox.warning(self, "Code", "Paste some code first.")
            return
        self.result_name, self.result_code, self.result_type = name, code, self.type_box.currentText()
        self.accept()


# ── Setup check ───────────────────────────────────────────────────────────────

_LEVEL_GLYPH = {"ok": ("✓", T.GOOD), "info": ("○", T.MUTED), "warn": ("⚠", T.WARN), "error": ("✗", T.BAD)}
_KURULUM_URL = "https://github.com/sekerahmet/abap_ai_open/blob/main/KURULUM.md"


class SetupCheckDialog(QDialog):
    """
    Non-modal overview of the environment (RFC SDK, Claude CLI, Python/MCP, Git, .env) with
    fix hints and copyable commands.  app must provide run_setup_check(show) and
    setup_startup_flag / set_setup_startup_flag(bool).
    """

    def __init__(self, app, results, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Setup check")
        self.setMinimumSize(640, 420)
        self.resize(720, 520)
        lay = QVBoxLayout(self); lay.setSpacing(8)
        head = QLabel("What this machine can do right now. Nothing here is required to browse local files.")
        head.setObjectName("muted"); head.setWordWrap(True)
        lay.addWidget(head)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(self.scroll, 1)
        self.startup_chk = QCheckBox("Check at startup and open this window when something a configured feature needs is missing")
        self.startup_chk.setChecked(bool(app.setup_startup_flag()))
        self.startup_chk.toggled.connect(app.set_setup_startup_flag)
        lay.addWidget(self.startup_chk)
        row = QHBoxLayout()
        self.b_recheck = QPushButton("⟳ Re-check"); self.b_recheck.setObjectName("accent")
        self.b_recheck.clicked.connect(self._recheck)
        b_doc = QPushButton("Open KURULUM.md"); b_doc.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(_KURULUM_URL)))
        b_close = QPushButton("Close"); b_close.clicked.connect(self.close)
        row.addWidget(self.b_recheck); row.addWidget(b_doc); row.addStretch(1); row.addWidget(b_close)
        lay.addLayout(row)
        self.set_results(results)

    def _recheck(self):
        self.b_recheck.setEnabled(False); self.b_recheck.setText("Checking…")
        self.app.run_setup_check(show=False)

    def set_results(self, results):
        self.b_recheck.setEnabled(True); self.b_recheck.setText("⟳ Re-check")
        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(8)
        if results is None:
            lbl = QLabel("Checking…"); lbl.setObjectName("muted"); bl.addWidget(lbl)
        for r in results or []:
            bl.addWidget(self._card(r))
        bl.addStretch(1)
        self.scroll.setWidget(body)

    def _card(self, r: dict) -> QFrame:
        card = QFrame(); card.setObjectName("card")
        cl = QVBoxLayout(card); cl.setContentsMargins(12, 8, 12, 8); cl.setSpacing(4)
        glyph, color = _LEVEL_GLYPH.get(r["level"], ("•", T.TEXT))
        top = QHBoxLayout()
        g = QLabel(glyph); g.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: bold;"); g.setFixedWidth(22)
        name = QLabel(r["label"]); name.setStyleSheet("font-weight: bold;")
        st = QLabel(r["status"]); st.setStyleSheet(f"color: {color};")
        top.addWidget(g); top.addWidget(name); top.addSpacing(8); top.addWidget(st, 1)
        cl.addLayout(top)
        if r.get("detail"):
            d = QLabel(r["detail"]); d.setObjectName("dim"); d.setWordWrap(True)
            d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); cl.addWidget(d)
        if r.get("fix"):
            f = QLabel(r["fix"]); f.setWordWrap(True)
            f.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); cl.addWidget(f)
        if r.get("cmd"):
            row = QHBoxLayout()
            e = QLineEdit(r["cmd"]); e.setReadOnly(True); e.setStyleSheet(f'font-family: "{T.MONO}";')
            b = QPushButton("Copy"); b.setFixedWidth(60)
            b.clicked.connect(lambda _c=False, t=r["cmd"], btn=b: (QApplication.clipboard().setText(t), btn.setText("Copied")))
            row.addWidget(e, 1); row.addWidget(b)
            cl.addLayout(row)
        return card
