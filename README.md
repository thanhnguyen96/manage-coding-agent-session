# manage-coding-agent-session (`mcas`)

Browse, inspect, copy, and automatically sync coding-agent conversation sessions across **Antigravity CLI (`agy`)** and **Claude Code CLI (`claude`)** on Windows — via CLI, Web GUI, and Windows system tray.

> **Unofficial.** Not affiliated with Google or Anthropic. Zero third-party dependencies (Python 3 standard library + built-in Windows PowerShell 5.1).

## Supported Providers

| Provider | Status | Local Storage |
|---|---|---|
| **Antigravity CLI (`agy`)** | Supported | `%USERPROFILE%\.gemini\antigravity-cli\conversations\*.db` + `conversation_summaries.db` |
| **Claude Code CLI (`claude`)** | Supported | `%USERPROFILE%\.claude\projects\<slug>\*.jsonl` |
| **OpenCode (`opencode`)** | Planned (`Coming Soon`) | Architecture ready via `AgentProvider` registry |
| **Codex CLI (`codex`)** | Planned (`Coming Soon`) | Architecture ready via `AgentProvider` registry |

## Requirements

- Windows 10 / 11 with PowerShell 5.1+ (built in)
- Python 3.8+ (`python` or `py -3` on `PATH`)
- No admin rights, no Node.js, no `pip install`

*(On macOS/Linux, `mcas.py` can also be run directly with `python3 mcas.py ...`.)*

## Install

```powershell
irm https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session/HEAD/install.ps1 | iex
```

Pin a specific git tag, branch, or commit with `$env:MCAS_REF = 'v0.1.0'` before running.

Prefer to inspect first?

```powershell
irm https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session/HEAD/install.ps1 -OutFile install.ps1
notepad install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Open a new terminal afterwards so `mcas` (and `manage-coding-agent-session`) is on your `PATH`.

<details>
<summary>Environment variables (installation & overrides)</summary>

| Variable | Default | Purpose |
|---|---|---|
| `MCAS_ROOT` | `%LOCALAPPDATA%\mcas` | Install folder (`app\`, `bin\`, `ui.json`, `sync_state.json`) |
| `MCAS_PORT` | `47831` | Default Web UI port |
| `MCAS_REF` | `HEAD` | Git ref used by `install.ps1` and `mcas update` |
| `MCAS_SOURCE` | *(unset)* | Local folder path to install/update from instead of GitHub |
| `MCAS_AGY_DIR` | `~/.gemini/antigravity-cli` | Override Antigravity CLI data directory |
| `MCAS_AGY_IDE_DIR` | `~/.gemini/antigravity` | Override Antigravity IDE data directory |
| `MCAS_CLAUDE_DIR` | `~/.claude` | Override Claude Code data directory |

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
- **Conversations, Provider Sync & Cleanup**: Synced conversations across providers merge into a single row with a tag like `(claude, agy)`. Use the **Providers** checkbox dropdown on any row to add (`+ Add`), remove (`- Remove`), or untick all checkboxes & click **Apply** (with a delete confirmation dialog) to delete completely; sort by newest, largest size, or turn count; **Lighten** bulky conversations in-place (keep newest 50%/N turns, truncate tool outputs, purge heavy brain/subagent sidecars); bulk **Copy Selected** / **Delete Selected**; 1-click **Sync folder** modal; **Clean Up** storage modal (dry-run preview, orphans, empty/subagent/old/large sessions, SQLite `VACUUM`); and slide-over **Preview** drawer with provider tabs, size breakdown, collapsible `<thought>` blocks, tool calls/results, and copyable `Resume Dir` / `Resume Cmd`.
- **Auto-Sync & Activity**: Enable/disable background bidirectional sync, configure direction, check interval (`10s`–`300s`), scope (`All workspaces` vs `Selected folders only`), `<thought>` folding, trigger **Sync Now**, and review recent sync activity with target resume commands.

**System Tray icon** (while the server runs):
- **Left-click**: Open Web UI in your browser
- **Right-click**:
  - **Open mcas**
  - **Auto-Sync: ON / OFF** (live toggle)
  - **Sync Now** (runs an immediate sync cycle and displays a balloon summary)
  - **Start with Windows** (creates/removes Startup shortcut)
  - **Quit mcas** (gracefully stops server and removes tray icon)

## CLI Usage

```powershell
# List recent top-level sessions across agy and claude
mcas list
mcas list --cwd C:\path\to\project --source agy --limit 20
mcas list --q "auth" --all --json

# Copy a single conversation between providers (supports UUID, prefix, or 'latest')
mcas copy latest --to claude
mcas copy <id_or_prefix> --to agy --from claude --new-id --include-thinking
mcas agy2claude <conv_id> --target-cwd C:\other\repo
mcas claude2agy <session_id> --overwrite

# Copy all top-level sessions for a workspace folder from one provider to another
mcas sync-dir --from agy --to claude --cwd C:\path\to\project

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
mcas update              # update to latest version (preserves sync_state.json)
mcas uninstall           # remove app/shims/shortcuts (keeps sync_state.json if present)
mcas uninstall --purge   # remove everything including sync_state.json
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
- **Thinking blocks**: By default, raw thinking is not injected into visible assistant text. Pass `--include-thinking` (or enable **Include `<thought>`**) to fold reasoning into `<thought>...</thought>` assistant text so it remains visible upon resume.

## Development & Sandbox Testing

Run the isolated end-to-end sandbox suite (never touches your real `~/.claude`, `~/.gemini/antigravity-cli`, `%LOCALAPPDATA%\mcas`, or User `PATH`):

```powershell
# Install from local checkout
$env:MCAS_SOURCE = (Get-Location).Path
.\install.ps1

# Run full sandbox verification (seed, install, CLI, Web API, auth 403, loop-free autosync, update, uninstall)
powershell -NoProfile -ExecutionPolicy Bypass -File tests\sandbox.ps1
```

> **Note for contributors:** Keep all `.ps1` files strictly ASCII-only (`[0x00..0x7F]`). Windows PowerShell 5.1 reads BOM-less `.ps1` files using the system ANSI code page.
