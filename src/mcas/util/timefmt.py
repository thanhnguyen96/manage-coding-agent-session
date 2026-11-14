from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import re

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
