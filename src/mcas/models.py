from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import hashlib
import json
import os

from mcas.util.pathconv import normalize_windows_path

@dataclass
class CanonicalToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass
class CanonicalToolResult:
    tool_use_id: str
    name: str
    arguments_json: str
    content: str
    is_error: bool = False


@dataclass
class CanonicalTurn:
    role: str  # "user" | "assistant" | "tool_result"
    timestamp: datetime
    text: str = ""
    thinking: str = ""
    tool_calls: list[CanonicalToolCall] = field(default_factory=list)
    tool_results: list[CanonicalToolResult] = field(default_factory=list)


@dataclass
class CanonicalSession:
    session_id: str
    source: str
    title: str
    preview: str
    cwd: str | None
    created_at: datetime
    updated_at: datetime
    turns: list[CanonicalTurn] = field(default_factory=list)


@dataclass
class SessionSummary:
    session_id: str
    source: str
    updated_at: datetime
    title: str
    preview: str
    cwd: str | None
    location: Path
    step_or_msg_count: int = 0
    extra: str = ""
    size_bytes: int = 0

def canonical_session_hash(session: CanonicalSession) -> str:
    """Compute a deterministic SHA-256 content hash for loop-free sync comparisons."""
    payload = {
        "title": session.title,
        "cwd": os.path.normcase(normalize_windows_path(session.cwd)) if session.cwd else "",
        "turns": [
            {
                "role": t.role,
                "text": t.text,
                "thinking": t.thinking,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments_json}
                    for tc in t.tool_calls
                ],
                "tool_results": [
                    {
                        "tool_use_id": tr.tool_use_id,
                        "name": tr.name,
                        "content": tr.content,
                        "is_error": tr.is_error,
                    }
                    for tr in t.tool_results
                ],
            }
            for t in session.turns
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
