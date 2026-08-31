"""PiSource: discover and parse Pi session files.

Facts verified on real data (2026-08-31):
- Sessions: ~/.pi/agent/sessions/--<cwd-with-/-as--->--/<ISO-ts>_<uuid>.jsonl
- Header: {type: session, version: 3, id, timestamp, cwd} — not part of the tree
- Tree via id/parentId; recover the current leaf path (latest timestamp among
  entries with no children). Linear files (no parentId) keep record order.
- Assistant content blocks: text / thinking / toolCall (id, name, arguments)
- Tool results are separate messages, role=toolResult, paired by toolCallId
- Compaction entries reset context like Claude compact_boundary
"""
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from . import Event, Session, SessionMeta, Source

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def default_sessions_dir() -> str:
    agent = os.environ.get("PI_AGENT_DIR") or os.path.join(os.path.expanduser("~"), ".pi", "agent")
    return os.path.join(agent, "sessions")


def _uuid_from_filename(name: str) -> Optional[str]:
    stem = name[:-6] if name.endswith(".jsonl") else name
    m = UUID_RE.search(stem)
    return m.group(0).lower() if m else None


def _content_text(content) -> Tuple[str, int]:
    if isinstance(content, str):
        return content, 0
    if not isinstance(content, list):
        return "", 0
    parts, n_img = [], 0
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            parts.append(b["text"])
        elif b.get("type") == "image":
            n_img += 1
    return "\n".join(parts), n_img


def _iter_records(path: str) -> Tuple[List[dict], int]:
    records, bad = [], 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                bad += 1
    return records, bad


