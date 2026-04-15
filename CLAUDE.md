# ABAP AI IDE — Project Reference for Claude

## What This Is
A desktop IDE built with Python + CustomTkinter that connects to SAP systems via RFC (pyrfc),
fetches ABAP source code, parses referenced objects, and displays a git-aware workspace explorer.
Also exposes an MCP server so Claude Desktop can read/write the same objects via RFC + local workspace.
Packaged as a single Windows .exe with PyInstaller.

---

## Architecture (3 layers)

```
ui/          ← CustomTkinter GUI  (never touch SAP/AI directly)
core/        ← Business logic     (SAP readers, controller)
utils/       ← Stateless helpers  (parser, highlighter, workspace, github_sync)
```

### Layer responsibilities
| Layer | Allowed to | Must NOT |
|---|---|---|
| `ui/` | call `app.controller.*`, update widgets | import pyrfc, call AI directly |
| `core/` | RFC calls, pure logic | import tkinter |
| `utils/` | regex, text manipulation, filesystem | import tkinter, pyrfc, `core.*` |

---

## File Map

| File | Role |
|---|---|
| `main.py` | Entry point — just `App().mainloop()` |
| `main.spec` | PyInstaller spec (`console=True` for dev, `False` for release) |
| `mcp_server.py` | FastMCP server — exposes SAP RFC + workspace tools to Claude Desktop |
| `ui/main_app.py` | `App(ctk.CTk)` — glue class; owns threading, tab routing, log |
| `ui/panels/sidebar.py` | `SidebarPanel` — connection profiles only (profile dropdown + 6 Entry fields + Save button) |
| `ui/panels/editor.py` | `EditorPanel` — custom tab bar + content area |
| `ui/panels/explorer_panel.py` | `ExplorerPanel` — git-aware right column: SAP Objects + Workspace tabs, Push/Pull/Refresh toolbar, branch label |
| `core/controller.py` | `AnalysisController` — single facade for all SAP operations |
| `core/sap/connection.py` | `SAPConnectionManager` — singleton, always fresh connect (no ping) |
| `core/sap/program_reader.py` | `ProgramReader` — RPY_PROGRAM_READ, RPY_FUNCTIONMODULE_READ, class includes |
| `core/sap/program_writer.py` | `ProgramWriter` — transport list, TR assign, syntax check, write (5 candidates) |
| `core/sap/ddic_reader.py` | `DDICReader` — DDIF_FIELDINFO_GET, TADIR batch check |
| `core/config.py` | `Config` — env var defaults |
| `utils/highlighter.py` | `ABAPHighlighter` — regex tag-based syntax coloring for CTkTextbox |
| `utils/parser.py` | `ABAPParser` — extracts DICT/CLASS/INCLUDES/FIELDS/events from ABAP source |
| `utils/workspace.py` | `workspace` — filesystem bridge; saves/reads Z*/Y* objects in AppData |
| `utils/github_sync.py` | `github_sync` — push/pull workspace to GitHub via subprocess git; git status + branch query |

---

## Key Patterns

### Threading rule (critical)
All SAP RFC calls run in `daemon=True` threads. GUI updates **must** use `self.after(0, fn, args)`.
Never call tkinter widgets from a background thread directly — includes `messagebox` dialogs.

```python
# CORRECT — dialog from background thread
self.after(0, self._ask_something, arg1, arg2)   # runs on main thread
return   # background thread ends here

# WRONG — will crash
mbox.askyesno(...)   # called from background thread
```

### Tab opening (duplicate guard)
`open_code_tab`, `open_ddic_tab`, and `open_diff_tab` all check `self.editor.tabs_dict` first.
If tab name already exists → just `set_active(name)`, do not create a duplicate.

### DDIC display
Tables and Structures open via `open_ddic_tab(name, attrs, ftype)` which renders a `ttk.Treeview`
with columns: Field Name / Type / Length / Description.
Code tabs open via `open_code_tab(name, code, _attrs, prog, ftype)` which renders a `CTkTextbox` + highlighter.
Diff tabs open via `open_diff_tab(name, original_code, proposed_code)` with green/red line coloring.

### Workspace-first fetch
`run_fetch` and `run_sub_fetch` both accept a `force=False` parameter.
- `force=False` (default): check `utils/workspace` first; skip RFC if found
- `force=True`: always hit SAP, overwrite workspace cache
- Re-fetch button in each code/DDIC tab calls `refetch_object(tab_name, prog, ftype)`
- For `ftype="Program"`, Re-fetch also passes `force_sub=True` to `run_proactive_check`,
  forcing all discovered Z*/Y* tables to be re-fetched from SAP (not just re-used from cache)

### .env path (PyInstaller safe)
All modules that load `.env` use a `_find_dotenv()` helper:
```python
def _find_dotenv():
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), ".env")
    return os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_find_dotenv())
```
After building, copy `.env` next to `dist/main.exe` — it is NOT bundled (contains secrets).

### App context wiring
`SidebarPanel`, `EditorPanel`, `ExplorerPanel` each receive `app_context` (the `App` instance).
They set widget references on `app` (e.g. `self.app.fetch_btn = ...`) so `App` can call them
from threading callbacks without circular imports.

---

## Data Persistence

