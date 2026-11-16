from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
import shutil
import sqlite3

from mcas.cache import _evict_session_cache
from mcas.config import (
    get_agy_cli_dir,
    get_agy_ide_dir,
    get_claude_dir,
)
from mcas.errors import SyncError
from mcas.models import (
    CanonicalSession,
    CanonicalToolResult,
    CanonicalTurn,
    SessionSummary,
    canonical_session_hash,
)
from mcas.providers import get_provider
from mcas.providers.agy.storage import (
    _agy_base_dir_for,
    _find_agy_descendants,
)
from mcas.sync.state import (
    _SYNC_LOCK,
    _append_history,
    _load_state,
    _save_state,
)
from mcas.util.fsio import _connect_sqlite
from mcas.util.text import format_bytes

def _update_pair_after_trim(
    provider_id: str,
    session_id: str,
    fp: tuple[int, int],
    session_hash: str,
    title: str,
) -> None:
    with _SYNC_LOCK:
        state = _load_state()
        changed = False
        for p in state.get("pairs", []):
            if p.get("source") == provider_id and p.get("source_id") == session_id:
                p["last_source_mtime_ns"] = fp[0]
                p["last_source_count"] = fp[1]
                p["last_source_hash"] = session_hash
                changed = True
            elif p.get("target") == provider_id and p.get("target_id") == session_id:
                p["last_target_mtime_ns"] = fp[0]
                p["last_target_count"] = fp[1]
                p["last_target_hash"] = session_hash
                changed = True
        if changed:
            _save_state(state)

