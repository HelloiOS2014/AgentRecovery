#!/bin/sh
# Copy the binary launcher into host packages and run Go self-test.
# Runtime hosts no longer ship Python.
set -eu
cd "$(dirname "$0")/.."

VERSION=$(tr -d ' \n' < VERSION)
echo "== version $VERSION"

for DST in hosts/codex/plugins/recover/scripts hosts/pi/package/scripts; do
  mkdir -p "$DST"
  cp scripts/recover-run.sh "$DST/recover-run.sh"
  cp VERSION "$DST/VERSION"
  chmod +x "$DST/recover-run.sh"
done

echo "== go self-test"
go test ./...
go build -o /tmp/recover-agentrecovery ./cmd/recover
/tmp/recover-agentrecovery self-test >/dev/null && echo "  ok"

echo "PACK OK"
