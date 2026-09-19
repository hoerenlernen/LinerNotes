# Installation

## Deutsch

Dies ist eine frühe Veröffentlichung für die in der README beschriebene Linn-Anlage. Ein Download installiert weder Linn/MinimServer noch Roon oder Navidrome. Python 3.13 oder 3.14 wird empfohlen. Der Server muss den Linn erreichen; SSDP-Gerätesuche funktioniert normalerweise nur im selben lokalen Netz. Ohne auffindbaren oder explizit konfigurierten Player startet der Server nicht.

### 1. Server vorbereiten

Beispiel Debian, als normaler Benutzer (nur Paketinstallation mit sudo):

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv git ffmpeg
git clone https://github.com/hoerenlernen/LinerNotes.git
cd LinerNotes
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p ~/.config/liner
cp .env.beispiel ~/.config/liner/liner.env
chmod 600 ~/.config/liner/liner.env
```

Existiert bereits eine Konfiguration, diese ergänzen statt überschreiben. Auf macOS Python und ffmpeg separat bereitstellen; danach dieselben Schritte ab `git clone`.

### 2. Konfiguration anpassen

In `~/.config/liner/liner.env` absolute Pfade verwenden; keine Shell-Variablen oder `~` in Werten. Werte mit Leerzeichen in Anführungszeichen setzen. Die Datei wird als Daten gelesen, nicht als Shell-Skript ausgeführt. Vorhandene Umgebungsvariablen haben Vorrang. `LINER_ENV` kann eine andere Datei auswählen.

- `LINER_MUSIC_ROOT`: vorhandenes Verzeichnis der FLAC-Bibliothek, für den Dienst lesbar.
- `LINER_DB` und `LINER_COVERDIR`: optional eigene beschreibbare Pfade; standardmäßig `data/` im Projekt. Index und Server müssen dieselbe Datenbank verwenden.
- `LINER_BIND`: standardmäßig `127.0.0.1`. Für direkten LAN-Zugriff die LAN-IP des Servers eintragen.
- `LINER_CALLBACK_BASIS`: vollständige Adresse wie `http://192.168.1.10:5060/notify`, die der Linn erreichen kann. `/notify` nicht weglassen. Bei Bindung nur an localhost ist ein passend eingerichteter Proxy für den Callback nötig.
- Player und MinimServer werden gesucht. Falls nötig `LINN_HOST`, tatsächlichen `LINN_PORT`, `LINN_UDN` beziehungsweise `MINIM_BASIS`, `MINIM_UDN` setzen. Port und UDN nicht aus der Beispielkonfiguration übernehmen. Die aktuellen URLs werden gerätespezifisch aufgebaut; siehe Geräteanbindung.
- Navidrome optional: API-Adresse und Zugangsdaten eintragen; für genaue Dateizuordnung zusätzlich die Datenbank nur lesbar bereitstellen. Ohne Navidrome `NAVIDROME_SPIEGELN=0` setzen.

Keine eigene Benutzeranmeldung: nur vertrauenswürdige Geräte dürfen den Server erreichen. Nicht ungeschützt ins Internet freigeben. Ein Browser-Origin-Check ersetzt keine Zugriffskontrolle. Bei einem Reverse Proxy Host und Origin konsistent weiterreichen und Zugriff durch VPN oder Authentifizierung schützen; `/notify` muss separat nur für den tatsächlichen Linn zugänglich sein. Die direkte Callback-Prüfung erwartet dessen Quell-IP, daher den Callback möglichst direkt im LAN führen.

### 3. Index und Start

```sh
.venv/bin/python bin/index.py
.venv/bin/python bin/server.py
```

Browser: `http://SERVER-LAN-IP:5060`. Den Server laufen lassen. Die Datenbank wird beim ersten Start initialisiert; vollständige Albumdaten brauchen den Index und passende Dateimetadaten. Prüfen: aktueller Titel, Albumzuordnung, Cover, Pause/Play und bei vorhandenem MinimServer die Suche. Hardware-/Quellenunterschiede können einzelne Funktionen einschränken.

Optional DR berechnen (kann lange dauern, liest/dekodiert die Musik):

```sh
.venv/bin/python bin/dr-scan.py --jobs 2
```

Der DR-Wert stammt aus der Projektimplementierung und ist keine zertifizierte Messung. `bin/liner-pflege.sh` aktualisiert Index und DR. Externe Metadaten werden separat mit `bin/enrich-alle.sh` abgefragt; vorher dessen Quellen und optionale Zugangsdaten prüfen. Musikdateien werden dabei nicht verändert. `tagvault.py restore --apply` ist dagegen ausdrücklich ein schreibendes Wartungswerkzeug.

### 4. Optional dauerhaft starten

Die Beispiel-Unit unter `beispiele/linernotes.service` setzt den Clone unter `~/LinerNotes` voraus. Bei anderem Installationsort beide Pfade anpassen.

