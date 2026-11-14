from __future__ import annotations

from datetime import datetime
from typing import Any

from mcas.errors import SyncError
from mcas.models import CanonicalTurn
from mcas.util.text import (
    ToolIdMapper,
    parse_tool_input_dict,
    sanitize_tool_name,
)

def _normalize_turns_for_claude(
    turns: list[CanonicalTurn], include_thinking: bool = False
) -> list[dict[str, Any]]:
    """Enforce Anthropic API replay invariants:

    - Sanitize `tool_use.id` and `tool_use.name` to `^[a-zA-Z0-9_-]{1,64}$`.
    - Ensure `tool_use.input` is always a JSON dict.
    - Pair every `tool_use` with a `tool_result` in the immediately following `user`
      message (synthesizing interrupted `tool_result`s when missing).
    - Coalesce consecutive same-role turns; drop empty text/turns; start on `user`.
    """
    id_mapper = ToolIdMapper()
    normalized: list[dict[str, Any]] = []
    pending_tool_uses: list[tuple[str, str, str]] = []

    def append_message(role: str, blocks: list[dict[str, Any]], ts: datetime) -> None:
        if not blocks:
            return
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["blocks"].extend(blocks)
            normalized[-1]["timestamp"] = ts
        else:
            normalized.append({"role": role, "blocks": list(blocks), "timestamp": ts})

    def flush_unpaired_tools(ts: datetime) -> None:
        if not pending_tool_uses:
            return
        synth_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": safe_id,
                "content": "[Interrupted or no tool output recorded]",
                "is_error": True,
            }
            for _, safe_id, _ in pending_tool_uses
        ]
        pending_tool_uses.clear()
        append_message("user", synth_blocks, ts)

    for turn in turns:
        if turn.role == "user":
            flush_unpaired_tools(turn.timestamp)
            if turn.text.strip():
                append_message(
                    "user", [{"type": "text", "text": turn.text}], turn.timestamp
                )

        elif turn.role == "assistant":
            flush_unpaired_tools(turn.timestamp)
            blocks: list[dict[str, Any]] = []
            text_parts: list[str] = []
            if include_thinking and turn.thinking.strip():
                text_parts.append(f"<thought>\n{turn.thinking.strip()}\n</thought>")
            if turn.text.strip():
                text_parts.append(turn.text)
            combined_text = "\n\n".join(text_parts)
            if combined_text.strip():
                blocks.append({"type": "text", "text": combined_text})

            new_pending: list[tuple[str, str, str]] = []
            for tc in turn.tool_calls:
                safe_id = id_mapper.allocate(tc.id)
                safe_name = sanitize_tool_name(tc.name)
                input_dict = parse_tool_input_dict(tc.arguments_json)
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": safe_id,
                        "name": safe_name,
                        "input": input_dict,
                    }
                )
                new_pending.append((tc.id, safe_id, safe_name))

            if blocks:
                append_message("assistant", blocks, turn.timestamp)
                pending_tool_uses.extend(new_pending)

        elif turn.role == "tool_result":
            for tr in turn.tool_results:
                matched_idx: int | None = None
                if tr.tool_use_id:
                    for i, (raw_id, _, _) in enumerate(pending_tool_uses):
                        if raw_id == tr.tool_use_id:
                            matched_idx = i
                            break
                if matched_idx is None and pending_tool_uses:
                    matched_idx = 0

                if matched_idx is not None:
                    _, safe_id, _ = pending_tool_uses.pop(matched_idx)
                    append_message(
                        "user",
                        [
                            {
                                "type": "tool_result",
                                "tool_use_id": safe_id,
                                "content": tr.content if tr.content else "(empty output)",
                                "is_error": tr.is_error,
                            }
                        ],
                        turn.timestamp,
                    )
                else:
                    label = tr.name or "tool"
                    body = tr.content.strip() or "(empty output)"
                    append_message(
                        "user",
                        [{"type": "text", "text": f"[Tool output ({label})]\n{body}"}],
                        turn.timestamp,
                    )

    if turns:
        flush_unpaired_tools(turns[-1].timestamp)

    if not normalized:
        raise SyncError("No valid user/assistant turns found in source conversation.")

    if normalized[0]["role"] != "user":
        normalized.insert(
            0,
            {
                "role": "user",
                "blocks": [{"type": "text", "text": "[Continued conversation]"}],
                "timestamp": normalized[0]["timestamp"],
            },
        )

    return normalized
