from __future__ import annotations

from pathlib import Path
import os

from mcas.constants import DEFAULT_PORT
from mcas.errors import SyncError

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
