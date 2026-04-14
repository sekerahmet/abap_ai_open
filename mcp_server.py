"""
ABAP AI — MCP Server
Exposes SAP RFC operations as tools for Claude Desktop / Claude Code.

Connection priority at startup:
  1. SAP_PROFILE env var  → load named profile from systems.json
  2. First profile in systems.json (if any exist)
  3. SAP_* env vars in .env  (fallback)

Profile can be changed at runtime via the switch_profile tool.

Start: python mcp_server.py
"""

import os
import json
import sys
from utils.env_loader import load_robust_env
from fastmcp import FastMCP

load_robust_env()

from core.sap.program_reader import ProgramReader
from core.sap.ddic_reader import DDICReader
from utils import workspace

# ── Connection state ──────────────────────────────────────────────────────────

_APPDATA_SYSTEMS = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ABAP_AI", "systems.json"
)

# Mutable — updated in place by switch_profile so all readers pick up the change
CONN: dict = {}
_active_profile: str = ""


def _read_profiles() -> dict:
    if not os.path.exists(_APPDATA_SYSTEMS):
        return {}
    with open(_APPDATA_SYSTEMS, "r") as f:
        return json.load(f)


def _profile_data_to_conn(data: dict) -> dict:
    """Convert a systems.json profile entry to pyrfc-ready params."""
    conn = {k: data[k] for k in ("ashost", "sysnr", "client", "user", "passwd") if data.get(k)}
    router = data.get("saprouter") or data.get("router", "")
    if router:
        conn["saprouter"] = router
    return conn


def _init_conn():
    """Load initial connection at startup."""
    global _active_profile
    profiles = _read_profiles()

    target = os.environ.get("SAP_PROFILE", "")
    name = target if target in profiles else (next(iter(profiles)) if profiles else "")
    data = profiles.get(name, {})

    if data:
        _active_profile = name
        CONN.update(_profile_data_to_conn(data))
        return

    # Fallback: .env vars
    _active_profile = ".env"
    env_conn = {
        "ashost": os.getenv("SAP_ASHOST", ""),
        "sysnr":  os.getenv("SAP_SYSNR", "00"),
        "client": os.getenv("SAP_CLIENT", "100"),
        "user":   os.getenv("SAP_USER", ""),
        "passwd": os.getenv("SAP_PASSWD", ""),
    }
    CONN.update({k: v for k, v in env_conn.items() if v})
    router = os.getenv("SAP_ROUTER", "")
    if router:
        CONN["saprouter"] = router


_init_conn()


def _program_reader() -> ProgramReader:
    return ProgramReader(dict(CONN))  # copy so singleton detects param changes


def _ddic_reader() -> DDICReader:
    return DDICReader(dict(CONN))


# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "ABAP AI",
    instructions=(
        "You are connected to a live SAP system via RFC and a local workspace cache. "
        "Fetch tools check the workspace cache first; [SOURCE: workspace/...] means cached, "
        "[SOURCE: SAP/...] means live. Use force_fetch=True only when you need the absolute "
        "latest version from SAP. "
        "IMPORTANT: Always call list_sap_profiles first to confirm which system is active. "
        "If the requested object is not found, suggest switching profiles with switch_profile. "
        "When you need more context about a referenced table, class, or include — fetch it autonomously. "
        "Standard SAP objects (not Z*/Y*) are fetched live and not saved to the workspace cache."
    ),
)


@mcp.tool()
def list_sap_profiles() -> str:
    """
    List all available SAP connection profiles and show which one is currently active.
    Call this first when unsure which system to use.
    """
    profiles = _read_profiles()
    if not profiles:
        return "No profiles found. Configure connections in the ABAP AI IDE first."

    lines = [f"Active profile: {_active_profile}\n", "Available profiles:"]
    for name, data in profiles.items():
        marker = "  -> " if name == _active_profile else "     "
        lines.append(
            f"{marker}{name}  "
            f"(host: {data.get('ashost', '?')}  "
            f"client: {data.get('client', '?')})"
        )
    return "\n".join(lines)


