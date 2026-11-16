"""Codex rollout file scanning, SQLite index sync, and path helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re
import sqlite3
import sys

from mcas.cache import (
    _CACHE_LOCK,
    _CODEX_SUMMARY_CACHE,
    _fast_file_size,
    _file_stat_sig,
)
from mcas.config import (
    get_codex_archived_dir,
    get_codex_dir,
    get_codex_sessions_dir,
)
from mcas.errors import SyncError
from mcas.models import SessionSummary
from mcas.providers.codex.convert import (
    _extract_codex_reasoning_text,
    _extract_codex_text_blocks,
    _extract_codex_user_message_text,
)
from mcas.util.fsio import _connect_sqlite
from mcas.util.pathconv import normalize_windows_path
from mcas.util.text import (
    is_valid_uuid,
    truncate_preview,
)
from mcas.util.timefmt import parse_ts

_ROLLOUT_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def _validate_codex_id_token(raw: str) -> str:
    """Validate that a user-supplied session ID token contains no path traversal characters."""
    sid = (raw or "").strip()
    if not sid or "/" in sid or "\\" in sid or ".." in sid or "\x00" in sid:
        raise SyncError(f"Invalid Codex session ID '{raw}'.")
    return sid


def _assert_within_codex_dir(path: Path) -> None:
    """Ensure a target rollout path resolves inside the configured Codex directory."""
    base = get_codex_dir().resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(base):
        raise SyncError(f"Refusing path outside Codex directory: {path}")


def _normalize_codex_id(raw_id: str) -> str:
    sid = raw_id.strip()
    return sid.lower() if is_valid_uuid(sid) else sid


def _extract_valid_payload_id(payload: dict[str, Any]) -> str | None:
    """Return normalized `payload['id']` if non-empty and free of path traversal tokens."""
    raw_id = payload.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        cand = raw_id.strip()
        if "/" not in cand and "\\" not in cand and ".." not in cand and "\x00" not in cand:
            return _normalize_codex_id(cand)
    return None


def extract_uuid_from_rollout_stem(stem: str) -> str:
    """Extract the trailing UUID from a `rollout-<ts>-<uuid>` filename stem."""
    match = _ROLLOUT_UUID_RE.search(stem or "")
    if match:
        return match.group(1).lower()
    return stem


def _extract_codex_subagent_meta(payload: dict[str, Any]) -> tuple[bool, int]:
    """Detect Codex subagent/guardian rollouts from `session_meta.payload`."""
    is_subagent = False
    depth = 0

    src = payload.get("source")
    if isinstance(src, str) and src.strip().startswith("{"):
        try:
            src = json.loads(src)
        except json.JSONDecodeError:
            pass
    if isinstance(src, dict) and "subagent" in src:
        is_subagent = True
        sub = src.get("subagent")
        if isinstance(sub, dict) and isinstance(sub.get("thread_spawn"), dict):
            try:
                depth = max(1, int(sub["thread_spawn"].get("depth") or 1))
            except (TypeError, ValueError):
                depth = 1
        else:
            depth = 1

    thread_src = payload.get("thread_source")
    if isinstance(thread_src, str) and thread_src.strip().lower() in (
        "subagent",
        "guardian_review",
    ):
        is_subagent = True
        if depth == 0:
            depth = 1

    return is_subagent, depth


def _extract_event_msg_text(payload: dict[str, Any]) -> str:
    """Extract readable text from an `event_msg` payload (`user_message`/`agent_message`/`agent_reasoning`)."""
    for key in ("message", "text"):
        val = payload.get(key)
        if isinstance(val, str):
            return val
    return _extract_codex_text_blocks(payload.get("content"), allow_image_tag=False)


def _read_codex_title_overrides() -> tuple[dict[str, str], dict[str, str]]:
    """Return `(custom_names, fallback_db_titles)` from `session_index.jsonl` and `state_5.sqlite`."""
    custom_names: dict[str, str] = {}
    db_titles: dict[str, str] = {}

    codex_dir = get_codex_dir()
    idx_path = codex_dir / "session_index.jsonl"
    if idx_path.is_file():
        try:
            with idx_path.open("r", encoding="utf-8-sig", errors="replace") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    raw_id = rec.get("id")
                    name = rec.get("thread_name")
                    if isinstance(raw_id, str) and raw_id.strip() and isinstance(name, str) and name.strip():
                        custom_names[_normalize_codex_id(raw_id)] = name.strip()
        except OSError:
            pass

    state_db = codex_dir / "state_5.sqlite"
    if state_db.is_file():
        try:
            con = _connect_sqlite(state_db)
            try:
                has_table = (
                    con.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'"
                    ).fetchone()
                    is not None
                )
                if has_table:
                    cols = {str(r[1]) for r in con.execute("PRAGMA table_info(threads)")}
                    if "id" in cols:
                        sel_name = "name" if "name" in cols else "NULL AS name"
                        sel_title = "title" if "title" in cols else "NULL AS title"
                        for row in con.execute(f"SELECT id, {sel_name}, {sel_title} FROM threads"):
                            rid = str(row[0] or "").strip()
                            if not rid:
                                continue
                            nid = _normalize_codex_id(rid)
                            if isinstance(row[1], str) and row[1].strip():
                                custom_names[nid] = row[1].strip()
                            if isinstance(row[2], str) and row[2].strip():
                                db_titles[nid] = row[2].strip()
            finally:
                con.close()
        except sqlite3.Error:
            pass

    return custom_names, db_titles


def _extract_codex_id_from_file(jsonl_path: Path) -> str:
    """Read `session_meta.payload.id` from a rollout file, falling back to filename UUID."""
    try:
        with jsonl_path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for _ in range(10):
                raw_line = fh.readline()
                if not raw_line:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "session_meta":
                    pl = rec.get("payload")
                    if isinstance(pl, dict):
                        valid_id = _extract_valid_payload_id(pl)
                        if valid_id:
                            return valid_id
                    break
    except OSError:
        pass
    return extract_uuid_from_rollout_stem(jsonl_path.stem)


def _is_archived_rollout_path(jsonl_path: Path) -> bool:
    """Return True if `jsonl_path` resides under Codex's `archived_sessions/` directory."""
    try:
        archived_root = get_codex_archived_dir().resolve()
        return jsonl_path.resolve().is_relative_to(archived_root)
    except OSError:
        return "archived_sessions" in jsonl_path.parts


