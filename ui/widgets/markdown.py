"""
Markdown → HTML for chat bubbles, plus the MessageBubble widget.

Supported: headings, bold, italic, inline code, bullet / numbered lists,
fenced code blocks (rendered as separate CodeBlock widgets with Copy /
"Open as proposal" buttons), paragraphs.
"""

import re
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QPlainTextEdit, QSizePolicy)

from ui import theme as T

_FENCE = re.compile(r"```([\w+.-]*)[ \t]*\n(.*?)(?:```|\Z)", re.S)
_INLINE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)")
_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*•]\s+(.*)$")
_NUM = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")


def _inline(text: str) -> str:
    out = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append(f"<b>{html.escape(part[2:-2])}</b>")
        elif part.startswith("`") and part.endswith("`"):
            out.append(f'<code style="background:{T.CODE_BG};color:#e5c07b;font-family:{T.MONO};">'
                       f'{html.escape(part[1:-1])}</code>')
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append(f"<i>{html.escape(part[1:-1])}</i>")
        else:
            out.append(html.escape(part))
    return "".join(out)


def prose_to_html(text: str) -> str:
    lines, out, in_list = text.split("\n"), [], None
    def close():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None
    for line in lines:
        if not line.strip():
            close()
            continue
        hm = _HEAD.match(line)
        if hm:
            close()
            size = {1: 15, 2: 14, 3: 13}.get(len(hm.group(1)), 12)
            out.append(f'<p style="font-size:{size}px;font-weight:bold;color:#fff;margin:6px 0 2px 0">{_inline(hm.group(2))}</p>')
            continue
        bm = _BULLET.match(line)
        if bm:
            close()
            out.append(f"<p style='margin:2px 0 2px 14px'>•&nbsp; {_inline(bm.group(2))}</p>")
            continue
        nm = _NUM.match(line)
        if nm:                       # QLabel's rich text does not number <ol> reliably → explicit numbers
            close()
            out.append(f"<p style='margin:2px 0 2px 14px'><b>{nm.group(2)}.</b>&nbsp; {_inline(nm.group(3))}</p>")
            continue
        close()
        out.append(f"<p style='margin:2px 0'>{_inline(line)}</p>")
    close()
    return "".join(out)


def split_blocks(md: str) -> list:
    """[("prose", text) | ("code", lang, code), …]"""
    blocks, pos = [], 0
    for m in _FENCE.finditer(md):
        if md[pos:m.start()].strip():
            blocks.append(("prose", md[pos:m.start()]))
        blocks.append(("code", m.group(1), m.group(2).rstrip("\n")))
        pos = m.end()
    if md[pos:].strip():
        blocks.append(("prose", md[pos:]))
    return blocks


class CodeBlock(QFrame):
    copy_requested = Signal(str)
    proposal_requested = Signal(str)

    def __init__(self, code: str, lang: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: {T.CODE_BG}; border: 1px solid {T.BORDER}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(10, 4, 6, 2)
        lbl = QLabel(lang or "code"); lbl.setObjectName("dim")
        head.addWidget(lbl); head.addStretch(1)
        b_copy = QPushButton("Copy"); b_copy.setObjectName("flat")
        b_copy.clicked.connect(lambda: self.copy_requested.emit(code))
        head.addWidget(b_copy)
        if (lang or "").lower() in ("abap", "") and len(code.splitlines()) >= 3:
            b_prop = QPushButton("Open as proposal"); b_prop.setObjectName("flat")
            b_prop.setStyleSheet(f"color: {T.CLAUDE_H};")
            b_prop.clicked.connect(lambda: self.proposal_requested.emit(code))
            head.addWidget(b_prop)
        lay.addLayout(head)
        ed = QPlainTextEdit(code)
        ed.setReadOnly(True)
        ed.setFont(QFont(T.MONO, 10))
        ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        ed.setStyleSheet(f"QPlainTextEdit {{ background: {T.CODE_BG}; border: none; padding: 4px 8px; }}")
        ed.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lines = max(1, len(code.splitlines()))
        ed.setFixedHeight(min(lines, 28) * ed.fontMetrics().height() + 22)
        if lang.lower() == "abap" or not lang:
            from ui.highlighter_qt import AbapHighlighter
            self._hl = AbapHighlighter(ed.document())
        lay.addWidget(ed)


class MessageBubble(QFrame):
    """One chat message. role: 'user' | 'ai' | 'tool' | 'meta' | 'error'."""
    copy_requested = Signal(str)
    proposal_requested = Signal(str)

    def __init__(self, role: str, text: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.setObjectName("bubble_user" if role == "user" else "bubble_ai")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 8, 12, 8)
        self._lay.setSpacing(6)
        head = QLabel({"user": "You", "ai": "Claude", "tool": "", "meta": "", "error": "Error"}.get(role, ""))
        head.setObjectName("h")
        if role in ("user", "ai"):
            head.setStyleSheet(f"color: {'#9cdcfe' if role == 'user' else T.CLAUDE_H};")
            self._lay.addWidget(head)
        self._raw = ""
        self._live = None
        if text:
            self.set_markdown(text)

    def _clear(self):
        while self._lay.count() > 1:
            item = self._lay.takeAt(1)
            w = item.widget()
            if w:
                w.deleteLater()

    def append_raw(self, text: str):
        """Streaming: show raw text in a single label until the block is complete."""
        self._raw += text
        if self._live is None:
            self._live = QLabel()
            self._live.setWordWrap(True)
            self._live.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._lay.addWidget(self._live)
        self._live.setText(self._raw)

    def finish_raw(self):
        if self._live is not None:
            self._live.deleteLater()
            self._live = None
        raw, self._raw = self._raw, ""
        if raw.strip():
            self.add_markdown(raw)

    def set_markdown(self, md: str):
        self._clear()
        self.add_markdown(md)

    def add_markdown(self, md: str):
        for blk in split_blocks(md):
            if blk[0] == "prose":
                lbl = QLabel(prose_to_html(blk[1]))
                lbl.setWordWrap(True)
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                lbl.setOpenExternalLinks(True)
                self._lay.addWidget(lbl)
            else:
                cb = CodeBlock(blk[2], blk[1])
                cb.copy_requested.connect(self.copy_requested)
                cb.proposal_requested.connect(self.proposal_requested)
                self._lay.addWidget(cb)

    def add_tool(self, name: str):
        lbl = QLabel(f"⚙  {name} …")
        lbl.setStyleSheet(f"color: {T.MUTED}; font-family: {T.MONO}; font-size: 11px;")
        lbl.setProperty("tool", True)
        self._lay.addWidget(lbl)
        self._last_tool = lbl

    def tool_done(self):
        lbl = getattr(self, "_last_tool", None)
        if lbl and not lbl.text().endswith("✓"):
            lbl.setText(lbl.text().rstrip(" …") + "  ✓")

    def add_meta(self, text: str):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {T.GOOD}; font-family: {T.MONO}; font-size: 11px;")
        self._lay.addWidget(lbl)

    def add_error(self, text: str):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {T.BAD};")
        self._lay.addWidget(lbl)
