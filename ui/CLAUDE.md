# ui/ — Presentation Layer

## Layout (3-column grid in App window)
```
col 0 (minsize=270)   col 1 (weight=1)      col 2 (minsize=420)
┌─────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│  SidebarPanel   │   │   EditorPanel    │   │  ExplorerPanel  │
│                 │   │                  │   │                 │
│ · Connection    │   │ · Fetch bar      │   │ · SAP Objects   │
│   profiles      │   │ · Tab bar        │   │   tab (tree)    │
│   (scroll)      │   │ · Content area   │   │ · Workspace     │
│                 │   │   (tabs)         │   │   tab (tree)    │
└─────────────────┘   └──────────────────┘   │   + Push/Pull   │
                                              │   + 🌿 branch   │
                                              └─────────────────┘
```

---

## main_app.py — App(ctk.CTk)

Central glue class. Owns:
- `self.controller` — `AnalysisController` instance
- `self.systems_data` — loaded from `%APPDATA%\ABAP_AI\systems.json`
- `self.tabs_dict` — `{tab_name: {"textbox": widget, "code": str, "prog": str, "ftype": str, "source_profile": str}}`
- `self.active_tab_name` — currently visible tab name (synced from EditorPanel)
- `self._watched_proposals` — set of `"profile/project/filename"` keys already opened as diff tabs
- `self.current_main_program` — set during `run_fetch`; used by `run_sub_fetch` to save includes under the right project

### Widget references set by panels (on `self.app`)
```
app.fetch_btn       set by EditorPanel
app.fetch_type_var  set by EditorPanel (OptionMenu variable)
app.prog_name       set by EditorPanel (name Entry)
app.sap_ashost      set by SidebarPanel
app.sap_sysnr       set by SidebarPanel
app.sap_client      set by SidebarPanel
app.sap_user        set by SidebarPanel
app.sap_passwd      set by SidebarPanel
app.sap_router      set by SidebarPanel
app.tree            set by ExplorerPanel  (SAP Object Explorer Treeview)
app.tree_roots      set by ExplorerPanel  (dict: category → treeview iid)
app.ws_tree         set by ExplorerPanel  (Workspace Explorer Treeview)
```

### Tab opening methods
| Method | Signature | When to use |
|---|---|---|
| `open_code_tab` | `(name, code, _attrs=None, prog=None, ftype=None, source_profile=None)` | Programs, includes, classes, FMs, proposals |
| `open_ddic_tab` | `(name, attrs, ftype="Table")` | Tables and Structures (renders ttk.Treeview grid) |
| `open_diff_tab` | `(name, original_code, proposed_code)` | Proposals arriving in prop/ or SAP-vs-workspace diffs |

All three check `self.editor.tabs_dict` first — if already open, call `set_active(name)` and return.
`open_code_tab` registers into `self.tabs_dict` (stores `prog`, `ftype`, `source_profile` for Re-fetch and Upload).

### Re-fetch from SAP
- `open_code_tab` renders a "Re-fetch from SAP" button in the tab toolbar when `prog` and `ftype` are provided.
- `open_ddic_tab` renders the same button in the header row.
- Both call `refetch_object(tab_name, prog, ftype)` which closes the tab and starts `run_fetch(..., force=True)`.
- For `ftype="Program"`: also passes `force_sub=True` → `run_proactive_check(force=True)` →
  all Z*/Y* tables are re-fetched from SAP (cache skipped). Full program + includes + tables refresh in one click.

### Key flow: fetch (workspace-first)
```
fetch_program_flow()              ← triggered by Fetch button
  → threading.Thread(run_fetch, force=False)
      ├── workspace cache hit?
      │     YES → open_code_tab / open_ddic_tab (no RFC)
      │     NO  → controller.fetch_*() via RFC
      │           → save to workspace ({prog}/programs/ or tables/)
      │           → open tab
      │           → refresh_workspace_tree()
      └── if Program: threading.Thread(run_proactive_check, force=force_sub)
               → fetch each include from SAP, save under {PROG}/programs/
               → check_objects_batch against TADIR
               → populate_tree()
               → for each Z*/Y* TABL/VIEW in registry (not yet cached, or force=True):
                   fetch DDIF_FIELDINFO_GET → save to {PROG}/tables/{ZTABLE}.json
               → refresh_workspace_tree() if any table saved
```

### Key flow: SAP upload
```
open_transport_dialog(prog, ftype, get_code_fn, source_profile)
  → system mismatch guard (source_profile vs active profile)
  → list_transports() → radio button dialog
  → _run_upload thread:
      ├── check_syntax() → SYNTAX_CHECK RFC
      │     error   → _ask_syntax_error dialog (main thread) → cancel or _do_write
      │     warning → log only, continue
      │     unavail → skip
      └── _do_write thread:
            ├── assign_to_transport() → RS_CORR_INSERT
            │     fail → _ask_skip_tr_assign dialog
            └── write_program() → candidates 1-5
```

