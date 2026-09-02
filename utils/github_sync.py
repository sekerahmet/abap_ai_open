"""
github_sync — push/pull the workspace folder to/from a GitHub repository.

Uses the git CLI via subprocess.  All git work is serialised through one lock so
the background status refresh never collides with a push/pull.

.env keys:
    GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
    GITHUB_REPO=https://github.com/username/abap-workspace

Security:
  * The token is never written to .git/config.  It is passed per command as an
    HTTP Authorization header (-c http.extraheader=…).
  * Any token that an earlier version stored in the remote URL is scrubbed on the
    next push/pull, and tokens are masked in returned error text.
"""

import os
import sys
import base64
import threading
import subprocess

from utils.env_loader import load_robust_env

load_robust_env()

_WORKSPACE_ROOT = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ABAP_AI", "workspace"
)

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_lock = threading.RLock()

_FALLBACK_NAME  = "ABAP AI IDE"
_FALLBACK_EMAIL = "abap-ai@localhost"


# ── Low level ─────────────────────────────────────────────────────────────────

def _token() -> str:
    return os.getenv("GITHUB_TOKEN", "").strip()


def _repo() -> str:
    return os.getenv("GITHUB_REPO", "").strip()


def _auth_args(token: str) -> list:
    if not token:
        return []
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", "credential.helper=", "-c", f"http.extraheader=AUTHORIZATION: basic {b64}"]


def _scrub(text: str, token: str) -> str:
    if not text:
        return ""
    if token:
        text = text.replace(token, "***")
        b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        text = text.replace(b64, "***")
    return text


