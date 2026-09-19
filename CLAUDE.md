# Liner Notes: Projektregeln

- Architektur und Installation: README.md und docs/installation.md.
- Server in app/, Werkzeuge in bin/, Web in static/, tvOS in tvos/.
- Musikdateien sind für den Server und normale Analysewerkzeuge nur lesbar.
  Ausnahme: das ausdrücklich manuell ausgeführte tagvault restore --apply.
- Keine privaten Konfigurationen, Datenbanken, Tokens oder Team-IDs committen.
- Geräteadressen und Pfade über .env; Signierung über tvos/Lokal.xcconfig.
- UPnP-Eventing bevorzugen, externe Abrufe cachen und Rate-Limits beachten.
- Texte mit ihrer tatsächlichen Herkunft ausweisen, keine Autorschaft erfinden.
- Tests: .venv/bin/python -m unittest discover -s tests -v.
- Keine Live-Geräte für automatisierte Schreib- oder Wiedergabetests verwenden.