@mcp.tool()
def switch_profile(profile_name: str) -> str:
    """
    Switch the active SAP connection to a different profile from systems.json.
    Use list_sap_profiles to see available profile names.
    After switching, subsequent fetch calls will use the new system.
    """
    global _active_profile
    profiles = _read_profiles()

    if profile_name not in profiles:
        available = ", ".join(profiles.keys()) or "none"
        return f"ERROR: Profile '{profile_name}' not found. Available: {available}"

    data = profiles[profile_name]
    new_conn = _profile_data_to_conn(data)

    # Update CONN in place — SAPConnectionManager singleton detects the change
    CONN.clear()
    CONN.update(new_conn)
    _active_profile = profile_name

    return (
        f"Switched to profile: {profile_name}\n"
        f"  Host:   {data.get('ashost', '?')}\n"
        f"  Client: {data.get('client', '?')}\n"
        f"  User:   {data.get('user', '?')}"
    )


def _fmt_table(name: str, fields: list) -> str:
    """Format table field list as readable text (shared by workspace and SAP paths)."""
    header = f"Table: {name}  ({len(fields)} fields)\n"
    separator = "-" * 80 + "\n"
    rows = "\n".join(
        f"{f.get('Field', ''):<30}  {f.get('Type', ''):<10}  "
        f"{str(f.get('Len', '')):<6}  {f.get('Description', '')}"
        for f in fields
    )
    return header + separator + rows


@mcp.tool()
def fetch_program(program_name: str, force_fetch: bool = False) -> str:
    """
    Return the ABAP source code for a program or include.
    Strategy: workspace cache first → SAP live fetch if not found (or force_fetch=True).
    Works for: REPORT programs, INCLUDE programs, any PROG-type object.
    Set force_fetch=True to bypass the cache and pull the latest version from SAP.
    """
    name = program_name.upper()
    if not force_fetch:
        cached = workspace.read_code(_active_profile, "Program", name)
        if cached:
            return f"[SOURCE: workspace/{_active_profile}]\n{cached}"

    code, err = _program_reader().fetch_code(name)
    if not code:
        return f"ERROR: {err}\nTip: Use list_sap_profiles / switch_profile to change the active system."
    workspace.save_code(_active_profile, "Program", name, code)
    return f"[SOURCE: SAP/{_active_profile}]\n{code}"


@mcp.tool()
def fetch_function_module(function_name: str, force_fetch: bool = False) -> str:
    """
    Return the ABAP source code of a Function Module.
    Strategy: workspace cache first → SAP live fetch if not found (or force_fetch=True).
    Set force_fetch=True to bypass the cache and pull the latest version from SAP.
    """
    name = function_name.upper()
    if not force_fetch:
        cached = workspace.read_code(_active_profile, "Function Module", name)
        if cached:
            return f"[SOURCE: workspace/{_active_profile}]\n{cached}"

    code, err = _program_reader().fetch_function_module(name)
    if not code:
        return f"ERROR: {err}"
    workspace.save_code(_active_profile, "Function Module", name, code)
    return f"[SOURCE: SAP/{_active_profile}]\n{code}"


@mcp.tool()
def fetch_class(class_name: str, force_fetch: bool = False) -> str:
    """
    Return the ABAP source of a Global Class (pool + public + private sections).
    Strategy: workspace cache first → SAP live fetch if not found (or force_fetch=True).
    Set force_fetch=True to bypass the cache and pull the latest version from SAP.
    """
    name = class_name.upper()
    if not force_fetch:
        cached = workspace.read_code(_active_profile, "Global Class", name)
        if cached:
            return f"[SOURCE: workspace/{_active_profile}]\n{cached}"

    code, err = _program_reader().fetch_class_source(name)
    if not code:
        return f"ERROR: {err}"
    workspace.save_code(_active_profile, "Global Class", name, code)
    return f"[SOURCE: SAP/{_active_profile}]\n{code}"


@mcp.tool()
def fetch_table_fields(table_name: str, force_fetch: bool = False) -> str:
    """
    Return field definitions for an SAP table or structure.
    Strategy: workspace cache first → SAP live fetch if not found (or force_fetch=True).
    Returns: FIELDNAME | TYPE | LENGTH | DESCRIPTION
    Set force_fetch=True to bypass the cache and pull the latest version from SAP.
    """
    name = table_name.upper()
    if not force_fetch:
        cached_fields = workspace.read_table_fields(_active_profile, name)
        if cached_fields:
            return f"[SOURCE: workspace/{_active_profile}]\n{_fmt_table(name, cached_fields)}"

    _, attrs = _ddic_reader().fetch_table(name)
    if not attrs or isinstance(attrs, str):
        return f"ERROR: {attrs}"

    fields = attrs.get("FIELDS", [])
    if not fields:
        return f"Table '{table_name}' found but has no fields."

    workspace.save_table(_active_profile, name, fields)
    return f"[SOURCE: SAP/{_active_profile}]\n{_fmt_table(name, fields)}"


