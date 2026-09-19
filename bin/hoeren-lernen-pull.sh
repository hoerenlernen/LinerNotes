#!/bin/bash
# Hoeren-Lernen-Repo aktualisieren. NUR lesend benutzt, nie geschrieben:
# die Texte werden im Repo gepflegt, Liner liest sie.
#
# Das Repo steht unter CC BY-NC-SA 4.0. Die Namensnennung erscheint an
# jeder Stelle, an der Liner daraus zitiert - darum darf dieser Ordner
# nie in die Musik-Tags wandern.
set -euo pipefail
# Pfade kommen aus der Umgebung. Der Log liegt im Datenverzeichnis des
# Projekts, das aus dem Ort dieses Skripts abgeleitet wird.
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PFAD="${HOERENLERNEN_PFAD:-}"
LOG="${HOERENLERNEN_LOG:-$HIER/data/hoeren-lernen-pull.log}"
if [ -z "$PFAD" ]; then
  echo "HOERENLERNEN_PFAD ist nicht gesetzt - nichts zu tun." >&2
  exit 0
fi

mkdir -p "$(dirname "$LOG")"
{
  echo "--- $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ ! -d "$PFAD/.git" ]; then
    echo "  kein Git-Repo unter dem angegebenen Pfad - nichts zu tun"
    exit 0
  fi
  cd "$PFAD" || exit 1
  VORHER=$(git rev-parse HEAD 2>/dev/null)
  git -c gc.auto=0 pull --ff-only --quiet 2>&1 | sed 's/^/  /'
  NACHHER=$(git rev-parse HEAD 2>/dev/null)
  if [ "$VORHER" != "$NACHHER" ]; then
    echo "  neu: $VORHER -> $NACHHER"
    git log --oneline "$VORHER..$NACHHER" 2>/dev/null | sed 's/^/    /'
    # Liner neu einlesen lassen. Der Dienst liest beim Start und auf
    # Anforderung; ein Neustart ist unnoetig.
    curl -fsS -X POST "http://127.0.0.1:${LINER_PORT:-5060}/api/hoerenlernen/neu-einlesen" \
      >/dev/null 2>&1 && echo "  Liner hat neu eingelesen" \
      || echo "  Hinweis: Liner nicht erreichbar, liest beim naechsten Start"
  else
    echo "  unveraendert ($NACHHER)"
  fi
} >> "$LOG" 2>&1