def trim_session(
    *,
    source: str,
    session_id: str,
    keep_ratio: float | None = None,
    keep_last_turns: int | None = None,
    max_tool_output_chars: int = 2000,
    strip_thinking: bool = True,
    purge_sidecars: bool = True,
) -> dict[str, Any]:
    """Compact/lighten a conversation in-place by keeping recent turns and stripping heavy artifacts."""
    prov = get_provider(source, require_supported=True)
    sid_arg = (session_id or "").strip()
    if not sid_arg or sid_arg.lower() == "latest":
        raise SyncError("An explicit session ID is required for trim/lighten.")

    if keep_ratio is not None and not (0.0 < keep_ratio <= 1.0):
        raise SyncError("keepRatio must be between 0 and 1.0.")
    if keep_last_turns is not None and keep_last_turns < 0:
        raise SyncError("keepLastTurns cannot be negative.")
    if max_tool_output_chars < 0:
        raise SyncError("maxToolOutputChars cannot be negative.")

    loc, summary = prov.find_session(sid_arg)
    session = prov.read_session(loc, summary=summary)
    real_id = session.session_id
    app_dir = "antigravity" if (summary and "antigravity" in summary.extra.split(",")) else "antigravity-cli"

    child_ids: list[str] = _find_agy_descendants(real_id) if prov.id == "agy" else []
    before_bytes, _, _ = prov.get_session_size(
        real_id, loc, summary=summary, include_children=purge_sidecars
    )

    orig_turns = list(session.turns)
    turns_before = len(orig_turns)
    turns = list(orig_turns)

    if keep_last_turns is not None and keep_last_turns > 0 and len(turns) > keep_last_turns:
        turns = turns[-keep_last_turns:]
    elif keep_ratio is not None and 0.0 < keep_ratio < 1.0 and len(turns) > 1:
        keep_n = max(1, min(len(turns), int(round(len(turns) * keep_ratio))))
        if keep_n < len(turns):
            turns = turns[-keep_n:]

    while len(turns) > 1 and turns[0].role == "tool_result":
        turns.pop(0)

    cleaned_turns: list[CanonicalTurn] = []
    for t in turns:
        new_thinking = "" if strip_thinking else t.thinking
        new_trs: list[CanonicalToolResult] = []
        for tr in t.tool_results:
            content = tr.content
            if max_tool_output_chars > 0 and len(content) > max_tool_output_chars:
                dropped = len(content) - max_tool_output_chars
                content = (
                    content[:max_tool_output_chars].rstrip()
                    + f"\n... [truncated {dropped} chars by mcas]"
                )
            new_trs.append(replace(tr, content=content))
        new_t = CanonicalTurn(
            role=t.role,
            timestamp=t.timestamp,
            text=t.text,
            thinking=new_thinking,
            tool_calls=list(t.tool_calls),
            tool_results=new_trs,
        )
        if new_t.text.strip() or new_t.thinking.strip() or new_t.tool_calls or new_t.tool_results:
            cleaned_turns.append(new_t)

    if not cleaned_turns:
        cleaned_turns = [
            CanonicalTurn(
                role="user",
                timestamp=session.updated_at,
                text="[Conversation trimmed by mcas]",
            )
        ]
    elif cleaned_turns[0].role != "user":
        cleaned_turns.insert(
            0,
            CanonicalTurn(
                role="user",
                timestamp=cleaned_turns[0].timestamp,
                text="[Earlier conversation turns trimmed by mcas]",
            ),
        )

    trimmed_sess = CanonicalSession(
        session_id=real_id,
        source=session.source,
        title=session.title,
        preview=session.preview,
        cwd=session.cwd,
        created_at=session.created_at,
        updated_at=session.updated_at,
        turns=cleaned_turns,
    )

    _, out_path, _ = prov.write_session(
        trimmed_sess,
        target_cwd=None if prov.id == "claude" else session.cwd,
        new_id=False,
        overwrite=True,
        include_thinking=False,
        forced_target_id=real_id,
    )

    sidecars_removed = 0
    if purge_sidecars:
        if prov.id == "agy":
            base_dir = _agy_base_dir_for(out_path, app_data_dir=app_dir)
            br_dir = base_dir / "brain" / real_id
            if br_dir.is_dir():
                shutil.rmtree(br_dir, ignore_errors=True)
                sidecars_removed += 1
            ann = base_dir / "annotations" / f"{real_id}.pbtxt"
            if ann.is_file():
                ann.unlink(missing_ok=True)
                sidecars_removed += 1
            imp = base_dir / "implicit" / f"{real_id}.pb"
            if imp.is_file():
                imp.unlink(missing_ok=True)
                sidecars_removed += 1

            # Delete child subagent DBs & sidecars without touching main `real_id`
            cli_dir = get_agy_cli_dir()
            ide_dir = get_agy_ide_dir()
            sum_db = cli_dir / "conversation_summaries.db"
            for cid in child_ids:
                for bdir in {base_dir, cli_dir, ide_dir}:
                    cdb = bdir / "conversations" / f"{cid}.db"
                    if cdb.exists():
                        cdb.unlink(missing_ok=True)
                        sidecars_removed += 1
                    Path(str(cdb) + "-wal").unlink(missing_ok=True)
                    Path(str(cdb) + "-shm").unlink(missing_ok=True)
                    _evict_session_cache(cdb)
                    cbr = bdir / "brain" / cid
                    if cbr.is_dir():
                        shutil.rmtree(cbr, ignore_errors=True)
                        sidecars_removed += 1
                    (bdir / "annotations" / f"{cid}.pbtxt").unlink(missing_ok=True)
                    (bdir / "implicit" / f"{cid}.pb").unlink(missing_ok=True)
            if child_ids and sum_db.is_file():
                con = _connect_sqlite(sum_db)
                try:
                    for cid in child_ids:
                        for tbl in ("conversation_summaries", "conversations_fts", "conversation_parents"):
                            try:
                                con.execute(f"DELETE FROM {tbl} WHERE conversation_id = ?", (cid,))
                            except sqlite3.Error:
                                pass
                    con.commit()
                finally:
                    con.close()
        elif prov.id == "claude":
            fh_dir = get_claude_dir() / "file-history" / real_id
            if fh_dir.is_dir():
                shutil.rmtree(fh_dir, ignore_errors=True)
                sidecars_removed += 1
            sidecar_dir = out_path.with_suffix("")
            if sidecar_dir.is_dir():
                for child in list(sidecar_dir.iterdir()):
                    if child.name == "custom-title.json":
                        continue
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                    sidecars_removed += 1
                try:
                    if not any(sidecar_dir.iterdir()):
                        sidecar_dir.rmdir()
                except OSError:
                    pass

    _evict_session_cache(loc)
    _evict_session_cache(out_path)

    sum_stub = SessionSummary(
        session_id=real_id,
        source=prov.id,
        updated_at=trimmed_sess.updated_at,
        title=trimmed_sess.title,
        preview=trimmed_sess.preview,
        cwd=trimmed_sess.cwd,
        location=out_path,
        step_or_msg_count=len(cleaned_turns),
    )
    re_read = prov.read_session(out_path, summary=sum_stub)
    new_fp = prov.get_sync_fingerprint(sum_stub)
    new_hash = canonical_session_hash(re_read)
    _update_pair_after_trim(prov.id, real_id, new_fp, new_hash, re_read.title)

    after_bytes, _, _ = prov.get_session_size(
        real_id, out_path, summary=summary, include_children=False
    )
    freed_bytes = max(0, before_bytes - after_bytes)

    with _SYNC_LOCK:
        state = _load_state()
        _append_history(
            state,
            kind="trim",
            direction=prov.id,
            source=prov.id,
            target="",
            source_id=real_id,
            target_id="",
            title=re_read.title,
            cwd=re_read.cwd or "",
            status="ok",
            message=(
                f"Trimmed {turns_before} -> {len(re_read.turns)} turn(s), "
                f"{format_bytes(before_bytes)} -> {format_bytes(after_bytes)} "
                f"(freed {format_bytes(freed_bytes)})"
            ),
            resume_cmd=prov.resume_command(real_id),
        )
        _save_state(state)

    msg = (
        f"Lightened {prov.id} session {real_id[:8]}: "
        f"{turns_before} -> {len(re_read.turns)} turn(s), "
        f"{format_bytes(before_bytes)} -> {format_bytes(after_bytes)} "
        f"(freed {format_bytes(freed_bytes)})."
    )
    return {
        "ok": True,
        "source": prov.id,
        "id": real_id,
        "title": re_read.title,
        "turnsBefore": turns_before,
        "turnsAfter": len(re_read.turns),
        "beforeBytes": before_bytes,
        "beforeHuman": format_bytes(before_bytes),
        "afterBytes": after_bytes,
        "afterHuman": format_bytes(after_bytes),
        "freedBytes": freed_bytes,
        "freedHuman": format_bytes(freed_bytes),
        "sidecarsRemoved": sidecars_removed,
        "resumeCmd": prov.resume_command(real_id),
        "message": msg,
    }
