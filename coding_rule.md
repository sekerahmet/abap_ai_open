# ABAP AI IDE — Coding Rules

## 1. Layer Separation (hard rule)

| Layer | Import allowed | Import forbidden |
|---|---|---|
| `ui/` | `core.*`, `utils.*`, `customtkinter`, `tkinter` | `pyrfc`, direct AI SDK calls |
| `core/` | `pyrfc`, AI SDKs, `utils.*` | `tkinter`, `customtkinter` |
| `utils/` | `re`, stdlib only | `tkinter`, `pyrfc`, `core.*` |

Breaking layer separation causes circular imports and makes unit testing impossible.

---

## 2. Threading — Non-Negotiable

Every SAP RFC call **must** run in a daemon thread.
Every widget update **must** go through `self.after(0, fn, args)`.

```python
# CORRECT
threading.Thread(target=self._do_work, daemon=True).start()

def _do_work(self):
    result = self.controller.fetch_program(conn, name)
    self.after(0, self.write_log, "done")   # safe GUI update

# WRONG — will freeze UI or crash
result = self.controller.fetch_program(conn, name)  # blocking call on main thread
self.write_log("done")                               # widget call from background thread
```

---

## 3. Return Convention for SAP Methods

All `core/` methods that call SAP return a 2-tuple:
```python
return result, attrs    # success
return None, error_str  # failure
```
Callers check `if not code:` before using the result.
Never raise exceptions across the thread boundary — catch and return `(None, str(e))`.

---

## 4. Tab Deduplication

Never open a new tab if one with the same name is already open.
Always start `open_*_tab` methods with:
```python
if name in self.editor.tabs_dict:
    self.editor.set_active(name)
    return
```

---

## 5. SAP Connection Parameters

- pyrfc expects `saprouter` (not `router`) — always map the key before passing to `pyrfc.Connection`
- `get_current_conn()` in `App` handles this mapping — use it, don't build conn dicts manually
- `SAPConnectionManager` is a singleton; params-change detection is automatic

---

## 6. RFC_READ_TABLE OPTIONS Format

Each OPTIONS row must be `{"TEXT": "..."}` with content **≤ 72 characters**.
One condition per row. `OR` goes at the **start** of subsequent rows:

```python
options = []
for i, name in enumerate(names):
    line = f"OBJ_NAME = '{name}'"
    if i > 0:
        line = "OR " + line
    options.append({"TEXT": line})
```

`WA` field is **fixed-width** — always slice, never split:
```python
obj_name = wa[:40].strip()
obj_type = wa[40:].strip()
```

---

## 7. DDIC Display vs Code Display

| Object type | Display method |
|---|---|
| Table, Structure | `open_ddic_tab(name, attrs)` — ttk.Treeview grid |
| Program, Include, Class, FM, Proposal | `open_code_tab(name, code)` — CTkTextbox + highlighter |

Never display table field lists as code text.

---

## 8. ABAP Parser — Adding Patterns Safely

Before adding a new DICT regex pattern:
1. Confirm it won't match ABAP keywords (check `_ABAP_KEYWORDS` set)
2. Confirm it captures a group `(\w+)` for the object name (not the full match)
3. Test against screen field names like `SO_MATNR-LOW`, `SSCRFIELDS`, `TEXT-001`

The TADIR filter in `populate_tree` is a second safety net but not a substitute for clean patterns.

---

## 9. App Context Wiring (Widget References)

Panels store widget references on the `app` object using `setattr(self.app, "sap_"+attr, entry)`.
This is intentional — it avoids circular imports while letting `App`'s threaded callbacks
reach panel widgets. Follow this pattern when adding new fields.

---

## 10. Naming Conventions

| Thing | Convention | Example |
|---|---|---|
| Background worker method | `run_*` | `run_fetch`, `run_proactive_check` |
| GUI update / tab opener | `open_*_tab`, `update_*`, `write_*` | `open_code_tab`, `write_log` |
| SAP reader methods | `fetch_*`, `check_*` | `fetch_table`, `check_objects_batch` |
| Panel setup methods | `_setup_*` | `_setup_ui`, `_setup_explorer` |
| Private helpers | leading underscore | `_merge`, `_worst` |

---

## 11. No Unused Code / Speculation

- Do not add error handling for scenarios that can't happen inside this codebase
- Do not add configuration flags for hypothetical future options
- Do not create helper functions for one-off operations
- Remove code before committing if it is commented out

---

## 12. PyInstaller Packaging Notes

- User data goes to `%APPDATA%\ABAP_AI\` — never relative to the exe or the working directory
- `console=True` during development (tracebacks visible), `False` for release
- After any code change: rebuild with `pyinstaller main.spec`
- `--clean` only wipes `build/` and `dist/` — `%APPDATA%` is untouched

---

## 13. Proposal Protocol (MCP server → IDE)

The MCP server writes proposals to `workspace/{profile}/{project}/proposals/*.abap`.
The IDE polls every 2000 ms via `_poll_proposals()` and opens a diff tab automatically.

Watched key format: `"profile/project/filename"` — tracked in `_watched_proposals` set.
Do not change the proposals subfolder name without updating both `workspace.py` and `_poll_proposals`.
