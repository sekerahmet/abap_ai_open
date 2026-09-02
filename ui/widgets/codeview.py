"""
CodeView — tk.Text with a line-number gutter, active-line highlight and a
Ctrl+F find bar. Used for every code tab.

    view = CodeView(parent, code)
    view.text            # the underlying tk.Text (highlighter / tags work on it)
    view.get()           # current content
    view.set_editable(True / False)
    view.goto(line)      # scroll + highlight a line
"""

import tkinter as tk
import customtkinter as ctk

from ui import theme as T


class CodeView(ctk.CTkFrame):
    def __init__(self, parent, code: str = "", font=T.FONT_MONO):
        super().__init__(parent, fg_color=T.SURFACE, corner_radius=6)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._font = font
        self._editable = False

        # ── Find bar (hidden until Ctrl+F) ───────────────────────────────────
        self.findbar = ctk.CTkFrame(self, fg_color=T.PANEL_ALT, corner_radius=0, height=32)
        self.findbar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.findbar, text="Find", font=ctk.CTkFont(size=11),
                     text_color=T.MUTED).grid(row=0, column=0, padx=(10, 6))
        self.find_entry = ctk.CTkEntry(self.findbar, height=26, placeholder_text="text…  (Enter next · Shift+Enter prev · Esc close)")
        self.find_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self.find_count = ctk.StringVar(value="")
        ctk.CTkLabel(self.findbar, textvariable=self.find_count, font=ctk.CTkFont(size=11),
                     text_color=T.MUTED, width=90).grid(row=0, column=2, padx=6)
        ctk.CTkButton(self.findbar, text="▲", width=28, height=24, fg_color=T.PANEL,
                      hover_color=T.BORDER, command=lambda: self.find_next(-1)).grid(row=0, column=3, padx=2)
        ctk.CTkButton(self.findbar, text="▼", width=28, height=24, fg_color=T.PANEL,
                      hover_color=T.BORDER, command=lambda: self.find_next(1)).grid(row=0, column=4, padx=2)
        ctk.CTkButton(self.findbar, text="✕", width=28, height=24, fg_color=T.PANEL,
                      hover_color=T.DANGER_HOVER, command=self.hide_find).grid(row=0, column=5, padx=(2, 8))
        self.find_entry.bind("<Return>", lambda _e: self.find_next(1))
        self.find_entry.bind("<Shift-Return>", lambda _e: self.find_next(-1))
        self.find_entry.bind("<Escape>", lambda _e: self.hide_find())
        self.find_entry.bind("<KeyRelease>", self._on_find_typed)

        # ── Gutter + text ─────────────────────────────────────────────────────
        self.gutter = tk.Canvas(self, width=56, bg=T.PANEL, highlightthickness=0, bd=0)
        self.gutter.grid(row=1, column=0, sticky="ns", padx=(4, 0), pady=4)

        self.text = tk.Text(self, font=font, bg=T.SURFACE, fg=T.TEXT, insertbackground="#ffffff",
                            selectbackground=T.SELECT, selectforeground="#ffffff",
                            wrap="none", undo=True, bd=0, highlightthickness=0, padx=8, pady=4)
        self.text.grid(row=1, column=1, sticky="nsew", pady=4)

        vsb = ctk.CTkScrollbar(self, command=self._yview)
        vsb.grid(row=1, column=2, sticky="ns", pady=4)
        hsb = ctk.CTkScrollbar(self, orientation="horizontal", command=self.text.xview)
        hsb.grid(row=2, column=1, sticky="ew", padx=4)
        self.text.configure(yscrollcommand=lambda a, b: (vsb.set(a, b), self._redraw_gutter()),
                            xscrollcommand=hsb.set)

        self.text.tag_config("active_line", background=T.ACTIVE_LINE)
        self.text.tag_config("find", background=T.FIND)
        self.text.tag_config("find_cur", background=T.FIND_CUR)
        self.text.tag_config("hl", background="#4a4a00")
        self.text.tag_raise("sel")

        self.text.insert("1.0", code)
        self.text.edit_reset()
        self.text.configure(state="disabled")

        for seq in ("<KeyRelease>", "<ButtonRelease-1>", "<Configure>", "<<Modified>>"):
            self.text.bind(seq, self._on_change, add="+")
        self.text.bind("<Control-f>", lambda _e: (self.show_find(), "break")[1])
        self.text.bind("<Control-F>", lambda _e: (self.show_find(), "break")[1])
        self.text.bind("<F3>", lambda _e: (self.find_next(1), "break")[1])
        self.text.bind("<Shift-F3>", lambda _e: (self.find_next(-1), "break")[1])
        self.text.bind("<Escape>", lambda _e: self.hide_find())
        self.gutter.bind("<MouseWheel>", lambda e: self.text.yview_scroll(-int(e.delta / 120) * 3, "units"))
        self.after(50, self._redraw_gutter)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_editable(self, editable: bool):
        self._editable = editable
        self.text.configure(state="normal" if editable else "disabled")
        if editable:
            self.text.focus_set()

    def goto(self, line: int):
        self.text.tag_remove("hl", "1.0", "end")
        self.text.tag_add("hl", f"{line}.0", f"{line}.end")
        self.text.see(f"{line}.0")
        self.text.mark_set("insert", f"{line}.0")
        self._highlight_active_line()
        self._redraw_gutter()

    # ── Internals ─────────────────────────────────────────────────────────────

    def _yview(self, *args):
        self.text.yview(*args)
        self._redraw_gutter()

    def _on_change(self, _event=None):
        try:
            self.text.edit_modified(False)
        except tk.TclError:
            pass
        self._highlight_active_line()
        self._redraw_gutter()

    def _highlight_active_line(self):
        self.text.tag_remove("active_line", "1.0", "end")
        self.text.tag_add("active_line", "insert linestart", "insert lineend+1c")

    def _redraw_gutter(self):
        g = self.gutter
        g.delete("all")
        try:
            total = int(self.text.index("end-1c").split(".")[0])
        except tk.TclError:
            return
        width = max(3, len(str(total))) * 8 + 16
        g.configure(width=width)
        cur = int(self.text.index("insert").split(".")[0])
        idx = self.text.index("@0,0")
        while True:
            info = self.text.dlineinfo(idx)
            if info is None:
                break
            y = info[1]
            ln = int(idx.split(".")[0])
            g.create_text(width - 8, y + info[3] // 2, anchor="e", text=str(ln),
                          fill=T.TEXT if ln == cur else T.DIM, font=T.FONT_MONO_SM)
            idx = self.text.index(f"{idx}+1line")
            if int(idx.split(".")[0]) > total:
                break

    # ── Find ──────────────────────────────────────────────────────────────────

    def show_find(self):
        self.findbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        try:
            sel = self.text.get("sel.first", "sel.last")
            if sel and "\n" not in sel:
                self.find_entry.delete(0, "end")
                self.find_entry.insert(0, sel)
        except tk.TclError:
            pass
        self.find_entry.focus_set()
        self.find_entry.select_range(0, "end")
        self._mark_all()

    def hide_find(self):
        self.findbar.grid_forget()
        self.text.tag_remove("find", "1.0", "end")
        self.text.tag_remove("find_cur", "1.0", "end")
        self.find_count.set("")
        self.text.focus_set()

    def _on_find_typed(self, event):
        if event.keysym in ("Return", "Escape", "Shift_L", "Shift_R"):
            return
        self._mark_all()

    def _mark_all(self):
        self.text.tag_remove("find", "1.0", "end")
        self.text.tag_remove("find_cur", "1.0", "end")
        needle = self.find_entry.get()
        if not needle:
            self.find_count.set("")
            return
        n, pos = 0, "1.0"
        while True:
            pos = self.text.search(needle, pos, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(needle)}c"
            self.text.tag_add("find", pos, end)
            n += 1
            pos = end
        self.find_count.set(f"{n} match{'es' if n != 1 else ''}")
        if n:
            self.find_next(1)

    def find_next(self, direction: int = 1):
        needle = self.find_entry.get()
        if not needle:
            return
        start = self.text.index("insert")
        if direction > 0:
            pos = self.text.search(needle, f"{start}+1c", stopindex="end", nocase=True) or \
                  self.text.search(needle, "1.0", stopindex="end", nocase=True)
        else:
            pos = self.text.search(needle, start, stopindex="1.0", backwards=True, nocase=True) or \
                  self.text.search(needle, "end", stopindex="1.0", backwards=True, nocase=True)
        if not pos:
            return
        end = f"{pos}+{len(needle)}c"
        self.text.tag_remove("find_cur", "1.0", "end")
        self.text.tag_add("find_cur", pos, end)
        self.text.mark_set("insert", pos)
        self.text.see(pos)
        self._highlight_active_line()
        self._redraw_gutter()
