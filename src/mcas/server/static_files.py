from __future__ import annotations

import mimetypes
import urllib.parse
from http import HTTPStatus
from pathlib import Path

_MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
}


def get_static_dir() -> Path:
    """Return the package directory containing static web UI assets."""
    return Path(__file__).resolve().parent / "static"


def get_default_ui_html_path() -> Path:
    """Return the default `index.html` path under the static directory."""
    return get_static_dir() / "index.html"


def get_mime_type(path: Path) -> str:
    """Return an explicit UTF-8/binary MIME type for a static asset path."""
    ext = path.suffix.lower()
    if ext in _MIME_TYPES:
        return _MIME_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def render_index_html(ui_html_path: Path, token: str) -> tuple[int, str, bytes]:
    """Read `index.html` and inject the per-process API token."""
    if not ui_html_path.is_file():
        return (
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "application/json; charset=utf-8",
            f'{{"error": "ui.html not found: {ui_html_path.as_posix()}"}}'.encode("utf-8"),
        )
    html_text = ui_html_path.read_text(encoding="utf-8")
    rendered = html_text.replace("__MCAS_TOKEN__", token)
    return HTTPStatus.OK, "text/html; charset=utf-8", rendered.encode("utf-8")


def resolve_static_asset(static_dir: Path, raw_relpath: str) -> Path | None:
    """Safely resolve a `/static/<relpath>` request inside `static_dir`.

    Returns None on missing files or any path-traversal attempt.
    """
    if not static_dir.is_dir():
        return None
    decoded = urllib.parse.unquote(raw_relpath).lstrip("/")
    if not decoded or "\\" in decoded or "\x00" in decoded:
        return None
    parts = [p for p in decoded.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None

    try:
        base = static_dir.resolve(strict=True)
        candidate = (base / Path(*parts)).resolve(strict=True)
    except OSError:
        return None

    if not candidate.is_relative_to(base) or not candidate.is_file():
        return None
    return candidate
