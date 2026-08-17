#!/usr/bin/env python3
"""AgentRecovery (Codex side) — recover Claude Code sessions into Codex.

Usage:
  python3 recover-claude.py list
  python3 recover-claude.py show <session-id|index> [--recent N]
  python3 recover-claude.py self-test

Exit codes (list):
  0 = listed (may be empty — that is a real empty result)
  1 = Claude Code not detected (~/.claude/projects missing)
  2 = permission/sandbox blocked reading ~/.claude — NOT "no sessions"
"""
import os
import sys
from typing import List, Optional

from core import (DEFAULT_RECENT, Event, FILE_TOOL_HINTS_CLAUDE, FILELIST_CAP,
                  Session, SessionMeta, render_session)
from sources import SOURCES

ARCHIVE_REL = ".recover-handoff"


def _is_current(cwd: Optional[str], cur: str) -> bool:
    """True when a session's original cwd is the directory we run in."""
    if not cwd:
        return False
    return os.path.realpath(cwd) == os.path.realpath(cur)


def _sort_by_current(metas: List[SessionMeta], cur: str) -> List[SessionMeta]:
    metas.sort(key=lambda m: _is_current(m.cwd, cur), reverse=True)
    return metas


def cmd_list(limit: int) -> int:
    from sources.claude import ClaudeSource, default_projects_dir

    src = ClaudeSource()
    cur = os.getcwd()
    try:
        metas = _sort_by_current(src.list_sessions(limit), cur)
    except PermissionError:
        print("❌ 无权限读取 %s —— 沙箱可能拦截了对 ~/.claude 的访问。"
              "请批准 python3 的磁盘读取权限后重试。" % default_projects_dir())
        return 2
    except OSError as err:
        print("❌ 读取 %s 失败：%s" % (default_projects_dir(), err))
        return 2
    if not metas:
        print("⚠️ 未检测到 Claude Code 会话（%s 不存在或为空）。" % default_projects_dir())
        print("   若你确定安装过 Claude Code 并工作过，请检查沙箱是否拦住了对 ~/.claude 的读取。")
        return 1
    print("[claude] 最近 %d 个会话：输入序号或粘贴完整 session ID（* = 当前项目）" % len(metas))
    for i, m in enumerate(metas, 1):
        title = m.title or "无标题"
        mark = "*" if _is_current(m.cwd, cur) else " "
        print("%s%3d. %-28s %s  cwd=%s  (%s)" % (
            mark, i, title[:28], (m.updated_at or "")[:16], m.cwd or "?", m.id))
    print("\n用法：@recover-claude <序号> 或直接给完整 session ID")
    return 0


def cmd_show(session_id: str, recent: int) -> int:
    from sources.claude import ClaudeSource

    src = ClaudeSource()
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
    except PermissionError:
        print("❌ 无权限读取 ~/.claude —— 沙箱可能拦截了对它的访问，请批准磁盘读取权限。")
        return 2
    if session.meta.cwd and not _is_current(session.meta.cwd, os.getcwd()):
        print("⚠️ 此会话来自其他项目：%s（当前目录：%s），路径请注意核对"
              % (session.meta.cwd, os.getcwd()))
    text = render_session(session, recent, file_hints=FILE_TOOL_HINTS_CLAUDE)
    print(text)
    try:
        os.makedirs(ARCHIVE_REL, mode=0o700, exist_ok=True)
        path = os.path.join(ARCHIVE_REL, session_id + ".md")
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o600)
        print("\n[存档] %s（工作区内，沙箱可写）" % path)
    except OSError as err:
        print("[警告] 存档失败：%s（不影响已输出的恢复上下文）" % err)
    return 0


