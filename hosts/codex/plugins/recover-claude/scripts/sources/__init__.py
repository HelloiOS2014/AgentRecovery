"""Codex-side AgentRecovery source registry.

Shared types come from core.py (packed into this plugin at release time; see
scripts/pack-codex-plugin.sh in the repo root). This module re-exports them
for the host parsers.
"""
from typing import Dict, List

from core import Event, Session, SessionMeta  # noqa: F401  (re-exported)

class Source:
    name: str = "?"

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        raise NotImplementedError

    def read_session(self, session_id: str) -> Session:
        raise NotImplementedError

from .claude import ClaudeSource  # noqa: E402

SOURCES: Dict[str, type] = {"claude": ClaudeSource}
