"""
github_sync — push/pull workspace files to/from a GitHub repository.

Uses subprocess git commands — no extra dependencies.

.env keys required:
    GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
    GITHUB_REPO=https://github.com/username/abap-workspace
"""

import os
import sys
import subprocess
from utils.env_loader import load_robust_env

load_robust_env()

_WORKSPACE_ROOT = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ABAP_AI", "workspace"
)


def _run(args: list, cwd: str) -> tuple:
    """Run a git command. Returns (stdout, stderr, returncode)."""
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _authenticated_url(repo_url: str, token: str) -> str:
    """Inject token into HTTPS URL: https://token@github.com/..."""
    if token and repo_url.startswith("https://"):
        return repo_url.replace("https://", f"https://{token}@", 1)
    return repo_url


def push_workspace(profile: str, commit_msg: str = "") -> tuple:
    """
    Push workspace files for a profile to GitHub.
    Restructures the push so that the profile appears as a folder in the repo.
    """
    token = os.getenv("GITHUB_TOKEN", "")
    repo  = os.getenv("GITHUB_REPO",  "")

    if not token or not repo:
        return False, "GITHUB_TOKEN ve GITHUB_REPO .env içerisinde tanımlanmış olmalı."

    # Use _WORKSPACE_ROOT as the git base, not the profile folder
    base_dir = _WORKSPACE_ROOT
    if not os.path.exists(base_dir):
        os.makedirs(base_dir, exist_ok=True)

    auth_url = _authenticated_url(repo, token)
    msg = commit_msg or f"ABAP AI: sync {profile}"

    # Init repo at root if needed
    if not os.path.isdir(os.path.join(base_dir, ".git")):
        _, err, rc = _run(["git", "init"], base_dir)
        if rc != 0:
            return False, f"git init failed: {err}"
        _, err, rc = _run(["git", "remote", "add", "origin", auth_url], base_dir)
        if rc != 0:
            return False, f"git remote add failed: {err}"
    else:
        _run(["git", "remote", "set-url", "origin", auth_url], base_dir)

    # Ensure a .gitignore exists at root to skip proposals
    gitignore_path = os.path.join(base_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        with open(gitignore_path, "w") as f:
            f.write("**/proposals/\n")

    # Add ONLY the current profile folder
    _, err, rc = _run(["git", "add", profile], base_dir)
    if rc != 0:
        return False, f"git add failed: {err}"

    # Check if there's anything to commit
    status_out, _, _ = _run(["git", "status", "--porcelain", profile], base_dir)
    if not status_out:
        return True, "Değişiklik yok — Workspace zaten güncel."

    _, err, rc = _run(["git", "commit", "-m", msg], base_dir)
    if rc != 0:
        return False, f"git commit failed: {err}"

    # Force push to establish the new structure or overwrite remote
    out, err, rc = _run(["git", "push", "-u", "origin", "HEAD:main", "--force"], base_dir)
    if rc != 0:
        return False, f"git push failed: {err}"

    return True, f"GitHub'a gönderildi: {repo}\n{out}"


def get_git_status() -> dict:
    """
    Run `git status --porcelain -u` in the workspace root.
    Returns { relative_path: status } where status is:
      'M'  modified (staged or unstaged)
      '?'  untracked / new
      'D'  deleted
    Returns {} if no .git repo exists yet.
    """
    if not os.path.isdir(os.path.join(_WORKSPACE_ROOT, ".git")):
        return {}
    out, _, rc = _run(["git", "status", "--porcelain", "-u"], _WORKSPACE_ROOT)
    if rc != 0:
        return {}
    result = {}
    for line in out.splitlines():
        if len(line) < 4:
            continue
        xy   = line[:2]
        path = line[3:].strip().strip('"').replace("\\", "/")
        if "?" in xy:
            result[path] = "?"
        elif "D" in xy:
            result[path] = "D"
        else:
            result[path] = "M"
    return result


def get_branch_name() -> str:
    """Return the current git branch name, or '' if no .git repo."""
    if not os.path.isdir(os.path.join(_WORKSPACE_ROOT, ".git")):
        return ""
    out, _, rc = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], _WORKSPACE_ROOT)
    return out if rc == 0 else ""


def pull_workspace(profile: str) -> tuple:
    """
    Pull latest changes from GitHub into the unified workspace root.
    """
    token = os.getenv("GITHUB_TOKEN", "")
    repo  = os.getenv("GITHUB_REPO",  "")

    if not token or not repo:
        return False, "GITHUB_TOKEN ve GITHUB_REPO .env içerisinde tanımlanmış olmalı."

    base_dir = _WORKSPACE_ROOT
    os.makedirs(base_dir, exist_ok=True)
    auth_url = _authenticated_url(repo, token)

    if not os.path.isdir(os.path.join(base_dir, ".git")):
        # Clone into a temp folder or init and pull
        _, err, rc = _run(["git", "init"], base_dir)
        if rc != 0: return False, f"git init failed: {err}"
        _run(["git", "remote", "add", "origin", auth_url], base_dir)
    
    _run(["git", "remote", "set-url", "origin", auth_url], base_dir)
    
    # Fetch and reset/pull
    _run(["git", "fetch", "origin"], base_dir)
    out, err, rc = _run(["git", "pull", "origin", "main"], base_dir)
    if rc != 0:
        return False, f"git pull failed: {err}"
    
    return True, f"GitHub'dan güncellemeler çekildi.\n{out}"
