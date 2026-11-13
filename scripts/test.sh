#!/bin/bash
# Run every check: Python tests + lint, dashboard type check, Swift tests.
#   UV_PROJECT_ENVIRONMENT=~/.fleet/venv-dev scripts/test.sh
set -euo pipefail
cd "$(dirname "$0")/.."

UV="$(command -v uv || cat "${FLEET_HOME:-$HOME/.fleet}/uv-path" 2>/dev/null || echo uv)"
echo "→ pytest + ruff"
"$UV" run --quiet pytest -q
"$UV" run --quiet ruff check fleetd tests

echo "→ dashboard (tsc --noEmit)"
npm run -s check

echo "→ swift test"
# With only the Command Line Tools (no Xcode), Swift Testing lives outside the
# default search paths; Xcode (and GitHub's macOS runners) find it on their own.
EXTRA=()
if ! xcodebuild -version >/dev/null 2>&1; then
  F=/Library/Developer/CommandLineTools/Library/Developer/Frameworks
  L=/Library/Developer/CommandLineTools/Library/Developer/usr/lib
  EXTRA=(-Xswiftc -F -Xswiftc "$F" -Xlinker -F -Xlinker "$F" -Xlinker -rpath -Xlinker "$F" -Xlinker -rpath -Xlinker "$L")
fi
swift test --package-path app "${EXTRA[@]}"
echo "✓ all checks passed"
