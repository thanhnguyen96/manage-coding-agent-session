from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import json
import os
import threading

from mcas.config import get_sync_state_path
from mcas.constants import MAX_HISTORY_ENTRIES
from mcas.errors import SyncError
from mcas.util.fsio import _atomic_write_json
from mcas.util.pathconv import normalize_windows_path
from mcas.util.timefmt import (
    format_iso_utc,
    format_local_time,
    parse_ts,
)

_SYNC_LOCK = threading.RLock()

def _default_state() -> dict[str, Any]:
    return {
        "config": {
            "enabled": False,
            "intervalSec": 30,
            "direction": "both",
            "scope": "all",
            "watchedFolders": [],
            "includeThinking": False,
        },
        "runtime": {
            "lastRunAt": None,
            "lastError": None,
        },
        "pairs": [],
        "skipped": {},
        "history": [],
    }

def _normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    d = _default_state()["config"]
    if not isinstance(raw, dict):
        return d
    enabled = bool(raw.get("enabled", d["enabled"]))
    try:
        interval = int(raw.get("intervalSec", d["intervalSec"]))
    except (TypeError, ValueError):
        interval = d["intervalSec"]
    interval = max(5, interval)

    direction = str(raw.get("direction", d["direction"])).strip().lower()
    if direction not in ("both", "agy2claude", "claude2agy"):
        direction = d["direction"]

    scope = str(raw.get("scope", d["scope"])).strip().lower()
    if scope not in ("all", "watched"):
        scope = d["scope"]

    watched: list[str] = []
    seen_norm: set[str] = set()
    raw_folders = raw.get("watchedFolders") or []
    if isinstance(raw_folders, list):
        for item in raw_folders:
            if not isinstance(item, str) or not item.strip():
                continue
            norm = normalize_windows_path(item)
            key = os.path.normcase(norm)
            if key not in seen_norm:
                seen_norm.add(key)
                watched.append(norm)

    include_thinking = bool(raw.get("includeThinking", d["includeThinking"]))
    return {
        "enabled": enabled,
        "intervalSec": interval,
        "direction": direction,
        "scope": scope,
        "watchedFolders": watched,
        "includeThinking": include_thinking,
    }

def _load_state() -> dict[str, Any]:
    with _SYNC_LOCK:
        path = get_sync_state_path()
        base = _default_state()
        if not path.is_file():
            return base
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return base
        if not isinstance(loaded, dict):
            return base
        base["config"] = _normalize_config(loaded.get("config"))
        rt = loaded.get("runtime")
        if isinstance(rt, dict):
            base["runtime"]["lastRunAt"] = rt.get("lastRunAt")
            base["runtime"]["lastError"] = rt.get("lastError")
        if isinstance(loaded.get("pairs"), list):
            base["pairs"] = [p for p in loaded["pairs"] if isinstance(p, dict)]
        if isinstance(loaded.get("skipped"), dict):
            base["skipped"] = dict(loaded["skipped"])
        if isinstance(loaded.get("history"), list):
            base["history"] = [h for h in loaded["history"] if isinstance(h, dict)][
                :MAX_HISTORY_ENTRIES
            ]
        return base

def _save_state(state: dict[str, Any]) -> None:
    with _SYNC_LOCK:
        state["history"] = state.get("history", [])[:MAX_HISTORY_ENTRIES]
        _atomic_write_json(get_sync_state_path(), state)

def _pair_matches_endpoint(pair: dict[str, Any], provider: str, sid: str) -> bool:
    return (pair.get("source") == provider and pair.get("source_id") == sid) or (
        pair.get("target") == provider and pair.get("target_id") == sid
    )

def _upsert_pair(
    state: dict[str, Any],
    source: str,
    source_id: str,
    target: str,
    target_id: str,
    src_fp: tuple[int, int],
    src_hash: str,
    tgt_fp: tuple[int, int],
    tgt_hash: str,
    cwd: str | None,
    title: str,
    now: datetime,
) -> None:
    pairs = state.setdefault("pairs", [])
    kept = [
        p
        for p in pairs
        if not (
            _pair_matches_endpoint(p, source, source_id)
            and _pair_matches_endpoint(p, target, target_id)
        )
    ]
    kept.append(
        {
            "source": source,
            "source_id": source_id,
            "target": target,
            "target_id": target_id,
            "last_source_mtime_ns": src_fp[0],
            "last_source_count": src_fp[1],
            "last_source_hash": src_hash,
            "last_target_mtime_ns": tgt_fp[0],
            "last_target_count": tgt_fp[1],
            "last_target_hash": tgt_hash,
            "cwd": cwd or "",
            "title": title or "",
            "last_sync_at": format_iso_utc(now),
        }
    )
    state["pairs"] = kept

