#!/usr/bin/env bash
set -euo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HIER"
PYTHON="${LINER_PYTHON:-$HIER/.venv/bin/python}"
# Fehler stoppen den Lauf; Ausgabe wird von Terminal, cron oder journald erfasst.
# Externe Anreicherung ist ein separater, bewusster Aufruf von enrich-alle.sh.
nice -n 19 "$PYTHON" bin/index.py
nice -n 19 "$PYTHON" bin/dr-scan.py --jobs 2
