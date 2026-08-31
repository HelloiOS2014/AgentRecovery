#!/usr/bin/env python3
"""AgentRecovery (Pi side) — recover sessions into Pi.

Usage:
  python3 recover.py list [--self] [--json]
  python3 recover.py show <session-id|index> [--recent N] [--self] [--json]
  python3 recover.py self-test

/recover lists every non-Pi source (Claude Code, Codex, …).
/recover-self lists Pi's own sessions. An exact session ID is
auto-detected across all stores.

Exit codes (list):
  0 = listed (may be empty — that is a real empty result)
  1 = nothing found (target session store absent or empty)
  2 = permission/sandbox blocked reading the store — NOT "no sessions"
"""
import json
import os
import sys
from typing import Dict, List, Optional

from core import (DEFAULT_RECENT, FILE_TOOL_HINTS_CLAUDE, FILE_TOOL_HINTS_CODEX,
                  FILE_TOOL_HINTS_PI, SessionMeta, render_session)
from sources import SOURCES, collect_metas, target_names

ARCHIVE_REL = ".recover-handoff"

SELF = "pi"


def _is_current(cwd: Optional[str], cur: str) -> bool:
    if not cwd:
        return False
    return os.path.realpath(cwd) == os.path.realpath(cur)


def _sort_by_current(metas: List[SessionMeta], cur: str) -> List[SessionMeta]:
    metas.sort(key=lambda m: _is_current(m.cwd, cur), reverse=True)
    return metas


def _instances() -> Dict[str, object]:
    return {name: cls() for name, cls in SOURCES.items()}


def _hints_for(src_name: str) -> tuple:
    if src_name == "claude":
        return FILE_TOOL_HINTS_CLAUDE
    if src_name == "pi":
        return FILE_TOOL_HINTS_PI
    return FILE_TOOL_HINTS_CODEX


def _meta_json(m: SessionMeta, cur: str) -> dict:
    return {
        "source": m.source,
        "id": m.id,
        "title": m.title,
        "cwd": m.cwd,
        "updated_at": m.updated_at,
        "started_at": m.started_at,
        "model": m.model,
        "current": _is_current(m.cwd, cur),
    }


def _fail(msg: str, as_json: bool, code: int) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    else:
        print(msg)
    return code


def cmd_list(limit: int, self_mode: bool = False, as_json: bool = False) -> int:
    srcs = _instances()
    names = target_names(SELF, self_mode)
    cur = os.getcwd()
    metas, blocked = collect_metas(srcs, names, limit)
    metas = _sort_by_current(metas, cur)
    if as_json:
        payload = {
            "ok": bool(metas),
            "sessions": [_meta_json(m, cur) for m in metas],
            "blocked": blocked,
        }
        if not metas:
            payload["error"] = (
                "permission blocked: " + ",".join(blocked) if blocked else "no sessions"
            )
        print(json.dumps(payload, ensure_ascii=False))
        if metas:
            return 0
        return 2 if blocked else 1
    if not metas:
        if blocked:
            print("❌ 无权限读取 %s 会话目录（沙箱/权限拦截，不是空列表）" % "/".join(blocked))
            return 2
        print("未检测到 %s 会话（目录不存在或为空）。" % "/".join(names))
        return 1
    if blocked:
        print("⚠️ 无法读取：%s" % ", ".join(blocked))
    print("[%s] 最近 %d 个会话：输入序号或粘贴完整 session ID（* = 当前项目）" % (
        "/".join(names), len(metas)))
    for i, m in enumerate(metas, 1):
        title = m.title or "无标题"
        mark = "*" if _is_current(m.cwd, cur) else " "
        print("%s%3d. [%s] %-24s %s  cwd=%s  (%s)" % (
            mark, i, m.source or "?", title[:24], (m.updated_at or "")[:16], m.cwd or "?", m.id))
    print("\n用法：/recover <序号> 或 /recover <完整session ID>（跨源自动识别）")
    return 0


