from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mcas.errors import SyncError
from mcas.providers import get_provider
from mcas.services.clean import clean_storage
from mcas.services.delete import delete_sessions_batch
from mcas.services.trim import trim_session
from mcas.sync.autosync import run_autosync_cycle
from mcas.sync.state import get_autosync_dict, update_autosync_config
from mcas.util.pathconv import normalize_windows_path


def _parse_bool_str(val: str) -> bool:
    s = (val or "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise SyncError(f"Invalid boolean value '{val}'. Use true|false.")


def cmd_delete(args: argparse.Namespace) -> int:
    prov = get_provider(args.source, require_supported=True)
    raw_ids = [s.strip() for s in args.ids if s.strip()]
    if not raw_ids or any(s.lower() == "latest" for s in raw_ids):
        raise SyncError("Explicit session ID(s) are required for delete.")

    resolved_items: list[dict[str, str]] = []
    for sid in raw_ids:
        loc, summary = prov.find_session(sid)
        resolved_id = summary.session_id if summary else loc.stem
        resolved_items.append({"source": prov.id, "id": resolved_id})

    also_synced = bool(getattr(args, "also_synced", False))
    if not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Non-interactive delete requires --yes.")
        id_list = ", ".join(x["id"] for x in resolved_items)
        peer_note = " (and linked synced peer sessions)" if also_synced else ""
        ans = input(
            f"Delete {len(resolved_items)} {prov.id} session(s) [{id_list}]{peer_note}? [y/N] "
        ).strip()
        if ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    res = delete_sessions_batch(resolved_items, delete_synced_peers=also_synced)
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["message"])
    return 0


def cmd_trim(args: argparse.Namespace) -> int:
    prov = get_provider(args.source, require_supported=True)
    sid = args.id.strip()
    if not sid or sid.lower() == "latest":
        raise SyncError("An explicit session ID is required for trim.")

    loc, summary = prov.find_session(sid)
    resolved_id = summary.session_id if summary else loc.stem

    keep_last_turns: int | None = args.keep_turns
    keep_ratio: float | None = args.keep_ratio
    if args.keep_all_turns:
        keep_last_turns = None
        keep_ratio = 1.0
    elif keep_last_turns is None and keep_ratio is None:
        keep_ratio = 0.5

    if not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Non-interactive trim requires --yes.")
        ans = input(
            f"Lighten/trim {prov.id} session {resolved_id} in-place? [y/N] "
        ).strip()
        if ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    res = trim_session(
        source=prov.id,
        session_id=resolved_id,
        keep_ratio=keep_ratio,
        keep_last_turns=keep_last_turns,
        max_tool_output_chars=args.max_tool_chars,
        strip_thinking=not args.keep_thinking,
        purge_sidecars=not args.keep_sidecars,
    )
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["message"])
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    cwd_val = normalize_windows_path(args.cwd) if args.cwd else None
    clean_orphans = not args.no_orphans
    vacuum_db = not args.no_vacuum

    if not args.dry_run and not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Non-interactive clean requires --yes (or use --dry-run).")
        preview = clean_storage(
            source=args.source,
            cwd=cwd_val,
            clean_orphans=clean_orphans,
            delete_empty=args.empty,
            delete_subagents=args.subagents,
            older_than_days=args.older_than,
            larger_than_mb=args.larger_than,
            vacuum_db=vacuum_db,
            dry_run=True,
        )
        print(preview["message"])
        ans = input("Proceed with storage cleanup? [y/N] ").strip()
        if ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    res = clean_storage(
        source=args.source,
        cwd=cwd_val,
        clean_orphans=clean_orphans,
        delete_empty=args.empty,
        delete_subagents=args.subagents,
        older_than_days=args.older_than,
        larger_than_mb=args.larger_than,
        vacuum_db=vacuum_db,
        dry_run=bool(args.dry_run),
    )
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["message"])
        for item in res.get("previewItems", [])[:25]:
            print(f"  - [{item.get('kind', '')}] {item.get('label', '')} ({item.get('sizeHuman', '')})")
    return 0


def cmd_autosync(args: argparse.Namespace) -> int:
    action = args.action
    inc_think = (
        _parse_bool_str(args.include_thinking)
        if args.include_thinking is not None
        else None
    )

    enabled_override: bool | None = None
    if action == "on":
        enabled_override = True
    elif action == "off":
        enabled_override = False

    if any(
        x is not None
        for x in (
            enabled_override,
            args.interval,
            args.direction,
            args.scope,
            args.watch,
            args.unwatch,
            inc_think,
        )
    ):
        update_autosync_config(
            enabled=enabled_override,
            interval_sec=args.interval,
            direction=args.direction,
            scope=args.scope,
            add_watch=args.watch,
            remove_watch=args.unwatch,
            include_thinking=inc_think,
        )

    run_res: dict[str, Any] | None = None
    if action == "run":
        run_res = run_autosync_cycle(force_even_if_disabled=True)

    cfg = get_autosync_dict()
    if args.json:
        out: dict[str, Any] = {"autosync": cfg}
        if run_res is not None:
            out["result"] = run_res
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if run_res is not None:
        print(run_res["message"])
    print(f"Auto-sync:  {'ON' if cfg['enabled'] else 'OFF'}")
    print(f"Direction:  {cfg['direction']}")
    print(f"Interval:   {cfg['intervalSec']}s")
    print(f"Scope:      {cfg['scope']}")
    print(f"Thinking:   {'on' if cfg['includeThinking'] else 'off'}")
    print(
        "Watched:    "
        + (", ".join(cfg["watchedFolders"]) if cfg["watchedFolders"] else "(none)")
    )
    print(f"Pairs:      {cfg['pairCount']}")
    if cfg["lastRunLocal"]:
        print(f"Last run:   {cfg['lastRunLocal']}")
    if cfg["lastError"]:
        print(f"Last error: {cfg['lastError']}")
    return 0