```sh
mkdir -p ~/.config/systemd/user
cp doku/beispiele/linernotes.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now linernotes
journalctl --user -u linernotes
```

Für Start ohne Benutzeranmeldung kann ein Administrator `loginctl enable-linger BENUTZERNAME` aktivieren. Vor Indexläufen müssen Musiklaufwerke eingehängt sein. Datenbank, Cover und Konfiguration unabhängig vom Quellcode sichern. Keine vorhandene Produktions-Unit blind ersetzen.

### 5. Hören-Lernen und Apple TV

Das optionale [Inhaltsrepository](https://github.com/hoerenlernen/hoeren-lernen) separat klonen und seinen absoluten Pfad als `HOERENLERNEN_PFAD` eintragen. Der Server sucht darin rekursiv nach `*_Hoeren.md`; nur passende Aufnahmen zeigen Hinweise. Die eigene Inhaltslizenz bleibt erhalten. Nach Änderungen neu starten oder den Endpunkt `/api/hoerenlernen/neu-einlesen` per POST aufrufen. Das Pull-Werkzeug benötigt `HOERENLERNEN_PFAD` ausdrücklich in seiner Prozessumgebung; es lädt die Konfigurationsdatei nicht selbst.

Die native App wird separat gebaut: [Apple-TV-Anleitung](../tvos/README.md). Auf dem Apple TV die vom Gerät erreichbare Serveradresse einstellen, nicht `localhost`.

## English

This early release targets the Linn setup described in the README. It does not install the player, MinimServer, Roon or Navidrome. Python 3.13 or 3.14 is recommended. The server must reach the Linn; discovery normally requires the same local network. Without a discovered or explicitly configured player, startup stops.

1. On Debian install `python3 python3-venv git ffmpeg` using apt, then run the clone, virtual-environment and dependency commands in step 1 above. On macOS provide Python and ffmpeg separately. Run the application as a regular user.
2. Copy `.env.beispiel` to `~/.config/liner/liner.env`, protect it with `chmod 600` and edit it. Preserve any existing configuration. Use absolute paths, quote spaces, and do not use shell expansion. Existing environment variables take precedence; `LINER_ENV` selects an alternative configuration file.
3. Set `LINER_MUSIC_ROOT` to your readable FLAC library. Optional `LINER_DB` and `LINER_COVERDIR` choose writable application storage; defaults are under `data/`. Index and server must use the same database. Keep media mounted before indexing.
4. `LINER_BIND` defaults to localhost. For direct LAN access set the server's LAN IP. Set `LINER_CALLBACK_BASIS` to the complete player-reachable URL, including `/notify`, for example `http://192.168.1.10:5060/notify`. Direct LAN callbacks preserve the player source IP required by the callback check.
5. Discovery can be replaced by explicit `LINN_HOST`, actual `LINN_PORT` and `LINN_UDN`; similarly configure MinimServer with `MINIM_BASIS` and `MINIM_UDN`. Example values are placeholders. Device-specific URL construction is explained in the architecture guide.
6. Navidrome is optional: set API credentials and, for exact file matching, provide read-only database access. Set `NAVIDROME_SPIEGELN=0` to disable it.
7. Run `.venv/bin/python bin/index.py`, then `.venv/bin/python bin/server.py`. Open `http://SERVER-LAN-IP:5060`. Check current track, album matching, covers, controls and optional MinimServer search. Database tables are created automatically, but full local matching requires an index and suitable file metadata.
8. Optionally run `.venv/bin/python bin/dr-scan.py --jobs 2`. This can take a long time and is not a certified DR measurement. `bin/liner-pflege.sh` refreshes the index and DR results. External enrichment is separate (`bin/enrich-alle.sh`); review sources and optional credentials first. Normal operation reads music files; `tagvault.py restore --apply` explicitly writes tags.

For unattended Linux operation, adapt the user service in [beispiele/linernotes.service](beispiele/linernotes.service), copy it to `~/.config/systemd/user/`, then use the systemctl commands above. Its paths assume `~/LinerNotes`. An administrator can enable user lingering for startup without login. Back up application data and configuration separately; do not overwrite an existing production service blindly.

There is no built-in login. Restrict access to trusted devices or a secured VPN/access gateway; never expose the API unprotected. Origin checks are not authentication. A reverse proxy must preserve consistent Host/Origin values and enforce access control. Keep `/notify` restricted to the actual player, preferably using a direct LAN callback.

Clone [Hören-Lernen](https://github.com/hoerenlernen/hoeren-lernen) separately and set its absolute path as `HOERENLERNEN_PFAD`. The server reads matching `*_Hoeren.md` files recursively; their separate content licence applies. Restart or POST to `/api/hoerenlernen/neu-einlesen` after changes. The optional pull shell script expects the path in its process environment and does not load the configuration file itself.

For the optional native app see [Apple TV](../tvos/README.md#english). Use a server address reachable from the TV, not localhost.
