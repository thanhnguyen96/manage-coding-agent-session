"""Desktop UI launcher, stop, update, and uninstall operations for mcas."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from mcas.config import (
    get_default_port,
    get_mcas_root,
    get_startup_lnk_path,
    get_sync_state_path,
    get_ui_json_path,
)
from mcas.constants import VERSION
from mcas.errors import SyncError

from .autostart import get_start_menu_lnk_path, resolve_python_exe

_REPO_GIT_BASE = "git+https://github.com/thanhnguyen96/manage-coding-agent-session"
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def _ui_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _read_ui_state() -> dict[str, Any] | None:
    ui_path = get_ui_json_path()
    if not ui_path.is_file():
        return None
    try:
        data = json.loads(ui_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _is_port_free_fast(port: int) -> bool:
    """Fast local TCP bind check avoiding Windows 2s connect-refused delay."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _get_port_owner(port: int) -> str:
    """Return 'mcas', 'other', or 'free' for 127.0.0.1:<port>."""
    if _is_port_free_fast(port):
        return "free"
    req = urllib.request.Request(f"{_ui_url(port)}/health", method="GET")
    try:
        with _NO_PROXY_OPENER.open(req, timeout=2.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("app") == "mcas":
                return "mcas"
            return "other"
    except urllib.error.HTTPError:
        return "other"
    except (urllib.error.URLError, OSError, ValueError):
        return "other"


def _post_api_quit(port: int, token: str, timeout: float = 3.0) -> None:
    req = urllib.request.Request(
        f"{_ui_url(port)}/api/quit",
        data=b"{}",
        method="POST",
        headers={
            "X-Mcas-Token": token,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        resp.read()


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        process_query_limited = 0x1000
        still_active = 259
        handle = kernel32.OpenProcess(process_query_limited, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_uint(0)
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        process_terminate = 0x0001
        handle = kernel32.OpenProcess(process_terminate, False, int(pid))
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _open_browser(url: str) -> None:
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]
    else:
        webbrowser.open(url)


def open_ui(port: int | None = None, no_browser: bool = False) -> str:
    """Start the mcas UI server (and Windows tray) if not already running, optionally open browser."""
    resolved_port = _validate_port(port)
    url = _ui_url(resolved_port)
    owner = _get_port_owner(resolved_port)
    if owner == "other":
        raise SyncError(
            f"Port {resolved_port} is used by another program. "
            "Use another one: mcas ui --port <n> (or set MCAS_PORT)"
        )

    if owner == "free":
        ui_json_path = get_ui_json_path()
        old_state = _read_ui_state()
        if old_state and int(old_state.get("port") or 0) == resolved_port:
            ui_json_path.unlink(missing_ok=True)

        if sys.platform == "win32":
            tray_ps1 = Path(__file__).resolve().parent / "tray.ps1"
            if not tray_ps1.is_file():
                raise SyncError(f"Missing tray script: {tray_ps1}")
            py_exe = resolve_python_exe(prefer_windowless=True)
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(tray_ps1),
                "-Python",
                py_exe,
                "-Port",
                str(resolved_port),
            ]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
        else:
            cmd = [sys.executable, "-B", "-m", "mcas", "__serve", "--port", str(resolved_port)]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )

        deadline = time.monotonic() + 15.0
        ready = False
        while time.monotonic() < deadline:
            st = _read_ui_state()
            if (
                st
                and int(st.get("port") or 0) == resolved_port
                and st.get("token")
                and _get_port_owner(resolved_port) == "mcas"
            ):
                ready = True
                break
            if proc.poll() is not None:
                st2 = _read_ui_state()
                if (
                    st2
                    and int(st2.get("port") or 0) == resolved_port
                    and st2.get("token")
                    and _get_port_owner(resolved_port) == "mcas"
                ):
                    ready = True
                break
            time.sleep(0.15)

        if not ready:
            raise SyncError(f"The mcas UI server did not start on port {resolved_port}.")

    if not no_browser:
        _open_browser(url)

    return f"mcas web UI: {url}  (tray icon: right-click > Quit, or mcas ui stop)"


def stop_ui() -> str:
    """Stop the running mcas web UI server and tray icon."""
    ui_json_path = get_ui_json_path()
    s = _read_ui_state()
    if not s:
        return "The web UI is not running."

    try:
        port = int(s.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    try:
        server_pid = int(s.get("pid") or 0)
    except (TypeError, ValueError):
        server_pid = 0
    token = str(s.get("token") or "")

    if port > 0 and token:
        try:
            _post_api_quit(port, token, timeout=3.0)
        except (urllib.error.URLError, OSError, ValueError):
            if server_pid > 0:
                _kill_pid(server_pid)
    elif server_pid > 0:
        _kill_pid(server_pid)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        pid_alive = server_pid > 0 and _is_pid_alive(server_pid)
        port_busy = port > 0 and _get_port_owner(port) != "free"
        if not pid_alive and not port_busy:
            break
        time.sleep(0.15)

    if server_pid > 0 and _is_pid_alive(server_pid):
        _kill_pid(server_pid)

    ui_json_path.unlink(missing_ok=True)
    return "Web UI stopped."


def _get_uv_tool_bin_dir(uv_exe: str) -> Path | None:
    override = os.environ.get("UV_TOOL_BIN_DIR", "").strip()
    if override:
        return Path(override)
    try:
        res = subprocess.run(
            [uv_exe, "tool", "dir", "--bin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            return Path(res.stdout.strip().splitlines()[-1].strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _rename_locked_exes_for_update(uv_exe: str) -> list[tuple[Path, Path]]:
    """Rename running mcas.exe shims on Windows so uv can write new entrypoints."""
    if sys.platform != "win32":
        return []
    dirs: list[Path] = []
    bin_dir = _get_uv_tool_bin_dir(uv_exe)
    if bin_dir:
        dirs.append(bin_dir)
    exe_dir = Path(sys.executable).resolve().parent
    if exe_dir not in dirs:
        dirs.append(exe_dir)

    renamed: list[tuple[Path, Path]] = []
    for folder in dirs:
        exe_path = folder / "mcas.exe"
        if exe_path.is_file():
            old_path = folder / "mcas.exe.old"
            try:
                old_path.unlink(missing_ok=True)
                exe_path.rename(old_path)
                renamed.append((exe_path, old_path))
            except OSError:
                pass
    return renamed


def _cleanup_old_exes_detached(old_paths: list[Path]) -> None:
    if sys.platform != "win32" or not old_paths:
        return
    cmds = [f'del /f /q "{p}" 2>nul' for p in old_paths]
    joined = " & ".join(cmds)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    try:
        subprocess.Popen(
            f"cmd.exe /c ping -n 2 127.0.0.1 >nul & {joined}",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except OSError:
        pass


def update() -> str:
    """Update mcas via uv (reinstalling from MCAS_SOURCE / MCAS_REF or upgrading)."""
    uv_exe = shutil.which("uv")
    if not uv_exe:
        raise SyncError(
            "uv is required for 'mcas update'. "
            "Install uv (winget install astral-sh.uv) and try again."
        )

    ui_state = _read_ui_state()
    ui_was_running = False
    old_port = 0
    if ui_state:
        try:
            old_port = int(ui_state.get("port") or 0)
        except (TypeError, ValueError):
            old_port = 0
        try:
            pid = int(ui_state.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if (pid > 0 and _is_pid_alive(pid)) or (
            old_port > 0 and _get_port_owner(old_port) == "mcas"
        ):
            ui_was_running = True

    if ui_was_running:
        stop_ui()

    mcas_source = os.environ.get("MCAS_SOURCE", "").strip()
    mcas_ref = os.environ.get("MCAS_REF", "").strip()
    if mcas_source:
        cmd = [uv_exe, "tool", "install", "--force", "--reinstall", mcas_source]
    elif mcas_ref:
        cmd = [uv_exe, "tool", "install", "--force", "--reinstall", f"{_REPO_GIT_BASE}@{mcas_ref}"]
    else:
        cmd = [uv_exe, "tool", "upgrade", "mcas"]

    renamed = _rename_locked_exes_for_update(uv_exe)
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        for orig_p, old_p in renamed:
            if not orig_p.exists() and old_p.exists():
                try:
                    old_p.rename(orig_p)
                except OSError:
                    pass
        raise SyncError(f"Update failed: {exc}") from exc

    if res.returncode != 0:
        for orig_p, old_p in renamed:
            if not orig_p.exists() and old_p.exists():
                try:
                    old_p.rename(orig_p)
                except OSError:
                    pass
        msg = (res.stderr or res.stdout or "").strip() or f"exit code {res.returncode}"
        raise SyncError(f"Update failed: {msg}")

    try:
        installed_ver = VERSION
        try:
            ver_res = subprocess.run(
                [sys.executable, "-B", "-m", "mcas", "version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if ver_res.returncode == 0 and ver_res.stdout.strip():
                installed_ver = ver_res.stdout.strip().splitlines()[-1].strip()
        except (OSError, subprocess.SubprocessError):
            pass

        if ui_was_running:
            restart_port = old_port if old_port > 0 else get_default_port()
            open_ui(restart_port, no_browser=True)

        return f"mcas {installed_ver} installed."
    finally:
        _cleanup_old_exes_detached([old_p for _, old_p in renamed])


def _remove_legacy_bin_from_user_path(bin_dir: Path) -> None:
    """Remove legacy %LOCALAPPDATA%\\mcas\\bin from Windows User PATH if present."""
    if sys.platform != "win32":
        return
    norm_target = str(bin_dir).rstrip("\\/").lower()
    ps_script = (
        "$t = $env:MCAS_LEGACY_BIN; "
        "$p = [Environment]::GetEnvironmentVariable('Path', 'User'); "
        "if (-not $p) { exit 0 }; "
        "$parts = @($p -split ';' | Where-Object { $_ }); "
        "$kept = @($parts | Where-Object { $_.TrimEnd('\\', '/').ToLowerInvariant() -ne $t }); "
        "if ($kept.Count -ne $parts.Count) { "
        "[Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User') }"
    )
    env = os.environ.copy()
    env["MCAS_LEGACY_BIN"] = norm_target
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _spawn_uv_tool_uninstall(uv_exe: str) -> None:
    """Run 'uv tool uninstall mcas' detached after this process exits."""
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            f'cmd.exe /c ping -n 2 127.0.0.1 >nul & "{uv_exe}" tool uninstall mcas >nul 2>&1',
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    else:
        subprocess.Popen(
            ["sh", "-c", 'sleep 1; "$1" tool uninstall mcas >/dev/null 2>&1', "_", uv_exe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )


def uninstall(purge: bool = False) -> str:
    """Stop UI, remove shortcuts and data dir (keeping sync_state.json unless purge), and uninstall uv tool."""
    stop_ui()

    get_startup_lnk_path().unlink(missing_ok=True)
    get_start_menu_lnk_path().unlink(missing_ok=True)

    root = get_mcas_root()
    _remove_legacy_bin_from_user_path(root / "bin")

    sync_state = get_sync_state_path()
    kept_msg = ""
    if root.exists():
        if purge or not sync_state.is_file():
            shutil.rmtree(root, ignore_errors=True)
        else:
            kept_msg = (
                f"Kept auto-sync settings/state in {sync_state}. "
                "Re-run 'mcas uninstall --purge' to delete everything.\n"
            )
            for child in list(root.iterdir()):
                if child == sync_state:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)

    uv_exe = shutil.which("uv")
    if uv_exe:
        _spawn_uv_tool_uninstall(uv_exe)

    return f"{kept_msg}mcas uninstalled."