def _run(args: list, cwd: str, token: str = "") -> tuple:
    """Run a git command. Returns (stdout, stderr, returncode). Never prompts."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = ""
    cmd = ["git", "-c", "core.quotepath=false"] + _auth_args(token) + args
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env,
                           creationflags=_CREATE_NO_WINDOW, timeout=180)
    except FileNotFoundError:
        return "", "git was not found in PATH. Install Git for Windows.", 127
    except subprocess.TimeoutExpired:
        return "", "git command timed out (network / credential prompt?).", 124
    return r.stdout.strip(), _scrub(r.stderr.strip(), token), r.returncode


def _has_repo() -> bool:
    return os.path.isdir(os.path.join(_WORKSPACE_ROOT, ".git"))


def _ensure_repo(repo_url: str) -> tuple:
    """Init repo on 'main', point origin at the plain URL, ensure identity + .gitignore."""
    os.makedirs(_WORKSPACE_ROOT, exist_ok=True)
    base = _WORKSPACE_ROOT

    if not _has_repo():
        _, err, rc = _run(["init"], base)
        if rc != 0:
            return False, f"git init failed: {err}"
        _run(["symbolic-ref", "HEAD", "refs/heads/main"], base)

    out, _, rc = _run(["remote", "get-url", "origin"], base)
    if rc != 0:
        _, err, rc = _run(["remote", "add", "origin", repo_url], base)
        if rc != 0:
            return False, f"git remote add failed: {err}"
    elif out != repo_url:                      # also removes token-in-URL from old versions
        _run(["remote", "set-url", "origin", repo_url], base)

    name, _, _ = _run(["config", "user.name"], base)
    if not name:
        _run(["config", "user.name", _FALLBACK_NAME], base)
    email, _, _ = _run(["config", "user.email"], base)
    if not email:
        _run(["config", "user.email", _FALLBACK_EMAIL], base)

    gi = os.path.join(base, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("**/proposals/\n")
    return True, ""


def _check_env() -> tuple:
    if not _token() or not _repo():
        return False, "GITHUB_TOKEN ve GITHUB_REPO .env içerisinde tanımlanmış olmalı."
    return True, ""


def _commit_all(pathspec: list, msg: str) -> tuple:
    """Stage pathspec and commit if anything is staged. Returns (committed: bool, err)."""
    base = _WORKSPACE_ROOT
    _, err, rc = _run(["add", "-A", "--"] + pathspec, base)
    if rc != 0:
        return False, f"git add failed: {err}"
    _, _, rc = _run(["diff", "--cached", "--quiet"], base)
    if rc == 0:
        return False, ""                       # nothing staged
    _, err, rc = _run(["commit", "-q", "-m", msg], base)
    if rc != 0:
        return False, f"git commit failed: {err}"
    return True, ""


# ── Public API ────────────────────────────────────────────────────────────────

def push_workspace(profile: str, commit_msg: str = "") -> tuple:
    """Commit the profile folder and push to origin/main (no force)."""
    with _lock:
        ok, msg = _check_env()
        if not ok:
            return False, msg
        repo, token = _repo(), _token()
        ok, msg = _ensure_repo(repo)
        if not ok:
            return False, msg
        base = _WORKSPACE_ROOT

        if not os.path.isdir(os.path.join(base, profile)):
            return False, f"Workspace klasörü yok: {profile}"

        committed, err = _commit_all([profile, ".gitignore"], commit_msg or f"ABAP AI: sync {profile}")
        if err:
            return False, err

        # Anything not yet on the remote (a merge commit from Pull, a push that failed earlier)?
        ahead, _, rc = _run(["rev-list", "--count", "origin/main..HEAD"], base)
        pending = committed or rc != 0 or ahead.strip() not in ("", "0")

        out, err, rc = _run(["push", "-u", "origin", "HEAD:main"], base, token)
        if rc != 0:
            low = err.lower()
            if "fetch first" in low or "rejected" in low or "non-fast-forward" in low:
                return False, ("Remote'da yerelde olmayan commit'ler var.\n"
                               "Önce Pull yapın, sonra tekrar Push deneyin.\n\n" + err)
            return False, f"git push failed: {err}"

        if pending:
            return True, f"GitHub'a gönderildi: {repo}"
        return True, "Değişiklik yok — remote zaten güncel."


def pull_workspace(profile: str) -> tuple:
    """Fetch origin/main and merge it into the local workspace (all profiles)."""
    with _lock:
        ok, msg = _check_env()
        if not ok:
            return False, msg
        repo, token = _repo(), _token()
        ok, msg = _ensure_repo(repo)
        if not ok:
            return False, msg
        base = _WORKSPACE_ROOT

        # Keep local edits safe: commit them before merging.
        _, err = _commit_all(["."], "ABAP AI: local changes before pull")
        if err:
            return False, err

        _, err, rc = _run(["fetch", "origin"], base, token)
        if rc != 0:
            return False, f"git fetch failed: {err}"

        _, _, rc = _run(["rev-parse", "--verify", "-q", "origin/main"], base)
        if rc != 0:
            return False, "Remote'da 'main' branch'i yok — önce Push yapın."

        head, _, head_rc = _run(["rev-parse", "--verify", "-q", "HEAD"], base)
        if head_rc != 0:                       # unborn branch: adopt remote directly
            _, err, rc = _run(["reset", "--hard", "origin/main"], base)
            if rc != 0:
                return False, f"git reset failed: {err}"
        else:
            out, err, rc = _run(["merge", "--no-edit", "--allow-unrelated-histories",
                                 "origin/main"], base)
            if rc != 0:
                _run(["merge", "--abort"], base)
                return False, ("Merge çakışması oluştu; merge geri alındı.\n"
                               "Çakışan dosyaları workspace klasöründe git ile çözün.\n\n" + err)

        _run(["branch", "--set-upstream-to=origin/main"], base)
        return True, f"GitHub'dan güncellemeler çekildi ({profile} dahil tüm profiller)."


def get_git_status() -> dict:
    """
    { "profile/project/folder/file": 'M' | '?' | 'D' }  — {} if no repo yet.
    Paths are workspace-relative with forward slashes.
    """
    with _lock:
        if not _has_repo():
            return {}
        out, _, rc = _run(["status", "--porcelain", "-uall"], _WORKSPACE_ROOT)
        if rc != 0:
            return {}
    result = {}
    for line in out.splitlines():
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"').replace("\\", "/")
        if "?" in xy:
            result[path] = "?"
        elif "D" in xy:
            result[path] = "D"
        else:
            result[path] = "M"
    return result


def get_branch_name() -> str:
    with _lock:
        if not _has_repo():
            return ""
        out, _, rc = _run(["rev-parse", "--abbrev-ref", "HEAD"], _WORKSPACE_ROOT)
        return out if rc == 0 else ""
