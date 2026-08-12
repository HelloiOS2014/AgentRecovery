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
