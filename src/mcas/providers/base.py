from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from mcas.errors import SyncError
from mcas.models import (
    CanonicalSession,
    SessionSummary,
)
from mcas.util.text import format_bytes

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
        from mcas.providers import get_supported_providers

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
