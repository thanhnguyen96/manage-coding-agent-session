from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcas.config import _user_home
from mcas.models import (
    CanonicalSession,
    SessionSummary,
    canonical_session_hash,
)
from mcas.providers import get_provider
from mcas.providers.base import AgentProvider
from mcas.sync.state import (
    _SYNC_LOCK,
    _append_history,
    _extract_pair_sides,
    _load_state,
    _save_state,
    _upsert_pair,
    get_autosync_dict,
    update_autosync_config,
)

__all__ = [
    "is_sync_running",
    "run_autosync_cycle",
    "get_autosync_dict",
    "update_autosync_config",
]
from mcas.util.pathconv import paths_match
from mcas.util.timefmt import format_iso_utc

_SYNC_RUNNING = False


def is_sync_running() -> bool:
    """Return True if an auto-sync cycle is currently executing."""
    return _SYNC_RUNNING

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

def run_autosync_cycle(force_even_if_disabled: bool = False) -> dict[str, Any]:
    """Run one loop-free bidirectional auto-sync pass."""
    global _SYNC_RUNNING
    with _SYNC_LOCK:
        if _SYNC_RUNNING:
            return {"ran": False, "synced": 0, "skipped": 0, "errors": 0, "message": "Sync already in progress."}
        _SYNC_RUNNING = True
        try:
            state = _load_state()
            cfg = state["config"]
            if not cfg["enabled"] and not force_even_if_disabled:
                return {"ran": False, "synced": 0, "skipped": 0, "errors": 0, "message": "Auto-sync is disabled."}

            direction = cfg["direction"]
            scope = cfg["scope"]
            watched_folders = cfg["watchedFolders"]
            include_thinking = cfg["includeThinking"]

            agy_prov = get_provider("agy")
            claude_prov = get_provider("claude")

            agy_list = agy_prov.list_sessions(include_all=False)
            claude_list = claude_prov.list_sessions(include_all=False)
            agy_map = {s.session_id: s for s in agy_list}
            claude_map = {s.session_id: s for s in claude_list}

            def in_scope(summary: SessionSummary) -> bool:
                if summary.step_or_msg_count <= 0:
                    return False
                if scope == "watched":
                    if not summary.cwd:
                        return False
                    return any(paths_match(summary.cwd, wf) for wf in watched_folders)
                return True

            fallback_cwd = str(_user_home())
            paired_agy: set[str] = set()
            paired_claude: set[str] = set()
            synced_count = 0
            unchanged_count = 0
            error_count = 0
            first_error: str | None = None
            new_pairs: list[dict[str, Any]] = []
            skipped_map: dict[str, Any] = dict(state.get("skipped", {}))

            # 1. Evaluate existing pairs in ledger
            for pair in list(state["pairs"]):
                sides = _extract_pair_sides(pair)
                if sides is None:
                    continue
                agy_id, agy_rec_fp, agy_rec_hash, claude_id, claude_rec_fp, claude_rec_hash = sides
                agy_sum = agy_map.get(agy_id)
                claude_sum = claude_map.get(claude_id)

                if agy_sum is None and claude_sum is None:
                    # Both deleted on disk -> prune stale pair
                    continue

                if agy_sum is not None:
                    paired_agy.add(agy_id)
                if claude_sum is not None:
                    paired_claude.add(claude_id)

                # If one side was removed externally, keep pair only if other side shouldn't sync,
                # or let surviving side re-sync if in scope and direction allows.
                if agy_sum is None or claude_sum is None:
                    surviving_src = agy_prov if agy_sum is not None else claude_prov
                    surviving_sum = agy_sum if agy_sum is not None else claude_sum
                    missing_tgt = claude_prov if agy_sum is not None else agy_prov
                    missing_tgt_id = claude_id if agy_sum is not None else agy_id
                    allowed = direction == "both" or direction == f"{surviving_src.id}2{missing_tgt.id}"
                    if surviving_sum and allowed and in_scope(surviving_sum):
                        try:
                            _sync_one_pair_direction(
                                src_prov=surviving_src,
                                tgt_prov=missing_tgt,
                                src_sum=surviving_sum,
                                tgt_id=missing_tgt_id,
                                overwrite=True,
                                include_thinking=include_thinking,
                                fallback_cwd=fallback_cwd,
                                state=state,
                                kind="auto",
                            )
                            synced_count += 1
                            new_pairs.append(state["pairs"][-1])
                        except Exception as exc:
                            error_count += 1
                            first_error = first_error or str(exc)
                            new_pairs.append(pair)
                            _append_history(
                                state,
                                kind="auto",
                                direction=f"{surviving_src.id}2{missing_tgt.id}",
                                source=surviving_src.id,
                                target=missing_tgt.id,
                                source_id=surviving_sum.session_id,
                                target_id=missing_tgt_id,
                                title=surviving_sum.title,
                                cwd=surviving_sum.cwd,
                                status="error",
                                message=str(exc),
                            )
                    else:
                        new_pairs.append(pair)
                    continue

                # Both sides exist on disk -> check fingerprints
                agy_fp = agy_prov.get_sync_fingerprint(agy_sum)
                claude_fp = claude_prov.get_sync_fingerprint(claude_sum)

                agy_changed = (agy_fp[0] > agy_rec_fp[0]) or (agy_fp[1] != agy_rec_fp[1])
                claude_changed = (claude_fp[0] > claude_rec_fp[0]) or (claude_fp[1] != claude_rec_fp[1])

                if not agy_changed and not claude_changed:
                    unchanged_count += 1
                    new_pairs.append(pair)
                    continue

                agy_sess: CanonicalSession | None = None
                agy_hash = agy_rec_hash
                if agy_changed:
                    try:
                        agy_sess = agy_prov.read_session(agy_sum.location, summary=agy_sum)
                        agy_fp = agy_prov.get_sync_fingerprint(agy_sum)
                        agy_hash = canonical_session_hash(agy_sess)
                        if agy_rec_hash and agy_hash == agy_rec_hash:
                            agy_changed = False
                    except Exception as exc:
                        error_count += 1
                        first_error = first_error or str(exc)
                        agy_changed = False

                claude_sess: CanonicalSession | None = None
                claude_hash = claude_rec_hash
                if claude_changed:
                    try:
                        claude_sess = claude_prov.read_session(claude_sum.location, summary=claude_sum)
                        claude_fp = claude_prov.get_sync_fingerprint(claude_sum)
                        claude_hash = canonical_session_hash(claude_sess)
                        if claude_rec_hash and claude_hash == claude_rec_hash:
                            claude_changed = False
                    except Exception as exc:
                        error_count += 1
                        first_error = first_error or str(exc)
                        claude_changed = False

                # Update metadata fingerprint in pair even if hashes matched (no write needed)
                if not agy_changed and not claude_changed:
                    updated_pair = dict(pair)
                    if updated_pair.get("source") == "agy":
                        updated_pair["last_source_mtime_ns"] = agy_fp[0]
                        updated_pair["last_source_count"] = agy_fp[1]
                        updated_pair["last_target_mtime_ns"] = claude_fp[0]
                        updated_pair["last_target_count"] = claude_fp[1]
                    else:
                        updated_pair["last_source_mtime_ns"] = claude_fp[0]
                        updated_pair["last_source_count"] = claude_fp[1]
                        updated_pair["last_target_mtime_ns"] = agy_fp[0]
                        updated_pair["last_target_count"] = agy_fp[1]
                    new_pairs.append(updated_pair)
                    unchanged_count += 1
                    continue

                # Determine winning sync direction
                chosen_dir: tuple[AgentProvider, AgentProvider, SessionSummary, CanonicalSession | None, tuple[int, int], str, str] | None = None
                if agy_changed and claude_changed:
                    if direction == "agy2claude":
                        chosen_dir = (agy_prov, claude_prov, agy_sum, agy_sess, agy_fp, agy_hash, claude_id)
                    elif direction == "claude2agy":
                        chosen_dir = (claude_prov, agy_prov, claude_sum, claude_sess, claude_fp, claude_hash, agy_id)
                    else:
                        if agy_fp[0] >= claude_fp[0]:
                            chosen_dir = (agy_prov, claude_prov, agy_sum, agy_sess, agy_fp, agy_hash, claude_id)
                        else:
                            chosen_dir = (claude_prov, agy_prov, claude_sum, claude_sess, claude_fp, claude_hash, agy_id)
                elif agy_changed and direction in ("both", "agy2claude") and in_scope(agy_sum):
                    chosen_dir = (agy_prov, claude_prov, agy_sum, agy_sess, agy_fp, agy_hash, claude_id)
                elif claude_changed and direction in ("both", "claude2agy") and in_scope(claude_sum):
                    chosen_dir = (claude_prov, agy_prov, claude_sum, claude_sess, claude_fp, claude_hash, agy_id)

                if chosen_dir is None:
                    new_pairs.append(pair)
                    unchanged_count += 1
                    continue

                s_prov, t_prov, s_sum, s_sess, s_fp, s_hash, t_id = chosen_dir
                try:
                    _sync_one_pair_direction(
                        src_prov=s_prov,
                        tgt_prov=t_prov,
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
                    new_pairs.append(state["pairs"][-1])
                except Exception as exc:
                    error_count += 1
                    first_error = first_error or str(exc)
                    new_pairs.append(pair)
                    _append_history(
                        state,
                        kind="auto",
                        direction=f"{s_prov.id}2{t_prov.id}",
                        source=s_prov.id,
                        target=t_prov.id,
                        source_id=s_sum.session_id,
                        target_id=t_id,
                        title=s_sum.title,
                        cwd=s_sum.cwd,
                        status="error",
                        message=str(exc),
                    )

            state["pairs"] = new_pairs

            # 2. Pre-existing unpaired sessions that already share the same UUID across both providers
            shared_unpaired = (set(agy_map.keys()) - paired_agy) & (
                set(claude_map.keys()) - paired_claude
            )
            for sid in sorted(shared_unpaired):
                agy_sum = agy_map[sid]
                claude_sum = claude_map[sid]
                if not (in_scope(agy_sum) or in_scope(claude_sum)):
                    continue
                paired_agy.add(sid)
                paired_claude.add(sid)
                try:
                    agy_sess = agy_prov.read_session(agy_sum.location, summary=agy_sum)
                    agy_fp = agy_prov.get_sync_fingerprint(agy_sum)
                    agy_hash = canonical_session_hash(agy_sess)

                    claude_sess = claude_prov.read_session(claude_sum.location, summary=claude_sum)
                    claude_fp = claude_prov.get_sync_fingerprint(claude_sum)
                    claude_hash = canonical_session_hash(claude_sess)

                    # Decide if already identical or one is newer
                    if agy_hash == claude_hash:
                        _upsert_pair(
                            state=state,
                            source="agy",
                            source_id=sid,
                            target="claude",
                            target_id=sid,
                            src_fp=agy_fp,
                            src_hash=agy_hash,
                            tgt_fp=claude_fp,
                            tgt_hash=claude_hash,
                            cwd=agy_sess.cwd or claude_sess.cwd or fallback_cwd,
                            title=agy_sess.title or claude_sess.title,
                            now=datetime.now(timezone.utc),
                        )
                        unchanged_count += 1
                    elif direction == "agy2claude" or (
                        direction == "both" and agy_sum.updated_at >= claude_sum.updated_at
                    ):
                        _sync_one_pair_direction(
                            src_prov=agy_prov,
                            tgt_prov=claude_prov,
                            src_sum=agy_sum,
                            tgt_id=sid,
                            overwrite=True,
                            include_thinking=include_thinking,
                            fallback_cwd=fallback_cwd,
                            src_session=agy_sess,
                            src_fp=agy_fp,
                            src_hash=agy_hash,
                            state=state,
                            kind="auto",
                        )
                        synced_count += 1
                    else:
                        _sync_one_pair_direction(
                            src_prov=claude_prov,
                            tgt_prov=agy_prov,
                            src_sum=claude_sum,
                            tgt_id=sid,
                            overwrite=True,
                            include_thinking=include_thinking,
                            fallback_cwd=fallback_cwd,
                            src_session=claude_sess,
                            src_fp=claude_fp,
                            src_hash=claude_hash,
                            state=state,
                            kind="auto",
                        )
                        synced_count += 1
                except Exception as exc:
                    error_count += 1
                    first_error = first_error or str(exc)

            # 3. Discover brand-new unpaired sessions
            passes: list[tuple[AgentProvider, AgentProvider, dict[str, SessionSummary], set[str], set[str]]] = []
            if direction in ("both", "agy2claude"):
                passes.append((agy_prov, claude_prov, agy_map, paired_agy, paired_claude))
            if direction in ("both", "claude2agy"):
                passes.append((claude_prov, agy_prov, claude_map, paired_claude, paired_agy))

            for src_prov, tgt_prov, src_map, seen_src, seen_tgt in passes:
                for sid, summary in src_map.items():
                    if sid in seen_src or not in_scope(summary):
                        continue
                    fp_now = src_prov.get_sync_fingerprint(summary)
                    skip_key = f"{src_prov.id}:{sid}"
                    prev_skip = skipped_map.get(skip_key)
                    if (
                        isinstance(prev_skip, dict)
                        and prev_skip.get("mtime_ns") == fp_now[0]
                        and prev_skip.get("count") == fp_now[1]
                    ):
                        unchanged_count += 1
                        continue
                    try:
                        tgt_id, _out_path, _regen = _sync_one_pair_direction(
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
                        seen_src.add(sid)
                        seen_tgt.add(tgt_id)
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
                            direction=f"{src_prov.id}2{tgt_prov.id}",
                            source=src_prov.id,
                            target=tgt_prov.id,
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
