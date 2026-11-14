from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os

from mcas.errors import SyncError
from mcas.models import (
    SessionSummary,
    canonical_session_hash,
)
from mcas.providers import (
    get_provider,
    get_supported_providers,
)
from mcas.providers.base import AgentProvider
from mcas.sync.state import (
    _SYNC_LOCK,
    _append_history,
    _load_state,
    _pair_matches_endpoint,
    _save_state,
    _upsert_pair,
)
from mcas.util.pathconv import normalize_windows_path

def _resolve_source_for_copy(
    id_or_latest: str,
    from_provider_id: str | None,
    to_provider_id: str,
    cwd: str | None,
    all_cwd: bool,
) -> tuple[AgentProvider, Path, SessionSummary | None]:
    if from_provider_id:
        prov = get_provider(from_provider_id, require_supported=True)
        loc, summary = prov.find_session(id_or_latest, cwd=cwd, all_cwd=all_cwd)
        return prov, loc, summary

    supported = get_supported_providers()
    if id_or_latest.lower() == "latest":
        candidates: list[tuple[AgentProvider, Path, SessionSummary]] = []
        pool = [p for p in supported if p.id != to_provider_id] or supported
        target_cwd = None if all_cwd else normalize_windows_path(cwd or os.getcwd())
        for prov in pool:
            items = prov.list_sessions(include_all=False, cwd_filter=target_cwd)
            if items:
                candidates.append((prov, items[0].location, items[0]))
        if not candidates:
            scope = "any workspace" if all_cwd else f"workspace '{target_cwd}'"
            raise SyncError(f"No conversation found for {scope}.")
        candidates.sort(key=lambda item: item[2].updated_at, reverse=True)
        prov, loc, summ = candidates[0]
        return prov, loc, summ

    found: list[tuple[AgentProvider, Path, SessionSummary | None]] = []
    for prov in supported:
        try:
            loc, summ = prov.find_session(id_or_latest, cwd=cwd, all_cwd=all_cwd)
            found.append((prov, loc, summ))
        except SyncError:
            pass
    if not found:
        raise SyncError(f"Session '{id_or_latest}' not found in any supported provider.")
    if len(found) == 1:
        return found[0]
    other = [f for f in found if f[0].id != to_provider_id]
    if len(other) == 1:
        return other[0]
    names = ", ".join(f[0].id for f in found)
    raise SyncError(
        f"Session '{id_or_latest}' exists in multiple providers ({names}). Pass --from."
    )

def copy_one_session(
    *,
    id_or_latest: str,
    to_provider_id: str,
    from_provider_id: str | None = None,
    cwd: str | None = None,
    target_cwd: str | None = None,
    all_cwd: bool = False,
    new_id: bool = False,
    overwrite: bool = False,
    include_thinking: bool = False,
    kind: str = "manual",
) -> dict[str, Any]:
    to_prov = get_provider(to_provider_id, require_supported=True)
    src_prov, loc, summary = _resolve_source_for_copy(
        id_or_latest=id_or_latest,
        from_provider_id=from_provider_id,
        to_provider_id=to_prov.id,
        cwd=cwd,
        all_cwd=all_cwd,
    )
    session = src_prov.read_session(loc, summary=summary)
    if src_prov.id == to_prov.id and not new_id and not overwrite and not target_cwd:
        raise SyncError(
            f"Source and target provider are both '{to_prov.id}'. "
            "Use --new-id or --target-cwd to clone."
        )

    effective_cwd = (
        normalize_windows_path(target_cwd)
        if target_cwd
        else (
            normalize_windows_path(cwd)
            if cwd
            else (session.cwd or normalize_windows_path(os.getcwd()))
        )
    )
    src_fp = src_prov.get_sync_fingerprint(
        summary
        or SessionSummary(
            session_id=session.session_id,
            source=src_prov.id,
            updated_at=session.updated_at,
            title=session.title,
            preview=session.preview,
            cwd=session.cwd,
            location=loc,
            step_or_msg_count=len(session.turns),
        )
    )
    src_hash = canonical_session_hash(session)

    # Check if already paired when overwrite=True and source/target IDs differ
    forced_target_id: str | None = None
    if overwrite and not new_id and src_prov.id != to_prov.id:
        with _SYNC_LOCK:
            state = _load_state()
            for p in state["pairs"]:
                if p.get("source") == src_prov.id and p.get("source_id") == session.session_id and p.get("target") == to_prov.id:
                    forced_target_id = p.get("target_id")
                    break
                if p.get("target") == src_prov.id and p.get("target_id") == session.session_id and p.get("source") == to_prov.id:
                    forced_target_id = p.get("source_id")
                    break

    tgt_id, out_path, regenerated = to_prov.write_session(
        session,
        target_cwd=effective_cwd,
        new_id=new_id,
        overwrite=overwrite,
        include_thinking=include_thinking,
        forced_target_id=forced_target_id,
    )

    if src_prov.id != to_prov.id:
        tgt_stub = SessionSummary(
            session_id=tgt_id,
            source=to_prov.id,
            updated_at=session.updated_at,
            title=session.title,
            preview=session.preview,
            cwd=effective_cwd,
            location=out_path,
            step_or_msg_count=0,
        )
        tgt_session = to_prov.read_session(out_path, summary=tgt_stub)
        tgt_fp = to_prov.get_sync_fingerprint(tgt_stub)
        tgt_hash = canonical_session_hash(tgt_session)

        with _SYNC_LOCK:
            state = _load_state()
            now = datetime.now(timezone.utc)
            already_paired_elsewhere = any(
                _pair_matches_endpoint(p, src_prov.id, session.session_id)
                and not _pair_matches_endpoint(p, to_prov.id, tgt_id)
                for p in state["pairs"]
            )
            if not (new_id and already_paired_elsewhere):
                _upsert_pair(
                    state=state,
                    source=src_prov.id,
                    source_id=session.session_id,
                    target=to_prov.id,
                    target_id=tgt_id,
                    src_fp=src_fp,
                    src_hash=src_hash,
                    tgt_fp=tgt_fp,
                    tgt_hash=tgt_hash,
                    cwd=effective_cwd,
                    title=session.title,
                    now=now,
                )
            _append_history(
                state,
                kind=kind,
                direction=f"{src_prov.id}2{to_prov.id}",
                source=src_prov.id,
                target=to_prov.id,
                source_id=session.session_id,
                target_id=tgt_id,
                title=session.title,
                cwd=effective_cwd,
                status="ok",
                message=(
                    f"Copied {len(session.turns)} turn(s)"
                    + (" (new UUID on collision)" if regenerated else "")
                ),
                resume_cmd=to_prov.resume_command(tgt_id),
                now=now,
            )
            _save_state(state)

    resume_cmd = to_prov.resume_command(tgt_id)
    return {
        "ok": True,
        "status": "ok",
        "source": src_prov.id,
        "sourceId": session.session_id,
        "toProvider": to_prov.id,
        "targetId": tgt_id,
        "targetCwd": effective_cwd,
        "title": session.title,
        "path": str(out_path),
        "regenerated": regenerated,
        "resumeCmd": resume_cmd,
        "resumeDir": effective_cwd,
        "message": f"Copied {src_prov.id}:{session.session_id[:8]} -> {to_prov.id}:{tgt_id[:8]}",
        "error": None,
    }