### Proposal file watcher
`_poll_proposals()` runs every 2000 ms via `self.after(2000, _poll_proposals)`.
For each new file in `workspace/{profile}/{project}/proposals/`:
- If original code exists in `self.tabs_dict` → `open_diff_tab`
- Otherwise → `open_code_tab` as proposal tab

Watched key format: `"profile/project/filename"` — stored in `_watched_proposals` set.

### Workspace Explorer methods
| Method | Description |
|---|---|
| `refresh_workspace_tree()` | Reloads `ws_tree` Treeview from disk + git status. Called on startup, after every workspace save, proposal poll, and after Push/Pull. |
| `on_workspace_select(event)` | Double-click handler on `ws_tree`. Opens file from workspace (no RFC). |

`_WS_FOLDER_META` maps subfolder names to `(display_label, ftype_string)`:
- `"programs"`  → `("📝  Programs",  "Program")`
- `"tables"`    → `("📊  Tables",    "Table")`
- `"proposals"` → `("📬  Proposals", "Program")`

Tree node values stored as 5-tuple: `(kind, profile, folder, filename, project)`.
- `kind` — display string from `_WS_FOLDER_META` (visible "Kind" column)
- `_p`, `_fo`, `_fn`, `_proj` — hidden metadata columns read by `on_workspace_select`

`.json` files in `tables/` open as DDIC tabs; `.abap` in `proposals/` open as Proposal tabs.

### Git status in Workspace tree
`refresh_workspace_tree()` calls `github_sync.get_git_status()` to annotate nodes with color tags and icon prefixes:

| Status | Tag | Color | Prefix |
|---|---|---|---|
| Modified | `ws_modified` | `#e5c07b` amber | `● ` |
| Untracked/New | `ws_new` | `#98c379` green | `+ ` |
| Deleted | `ws_deleted` | `#e06c75` red | `✗ ` |

Status is aggregated upward: file → folder-node → project-node → profile-node (priority: M > ? > D).

### GitHub sync
```
github_push() → thread → github_sync.push_workspace(profile)
                → on success: refresh_workspace_tree() + explorer_panel.update_branch_label()
github_pull() → thread → github_sync.pull_workspace(profile)
                → on success: explorer_panel.update_branch_label()
```
Push/Pull buttons live in the ExplorerPanel Workspace tab toolbar (not the sidebar).

### Connection params
`get_current_conn()` reads the sidebar Entry widgets and maps `router` → `saprouter`
(pyrfc expects the key `saprouter`, not `router`).

---

## panels/sidebar.py — SidebarPanel

Single grid row:
- **Row 0** (`weight=0`): scrollable connection settings (profile dropdown + New/Del buttons + 6 Entry fields + Save Profile button)

`app.sap_*` entry references are set here via `setattr(self.app, "sap_"+attr, entry)`.
No explorer trees, no GitHub buttons — those are in `ExplorerPanel`.

---

## panels/editor.py — EditorPanel

Custom tab system (not CTkTabview — built manually for closable tabs).

| Method | Description |
|---|---|
| `add_tab(name, is_closable=True)` | Creates header button + content frame, returns content frame |
| `set_active(name)` | Shows tab content, highlights header, syncs `app.active_tab_name` |
| `close_tab(name)` | Destroys widgets, cleans `tabs_dict` and `app.tabs_dict` |

`tabs_dict` in EditorPanel: `{name: {"header": frame, "content": frame}}`
`tabs_dict` in App: `{name: {"textbox": widget, "code": str, "prog": str, "ftype": str, "source_profile": str}}`

**System Logs tab** is created at startup with `is_closable=False` and always stays.

---

## panels/explorer_panel.py — ExplorerPanel

Two-tab panel occupying col 2 (right column).

### SAP Objects tab
`ttk.Treeview` with Name + Type columns.
Root nodes created at startup and assigned to `app.tree_roots`:
- `app.tree_roots["DICT"]` — 📦 Dictionary
- `app.tree_roots["CLASS"]` — 💠 Classes
- `app.tree_roots["INCLUDES"]` — 📎 Includes
- `app.tree_roots["FIELDS"]` — 🔗 Local Refs

`app.tree` = the Treeview widget. Bind `<<TreeviewSelect>>` fires `app.on_tree_select`.

### Workspace tab
Toolbar (row 0): `[⬆ Push]` `[⬇ Pull]` `[⟳]` spacer `🌿 <branch>`
Tree (row 1): `app.ws_tree` — columns `("kind","_p","_fo","_fn","_proj")`, displaycolumns `("kind",)`.

`update_branch_label()` — reads branch name from `github_sync.get_branch_name()` and updates the toolbar label. Called at startup (`after(600, ...)`) and after Push/Pull completes.

Double-click on ws_tree fires `app.on_workspace_select`.