def _scan_codex_summary(
    jsonl_path: Path, is_archived: bool | None = None
) -> SessionSummary | None:
    """Quickly extract summary metadata from a single Codex rollout `.jsonl` file."""
    archived = _is_archived_rollout_path(jsonl_path) if is_archived is None else is_archived
    sig_pair = _file_stat_sig(jsonl_path)
    if sig_pair[0] < 0:
        return None
    sig = (sig_pair[0], sig_pair[1], 1 if archived else 0, 0)
    cache_key = str(jsonl_path)
    with _CACHE_LOCK:
        cached = _CODEX_SUMMARY_CACHE.get(cache_key)
        if cached is not None and cached[0] == sig:
            return cached[1]

    sid = extract_uuid_from_rollout_stem(jsonl_path.stem)
    seen_session_meta = False
    cwd: str | None = None
    last_ts: datetime | None = None
    is_subagent = False
    depth = 0
    resp_turn_count = 0
    event_turn_count = 0
    resp_first_prompt = ""
    event_first_prompt = ""

    try:
        with jsonl_path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue

                if rec.get("timestamp"):
                    last_ts = parse_ts(rec["timestamp"], fallback=last_ts)

                rtype = rec.get("type")
                payload = rec.get("payload")
                if not isinstance(payload, dict):
                    continue

                if rtype == "session_meta":
                    if not seen_session_meta:
                        seen_session_meta = True
                        valid_id = _extract_valid_payload_id(payload)
                        if valid_id:
                            sid = valid_id
                        if not cwd and isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
                            cwd = normalize_windows_path(payload["cwd"])
                        if payload.get("timestamp") and last_ts is None:
                            last_ts = parse_ts(payload["timestamp"], fallback=last_ts)
                        sub_flag, sub_depth = _extract_codex_subagent_meta(payload)
                        if sub_flag:
                            is_subagent = True
                            depth = max(depth, sub_depth)

                elif rtype == "turn_context":
                    if not cwd and isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
                        cwd = normalize_windows_path(payload["cwd"])

                elif rtype == "response_item":
                    ptype = payload.get("type")
                    if ptype == "message":
                        role = payload.get("role")
                        if role == "user":
                            txt = _extract_codex_user_message_text(payload).strip()
                            if txt:
                                resp_turn_count += 1
                                if not resp_first_prompt:
                                    resp_first_prompt = txt
                        elif role == "assistant":
                            txt = _extract_codex_text_blocks(payload.get("content")).strip()
                            if txt:
                                resp_turn_count += 1
                    elif ptype == "agent_message":
                        txt = _extract_codex_text_blocks(payload.get("content")).strip()
                        if txt:
                            resp_turn_count += 1
                    elif ptype == "reasoning":
                        if _extract_codex_reasoning_text(payload):
                            resp_turn_count += 1
                    elif ptype in (
                        "function_call",
                        "custom_tool_call",
                        "local_shell_call",
                        "web_search_call",
                        "function_call_output",
                        "custom_tool_call_output",
                        "local_shell_call_output",
                    ):
                        resp_turn_count += 1

                elif rtype == "event_msg":
                    ptype = payload.get("type")
                    if ptype == "user_message":
                        txt = _extract_event_msg_text(payload).strip()
                        if txt:
                            event_turn_count += 1
                            if not event_first_prompt:
                                event_first_prompt = txt
                    elif ptype == "agent_message":
                        txt = _extract_event_msg_text(payload).strip()
                        if txt:
                            event_turn_count += 1
    except OSError:
        return None

    try:
        fallback_dt = datetime.fromtimestamp(sig[0] / 1_000_000_000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        fallback_dt = datetime.now(timezone.utc)

    updated = last_ts or fallback_dt
    turn_count = resp_turn_count if resp_turn_count > 0 else event_turn_count
    first_prompt = event_first_prompt or resp_first_prompt
    title = truncate_preview(first_prompt, 80) or "(untitled)"
    preview = truncate_preview(first_prompt, 120)

    extra_parts: list[str] = []
    if is_subagent:
        extra_parts.append(f"depth={max(1, depth)}")
    if archived:
        extra_parts.append("archived")

    summary = SessionSummary(
        session_id=sid,
        source="codex",
        updated_at=updated,
        title=title,
        preview=preview,
        cwd=cwd,
        location=jsonl_path,
        step_or_msg_count=turn_count,
        extra=",".join(extra_parts),
        size_bytes=sig_pair[1],
    )
    with _CACHE_LOCK:
        _CODEX_SUMMARY_CACHE[cache_key] = (sig, summary)
    return summary


def _iter_codex_rollouts(include_archived: bool = True) -> list[tuple[Path, bool]]:
    """Return `(path, is_archived)` pairs for Codex rollout JSONL files."""
    files: list[tuple[Path, bool]] = []
    sessions_dir = get_codex_sessions_dir()
    if sessions_dir.is_dir():
        for p in sessions_dir.rglob("*.jsonl"):
            if p.is_file():
                files.append((p, False))
    if include_archived:
        archived_dir = get_codex_archived_dir()
        if archived_dir.is_dir():
            for p in archived_dir.rglob("*.jsonl"):
                if p.is_file():
                    files.append((p, True))
    return files


def _codex_target_exists(session_id: str) -> Path | None:
    """Return existing rollout path if `session_id` already exists in Codex storage."""
    norm = _normalize_codex_id(session_id).lower()
    for path, _ in _iter_codex_rollouts(include_archived=True):
        summary = _scan_codex_summary(path)
        cand_id = summary.session_id if summary else extract_uuid_from_rollout_stem(path.stem)
        if cand_id.lower() == norm:
            return path
    return None


def _upsert_codex_sqlite_thread(
    *,
    session_id: str,
    out_path: Path,
    created_at: datetime,
    updated_at: datetime,
    cwd: str,
    title: str,
    preview: str,
    first_user_message: str,
    custom_name: str,
    is_archived: bool,
) -> None:
    """Safely upsert a `threads` row in `state_5.sqlite` without triggering `ON DELETE CASCADE`."""
    state_db = get_codex_dir() / "state_5.sqlite"
    if not state_db.is_file():
        return

    con = _connect_sqlite(state_db)
    try:
        has_table = (
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'"
            ).fetchone()
            is not None
        )
        if not has_table:
            return

        info_rows = con.execute("PRAGMA table_info(threads)").fetchall()
        existing_cols = {
            str(r[1]): (str(r[2] or ""), int(r[3] or 0), r[4]) for r in info_rows
        }
        if "id" not in existing_cols:
            return

        created_s = int(created_at.timestamp())
        updated_s = int(updated_at.timestamp())
        created_ms = int(created_at.timestamp() * 1000)
        updated_ms = int(updated_at.timestamp() * 1000)

        row_values: dict[str, Any] = {
            "id": session_id,
            "rollout_path": str(out_path),
            "created_at": created_s,
            "updated_at": updated_s,
            "source": "cli",
            "model_provider": "openai",
            "cwd": cwd,
            "title": title,
            "sandbox_policy": '{"type":"read-only"}',
            "approval_mode": "on-request",
            "has_user_event": 1,
            "archived": 1 if is_archived else 0,
            "first_user_message": first_user_message,
            "preview": preview,
            "thread_source": "user",
            "created_at_ms": created_ms,
            "updated_at_ms": updated_ms,
            "recency_at": updated_s,
            "recency_at_ms": updated_ms,
            "history_mode": "legacy",
        }
        if custom_name:
            row_values["name"] = custom_name

        for col_name, (col_type, notnull, dflt) in existing_cols.items():
            if notnull and dflt is None and col_name not in row_values:
                row_values[col_name] = 0 if "INT" in col_type.upper() else ""

        insert_cols = [c for c in row_values if c in existing_cols]
        update_cols = [
            c for c in insert_cols if c not in ("id", "created_at", "created_at_ms")
        ]

        exists_row = con.execute(
            "SELECT 1 FROM threads WHERE id = ?", (session_id,)
        ).fetchone()
        if exists_row is not None:
            if update_cols:
                set_clause = ", ".join(f'"{c}" = ?' for c in update_cols)
                con.execute(
                    f"UPDATE threads SET {set_clause} WHERE id = ?",
                    [row_values[c] for c in update_cols] + [session_id],
                )
        else:
            col_list = ", ".join(f'"{c}"' for c in insert_cols)
            placeholders = ", ".join("?" for _ in insert_cols)
            con.execute(
                f"INSERT INTO threads ({col_list}) VALUES ({placeholders})",
                [row_values[c] for c in insert_cols],
            )
        con.commit()
    except sqlite3.Error as exc:
        print(f"Warning: failed to update {state_db}: {exc}", file=sys.stderr)
    finally:
        con.close()


def _delete_codex_sqlite_thread(session_id: str) -> None:
    """Remove `session_id` from `state_5.sqlite` `threads` if the DB exists."""
    state_db = get_codex_dir() / "state_5.sqlite"
    if not state_db.is_file():
        return
    con = _connect_sqlite(state_db)
    try:
        has_table = (
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'"
            ).fetchone()
            is not None
        )
        if has_table:
            con.execute("DELETE FROM threads WHERE id = ?", (session_id,))
            con.commit()
    except sqlite3.Error as exc:
        print(f"Warning: failed to delete row from {state_db}: {exc}", file=sys.stderr)
    finally:
        con.close()


def get_codex_session_size(jsonl_path: Path | None = None) -> tuple[int, int, int]:
    """Return `(total_bytes, main_bytes, sidecar_bytes)` for a Codex rollout file."""
    main_bytes = _fast_file_size(jsonl_path) if jsonl_path else 0
    return main_bytes, main_bytes, 0
