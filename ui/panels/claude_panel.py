"""
ClaudePanel — one Claude Code session inside an editor tab.

Transcript (read-only, light markdown rendering) + multiline input. Every
message runs `claude -p` in a worker thread through
core.claude_runner.ClaudeSession; stream events are rendered on the main thread
via app.after(0, …). Text is streamed raw and re-rendered as markdown when the
text block completes; code blocks get Copy / "Open as proposal" buttons.
"""

import re
import time
import threading
import customtkinter as ctk

from core.claude_runner import (ClaudeSession, find_claude, auth_info, billing_label,
                                format_reset, USAGE_WINDOWS)
from ui import theme as T

_FENCE  = re.compile(r"```([\w+.-]*)[ \t]*\n(.*?)(?:```|\Z)", re.S)
_INLINE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)")
_HEAD   = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*•]\s+(.*)$")


class ClaudePanel(ctk.CTkFrame):
    def __init__(self, parent, app_context, session: ClaudeSession, title: str):
        super().__init__(parent, fg_color="transparent")
        self.app = app_context
        self.session = session
        self.title = title
        self._busy = False
        self._streamed_text = False
        self._last_block = None          # "text" | "tool"
        self._in_text_block = False
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build()
        self.bind("<Destroy>", lambda _e: self.session.stop())

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        hdr = ctk.CTkFrame(self, height=32, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=T.PAD, pady=(6, 2))

        self._title_var = ctk.StringVar()
        ctk.CTkLabel(hdr, textvariable=self._title_var,
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        self._refresh_title()

        self.stop_btn = ctk.CTkButton(hdr, text="Stop", width=70, fg_color=T.DANGER,
                                      hover_color=T.DANGER_HOVER, command=self._stop, state="disabled")
        self.stop_btn.pack(side="right", padx=(4, 0))
        self.ctx_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(hdr, text="Attach open code tab as context", variable=self.ctx_var,
                        font=ctk.CTkFont(size=11)).pack(side="right", padx=10)

        self._sessions = self.app.list_claude_sessions(self.session.profile)
        labels = [f"{x['title']}  ·  {x.get('last', '')}  ·  {x['id'][:8]}" for x in self._sessions]
        self.resume_menu = ctk.CTkOptionMenu(
            hdr, values=labels or ["(no earlier sessions)"], width=230, height=26,
            font=ctk.CTkFont(size=11), dynamic_resizing=False, command=self._resume_pick,
            fg_color=T.PANEL_ALT, button_color=T.PANEL_ALT, button_hover_color=T.BORDER)
        self.resume_menu.set("Resume earlier session…")
        self.resume_menu.pack(side="right", padx=6)

        # ── Usage bars ────────────────────────────────────────────────────────
        usage = ctk.CTkFrame(self, fg_color=T.PANEL_ALT, corner_radius=6)
        usage.grid(row=1, column=0, sticky="ew", padx=T.PAD, pady=(0, 4))
        usage.grid_columnconfigure(1, weight=1)
        small = ctk.CTkFont(family="Segoe UI", size=11)
        self._usage_rows = {}
        for key, label in USAGE_WINDOWS:
            lbl = ctk.CTkLabel(usage, text=label, font=small, text_color=T.MUTED, width=120, anchor="w")
            bar = ctk.CTkProgressBar(usage, height=8, progress_color=T.ACCENT)
            bar.set(0)
            var = ctk.StringVar(value="—")
            txt = ctk.CTkLabel(usage, textvariable=var, font=small, text_color=T.TEXT,
                               width=190, anchor="w")
            self._usage_rows[key] = (lbl, bar, var, txt)
        self._usage_stamp = ctk.StringVar(value="")
        ctk.CTkLabel(usage, textvariable=self._usage_stamp, font=ctk.CTkFont(size=10),
                     text_color=T.DIM, anchor="e").grid(row=0, column=3, rowspan=3,
                                                        padx=(4, 10), sticky="e")
        self._render_usage()

        # ── Transcript ────────────────────────────────────────────────────────
        self.out = ctk.CTkTextbox(self, font=("Segoe UI", 13), wrap="word", fg_color=T.SURFACE,
                                  spacing1=2, spacing3=2)
        self.out.grid(row=2, column=0, sticky="nsew", padx=T.PAD, pady=2)
        self.out._textbox.tag_config("user",  foreground="#9cdcfe", lmargin1=12, lmargin2=12)
        self.out._textbox.tag_config("me",    foreground=T.ACCENT_HOVER, font=("Segoe UI", 11, "bold"))
        self.out._textbox.tag_config("tool",  foreground=T.MUTED, font=("Consolas", 11))
        self.out._textbox.tag_config("meta",  foreground=T.GOOD, font=("Consolas", 11))
        self.out._textbox.tag_config("error", foreground=T.BAD)
        self.out._textbox.tag_config("raw",   foreground=T.TEXT)
        self.out._textbox.tag_config("p",     foreground=T.TEXT, lmargin1=12, lmargin2=12)
        self.out._textbox.tag_config("h",     foreground="#ffffff", font=("Segoe UI", 14, "bold"),
                            lmargin1=12, spacing1=8, spacing3=2)
        self.out._textbox.tag_config("b",     foreground="#ffffff", font=("Segoe UI", 13, "bold"))
        self.out._textbox.tag_config("ic",    foreground="#e5c07b", font=("Consolas", 12), background="#111214")
        self.out._textbox.tag_config("code",  foreground="#dcdcdc", font=("Consolas", 12), background="#111214",
                            lmargin1=20, lmargin2=20, rmargin=20, spacing1=2, spacing3=2)
        self.out.configure(state="disabled")

        # ── Input ─────────────────────────────────────────────────────────────
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=T.PAD, pady=(2, T.PAD))
        bottom.grid_columnconfigure(0, weight=1)
        self.inp = ctk.CTkTextbox(bottom, height=76, font=("Segoe UI", 13), wrap="word")
        self.inp.grid(row=0, column=0, sticky="ew")
        self.inp.bind("<Control-Return>", lambda _e: (self.send(), "break")[1])
        self.send_btn = ctk.CTkButton(bottom, text="Send\n(Ctrl+Enter)", width=110, height=76,
                                      fg_color=T.CLAUDE, hover_color=T.CLAUDE_HOVER, command=self.send)
        self.send_btn.grid(row=0, column=1, padx=(6, 0))

        intro = (f"Claude Code session for profile '{self.session.profile}'.\n"
                 f"Working directory: {self.session.cwd}\n"
                 + ("SAP tools attached via MCP.\n" if self.session.has_mcp else
                    "MCP server not found — only cached workspace files are available.\n"))
        if not find_claude():
            intro += "Claude Code CLI not installed: winget install Anthropic.ClaudeCode\n"
        bl = billing_label()
        if bl:
            intro += f"Auth: {bl}.\n"
        self._append(intro + "\n", "meta")
        self.inp.focus_set()

    def _resume_pick(self, label: str):
        self.resume_menu.set("Resume earlier session…")
        for x in self._sessions:
            if label.endswith(x["id"][:8]):
                self.app.open_claude_tab(session_id=x["id"], title=x["title"])
                return

    def _render_usage(self):
        row = 0
        for key, (lbl, bar, var, txt) in self._usage_rows.items():
            info = self.session.usage.get(key)
            if not info:
                for w in (lbl, bar, txt):
                    w.grid_forget()
                continue
            util, resets = info
            pct = max(0.0, min(1.0, util))
            bar.set(pct)
            bar.configure(progress_color=T.BAD if pct >= 0.9 else T.WARN if pct >= 0.7 else T.ACCENT)
            var.set(f"{pct * 100:.0f}%   {format_reset(resets)}")
            lbl.grid(row=row, column=0, padx=(10, 4), pady=3, sticky="w")
            bar.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
            txt.grid(row=row, column=2, padx=(4, 4), pady=3, sticky="w")
            row += 1
        if row == 0:
            lbl, bar, var, txt = self._usage_rows[USAGE_WINDOWS[0][0]]
            var.set("usage appears after the first message")
            lbl.grid(row=0, column=0, padx=(10, 4), pady=3, sticky="w")
            txt.grid(row=0, column=2, padx=4, pady=3, sticky="w")
            self._usage_stamp.set("")
            return
        saved = self.session.usage_saved_at
        if saved:
            age = int(time.time()) - int(saved)
            self._usage_stamp.set("live" if age < 120 else
                                  f"as of {time.strftime('%H:%M', time.localtime(saved))}")

    def _subscription(self) -> bool:
        return auth_info().get("authMethod") == "claude.ai"

    def _refresh_title(self):
        sid = (self.session.session_id or "new")[:8]
        tail = (f"{auth_info().get('subscriptionType', 'subscription')} plan" if self._subscription()
                else f"${self.session.total_cost:.3f}")
        self._title_var.set(f"{self.title}   ·   session {sid}   ·   {tail}")

    # ── Transcript helpers ────────────────────────────────────────────────────

    def _append(self, text: str, tag: str = None):
        self.out.configure(state="normal")
        if tag:
            self.out.insert("end", text, tag)
        else:
            self.out.insert("end", text)
        self.out.see("end")
        self.out.configure(state="disabled")

    def _render_markdown(self, md: str):
        """Append md rendered with tags (caller has set the widget to state=normal)."""
        pos = 0
        for m in _FENCE.finditer(md):
            self._render_prose(md[pos:m.start()])
            self._render_code(m.group(2).rstrip("\n"), m.group(1))
            pos = m.end()
        self._render_prose(md[pos:])

    def _render_prose(self, text: str):
        for line in text.split("\n"):
            hm = _HEAD.match(line)
            if hm:
                self.out.insert("end", hm.group(2).strip() + "\n", "h")
                continue
            bm = _BULLET.match(line)
            if bm:
                self.out.insert("end", bm.group(1) + "  •  ", "p")
                self._render_inline(bm.group(2))
                self.out.insert("end", "\n")
                continue
            self._render_inline(line)
            self.out.insert("end", "\n")

    def _render_inline(self, line: str):
        for part in _INLINE.split(line):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                self.out.insert("end", part[2:-2], ("p", "b"))
            elif part.startswith("`") and part.endswith("`"):
                self.out.insert("end", part[1:-1], ("p", "ic"))
            else:
                self.out.insert("end", part, "p")

    def _render_code(self, code: str, lang: str):
        self.out.insert("end", "\n")
        self.out.insert("end", code + "\n", "code")
        bar = ctk.CTkFrame(self.out, fg_color="transparent")
        ctk.CTkLabel(bar, text=(lang or "code"), font=("Consolas", 10), text_color=T.DIM
                     ).pack(side="left", padx=(20, 8))
        ctk.CTkButton(bar, text="Copy", width=60, height=22, font=ctk.CTkFont(size=11),
                      fg_color=T.PANEL_ALT, hover_color=T.BORDER,
                      command=lambda c=code: self.app.copy_to_clipboard(c)).pack(side="left", padx=2)
        if lang.lower() in ("abap", "") and len(code.splitlines()) >= 3:
            ctk.CTkButton(bar, text="Open as proposal", width=130, height=22, font=ctk.CTkFont(size=11),
                          fg_color=T.CLAUDE, hover_color=T.CLAUDE_HOVER,
                          command=lambda c=code: self.app.proposal_from_code(c)).pack(side="left", padx=2)
        self.out._textbox.window_create("end", window=bar, pady=2)
        self.out.insert("end", "\n\n")

    # ── Send / receive ────────────────────────────────────────────────────────

    def send(self):
        if self._busy:
            return
        text = self.inp.get("0.0", "end-1c").strip()
        if not text:
            return
        self.inp.delete("0.0", "end")

        prompt = text
        if self.ctx_var.get():
            ctx = self.app.get_active_code_context()
            if ctx:
                prompt = ctx + "\n\n" + text

        self._append("\nYou\n", "me")
        self._append(text + "\n", "user")
        self._append("\nClaude\n", "me")
        self._busy = True
        self._streamed_text = False
        self._last_block = None
        self._in_text_block = False
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        def _work():
            self.session.send(prompt,
                              on_event=lambda ev: self.app.after(0, self._on_event, ev),
                              on_done=lambda res: self.app.after(0, self._on_done, res))
        threading.Thread(target=_work, daemon=True).start()

    def _stop(self):
        self.session.stop()

    def _begin_text_block(self):
        self.out.configure(state="normal")
        if self._last_block == "tool":
            self.out.insert("end", "\n")
        self.out._textbox.mark_set("blk", "end-1c")
        self.out._textbox.mark_gravity("blk", "left")
        self.out.configure(state="disabled")
        self._in_text_block = True

    def _end_text_block(self):
        if not self._in_text_block:
            return
        self._in_text_block = False
        self.out.configure(state="normal")
        try:
            raw = self.out.get("blk", "end-1c")
            self.out.delete("blk", "end-1c")
            self._render_markdown(raw)
        finally:
            self.out.see("end")
            self.out.configure(state="disabled")

    def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "stream_event":
            e = ev.get("event", {})
            et = e.get("type")
            if et == "content_block_start":
                cb = e.get("content_block", {})
                if cb.get("type") == "text":
                    self._begin_text_block()
                    self._last_block = "text"
                elif cb.get("type") == "tool_use":
                    self._end_text_block()
                    name = cb.get("name", "tool")
                    if "__" in name:
                        name = name.split("__")[-1]
                    self._append(("\n" if self._last_block == "text" else "") + f"   ⚙ {name} …", "tool")
                    self._last_block = "tool"
            elif et == "content_block_delta":
                d = e.get("delta", {})
                if d.get("type") == "text_delta":
                    if not self._in_text_block:
                        self._begin_text_block()
                    self._append(d.get("text", ""), "raw")
                    self._streamed_text = True
                    self._last_block = "text"
            elif et == "content_block_stop":
                self._end_text_block()
        elif t == "user":
            blocks = ev.get("message", {}).get("content", [])
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                self._append(" ✓\n", "tool")
                self._last_block = "tool"
        elif t == "rate_limit_event":
            self._render_usage()

    def _on_done(self, res: dict):
        self._end_text_block()
        self._busy = False
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if res.get("is_error"):
            self._append(f"\n[error] {res.get('result', '')}\n", "error")
        elif not self._streamed_text and res.get("result"):
            self.out.configure(state="normal")
            self._render_markdown(str(res["result"]))
            self.out.configure(state="disabled")
        cost = res.get("total_cost_usd")
        turns = res.get("num_turns")
        ms = res.get("duration_ms")
        cost_txt = ""
        if isinstance(cost, (int, float)):
            cost_txt = (f"API-equivalent ~${cost:.3f} (not billed)" if self._subscription()
                        else f"${cost:.3f}")
        meta = "  ".join(x for x in (
            f"turns={turns}" if turns is not None else "",
            cost_txt,
            f"{ms / 1000:.1f}s" if isinstance(ms, (int, float)) else "") if x)
        if meta:
            self._append(f"— {meta}\n", "meta")
        self._render_usage()
        self._refresh_title()
        self.app.remember_claude_session(self.session, self.title)
