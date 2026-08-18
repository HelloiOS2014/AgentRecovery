"""ClaudeSource: discover and parse Claude Code session files.

Facts verified on real data (2026-08-13):
- Sessions: ~/.claude/projects/<slugified-cwd>/<uuid>.jsonl, one level deep
  (session subdirs like subagents/ are skipped). Honor CLAUDE_CONFIG_DIR.
- Titles: "ai-title" records (last occurrence wins).
- cwd / model: per-record fields on user/assistant records (model may switch
  mid-session; we collect the unique set).
- An assistant message is streamed across several records sharing
  message.id (thinking / text / tool_use); tool results arrive as user
  records whose content is only tool_result blocks — they must NOT open a
  new turn.
- Compaction: a system record with subtype "compact_boundary"; the whole
  pre-compact history stays in the file, so parsing resets at the last
  boundary and the follow-up summary message (isCompactSummary) is surfaced
  as a warning, not a user turn.
- Tool outputs are sometimes stubbed (<persisted-output>, content-replacement)
  — detect and warn instead of pretending the content is present.
"""
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from . import Event, Session, SessionMeta, Source

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Records that never carry conversation content (silently skipped).
NOISE_TYPES = frozenset((
    "last-prompt", "mode", "permission-mode", "file-history-snapshot",
    "file-history-delta", "queue-operation",
))

# Meta/transcript-only flags: true means "not real conversation content".
SKIP_FLAGS = ("isMeta", "isCompactSummary", "isVirtual", "isVisibleInTranscriptOnly")

# User messages that are local-command echoes (/compact and friends).
LOCAL_CMD_PREFIXES = ("<command-name>", "<local-command-")


def default_projects_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def _iter_records(path: str) -> Tuple[List[dict], int]:
    """All JSON records + count of bad lines."""
    records, bad = [], 0
    with open(path) as fh:
        for line in fh:
            try:
                records.append(json.loads(line))
            except ValueError:
                bad += 1
    return records, bad


