from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from mcas.cache import (
    _AGY_META_CACHE,
    _CACHE_LOCK,
    _fast_dir_size,
    _fast_file_size,
    _file_stat_sig,
)
from mcas.config import (
    get_agy_cli_dir,
    get_agy_ide_dir,
)
from mcas.util.fsio import _connect_sqlite
from mcas.util.pathconv import file_uri_to_windows_path
from mcas.util.protobuf import (
    decode_pb,
    pb_all,
    pb_first,
    pb_str_val,
    pb_timestamp_val,
)

def _agy_base_dir_for(db_path: Path | None, app_data_dir: str = "antigravity-cli") -> Path:
    if db_path is not None and db_path.parent.name == "conversations":
        return db_path.parent.parent
    if app_data_dir == "antigravity":
        return get_agy_ide_dir()
    return get_agy_cli_dir()

def _agy_db_bundle_size(db_path: Path) -> int:
    return (
        _fast_file_size(db_path)
        + _fast_file_size(Path(str(db_path) + "-wal"))
        + _fast_file_size(Path(str(db_path) + "-shm"))
    )

def _find_agy_descendants(conv_id: str) -> list[str]:
    """Return all descendant child conversation IDs for `conv_id` via conversation_summaries."""
    sum_db = get_agy_cli_dir() / "conversation_summaries.db"
    if not sum_db.is_file():
        return []
    children_map: dict[str, list[str]] = {}
    try:
        con = _connect_sqlite(sum_db)
        try:
            for row in con.execute(
                "SELECT conversation_id, parent_conversation_id FROM conversation_summaries WHERE parent_conversation_id != ''"
            ):
                cid, pid = str(row[0] or ""), str(row[1] or "")
                if cid and pid:
                    children_map.setdefault(pid, []).append(cid)
            try:
                for row in con.execute("SELECT conversation_id, parent_id FROM conversation_parents"):
                    cid, pid = str(row[0] or ""), str(row[1] or "")
                    if cid and pid:
                        children_map.setdefault(pid, []).append(cid)
            except sqlite3.Error:
                pass
        finally:
            con.close()
    except sqlite3.Error:
        return []

    descendants: list[str] = []
    visited = {conv_id}
    queue = list(children_map.get(conv_id, []))
    while queue:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        descendants.append(cur)
        queue.extend(children_map.get(cur, []))
    return descendants

def get_agy_session_size(
    conv_id: str,
    db_path: Path | None = None,
    child_ids: list[str] | None = None,
    app_data_dir: str = "antigravity-cli",
) -> tuple[int, int, int]:
    """Return `(total_bytes, main_bytes, sidecar_bytes)` for an Antigravity session."""
    base_dir = _agy_base_dir_for(db_path, app_data_dir=app_data_dir)
    main_db = db_path or (base_dir / "conversations" / f"{conv_id}.db")
    main_bytes = _agy_db_bundle_size(main_db)

    sidecar_bytes = (
        _fast_dir_size(base_dir / "brain" / conv_id)
        + _fast_file_size(base_dir / "annotations" / f"{conv_id}.pbtxt")
        + _fast_file_size(base_dir / "implicit" / f"{conv_id}.pb")
    )

    if child_ids:
        cli_dir = get_agy_cli_dir()
        ide_dir = get_agy_ide_dir()
        for child_id in child_ids:
            child_db = _resolve_agy_db_path(child_id, app_data_dir)
            if child_db is not None:
                sidecar_bytes += _agy_db_bundle_size(child_db)
                cbase = _agy_base_dir_for(child_db, app_data_dir)
            else:
                cbase = base_dir
            for bdir in ({cbase, cli_dir} if base_dir == cli_dir else {cbase, ide_dir}):
                sidecar_bytes += (
                    _fast_dir_size(bdir / "brain" / child_id)
                    + _fast_file_size(bdir / "annotations" / f"{child_id}.pbtxt")
                    + _fast_file_size(bdir / "implicit" / f"{child_id}.pb")
                )

    return main_bytes + sidecar_bytes, main_bytes, sidecar_bytes

def _extract_agy_meta_from_db(
    db_path: Path,
) -> tuple[str | None, datetime | None, int, int]:
    """Read workspace URI, creation timestamp, nesting depth, and step count from `.db`."""
    sig = (*_file_stat_sig(db_path), *_file_stat_sig(Path(str(db_path) + "-wal")))
    if sig[0] < 0:
        return None, None, 0, 0
    cache_key = str(db_path)
    with _CACHE_LOCK:
        cached = _AGY_META_CACHE.get(cache_key)
        if cached is not None and cached[0] == sig:
            return cached[1]

    try:
        con = _connect_sqlite(db_path)
    except sqlite3.Error:
        return None, None, 0, 0
    result: tuple[str | None, datetime | None, int, int] = (None, None, 0, 0)
    try:
        step_count = 0
        try:
            cnt_row = con.execute("SELECT COUNT(*) FROM steps").fetchone()
            if cnt_row:
                step_count = int(cnt_row[0] or 0)
        except sqlite3.Error:
            pass

        row = con.execute(
            "SELECT data FROM trajectory_metadata_blob WHERE id = 'main'"
        ).fetchone()
        if not row or not row[0]:
            result = (None, None, 0, step_count)
        else:
            fields = decode_pb(row[0])
            created = pb_timestamp_val(fields, 2)
            nesting_depth = int(pb_first(fields, 17, 0) or 0)
            uris = [u.decode("utf-8", errors="replace") for u in pb_all(fields, 7, 2)]
            if not uris:
                ws_msg = pb_first(fields, 1, 2)
                if isinstance(ws_msg, (bytes, bytearray)):
                    ws_fields = decode_pb(ws_msg)
                    uri = pb_str_val(ws_fields, 1)
                    if uri:
                        uris.append(uri)
            win_found: str | None = None
            for uri in uris:
                win_path = file_uri_to_windows_path(uri)
                if win_path:
                    win_found = win_path
                    break
            result = (win_found, created, nesting_depth, step_count)
    except sqlite3.Error:
        return None, None, 0, 0
    finally:
        con.close()

    with _CACHE_LOCK:
        _AGY_META_CACHE[cache_key] = (sig, result)
    return result

def _resolve_agy_db_path(conv_id: str, app_data_dir: str = "antigravity-cli") -> Path | None:
    """Find the `.db` path for a conversation ID across CLI and IDE dirs."""
    cli_dir = get_agy_cli_dir()
    ide_dir = get_agy_ide_dir()
    candidates = []
    if app_data_dir == "antigravity":
        candidates.append(ide_dir / "conversations" / f"{conv_id}.db")
        candidates.append(cli_dir / "conversations" / f"{conv_id}.db")
    else:
        candidates.append(cli_dir / "conversations" / f"{conv_id}.db")
        candidates.append(ide_dir / "conversations" / f"{conv_id}.db")
    for p in candidates:
        if p.is_file():
            return p
    return None

def _agy_target_exists(conv_id: str) -> bool:
    """Check whether `conv_id` exists in `conversations/` or `conversation_summaries.db`."""
    cli_dir = get_agy_cli_dir()
    if (cli_dir / "conversations" / f"{conv_id}.db").exists():
        return True
    sum_db = cli_dir / "conversation_summaries.db"
    if sum_db.is_file():
        con = _connect_sqlite(sum_db)
        try:
            row = con.execute(
                "SELECT 1 FROM conversation_summaries WHERE conversation_id = ?", (conv_id,)
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False
        finally:
            con.close()
    return False
