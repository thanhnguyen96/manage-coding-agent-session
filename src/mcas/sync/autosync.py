"""Loop-free multi-provider auto-sync engine."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcas.config import _user_home
from mcas.models import (
    CanonicalSession,
    SessionSummary,
    canonical_session_hash,
)
from mcas.providers import get_supported_providers
from mcas.providers.base import AgentProvider
from mcas.sync.state import (
    _SYNC_LOCK,
    _append_history,
    _extract_pair_sides,
    _load_state,
    _save_state,
    _upsert_pair,
    get_autosync_dict,
    parse_direction_routes,
    update_autosync_config,
)
from mcas.util.pathconv import paths_match
from mcas.util.timefmt import format_iso_utc

__all__ = [
    "is_sync_running",
    "run_autosync_cycle",
    "get_autosync_dict",
    "update_autosync_config",
]

_SYNC_RUNNING = False


def is_sync_running() -> bool:
    """Return True if an auto-sync cycle is currently executing."""
    return _SYNC_RUNNING


def _read_with_fp_hash(
    prov: AgentProvider, summary: SessionSummary
) -> tuple[CanonicalSession, tuple[int, int], str]:
    sess = prov.read_session(summary.location, summary=summary)
    fp = prov.get_sync_fingerprint(summary)
    return sess, fp, canonical_session_hash(sess)


def _sync_one_pair_direction(
    *,
    src_prov: AgentProvider,
    tgt_prov: AgentProvider,
    src_sum: SessionSummary,
    tgt_id: str | None,
    overwrite: bool,
    include_thinking: bool,
    fallback_cwd: str,
    src_session: CanonicalSession | None = None,
    src_fp: tuple[int, int] | None = None,
    src_hash: str | None = None,
    state: dict[str, Any],
    kind: str,
) -> tuple[str, Path, bool]:
    """Perform a single directional copy and update ledger + history in `state`."""
    if src_session is None:
        src_session = src_prov.read_session(src_sum.location, summary=src_sum)
    if src_fp is None:
        src_fp = src_prov.get_sync_fingerprint(src_sum)
    if src_hash is None:
        src_hash = canonical_session_hash(src_session)

    target_cwd = src_session.cwd or src_sum.cwd or fallback_cwd
    new_tgt_id, out_path, regenerated = tgt_prov.write_session(
        src_session,
        target_cwd=target_cwd,
        new_id=False,
        overwrite=overwrite,
        include_thinking=include_thinking,
        forced_target_id=tgt_id if overwrite else None,
    )

    tgt_sum_stub = SessionSummary(
        session_id=new_tgt_id,
        source=tgt_prov.id,
        updated_at=src_session.updated_at,
        title=src_session.title,
        preview=src_session.preview,
        cwd=target_cwd,
        location=out_path,
        step_or_msg_count=0,
    )
    tgt_session = tgt_prov.read_session(out_path, summary=tgt_sum_stub)
    tgt_fp = tgt_prov.get_sync_fingerprint(tgt_sum_stub)
    tgt_hash = canonical_session_hash(tgt_session)

    now = datetime.now(timezone.utc)
    _upsert_pair(
        state=state,
        source=src_prov.id,
        source_id=src_session.session_id,
        target=tgt_prov.id,
        target_id=new_tgt_id,
        src_fp=src_fp,
        src_hash=src_hash,
        tgt_fp=tgt_fp,
        tgt_hash=tgt_hash,
        cwd=target_cwd,
        title=src_session.title,
        now=now,
    )
    resume_cmd = tgt_prov.resume_command(new_tgt_id)
    _append_history(
        state,
        kind=kind,
        direction=f"{src_prov.id}2{tgt_prov.id}",
        source=src_prov.id,
        target=tgt_prov.id,
        source_id=src_session.session_id,
        target_id=new_tgt_id,
        title=src_session.title,
        cwd=target_cwd,
        status="ok",
        message=f"Synced {len(src_session.turns)} turn(s) -> {tgt_prov.id}:{new_tgt_id[:8]}",
        resume_cmd=resume_cmd,
        now=now,
    )
    return new_tgt_id, out_path, regenerated


def _refresh_pair_endpoints(
    pair: dict[str, Any],
    endpoint_states: dict[tuple[str, str], tuple[tuple[int, int], str]],
) -> dict[str, Any]:
    """Update recorded fingerprint/hash on either side of `pair` from `endpoint_states`."""
    updated = dict(pair)
    src_key = (str(updated.get("source") or ""), str(updated.get("source_id") or ""))
    tgt_key = (str(updated.get("target") or ""), str(updated.get("target_id") or ""))
    if src_key in endpoint_states:
        fp, h = endpoint_states[src_key]
        updated["last_source_mtime_ns"] = fp[0]
        updated["last_source_count"] = fp[1]
        if h:
            updated["last_source_hash"] = h
    if tgt_key in endpoint_states:
        fp, h = endpoint_states[tgt_key]
        updated["last_target_mtime_ns"] = fp[0]
        updated["last_target_count"] = fp[1]
        if h:
            updated["last_target_hash"] = h
    return updated


def run_autosync_cycle(force_even_if_disabled: bool = False) -> dict[str, Any]:
    """Run one loop-free multi-provider auto-sync pass across all configured routes."""
    global _SYNC_RUNNING
    with _SYNC_LOCK:
        if _SYNC_RUNNING:
            return {
                "ran": False,
                "synced": 0,
                "skipped": 0,
                "errors": 0,
                "message": "Sync already in progress.",
            }
        _SYNC_RUNNING = True
        try:
            state = _load_state()
            cfg = state["config"]
            if not cfg["enabled"] and not force_even_if_disabled:
                return {
                    "ran": False,
                    "synced": 0,
                    "skipped": 0,
                    "errors": 0,
                    "message": "Auto-sync is disabled.",
                }

            _, routes = parse_direction_routes(cfg["direction"], strict=False)
            route_set = set(routes)
            scope = cfg["scope"]
            watched_folders = cfg["watchedFolders"]
            include_thinking = cfg["includeThinking"]

            provs: dict[str, AgentProvider] = {
                p.id: p for p in get_supported_providers()
            }
            maps: dict[str, dict[str, SessionSummary]] = {
                pid: {s.session_id: s for s in prov.list_sessions(include_all=False)}
                for pid, prov in provs.items()
            }

            def in_scope(summary: SessionSummary) -> bool:
                if summary.step_or_msg_count <= 0:
                    return False
                if scope == "watched":
                    if not summary.cwd:
                        return False
                    return any(paths_match(summary.cwd, wf) for wf in watched_folders)
                return True

            fallback_cwd = str(_user_home())
            paired_with: dict[tuple[str, str], set[str]] = {}
            adj: dict[tuple[str, str], set[tuple[str, str]]] = {}

            def mark_paired(p1: str, id1: str, p2: str, id2: str) -> None:
                paired_with.setdefault((p1, id1), set()).add(p2)
                paired_with.setdefault((p2, id2), set()).add(p1)
                adj.setdefault((p1, id1), set()).add((p2, id2))
                adj.setdefault((p2, id2), set()).add((p1, id1))

            def has_component_peer(
                start: tuple[str, str], target_prov: str
            ) -> tuple[str, str] | None:
                visited = {start}
                q: deque[tuple[str, str]] = deque([start])
                while q:
                    cur = q.popleft()
                    for nxt in adj.get(cur, ()):
                        if nxt in visited:
                            continue
                        visited.add(nxt)
                        if nxt[0] == target_prov and nxt[1] in maps.get(target_prov, {}):
                            return nxt
                        q.append(nxt)
                return None

            synced_count = 0
            unchanged_count = 0
            error_count = 0
            first_error: str | None = None
            new_pairs: list[dict[str, Any]] = []
            skipped_map: dict[str, Any] = dict(state.get("skipped", {}))

            live_fp_cache: dict[tuple[str, str], tuple[int, int]] = {}
            live_sess_cache: dict[
                tuple[str, str], tuple[CanonicalSession, tuple[int, int], str]
            ] = {}
            endpoint_states: dict[tuple[str, str], tuple[tuple[int, int], str]] = {}
            written_endpoints: set[tuple[str, str]] = set()
            edited_sources: set[tuple[str, str]] = set()
            synced_pair_keys: set[frozenset[tuple[str, str]]] = set()

            def get_live_fp(pid: str, summary: SessionSummary) -> tuple[int, int]:
                key = (pid, summary.session_id)
                if key not in live_fp_cache:
                    live_fp_cache[key] = provs[pid].get_sync_fingerprint(summary)
                return live_fp_cache[key]

            def get_live_read(
                pid: str, summary: SessionSummary
            ) -> tuple[CanonicalSession, tuple[int, int], str]:
                key = (pid, summary.session_id)
                if key not in live_sess_cache:
                    sess, fp, shash = _read_with_fp_hash(provs[pid], summary)
                    live_sess_cache[key] = (sess, fp, shash)
                    live_fp_cache[key] = fp
                return live_sess_cache[key]

            def record_sync_result(
                s_pid: str,
                s_id: str,
                t_pid: str,
                t_id: str,
                out_path: Path,
                base_summary: SessionSummary,
                target_cwd: str | None,
            ) -> dict[str, Any]:
                latest_pair = state["pairs"][-1]
                s_fp = (
                    int(latest_pair["last_source_mtime_ns"]),
                    int(latest_pair["last_source_count"]),
                )
                s_hash = str(latest_pair["last_source_hash"])
                t_fp = (
                    int(latest_pair["last_target_mtime_ns"]),
                    int(latest_pair["last_target_count"]),
                )
                t_hash = str(latest_pair["last_target_hash"])
                endpoint_states[(s_pid, s_id)] = (s_fp, s_hash)
                endpoint_states[(t_pid, t_id)] = (t_fp, t_hash)
                edited_sources.add((s_pid, s_id))
                written_endpoints.add((t_pid, t_id))
                synced_pair_keys.add(frozenset({(s_pid, s_id), (t_pid, t_id)}))
                live_fp_cache[(t_pid, t_id)] = t_fp
                live_sess_cache.pop((t_pid, t_id), None)
                maps[t_pid][t_id] = SessionSummary(
                    session_id=t_id,
                    source=t_pid,
                    updated_at=base_summary.updated_at,
                    title=base_summary.title,
                    preview=base_summary.preview,
                    cwd=target_cwd,
                    location=out_path,
                    step_or_msg_count=t_fp[1],
                )
                return latest_pair

            # 1a. Prune dead pairs and recover missing-side pairs
            live_pairs: list[
                tuple[
                    dict[str, Any],
                    str,
                    str,
                    tuple[int, int],
                    str,
                    str,
                    str,
                    tuple[int, int],
                    str,
                ]
            ] = []
            for pair in list(state["pairs"]):
                sides = _extract_pair_sides(pair)
                if sides is None:
                    continue
                p1, id1, fp1_rec, h1_rec, p2, id2, fp2_rec, h2_rec = sides
                s1 = maps[p1].get(id1)
                s2 = maps[p2].get(id2)

                if s1 is None and s2 is None:
                    continue

                if s1 is None or s2 is None:
                    surv_p, surv_id, surv_sum = (p1, id1, s1) if s1 is not None else (p2, id2, s2)
                    miss_p, miss_id = (p2, id2) if s1 is not None else (p1, id1)
                    if (
                        surv_sum is not None
                        and (surv_p, miss_p) in route_set
                        and in_scope(surv_sum)
                    ):
                        try:
                            new_id, out_path, _ = _sync_one_pair_direction(
                                src_prov=provs[surv_p],
                                tgt_prov=provs[miss_p],
                                src_sum=surv_sum,
                                tgt_id=miss_id,
                                overwrite=True,
                                include_thinking=include_thinking,
                                fallback_cwd=fallback_cwd,
                                state=state,
                                kind="auto",
                            )
                            synced_count += 1
                            mark_paired(surv_p, surv_id, miss_p, new_id)
                            new_pairs.append(
                                record_sync_result(
                                    surv_p,
                                    surv_id,
                                    miss_p,
                                    new_id,
                                    out_path,
                                    surv_sum,
                                    surv_sum.cwd or fallback_cwd,
                                )
                            )
                        except Exception as exc:
                            error_count += 1
                            first_error = first_error or str(exc)
                            paired_with.setdefault((surv_p, surv_id), set()).add(miss_p)
                            new_pairs.append(pair)
                            _append_history(
                                state,
                                kind="auto",
                                direction=f"{surv_p}2{miss_p}",
                                source=surv_p,
                                target=miss_p,
                                source_id=surv_id,
                                target_id=miss_id,
                                title=surv_sum.title,
                                cwd=surv_sum.cwd,
                                status="error",
                                message=str(exc),
                            )
                    else:
                        paired_with.setdefault((surv_p, surv_id), set()).add(miss_p)
                        new_pairs.append(pair)
                    continue

                mark_paired(p1, id1, p2, id2)
                live_pairs.append((pair, p1, id1, fp1_rec, h1_rec, p2, id2, fp2_rec, h2_rec))

            # 1b. Evaluate existing live pairs
            for pair, p1, id1, fp1_rec, h1_rec, p2, id2, fp2_rec, h2_rec in live_pairs:
                s1 = maps[p1][id1]
                s2 = maps[p2][id2]
                fp1 = endpoint_states[(p1, id1)][0] if (p1, id1) in written_endpoints else get_live_fp(p1, s1)
                fp2 = endpoint_states[(p2, id2)][0] if (p2, id2) in written_endpoints else get_live_fp(p2, s2)

                c1 = (fp1[0] > fp1_rec[0]) or (fp1[1] != fp1_rec[1])
                c2 = (fp2[0] > fp2_rec[0]) or (fp2[1] != fp2_rec[1])

                if not c1 and not c2:
                    unchanged_count += 1
                    new_pairs.append(pair)
                    continue

                if (p1, id1) in written_endpoints and (p2, id2) in written_endpoints:
                    new_pairs.append(_refresh_pair_endpoints(pair, endpoint_states))
                    unchanged_count += 1
                    continue

                sess1: CanonicalSession | None = None
                h1 = h1_rec
                if c1 and (p1, id1) not in written_endpoints:
                    try:
                        sess1, fp1, h1 = get_live_read(p1, s1)
                        if h1_rec and h1 == h1_rec:
                            c1 = False
                            endpoint_states[(p1, id1)] = (fp1, h1)
                    except Exception as exc:
                        error_count += 1
                        first_error = first_error or str(exc)
                        c1 = False
                elif (p1, id1) in written_endpoints:
                    fp1, h1 = endpoint_states[(p1, id1)]

                sess2: CanonicalSession | None = None
                h2 = h2_rec
                if c2 and (p2, id2) not in written_endpoints:
                    try:
                        sess2, fp2, h2 = get_live_read(p2, s2)
                        if h2_rec and h2 == h2_rec:
                            c2 = False
                            endpoint_states[(p2, id2)] = (fp2, h2)
                    except Exception as exc:
                        error_count += 1
                        first_error = first_error or str(exc)
                        c2 = False
                elif (p2, id2) in written_endpoints:
                    fp2, h2 = endpoint_states[(p2, id2)]

                if not c1 and not c2:
                    updated_pair = dict(pair)
                    updated_pair["last_source_mtime_ns"] = fp1[0]
                    updated_pair["last_source_count"] = fp1[1]
                    updated_pair["last_target_mtime_ns"] = fp2[0]
                    updated_pair["last_target_count"] = fp2[1]
                    new_pairs.append(updated_pair)
                    unchanged_count += 1
                    continue

                if c1 and c2 and h1 and h2 and h1 == h2:
                    updated_pair = dict(pair)
                    updated_pair["last_source_mtime_ns"] = fp1[0]
                    updated_pair["last_source_count"] = fp1[1]
                    updated_pair["last_source_hash"] = h1
                    updated_pair["last_target_mtime_ns"] = fp2[0]
                    updated_pair["last_target_count"] = fp2[1]
                    updated_pair["last_target_hash"] = h2
                    new_pairs.append(updated_pair)
                    unchanged_count += 1
                    continue

                chosen: tuple[str, str, SessionSummary, CanonicalSession | None, tuple[int, int], str, str, str] | None = None
                if c1 and c2:
                    allow_12 = (p1, p2) in route_set and in_scope(s1)
                    allow_21 = (p2, p1) in route_set and in_scope(s2)
                    if allow_12 and allow_21:
                        if fp1[0] >= fp2[0]:
                            chosen = (p1, id1, s1, sess1, fp1, h1, p2, id2)
                        else:
                            chosen = (p2, id2, s2, sess2, fp2, h2, p1, id1)
                    elif allow_12:
                        chosen = (p1, id1, s1, sess1, fp1, h1, p2, id2)
                    elif allow_21:
                        chosen = (p2, id2, s2, sess2, fp2, h2, p1, id1)
                elif c1 and (p1, p2) in route_set and in_scope(s1):
                    chosen = (p1, id1, s1, sess1, fp1, h1, p2, id2)
                elif c2 and (p2, p1) in route_set and in_scope(s2):
                    chosen = (p2, id2, s2, sess2, fp2, h2, p1, id1)

                if chosen is None:
                    new_pairs.append(pair)
                    unchanged_count += 1
                    continue

                s_pid, s_id, s_sum, s_sess, s_fp, s_hash, t_pid, t_id = chosen
                try:
                    new_tgt_id, out_path, _ = _sync_one_pair_direction(
                        src_prov=provs[s_pid],
                        tgt_prov=provs[t_pid],
                        src_sum=s_sum,
                        tgt_id=t_id,
                        overwrite=True,
                        include_thinking=include_thinking,
                        fallback_cwd=fallback_cwd,
                        src_session=s_sess,
                        src_fp=s_fp,
                        src_hash=s_hash,
                        state=state,
                        kind="auto",
                    )
                    synced_count += 1
                    new_pairs.append(
                        record_sync_result(
                            s_pid,
                            s_id,
                            t_pid,
                            new_tgt_id,
                            out_path,
                            s_sum,
                            s_sum.cwd or fallback_cwd,
                        )
                    )
                except Exception as exc:
                    error_count += 1
                    first_error = first_error or str(exc)
                    new_pairs.append(pair)
                    _append_history(
                        state,
                        kind="auto",
                        direction=f"{s_pid}2{t_pid}",
                        source=s_pid,
                        target=t_pid,
                        source_id=s_id,
                        target_id=t_id,
                        title=s_sum.title,
                        cwd=s_sum.cwd,
                        status="error",
                        message=str(exc),
                    )

            # 1c. Propagate written endpoints across any earlier-evaluated hub pairs and refresh hashes
            if written_endpoints:
                refreshed_pairs: list[dict[str, Any]] = []
                for pair in new_pairs:
                    sides = _extract_pair_sides(pair)
                    if sides is None:
                        refreshed_pairs.append(pair)
                        continue
                    p1, id1, _fp1, _h1, p2, id2, _fp2, _h2 = sides
                    pkey = frozenset({(p1, id1), (p2, id2)})
                    if pkey in synced_pair_keys:
                        refreshed_pairs.append(_refresh_pair_endpoints(pair, endpoint_states))
                        continue
                    w1 = (p1, id1) in written_endpoints
                    w2 = (p2, id2) in written_endpoints
                    prop_dir: tuple[str, str, str, str] | None = None
                    if w1 and not w2 and (p2, id2) not in edited_sources:
                        prop_dir = (p1, id1, p2, id2)
                    elif w2 and not w1 and (p1, id1) not in edited_sources:
                        prop_dir = (p2, id2, p1, id1)

                    if (
                        prop_dir is not None
                        and (prop_dir[0], prop_dir[2]) in route_set
                        and prop_dir[1] in maps.get(prop_dir[0], {})
                        and prop_dir[3] in maps.get(prop_dir[2], {})
                    ):
                        sp, sid_p, tp, tid_p = prop_dir
                        src_sum_p = maps[sp][sid_p]
                        if in_scope(src_sum_p):
                            try:
                                new_tgt_id, out_path, _ = _sync_one_pair_direction(
                                    src_prov=provs[sp],
                                    tgt_prov=provs[tp],
                                    src_sum=src_sum_p,
                                    tgt_id=tid_p,
                                    overwrite=True,
                                    include_thinking=include_thinking,
                                    fallback_cwd=fallback_cwd,
                                    state=state,
                                    kind="auto",
                                )
                                synced_count += 1
                                refreshed_pairs.append(
                                    record_sync_result(
                                        sp,
                                        sid_p,
                                        tp,
                                        new_tgt_id,
                                        out_path,
                                        src_sum_p,
                                        src_sum_p.cwd or fallback_cwd,
                                    )
                                )
                                continue
                            except Exception as exc:
                                error_count += 1
                                first_error = first_error or str(exc)
                    refreshed_pairs.append(_refresh_pair_endpoints(pair, endpoint_states))
                new_pairs = refreshed_pairs

            state["pairs"] = new_pairs

            def link_component_pair(
                src_pid: str, src_sid: str, tgt_pid: str, tgt_sid: str
            ) -> bool:
                """Link two already-connected endpoints in `state['pairs']` without writing files."""
                try:
                    s_sum = maps[src_pid][src_sid]
                    t_sum = maps[tgt_pid][tgt_sid]
                    s_sess, s_fp, s_hash = get_live_read(src_pid, s_sum)
                    t_sess, t_fp, t_hash = get_live_read(tgt_pid, t_sum)
                    _upsert_pair(
                        state=state,
                        source=src_pid,
                        source_id=src_sid,
                        target=tgt_pid,
                        target_id=tgt_sid,
                        src_fp=s_fp,
                        src_hash=s_hash,
                        tgt_fp=t_fp,
                        tgt_hash=t_hash,
                        cwd=s_sess.cwd or t_sess.cwd or fallback_cwd,
                        title=s_sess.title or t_sess.title,
                        now=datetime.now(timezone.utc),
                    )
                    mark_paired(src_pid, src_sid, tgt_pid, tgt_sid)
                    return True
                except Exception:
                    return False

            # 2. Pre-existing unpaired sessions sharing the same UUID across enabled provider pairs
            prov_ids = list(provs.keys())
            for i, p1 in enumerate(prov_ids):
                for p2 in prov_ids[i + 1 :]:
                    allow_12 = (p1, p2) in route_set
                    allow_21 = (p2, p1) in route_set
                    if not (allow_12 or allow_21):
                        continue
                    shared_ids = sorted(set(maps[p1].keys()) & set(maps[p2].keys()))
                    for sid in shared_ids:
                        if p2 in paired_with.get((p1, sid), set()) or p1 in paired_with.get((p2, sid), set()):
                            continue
                        s1 = maps[p1][sid]
                        s2 = maps[p2][sid]
                        if not (in_scope(s1) or in_scope(s2)):
                            continue
                        existing_peer = has_component_peer((p1, sid), p2)
                        if existing_peer is not None:
                            if link_component_pair(p1, sid, p2, existing_peer[1]):
                                unchanged_count += 1
                            continue
                        mark_paired(p1, sid, p2, sid)
                        try:
                            sess1, fp1, h1 = get_live_read(p1, s1)
                            sess2, fp2, h2 = get_live_read(p2, s2)
                            if h1 == h2:
                                _upsert_pair(
                                    state=state,
                                    source=p1,
                                    source_id=sid,
                                    target=p2,
                                    target_id=sid,
                                    src_fp=fp1,
                                    src_hash=h1,
                                    tgt_fp=fp2,
                                    tgt_hash=h2,
                                    cwd=sess1.cwd or sess2.cwd or fallback_cwd,
                                    title=sess1.title or sess2.title,
                                    now=datetime.now(timezone.utc),
                                )
                                unchanged_count += 1
                            elif allow_12 and (not allow_21 or s1.updated_at >= s2.updated_at):
                                new_id, out_path, _ = _sync_one_pair_direction(
                                    src_prov=provs[p1],
                                    tgt_prov=provs[p2],
                                    src_sum=s1,
                                    tgt_id=sid,
                                    overwrite=True,
                                    include_thinking=include_thinking,
                                    fallback_cwd=fallback_cwd,
                                    src_session=sess1,
                                    src_fp=fp1,
                                    src_hash=h1,
                                    state=state,
                                    kind="auto",
                                )
                                record_sync_result(p1, sid, p2, new_id, out_path, s1, s1.cwd or fallback_cwd)
                                synced_count += 1
                            elif allow_21:
                                new_id, out_path, _ = _sync_one_pair_direction(
                                    src_prov=provs[p2],
                                    tgt_prov=provs[p1],
                                    src_sum=s2,
                                    tgt_id=sid,
                                    overwrite=True,
                                    include_thinking=include_thinking,
                                    fallback_cwd=fallback_cwd,
                                    src_session=sess2,
                                    src_fp=fp2,
                                    src_hash=h2,
                                    state=state,
                                    kind="auto",
                                )
                                record_sync_result(p2, sid, p1, new_id, out_path, s2, s2.cwd or fallback_cwd)
                                synced_count += 1
                        except Exception as exc:
                            error_count += 1
                            first_error = first_error or str(exc)

            # 3. Discover brand-new unpaired sessions across enabled directed routes
            for src_id, tgt_id in routes:
                src_prov = provs[src_id]
                tgt_prov = provs[tgt_id]
                for sid, summary in list(maps[src_id].items()):
                    if tgt_id in paired_with.get((src_id, sid), set()):
                        continue
                    if not in_scope(summary):
                        continue

                    existing_peer = has_component_peer((src_id, sid), tgt_id)
                    if existing_peer is not None:
                        link_component_pair(src_id, sid, tgt_id, existing_peer[1])
                        continue

                    fp_now = get_live_fp(src_id, summary)
                    skip_key = f"{src_id}:{sid}->{tgt_id}"
                    prev_skip = skipped_map.get(skip_key)
                    if (
                        isinstance(prev_skip, dict)
                        and prev_skip.get("mtime_ns") == fp_now[0]
                        and prev_skip.get("count") == fp_now[1]
                    ):
                        unchanged_count += 1
                        continue

                    try:
                        new_tgt_id, out_path, _ = _sync_one_pair_direction(
                            src_prov=src_prov,
                            tgt_prov=tgt_prov,
                            src_sum=summary,
                            tgt_id=None,
                            overwrite=False,
                            include_thinking=include_thinking,
                            fallback_cwd=fallback_cwd,
                            state=state,
                            kind="auto",
                        )
                        mark_paired(src_id, sid, tgt_id, new_tgt_id)
                        record_sync_result(
                            src_id,
                            sid,
                            tgt_id,
                            new_tgt_id,
                            out_path,
                            summary,
                            summary.cwd or fallback_cwd,
                        )
                        skipped_map.pop(skip_key, None)
                        synced_count += 1
                    except Exception as exc:
                        skipped_map[skip_key] = {
                            "mtime_ns": fp_now[0],
                            "count": fp_now[1],
                            "error": str(exc),
                        }
                        error_count += 1
                        first_error = first_error or str(exc)
                        _append_history(
                            state,
                            kind="auto",
                            direction=f"{src_id}2{tgt_id}",
                            source=src_id,
                            target=tgt_id,
                            source_id=sid,
                            target_id="",
                            title=summary.title,
                            cwd=summary.cwd,
                            status="error",
                            message=str(exc),
                        )

            state["skipped"] = skipped_map
            now = datetime.now(timezone.utc)
            state["runtime"]["lastRunAt"] = format_iso_utc(now)
            state["runtime"]["lastError"] = first_error
            _save_state(state)

            msg = (
                f"Auto-sync: {synced_count} synced, {unchanged_count} unchanged"
                + (f", {error_count} error(s)" if error_count else "")
                + "."
            )
            return {
                "ran": True,
                "synced": synced_count,
                "unchanged": unchanged_count,
                "errors": error_count,
                "message": msg,
            }
        finally:
            _SYNC_RUNNING = False
