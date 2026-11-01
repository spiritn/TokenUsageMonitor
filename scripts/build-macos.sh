#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
[ "$(uname -s)" = Darwin ] || { echo '首版仅支持 macOS' >&2; exit 1; }
APP=".build/Token Monitor.app"
ARCH="$(uname -m)"
case "$ARCH" in x86_64|arm64) ;; *) echo "不支持的架构：$ARCH" >&2; exit 1;; esac
xcrun --find swiftc >/dev/null 2>&1 || { echo '请先安装 Xcode Command Line Tools：xcode-select --install' >&2; exit 1; }
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp native/Info.plist "$APP/Contents/Info.plist"
xcrun swiftc -target "${ARCH}-apple-macosx11.0" -module-cache-path "${TMPDIR:-/tmp}/token-monitor-swift-cache" \
  native/TokenMonitor.swift -o "$APP/Contents/MacOS/TokenMonitor" -framework Cocoa -framework SwiftUI
/usr/bin/codesign --force --sign - "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
