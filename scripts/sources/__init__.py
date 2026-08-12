"""AgentRecovery source registry (B-lite interface)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class SessionMeta:
    id: str
    title: Optional[str] = None
    cwd: Optional[str] = None
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    model: Optional[str] = None

@dataclass
class Event:
    kind: str            # user_msg | assistant_msg | reasoning | tool_call | tool_output
    role: Optional[str] = None
    text: Optional[str] = None          # message text / reasoning summary / tool name
    tool_args: Optional[str] = None
    tool_output: Optional[str] = None

@dataclass
class Session:
    meta: SessionMeta
    events: List[Event] = field(default_factory=list)
    compacted: bool = False
    warnings: List[str] = field(default_factory=list)

class Source:
    name: str = "?"

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        raise NotImplementedError

    def read_session(self, session_id: str) -> Session:
        raise NotImplementedError

from .codex import CodexSource  # noqa: E402  (import at end avoids circular import)

SOURCES: Dict[str, type] = {"codex": CodexSource}
