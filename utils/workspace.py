"""
workspace — filesystem bridge between ABAP AI IDE and Claude Desktop (MCP).

Folder layout:
    workspace/
    └── {profile}/
        └── {project}/        ← Z*/Y* main-object name (uppercase)
            ├── programs/     ← source files (.abap)  — programs, includes, classes, FMs
            ├── tables/       ← table/structure field definitions (.json)
            └── proposals/    ← AI proposals (.abap)

Only custom objects (Z* / Y* prefix) are saved.
Standard SAP objects are fetched for display but never written to disk.

Location rule: if a file already exists under ANY project of the profile, that
location wins.  This keeps includes and tables under the main program that first
discovered them instead of creating duplicates on Save / Re-fetch.
"""

import os
import re
import json

_WORKSPACE_ROOT = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ABAP_AI", "workspace"
)

SOURCE_FOLDER = "programs"
TABLE_FOLDER  = "tables"
PROP_FOLDER   = "proposals"
FOLDERS       = (SOURCE_FOLDER, TABLE_FOLDER, PROP_FOLDER)

_DDIC_TYPES = ("TABL", "VIEW", "Table", "Structure")


def root() -> str:
    return _WORKSPACE_ROOT


def _is_custom(name: str) -> bool:
    return name.upper().startswith(("Z", "Y"))


