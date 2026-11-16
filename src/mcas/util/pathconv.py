from __future__ import annotations

from pathlib import Path
import json
import os
import re
import urllib.parse

def normalize_windows_path(path: str) -> str:
    """Normalize a local path (preserving drive-root trailing slash and drive casing)."""
    raw = path.strip()
    if raw[:8].upper() == "\\\\?\\UNC\\":
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
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
