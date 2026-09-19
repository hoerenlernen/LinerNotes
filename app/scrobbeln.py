# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Scrobbeln an Last.fm - fuer alles, was der Linn spielt.

Warum dieser Dienst das tut und nicht Navidrome: Navidrome scrobbelt nur,
was durch seine eigenen Clients laeuft (Feishin, play:sub, Amperfy). Der
Hauptweg - Linn ueber Kazoo und MinimServer - kommt dort nie vorbei. Ohne
diesen Baustein besteht das Last.fm-Profil aus einer kleinen, schiefen
Stichprobe, und Empfehlungen, die darauf beruhen, taugen nichts.

Zugang: Der Session-Key gehoert zu GENAU dem ApiKey/Secret-Paar, mit dem
er erzeugt wurde (hier: der Zugang, den Navidrome benutzt). Mit einem
anderen Key ist er ungueltig - darum liegen drei getrennte Werte in der
Umgebung: LASTFM_SCROBBLE_KEY, LASTFM_SCROBBLE_SECRET,
LASTFM_SESSION_KEY.

Wann ein Titel zaehlt (Last.fm-Regel, hier bewusst genauso):
  - Laufzeit mindestens 30 Sekunden
  - gehoert mindestens die Haelfte, oder mindestens 4 Minuten

Gemessen wird an der Abspielposition des Linn, nicht an der Wanduhr.
Damit zaehlt eine halbe Stunde Pause nicht als Hoeren, und ein Sprung
nach vorn taeuscht nichts vor.

Fehlgeschlagene Sendungen bleiben in der Warteschlange und werden beim
naechsten Durchgang erneut versucht. Nichts geht verloren, wenn Last.fm
kurz nicht erreichbar ist oder der Dienst neu startet.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request

log = logging.getLogger("liner.scrobbeln")

ENDPUNKT = "https://ws.audioscrobbler.com/2.0/"

# Last.fm nimmt bis zu 50 Scrobbles pro Anfrage.
STAPEL = 50

# Schwellen der Last.fm-Regel.
MINDESTLAUFZEIT = 30
SPAETESTENS_NACH = 240

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrobble (
  id             INTEGER PRIMARY KEY,
  artist         TEXT NOT NULL,
  titel          TEXT NOT NULL,
  album          TEXT,
  albumkuenstler TEXT,
  dauer_s        INTEGER,
  tracknummer    INTEGER,
  mbid           TEXT,
  zeitstempel    INTEGER NOT NULL,
  gesendet       INTEGER NOT NULL DEFAULT 0,
  versuche       INTEGER NOT NULL DEFAULT 0,
  fehler         TEXT,
  quelle         TEXT,
  UNIQUE(artist, titel, zeitstempel)
);
CREATE INDEX IF NOT EXISTS i_scrobble_offen
  ON scrobble(gesendet, zeitstempel);
"""


# --------------------------------------------------------------------------
# Last.fm-Protokoll
# --------------------------------------------------------------------------

class Zugang:
    """Die drei Werte, die eine Sendung unterschreiben."""

    def __init__(self, key=None, secret=None, session=None):
        self.key = key or os.environ.get("LASTFM_SCROBBLE_KEY", "")
        self.secret = secret or os.environ.get("LASTFM_SCROBBLE_SECRET", "")
        self.session = session or os.environ.get("LASTFM_SESSION_KEY", "")

    @property
    def vollstaendig(self):
        return bool(self.key and self.secret and self.session)


def _unterschreiben(felder, secret):
    """Last.fm-Signatur: alle Felder nach Namen sortiert aneinander, dann
    das Secret anhaengen, dann md5. `format` und `callback` bleiben aussen
    vor - so steht es in der Schnittstellenbeschreibung."""
    roh = "".join(k + str(felder[k]) for k in sorted(felder)
                  if k not in ("format", "callback"))
    return hashlib.md5((roh + secret).encode("utf-8")).hexdigest()


def _senden(methode, zugang, felder, timeout=25):
    felder = dict(felder)
    felder["method"] = methode
    felder["api_key"] = zugang.key
    felder["sk"] = zugang.session
    felder["api_sig"] = _unterschreiben(felder, zugang.secret)
    felder["format"] = "json"
    daten = urllib.parse.urlencode(
        {k: v for k, v in felder.items() if v not in (None, "")}).encode("utf-8")
    anfrage = urllib.request.Request(
        ENDPUNKT, data=daten,
        headers={"User-Agent": "liner-notes/1.0 (+lokal)"})
    with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
        return json.loads(antwort.read().decode("utf-8"))


def jetzt_melden(zugang, artist, titel, album=None, dauer_s=None):
    """„Läuft gerade" - kein Scrobble, nur die Anzeige auf Last.fm."""
    felder = {"artist": artist, "track": titel}
    if album:
        felder["album"] = album
    if dauer_s:
        felder["duration"] = int(dauer_s)
    return _senden("track.updateNowPlaying", zugang, felder)


