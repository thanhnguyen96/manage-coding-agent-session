#!/usr/bin/env python3
"""mcas - Manage Coding Agent Session.

Extensible session browser, bidirectional converter, loop-free auto-sync daemon,
and local HTTP JSON API server for coding agent CLI sessions.

Active providers:
  - agy (Antigravity CLI)
  - claude (Claude Code CLI)

Planned / future provider descriptors:
  - opencode (coming soon)
  - codex (coming soon)

Uses Python 3 standard library only and UTF-8 everywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_NAME = "mcas"
VERSION = "0.1.0"
DEFAULT_PORT = 47831
MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024
MAX_HISTORY_ENTRIES = 100

# Antigravity CortexStepType enum values
STEP_USER_INPUT = 14
STEP_PLANNER_RESPONSE = 15
STEP_ERROR_MESSAGE = 17
STEP_CHECKPOINT = 23
STEP_SYSTEM_MESSAGE = 101
STEP_GENERIC = 132

# Antigravity CortexStepStatus enum values
STATUS_RUNNING = 2
STATUS_DONE = 3
STATUS_CANCELED = 6
STATUS_ERROR = 7
STATUS_INTERRUPTED = 12

# Antigravity step metadata source values
SOURCE_MODEL = 2
SOURCE_USER_IMPLICIT = 3
SOURCE_USER_EXPLICIT = 4
SOURCE_SYSTEM = 5

# Antigravity trajectory constants
TRAJECTORY_TYPE_CASCADE = 4
TRAJECTORY_SOURCE_CLI = 17
STOP_REASON_STOP_PATTERN = 2
CASCADE_STATUS_IDLE = 1
TERMINATION_REASON_IDLE = 4

TOOL_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

CONVERSATION_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS `trajectory_meta` (
    `trajectory_id` text,
    `cascade_id` text,
    `trajectory_type` integer,
    `source` integer,
    PRIMARY KEY (`trajectory_id`)
);
CREATE TABLE IF NOT EXISTS `steps` (
    `idx` integer,
    `step_type` integer NOT NULL DEFAULT 0,
    `status` integer NOT NULL DEFAULT 0,
    `has_subtrajectory` numeric NOT NULL DEFAULT false,
    `metadata` blob,
    `error_details` blob,
    `permissions` blob,
    `task_details` blob,
    `render_info` blob,
    `step_payload` blob,
    `step_format` integer NOT NULL DEFAULT 0,
    PRIMARY KEY (`idx`)
);
CREATE INDEX IF NOT EXISTS `idx_steps_status` ON `steps`(`status`);
CREATE INDEX IF NOT EXISTS `idx_steps_step_type` ON `steps`(`step_type`);
CREATE TABLE IF NOT EXISTS `gen_metadata` (
    `idx` integer,
    `data` blob,
    `size` integer NOT NULL DEFAULT 0,
    PRIMARY KEY (`idx`)
);
CREATE TABLE IF NOT EXISTS `executor_metadata` (
    `idx` integer,
    `data` blob,
    PRIMARY KEY (`idx`)
);
CREATE TABLE IF NOT EXISTS `parent_references` (
    `idx` integer,
    `data` blob,
    PRIMARY KEY (`idx`)
);
CREATE TABLE IF NOT EXISTS `trajectory_metadata_blob` (
    `id` text DEFAULT "main",
    `data` blob,
    PRIMARY KEY (`id`)
);
CREATE TABLE IF NOT EXISTS `battle_mode_infos` (
    `idx` integer,
    `data` blob,
    PRIMARY KEY (`idx`)
);
"""

SUMMARIES_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS `conversation_summaries` (
    `conversation_id` text,
    `title` text NOT NULL DEFAULT "",
    `preview` text NOT NULL DEFAULT "",
    `step_count` integer NOT NULL DEFAULT 0,
    `last_modified_time` datetime NOT NULL,
    `workspace_uris` text NOT NULL,
    `status` text NOT NULL DEFAULT "",
    `source` text NOT NULL DEFAULT "",
    `project_id` text NOT NULL DEFAULT "",
    `agent_name` text NOT NULL DEFAULT "",
    `parent_conversation_id` text NOT NULL DEFAULT "",
    `nesting_depth` integer NOT NULL DEFAULT 0,
    `battle_id` text NOT NULL DEFAULT "",
    `winning_conversation_id` text NOT NULL DEFAULT "",
    `not_fully_idle` numeric NOT NULL DEFAULT false,
    `killed` numeric NOT NULL DEFAULT false,
    `last_user_input_time` datetime NOT NULL,
    `last_user_input_step_index` integer NOT NULL DEFAULT -1,
    `app_data_dir` text NOT NULL DEFAULT "",
    `raw_summary` blob,
    group_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (`conversation_id`)
);
CREATE INDEX IF NOT EXISTS `idx_conversation_summaries_last_user_input_time`
    ON `conversation_summaries`(`last_user_input_time`);
CREATE INDEX IF NOT EXISTS `idx_conversation_summaries_last_modified_time`
    ON `conversation_summaries`(`last_modified_time`);
