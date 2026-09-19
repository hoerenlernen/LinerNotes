#!/usr/bin/env bash
set -euo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HIER"
PYTHON="${LINER_PYTHON:-$HIER/.venv/bin/python}"
exec "$PYTHON" bin/enrich.py --quellen musicbrainz,coverart,wikipedia,lastfm "$@"
