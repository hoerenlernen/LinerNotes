# Geräteunterstützung / Device support

## Deutsch

### Aktuell erprobte Anlage

Die bisherige Anlage verwendet einen Linn Majik DS-I, einen LinerNotes-Server auf Debian, eine lokale FLAC-Bibliothek, MinimServer, optional Roon als Wiedergabequelle und Navidrome für den Favoritenabgleich. Die Anzeige läuft in Safari, Firefox und der nativen Apple-TV-App. Diese Kombination ist die Referenz; sie ist keine Liste von Diensten, die jeder zwingend betreiben muss.

- Der **Linn** liefert laufenden Titel, Wiedergabestatus, Zeit und verfügbare technische Angaben. LinerNotes sendet Steuerbefehle ebenfalls an ihn.
- **MinimServer** beantwortet Bibliothekssuchen und liefert die Medien-URLs für das Einreihen von Titeln auf dem Linn.
- **Roon** kann die Musik zum Linn liefern. LinerNotes fragt dabei weiterhin den Linn ab, keine Roon-Zonen-API.
- **Navidrome** dient dem Favoritenabgleich. API-Aufrufe setzen oder entfernen Markierungen. Lesender Zugriff auf die Datenbank dient der Pfadzuordnung und dem Rückimport. Ein eigenständiger Navidrome-Playeradapter fehlt derzeit.
- **Hören-Lernen** liefert optionale Texte und Zeitmarken. Der Server ordnet sie dem aktuellen Titel und seiner Position zu.
- **Browser und Apple-TV-App** erhalten den Anzeigezustand vom LinerNotes-Server. Sie verbinden sich für diese Funktionen nicht direkt mit dem Player.

### Datenfluss und Einstiegspunkte

| Bereich | Aktueller Code |
| --- | --- |
| Gerätesuche und Konfiguration | [`app/finden.py`](../app/finden.py): SSDP oder explizite Host-, Port- und UDN-Angaben. |
| Playerzugriff | [`app/linn.py`](../app/linn.py): OpenHome/SOAP, Zustandsabfragen, Steuerung und Ereignisabonnements. |
| Zusammenführung und API | [`app/main.py`](../app/main.py): startet die konkrete Linn-Anbindung, bereitet den Anzeigezustand auf und stellt HTTP/SSE bereit. |
| Bibliothek und Medien-URLs | [`app/bibliothek.py`](../app/bibliothek.py): ContentDirectory-Suche, derzeit mit Annahmen über MinimServer. |
| Lokale Metadaten | [`app/notes.py`](../app/notes.py): Zuordnung zu lokalen Dateien und aufbereitete Albuminformationen. |
| Navidrome | [`app/navidrome.py`](../app/navidrome.py): Favoriten und Pfadzuordnung. |
| Hörtexte | [`app/hoerenlernen.py`](../app/hoerenlernen.py): Lesen und Zuordnen der separat gepflegten Inhalte. |
| Web / TV | [`static/index.html`](../static/index.html) und [`tvos/LinerNotes`](../tvos/LinerNotes): Darstellung und Bedienung über die Server-API. |

### Andere Geräte anbinden

Weitere OpenHome-Streamer sind Kandidaten. Eine Marke allein ist kein Nachweis für OpenHome-Unterstützung. Bei NAD, Yamaha oder anderen Herstellern müssen Modell, Firmware und verfügbare Protokolle geprüft werden.

Die aktuelle Implementierung setzt konkrete Service-Versionen und aus Host, UDN und Dienstnamen gebildete URLs voraus. Ein anderes OpenHome-Gerät kann abweichende Control- und Event-URLs oder einen anderen Funktionsumfang haben. Die Gerätebeschreibung muss ausgewertet und die Implementierung bei Bedarf angepasst werden. Reines UPnP-AV/DLNA ist nicht gleichbedeutend mit OpenHome.