| What | Where |
|---|---|
| Connection profiles | `%APPDATA%\ABAP_AI\systems.json` |
| Workspace (Z*/Y* source code) | `%APPDATA%\ABAP_AI\workspace\{profile}\{PROG_NAME}\programs\` |
| Workspace (Z*/Y* table fields) | `%APPDATA%\ABAP_AI\workspace\{profile}\{PROG_NAME}\tables\` |
| AI proposals | `%APPDATA%\ABAP_AI\workspace\{profile}\{PROG_NAME}\proposals\` |
| GitHub token | `.env` → `GITHUB_TOKEN`, `GITHUB_REPO` |

All paths use `%APPDATA%` — survive `pyinstaller --clean`, uninstall, and `dist/` deletion.
Directory is auto-created on first run.

---

## Build

```bash
pyinstaller main.spec
# Output: dist/main.exe
# console=True during development (shows tracebacks)
# console=False for release build
```

**After `--clean`:** only `dist/` is wiped. AppData (`systems.json`, `workspace/`) is untouched.
**After build:** copy `.env` next to `dist/main.exe`.

---

## RFC Functions Used

| RFC | Purpose |
|---|---|
| `RPY_PROGRAM_READ` | Fetch ABAP program / include source |
| `RPY_FUNCTIONMODULE_READ` | Fetch Function Module source |
| `DDIF_FIELDINFO_GET` | Fetch table/structure field metadata |
| `RFC_READ_TABLE` on `TADIR` | Verify which objects exist in the system (batch) |
| `RFC_READ_TABLE` on `E070`/`E07T` | List open transport requests + descriptions |
| `RS_CORR_INSERT` | Assign object to transport request (OBJECT_CLASS: ABAP/CLAS) |
| `SYNTAX_CHECK` | Check ABAP syntax before write (optional — skipped if unavailable) |
| `RPY_PROGRAM_WRITE` | Write ABAP source (candidate 1) |
| `RPY_PROGRAM_INSERT_MASTER` | Write ABAP source (candidate 2) |
| `RS_PROGRAM_WRITE` | Write ABAP source (candidate 3 — older systems) |
| `RFC_ABAP_INSTALL_AND_RUN` | Write ABAP source (candidate 4 — PROGRAMNAME, MODE='F', PROGRAM table) |
| `Z_ABAP_AI_WRITE_PROG` | Write ABAP source (candidate 5 — custom Z FM, most reliable fallback) |

### Z_ABAP_AI_WRITE_PROG — custom FM signature
```abap
FUNCTION Z_ABAP_AI_WRITE_PROG.
*"  IMPORTING  REFERENCE(IV_PROG) TYPE SYREPID
*"  TABLES     IT_SOURCE LIKE ZABAP_AI_SRCLINE
*"  EXCEPTIONS WRITE_ERROR
  INSERT REPORT iv_prog FROM it_source.
  IF sy-subrc <> 0. RAISE write_error. ENDIF.
ENDFUNCTION.
```
`ZABAP_AI_SRCLINE` = SE11 structure with one field: `LINE CHAR 72`.
Must be Remote-Enabled (RFC) and activated in the target system.

### Write flow (ProgramWriter.write_program)
```
1. SYNTAX_CHECK           → warn/block on errors, skip if RFC unavailable
2. RS_CORR_INSERT         → assign to TR (TR_ASSIGN_FAILED marker if fails)
3. write candidates 1–5   → first success wins
```

### TADIR query constraints
- `OPTIONS` rows max **72 chars** each
- One condition per row; subsequent rows start with `OR `
- `WA` field is fixed-width: `OBJ_NAME` = chars 0–39, `OBJECT` = chars 40+
- Do NOT use `.split()` on WA — always slice by position

### RS_CORR_INSERT OBJECT_CLASS mapping
Different from TADIR OBJECT field:
- `PROG` / `FUGR` → `"ABAP"`
- `CLAS` → `"CLAS"`

---

## SAP Object Types (TADIR OBJECT field)
| Code | Meaning |
|---|---|
| `PROG` | Program / Include |
| `TABL` | Transparent Table |
| `VIEW` | View |
| `CLAS` | Global Class |
| `FUGR` | Function Group |
| `FUNC` | Function Module |
| `MSAG` | Message Class |

---

## MCP Server (`mcp_server.py`)

Started separately: `python mcp_server.py`. Registered in Claude Desktop config.

### Connection management
- `CONN` dict and `_active_profile` string are module-level mutables
- `switch_profile` tool updates `CONN` in-place; `SAPConnectionManager` detects param change
- `_program_reader()` and `_ddic_reader()` always pass `dict(CONN)` (copy) so singleton resets

### Workspace-first in MCP tools
All four fetch tools follow the same pattern as the IDE:
```
fetch_program(name, force_fetch=False)
  → workspace.read_code(_active_profile, ftype, name) if not force_fetch
  → SAP RFC if cache miss, then workspace.save_code(...)
```
Return value prefixed with `[SOURCE: workspace/profile]` or `[SOURCE: SAP/profile]`.

### MCP tool inventory
| Tool | Description |
|---|---|
| `list_sap_profiles` | Show available profiles + active one |
| `switch_profile` | Change active SAP connection at runtime |
| `fetch_program` | Workspace-first; falls back to SAP RFC |
| `fetch_function_module` | Workspace-first; falls back to SAP RFC |
| `fetch_class` | Workspace-first; falls back to SAP RFC |
| `fetch_table_fields` | Workspace-first (JSON); falls back to SAP RFC |
| `check_objects_in_tadir` | Live TADIR batch check (no workspace) |
| `list_workspace_files` | List all cached Z*/Y* files by profile |
| `read_workspace_file` | Read any workspace file directly |
| `write_proposal` | Write proposed ABAP to prop/ → IDE opens diff tab |
