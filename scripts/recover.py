#!/usr/bin/env python3
"""AgentRecovery CLI — recover sessions from other agents into Claude Code.

Usage:
  python3 recover.py list [--limit N]
  python3 recover.py show <session-id> [--recent N]
  python3 recover.py self-test
"""
import sys
from typing import List


def cmd_list(limit: int) -> int:
    from sources import SOURCES

    for name, src_cls in SOURCES.items():
        metas = src_cls().list_sessions(limit)
        print("[%s] 最近 %d 个会话：输入序号或粘贴完整 session ID" % (name, len(metas)))
        for i, m in enumerate(metas, 1):
            title = m.title or "无标题"
            print("%3d. %-28s %s  cwd=%s  (%s…)" % (
                i, title[:28], (m.updated_at or "")[:16], m.cwd or "?", m.id[:8]))
        print("\n用法：/recover <序号> 或 /recover <完整session ID>")
        return 0
    return 1


def cmd_show(session_id: str, recent: int) -> int:
    raise NotImplementedError


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
    sid_modern = "019ff01c-a6cd-73f0-a62a-573d6262843a"
    sid_legacy = "3e1a6ed1-6eba-45af-a755-922395d6feb2"
    sid_arch = "019cb8bd-48b2-7913-8082-1b75d088f640"
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
