#!/bin/bash
#
# Prüft die Datenmodelle und den Ereignisstrom gegen einen LAUFENDEN
# Server. Läuft auf dem Mac, ohne tvOS-Simulator.
#
# Warum es das gibt: Ein erfolgreicher Compilerlauf sagt nichts darüber,
# ob die Strukturen zu dem passen, was der Server tatsächlich schickt.
# Genau dort entstehen die Fehler – ein Feld, das mal fehlt, ein Typ, der
# anders ist als angenommen. Nach jeder Änderung an der API hier prüfen.
#
#   ./pruefen.sh [http://localhost:5060]
#
set -euo pipefail
BASIS="${1:-http://localhost:5060}"
HIER="$(cd "$(dirname "$0")" && pwd)"
QUELLE="$HIER/../LinerNotes"
ARBEIT="$(mktemp -d)"
trap 'rm -rf "$ARBEIT"' EXIT

echo "=== Modelle gegen $BASIS ==="
mkdir -p "$ARBEIT/modelle"
cp "$QUELLE/Modelle.swift" "$ARBEIT/modelle/"
cp "$HIER/modelltest.swift" "$ARBEIT/modelle/main.swift"
( cd "$ARBEIT/modelle" && swiftc -O Modelle.swift main.swift -o test )
"$ARBEIT/modelle/test" "$BASIS"
ERG1=$?

echo
echo "=== Ereignisstrom gegen $BASIS ==="
mkdir -p "$ARBEIT/strom"
cp "$QUELLE/Modelle.swift" "$QUELLE/ApiClient.swift" "$ARBEIT/strom/"
cp "$HIER/stromtest.swift" "$ARBEIT/strom/main.swift"
( cd "$ARBEIT/strom" && swiftc -O Modelle.swift ApiClient.swift main.swift -o test )
"$ARBEIT/strom/test" "$BASIS"
ERG2=$?

echo
if [ $ERG1 -eq 0 ] && [ $ERG2 -eq 0 ]; then
    echo "Alles in Ordnung."
    exit 0
fi
echo "Es gab Fehler (Modelle: $ERG1, Strom: $ERG2)."
exit 1
