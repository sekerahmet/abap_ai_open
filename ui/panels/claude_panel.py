"""
ClaudePanel — one Claude Code session inside an editor tab.

Transcript (read-only) + multiline input. Every message runs `claude -p` in a
worker thread through core.claude_runner.ClaudeSession; stream events are
rendered on the main thread via app.after(0, …).
"""

import threading
import customtkinter as ctk

from core.claude_runner import ClaudeSession, find_claude, auth_info, billing_label, format_reset


class ClaudePanel(ctk.CTkFrame):
    def __init__(self, parent, app_context, session: ClaudeSession, title: str):
        super().__init__(parent, fg_color="transparent")
        self.app = app_context
        self.session = session
        self.title = title
        self._busy = False
        self._streamed_text = False
        self._last_block = None          # "text" | "tool"
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build()
        self.bind("<Destroy>", lambda _e: self.session.stop())

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        hdr = ctk.CTkFrame(self, height=32, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))

        self._title_var = ctk.StringVar()
        ctk.CTkLabel(hdr, textvariable=self._title_var,
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        self._refresh_title()

        self.stop_btn = ctk.CTkButton(hdr, text="Stop", width=70, fg_color="#6e2b28",
                                      hover_color="#8e3b38", command=self._stop, state="disabled")
        self.stop_btn.pack(side="right", padx=(4, 0))
        self.ctx_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(hdr, text="Attach open code tab as context", variable=self.ctx_var,
                        font=ctk.CTkFont(size=11)).pack(side="right", padx=10)

        self._sessions = self.app.list_claude_sessions(self.session.profile)
        labels = [f"{x['title']}  ·  {x.get('last', '')}  ·  {x['id'][:8]}" for x in self._sessions]
        self.resume_menu = ctk.CTkOptionMenu(
            hdr, values=labels or ["(no earlier sessions)"], width=230, height=26,
            font=ctk.CTkFont(size=11), dynamic_resizing=False, command=self._resume_pick)
        self.resume_menu.set("Resume earlier session…")
        self.resume_menu.pack(side="right", padx=6)

        # Usage bars (5-hour session window, 7-day window) — filled from rate_limit_event
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, weight=0)
        usage = ctk.CTkFrame(self, fg_color="#232324", corner_radius=6)
        usage.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        usage.grid_columnconfigure((1, 4), weight=1, uniform="u")
        small = ctk.CTkFont(family="Segoe UI", size=11)
        self._usage_vars = {}
        self._usage_bars = {}
        for col, key, label in ((0, "five_hour", "Session (5h)"), (3, "seven_day", "Weekly (7d)")):
            ctk.CTkLabel(usage, text=label, font=small, text_color="#aaaaaa", width=88,
                         anchor="w").grid(row=0, column=col, padx=(10, 4), pady=5)
            bar = ctk.CTkProgressBar(usage, height=8, progress_color="#3b8ed0")
            bar.set(0)
            bar.grid(row=0, column=col + 1, sticky="ew", padx=4)
            var = ctk.StringVar(value="—")
            ctk.CTkLabel(usage, textvariable=var, font=small, text_color="#cccccc", width=150,
                         anchor="w").grid(row=0, column=col + 2, padx=(4, 10))
            self._usage_vars[key] = var
            self._usage_bars[key] = bar
        self._render_usage()

        self.out = ctk.CTkTextbox(self, font=("Consolas", 13), wrap="word", fg_color="#1a1a1b")
        self.out.grid(row=2, column=0, sticky="nsew", padx=10, pady=2)
        self.out.tag_config("user",  foreground="#9cdcfe")
        self.out.tag_config("me",    foreground="#569cd6")
        self.out.tag_config("tool",  foreground="#8a8a8a")
        self.out.tag_config("meta",  foreground="#6a9955")
        self.out.tag_config("error", foreground="#e06c75")
        self.out.configure(state="disabled")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 8))
        bottom.grid_columnconfigure(0, weight=1)
        self.inp = ctk.CTkTextbox(bottom, height=76, font=("Segoe UI", 13), wrap="word")
        self.inp.grid(row=0, column=0, sticky="ew")
        self.inp.bind("<Control-Return>", lambda _e: (self.send(), "break")[1])
        self.send_btn = ctk.CTkButton(bottom, text="Send\n(Ctrl+Enter)", width=110, height=76,
                                      command=self.send)
        self.send_btn.grid(row=0, column=1, padx=(6, 0))

        intro = ("Claude Code session for profile "
                 f"'{self.session.profile}'. Working directory: {self.session.cwd}\n"
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
        for key, var in self._usage_vars.items():
            info = self.session.usage.get(key)
            bar = self._usage_bars[key]
            if not info:
                var.set("—")
                bar.set(0)
                continue
            util, resets = info
            pct = max(0.0, min(1.0, util))
            bar.set(pct)
            bar.configure(progress_color="#e06c75" if pct >= 0.9 else "#e5c07b" if pct >= 0.7 else "#3b8ed0")
            var.set(f"{pct * 100:.0f}%   {format_reset(resets)}")

    def _subscription(self) -> bool:
        return auth_info().get("authMethod") == "claude.ai"

    def _refresh_title(self):
        sid = (self.session.session_id or "new")[:8]
        tail = (f"{auth_info().get('subscriptionType', 'subscription')} plan" if self._subscription()
                else f"${self.session.total_cost:.3f}")
        self._title_var.set(f"{self.title}   ·   session {sid}   ·   {tail}")

    def _append(self, text: str, tag: str = None):
        self.out.configure(state="normal")
        if tag:
            self.out.insert("end", text, tag)
        else:
            self.out.insert("end", text)
        self.out.see("end")
        self.out.configure(state="disabled")

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

        self._append("\nYou:\n", "me")
        self._append(text + "\n", "user")
        self._append("\nClaude:\n", "me")
        self._busy = True
        self._streamed_text = False
        self._last_block = None
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        def _work():
            self.session.send(prompt,
                              on_event=lambda ev: self.app.after(0, self._on_event, ev),
                              on_done=lambda res: self.app.after(0, self._on_done, res))
        threading.Thread(target=_work, daemon=True).start()

    def _stop(self):
        self.session.stop()

    def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "stream_event":
            e = ev.get("event", {})
            et = e.get("type")
            if et == "content_block_delta":
                d = e.get("delta", {})
                if d.get("type") == "text_delta":
                    if self._last_block == "tool":
                        self._append("\n")
                    self._append(d.get("text", ""))
                    self._streamed_text = True
                    self._last_block = "text"
            elif et == "content_block_start":
                cb = e.get("content_block", {})
                if cb.get("type") == "tool_use":
                    name = cb.get("name", "tool")
                    if "__" in name:
                        name = name.split("__")[-1]
                    self._append(("\n" if self._last_block == "text" else "") + f"  ⚙ {name} …", "tool")
                    self._last_block = "tool"
        elif t == "user":
            blocks = ev.get("message", {}).get("content", [])
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                self._append(" ✓\n", "tool")
                self._last_block = "tool"
        elif t == "rate_limit_event":
            self._render_usage()
        elif t == "result":
            pass   # handled in _on_done

    def _on_done(self, res: dict):
        self._busy = False
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if res.get("is_error"):
            self._append(f"\n[error] {res.get('result', '')}\n", "error")
        elif not self._streamed_text and res.get("result"):
            self._append(str(res["result"]) + "\n")
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
            self._append(f"\n— {meta}\n", "meta")
        self._refresh_title()
        self.app.remember_claude_session(self.session, self.title)
