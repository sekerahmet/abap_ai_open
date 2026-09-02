"""
ClaudeChatTab — one Claude Code session as a chat: message bubbles, composer with
attachments (files / pasted images), model selector, context toggle, Stop.
"""

import os
import time
import shutil

from PySide6.QtCore import Qt, Signal, QMimeData
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QFrame, QTextEdit, QComboBox, QCheckBox, QFileDialog)

from core.claude_runner import ClaudeSession, find_claude
from ui import theme as T
from ui.bridge import run_bg
from ui.widgets.markdown import MessageBubble

MODELS = [("Default (CLI setting)", ""), ("Fable 5.1", "claude-fable-5-1"), ("Opus 5", "claude-opus-5"),
          ("Sonnet 5", "claude-sonnet-5"), ("Haiku 4.5", "claude-haiku-4-5-20251001")]
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


class Composer(QTextEdit):
    """Input box: Ctrl+Enter sends, Ctrl+V with an image attaches it, drag-drop files attach."""
    send = Signal()
    image_pasted = Signal(object)      # QImage
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText("Message Claude…   Ctrl+Enter to send · Ctrl+V pastes images · drop files to attach")
        self.setStyleSheet("QTextEdit { background: transparent; border: none; }")

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.send.emit()
            return
        super().keyPressEvent(e)

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData):
        if source.hasImage():
            self.image_pasted.emit(QImage(source.imageData()))
            return
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                return
        super().insertFromMimeData(source)


