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
ui/          ← PySide6 (Qt) GUI  (never touches pyrfc directly)
core/        ← SAP readers + controller facade
utils/       ← Stateless helpers  (parser, highlighter, workspace, github_sync, env_loader)
```

| Layer | Allowed to | Must NOT |
|---|---|---|
| `ui/` | call `self.controller.*`, update widgets | import pyrfc |
| `core/` | RFC calls, pure logic | import PySide6 |
| `utils/` | regex, text manipulation, filesystem, subprocess git | import PySide6, pyrfc, `core.*` |

---

## File Map

| File | Role |
|---|---|
| `main.py` | Entry point — runs `App()`, writes uncaught exceptions to `%APPDATA%\ABAP_AI\crash.log` |
| `main.spec` | PyInstaller spec (`console=False`, `debug=False`; flip `console=True` to see tracebacks) |
| `mcp_server.py` | FastMCP server — read-only SAP RFC + workspace tools for Claude Desktop |
| `assets/abap_ai.ico` | application icon (window / taskbar / exe via `main.spec` `icon=` + `datas`) |
| `ui/theme.py` | colours + the application-wide Qt stylesheet (`QSS`) |
| `ui/bridge.py` | `Bridge.call(fn, …)` = run on the GUI thread from any thread (queued signal); `run_bg` |
| `ui/main_window.py` | `MainWindow(QMainWindow)` — toolbar, tabs, docks, status bar, all app logic (fetch, discovery, workspace, proposals, git, Claude sessions, Local profile, Open file / Paste code) |
| `ui/dialogs.py` | `ConnectionDialog`, `PasteCodeDialog`, `CONN_FIELDS` |
| `ui/highlighter_qt.py` | `AbapHighlighter`, `DiffHighlighter` (QSyntaxHighlighter) |
| `ui/widgets/code_editor.py` | `CodeView` = `CodeEditor(QPlainTextEdit)` with line numbers, current line, Ctrl+F find bar |
| `ui/widgets/tables.py` | `fields_table`, `data_table` (QTableWidget) |
| `ui/widgets/markdown.py` | markdown→HTML, `MessageBubble`, `CodeBlock` (Copy / Open as proposal) |
| `ui/panels/sap_tree.py` | `SapObjectsPanel` (filter + tree; `TADIR_META`) |
| `ui/panels/workspace_tree.py` | `WorkspacePanel` — `build()` SAP layout / `build_free()` Local folder tree; Push/Pull/📂/⟳, filter, git colours, context menus, file drop |
| `ui/panels/claude_side.py` | `ClaudeSidePanel` (account, usage bars, session manager) — left dock |
| `ui/panels/claude_chat.py` | `ClaudeChatTab` (bubbles, composer with attachments / image paste / model combo) |
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
All RFC / git / disk-heavy work runs in daemon threads (`ui.bridge.run_bg`). GUI updates **must**
go through `self.ui.call(fn, *args)` (a queued Qt signal executed on the GUI thread). Never touch
widgets or QMessageBox from a background thread. Read the active profile on the GUI thread and
pass it into the worker.

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
- `scan_proposals(profile)` walks the whole profile for `proposals/` folders at any depth and
  returns `(parent_rel, filename, mtime)`; a proposal is opened when its key is new **or its
  mtime changed**. `_open_proposal` decides SAP-style (key = object name) vs free-form (key =
  relative path) via `workspace.find_original`
- `_seed_proposals(profile)` marks existing proposals as seen at startup / profile switch
The loop is wrapped in try/finally so an error never stops polling.

### Claude Code tab (subscription, no API key)
`ClaudeChatTab`: message bubbles (markdown → HTML; code blocks are `CodeBlock` widgets with
Copy / "Open as proposal" → `MainWindow.proposal_from_code`), composer with `+` file attach,
Ctrl+V image paste and drag-drop (files are copied to `workspace/<profile>/_attachments/` and
their relative paths are listed in the prompt for the Read tool), model combo (`--model`),
context checkbox, Stop. Text streams raw and is re-rendered when the text block completes.
Usage windows from `rate_limit_event` persist in `claude_usage.json` and feed the left dock.
Session titles default to the first prompt; sessions are listed per profile in the left dock.
"+ New session" in the left dock opens `Claude: #n` (there is no toolbar button any more); the
session list has right-click Rename / Delete (Delete asks, optionally removes the transcript file). Each message = one `claude -p` subprocess (prompt on stdin,
`--resume <session_id>` after the first turn, `--include-partial-messages` for streaming).
cwd = `workspace/{profile}`; allowed tools = Read/Glob/Grep/LS + `mcp__<server>`; the MCP
server config is taken from Claude Desktop's config (any server whose args mention
`mcp_server.py`), else this checkout's `mcp_server.py`, with `SAP_PROFILE` set to the active
profile. `get_active_code_context()` prepends the open code tab (inline ≤ 20k chars, else the
path). Sessions are listed in `%APPDATA%\ABAP_AI\claude_sessions.json`. The Agent SDK is
deliberately not used: Anthropic does not allow subscription auth through the SDK for
third-party apps; the CLI in `-p` mode is fine for the user's own tooling.

