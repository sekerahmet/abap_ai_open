"""
Environment / setup checks — no Qt, no side effects.

run_all(has_sap_profiles) → list of result dicts (blocking: runs a few subprocesses,
call it from a worker thread):

    {"key": str, "label": str, "level": "ok" | "info" | "warn" | "error",
     "status": short text, "detail": longer text, "fix": what to do, "cmd": copyable command}

Levels: error = a configured feature cannot work (e.g. SAP profiles exist but the RFC SDK is
missing); warn = an optional feature is unavailable; info = a note; ok = fine.
"""

import os
import sys
import shutil
import subprocess

from core.claude_runner import find_claude, auth_info, find_mcp_server, reset_auth_cache, _CREATE_NO_WINDOW
from utils.env_loader import load_robust_env

RFC_DLLS = ("sapnwrfc.dll", "icudt50.dll", "icuin50.dll", "icuuc50.dll")
MIN_PY = (3, 10)
KURULUM_URL = "https://github.com/sekerahmet/abap_ai_open/blob/main/KURULUM.md"


def _run(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_CREATE_NO_WINDOW)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:                       # FileNotFoundError, TimeoutExpired, …
        return -1, "", str(e)


def _res(key, label, level, status, detail="", fix="", cmd=""):
    return {"key": key, "label": label, "level": level, "status": status, "detail": detail, "fix": fix, "cmd": cmd}


def exe_dir() -> str:
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── SAP RFC SDK ───────────────────────────────────────────────────────────────

