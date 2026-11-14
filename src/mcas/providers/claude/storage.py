from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from mcas.cache import (
    _CACHE_LOCK,
    _CLAUDE_SUMMARY_CACHE,
    _fast_dir_size,
    _fast_file_size,
    _file_stat_sig,
)
from mcas.config import (
    get_claude_dir,
    get_claude_projects_dir,
)
from mcas.models import SessionSummary
from mcas.util.pathconv import normalize_windows_path
from mcas.util.text import truncate_preview
from mcas.util.timefmt import parse_ts

def get_claude_session_size(
    session_id: str, jsonl_path: Path | None = None
) -> tuple[int, int, int]:
    """Return `(total_bytes, main_bytes, sidecar_bytes)` for a Claude Code session."""
    main_bytes = _fast_file_size(jsonl_path) if jsonl_path else 0
    sidecar_dir = jsonl_path.with_suffix("") if jsonl_path else None
    fh_dir = get_claude_dir() / "file-history" / session_id
    sidecar_bytes = (_fast_dir_size(sidecar_dir) if sidecar_dir else 0) + _fast_dir_size(fh_dir)
    return main_bytes + sidecar_bytes, main_bytes, sidecar_bytes

def _extract_claude_text_from_content(content: Any) -> str:
    """Extract readable text from Claude `user` or `tool_result` content (str or list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    txt = block.get("text")
                    if isinstance(txt, str) and txt:
                        parts.append(txt)
                elif btype == "image":
                    parts.append("[image]")
                elif btype == "document":
                    parts.append("[document]")
                elif btype == "tool_reference":
                    name = block.get("tool_name") or block.get("name") or ""
                    parts.append(f"[tool_reference: {name}]" if name else "[tool_reference]")
                elif isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)

def _read_custom_title_sidecar(jsonl_path: Path) -> str:
    """Check sibling `<uuid>/custom-title.json` if present."""
    sidecar = jsonl_path.parent / jsonl_path.stem / "custom-title.json"
    if not sidecar.is_file():
        return ""
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            return str(data.get("customTitle") or data.get("title") or "").strip()
    except (OSError, json.JSONDecodeError):
        pass
    return ""

def _scan_claude_summary(jsonl_path: Path) -> SessionSummary | None:
    """Quickly extract summary metadata from a single Claude `.jsonl` file."""
    sidecar = jsonl_path.parent / jsonl_path.stem / "custom-title.json"
    sig = (*_file_stat_sig(jsonl_path), *_file_stat_sig(sidecar))
    if sig[0] < 0:
        return None
    cache_key = str(jsonl_path)
    with _CACHE_LOCK:
        cached = _CLAUDE_SUMMARY_CACHE.get(cache_key)
        if cached is not None and cached[0] == sig:
            return cached[1]

    sid = jsonl_path.stem
    custom_title = _read_custom_title_sidecar(jsonl_path)
    ai_title = ""
    first_prompt = ""
    cwd: str | None = None
    last_ts: datetime | None = None
    msg_count = 0

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
                if not cwd and isinstance(rec.get("cwd"), str) and rec["cwd"].strip():
                    cwd = normalize_windows_path(rec["cwd"])

                rtype = rec.get("type")
                if rtype == "custom-title" and isinstance(rec.get("customTitle"), str):
                    custom_title = rec["customTitle"].strip()
                elif rtype == "ai-title" and isinstance(rec.get("aiTitle"), str):
                    ai_title = rec["aiTitle"].strip()
                elif rtype in ("user", "assistant"):
                    if rec.get("isSidechain") is True or rec.get("isMeta") is True:
                        continue
                    msg_count += 1
                    if rtype == "user" and not first_prompt:
                        msg = rec.get("message") or {}
                        content = msg.get("content")
                        if isinstance(content, list) and all(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in content
                        ):
                            continue
                        extracted = _extract_claude_text_from_content(content).strip()
                        if extracted:
                            first_prompt = extracted
    except OSError:
        return None

    try:
        fallback_dt = datetime.fromtimestamp(sig[0] / 1_000_000_000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        fallback_dt = datetime.now(timezone.utc)
    updated = last_ts or fallback_dt
    title = custom_title or ai_title or truncate_preview(first_prompt, 80) or "(untitled)"
    preview = truncate_preview(first_prompt or title, 120)

    summary = SessionSummary(
        session_id=sid,
        source="claude",
        updated_at=updated,
        title=title,
        preview=preview,
        cwd=cwd,
        location=jsonl_path,
        step_or_msg_count=msg_count,
    )
    with _CACHE_LOCK:
        _CLAUDE_SUMMARY_CACHE[cache_key] = (sig, summary)
    return summary

def _claude_target_exists(session_id: str) -> Path | None:
    """Return existing `.jsonl` path if `session_id` already exists in any Claude project."""
    projects_dir = get_claude_projects_dir()
    if not projects_dir.is_dir():
        return None
    for proj_dir in projects_dir.iterdir():
        if proj_dir.is_dir():
            candidate = proj_dir / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate
    return None