Es gibt noch keine austauschbare Plugin-Schnittstelle. Beiträge können die vorhandene OpenHome-Anbindung erweitern oder einen neuen Adapter einführen. Dieser muss mit dem Serverzustand zusammenarbeiten, damit Browser und TV-App eine konsistente Anzeige erhalten. Fehlende Fähigkeiten müssen als nicht verfügbar behandelt werden; Steuerbefehle dürfen nicht versehentlich andere Aktionen auslösen.

Für einen Beitrag bitte dokumentieren und am echten Gerät prüfen:

1. Modell, Firmware, verwendete Schnittstelle und Wiedergabequellen.
2. Titelwechsel, Metadaten, Position, Pause, Stopp und Wiederverbindung.
3. Unterstützte Steuerbefehle und sichere Lautstärkegrenzen, falls Lautstärkesteuerung vorhanden ist.
4. Herkunft abspielbarer Medien-URLs; Playeranbindung und Bibliotheksanbindung sind getrennte Aufgaben.
5. Verhalten im Browser und auf Apple TV; zeitlich passende Hinweise, soweit die Position verfügbar ist.

Ungetestete Funktionen bitte ausdrücklich kennzeichnen. Private Geräteadressen, Zugangsdaten und Musikdateien nicht veröffentlichen. Unterstützung für alle Geräte ist keine Voraussetzung für den ersten Release.

## English

### Reference setup

The existing setup uses a Linn Majik DS-I, a LinerNotes server on Debian, a local FLAC library, MinimServer, optional Roon playback and Navidrome favourite synchronisation. Displays include Safari, Firefox and the native Apple TV app. This is the reference configuration, not a list of mandatory services for every user.

- The **Linn** provides the current track, transport state, timing and available format information. Playback commands also go to the Linn.
- **MinimServer** handles library searches and provides playable media URLs for the Linn queue.
- **Roon** can send music to the Linn. LinerNotes still reads the Linn; it does not use a Roon zone API.
- **Navidrome** synchronises favourites. API calls change star status; read-only database access supports path matching and importing favourites. There is no standalone Navidrome player adapter yet.
- **Hören-Lernen** supplies optional text and timing information, matched to the current track and position by the server.
- **Browsers and the Apple TV app** receive their display state from LinerNotes, rather than connecting directly to the player for these functions.

### Code entry points

Device discovery is in [`app/finden.py`](../app/finden.py); player communication and events are in [`app/linn.py`](../app/linn.py). [`app/main.py`](../app/main.py) currently constructs the concrete Linn integration, builds display state and serves HTTP/SSE.

[`app/bibliothek.py`](../app/bibliothek.py) handles media-server searches and URLs, with MinimServer-specific assumptions. [`app/notes.py`](../app/notes.py) matches local files and assembles album information. [`app/navidrome.py`](../app/navidrome.py) handles favourites, and [`app/hoerenlernen.py`](../app/hoerenlernen.py) reads the separate listening content.

The clients are [`static/index.html`](../static/index.html) and [`tvos/LinerNotes`](../tvos/LinerNotes).

### Adding devices

Other OpenHome streamers are candidates for support. A brand name is not proof of OpenHome support: for NAD, Yamaha or any other manufacturer, check the specific model, firmware and protocols.

The current code assumes particular service versions and constructs URLs from the host, UDN and service name. Other OpenHome devices may expose different control/event URLs or capabilities. Inspect their device descriptions and adapt the implementation as needed. UPnP-AV/DLNA alone does not imply OpenHome support.

There is no interchangeable plug-in interface yet. Contributions can extend the existing OpenHome implementation or introduce another adapter. It must integrate with server state so that the web and TV clients remain consistent. Missing capabilities should be reported as unavailable, and commands must never trigger an unintended action.

Please document and verify on the actual device:

1. Model, firmware, interface and playback sources.
2. Track changes, metadata, position, pause, stop and reconnection.
3. Supported commands and safe volume limits where volume control is available.
4. How playable URLs are obtained; player support and library support are separate concerns.
5. Browser and Apple TV behaviour, including timed notes where position is available.

Clearly identify untested features. Do not publish private addresses, credentials or music files. Universal device support is not a prerequisite for the first release.
