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

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
_APP_DIR = os.path.join(_APPDATA, "ABAP_AI")

READ_TOOLS = ["Read", "Glob", "Grep", "LS"]

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

    ms = os.path.join(os.path.dirname(sys.executable), "mcp_server.py")
    py = shutil.which("python")
    if os.path.isfile(ms) and py:
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


# ── Session ───────────────────────────────────────────────────────────────────

class ClaudeSession:
    def __init__(self, cwd: str, profile: str, session_id: str = None, max_turns: int = 40):
        self.cwd = cwd
        self.profile = profile
        self.session_id = session_id
        self.max_turns = max_turns
        self.total_cost = 0.0
        self.mcp_path, self.mcp_name = write_mcp_config(profile)
        self._proc = None

    @property
    def has_mcp(self) -> bool:
        return bool(self.mcp_path)

    def _build_cmd(self, exe: str) -> list:
        tools = list(READ_TOOLS)
        if self.mcp_name:
            tools.append(f"mcp__{self.mcp_name}")
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages",
               "--max-turns", str(self.max_turns),
               "--allowedTools", ",".join(tools),
               "--append-system-prompt", SYSTEM_PROMPT.format(profile=self.profile)]
        if self.mcp_path:
            cmd += ["--mcp-config", self.mcp_path]
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