def _ensure(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _folder_for(ftype: str) -> str:
    if ftype == "PROP":
        return PROP_FOLDER
    if ftype in _DDIC_TYPES:
        return TABLE_FOLDER
    return SOURCE_FOLDER


def _filename(ftype: str, name: str) -> str:
    ext = ".json" if ftype in _DDIC_TYPES else ".abap"
    return f"{name.upper()}{ext}"


# ── Location helpers ──────────────────────────────────────────────────────────

def abs_path(profile: str, project: str = "", folder: str = "", filename: str = "") -> str:
    """Absolute path for any node level (profile / project / folder / file)."""
    parts = [p for p in (profile, project, folder, filename) if p]
    return os.path.join(_WORKSPACE_ROOT, *parts)


def find_project(profile: str, folder: str, filename: str):
    """Project folder that already contains folder/filename, or None."""
    base = os.path.join(_WORKSPACE_ROOT, profile)
    if not os.path.isdir(base):
        return None
    stem = os.path.splitext(filename)[0].upper()
    candidates = [stem] + sorted(d for d in os.listdir(base) if d != stem)
    for proj in candidates:
        if os.path.isfile(os.path.join(base, proj, folder, filename)):
            return proj
    return None


def _resolve_project(profile: str, folder: str, filename: str, project) -> str:
    existing = find_project(profile, folder, filename)
    if existing:
        return existing
    return (project or os.path.splitext(filename)[0]).upper()


def get_path(profile: str, ftype: str, name: str, project: str = None) -> str:
    """Filesystem path an object would be written to (existing location wins)."""
    folder = _folder_for(ftype)
    fname  = _filename(ftype, name)
    proj   = _resolve_project(profile, folder, fname, project)
    return os.path.join(_WORKSPACE_ROOT, profile, proj, folder, fname)


# ── Write API ─────────────────────────────────────────────────────────────────

def save_code(profile: str, ftype: str, name: str, code: str, project: str = None) -> str:
    """Save ABAP source. Returns path, or '' for standard (non Z/Y) objects."""
    if not _is_custom(name):
        return ""
    path = get_path(profile, ftype, name, project)
    _ensure(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def save_table(profile: str, name: str, fields: list, project: str = None) -> str:
    """Save table field definitions as JSON. Returns path, or '' for standard objects."""
    if not _is_custom(name):
        return ""
    path = get_path(profile, "TABL", name, project)
    _ensure(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=2, ensure_ascii=False)
    return path


def write_proposal(profile: str, name: str, code: str, project: str = None) -> str:
    """
    Write an AI proposal.  Location: existing proposal → project that holds the
    program source → explicit project → own folder.
    """
    fname = _filename("PROP", name)
    proj = (find_project(profile, PROP_FOLDER, fname)
            or find_project(profile, SOURCE_FOLDER, fname)
            or (project or name).upper())
    path = os.path.join(_WORKSPACE_ROOT, profile, proj, PROP_FOLDER, fname)
    _ensure(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def delete_path(path: str):
    """Delete a workspace file or folder. Refuses anything outside the workspace root."""
    import shutil
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(_WORKSPACE_ROOT) + os.sep):
        raise ValueError("Refusing to delete outside the workspace folder.")
    if os.path.isdir(real):
        shutil.rmtree(real)
    elif os.path.exists(real):
        os.remove(real)


# ── Read API ──────────────────────────────────────────────────────────────────

def read_file(profile: str, folder: str, filename: str, project: str = None) -> str:
    """Read a workspace file. Without project, searches all project folders."""
    if project:
        path = os.path.join(_WORKSPACE_ROOT, profile, project, folder, filename)
    else:
        proj = find_project(profile, folder, filename)
        if not proj:
            return ""
        path = os.path.join(_WORKSPACE_ROOT, profile, proj, folder, filename)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def read_code(profile: str, ftype: str, name: str, project: str = None) -> str:
    """Load ABAP source (programs/ or proposals/). '' if not found."""
    return read_file(profile, _folder_for(ftype if ftype == "PROP" else "PROG"),
                     f"{name.upper()}.abap", project)


def read_table_fields(profile: str, name: str, project: str = None) -> list:
    content = read_file(profile, TABLE_FOLDER, f"{name.upper()}.json", project)
    if not content:
        return []
    try:
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except Exception:
        return []


_RE_FM    = re.compile(r"^\s*FUNCTION\s+[\w/]+\s*\.", re.IGNORECASE | re.MULTILINE)
_RE_CLASS = re.compile(r"\bCLASS\s+\w+\s+DEFINITION\s+PUBLIC\b", re.IGNORECASE)
_RE_CLASS_BANNER = re.compile(r"^\* (Public section|Private section|Protected section|METHOD )",
                              re.MULTILINE)


def guess_ftype(code: str) -> str:
    """Infer the IDE object type of a cached .abap file from its content."""
    head = code[:4000]
    if _RE_FM.search(head):
        return "Function Module"
    if _RE_CLASS.search(head) or _RE_CLASS_BANNER.search(head):
        return "Global Class"
    return "Program"


# ── Listing ───────────────────────────────────────────────────────────────────

def list_files(profile: str) -> dict:
    """{ project: { "programs": [files], "tables": [...], "proposals": [...] } }"""
    base = os.path.join(_WORKSPACE_ROOT, profile)
    if not os.path.isdir(base):
        return {}
    result = {}
    for proj in sorted(os.listdir(base)):
        proj_path = os.path.join(base, proj)
        if not os.path.isdir(proj_path) or proj.startswith("."):
            continue
        entry = {}
        for sub in FOLDERS:
            sub_path = os.path.join(proj_path, sub)
            if os.path.isdir(sub_path):
                files = sorted(f for f in os.listdir(sub_path)
                               if os.path.isfile(os.path.join(sub_path, f)))
                if files:
                    entry[sub] = files
        if entry:
            result[proj] = entry
    return result


def list_profiles() -> list:
    if not os.path.isdir(_WORKSPACE_ROOT):
        return []
    return sorted(d for d in os.listdir(_WORKSPACE_ROOT)
                  if os.path.isdir(os.path.join(_WORKSPACE_ROOT, d)) and not d.startswith("."))


def scan_proposals(profile: str) -> list:
    """[(project, filename, mtime_ns), …] across all project folders."""
    base = os.path.join(_WORKSPACE_ROOT, profile)
    if not os.path.isdir(base):
        return []
    found = []
    for proj in sorted(os.listdir(base)):
        prop_dir = os.path.join(base, proj, PROP_FOLDER)
        if not os.path.isdir(prop_dir):
            continue
        for f in sorted(os.listdir(prop_dir)):
            p = os.path.join(prop_dir, f)
            if os.path.isfile(p):
                try:
                    found.append((proj, f, os.stat(p).st_mtime_ns))
                except OSError:
                    pass
    return found


def snapshot() -> tuple:
    """Cheap change-detection fingerprint of the whole workspace (paths + mtimes)."""
    if not os.path.isdir(_WORKSPACE_ROOT):
        return ()
    items = []
    for dirpath, dirnames, filenames in os.walk(_WORKSPACE_ROOT):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            p = os.path.join(dirpath, f)
            try:
                items.append((os.path.relpath(p, _WORKSPACE_ROOT), os.stat(p).st_mtime_ns))
            except OSError:
                pass
    return tuple(sorted(items))