@mcp.tool()
def check_objects_in_tadir(names: list[str]) -> dict:
    """
    Check which SAP object names exist in TADIR on the active system.
    Returns: {"OBJECT_NAME": "TYPE"} — unknown names are omitted.
    Example result: {"ACDOCA": "TABL", "ZCL_HELPER": "CLAS"}
    """
    if not names:
        return {}
    return _ddic_reader().check_objects_batch([n.upper() for n in names])


@mcp.tool()
def fetch_table_data(table_name: str, where_clause: str = "", max_rows: int = 200) -> str:
    """
    Fetch actual data rows from an SAP table (not just field definitions).
    Always fetches live from SAP — table data is not cached in the workspace.
    where_clause: optional WHERE condition (e.g. "BUKRS = '1000' AND GJAHR = '2024'")
    max_rows: maximum rows to return (default 200)
    """
    name = table_name.upper()
    columns, rows = _ddic_reader().fetch_table_data(name, where_clause, max_rows)
    if columns is None:
        return f"ERROR: {rows}"
    if not rows:
        msg = f"Table '{name}': no rows found"
        return msg + (f" for WHERE: {where_clause}" if where_clause else "")

    col_widths = [max(len(c), max((len(str(r[i])) for r in rows), default=0))
                  for i, c in enumerate(columns)]
    header = "  ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns))
    separator = "  ".join("-" * w for w in col_widths)
    data_lines = ["  ".join(str(r[i]).ljust(col_widths[i]) for i in range(len(columns)))
                  for r in rows]

    result = f"Table: {name}  ({len(rows)} rows)\n"
    if where_clause:
        result += f"WHERE: {where_clause}\n"
    result += separator + "\n" + header + "\n" + separator + "\n"
    result += "\n".join(data_lines)
    return result


# ── Workspace tools ──────────────────────────────────────────────────────────

@mcp.tool()
def list_workspace_files(profile: str = "") -> str:
    """
    List all custom (Z*/Y*) objects already fetched and saved by the ABAP AI IDE.
    These files are available for analysis without a live SAP connection.
    Leave profile empty to list all profiles.
    """
    if not profile:
        profiles = workspace.list_profiles()
        return "Available profiles:\n" + "\n".join(f"- {p}" for p in profiles)

    all_files = workspace.list_files(profile)
    if not all_files:
        return f"Workspace for profile '{profile}' is empty."

    lines = [f"Workspace for profile '{profile}':"]
    for project, types in sorted(all_files.items()):
        lines.append(f"\n📂 {project}/")
        for folder, fnames in sorted(types.items()):
            lines.append(f"  {folder}/")
            for f in fnames:
                lines.append(f"    {f}")
    return "\n".join(lines)


@mcp.tool()
def read_workspace_file(profile: str, folder: str, filename: str, project: str = "") -> str:
    """
    Read a file from the local workspace saved by the ABAP AI IDE.
    Use list_workspace_files to discover available files.
    folder: "programs" for source files, "proposals" for AI proposals
    filename: e.g. ZFI_001_UFRS_01.abap or ZTABLE.json
    project: (optional) The project/program folder name.
    """
    content = workspace.read_file(profile, folder, filename, project=project if project else None)
    if not content:
        loc = f"{project}/{folder}/{filename}" if project else f"*/{folder}/{filename}"
        return f"File not found: workspace/{profile}/{loc}"
    return content


@mcp.tool()
def write_proposal(profile: str, program_name: str, code: str, project: str = "") -> str:
    """
    Write an AI-generated code proposal to the workspace PROP/ folder.
    The ABAP AI IDE watches this folder and will automatically open
    a diff tab showing added lines (green) and removed lines (red).
    program_name: the original program name (e.g. ZFI_001_UFRS_01)
    code: the complete proposed ABAP source code
    project: (optional) The project/program folder name.
    """
    # If project not passed, guess from program_name
    proj = project if project else program_name.upper()
    path = workspace.write_proposal(profile, program_name, code, project=proj)
    return f"Proposal saved to project '{proj}': {path}\nThe ABAP AI IDE will open a diff tab within 2 seconds."


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not CONN.get("ashost"):
        print(
            "WARNING: No SAP connection configured.\n"
            "  Option 1: Open ABAP AI IDE, save a connection profile.\n"
            "  Option 2: Fill in SAP_* vars in .env\n"
            "  Option 3: Set SAP_PROFILE env var to a profile name.",
            file=sys.stderr,
        )
    mcp.run()