class PiSource(Source):
    name = "pi"

    def __init__(self, sessions_dir: Optional[str] = None) -> None:
        self.sessions_dir = sessions_dir or default_sessions_dir()

    def _session_files(self) -> Dict[str, str]:
        found: Dict[str, Tuple[str, float]] = {}
        if not os.path.isdir(self.sessions_dir):
            return {}
        for root, _, files in os.walk(self.sessions_dir):
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                sid = _uuid_from_filename(f)
                if not sid:
                    continue
                path = os.path.join(root, f)
                mtime = os.path.getmtime(path)
                if sid not in found or mtime > found[sid][1]:
                    found[sid] = (path, mtime)
        return {sid: pair[0] for sid, pair in found.items()}

    def _scan_meta(self, path: str, sid: str) -> SessionMeta:
        title = cwd = started_at = model = None
        records, _ = _iter_records(path)
        models: Set[str] = set()
        for r in records:
            t = r.get("type")
            if t == "session":
                cwd = r.get("cwd") or cwd
                started_at = r.get("timestamp") or started_at
                if r.get("id"):
                    sid = r["id"]
            elif t == "session_info" and r.get("name"):
                title = r["name"]
            elif t == "model_change" and r.get("modelId"):
                models.add(r["modelId"])
            elif t == "message":
                msg = r.get("message") or {}
                if msg.get("model"):
                    models.add(msg["model"])
                if title is None and msg.get("role") == "user":
                    text, _ = _content_text(msg.get("content"))
                    text = text.strip()
                    if text:
                        title = text.splitlines()[0][:80]
        if models:
            model = ", ".join(sorted(models))
        return SessionMeta(id=sid, title=title, cwd=cwd, started_at=started_at, model=model)

    def list_sessions(self, limit: int = 20) -> List[SessionMeta]:
        files = self._session_files()
        if not files:
            return []
        order = sorted(files.items(), key=lambda kv: os.path.getmtime(kv[1]), reverse=True)
        metas = []
        for sid, path in order[:limit]:
            meta = self._scan_meta(path, sid)
            meta.updated_at = datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
            metas.append(meta)
        return metas

    def _find_file(self, session_id: str) -> Optional[str]:
        want = session_id.lower()
        files = self._session_files()
        if want in files:
            return files[want]
        # partial / exact header id mismatch vs filename
        for sid, path in files.items():
            if sid.startswith(want) or want.startswith(sid):
                return path
        return None

    def read_session(self, session_id: str) -> Session:
        path = self._find_file(session_id)
        if not path:
            raise LookupError(
                "未找到 Pi 会话 %s（已扫描 %s）。\n"
                "会话 ID 来自 `~/.pi/agent/sessions/` 文件名或 `/session` 显示的 ID。"
                % (session_id, self.sessions_dir))
        return self._parse(path, session_id)

    def _leaf_path(self, records: List[dict]) -> List[dict]:
        tree = [r for r in records if r.get("type") != "session" and r.get("id")]
        if not tree:
            return []
        if all(not r.get("parentId") for r in tree):
            return tree
        by_id = {r["id"]: r for r in tree}
        children: Set[str] = set()
        for r in tree:
            pid = r.get("parentId")
            if pid:
                children.add(pid)
        leaves = [r for r in tree if r["id"] not in children]
        if not leaves:
            leaf = tree[-1]
        else:
            leaf = max(leaves, key=lambda r: r.get("timestamp") or "")
        path, seen = [], set()
        cur = leaf.get("id")
        while cur and cur in by_id and cur not in seen:
            path.append(by_id[cur])
            seen.add(cur)
            cur = by_id[cur].get("parentId")
        path.reverse()
        return path

    def _after_compaction(self, path: List[dict]) -> Tuple[List[dict], Optional[list], bool, Optional[str]]:
        last = None
        for i, r in enumerate(path):
            if r.get("type") == "compaction":
                last = i
        if last is None:
            return path, None, False, None
        rec = path[last]
        summary = rec.get("summary")
        tail = rec.get("retainedTail") if isinstance(rec.get("retainedTail"), list) else None
        rest = [r for r in path[last + 1 :] if r.get("type") != "compaction"]
        return rest, tail, True, summary

    def _parse(self, path: str, session_id: str) -> Session:
        meta = SessionMeta(id=session_id, source="pi")
        events: List[Event] = []
        calls: Dict[str, int] = {}
        warnings: List[str] = []
        models: Set[str] = set()
        unknown: Dict[str, int] = {}
        skipped_images = 0

        records, bad = _iter_records(path)
        for r in records:
            if r.get("type") == "session":
                meta.cwd = r.get("cwd") or meta.cwd
                meta.started_at = r.get("timestamp") or meta.started_at
                if r.get("id"):
                    meta.id = r["id"]
                break

        path_recs = self._leaf_path(records)
        rest, retained, compacted, summary = self._after_compaction(path_recs)
        if compacted:
            warnings.append("会话压缩过：已重置到压缩边界，仅保留摘要之后的上下文")
            if summary:
                warnings.append("压缩摘要：%s" % (summary[:200] + ("…" if len(summary) > 200 else "")))
        if retained:
            for msg in retained:
                if isinstance(msg, dict):
                    skipped_images += self._handle_message(msg, events, calls, warnings, models)

        for r in rest:
            t = r.get("type")
            if t == "message":
                skipped_images += self._handle_message(r.get("message") or {}, events, calls, warnings, models)
            elif t == "session_info":
                if r.get("name"):
                    meta.title = r["name"]
            elif t == "model_change":
                if r.get("modelId"):
                    models.add(r["modelId"])
            elif t in ("thinking_level_change", "custom", "custom_message",
                       "label", "branch_summary", "compaction"):
                pass
            else:
                unknown[t or "?"] = unknown.get(t or "?", 0) + 1

        if meta.title is None:
            for e in events:
                if e.kind == "user_msg" and (e.text or "").strip():
                    meta.title = e.text.strip().splitlines()[0][:80]
                    break
        if models:
            meta.model = ", ".join(sorted(models))
        if bad:
            warnings.append("解析中跳过 %d 个坏行（并发写入或中断所致）" % bad)
        if skipped_images:
            warnings.append("跳过 %d 张图片（不恢复附件）" % skipped_images)
        for t, n in sorted(unknown.items()):
            warnings.append("跳过未知记录类型 %s（%d 条）" % (t, n))
        return Session(meta=meta, events=events, compacted=compacted, warnings=warnings)

    @staticmethod
    def _handle_message(msg, events, calls, warnings, models) -> int:
        n_img = 0
        role = msg.get("role")
        if msg.get("model"):
            models.add(msg["model"])
        if role == "user":
            text, n_img = _content_text(msg.get("content"))
            if text.strip():
                events.append(Event(kind="user_msg", role="user", text=text.strip()))
        elif role == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "thinking":
                        th = b.get("thinking")
                        if isinstance(th, str) and th.strip():
                            events.append(Event(kind="reasoning", role="assistant", text=th.strip()))
                    elif bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                        events.append(Event(kind="assistant_msg", role="assistant", text=b["text"].strip()))
                    elif bt == "toolCall":
                        args = b.get("arguments")
                        if not isinstance(args, str):
                            args = json.dumps(args, ensure_ascii=False) if args is not None else ""
                        events.append(Event(kind="tool_call", text=b.get("name") or "?", tool_args=args))
                        if b.get("id"):
                            calls[b["id"]] = len(events) - 1
                    elif bt == "image":
                        n_img += 1
            elif isinstance(content, str) and content.strip():
                events.append(Event(kind="assistant_msg", role="assistant", text=content.strip()))
        elif role == "toolResult":
            text, n_img = _content_text(msg.get("content"))
            tid = msg.get("toolCallId")
            if tid and tid in calls:
                events[calls[tid]].tool_output = text
            else:
                events.append(Event(kind="tool_output", text=text))
                warnings.append("存在无法配对 toolCallId 的工具输出（已顺序追加）")
        elif role == "bashExecution":
            events.append(Event(
                kind="tool_call",
                text="bash",
                tool_args=msg.get("command") or "",
                tool_output=msg.get("output") or "",
            ))
        return n_img
