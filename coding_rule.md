# ABAP AI IDE — Coding Rules

## 0. Read-only SAP (hard rule)

The RFC connection is **one-way**. Only read-only function modules are called
(`RPY_*_READ*`, `DDIF_FIELDINFO_GET`, `RFC_READ_TABLE`). Never add a write, transport,
activation or "install and run" RFC. Code changes produced by AI go to the workspace
`proposals/` folder, never to SAP.

---

## 1. Layer Separation

| Layer | Import allowed | Import forbidden |
|---|---|---|
| `ui/` | `core.*`, `utils.*`, `customtkinter`, `tkinter` | `pyrfc` |
| `core/` | `pyrfc`, `utils.*` | `tkinter`, `customtkinter` |
| `utils/` | stdlib only | `tkinter`, `pyrfc`, `core.*` |

---

## 2. Threading — Non-Negotiable

Every RFC / git / heavy disk call runs in a daemon thread; every widget update goes through
`self.after(0, fn, args)`. Read Tk variables (active profile, entry fields) on the main thread
**before** starting the thread and pass the values as arguments.

```python
# CORRECT
threading.Thread(target=self.run_fetch,
                 args=(self.get_current_conn(), prog, ftype, self.active_profile()),
                 daemon=True).start()

def run_fetch(self, conn, prog, ftype, profile, ...):
    code, attrs = self.controller.fetch_program(conn, prog)
    self.after(0, self.open_code_tab, ...)     # safe GUI update
```

---

## 3. Return Convention for SAP Methods

All `core/` methods return a 2-tuple: `(result, attrs)` on success, `(None, error_str)` on
failure. Batch methods return `{NAME: (result, attrs_or_err)}`. Never raise across the thread
boundary — catch and return `(None, str(e))`.

---

## 4. Connections

`SAPConnectionManager` is a plain class (no singleton). `execute()` = open, call, close.
Use `with mgr.session() as call:` when doing several calls in a row. The controller is
stateless: always pass the conn dict, never cache readers.
pyrfc expects `saprouter` (not `router`) — `App.get_current_conn()` does the mapping.

---

## 5. Tab Names

Build every tab title with `_tab_name(ftype, name)` (`main_app.py`). Names are upper-cased;
tables and structures both use `Table:`. Proposal code tabs are `Proposal: X`, diffs `Diff: X`.
Every `open_*_tab` starts with the duplicate guard:
```python
if name in self.editor.tabs_dict:
    self.editor.set_active(name)
    return
```

---

## 6. Workspace Writes

Call `workspace.save_code / save_table / write_proposal` with `project=None` unless you are
caching a dependency under a known main program. The module keeps a file where it already
exists; never compute paths by hand in `ui/`.

---

## 7. RFC_READ_TABLE OPTIONS

Rows ≤ 72 chars, one condition per row, `OR ` / `AND ` at the **start** of later rows.
Use `ddic_reader.split_where()` for user-supplied WHERE clauses. TADIR checks are chunked
(40 names per call). `WA` for TADIR is fixed-width — slice by position.

---

## 8. Parser Patterns

Before adding a DICT/CLASS regex:
1. It must capture the object name in group 1.
2. Check it against `_ABAP_KEYWORDS` / `_CLASS_EXCLUDE`.
3. Test with `python -c "from utils.parser import ABAPParser; ..."` on a snippet containing
   screen fields (`SO_MATNR-LOW`, `SSCRFIELDS`, `TEXT-001`), `INCLUDE STRUCTURE`, comments.
Comments are stripped before matching; the TADIR filter in `populate_tree` is the second net.

---

## 9. Proposal Watcher

`_poll_proposals` must stay cheap (snapshot + scan, no git, no RFC) and must always
re-schedule itself in `finally`. New proposals are detected by key **and mtime**.

---

## 10. Naming

| Thing | Convention | Example |
|---|---|---|
| Background worker | `run_*` / `_*_worker` | `run_fetch`, `_ws_refresh_worker` |
| GUI update / tab opener | `open_*_tab`, `write_*`, `_ws_apply` | `open_code_tab` |
| SAP readers | `fetch_*`, `check_*` | `fetch_table`, `check_objects_batch` |
| Panel setup | `_setup_*` | `_setup_workspace_tree` |

---

## 11. No Speculative Code

No handlers for scenarios that cannot happen here, no config flags for hypothetical options,
no commented-out code in commits.

---

## 12. Packaging

User data → `%APPDATA%\ABAP_AI\`. `main.spec`: `console=False`, `debug=False`.
Uncaught exceptions land in `%APPDATA%\ABAP_AI\crash.log`. Rebuild with `pyinstaller main.spec`
and copy `.env` next to the exe. `systems.json` is git-ignored — keep it that way.
