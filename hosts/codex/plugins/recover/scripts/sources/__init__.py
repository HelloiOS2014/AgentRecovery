"""AgentRecovery source registry (B-lite interface).

Shared types live in core.py (single source of truth, packed into both
plugins at release time); this module re-exports them for the host parsers.
"""
from typing import Dict, List

from core import Event, Session, SessionMeta  # noqa: F401  (re-exported)

class Source:
    name: str = "?"

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        raise NotImplementedError

    def read_session(self, session_id: str) -> Session:
        raise NotImplementedError

from .claude import ClaudeSource  # noqa: E402  (import at end avoids circular import)
from .codex import CodexSource  # noqa: E402

# Both parsers ship in both plugins: each side can recover the other agent's
# sessions and its own (cross-project / handoff scenarios).
SOURCES: Dict[str, type] = {"codex": CodexSource, "claude": ClaudeSource}