class ClaudeChatTab(QWidget):
    session_updated = Signal(object)      # ClaudeSession (after each turn)
    proposal_requested = Signal(str)
    copy_requested = Signal(str)
    context_requested = Signal(object)    # callable(str) receives context text

    def __init__(self, app, session: ClaudeSession, title: str, parent=None):
        super().__init__(parent)
        self.app = app
        self.session = session
        self.title = title
        self._busy = False
        self._attachments = []          # [(display_name, abs_path)]
        self._bubble = None
        self._streamed = False
        self._in_text = False
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.head_lbl = QLabel(); self.head_lbl.setStyleSheet("font-weight: bold;")
        head.addWidget(self.head_lbl); head.addStretch(1)
        self.ctx_chk = QCheckBox("Attach open code tab as context"); self.ctx_chk.setChecked(True)
        head.addWidget(self.ctx_chk)
        self.stop_btn = QPushButton("Stop"); self.stop_btn.setObjectName("danger"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.session.stop())
        head.addWidget(self.stop_btn)
        lay.addLayout(head)
        self._refresh_head()

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 8px; }}")
        self.feed = QWidget(); self.feed.setStyleSheet(f"background: {T.SURFACE};")
        self.feed_lay = QVBoxLayout(self.feed)
        self.feed_lay.setContentsMargins(12, 12, 12, 12)
        self.feed_lay.setSpacing(10)
        self.feed_lay.addStretch(1)
        self.scroll.setWidget(self.feed)
        lay.addWidget(self.scroll, 1)

        intro = (f"Session for profile **{self.session.profile}** · working directory `{self.session.cwd}`\n"
                 + ("SAP tools attached via MCP." if self.session.has_mcp else
                    "MCP server not found — only cached workspace files are available."))
        if not find_claude():
            intro += "\n\n**Claude Code CLI not installed:** `winget install Anthropic.ClaudeCode`"
        self._add_bubble("meta").set_markdown(intro)

        comp = QFrame(); comp.setObjectName("composer")
        cl = QVBoxLayout(comp); cl.setContentsMargins(10, 8, 10, 8); cl.setSpacing(6)
        self.chips = QHBoxLayout(); self.chips.setSpacing(6); self.chips.addStretch(1)
        cl.addLayout(self.chips)
        self.input = Composer(); self.input.setFixedHeight(84)
        self.input.send.connect(self.send)
        self.input.image_pasted.connect(self._attach_image)
        self.input.files_dropped.connect(lambda ps: [self._attach_file(p) for p in ps])
        cl.addWidget(self.input)
        row = QHBoxLayout()
        b_add = QPushButton("+"); b_add.setObjectName("flat"); b_add.setFixedWidth(30)
        b_add.setToolTip("Attach a file"); b_add.clicked.connect(self._pick_file)
        row.addWidget(b_add)
        self.model = QComboBox()
        for label, mid in MODELS:
            self.model.addItem(label, mid)
        self.model.setFixedWidth(180)
        self.model.currentIndexChanged.connect(lambda _i: setattr(self.session, "model", self.model.currentData()))
        row.addWidget(self.model)
        self.mode_lbl = QLabel("read-only tools · MCP"); self.mode_lbl.setObjectName("dim")
        row.addWidget(self.mode_lbl)
        row.addStretch(1)
        self.send_btn = QPushButton("Send  ⏎"); self.send_btn.setObjectName("claude")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        cl.addLayout(row)
        lay.addWidget(comp)
        self.input.setFocus()

    def _refresh_head(self):
        sid = (self.session.session_id or "new")[:8]
        self.head_lbl.setText(f"{self.title}   ·   session {sid}")

    def _add_bubble(self, role: str, text: str = "") -> MessageBubble:
        b = MessageBubble(role, text)
        b.copy_requested.connect(self.copy_requested)
        b.proposal_requested.connect(self.proposal_requested)
        self.feed_lay.insertWidget(self.feed_lay.count() - 1, b)
        self._scroll_bottom()
        return b

    def _scroll_bottom(self):
        sb = self.scroll.verticalScrollBar()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(30, lambda: sb.setValue(sb.maximum()))

    # ── attachments ───────────────────────────────────────────────────────────
    def _attach_dir(self) -> str:
        d = os.path.join(self.session.cwd, "_attachments")
        os.makedirs(d, exist_ok=True)
        return d

    def _pick_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        for p in paths:
            self._attach_file(p)

    def _attach_file(self, path: str):
        if not os.path.isfile(path):
            return
        name = os.path.basename(path)
        dest = os.path.join(self._attach_dir(), f"{int(time.time())}_{name}")
        try:
            shutil.copy2(path, dest)
        except OSError:
            return
        self._add_chip(name, dest)

    def _attach_image(self, img):
        if img is None or img.isNull():
            return
        dest = os.path.join(self._attach_dir(), f"paste_{int(time.time() * 1000)}.png")
        if img.save(dest, "PNG"):
            self._add_chip(f"image {img.width()}×{img.height()}", dest)

    def _add_chip(self, label: str, path: str):
        chip = QFrame(); chip.setObjectName("chip")
        hl = QHBoxLayout(chip); hl.setContentsMargins(8, 2, 4, 2); hl.setSpacing(4)
        glyph = "🖼" if path.lower().endswith(_IMG_EXT) else "📎"
        hl.addWidget(QLabel(f"{glyph} {label}"))
        x = QPushButton("✕"); x.setObjectName("flat"); x.setFixedWidth(22)
        hl.addWidget(x)
        entry = (label, path, chip)
        self._attachments.append(entry)
        x.clicked.connect(lambda: self._remove_chip(entry))
        self.chips.insertWidget(self.chips.count() - 1, chip)

    def _remove_chip(self, entry):
        if entry in self._attachments:
            self._attachments.remove(entry)
        entry[2].deleteLater()

    # ── send / receive ────────────────────────────────────────────────────────
    def send(self):
        if self._busy:
            return
        text = self.input.toPlainText().strip()
        if not text and not self._attachments:
            return
        self.input.clear()

        parts = []
        if self.ctx_chk.isChecked():
            ctx = self.app.get_active_code_context()
            if ctx:
                parts.append(ctx)
        if self._attachments:
            rel = [os.path.relpath(p, self.session.cwd) for _, p, _ in self._attachments]
            parts.append("[Attached files — read them with the Read tool (images are viewable)]:\n"
                         + "\n".join(f"- {r}" for r in rel))
        parts.append(text or "(see attachments)")
        prompt = "\n\n".join(parts)

        shown = text or "(attachments only)"
        if self._attachments:
            shown += "\n\n" + "\n".join(f"📎 {n}" for n, _, _ in self._attachments)
        self._add_bubble("user").set_markdown(shown)
        for _, _, chip in list(self._attachments):
            chip.deleteLater()
        self._attachments.clear()

        self._bubble = self._add_bubble("ai")
        self._streamed = False
        self._in_text = False
        self._busy = True
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        ui = self.app.ui
        run_bg(self.session.send, prompt,
               lambda ev: ui.call(self._on_event, ev),
               lambda res: ui.call(self._on_done, res))

    def _on_event(self, ev: dict):
        t = ev.get("type")
        b = self._bubble
        if t == "stream_event":
            e = ev.get("event", {}); et = e.get("type")
            if et == "content_block_start":
                cb = e.get("content_block", {})
                if cb.get("type") == "text":
                    self._in_text = True
                elif cb.get("type") == "tool_use":
                    b.finish_raw(); self._in_text = False
                    name = cb.get("name", "tool")
                    b.add_tool(name.split("__")[-1] if "__" in name else name)
            elif et == "content_block_delta":
                d = e.get("delta", {})
                if d.get("type") == "text_delta":
                    b.append_raw(d.get("text", "")); self._streamed = True; self._in_text = True
            elif et == "content_block_stop":
                if self._in_text:
                    b.finish_raw(); self._in_text = False
            self._scroll_bottom()
        elif t == "user":
            blocks = ev.get("message", {}).get("content", [])
            if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in blocks):
                b.tool_done()
        elif t == "rate_limit_event":
            self.app.usage_updated(self.session)

    def _on_done(self, res: dict):
        b = self._bubble
        b.finish_raw()
        self._busy = False
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if res.get("is_error"):
            b.add_error(str(res.get("result", "")))
        elif not self._streamed and res.get("result"):
            b.add_markdown(str(res["result"]))
        cost = res.get("total_cost_usd"); turns = res.get("num_turns"); ms = res.get("duration_ms")
        bits = []
        if turns is not None:
            bits.append(f"turns={turns}")
        if isinstance(cost, (int, float)):
            bits.append(f"API-equivalent ~${cost:.3f}" + (" (not billed)" if self.app.is_subscription() else ""))
        if isinstance(ms, (int, float)):
            bits.append(f"{ms / 1000:.1f}s")
        if bits:
            b.add_meta("— " + "  ".join(bits))
        self._refresh_head()
        self._scroll_bottom()
        self.session_updated.emit(self.session)

    def closeEvent(self, e):
        self.session.stop()
        super().closeEvent(e)