def _append_history(
    state: dict[str, Any],
    *,
    kind: str,
    direction: str,
    source: str,
    target: str,
    source_id: str,
    target_id: str,
    title: str,
    cwd: str | None,
    status: str,
    message: str,
    resume_cmd: str = "",
    now: datetime | None = None,
) -> None:
    ts = now or datetime.now(timezone.utc)
    entry = {
        "timestamp": format_iso_utc(ts),
        "timestampLocal": format_local_time(ts),
        "kind": kind,
        "direction": direction,
        "source": source,
        "target": target,
        "sourceId": source_id,
        "targetId": target_id,
        "title": title,
        "cwd": cwd or "",
        "status": status,
        "message": message,
        "resumeCmd": resume_cmd,
    }
    history = state.setdefault("history", [])
    history.insert(0, entry)
    state["history"] = history[:MAX_HISTORY_ENTRIES]

def _remove_pairs_for(provider: str, sid: str) -> None:
    with _SYNC_LOCK:
        state = _load_state()
        before = len(state["pairs"])
        state["pairs"] = [
            p for p in state["pairs"] if not _pair_matches_endpoint(p, provider, sid)
        ]
        state.get("skipped", {}).pop(f"{provider}:{sid}", None)
        if len(state["pairs"]) != before:
            _save_state(state)
        else:
            _save_state(state)

def get_autosync_dict() -> dict[str, Any]:
    from mcas.sync.autosync import is_sync_running

    with _SYNC_LOCK:
        state = _load_state()
        cfg = state["config"]
        rt = state["runtime"]
        last_run_at = rt.get("lastRunAt")
        last_run_local = (
            format_local_time(parse_ts(last_run_at)) if last_run_at else None
        )
        return {
            "enabled": cfg["enabled"],
            "running": is_sync_running(),
            "intervalSec": cfg["intervalSec"],
            "direction": cfg["direction"],
            "scope": cfg["scope"],
            "watchedFolders": list(cfg["watchedFolders"]),
            "includeThinking": cfg["includeThinking"],
            "lastRunAt": last_run_at,
            "lastRunLocal": last_run_local,
            "lastError": rt.get("lastError"),
            "pairCount": len(state["pairs"]),
            "history": list(state["history"]),
        }

def update_autosync_config(
    *,
    enabled: bool | None = None,
    interval_sec: int | None = None,
    direction: str | None = None,
    scope: str | None = None,
    watched_folders: list[str] | None = None,
    add_watch: list[str] | None = None,
    remove_watch: list[str] | None = None,
    include_thinking: bool | None = None,
) -> dict[str, Any]:
    with _SYNC_LOCK:
        state = _load_state()
        cfg = dict(state["config"])

        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if interval_sec is not None:
            try:
                iv = int(interval_sec)
            except (TypeError, ValueError) as exc:
                raise SyncError(f"Invalid intervalSec '{interval_sec}'.") from exc
            cfg["intervalSec"] = max(5, iv)
        if direction is not None:
            d = str(direction).strip().lower()
            if d not in ("both", "agy2claude", "claude2agy"):
                raise SyncError(
                    f"Invalid direction '{direction}'. Use 'both', 'agy2claude', or 'claude2agy'."
                )
            cfg["direction"] = d
        if scope is not None:
            sc = str(scope).strip().lower()
            if sc not in ("all", "watched"):
                raise SyncError(f"Invalid scope '{scope}'. Use 'all' or 'watched'.")
            cfg["scope"] = sc
        if watched_folders is not None:
            if not isinstance(watched_folders, list):
                raise SyncError("watchedFolders must be a list of folder paths.")
            cfg["watchedFolders"] = watched_folders
        if add_watch:
            existing = list(cfg.get("watchedFolders", []))
            for w in add_watch:
                if w and w.strip():
                    existing.append(normalize_windows_path(w))
            cfg["watchedFolders"] = existing
        if remove_watch:
            remove_keys = {
                os.path.normcase(normalize_windows_path(w))
                for w in remove_watch
                if w and w.strip()
            }
            cfg["watchedFolders"] = [
                w
                for w in cfg.get("watchedFolders", [])
                if os.path.normcase(normalize_windows_path(w)) not in remove_keys
            ]
        if include_thinking is not None:
            cfg["includeThinking"] = bool(include_thinking)

        state["config"] = _normalize_config(cfg)
        _save_state(state)
    return get_autosync_dict()

def _extract_pair_sides(
    pair: dict[str, Any]
) -> tuple[str, tuple[int, int], str, str, tuple[int, int], str] | None:
    src = pair.get("source")
    tgt = pair.get("target")
    src_id = pair.get("source_id")
    tgt_id = pair.get("target_id")
    if not src_id or not tgt_id:
        return None
    src_fp = (
        int(pair.get("last_source_mtime_ns") or 0),
        int(pair.get("last_source_count") or 0),
    )
    tgt_fp = (
        int(pair.get("last_target_mtime_ns") or 0),
        int(pair.get("last_target_count") or 0),
    )
    src_hash = str(pair.get("last_source_hash") or "")
    tgt_hash = str(pair.get("last_target_hash") or "")
    if src == "agy" and tgt == "claude":
        return src_id, src_fp, src_hash, tgt_id, tgt_fp, tgt_hash
    if src == "claude" and tgt == "agy":
        return tgt_id, tgt_fp, tgt_hash, src_id, src_fp, src_hash
    return None
