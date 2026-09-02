import customtkinter as ctk

from ui import theme as T

# glyph shown in front of the tab title, by title prefix
_TAB_GLYPH = {
    "Program": "📝", "Global Class": "💎", "Function Module": "⚙", "Table": "▦",
    "Data": "▤", "Proposal": "📬", "Diff": "±", "Claude": "✦", "System Logs": "≡",
}


def _glyph(name: str) -> str:
    prefix = name.split(":", 1)[0]
    return _TAB_GLYPH.get(prefix, "")


class EditorPanel(ctk.CTkFrame):
    """Horizontally scrollable tab bar + content area (fetch controls live in TopBar)."""

    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app_context
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.tabs_dict = {}
        self.active_tab_name = None
        self._setup_ui()

    def _setup_ui(self):
        self.headers_bar = ctk.CTkScrollableFrame(self, orientation="horizontal", height=34,
                                                  fg_color=T.PANEL, corner_radius=6)
        self.headers_bar.grid(row=0, column=0, sticky="ew", padx=T.PAD, pady=(T.PAD, 0))
        # hide the scrollbar unless the tabs overflow
        try:
            self._hbar_sb = self.headers_bar._scrollbar
            self._hbar_canvas = self.headers_bar._parent_canvas
            self.headers_bar.bind("<Configure>", lambda _e: self.after(10, self._update_tab_scrollbar))
            self._hbar_canvas.bind("<Configure>", lambda _e: self.after(10, self._update_tab_scrollbar))
        except AttributeError:
            self._hbar_sb = None

        self.content_area = ctk.CTkFrame(self, corner_radius=8, fg_color=T.SURFACE)
        self.content_area.grid(row=1, column=0, sticky="nsew", padx=T.PAD, pady=(4, T.PAD))
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)

    def _update_tab_scrollbar(self):
        if not self._hbar_sb:
            return
        try:
            need = self.headers_bar.winfo_reqwidth() > self._hbar_canvas.winfo_width() + 2
            if need:
                self._hbar_sb.grid()
            else:
                self._hbar_sb.grid_remove()
        except Exception:
            pass

    # ── Tab API ───────────────────────────────────────────────────────────────

    def add_tab(self, name, is_closable=True):
        header = ctk.CTkFrame(self.headers_bar, fg_color=T.PANEL_ALT, height=28, corner_radius=6)
        header.pack(side="left", padx=2, pady=3)

        g = _glyph(name)
        lbl = ctk.CTkLabel(header, text=f"{g} {name}" if g else name, font=ctk.CTkFont(size=12))
        lbl.pack(side="left", padx=(10, 5))
        lbl.bind("<Button-1>", lambda _e: self.set_active(name))
        lbl.bind("<Button-2>", lambda _e: self.close_tab(name) if is_closable else None)

        if is_closable:
            ctk.CTkButton(header, text="✕", width=20, height=20, fg_color="transparent",
                          hover_color=T.DANGER_HOVER, command=lambda: self.close_tab(name)
                          ).pack(side="left", padx=(0, 5))

        content = ctk.CTkFrame(self.content_area, fg_color="transparent")
        self.tabs_dict[name] = {"header": header, "content": content}
        self.after(30, self._update_tab_scrollbar)
        return content

    def set_active(self, name):
        if name not in self.tabs_dict:
            return
        if self.active_tab_name and self.active_tab_name in self.tabs_dict:
            self.tabs_dict[self.active_tab_name]["header"].configure(fg_color=T.PANEL_ALT)
            self.tabs_dict[self.active_tab_name]["content"].grid_forget()

        self.active_tab_name = name
        self.tabs_dict[name]["header"].configure(fg_color=T.ACCENT)
        self.tabs_dict[name]["content"].grid(row=0, column=0, sticky="nsew")
        self.app.active_tab_name = name

    def close_tab(self, name):
        if name not in self.tabs_dict:
            return
        self.tabs_dict[name]["header"].destroy()
        self.tabs_dict[name]["content"].destroy()
        del self.tabs_dict[name]
        self.app.tabs_dict.pop(name, None)
        self.after(30, self._update_tab_scrollbar)

        if self.active_tab_name == name:
            self.active_tab_name = None
            if self.tabs_dict:
                self.set_active(list(self.tabs_dict.keys())[-1])
