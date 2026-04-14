# ui/ — Presentation Layer

## Layout (3-column grid in App window)
```
col 0 (minsize=320)   col 1 (weight=1)      col 2 (minsize=380)
┌─────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│  SidebarPanel   │   │   EditorPanel    │   │   ChatPanel     │
│                 │   │                  │   │                 │
│ · Connection    │   │ · Fetch bar      │   │ · Chat log      │
│   profiles      │   │ · Tab bar        │   │ · Input + Send  │
│ · GitHub        │   │ · Content area   │   │                 │
│   Push / Pull   │   │   (tabs)         │   │                 │
│ · SAP Objects   │   │                  │   │                 │
│   Explorer      │   │                  │   │                 │
│ · Workspace     │   │                  │   │                 │
│   Explorer      │   │                  │   │                 │
└─────────────────┘   └──────────────────┘   └─────────────────┘
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
app.send_btn        set by ChatPanel
app.chat_input      set by ChatPanel
app.chat_log        set by ChatPanel
app.sap_ashost      set by SidebarPanel
app.sap_sysnr       set by SidebarPanel
app.sap_client      set by SidebarPanel
app.sap_user        set by SidebarPanel
app.sap_passwd      set by SidebarPanel
app.sap_router      set by SidebarPanel
app.tree            set by SidebarPanel  (SAP Object Explorer Treeview)
app.tree_roots      set by SidebarPanel  (dict: category → treeview iid)
app.ws_tree         set by SidebarPanel  (Workspace Explorer Treeview)
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

### Key flow: AI chat
```
send_chat()
  → threading.Thread(run_ai)
    → controller.send_chat(prompt)
    → after(0, chat.on_chat_response, res)
      → detects [[FETCH:...]] tags → triggers run_sub_fetch threads (workspace-first)
      → detects [[PROPOSAL:...]] → calls open_suggestion_tab
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
| `refresh_workspace_tree()` | Reloads `ws_tree` Treeview from disk. Called on startup, after every workspace save, and by proposal poller. |
| `on_workspace_select(event)` | Double-click handler on `ws_tree`. Opens file from workspace (no RFC). |

`_WS_FOLDER_META` maps subfolder names to `(display_label, ftype_string)`:
- `"programs"`  → `("📝  Programs",  "Program")`
- `"tables"`    → `("📊  Tables",    "Table")`
- `"proposals"` → `("📬  Proposals", "Program")`

Tree node values stored as `(profile, folder, filename, project)`.
`.json` files in `tables/` open as DDIC tabs; `.abap` in `proposals/` open as Proposal tabs.

### GitHub sync
```
github_push() → thread → github_sync.push_workspace(profile)
github_pull() → thread → github_sync.pull_workspace(profile)
```
Buttons are always visible in sidebar row 1 (not inside the scrollable settings frame).

### Connection params
`get_current_conn()` reads the sidebar Entry widgets and maps `router` → `saprouter`
(pyrfc expects the key `saprouter`, not `router`).

---

## panels/sidebar.py — SidebarPanel

Grid rows:
- **Row 0** (`weight=0`): scrollable connection settings (profile dropdown + 6 Entry fields + Save button)
- **Row 1** (`weight=0`): GitHub Push / Pull buttons — always visible, never inside scroll frame
- **Row 2** (`weight=1`): `CTkTabview` with two tabs:
  - **"SAP Objects"**: `ttk.Treeview` with root nodes DICT / CLASS / INCLUDES / FIELDS
  - **"Workspace"**: `ttk.Treeview` showing `profile → program → subfolder → filename` + Refresh button

`app.tree` and `app.tree_roots` point to the SAP Objects tree.
`app.ws_tree` points to the Workspace tree.

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

## panels/chat_panel.py — ChatPanel

`on_chat_response(text)` handles two AI protocol tags:
- `[[FETCH:Category:Name]]` → fires `run_sub_fetch` threads (autonomous fetch, workspace-first)
- `[[PROPOSAL:FileName]]...[[END_PROPOSAL]]` → calls `open_suggestion_tab`

`send_btn` starts disabled, re-enabled in `on_chat_response` (or `reset_buttons`).
Chat input bound to `<Return>` key.