### Layout (Qt)
`QMainWindow`: toolbar (profile combo · ⚙ · type · name · Fetch · Open file… · Paste code),
central `QTabWidget` (`self.tabs[name] = {widget, kind, view, code, prog, ftype, source_profile, rel}`;
each closable tab gets its own `✕` `QToolButton#tabclose` because the stock close icon is invisible on the dark theme),
right docks SAP OBJECTS / WORKSPACE (tabified), left dock CLAUDE, status bar. Dock layout and
geometry are saved with `saveState/saveGeometry` into `ui_state.json`. Shortcuts: Ctrl+B Claude
dock, Ctrl+Shift+E workspace, Ctrl+Shift+O objects, Ctrl+W close tab, Ctrl+F find.
Profiles are edited in `ConnectionDialog`; `get_current_conn()` reads `systems_data`.
`save_profile(name, data)` / `delete_profile(name)` are the only write paths.

### Local profile = free-form workspace
`LOCAL_PROFILE` (`workspace.LOCAL_PROFILE`, "Local (no SAP)") is always the last entry of the
profile combo. With it active Fetch is disabled and the WORKSPACE dock switches to
`WorkspacePanel.build_free()`: the raw folder tree under `workspace/Local (no SAP)/`, which the
user organises however they like (in the IDE via right-click New folder / New file / Import files /
Rename / Delete, by dropping files on the tree, or directly in Explorer via 📂 — the 2 s snapshot
poll picks up external changes). Any file opens: `.abap`-like → code tab (`guess_ftype`),
`.json` field list → table tab, anything else → plain "File:" tab.
Tabs for free-form files are keyed by the relative path (`Program: reports/ZFI_X.abap`) and carry
`rel` in their tab entry; Save writes back to that path. "Open file…" / "Paste code" import into
the folder selected in the tree (root if none); names are **not** forced to Z/Y here.
Proposals follow the same rule as SAP projects: the proposal for `<dir>/X.abap` is
`<dir>/proposals/X.abap` (`workspace.proposal_rel`); `find_original` maps it back
(`<dir>/programs/X` → `<dir>/X` → any `*/programs/X` → anywhere). Diff / Proposal tabs are keyed
by the target's relative path. `ClaudeSession.is_local` selects `SYSTEM_PROMPT_LOCAL`, which tells
Claude to use `write_proposal(..., path=<relative file path>)`.

### Panels talk via signals
Panels never touch the window; they emit (`jump`, `open_object`, `open_file`, `reveal`, `delete`,
`push`, `pull`, `refresh`, `new_session`, `open_session`, `forget_session`) and `MainWindow`
connects them. Filters re-render from the panel's last data without RFC or git.

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
| `list_workspace_files` / `read_workspace_file` | Browse the cache (relative paths; `read_workspace_file(path=…)` for any layout) |
| `write_proposal` | Write proposed ABAP to `proposals/` → IDE opens a diff tab within 2 s; `path=` targets any file (free-form / nested) |

Return values are prefixed with `[SOURCE: workspace/profile]` or `[SOURCE: SAP/profile]`.
