# manage-coding-agent-session (`mcas`)

Browse, inspect, copy, and automatically sync coding-agent conversation sessions across **Antigravity CLI (`agy`)**, **Claude Code CLI (`claude`)**, and **Codex CLI (`codex`)** — via CLI, Web GUI, and Windows system tray.

> **Unofficial.** Not affiliated with Google, Anthropic, or OpenAI. Zero third-party runtime dependencies (Python 3.10+ standard library + built-in Windows PowerShell 5.1 for the Windows tray).

## Supported Providers

| Provider | Status | Local Storage |
|---|---|---|
| **Antigravity CLI (`agy`)** | Supported | `%USERPROFILE%\.gemini\antigravity-cli\conversations\*.db` + `conversation_summaries.db` |
| **Claude Code CLI (`claude`)** | Supported | `%USERPROFILE%\.claude\projects\<slug>\*.jsonl` |
| **Codex CLI (`codex`)** | Supported | `%USERPROFILE%\.codex\sessions\YYYY\MM\DD\rollout-*.jsonl`, `archived_sessions\*.jsonl`, `state_5.sqlite` |
| **OpenCode (`opencode`)** | Planned (`Coming Soon`) | Architecture ready via `AgentProvider` registry |

## Requirements

- Windows 10 / 11 with PowerShell 5.1+ (built in) for the system tray & Windows shortcuts
- [`uv`](https://docs.astral.sh/uv/) (auto-installed by `install.ps1` if missing, or `winget install astral-sh.uv`)
- Python 3.10+ (managed automatically by `uv` if not already installed)

*(On macOS/Linux, install with `uv tool install git+https://github.com/thanhnguyen96/manage-coding-agent-session` or run from a checkout with `uv run mcas ...`.)*

## Install

```powershell
irm https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session/HEAD/install.ps1 | iex
```

Or install directly with `uv`:

```powershell
uv tool install git+https://github.com/thanhnguyen96/manage-coding-agent-session
```

Pin a specific git tag, branch, or commit with `$env:MCAS_REF = 'v0.3.0'` before running `install.ps1`.

Prefer to inspect `install.ps1` first?

```powershell
irm https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session/HEAD/install.ps1 -OutFile install.ps1
notepad install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Open a new terminal afterwards so `mcas` is on your `PATH`.

<details>
<summary>Environment variables (installation & overrides)</summary>

| Variable | Default | Purpose |
|---|---|---|
| `MCAS_ROOT` | `%LOCALAPPDATA%\mcas` | Data & runtime state directory (`ui.json`, `sync_state.json`) |
| `MCAS_PORT` | `47831` | Default Web UI port |
| `MCAS_REF` | `HEAD` | Git ref used by `install.ps1` and `mcas update` |
| `MCAS_SOURCE` | *(unset)* | Local folder path to install/update from instead of GitHub |
| `MCAS_AGY_DIR` | `~/.gemini/antigravity-cli` | Override Antigravity CLI data directory |
| `MCAS_AGY_IDE_DIR` | `~/.gemini/antigravity` | Override Antigravity IDE data directory |
| `MCAS_CLAUDE_DIR` | `~/.claude` | Override Claude Code data directory |
| `MCAS_CODEX_DIR` | `~/.codex` | Override Codex CLI data directory |

</details>

## Web GUI & System Tray

Run `mcas` (with no arguments) or open **mcas** from the Windows Start Menu:

```powershell
mcas                   # starts hidden server + tray icon and opens http://127.0.0.1:47831
mcas ui --port 48000   # use a custom port (or set MCAS_PORT)
mcas ui --no-browser   # start server + tray icon in background without opening a tab
mcas ui stop           # stop the server and remove the tray icon
```

The Web UI binds strictly to `127.0.0.1`, verifies `Host` headers, and authenticates all `/api/*` endpoints with a per-session `X-Mcas-Token`.

**Web UI features:**
- **Basic / Advanced Modes, Bilingual (`EN` / `VI`), Theme Toggle & Visual Help**: Defaults to **Basic** mode with safe defaults for daily use and includes a one-click **Basic / Advanced** toggle on the tab bar to reveal power filters, custom folder overrides, `<thought>` options, and watched-folder Auto-Sync settings. Top-right utility bar includes a **Settings** gear menu (**Start with Windows** & **Stop server**), **`? Help`** (shortcut `?`) with an annotated visual guide & FAQ, an **`EN / VI`** flag dropdown for live English / Tiếng Việt switching (persisted in `localStorage`), and a **Light / Dark** theme toggle. See also the illustrated walkthrough in [docs/ui-guide.md](docs/ui-guide.md).
- **Conversations, Provider Sync & Cleanup**: Synced conversations across providers merge into a single row with a tag like `(claude, agy, codex)`. Use the **Providers** checkbox dropdown on any row to add (`+ Add`), remove (`- Remove`), or untick all checkboxes & click **Apply** (with a delete confirmation dialog) to delete completely; sort by newest, largest size, or turn count; **Lighten** bulky conversations in-place (keep newest 50%/N turns, truncate tool outputs, purge heavy brain/subagent sidecars); bulk **Copy Selected** / **Delete Selected**; 1-click **Sync folder** modal; **Clean Up** storage modal (dry-run preview, orphans, empty/subagent/old/large sessions, SQLite `VACUUM` for `agy`); and slide-over **Preview** drawer with provider tabs, target copy selector, size breakdown, collapsible `<thought>` blocks, tool calls/results, and copyable `Resume Dir` / `Resume Cmd`.
- **Auto-Sync & Activity**: Enable/disable background 2-way or 3-way sync, configure direction (`both` across all active providers, pair-specific like `agy_codex`, or 1-way like `claude2codex`), check interval (`10s`-`300s`), scope (`All workspaces` vs `Selected folders only`), `<thought>` folding, trigger **Sync Now**, and review recent sync activity with target resume commands.

**System Tray icon** (Windows, while the server runs):
- **Left-click**: Open Web UI in your browser
- **Right-click**:
  - **Open mcas**
  - **Auto-Sync: ON / OFF** (live toggle)
  - **Sync Now** (runs an immediate sync cycle and displays a balloon summary)
  - **Start with Windows** (creates/removes Startup shortcut)
  - **Quit mcas** (gracefully stops server and removes tray icon)

## CLI Usage

```powershell
# List recent top-level sessions across agy, claude, and codex
mcas list
mcas list --cwd C:\path\to\project --source codex --limit 20
mcas list --q "auth" --all --json

# Copy a single conversation between providers (supports UUID, prefix, or 'latest')
mcas copy latest --to claude
mcas copy <id_or_prefix> --to codex --from claude --new-id --include-thinking
mcas agy2claude <conv_id> --target-cwd C:\other\repo
mcas claude2agy <session_id> --overwrite

# Copy all top-level sessions for a workspace folder from one provider to another
mcas sync-dir --from claude --to codex --cwd C:\path\to\project

# Inspect metadata/turns/size, delete (single, bulk, or synced peers), lighten, or clean up
mcas list --sort size
mcas inspect <id> --source agy
mcas delete <id> --source claude --yes
mcas delete <id1> <id2> --source agy --also-synced --yes
mcas trim <id> --source claude --keep-ratio 0.5 --yes
mcas clean --dry-run
mcas clean --empty --subagents --yes

# Configure or trigger background Auto-Sync
mcas autosync status
mcas autosync on --direction both --scope all --interval 30
mcas autosync on --scope watched --watch C:\path\to\project
mcas autosync run
mcas autosync off

# Lifecycle
mcas autostart on        # start tray icon + Auto-Sync automatically on Windows sign-in
mcas autostart off
mcas update              # update via uv (preserves sync_state.json)
mcas uninstall           # remove tool/shortcuts/ui.json (keeps sync_state.json if present)
mcas uninstall --purge   # remove everything including sync_state.json
mcas version             # print installed version
mcas help                # show help
```

## How Loop-Free Auto-Sync Works

Auto-Sync maintains a pairing ledger in `%LOCALAPPDATA%\mcas\sync_state.json`:
- Whenever a session is synced (`A -> B`), `mcas` records `(source, source_id, target, target_id)` along with **both** the source and target `(mtime_ns, turn_count)` after the write.
- On subsequent ticks, `mcas` only updates `B` in-place (`overwrite=True`) if `A` independently gains turns or advances beyond the recorded `(mtime_ns, count)`.
- Because the target's post-write `(mtime_ns, count)` is recorded in the ledger, writing `B` never echoes back as a reverse `B -> A` sync on the next tick.
- If `B` later gains new turns independently, `mcas` cleanly syncs those back to `A`.

## Conversion Details & Edge Cases

- **Direct SQLite + protobuf reads/writes for `agy`**: Reads `steps` (`ORDER BY idx`) directly from `.db` files (avoiding `transcript.jsonl` formatting differences) and updates both `conversation_summaries` and `conversations_fts` (`chunk_index = -1` and `0`) cleanly, unlinking stale `-wal`/`-shm` sidecars on `--overwrite`.
- **Anthropic replay invariants for `claude`**: Sanitizes tool IDs/names to `^[a-zA-Z0-9_-]{1,64}$`, ensures dict tool inputs, enforces `user`/`assistant` alternation, and synthesizes `tool_result`s for interrupted calls.
- **Claude workspace directory matching**: `claude --resume <id>` only looks inside `~/.claude/projects/<slug_of_current_cwd>/`. When targeting a workspace different from your current directory, `mcas` writes to the target slug and prints both `Resume Dir` (`cd "<target>"`) and `claude --resume <id>`.
- **Codex CLI (`codex`) rollout & SQLite index**: Reads `rollout-YYYY-MM-DDTHH-MM-SS-<uuid>.jsonl` under `sessions/` (plus `archived_sessions/` when `--all` is passed) alongside `session_index.jsonl` and `state_5.sqlite` (`threads`), normalizing Windows `\\?\` extended-length UNC paths in `cwd`. On writes, `mcas` writes atomic legacy rollout JSONL, upserts `state_5.sqlite` (`history_mode='legacy'`) via `UPDATE`/`INSERT` (preserving foreign-key children and paginated threads), and prints `Resume Dir` + `codex resume <id>` when targeting another folder.
- **Thinking blocks**: By default, raw thinking is not injected into visible assistant text. Pass `--include-thinking` (or enable **Include `<thought>`**) to fold reasoning into `<thought>...</thought>` assistant text so it remains visible upon resume.

## Project Layout

```text
pyproject.toml               # uv package metadata, CLI entry point (mcas.cli:main), ruff/pytest config
install.ps1                  # Windows installer (installs uv if needed + uv tool install + Start Menu shortcut)
src/mcas/
  __init__.py                # exports __version__
  __main__.py                # python -m mcas entry point
  constants.py, errors.py, config.py, models.py, cache.py
  util/                      # protobuf codec, path/slug conversion, timefmt, text helpers, atomic fsio
  providers/
    base.py, __init__.py     # AgentProvider ABC, FutureProvider, provider registry
    agy/                     # schema, pb_builders, storage, provider
    claude/                  # storage, convert, provider
    codex/                   # storage, convert, provider
  sync/                      # state.py (sync_state.json), copy.py, autosync.py
  services/                  # query.py, delete.py, trim.py, clean.py
  server/
    app.py, routes.py, static_files.py
    static/                  # index.html, css/*, js/* (i18n/modals), img/guide-*.png
  cli/
    __init__.py, parser.py
    commands/                # sessions.py, maintenance.py, app_cmds.py
  desktop/
    autostart.py             # Windows Startup shortcut (test_autostart / set_autostart)
    launcher.py              # open_ui / stop_ui / update / uninstall
    tray.ps1                 # Windows PowerShell WinForms system tray host
docs/
  ui-guide.md                # Annotated visual Web UI guide
tests/                       # pytest unit/integration/API suite, CDP browser E2E, Windows sandbox E2E
```

## Development & Sandbox Testing

```powershell
# Sync dev environment (Python + pytest + ruff)
uv sync

# Run CLI from source
uv run mcas --help
uv run mcas list

# Run tests & linter (all tests run in isolated temp sandboxes)
uv run pytest
uv run ruff check src tests

# Install from local checkout into uv tool environment
$env:MCAS_SOURCE = (Get-Location).Path
.\install.ps1

# Run full Windows install E2E in an isolated temp sandbox
powershell -NoProfile -ExecutionPolicy Bypass -File tests\sandbox.ps1
```

> **Note for contributors:** Keep all `.ps1` files strictly ASCII-only (`[0x00..0x7F]`) with CRLF line endings. Windows PowerShell 5.1 reads BOM-less `.ps1` files using the system ANSI code page.
