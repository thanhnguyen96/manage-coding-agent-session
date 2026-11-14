from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import shutil
import sys
import uuid

from mcas.cache import (
    _evict_session_cache,
    _fast_dir_size,
    _fast_file_size,
)
from mcas.config import (
    get_claude_dir,
    get_claude_projects_dir,
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
from mcas.providers.claude.convert import _normalize_turns_for_claude
from mcas.providers.claude.storage import (
    _claude_target_exists,
    _extract_claude_text_from_content,
    _read_custom_title_sidecar,
    _scan_claude_summary,
    get_claude_session_size,
)
from mcas.util.pathconv import (
    normalize_windows_path,
    paths_match,
    windows_path_to_slug,
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

class ClaudeProvider(AgentProvider):
    id = "claude"
    name = "Claude Code"
    badge = "CLAUDE"
    supported = True
    coming_soon = False

    def resume_command(self, session_id: str) -> str:
        return f"claude --resume {session_id}"

    def get_sync_fingerprint(self, summary: SessionSummary) -> tuple[int, int]:
        try:
            mtime_ns = summary.location.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        fresh = _scan_claude_summary(summary.location)
        count = fresh.step_or_msg_count if fresh else summary.step_or_msg_count
        return mtime_ns, count

    def list_sessions(
        self, include_all: bool = False, cwd_filter: str | None = None
    ) -> list[SessionSummary]:
        projects_dir = get_claude_projects_dir()
        if not projects_dir.is_dir():
            return []

        target_slug_lower = windows_path_to_slug(cwd_filter).lower() if cwd_filter else None
        results: list[SessionSummary] = []

        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            if target_slug_lower and proj_dir.name.lower() != target_slug_lower:
                continue
            for jsonl_path in proj_dir.glob("*.jsonl"):
                if not jsonl_path.is_file():
                    continue
                summary = _scan_claude_summary(jsonl_path)
                if not summary:
                    continue
                if not include_all and summary.step_or_msg_count == 0:
                    continue
                if cwd_filter and summary.cwd and not paths_match(summary.cwd, cwd_filter):
                    continue
                total_bytes, _, _ = get_claude_session_size(summary.session_id, jsonl_path=jsonl_path)
                results.append(replace(summary, size_bytes=total_bytes))

        results.sort(key=lambda s: s.updated_at, reverse=True)
        return results

    def find_session(
        self, id_or_latest: str, cwd: str | None = None, all_cwd: bool = False
    ) -> tuple[Path, SessionSummary | None]:
        if id_or_latest.lower() == "latest":
            target_cwd = None if all_cwd else normalize_windows_path(cwd or os.getcwd())
            candidates = self.list_sessions(include_all=False, cwd_filter=target_cwd)
            if not candidates:
                scope = "any workspace" if all_cwd else f"workspace '{target_cwd}'"
                raise SyncError(
                    f"No Claude Code session found for {scope}. "
                    "Run `list` to inspect available sessions, pass an explicit session ID, "
                    "or use `--all-cwd`."
                )
            return candidates[0].location, candidates[0]

        projects_dir = get_claude_projects_dir()
        if not projects_dir.is_dir():
            raise SyncError(f"Claude projects directory not found: {projects_dir}")

        exact_matches: list[Path] = []
        prefix_matches: list[Path] = []
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            exact = proj_dir / f"{id_or_latest}.jsonl"
            if exact.is_file():
                exact_matches.append(exact)
            else:
                for p in proj_dir.glob(f"{id_or_latest}*.jsonl"):
                    if p.is_file():
                        prefix_matches.append(p)

        chosen: Path | None = None
        if len(exact_matches) == 1:
            chosen = exact_matches[0]
        elif len(exact_matches) > 1:
            if cwd:
                target_slug = windows_path_to_slug(cwd).lower()
                for p in exact_matches:
                    if p.parent.name.lower() == target_slug:
                        chosen = p
                        break
            if chosen is None:
                chosen = exact_matches[0]
        elif len(prefix_matches) == 1:
            chosen = prefix_matches[0]
        elif len(prefix_matches) > 1:
            ids = ", ".join(p.stem for p in prefix_matches[:5])
            raise SyncError(f"Ambiguous Claude session prefix '{id_or_latest}': matches {ids}")

        if chosen is None:
            raise SyncError(f"Claude session '{id_or_latest}' not found.")
        return chosen, _scan_claude_summary(chosen)

    def read_session(
        self, location: Path, summary: SessionSummary | None = None
    ) -> CanonicalSession:
        session_id = location.stem
        custom_title = _read_custom_title_sidecar(location)
        ai_title = ""
        cwd: str | None = None
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        fallback_ts = datetime.fromtimestamp(location.stat().st_mtime, tz=timezone.utc)

        turns: list[CanonicalTurn] = []
        tool_lookup: dict[str, CanonicalToolCall] = {}
        corrupt_lines = 0

        pending_msg_id: str | None = None
        pending_ts: datetime | None = None
        pending_texts: list[str] = []
        pending_thinking: list[str] = []
        pending_tools: list[CanonicalToolCall] = []

        def flush_assistant() -> None:
            nonlocal pending_msg_id, pending_ts, pending_texts, pending_thinking, pending_tools
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
            pending_msg_id = None
            pending_ts = None
            pending_texts = []
            pending_thinking = []
            pending_tools = []

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

                if not cwd and isinstance(rec.get("cwd"), str) and rec["cwd"].strip():
                    cwd = normalize_windows_path(rec["cwd"])

                rtype = rec.get("type")
                if rtype == "custom-title" and isinstance(rec.get("customTitle"), str):
                    custom_title = rec["customTitle"].strip()
                    continue
                if rtype == "ai-title" and isinstance(rec.get("aiTitle"), str):
                    ai_title = rec["aiTitle"].strip()
                    continue
                if rtype not in ("user", "assistant"):
                    continue
                if rec.get("isSidechain") is True or rec.get("isMeta") is True:
                    continue

                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")

                if rtype == "assistant":
                    msg_id = msg.get("id") if isinstance(msg.get("id"), str) else None
                    if (
                        pending_ts is not None
                        and msg_id
                        and pending_msg_id == msg_id
                    ):
                        pending_ts = ts
                    else:
                        flush_assistant()
                        pending_msg_id = msg_id
                        pending_ts = ts

                    if isinstance(content, str):
                        if content.strip():
                            pending_texts.append(content)
                    elif isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "text":
                                txt = block.get("text")
                                if isinstance(txt, str) and txt.strip():
                                    pending_texts.append(txt)
                            elif btype == "thinking":
                                th = block.get("thinking")
                                if isinstance(th, str) and th.strip():
                                    pending_thinking.append(th)
                            elif btype == "tool_use":
                                tc_id = str(block.get("id") or "")
                                tc_name = str(block.get("name") or "tool")
                                tc_input = block.get("input")
                                if isinstance(tc_input, dict):
                                    args_json = json.dumps(tc_input, ensure_ascii=False)
                                elif tc_input is None:
                                    args_json = "{}"
                                else:
                                    args_json = json.dumps({"value": tc_input}, ensure_ascii=False)
                                tc = CanonicalToolCall(
                                    id=tc_id, name=tc_name, arguments_json=args_json
                                )
                                pending_tools.append(tc)
                                if tc_id:
                                    tool_lookup[tc_id] = tc

                elif rtype == "user":
                    flush_assistant()
                    if isinstance(content, str):
                        if content.strip():
                            turns.append(CanonicalTurn(role="user", timestamp=ts, text=content))
                    elif isinstance(content, list):
                        tool_results: list[CanonicalToolResult] = []
                        non_tool_blocks: list[Any] = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tu_id = str(block.get("tool_use_id") or "")
                                matched = tool_lookup.get(tu_id)
                                res_text = _extract_claude_text_from_content(block.get("content"))
                                is_err = bool(block.get("is_error"))
                                if is_err and res_text and not res_text.startswith("[Tool Error]"):
                                    res_text = f"[Tool Error]\n{res_text}"
                                elif is_err and not res_text:
                                    res_text = "[Tool Error]"
                                tool_results.append(
                                    CanonicalToolResult(
                                        tool_use_id=tu_id,
                                        name=matched.name if matched else "tool",
                                        arguments_json=matched.arguments_json if matched else "{}",
                                        content=res_text,
                                        is_error=is_err,
                                    )
                                )
                            else:
                                non_tool_blocks.append(block)
                        if tool_results:
                            turns.append(
                                CanonicalTurn(
                                    role="tool_result", timestamp=ts, tool_results=tool_results
                                )
                            )
                        if non_tool_blocks:
                            user_text = _extract_claude_text_from_content(non_tool_blocks)
                            if user_text.strip():
                                turns.append(
                                    CanonicalTurn(role="user", timestamp=ts, text=user_text)
                                )

        flush_assistant()

        if corrupt_lines:
            print(
                f"Warning: skipped {corrupt_lines} corrupt JSON line(s) in {location.name}",
                file=sys.stderr,
            )

        first_user_text = next((t.text for t in turns if t.role == "user" and t.text.strip()), "")
        preview = truncate_preview(first_user_text)
        title = derive_title(custom_title or ai_title, preview)

        return CanonicalSession(
            session_id=session_id,
            source="claude",
            title=title,
            preview=preview,
            cwd=cwd,
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
        slug = windows_path_to_slug(norm_cwd)
        out_dir = get_claude_projects_dir() / slug

        regenerated = False
        if forced_target_id:
            target_id = forced_target_id.strip()
            existing = _claude_target_exists(target_id)
            if existing is not None and overwrite and target_cwd is None:
                out_dir = existing.parent
        elif new_id or not is_valid_uuid(session.session_id):
            target_id = str(uuid.uuid4())
        else:
            target_id = session.session_id.lower()
            existing = _claude_target_exists(target_id)
            if existing is not None:
                if not overwrite:
                    target_id = str(uuid.uuid4())
                    regenerated = True
                elif target_cwd is None:
                    out_dir = existing.parent

        out_path = out_dir / f"{target_id}.jsonl"
        messages = _normalize_turns_for_claude(session.turns, include_thinking=include_thinking)

        lines: list[dict[str, Any]] = []
        if session.title:
            lines.append(
                {
                    "type": "custom-title",
                    "customTitle": session.title,
                    "sessionId": target_id,
                }
            )

        parent_uuid: str | None = None
        last_uuid: str | None = None
        last_user_prompt = ""

        for msg in messages:
            rec_uuid = str(uuid.uuid4())
            role = msg["role"]
            blocks = msg["blocks"]
            ts_iso = format_iso_utc(msg["timestamp"])

            if role == "user" and len(blocks) == 1 and blocks[0].get("type") == "text":
                msg_content: Any = blocks[0]["text"]
                last_user_prompt = blocks[0]["text"]
            else:
                msg_content = blocks
                if role == "user":
                    texts = [b["text"] for b in blocks if b.get("type") == "text" and b.get("text")]
                    if texts:
                        last_user_prompt = "\n".join(texts)

            lines.append(
                {
                    "type": role,
                    "uuid": rec_uuid,
                    "parentUuid": parent_uuid,
                    "sessionId": target_id,
                    "isSidechain": False,
                    "timestamp": ts_iso,
                    "cwd": norm_cwd,
                    "message": {
                        "role": role,
                        "content": msg_content,
                    },
                }
            )
            parent_uuid = rec_uuid
            last_uuid = rec_uuid

        if last_user_prompt and last_uuid:
            lines.append(
                {
                    "type": "last-prompt",
                    "lastPrompt": truncate_preview(last_user_prompt, 500),
                    "leafUuid": last_uuid,
                    "sessionId": target_id,
                }
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = out_dir / f".{target_id}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
                for item in lines:
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            os.replace(tmp_path, out_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

        _evict_session_cache(out_path)
        return target_id, out_path, regenerated

    def delete_session(self, session_id: str) -> dict[str, Any]:
        sid = session_id.strip()
        if not sid or sid.lower() == "latest":
            raise SyncError("An explicit Claude session ID is required for delete.")
        jsonl_path, _ = self.find_session(sid)
        real_id = jsonl_path.stem
        sidecar_dir = jsonl_path.with_suffix("")
        fh_dir = get_claude_dir() / "file-history" / real_id

        freed_bytes = (
            _fast_file_size(jsonl_path)
            + _fast_dir_size(sidecar_dir)
            + _fast_dir_size(fh_dir)
        )

        jsonl_path.unlink(missing_ok=True)
        if sidecar_dir.is_dir():
            shutil.rmtree(sidecar_dir, ignore_errors=True)
        if fh_dir.is_dir():
            shutil.rmtree(fh_dir, ignore_errors=True)

        proj_dir = jsonl_path.parent
        try:
            if proj_dir.is_dir() and not any(proj_dir.iterdir()):
                proj_dir.rmdir()
        except OSError:
            pass

        _evict_session_cache(jsonl_path)
        return {
            "source": "claude",
            "id": real_id,
            "deleted": True,
            "freedBytes": freed_bytes,
            "freedHuman": format_bytes(freed_bytes),
        }
