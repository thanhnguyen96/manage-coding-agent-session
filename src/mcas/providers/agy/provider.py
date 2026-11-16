from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import shutil
import sqlite3
import sys
import uuid

from mcas.cache import (
    _evict_session_cache,
    _fast_dir_size,
    _fast_file_size,
    _invalidate_size_cache,
)
from mcas.config import (
    get_agy_cli_dir,
    get_agy_ide_dir,
)
from mcas.errors import SyncError
from mcas.models import (
    CanonicalSession,
    CanonicalToolCall,
    CanonicalToolResult,
    CanonicalTurn,
    SessionSummary,
)
from mcas.providers.agy.pb_builders import (
    _build_chat_tool_call_pb,
    _build_step_metadata_pb,
    _build_trajectory_metadata_pb,
)
from mcas.providers.agy.schema import (
    CASCADE_STATUS_IDLE,
    CONVERSATION_DB_SCHEMA,
    SOURCE_MODEL,
    SOURCE_USER_EXPLICIT,
    STATUS_DONE,
    STATUS_ERROR,
    STEP_CHECKPOINT,
    STEP_GENERIC,
    STEP_PLANNER_RESPONSE,
    STEP_USER_INPUT,
    STOP_REASON_STOP_PATTERN,
    SUMMARIES_DB_SCHEMA,
    TERMINATION_REASON_IDLE,
    TRAJECTORY_SOURCE_CLI,
    TRAJECTORY_TYPE_CASCADE,
)
from mcas.providers.agy.storage import (
    _agy_base_dir_for,
    _agy_db_bundle_size,
    _agy_target_exists,
    _extract_agy_meta_from_db,
    _find_agy_descendants,
    _resolve_agy_db_path,
    get_agy_session_size,
)
from mcas.providers.base import AgentProvider
from mcas.util.fsio import _connect_sqlite
from mcas.util.pathconv import (
    file_uri_to_windows_path,
    normalize_windows_path,
    parse_workspace_uris,
    paths_match,
    windows_path_to_file_uri,
)
from mcas.util.protobuf import (
    decode_pb,
    pb_all,
    pb_first,
    pb_msg,
    pb_str,
    pb_str_val,
    pb_timestamp,
    pb_timestamp_val,
    pb_varint,
)
from mcas.util.text import (
    derive_title,
    format_bytes,
    is_valid_uuid,
    parse_tool_input_dict,
    truncate_preview,
)
from mcas.util.timefmt import (
    dt_to_ns,
    format_agy_sql_ts,
    parse_ts,
)