def cmd_show(session_id: str, recent: int, self_mode: bool = False, as_json: bool = False) -> int:
    srcs = _instances()
    name: Optional[str] = None
    src = None
    if session_id.isdigit() and int(session_id) >= 1:
        names = target_names(SELF, self_mode)
        metas, _blocked = collect_metas(srcs, names, 20)
        metas = _sort_by_current(metas, os.getcwd())
        if int(session_id) > len(metas):
            return _fail("序号 %s 超出范围（有效 1..%d）" % (session_id, len(metas)), as_json, 1)
        picked = metas[int(session_id) - 1]
        session_id = picked.id
        name, src = picked.source, srcs[picked.source]
    else:
        for cand, inst in srcs.items():
            try:
                inst.read_session(session_id)
            except LookupError:
                continue
            except PermissionError:
                return _fail("❌ 无权限读取 %s 源（沙箱/权限拦截）" % cand, as_json, 2)
            name, src = cand, inst
            break
        if name is None or src is None:
            return _fail("未找到会话 %s（已同时搜索所有会话存储）" % session_id, as_json, 1)
    try:
        session = src.read_session(session_id)
    except LookupError as err:
        return _fail(str(err), as_json, 1)
    except PermissionError:
        return _fail("❌ 无权限读取 %s 源（沙箱/权限拦截）" % name, as_json, 2)
    session.meta.source = name
    extra = []
    if session.meta.cwd and not _is_current(session.meta.cwd, os.getcwd()):
        extra.append("此会话来自其他项目：%s（当前目录：%s），路径请注意核对"
                     % (session.meta.cwd, os.getcwd()))
        if not as_json:
            print("⚠️ " + extra[-1])
    text = render_session(session, recent, file_hints=_hints_for(name))
    archive = None
    try:
        os.makedirs(ARCHIVE_REL, mode=0o700, exist_ok=True)
        path = os.path.join(ARCHIVE_REL, session_id + ".md")
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o600)
        archive = path
    except OSError as err:
        extra.append("存档失败：%s" % err)
    if as_json:
        print(json.dumps({
            "ok": True,
            "source": name,
            "id": session_id,
            "meta": {
                "title": session.meta.title,
                "cwd": session.meta.cwd,
                "model": session.meta.model,
                "started_at": session.meta.started_at,
                "updated_at": session.meta.updated_at,
            },
            "handoff": text,
            "warnings": extra + session.warnings,
            "archive": archive,
            "compacted": session.compacted,
        }, ensure_ascii=False))
        return 0
    print(text)
    if archive:
        print("\n[存档] %s（工作区内，沙箱可写）" % archive)
    else:
        for w in extra:
            if w.startswith("存档失败"):
                print("[警告] %s（不影响已输出的恢复上下文）" % w)
    return 0


def run_self_test() -> int:
    import tempfile

    failures = []

    def check(name: str, cond: bool) -> None:
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    from sources.pi import PiSource

    tmp = tempfile.mkdtemp(prefix="ar-pi-self-test-")
    pid = "01234567-89ab-cdef-0123-456789abcdef"
    pdir = os.path.join(tmp, "--Users-demo--")
    os.makedirs(pdir)
    pfile = os.path.join(pdir, "2026-08-31T10-00-00-000Z_" + pid + ".jsonl")

    def pline(obj):
        return json.dumps(obj, ensure_ascii=False) + "\n"

    with open(pfile, "w") as fh:
        fh.write(pline({"type": "session", "version": 3, "id": pid,
                        "timestamp": "2026-08-31T10:00:00.000Z", "cwd": "/Users/demo"}))
        fh.write(pline({"type": "session_info", "id": "n1", "parentId": None,
                        "timestamp": "2026-08-31T10:00:01.000Z", "name": "修报错"}))
        fh.write(pline({"type": "message", "id": "a1", "parentId": "n1",
                        "timestamp": "2026-08-31T10:00:02.000Z",
                        "message": {"role": "user", "content": [{"type": "text", "text": "主线任务"}]}}))
        fh.write(pline({"type": "message", "id": "a2", "parentId": "a1",
                        "timestamp": "2026-08-31T10:00:03.000Z",
                        "message": {"role": "assistant", "model": "grok-4.6",
                                     "content": [
                                         {"type": "text", "text": "我来写"},
                                         {"type": "toolCall", "id": "call_w", "name": "write",
                                          "arguments": {"path": "src/a.py", "content": "x"}}]}}))
        fh.write(pline({"type": "message", "id": "a3", "parentId": "a2",
                        "timestamp": "2026-08-31T10:00:04.000Z",
                        "message": {"role": "toolResult", "toolCallId": "call_w", "toolName": "write",
                                     "content": [{"type": "text", "text": "wrote"}], "isError": False}}))
        fh.write(pline({"type": "message", "id": "b1", "parentId": "a1",
                        "timestamp": "2026-08-31T10:00:05.000Z",
                        "message": {"role": "user", "content": [{"type": "text", "text": "旁支不要"}]}}))
        fh.write(pline({"type": "message", "id": "a4", "parentId": "a3",
                        "timestamp": "2026-08-31T10:00:07.000Z",
                        "message": {"role": "user", "content": [{"type": "text", "text": "继续主线"}]}}))

    ps = PiSource(sessions_dir=tmp)
    metas = ps.list_sessions(20)
    check("pi list finds session", any(m.id == pid for m in metas))
    check("pi title from session_info", metas and metas[0].title == "修报错")
    sess = ps.read_session(pid)
    texts = [e.text for e in sess.events]
    check("pi leaf drops branch", "旁支不要" not in texts)
    check("pi leaf keeps mainline", "主线任务" in texts and "继续主线" in texts)
    paired = [e for e in sess.events if e.kind == "tool_call" and e.text == "write"]
    check("pi toolCall paired", paired and paired[0].tool_output == "wrote")
    out = render_session(sess, recent=10, file_hints=FILE_TOOL_HINTS_PI)
    check("render includes goal", "继续主线" in out)
    check("render lists write path", "src/a.py" in out)
    check("recover-self is pi only", target_names("pi", True) == ["pi"])
    check("recover lists claude and codex",
          target_names("pi", False) == ["codex", "claude"])

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
        return cmd_list(20, "--self" in argv, "--json" in argv)
    if cmd == "show" and len(argv) >= 3:
        recent = DEFAULT_RECENT
        if "--recent" in argv:
            try:
                recent = int(argv[argv.index("--recent") + 1])
            except (ValueError, IndexError):
                print("用法：show <session-id> [--recent N] [--self] [--json]")
                return 2
        return cmd_show(argv[2], recent, "--self" in argv, "--json" in argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
