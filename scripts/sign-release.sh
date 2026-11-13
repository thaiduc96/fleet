#!/bin/bash
# Sign a published release so Fleet.app will install it as an update.
#
#   scripts/sign-release.sh v1.2.3
#
# The release workflow builds and publishes Fleet-<ver>.zip without a signature;
# apps ignore a release until Fleet-<ver>.zip.sig exists. The private key never
# leaves this Mac (~/.fleet/release-signing-key, or FLEET_SIGNING_KEY_FILE).
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${1:?usage: scripts/sign-release.sh vX.Y.Z}"
VER="${TAG#v}"
ZIP="Fleet-$VER.zip"
KEY="${FLEET_SIGNING_KEY_FILE:-$HOME/.fleet/release-signing-key}"
PUB="$(sed -n 's/.*static let updatePublicKey = "\(.*\)".*/\1/p' app/Sources/Fleet/Env.swift)"
[[ -r "$KEY" ]] || { echo "no signing key at $KEY" >&2; exit 1; }
[[ -n "$PUB" ]] || { echo "public key not found in app/Sources/Fleet/Env.swift" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "→ downloading $ZIP from release $TAG"
gh release download "$TAG" --pattern "$ZIP" --dir "$TMP"
gh release download "$TAG" --pattern checksums.txt --dir "$TMP"
(cd "$TMP" && grep " $ZIP\$" checksums.txt | shasum -a 256 -c -)

echo "→ signing"
swift scripts/sign-release.swift "$TMP/$ZIP" "$KEY" "$PUB" > "$TMP/$ZIP.sig"
gh release upload "$TAG" "$TMP/$ZIP.sig" --clobber
echo "✓ $TAG signed; Fleet.app on every Mac can now update to $VER"
