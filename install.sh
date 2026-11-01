#!/bin/sh
set -eu
VERSION=0.1.0
REPOSITORY=spiritn/TokenUsageMonitor
[ "$(uname -s)" = Darwin ] || { echo '首版仅支持 macOS' >&2; exit 1; }
[ "$(id -u)" -ne 0 ] || { echo '请使用当前用户安装，无需 sudo' >&2; exit 1; }
PYTHON="${TOKEN_MONITOR_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if "$candidate" -c 'import sys; assert sys.version_info >= (3, 10); import zoneinfo' 2>/dev/null; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "$PYTHON" ] && "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10); import zoneinfo' 2>/dev/null || {
  echo '需要 Python 3.10+。有 Homebrew 时可运行：brew install python@3.13；也可用 TOKEN_MONITOR_PYTHON 指定现有版本。' >&2; exit 1;
}
SOURCE="${TOKEN_MONITOR_SOURCE_DIR:-}"
if [ -z "$SOURCE" ] && [ -f "$(dirname "$0")/VERSION" ]; then
  SOURCE="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
fi
if [ -n "$SOURCE" ]; then
  exec "$PYTHON" "$SOURCE/scripts/install.py" "$@"
fi
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/token-monitor-download.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
ASSET="token-monitor-${VERSION}.tar.gz"
URL="https://github.com/${REPOSITORY}/releases/download/v${VERSION}"
curl --fail --location --proto '=https' --tlsv1.2 "$URL/$ASSET" -o "$STAGE/$ASSET"
curl --fail --location --proto '=https' --tlsv1.2 "$URL/SHA256SUMS" -o "$STAGE/SHA256SUMS"
(cd "$STAGE" && /usr/bin/shasum -a 256 -c SHA256SUMS)
tar -xzf "$STAGE/$ASSET" -C "$STAGE"
"$PYTHON" "$STAGE/token-monitor-${VERSION}/scripts/install.py" "$@"
