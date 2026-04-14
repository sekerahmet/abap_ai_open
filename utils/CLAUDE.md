# utils/ — Stateless Helpers

## highlighter.py — ABAPHighlighter

Applies syntax color tags to a `CTkTextbox` widget (which wraps a tk.Text internally).

```python
ABAPHighlighter.apply(textbox)   # call once after insert, before configure(state="disabled")
```

Colors (dark theme):
- `keyword` → `#569cd6` (blue)
- `string` → `#ce9178` (orange)
- `comment` → `#6a9955` (green)

Patterns:
- Keywords: explicit whitelist regex (REPORT, DATA, SELECT, LOOP, METHOD, etc.)
- Strings: `'...'` and `` `...` ``
- Comments: `*` at line start, `"` inline

**Note:** The textbox must be in normal (editable) state before calling `apply()`.
The caller is responsible for setting `state="disabled"` after.

---

## parser.py — ABAPParser

`ABAPParser.get_objects(code)` → `dict[category, list[{"name": str, "line": int}]]`

### Categories returned
| Category | What it finds |
|---|---|
| `DICT` | Tables/structures referenced (FROM, TYPE x-field, LIKE x-field, TABLE OF, TABLES:, namespace types Z*/Y*/XX_*) |
| `CLASS` | Local class definitions (`CLASS name DEFINITION`) |
| `FIELDS` | Local DATA declarations |
| `EVENTS` | INITIALIZATION, START-OF-SELECTION, END-OF-SELECTION, AT SELECTION-SCREEN |
| `PBO` | MODULE name OUTPUT |
| `PAI` | MODULE name INPUT |
| `INCLUDES` | INCLUDE statements |

### DICT filter — _ABAP_KEYWORDS
A large set of ABAP primitive types and statement keywords that look like type names but are not
dictionary objects (e.g. STRING, CHAR, TABLE, END, MESSAGE, VALUE, SELECTION, etc.).
**When adding new DICT patterns, always verify against this set.**

### Deep discovery flow (owned by main_app.py, not parser)
`run_proactive_check` fetches each include found by the parser, runs `get_objects` on it,
and merges results. Final dict/class/include names are checked against TADIR via
`check_objects_batch`. Only TADIR-verified objects appear in the SAP Object Explorer.
After the TADIR check, any Z*/Y* tables (`TABL`/`VIEW`) not yet cached are automatically
fetched via `DDIF_FIELDINFO_GET` and saved to `{main_program}/tables/{ZTABLE}.json`.
Pass `force=True` to bypass the cache check and re-fetch all tables from SAP.

---

## workspace.py — Workspace Bridge

Filesystem bridge between the IDE and Claude Desktop MCP server.
All paths live under `%APPDATA%\ABAP_AI\workspace\` — never under `dist/`.

### Folder layout
```
%APPDATA%\ABAP_AI\workspace\
└── {profile}/                  ← SAP system profile name (e.g. HCS_DEV)
    └── {PROG_NAME}/            ← Z*/Y* object name (e.g. ZFI_CO_001)
        ├── programs/           ← source files: .abap (programs, includes, classes, FMs)
        ├── tables/             ← table/structure field definitions: .json
        └── proposals/          ← AI proposals: .abap
```

Only Z*/Y* objects are saved. Standard SAP objects are fetched for display but never written to disk.
Includes and discovered Z*/Y* tables are saved under the **main program's** project folder
(via `project=` parameter). Directly fetched tables get their own project folder.

### Public API

| Function | Signature | Purpose |
|---|---|---|
| `save_code` | `(profile, ftype, name, code, project=None)` | Save ABAP source; `""` if standard object |
| `save_table` | `(profile, name, fields, project=None)` | Save field list as JSON to `tables/` |
| `read_code` | `(profile, ftype, name, project=None)` | Load ABAP source; `""` if not found |
| `read_table_fields` | `(profile, name, project=None)` | Load JSON field list from `tables/`; `[]` if not found |
| `read_file` | `(profile, folder, filename, project=None)` | Raw read by subfolder + filename |
| `write_proposal` | `(profile, name, code, project=None)` | Write to `proposals/`; IDE polls and opens diff tab |
| `list_files` | `(profile)` | `{PROG_NAME: {"programs": [...], "tables": [...], "proposals": [...]}}` |
| `list_profiles` | `()` | Profile names with existing workspace folders |
| `scan_proposals` | `(profile)` | `[(project, filename), ...]` from all `proposals/` dirs |
| `get_path` | `(profile, ftype, name, project=None)` | Resolve full filesystem path |

### Subfolder mapping
Source types (PROG, CLAS, FUNC, and all non-table/non-proposal types) → `programs/`
Table/structure types (TABL, VIEW, Table, Structure) → `tables/`
Proposals (PROP) → `proposals/`

### project= parameter
- When `project=None`: uses the object name itself as the project folder
- When `project="ZMAIN"`: saves under `ZMAIN/{subfolder}/` (used for objects belonging to a main program)
- `read_file` without project: searches all project folders (stem-first, then iterate)

### Workspace-first pattern
Both the IDE (`run_fetch`, `run_sub_fetch`) and the MCP server fetch tools follow this pattern:
1. `read_code` / `read_table_fields` → return cached version if found
2. On cache miss → RFC call → save result → return fresh version
3. `force=True` / `force_fetch=True` parameter bypasses cache and overwrites it

---

## github_sync.py — GitHub Sync

Push/pull workspace files to a GitHub repository via subprocess git commands.
No extra Python dependencies beyond `subprocess` (already stdlib).

### .env keys required
```
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
GITHUB_REPO=https://github.com/username/abap-workspace
```

### Public API
| Function | Returns | Notes |
|---|---|---|
| `push_workspace(profile, commit_msg="")` | `(True, info)` or `(False, error)` | Excludes `*/proposals/` (proposals are transient) |
| `pull_workspace(profile)` | `(True, info)` or `(False, error)` | git clone on first run, git pull thereafter |

### Token injection
`https://github.com/...` → `https://{token}@github.com/...` (never stored in git config)

### Push staging strategy
`git add .` then `git rm --cached */proposals/` — stages `programs/` and `tables/` files, unstages proposals.
