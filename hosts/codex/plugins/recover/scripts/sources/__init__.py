"""AgentRecovery source registry (B-lite interface).

Shared types live in core.py (single source of truth, packed into both
plugins at release time); this module re-exports them for the host parsers.
"""
from typing import Dict, List, Tuple

from core import Event, Session, SessionMeta  # noqa: F401  (re-exported)


class Source:
    name: str = "?"

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        raise NotImplementedError

    def read_session(self, session_id: str) -> Session:
        raise NotImplementedError


from .claude import ClaudeSource  # noqa: E402  (import at end avoids circular import)
from .codex import CodexSource  # noqa: E402
from .pi import PiSource  # noqa: E402

# Both parsers ship in every host: each side can recover the other agents'
# sessions and its own (cross-project / handoff scenarios).
SOURCES: Dict[str, type] = {"codex": CodexSource, "claude": ClaudeSource, "pi": PiSource}


def target_names(self_name: str, self_mode: bool) -> List[str]:
    """/recover-self → only this host; /recover → every other source."""
    if self_mode:
        return [self_name]
    return [n for n in SOURCES if n != self_name]


def collect_metas(
    instances: Dict[str, Source], names: List[str], limit: int
) -> Tuple[List[SessionMeta], List[str]]:
    """List `limit` newest sessions across `names`, tagging each with .source.

    Returns (metas, blocked_source_names). A source that raises PermissionError
    or OSError is blocked, not empty.
    """
    tagged: List[SessionMeta] = []
    blocked: List[str] = []
    for name in names:
        src = instances.get(name)
        if src is None:
            continue
        try:
            metas = src.list_sessions(limit)
        except (PermissionError, OSError):
            blocked.append(name)
            continue
        for m in metas:
            m.source = name
            tagged.append(m)
    tagged.sort(key=lambda m: m.updated_at or "", reverse=True)
    return tagged[:limit], blocked
