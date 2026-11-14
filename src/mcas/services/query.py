from __future__ import annotations

from pathlib import Path
from typing import Any
import os

from mcas.config import (
    get_default_port,
    get_mcas_root,
)
from mcas.constants import VERSION
from mcas.errors import SyncError
from mcas.models import SessionSummary
from mcas.providers import (
    PROVIDERS,
    _collect_sessions_by_provider,
    get_provider,
    get_supported_providers,
)
from mcas.providers.agy.storage import (
    _find_agy_descendants,
    get_agy_session_size,
)
from mcas.providers.base import AgentProvider
from mcas.providers.claude.storage import get_claude_session_size
from mcas.services.clean import get_storage_summary
from mcas.sync.state import (
    _SYNC_LOCK,
    _load_state,
    get_autosync_dict,
)
from mcas.util.pathconv import (
    normalize_windows_path,
    paths_match,
    windows_path_to_slug,
)
from mcas.util.text import format_bytes
from mcas.util.timefmt import (
    format_iso_utc,
    format_local_time,
)

def _build_synced_to_lookup(
    all_summaries_by_provider: dict[str, dict[str, SessionSummary]]
) -> dict[tuple[str, str], list[dict[str, str]]]:
    with _SYNC_LOCK:
        state = _load_state()
        pairs = list(state["pairs"])

    lookup: dict[tuple[str, str], list[dict[str, str]]] = {}

    def add_link(src: str, sid: str, tgt: str, tid: str) -> None:
        tgt_map = all_summaries_by_provider.get(tgt, {})
        tgt_sum = tgt_map.get(tid)
        if tgt_sum is None:
            return
        prov = PROVIDERS.get(tgt)
        if not prov:
            return
        arr = lookup.setdefault((src, sid), [])
        if any(x["provider"] == tgt and x["id"] == tid for x in arr):
            return
        arr.append(
            {
                "provider": tgt,
                "id": tid,
                "title": tgt_sum.title,
                "preview": tgt_sum.preview,
                "cwd": tgt_sum.cwd or "",
                "updatedAt": format_iso_utc(tgt_sum.updated_at),
                "updatedLocal": format_local_time(tgt_sum.updated_at),
                "count": tgt_sum.step_or_msg_count,
                "sizeBytes": tgt_sum.size_bytes,
                "sizeHuman": format_bytes(tgt_sum.size_bytes),
                "extra": tgt_sum.extra,
                "resumeCmd": prov.resume_command(tid),
                "resumeDir": tgt_sum.cwd or "",
            }
        )

    for p in pairs:
        s = str(p.get("source") or "")
        sid = str(p.get("source_id") or "")
        t = str(p.get("target") or "")
        tid = str(p.get("target_id") or "")
        if s and sid and t and tid:
            add_link(s, sid, t, tid)
            add_link(t, tid, s, sid)

    # Also detect identical-UUID pairs created externally before ledger existed
    agy_ids = set(all_summaries_by_provider.get("agy", {}).keys())
    claude_ids = set(all_summaries_by_provider.get("claude", {}).keys())
    for shared_id in agy_ids & claude_ids:
        add_link("agy", shared_id, "claude", shared_id)
        add_link("claude", shared_id, "agy", shared_id)

    return lookup

