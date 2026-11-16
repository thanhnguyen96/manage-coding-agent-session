from __future__ import annotations

from mcas.errors import SyncError
from mcas.models import SessionSummary
from mcas.providers.agy.provider import AgyProvider
from mcas.providers.base import (
    AgentProvider,
    FutureProvider,
)
from mcas.providers.claude.provider import ClaudeProvider
from mcas.providers.codex.provider import CodexProvider

PROVIDERS: dict[str, AgentProvider] = {
    "agy": AgyProvider(),
    "claude": ClaudeProvider(),
    "codex": CodexProvider(),
    "opencode": FutureProvider("opencode", "OpenCode", badge="Soon"),
}

def get_supported_providers() -> list[AgentProvider]:
    return [p for p in PROVIDERS.values() if p.supported]

def get_provider(provider_id: str, require_supported: bool = True) -> AgentProvider:
    pid = (provider_id or "").strip().lower()
    prov = PROVIDERS.get(pid)
    if prov is None:
        known = ", ".join(PROVIDERS.keys())
        raise SyncError(f"Unknown provider '{provider_id}'. Known providers: {known}.")
    if require_supported and not prov.supported:
        supported = ", ".join(p.id for p in get_supported_providers())
        raise SyncError(
            f"Provider '{prov.id}' ({prov.name}) is not supported yet (coming soon). "
            f"Supported providers: {supported}."
        )
    return prov


def collect_sessions_by_provider(
    include_all: bool,
) -> dict[str, list[SessionSummary]]:
    out: dict[str, list[SessionSummary]] = {}
    for prov in get_supported_providers():
        out[prov.id] = prov.list_sessions(include_all=include_all, cwd_filter=None)
    return out


_collect_sessions_by_provider = collect_sessions_by_provider
