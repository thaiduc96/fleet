#!/bin/bash
# Build dist/Fleet.app (Swift menu bar app + a self-contained Python running fleetd)
# and dist/Fleet-<version>.zip + dist/checksums.txt for a GitHub release.
#
#   scripts/build-app.sh            # version from ./VERSION
#   scripts/build-app.sh 1.2.3-dev
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
VERSION="${1:-$(tr -d '[:space:]' < VERSION)}"
DIST="$ROOT/dist"
APP="$DIST/Fleet.app"
PYVER="3.12"

UV="$(command -v uv || cat "${FLEET_HOME:-$HOME/.fleet}/uv-path" 2>/dev/null || true)"
[[ -x "$UV" ]] || { echo "uv not found (https://astral.sh/uv)" >&2; exit 1; }

echo "→ Fleet $VERSION"
rm -rf "$APP" "$DIST/Fleet-"*.zip "$DIST/checksums.txt"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/backend"

echo "→ swift build (release)"
swift build -c release --package-path app --product Fleet
cp "app/.build/release/Fleet" "$APP/Contents/MacOS/Fleet"

echo "→ bundling Python $PYVER"
"$UV" python install --quiet "$PYVER"
PY_BIN="$("$UV" python find --managed-python "$PYVER")"
PY_ROOT="$(cd "$(dirname "$PY_BIN")/.." && pwd -P)"
cp -R "$PY_ROOT" "$APP/Contents/Resources/backend/python"
BPY="$APP/Contents/Resources/backend/python/bin/python3"
# fleetd and its dependencies go straight into the bundled interpreter.
"$UV" pip install --quiet --python "$BPY" --break-system-packages --no-cache "$ROOT"
# Trim what fleetd never uses.
LIB="$APP/Contents/Resources/backend/python/lib/python$PYVER"
rm -rf "$LIB/test" "$LIB/idlelib" "$LIB/tkinter" "$LIB/turtledemo" "$LIB/ensurepip" \
       "$LIB/lib-dynload/_tkinter"* "$APP/Contents/Resources/backend/python/include" \
       "$APP/Contents/Resources/backend/python/share" "$APP/Contents/Resources/backend/python/lib/tcl"* \
       "$APP/Contents/Resources/backend/python/lib/tk"* "$APP/Contents/Resources/backend/python/lib/itcl"* \
       "$APP/Contents/Resources/backend/python/lib/thread"*
find "$APP/Contents/Resources/backend" -name "__pycache__" -type d -prune -exec rm -rf {} +
# Precompile now so the .pyc files are inside the signed bundle (fleetd runs with -B
# and never writes into the app, which would break its code signature).
"$BPY" -I -m compileall -q "$APP/Contents/Resources/backend/python/lib" >/dev/null || true
"$BPY" -I -B -c "import fleetd.main, fastapi, uvicorn, httpx; print('  fleetd import ok')"

echo "→ Info.plist"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.fleet.app</string>
  <key>CFBundleName</key><string>Fleet</string>
  <key>CFBundleDisplayName</key><string>Fleet</string>
  <key>CFBundleExecutable</key><string>Fleet</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleIconFile</key><string>Fleet</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Fleet — Claude Code sessions across your Macs</string>
</dict>
</plist>
EOF
if [[ -f app/Fleet.icns ]]; then cp app/Fleet.icns "$APP/Contents/Resources/Fleet.icns"; fi

echo "→ codesign (ad-hoc)"
# Files under an iCloud-synced ~/Documents carry Finder/iCloud xattrs that codesign rejects.
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "→ zip + checksums"
# No resource forks / extended attributes in the archive: they would break the
# signature check on the receiving Mac.
ditto -c -k --norsrc --noextattr --keepParent "$APP" "$DIST/Fleet-$VERSION.zip"
CHECK="$(mktemp -d)"
ditto -x -k "$DIST/Fleet-$VERSION.zip" "$CHECK"
codesign --verify --deep --strict "$CHECK/Fleet.app" || { echo "✗ unpacked app fails codesign" >&2; exit 1; }
rm -rf "$CHECK"
cp scripts/install.sh "$DIST/install.sh"
(cd "$DIST" && shasum -a 256 "Fleet-$VERSION.zip" install.sh > checksums.txt)
du -sh "$APP" "$DIST/Fleet-$VERSION.zip" | sed 's#'"$DIST"'/##'
echo "✓ $DIST/Fleet-$VERSION.zip"