def _matches_query(s: SessionSummary, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    haystack = " ".join(
        [
            s.session_id,
            s.source,
            s.title,
            s.preview,
            s.cwd or "",
            s.extra,
        ]
    ).lower()
    return q in haystack

def _sort_sessions(sessions: list[SessionSummary], sort: str = "updated_desc") -> None:
    sort_key = (sort or "updated_desc").strip().lower()
    if sort_key in ("size_desc", "size"):
        sessions.sort(key=lambda s: (s.size_bytes, s.updated_at), reverse=True)
    elif sort_key == "size_asc":
        sessions.sort(key=lambda s: (s.size_bytes, s.updated_at))
    elif sort_key in ("turns_desc", "turns"):
        sessions.sort(key=lambda s: (s.step_or_msg_count, s.updated_at), reverse=True)
    elif sort_key in ("updated_asc", "oldest"):
        sessions.sort(key=lambda s: s.updated_at)
    else:
        sessions.sort(key=lambda s: s.updated_at, reverse=True)

def query_sessions_api(
    *,
    source: str = "all",
    cwd: str | None = None,
    q: str | None = None,
    include_all: bool = False,
    limit: int = 200,
    sort: str = "updated_desc",
) -> dict[str, Any]:
    src = (source or "all").strip().lower()
    if src != "all":
        get_provider(src, require_supported=True)

    sort_key = (sort or "updated_desc").strip().lower()
    cwd_filter = normalize_windows_path(cwd) if cwd and cwd.strip() else None

    # Fetch full inventory (include_all=True) to compute accurate syncedTo links even if target is unindexed/all
    all_by_prov_list = _collect_sessions_by_provider(include_all=True)
    all_by_prov_map = {
        pid: {s.session_id: s for s in slist}
        for pid, slist in all_by_prov_list.items()
    }
    synced_lookup = _build_synced_to_lookup(all_by_prov_map)

    if include_all:
        base_by_prov = all_by_prov_list
    else:
        base_by_prov = _collect_sessions_by_provider(include_all=False)

    matched: list[SessionSummary] = []
    cwd_slug_lower = windows_path_to_slug(cwd_filter).lower() if cwd_filter else None
    for pid, slist in base_by_prov.items():
        if src != "all" and pid != src:
            continue
        for s in slist:
            if cwd_filter:
                if s.cwd:
                    if not paths_match(s.cwd, cwd_filter):
                        continue
                elif pid == "claude" and cwd_slug_lower:
                    if s.location.parent.name.lower() != cwd_slug_lower:
                        continue
                else:
                    continue
            if q and not _matches_query(s, q):
                continue
            matched.append(s)

    _sort_sessions(matched, sort_key)

    total = len(matched)
    if limit and limit > 0:
        matched = matched[:limit]

    items = []
    for s in matched:
        prov = PROVIDERS[s.source]
        items.append(
            {
                "id": s.session_id,
                "source": s.source,
                "title": s.title,
                "preview": s.preview,
                "cwd": s.cwd or "",
                "updatedAt": format_iso_utc(s.updated_at),
                "updatedLocal": format_local_time(s.updated_at),
                "count": s.step_or_msg_count,
                "sizeBytes": s.size_bytes,
                "sizeHuman": format_bytes(s.size_bytes),
                "extra": s.extra,
                "resumeCmd": prov.resume_command(s.session_id),
                "resumeDir": s.cwd or "",
                "syncedTo": synced_lookup.get((s.source, s.session_id), []),
            }
        )
    return {"sessions": items, "total": total}

def get_session_detail(source: str | None, session_id: str) -> dict[str, Any]:
    if not session_id or not session_id.strip():
        raise SyncError("Session ID is required.")
    sid = session_id.strip()

    if source:
        prov = get_provider(source, require_supported=True)
        loc, summary = prov.find_session(sid)
    else:
        matches: list[tuple[AgentProvider, Path, SessionSummary | None]] = []
        for prov in get_supported_providers():
            try:
                loc, summary = prov.find_session(sid)
                matches.append((prov, loc, summary))
            except SyncError:
                pass
        if not matches:
            raise SyncError(f"Session '{sid}' not found in any supported provider.")
        if len(matches) > 1:
            names = ", ".join(m[0].id for m in matches)
            raise SyncError(
                f"Session '{sid}' exists in multiple providers ({names}). Specify --source."
            )
        prov, loc, summary = matches[0]

    session = prov.read_session(loc, summary=summary)
    all_by_prov_list = _collect_sessions_by_provider(include_all=True)
    all_by_prov_map = {
        pid: {s.session_id: s for s in slist}
        for pid, slist in all_by_prov_list.items()
    }
    synced_lookup = _build_synced_to_lookup(all_by_prov_map)

    if prov.id == "agy":
        app_dir = "antigravity" if (summary and "antigravity" in summary.extra.split(",")) else "antigravity-cli"
        child_ids = _find_agy_descendants(session.session_id)
        size_bytes, main_bytes, sidecar_bytes = get_agy_session_size(
            session.session_id, db_path=loc, child_ids=child_ids, app_data_dir=app_dir
        )
    else:
        size_bytes, main_bytes, sidecar_bytes = get_claude_session_size(
            session.session_id, jsonl_path=loc
        )

    turns_json = []
    for i, t in enumerate(session.turns):
        turns_json.append(
            {
                "index": i,
                "role": t.role,
                "text": t.text,
                "thinking": t.thinking,
                "timestamp": format_iso_utc(t.timestamp),
                "timestampLocal": format_local_time(t.timestamp),
                "toolCalls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments_json,
                    }
                    for tc in t.tool_calls
                ],
                "toolResults": [
                    {
                        "toolUseId": tr.tool_use_id,
                        "name": tr.name,
                        "arguments": tr.arguments_json,
                        "content": tr.content,
                        "isError": tr.is_error,
                    }
                    for tr in t.tool_results
                ],
            }
        )

    return {
        "id": session.session_id,
        "source": session.source,
        "title": session.title,
        "preview": session.preview,
        "cwd": session.cwd or "",
        "createdAt": format_iso_utc(session.created_at),
        "updatedAt": format_iso_utc(session.updated_at),
        "updatedLocal": format_local_time(session.updated_at),
        "count": summary.step_or_msg_count if summary else len(session.turns),
        "sizeBytes": size_bytes,
        "sizeHuman": format_bytes(size_bytes),
        "mainBytes": main_bytes,
        "mainHuman": format_bytes(main_bytes),
        "sidecarBytes": sidecar_bytes,
        "sidecarHuman": format_bytes(sidecar_bytes),
        "extra": summary.extra if summary else "",
        "path": str(loc),
        "resumeCmd": prov.resume_command(session.session_id),
        "resumeDir": session.cwd or "",
        "syncedTo": synced_lookup.get((session.source, session.session_id), []),
        "turns": turns_json,
    }

