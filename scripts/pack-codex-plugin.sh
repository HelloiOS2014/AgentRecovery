#!/bin/sh
# Pack the shared core (render core + both session parsers) into the Codex
# plugin, verify byte-identical, and run both sides' self-tests. Run after any
# change to scripts/core.py or scripts/sources/ (and before republishing).
set -eu
cd "$(dirname "$0")/.."

SRC_ROOT=scripts
DST_ROOT=hosts/codex/plugins/recover/scripts

echo "== syncing core + sources -> Codex plugin"
for f in core.py sources/__init__.py sources/codex.py sources/claude.py; do
  cp "$SRC_ROOT/$f" "$DST_ROOT/$f"
done

echo "== sha256"
for f in core.py sources/__init__.py sources/codex.py sources/claude.py; do
  S1=$(shasum -a 256 "$SRC_ROOT/$f" | cut -d' ' -f1)
  S2=$(shasum -a 256 "$DST_ROOT/$f" | cut -d' ' -f1)
  [ "$S1" = "$S2" ] || { echo "FAIL: $f differs after copy"; exit 1; }
  echo "  $S1  $f"
done

echo "== self-test: Claude Code side"
python3 scripts/recover.py self-test >/dev/null && echo "  ok" || { echo "FAIL"; exit 1; }

echo "== self-test: Codex side"
(cd hosts/codex/plugins/recover/scripts && python3 recover.py self-test >/dev/null && echo "  ok") || { echo "FAIL"; exit 1; }

echo "PACK OK"
