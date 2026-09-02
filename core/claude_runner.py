"""
claude_runner — drive the Claude Code CLI in headless mode from the IDE.

Uses the user's own Claude Code login (Pro/Max subscription); no API key.
Each ClaudeSession maps to one Claude Code session id, continued with --resume.

    claude -p --output-format stream-json --verbose --include-partial-messages
           [--resume <id>] --mcp-config <file> --allowedTools ... --append-system-prompt ...
    (prompt is written to stdin)

Events handed to on_event(dict) are the raw stream-json lines. The final
"result" event carries session_id, total_cost_usd, num_turns, is_error, result.
"""

import os
import sys
import json
import shutil
import subprocess
from utils.workspace import LOCAL_PROFILE

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
_APP_DIR = os.path.join(_APPDATA, "ABAP_AI")

READ_TOOLS = ["Read", "Glob", "Grep", "LS"]
USAGE_FILE = os.path.join(_APP_DIR, "claude_usage.json")

# rate-limit windows reported by the CLI → UI label (order = display order)
USAGE_WINDOWS = [
    ("five_hour", "Session (5h)"),
    ("seven_day", "Weekly (7d)"),
    ("seven_day_overage_included", "Fable limit (7d)"),
]


def load_usage() -> tuple:
    """(usage_dict, saved_epoch) from the last rate_limit_event seen by any session."""
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        usage = {k: (float(v[0]), v[1]) for k, v in data.get("windows", {}).items()}
        return usage, data.get("saved_at")
    except Exception:
        return {}, None


def save_usage(usage: dict):
    import time
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump({"saved_at": int(time.time()), "windows": usage}, f)
    except OSError:
        pass

SYSTEM_PROMPT = (
    "You are running inside ABAP AI IDE, a read-only SAP ABAP code explorer.\n"
    "Working directory = local workspace cache for SAP profile '{profile}'. Layout:\n"
    "  <PROJECT>/programs/*.abap   programs, includes, global classes, function modules\n"
    "  <PROJECT>/tables/*.json     table / structure field definitions\n"
    "  <PROJECT>/proposals/*.abap  proposals you have written\n"
    "Use Read / Glob / Grep for cached files. Use the MCP tools (fetch_program, fetch_class, "
    "fetch_function_module, fetch_table_fields, fetch_table_data, check_objects_in_tadir) for "
    "anything not cached or when the user wants the live SAP version.\n"
    "SAP is READ-ONLY: nothing can be written to SAP. To propose a code change call "
    "write_proposal(profile='{profile}', program_name=<NAME>, code=<complete new source>) — "
    "the IDE opens a diff tab automatically. Never write or edit files directly.\n"
    "Reply in the language the user writes in. Be concise."
)

SYSTEM_PROMPT_LOCAL = (
    "You are running inside ABAP AI IDE. This session has NO SAP connection: the working directory is "
    "the user's own local workspace, organised freely as folders and files (mostly ABAP sources, *.abap; "
    "also notes, JSON table definitions, etc.). Explore it with Read / Glob / Grep; use paths relative to "
    "the working directory. Do not call any SAP fetch tools.\n"
    "Never write or edit the user's files directly. To propose a change to a file call "
    "write_proposal(profile='{profile}', program_name=<file name without extension>, "
    "code=<complete new file content>, path=<the file's relative path, e.g. 'reports/ZFI_001.abap'>). "
    "The proposal is stored as <that folder>/proposals/<file name> and the IDE opens a diff tab "
    "automatically. If write_proposal is unavailable, put the complete new file in a single ```abap "
    "code block so the user can open it as a proposal.\n"
    "Reply in the language the user writes in. Be concise."
)


# ── Discovery ─────────────────────────────────────────────────────────────────

