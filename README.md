# ABAP AI IDE

A Windows desktop IDE (Python + PySide6) for **reading** ABAP code from SAP systems, exploring the
objects a program depends on, caching custom (Z*/Y*) objects in a git-backed workspace, and
letting Claude (via MCP) analyse that code and propose changes as reviewable diffs.

> **Read-only by design.** The IDE and the MCP server only call read RFCs
> (`RPY_PROGRAM_READ`, `DDIF_FIELDINFO_GET`, `RFC_READ_TABLE`, …). Nothing is ever written
> back to SAP. AI suggestions land in a local `proposals/` folder and are shown as diffs.

## Features

- Fetch programs, includes, function modules, global classes (all sections + methods), table
  and structure definitions, and table data — workspace cache first, SAP on miss
- Object explorer: dictionary objects, classes, includes, forms and local references found in
  the source, verified against TADIR; click to jump, double-click to open
- Workspace explorer with live git status, Push/Pull to a private GitHub repo, right-click
  *Open / Show in Windows Explorer / Delete*
- Proposal watcher: when Claude writes a proposal through the MCP server, a diff tab opens
  automatically
- **Claude Code inside the IDE**: `✦ Claude` opens a session tab that runs the Claude Code CLI
  with your own subscription login (no API key). It sees the workspace files, the open code tab,
  and the SAP MCP tools; code suggestions come back as proposals / diff tabs
- **Local mode**: pick "Local (no SAP)" and use *Open file…* / *Paste code* to work on ABAP
  sources without any SAP system
- Dockable panels (Claude · SAP Objects · Workspace), tabs, dark theme; layout is remembered
- Single-file `.exe` build with PyInstaller; all user data lives in `%APPDATA%\ABAP_AI`

## Requirements

- Windows 10/11, Python 3.12
- [SAP NetWeaver RFC SDK](https://support.sap.com/en/product/connectors/nwrfcsdk.html)
  installed and on `PATH` (needed by `pyrfc`)
- Git for Windows (for workspace sync)
- Optional: Claude Desktop (MCP) and/or Claude Code CLI (`winget install Anthropic.ClaudeCode`,
  then run `claude` once to log in) for the in-IDE Claude tab

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env      # then fill in GITHUB_TOKEN / GITHUB_REPO
python main.py
```

Create a connection profile in the sidebar (app server, system number, client, user, password,
optional SAP router) and press **Save Profile**. Profiles are stored in
`%APPDATA%\ABAP_AI\systems.json` on your machine only.

## GitHub workspace sync

Push/Pull sync the local workspace (`%APPDATA%\ABAP_AI\workspace`) with a **private** repo of
your own. Put the repo URL and a personal access token in `.env`:

```
GITHUB_REPO=https://github.com/<you>/<private-workspace-repo>
GITHUB_TOKEN=github_pat_...   # fine-grained: Contents = Read and write, or classic 'repo'
```

The token is passed to git as an HTTP header per command and never stored in `.git/config`.

## MCP server (Claude Desktop / Claude Code)

```bash
python mcp_server.py
```

Register it in Claude Desktop's config, or for Claude Code:

```bash
claude mcp add abap-ai -- python C:\path\to\mcp_server.py
```

Tools: `list_sap_profiles`, `switch_profile`, `fetch_program`, `fetch_function_module`,
`fetch_class`, `fetch_table_fields`, `fetch_table_data`, `check_objects_in_tadir`,
`list_workspace_files`, `read_workspace_file`, `write_proposal`.

## Build

```bash
pyinstaller main.spec      # → dist/main.exe ; copy .env next to it
```

`main.spec` builds without a console; crashes are written to `%APPDATA%\ABAP_AI\crash.log`.

## Project layout

```
ui/       PySide6 GUI (main_window.py, panels/, widgets/, dialogs.py)
core/     SAP readers and the controller facade (pyrfc lives only here)
utils/    parser, highlighter, workspace, github_sync, env_loader
```

See [CLAUDE.md](CLAUDE.md) for the architecture reference and [coding_rule.md](coding_rule.md)
for the coding rules.

## Security notes

- Never commit `.env` or `systems.json` — both are git-ignored.
- Use a dedicated read-only SAP user for the IDE if your security policy allows it.
- The workspace repo contains your custom ABAP source; keep it private.