CREATE TABLE IF NOT EXISTS search_schema_version (
    version INTEGER PRIMARY KEY
);
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    conversation_id UNINDEXED,
    chunk_index UNINDEXED,
    chunk_first_step UNINDEXED,
    chunk_last_step UNINDEXED,
    last_modified_time UNINDEXED,
    step_count UNINDEXED,
    last_step_created_at UNINDEXED,
    archived UNINDEXED,
    user_step_byte_offsets UNINDEXED,
    agent_step_byte_offsets UNINDEXED,
    title,
    workspace,
    project,
    user_body,
    agent_body,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '1 2 3'
);
CREATE TABLE IF NOT EXISTS conversation_parents (
    conversation_id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL
) WITHOUT ROWID;
"""


class SyncError(Exception):
    """Expected, user-facing operational error."""


# ---------------------------------------------------------------------------
# Environment & Runtime Paths
# ---------------------------------------------------------------------------


def _user_home() -> Path:
    up = os.environ.get("USERPROFILE", "").strip()
    if up:
        return Path(up)
    return Path.home()


def get_mcas_root() -> Path:
    override = os.environ.get("MCAS_ROOT", "").strip()
    if override:
        return Path(override)
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app:
        return Path(local_app) / "mcas"
    return _user_home() / "AppData" / "Local" / "mcas"


def get_default_port() -> int:
    raw = os.environ.get("MCAS_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise SyncError(f"Invalid MCAS_PORT '{raw}' (must be 1024-65535).") from exc
    if port < 1024 or port > 65535:
        raise SyncError(f"Invalid MCAS_PORT '{raw}' (use 1024-65535).")
    return port


def get_agy_cli_dir() -> Path:
    override = os.environ.get("MCAS_AGY_DIR", "").strip()
    if override:
        return Path(override)
    return _user_home() / ".gemini" / "antigravity-cli"


def get_agy_ide_dir() -> Path:
    override = os.environ.get("MCAS_AGY_IDE_DIR", "").strip()
    if override:
        return Path(override)
    return _user_home() / ".gemini" / "antigravity"


def get_claude_dir() -> Path:
    override = os.environ.get("MCAS_CLAUDE_DIR", "").strip()
    if override:
        return Path(override)
    return _user_home() / ".claude"


def get_claude_projects_dir() -> Path:
    return get_claude_dir() / "projects"


def get_startup_lnk_path() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    base = Path(appdata) if appdata else (_user_home() / "AppData" / "Roaming")
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "mcas.lnk"


def get_sync_state_path() -> Path:
    return get_mcas_root() / "sync_state.json"


def get_ui_json_path() -> Path:
    return get_mcas_root() / "ui.json"


def test_autostart() -> bool:
    return get_startup_lnk_path().is_file()


def set_autostart(on: bool, port: int) -> str:
    ps_script = Path(__file__).resolve().parent / "mcas.ps1"
    if not ps_script.is_file():
        raise SyncError(f"mcas.ps1 not found beside mcas.py: {ps_script}")
    action = "on" if on else "off"
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps_script),
        "autostart",
        action,
        "--port",
        str(port),
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(f"Failed to toggle autostart: {exc}") from exc
    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "").strip() or f"exit code {res.returncode}"
        raise SyncError(f"Autostart command failed: {msg}")
    out = (res.stdout or "").strip()
    if out:
        return out.splitlines()[-1].strip()
    return (
        "mcas will start in the tray when you sign in to Windows."
        if on
        else "mcas will not start with Windows."
    )


# ---------------------------------------------------------------------------
# Minimal Protobuf wire codec (stdlib-only)
# ---------------------------------------------------------------------------


def encode_varint(value: int) -> bytes:
    """Encode an integer as a base-128 protobuf varint (two's complement for <0)."""
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def pb_varint(field_num: int, value: int) -> bytes:
    """Encode a protobuf varint field (wire type 0)."""
    return encode_varint((field_num << 3) | 0) + encode_varint(value)


def pb_bytes(field_num: int, data: bytes) -> bytes:
    """Encode a protobuf length-delimited field (wire type 2)."""
    return encode_varint((field_num << 3) | 2) + encode_varint(len(data)) + data


def pb_str(field_num: int, text: str) -> bytes:
    """Encode a non-empty UTF-8 string field."""
    return pb_bytes(field_num, text.encode("utf-8"))


def pb_msg(field_num: int, payload: bytes) -> bytes:
    """Encode an embedded protobuf sub-message field."""
    return pb_bytes(field_num, payload)


def pb_timestamp(field_num: int, dt: datetime) -> bytes:
    """Encode google.protobuf.Timestamp {int64 seconds = 1; int32 nanos = 2;}."""
    try:
        utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        epoch = utc.timestamp()
    except (OSError, OverflowError, ValueError):
        utc = datetime(1970, 1, 1, tzinfo=timezone.utc)
        epoch = 0.0
    if epoch < 0:
        epoch = 0.0
        nanos = 0
    else:
        nanos = utc.microsecond * 1000
    secs = int(epoch)
    parts = [pb_varint(1, secs)]
    if nanos:
        parts.append(pb_varint(2, nanos))
    return pb_msg(field_num, b"".join(parts))


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a varint from `data` starting at `pos`."""
    n = len(data)
    result = 0
    shift = 0
    while pos < n:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("Malformed varint")
    raise ValueError("Truncated varint")


def decode_pb(data: bytes) -> list[tuple[int, int, Any]]:
    """Decode raw protobuf wire bytes into [(field_num, wire_type, value), ...].

    Stops gracefully on truncated or unknown wire types so corrupt payloads
    never crash the tool.
    """
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    n = len(data)
    while pos < n:
        try:
            key, pos = _read_varint(data, pos)
            field_num = key >> 3
            wire_type = key & 0x07
            if field_num <= 0:
                break
            if wire_type == 0:
                val, pos = _read_varint(data, pos)
                fields.append((field_num, 0, val))
            elif wire_type == 1:
                if pos + 8 > n:
                    break
                val = struct.unpack("<Q", data[pos : pos + 8])[0]
                pos += 8
                fields.append((field_num, 1, val))
            elif wire_type == 2:
                length, pos = _read_varint(data, pos)
                if pos + length > n:
                    break
                val = data[pos : pos + length]
                pos += length
                fields.append((field_num, 2, val))
            elif wire_type == 5:
                if pos + 4 > n:
                    break
                val = struct.unpack("<I", data[pos : pos + 4])[0]
                pos += 4
                fields.append((field_num, 5, val))
            else:
                break
        except ValueError:
            break
    return fields


def pb_first(
    fields: list[tuple[int, int, Any]], field_num: int, wire_type: int | None = None
) -> Any:
    """Return the first matching field value or None."""
    for fn, wt, val in fields:
        if fn == field_num and (wire_type is None or wt == wire_type):
            return val
    return None


def pb_all(
    fields: list[tuple[int, int, Any]], field_num: int, wire_type: int | None = None
) -> list[Any]:
    """Return all matching field values in order."""
    return [
        val
        for fn, wt, val in fields
        if fn == field_num and (wire_type is None or wt == wire_type)
    ]


def pb_str_val(fields: list[tuple[int, int, Any]], field_num: int) -> str:
    """Return the first UTF-8 string value for `field_num`, or ''."""
    raw = pb_first(fields, field_num, 2)
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return ""


def pb_timestamp_val(fields: list[tuple[int, int, Any]], field_num: int) -> datetime | None:
    """Decode google.protobuf.Timestamp sub-message into a UTC datetime."""
    raw = pb_first(fields, field_num, 2)
    if not isinstance(raw, (bytes, bytearray)):
        return None
    sub = decode_pb(raw)
    secs = pb_first(sub, 1, 0)
    if secs is None:
        return None
    nanos = pb_first(sub, 2, 0) or 0
    try:
        dt = datetime.fromtimestamp(secs + nanos / 1_000_000_000, tz=timezone.utc)
        if dt.year < 1970:
            return None
        return dt
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Path, URI, slug, timestamp & tool helpers
# ---------------------------------------------------------------------------


def normalize_windows_path(path: str) -> str:
    """Normalize a local path (preserving drive-root trailing slash and drive casing)."""
    raw = path.strip()
    if len(raw) == 2 and raw[1] == ":" and raw[0].isalpha():
        raw += "\\"
    cleaned = os.path.normpath(os.path.abspath(raw))
    if len(cleaned) >= 2 and cleaned[1] == ":" and cleaned[0].isalpha():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def paths_match(path_a: str | None, path_b: str | None) -> bool:
    """Compare two filesystem paths with OS-appropriate case sensitivity."""
    if not path_a or not path_b:
        return False
    return os.path.normcase(normalize_windows_path(path_a)) == os.path.normcase(
        normalize_windows_path(path_b)
    )


def windows_path_to_slug(path: str) -> str:
    """Convert a path into Claude Code's project directory slug.

    Verified rule: normalize (`abspath` + `normpath`, uppercase drive letter),
    then replace every non-ASCII-alphanumeric character with `-` (no collapsing).
    E.g. `C:\\_personal\\_empty` -> `C---personal--empty`, `C:\\` -> `C--`.
    """
    norm = normalize_windows_path(path)
    return re.sub(r"[^a-zA-Z0-9]", "-", norm)


def windows_path_to_file_uri(path: str) -> str:
    """Convert a normalized Windows path to a `file:///` URI matching `agy` CLI."""
    norm = normalize_windows_path(path)
    try:
        return Path(norm).as_uri()
    except ValueError:
        slash_path = norm.replace("\\", "/")
        if not slash_path.startswith("/"):
            slash_path = "/" + slash_path
        return "file://" + urllib.parse.quote(slash_path, safe="/:")


def file_uri_to_windows_path(uri: str) -> str | None:
    """Decode an `agy` `file:///` workspace URI (including `%3A`/`%20`/UTF-8) to Windows path."""
    raw = uri.strip()
    if not raw:
        return None
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() != "file":
        if re.match(r"^[a-zA-Z]:[\\/]", raw) or raw.startswith("\\\\") or raw.startswith("/"):
            return normalize_windows_path(raw)
        return None

    decoded_path = urllib.parse.unquote(parsed.path)
    if parsed.netloc and parsed.netloc.lower() not in ("", "localhost"):
        unc = f"\\\\{parsed.netloc}" + decoded_path.replace("/", "\\")
        return normalize_windows_path(unc)

    if os.name == "nt" and len(decoded_path) >= 3 and decoded_path[0] == "/" and decoded_path[2] == ":":
        decoded_path = decoded_path[1:]
        if len(decoded_path) == 2:
            decoded_path += "/"
    return normalize_windows_path(decoded_path)


def parse_workspace_uris(raw: str | None) -> list[str]:
    """Parse `workspace_uris` SQLite column (`'["file:///..."]'` or `''`)."""
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [str(item) for item in loaded if item]
        except json.JSONDecodeError:
            pass
    return [text]


def parse_ts(value: Any, fallback: datetime | None = None) -> datetime:
    """Parse ISO-8601, SQLite UTC string (up to 9 fractional digits), or epoch number."""
    if isinstance(value, datetime):
        if value.year >= 1970:
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        secs = value / 1000.0 if value > 1e12 else float(value)
        try:
            dt = datetime.fromtimestamp(secs, tz=timezone.utc)
            if dt.year >= 1970:
                return dt
        except (OverflowError, OSError, ValueError):
            pass
    elif isinstance(value, str):
        s = value.strip()
        if s:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            s = re.sub(r"\.(\d{6})\d+", r".\1", s)
            try:
                dt = datetime.fromisoformat(s)
                if dt.year >= 1970:
                    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return fallback or datetime.now(timezone.utc)


def format_iso_utc(dt: datetime) -> str:
    """Format datetime as UTC ISO-8601 string (`...Z`)."""
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def format_agy_sql_ts(dt: datetime) -> str:
    """Format datetime matching `agy` `conversation_summaries` datetime string."""
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return utc.strftime("%Y-%m-%d %H:%M:%S.%f+00:00")


def format_local_time(dt: datetime) -> str:
    if dt.year < 1970:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    try:
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def dt_to_ns(dt: datetime) -> int:
    """Convert datetime to integer UNIX nanoseconds."""
    try:
        utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        ts = utc.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0
    if ts < 0:
        return 0
    return int(ts * 1_000_000) * 1000


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


# ---------------------------------------------------------------------------
# Canonical Session Model
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Provider Abstraction & Registry
# ---------------------------------------------------------------------------


class AgentProvider(ABC):
    id: str
    name: str
    badge: str
    supported: bool
    coming_soon: bool

    def descriptor(self, count: int = 0, size_bytes: int = 0) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "badge": self.badge,
            "supported": self.supported,
            "comingSoon": self.coming_soon,
            "count": count,
            "sizeBytes": size_bytes,
            "sizeHuman": format_bytes(size_bytes),
        }

    @abstractmethod
    def list_sessions(
        self, include_all: bool = False, cwd_filter: str | None = None
    ) -> list[SessionSummary]: ...

    @abstractmethod
    def find_session(
        self, id_or_latest: str, cwd: str | None = None, all_cwd: bool = False
    ) -> tuple[Path, SessionSummary | None]: ...

    @abstractmethod
    def read_session(
        self, location: Path, summary: SessionSummary | None = None
    ) -> CanonicalSession: ...

    @abstractmethod
    def write_session(
        self,
        session: CanonicalSession,
        target_cwd: str | None,
        new_id: bool = False,
        overwrite: bool = False,
        include_thinking: bool = False,
        forced_target_id: str | None = None,
    ) -> tuple[str, Path, bool]: ...

    @abstractmethod
    def delete_session(self, session_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def resume_command(self, session_id: str) -> str: ...

    @abstractmethod
    def get_sync_fingerprint(self, summary: SessionSummary) -> tuple[int, int]: ...


class FutureProvider(AgentProvider):
    """Descriptor placeholder for upcoming coding agent providers."""

    def __init__(self, provider_id: str, name: str, badge: str = "Soon") -> None:
        self.id = provider_id
        self.name = name
        self.badge = badge
        self.supported = False
        self.coming_soon = True

    def _unsupported(self) -> SyncError:
        supported = ", ".join(p.id for p in get_supported_providers())
        return SyncError(
            f"Provider '{self.id}' ({self.name}) is not supported yet (coming soon). "
            f"Supported providers: {supported}."
        )

    def list_sessions(
        self, include_all: bool = False, cwd_filter: str | None = None
    ) -> list[SessionSummary]:
        raise self._unsupported()

    def find_session(
        self, id_or_latest: str, cwd: str | None = None, all_cwd: bool = False
    ) -> tuple[Path, SessionSummary | None]:
        raise self._unsupported()

    def read_session(
        self, location: Path, summary: SessionSummary | None = None
    ) -> CanonicalSession:
        raise self._unsupported()

    def write_session(
        self,
        session: CanonicalSession,
        target_cwd: str | None,
        new_id: bool = False,
        overwrite: bool = False,
        include_thinking: bool = False,
        forced_target_id: str | None = None,
    ) -> tuple[str, Path, bool]:
        raise self._unsupported()

    def delete_session(self, session_id: str) -> dict[str, Any]:
        raise self._unsupported()

    def resume_command(self, session_id: str) -> str:
        return ""

    def get_sync_fingerprint(self, summary: SessionSummary) -> tuple[int, int]:
        raise self._unsupported()


# ---------------------------------------------------------------------------
# Antigravity (agy) Provider
# ---------------------------------------------------------------------------


_CACHE_LOCK = threading.Lock()
_AGY_META_CACHE: dict[
    str, tuple[tuple[int, int, int, int], tuple[str | None, datetime | None, int, int]]
] = {}
_CLAUDE_SUMMARY_CACHE: dict[str, tuple[tuple[int, int, int, int], SessionSummary]] = {}
_DIR_SIZE_CACHE: dict[str, tuple[float, int, int]] = {}


def _invalidate_size_cache() -> None:
    with _CACHE_LOCK:
        _DIR_SIZE_CACHE.clear()


def _evict_session_cache(path: Path | None = None) -> None:
    with _CACHE_LOCK:
        if path is not None:
            key = str(path)
            _AGY_META_CACHE.pop(key, None)
            _CLAUDE_SUMMARY_CACHE.pop(key, None)
        _DIR_SIZE_CACHE.clear()


def _file_stat_sig(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return -1, -1


def _fast_file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _fast_dir_size(path: Path, ttl_sec: float = 5.0) -> int:
    if not path.is_dir():
        return 0
    key = str(path)
    now = time.monotonic()
    try:
        dir_mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return 0
    with _CACHE_LOCK:
        cached = _DIR_SIZE_CACHE.get(key)
        if cached is not None and cached[0] > now and cached[1] == dir_mtime_ns:
            return cached[2]

    total = 0
    stack = [key]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass

    with _CACHE_LOCK:
        _DIR_SIZE_CACHE[key] = (now + ttl_sec, dir_mtime_ns, total)
    return total


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


def get_claude_session_size(
    session_id: str, jsonl_path: Path | None = None
) -> tuple[int, int, int]:
    """Return `(total_bytes, main_bytes, sidecar_bytes)` for a Claude Code session."""
    main_bytes = _fast_file_size(jsonl_path) if jsonl_path else 0
    sidecar_dir = jsonl_path.with_suffix("") if jsonl_path else None
    fh_dir = get_claude_dir() / "file-history" / session_id
    sidecar_bytes = (_fast_dir_size(sidecar_dir) if sidecar_dir else 0) + _fast_dir_size(fh_dir)
    return main_bytes + sidecar_bytes, main_bytes, sidecar_bytes


def _connect_sqlite(path: Path, timeout: float = 15.0) -> sqlite3.Connection:
    """Open SQLite DB with busy timeout for WAL safety."""
    return sqlite3.connect(str(path), timeout=timeout)


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


def _build_chat_tool_call_pb(tc: CanonicalToolCall) -> bytes:
    """Encode ChatToolCall {1: id, 2: name, 3: arguments_json}."""
    return b"".join(
        [
            pb_str(1, tc.id),
            pb_str(2, tc.name),
            pb_str(3, tc.arguments_json or "{}"),
        ]
    )


def _build_step_metadata_pb(
    ts: datetime,
    source: int,
    traj_id: str,
    step_idx: int,
    conv_id: str,
    execution_id: str,
    tool_call: CanonicalToolCall | None = None,
) -> bytes:
    """Encode CortexStepMetadata protobuf bytes."""
    src_info_parts = [pb_str(1, traj_id)]
    if step_idx != 0:
        src_info_parts.append(pb_varint(2, step_idx))
    src_info_parts.append(pb_str(4, conv_id))

    parts = [
        pb_timestamp(1, ts),
        pb_varint(3, source),
    ]
    if tool_call is not None:
        parts.append(pb_msg(4, _build_chat_tool_call_pb(tool_call)))
    if source == SOURCE_MODEL:
        parts.append(pb_timestamp(8, ts))
    parts.append(pb_str(12, execution_id))
    parts.append(pb_msg(20, b"".join(src_info_parts)))
    if source == SOURCE_MODEL:
        parts.append(pb_timestamp(32, ts))
    return b"".join(parts)


def _build_trajectory_metadata_pb(
    workspace_uri: str | None,
    created_at: datetime,
    init_state_id: str,
    conv_id: str,
) -> bytes:
    """Encode CortexTrajectoryMetadata protobuf bytes."""
    parts: list[bytes] = []
    if workspace_uri:
        parts.append(pb_msg(1, pb_str(1, workspace_uri)))
    parts.append(pb_timestamp(2, created_at))
    parts.append(pb_str(3, init_state_id))
    parts.append(pb_str(6, conv_id))
    if workspace_uri:
        parts.append(pb_str(7, workspace_uri))
    parts.append(pb_str(18, "default-cli-project"))
    return b"".join(parts)


class AgyProvider(AgentProvider):
    id = "agy"
    name = "Antigravity"
    badge = "AGY"
    supported = True
    coming_soon = False

    def resume_command(self, session_id: str) -> str:
        return f"agy --conversation {session_id}"

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


# ---------------------------------------------------------------------------
# Claude Code (claude) Provider
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, AgentProvider] = {
    "agy": AgyProvider(),
    "claude": ClaudeProvider(),
    "opencode": FutureProvider("opencode", "OpenCode", badge="Soon"),
    "codex": FutureProvider("codex", "Codex", badge="Soon"),
}


def get_supported_providers() -> list[AgentProvider]:
    return [p for p in PROVIDERS.values() if p.supported]


def get_provider(provider_id: str, require_supported: bool = True) -> AgentProvider:
    pid = (provider_id or "").strip().lower()
    prov = PROVIDERS.get(pid)
    if prov is None:
        known = ", ".join(PROVIDERS.keys())
        raise SyncError(f"Unknown provider '{provider_id}'. Known providers: {known}.")
    if require_supported and not prov.supported:
        supported = ", ".join(p.id for p in get_supported_providers())
        raise SyncError(
            f"Provider '{prov.id}' ({prov.name}) is not supported yet (coming soon). "
            f"Supported providers: {supported}."
        )
    return prov


# ---------------------------------------------------------------------------
# Sync State, Ledger & Loop-Free Auto-Sync Engine
# ---------------------------------------------------------------------------

_SYNC_LOCK = threading.RLock()
_SYNC_RUNNING = False


def _default_state() -> dict[str, Any]:
    return {
        "config": {
            "enabled": False,
            "intervalSec": 30,
            "direction": "both",
            "scope": "all",
            "watchedFolders": [],
            "includeThinking": False,
        },
        "runtime": {
            "lastRunAt": None,
            "lastError": None,
        },
        "pairs": [],
        "skipped": {},
        "history": [],
    }


def _normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    d = _default_state()["config"]
    if not isinstance(raw, dict):
        return d
    enabled = bool(raw.get("enabled", d["enabled"]))
    try:
        interval = int(raw.get("intervalSec", d["intervalSec"]))
    except (TypeError, ValueError):
        interval = d["intervalSec"]
    interval = max(5, interval)

    direction = str(raw.get("direction", d["direction"])).strip().lower()
    if direction not in ("both", "agy2claude", "claude2agy"):
        direction = d["direction"]

    scope = str(raw.get("scope", d["scope"])).strip().lower()
    if scope not in ("all", "watched"):
        scope = d["scope"]

    watched: list[str] = []
    seen_norm: set[str] = set()
    raw_folders = raw.get("watchedFolders") or []
    if isinstance(raw_folders, list):
        for item in raw_folders:
            if not isinstance(item, str) or not item.strip():
                continue
            norm = normalize_windows_path(item)
            key = os.path.normcase(norm)
            if key not in seen_norm:
                seen_norm.add(key)
                watched.append(norm)

    include_thinking = bool(raw.get("includeThinking", d["includeThinking"]))
    return {
        "enabled": enabled,
        "intervalSec": interval,
        "direction": direction,
        "scope": scope,
        "watchedFolders": watched,
        "includeThinking": include_thinking,
    }


def _load_state() -> dict[str, Any]:
    with _SYNC_LOCK:
        path = get_sync_state_path()
        base = _default_state()
        if not path.is_file():
            return base
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return base
        if not isinstance(loaded, dict):
            return base
        base["config"] = _normalize_config(loaded.get("config"))
        rt = loaded.get("runtime")
        if isinstance(rt, dict):
            base["runtime"]["lastRunAt"] = rt.get("lastRunAt")
            base["runtime"]["lastError"] = rt.get("lastError")
        if isinstance(loaded.get("pairs"), list):
            base["pairs"] = [p for p in loaded["pairs"] if isinstance(p, dict)]
        if isinstance(loaded.get("skipped"), dict):
            base["skipped"] = dict(loaded["skipped"])
        if isinstance(loaded.get("history"), list):
            base["history"] = [h for h in loaded["history"] if isinstance(h, dict)][
                :MAX_HISTORY_ENTRIES
            ]
        return base


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.{uuid.uuid4().hex[:8]}.tmp"
    content = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        tmp_path.write_text(content, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _save_state(state: dict[str, Any]) -> None:
    with _SYNC_LOCK:
        state["history"] = state.get("history", [])[:MAX_HISTORY_ENTRIES]
        _atomic_write_json(get_sync_state_path(), state)


def _pair_matches_endpoint(pair: dict[str, Any], provider: str, sid: str) -> bool:
    return (pair.get("source") == provider and pair.get("source_id") == sid) or (
        pair.get("target") == provider and pair.get("target_id") == sid
    )


def _upsert_pair(
    state: dict[str, Any],
    source: str,
    source_id: str,
    target: str,
    target_id: str,
    src_fp: tuple[int, int],
    src_hash: str,
    tgt_fp: tuple[int, int],
    tgt_hash: str,
    cwd: str | None,
    title: str,
    now: datetime,
) -> None:
    pairs = state.setdefault("pairs", [])
    kept = [
        p
        for p in pairs
        if not (
            _pair_matches_endpoint(p, source, source_id)
            and _pair_matches_endpoint(p, target, target_id)
        )
    ]
    kept.append(
        {
            "source": source,
            "source_id": source_id,
            "target": target,
            "target_id": target_id,
            "last_source_mtime_ns": src_fp[0],
            "last_source_count": src_fp[1],
            "last_source_hash": src_hash,
            "last_target_mtime_ns": tgt_fp[0],
            "last_target_count": tgt_fp[1],
            "last_target_hash": tgt_hash,
            "cwd": cwd or "",
            "title": title or "",
            "last_sync_at": format_iso_utc(now),
        }
    )
    state["pairs"] = kept


def _append_history(
    state: dict[str, Any],
    *,
    kind: str,
    direction: str,
    source: str,
    target: str,
    source_id: str,
    target_id: str,
    title: str,
    cwd: str | None,
    status: str,
    message: str,
    resume_cmd: str = "",
    now: datetime | None = None,
) -> None:
    ts = now or datetime.now(timezone.utc)
    entry = {
        "timestamp": format_iso_utc(ts),
        "timestampLocal": format_local_time(ts),
        "kind": kind,
        "direction": direction,
        "source": source,
        "target": target,
        "sourceId": source_id,
        "targetId": target_id,
        "title": title,
        "cwd": cwd or "",
        "status": status,
        "message": message,
        "resumeCmd": resume_cmd,
    }
    history = state.setdefault("history", [])
    history.insert(0, entry)
    state["history"] = history[:MAX_HISTORY_ENTRIES]


def _remove_pairs_for(provider: str, sid: str) -> None:
    with _SYNC_LOCK:
        state = _load_state()
        before = len(state["pairs"])
        state["pairs"] = [
            p for p in state["pairs"] if not _pair_matches_endpoint(p, provider, sid)
        ]
        state.get("skipped", {}).pop(f"{provider}:{sid}", None)
        if len(state["pairs"]) != before:
            _save_state(state)
        else:
            _save_state(state)


def get_autosync_dict() -> dict[str, Any]:
    with _SYNC_LOCK:
        state = _load_state()
        cfg = state["config"]
        rt = state["runtime"]
        last_run_at = rt.get("lastRunAt")
        last_run_local = (
            format_local_time(parse_ts(last_run_at)) if last_run_at else None
        )
        return {
            "enabled": cfg["enabled"],
            "running": _SYNC_RUNNING,
            "intervalSec": cfg["intervalSec"],
            "direction": cfg["direction"],
            "scope": cfg["scope"],
            "watchedFolders": list(cfg["watchedFolders"]),
            "includeThinking": cfg["includeThinking"],
            "lastRunAt": last_run_at,
            "lastRunLocal": last_run_local,
            "lastError": rt.get("lastError"),
            "pairCount": len(state["pairs"]),
            "history": list(state["history"]),
        }


def update_autosync_config(
    *,
    enabled: bool | None = None,
    interval_sec: int | None = None,
    direction: str | None = None,
    scope: str | None = None,
    watched_folders: list[str] | None = None,
    add_watch: list[str] | None = None,
    remove_watch: list[str] | None = None,
    include_thinking: bool | None = None,
) -> dict[str, Any]:
    with _SYNC_LOCK:
        state = _load_state()
        cfg = dict(state["config"])

        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if interval_sec is not None:
            try:
                iv = int(interval_sec)
            except (TypeError, ValueError) as exc:
                raise SyncError(f"Invalid intervalSec '{interval_sec}'.") from exc
            cfg["intervalSec"] = max(5, iv)
        if direction is not None:
            d = str(direction).strip().lower()
            if d not in ("both", "agy2claude", "claude2agy"):
                raise SyncError(
                    f"Invalid direction '{direction}'. Use 'both', 'agy2claude', or 'claude2agy'."
                )
            cfg["direction"] = d
        if scope is not None:
            sc = str(scope).strip().lower()
            if sc not in ("all", "watched"):
                raise SyncError(f"Invalid scope '{scope}'. Use 'all' or 'watched'.")
            cfg["scope"] = sc
        if watched_folders is not None:
            if not isinstance(watched_folders, list):
                raise SyncError("watchedFolders must be a list of folder paths.")
            cfg["watchedFolders"] = watched_folders
        if add_watch:
            existing = list(cfg.get("watchedFolders", []))
            for w in add_watch:
                if w and w.strip():
                    existing.append(normalize_windows_path(w))
            cfg["watchedFolders"] = existing
        if remove_watch:
            remove_keys = {
                os.path.normcase(normalize_windows_path(w))
                for w in remove_watch
                if w and w.strip()
            }
            cfg["watchedFolders"] = [
                w
                for w in cfg.get("watchedFolders", [])
                if os.path.normcase(normalize_windows_path(w)) not in remove_keys
            ]
        if include_thinking is not None:
            cfg["includeThinking"] = bool(include_thinking)

        state["config"] = _normalize_config(cfg)
        _save_state(state)
    return get_autosync_dict()


def _extract_pair_sides(
    pair: dict[str, Any]
) -> tuple[str, tuple[int, int], str, str, tuple[int, int], str] | None:
    src = pair.get("source")
    tgt = pair.get("target")
    src_id = pair.get("source_id")
    tgt_id = pair.get("target_id")
    if not src_id or not tgt_id:
        return None
    src_fp = (
        int(pair.get("last_source_mtime_ns") or 0),
        int(pair.get("last_source_count") or 0),
    )
    tgt_fp = (
        int(pair.get("last_target_mtime_ns") or 0),
        int(pair.get("last_target_count") or 0),
    )
    src_hash = str(pair.get("last_source_hash") or "")
    tgt_hash = str(pair.get("last_target_hash") or "")
    if src == "agy" and tgt == "claude":
        return src_id, src_fp, src_hash, tgt_id, tgt_fp, tgt_hash
    if src == "claude" and tgt == "agy":
        return tgt_id, tgt_fp, tgt_hash, src_id, src_fp, src_hash
    return None


def _sync_one_pair_direction(
    *,
    src_prov: AgentProvider,
    tgt_prov: AgentProvider,
    src_sum: SessionSummary,
    tgt_id: str | None,
    overwrite: bool,
    include_thinking: bool,
    fallback_cwd: str,
    src_session: CanonicalSession | None = None,
    src_fp: tuple[int, int] | None = None,
    src_hash: str | None = None,
    state: dict[str, Any],
    kind: str,
) -> tuple[str, Path, bool]:
    """Perform a single directional copy and update ledger + history in `state`."""
    if src_session is None:
        src_session = src_prov.read_session(src_sum.location, summary=src_sum)
    if src_fp is None:
        src_fp = src_prov.get_sync_fingerprint(src_sum)
    if src_hash is None:
        src_hash = canonical_session_hash(src_session)

    target_cwd = src_session.cwd or src_sum.cwd or fallback_cwd
    new_tgt_id, out_path, regenerated = tgt_prov.write_session(
        src_session,
        target_cwd=target_cwd,
        new_id=False,
        overwrite=overwrite,
        include_thinking=include_thinking,
        forced_target_id=tgt_id if overwrite else None,
    )

    tgt_sum_stub = SessionSummary(
        session_id=new_tgt_id,
        source=tgt_prov.id,
        updated_at=src_session.updated_at,
        title=src_session.title,
        preview=src_session.preview,
        cwd=target_cwd,
        location=out_path,
        step_or_msg_count=0,
    )
    tgt_session = tgt_prov.read_session(out_path, summary=tgt_sum_stub)
    tgt_fp = tgt_prov.get_sync_fingerprint(tgt_sum_stub)
    tgt_hash = canonical_session_hash(tgt_session)

    now = datetime.now(timezone.utc)
    _upsert_pair(
        state=state,
        source=src_prov.id,
        source_id=src_session.session_id,
        target=tgt_prov.id,
        target_id=new_tgt_id,
        src_fp=src_fp,
        src_hash=src_hash,
        tgt_fp=tgt_fp,
        tgt_hash=tgt_hash,
        cwd=target_cwd,
        title=src_session.title,
        now=now,
    )
    resume_cmd = tgt_prov.resume_command(new_tgt_id)
    _append_history(
        state,
        kind=kind,
        direction=f"{src_prov.id}2{tgt_prov.id}",
        source=src_prov.id,
        target=tgt_prov.id,
        source_id=src_session.session_id,
        target_id=new_tgt_id,
        title=src_session.title,
        cwd=target_cwd,
        status="ok",
        message=f"Synced {len(src_session.turns)} turn(s) -> {tgt_prov.id}:{new_tgt_id[:8]}",
        resume_cmd=resume_cmd,
        now=now,
    )
    return new_tgt_id, out_path, regenerated


def run_autosync_cycle(force_even_if_disabled: bool = False) -> dict[str, Any]:
    """Run one loop-free bidirectional auto-sync pass."""
    global _SYNC_RUNNING
    with _SYNC_LOCK:
        if _SYNC_RUNNING:
            return {"ran": False, "synced": 0, "skipped": 0, "errors": 0, "message": "Sync already in progress."}
        _SYNC_RUNNING = True
        try:
            state = _load_state()
            cfg = state["config"]
            if not cfg["enabled"] and not force_even_if_disabled:
                return {"ran": False, "synced": 0, "skipped": 0, "errors": 0, "message": "Auto-sync is disabled."}

            direction = cfg["direction"]
            scope = cfg["scope"]
            watched_folders = cfg["watchedFolders"]
            include_thinking = cfg["includeThinking"]

            agy_prov = get_provider("agy")
            claude_prov = get_provider("claude")

            agy_list = agy_prov.list_sessions(include_all=False)
            claude_list = claude_prov.list_sessions(include_all=False)
            agy_map = {s.session_id: s for s in agy_list}
            claude_map = {s.session_id: s for s in claude_list}

            def in_scope(summary: SessionSummary) -> bool:
                if summary.step_or_msg_count <= 0:
                    return False
                if scope == "watched":
                    if not summary.cwd:
                        return False
                    return any(paths_match(summary.cwd, wf) for wf in watched_folders)
                return True

            fallback_cwd = str(_user_home())
            paired_agy: set[str] = set()
            paired_claude: set[str] = set()
            synced_count = 0
            unchanged_count = 0
            error_count = 0
            first_error: str | None = None
            new_pairs: list[dict[str, Any]] = []
            skipped_map: dict[str, Any] = dict(state.get("skipped", {}))

            # 1. Evaluate existing pairs in ledger
            for pair in list(state["pairs"]):
                sides = _extract_pair_sides(pair)
                if sides is None:
                    continue
                agy_id, agy_rec_fp, agy_rec_hash, claude_id, claude_rec_fp, claude_rec_hash = sides
                agy_sum = agy_map.get(agy_id)
                claude_sum = claude_map.get(claude_id)

                if agy_sum is None and claude_sum is None:
                    # Both deleted on disk -> prune stale pair
                    continue

                if agy_sum is not None:
                    paired_agy.add(agy_id)
                if claude_sum is not None:
                    paired_claude.add(claude_id)

                # If one side was removed externally, keep pair only if other side shouldn't sync,
                # or let surviving side re-sync if in scope and direction allows.
                if agy_sum is None or claude_sum is None:
                    surviving_src = agy_prov if agy_sum is not None else claude_prov
                    surviving_sum = agy_sum if agy_sum is not None else claude_sum
                    missing_tgt = claude_prov if agy_sum is not None else agy_prov
                    missing_tgt_id = claude_id if agy_sum is not None else agy_id
                    allowed = direction == "both" or direction == f"{surviving_src.id}2{missing_tgt.id}"
                    if surviving_sum and allowed and in_scope(surviving_sum):
                        try:
                            _sync_one_pair_direction(
                                src_prov=surviving_src,
                                tgt_prov=missing_tgt,
                                src_sum=surviving_sum,
                                tgt_id=missing_tgt_id,
                                overwrite=True,
                                include_thinking=include_thinking,
                                fallback_cwd=fallback_cwd,
                                state=state,
                                kind="auto",
                            )
                            synced_count += 1
                            new_pairs.append(state["pairs"][-1])
                        except Exception as exc:
                            error_count += 1
                            first_error = first_error or str(exc)
                            new_pairs.append(pair)
                            _append_history(
                                state,
                                kind="auto",
                                direction=f"{surviving_src.id}2{missing_tgt.id}",
                                source=surviving_src.id,
                                target=missing_tgt.id,
                                source_id=surviving_sum.session_id,
                                target_id=missing_tgt_id,
                                title=surviving_sum.title,
                                cwd=surviving_sum.cwd,
                                status="error",
                                message=str(exc),
                            )
                    else:
                        new_pairs.append(pair)
                    continue

                # Both sides exist on disk -> check fingerprints
                agy_fp = agy_prov.get_sync_fingerprint(agy_sum)
                claude_fp = claude_prov.get_sync_fingerprint(claude_sum)

                agy_changed = (agy_fp[0] > agy_rec_fp[0]) or (agy_fp[1] != agy_rec_fp[1])
                claude_changed = (claude_fp[0] > claude_rec_fp[0]) or (claude_fp[1] != claude_rec_fp[1])

                if not agy_changed and not claude_changed:
                    unchanged_count += 1
                    new_pairs.append(pair)
                    continue

                agy_sess: CanonicalSession | None = None
                agy_hash = agy_rec_hash
                if agy_changed:
                    try:
                        agy_sess = agy_prov.read_session(agy_sum.location, summary=agy_sum)
                        agy_fp = agy_prov.get_sync_fingerprint(agy_sum)
                        agy_hash = canonical_session_hash(agy_sess)
                        if agy_rec_hash and agy_hash == agy_rec_hash:
                            agy_changed = False
                    except Exception as exc:
                        error_count += 1
                        first_error = first_error or str(exc)
                        agy_changed = False

                claude_sess: CanonicalSession | None = None
                claude_hash = claude_rec_hash
                if claude_changed:
                    try:
                        claude_sess = claude_prov.read_session(claude_sum.location, summary=claude_sum)
                        claude_fp = claude_prov.get_sync_fingerprint(claude_sum)
                        claude_hash = canonical_session_hash(claude_sess)
                        if claude_rec_hash and claude_hash == claude_rec_hash:
                            claude_changed = False
                    except Exception as exc:
                        error_count += 1
                        first_error = first_error or str(exc)
                        claude_changed = False

                # Update metadata fingerprint in pair even if hashes matched (no write needed)
                if not agy_changed and not claude_changed:
                    updated_pair = dict(pair)
                    if updated_pair.get("source") == "agy":
                        updated_pair["last_source_mtime_ns"] = agy_fp[0]
                        updated_pair["last_source_count"] = agy_fp[1]
                        updated_pair["last_target_mtime_ns"] = claude_fp[0]
                        updated_pair["last_target_count"] = claude_fp[1]
                    else:
                        updated_pair["last_source_mtime_ns"] = claude_fp[0]
                        updated_pair["last_source_count"] = claude_fp[1]
                        updated_pair["last_target_mtime_ns"] = agy_fp[0]
                        updated_pair["last_target_count"] = agy_fp[1]
                    new_pairs.append(updated_pair)
                    unchanged_count += 1
                    continue

                # Determine winning sync direction
                chosen_dir: tuple[AgentProvider, AgentProvider, SessionSummary, CanonicalSession | None, tuple[int, int], str, str] | None = None
                if agy_changed and claude_changed:
                    if direction == "agy2claude":
                        chosen_dir = (agy_prov, claude_prov, agy_sum, agy_sess, agy_fp, agy_hash, claude_id)
                    elif direction == "claude2agy":
                        chosen_dir = (claude_prov, agy_prov, claude_sum, claude_sess, claude_fp, claude_hash, agy_id)
                    else:
                        if agy_fp[0] >= claude_fp[0]:
                            chosen_dir = (agy_prov, claude_prov, agy_sum, agy_sess, agy_fp, agy_hash, claude_id)
                        else:
                            chosen_dir = (claude_prov, agy_prov, claude_sum, claude_sess, claude_fp, claude_hash, agy_id)
                elif agy_changed and direction in ("both", "agy2claude") and in_scope(agy_sum):
                    chosen_dir = (agy_prov, claude_prov, agy_sum, agy_sess, agy_fp, agy_hash, claude_id)
                elif claude_changed and direction in ("both", "claude2agy") and in_scope(claude_sum):
                    chosen_dir = (claude_prov, agy_prov, claude_sum, claude_sess, claude_fp, claude_hash, agy_id)

                if chosen_dir is None:
                    new_pairs.append(pair)
                    unchanged_count += 1
                    continue

                s_prov, t_prov, s_sum, s_sess, s_fp, s_hash, t_id = chosen_dir
                try:
                    _sync_one_pair_direction(
                        src_prov=s_prov,
                        tgt_prov=t_prov,
                        src_sum=s_sum,
                        tgt_id=t_id,
                        overwrite=True,
                        include_thinking=include_thinking,
                        fallback_cwd=fallback_cwd,
                        src_session=s_sess,
                        src_fp=s_fp,
                        src_hash=s_hash,
                        state=state,
                        kind="auto",
                    )
                    synced_count += 1
                    new_pairs.append(state["pairs"][-1])
                except Exception as exc:
                    error_count += 1
                    first_error = first_error or str(exc)
                    new_pairs.append(pair)
                    _append_history(
                        state,
                        kind="auto",
                        direction=f"{s_prov.id}2{t_prov.id}",
                        source=s_prov.id,
                        target=t_prov.id,
                        source_id=s_sum.session_id,
                        target_id=t_id,
                        title=s_sum.title,
                        cwd=s_sum.cwd,
                        status="error",
                        message=str(exc),
                    )

            state["pairs"] = new_pairs

            # 2. Pre-existing unpaired sessions that already share the same UUID across both providers
            shared_unpaired = (set(agy_map.keys()) - paired_agy) & (
                set(claude_map.keys()) - paired_claude
            )
            for sid in sorted(shared_unpaired):
                agy_sum = agy_map[sid]
                claude_sum = claude_map[sid]
                if not (in_scope(agy_sum) or in_scope(claude_sum)):
                    continue
                paired_agy.add(sid)
                paired_claude.add(sid)
                try:
                    agy_sess = agy_prov.read_session(agy_sum.location, summary=agy_sum)
                    agy_fp = agy_prov.get_sync_fingerprint(agy_sum)
                    agy_hash = canonical_session_hash(agy_sess)

                    claude_sess = claude_prov.read_session(claude_sum.location, summary=claude_sum)
                    claude_fp = claude_prov.get_sync_fingerprint(claude_sum)
                    claude_hash = canonical_session_hash(claude_sess)

                    # Decide if already identical or one is newer
                    if agy_hash == claude_hash:
                        _upsert_pair(
                            state=state,
                            source="agy",
                            source_id=sid,
                            target="claude",
                            target_id=sid,
                            src_fp=agy_fp,
                            src_hash=agy_hash,
                            tgt_fp=claude_fp,
                            tgt_hash=claude_hash,
                            cwd=agy_sess.cwd or claude_sess.cwd or fallback_cwd,
                            title=agy_sess.title or claude_sess.title,
                            now=datetime.now(timezone.utc),
                        )
                        unchanged_count += 1
                    elif direction == "agy2claude" or (
                        direction == "both" and agy_sum.updated_at >= claude_sum.updated_at
                    ):
                        _sync_one_pair_direction(
                            src_prov=agy_prov,
                            tgt_prov=claude_prov,
                            src_sum=agy_sum,
                            tgt_id=sid,
                            overwrite=True,
                            include_thinking=include_thinking,
                            fallback_cwd=fallback_cwd,
                            src_session=agy_sess,
                            src_fp=agy_fp,
                            src_hash=agy_hash,
                            state=state,
                            kind="auto",
                        )
                        synced_count += 1
                    else:
                        _sync_one_pair_direction(
                            src_prov=claude_prov,
                            tgt_prov=agy_prov,
                            src_sum=claude_sum,
                            tgt_id=sid,
                            overwrite=True,
                            include_thinking=include_thinking,
                            fallback_cwd=fallback_cwd,
                            src_session=claude_sess,
                            src_fp=claude_fp,
                            src_hash=claude_hash,
                            state=state,
                            kind="auto",
                        )
                        synced_count += 1
                except Exception as exc:
                    error_count += 1
                    first_error = first_error or str(exc)

            # 3. Discover brand-new unpaired sessions
            passes: list[tuple[AgentProvider, AgentProvider, dict[str, SessionSummary], set[str], set[str]]] = []
            if direction in ("both", "agy2claude"):
                passes.append((agy_prov, claude_prov, agy_map, paired_agy, paired_claude))
            if direction in ("both", "claude2agy"):
                passes.append((claude_prov, agy_prov, claude_map, paired_claude, paired_agy))

            for src_prov, tgt_prov, src_map, seen_src, seen_tgt in passes:
                for sid, summary in src_map.items():
                    if sid in seen_src or not in_scope(summary):
                        continue
                    fp_now = src_prov.get_sync_fingerprint(summary)
                    skip_key = f"{src_prov.id}:{sid}"
                    prev_skip = skipped_map.get(skip_key)
                    if (
                        isinstance(prev_skip, dict)
                        and prev_skip.get("mtime_ns") == fp_now[0]
                        and prev_skip.get("count") == fp_now[1]
                    ):
                        unchanged_count += 1
                        continue
                    try:
                        tgt_id, _out_path, _regen = _sync_one_pair_direction(
                            src_prov=src_prov,
                            tgt_prov=tgt_prov,
                            src_sum=summary,
                            tgt_id=None,
                            overwrite=False,
                            include_thinking=include_thinking,
                            fallback_cwd=fallback_cwd,
                            state=state,
                            kind="auto",
                        )
                        seen_src.add(sid)
                        seen_tgt.add(tgt_id)
                        skipped_map.pop(skip_key, None)
                        synced_count += 1
                    except Exception as exc:
                        skipped_map[skip_key] = {
                            "mtime_ns": fp_now[0],
                            "count": fp_now[1],
                            "error": str(exc),
                        }
                        error_count += 1
                        first_error = first_error or str(exc)
                        _append_history(
                            state,
                            kind="auto",
                            direction=f"{src_prov.id}2{tgt_prov.id}",
                            source=src_prov.id,
                            target=tgt_prov.id,
                            source_id=sid,
                            target_id="",
                            title=summary.title,
                            cwd=summary.cwd,
                            status="error",
                            message=str(exc),
                        )

            state["skipped"] = skipped_map
            now = datetime.now(timezone.utc)
            state["runtime"]["lastRunAt"] = format_iso_utc(now)
            state["runtime"]["lastError"] = first_error
            _save_state(state)

            msg = (
                f"Auto-sync: {synced_count} synced, {unchanged_count} unchanged"
                + (f", {error_count} error(s)" if error_count else "")
                + "."
            )
            return {
                "ran": True,
                "synced": synced_count,
                "unchanged": unchanged_count,
                "errors": error_count,
                "message": msg,
            }
        finally:
            _SYNC_RUNNING = False


# ---------------------------------------------------------------------------
# High-Level Session Operations (shared by CLI and HTTP API)
# ---------------------------------------------------------------------------


def _resolve_source_for_copy(
    id_or_latest: str,
    from_provider_id: str | None,
    to_provider_id: str,
    cwd: str | None,
    all_cwd: bool,
) -> tuple[AgentProvider, Path, SessionSummary | None]:
    if from_provider_id:
        prov = get_provider(from_provider_id, require_supported=True)
        loc, summary = prov.find_session(id_or_latest, cwd=cwd, all_cwd=all_cwd)
        return prov, loc, summary

    supported = get_supported_providers()
    if id_or_latest.lower() == "latest":
        candidates: list[tuple[AgentProvider, Path, SessionSummary]] = []
        pool = [p for p in supported if p.id != to_provider_id] or supported
        target_cwd = None if all_cwd else normalize_windows_path(cwd or os.getcwd())
        for prov in pool:
            items = prov.list_sessions(include_all=False, cwd_filter=target_cwd)
            if items:
                candidates.append((prov, items[0].location, items[0]))
        if not candidates:
            scope = "any workspace" if all_cwd else f"workspace '{target_cwd}'"
            raise SyncError(f"No conversation found for {scope}.")
        candidates.sort(key=lambda item: item[2].updated_at, reverse=True)
        prov, loc, summ = candidates[0]
        return prov, loc, summ

    found: list[tuple[AgentProvider, Path, SessionSummary | None]] = []
    for prov in supported:
        try:
            loc, summ = prov.find_session(id_or_latest, cwd=cwd, all_cwd=all_cwd)
            found.append((prov, loc, summ))
        except SyncError:
            pass
    if not found:
        raise SyncError(f"Session '{id_or_latest}' not found in any supported provider.")
    if len(found) == 1:
        return found[0]
    other = [f for f in found if f[0].id != to_provider_id]
    if len(other) == 1:
        return other[0]
    names = ", ".join(f[0].id for f in found)
    raise SyncError(
        f"Session '{id_or_latest}' exists in multiple providers ({names}). Pass --from."
    )


def copy_one_session(
    *,
    id_or_latest: str,
    to_provider_id: str,
    from_provider_id: str | None = None,
    cwd: str | None = None,
    target_cwd: str | None = None,
    all_cwd: bool = False,
    new_id: bool = False,
    overwrite: bool = False,
    include_thinking: bool = False,
    kind: str = "manual",
) -> dict[str, Any]:
    to_prov = get_provider(to_provider_id, require_supported=True)
    src_prov, loc, summary = _resolve_source_for_copy(
        id_or_latest=id_or_latest,
        from_provider_id=from_provider_id,
        to_provider_id=to_prov.id,
        cwd=cwd,
        all_cwd=all_cwd,
    )
    session = src_prov.read_session(loc, summary=summary)
    if src_prov.id == to_prov.id and not new_id and not overwrite and not target_cwd:
        raise SyncError(
            f"Source and target provider are both '{to_prov.id}'. "
            "Use --new-id or --target-cwd to clone."
        )

    effective_cwd = (
        normalize_windows_path(target_cwd)
        if target_cwd
        else (
            normalize_windows_path(cwd)
            if cwd
            else (session.cwd or normalize_windows_path(os.getcwd()))
        )
    )
    src_fp = src_prov.get_sync_fingerprint(
        summary
        or SessionSummary(
            session_id=session.session_id,
            source=src_prov.id,
            updated_at=session.updated_at,
            title=session.title,
            preview=session.preview,
            cwd=session.cwd,
            location=loc,
            step_or_msg_count=len(session.turns),
        )
    )
    src_hash = canonical_session_hash(session)

    # Check if already paired when overwrite=True and source/target IDs differ
    forced_target_id: str | None = None
    if overwrite and not new_id and src_prov.id != to_prov.id:
        with _SYNC_LOCK:
            state = _load_state()
            for p in state["pairs"]:
                if p.get("source") == src_prov.id and p.get("source_id") == session.session_id and p.get("target") == to_prov.id:
                    forced_target_id = p.get("target_id")
                    break
                if p.get("target") == src_prov.id and p.get("target_id") == session.session_id and p.get("source") == to_prov.id:
                    forced_target_id = p.get("source_id")
                    break

    tgt_id, out_path, regenerated = to_prov.write_session(
        session,
        target_cwd=effective_cwd,
        new_id=new_id,
        overwrite=overwrite,
        include_thinking=include_thinking,
        forced_target_id=forced_target_id,
    )

    if src_prov.id != to_prov.id:
        tgt_stub = SessionSummary(
            session_id=tgt_id,
            source=to_prov.id,
            updated_at=session.updated_at,
            title=session.title,
            preview=session.preview,
            cwd=effective_cwd,
            location=out_path,
            step_or_msg_count=0,
        )
        tgt_session = to_prov.read_session(out_path, summary=tgt_stub)
        tgt_fp = to_prov.get_sync_fingerprint(tgt_stub)
        tgt_hash = canonical_session_hash(tgt_session)

        with _SYNC_LOCK:
            state = _load_state()
            now = datetime.now(timezone.utc)
            already_paired_elsewhere = any(
                _pair_matches_endpoint(p, src_prov.id, session.session_id)
                and not _pair_matches_endpoint(p, to_prov.id, tgt_id)
                for p in state["pairs"]
            )
            if not (new_id and already_paired_elsewhere):
                _upsert_pair(
                    state=state,
                    source=src_prov.id,
                    source_id=session.session_id,
                    target=to_prov.id,
                    target_id=tgt_id,
                    src_fp=src_fp,
                    src_hash=src_hash,
                    tgt_fp=tgt_fp,
                    tgt_hash=tgt_hash,
                    cwd=effective_cwd,
                    title=session.title,
                    now=now,
                )
            _append_history(
                state,
                kind=kind,
                direction=f"{src_prov.id}2{to_prov.id}",
                source=src_prov.id,
                target=to_prov.id,
                source_id=session.session_id,
                target_id=tgt_id,
                title=session.title,
                cwd=effective_cwd,
                status="ok",
                message=(
                    f"Copied {len(session.turns)} turn(s)"
                    + (" (new UUID on collision)" if regenerated else "")
                ),
                resume_cmd=to_prov.resume_command(tgt_id),
                now=now,
            )
            _save_state(state)

    resume_cmd = to_prov.resume_command(tgt_id)
    return {
        "ok": True,
        "status": "ok",
        "source": src_prov.id,
        "sourceId": session.session_id,
        "toProvider": to_prov.id,
        "targetId": tgt_id,
        "targetCwd": effective_cwd,
        "title": session.title,
        "path": str(out_path),
        "regenerated": regenerated,
        "resumeCmd": resume_cmd,
        "resumeDir": effective_cwd,
        "message": f"Copied {src_prov.id}:{session.session_id[:8]} -> {to_prov.id}:{tgt_id[:8]}",
        "error": None,
    }


def sync_directory_sessions(
    *,
    from_provider_id: str,
    to_provider_id: str,
    cwd: str,
    target_cwd: str | None = None,
    overwrite: bool = False,
    include_thinking: bool = False,
) -> list[dict[str, Any]]:
    from_prov = get_provider(from_provider_id, require_supported=True)
    to_prov = get_provider(to_provider_id, require_supported=True)
    if from_prov.id == to_prov.id:
        raise SyncError("Source and target providers for sync-dir must be different.")

    norm_src_cwd = normalize_windows_path(cwd)
    norm_tgt_cwd = normalize_windows_path(target_cwd) if target_cwd else norm_src_cwd

    src_sessions = from_prov.list_sessions(include_all=False, cwd_filter=norm_src_cwd)
    tgt_ids = {s.session_id for s in to_prov.list_sessions(include_all=True)}

    with _SYNC_LOCK:
        state = _load_state()
        pair_map: dict[str, str] = {}
        for p in state["pairs"]:
            if p.get("source") == from_prov.id and p.get("target") == to_prov.id:
                pair_map[str(p.get("source_id"))] = str(p.get("target_id"))
            elif p.get("target") == from_prov.id and p.get("source") == to_prov.id:
                pair_map[str(p.get("target_id"))] = str(p.get("source_id"))

    results: list[dict[str, Any]] = []
    for summary in src_sessions:
        existing_tgt_id = pair_map.get(summary.session_id)
        target_exists = (
            (existing_tgt_id in tgt_ids)
            if existing_tgt_id
            else (summary.session_id in tgt_ids)
        )
        if target_exists and not overwrite:
            known_id = existing_tgt_id or summary.session_id
            results.append(
                {
                    "ok": True,
                    "status": "skipped",
                    "source": from_prov.id,
                    "sourceId": summary.session_id,
                    "toProvider": to_prov.id,
                    "targetId": known_id,
                    "targetCwd": norm_tgt_cwd,
                    "title": summary.title,
                    "path": None,
                    "regenerated": False,
                    "resumeCmd": to_prov.resume_command(known_id),
                    "resumeDir": norm_tgt_cwd,
                    "message": "Already exists in target (skipped without --overwrite).",
                    "error": None,
                }
            )
            continue
        try:
            res = copy_one_session(
                id_or_latest=summary.session_id,
                from_provider_id=from_prov.id,
                to_provider_id=to_prov.id,
                cwd=norm_src_cwd,
                target_cwd=norm_tgt_cwd,
                all_cwd=True,
                new_id=False,
                overwrite=overwrite,
                include_thinking=include_thinking,
                kind="manual",
            )
            results.append(res)
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "status": "error",
                    "source": from_prov.id,
                    "sourceId": summary.session_id,
                    "toProvider": to_prov.id,
                    "targetId": None,
                    "targetCwd": norm_tgt_cwd,
                    "title": summary.title,
                    "path": None,
                    "regenerated": False,
                    "resumeCmd": None,
                    "resumeDir": norm_tgt_cwd,
                    "message": str(exc),
                    "error": str(exc),
                }
            )
    return results


def _build_synced_to_lookup(
    all_summaries_by_provider: dict[str, dict[str, SessionSummary]]
) -> dict[tuple[str, str], list[dict[str, str]]]:
    with _SYNC_LOCK:
        state = _load_state()
        pairs = list(state["pairs"])

    lookup: dict[tuple[str, str], list[dict[str, str]]] = {}

    def add_link(src: str, sid: str, tgt: str, tid: str) -> None:
        tgt_map = all_summaries_by_provider.get(tgt, {})
        tgt_sum = tgt_map.get(tid)
        if tgt_sum is None:
            return
        prov = PROVIDERS.get(tgt)
        if not prov:
            return
        arr = lookup.setdefault((src, sid), [])
        if any(x["provider"] == tgt and x["id"] == tid for x in arr):
            return
        arr.append(
            {
                "provider": tgt,
                "id": tid,
                "title": tgt_sum.title,
                "preview": tgt_sum.preview,
                "cwd": tgt_sum.cwd or "",
                "updatedAt": format_iso_utc(tgt_sum.updated_at),
                "updatedLocal": format_local_time(tgt_sum.updated_at),
                "count": tgt_sum.step_or_msg_count,
                "sizeBytes": tgt_sum.size_bytes,
                "sizeHuman": format_bytes(tgt_sum.size_bytes),
                "extra": tgt_sum.extra,
                "resumeCmd": prov.resume_command(tid),
                "resumeDir": tgt_sum.cwd or "",
            }
        )

    for p in pairs:
        s = str(p.get("source") or "")
        sid = str(p.get("source_id") or "")
        t = str(p.get("target") or "")
        tid = str(p.get("target_id") or "")
        if s and sid and t and tid:
            add_link(s, sid, t, tid)
            add_link(t, tid, s, sid)

    # Also detect identical-UUID pairs created externally before ledger existed
    agy_ids = set(all_summaries_by_provider.get("agy", {}).keys())
    claude_ids = set(all_summaries_by_provider.get("claude", {}).keys())
    for shared_id in agy_ids & claude_ids:
        add_link("agy", shared_id, "claude", shared_id)
        add_link("claude", shared_id, "agy", shared_id)

    return lookup


def _collect_sessions_by_provider(
    include_all: bool,
) -> dict[str, list[SessionSummary]]:
    out: dict[str, list[SessionSummary]] = {}
    for prov in get_supported_providers():
        out[prov.id] = prov.list_sessions(include_all=include_all, cwd_filter=None)
    return out


def _matches_query(s: SessionSummary, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    haystack = " ".join(
        [
            s.session_id,
            s.source,
            s.title,
            s.preview,
            s.cwd or "",
            s.extra,
        ]
    ).lower()
    return q in haystack


def _resolve_synced_peers(provider_id: str, session_id: str) -> list[tuple[str, str]]:
    """Return synced counterpart `(other_provider, other_id)` entries for `provider_id:session_id`."""
    peers: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    with _SYNC_LOCK:
        state = _load_state()
        for p in state.get("pairs", []):
            s = str(p.get("source") or "")
            sid = str(p.get("source_id") or "")
            t = str(p.get("target") or "")
            tid = str(p.get("target_id") or "")
            if s == provider_id and sid == session_id and t and tid:
                key = (t, tid)
                if key not in seen:
                    seen.add(key)
                    peers.append(key)
            elif t == provider_id and tid == session_id and s and sid:
                key = (s, sid)
                if key not in seen:
                    seen.add(key)
                    peers.append(key)

    for prov in get_supported_providers():
        if prov.id == provider_id or not is_valid_uuid(session_id):
            continue
        key = (prov.id, session_id)
        if key in seen:
            continue
        try:
            prov.find_session(session_id)
            seen.add(key)
            peers.append(key)
        except SyncError:
            pass
    return peers


def delete_sessions_batch(
    items: list[dict[str, Any]],
    *,
    delete_synced_peers: bool = False,
) -> dict[str, Any]:
    """Delete one or more sessions cleanly (with optional counterpart removal)."""
    if not items:
        raise SyncError("At least one session item is required for deletion.")

    queue: list[tuple[str, str, bool]] = []
    for item in items:
        src = str(item.get("source") or "").strip().lower()
        sid = str(item.get("id") or "").strip()
        if not src or not sid:
            continue
        queue.append((src, sid, False))

    if not queue:
        raise SyncError("source and id are required for deletion.")

    if delete_synced_peers:
        expanded: list[tuple[str, str, bool]] = []
        for src, sid, is_peer in queue:
            expanded.append((src, sid, is_peer))
            try:
                prov = get_provider(src, require_supported=True)
                loc, summary = prov.find_session(sid)
                resolved_id = summary.session_id if summary else loc.stem
                for peer_src, peer_id in _resolve_synced_peers(prov.id, resolved_id):
                    expanded.append((peer_src, peer_id, True))
            except SyncError:
                pass
        queue = expanded

    done_keys: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    total_freed = 0
    deleted_count = 0

    for src, sid, is_peer in queue:
        try:
            prov = get_provider(src, require_supported=True)
            loc, summary = prov.find_session(sid)
            resolved_id = summary.session_id if summary else loc.stem
            title = summary.title if summary else resolved_id
            cwd_val = summary.cwd if summary else ""
        except SyncError as exc:
            if is_peer:
                continue
            results.append(
                {
                    "ok": False,
                    "source": src,
                    "id": sid,
                    "freedBytes": 0,
                    "freedHuman": "0 B",
                    "error": str(exc),
                }
            )
            continue

        key = (prov.id, resolved_id)
        if key in done_keys:
            continue
        done_keys.add(key)

        try:
            del_res = prov.delete_session(resolved_id)
            _remove_pairs_for(prov.id, del_res["id"])
            freed = int(del_res.get("freedBytes") or 0)
            total_freed += freed
            deleted_count += 1
            results.append(
                {
                    "ok": True,
                    "source": prov.id,
                    "id": del_res["id"],
                    "title": title,
                    "cwd": cwd_val or "",
                    "freedBytes": freed,
                    "freedHuman": format_bytes(freed),
                    "childrenDeleted": int(del_res.get("childrenDeleted") or 0),
                    "isSyncedPeer": is_peer,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "source": prov.id,
                    "id": resolved_id,
                    "freedBytes": 0,
                    "freedHuman": "0 B",
                    "error": str(exc),
                }
            )

    if deleted_count == 0:
        first_err = next((r["error"] for r in results if r.get("error")), "No sessions were deleted.")
        raise SyncError(first_err)

    with _SYNC_LOCK:
        state = _load_state()
        for r in results:
            if not r.get("ok"):
                continue
            _append_history(
                state,
                kind="delete",
                direction=r["source"],
                source=r["source"],
                target="",
                source_id=r["id"],
                target_id="",
                title=r.get("title") or r["id"],
                cwd=r.get("cwd") or "",
                status="ok",
                message=f"Deleted ({r['freedHuman']} freed)",
            )
        _save_state(state)

    msg = f"Deleted {deleted_count} session(s) (freed {format_bytes(total_freed)})."
    return {
        "ok": True,
        "deletedCount": deleted_count,
        "freedBytes": total_freed,
        "freedHuman": format_bytes(total_freed),
        "results": results,
        "message": msg,
    }


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

    child_ids: list[str] = []
    if prov.id == "agy":
        child_ids = _find_agy_descendants(real_id)
        before_bytes, _, _ = get_agy_session_size(
            real_id, db_path=loc, child_ids=child_ids if purge_sidecars else None, app_data_dir=app_dir
        )
    else:
        before_bytes, _, _ = get_claude_session_size(real_id, jsonl_path=loc)

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
        else:
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

    if prov.id == "agy":
        after_bytes, _, _ = get_agy_session_size(real_id, db_path=out_path, app_data_dir=app_dir)
    else:
        after_bytes, _, _ = get_claude_session_size(real_id, jsonl_path=out_path)
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


def _scan_storage_orphans(
    source: str = "all", cwd_filter: str | None = None
) -> dict[str, Any]:
    """Identify orphaned sidecars and stale DB rows without touching active sessions."""
    src = (source or "all").strip().lower()
    cli_dir = get_agy_cli_dir()
    ide_dir = get_agy_ide_dir()
    claude_dir = get_claude_dir()
    proj_dir = get_claude_projects_dir()

    orphan_paths: list[tuple[str, Path, int, str]] = []  # (provider, path, bytes, label)
    stale_summary_ids: list[str] = []

    if src in ("all", "agy"):
        cli_conv_ids = (
            {p.stem for p in (cli_dir / "conversations").glob("*.db") if p.is_file()}
            if (cli_dir / "conversations").is_dir()
            else set()
        )
        ide_conv_ids = (
            {p.stem for p in (ide_dir / "conversations").glob("*.db") if p.is_file()}
            if (ide_dir / "conversations").is_dir()
            else set()
        )
        if not cwd_filter:
            br_root = cli_dir / "brain"
            if br_root.is_dir():
                for d in br_root.iterdir():
                    if d.is_dir() and d.name not in cli_conv_ids:
                        sz = _fast_dir_size(d)
                        orphan_paths.append(("agy", d, sz, f"orphan brain/{d.name}"))
            ann_root = cli_dir / "annotations"
            if ann_root.is_dir():
                for f in ann_root.glob("*.pbtxt"):
                    if f.is_file() and f.stem not in cli_conv_ids:
                        orphan_paths.append(("agy", f, _fast_file_size(f), f"orphan annotations/{f.name}"))
            imp_root = cli_dir / "implicit"
            if imp_root.is_dir():
                for f in imp_root.glob("*.pb"):
                    if f.is_file() and f.stem not in cli_conv_ids:
                        orphan_paths.append(("agy", f, _fast_file_size(f), f"orphan implicit/{f.name}"))

            sum_db = cli_dir / "conversation_summaries.db"
            if sum_db.is_file():
                con = _connect_sqlite(sum_db)
                try:
                    for row in con.execute(
                        "SELECT conversation_id, app_data_dir FROM conversation_summaries"
                    ):
                        cid = str(row[0] or "")
                        app_d = str(row[1] or "antigravity-cli")
                        if not cid:
                            continue
                        if app_d == "antigravity":
                            if cid not in ide_conv_ids and cid not in cli_conv_ids:
                                stale_summary_ids.append(cid)
                        elif cid not in cli_conv_ids and cid not in ide_conv_ids:
                            stale_summary_ids.append(cid)
                except sqlite3.Error:
                    pass
                finally:
                    con.close()

    if src in ("all", "claude"):
        target_slug = windows_path_to_slug(cwd_filter).lower() if cwd_filter else None
        all_claude_ids: set[str] = set()
        if proj_dir.is_dir():
            for pdir in proj_dir.iterdir():
                if not pdir.is_dir():
                    continue
                jsonl_stems: set[str] = set()
                for jf in pdir.glob("*.jsonl"):
                    if jf.is_file():
                        jsonl_stems.add(jf.stem)
                        all_claude_ids.add(jf.stem)
                if target_slug and pdir.name.lower() != target_slug:
                    continue
                for sub in pdir.iterdir():
                    if sub.is_dir() and sub.name not in jsonl_stems:
                        sz = _fast_dir_size(sub)
                        orphan_paths.append(("claude", sub, sz, f"orphan claude/{pdir.name}/{sub.name}"))

        if not cwd_filter:
            fh_root = claude_dir / "file-history"
            if fh_root.is_dir():
                for d in fh_root.iterdir():
                    if d.is_dir() and d.name not in all_claude_ids:
                        sz = _fast_dir_size(d)
                        orphan_paths.append(("claude", d, sz, f"orphan file-history/{d.name}"))

    orphan_bytes = sum(x[2] for x in orphan_paths)
    return {
        "orphanPaths": orphan_paths,
        "staleSummaryIds": stale_summary_ids,
        "orphanCount": len(orphan_paths) + len(stale_summary_ids),
        "orphanBytes": orphan_bytes,
    }


def get_storage_summary(
    by_prov_all: dict[str, list[SessionSummary]] | None = None
) -> dict[str, Any]:
    cli_dir = get_agy_cli_dir()
    claude_dir = get_claude_dir()

    sum_db = cli_dir / "conversation_summaries.db"
    agy_bytes = (
        _fast_dir_size(cli_dir / "conversations")
        + _fast_dir_size(cli_dir / "brain")
        + _fast_dir_size(cli_dir / "annotations")
        + _fast_dir_size(cli_dir / "implicit")
        + _fast_file_size(sum_db)
        + _fast_file_size(Path(str(sum_db) + "-wal"))
        + _fast_file_size(Path(str(sum_db) + "-shm"))
    )
    claude_bytes = (
        _fast_dir_size(claude_dir / "projects")
        + _fast_dir_size(claude_dir / "file-history")
    )
    total_bytes = agy_bytes + claude_bytes

    orphans = _scan_storage_orphans("all")
    empty_count = 0
    subagent_count = 0
    if by_prov_all:
        for s in by_prov_all.get("agy", []):
            extras = [x.strip() for x in s.extra.split(",") if x.strip()]
            if "antigravity" in extras:
                continue
            if s.step_or_msg_count == 0:
                empty_count += 1
            if any(x.startswith("depth=") and x != "depth=0" for x in extras):
                subagent_count += 1
        for s in by_prov_all.get("claude", []):
            if s.step_or_msg_count == 0:
                empty_count += 1
            sub_dir = s.location.with_suffix("") / "subagents"
            if sub_dir.is_dir():
                subagent_count += 1

    return {
        "agyBytes": agy_bytes,
        "agyHuman": format_bytes(agy_bytes),
        "claudeBytes": claude_bytes,
        "claudeHuman": format_bytes(claude_bytes),
        "totalBytes": total_bytes,
        "totalHuman": format_bytes(total_bytes),
        "orphanBytes": orphans["orphanBytes"],
        "orphanHuman": format_bytes(orphans["orphanBytes"]),
        "orphanCount": orphans["orphanCount"],
        "emptyCount": empty_count,
        "subagentCount": subagent_count,
    }


def clean_storage(
    *,
    source: str = "all",
    cwd: str | None = None,
    clean_orphans: bool = True,
    delete_empty: bool = False,
    delete_subagents: bool = False,
    older_than_days: int | None = None,
    larger_than_mb: float | None = None,
    vacuum_db: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reclaim disk space by removing orphans, empty/subagent/old/large sessions, and vacuuming SQLite."""
    src = (source or "all").strip().lower()
    if src != "all":
        get_provider(src, require_supported=True)
    cwd_filter = normalize_windows_path(cwd) if cwd and cwd.strip() else None

    now = datetime.now(timezone.utc)
    min_age_sec = older_than_days * 86400.0 if (older_than_days is not None and older_than_days >= 1) else None
    min_size_bytes = int(larger_than_mb * 1024 * 1024) if (larger_than_mb is not None and larger_than_mb > 0) else None

    orphan_info = (
        _scan_storage_orphans(source=src, cwd_filter=cwd_filter)
        if clean_orphans
        else {"orphanPaths": [], "staleSummaryIds": [], "orphanCount": 0, "orphanBytes": 0}
    )

    sessions_to_delete: dict[tuple[str, str], tuple[SessionSummary, str]] = {}
    claude_subagent_dirs: list[tuple[Path, int, str]] = []

    all_by_prov = _collect_sessions_by_provider(include_all=True)
    cwd_slug_lower = windows_path_to_slug(cwd_filter).lower() if cwd_filter else None

    def matches_cwd(s: SessionSummary) -> bool:
        if not cwd_filter:
            return True
        if s.cwd:
            return paths_match(s.cwd, cwd_filter)
        if s.source == "claude" and cwd_slug_lower:
            return s.location.parent.name.lower() == cwd_slug_lower
        return False

    for pid, slist in all_by_prov.items():
        if src != "all" and pid != src:
            continue
        for s in slist:
            extras = [x.strip() for x in s.extra.split(",") if x.strip()]
            if "antigravity" in extras:
                continue
            if not matches_cwd(s):
                continue

            is_subagent = any(x.startswith("depth=") and x != "depth=0" for x in extras)
            reason = ""
            if delete_empty and s.step_or_msg_count == 0:
                reason = "empty (0 turns)"
            elif delete_subagents and is_subagent:
                reason = "subagent"
            elif (min_age_sec is not None or min_size_bytes is not None) and not is_subagent and s.step_or_msg_count > 0:
                age_ok = True
                size_ok = True
                if min_age_sec is not None:
                    age_ok = (now - s.updated_at).total_seconds() >= min_age_sec
                if min_size_bytes is not None:
                    size_ok = s.size_bytes >= min_size_bytes
                if age_ok and size_ok:
                    parts = []
                    if min_age_sec is not None:
                        parts.append(f">{older_than_days}d")
                    if min_size_bytes is not None:
                        parts.append(f">={larger_than_mb}MB")
                    reason = " & ".join(parts)

            if reason:
                sessions_to_delete[(s.source, s.session_id)] = (s, reason)
            elif delete_subagents and pid == "claude":
                sub_dir = s.location.with_suffix("") / "subagents"
                if sub_dir.is_dir():
                    sz = _fast_dir_size(sub_dir)
                    claude_subagent_dirs.append((sub_dir, sz, f"claude/{s.session_id[:8]}/subagents"))

    would_free = (
        orphan_info["orphanBytes"]
        + sum(s.size_bytes for s, _ in sessions_to_delete.values())
        + sum(sz for _, sz, _ in claude_subagent_dirs)
    )

    preview_items: list[dict[str, Any]] = []
    for _prov, p, sz, label in orphan_info["orphanPaths"][:25]:
        preview_items.append({"kind": "orphan", "label": label, "bytes": sz, "human": format_bytes(sz)})
    for sid_stale in orphan_info["staleSummaryIds"][:10]:
        preview_items.append({"kind": "db-stale", "label": f"stale index row {sid_stale[:12]}", "bytes": 0, "human": "0 B"})
    for _dir, sz, label in claude_subagent_dirs[:20]:
        preview_items.append({"kind": "subagents", "label": label, "bytes": sz, "human": format_bytes(sz)})
    for (_src, _sid), (s, reason) in list(sessions_to_delete.items())[:30]:
        preview_items.append(
            {
                "kind": "session",
                "source": s.source,
                "id": s.session_id,
                "label": f"{s.source}:{s.session_id[:8]} {truncate_preview(s.title, 48)} ({reason})",
                "bytes": s.size_bytes,
                "human": format_bytes(s.size_bytes),
            }
        )

    if dry_run:
        msg = (
            f"Dry run: would delete {len(sessions_to_delete)} session(s), "
            f"{orphan_info['orphanCount']} orphan(s), "
            f"{len(claude_subagent_dirs)} Claude subagent dir(s) -> "
            f"would free {format_bytes(would_free)}."
        )
        return {
            "ok": True,
            "dryRun": True,
            "sessionsDeleted": len(sessions_to_delete),
            "orphansCleaned": orphan_info["orphanCount"],
            "subagentDirsCleaned": len(claude_subagent_dirs),
            "freedBytes": would_free,
            "freedHuman": format_bytes(would_free),
            "vacuumSavedBytes": 0,
            "vacuumSavedHuman": "0 B",
            "previewItems": preview_items,
            "message": msg,
        }

    freed_bytes = 0
    orphans_cleaned = 0
    sessions_deleted = 0
    subagent_dirs_cleaned = 0

    for _prov, p, sz, _label in orphan_info["orphanPaths"]:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                freed_bytes += sz
                orphans_cleaned += 1
            elif p.is_file():
                p.unlink(missing_ok=True)
                freed_bytes += sz
                orphans_cleaned += 1
        except OSError:
            pass

    sum_db = get_agy_cli_dir() / "conversation_summaries.db"
    if orphan_info["staleSummaryIds"] and sum_db.is_file():
        con = _connect_sqlite(sum_db)
        try:
            for cid in orphan_info["staleSummaryIds"]:
                for tbl in ("conversation_summaries", "conversations_fts", "conversation_parents"):
                    try:
                        con.execute(f"DELETE FROM {tbl} WHERE conversation_id = ?", (cid,))
                    except sqlite3.Error:
                        pass
                orphans_cleaned += 1
            con.commit()
        finally:
            con.close()

    for sub_dir, sz, _label in claude_subagent_dirs:
        if sub_dir.is_dir():
            shutil.rmtree(sub_dir, ignore_errors=True)
            freed_bytes += sz
            subagent_dirs_cleaned += 1
            parent_dir = sub_dir.parent
            try:
                if parent_dir.is_dir() and not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
            except OSError:
                pass

    for (_src, _sid), (s, _reason) in sessions_to_delete.items():
        try:
            prov = get_provider(s.source, require_supported=True)
            dres = prov.delete_session(s.session_id)
            _remove_pairs_for(prov.id, dres["id"])
            freed_bytes += int(dres.get("freedBytes") or 0)
            sessions_deleted += 1
        except Exception:
            pass

    vacuum_saved = 0
    if vacuum_db and src in ("all", "agy") and sum_db.is_file():
        before_sum = (
            _fast_file_size(sum_db)
            + _fast_file_size(Path(str(sum_db) + "-wal"))
            + _fast_file_size(Path(str(sum_db) + "-shm"))
        )
        try:
            con = _connect_sqlite(sum_db)
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("VACUUM")
                con.commit()
            finally:
                con.close()
            after_sum = (
                _fast_file_size(sum_db)
                + _fast_file_size(Path(str(sum_db) + "-wal"))
                + _fast_file_size(Path(str(sum_db) + "-shm"))
            )
            vacuum_saved = max(0, before_sum - after_sum)
            freed_bytes += vacuum_saved
        except sqlite3.Error:
            pass

    _invalidate_size_cache()

    if sessions_deleted or orphans_cleaned or subagent_dirs_cleaned or vacuum_saved:
        with _SYNC_LOCK:
            state = _load_state()
            _append_history(
                state,
                kind="clean",
                direction=src,
                source=src,
                target="",
                source_id="",
                target_id="",
                title="Storage cleanup",
                cwd=cwd_filter or "",
                status="ok",
                message=(
                    f"Freed {format_bytes(freed_bytes)} "
                    f"({sessions_deleted} sessions, {orphans_cleaned} orphans, "
                    f"{subagent_dirs_cleaned} subagent dirs)"
                ),
            )
            _save_state(state)

    msg = (
        f"Cleanup complete: freed {format_bytes(freed_bytes)} "
        f"({sessions_deleted} session(s), {orphans_cleaned} orphan(s), "
        f"{subagent_dirs_cleaned} Claude subagent dir(s))."
    )
    return {
        "ok": True,
        "dryRun": False,
        "sessionsDeleted": sessions_deleted,
        "orphansCleaned": orphans_cleaned,
        "subagentDirsCleaned": subagent_dirs_cleaned,
        "freedBytes": freed_bytes,
        "freedHuman": format_bytes(freed_bytes),
        "vacuumSavedBytes": vacuum_saved,
        "vacuumSavedHuman": format_bytes(vacuum_saved),
        "previewItems": preview_items,
        "message": msg,
    }


def _sort_sessions(sessions: list[SessionSummary], sort: str = "updated_desc") -> None:
    sort_key = (sort or "updated_desc").strip().lower()
    if sort_key in ("size_desc", "size"):
        sessions.sort(key=lambda s: (s.size_bytes, s.updated_at), reverse=True)
    elif sort_key == "size_asc":
        sessions.sort(key=lambda s: (s.size_bytes, s.updated_at))
    elif sort_key in ("turns_desc", "turns"):
        sessions.sort(key=lambda s: (s.step_or_msg_count, s.updated_at), reverse=True)
    elif sort_key in ("updated_asc", "oldest"):
        sessions.sort(key=lambda s: s.updated_at)
    else:
        sessions.sort(key=lambda s: s.updated_at, reverse=True)


def query_sessions_api(
    *,
    source: str = "all",
    cwd: str | None = None,
    q: str | None = None,
    include_all: bool = False,
    limit: int = 200,
    sort: str = "updated_desc",
) -> dict[str, Any]:
    src = (source or "all").strip().lower()
    if src != "all":
        get_provider(src, require_supported=True)

    sort_key = (sort or "updated_desc").strip().lower()
    cwd_filter = normalize_windows_path(cwd) if cwd and cwd.strip() else None

    # Fetch full inventory (include_all=True) to compute accurate syncedTo links even if target is unindexed/all
    all_by_prov_list = _collect_sessions_by_provider(include_all=True)
    all_by_prov_map = {
        pid: {s.session_id: s for s in slist}
        for pid, slist in all_by_prov_list.items()
    }
    synced_lookup = _build_synced_to_lookup(all_by_prov_map)

    if include_all:
        base_by_prov = all_by_prov_list
    else:
        base_by_prov = _collect_sessions_by_provider(include_all=False)

    matched: list[SessionSummary] = []
    cwd_slug_lower = windows_path_to_slug(cwd_filter).lower() if cwd_filter else None
    for pid, slist in base_by_prov.items():
        if src != "all" and pid != src:
            continue
        for s in slist:
            if cwd_filter:
                if s.cwd:
                    if not paths_match(s.cwd, cwd_filter):
                        continue
                elif pid == "claude" and cwd_slug_lower:
                    if s.location.parent.name.lower() != cwd_slug_lower:
                        continue
                else:
                    continue
            if q and not _matches_query(s, q):
                continue
            matched.append(s)

    _sort_sessions(matched, sort_key)

    total = len(matched)
    if limit and limit > 0:
        matched = matched[:limit]

    items = []
    for s in matched:
        prov = PROVIDERS[s.source]
        items.append(
            {
                "id": s.session_id,
                "source": s.source,
                "title": s.title,
                "preview": s.preview,
                "cwd": s.cwd or "",
                "updatedAt": format_iso_utc(s.updated_at),
                "updatedLocal": format_local_time(s.updated_at),
                "count": s.step_or_msg_count,
                "sizeBytes": s.size_bytes,
                "sizeHuman": format_bytes(s.size_bytes),
                "extra": s.extra,
                "resumeCmd": prov.resume_command(s.session_id),
                "resumeDir": s.cwd or "",
                "syncedTo": synced_lookup.get((s.source, s.session_id), []),
            }
        )
    return {"sessions": items, "total": total}


def get_session_detail(source: str | None, session_id: str) -> dict[str, Any]:
    if not session_id or not session_id.strip():
        raise SyncError("Session ID is required.")
    sid = session_id.strip()

    if source:
        prov = get_provider(source, require_supported=True)
        loc, summary = prov.find_session(sid)
    else:
        matches: list[tuple[AgentProvider, Path, SessionSummary | None]] = []
        for prov in get_supported_providers():
            try:
                loc, summary = prov.find_session(sid)
                matches.append((prov, loc, summary))
            except SyncError:
                pass
        if not matches:
            raise SyncError(f"Session '{sid}' not found in any supported provider.")
        if len(matches) > 1:
            names = ", ".join(m[0].id for m in matches)
            raise SyncError(
                f"Session '{sid}' exists in multiple providers ({names}). Specify --source."
            )
        prov, loc, summary = matches[0]

    session = prov.read_session(loc, summary=summary)
    all_by_prov_list = _collect_sessions_by_provider(include_all=True)
    all_by_prov_map = {
        pid: {s.session_id: s for s in slist}
        for pid, slist in all_by_prov_list.items()
    }
    synced_lookup = _build_synced_to_lookup(all_by_prov_map)

    if prov.id == "agy":
        app_dir = "antigravity" if (summary and "antigravity" in summary.extra.split(",")) else "antigravity-cli"
        child_ids = _find_agy_descendants(session.session_id)
        size_bytes, main_bytes, sidecar_bytes = get_agy_session_size(
            session.session_id, db_path=loc, child_ids=child_ids, app_data_dir=app_dir
        )
    else:
        size_bytes, main_bytes, sidecar_bytes = get_claude_session_size(
            session.session_id, jsonl_path=loc
        )

    turns_json = []
    for i, t in enumerate(session.turns):
        turns_json.append(
            {
                "index": i,
                "role": t.role,
                "text": t.text,
                "thinking": t.thinking,
                "timestamp": format_iso_utc(t.timestamp),
                "timestampLocal": format_local_time(t.timestamp),
                "toolCalls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments_json,
                    }
                    for tc in t.tool_calls
                ],
                "toolResults": [
                    {
                        "toolUseId": tr.tool_use_id,
                        "name": tr.name,
                        "arguments": tr.arguments_json,
                        "content": tr.content,
                        "isError": tr.is_error,
                    }
                    for tr in t.tool_results
                ],
            }
        )

    return {
        "id": session.session_id,
        "source": session.source,
        "title": session.title,
        "preview": session.preview,
        "cwd": session.cwd or "",
        "createdAt": format_iso_utc(session.created_at),
        "updatedAt": format_iso_utc(session.updated_at),
        "updatedLocal": format_local_time(session.updated_at),
        "count": summary.step_or_msg_count if summary else len(session.turns),
        "sizeBytes": size_bytes,
        "sizeHuman": format_bytes(size_bytes),
        "mainBytes": main_bytes,
        "mainHuman": format_bytes(main_bytes),
        "sidecarBytes": sidecar_bytes,
        "sidecarHuman": format_bytes(sidecar_bytes),
        "extra": summary.extra if summary else "",
        "path": str(loc),
        "resumeCmd": prov.resume_command(session.session_id),
        "resumeDir": session.cwd or "",
        "syncedTo": synced_lookup.get((session.source, session.session_id), []),
        "turns": turns_json,
    }


