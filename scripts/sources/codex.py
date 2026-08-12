"""CodexSource: discover and parse Codex session files (spec v4).

Facts verified on real data (2026-08-12):
- Sessions: ~/.codex/sessions/**/rollout-<timestamp>-<uuid>.jsonl (2026+, dated dirs),
  ~/.codex/sessions/rollout-*.json (2025 era, root), ~/.codex/archived_sessions/rollout-*.jsonl.
- ID is the trailing UUID of the filename.
- session_index.jsonl holds thread_name titles only (may contain duplicate ids).
"""
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import Session, SessionMeta, Source

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Codex-injected wrapper blocks stripped verbatim from user messages (allowlist).
# Anything else inside angle brackets (<redacted>, <path>...) is transcript content — keep.
WRAPPER_TAGS: Tuple[str, ...] = ("environment_context", "recommended_plugins")


def strip_wrapper_blocks(text: str) -> str:
    """Remove known Codex-injected wrapper blocks. re.S: blocks are multi-line (verified)."""
    for tag in WRAPPER_TAGS:
        text = re.sub(r"<%s>.*?</%s>" % (tag, tag), "", text, flags=re.S)
    return text


def _uuid_from_filename(name: str) -> Optional[str]:
    stem = name[:-6] if name.endswith(".jsonl") else name[:-5]
    m = UUID_RE.search(stem)
    return m.group(0) if m else None


class CodexSource(Source):
    name = "codex"

    def __init__(self, base_dirs: Optional[List[str]] = None) -> None:
        if base_dirs is None:
            home = os.path.expanduser("~")
            base_dirs = [
                os.path.join(home, ".codex", "sessions"),
                os.path.join(home, ".codex", "archived_sessions"),
            ]
        self.dirs = base_dirs
        self.index_path = os.path.join(os.path.expanduser("~"), ".codex", "session_index.jsonl")

    def _find_file(self, session_id: str) -> Optional[str]:
        for base in self.dirs:
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for f in files:
                    if f.startswith("rollout-") and _uuid_from_filename(f) == session_id:
                        return os.path.join(root, f)
        return None

    def _load_titles(self) -> Dict[str, str]:
        best: Dict[str, str] = {}
        try:
            with open(self.index_path) as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    sid, title = d.get("id"), d.get("thread_name")
                    if sid and title:
                        best[sid] = title  # last occurrence wins (index appends newer entries)
        except OSError:
            pass
        return best

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        found: Dict[str, Tuple[str, float]] = {}  # id -> (path, mtime); newest wins across dirs
        for base in self.dirs:
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for f in files:
                    if not (f.startswith("rollout-") and (f.endswith(".jsonl") or f.endswith(".json"))):
                        continue
                    sid = _uuid_from_filename(f)
                    if not sid:
                        continue
                    path = os.path.join(root, f)
                    mtime = os.path.getmtime(path)
                    if sid not in found or mtime > found[sid][1]:
                        found[sid] = (path, mtime)
        titles = self._load_titles()
        metas = []
        for sid, (_path, mtime) in found.items():
            metas.append(SessionMeta(
                id=sid,
                title=titles.get(sid),
                updated_at=datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            ))
        metas.sort(key=lambda m: m.updated_at or "", reverse=True)
        return metas[:limit]

    def read_session(self, session_id: str) -> Session:
        raise NotImplementedError  # Task 3
