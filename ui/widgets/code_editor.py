"""
CodeView — QPlainTextEdit with line numbers, current-line highlight and a
Ctrl+F find bar. `view.editor` is the QPlainTextEdit.
"""

from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import (QColor, QPainter, QTextFormat, QFont, QTextCursor, QTextDocument,
                           QKeySequence, QShortcut, QFontMetrics)
from PySide6.QtWidgets import (QPlainTextEdit, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                               QLabel, QPushButton, QTextEdit)

from ui import theme as T
from ui.highlighter_qt import AbapHighlighter, DiffHighlighter


class _LineArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_area_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_area(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("code")
        font = QFont(T.MONO, 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)
        self._area = _LineArea(self)
        self.blockCountChanged.connect(self._update_width)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_width(0)
        self._highlight_line()

    def line_area_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_width(self, _n):
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)

    def _update_area(self, rect, dy):
        if dy:
            self._area.scroll(0, dy)
        else:
            self._area.update(0, rect.y(), self._area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_width(0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._area.setGeometry(QRect(cr.left(), cr.top(), self.line_area_width(), cr.height()))

    def paint_line_area(self, event):
        p = QPainter(self._area)
        p.fillRect(event.rect(), QColor(T.PANEL))
        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        cur = self.textCursor().blockNumber()
        w = self._area.width()
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(T.TEXT if n == cur else T.DIM))
                p.drawText(0, top, w - 8, h, Qt.AlignmentFlag.AlignRight, str(n + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1

    def _highlight_line(self):
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(T.ACTIVE_LINE))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel] + getattr(self, "_find_sels", []))

    def goto_line(self, line: int):
        block = self.document().findBlockByNumber(max(0, line - 1))
        cur = QTextCursor(block)
        self.setTextCursor(cur)
        self.centerCursor()
        self.setFocus()


class CodeView(QWidget):
    """Editor + find bar. mode: 'abap' | 'diff' | 'plain'."""
    modified = Signal()

    def __init__(self, code: str = "", mode: str = "abap", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.findbar = QWidget()
        fl = QHBoxLayout(self.findbar)
        fl.setContentsMargins(8, 4, 8, 4)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find…   Enter = next · Shift+Enter = previous · Esc = close")
        self.find_count = QLabel("")
        self.find_count.setObjectName("muted")
        b_prev = QPushButton("▲"); b_next = QPushButton("▼"); b_close = QPushButton("✕")
        for b in (b_prev, b_next, b_close):
            b.setObjectName("flat"); b.setFixedWidth(28)
        fl.addWidget(QLabel("Find")); fl.addWidget(self.find_edit, 1); fl.addWidget(self.find_count)
        fl.addWidget(b_prev); fl.addWidget(b_next); fl.addWidget(b_close)
        self.findbar.hide()
        lay.addWidget(self.findbar)

        self.editor = CodeEditor()
        self.editor.setPlainText(code)
        self.editor.setReadOnly(True)
        lay.addWidget(self.editor, 1)
        if mode == "abap":
            self._hl = AbapHighlighter(self.editor.document())
        elif mode == "diff":
            self._hl = DiffHighlighter(self.editor.document())
        self.editor.document().modificationChanged.connect(lambda _m: self.modified.emit())

        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.show_find)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self.find_next(True))
        QShortcut(QKeySequence("Shift+F3"), self, activated=lambda: self.find_next(False))
        QShortcut(QKeySequence("Escape"), self.find_edit, activated=self.hide_find)
        self.find_edit.returnPressed.connect(lambda: self.find_next(True))
        self.find_edit.textChanged.connect(self._mark_all)
        b_next.clicked.connect(lambda: self.find_next(True))
        b_prev.clicked.connect(lambda: self.find_next(False))
        b_close.clicked.connect(self.hide_find)

    # ── API ───────────────────────────────────────────────────────────────────
    def get(self) -> str:
        return self.editor.toPlainText()

    def set_editable(self, editable: bool):
        self.editor.setReadOnly(not editable)
        if editable:
            self.editor.setFocus()

    def goto(self, line: int):
        self.editor.goto_line(line)

    # ── Find ──────────────────────────────────────────────────────────────────
    def show_find(self):
        self.findbar.show()
        sel = self.editor.textCursor().selectedText()
        if sel and " " not in sel:
            self.find_edit.setText(sel)
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self._mark_all()

    def hide_find(self):
        self.findbar.hide()
        self.editor._find_sels = []
        self.editor._highlight_line()
        self.editor.setFocus()

    def _mark_all(self):
        needle = self.find_edit.text()
        sels = []
        if needle:
            doc = self.editor.document()
            cur = QTextCursor(doc)
            while True:
                cur = doc.find(needle, cur)
                if cur.isNull():
                    break
                s = QTextEdit.ExtraSelection()
                s.format.setBackground(QColor("#5a5a1a"))
                s.cursor = cur
                sels.append(s)
        self.editor._find_sels = sels
        self.editor._highlight_line()
        self.find_count.setText(f"{len(sels)} match{'es' if len(sels) != 1 else ''}" if needle else "")

    def find_next(self, forward: bool = True):
        needle = self.find_edit.text()
        if not needle:
            return
        flags = QTextDocument.FindFlag(0) if forward else QTextDocument.FindFlag.FindBackward
        if not self.editor.find(needle, flags):
            cur = self.editor.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start if forward else QTextCursor.MoveOperation.End)
            self.editor.setTextCursor(cur)
            self.editor.find(needle, flags)
        self.editor.centerCursor()
