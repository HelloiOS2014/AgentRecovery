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

from . import Event, Session, SessionMeta, Source

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


def _first_cwd(path: str) -> Optional[str]:
    """cwd from the first session_meta record (first few lines only)."""
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "session_meta":
                    return (d.get("payload") or {}).get("cwd")
    except OSError:
        pass
    return None


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
        metas = metas[:limit]
        # cwd lives in the first session_meta record; read it for the returned
        # subset only (a few files, not the whole archive).
        for m in metas:
            m.cwd = _first_cwd(found[m.id][0])
        return metas

    def read_session(self, session_id: str) -> Session:
        path = self._find_file(session_id)
        if not path:
            raise LookupError(
                "未找到会话 %s：已扫描 %s 与 %s（含归档与 2025 旧格式）。\n"
                "获取 ID 的方式：Codex CLI 退出时输出 `codex resume <id>`，或桌面端复制。"
                % (session_id, self.dirs[0], self.dirs[1]))
        meta, events, compacted, warnings = self._parse(path, session_id)
        return Session(meta=meta, events=events, compacted=compacted, warnings=warnings)

    def _parse(self, path: str, session_id: str) -> Tuple[SessionMeta, List[Event], bool, List[str]]:
        meta = SessionMeta(id=session_id)
        events: List[Event] = []
        calls: Dict[str, int] = {}  # call_id -> index in events
        compacted = False
        warnings: List[str] = []
        bad_lines = 0

        with open(path) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    bad_lines += 1
                    continue
                t = d.get("type")
                if t == "session_meta":
                    p = d.get("payload") or {}
                    meta = SessionMeta(
                        id=session_id,
                        cwd=p.get("cwd"),
                        started_at=p.get("timestamp"),
                        model=p.get("model") or p.get("model_provider"),
                    )
                elif t == "compacted":
                    compacted = True
                elif t == "response_item":
                    self._handle_item(d.get("payload") or {}, events, calls, warnings)

        if bad_lines:
            warnings.append("解析中跳过 %d 个坏行（并发写入或中断所致）" % bad_lines)
        return meta, events, compacted, warnings

    @staticmethod
    def _handle_item(it, events, calls, warnings) -> None:
        k = it.get("type")
        if k == "message":
            role = it.get("role")
            if role not in ("user", "assistant"):
                return  # developer 消息（人格提示词、app-context 等）整体丢弃
            text = "".join(
                c.get("text", "") for c in it.get("content", [])
                if isinstance(c, dict) and isinstance(c.get("text"), str)
            )
            if role == "user":
                text = strip_wrapper_blocks(text)
            events.append(Event(kind="user_msg" if role == "user" else "assistant_msg",
                                role=role, text=text))
        elif k in ("function_call", "custom_tool_call"):
            args = it.get("arguments")
            if args is None:
                inp = it.get("input")
                args = json.dumps(inp, ensure_ascii=False) if inp is not None else ""
            events.append(Event(kind="tool_call", text=it.get("name"), tool_args=str(args)))
            calls[it.get("call_id")] = len(events) - 1
        elif k in ("function_call_output", "custom_tool_call_output"):
            out = it.get("output", it.get("content", ""))
            if not isinstance(out, str):
                out = json.dumps(out, ensure_ascii=False)
            idx = calls.get(it.get("call_id"))
            if idx is not None:
                events[idx].tool_output = out
            else:
                events.append(Event(kind="tool_output", text=out))
                warnings.append("存在无法配对 call_id 的工具输出（已顺序追加）")
        elif k == "reasoning":
            summary = it.get("summary_text")
            if summary:
                events.append(Event(kind="reasoning", text=summary))
            else:
                events.append(Event(kind="reasoning", text="[思维链已加密，跳过]"))