def get_status_dict(port: int | None = None) -> dict[str, Any]:
    by_prov = _collect_sessions_by_provider(include_all=False)
    by_prov_all = _collect_sessions_by_provider(include_all=True)
    storage = get_storage_summary(by_prov_all=by_prov_all)

    providers_out = []
    ws_buckets: dict[str, dict[str, Any]] = {}

    for pid, prov in PROVIDERS.items():
        slist = by_prov.get(pid, [])
        psize = (
            storage["agyBytes"]
            if pid == "agy"
            else storage["claudeBytes"]
            if pid == "claude"
            else 0
        )
        providers_out.append(prov.descriptor(count=len(slist), size_bytes=psize))
        for s in slist:
            if not s.cwd:
                continue
            key = os.path.normcase(s.cwd)
            bucket = ws_buckets.get(key)
            if bucket is None:
                bucket = {
                    "cwd": s.cwd,
                    "total": 0,
                    "counts": {p.id: 0 for p in get_supported_providers()},
                }
                ws_buckets[key] = bucket
            bucket["total"] += 1
            bucket["counts"][pid] = bucket["counts"].get(pid, 0) + 1

    workspaces = sorted(
        ws_buckets.values(), key=lambda w: (-int(w["total"]), str(w["cwd"]).lower())
    )

    return {
        "version": VERSION,
        "autostart": test_autostart(),
        "port": port if port is not None else get_default_port(),
        "root": str(get_mcas_root()),
        "providers": providers_out,
        "workspaces": workspaces,
        "storage": storage,
        "autosync": get_autosync_dict(),
    }


