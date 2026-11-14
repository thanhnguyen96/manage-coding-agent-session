"""Windows Startup shortcut management for mcas."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mcas.config import get_default_port, get_startup_lnk_path
from mcas.errors import SyncError


def get_start_menu_lnk_path() -> Path:
    """Return the Windows Start Menu shortcut path for mcas."""
    return get_startup_lnk_path().parent.parent / "mcas.lnk"


def resolve_python_exe(prefer_windowless: bool = True) -> str:
    """Return pythonw.exe beside sys.executable on Windows when available."""
    if prefer_windowless and sys.platform == "win32":
        exe = Path(sys.executable)
        if exe.name.lower() == "python.exe":
            pyw = exe.with_name("pythonw.exe")
            if pyw.is_file():
                return str(pyw)
    return sys.executable


def _validate_port(port: int | None) -> int:
    if port is None or port == 0:
        return get_default_port()
    try:
        n = int(port)
    except (TypeError, ValueError) as exc:
        raise SyncError(f"Invalid port '{port}' (use 1024-65535).") from exc
    if n < 1024 or n > 65535:
        raise SyncError(f"Invalid port '{n}' (use 1024-65535).")
    return n


def test_autostart() -> bool:
    """Return True if the Windows Startup shortcut exists."""
    return get_startup_lnk_path().is_file()


def set_autostart(on: bool, port: int = 0) -> str:
    """Create or remove the Windows Startup shortcut for the mcas tray."""
    lnk = get_startup_lnk_path()
    if not on:
        lnk.unlink(missing_ok=True)
        return "mcas will not start with Windows."

    resolved_port = _validate_port(port)
    if sys.platform != "win32":
        raise SyncError("Windows Startup shortcut is only supported on Windows.")

    tray_ps1 = Path(__file__).resolve().parent / "tray.ps1"
    if not tray_ps1.is_file():
        raise SyncError(f"tray.ps1 not found: {tray_ps1}")

    py_exe = resolve_python_exe(prefer_windowless=True)
    lnk.parent.mkdir(parents=True, exist_ok=True)

    args = (
        f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
        f'-File "{tray_ps1}" -Python "{py_exe}" -Port {resolved_port}'
    )
    ps_script = (
        "$ErrorActionPreference = 'Stop'; "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MCAS_LNK_PATH); "
        "$s.TargetPath = $env:MCAS_LNK_TARGET; "
        "$s.Arguments = $env:MCAS_LNK_ARGS; "
        "$s.Description = $env:MCAS_LNK_DESC; "
        "$s.WindowStyle = 7; "
        "$s.Save()"
    )
    env = os.environ.copy()
    env["MCAS_LNK_PATH"] = str(lnk)
    env["MCAS_LNK_TARGET"] = "powershell.exe"
    env["MCAS_LNK_ARGS"] = args
    env["MCAS_LNK_DESC"] = "mcas tray icon and Auto-Sync"

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(f"Failed to toggle autostart: {exc}") from exc

    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "").strip() or f"exit code {res.returncode}"
        raise SyncError(f"Autostart command failed: {msg}")

    return "mcas will start in the tray when you sign in to Windows."
