#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
VERSION=$(tr -d ' \n' < VERSION)
mkdir -p dist
for pair in darwin/arm64 darwin/amd64 linux/arm64 linux/amd64; do
  OS=${pair%/*}
  ARCH=${pair#*/}
  echo "build recover-${OS}-${ARCH}"
  CGO_ENABLED=0 GOOS="$OS" GOARCH="$ARCH" go build -trimpath \
    -ldflags "-s -w -X main.Version=${VERSION}" \
    -o "dist/recover-${OS}-${ARCH}" ./cmd/recover
done
(
  cd dist
  shasum -a 256 recover-darwin-arm64 recover-darwin-amd64 recover-linux-arm64 recover-linux-amd64 > checksums.txt
)
echo "OK $VERSION"
ls -l dist
