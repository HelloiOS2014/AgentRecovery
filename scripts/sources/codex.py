"""CodexSource: discover and parse Codex session files (spec v4)."""
from typing import List, Optional

from . import Session, SessionMeta, Source


class CodexSource(Source):
    name = "codex"

    def __init__(self, base_dirs: Optional[List[str]] = None) -> None:
        self.dirs = base_dirs  # filled in Task 2

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        raise NotImplementedError

    def read_session(self, session_id: str) -> Session:
        raise NotImplementedError
