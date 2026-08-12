#!/usr/bin/env python3
"""AgentRecovery CLI — recover sessions from other agents into Claude Code.

Usage:
  python3 recover.py list [--limit N]
  python3 recover.py show <session-id> [--recent N]
  python3 recover.py self-test
"""
import os
import re
import sys
from typing import List, Optional

from sources import SOURCES, Session, Event

CAPS = {"usr": 1000, "asst": 1500, "args": 600, "out": 1200, "reason": 100}
RECENT_BUDGET = 40000      # soft: newest turn is always kept even if over
HIST_BUDGET = 20000
HIST_TURNS = 50
FILELIST_CAP = 40
DEFAULT_RECENT = 10
TRUNC = "…(截断)"
ARCHIVE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "recover-handoffs")
FILE_TOOL_HINTS = ("write", "apply_patch", "edit")


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + TRUNC


def _is_current(cwd: Optional[str], cur: str) -> bool:
    """True when a session's original cwd is the directory we run in
    (realpath so symlinked paths match)."""
    if not cwd:
        return False
    return os.path.realpath(cwd) == os.path.realpath(cur)


def _sort_by_current(metas: List["SessionMeta"], cur: str) -> List["SessionMeta"]:
    """Stable sort: sessions from the current project first, rest by mtime."""
    metas.sort(key=lambda m: _is_current(m.cwd, cur), reverse=True)
    return metas


def _file_changes(events: List[Event]) -> List[str]:
    seen, out = set(), []
    for e in events:
        if e.kind != "tool_call":
            continue
        name = (e.text or "").lower()
        if not any(h in name for h in FILE_TOOL_HINTS):
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


def render_session(session: Session, recent: int) -> str:
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

    files = _file_changes(session.events)
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


def cmd_list(limit: int) -> int:
    from sources import SOURCES

    cur = os.getcwd()
    for name, src_cls in SOURCES.items():
        metas = _sort_by_current(src_cls().list_sessions(limit), cur)
        print("[%s] 最近 %d 个会话：输入序号或粘贴完整 session ID（* = 当前项目）" % (name, len(metas)))
        for i, m in enumerate(metas, 1):
            title = m.title or "无标题"
            mark = "*" if _is_current(m.cwd, cur) else " "
            print("%s%3d. %-28s %s  cwd=%s  (%s)" % (
                mark, i, title[:28], (m.updated_at or "")[:16], m.cwd or "?", m.id))
        print("\n用法：/recover <序号> 或 /recover <完整session ID>")
        return 0
    return 1


def cmd_show(session_id: str, recent: int) -> int:
    src = SOURCES["codex"]()
    if session_id.isdigit() and int(session_id) >= 1:
        n = int(session_id)
        metas = _sort_by_current(src.list_sessions(20), os.getcwd())
        if n > len(metas):
            print("序号 %d 超出范围（有效 1..%d）" % (n, len(metas)))
            return 1
        session_id = metas[n - 1].id
    try:
        session = src.read_session(session_id)
    except LookupError as err:
        print(str(err))
        return 1
    if session.meta.cwd and not _is_current(session.meta.cwd, os.getcwd()):
        print("⚠️ 此会话来自其他项目：%s（当前目录：%s），路径请注意核对" % (session.meta.cwd, os.getcwd()))
    try:
        session = src.read_session(session_id)
    except LookupError as err:
        print(str(err))
        return 1
    if not session.meta.title and hasattr(src, "_load_titles"):
        # Task 3 缺口：标题只在 list_sessions 注入，read_session 不带；这里补一次
        # 索引查询（recover.py 侧最小修补，不动 sources）。局限：仅 codex 源有 _load_titles。
        title = src._load_titles().get(session_id)
        if title:
            session.meta.title = title
    text = render_session(session, recent)
    print(text)
    try:
        os.makedirs(ARCHIVE_DIR, mode=0o700, exist_ok=True)
        path = os.path.join(ARCHIVE_DIR, session_id + ".md")
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o600)
        print("\n[存档] %s" % path)
    except OSError as err:
        print("[警告] 存档失败：%s" % err)
    return 0