def find_claude() -> str:
    """Path to the claude executable, or '' if not installed."""
    p = shutil.which("claude")
    if p:
        return p
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "claude.exe"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe"),
        os.path.join(_APPDATA, "npm", "claude.cmd"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def find_mcp_server(profile: str):
    """
    (server_name, server_config) for the ABAP AI MCP server, or (None, None).
    1. Claude Desktop config (already working for the user)
    2. running from source → this checkout's mcp_server.py
    3. mcp_server.py next to the .exe + python on PATH
    """
    def _with_profile(srv: dict) -> dict:
        srv = dict(srv)
        srv.setdefault("type", "stdio")
        env = dict(srv.get("env") or {})
        env["SAP_PROFILE"] = profile
        srv["env"] = env
        return srv

    desktop_cfg = os.path.join(_APPDATA, "Claude", "claude_desktop_config.json")
    try:
        with open(desktop_cfg, "r", encoding="utf-8") as f:
            servers = json.load(f).get("mcpServers", {})
    except Exception:
        servers = {}
    for name, srv in servers.items():
        blob = " ".join([str(srv.get("command", ""))] + [str(a) for a in srv.get("args", [])])
        if "mcp_server.py" in blob:
            return name, _with_profile(srv)

    if not getattr(sys, "frozen", False):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ms = os.path.join(here, "mcp_server.py")
        if os.path.isfile(ms):
            return "abap-ai", _with_profile({"command": sys.executable, "args": [ms]})

    # exe next to / inside a source checkout (dist\main.exe → repo root) or ABAP_AI_MCP_SERVER
    exe_dir = os.path.dirname(sys.executable)
    candidates = [os.environ.get("ABAP_AI_MCP_SERVER", ""),
                  os.path.join(exe_dir, "mcp_server.py"),
                  os.path.join(os.path.dirname(exe_dir), "mcp_server.py")]
    py = shutil.which("python") or shutil.which("py")
    for ms in candidates:
        if ms and os.path.isfile(ms) and py:
            return "abap-ai", _with_profile({"command": py, "args": [ms]})
    return None, None


def write_mcp_config(profile: str):
    """Write the per-profile MCP config file. Returns (path, server_name) or ('', None)."""
    name, srv = find_mcp_server(profile)
    if not srv:
        return "", None
    os.makedirs(_APP_DIR, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in profile)
    path = os.path.join(_APP_DIR, f"claude_mcp_{safe}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {name: srv}}, f, indent=2)
    return path, name


_auth_cache = None


def reset_auth_cache():
    global _auth_cache
    _auth_cache = None


def auth_info() -> dict:
    """
    Cached `claude auth status`. Keys of interest: loggedIn, authMethod
    ("claude.ai" = subscription, "api_key" = pay-per-use), subscriptionType.
    """
    global _auth_cache
    if _auth_cache is not None:
        return _auth_cache
    exe = find_claude()
    info = {}
    if exe:
        try:
            r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20,
                               creationflags=_CREATE_NO_WINDOW)
            info = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        except Exception:
            info = {}
    _auth_cache = info
    return info


def format_reset(epoch) -> str:
    """'resets in 2h 05m' style text for an epoch timestamp."""
    import time
    try:
        secs = int(epoch) - int(time.time())
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return "resets now"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"resets in {d}d {h}h"
    if h:
        return f"resets in {h}h {m:02d}m"
    return f"resets in {m}m"


def billing_label() -> str:
    """Short text for the UI: how usage is paid for."""
    info = auth_info()
    if not info:
        return ""
    if info.get("authMethod") == "claude.ai":
        plan = info.get("subscriptionType") or "subscription"
        return f"{plan} subscription — not billed per request"
    return "API key — pay per token"


# ── Transcripts (Claude Code's own session files) ────────────────────────────

_CLAUDE_HOME = os.path.join(os.path.expanduser("~"), ".claude")


def _project_dir_candidates(cwd: str) -> list:
    """Claude Code stores a project's sessions under ~/.claude/projects/<cwd with non-alnum → '-'>."""
    base = os.path.join(_CLAUDE_HOME, "projects")
    import re as _re
    enc = _re.sub(r"[^A-Za-z0-9]", "-", os.path.normpath(cwd))
    cands = [os.path.join(base, enc), os.path.join(base, enc[0].lower() + enc[1:])]
    return cands


def transcript_path(cwd: str, session_id: str) -> str:
    if not session_id:
        return ""
    for d in _project_dir_candidates(cwd):
        p = os.path.join(d, f"{session_id}.jsonl")
        if os.path.isfile(p):
            return p
    base = os.path.join(_CLAUDE_HOME, "projects")
    if os.path.isdir(base):
        for d in os.listdir(base):
            p = os.path.join(base, d, f"{session_id}.jsonl")
            if os.path.isfile(p):
                return p
    return ""


def load_transcript(cwd: str, session_id: str) -> list:
    """
    [{"role": "user", "text": …} | {"role": "ai", "text": md, "tools": [names]}, …]
    Tool results, thinking blocks, side-chains and IDE context prefixes are dropped.
    """
    path = transcript_path(cwd, session_id)
    if not path:
        return []
    items = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("isSidechain"):
                    continue
                t = d.get("type")
                msg = d.get("message") or {}
                content = msg.get("content")
                if t == "user":
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                        if not parts:
                            continue                     # tool_result only
                        text = "\n".join(parts)
                    else:
                        continue
                    text = _strip_ide_context(text)
                    if text.strip():
                        items.append({"role": "user", "text": text})
                elif t == "assistant" and isinstance(content, list):
                    texts, tools = [], []
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text" and b.get("text"):
                            texts.append(b["text"])
                        elif b.get("type") == "tool_use":
                            n = b.get("name", "tool")
                            tools.append(n.split("__")[-1] if "__" in n else n)
                    if not texts and not tools:
                        continue
                    if items and items[-1]["role"] == "ai":
                        items[-1]["text"] = (items[-1]["text"] + "\n\n" + "\n".join(texts)).strip()
                        items[-1]["tools"].extend(tools)
                    else:
                        items.append({"role": "ai", "text": "\n".join(texts), "tools": tools})
    except OSError:
        return []
    return items


def _strip_ide_context(text: str) -> str:
    """Remove the [IDE context] / [Attached files] preamble the IDE prepends to prompts."""
    marker = "\n\n"
    if text.startswith("[IDE context]") or text.startswith("[Attached files"):
        # the user's own words are the last paragraph block after the preamble
        parts = text.split(marker)
        keep = [p for p in parts if not (p.startswith("[IDE context]") or p.startswith("[Attached files")
                                        or p.startswith("Current content:") or p.startswith("```"))]
        return marker.join(keep).strip() if keep else text
    return text


# ── Session ───────────────────────────────────────────────────────────────────

class ClaudeSession:
    def __init__(self, cwd: str, profile: str, session_id: str = None, max_turns: int = 40,
                 model: str = ""):
        self.cwd = cwd
        self.profile = profile
        self.session_id = session_id
        self.max_turns = max_turns
        self.model = model                      # "" → CLI default; else passed as --model
        self.effort = ""                        # "" | low | medium | high | max  (--effort)
        self.total_cost = 0.0
        self.usage, self.usage_saved_at = load_usage()   # {window: (utilization, resets_at)}
        self.mcp_path, self.mcp_name = write_mcp_config(profile)
        self._proc = None

    @property
    def has_mcp(self) -> bool:
        return bool(self.mcp_path)

    @property
    def is_local(self) -> bool:
        return self.profile == LOCAL_PROFILE

    def _build_cmd(self, exe: str) -> list:
        tools = list(READ_TOOLS)
        if self.mcp_name:
            tools.append(f"mcp__{self.mcp_name}")
        sys_prompt = SYSTEM_PROMPT_LOCAL if self.is_local else SYSTEM_PROMPT
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages",
               "--max-turns", str(self.max_turns),
               "--allowedTools", ",".join(tools),
               "--append-system-prompt", sys_prompt.format(profile=self.profile)]
        if self.mcp_path:
            cmd += ["--mcp-config", self.mcp_path]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def send(self, prompt: str, on_event, on_done):
        """
        Blocking — call from a worker thread.
        on_event(dict) for every stream-json line; on_done(dict) with the final result
        ({"is_error": bool, "result": str, "session_id": str, "total_cost_usd": float, ...}).
        """
        exe = find_claude()
        if not exe:
            on_done({"is_error": True,
                     "result": "Claude Code CLI not found. Install: winget install Anthropic.ClaudeCode"})
            return
        os.makedirs(self.cwd, exist_ok=True)
        try:
            self._proc = subprocess.Popen(
                self._build_cmd(exe), cwd=self.cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=_CREATE_NO_WINDOW)
        except Exception as e:
            on_done({"is_error": True, "result": f"Could not start claude: {e}"})
            return

        proc = self._proc
        result = None
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                sid = ev.get("session_id")
                if sid:
                    self.session_id = sid
                if ev.get("type") == "rate_limit_event":
                    windows = (ev.get("rate_limit_info") or {}).get("unifiedWindows") or {}
                    changed = False
                    for key, w in windows.items():
                        if isinstance(w, dict) and w.get("utilization") is not None:
                            self.usage[key] = (float(w["utilization"]), w.get("resetsAt"))
                            changed = True
                    if changed:
                        import time
                        self.usage_saved_at = int(time.time())
                        save_usage(self.usage)
                if ev.get("type") == "result":
                    result = ev
                    try:
                        self.total_cost += float(ev.get("total_cost_usd") or 0)
                    except (TypeError, ValueError):
                        pass
                on_event(ev)
            stderr = proc.stderr.read()
            rc = proc.wait()
        except Exception as e:
            on_done({"is_error": True, "result": f"claude stream error: {e}"})
            return
        finally:
            self._proc = None

        if result is None:
            msg = stderr.strip() or f"claude exited with code {rc}"
            if rc == -9 or rc == 1 and not stderr.strip():
                msg = "Stopped."
            result = {"is_error": True, "result": msg, "session_id": self.session_id}
        result.setdefault("session_id", self.session_id)
        on_done(result)

    def stop(self):
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
