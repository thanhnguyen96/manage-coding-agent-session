from __future__ import annotations

from typing import Any

from mcas.errors import SyncError
from mcas.providers import (
    get_provider,
    get_supported_providers,
)
from mcas.sync.state import (
    _SYNC_LOCK,
    _append_history,
    _load_state,
    _remove_pairs_for,
    _save_state,
)
from mcas.util.text import (
    format_bytes,
    is_valid_uuid,
)

def _resolve_synced_peers(provider_id: str, session_id: str) -> list[tuple[str, str]]:
    """Return synced counterpart `(other_provider, other_id)` entries for `provider_id:session_id`."""
    peers: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    with _SYNC_LOCK:
        state = _load_state()
        for p in state.get("pairs", []):
            s = str(p.get("source") or "")
            sid = str(p.get("source_id") or "")
            t = str(p.get("target") or "")
            tid = str(p.get("target_id") or "")
            if s == provider_id and sid == session_id and t and tid:
                key = (t, tid)
                if key not in seen:
                    seen.add(key)
                    peers.append(key)
            elif t == provider_id and tid == session_id and s and sid:
                key = (s, sid)
                if key not in seen:
                    seen.add(key)
                    peers.append(key)

    for prov in get_supported_providers():
        if prov.id == provider_id or not is_valid_uuid(session_id):
            continue
        key = (prov.id, session_id)
        if key in seen:
            continue
        try:
            prov.find_session(session_id)
            seen.add(key)
            peers.append(key)
        except SyncError:
            pass
    return peers

def delete_sessions_batch(
    items: list[dict[str, Any]],
    *,
    delete_synced_peers: bool = False,
) -> dict[str, Any]:
    """Delete one or more sessions cleanly (with optional counterpart removal)."""
    if not items:
        raise SyncError("At least one session item is required for deletion.")

    queue: list[tuple[str, str, bool]] = []
    for item in items:
        src = str(item.get("source") or "").strip().lower()
        sid = str(item.get("id") or "").strip()
        if not src or not sid:
            continue
        queue.append((src, sid, False))

    if not queue:
        raise SyncError("source and id are required for deletion.")

    if delete_synced_peers:
        expanded: list[tuple[str, str, bool]] = []
        for src, sid, is_peer in queue:
            expanded.append((src, sid, is_peer))
            try:
                prov = get_provider(src, require_supported=True)
                loc, summary = prov.find_session(sid)
                resolved_id = summary.session_id if summary else prov.session_id_from_location(loc)
                for peer_src, peer_id in _resolve_synced_peers(prov.id, resolved_id):
                    expanded.append((peer_src, peer_id, True))
            except SyncError:
                pass
        queue = expanded

    done_keys: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    total_freed = 0
    deleted_count = 0

    for src, sid, is_peer in queue:
        try:
            prov = get_provider(src, require_supported=True)
            loc, summary = prov.find_session(sid)
            resolved_id = summary.session_id if summary else prov.session_id_from_location(loc)
            title = summary.title if summary else resolved_id
            cwd_val = summary.cwd if summary else ""
        except SyncError as exc:
            if is_peer:
                continue
            results.append(
                {
                    "ok": False,
                    "source": src,
                    "id": sid,
                    "freedBytes": 0,
                    "freedHuman": "0 B",
                    "error": str(exc),
                }
            )
            continue

        key = (prov.id, resolved_id)
        if key in done_keys:
            continue
        done_keys.add(key)

        try:
            del_res = prov.delete_session(resolved_id)
            _remove_pairs_for(prov.id, del_res["id"])
            freed = int(del_res.get("freedBytes") or 0)
            total_freed += freed
            deleted_count += 1
            results.append(
                {
                    "ok": True,
                    "source": prov.id,
                    "id": del_res["id"],
                    "title": title,
                    "cwd": cwd_val or "",
                    "freedBytes": freed,
                    "freedHuman": format_bytes(freed),
                    "childrenDeleted": int(del_res.get("childrenDeleted") or 0),
                    "isSyncedPeer": is_peer,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "source": prov.id,
                    "id": resolved_id,
                    "freedBytes": 0,
                    "freedHuman": "0 B",
                    "error": str(exc),
                }
            )

    if deleted_count == 0:
        first_err = next((r["error"] for r in results if r.get("error")), "No sessions were deleted.")
        raise SyncError(first_err)

    with _SYNC_LOCK:
        state = _load_state()
        for r in results:
            if not r.get("ok"):
                continue
            _append_history(
                state,
                kind="delete",
                direction=r["source"],
                source=r["source"],
                target="",
                source_id=r["id"],
                target_id="",
                title=r.get("title") or r["id"],
                cwd=r.get("cwd") or "",
                status="ok",
                message=f"Deleted ({r['freedHuman']} freed)",
            )
        _save_state(state)

    msg = f"Deleted {deleted_count} session(s) (freed {format_bytes(total_freed)})."
    return {
        "ok": True,
        "deletedCount": deleted_count,
        "freedBytes": total_freed,
        "freedHuman": format_bytes(total_freed),
        "results": results,
        "message": msg,
    }