class AgyProvider(AgentProvider):
    id = "agy"
    name = "Antigravity"
    badge = "AGY"
    supported = True
    coming_soon = False

    def resume_command(self, session_id: str) -> str:
        return f"agy --conversation {session_id}"

    def get_session_size(
        self,
        session_id: str,
        location: Path,
        summary: SessionSummary | None = None,
        *,
        include_children: bool = True,
    ) -> tuple[int, int, int]:
        app_dir = (
            "antigravity"
            if (summary and "antigravity" in summary.extra.split(","))
            else "antigravity-cli"
        )
        child_ids = _find_agy_descendants(session_id) if include_children else None
        return get_agy_session_size(
            session_id, db_path=location, child_ids=child_ids, app_data_dir=app_dir
        )

    def get_sync_fingerprint(self, summary: SessionSummary) -> tuple[int, int]:
        db_path = summary.location
        max_mtime = 0
        for p in (db_path, Path(str(db_path) + "-wal")):
            try:
                if p.is_file():
                    max_mtime = max(max_mtime, p.stat().st_mtime_ns)
            except OSError:
                pass
        step_count = summary.step_or_msg_count
        if db_path.is_file():
            try:
                con = _connect_sqlite(db_path)
                try:
                    row = con.execute("SELECT COUNT(*) FROM steps").fetchone()
                    if row is not None:
                        step_count = int(row[0] or 0)
                finally:
                    con.close()
            except sqlite3.Error:
                pass
        return max_mtime, step_count

    def list_sessions(
        self, include_all: bool = False, cwd_filter: str | None = None
    ) -> list[SessionSummary]:
        cli_dir = get_agy_cli_dir()
        results: list[SessionSummary] = []
        all_indexed_ids: set[str] = set()
        children_map: dict[str, list[str]] = {}

        def collect_descendants(root_id: str) -> list[str]:
            if include_all:
                return []
            out: list[str] = []
            seen = {root_id}
            q = list(children_map.get(root_id, []))
            while q:
                cur = q.pop(0)
                if cur in seen:
                    continue
                seen.add(cur)
                out.append(cur)
                q.extend(children_map.get(cur, []))
            return out

        sum_db = cli_dir / "conversation_summaries.db"
        if sum_db.is_file():
            con = _connect_sqlite(sum_db)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT * FROM conversation_summaries ORDER BY last_modified_time DESC"
                ).fetchall()
                for row in rows:
                    cid = row["conversation_id"]
                    pid = row["parent_conversation_id"] or ""
                    if cid and pid:
                        children_map.setdefault(pid, []).append(cid)
                try:
                    for prow in con.execute("SELECT conversation_id, parent_id FROM conversation_parents"):
                        cid, pid = str(prow[0] or ""), str(prow[1] or "")
                        if cid and pid:
                            children_map.setdefault(pid, []).append(cid)
                except sqlite3.Error:
                    pass

                for row in rows:
                    cid = row["conversation_id"]
                    all_indexed_ids.add(cid)

                    app_dir = row["app_data_dir"] or "antigravity-cli"
                    nesting_depth = int(row["nesting_depth"] or 0)
                    step_count = int(row["step_count"] or 0)

                    if not include_all:
                        if app_dir != "antigravity-cli" or nesting_depth != 0 or step_count == 0:
                            continue

                    db_path = _resolve_agy_db_path(cid, app_dir)
                    if not db_path:
                        continue

                    uris = parse_workspace_uris(row["workspace_uris"])
                    cwd = None
                    for uri in uris:
                        cwd = file_uri_to_windows_path(uri)
                        if cwd:
                            break
                    if cwd_filter and not paths_match(cwd, cwd_filter):
                        continue

                    title = (row["title"] or "").strip()
                    preview = (row["preview"] or "").strip()
                    try:
                        db_mtime_dt = datetime.fromtimestamp(
                            db_path.stat().st_mtime, tz=timezone.utc
                        )
                    except OSError:
                        db_mtime_dt = None
                    updated = parse_ts(row["last_modified_time"], fallback=db_mtime_dt)
                    extra_parts = []
                    if nesting_depth:
                        extra_parts.append(f"depth={nesting_depth}")
                    if app_dir != "antigravity-cli":
                        extra_parts.append(app_dir)
                    desc_ids = collect_descendants(cid)
                    total_bytes, _, _ = get_agy_session_size(
                        cid, db_path=db_path, child_ids=desc_ids, app_data_dir=app_dir
                    )
                    results.append(
                        SessionSummary(
                            session_id=cid,
                            source="agy",
                            updated_at=updated,
                            title=title or truncate_preview(preview, 80) or "(untitled)",
                            preview=truncate_preview(preview or title, 120),
                            cwd=cwd,
                            location=db_path,
                            step_or_msg_count=step_count,
                            extra=",".join(extra_parts),
                            size_bytes=total_bytes,
                        )
                    )
            except sqlite3.Error as exc:
                print(f"Warning: failed to read {sum_db}: {exc}", file=sys.stderr)
            finally:
                con.close()

        conv_dir = cli_dir / "conversations"
        if conv_dir.is_dir():
            for db_path in conv_dir.glob("*.db"):
                cid = db_path.stem
                if cid in all_indexed_ids:
                    continue
                cwd, _created, nesting_depth, step_count = _extract_agy_meta_from_db(db_path)
                if not include_all and (nesting_depth != 0 or step_count == 0):
                    continue
                if cwd_filter and not paths_match(cwd, cwd_filter):
                    continue
                mtime = datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc)
                extra = f"depth={nesting_depth},unindexed" if nesting_depth else "unindexed"
                total_bytes, _, _ = get_agy_session_size(cid, db_path=db_path)
                results.append(
                    SessionSummary(
                        session_id=cid,
                        source="agy",
                        updated_at=mtime,
                        title="(unindexed agy session)",
                        preview="",
                        cwd=cwd,
                        location=db_path,
                        step_or_msg_count=step_count,
                        extra=extra,
                        size_bytes=total_bytes,
                    )
                )

        results.sort(key=lambda s: s.updated_at, reverse=True)
        return results

    def find_session(
        self, id_or_latest: str, cwd: str | None = None, all_cwd: bool = False
    ) -> tuple[Path, SessionSummary | None]:
        if id_or_latest.lower() == "latest":
            target_cwd = None if all_cwd else normalize_windows_path(cwd or os.getcwd())
            candidates = self.list_sessions(include_all=False, cwd_filter=target_cwd)
            non_empty = [c for c in candidates if c.step_or_msg_count > 0 and c.location.is_file()]
            chosen_pool = non_empty or [c for c in candidates if c.location.is_file()]
            if not chosen_pool:
                scope = "any workspace" if all_cwd else f"workspace '{target_cwd}'"
                raise SyncError(
                    f"No Antigravity (agy) conversation found for {scope}. "
                    "Run `list` to inspect available sessions, pass an explicit conversation ID, "
                    "or use `--all-cwd`."
                )
            chosen = chosen_pool[0]
            return chosen.location, chosen

        direct = _resolve_agy_db_path(id_or_latest)
        if direct:
            summaries = {s.session_id: s for s in self.list_sessions(include_all=True)}
            return direct, summaries.get(direct.stem)

        cli_dir = get_agy_cli_dir()
        ide_dir = get_agy_ide_dir()
        matches: list[Path] = []
        for base in (cli_dir / "conversations", ide_dir / "conversations"):
            if base.is_dir():
                for p in base.glob(f"{id_or_latest}*.db"):
                    if p not in matches:
                        matches.append(p)
        if len(matches) == 1:
            p = matches[0]
            summaries = {s.session_id: s for s in self.list_sessions(include_all=True)}
            return p, summaries.get(p.stem)
        if len(matches) > 1:
            ids = ", ".join(m.stem for m in matches[:5])
            raise SyncError(f"Ambiguous agy ID prefix '{id_or_latest}': matches {ids}")
        raise SyncError(f"Antigravity conversation '{id_or_latest}' not found.")

    def read_session(
        self, location: Path, summary: SessionSummary | None = None
    ) -> CanonicalSession:
        conv_id = location.stem
        con = _connect_sqlite(location)
        try:
            meta_row = con.execute(
                "SELECT trajectory_id, cascade_id FROM trajectory_meta LIMIT 1"
            ).fetchone()
            if meta_row and meta_row[1]:
                conv_id = meta_row[1]

            cwd, traj_created, _depth, _cnt = _extract_agy_meta_from_db(location)
            if not cwd and summary and summary.cwd:
                cwd = summary.cwd

            turns: list[CanonicalTurn] = []
            checkpoint_title = ""
            first_ts: datetime | None = traj_created
            last_ts: datetime | None = None

            rows = con.execute(
                "SELECT idx, step_type, status, metadata, step_payload FROM steps ORDER BY idx ASC"
            ).fetchall()
        except sqlite3.Error as exc:
            raise SyncError(f"Failed to read Antigravity DB {location}: {exc}") from exc
        finally:
            con.close()

        fallback_ts = datetime.fromtimestamp(location.stat().st_mtime, tz=timezone.utc)

        for _idx, step_type, status, meta_blob, payload_blob in rows:
            meta_fields = decode_pb(meta_blob) if meta_blob else []
            payload_fields = decode_pb(payload_blob) if payload_blob else []
            if not meta_fields:
                meta_bytes = pb_first(payload_fields, 5, 2)
                if isinstance(meta_bytes, (bytes, bytearray)):
                    meta_fields = decode_pb(meta_bytes)

            step_ts = (
                pb_timestamp_val(meta_fields, 1)
                or pb_timestamp_val(meta_fields, 8)
                or last_ts
                or first_ts
                or fallback_ts
            )
            if first_ts is None:
                first_ts = step_ts
            last_ts = step_ts

            if step_type == STEP_USER_INPUT:
                sub_raw = pb_first(payload_fields, 19, 2)
                if not isinstance(sub_raw, (bytes, bytearray)):
                    continue
                sub = decode_pb(sub_raw)
                text = pb_str_val(sub, 2)
                if not text:
                    items = []
                    for item_raw in pb_all(sub, 3, 2):
                        item_fields = decode_pb(item_raw)
                        item_text = pb_str_val(item_fields, 1)
                        if item_text:
                            items.append(item_text)
                    text = "\n".join(items)
                if not text:
                    text = pb_str_val(sub, 1)
                if text.strip():
                    turns.append(CanonicalTurn(role="user", timestamp=step_ts, text=text))

            elif step_type == STEP_PLANNER_RESPONSE:
                sub_raw = pb_first(payload_fields, 20, 2)
                if not isinstance(sub_raw, (bytes, bytearray)):
                    continue
                sub = decode_pb(sub_raw)
                text = pb_str_val(sub, 8) or pb_str_val(sub, 1)
                thinking = pb_str_val(sub, 3)
                tool_calls: list[CanonicalToolCall] = []
                for tc_raw in pb_all(sub, 7, 2):
                    tc_fields = decode_pb(tc_raw)
                    tc_id = pb_str_val(tc_fields, 1)
                    tc_name = pb_str_val(tc_fields, 2) or "tool"
                    tc_args = pb_str_val(tc_fields, 3) or "{}"
                    tool_calls.append(
                        CanonicalToolCall(id=tc_id, name=tc_name, arguments_json=tc_args)
                    )
                if text.strip() or thinking.strip() or tool_calls:
                    turns.append(
                        CanonicalTurn(
                            role="assistant",
                            timestamp=step_ts,
                            text=text,
                            thinking=thinking,
                            tool_calls=tool_calls,
                        )
                    )

            elif step_type == STEP_GENERIC:
                sub_raw = pb_first(payload_fields, 140, 2)
                if not isinstance(sub_raw, (bytes, bytearray)):
                    continue
                sub = decode_pb(sub_raw)
                res_raw = pb_first(sub, 2, 2)
                result_text = ""
                if isinstance(res_raw, (bytes, bytearray)):
                    res_fields = decode_pb(res_raw)
                    result_text = pb_str_val(res_fields, 1)

                tc_raw = pb_first(meta_fields, 4, 2)
                tc_id = ""
                tc_name = "tool"
                tc_args = "{}"
                if isinstance(tc_raw, (bytes, bytearray)):
                    tc_fields = decode_pb(tc_raw)
                    tc_id = pb_str_val(tc_fields, 1)
                    tc_name = pb_str_val(tc_fields, 2) or "tool"
                    tc_args = pb_str_val(tc_fields, 3) or "{}"

                is_err = status == STATUS_ERROR
                turns.append(
                    CanonicalTurn(
                        role="tool_result",
                        timestamp=step_ts,
                        tool_results=[
                            CanonicalToolResult(
                                tool_use_id=tc_id,
                                name=tc_name,
                                arguments_json=tc_args,
                                content=result_text,
                                is_error=is_err,
                            )
                        ],
                    )
                )

            elif step_type == STEP_CHECKPOINT:
                sub_raw = pb_first(payload_fields, 30, 2)
                if isinstance(sub_raw, (bytes, bytearray)):
                    sub = decode_pb(sub_raw)
                    cp_title = pb_str_val(sub, 10) or pb_str_val(sub, 4)
                    if cp_title and not checkpoint_title:
                        checkpoint_title = cp_title
                    compacted_summary = pb_str_val(sub, 5)
                    if compacted_summary.strip():
                        turns.append(
                            CanonicalTurn(
                                role="user",
                                timestamp=step_ts,
                                text=f"[Compacted session summary]\n{compacted_summary.strip()}",
                            )
                        )

        first_user_text = next((t.text for t in turns if t.role == "user" and t.text.strip()), "")
        title = (summary.title if summary else "") or checkpoint_title
        if title == "(untitled)":
            title = ""
        preview = (summary.preview if summary else "") or truncate_preview(first_user_text)
        title = derive_title(title, preview)
        created_at = first_ts or fallback_ts
        updated_at = (summary.updated_at if summary else None) or last_ts or fallback_ts

        return CanonicalSession(
            session_id=conv_id,
            source="agy",
            title=title,
            preview=preview,
            cwd=cwd,
            created_at=created_at,
            updated_at=updated_at,
            turns=turns,
        )

    def write_session(
        self,
        session: CanonicalSession,
        target_cwd: str | None,
        new_id: bool = False,
        overwrite: bool = False,
        include_thinking: bool = False,
        forced_target_id: str | None = None,
    ) -> tuple[str, Path, bool]:
        cli_dir = get_agy_cli_dir()
        norm_cwd = normalize_windows_path(target_cwd) if target_cwd else None
        workspace_uri = windows_path_to_file_uri(norm_cwd) if norm_cwd else None

        regenerated = False
        if forced_target_id:
            conv_id = forced_target_id.strip()
        elif new_id or not is_valid_uuid(session.session_id):
            conv_id = str(uuid.uuid4())
        else:
            conv_id = session.session_id.lower()
            if _agy_target_exists(conv_id) and not overwrite:
                conv_id = str(uuid.uuid4())
                regenerated = True

        turns = list(session.turns)
        if not turns:
            raise SyncError("No valid conversation messages found to convert.")

        if not any(t.role == "user" and t.text.strip() for t in turns):
            turns.insert(
                0,
                CanonicalTurn(
                    role="user",
                    timestamp=session.created_at,
                    text="[Continued conversation]",
                ),
            )

        traj_id = str(uuid.uuid4())
        init_state_id = str(uuid.uuid4())
        execution_id = str(uuid.uuid4())

        step_rows: list[tuple[int, int, int, int, bytes, bytes]] = []
        user_offsets: list[list[int]] = []
        user_body_parts: list[str] = []
        user_byte_len = 0
        agent_offsets: list[list[int]] = []
        agent_body_parts: list[str] = []
        agent_byte_len = 0

        last_user_idx = -1
        last_user_time = session.created_at
        first_user_text = ""
        pending_tools: list[CanonicalToolCall] = []

        def append_generic_step(tc_meta: CanonicalToolCall, res_text: str, ts: datetime) -> None:
            idx = len(step_rows)
            meta_pb = _build_step_metadata_pb(
                ts,
                SOURCE_MODEL,
                traj_id,
                idx,
                conv_id,
                execution_id,
                tool_call=tc_meta,
            )
            gen_parts: list[bytes] = []
            parsed_args = parse_tool_input_dict(tc_meta.arguments_json)
            for k, v in parsed_args.items():
                v_str = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                gen_parts.append(pb_msg(1, pb_str(1, str(k)) + pb_str(2, v_str)))
            gen_parts.append(pb_msg(2, pb_str(1, res_text or "(empty output)")))
            gen_pb = b"".join(gen_parts)

            step_pb = (
                pb_varint(1, STEP_GENERIC)
                + pb_varint(4, STATUS_DONE)
                + pb_msg(5, meta_pb)
                + pb_msg(140, gen_pb)
            )
            step_rows.append((idx, STEP_GENERIC, STATUS_DONE, 0, meta_pb, step_pb))

        def flush_pending_agy_tools(ts: datetime) -> None:
            while pending_tools:
                tc_unpaired = pending_tools.pop(0)
                append_generic_step(
                    tc_unpaired, "[Interrupted or no tool output recorded]", ts
                )

        for turn in turns:
            if turn.role == "user":
                text = turn.text
                if not text.strip():
                    continue
                flush_pending_agy_tools(turn.timestamp)
                idx = len(step_rows)
                if not first_user_text:
                    first_user_text = text
                meta_pb = _build_step_metadata_pb(
                    turn.timestamp, SOURCE_USER_EXPLICIT, traj_id, idx, conv_id, execution_id
                )
                ui_pb = pb_str(2, text) + pb_msg(3, pb_str(1, text))
                step_pb = (
                    pb_varint(1, STEP_USER_INPUT)
                    + pb_varint(4, STATUS_DONE)
                    + pb_msg(5, meta_pb)
                    + pb_msg(19, ui_pb)
                )
                step_rows.append((idx, STEP_USER_INPUT, STATUS_DONE, 0, meta_pb, step_pb))

                user_offsets.append([idx, user_byte_len])
                line = text + "\n"
                user_body_parts.append(line)
                user_byte_len += len(line.encode("utf-8"))
                last_user_idx = idx
                last_user_time = turn.timestamp

            elif turn.role == "assistant":
                body_text = turn.text.strip()
                thinking_text = turn.thinking.strip()
                if include_thinking and thinking_text:
                    thought_block = f"<thought>\n{thinking_text}\n</thought>"
                    body_text = f"{thought_block}\n\n{body_text}" if body_text else thought_block

                if not body_text and not thinking_text and not turn.tool_calls:
                    continue

                flush_pending_agy_tools(turn.timestamp)
                idx = len(step_rows)
                meta_pb = _build_step_metadata_pb(
                    turn.timestamp, SOURCE_MODEL, traj_id, idx, conv_id, execution_id
                )
                pr_parts: list[bytes] = []
                if body_text:
                    pr_parts.append(pb_str(1, body_text))
                    pr_parts.append(pb_str(8, body_text))
                if thinking_text:
                    # Never synthesize field 14 (`thinking_signature`)
                    pr_parts.append(pb_str(3, thinking_text))
                pr_parts.append(pb_str(6, f"bot-{uuid.uuid4()}"))
                for tc in turn.tool_calls:
                    tc_fixed = CanonicalToolCall(
                        id=tc.id or f"call_{uuid.uuid4().hex[:16]}",
                        name=tc.name or "tool",
                        arguments_json=tc.arguments_json or "{}",
                    )
                    pr_parts.append(pb_msg(7, _build_chat_tool_call_pb(tc_fixed)))
                    pending_tools.append(tc_fixed)
                pr_parts.append(pb_varint(12, STOP_REASON_STOP_PATTERN))
                pr_pb = b"".join(pr_parts)

                step_pb = (
                    pb_varint(1, STEP_PLANNER_RESPONSE)
                    + pb_varint(4, STATUS_DONE)
                    + pb_msg(5, meta_pb)
                    + pb_msg(20, pr_pb)
                )
                step_rows.append((idx, STEP_PLANNER_RESPONSE, STATUS_DONE, 0, meta_pb, step_pb))

                agent_offsets.append([idx, agent_byte_len])
                line = (body_text or "") + "\n"
                agent_body_parts.append(line)
                agent_byte_len += len(line.encode("utf-8"))

            elif turn.role == "tool_result":
                for tr in turn.tool_results:
                    matched_tc: CanonicalToolCall | None = None
                    if tr.tool_use_id:
                        for i, ptc in enumerate(pending_tools):
                            if ptc.id == tr.tool_use_id:
                                matched_tc = pending_tools.pop(i)
                                break
                    if matched_tc is None and pending_tools:
                        matched_tc = pending_tools.pop(0)

                    if matched_tc is not None:
                        append_generic_step(matched_tc, tr.content, turn.timestamp)
                    else:
                        text = f"[Tool output ({tr.name or 'tool'})]\n{tr.content or '(empty output)'}"
                        idx = len(step_rows)
                        if not first_user_text:
                            first_user_text = text
                        meta_pb = _build_step_metadata_pb(
                            turn.timestamp, SOURCE_USER_EXPLICIT, traj_id, idx, conv_id, execution_id
                        )
                        ui_pb = pb_str(2, text) + pb_msg(3, pb_str(1, text))
                        step_pb = (
                            pb_varint(1, STEP_USER_INPUT)
                            + pb_varint(4, STATUS_DONE)
                            + pb_msg(5, meta_pb)
                            + pb_msg(19, ui_pb)
                        )
                        step_rows.append((idx, STEP_USER_INPUT, STATUS_DONE, 0, meta_pb, step_pb))
                        user_offsets.append([idx, user_byte_len])
                        line = text + "\n"
                        user_body_parts.append(line)
                        user_byte_len += len(line.encode("utf-8"))
                        last_user_idx = idx
                        last_user_time = turn.timestamp

        flush_pending_agy_tools(session.updated_at)

        if not step_rows:
            raise SyncError("No convertible steps produced.")

        conv_dir = cli_dir / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        db_path = conv_dir / f"{conv_id}.db"
        tmp_db_path = conv_dir / f".{conv_id}.{uuid.uuid4().hex[:8]}.db.tmp"

        traj_meta_blob = _build_trajectory_metadata_pb(
            workspace_uri, session.created_at, init_state_id, conv_id
        )

        try:
            con = _connect_sqlite(tmp_db_path)
            try:
                con.executescript(CONVERSATION_DB_SCHEMA)
                con.execute(
                    "INSERT INTO trajectory_meta (trajectory_id, cascade_id, trajectory_type, source) VALUES (?, ?, ?, ?)",
                    (traj_id, conv_id, TRAJECTORY_TYPE_CASCADE, TRAJECTORY_SOURCE_CLI),
                )
                con.execute(
                    "INSERT INTO trajectory_metadata_blob (id, data) VALUES ('main', ?)",
                    (traj_meta_blob,),
                )
                con.executemany(
                    "INSERT INTO steps (idx, step_type, status, has_subtrajectory, metadata, step_payload, step_format) VALUES (?, ?, ?, ?, ?, ?, 0)",
                    step_rows,
                )
                con.commit()
            finally:
                con.close()
            for sidecar in (
                Path(str(db_path) + "-wal"),
                Path(str(db_path) + "-shm"),
            ):
                sidecar.unlink(missing_ok=True)
            os.replace(tmp_db_path, db_path)
            for sidecar in (
                Path(str(db_path) + "-wal"),
                Path(str(db_path) + "-shm"),
            ):
                sidecar.unlink(missing_ok=True)
        except Exception:
            for leftover in (
                tmp_db_path,
                Path(str(tmp_db_path) + "-wal"),
                Path(str(tmp_db_path) + "-shm"),
            ):
                leftover.unlink(missing_ok=True)
            raise

        preview = session.preview or truncate_preview(first_user_text)
        title = derive_title(session.title, preview)
        workspace_uris_json = json.dumps([workspace_uri]) if workspace_uri else ""

        raw_summary_parts = [
            pb_str(1, preview),
            pb_varint(2, len(step_rows)),
            pb_timestamp(3, session.updated_at),
            pb_str(4, traj_id),
            pb_varint(5, CASCADE_STATUS_IDLE),
            pb_timestamp(7, session.created_at),
            pb_timestamp(10, last_user_time),
            pb_msg(15, pb_str(1, title)),
        ]
        if last_user_idx >= 0:
            raw_summary_parts.append(pb_varint(16, last_user_idx))
        raw_summary_parts.extend(
            [
                pb_msg(17, traj_meta_blob),
                pb_varint(20, TRAJECTORY_SOURCE_CLI),
                pb_varint(22, TRAJECTORY_TYPE_CASCADE),
                pb_varint(28, TERMINATION_REASON_IDLE),
            ]
        )
        raw_summary_blob = b"".join(raw_summary_parts)

        cli_dir.mkdir(parents=True, exist_ok=True)
        sum_db_path = cli_dir / "conversation_summaries.db"
        sum_exists = sum_db_path.is_file()
        sum_con = _connect_sqlite(sum_db_path)
        try:
            has_table = False
            if sum_exists:
                has_table = (
                    sum_con.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_summaries'"
                    ).fetchone()
                    is not None
                )
            if not has_table:
                sum_con.execute("PRAGMA journal_mode = wal")
                sum_con.executescript(SUMMARIES_DB_SCHEMA)
                sum_con.execute(
                    "INSERT OR IGNORE INTO search_schema_version (version) VALUES (3)"
                )
            sum_con.execute(
                """
                INSERT OR REPLACE INTO conversation_summaries (
                    conversation_id, title, preview, step_count, last_modified_time,
                    workspace_uris, status, source, project_id, agent_name,
                    parent_conversation_id, nesting_depth, battle_id, winning_conversation_id,
                    not_fully_idle, killed, last_user_input_time, last_user_input_step_index,
                    app_data_dir, raw_summary, group_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv_id,
                    title,
                    preview,
                    len(step_rows),
                    format_agy_sql_ts(session.updated_at),
                    workspace_uris_json,
                    "CASCADE_RUN_STATUS_IDLE",
                    "",
                    "default-cli-project",
                    "",
                    "",
                    0,
                    "",
                    "",
                    0,
                    0,
                    format_agy_sql_ts(last_user_time),
                    last_user_idx,
                    "antigravity-cli",
                    raw_summary_blob,
                    "",
                ),
            )
            sum_con.execute(
                "DELETE FROM conversations_fts WHERE conversation_id = ?", (conv_id,)
            )
            ts_ns = dt_to_ns(session.updated_at)
            ws_basename = (Path(norm_cwd).name or norm_cwd) if norm_cwd else ""
            sum_con.execute(
                """
                INSERT INTO conversations_fts (
                    conversation_id, chunk_index, chunk_first_step, chunk_last_step,
                    last_modified_time, step_count, last_step_created_at, archived,
                    user_step_byte_offsets, agent_step_byte_offsets,
                    title, workspace, project, user_body, agent_body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv_id,
                    -1,
                    0,
                    0,
                    ts_ns,
                    len(step_rows),
                    ts_ns,
                    0,
                    "",
                    "",
                    title,
                    ws_basename,
                    "CLI Project",
                    "",
                    "",
                ),
            )
            sum_con.execute(
                """
                INSERT INTO conversations_fts (
                    conversation_id, chunk_index, chunk_first_step, chunk_last_step,
                    last_modified_time, step_count, last_step_created_at, archived,
                    user_step_byte_offsets, agent_step_byte_offsets,
                    title, workspace, project, user_body, agent_body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv_id,
                    0,
                    0,
                    len(step_rows) - 1,
                    0,
                    0,
                    0,
                    0,
                    json.dumps(user_offsets, separators=(",", ":")).encode("utf-8"),
                    json.dumps(agent_offsets, separators=(",", ":")).encode("utf-8"),
                    "",
                    "",
                    "",
                    "".join(user_body_parts),
                    "".join(agent_body_parts),
                ),
            )
            sum_con.commit()
        finally:
            sum_con.close()

        _evict_session_cache(db_path)
        return conv_id, db_path, regenerated

    def delete_session(self, session_id: str) -> dict[str, Any]:
        sid = session_id.strip()
        if not sid or sid.lower() == "latest":
            raise SyncError("An explicit Antigravity conversation ID is required for delete.")

        db_path: Path | None = None
        summary: SessionSummary | None = None
        try:
            db_path, summary = self.find_session(sid)
            conv_id = summary.session_id if summary else db_path.stem
        except SyncError:
            conv_id = sid
            sum_db = get_agy_cli_dir() / "conversation_summaries.db"
            found_in_db = False
            if sum_db.is_file():
                con = _connect_sqlite(sum_db)
                try:
                    row = con.execute(
                        "SELECT conversation_id FROM conversation_summaries WHERE conversation_id = ?",
                        (sid,),
                    ).fetchone()
                    if row:
                        found_in_db = True
                except sqlite3.Error:
                    pass
                finally:
                    con.close()
            if not found_in_db:
                raise

        child_ids = _find_agy_descendants(conv_id)
        all_ids = [conv_id] + child_ids

        cli_dir = get_agy_cli_dir()
        ide_dir = get_agy_ide_dir()
        primary_base = _agy_base_dir_for(db_path)
        search_bases = [primary_base]
        for cand_base in (cli_dir, ide_dir):
            if cand_base not in search_bases:
                search_bases.append(cand_base)

        freed_bytes = 0
        for target_cid in all_ids:
            # Remove conversation .db (+ WAL/SHM)
            db_candidates: list[Path] = []
            if target_cid == conv_id and db_path is not None:
                db_candidates.append(db_path)
            for bdir in search_bases:
                cand_db = bdir / "conversations" / f"{target_cid}.db"
                if cand_db not in db_candidates:
                    db_candidates.append(cand_db)
            for p_db in db_candidates:
                freed_bytes += _agy_db_bundle_size(p_db)
                if p_db.exists():
                    p_db.unlink(missing_ok=True)
                Path(str(p_db) + "-wal").unlink(missing_ok=True)
                Path(str(p_db) + "-shm").unlink(missing_ok=True)
                _evict_session_cache(p_db)

            # Remove brain, annotations, implicit sidecars from matching base(s)
            bases_for_sidecar = (
                [primary_base]
                if (target_cid == conv_id and db_path is not None)
                else search_bases
            )
            for bdir in bases_for_sidecar:
                br_dir = bdir / "brain" / target_cid
                if br_dir.is_dir():
                    freed_bytes += _fast_dir_size(br_dir)
                    shutil.rmtree(br_dir, ignore_errors=True)
                ann_file = bdir / "annotations" / f"{target_cid}.pbtxt"
                if ann_file.is_file():
                    freed_bytes += _fast_file_size(ann_file)
                    ann_file.unlink(missing_ok=True)
                imp_file = bdir / "implicit" / f"{target_cid}.pb"
                if imp_file.is_file():
                    freed_bytes += _fast_file_size(imp_file)
                    imp_file.unlink(missing_ok=True)

        sum_db = cli_dir / "conversation_summaries.db"
        if sum_db.is_file():
            con = _connect_sqlite(sum_db)
            try:
                for target_cid in all_ids:
                    for tbl in (
                        "conversation_summaries",
                        "conversations_fts",
                        "conversation_parents",
                    ):
                        try:
                            con.execute(f"DELETE FROM {tbl} WHERE conversation_id = ?", (target_cid,))
                        except sqlite3.Error:
                            pass
                con.commit()
            finally:
                con.close()

        _invalidate_size_cache()
        return {
            "source": "agy",
            "id": conv_id,
            "deleted": True,
            "freedBytes": freed_bytes,
            "freedHuman": format_bytes(freed_bytes),
            "childrenDeleted": len(child_ids),
        }
