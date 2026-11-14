from __future__ import annotations

from typing import Any
import json
import re
import uuid

from mcas.constants import TOOL_ID_RE

def is_valid_uuid(text: str) -> bool:
    """Return True if `text` is a valid canonical UUID string."""
    try:
        parsed = uuid.UUID(text)
        return str(parsed) == text.lower()
    except (ValueError, AttributeError):
        return False

def sanitize_tool_name(name: str) -> str:
    """Sanitize a tool name to Anthropic's `^[a-zA-Z0-9_-]{1,64}$` constraint."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", (name or "").strip())
    cleaned = cleaned[:64].strip("_-") if cleaned else ""
    return cleaned or "unknown_tool"

class ToolIdMapper:
    """Remaps arbitrary tool call IDs to unique Anthropic-valid `^[a-zA-Z0-9_-]{1,64}$` IDs."""

    def __init__(self) -> None:
        self._used: set[str] = set()
        self._counter = 0

    def allocate(self, raw_id: str | None) -> str:
        candidate = (raw_id or "").strip()
        if candidate and TOOL_ID_RE.match(candidate) and candidate not in self._used:
            self._used.add(candidate)
            return candidate

        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", candidate)
        if sanitized and sanitized not in self._used and len(sanitized) <= 64:
            self._used.add(sanitized)
            return sanitized

        while True:
            self._counter += 1
            base = sanitized[:48].rstrip("_-") if sanitized else "toolu"
            if not base:
                base = "toolu"
            new_id = f"{base}_{self._counter}"[:64]
            if TOOL_ID_RE.match(new_id) and new_id not in self._used:
                self._used.add(new_id)
                return new_id

def parse_tool_input_dict(arguments_json: str) -> dict[str, Any]:
    """Parse `arguments_json` into a JSON object dict (wrapping non-dict/invalid JSON)."""
    raw = (arguments_json or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except json.JSONDecodeError:
        return {"raw": arguments_json}

def truncate_preview(text: str, max_len: int = 160) -> str:
    """Produce a clean single-line preview string."""
    one_line = " ".join((text or "").split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3].rstrip() + "..."

def derive_title(title: str, preview: str) -> str:
    """Derive a concise title if none was explicitly set."""
    if title and title.strip():
        return title.strip()
    if preview and preview.strip():
        return truncate_preview(preview, 80)
    return "Imported Conversation"

def format_bytes(num_bytes: int | float) -> str:
    """Format byte count into a concise human-readable string."""
    val = float(max(0, num_bytes or 0))
    if val < 1024:
        return f"{int(val)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        val /= 1024.0
        if val < 1024.0 or unit == "TB":
            if unit == "KB" and val >= 10:
                return f"{val:.0f} KB"
            return f"{val:.1f} {unit}"
    return f"{int(num_bytes or 0)} B"
