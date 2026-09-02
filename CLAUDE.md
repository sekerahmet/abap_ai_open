# ABAP AI IDE — Project Reference for Claude

## What This Is
A desktop IDE built with Python + CustomTkinter that connects to SAP systems via RFC (pyrfc),
fetches ABAP source code **read-only**, parses referenced objects, and displays a git-aware
workspace explorer. Also exposes an MCP server so Claude Desktop can read the same objects
via RFC + local workspace and drop code *proposals* into the workspace (shown as diffs).

**Hard constraint: the SAP connection is one-way. Nothing is ever written to SAP.**
There is no upload, transport, syntax-check or AI-chat code in this project any more —
do not re-introduce them.

Packaged as a single Windows .exe with PyInstaller.

---

## Architecture (3 layers)

```
ui/          ← CustomTkinter GUI  (never touches pyrfc directly)
core/        ← SAP readers + controller facade
utils/       ← Stateless helpers  (parser, highlighter, workspace, github_sync, env_loader)
```

| Layer | Allowed to | Must NOT |
|---|---|---|
| `ui/` | call `app.controller.*`, update widgets | import pyrfc |
| `core/` | RFC calls, pure logic | import tkinter |
| `utils/` | regex, text manipulation, filesystem, subprocess git | import tkinter, pyrfc, `core.*` |

---

## File Map

| File | Role |
|---|---|
| `main.py` | Entry point — runs `App()`, writes uncaught exceptions to `%APPDATA%\ABAP_AI\crash.log` |
| `main.spec` | PyInstaller spec (`console=False`, `debug=False`; flip `console=True` to see tracebacks) |
| `mcp_server.py` | FastMCP server — read-only SAP RFC + workspace tools for Claude Desktop |
| `ui/main_app.py` | `App(ctk.CTk)` — glue: profiles, threads, tabs, SAP-object tree, workspace explorer, proposal watcher, GitHub sync |
| `ui/panels/sidebar.py` | `SidebarPanel` — connection profiles (dropdown + entries + Save), `CONN_FIELDS` |
| `ui/panels/editor.py` | `EditorPanel` — fetch bar, horizontally scrollable tab bar, content area, `OBJECT_TYPES` |
| `ui/panels/explorer_panel.py` | `ExplorerPanel` — SAP Objects tree + Workspace tree (git status, Push/Pull/Refresh, branch label) |
| `ui/panels/claude_panel.py` | `ClaudePanel` — one Claude Code session per tab (transcript + input, resume menu) |
| `core/claude_runner.py` | `ClaudeSession` — runs `claude -p --output-format stream-json` with the user's subscription login; MCP config discovery |
| `core/controller.py` | `AnalysisController` — stateless facade; builds a reader per call from the conn dict it receives |
| `core/sap/connection.py` | `SAPConnectionManager` — one connection per `execute()`, or `session()` for batches; no shared state |
| `core/sap/program_reader.py` | `ProgramReader` — programs/includes, function modules, global classes (all includes + methods via TMDIR) |
| `core/sap/ddic_reader.py` | `DDICReader` — DDIF_FIELDINFO_GET, RFC_READ_TABLE data, chunked TADIR check |
| `utils/parser.py` | `ABAPParser` — strips comments, extracts DICT/CLASS/INCLUDES/FORMS/FIELDS/EVENTS with line numbers |
| `utils/highlighter.py` | `ABAPHighlighter` — line/col based syntax colouring (fast on large sources) |
| `utils/workspace.py` | filesystem bridge; Z*/Y* objects in AppData; "existing location wins" rule |
| `utils/github_sync.py` | push/pull via git CLI; token passed as HTTP header, never stored; serialised by a lock |
| `utils/env_loader.py` | `.env` discovery for source and PyInstaller runs |

---

## Key Patterns

### Threading rule (critical)
All RFC / git / disk-heavy work runs in `daemon=True` threads. GUI updates **must** go through
`self.after(0, fn, args)`. Never touch widgets (or `messagebox`) from a background thread.
Read `StringVar`s (e.g. the active profile) on the main thread and pass the value into the worker.

### Connection handling
`SAPConnectionManager` is **not** a singleton. `execute()` opens → calls → closes; `session()`
keeps one connection for a batch. The controller is stateless, so the sidebar's active profile is
always the system that gets called.

### Tab naming (duplicate guard)
Every tab title is produced by `_tab_name(ftype, NAME)` in `main_app.py`:
`Program: X`, `Global Class: X`, `Function Module: X`, `Table: X` (tables *and* structures),
plus `Proposal: X` (editable proposal code), `Diff: X`, `Data: X [where]`.
`open_*_tab` methods first check `self.editor.tabs_dict` and just activate an existing tab.

### Workspace-first fetch
`run_fetch` / `run_sub_fetch` take `force=False`:
- `force=False`: read from `utils/workspace` first; RFC only on cache miss
- `force=True` (Re-fetch button): always RFC, overwrite the cache; for `Program` also re-parse
  includes and re-cache Z*/Y* tables (`force_sub=True`)
Loading a Program from the workspace builds the object tree offline (`_populate_tree_offline`)
using only cached knowledge — no TADIR check until Re-fetch.

### Workspace location rule
`workspace.save_*` / `write_proposal` keep a file where it already exists (any project folder of
the profile). Otherwise `project=` is used, else the object's own folder. This stops Save /
Re-fetch from creating duplicate copies of includes and tables.

### Cached .abap object type
The workspace stores no type metadata; `workspace.guess_ftype(code)` infers
`Function Module` / `Global Class` / `Program` from the content when a file is opened.

