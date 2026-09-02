"""ABAP and unified-diff syntax highlighters for QPlainTextEdit."""

import re
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont

from utils.highlighter import ABAPHighlighter as _Tk   # keyword set only
from ui import theme as T


def _fmt(color: str, bold=False, italic=False, bg: str = None) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    if bg:
        f.setBackground(QColor(bg))
    return f


class AbapHighlighter(QSyntaxHighlighter):
    KEYWORDS = _Tk.KEYWORDS
    _WORD = re.compile(r"[A-Za-z_][\w\-]*")
    _NUM = re.compile(r"\b\d+\b")

    def __init__(self, doc):
        super().__init__(doc)
        self.f_kw = _fmt("#569cd6")
        self.f_str = _fmt("#ce9178")
        self.f_cmt = _fmt("#6a9955", italic=True)
        self.f_num = _fmt("#b5cea8")

    def highlightBlock(self, line: str):
        if line.startswith("*"):
            self.setFormat(0, len(line), self.f_cmt)
            return
        i, n = 0, len(line)
        while i < n:
            ch = line[i]
            if ch in ("'", "`"):
                j = i + 1
                while j < n:
                    if line[j] == ch:
                        if ch == "'" and j + 1 < n and line[j + 1] == "'":
                            j += 2
                            continue
                        break
                    j += 1
                self.setFormat(i, min(j + 1, n) - i, self.f_str)
                i = j + 1
                continue
            if ch == "|":
                j = line.find("|", i + 1)
                j = n - 1 if j < 0 else j
                self.setFormat(i, j + 1 - i, self.f_str)
                i = j + 1
                continue
            if ch == '"':
                self.setFormat(i, n - i, self.f_cmt)
                return
            if ch.isalpha() or ch == "_":
                m = self._WORD.match(line, i)
                if m.group(0).upper() in self.KEYWORDS:
                    self.setFormat(i, m.end() - i, self.f_kw)
                i = m.end()
                continue
            if ch.isdigit():
                m = self._NUM.match(line, i)
                if m:
                    self.setFormat(i, m.end() - i, self.f_num)
                    i = m.end()
                    continue
            i += 1


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.f_add = _fmt(T.GOOD, bg="#1a3a1a")
        self.f_del = _fmt(T.BAD, bg="#3a1a1a")
        self.f_hdr = _fmt("#569cd6", bold=True)
        self.f_meta = _fmt(T.DIM)

    def highlightBlock(self, line: str):
        if line.startswith(("+++", "---")):
            self.setFormat(0, len(line), self.f_meta)
        elif line.startswith("@@"):
            self.setFormat(0, len(line), self.f_hdr)
        elif line.startswith("+"):
            self.setFormat(0, len(line), self.f_add)
        elif line.startswith("-"):
            self.setFormat(0, len(line), self.f_del)
