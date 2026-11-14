"""Pytest fixtures providing isolated sandbox directories and seed helpers."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fixtures.seed import seed_standard_trio


@dataclass
class SandboxEnv:
    root: Path
    mcas_root: Path
    agy_dir: Path
    agy_ide_dir: Path
    claude_dir: Path
    localappdata: Path
    appdata: Path
    userprofile: Path
    ws_a: Path
    ws_b: Path
    ws_c: Path
    env: dict[str, str]


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _clear_mcas_caches() -> None:
    try:
        from mcas import cache as mcas_cache

        fn = getattr(mcas_cache, "_invalidate_size_cache", None)
        if callable(fn):
            fn()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SandboxEnv:
    """Redirect all mcas/agy/claude/Windows user directories to a temp sandbox."""
    localappdata = tmp_path / "Local"
    appdata = tmp_path / "Roaming"
    userprofile = tmp_path / "home"
    mcas_root = localappdata / "mcas"
    agy_dir = tmp_path / "agy-cli"
    agy_ide_dir = tmp_path / "agy-ide"
    claude_dir = tmp_path / "claude"
    ws_a = tmp_path / "work" / "projA"
    ws_b = tmp_path / "work" / "projB"
    ws_c = tmp_path / "work" / "projAuto"

    for d in (
        localappdata,
        appdata,
        userprofile,
        mcas_root,
        agy_dir,
        agy_ide_dir,
        claude_dir,
        ws_a,
        ws_b,
        ws_c,
    ):
        d.mkdir(parents=True, exist_ok=True)

    port = str(find_free_port())
    overrides = {
        "LOCALAPPDATA": str(localappdata),
        "APPDATA": str(appdata),
        "USERPROFILE": str(userprofile),
        "MCAS_ROOT": str(mcas_root),
        "MCAS_AGY_DIR": str(agy_dir),
        "MCAS_AGY_IDE_DIR": str(agy_ide_dir),
        "MCAS_CLAUDE_DIR": str(claude_dir),
        "MCAS_PORT": port,
    }
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)

    full_env = os.environ.copy()
    full_env.update(overrides)
    _clear_mcas_caches()
    yield SandboxEnv(
        root=tmp_path,
        mcas_root=mcas_root,
        agy_dir=agy_dir,
        agy_ide_dir=agy_ide_dir,
        claude_dir=claude_dir,
        localappdata=localappdata,
        appdata=appdata,
        userprofile=userprofile,
        ws_a=ws_a,
        ws_b=ws_b,
        ws_c=ws_c,
        env=full_env,
    )
    _clear_mcas_caches()


@pytest.fixture
def seeded_sandbox(sandbox: SandboxEnv) -> tuple[SandboxEnv, dict[str, str]]:
    """Sandbox pre-populated with 1 AGY session and 2 Claude sessions."""
    ids = seed_standard_trio(
        sandbox.agy_dir,
        sandbox.claude_dir,
        sandbox.ws_a,
        sandbox.ws_b,
    )
    return sandbox, ids
