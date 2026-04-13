# core/ — Business Logic Layer

## Overview
Pure Python logic. No tkinter imports anywhere in this package.
All public methods return `(result, error_or_attrs)` tuples so callers can check success without exceptions.

---

## controller.py — AnalysisController
Single facade used by `ui/main_app.py`. Lazily initializes SAP and AI on first use.

```
fetch_program(conn_params, name)         → (code, attrs)   uses ProgramReader
fetch_ddic_object(conn_params, name)     → (code, attrs)   uses DDICReader
fetch_class_source(conn_params, name)    → (code, attrs)   uses ProgramReader
fetch_function_module(conn_params, name) → (code, attrs)   uses ProgramReader
check_objects_batch(conn_params, names)  → {OBJ_NAME: OBJECT_TYPE}
list_transports(conn_params, user="")    → [{"TRKORR", "AS4USER", "AS4TEXT"}, ...]
check_syntax(conn_params, prog, source)  → (True, warnings) | (False, errors) | (None, "")
upload_program(conn_params, prog, source, trkorr, skip_tr_assign=False)
                                         → (True, "") | (False, error_msg)
initialize_ai(gemini_key)               → GeminiClient instance
send_chat(message)                      → str response
run_analysis(code, attrs, mode)         → str response
```

**Important:** `initialize_sap(conn_params)` is called automatically on first fetch if not yet done.
When conn_params change (profile switch), `SAPConnectionManager.__new__` detects it and resets the connection.

`upload_program` returns `False, "TR_ASSIGN_FAILED:..."` as a special marker when TR assignment fails —
the UI catches this prefix and asks the user whether to write without TR assignment.

---

## sap/connection.py — SAPConnectionManager (Singleton)

- One instance per process (`__new__` pattern with `_lock`)
- Params change detection: if new `conn_params != stored params`, resets `conn = None`
- `connect()` always creates a **fresh** pyrfc.Connection — old conn is closed first
- **No `ping()`** — ping can block indefinitely on dead sessions

```python
mgr = SAPConnectionManager(conn_params)
result = mgr.execute("RFC_FUNCTION", PARAM=value)
```

---

## sap/program_reader.py — ProgramReader

| Method | RFC used | Notes |
|---|---|---|
| `fetch_code(name)` | `RPY_PROGRAM_READ` | works for programs and includes |
| `fetch_function_module(name)` | `RPY_FUNCTIONMODULE_READ` | returns `SOURCE_EXTENDED` or `SOURCE` |
| `fetch_class_source(name)` | `RPY_PROGRAM_READ` x3 | fetches CP + PU + PRI includes, joins with `\n\n` |

All return `(code_str, attrs_dict)` or `(None, error_str)`.

---

## sap/program_writer.py — ProgramWriter

### list_open_transports(user="") → list
Queries E070 for open workbench TRs (TRSTATUS='D', TRFUNCTION='K'), then E07T for descriptions.
E07T failure is silently ignored (descriptions are optional).

### assign_to_transport(trkorr, prog_name, obj_type="PROG") → (bool, str)
Calls RS_CORR_INSERT. OBJECT_CLASS mapping:
- `PROG` / `FUGR` → `"ABAP"`
- `CLAS` → `"CLAS"`

DIALOG=" " and GLOBAL_LOCK=" " prevent interactive popups.

### check_syntax(prog_name, source_code) → (bool|None, str)
Calls `SYNTAX_CHECK` RFC.
- `(True, "")` — clean
- `(True, warnings)` — warnings only
- `(False, errors)` — syntax errors with line numbers
- `(None, "")` — RFC unavailable; caller should skip check and proceed

### write_program(prog_name, source_code) → (bool, str)
Tries 5 candidates in order, returns on first success:
1. `RPY_PROGRAM_WRITE` — PROG_INF + SOURCE_EXTENDED
2. `RPY_PROGRAM_INSERT_MASTER` — PROG_INF + SOURCE_EXTENDED
3. `RS_PROGRAM_WRITE` — PROGRAMM + QUELLCODE (older systems)
4. `RFC_ABAP_INSTALL_AND_RUN` — PROGRAMNAME + MODE='F' + PROGRAM table; checks ERRORMESSAGE export
5. `Z_ABAP_AI_WRITE_PROG` — custom Z FM; IV_PROG + IT_SOURCE (LIKE ZABAP_AI_SRCLINE)

All candidates use `lines = [{"LINE": line} for line in source_code.splitlines()]`.

---

## sap/ddic_reader.py — DDICReader

| Method | RFC used | Notes |
|---|---|---|
| `fetch_table(name)` | `DDIF_FIELDINFO_GET` | returns ALL fields (no slice limit) |
| `check_objects_batch(names)` | `RFC_READ_TABLE` on TADIR | deduplicates, one OR condition per OPTIONS row |

### check_objects_batch WA parsing
```python
wa = row.get("WA", "")
obj_name = wa[:40].strip()   # fixed width — do NOT split()
obj_type = wa[40:].strip()
```

---

## ai/gemini_client.py — GeminiClient

- Model: `gemini-2.0-flash-preview`
- Stateful: `chat_session` created once via `_ensure_chat()`, reused for the lifetime of the app
- System instruction injected at session creation (FETCH/PROPOSAL protocol + ABAP expert role)
- `send_message(text)` → plain string (handles exceptions, returns error string)
- `run_analysis(code, attrs, mode)` → builds prompt from `Config.get_prompt(mode, ...)` then calls `send_message`

## ai/base.py — AbstractAIClient
ABC with `__init__`, `send_message`, `run_analysis`. Implement this to add a new AI backend (e.g. Claude API).

## config.py — Config
- Prompt templates keyed by mode: `"review"`, `"performance"`, `"security"`, `"documentation"`
- `Config.get_prompt(mode, code, attrs)` prepends attrs block when attrs provided
- SAP_CONNECTION dict reads from `.env` (used as fallback; UI overrides via connection profiles)
- Uses `_find_dotenv()` helper — safe for both dev and PyInstaller frozen exe

---

## Note: MCP server does NOT use core/ directly
`mcp_server.py` instantiates `ProgramReader` and `DDICReader` directly (not via `AnalysisController`)
to keep the MCP server self-contained and independent of the GUI layer.
`SAPConnectionManager` singleton is shared — param-change detection still applies.