### SAP Objects tree
`values=(tadir_type, line, name)`. Single click → `jump_to_line` in the main program tab.
Double click → `run_sub_fetch` with category from `_TADIR_META` (TABL/VIEW → DICT,
CLAS → CLASS, PROG → PROG, FUNC → FUNC); other TADIR types only jump.

### Proposal watcher
`_poll_proposals` runs every 2 s on the main thread but does only cheap work:
- `workspace.snapshot()` (paths + mtimes) → if changed, `refresh_workspace_tree()` which does
  `git status` in a background thread and rebuilds the tree preserving open state + scroll
- `scan_proposals(profile)` → a proposal is opened when its key is new **or its mtime changed**
- `_seed_proposals(profile)` marks existing proposals as seen at startup / profile switch
The loop is wrapped in try/finally so an error never stops polling.

### Claude Code tab (subscription, no API key)
`✦ Claude` opens `Claude: #n`. Each message = one `claude -p` subprocess (prompt on stdin,
`--resume <session_id>` after the first turn, `--include-partial-messages` for streaming).
cwd = `workspace/{profile}`; allowed tools = Read/Glob/Grep/LS + `mcp__<server>`; the MCP
server config is taken from Claude Desktop's config (any server whose args mention
`mcp_server.py`), else this checkout's `mcp_server.py`, with `SAP_PROFILE` set to the active
profile. `get_active_code_context()` prepends the open code tab (inline ≤ 20k chars, else the
path). Sessions are listed in `%APPDATA%\ABAP_AI\claude_sessions.json`. The Agent SDK is
deliberately not used: Anthropic does not allow subscription auth through the SDK for
third-party apps; the CLI in `-p` mode is fine for the user's own tooling.

### App context wiring
Panels receive `app_context` (the `App`) and set widget references on it
(`self.app.sap_ashost`, `self.app.tree`, `self.app.ws_tree`, `self.app.fetch_btn`).

### .env
All modules use `utils.env_loader.load_robust_env()`. `.env` is **not** bundled — copy it next
to `dist/main.exe`. Keys: `GITHUB_TOKEN`, `GITHUB_REPO`, optional `SAP_*` fallbacks for the MCP server.

---

## Data Persistence

| What | Where |
|---|---|
| Connection profiles (plain-text passwords) | `%APPDATA%\ABAP_AI\systems.json` |
| Workspace source | `%APPDATA%\ABAP_AI\workspace\{profile}\{PROJECT}\programs\NAME.abap` |
| Workspace table fields | `%APPDATA%\ABAP_AI\workspace\{profile}\{PROJECT}\tables\NAME.json` |
| AI proposals | `%APPDATA%\ABAP_AI\workspace\{profile}\{PROJECT}\proposals\NAME.abap` |
| Crash log | `%APPDATA%\ABAP_AI\crash.log` |
| Claude sessions / MCP config | `%APPDATA%\ABAP_AI\claude_sessions.json`, `claude_mcp_<profile>.json` |
| Workspace git repo | `%APPDATA%\ABAP_AI\workspace\.git` (branch `main`, proposals ignored) |

`systems.json` in the repo root is git-ignored — never commit it.

---

## Build

```bash
pyinstaller main.spec       # → dist/main.exe ; then copy .env next to it
```
`main.spec` has `console=False`. For debugging a build, set `console=True` temporarily or
read `%APPDATA%\ABAP_AI\crash.log`.

---

## RFC Functions Used (all read-only)

| RFC | Purpose |
|---|---|
| `RPY_PROGRAM_READ` | Program / include source (`SOURCE_EXTENDED` or `SOURCE`) |
| `RPY_FUNCTIONMODULE_READ_NEW` → `RPY_FUNCTIONMODULE_READ` | Function module source |
| `RFC_READ_TABLE` on `TMDIR` | Method list of a class → `…CM001` include names (base-36 index) |
| `DDIF_FIELDINFO_GET` | Table / structure / view fields |
| `RFC_READ_TABLE` on `TADIR` | Batch existence check (40 names per call) |
| `RFC_READ_TABLE` on any table | Table data (max 200 rows, WHERE split at word boundaries) |

### Class include naming
Class name padded to 30 chars with `=`: `ZCL_X====================CU`.
Sections: `CCDEF`, `CU` (public), `CO` (protected), `CI` (private), `CCMAC`, `CCIMP`.
Methods: `CM` + 3-digit base-36 of `TMDIR.METHODINDX`.

### RFC_READ_TABLE constraints
- `OPTIONS` rows max **72 chars**; one condition per row; later rows start with `OR ` / `AND `
- TADIR `WA` is fixed-width: `OBJ_NAME` = chars 0–39, `OBJECT` = 40+ — slice, never split
- With `DELIMITER="|"`, split on `|` (E070/TMDIR style reads)

---

## MCP Server (`mcp_server.py`)

Started separately: `python mcp_server.py`. Registered in Claude Desktop config.

| Tool | Description |
|---|---|
| `list_sap_profiles` | Show available profiles + active one |
| `switch_profile` | Change active SAP connection at runtime |
| `fetch_program` / `fetch_function_module` / `fetch_class` | Workspace-first; SAP RFC on miss; `force_fetch=True` bypasses cache |
| `fetch_table_fields` | Workspace-first (JSON); SAP RFC on miss |
| `fetch_table_data` | Live RFC_READ_TABLE rows (never cached) |
| `check_objects_in_tadir` | Live TADIR batch check |
| `list_workspace_files` / `read_workspace_file` | Browse the cache |
| `write_proposal` | Write proposed ABAP to `proposals/` → IDE opens a diff tab within 2 s |

Return values are prefixed with `[SOURCE: workspace/profile]` or `[SOURCE: SAP/profile]`.
