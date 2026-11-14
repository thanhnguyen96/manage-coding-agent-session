from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import sqlite3
import time
import uuid

def _connect_sqlite(path: Path, timeout: float = 15.0) -> sqlite3.Connection:
    """Open SQLite DB with busy timeout for WAL safety."""
    return sqlite3.connect(str(path), timeout=timeout)

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