def stapel_senden(zugang, eintraege):
    """Bis zu 50 Scrobbles in einer Anfrage.

    Rueckgabe: (angenommen, ignoriert, gruende) - `gruende` sammelt die
    Begruendungen, die Last.fm zu ignorierten Titeln mitschickt. Die sind
    aufschlussreich: Code 1 heisst „Kuenstler unbekannt", Code 3
    „Zeitstempel zu alt"."""
    felder = {}
    for i, e in enumerate(eintraege):
        felder["artist[%d]" % i] = e["artist"]
        felder["track[%d]" % i] = e["titel"]
        felder["timestamp[%d]" % i] = int(e["zeitstempel"])
        if e.get("album"):
            felder["album[%d]" % i] = e["album"]
        if e.get("albumkuenstler") and e["albumkuenstler"] != e["artist"]:
            felder["albumArtist[%d]" % i] = e["albumkuenstler"]
        if e.get("dauer_s"):
            felder["duration[%d]" % i] = int(e["dauer_s"])
        if e.get("tracknummer"):
            felder["trackNumber[%d]" % i] = int(e["tracknummer"])
        if e.get("mbid"):
            felder["mbid[%d]" % i] = e["mbid"]

    antwort = _senden("track.scrobble", zugang, felder)
    block = antwort.get("scrobbles") or {}
    attr = block.get("@attr") or {}
    angenommen = int(attr.get("accepted") or 0)
    ignoriert = int(attr.get("ignored") or 0)

    gruende = []
    liste = block.get("scrobble") or []
    if isinstance(liste, dict):
        liste = [liste]
    for s in liste:
        i = s.get("ignoredMessage") or {}
        if (i.get("code") or "0") != "0":
            gruende.append("%s: %s (Code %s)" % (
                (s.get("track") or {}).get("#text", "?"),
                i.get("#text") or "ohne Angabe", i.get("code")))
    return angenommen, ignoriert, gruende


# --------------------------------------------------------------------------
# Beobachtung des laufenden Titels
# --------------------------------------------------------------------------

