#!/bin/sh
# Resolve the AgentRecovery binary (cache, local build, or GitHub Release)
# then exec it. No python3.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
VERSION=0.4.0
if [ -f "$ROOT/VERSION" ]; then
  VERSION=$(tr -d ' \n' < "$ROOT/VERSION")
elif [ -f "$ROOT/../VERSION" ]; then
  VERSION=$(tr -d ' \n' < "$ROOT/../VERSION")
fi
REPO="HelloiOS2014/AgentRecovery"

if [ -n "$AGENT_RECOVERY_BIN" ] && [ -x "$AGENT_RECOVERY_BIN" ]; then
  exec "$AGENT_RECOVERY_BIN" "$@"
fi

# Dev: go build -o scripts/recover ./cmd/recover
if [ -x "$ROOT/recover" ]; then
  exec "$ROOT/recover" "$@"
fi

os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m)
case "$arch" in
  x86_64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
esac
name="recover-${os}-${arch}"

cache="${XDG_CACHE_HOME:-$HOME/.cache}/agent-recovery/v${VERSION}"
bin="$cache/$name"
if [ -x "$bin" ]; then
  exec "$bin" "$@"
fi

url="https://github.com/${REPO}/releases/download/v${VERSION}/${name}"
sums_url="https://github.com/${REPO}/releases/download/v${VERSION}/checksums.txt"
mkdir -p "$cache"
tmp="$cache/$name.partial"
sums="$cache/checksums.txt"

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    echo "❌ 无法下载 AgentRecovery 二进制：需要 curl 或 wget" >&2
    echo "   $1" >&2
    exit 2
  fi
}

if ! fetch "$url" "$tmp"; then
  echo "❌ 无法下载 $url" >&2
  echo "   请检查网络，或设置 AGENT_RECOVERY_BIN 指向本地 recover 二进制。" >&2
  echo "   这不是「没有会话」。" >&2
  rm -f "$tmp"
  exit 2
fi

if fetch "$sums_url" "$sums"; then
  expect=$(grep " ${name}\$" "$sums" | awk '{print $1}')
  if [ -n "$expect" ]; then
    got=$(shasum -a 256 "$tmp" 2>/dev/null | awk '{print $1}')
    if [ -z "$got" ]; then
      got=$(sha256sum "$tmp" | awk '{print $1}')
    fi
    if [ "$got" != "$expect" ]; then
      echo "❌ 二进制校验失败（$name）" >&2
      rm -f "$tmp"
      exit 2
    fi
  fi
fi

chmod 0755 "$tmp"
mv "$tmp" "$bin"
exec "$bin" "$@"
