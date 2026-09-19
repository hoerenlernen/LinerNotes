# Mitmachen / Contributing

Beiträge zu Geräten, Dokumentation, Fehlern und Übersetzungen sind willkommen. Bitte beschreibe Problem und Änderung in einem Issue oder Pull Request. Für neue Player sind konkrete Modelle, Schnittstellen und Tests mit echter Hardware wichtig; siehe `doku/geraeteanbindung.md`.

Contributions to device support, documentation, fixes and translations are welcome. Describe the problem and change in an issue or pull request. Include exact device models, interfaces and real hardware checks for new integrations. Contributions to this repository are under GPL-3.0-only; retain existing notices. Hören-Lernen content belongs in its separate repository under its own licence.

Never commit credentials, local configuration, databases, music files or personal signing settings. Report bugs with revision, OS, player, playback source and reproduction steps. German or English is fine.

Local checks (no real player required):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app bin
```

For tvOS changes, also build the simulator target described in `tvos/README.md`. Automated tests mock hardware and do not prove compatibility with an untested streamer.