class Scrobbler:
    """Verfolgt den laufenden Titel und reiht faellige Scrobbles ein.

    `beobachten()` ist billig und macht keine Netzanfrage - es wird bei
    jeder Zustandsaenderung aufgerufen. Gesendet wird getrennt davon in
    `abschicken()`, das in einem eigenen Thread laufen darf.
    """

    def __init__(self, db_pfad, zugang=None):
        self.db_pfad = db_pfad
        self.zugang = zugang or Zugang()
        self.aktuell = None
        self.jetzt_offen = None      # was noch als „läuft gerade" zu melden ist
        self.anlegen()

    # -- Datenbank ---------------------------------------------------------

    def _con(self):
        con = sqlite3.connect(self.db_pfad, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def anlegen(self):
        try:
            with self._con() as con:
                con.executescript(SCHEMA)
        except Exception as e:
            log.warning("Scrobble-Tabelle nicht anlegbar: %s", e)

    def _einreihen(self, eintrag):
        try:
            with self._con() as con:
                con.execute(
                    "INSERT OR IGNORE INTO scrobble (artist, titel, album,"
                    " albumkuenstler, dauer_s, tracknummer, mbid, zeitstempel,"
                    " quelle) VALUES (?,?,?,?,?,?,?,?,?)",
                    (eintrag["artist"], eintrag["titel"], eintrag.get("album"),
                     eintrag.get("albumkuenstler"), eintrag.get("dauer_s"),
                     eintrag.get("tracknummer"), eintrag.get("mbid"),
                     int(eintrag["zeitstempel"]), eintrag.get("quelle") or "linn"))
            log.info("Scrobble eingereiht: %s - %s", eintrag["artist"], eintrag["titel"])
            return True
        except Exception as e:
            log.warning("Scrobble nicht einreihbar: %s", e)
            return False

    # -- Beobachtung -------------------------------------------------------

    @staticmethod
    def _tracknummer(daten):
        try:
            return int(str(daten.get("tracknummer") or "").lstrip("0") or 0) or None
        except (TypeError, ValueError):
            return None

    def beobachten(self, daten):
        """Einen Messpunkt verarbeiten. Kein Netz, kein Blockieren."""
        if not daten:
            return
        artist = (daten.get("artist") or "").strip()
        titel = (daten.get("titel") or "").strip()
        # Ohne Kuenstler und Titel nimmt Last.fm nichts an - Radio ohne
        # Metadaten, ein analoger Eingang, AirPlay ohne Angaben.
        if not artist or not titel:
            self.aktuell = None
            return

        zeit = daten.get("zeit") or {}
        position = zeit.get("position_s") or 0
        dauer = zeit.get("dauer_s") or 0
        # Wenn der Linn keine Laufzeit meldet, aus der Titelliste holen.
        if not dauer:
            for t in daten.get("tracks") or []:
                if (t.get("titel") or "").strip() == titel and t.get("dauer_s"):
                    dauer = int(t["dauer_s"])
                    break

        schluessel = (artist, titel, daten.get("album") or "")
        if not self.aktuell or self.aktuell["schluessel"] != schluessel:
            # Neuer Titel. Beginn zurueckrechnen, damit der Zeitstempel den
            # Anfang meint und nicht den Moment, in dem wir hinsehen.
            self.aktuell = {
                "schluessel": schluessel,
                "beginn": time.time() - min(position, dauer or position),
                "hoechste_position": position,
                "dauer": dauer,
                "gesendet": False,
                "daten": daten,
            }
            if daten.get("laeuft"):
                self.jetzt_offen = {
                    "artist": artist, "titel": titel,
                    "album": daten.get("album"), "dauer_s": dauer or None}
            return

        lauf = self.aktuell
        lauf["daten"] = daten
        if dauer and not lauf["dauer"]:
            lauf["dauer"] = dauer
        if position > lauf["hoechste_position"]:
            lauf["hoechste_position"] = position

        if lauf["gesendet"]:
            return

        dauer = lauf["dauer"]
        if not dauer or dauer < MINDESTLAUFZEIT:
            return
        schwelle = min(dauer / 2.0, SPAETESTENS_NACH)
        if lauf["hoechste_position"] < schwelle:
            return

        lauf["gesendet"] = True
        self._einreihen({
            "artist": artist,
            "titel": titel,
            "album": daten.get("album"),
            "albumkuenstler": daten.get("artist"),
            "dauer_s": int(dauer),
            "tracknummer": self._tracknummer(daten),
            "mbid": (daten.get("mb_zuordnung") or {}).get("recording_mbid")
                    if isinstance(daten.get("mb_zuordnung"), dict) else None,
            "zeitstempel": int(lauf["beginn"]),
            "quelle": (daten.get("quelle") or {}).get("typ") or "linn",
        })

    # -- Senden ------------------------------------------------------------

    def offen(self):
        try:
            with self._con() as con:
                return con.execute(
                    "SELECT count(*) FROM scrobble WHERE gesendet=0").fetchone()[0]
        except Exception:
            return 0

    def abschicken(self, hoechstens=STAPEL * 4):
        """Warteschlange leeren. Laeuft im Thread, darf blockieren.

        Rueckgabe: (angenommen, ignoriert, uebrig)
        """
        if not self.zugang.vollstaendig:
            return 0, 0, self.offen()

        # „Läuft gerade" zuerst - das ist zeitkritisch, ein Scrobble nicht.
        if self.jetzt_offen:
            j, self.jetzt_offen = self.jetzt_offen, None
            try:
                jetzt_melden(self.zugang, j["artist"], j["titel"],
                             j.get("album"), j.get("dauer_s"))
            except Exception as e:
                log.debug("Meldung „laeuft gerade“ nicht abgesetzt: %s", e)

        angenommen = ignoriert = 0
        while hoechstens > 0:
            with self._con() as con:
                zeilen = con.execute(
                    "SELECT id, artist, titel, album, albumkuenstler, dauer_s,"
                    " tracknummer, mbid, zeitstempel FROM scrobble"
                    " WHERE gesendet=0 AND versuche < 12"
                    " ORDER BY zeitstempel LIMIT ?", (min(STAPEL, hoechstens),)
                ).fetchall()
            if not zeilen:
                break
            hoechstens -= len(zeilen)
            felder = ("id", "artist", "titel", "album", "albumkuenstler",
                      "dauer_s", "tracknummer", "mbid", "zeitstempel")
            eintraege = [dict(zip(felder, z)) for z in zeilen]
            ids = [e["id"] for e in eintraege]
            try:
                a, i, gruende = stapel_senden(self.zugang, eintraege)
            except Exception as e:
                # Netz weg oder Last.fm launisch: Versuch zaehlen, spaeter
                # erneut. Nach zwoelf Versuchen ruht der Eintrag - dann
                # stimmt etwas grundsaetzlich nicht, und stures Wiederholen
                # hilft nicht.
                with self._con() as con:
                    con.execute(
                        "UPDATE scrobble SET versuche = versuche + 1, fehler = ?"
                        " WHERE id IN (%s)" % ",".join("?" * len(ids)),
                        [str(e)[:200]] + ids)
                log.warning("Scrobble-Stapel fehlgeschlagen (%d Titel): %s",
                            len(ids), e)
                break

            angenommen += a
            ignoriert += i
            for g in gruende:
                log.info("Last.fm hat abgelehnt - %s", g)
            # Angenommen wie ignoriert gelten als erledigt: ein Titel, den
            # Last.fm nicht will, will es auch beim zehnten Mal nicht.
            with self._con() as con:
                con.execute(
                    "UPDATE scrobble SET gesendet=1 WHERE id IN (%s)"
                    % ",".join("?" * len(ids)), ids)

        if angenommen or ignoriert:
            log.info("Scrobbles gesendet: %d angenommen, %d abgelehnt, %d offen",
                     angenommen, ignoriert, self.offen())
        return angenommen, ignoriert, self.offen()
