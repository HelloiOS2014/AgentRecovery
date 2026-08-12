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
    raise NotImplementedError


def cmd_show(session_id: str, recent: int) -> int:
    raise NotImplementedError


def run_self_test() -> int:
    raise NotImplementedError


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