def find_rfc_dll() -> str:
    """Folder that holds sapnwrfc.dll (exe folder first, then PATH), or ''."""
    dirs = [exe_dir(), os.getcwd()] + [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for d in dirs:
        if os.path.isfile(os.path.join(d, "sapnwrfc.dll")):
            return d
    return ""


def pyrfc_error() -> str:
    """'' when pyrfc imports fine, else the error text."""
    try:
        import pyrfc  # noqa: F401
        return ""
    except Exception as e:                       # ImportError (DLL load failed) or ModuleNotFoundError
        return str(e)


def check_rfc_sdk(has_sap_profiles: bool) -> dict:
    label = "SAP RFC SDK"
    err = pyrfc_error()
    where = find_rfc_dll()
    if not err:
        return _res("rfc", label, "ok", "ready", f"sapnwrfc.dll in {where}" if where else "pyrfc loads")
    level = "error" if has_sap_profiles else "warn"
    if "No module named" in err:
        return _res("rfc", label, level, "pyrfc not installed", err,
                    "Install the pyrfc package (source runs only).", "pip install pyrfc")
    missing = [d for d in RFC_DLLS if not os.path.isfile(os.path.join(where or exe_dir(), d))]
    return _res("rfc", label, level, "DLLs missing" if not where else "DLL load failed",
                (f"Missing next to the exe: {', '.join(missing)}" if missing else err)
                + ("" if has_sap_profiles else " — only needed for SAP profiles; Local mode works without it."),
                f"Copy the SAP NetWeaver RFC SDK files ({', '.join(RFC_DLLS)}) into:\n{exe_dir()}\n"
                "The SDK is SAP-licensed and not part of this download — get it from your SAP admin.", "")


# ── Claude Code CLI ───────────────────────────────────────────────────────────

def check_claude() -> dict:
    label = "Claude Code CLI"
    exe = find_claude()
    if not exe:
        return _res("claude", label, "warn", "not installed",
                    "The Claude tab needs the Claude Code CLI (works with a Pro/Max subscription, no API key).",
                    "Install it, then open a new terminal, run  claude  once and sign in.",
                    "winget install --id Anthropic.ClaudeCode -e")
    rc, out, _ = _run([exe, "--version"], timeout=20)
    version = out.splitlines()[0] if out else "?"
    reset_auth_cache()
    info = auth_info()
    if not info.get("loggedIn"):
        return _res("claude", label, "warn", f"{version} — not signed in",
                    "Installed, but no login yet.", "Open a terminal, run  claude  and sign in with your subscription, then /exit.",
                    "claude")
    plan = info.get("subscriptionType") or ("API key" if info.get("authMethod") != "claude.ai" else "subscription")
    return _res("claude", label, "ok", f"{version} · {info.get('email', '')} · {plan}")


# ── Python + MCP server (Claude's SAP tools / write_proposal) ─────────────────

def _python_exe() -> str:
    if not getattr(sys, "frozen", False):
        return sys.executable
    for name in ("python", "python3", "py"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:        # the Store alias stub is not a real interpreter
            return p
    return ""


def check_python_mcp() -> dict:
    label = "Python + MCP server"
    py = _python_exe()
    if not py:
        return _res("mcp", label, "warn", "Python not found",
                    "Needed only so Claude can call the SAP tools and write_proposal (the MCP server is a Python script). "
                    "The IDE itself does not need Python.",
                    "Install Python 3.12, then:  pip install -r requirements.txt  in the source folder.",
                    "winget install --id Python.Python.3.12 -e")
    code = ("import sys;print(sys.version.split()[0])\n"
            "for m in ('fastmcp','pyrfc'):\n"
            "  try:\n    mod=__import__(m);print(m+' '+getattr(mod,'__version__','?'))\n"
            "  except Exception as e:\n    print(m+' MISSING '+type(e).__name__)\n")
    rc, out, err = _run([py, "-c", code], timeout=40)
    if rc != 0 or not out:
        return _res("mcp", label, "warn", "Python does not run", err[-300:] or "no output", "Reinstall Python.", "")
    lines = out.splitlines()
    ver = lines[0]
    try:
        vt = tuple(int(x) for x in ver.split(".")[:2])
    except ValueError:
        vt = (0, 0)
    missing = [l.split()[0] for l in lines[1:] if "MISSING" in l]
    name, srv = find_mcp_server("")
    detail = f"{py} (Python {ver}); " + (f"MCP server: {name}" if srv else "mcp_server.py not found")
    if vt < MIN_PY:
        return _res("mcp", label, "warn", f"Python {ver} is too old", detail,
                    f"Python {MIN_PY[0]}.{MIN_PY[1]}+ is required for the MCP server.", "winget install --id Python.Python.3.12 -e")
    if missing:
        return _res("mcp", label, "warn", f"missing package: {', '.join(missing)}", detail,
                    "Install the packages into that Python.", f'"{py}" -m pip install fastmcp pyrfc python-dotenv')
    if not srv:
        return _res("mcp", label, "warn", "mcp_server.py not found", detail,
                    "Clone the source next to the exe (or set ABAP_AI_MCP_SERVER to the path of mcp_server.py), "
                    "or register the server in Claude Desktop's config.",
                    "git clone https://github.com/sekerahmet/abap_ai_open")
    return _res("mcp", label, "ok", f"Python {ver} · fastmcp, pyrfc · server '{name}'", detail)


# ── Git + GitHub sync ─────────────────────────────────────────────────────────

def check_git() -> dict:
    label = "Git (workspace sync)"
    git = shutil.which("git")
    if not git:
        return _res("git", label, "warn", "not installed", "Push / Pull of the workspace need Git for Windows.",
                    "Install Git, then restart the IDE.", "winget install --id Git.Git -e")
    rc, out, _ = _run([git, "--version"], timeout=15)
    return _res("git", label, "ok", out or "found", git)


def check_github_env() -> dict:
    label = "GitHub sync (.env)"
    load_robust_env()
    token, repo = os.getenv("GITHUB_TOKEN", ""), os.getenv("GITHUB_REPO", "")
    if token and repo:
        return _res("env", label, "ok", "configured", repo)
    return _res("env", label, "info", "not configured",
                "Optional: sync the workspace with a private GitHub repo.",
                f"Create a .env file next to the exe with GITHUB_TOKEN and GITHUB_REPO (see KURULUM.md):\n{exe_dir()}",
                "")


# ── all ───────────────────────────────────────────────────────────────────────

def run_all(has_sap_profiles: bool = True) -> list:
    results = []
    for fn, args in ((check_rfc_sdk, (has_sap_profiles,)), (check_claude, ()), (check_python_mcp, ()),
                     (check_git, ()), (check_github_env, ())):
        try:
            results.append(fn(*args))
        except Exception as e:                   # a broken check must never take the others down
            results.append(_res(fn.__name__, fn.__name__, "warn", "check failed", str(e)))
    return results


def summary(results: list) -> tuple:
    """(errors, warnings)"""
    return (sum(1 for r in results if r["level"] == "error"), sum(1 for r in results if r["level"] == "warn"))
