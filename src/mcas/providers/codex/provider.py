"""Codex CLI AgentProvider implementation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import sys
import uuid

from mcas.cache import (
    _evict_session_cache,
    _fast_file_size,
    _invalidate_size_cache,
)
from mcas.config import (
    get_codex_archived_dir,
    get_codex_sessions_dir,
)
from mcas.errors import SyncError
from mcas.models import (
    CanonicalSession,
    CanonicalToolCall,
    CanonicalToolResult,
    CanonicalTurn,
    SessionSummary,
)
from mcas.providers.base import AgentProvider
from mcas.providers.codex.convert import (
    _extract_codex_reasoning_text,
    _extract_codex_text_blocks,
    _extract_codex_user_message_text,
    _normalize_tool_args_json,
    _normalize_turns_for_codex,
)
from mcas.providers.codex.storage import (
    _assert_within_codex_dir,
    _codex_target_exists,
    _delete_codex_sqlite_thread,
    _extract_codex_id_from_file,
    _extract_event_msg_text,
    _extract_valid_payload_id,
    _iter_codex_rollouts,
    _normalize_codex_id,
    _read_codex_title_overrides,
    _scan_codex_summary,
    _upsert_codex_sqlite_thread,
    _validate_codex_id_token,
    get_codex_session_size,
)
from mcas.util.pathconv import (
    normalize_windows_path,
    paths_match,
)
from mcas.util.text import (
    derive_title,
    format_bytes,
    is_valid_uuid,
    truncate_preview,
)
from mcas.util.timefmt import (
    format_iso_utc,
    parse_ts,
)


def _apply_title_override(
    summary: SessionSummary,
    custom_names: dict[str, str],
    db_titles: dict[str, str],
) -> SessionSummary:
    """Apply thread name overrides from `session_index.jsonl` or `state_5.sqlite`."""
    nid = _normalize_codex_id(summary.session_id)
    custom = custom_names.get(nid)
    if custom:
        return replace(summary, title=custom)
    if summary.title == "(untitled)":
        db_title = db_titles.get(nid)
        if db_title:
            return replace(summary, title=truncate_preview(db_title, 80))
    return summary


class CodexProvider(AgentProvider):
    """Session provider for OpenAI Codex CLI (`~/.codex`)."""

    id = "codex"
    name = "Codex"
    badge = "CODEX"
    supported = True
    coming_soon = False

    def resume_command(self, session_id: str) -> str:
        return f"codex resume {session_id}"

    def session_id_from_location(self, location: Path) -> str:
        return _extract_codex_id_from_file(location)

    def get_session_size(
        self,
        session_id: str,
        location: Path,
        summary: SessionSummary | None = None,
        *,
        include_children: bool = True,
    ) -> tuple[int, int, int]:
        return get_codex_session_size(jsonl_path=location)

    def get_sync_fingerprint(self, summary: SessionSummary) -> tuple[int, int]:
        try:
            mtime_ns = summary.location.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        fresh = _scan_codex_summary(summary.location)
        count = fresh.step_or_msg_count if fresh else summary.step_or_msg_count
        return mtime_ns, count

    def list_sessions(
        self, include_all: bool = False, cwd_filter: str | None = None
    ) -> list[SessionSummary]:
        rollouts = _iter_codex_rollouts(include_archived=include_all)
        if not rollouts:
            return []

        custom_names, db_titles = _read_codex_title_overrides()
        by_id: dict[str, SessionSummary] = {}

        for jsonl_path, is_archived in rollouts:
            summary = _scan_codex_summary(jsonl_path, is_archived=is_archived)
            if not summary:
                continue
            extras = [x.strip() for x in summary.extra.split(",") if x.strip()]
            is_subagent = any(x.startswith("depth=") and x != "depth=0" for x in extras)
            if not include_all and (is_subagent or is_archived or summary.step_or_msg_count == 0):
                continue
            if cwd_filter and (not summary.cwd or not paths_match(summary.cwd, cwd_filter)):
                continue

            enriched = _apply_title_override(summary, custom_names, db_titles)
            existing = by_id.get(enriched.session_id)
            if existing is None or (
                "archived" in existing.extra.split(",") and not is_archived
            ):
                by_id[enriched.session_id] = enriched

        results = list(by_id.values())
        results.sort(key=lambda s: s.updated_at, reverse=True)
        return results

    def find_session(
        self, id_or_latest: str, cwd: str | None = None, all_cwd: bool = False
    ) -> tuple[Path, SessionSummary | None]:
        raw_query = (id_or_latest or "").strip()
        if raw_query.lower() == "latest":
            target_cwd = None if all_cwd else normalize_windows_path(cwd or os.getcwd())
            candidates = self.list_sessions(include_all=False, cwd_filter=target_cwd)
            if not candidates:
                scope = "any workspace" if all_cwd else f"workspace '{target_cwd}'"
                raise SyncError(
                    f"No Codex session found for {scope}. "
                    "Run `list` to inspect available sessions, pass an explicit session ID, "
                    "or use `--all-cwd`."
                )
            return candidates[0].location, candidates[0]

        query = _validate_codex_id_token(raw_query)
        query_lower = query.lower()

        rollouts = _iter_codex_rollouts(include_archived=True)
        if not rollouts:
            raise SyncError(f"Codex session '{id_or_latest}' not found.")

        custom_names, db_titles = _read_codex_title_overrides()
        exact_matches: list[tuple[Path, SessionSummary, bool]] = []
        prefix_matches: list[tuple[Path, SessionSummary, bool]] = []

        for path, is_archived in rollouts:
            summary = _scan_codex_summary(path, is_archived=is_archived)
            if summary is None:
                continue
            enriched = _apply_title_override(summary, custom_names, db_titles)
            sid_lower = enriched.session_id.lower()
            if sid_lower == query_lower:
                exact_matches.append((path, enriched, is_archived))
            elif sid_lower.startswith(query_lower):
                prefix_matches.append((path, enriched, is_archived))

        def pick_match(
            pool: list[tuple[Path, SessionSummary, bool]]
        ) -> tuple[Path, SessionSummary]:
            if cwd:
                for p, s, _arch in pool:
                    if s.cwd and paths_match(s.cwd, cwd):
                        return p, s
            for p, s, arch in pool:
                if not arch:
                    return p, s
            return pool[0][0], pool[0][1]

        if exact_matches:
            return pick_match(exact_matches)
        if len(prefix_matches) == 1:
            return prefix_matches[0][0], prefix_matches[0][1]
        if len(prefix_matches) > 1:
            ids = ", ".join(s.session_id for _, s, _ in prefix_matches[:5])
            raise SyncError(f"Ambiguous Codex session prefix '{id_or_latest}': matches {ids}")

        raise SyncError(f"Codex session '{id_or_latest}' not found.")

    def read_session(
        self, location: Path, summary: SessionSummary | None = None
    ) -> CanonicalSession:
        session_id = _extract_codex_id_from_file(location)
        seen_session_meta = False
        cwd: str | None = None
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        fallback_ts = datetime.fromtimestamp(location.stat().st_mtime, tz=timezone.utc)

        turns: list[CanonicalTurn] = []
        event_turns: list[CanonicalTurn] = []
        event_first_user = ""
        tool_lookup: dict[str, CanonicalToolCall] = {}
        corrupt_lines = 0

        pending_ts: datetime | None = None
        pending_texts: list[str] = []
        pending_thinking: list[str] = []
        pending_tools: list[CanonicalToolCall] = []

        def flush_assistant() -> None:
            nonlocal pending_ts, pending_texts, pending_thinking, pending_tools
            if pending_texts or pending_thinking or pending_tools:
                turns.append(
                    CanonicalTurn(
                        role="assistant",
                        timestamp=pending_ts or last_ts or fallback_ts,
                        text="\n\n".join(pending_texts),
                        thinking="\n\n".join(pending_thinking),
                        tool_calls=list(pending_tools),
                    )
                )
            pending_ts = None
            pending_texts = []
            pending_thinking = []
            pending_tools = []

        def record_tool_call(tc_id: str, tc_name: str, args_json: str, ts: datetime) -> None:
            nonlocal pending_ts
            pending_ts = ts
            tc = CanonicalToolCall(id=tc_id, name=tc_name, arguments_json=args_json)
            pending_tools.append(tc)
            if tc_id:
                tool_lookup[tc_id] = tc

        with location.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    corrupt_lines += 1
                    continue
                if not isinstance(rec, dict):
                    continue

                if rec.get("timestamp"):
                    ts = parse_ts(rec["timestamp"], fallback=last_ts or fallback_ts)
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
                else:
                    ts = last_ts or fallback_ts

                rtype = rec.get("type")
                payload = rec.get("payload")
                if not isinstance(payload, dict):
                    continue

                if rtype == "session_meta":
                    if not seen_session_meta:
                        seen_session_meta = True
                        valid_id = _extract_valid_payload_id(payload)
                        if valid_id:
                            session_id = valid_id
                        if not cwd and isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
                            cwd = normalize_windows_path(payload["cwd"])
                        if payload.get("timestamp") and first_ts is None:
                            meta_ts = parse_ts(payload["timestamp"], fallback=fallback_ts)
                            first_ts = meta_ts
                            if last_ts is None:
                                last_ts = meta_ts
                    continue

                if rtype == "turn_context":
                    if not cwd and isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
                        cwd = normalize_windows_path(payload["cwd"])
                    continue

                if rtype == "event_msg":
                    ptype = payload.get("type")
                    if ptype == "user_message":
                        msg_txt = _extract_event_msg_text(payload)
                        if msg_txt.strip():
                            if not event_first_user:
                                event_first_user = msg_txt.strip()
                            event_turns.append(
                                CanonicalTurn(role="user", timestamp=ts, text=msg_txt)
                            )
                    elif ptype == "agent_message":
                        msg_txt = _extract_event_msg_text(payload)
                        if msg_txt.strip():
                            event_turns.append(
                                CanonicalTurn(role="assistant", timestamp=ts, text=msg_txt)
                            )
                    elif ptype == "agent_reasoning":
                        th_txt = _extract_event_msg_text(payload)
                        if th_txt.strip():
                            if event_turns and event_turns[-1].role == "assistant" and not event_turns[-1].text:
                                event_turns[-1].thinking = (
                                    f"{event_turns[-1].thinking}\n\n{th_txt}"
                                    if event_turns[-1].thinking
                                    else th_txt
                                )
                            else:
                                event_turns.append(
                                    CanonicalTurn(role="assistant", timestamp=ts, thinking=th_txt)
                                )
                    continue

                if rtype != "response_item":
                    continue

                ptype = payload.get("type")
                if ptype == "message":
                    role = payload.get("role")
                    if role == "developer":
                        continue
                    if role == "user":
                        user_text = _extract_codex_user_message_text(payload)
                        if user_text.strip():
                            flush_assistant()
                            turns.append(
                                CanonicalTurn(role="user", timestamp=ts, text=user_text)
                            )
                    elif role == "assistant":
                        asst_text = _extract_codex_text_blocks(payload.get("content"))
                        if asst_text.strip():
                            pending_ts = ts
                            pending_texts.append(asst_text)

                elif ptype == "agent_message":
                    agent_text = _extract_codex_text_blocks(payload.get("content"))
                    if agent_text.strip():
                        flush_assistant()
                        author = str(payload.get("author") or "").strip()
                        if author and not agent_text.lstrip().startswith("Message Type:"):
                            agent_text = f"[Agent message from {author}]\n{agent_text}"
                        turns.append(
                            CanonicalTurn(role="user", timestamp=ts, text=agent_text)
                        )

                elif ptype == "reasoning":
                    th_text = _extract_codex_reasoning_text(payload)
                    if th_text.strip():
                        pending_ts = ts
                        pending_thinking.append(th_text)

                elif ptype == "function_call":
                    tc_id = str(payload.get("call_id") or payload.get("id") or "")
                    tc_name = str(payload.get("name") or "tool")
                    args_json = _normalize_tool_args_json(payload.get("arguments"))
                    record_tool_call(tc_id, tc_name, args_json, ts)

                elif ptype == "custom_tool_call":
                    tc_id = str(payload.get("call_id") or payload.get("id") or "")
                    tc_name = str(payload.get("name") or "custom_tool")
                    args_json = _normalize_tool_args_json(payload.get("input"))
                    record_tool_call(tc_id, tc_name, args_json, ts)

                elif ptype == "local_shell_call":
                    tc_id = str(payload.get("call_id") or payload.get("id") or "")
                    raw_action = payload.get("action")
                    if raw_action is None and "command" in payload:
                        raw_action = {"command": payload.get("command")}
                    args_json = _normalize_tool_args_json(raw_action)
                    record_tool_call(tc_id, "shell", args_json, ts)

                elif ptype == "web_search_call":
                    tc_id = str(payload.get("call_id") or payload.get("id") or "")
                    raw_action = payload.get("action")
                    if raw_action is None and "query" in payload:
                        raw_action = {"query": payload.get("query")}
                    args_json = _normalize_tool_args_json(raw_action)
                    record_tool_call(tc_id, "web_search", args_json, ts)

                elif ptype in (
                    "function_call_output",
                    "custom_tool_call_output",
                    "local_shell_call_output",
                ):
                    flush_assistant()
                    call_id = str(payload.get("call_id") or "")
                    matched = tool_lookup.get(call_id)
                    res_text = _extract_codex_text_blocks(
                        payload.get("output"), allow_image_tag=True
                    )
                    is_err = (
                        bool(payload.get("is_error"))
                        or str(payload.get("status") or "").lower() == "failed"
                    )
                    turns.append(
                        CanonicalTurn(
                            role="tool_result",
                            timestamp=ts,
                            tool_results=[
                                CanonicalToolResult(
                                    tool_use_id=call_id,
                                    name=matched.name if matched else "tool",
                                    arguments_json=matched.arguments_json if matched else "{}",
                                    content=res_text,
                                    is_error=is_err,
                                )
                            ],
                        )
                    )

        flush_assistant()

        if corrupt_lines:
            print(
                f"Warning: skipped {corrupt_lines} corrupt JSON line(s) in {location.name}",
                file=sys.stderr,
            )

        if not turns and event_turns:
            turns = event_turns

        first_user_text = next(
            (t.text for t in turns if t.role == "user" and t.text.strip()), ""
        )
        preview = truncate_preview(first_user_text or event_first_user)

        custom_names, db_titles = _read_codex_title_overrides()
        raw_title = (
            (summary.title if summary and summary.title not in ("", "(untitled)") else "")
            or custom_names.get(session_id, "")
            or truncate_preview(event_first_user, 80)
            or truncate_preview(db_titles.get(session_id, ""), 80)
        )
        title = derive_title(raw_title, preview)

        return CanonicalSession(
            session_id=session_id,
            source="codex",
            title=title,
            preview=preview,
            cwd=cwd or (summary.cwd if summary else None),
            created_at=first_ts or fallback_ts,
            updated_at=last_ts or fallback_ts,
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
        norm_cwd = normalize_windows_path(target_cwd or session.cwd or os.getcwd())
        created_dt = (session.created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        updated_dt = (session.updated_at or created_dt).astimezone(timezone.utc)

        regenerated = False
        existing: Path | None = None
        if forced_target_id:
            target_id = _normalize_codex_id(_validate_codex_id_token(forced_target_id))
            existing = _codex_target_exists(target_id)
        elif new_id or not is_valid_uuid(session.session_id):
            target_id = str(uuid.uuid4())
        else:
            target_id = session.session_id.lower()
            existing = _codex_target_exists(target_id)
            if existing is not None and not overwrite:
                target_id = str(uuid.uuid4())
                existing = None
                regenerated = True

        if existing is not None and overwrite:
            out_path = existing
        else:
            yyyy = created_dt.strftime("%Y")
            mm = created_dt.strftime("%m")
            dd = created_dt.strftime("%d")
            ts_slug = created_dt.strftime("%Y-%m-%dT%H-%M-%S")
            out_path = (
                get_codex_sessions_dir() / yyyy / mm / dd / f"rollout-{ts_slug}-{target_id}.jsonl"
            )

        _assert_within_codex_dir(out_path)

        turn_records = _normalize_turns_for_codex(
            session.turns, include_thinking=include_thinking
        )
        created_iso = format_iso_utc(created_dt)
        lines: list[dict[str, Any]] = [
            {
                "timestamp": created_iso,
                "type": "session_meta",
                "payload": {
                    "id": target_id,
                    "timestamp": created_iso,
                    "cwd": norm_cwd,
                    "originator": "codex_cli_rs",
                    "source": "cli",
                    "thread_source": "user",
                    "model_provider": "openai",
                },
            },
            {
                "timestamp": created_iso,
                "type": "turn_context",
                "payload": {
                    "cwd": norm_cwd,
                    "approval_policy": "on-request",
                    "sandbox_policy": {"type": "read-only"},
                },
            },
            *turn_records,
        ]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.parent / f".{target_id}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
                for item in lines:
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            os.replace(tmp_path, out_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

        first_user_msg = next(
            (
                str(r["payload"].get("message") or "")
                for r in turn_records
                if r.get("type") == "event_msg"
                and isinstance(r.get("payload"), dict)
                and r["payload"].get("type") == "user_message"
                and str(r["payload"].get("message") or "").strip()
            ),
            "[Continued conversation]",
        )
        preview = session.preview or truncate_preview(first_user_msg)
        title = derive_title(session.title, preview)
        custom_name = (
            session.title.strip()
            if session.title and session.title.strip() not in ("", "(untitled)")
            else ""
        )
        archived_root = get_codex_archived_dir().resolve()
        is_archived = out_path.resolve().is_relative_to(archived_root)

        _upsert_codex_sqlite_thread(
            session_id=target_id,
            out_path=out_path,
            created_at=created_dt,
            updated_at=updated_dt,
            cwd=norm_cwd,
            title=title,
            preview=preview,
            first_user_message=first_user_msg,
            custom_name=custom_name,
            is_archived=is_archived,
        )

        _evict_session_cache(out_path)
        return target_id, out_path, regenerated

    def delete_session(self, session_id: str) -> dict[str, Any]:
        sid = _validate_codex_id_token(session_id)
        if sid.lower() == "latest":
            raise SyncError("An explicit Codex session ID is required for delete.")

        jsonl_path, summary = self.find_session(sid)
        _assert_within_codex_dir(jsonl_path)
        real_id = summary.session_id if summary else _extract_codex_id_from_file(jsonl_path)
        freed_bytes = _fast_file_size(jsonl_path)

        jsonl_path.unlink(missing_ok=True)

        sessions_root = get_codex_sessions_dir().resolve()
        cur_dir = jsonl_path.parent
        while True:
            try:
                resolved_cur = cur_dir.resolve()
            except OSError:
                break
            if resolved_cur == sessions_root or not resolved_cur.is_relative_to(sessions_root):
                break
            try:
                if cur_dir.is_dir() and not any(cur_dir.iterdir()):
                    cur_dir.rmdir()
                    cur_dir = cur_dir.parent
                else:
                    break
            except OSError:
                break

        _delete_codex_sqlite_thread(real_id)
        _evict_session_cache(jsonl_path)
        _invalidate_size_cache()

        return {
            "source": "codex",
            "id": real_id,
            "deleted": True,
            "freedBytes": freed_bytes,
            "freedHuman": format_bytes(freed_bytes),
        }
