#!/bin/bash
# Build, sign and publish a Fleet release from this Mac.
#
#   scripts/release.sh            # version from ./VERSION, tag vX.Y.Z on HEAD
#
# Runs every check, builds dist/Fleet-X.Y.Z.zip, signs it with the Ed25519
# release key (~/.fleet/release-signing-key; it never leaves this Mac), then
# creates the GitHub release with the zip, checksums.txt, install.sh and the
# .sig in one go — so there is never an unsigned release that apps could see.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(tr -d '[:space:]' < VERSION)"
TAG="v$VERSION"
ZIP="Fleet-$VERSION.zip"
KEY="${FLEET_SIGNING_KEY_FILE:-$HOME/.fleet/release-signing-key}"
PUB="$(sed -n 's/.*static let updatePublicKey = "\(.*\)".*/\1/p' app/Sources/Fleet/Env.swift)"

[[ -z "$(git status --porcelain)" ]] || { echo "✗ working tree not clean" >&2; exit 1; }
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || { echo "✗ release from main" >&2; exit 1; }
git fetch -q origin main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || { echo "✗ main is not in sync with origin" >&2; exit 1; }
gh release view "$TAG" >/dev/null 2>&1 && { echo "✗ $TAG already released; bump VERSION" >&2; exit 1; }
[[ -r "$KEY" ]] || { echo "✗ no signing key at $KEY" >&2; exit 1; }

echo "→ checks"
scripts/test.sh
echo "→ build $VERSION"
scripts/build-app.sh "$VERSION"
echo "→ sign"
swift scripts/sign-release.swift "dist/$ZIP" "$KEY" "$PUB" > "dist/$ZIP.sig"

echo "→ tag + release $TAG"
git tag -a "$TAG" -m "Fleet $VERSION"
git push -q origin "$TAG"
gh release create "$TAG" "dist/$ZIP" "dist/$ZIP.sig" dist/checksums.txt dist/install.sh \
  --title "Fleet $TAG" --generate-notes
echo "✓ Fleet $VERSION released; apps update within 6 hours (or 'Cập nhật tất cả máy')."