# ---------------------------------------------------------------------------
# HTTP JSON API Server + Auto-Sync Background Daemon
# ---------------------------------------------------------------------------


def _build_handler(
    *,
    port: int,
    token: str,
    ui_html_path: Path,
    stop_event: threading.Event,
    wake_event: threading.Event,
    server_ref: list[ThreadingHTTPServer],
) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class McasRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_bytes(self, status_code: int, content_type: str, body: bytes) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _send_json(self, status_code: int, obj: Any) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status_code, "application/json; charset=utf-8", body)

        def _check_host(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            if host not in allowed_hosts:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                return False
            return True

        def _read_json_body(self) -> dict[str, Any]:
            raw_len = self.headers.get("Content-Length", "0").strip()
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise SyncError("Invalid Content-Length.") from exc
            if length < 0 or length > MAX_REQUEST_BODY_BYTES:
                raise SyncError("Request body size out of bounds.")
            if length == 0:
                return {}
            raw_bytes = self.rfile.read(length)
            if not raw_bytes:
                return {}
            try:
                parsed = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SyncError("Invalid JSON request body.") from exc
            if not isinstance(parsed, dict):
                raise SyncError("JSON request body must be an object.")
            return parsed

        def _check_api_token(self) -> bool:
            req_token = (self.headers.get("X-Mcas-Token") or "").strip()
            if req_token != token:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._check_host():
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if path == "/":
                if not ui_html_path.is_file():
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"ui.html not found: {ui_html_path}"},
                    )
                    return
                html_text = ui_html_path.read_text(encoding="utf-8")
                rendered = html_text.replace("__MCAS_TOKEN__", token)
                self._send_bytes(
                    HTTPStatus.OK,
                    "text/html; charset=utf-8",
                    rendered.encode("utf-8"),
                )
                return

            if path == "/health":
                self._send_json(HTTPStatus.OK, {"app": APP_NAME, "version": VERSION})
                return

            if not path.startswith("/api/"):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            if not self._check_api_token():
                return

            action = path[5:]
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            try:
                if action == "status":
                    self._send_json(HTTPStatus.OK, get_status_dict(port=port))
                    return
                if action == "sessions":
                    src = (qs.get("source", ["all"])[0] or "all").strip()
                    cwd_val = qs.get("cwd", [""])[0] or None
                    q_val = qs.get("q", [""])[0] or None
                    sort_val = (qs.get("sort", ["updated_desc"])[0] or "updated_desc").strip()
                    all_raw = (qs.get("all", ["0"])[0] or "0").strip().lower()
                    include_all = all_raw in ("1", "true", "yes", "on")
                    limit_raw = (qs.get("limit", ["200"])[0] or "200").strip()
                    try:
                        limit_val = int(limit_raw)
                    except ValueError as exc:
                        raise SyncError(f"Invalid limit '{limit_raw}'.") from exc
                    res = query_sessions_api(
                        source=src,
                        cwd=cwd_val,
                        q=q_val,
                        include_all=include_all,
                        limit=limit_val,
                        sort=sort_val,
                    )
                    self._send_json(HTTPStatus.OK, res)
                    return
                if action == "session":
                    src = (qs.get("source", [""])[0] or "").strip() or None
                    sid = (qs.get("id", [""])[0] or "").strip()
                    detail = get_session_detail(src, sid)
                    self._send_json(HTTPStatus.OK, detail)
                    return

                self._send_json(
                    HTTPStatus.NOT_FOUND, {"error": f"Unknown API endpoint '{action}'"}
                )
            except SyncError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def do_POST(self) -> None:
            if not self._check_host():
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if not path.startswith("/api/"):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._check_api_token():
                return

            action = path[5:]
            try:
                body = self._read_json_body()

                if action == "quit":
                    self._send_json(HTTPStatus.OK, {"message": "bye"})

                    def _shutdown_async() -> None:
                        stop_event.set()
                        wake_event.set()
                        if server_ref:
                            server_ref[0].shutdown()

                    threading.Thread(target=_shutdown_async, daemon=True).start()
                    return

                if action == "copy":
                    to_provider = str(body.get("toProvider") or "").strip()
                    if not to_provider:
                        raise SyncError("toProvider is required.")
                    get_provider(to_provider, require_supported=True)

                    items = body.get("items")
                    if items is None and body.get("id"):
                        items = [{"source": body.get("source"), "id": body.get("id")}]
                    if not isinstance(items, list) or not items:
                        raise SyncError("items must be a non-empty list.")

                    target_cwd = body.get("targetCwd")
                    target_cwd_str = (
                        str(target_cwd).strip() if isinstance(target_cwd, str) and target_cwd.strip() else None
                    )
                    new_id = bool(body.get("newId", False))
                    overwrite = bool(body.get("overwrite", False))
                    include_thinking = bool(body.get("includeThinking", False))

                    results: list[dict[str, Any]] = []
                    ok_count = 0
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_src = str(item.get("source") or "").strip() or None
                        item_id = str(item.get("id") or "").strip()
                        try:
                            r = copy_one_session(
                                id_or_latest=item_id,
                                from_provider_id=item_src,
                                to_provider_id=to_provider,
                                target_cwd=target_cwd_str,
                                all_cwd=True,
                                new_id=new_id,
                                overwrite=overwrite,
                                include_thinking=include_thinking,
                                kind="manual",
                            )
                            results.append(r)
                            ok_count += 1
                        except Exception as exc:
                            results.append(
                                {
                                    "ok": False,
                                    "status": "error",
                                    "source": item_src or "",
                                    "sourceId": item_id,
                                    "toProvider": to_provider,
                                    "targetId": None,
                                    "targetCwd": target_cwd_str,
                                    "title": "",
                                    "path": None,
                                    "regenerated": False,
                                    "resumeCmd": None,
                                    "resumeDir": target_cwd_str,
                                    "message": str(exc),
                                    "error": str(exc),
                                }
                            )

                    if ok_count == 0:
                        err_msg = (
                            results[0]["error"] if results else "No sessions could be copied."
                        )
                        self._send_json(
                            HTTPStatus.BAD_REQUEST,
                            {"error": err_msg, "results": results},
                        )
                        return

                    err_count = len(results) - ok_count
                    msg = f"Copied {ok_count} session(s) to {to_provider}" + (
                        f" ({err_count} failed)." if err_count else "."
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "message": msg,
                            "results": results,
                            "status": get_status_dict(port=port),
                        },
                    )
                    return

                if action == "sync-dir":
                    from_provider = str(body.get("fromProvider") or "").strip()
                    to_provider = str(body.get("toProvider") or "").strip()
                    cwd_val = str(body.get("cwd") or "").strip()
                    if not from_provider or not to_provider:
                        raise SyncError("fromProvider and toProvider are required.")
                    if not cwd_val:
                        raise SyncError("Workspace folder (cwd) is required.")
                    target_cwd = body.get("targetCwd")
                    target_cwd_str = (
                        str(target_cwd).strip() if isinstance(target_cwd, str) and target_cwd.strip() else None
                    )
                    overwrite = bool(body.get("overwrite", False))
                    include_thinking = bool(body.get("includeThinking", False))

                    results = sync_directory_sessions(
                        from_provider_id=from_provider,
                        to_provider_id=to_provider,
                        cwd=cwd_val,
                        target_cwd=target_cwd_str,
                        overwrite=overwrite,
                        include_thinking=include_thinking,
                    )
                    copied = sum(1 for r in results if r["status"] == "ok")
                    skipped = sum(1 for r in results if r["status"] == "skipped")
                    errors = sum(1 for r in results if r["status"] == "error")
                    if results and copied == 0 and skipped == 0 and errors > 0:
                        self._send_json(
                            HTTPStatus.BAD_REQUEST,
                            {"error": results[0]["error"], "results": results},
                        )
                        return
                    msg = (
                        f"Folder sync {from_provider} -> {to_provider}: "
                        f"{copied} copied, {skipped} skipped"
                        + (f", {errors} error(s)." if errors else ".")
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "message": msg,
                            "results": results,
                            "status": get_status_dict(port=port),
                        },
                    )
                    return

                if action == "delete":
                    raw_items = body.get("items")
                    if isinstance(raw_items, list):
                        items = raw_items
                    else:
                        src = str(body.get("source") or "").strip()
                        sid = str(body.get("id") or "").strip()
                        if not src or not sid:
                            raise SyncError("source and id (or items) are required.")
                        items = [{"source": src, "id": sid}]
                    delete_peers = bool(body.get("deleteSyncedPeers", False))
                    del_res = delete_sessions_batch(
                        items,
                        delete_synced_peers=delete_peers,
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            **del_res,
                            "status": get_status_dict(port=port),
                        },
                    )
                    return

                if action == "trim":
                    src = str(body.get("source") or "").strip()
                    sid = str(body.get("id") or "").strip()
                    if not src or not sid:
                        raise SyncError("source and id are required.")
                    keep_ratio_raw = body.get("keepRatio")
                    keep_ratio: float | None = None
                    if keep_ratio_raw is not None and str(keep_ratio_raw).strip() != "":
                        try:
                            keep_ratio = float(keep_ratio_raw)
                        except (TypeError, ValueError) as exc:
                            raise SyncError(f"Invalid keepRatio '{keep_ratio_raw}'.") from exc
                    keep_turns_raw = body.get("keepLastTurns")
                    keep_turns: int | None = None
                    if keep_turns_raw is not None and str(keep_turns_raw).strip() != "":
                        try:
                            keep_turns = int(keep_turns_raw)
                        except (TypeError, ValueError) as exc:
                            raise SyncError(f"Invalid keepLastTurns '{keep_turns_raw}'.") from exc
                    max_chars_raw = body.get("maxToolOutputChars", 2000)
                    try:
                        max_chars = int(max_chars_raw) if max_chars_raw is not None else 2000
                    except (TypeError, ValueError) as exc:
                        raise SyncError(f"Invalid maxToolOutputChars '{max_chars_raw}'.") from exc
                    trim_res = trim_session(
                        source=src,
                        session_id=sid,
                        keep_ratio=keep_ratio,
                        keep_last_turns=keep_turns,
                        max_tool_output_chars=max_chars,
                        strip_thinking=bool(body.get("stripThinking", True)),
                        purge_sidecars=bool(body.get("purgeSidecars", True)),
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            **trim_res,
                            "status": get_status_dict(port=port),
                        },
                    )
                    return

                if action == "clean":
                    src = str(body.get("source") or "all").strip() or "all"
                    cwd_val = str(body.get("cwd") or "").strip() or None
                    older_raw = body.get("olderThanDays")
                    older_days: int | None = None
                    if older_raw is not None and str(older_raw).strip() != "":
                        try:
                            older_days = int(older_raw)
                        except (TypeError, ValueError) as exc:
                            raise SyncError(f"Invalid olderThanDays '{older_raw}'.") from exc
                    larger_raw = body.get("largerThanMb")
                    larger_mb: float | None = None
                    if larger_raw is not None and str(larger_raw).strip() != "":
                        try:
                            larger_mb = float(larger_raw)
                        except (TypeError, ValueError) as exc:
                            raise SyncError(f"Invalid largerThanMb '{larger_raw}'.") from exc
                    clean_res = clean_storage(
                        source=src,
                        cwd=cwd_val,
                        clean_orphans=bool(body.get("cleanOrphans", True)),
                        delete_empty=bool(body.get("deleteEmpty", False)),
                        delete_subagents=bool(body.get("deleteSubagents", False)),
                        older_than_days=older_days,
                        larger_than_mb=larger_mb,
                        vacuum_db=bool(body.get("vacuumDb", True)),
                        dry_run=bool(body.get("dryRun", False)),
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            **clean_res,
                            "status": get_status_dict(port=port),
                        },
                    )
                    return

                if action == "autosync":
                    prev_enabled = get_autosync_dict()["enabled"]
                    updated = update_autosync_config(
                        enabled=body["enabled"] if "enabled" in body else None,
                        interval_sec=body["intervalSec"] if "intervalSec" in body else None,
                        direction=body["direction"] if "direction" in body else None,
                        scope=body["scope"] if "scope" in body else None,
                        watched_folders=body["watchedFolders"] if "watchedFolders" in body else None,
                        include_thinking=body["includeThinking"] if "includeThinking" in body else None,
                    )
                    run_now = bool(body.get("runNow", False))
                    if run_now:
                        run_res = run_autosync_cycle(force_even_if_disabled=True)
                        msg = run_res["message"]
                        wake_event.set()
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "message": msg,
                                "autosync": get_autosync_dict(),
                                "runResult": run_res,
                                "status": get_status_dict(port=port),
                            },
                        )
                        return
                    if not prev_enabled and updated["enabled"]:
                        wake_event.set()
                    msg = (
                        f"Auto-sync {'enabled' if updated['enabled'] else 'disabled'} "
                        f"({updated['direction']}, {updated['intervalSec']}s, scope={updated['scope']})."
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "message": msg,
                            "autosync": updated,
                            "status": get_status_dict(port=port),
                        },
                    )
                    return

                if action == "autostart":
                    on = bool(body.get("on", False))
                    msg = set_autostart(on, port)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "message": msg,
                            "autostart": test_autostart(),
                            "status": get_status_dict(port=port),
                        },
                    )
                    return

                self._send_json(
                    HTTPStatus.NOT_FOUND, {"error": f"Unknown API endpoint '{action}'"}
                )
            except SyncError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def do_OPTIONS(self) -> None:
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

        def do_PUT(self) -> None:
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

        def do_DELETE(self) -> None:
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

    return McasRequestHandler


