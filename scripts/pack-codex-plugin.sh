#!/bin/sh
# Pack the shared core (render core + all session parsers) into the Codex
# plugin and the Pi package, verify byte-identical, and run every host's
# self-test. Run after any change to scripts/core.py or scripts/sources/
# (and before republishing).
set -eu
cd "$(dirname "$0")/.."

SRC_ROOT=scripts
DST_CODEX=hosts/codex/plugins/recover/scripts
DST_PI=hosts/pi/package/scripts

FILES="core.py sources/__init__.py sources/codex.py sources/claude.py sources/pi.py"

echo "== syncing core + sources -> Codex plugin + Pi package"
for DST in "$DST_CODEX" "$DST_PI"; do
  mkdir -p "$DST/sources"
  for f in $FILES; do
    cp "$SRC_ROOT/$f" "$DST/$f"
  done
done

echo "== sha256"
for f in $FILES; do
  S1=$(shasum -a 256 "$SRC_ROOT/$f" | cut -d' ' -f1)
  for DST in "$DST_CODEX" "$DST_PI"; do
    S2=$(shasum -a 256 "$DST/$f" | cut -d' ' -f1)
    [ "$S1" = "$S2" ] || { echo "FAIL: $f differs in $DST"; exit 1; }
  done
  echo "  $S1  $f"
done

echo "== self-test: Claude Code side"
python3 scripts/recover.py self-test >/dev/null && echo "  ok" || { echo "FAIL"; exit 1; }

echo "== self-test: Codex side"
(cd hosts/codex/plugins/recover/scripts && python3 recover.py self-test >/dev/null && echo "  ok") || { echo "FAIL"; exit 1; }

echo "== self-test: Pi side"
(cd hosts/pi/package/scripts && python3 recover.py self-test >/dev/null && echo "  ok") || { echo "FAIL"; exit 1; }

echo "PACK OK"
