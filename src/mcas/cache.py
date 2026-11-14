from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import threading
import time

from mcas.models import SessionSummary

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