def run_self_test() -> int:
    import json
    import os
    import tempfile

    failures = []

    def check(name: str, cond: bool) -> None:
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    from sources.codex import CodexSource, _uuid_from_filename

    # --- discovery fixtures: real filename shapes ---
    tmp = tempfile.mkdtemp(prefix="ar-self-test-")
    os.makedirs(os.path.join(tmp, "sessions", "2026", "08", "11"))
    os.makedirs(os.path.join(tmp, "sessions", "2025"))
    os.makedirs(os.path.join(tmp, "archived_sessions"))
    sid_modern = "01234567-89ab-cdef-0123-456789abcdef"
    sid_legacy = "fedcba98-7654-3210-fedc-ba9876543210"
    sid_arch = "11111111-2222-3333-4444-555555555555"
    modern = os.path.join(tmp, "sessions", "2026", "08", "11",
                          "rollout-2026-08-11T17-17-17-" + sid_modern + ".jsonl")
    legacy = os.path.join(tmp, "sessions", "2025", "rollout-2025-06-27-" + sid_legacy + ".json")
    arch = os.path.join(tmp, "archived_sessions",
                        "rollout-2026-03-04T20-05-38-" + sid_arch + ".jsonl")
    for p, content in [
        (modern, '{"type": "session_meta", "payload": {"cwd": "/x", "timestamp": "2026-08-11T10:00:00Z", "model_provider": "openai"}}\n'),
        (legacy, '{"type": "session_meta", "payload": {"cwd": "/y"}}\n'),
        (arch, '{"type": "session_meta", "payload": {"cwd": "/z"}}\n'),
    ]:
        with open(p, "w") as fh:
            fh.write(content)

    # updated_at comes from mtime (seconds precision); three writes in the same
    # second would tie the sort, so pin distinct mtimes matching the filenames.
    from datetime import datetime
    for p, iso in [
        (modern, "2026-08-11T17:17:17"),
        (legacy, "2025-06-27T12:00:00"),
        (arch, "2026-03-04T20:05:38"),
    ]:
        ts = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").timestamp()
        os.utime(p, (ts, ts))

    src = CodexSource(base_dirs=[os.path.join(tmp, "sessions"), os.path.join(tmp, "archived_sessions")])
    metas = src.list_sessions(limit=20)
    ids = [m.id for m in metas]
    check("list finds modern jsonl", sid_modern in ids)
    check("list finds legacy root .json", sid_legacy in ids)
    check("list finds archived jsonl", sid_arch in ids)
    check("list sorted by updated_at desc", metas[0].id == sid_modern)
    cwd_by_id = {m.id: m.cwd for m in metas}
    check("list fills cwd from first session_meta",
          cwd_by_id.get(sid_modern) == "/x" and cwd_by_id.get(sid_legacy) == "/y" and cwd_by_id.get(sid_arch) == "/z")
    from sources import SessionMeta
    from recover import _is_current, _sort_by_current
    check("_is_current matches by realpath", _is_current("/x", "/x") and not _is_current("/x", "/y"))
    check("_is_current false for missing cwd", not _is_current(None, "/x"))
    cur_sorted = _sort_by_current([
        SessionMeta(id="a", cwd="/y"), SessionMeta(id="b", cwd="/x"), SessionMeta(id="c", cwd="/y"),
    ], "/y")
    check("current-project sessions pinned first", [m.id for m in cur_sorted] == ["a", "c", "b"])
    check("uuid extraction from modern name",
          _uuid_from_filename("rollout-2026-08-11T17-17-17-" + sid_modern + ".jsonl") == sid_modern)
    check("uuid extraction from legacy .json",
          _uuid_from_filename("rollout-2025-06-27-" + sid_legacy + ".json") == sid_legacy)
    check("uuid extraction rejects non-uuid",
          _uuid_from_filename("rollout-2026-01-01.txt") is None)

    # --- title enrichment from session_index.jsonl (dedup, last wins) ---
    idx_path = os.path.join(tmp, "session_index.jsonl")
    with open(idx_path, "w") as fh:
        fh.write(json.dumps({"id": sid_modern, "thread_name": "旧标题", "updated_at": "2026-08-10T00:00:00Z"}) + "\n")
        fh.write(json.dumps({"id": sid_modern, "thread_name": "分析近7天用户反馈", "updated_at": "2026-08-11T00:00:00Z"}) + "\n")
        fh.write(json.dumps({"id": sid_legacy, "thread_name": "2025老会话"}) + "\n")
    src.index_path = idx_path
    metas = src.list_sessions(limit=20)
    by_id = {m.id: m for m in metas}
    check("title from index, last occurrence wins",
          by_id[sid_modern].title == "分析近7天用户反馈")
    check("title lookup works", by_id[sid_legacy].title == "2025老会话")
    check("archived entry has no title", by_id[sid_arch].title is None)

    # --- parse fixtures: full record handling ---
    def one_line(obj):
        return json.dumps(obj, ensure_ascii=False) + "\n"

    dev_text = "<permissions instructions>\n</permissions instructions>"
    user_text = ("<recommended_plugins>Here is a list of plugins…</recommended_plugins>\n"
                 "帮我分析一下这个报错\n<environment_context>\n<cwd>/Users/x</cwd>\n<shell>zsh</shell>\n</environment_context>")
    parse_file = os.path.join(tmp, "sessions", "2026", "08", "11",
                              "rollout-2026-08-11T17-17-17-" + sid_modern + ".jsonl")
    with open(parse_file, "w") as fh:
        fh.write(one_line({"type": "session_meta", "payload": {"cwd": "/x", "timestamp": "2026-08-11T10:00:00Z", "model_provider": "openai"}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": dev_text}]}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": user_text}]}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "我先看看这个报错"}]}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": "gAAAAABqcuS", "summary": []}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "reasoning", "summary_text": "用户想看报错分析"}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "custom_tool_call", "id": "ctc_1", "name": "apply_patch", "input": {"file_path": "/x/src/a.py", "patch": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-OLD\n+NEW"}, "call_id": "call_1"}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "custom_tool_call_output", "id": "ctco_1", "call_id": "call_1", "output": {"status": "success"}}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{\"cmd\":\"pwd\"}", "call_id": "call_2"}}))
        fh.write(one_line({"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call_9", "output": "orphan output"}}))
        fh.write(one_line({"type": "event_msg", "payload": {"type": "user_message", "data": {"text": "raw"}}}))
        fh.write(one_line({"type": "turn_context", "payload": {"turn_id": "t1"}}))
        fh.write("not-json-line\n")
        fh.write(one_line({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "改完了"}]}}))
        fh.write(one_line({"type": "compacted", "payload": {}}))
        fh.write(one_line({"type": "session_meta", "payload": {"cwd": "/x-new", "timestamp": "2026-08-11T11:00:00Z"}}))

    session = src.read_session(sid_modern)
    check("compacted flag set", session.compacted is True)
    check("bad line counted in warnings", any("坏行" in w for w in session.warnings))
    check("last session_meta wins", session.meta.cwd == "/x-new")
    check("developer message dropped",
          not any(e.kind == "assistant_msg" and e.role == "developer" for e in session.events))
    kinds = [e.kind for e in session.events]
    check("event order user→assistant→reasoning→reasoning→call→call→orphan-out→assistant",
          kinds == ["user_msg", "assistant_msg", "reasoning", "reasoning", "tool_call",
                    "tool_call", "tool_output", "assistant_msg"])
    user_ev = session.events[0]
    check("wrapper blocks stripped from user message",
          "<environment_context>" not in (user_ev.text or "") and "<recommended_plugins>" not in (user_ev.text or ""))
    check("user text survives stripping", "帮我分析一下这个报错" in (user_ev.text or ""))
    check("encrypted reasoning placeholder", session.events[2].text == "[思维链已加密，跳过]")
    check("plaintext reasoning summary kept", session.events[3].text == "用户想看报错分析")
    paired = [e for e in session.events if e.kind == "tool_call" and e.tool_name == "apply_patch"][0]
    check("tool output paired by call_id", paired.tool_output is not None and "success" in paired.tool_output)
    unpaired = [e for e in session.events if e.kind == "tool_output"]
    check("orphan output appended as separate event", len(unpaired) == 1)

    def _expect_lookup_error(src, sid):
        try:
            src.read_session(sid)
            return False
        except LookupError:
            return True

    check("lookup of missing session raises LookupError", _expect_lookup_error(src, "00000000-0000-0000-0000-000000000000"))

    # --- render fixtures: budgets, floor rule, file list, footer ---
    from sources import Event, Session, SessionMeta
    from recover import render_session, _truncate, _file_changes

    def make_turn(user_text, n_calls=0):
        evs = [Event(kind="user_msg", role="user", text=user_text)]
        for i in range(n_calls):
            evs.append(Event(kind="tool_call", text="exec", tool_args='{"cmd":"pwd"}', tool_output="x" * 3000))
        evs.append(Event(kind="assistant_msg", role="assistant", text="完成。" + "细节" * 50))
        return evs

    mega_events = []
    for t in range(8):
        mega_events += make_turn("第%d轮任务" % t, n_calls=2)
    # turn 9: the mega-turn (38 calls ≈ 目标「额度耗尽被杀」形态), newest
    mega_events += make_turn("最后一轮：大批量执行", n_calls=38)
    mega = Session(meta=SessionMeta(id=sid_modern, cwd="/x"), events=mega_events)
    out = render_session(mega, recent=10)
    check("newest turn always kept (floor rule)", "最后一轮：大批量执行" in out)
    check("oldest turns dropped when over budget", "第0轮任务" not in out)
    check("render non-empty", len(out) > 1000)
    check("footer truncation stats present", "截断统计" in out)
    check("truncated tool output carries marker", "…(截断)" in out)
    check("file list from apply_patch args", "src/a.py" in _file_changes([
        Event(kind="tool_call", text="apply_patch",
              tool_args='{"file_path": "src/a.py", "patch": "--- a/src/a.py\\n+++ b/src/a.py"}')]))
    check("file list dedup", len(_file_changes([
        Event(kind="tool_call", text="apply_patch", tool_args='{"file_path": "a.py"}'),
        Event(kind="tool_call", text="write", tool_args='{"file_path": "a.py"}')])) == 1)
    check("_truncate appends marker", _truncate("abcdef", 3) == "abc…(截断)")
    check("_truncate short text untouched", _truncate("abc", 5) == "abc")

    hist_out = render_session(Session(meta=SessionMeta(id="h"), events=mega_events), recent=2)
    check("history zone compresses old turns", "第0轮任务" in hist_out)

    if failures:
        print("SELF-TEST FAILED: " + ", ".join(failures))
        return 1
    print("SELF-TEST PASSED")
    return 0


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "self-test":
        return run_self_test()
    if cmd == "list":
        return cmd_list(20)
    if cmd == "show" and len(argv) >= 3:
        recent = 10
        if "--recent" in argv:
            try:
                recent = int(argv[argv.index("--recent") + 1])
            except (ValueError, IndexError):
                print("用法：show <session-id> [--recent N]")
                return 2
        return cmd_show(argv[2], recent)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