class ClaudeSource(Source):
    name = "claude"

    def __init__(self, projects_dir: Optional[str] = None) -> None:
        self.projects_dir = projects_dir or default_projects_dir()

    # --- discovery ---

    def _session_files(self) -> Dict[str, str]:
        """uuid -> path, one level under each project dir."""
        found: Dict[str, str] = {}
        if not os.path.isdir(self.projects_dir):
            return found
        for entry in os.listdir(self.projects_dir):
            sub = os.path.join(self.projects_dir, entry)
            if not os.path.isdir(sub):
                continue
            for name in os.listdir(sub):
                if not name.endswith(".jsonl"):
                    continue
                sid = name[:-6]
                if UUID_RE.match(sid):
                    found[sid] = os.path.join(sub, name)
        return found

    def _scan_meta(self, path: str) -> Tuple[Optional[str], Optional[str], Optional[str], Set[str]]:
        """title (ai-title last wins), cwd, started_at, models — no event parse."""
        title = cwd = started_at = None
        models: Set[str] = set()
        records, _ = _iter_records(path)
        for r in records:
            if r.get("type") == "ai-title" and r.get("aiTitle"):
                title = r["aiTitle"]
            elif r.get("type") in ("user", "assistant"):
                if cwd is None and r.get("cwd"):
                    cwd = r["cwd"]
                if started_at is None and r.get("timestamp"):
                    started_at = r["timestamp"]
                model = (r.get("message") or {}).get("model")
                if model:
                    models.add(model)
        return title, cwd, started_at, models

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        found = self._session_files()
        if not found:
            return []
        order = sorted(found.items(), key=lambda kv: os.path.getmtime(kv[1]), reverse=True)
        metas = []
        for sid, path in order[:limit]:
            title, cwd, started_at, models = self._scan_meta(path)
            metas.append(SessionMeta(
                id=sid, title=title, cwd=cwd, started_at=started_at,
                updated_at=datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
                model=", ".join(sorted(models)) or None,
            ))
        return metas

    # --- read ---

    def _find_file(self, session_id: str) -> Optional[str]:
        return self._session_files().get(session_id)

    def read_session(self, session_id: str) -> Session:
        path = self._find_file(session_id)
        if not path:
            raise LookupError(
                "未找到 Claude Code 会话 %s（已扫描 %s 下所有项目目录）。\n"
                "会话 ID 是 ~/.claude/projects/<项目>/*.jsonl 的文件名。" % (session_id, self.projects_dir))
        return self._parse(path, session_id)

    def _parse(self, path: str, session_id: str) -> Session:
        meta = SessionMeta(id=session_id)
        events: List[Event] = []
        calls: Dict[str, int] = {}   # tool_use_id -> index in events
        compacted = False
        warnings: List[str] = []
        models: Set[str] = set()
        unknown: Dict[str, int] = {}
        skipped_attachments = 0
        skipped_flags = 0
        skipped_local_cmd = 0

        records, bad = _iter_records(path)
        for r in records:
            t = r.get("type")
            if any(r.get(f) for f in SKIP_FLAGS):
                skipped_flags += 1
                continue
            if t == "user":
                if meta.cwd is None and r.get("cwd"):
                    meta.cwd = r["cwd"]
                if meta.started_at is None and r.get("timestamp"):
                    meta.started_at = r["timestamp"]
                skipped_local_cmd += self._handle_user(r, events, calls, warnings)
            elif t == "assistant":
                if meta.cwd is None and r.get("cwd"):
                    meta.cwd = r["cwd"]
                if meta.started_at is None and r.get("timestamp"):
                    meta.started_at = r["timestamp"]
                self._handle_assistant(r, events, calls, models)
            elif t == "ai-title":
                if r.get("aiTitle"):
                    meta.title = r["aiTitle"]
            elif t == "system":
                if r.get("subtype") == "compact_boundary":
                    events, calls = [], {}
                    compacted = True
                    warnings.append("会话在 %s 压缩过：已重置到压缩边界，仅保留摘要之后的上下文"
                                    % ((r.get("timestamp") or "?")[:16]))
            elif t == "attachment":
                skipped_attachments += 1
            elif t in NOISE_TYPES:
                pass
            else:
                unknown[t] = unknown.get(t, 0) + 1

        if bad:
            warnings.append("解析中跳过 %d 个坏行（并发写入或中断所致）" % bad)
        if skipped_flags:
            warnings.append("跳过 %d 条元数据/摘要记录（isMeta/isCompactSummary 等）" % skipped_flags)
        if skipped_local_cmd:
            warnings.append("跳过 %d 条本地命令回显（/compact 等）" % skipped_local_cmd)
        if skipped_attachments:
            warnings.append("跳过 %d 条 attachment 记录（hook、token 用量等）" % skipped_attachments)
        for t, n in sorted(unknown.items()):
            warnings.append("跳过未知记录类型 %s（%d 条）" % (t, n))

        if models:
            meta.model = ", ".join(sorted(models))
        return Session(meta=meta, events=events, compacted=compacted, warnings=warnings)

    # --- record handlers ---

    @staticmethod
    def _handle_user(r, events, calls, warnings) -> int:
        """Returns the number of local-command echoes skipped."""
        content = (r.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content.strip()
            if not text:
                return 0
            if text.startswith(LOCAL_CMD_PREFIXES):
                return 1
            events.append(Event(kind="user_msg", role="user", text=text))
            return 0
        if not isinstance(content, list):
            return 0
        texts, results = [], []
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and isinstance(b.get("text"), str):
                texts.append(b["text"])
            elif bt == "tool_result":
                results.append(b)
        if texts:
            events.append(Event(kind="user_msg", role="user",
                                text="\n".join(texts).strip()))
        for b in results:
            ClaudeSource._attach_tool_result(b, events, calls, warnings)
        return 0

    @staticmethod
    def _attach_tool_result(b, events, calls, warnings) -> None:
        tid = b.get("tool_use_id")
        content = b.get("content")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
            text = "\n".join(parts)
        elif isinstance(content, str):
            text = content
        else:
            text = ""
        if text.startswith("<persisted-output>"):
            text = "<persisted-output>…（完整输出在 ~/.claude 的 tool-results 目录）"
            warnings.append("存在持久化输出 stub，工具输出未内联")
        if tid and tid in calls:
            events[calls[tid]].tool_output = text
        else:
            events.append(Event(kind="tool_output", text=text))
            warnings.append("存在无法配对 tool_use_id 的工具输出（已顺序追加）")

    @staticmethod
    def _handle_assistant(r, events, calls, models) -> None:
        msg = r.get("message") or {}
        model = msg.get("model")
        if model:
            models.add(model)
        content = msg.get("content")
        if not isinstance(content, list):
            return
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "thinking":
                thinking = b.get("thinking")
                if isinstance(thinking, str) and thinking.strip():
                    events.append(Event(kind="reasoning", role="assistant", text=thinking.strip()))
            elif bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                events.append(Event(kind="assistant_msg", role="assistant", text=b["text"].strip()))
            elif bt == "tool_use":
                args = b.get("input")
                events.append(Event(kind="tool_call", text=b.get("name") or "?",
                                    tool_args=json.dumps(args, ensure_ascii=False) if args is not None else ""))
                if b.get("id"):
                    calls[b["id"]] = len(events) - 1
            # tool_use_2 = internal subagent calls; skipped on purpose
