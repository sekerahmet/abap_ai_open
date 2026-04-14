"""
WorkspaceManager — filesystem bridge between ABAP AI IDE and Claude Desktop.

Folder layout:
    workspace/
    └── {profile}/
        └── {program_name}/   ← Z*/Y* object name (uppercase)
            ├── programs/     ← source files (.abap) + table defs (.json)
            └── proposals/    ← AI proposals (.abap)

Only custom objects (Z* / Y* prefix) are saved.
Standard SAP objects are fetched for display but not written to disk.
"""

import os
import json

_WORKSPACE_ROOT = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ABAP_AI", "workspace"
)

# All source types go into programs/, proposals into proposals/
_SOURCE_FOLDER = "programs"
_PROP_FOLDER   = "proposals"


def _is_custom(name: str) -> bool:
    return name.upper().startswith(("Z", "Y"))


def _ensure(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _ext(ftype: str) -> str:
    return ".json" if ftype in ("TABL", "VIEW", "Table", "Structure") else ".abap"


# ── Path helpers ──────────────────────────────────────────────────────────────

def get_path(profile: str, ftype: str, name: str, project: str = None) -> str:
    """
    Return the filesystem path for an object.
    project defaults to the object name itself (each Z* object is its own project).
    """
    proj   = (project or name).upper()
    folder = _PROP_FOLDER if ftype == "PROP" else _SOURCE_FOLDER
    ext    = _ext(ftype)
    return os.path.join(_WORKSPACE_ROOT, profile, proj, folder, f"{name.upper()}{ext}")


# ── Public write API ──────────────────────────────────────────────────────────

def save_code(profile: str, ftype: str, name: str, code: str,
              project: str = None) -> str:
    """Save ABAP source to workspace. Returns path or '' for standard objects."""
    if not _is_custom(name):
        return ""
    path = get_path(profile, ftype, name, project)
    _ensure(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def save_table(profile: str, name: str, fields: list,
               project: str = None) -> str:
    """Save table field definitions as JSON. Returns path or '' for standard objects."""
    if not _is_custom(name):
        return ""
    path = get_path(profile, "TABL", name, project)
    _ensure(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=2, ensure_ascii=False)
    return path


def write_proposal(profile: str, name: str, code: str,
                   project: str = None) -> str:
    """Write an AI proposal to the project's proposals/ folder."""
    proj = (project or name).upper()
    path = get_path(profile, "PROP", name, proj)
    _ensure(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


# ── Public read API ───────────────────────────────────────────────────────────

def read_file(profile: str, folder: str, filename: str,
              project: str = None) -> str:
    """
    Read a workspace file by folder name and filename.
    If project is omitted, searches all project folders.
    """
    if project:
        path = os.path.join(_WORKSPACE_ROOT, profile, project, folder, filename)
    else:
        # 1. Try project named after the file stem
        stem = os.path.splitext(filename)[0].upper()
        path = os.path.join(_WORKSPACE_ROOT, profile, stem, folder, filename)
        if not os.path.exists(path):
            # 2. Search all project folders
            base = os.path.join(_WORKSPACE_ROOT, profile)
            if not os.path.exists(base):
                return ""
            for proj in os.listdir(base):
                p = os.path.join(base, proj, folder, filename)
                if os.path.exists(p):
                    path = p
                    break

    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_code(profile: str, ftype: str, name: str,
              project: str = None) -> str:
    """Load ABAP source from workspace. Returns '' if not found."""
    folder = _PROP_FOLDER if ftype == "PROP" else _SOURCE_FOLDER
    return read_file(profile, folder, f"{name.upper()}.abap", project)


def read_table_fields(profile: str, name: str,
                      project: str = None) -> list:
    """Load saved table field definitions. Returns [] if not found."""
    content = read_file(profile, _SOURCE_FOLDER, f"{name.upper()}.json", project)
    if not content:
        return []
    try:
        return json.loads(content)
    except Exception:
        return []


# ── Directory listing ─────────────────────────────────────────────────────────

def list_files(profile: str) -> dict:
    """
    Return workspace contents as:
        { prog_name: { "programs": [filenames], "proposals": [filenames] } }
    """
    base = os.path.join(_WORKSPACE_ROOT, profile)
    if not os.path.exists(base):
        return {}

    result = {}
    for proj in sorted(os.listdir(base)):
        proj_path = os.path.join(base, proj)
        if not os.path.isdir(proj_path):
            continue
        if not _is_custom(proj):
            continue   # skip non-Z/Y folders

        entry = {}
        for sub in (_SOURCE_FOLDER, _PROP_FOLDER):
            sub_path = os.path.join(proj_path, sub)
            if os.path.isdir(sub_path):
                files = sorted(os.listdir(sub_path))
                if files:
                    entry[sub] = files
        if entry:
            result[proj] = entry

    return result


def list_profiles() -> list:
    """Return profile names that have workspace folders."""
    if not os.path.exists(_WORKSPACE_ROOT):
        return []
    return [d for d in os.listdir(_WORKSPACE_ROOT)
            if os.path.isdir(os.path.join(_WORKSPACE_ROOT, d))]


def scan_proposals(profile: str) -> list:
    """
    Find all proposals across all project folders.
    Returns [ (project, filename), ... ]
    """
    base = os.path.join(_WORKSPACE_ROOT, profile)
    if not os.path.exists(base):
        return []

    found = []
    for proj in os.listdir(base):
        prop_dir = os.path.join(base, proj, _PROP_FOLDER)
        if os.path.isdir(prop_dir):
            for f in sorted(os.listdir(prop_dir)):
                found.append((proj, f))
    return found