def sync_directory_sessions(
    *,
    from_provider_id: str,
    to_provider_id: str,
    cwd: str,
    target_cwd: str | None = None,
    overwrite: bool = False,
    include_thinking: bool = False,
) -> list[dict[str, Any]]:
    from_prov = get_provider(from_provider_id, require_supported=True)
    to_prov = get_provider(to_provider_id, require_supported=True)
    if from_prov.id == to_prov.id:
        raise SyncError("Source and target providers for sync-dir must be different.")

    norm_src_cwd = normalize_windows_path(cwd)
    norm_tgt_cwd = normalize_windows_path(target_cwd) if target_cwd else norm_src_cwd

    src_sessions = from_prov.list_sessions(include_all=False, cwd_filter=norm_src_cwd)
    tgt_ids = {s.session_id for s in to_prov.list_sessions(include_all=True)}

    with _SYNC_LOCK:
        state = _load_state()
        pair_map: dict[str, str] = {}
        for p in state["pairs"]:
            if p.get("source") == from_prov.id and p.get("target") == to_prov.id:
                pair_map[str(p.get("source_id"))] = str(p.get("target_id"))
            elif p.get("target") == from_prov.id and p.get("source") == to_prov.id:
                pair_map[str(p.get("target_id"))] = str(p.get("source_id"))

    results: list[dict[str, Any]] = []
    for summary in src_sessions:
        existing_tgt_id = pair_map.get(summary.session_id)
        target_exists = (
            (existing_tgt_id in tgt_ids)
            if existing_tgt_id
            else (summary.session_id in tgt_ids)
        )
        if target_exists and not overwrite:
            known_id = existing_tgt_id or summary.session_id
            results.append(
                {
                    "ok": True,
                    "status": "skipped",
                    "source": from_prov.id,
                    "sourceId": summary.session_id,
                    "toProvider": to_prov.id,
                    "targetId": known_id,
                    "targetCwd": norm_tgt_cwd,
                    "title": summary.title,
                    "path": None,
                    "regenerated": False,
                    "resumeCmd": to_prov.resume_command(known_id),
                    "resumeDir": norm_tgt_cwd,
                    "message": "Already exists in target (skipped without --overwrite).",
                    "error": None,
                }
            )
            continue
        try:
            res = copy_one_session(
                id_or_latest=summary.session_id,
                from_provider_id=from_prov.id,
                to_provider_id=to_prov.id,
                cwd=norm_src_cwd,
                target_cwd=norm_tgt_cwd,
                all_cwd=True,
                new_id=False,
                overwrite=overwrite,
                include_thinking=include_thinking,
                kind="manual",
            )
            results.append(res)
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "status": "error",
                    "source": from_prov.id,
                    "sourceId": summary.session_id,
                    "toProvider": to_prov.id,
                    "targetId": None,
                    "targetCwd": norm_tgt_cwd,
                    "title": summary.title,
                    "path": None,
                    "regenerated": False,
                    "resumeCmd": None,
                    "resumeDir": norm_tgt_cwd,
                    "message": str(exc),
                    "error": str(exc),
                }
            )
    return results
