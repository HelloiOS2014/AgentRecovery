"""Shared rendering core for AgentRecovery — one source of truth, packed into
both the Claude Code plugin (scripts/) and the Codex plugin
(hosts/codex/plugins/recover-claude/scripts/) at release time.

Hold everything here that both sides must agree on: event/session types,
render budgets, and the handoff renderer. Anything host-specific (session
parsers, archive locations, CLI entry points) lives in each plugin's own code.
Keep this file host-agnostic: no paths, no I/O, no argv.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# --- budgets (moved from recover.py unchanged) ---
CAPS = {"usr": 1000, "asst": 1500, "args": 600, "out": 1200, "reason": 100}
RECENT_BUDGET = 40000      # soft: newest turn is always kept even if over
HIST_BUDGET = 20000
HIST_TURNS = 50
FILELIST_CAP = 40
DEFAULT_RECENT = 10
TRUNC = "…(截断)"

# File-writing tool name hints. Codex spells them lowercase; Claude Code spells
# them capitalized (Write/Edit/MultiEdit/NotebookEdit). The parser sources pass
# their own list into render_session/_file_changes.
FILE_TOOL_HINTS_CODEX = ("write", "apply_patch", "edit")
FILE_TOOL_HINTS_CLAUDE = ("write", "edit", "multiedit", "notebookedit", "apply_patch")


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

    @property
    def tool_name(self) -> Optional[str]:
        """Tool name for tool_call events (stored in text)."""
        return self.text


@dataclass
class Session:
    meta: SessionMeta
    events: List[Event] = field(default_factory=list)
    compacted: bool = False
    warnings: List[str] = field(default_factory=list)


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + TRUNC


def _file_changes(events: List[Event], hints: Tuple[str, ...]) -> List[str]:
    seen, out = set(), []
    for e in events:
        if e.kind != "tool_call":
            continue
        name = (e.text or "").lower()
        if not any(h in name for h in hints):
            continue
        args = e.tool_args or ""
        m = None
        for key in ("file_path", "path"):
            m = re.search(r'"%s"\s*:\s*"([^"]+)"' % key, args)
            if m:
                break
        if m is None:
            m = re.search(r"---\s+a/(\S+)", args)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def render_session(session: Session, recent: int,
                   file_hints: Tuple[str, ...] = FILE_TOOL_HINTS_CODEX) -> str:
    meta = session.meta
    lines = []
    truncated = 0  # counted inside cut()

    def cut(text: str, cap: int) -> str:
        """Truncate with marker and count. nonlocal: Python 3 only, fine for 3.9+."""
        nonlocal truncated
        if len(text) <= cap:
            return text
        truncated += 1
        return text[:cap] + TRUNC

    lines.append("# 恢复的会话上下文（%s）" % meta.id)
    lines.append("- 标题：%s" % (meta.title or "无标题"))
    lines.append("- 时间：%s → %s" % ((meta.started_at or "?")[:16], (meta.updated_at or "?")[:16]))
    lines.append("- 原工作目录：`%s`" % (meta.cwd or "?"))
    if meta.model:
        lines.append("- 模型：%s" % meta.model)
    if session.compacted:
        lines.append("- ⚠️ 该会话已压缩：工具调用细节不可用，仅消息骨架")
    lines.append("- 此文件包含工具输出，可能含密钥；请勿外发")
    lines.append("")

    # split turns at user messages
    turns = []
    for e in session.events:
        if e.kind == "user_msg":
            turns.append([e])
        elif turns:
            turns[-1].append(e)
        # orphan events before any user message: ignore

    stats = {"turns": len(turns), "recent_kept": 0, "recent_dropped": 0,
             "hist_kept": 0, "hist_dropped": 0, "truncated": 0, "files": 0}

    # --- history zone: turns[:-recent], compressed ---
    hist = turns[:-recent] if recent < len(turns) else []
    kept_hist, hist_size = [], 0
    for t in reversed(hist):
        user = cut((t[0].text or "").strip() or "[空]", 200)
        asst = next((cut((e.text or "").strip(), 400) for e in t
                     if e.kind == "assistant_msg"), "")
        tools = [e.text for e in t if e.kind == "tool_call"]
        block = "- 用户：%s\n- 助手：%s\n- 工具：%s" % (user, asst or "(无回复)", "，".join(str(x) for x in tools))
        new_size = hist_size + len(block)
        if len(kept_hist) >= HIST_TURNS or new_size > HIST_BUDGET:
            stats["hist_dropped"] += 1
            continue
        kept_hist.append(block)
        hist_size = new_size
    stats["hist_kept"] = len(kept_hist)

    # --- recent zone: last `recent` turns, verbatim within caps, floor on newest ---
    recents = turns[-recent:] if recent > 0 else []
    kept_rec, rec_size = [], 0
    for i in range(len(recents) - 1, -1, -1):
        t = recents[i]
        block_lines = []
        for e in t:
            if e.kind == "user_msg":
                text = (e.text or "").strip() or "[空]"
                block_lines.append("**用户**：%s" % cut(text, CAPS["usr"]))
            elif e.kind == "assistant_msg":
                block_lines.append("**助手**：%s" % cut((e.text or "").strip(), CAPS["asst"]))
            elif e.kind == "reasoning":
                block_lines.append("> %s" % cut((e.text or "").strip(), CAPS["reason"]))
            elif e.kind == "tool_call":
                block_lines.append("`工具` %s：%s" % (e.text or "?",
                                    cut((e.tool_args or "").strip(), CAPS["args"])))
                if e.tool_output:
                    block_lines.append("`输出` %s" % cut(e.tool_output, CAPS["out"]))
        block = "\n".join(block_lines)
        is_newest = (i == len(recents) - 1)
        if not is_newest and rec_size + len(block) > RECENT_BUDGET:
            stats["recent_dropped"] += 1
            continue
        kept_rec.append(block)
        rec_size += len(block)
    kept_rec.reverse()
    stats["recent_kept"] = len(kept_rec)
    stats["truncated"] = truncated

    if kept_rec:
        lines.append("## 最近现场（完整保真，逐项上限内）")
        lines.extend(kept_rec)
    if kept_hist:
        lines.append("\n## 更早历史（压缩）")
        lines.extend(kept_hist)

    files = _file_changes(session.events, file_hints)
    stats["files"] = len(files)
    lines.append("\n## 文件改动")
    if files:
        lines.extend("- %s" % f for f in files[:FILELIST_CAP])
        if len(files) > FILELIST_CAP:
            lines.append("- +%d 更多" % (len(files) - FILELIST_CAP))
    else:
        lines.append("（无识别出的写文件操作）")

    lines.append("\n## 截断统计")
    lines.append("- 总轮数 %d；最近区保留 %d 轮、丢弃 %d 轮；历史区保留 %d 轮、丢弃 %d 轮"
                 % (stats["turns"], stats["recent_kept"], stats["recent_dropped"],
                    stats["hist_kept"], stats["hist_dropped"]))
    lines.append("- 逐项截断 %d 条；文件清单 %d 条（上限 %d）"
                 % (stats["truncated"], stats["files"], FILELIST_CAP))
    for w in session.warnings:
        lines.append("- ⚠️ %s" % w)

    lines.append("\n## 继续任务")
    lines.append("此前的任务目标是恢复此会话的未完成工作。核对当前工作目录是否与原目录一致，")
    lines.append("注意工作区可能有未提交改动；然后继续任务。")
    return "\n".join(lines)