class _McasHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def serve_http(port: int, ui_html_path: Path) -> int:
    if port < 1024 or port > 65535:
        raise SyncError(f"Invalid port '{port}' (use 1024-65535).")
    if not ui_html_path.is_file():
        raise SyncError(f"ui.html not found: {ui_html_path}")

    token = uuid.uuid4().hex
    stop_event = threading.Event()
    wake_event = threading.Event()
    server_ref: list[ThreadingHTTPServer] = []

    handler_cls = _build_handler(
        port=port,
        token=token,
        ui_html_path=ui_html_path,
        stop_event=stop_event,
        wake_event=wake_event,
        server_ref=server_ref,
    )

    try:
        httpd = _McasHTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as exc:
        raise SyncError(f"Could not listen on 127.0.0.1:{port}: {exc}") from exc
    server_ref.append(httpd)

    ui_json_path = get_ui_json_path()
    _atomic_write_json(
        ui_json_path,
        {"port": port, "token": token, "pid": os.getpid()},
    )

    def _autosync_worker() -> None:
        last_tick = 0.0
        while not stop_event.is_set():
            try:
                cfg = get_autosync_dict()
                interval = max(5, int(cfg.get("intervalSec", 30)))
                enabled = bool(cfg.get("enabled", False))
            except Exception:
                interval = 30
                enabled = False

            now_mono = time.monotonic()
            if enabled and (now_mono - last_tick >= interval):
                try:
                    run_autosync_cycle(force_even_if_disabled=False)
                except Exception:
                    pass
                last_tick = time.monotonic()

            if wake_event.wait(timeout=1.0):
                wake_event.clear()
                last_tick = 0.0

    worker = threading.Thread(target=_autosync_worker, name="mcas-autosync", daemon=True)
    worker.start()

    def _handle_sig(_signum: int, _frame: Any) -> None:
        def _stop() -> None:
            stop_event.set()
            wake_event.set()
            httpd.shutdown()

        threading.Thread(target=_stop, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_sig)
        except (ValueError, OSError):
            pass

    try:
        httpd.serve_forever(poll_interval=0.1)
    finally:
        stop_event.set()
        wake_event.set()
        ui_json_path.unlink(missing_ok=True)
        try:
            httpd.server_close()
        except Exception:
            pass
        worker.join(timeout=2.0)

    return 0


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------


