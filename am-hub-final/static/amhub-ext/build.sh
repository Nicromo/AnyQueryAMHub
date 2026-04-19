#!/usr/bin/env bash
# Ре-билд amhub-ext: записать build-info.json и пересобрать zip.
# Использование: ./am-hub-final/static/amhub-ext/build.sh
#
# Результат:
#   - am-hub-final/static/amhub-ext/build-info.json (с UTC timestamp + git SHA)
#   - am-hub-final/static/amhub-ext.zip (ре-зиплен вместе с build-info.json)
set -euo pipefail

EXT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATIC_DIR="$(cd "$EXT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$STATIC_DIR/../.." && pwd)"

# 1. Build info
VERSION="$(python3 -c "import json; print(json.load(open('$EXT_DIR/manifest.json'))['version'])")"
BUILT_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
COMMIT="$(cd "$REPO_ROOT" && git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"

cat > "$EXT_DIR/build-info.json" <<EOF
{
  "version": "$VERSION",
  "built_at": "$BUILT_AT",
  "commit": "$COMMIT"
}
EOF

echo "[build] build-info: $VERSION · $BUILT_AT · $COMMIT"

# 2. Re-zip
cd "$STATIC_DIR"
rm -f amhub-ext.zip
cd amhub-ext
zip -qr "../amhub-ext.zip" . -x "*.DS_Store" "*.map"

SIZE_KB="$(du -k "$STATIC_DIR/amhub-ext.zip" | awk '{print $1}')"
echo "[build] amhub-ext.zip written (${SIZE_KB} KB)"
