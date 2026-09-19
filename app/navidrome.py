# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
navidrome.py – spiegelt die Merkliste in Navidrome.

WOZU
====
Damit die gemerkten Titel überall auftauchen, wo Navidrome gelesen wird:
Feishin am Mac, play:sub und Amperfy auf dem iPhone. Die Markierung lebt
dann nicht nur in dieser Anzeige.

WEG: SUBSONIC-API, NICHT DIE DATENBANK
======================================
Navidrome hat eine eigene SQLite-Datenbank mit der Tabelle `annotation`
(`starred`, `starred_at`). Direkt hineinzuschreiben wäre naheliegend und
falsch: es ist die Datenbank eines **laufenden** Dienstes, sie gehört dem
Benutzer `navidrome`, und ein Schreibzugriff von außen müsste entweder
dessen Rechte aufweichen oder über sudo laufen. Beides wäre genau die Art
Abkürzung, die später Ärger macht (vgl. Jellyfin in der Gruppe `gr`).

Darum die Subsonic-API. Sie ist dafür gemacht, Navidrome verwaltet seine
Zustände selbst, und ein Fehler bleibt ein Fehlschlag statt einer
beschädigten Datenbank.

**Token statt Passwort in der URL**: Subsonic erlaubt `t=md5(passwort+salt)`
mit einem frischen `salt` je Anfrage. Damit steht das Passwort nie in einer
URL, in einem Log oder in der Prozessliste.

ZUORDNUNG: ID AUS DER DATENBANK, SCHREIBEN ÜBER DIE API
=======================================================
Die Song-Id wird **lesend** aus `navidrome.db` geholt, nicht über die API.
Grund: `search3` liefert zwar ein Feld `path`, aber darin steht ein
*konstruierter* Pfad (`Künstler/Album/Track`), nicht der Dateipfad. Ein
Vergleich damit schlägt fehl – geprüft: von drei Merklisteneinträgen wurde
so nur einer gefunden, die übrigen galten als mehrdeutig.

In der Datenbank steht in `media_file.path` dagegen der echte Pfad
**relativ zur Musikwurzel**, in genau derselben Form wie im Liner-Index
(`track.pfad`). Damit ist die Zuordnung ein exakter Vergleich.

Die Datei ist für alle lesbar (`-rw-r--r--`) und wird ausschließlich
**read-only** geöffnet (`mode=ro`). Geschrieben wird nie in die Datenbank –
das bleibt der API vorbehalten.

