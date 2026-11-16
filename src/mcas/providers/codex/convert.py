"""CanonicalSession <-> Codex JSONL record conversion helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import json

from mcas.errors import SyncError
from mcas.models import CanonicalTurn
from mcas.util.text import (
    ToolIdMapper,
    parse_tool_input_dict,
    sanitize_tool_name,
)
from mcas.util.timefmt import format_iso_utc

_INJECTED_USER_PREFIXES = (
    "# AGENTS.md instructions for ",
    "<environment_context>",
    "<INSTRUCTIONS>",
    "<permissions instructions>",
    "<app-context>",
    "<skills_instructions>",
    "<user_instructions>",
    "<collaboration_mode>",
)


def _is_injected_user_text(text: str) -> bool:
    """Return True if text matches Codex-injected boilerplate rather than user input."""
    return text.lstrip().startswith(_INJECTED_USER_PREFIXES)


def _extract_codex_text_blocks(
    content: Any,
    *,
    allow_image_tag: bool = True,
    filter_kinds: list[Any] | None = None,
    skip_injected_prefixes: bool = False,
) -> str:
    """Extract readable text from Codex `content`, `summary`, or tool `output` values."""
    if isinstance(content, str):
        if skip_injected_prefixes and _is_injected_user_text(content):
            return ""
        return content

    if isinstance(content, list):
        parts: list[str] = []
        apply_kinds = isinstance(filter_kinds, list) and len(filter_kinds) == len(content)
        for idx, block in enumerate(content):
            if apply_kinds:
                kind = str(filter_kinds[idx] or "")
                if kind and not kind.startswith("user."):
                    continue
            if isinstance(block, str):
                if skip_injected_prefixes and _is_injected_user_text(block):
                    continue
                if block:
                    parts.append(block)
            elif isinstance(block, dict):
                btype = str(block.get("type") or "")
                if btype == "encrypted_content":
                    continue
                if btype in (
                    "input_text",
                    "output_text",
                    "text",
                    "summary_text",
                    "reasoning_text",
                ):
                    txt = block.get("text")
                    if isinstance(txt, str) and txt:
                        if skip_injected_prefixes and _is_injected_user_text(txt):
                            continue
                        parts.append(txt)
                elif allow_image_tag and btype in ("input_image", "image"):
                    parts.append("[image]")
                elif isinstance(block.get("text"), str) and block["text"]:
                    txt = block["text"]
                    if skip_injected_prefixes and _is_injected_user_text(txt):
                        continue
                    parts.append(txt)
        return "\n".join(parts)

    if isinstance(content, dict):
        for key in ("text", "output", "message"):
            val = content.get(key)
            if isinstance(val, str):
                return val
        return json.dumps(content, ensure_ascii=False)

    if content is None:
        return ""
    return str(content)


def _extract_codex_user_message_text(payload: dict[str, Any]) -> str:
    """Extract clean user prompt text from a Codex `message` item, skipping injected context."""
    meta = payload.get("internal_chat_message_metadata_passthrough")
    kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
    if isinstance(kinds, list) and kinds:
        if not any(isinstance(k, str) and k.startswith("user.") for k in kinds):
            return ""
    return _extract_codex_text_blocks(
        payload.get("content"),
        allow_image_tag=True,
        filter_kinds=kinds if isinstance(kinds, list) else None,
        skip_injected_prefixes=True,
    )


def _extract_codex_reasoning_text(payload: dict[str, Any]) -> str:
    """Extract non-encrypted reasoning summary/content text from a `reasoning` item."""
    summary_txt = _extract_codex_text_blocks(
        payload.get("summary"), allow_image_tag=False
    ).strip()
    content_txt = _extract_codex_text_blocks(
        payload.get("content"), allow_image_tag=False
    ).strip()
    if summary_txt and content_txt and summary_txt != content_txt:
        return f"{summary_txt}\n\n{content_txt}"
    return summary_txt or content_txt


def _normalize_tool_args_json(raw_val: Any) -> str:
    """Normalize tool call arguments or `custom_tool_call.input` into a JSON string."""
    if isinstance(raw_val, dict):
        return json.dumps(raw_val, ensure_ascii=False)
    if isinstance(raw_val, str):
        stripped = raw_val.strip()
        if not stripped:
            return "{}"
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
            return json.dumps({"value": parsed}, ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps({"raw": raw_val}, ensure_ascii=False)
    if raw_val is None:
        return "{}"
    return json.dumps({"value": raw_val}, ensure_ascii=False)


def _normalize_turns_for_codex(
    turns: list[CanonicalTurn], include_thinking: bool = False
) -> list[dict[str, Any]]:
    """Build ordered Codex JSONL `event_msg` / `response_item` records from canonical turns."""
    id_mapper = ToolIdMapper()
    records: list[dict[str, Any]] = []
    pending_calls: list[tuple[str, str, str]] = []
    has_user_record = False

    def emit(rtype: str, payload: dict[str, Any], ts: datetime) -> None:
        records.append(
            {
                "timestamp": format_iso_utc(ts),
                "type": rtype,
                "payload": payload,
            }
        )

    def emit_user_message(text: str, ts: datetime) -> None:
        nonlocal has_user_record
        has_user_record = True
        emit("event_msg", {"type": "user_message", "message": text}, ts)
        emit(
            "response_item",
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
            ts,
        )

    def flush_unpaired_calls(ts: datetime) -> None:
        while pending_calls:
            _, safe_id, _ = pending_calls.pop(0)
            emit(
                "response_item",
                {
                    "type": "function_call_output",
                    "call_id": safe_id,
                    "output": "[Interrupted or no tool output recorded]",
                },
                ts,
            )

    for turn in turns:
        if turn.role == "user":
            flush_unpaired_calls(turn.timestamp)
            if turn.text.strip():
                emit_user_message(turn.text, turn.timestamp)

        elif turn.role == "assistant":
            flush_unpaired_calls(turn.timestamp)
            text_parts: list[str] = []
            if include_thinking and turn.thinking.strip():
                text_parts.append(f"<thought>\n{turn.thinking.strip()}\n</thought>")
            if turn.text.strip():
                text_parts.append(turn.text)
            combined_text = "\n\n".join(text_parts)
            if combined_text.strip():
                emit(
                    "response_item",
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": combined_text}],
                    },
                    turn.timestamp,
                )

            for tc in turn.tool_calls:
                safe_id = id_mapper.allocate(tc.id)
                safe_name = sanitize_tool_name(tc.name)
                args_dict = parse_tool_input_dict(tc.arguments_json)
                emit(
                    "response_item",
                    {
                        "type": "function_call",
                        "call_id": safe_id,
                        "name": safe_name,
                        "arguments": json.dumps(args_dict, ensure_ascii=False),
                    },
                    turn.timestamp,
                )
                pending_calls.append((tc.id, safe_id, safe_name))

        elif turn.role == "tool_result":
            for tr in turn.tool_results:
                matched_idx: int | None = None
                if tr.tool_use_id:
                    for idx, (raw_id, _, _) in enumerate(pending_calls):
                        if raw_id == tr.tool_use_id:
                            matched_idx = idx
                            break
                if matched_idx is None and pending_calls:
                    matched_idx = 0

                if matched_idx is not None:
                    _, safe_id, _ = pending_calls.pop(matched_idx)
                    emit(
                        "response_item",
                        {
                            "type": "function_call_output",
                            "call_id": safe_id,
                            "output": tr.content if tr.content else "(empty output)",
                        },
                        turn.timestamp,
                    )
                else:
                    label = tr.name or "tool"
                    body = tr.content.strip() or "(empty output)"
                    emit_user_message(f"[Tool output ({label})]\n{body}", turn.timestamp)

    if turns:
        flush_unpaired_calls(turns[-1].timestamp)

    if not records:
        raise SyncError("No valid user/assistant turns found in source conversation.")

    if not has_user_record or (
        records[0]["type"] != "event_msg"
        or records[0]["payload"].get("type") != "user_message"
    ):
        first_ts = turns[0].timestamp
        ts_iso = format_iso_utc(first_ts)
        records.insert(
            0,
            {
                "timestamp": ts_iso,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "[Continued conversation]"}],
                },
            },
        )
        records.insert(
            0,
            {
                "timestamp": ts_iso,
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "[Continued conversation]"},
            },
        )

    return records
