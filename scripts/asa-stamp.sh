#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE=/var/lib/asa/version.json
JOURNAL=/home/arduino/asa_journal.py
cd "$REPO"
VERSION="$(cat VERSION 2>/dev/null || echo 0.0.0)"
COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
DIRTY=false
git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null || DIRTY=true
PREV="$(python3 -c "
import json
try: print(json.load(open('$VERSION_FILE')).get('version') or 'none')
except Exception: print('none')" 2>/dev/null || echo none)"
cat > "$VERSION_FILE" << EOF
{"version":"$VERSION","commit":"$COMMIT","branch":"$BRANCH","dirty":$DIRTY,"stamped":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
[ -f "$JOURNAL" ] && python3 "$JOURNAL" note "deploy $VERSION (${COMMIT:0:8}) from $PREV dirty=$DIRTY" > /dev/null
echo "stamped $VERSION (${COMMIT:0:8}) branch $BRANCH dirty=$DIRTY"
[ "$DIRTY" = true ] && echo "WARNING: working tree dirty - running code is not exactly this commit"
exit 0
