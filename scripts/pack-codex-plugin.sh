#!/bin/sh
# Pack the shared rendering core into the Codex plugin, verify byte-identical,
# and run both sides' self-tests. Run after any change to scripts/core.py
# (and before `codex plugin marketplace upgrade` / republish).
set -eu
cd "$(dirname "$0")/.."

SRC=scripts/core.py
DST=hosts/codex/plugins/recover-claude/scripts/core.py

echo "== syncing $SRC -> $DST"
cp "$SRC" "$DST"

echo "== sha256"
S1=$(shasum -a 256 "$SRC" | cut -d' ' -f1)
S2=$(shasum -a 256 "$DST" | cut -d' ' -f1)
echo "  $S1"
[ "$S1" = "$S2" ] || { echo "FAIL: checksums differ after copy"; exit 1; }
echo "  match"

echo "== self-test: Claude Code side"
python3 scripts/recover.py self-test >/dev/null && echo "  ok" || { echo "FAIL"; exit 1; }

echo "== self-test: Codex side"
(cd hosts/codex/plugins/recover-claude/scripts && python3 recover-claude.py self-test >/dev/null && echo "  ok") || { echo "FAIL"; exit 1; }

echo "PACK OK"