WAS NICHT PASSIERT
==================
Bestehende Favoriten in Navidrome werden **nicht** angetastet. Die
Spiegelung setzt nur, was in der Merkliste steht; sie entfernt nichts, was
dort schon markiert war (Navidrome hatte 24 Song- und 3 Album-Favoriten,
bevor dieses Modul entstand).
"""
import hashlib
import json
import logging
import os
import random
import string
import threading
import urllib.parse
import urllib.request

log = logging.getLogger("liner.navidrome")


class Navidrome:
    def __init__(self, url=None, benutzer=None, passwort=None, timeout=10,
                 db_pfad=None):
        self.url = (url or os.environ.get("NAVIDROME_URL",
                                          "http://127.0.0.1:4533")).rstrip("/")
        self.benutzer = benutzer or os.environ.get("NAVIDROME_USER", "")
        self._passwort = passwort or os.environ.get("NAVIDROME_PASS", "")
        self.timeout = timeout
        self.db_pfad = db_pfad or os.environ.get(
            "NAVIDROME_DB", "/var/lib/navidrome/navidrome.db")
        self._sperre = threading.Lock()

    @property
    def eingerichtet(self):
        return bool(self.benutzer and self._passwort)

    def _auth(self):
        """Frisches Salt je Anfrage, Passwort nur als MD5-Token."""
        salt = "".join(random.choice(string.ascii_lowercase + string.digits)
                       for _ in range(12))
        token = hashlib.md5((self._passwort + salt).encode("utf-8")).hexdigest()
        return {"u": self.benutzer, "t": token, "s": salt,
                "v": "1.16.1", "c": "liner-notes", "f": "json"}

    def _ruf(self, pfad, **args):
        felder = self._auth()
        felder.update({k: v for k, v in args.items() if v is not None})
        ziel = "%s/rest/%s?%s" % (self.url, pfad, urllib.parse.urlencode(felder))
        with self._sperre:
            with urllib.request.urlopen(ziel, timeout=self.timeout) as f:
                rohdaten = f.read().decode("utf-8", "replace")
        antwort = json.loads(rohdaten).get("subsonic-response", {})
        if antwort.get("status") != "ok":
            fehler = antwort.get("error") or {}
            raise RuntimeError("Navidrome: %s (Code %s)" % (
                fehler.get("message", "unbekannter Fehler"), fehler.get("code")))
        return antwort

    def erreichbar(self):
        try:
            self._ruf("ping")
            return True
        except Exception as e:
            log.warning("Navidrome nicht erreichbar: %s", e)
            return False

    # ------------------------------------------------------------------
    def song_id(self, relpfad, titel=None, artist=None):
        """Findet die Navidrome-Id zu einem Titel.

        Zuerst über den Dateipfad in `navidrome.db` (exakt, read-only).
        Nur wenn kein Pfad bekannt ist – etwa bei einem über AirPlay
        gemerkten Titel –, wird die API befragt, und dann nur bei
        eindeutigem Treffer: lieber nichts markieren als das Falsche."""
        if relpfad:
            treffer = self._id_aus_db(relpfad)
            if treffer:
                return treffer

        if not titel:
            return None
        try:
            antwort = self._ruf("search3", query=titel, songCount=50,
                                albumCount=0, artistCount=0)
        except Exception as e:
            log.warning("Navidrome-Suche fehlgeschlagen (%r): %s", titel, e)
            return None
        lieder = (antwort.get("searchResult3") or {}).get("song") or []
        passend = [l for l in lieder
                   if (l.get("title") or "").lower() == titel.lower()
                   and (not artist or (artist or "").lower()
                        in (l.get("artist") or "").lower())]
        if len(passend) == 1:
            return passend[0].get("id")
        if len(passend) > 1:
            log.info("Navidrome: %r ist mehrfach vorhanden (%d) – nicht "
                     "markiert", titel, len(passend))
        return None

    def _id_aus_db(self, relpfad):
        """Song-Id über den Dateipfad, direkt aus der Navidrome-Datenbank.

        Read-only (`mode=ro`), `immutable=0`, damit ein laufender Navidrome
        nicht gestört wird. Schlägt das fehl – etwa weil die Datei nicht
        lesbar ist –, wird still auf die API zurückgefallen."""
        if not self.db_pfad or not relpfad:
            return None
        import sqlite3
        ziel = relpfad.replace("\\", "/")
        try:
            con = sqlite3.connect(
                "file:%s?mode=ro" % urllib.parse.quote(self.db_pfad),
                uri=True, timeout=5)
            try:
                r = con.execute(
                    "SELECT id FROM media_file WHERE path = ? LIMIT 1",
                    (ziel,)).fetchone()
                if r:
                    return r[0]
                # Navidrome kann den Pfad mit fuehrendem "./" oder absolut
                # fuehren - beide Formen zur Sicherheit pruefen.
                r = con.execute(
                    "SELECT id FROM media_file WHERE path LIKE ? LIMIT 2",
                    ("%" + ziel,)).fetchall()
                if len(r) == 1:
                    return r[0][0]
            finally:
                con.close()
        except Exception as e:
            log.debug("Navidrome-DB nicht lesbar (%s): %s", self.db_pfad, e)
        return None

    def pfad_zu_id(self, song_id):
        """Umkehrung: Dateipfad zu einer Navidrome-Id.

        Gebraucht beim Abgleich in die andere Richtung - was dort markiert
        ist, soll in die Merkliste wandern, und die fuehrt Pfade. Auch das
        laeuft read-only ueber die Datenbank, weil die API nur den
        konstruierten Pfad liefert."""
        if not song_id or not self.db_pfad:
            return None
        import sqlite3
        try:
            con = sqlite3.connect(
                "file:%s?mode=ro" % urllib.parse.quote(self.db_pfad),
                uri=True, timeout=5)
            try:
                r = con.execute("SELECT path FROM media_file WHERE id=? LIMIT 1",
                                (song_id,)).fetchone()
                return r[0] if r else None
            finally:
                con.close()
        except Exception as e:
            log.debug("Navidrome-DB nicht lesbar: %s", e)
            return None

    def markieren(self, song_id, an=True):
        try:
            self._ruf("star" if an else "unstar", id=song_id)
            return True, None
        except Exception as e:
            return False, str(e)

    def markierte_ids(self):
        """Was dort schon markiert ist - damit nichts doppelt gesetzt und
        nichts fremdes entfernt wird."""
        try:
            antwort = self._ruf("getStarred2")
        except Exception as e:
            log.warning("Navidrome-Favoriten nicht lesbar: %s", e)
            return set()
        lieder = (antwort.get("starred2") or {}).get("song") or []
        return {l.get("id") for l in lieder if l.get("id")}