def run_self_test() -> int:
    import json
    import tempfile

    failures = []

    def check(name: str, cond: bool) -> None:
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    from sources.claude import ClaudeSource
    from sources import Event, Session, SessionMeta

    tmp = tempfile.mkdtemp(prefix="ar-codex-self-test-")
    sid = "01234567-89ab-cdef-0123-456789abcdef"
    sid2 = "fedcba98-7654-3210-fedc-ba9876543210"
    proj = os.path.join(tmp, "projects", "-Users-JOYY-code-demo")
    os.makedirs(os.path.join(proj, "subagents"))
    modern = os.path.join(proj, sid + ".jsonl")
    os.makedirs(os.path.join(tmp, "projects", "-Users-x"))
    other = os.path.join(tmp, "projects", "-Users-x", sid2 + ".jsonl")
    # non-session files and subagent files must be ignored
    with open(os.path.join(proj, "not-a-session.jsonl"), "w") as fh:
        fh.write("{}\n")
    with open(os.path.join(proj, "subagents", "agent-x.jsonl"), "w") as fh:
        fh.write("{}\n")
    with open(modern, "w") as fh:
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "旧标题"}) + "\n")
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "分析报错", "sessionId": sid}) + "\n")
        fh.write(json.dumps({"type": "user", "cwd": "/Users/JOYY/code/demo", "timestamp": "2026-08-13T10:00:00Z",
                             "message": {"role": "user", "content": "帮我看看这个报错"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "cwd": "/Users/JOYY/code/demo",
                             "message": {"id": "m1", "role": "assistant", "model": "deepseek-v4-flash",
                                         "content": [{"type": "thinking", "thinking": "先看错误"}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"id": "m1", "role": "assistant",
                                         "content": [{"type": "text", "text": "我来检查"}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"id": "m1", "role": "assistant",
                                         "content": [{"type": "tool_use", "id": "call_1", "name": "Read",
                                                      "input": {"file_path": "src/a.py"}}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"id": "m2", "role": "assistant",
                                         "content": [{"type": "tool_use_2", "id": "call_2", "name": "Subagent",
                                                      "input": {}}]}}) + "\n")
        fh.write(json.dumps({"type": "user", "message": {"role": "user",
                             "content": [{"type": "tool_result", "tool_use_id": "call_1",
                                          "content": "file content"}]}}) + "\n")
        fh.write(json.dumps({"type": "user", "isMeta": True, "message": {"role": "user",
                             "content": "meta dump"}}) + "\n")
        fh.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_success"}}) + "\n")
        fh.write(json.dumps({"type": "mystery-record", "x": 1}) + "\n")
        fh.write("not-json\n")
        fh.write(json.dumps({"type": "user", "message": {"role": "user",
                             "content": "<command-name>/compact</command-name>"}}) + "\n")
        fh.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                             "timestamp": "2026-08-13T11:00:00Z"}) + "\n")
        fh.write(json.dumps({"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
                             "message": {"role": "user", "content": "This session is being continued…"}}) + "\n")
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "继续改"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"id": "m3", "role": "assistant",
                             "content": [{"type": "tool_use", "id": "call_3", "name": "Write",
                                          "input": {"file_path": "src/b.py"}}]}}) + "\n")
        fh.write(json.dumps({"type": "user", "message": {"role": "user",
                             "content": [{"type": "tool_result", "tool_use_id": "call_3",
                                          "content": "<persisted-output>…"}]}}) + "\n")
    with open(other, "w") as fh:
        fh.write(json.dumps({"type": "user", "cwd": "/Users/x",
                             "message": {"role": "user", "content": "另一个项目"}}) + "\n")

    from datetime import datetime
    for p, iso in [(modern, "2026-08-13T12:00:00"), (other, "2026-08-12T08:00:00")]:
        ts = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").timestamp()
        os.utime(p, (ts, ts))

    src = ClaudeSource(projects_dir=os.path.join(tmp, "projects"))
    metas = src.list_sessions(limit=20)
    ids = [m.id for m in metas]
    check("list finds both sessions", sid in ids and sid2 in ids)
    check("list ignores subagent/non-session files", len(ids) == 2)
    check("list sorted by mtime desc", metas[0].id == sid)
    by_id = {m.id: m for m in metas}
    check("title last wins", by_id[sid].title == "分析报错")
    check("cwd from record", by_id[sid].cwd == "/Users/JOYY/code/demo")
    check("model set collected", by_id[sid].model == "deepseek-v4-flash")

    s = src.read_session(sid)
    kinds = [e.kind for e in s.events]
    check("events after reset: user(继续改)→call(输出配对)",
          kinds == ["user_msg", "tool_call"])
    check("compacted flag set", s.compacted is True)
    check("compact boundary reset drops pre-compact history",
          "帮我看看这个报错" not in [e.text for e in s.events])
    check("tool_use_2 skipped", not any(e.tool_name == "Subagent" for e in s.events))
    check("isMeta skipped", "meta dump" not in [e.text or "" for e in s.events])
    check("local command skipped", all("<command-name>" not in (e.text or "") for e in s.events))
    check("persisted stub detected", any("<persisted-output>" in (e.tool_output or "") for e in s.events))

    # pre-reset fixture lives in its own session file: pairing + split records
    sid3 = "11111111-2222-3333-4444-555555555555"
    p3 = os.path.join(proj, sid3 + ".jsonl")
    with open(p3, "w") as fh:
        fh.write(json.dumps({"type": "user", "cwd": "/Users/JOYY/code/demo",
                             "message": {"role": "user", "content": "帮我看看这个报错"}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"id": "m1", "role": "assistant", "model": "gpt-5",
                                         "content": [{"type": "thinking", "thinking": "先看错误"}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"id": "m1", "role": "assistant",
                                         "content": [{"type": "text", "text": "我来检查"}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"id": "m1", "role": "assistant",
                                         "content": [{"type": "tool_use", "id": "call_1", "name": "Read",
                                                      "input": {"file_path": "src/a.py"}}]}}) + "\n")
        fh.write(json.dumps({"type": "user", "message": {"role": "user",
                             "content": [{"type": "tool_result", "tool_use_id": "call_1",
                                          "content": "file content"}]}}) + "\n")
    s3 = src.read_session(sid3)
    k3 = [e.kind for e in s3.events]
    check("split assistant records: user→reasoning→asst→call",
          k3 == ["user_msg", "reasoning", "assistant_msg", "tool_call"])
    check("tool output paired by tool_use_id", s3.events[3].tool_output == "file content")
    check("model collected", s3.meta.model == "gpt-5")
    check("persisted stub detected", any("<persisted-output>" in (e.tool_output or "") for e in s.events))
    check("unknown record warned", any("mystery-record" in w for w in s.warnings))
    check("bad line warned", any("坏行" in w for w in s.warnings))
    check("attachment skipped+warned", any("attachment" in w for w in s.warnings))

    from core import render_session
    out = render_session(s, recent=10, file_hints=FILE_TOOL_HINTS_CLAUDE)
    check("render contains continuation goal", "继续改" in out)
    check("render flags compaction", "已压缩" in out)
    check("claude hints find Write file", "src/b.py" in out)
    from core import _file_changes
    check("claude hints catch Write", len(_file_changes(
        [Event(kind="tool_call", text="Write", tool_args='{"file_path": "x.py"}')],
        FILE_TOOL_HINTS_CLAUDE)) == 1)
    check("claude hints catch NotebookEdit", len(_file_changes(
        [Event(kind="tool_call", text="NotebookEdit", tool_args='{"file_path": "x.ipynb"}')],
        FILE_TOOL_HINTS_CLAUDE)) == 1)

    def _expect_lookup(src, sid):
        try:
            src.read_session(sid)
            return False
        except LookupError:
            return True

    check("lookup of missing session raises LookupError",
          _expect_lookup(src, "00000000-0000-0000-0000-000000000000"))

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
        recent = DEFAULT_RECENT
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
