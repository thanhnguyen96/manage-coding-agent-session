from __future__ import annotations

import argparse

from mcas.cli.commands.app_cmds import (
    cmd_autostart,
    cmd_help,
    cmd_serve,
    cmd_ui,
    cmd_uninstall,
    cmd_update,
    cmd_version,
)
from mcas.cli.commands.maintenance import cmd_autosync, cmd_clean, cmd_delete, cmd_trim
from mcas.cli.commands.sessions import (
    cmd_agy2claude,
    cmd_claude2agy,
    cmd_copy,
    cmd_inspect,
    cmd_list,
    cmd_sync_dir,
)
from mcas.constants import DEFAULT_PORT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcas",
        description="Manage Coding Agent Session (mcas) - browse, convert, and auto-sync agy, claude & codex sessions.",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ui = sub.add_parser("ui", help="Start/open or stop the local web UI and tray icon")
    p_ui.add_argument("action", nargs="?", default=None, help="Optional: 'stop'")
    p_ui.add_argument("--port", type=int, default=None, help=f"Port (default {DEFAULT_PORT})")
    p_ui.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    p_ui.set_defaults(func=cmd_ui)

    p_list = sub.add_parser("list", help="List sessions across coding agent providers")
    p_list.add_argument("--cwd", help="Filter conversations to a workspace folder")
    p_list.add_argument("--source", default="all", help="Provider filter: all|agy|claude|codex")
    p_list.add_argument("--q", help="Search text in ID, title, preview, or workspace")
    p_list.add_argument("--all", action="store_true", help="Include subagents, IDE, and 0-turn sessions")
    p_list.add_argument(
        "--sort",
        default="updated",
        choices=["updated", "oldest", "size", "size_asc", "turns"],
        help="Sort order (default updated)",
    )
    p_list.add_argument("--limit", type=int, default=30, help="Max rows (0 = unlimited, default 30)")
    p_list.add_argument("--json", action="store_true", help="Output JSON")
    p_list.set_defaults(func=cmd_list)

    p_copy = sub.add_parser("copy", help="Copy/convert a session to another provider")
    p_copy.add_argument("id_or_latest", help="Session UUID (or prefix) or 'latest'")
    p_copy.add_argument("--to", required=True, help="Target provider (agy|claude|codex)")
    p_copy.add_argument("--from", dest="from_provider", help="Source provider (agy|claude|codex)")
    p_copy.add_argument("--cwd", help="Workspace path (filters 'latest' and fallback target cwd)")
    p_copy.add_argument("--target-cwd", help="Override destination workspace folder")
    p_copy.add_argument("--all-cwd", action="store_true", help="Allow 'latest' across all workspaces")
    p_copy.add_argument("--new-id", action="store_true", help="Generate a fresh UUIDv4 for destination")
    p_copy.add_argument("--overwrite", action="store_true", help="Overwrite destination if UUID exists")
    p_copy.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_copy.add_argument("--json", action="store_true", help="Output JSON")
    p_copy.set_defaults(func=cmd_copy)

    p_a2c = sub.add_parser("agy2claude", help="Convert an agy session to Claude Code")
    p_a2c.add_argument("conv_id_or_latest", help="Antigravity UUID (or prefix) or 'latest'")
    p_a2c.add_argument("--cwd", help="Workspace path (filters 'latest' and fallback target cwd)")
    p_a2c.add_argument("--target-cwd", help="Override destination workspace folder")
    p_a2c.add_argument("--all-cwd", action="store_true", help="Allow 'latest' across all workspaces")
    p_a2c.add_argument("--new-id", action="store_true", help="Generate a fresh UUIDv4")
    p_a2c.add_argument("--overwrite", action="store_true", help="Overwrite if target exists")
    p_a2c.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_a2c.add_argument("--json", action="store_true", help="Output JSON")
    p_a2c.set_defaults(func=cmd_agy2claude)

    p_c2a = sub.add_parser("claude2agy", help="Convert a Claude Code session to Antigravity")
    p_c2a.add_argument("session_id_or_latest", help="Claude UUID (or prefix) or 'latest'")
    p_c2a.add_argument("--cwd", help="Workspace path (filters 'latest' and fallback target cwd)")
    p_c2a.add_argument("--target-cwd", help="Override destination workspace folder")
    p_c2a.add_argument("--all-cwd", action="store_true", help="Allow 'latest' across all workspaces")
    p_c2a.add_argument("--new-id", action="store_true", help="Generate a fresh UUIDv4")
    p_c2a.add_argument("--overwrite", action="store_true", help="Overwrite if target exists")
    p_c2a.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_c2a.add_argument("--json", action="store_true", help="Output JSON")
    p_c2a.set_defaults(func=cmd_claude2agy)

    p_sdir = sub.add_parser("sync-dir", help="Copy all top-level sessions for a workspace between providers")
    p_sdir.add_argument("--from", dest="from_provider", required=True, help="Source provider (agy|claude|codex)")
    p_sdir.add_argument("--to", required=True, help="Target provider (agy|claude|codex)")
    p_sdir.add_argument("--cwd", help="Source workspace folder (defaults to current working directory)")
    p_sdir.add_argument("--target-cwd", help="Destination workspace folder (defaults to --cwd)")
    p_sdir.add_argument("--overwrite", action="store_true", help="Overwrite existing target sessions")
    p_sdir.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_sdir.add_argument("--json", action="store_true", help="Output JSON")
    p_sdir.set_defaults(func=cmd_sync_dir)

    p_insp = sub.add_parser("inspect", help="Inspect full turns and metadata of a session")
    p_insp.add_argument("id", help="Session UUID or unique prefix")
    p_insp.add_argument("--source", help="Provider (agy|claude|codex)")
    p_insp.add_argument("--json", action="store_true", help="Output JSON")
    p_insp.set_defaults(func=cmd_inspect)

    p_del = sub.add_parser("delete", help="Delete one or more sessions from a provider")
    p_del.add_argument("ids", nargs="+", help="One or more session UUIDs or unique prefixes")
    p_del.add_argument("--source", required=True, help="Provider (agy|claude|codex)")
    p_del.add_argument(
        "--also-synced",
        action="store_true",
        help="Also delete linked synced peer session(s)",
    )
    p_del.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_del.add_argument("--json", action="store_true", help="Output JSON")
    p_del.set_defaults(func=cmd_delete)

    p_trim = sub.add_parser(
        "trim",
        aliases=["lighten"],
        help="Lighten a conversation in-place (trim old turns, strip thinking, purge sidecars)",
    )
    p_trim.add_argument("id", help="Session UUID or unique prefix")
    p_trim.add_argument("--source", required=True, help="Provider (agy|claude|codex)")
    p_trim.add_argument("--keep-ratio", type=float, default=None, help="Fraction of newest turns to keep (e.g. 0.5)")
    p_trim.add_argument("--keep-turns", type=int, default=None, help="Keep only the newest N turns")
    p_trim.add_argument("--keep-all-turns", action="store_true", help="Keep all turns; only trim bulky tool outputs, thinking, and sidecars")
    p_trim.add_argument("--max-tool-chars", type=int, default=2000, help="Truncate tool outputs above N chars (0 = disable, default 2000)")
    p_trim.add_argument("--keep-thinking", action="store_true", help="Preserve thinking blocks instead of stripping them")
    p_trim.add_argument("--keep-sidecars", action="store_true", help="Keep brain/subagent/file-history sidecars")
    p_trim.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_trim.add_argument("--json", action="store_true", help="Output JSON")
    p_trim.set_defaults(func=cmd_trim)

    p_clean = sub.add_parser(
        "clean",
        help="Bulk clean orphans, empty/subagent/old/large sessions, and vacuum SQLite DB",
    )
    p_clean.add_argument("--source", default="all", help="Provider filter: all|agy|claude|codex")
    p_clean.add_argument("--cwd", help="Limit session cleanup to a workspace folder")
    p_clean.add_argument("--no-orphans", action="store_true", help="Do not clean orphaned sidecar directories")
    p_clean.add_argument("--empty", action="store_true", help="Delete 0-turn empty sessions")
    p_clean.add_argument("--subagents", action="store_true", help="Delete subagent sessions and Claude subagents/ folders")
    p_clean.add_argument("--older-than", type=int, default=None, help="Delete sessions older than N days")
    p_clean.add_argument("--larger-than", type=float, default=None, help="Delete sessions larger than N MB")
    p_clean.add_argument("--no-vacuum", action="store_true", help="Skip SQLite VACUUM on conversation_summaries.db")
    p_clean.add_argument("--dry-run", action="store_true", help="Preview cleanup impact without deleting")
    p_clean.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_clean.add_argument("--json", action="store_true", help="Output JSON")
    p_clean.set_defaults(func=cmd_clean)

    p_auto = sub.add_parser("autosync", help="Configure or trigger loop-free auto-sync")
    p_auto.add_argument("action", choices=["status", "on", "off", "run"], help="Auto-sync action")
    p_auto.add_argument(
        "--direction",
        help="both|agy_claude|agy_codex|claude_codex|agy2claude|claude2agy|agy2codex|codex2agy|claude2codex|codex2claude",
    )
    p_auto.add_argument("--scope", help="all|watched")
    p_auto.add_argument("--interval", type=int, help="Interval in seconds (min 5)")
    p_auto.add_argument("--watch", action="append", help="Add workspace folder to watch list")
    p_auto.add_argument("--unwatch", action="append", help="Remove workspace folder from watch list")
    p_auto.add_argument(
        "--include-thinking",
        nargs="?",
        const="true",
        default=None,
        help="true|false",
    )
    p_auto.add_argument("--json", action="store_true", help="Output JSON")
    p_auto.set_defaults(func=cmd_autosync)

    p_as = sub.add_parser("autostart", help="Start tray icon and Auto-Sync with Windows")
    p_as.add_argument("action", nargs="?", default=None, help="on|off")
    p_as.add_argument("--port", type=int, default=None, help=f"Port (default {DEFAULT_PORT})")
    p_as.set_defaults(func=cmd_autostart)

    p_upd = sub.add_parser("update", help="Install the latest version of mcas")
    p_upd.set_defaults(func=cmd_update)

    p_un = sub.add_parser("uninstall", help="Remove mcas")
    p_un.add_argument("--purge", action="store_true", help="Also delete sync_state.json")
    p_un.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_un.set_defaults(func=cmd_uninstall)

    p_srv = sub.add_parser("__serve", help="Run the local HTTP API server + auto-sync daemon")
    p_srv.add_argument("--port", type=int, default=None, help=f"Port (default {DEFAULT_PORT})")
    p_srv.add_argument("--ui-html", help="Path to index.html")
    p_srv.set_defaults(func=cmd_serve)

    p_ver = sub.add_parser("version", help="Print mcas version")
    p_ver.set_defaults(func=cmd_version)

    p_help = sub.add_parser("help", help="Show usage summary")
    p_help.set_defaults(func=cmd_help)

    return parser
