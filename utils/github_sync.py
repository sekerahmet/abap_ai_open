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
    Returns (True, info_message) or (False, error_message).
    """
    token = os.getenv("GITHUB_TOKEN", "")
    repo  = os.getenv("GITHUB_REPO",  "")

    if not token or not repo:
        return False, "GITHUB_TOKEN and GITHUB_REPO must be set in .env"

    ws_dir = os.path.join(_WORKSPACE_ROOT, profile)
    if not os.path.isdir(ws_dir):
        return False, f"Workspace folder not found: {ws_dir}"

    auth_url = _authenticated_url(repo, token)
    msg = commit_msg or f"ABAP AI: sync {profile}"

    # Init repo if needed
    if not os.path.isdir(os.path.join(ws_dir, ".git")):
        _, err, rc = _run(["git", "init"], ws_dir)
        if rc != 0:
            return False, f"git init failed: {err}"
        _, err, rc = _run(["git", "remote", "add", "origin", auth_url], ws_dir)
        if rc != 0:
            return False, f"git remote add failed: {err}"
    else:
        # Update remote URL (token may have changed)
        _run(["git", "remote", "set-url", "origin", auth_url], ws_dir)

    # Stage all Z/Y project folders, exclude proposals/ inside each (proposals are transient)
    # Structure: ws_dir/{ZPROGRAM}/programs/ and {ZPROGRAM}/proposals/
    # We add everything then unstage proposals/ subfolders
    _run(["git", "add", "."], ws_dir)
    _run(["git", "rm", "-r", "--cached", "--ignore-unmatch", "*/proposals/"], ws_dir)

    # Check if there's anything to commit
    status_out, _, _ = _run(["git", "status", "--porcelain"], ws_dir)
    if not status_out:
        return True, "Nothing to commit — workspace already up to date."

    _, err, rc = _run(["git", "commit", "-m", msg], ws_dir)
    if rc != 0:
        return False, f"git commit failed: {err}"

    out, err, rc = _run(["git", "push", "--set-upstream", "origin", "main",
                          "--force-with-lease"], ws_dir)
    if rc != 0:
        # Try creating the branch on first push
        out, err, rc = _run(["git", "push", "-u", "origin", "HEAD:main"], ws_dir)
        if rc != 0:
            return False, f"git push failed: {err}"

    return True, f"Pushed to GitHub: {repo}\n{out}"


def pull_workspace(profile: str) -> tuple:
    """
    Pull latest changes from GitHub into the workspace profile folder.
    Returns (True, info_message) or (False, error_message).
    """
    token = os.getenv("GITHUB_TOKEN", "")
    repo  = os.getenv("GITHUB_REPO",  "")

    if not token or not repo:
        return False, "GITHUB_TOKEN and GITHUB_REPO must be set in .env"

    ws_dir = os.path.join(_WORKSPACE_ROOT, profile)
    os.makedirs(ws_dir, exist_ok=True)

    auth_url = _authenticated_url(repo, token)

    if not os.path.isdir(os.path.join(ws_dir, ".git")):
        out, err, rc = _run(["git", "clone", auth_url, "."], ws_dir)
        if rc != 0:
            return False, f"git clone failed: {err}"
        return True, f"Cloned from GitHub: {repo}"

    _run(["git", "remote", "set-url", "origin", auth_url], ws_dir)
    out, err, rc = _run(["git", "pull", "--rebase", "origin", "main"], ws_dir)
    if rc != 0:
        return False, f"git pull failed: {err}"
    return True, f"Pulled from GitHub.\n{out}"