def _parse_bool_str(val: str) -> bool:
    s = (val or "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise SyncError(f"Invalid boolean value '{val}'. Use true|false.")


def cmd_list(args: argparse.Namespace) -> int:
    src = (args.source or "all").strip().lower()
    if src != "all":
        get_provider(src, require_supported=True)
    cwd_filter = normalize_windows_path(args.cwd) if args.cwd else None

    sessions: list[SessionSummary] = []
    for prov in get_supported_providers():
        if src in ("all", prov.id):
            sessions.extend(
                prov.list_sessions(include_all=args.all, cwd_filter=cwd_filter)
            )

    if args.q:
        sessions = [s for s in sessions if _matches_query(s, args.q)]

    _sort_sessions(sessions, getattr(args, "sort", "updated"))
    if args.limit and args.limit > 0:
        sessions = sessions[: args.limit]

    if args.json:
        payload = [
            {
                "source": s.source,
                "id": s.session_id,
                "updated_at": format_iso_utc(s.updated_at),
                "updated_local": format_local_time(s.updated_at),
                "title": s.title,
                "preview": s.preview,
                "cwd": s.cwd,
                "count": s.step_or_msg_count,
                "size_bytes": s.size_bytes,
                "size_human": format_bytes(s.size_bytes),
                "path": str(s.location),
                "extra": s.extra,
                "resume_cmd": PROVIDERS[s.source].resume_command(s.session_id),
            }
            for s in sessions
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not sessions:
        print("No conversations found matching filters.")
        return 0

    print(
        f"{'SRC':<6} {'ID':<36} {'UPDATED':<19} {'CNT':>4} {'SIZE':>9}  {'CWD':<30} TITLE / PREVIEW"
    )
    print("-" * 132)
    for s in sessions:
        cwd_disp = s.cwd or "(none)"
        if len(cwd_disp) > 30:
            cwd_disp = "..." + cwd_disp[-27:]
        title_disp = truncate_preview(s.title or s.preview, 42)
        extra_tag = f" [{s.extra}]" if s.extra else ""
        print(
            f"{s.source:<6} {s.session_id:<36} {format_local_time(s.updated_at):<19} "
            f"{s.step_or_msg_count:>4} {format_bytes(s.size_bytes):>9}  {cwd_disp:<30} {title_disp}{extra_tag}"
        )
    return 0


def _print_copy_result(res: dict[str, Any]) -> None:
    print(f"Converted {res['source']} -> {res['toProvider']}")
    print(f"  Source ID:    {res['sourceId']}")
    regen_note = " (fresh UUID generated on collision)" if res["regenerated"] else ""
    print(f"  Converted ID: {res['targetId']}{regen_note}")
    print(f"  Target CWD:   {res['targetCwd']}")
    print(f"  Output Path:  {res['path']}")
    cur_cwd = normalize_windows_path(os.getcwd())
    if res["toProvider"] == "claude" and res["targetCwd"] and not paths_match(cur_cwd, res["targetCwd"]):
        print(f"  Resume dir:   cd \"{res['targetCwd']}\"")
    print(f"  Resume cmd:   {res['resumeCmd']}")


def cmd_copy(args: argparse.Namespace) -> int:
    res = copy_one_session(
        id_or_latest=args.id_or_latest,
        to_provider_id=args.to,
        from_provider_id=args.from_provider,
        cwd=args.cwd,
        target_cwd=args.target_cwd,
        all_cwd=args.all_cwd,
        new_id=args.new_id,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
        kind="manual",
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    _print_copy_result(res)
    return 0


def cmd_agy2claude(args: argparse.Namespace) -> int:
    res = copy_one_session(
        id_or_latest=args.conv_id_or_latest,
        from_provider_id="agy",
        to_provider_id="claude",
        cwd=args.cwd,
        target_cwd=args.target_cwd,
        all_cwd=args.all_cwd,
        new_id=args.new_id,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
        kind="manual",
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    _print_copy_result(res)
    return 0


def cmd_claude2agy(args: argparse.Namespace) -> int:
    res = copy_one_session(
        id_or_latest=args.session_id_or_latest,
        from_provider_id="claude",
        to_provider_id="agy",
        cwd=args.cwd,
        target_cwd=args.target_cwd,
        all_cwd=args.all_cwd,
        new_id=args.new_id,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
        kind="manual",
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    _print_copy_result(res)
    return 0


def cmd_sync_dir(args: argparse.Namespace) -> int:
    cwd_val = args.cwd or os.getcwd()
    results = sync_directory_sessions(
        from_provider_id=args.from_provider,
        to_provider_id=args.to,
        cwd=cwd_val,
        target_cwd=args.target_cwd,
        overwrite=args.overwrite,
        include_thinking=args.include_thinking,
    )
    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        return 0 if all(r["status"] != "error" for r in results) else 1

    if not results:
        print(
            f"No top-level {args.from_provider} sessions found in workspace '{normalize_windows_path(cwd_val)}'."
        )
        return 0

    copied = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors = sum(1 for r in results if r["status"] == "error")
    print(
        f"Sync dir {args.from_provider} -> {args.to}: {copied} copied, {skipped} skipped, {errors} error(s)"
    )
    for r in results:
        tag = r["status"].upper()
        tid = r["targetId"] or "-"
        print(f"  [{tag:<7}] {r['sourceId']} -> {tid}  {truncate_preview(r['title'], 50)}")
        if r["error"]:
            print(f"            error: {r['error']}")
    return 0 if errors == 0 else 1


def cmd_inspect(args: argparse.Namespace) -> int:
    detail = get_session_detail(args.source, args.id)
    if args.json:
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 0

    print(f"Source:     {detail['source']}")
    print(f"ID:         {detail['id']}")
    print(f"Title:      {detail['title']}")
    print(f"Workspace:  {detail['cwd'] or '(none)'}")
    print(f"Updated:    {detail['updatedLocal']}")
    print(f"Path:       {detail['path']}")
    print(f"Resume cmd: {detail['resumeCmd']}")
    if detail["syncedTo"]:
        links = ", ".join(f"{x['provider']}:{x['id']}" for x in detail["syncedTo"])
        print(f"Synced to:  {links}")
    print("-" * 80)
    for t in detail["turns"]:
        role = t["role"].upper()
        print(f"[{t['index']}] {role} ({t['timestampLocal']})")
        if t["thinking"]:
            print(f"  <thought> {truncate_preview(t['thinking'], 200)}")
        if t["text"]:
            print(f"  {t['text']}")
        for tc in t["toolCalls"]:
            print(f"  -> tool_call {tc['name']} ({tc['id']}): {truncate_preview(tc['arguments'], 140)}")
        for tr in t["toolResults"]:
            err = " ERROR" if tr["isError"] else ""
            print(
                f"  <- tool_result{err} {tr['name']} ({tr['toolUseId']}): {truncate_preview(tr['content'], 160)}"
            )
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    prov = get_provider(args.source, require_supported=True)
    raw_ids = [s.strip() for s in args.ids if s.strip()]
    if not raw_ids or any(s.lower() == "latest" for s in raw_ids):
        raise SyncError("Explicit session ID(s) are required for delete.")

    resolved_items: list[dict[str, str]] = []
    for sid in raw_ids:
        loc, summary = prov.find_session(sid)
        resolved_id = summary.session_id if summary else loc.stem
        resolved_items.append({"source": prov.id, "id": resolved_id})

    also_synced = bool(getattr(args, "also_synced", False))
    if not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Non-interactive delete requires --yes.")
        id_list = ", ".join(x["id"] for x in resolved_items)
        peer_note = " (and linked synced peer sessions)" if also_synced else ""
        ans = input(
            f"Delete {len(resolved_items)} {prov.id} session(s) [{id_list}]{peer_note}? [y/N] "
        ).strip()
        if ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    res = delete_sessions_batch(resolved_items, delete_synced_peers=also_synced)
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["message"])
    return 0


def cmd_trim(args: argparse.Namespace) -> int:
    prov = get_provider(args.source, require_supported=True)
    sid = args.id.strip()
    if not sid or sid.lower() == "latest":
        raise SyncError("An explicit session ID is required for trim.")

    loc, summary = prov.find_session(sid)
    resolved_id = summary.session_id if summary else loc.stem

    keep_last_turns: int | None = args.keep_turns
    keep_ratio: float | None = args.keep_ratio
    if args.keep_all_turns:
        keep_last_turns = None
        keep_ratio = 1.0
    elif keep_last_turns is None and keep_ratio is None:
        keep_ratio = 0.5

    if not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Non-interactive trim requires --yes.")
        ans = input(
            f"Lighten/trim {prov.id} session {resolved_id} in-place? [y/N] "
        ).strip()
        if ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    res = trim_session(
        source=prov.id,
        session_id=resolved_id,
        keep_ratio=keep_ratio,
        keep_last_turns=keep_last_turns,
        max_tool_output_chars=args.max_tool_chars,
        strip_thinking=not args.keep_thinking,
        purge_sidecars=not args.keep_sidecars,
    )
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["message"])
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    cwd_val = normalize_windows_path(args.cwd) if args.cwd else None
    clean_orphans = not args.no_orphans
    vacuum_db = not args.no_vacuum

    if not args.dry_run and not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Non-interactive clean requires --yes (or use --dry-run).")
        preview = clean_storage(
            source=args.source,
            cwd=cwd_val,
            clean_orphans=clean_orphans,
            delete_empty=args.empty,
            delete_subagents=args.subagents,
            older_than_days=args.older_than,
            larger_than_mb=args.larger_than,
            vacuum_db=vacuum_db,
            dry_run=True,
        )
        print(preview["message"])
        ans = input("Proceed with storage cleanup? [y/N] ").strip()
        if ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    res = clean_storage(
        source=args.source,
        cwd=cwd_val,
        clean_orphans=clean_orphans,
        delete_empty=args.empty,
        delete_subagents=args.subagents,
        older_than_days=args.older_than,
        larger_than_mb=args.larger_than,
        vacuum_db=vacuum_db,
        dry_run=bool(args.dry_run),
    )
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["message"])
        for item in res.get("previewItems", [])[:25]:
            print(f"  - [{item.get('kind', '')}] {item.get('label', '')} ({item.get('sizeHuman', '')})")
    return 0


def cmd_autosync(args: argparse.Namespace) -> int:
    action = args.action
    inc_think = (
        _parse_bool_str(args.include_thinking)
        if args.include_thinking is not None
        else None
    )

    enabled_override: bool | None = None
    if action == "on":
        enabled_override = True
    elif action == "off":
        enabled_override = False

    if any(
        x is not None
        for x in (
            enabled_override,
            args.interval,
            args.direction,
            args.scope,
            args.watch,
            args.unwatch,
            inc_think,
        )
    ):
        update_autosync_config(
            enabled=enabled_override,
            interval_sec=args.interval,
            direction=args.direction,
            scope=args.scope,
            add_watch=args.watch,
            remove_watch=args.unwatch,
            include_thinking=inc_think,
        )

    run_res: dict[str, Any] | None = None
    if action == "run":
        run_res = run_autosync_cycle(force_even_if_disabled=True)

    cfg = get_autosync_dict()
    if args.json:
        out: dict[str, Any] = {"autosync": cfg}
        if run_res is not None:
            out["result"] = run_res
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if run_res is not None:
        print(run_res["message"])
    print(f"Auto-sync:  {'ON' if cfg['enabled'] else 'OFF'}")
    print(f"Direction:  {cfg['direction']}")
    print(f"Interval:   {cfg['intervalSec']}s")
    print(f"Scope:      {cfg['scope']}")
    print(f"Thinking:   {'on' if cfg['includeThinking'] else 'off'}")
    print(
        "Watched:    "
        + (", ".join(cfg["watchedFolders"]) if cfg["watchedFolders"] else "(none)")
    )
    print(f"Pairs:      {cfg['pairCount']}")
    if cfg["lastRunLocal"]:
        print(f"Last run:   {cfg['lastRunLocal']}")
    if cfg["lastError"]:
        print(f"Last error: {cfg['lastError']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    port = args.port if args.port is not None else get_default_port()
    ui_html = (
        Path(args.ui_html)
        if args.ui_html
        else (Path(__file__).resolve().parent / "ui.html")
    )
    return serve_http(port=port, ui_html_path=ui_html)


def cmd_version(_args: argparse.Namespace) -> int:
    print(VERSION)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcas.py",
        description="Manage Coding Agent Session (mcas) - browse, convert, and auto-sync agy & claude sessions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List sessions across coding agent providers")
    p_list.add_argument("--cwd", help="Filter conversations to a workspace folder")
    p_list.add_argument("--source", default="all", help="Provider filter: all|agy|claude")
    p_list.add_argument("--q", help="Search text in ID, title, preview, or workspace")
    p_list.add_argument("--all", action="store_true", help="Include subagents, IDE, and 0-turn sessions")
    p_list.add_argument(
        "--sort",
        default="updated",
        choices=["updated", "oldest", "size", "size_asc", "turns"],
        help="Sort order (default updated)",
    )
    p_list.add_argument("--limit", type=int, default=30, help="Max rows (0 = unlimited, default 30)")
    p_list.add_argument("--json", action="store_true", help="Output JSON")
    p_list.set_defaults(func=cmd_list)

    p_copy = sub.add_parser("copy", help="Copy/convert a session to another provider")
    p_copy.add_argument("id_or_latest", help="Session UUID (or prefix) or 'latest'")
    p_copy.add_argument("--to", required=True, help="Target provider (agy|claude)")
    p_copy.add_argument("--from", dest="from_provider", help="Source provider (agy|claude)")
    p_copy.add_argument("--cwd", help="Workspace path (filters 'latest' and fallback target cwd)")
    p_copy.add_argument("--target-cwd", help="Override destination workspace folder")
    p_copy.add_argument("--all-cwd", action="store_true", help="Allow 'latest' across all workspaces")
    p_copy.add_argument("--new-id", action="store_true", help="Generate a fresh UUIDv4 for destination")
    p_copy.add_argument("--overwrite", action="store_true", help="Overwrite destination if UUID exists")
    p_copy.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_copy.add_argument("--json", action="store_true", help="Output JSON")
    p_copy.set_defaults(func=cmd_copy)

    p_a2c = sub.add_parser("agy2claude", help="Convert an agy session to Claude Code")
    p_a2c.add_argument("conv_id_or_latest", help="Antigravity UUID (or prefix) or 'latest'")
    p_a2c.add_argument("--cwd", help="Workspace path (filters 'latest' and fallback target cwd)")
    p_a2c.add_argument("--target-cwd", help="Override destination workspace folder")
    p_a2c.add_argument("--all-cwd", action="store_true", help="Allow 'latest' across all workspaces")
    p_a2c.add_argument("--new-id", action="store_true", help="Generate a fresh UUIDv4")
    p_a2c.add_argument("--overwrite", action="store_true", help="Overwrite if target exists")
    p_a2c.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_a2c.add_argument("--json", action="store_true", help="Output JSON")
    p_a2c.set_defaults(func=cmd_agy2claude)

    p_c2a = sub.add_parser("claude2agy", help="Convert a Claude Code session to Antigravity")
    p_c2a.add_argument("session_id_or_latest", help="Claude UUID (or prefix) or 'latest'")
    p_c2a.add_argument("--cwd", help="Workspace path (filters 'latest' and fallback target cwd)")
    p_c2a.add_argument("--target-cwd", help="Override destination workspace folder")
    p_c2a.add_argument("--all-cwd", action="store_true", help="Allow 'latest' across all workspaces")
    p_c2a.add_argument("--new-id", action="store_true", help="Generate a fresh UUIDv4")
    p_c2a.add_argument("--overwrite", action="store_true", help="Overwrite if target exists")
    p_c2a.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_c2a.add_argument("--json", action="store_true", help="Output JSON")
    p_c2a.set_defaults(func=cmd_claude2agy)

    p_sdir = sub.add_parser("sync-dir", help="Copy all top-level sessions for a workspace between providers")
    p_sdir.add_argument("--from", dest="from_provider", required=True, help="Source provider (agy|claude)")
    p_sdir.add_argument("--to", required=True, help="Target provider (agy|claude)")
    p_sdir.add_argument("--cwd", help="Source workspace folder (defaults to current working directory)")
    p_sdir.add_argument("--target-cwd", help="Destination workspace folder (defaults to --cwd)")
    p_sdir.add_argument("--overwrite", action="store_true", help="Overwrite existing target sessions")
    p_sdir.add_argument("--include-thinking", action="store_true", help="Fold thinking into <thought> blocks")
    p_sdir.add_argument("--json", action="store_true", help="Output JSON")
    p_sdir.set_defaults(func=cmd_sync_dir)

    p_insp = sub.add_parser("inspect", help="Inspect full turns and metadata of a session")
    p_insp.add_argument("id", help="Session UUID or unique prefix")
    p_insp.add_argument("--source", help="Provider (agy|claude)")
    p_insp.add_argument("--json", action="store_true", help="Output JSON")
    p_insp.set_defaults(func=cmd_inspect)

    p_del = sub.add_parser("delete", help="Delete one or more sessions from a provider")
    p_del.add_argument("ids", nargs="+", help="One or more session UUIDs or unique prefixes")
    p_del.add_argument("--source", required=True, help="Provider (agy|claude)")
    p_del.add_argument(
        "--also-synced",
        action="store_true",
        help="Also delete linked synced peer session(s)",
    )
    p_del.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_del.add_argument("--json", action="store_true", help="Output JSON")
    p_del.set_defaults(func=cmd_delete)

    p_trim = sub.add_parser(
        "trim",
        aliases=["lighten"],
        help="Lighten a conversation in-place (trim old turns, strip thinking, purge sidecars)",
    )
    p_trim.add_argument("id", help="Session UUID or unique prefix")
    p_trim.add_argument("--source", required=True, help="Provider (agy|claude)")
    p_trim.add_argument("--keep-ratio", type=float, default=None, help="Fraction of newest turns to keep (e.g. 0.5)")
    p_trim.add_argument("--keep-turns", type=int, default=None, help="Keep only the newest N turns")
    p_trim.add_argument("--keep-all-turns", action="store_true", help="Keep all turns; only trim bulky tool outputs, thinking, and sidecars")
    p_trim.add_argument("--max-tool-chars", type=int, default=2000, help="Truncate tool outputs above N chars (0 = disable, default 2000)")
    p_trim.add_argument("--keep-thinking", action="store_true", help="Preserve thinking blocks instead of stripping them")
    p_trim.add_argument("--keep-sidecars", action="store_true", help="Keep brain/subagent/file-history sidecars")
    p_trim.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_trim.add_argument("--json", action="store_true", help="Output JSON")
    p_trim.set_defaults(func=cmd_trim)

    p_clean = sub.add_parser(
        "clean",
        help="Bulk clean orphans, empty/subagent/old/large sessions, and vacuum SQLite DB",
    )
    p_clean.add_argument("--source", default="all", help="Provider filter: all|agy|claude")
    p_clean.add_argument("--cwd", help="Limit session cleanup to a workspace folder")
    p_clean.add_argument("--no-orphans", action="store_true", help="Do not clean orphaned sidecar directories")
    p_clean.add_argument("--empty", action="store_true", help="Delete 0-turn empty sessions")
    p_clean.add_argument("--subagents", action="store_true", help="Delete subagent sessions and Claude subagents/ folders")
    p_clean.add_argument("--older-than", type=int, default=None, help="Delete sessions older than N days")
    p_clean.add_argument("--larger-than", type=float, default=None, help="Delete sessions larger than N MB")
    p_clean.add_argument("--no-vacuum", action="store_true", help="Skip SQLite VACUUM on conversation_summaries.db")
    p_clean.add_argument("--dry-run", action="store_true", help="Preview cleanup impact without deleting")
    p_clean.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_clean.add_argument("--json", action="store_true", help="Output JSON")
    p_clean.set_defaults(func=cmd_clean)

    p_auto = sub.add_parser("autosync", help="Configure or trigger loop-free auto-sync")
    p_auto.add_argument("action", choices=["status", "on", "off", "run"], help="Auto-sync action")
    p_auto.add_argument("--direction", help="both|agy2claude|claude2agy")
    p_auto.add_argument("--scope", help="all|watched")
    p_auto.add_argument("--interval", type=int, help="Interval in seconds (min 5)")
    p_auto.add_argument("--watch", action="append", help="Add workspace folder to watch list")
    p_auto.add_argument("--unwatch", action="append", help="Remove workspace folder from watch list")
    p_auto.add_argument(
        "--include-thinking",
        nargs="?",
        const="true",
        default=None,
        help="true|false",
    )
    p_auto.add_argument("--json", action="store_true", help="Output JSON")
    p_auto.set_defaults(func=cmd_autosync)

    p_srv = sub.add_parser("__serve", help="Run the local HTTP API server + auto-sync daemon")
    p_srv.add_argument("--port", type=int, default=None, help=f"Port (default {DEFAULT_PORT})")
    p_srv.add_argument("--ui-html", help="Path to ui.html")
    p_srv.set_defaults(func=cmd_serve)

    p_ver = sub.add_parser("version", help="Print mcas version")
    p_ver.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SyncError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
