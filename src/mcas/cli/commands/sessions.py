from __future__ import annotations

import argparse
import json
import os
from typing import Any

from mcas.models import SessionSummary
from mcas.providers import PROVIDERS, get_provider, get_supported_providers
from mcas.services.query import _matches_query, _sort_sessions, get_session_detail
from mcas.sync.copy import copy_one_session, sync_directory_sessions
from mcas.util.pathconv import normalize_windows_path, paths_match
from mcas.util.text import format_bytes, truncate_preview
from mcas.util.timefmt import format_iso_utc, format_local_time


def cmd_list(args: argparse.Namespace) -> int:
    src = (args.source or "all").strip().lower()
    if src != "all":
        get_provider(src, require_supported=True)
    cwd_filter = normalize_windows_path(args.cwd) if args.cwd else None

    sessions: list[SessionSummary] = []
    for prov in get_supported_providers():
        if src in ("all", prov.id):
            sessions.extend(
                prov.list_sessions(include_all=args.all, cwd_filter=cwd_filter)
            )

    if args.q:
        sessions = [s for s in sessions if _matches_query(s, args.q)]

    _sort_sessions(sessions, getattr(args, "sort", "updated"))
    if args.limit and args.limit > 0:
        sessions = sessions[: args.limit]

    if args.json:
        payload = [
            {
                "source": s.source,
                "id": s.session_id,
                "updated_at": format_iso_utc(s.updated_at),
                "updated_local": format_local_time(s.updated_at),
                "title": s.title,
                "preview": s.preview,
                "cwd": s.cwd,
                "count": s.step_or_msg_count,
                "size_bytes": s.size_bytes,
                "size_human": format_bytes(s.size_bytes),
                "path": str(s.location),
                "extra": s.extra,
                "resume_cmd": PROVIDERS[s.source].resume_command(s.session_id),
            }
            for s in sessions
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not sessions:
        print("No conversations found matching filters.")
        return 0

    print(
        f"{'SRC':<6} {'ID':<36} {'UPDATED':<19} {'CNT':>4} {'SIZE':>9}  {'CWD':<30} TITLE / PREVIEW"
    )
    print("-" * 132)
    for s in sessions:
        cwd_disp = s.cwd or "(none)"
        if len(cwd_disp) > 30:
            cwd_disp = "..." + cwd_disp[-27:]
        title_disp = truncate_preview(s.title or s.preview, 42)
        extra_tag = f" [{s.extra}]" if s.extra else ""
        print(
            f"{s.source:<6} {s.session_id:<36} {format_local_time(s.updated_at):<19} "
            f"{s.step_or_msg_count:>4} {format_bytes(s.size_bytes):>9}  {cwd_disp:<30} {title_disp}{extra_tag}"
        )
    return 0


def _print_copy_result(res: dict[str, Any]) -> None:
    print(f"Converted {res['source']} -> {res['toProvider']}")
    print(f"  Source ID:    {res['sourceId']}")
    regen_note = " (fresh UUID generated on collision)" if res["regenerated"] else ""
    print(f"  Converted ID: {res['targetId']}{regen_note}")
    print(f"  Target CWD:   {res['targetCwd']}")
    print(f"  Output Path:  {res['path']}")
    cur_cwd = normalize_windows_path(os.getcwd())
    if res["toProvider"] == "claude" and res["targetCwd"] and not paths_match(cur_cwd, res["targetCwd"]):
        print(f"  Resume dir:   cd \"{res['targetCwd']}\"")
    print(f"  Resume cmd:   {res['resumeCmd']}")


def cmd_copy(args: argparse.Namespace) -> int:
    res = copy_one_session(
        id_or_latest=args.id_or_latest,
        to_provider_id=args.to,
        from_provider_id=args.from_provider,
        cwd=args.cwd,
        target_cwd=args.target_cwd,
        all_cwd=args.all_cwd,
        new_id=args.new_id,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
        kind="manual",
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    _print_copy_result(res)
    return 0


def cmd_agy2claude(args: argparse.Namespace) -> int:
    res = copy_one_session(
        id_or_latest=args.conv_id_or_latest,
        from_provider_id="agy",
        to_provider_id="claude",
        cwd=args.cwd,
        target_cwd=args.target_cwd,
        all_cwd=args.all_cwd,
        new_id=args.new_id,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
        kind="manual",
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    _print_copy_result(res)
    return 0


def cmd_claude2agy(args: argparse.Namespace) -> int:
    res = copy_one_session(
        id_or_latest=args.session_id_or_latest,
        from_provider_id="claude",
        to_provider_id="agy",
        cwd=args.cwd,
        target_cwd=args.target_cwd,
        all_cwd=args.all_cwd,
        new_id=args.new_id,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
        kind="manual",
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    _print_copy_result(res)
    return 0


def cmd_sync_dir(args: argparse.Namespace) -> int:
    cwd_val = args.cwd or os.getcwd()
    results = sync_directory_sessions(
        from_provider_id=args.from_provider,
        to_provider_id=args.to,
        cwd=cwd_val,
        target_cwd=args.target_cwd,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
    )
    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        return 0 if all(r["status"] != "error" for r in results) else 1

    if not results:
        print(
            f"No top-level {args.from_provider} sessions found in workspace '{normalize_windows_path(cwd_val)}'."
        )
        return 0

    copied = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors = sum(1 for r in results if r["status"] == "error")
    print(
        f"Sync dir {args.from_provider} -> {args.to}: {copied} copied, {skipped} skipped, {errors} error(s)"
    )
    for r in results:
        tag = r["status"].upper()
        tid = r["targetId"] or "-"
        print(f"  [{tag:<7}] {r['sourceId']} -> {tid}  {truncate_preview(r['title'], 50)}")
        if r["error"]:
            print(f"            error: {r['error']}")
    return 0 if errors == 0 else 1


def cmd_inspect(args: argparse.Namespace) -> int:
    detail = get_session_detail(args.source, args.id)
    if args.json:
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 0

    print(f"Source:     {detail['source']}")
    print(f"ID:         {detail['id']}")
    print(f"Title:      {detail['title']}")
    print(f"Workspace:  {detail['cwd'] or '(none)'}")
    print(f"Updated:    {detail['updatedLocal']}")
    print(f"Path:       {detail['path']}")
    print(f"Resume cmd: {detail['resumeCmd']}")
    if detail["syncedTo"]:
        links = ", ".join(f"{x['provider']}:{x['id']}" for x in detail["syncedTo"])
        print(f"Synced to:  {links}")
    print("-" * 80)
    for t in detail["turns"]:
        role = t["role"].upper()
        print(f"[{t['index']}] {role} ({t['timestampLocal']})")
        if t["thinking"]:
            print(f"  <thought> {truncate_preview(t['thinking'], 200)}")
        if t["text"]:
            print(f"  {t['text']}")
        for tc in t["toolCalls"]:
            print(f"  -> tool_call {tc['name']} ({tc['id']}): {truncate_preview(tc['arguments'], 140)}")
        for tr in t["toolResults"]:
            err = " ERROR" if tr["isError"] else ""
            print(
                f"  <- tool_result{err} {tr['name']} ({tr['toolUseId']}): {truncate_preview(tr['content'], 160)}"
            )
    return 0
