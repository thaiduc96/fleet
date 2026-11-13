#!/bin/bash
# Install (or update) Fleet.app from the latest GitHub release.
#
#   curl -fsSL https://github.com/thaiduc96/fleet/releases/latest/download/install.sh | bash
#
# Downloads with curl (no quarantine flag, so macOS opens the ad-hoc signed app
# without a Gatekeeper prompt), checks the SHA-256 from checksums.txt, puts
# Fleet.app in /Applications (or ~/Applications) and opens it. An existing
# Fleet install (config, token, peers) is kept and picked up by the app.
# Needs nothing beyond a stock macOS (no Python, no developer tools).
set -euo pipefail

REPO="thaiduc96/fleet"
API="https://api.github.com/repos/$REPO/releases/latest"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "✗ Fleet chỉ hỗ trợ Mac chip Apple (arm64); máy này là $(uname -m)." >&2
  exit 1
fi

echo "→ Tìm bản Fleet mới nhất…"
curl -fsSL "$API" -o "$TMP/release.json"
TAG="$(plutil -extract tag_name raw -o - "$TMP/release.json")"
VER="${TAG#v}"
BASE="https://github.com/$REPO/releases/download/$TAG"
ZIP="Fleet-$VER.zip"

echo "→ Tải $ZIP"
curl -fsSL -o "$TMP/$ZIP" "$BASE/$ZIP"
curl -fsSL -o "$TMP/checksums.txt" "$BASE/checksums.txt"
EXPECTED="$(awk -v f="$ZIP" '$2==f {print $1}' "$TMP/checksums.txt")"
ACTUAL="$(shasum -a 256 "$TMP/$ZIP" | awk '{print $1}')"
[[ -n "$EXPECTED" && "$EXPECTED" == "$ACTUAL" ]] || { echo "✗ Sai checksum, dừng cài đặt." >&2; exit 1; }

ditto -x -k "$TMP/$ZIP" "$TMP/unpacked"
codesign --verify --deep --strict "$TMP/unpacked/Fleet.app"

DEST="/Applications"
[[ -w "$DEST" ]] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
if pgrep -xq Fleet; then
  echo "→ Đóng Fleet đang chạy"
  pkill -TERM -x Fleet || true          # Fleet quits cleanly on SIGTERM (stops its fleetd too)
  for _ in $(seq 1 50); do pgrep -xq Fleet || break; sleep 0.2; done
  pkill -KILL -x Fleet 2>/dev/null || true
fi
rm -rf "$DEST/Fleet.app"
mv "$TMP/unpacked/Fleet.app" "$DEST/Fleet.app"
xattr -dr com.apple.quarantine "$DEST/Fleet.app" 2>/dev/null || true

echo "→ Mở Fleet $VER ($DEST/Fleet.app)"
open "$DEST/Fleet.app"
echo "✓ Xong. Biểu tượng Fleet nằm trên thanh menu (góc trên bên phải)."