def _is_autostart_enabled() -> bool:
    try:
        from mcas.desktop.autostart import test_autostart

        return bool(test_autostart())
    except ImportError:
        from mcas.config import get_startup_lnk_path

        return get_startup_lnk_path().is_file()


def get_status_dict(port: int | None = None) -> dict[str, Any]:
    by_prov = _collect_sessions_by_provider(include_all=False)
    by_prov_all = _collect_sessions_by_provider(include_all=True)
    storage = get_storage_summary(by_prov_all=by_prov_all)

    providers_out = []
    ws_buckets: dict[str, dict[str, Any]] = {}

    for pid, prov in PROVIDERS.items():
        slist = by_prov.get(pid, [])
        psize = (
            storage["agyBytes"]
            if pid == "agy"
            else storage["claudeBytes"]
            if pid == "claude"
            else 0
        )
        providers_out.append(prov.descriptor(count=len(slist), size_bytes=psize))
        for s in slist:
            if not s.cwd:
                continue
            key = os.path.normcase(s.cwd)
            bucket = ws_buckets.get(key)
            if bucket is None:
                bucket = {
                    "cwd": s.cwd,
                    "total": 0,
                    "counts": {p.id: 0 for p in get_supported_providers()},
                }
                ws_buckets[key] = bucket
            bucket["total"] += 1
            bucket["counts"][pid] = bucket["counts"].get(pid, 0) + 1

    workspaces = sorted(
        ws_buckets.values(), key=lambda w: (-int(w["total"]), str(w["cwd"]).lower())
    )

    return {
        "version": VERSION,
        "autostart": _is_autostart_enabled(),
        "port": port if port is not None else get_default_port(),
        "root": str(get_mcas_root()),
        "providers": providers_out,
        "workspaces": workspaces,
        "storage": storage,
        "autosync": get_autosync_dict(),
    }
