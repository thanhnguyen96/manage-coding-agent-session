from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcas.config import get_default_port, get_mcas_root
from mcas.constants import DEFAULT_PORT, VERSION
from mcas.errors import SyncError
from mcas.server.app import serve_http


def format_top_level_help() -> str:
    return (
        f"mcas {VERSION} - Manage Coding Agent Session (Antigravity CLI + Claude Code CLI)\n\n"
        f"  mcas                                    open the web UI and start the tray icon\n"
        f"  mcas ui [--port <n>] [--no-browser]     start/open the web UI (default port: {DEFAULT_PORT})\n"
        f"  mcas ui stop                            stop the web UI server and tray icon\n"
        f"  mcas list [options]                     list sessions from agy and claude\n"
        f"  mcas copy <id|latest> --to <provider>   copy a session between providers (agy, claude)\n"
        f"  mcas agy2claude <id|latest> [options]   copy an agy conversation to Claude Code\n"
        f"  mcas claude2agy <id|latest> [options]   copy a Claude Code session to agy\n"
        f"  mcas sync-dir --from <p> --to <p>       copy all top-level sessions in a workspace\n"
        f"  mcas inspect <id> [--source <p>]        inspect a session's metadata and turns\n"
        f"  mcas delete <id...> --source <p> [-y]   delete one or more sessions (+ --also-synced)\n"
        f"  mcas trim <id> --source <p> [-y]        lighten a conversation (keep newest half/N, purge sidecars)\n"
        f"  mcas clean [--dry-run] [--yes]          clean orphans, subagents, old/empty/large sessions + VACUUM\n"
        f"  mcas autosync <status|on|off|run>       manage or run background bidirectional Auto-Sync\n"
        f"  mcas autostart on|off [--port <n>]      start the tray icon (and Auto-Sync) with Windows\n"
        f"  mcas update                             install the latest version\n"
        f"  mcas uninstall [--purge] [-y]           remove mcas (--purge deletes sync_state.json too)\n"
        f"  mcas version                            print version\n"
        f"  mcas help                               show this help\n\n"
        f"Run 'mcas <subcommand> --help' for detailed flags (list, copy, sync-dir, inspect, delete, trim, clean, autosync).\n"
        f"Data dir: {get_mcas_root()}"
    )


def _validate_port(port: int) -> int:
    if port < 1024 or port > 65535:
        raise SyncError(f"Invalid port '{port}' (use 1024-65535).")
    return port


def cmd_serve(args: argparse.Namespace) -> int:
    port = args.port if args.port is not None else get_default_port()
    ui_html = Path(args.ui_html) if args.ui_html else None
    return serve_http(port=port, ui_html_path=ui_html)


def cmd_version(_args: argparse.Namespace) -> int:
    print(VERSION)
    return 0


def cmd_help(_args: argparse.Namespace) -> int:
    print(format_top_level_help())
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    try:
        from mcas.desktop.launcher import open_ui, stop_ui
    except ImportError as exc:
        raise SyncError("Desktop launcher support is not installed.") from exc

    action = (getattr(args, "action", None) or "").strip().lower()
    if action == "stop":
        print(stop_ui())
        return 0
    if action:
        raise SyncError(f"Unknown ui subcommand '{action}'. Use 'mcas ui' or 'mcas ui stop'.")

    port = _validate_port(args.port if args.port is not None else get_default_port())
    print(open_ui(port=port, no_browser=bool(args.no_browser)))
    return 0


def cmd_autostart(args: argparse.Namespace) -> int:
    try:
        from mcas.desktop.autostart import set_autostart, test_autostart
    except ImportError as exc:
        raise SyncError("Desktop autostart support is not installed.") from exc

    action = (getattr(args, "action", None) or "").strip().lower()
    if not action:
        state = "on" if test_autostart() else "off"
        print(f"autostart: {state}   (mcas autostart on|off)")
        return 0
    if action == "on":
        port = _validate_port(args.port if args.port is not None else get_default_port())
        print(set_autostart(True, port))
        return 0
    if action == "off":
        print(set_autostart(False, 0))
        return 0
    raise SyncError(f"Unknown autostart action '{action}'. Use 'on' or 'off'.")


def cmd_update(_args: argparse.Namespace) -> int:
    try:
        from mcas.desktop.launcher import update
    except ImportError as exc:
        raise SyncError("Desktop update support is not installed.") from exc

    print(update())
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    try:
        from mcas.desktop.launcher import uninstall
    except ImportError as exc:
        raise SyncError("Desktop uninstall support is not installed.") from exc

    purge = bool(args.purge)
    if purge and not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Non-interactive uninstall --purge requires --yes.")
        ans = input("Remove mcas and DELETE sync_state.json? This cannot be undone. [y/N] ").strip()
        if ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    print(uninstall(purge=purge))
    return 0
