#!/usr/bin/env bash
# Populate ./device-db with the pinned Harmony IR archive snapshot (CC0-1.0).
# Usage: scripts/fetch-device-db.sh [commit]
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=https://github.com/pickysysadmin/logitech-harmony-ir-archive
COMMIT=${1:-$(cat device-db/ARCHIVE_COMMIT)}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git clone --filter=blob:none --no-checkout "$REPO" "$TMP/archive"
git -C "$TMP/archive" checkout --quiet "$COMMIT"
for f in manifest.json index.json LICENSE index.html codesets devices protocols; do
  rm -rf "device-db/$f"
  mv "$TMP/archive/$f" device-db/
done
echo "device-db populated from $REPO@$COMMIT"
python3 -c "import json;m=json.load(open('device-db/manifest.json'));print(m['generated'],m['counts'])"
