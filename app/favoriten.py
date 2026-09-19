# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
favoriten.py – „Gefällt mir" für einzelne Titel.

ZWECK
=====
Beim Hören einen Titel merken und daraus später eine Playlist bauen. Zwei
Wege hinaus:

  1. M3U zum Herunterladen (für Kazoo, Navidrome, Feishin, mpv …)
  2. direkt in die Warteschlange des Linn (Playlist/Insert) – Stufe 2b

SCHLÜSSEL
=========
Maßgeblich ist der **Dateipfad, relativ zur Musikwurzel** – genau in der
Form, in der ihn auch der Index führt (`track.pfad`). Absolute Pfade wären
ein Fehler: die Anzeige liefert sie absolut, der Index relativ, und ein
Vergleich zwischen beiden schlägt dann still fehl. Relativ ist außerdem
portabel, falls die Bibliothek einmal woanders liegt.

Maßgeblich ist der Dateipfad. Er ist der einzige Wert, der eine Datei
zuverlässig wiederfindet: Tags können falsch sein (siehe Kapitel 13), IDs
der Warteschlange gelten nur für die aktuelle Sitzung, und die
Titelschreibweise unterscheidet sich zwischen Quellen.

Für Quellen **ohne** Bibliotheksdatei (AirPlay, Radio, analog) gibt es
keinen Pfad. Solche Titel werden trotzdem gespeichert – mit Künstler,
Album und Titel als Text und `pfad = NULL`. Sie erscheinen in der Liste,
aber nicht in der M3U, weil sich keine Datei daraus ableiten lässt. Das ist
ehrlicher, als sie stillschweigend zu verwerfen.

MUSIKDATEIEN
============
Werden nicht angefasst. Keine Tags, keine Playlist-Datei in den
Musikordnern – das Seeding darf nicht gestört werden. Alles steht in der
SQLite-Datenbank dieses Dienstes; die M3U wird bei Abruf erzeugt und nie
abgelegt.
"""
import logging
import os
import re
import sqlite3
import time

log = logging.getLogger("liner.favoriten")

SCHEMA = """
CREATE TABLE IF NOT EXISTS favorit (
  id         INTEGER PRIMARY KEY,
  pfad       TEXT UNIQUE,          -- NULL bei Quellen ohne Datei
  album_id   INTEGER,              -- kann fehlen
  artist     TEXT,
  album      TEXT,
  titel      TEXT,
  dauer_s    REAL,
  quelle     TEXT,                 -- welche Linn-Quelle lief
  res_url    TEXT,                 -- exakt die URL, die der Player bekam
  didl       TEXT,                 -- Originalmetadaten des Players
  gesetzt_am REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS i_favorit_zeit  ON favorit(gesetzt_am DESC);
CREATE INDEX IF NOT EXISTS i_favorit_album ON favorit(album_id);
"""


class Favoriten:
    def __init__(self, db, wurzel="/pfad/zur/musik"):
        self.db = db
        self.wurzel = os.path.realpath(wurzel)
        self._anlegen()

    # --- Pfadform -----------------------------------------------------
    def relativ(self, pfad):
        """Macht aus einem beliebigen Pfad die Indexform (relativ).

        Gibt None zurueck, wenn der Pfad ausserhalb der Bibliothek liegt -
        das ist zugleich die Sicherheitspruefung fuer Werte von aussen."""
        if not pfad:
            return None
        if not os.path.isabs(pfad):
            kandidat = os.path.realpath(os.path.join(self.wurzel, pfad))
        else:
            kandidat = os.path.realpath(pfad)
        if kandidat != self.wurzel and not kandidat.startswith(self.wurzel + os.sep):
            return None
        return os.path.relpath(kandidat, self.wurzel)

    def absolut(self, rel):
        return os.path.join(self.wurzel, rel) if rel else None

    def _con(self):
        con = sqlite3.connect(self.db, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def _anlegen(self):
        try:
            with self._con() as con:
                con.executescript(SCHEMA)
                # Nachtraeglich hinzugefuegte Spalten (bestehende Tabellen)
                vorhanden = {r[1] for r in con.execute("PRAGMA table_info(favorit)")}
                for spalte in ("res_url", "didl"):
                    if spalte not in vorhanden:
                        con.execute("ALTER TABLE favorit ADD COLUMN %s TEXT" % spalte)
                        log.info("Spalte %s ergaenzt", spalte)
        except Exception as e:
            log.error("Favoritentabelle nicht anlegbar: %s", e)

    # ------------------------------------------------------------------
    def gesetzt(self, pfad):
        rel = self.relativ(pfad)
        if not rel:
            return False
        try:
            with self._con() as con:
                return con.execute("SELECT 1 FROM favorit WHERE pfad=?",
                                   (rel,)).fetchone() is not None
        except Exception as e:
            log.warning("Favoritenabfrage fehlgeschlagen: %s", e)
            return False

    def umschalten(self, inhalt):
        """Setzt oder entfernt den Favoriten für den übergebenen Titel.

        `inhalt` ist das Anzeige-Dictionary (aus notes.bauen). Gibt
        (gesetzt, grund) zurück; `gesetzt` ist der Zustand NACH dem
        Umschalten."""
        # Die Anzeige liefert den Pfad absolut, gespeichert wird relativ.
        pfad = self.relativ((inhalt or {}).get("aktueller_pfad"))
        titel = (inhalt or {}).get("titel")
        if not pfad and not titel:
            return None, "Kein Titel erkennbar – nichts zu merken."

        try:
            with self._con() as con:
                if pfad:
                    vorhanden = con.execute(
                        "SELECT id FROM favorit WHERE pfad=?", (pfad,)).fetchone()
                    if not vorhanden and titel:
                        # Derselbe Titel kann schon ohne Dateibezug gemerkt
                        # sein (ueber Roon oder AirPlay). Dann wird dieser
                        # Eintrag um den Pfad ergaenzt, statt eine Dublette
                        # anzulegen.
                        s_neu = self._schluessel(titel, inhalt.get("artist"))
                        for r in con.execute(
                                "SELECT id, titel, artist FROM favorit "
                                "WHERE pfad IS NULL"):
                            if self._schluessel(r["titel"], r["artist"]) == s_neu:
                                con.execute(
                                    "UPDATE favorit SET pfad=?, album_id=?,"
                                    " res_url=coalesce(?, res_url),"
                                    " didl=coalesce(?, didl) WHERE id=?",
                                    (pfad, inhalt.get("album_id"),
                                     inhalt.get("res_url"),
                                     inhalt.get("didl_roh"), r["id"]))
                                log.info("Merkliste: %r um den Dateibezug "
                                         "ergaenzt statt doppelt angelegt", titel)
                                return True, None
                else:
                    # Ohne Pfad über die Textfelder erkennen
                    vorhanden = con.execute(
                        "SELECT id FROM favorit WHERE pfad IS NULL AND "
                        "ifnull(titel,'')=? AND ifnull(artist,'')=?",
                        (titel or "", inhalt.get("artist") or "")).fetchone()
                if vorhanden:
                    con.execute("DELETE FROM favorit WHERE id=?", (vorhanden["id"],))
                    return False, None
                # Die res-URL und die Originalmetadaten mitschreiben.
                #
                # Das ist der verlaessliche Weg, den Titel spaeter wieder in
                # die Warteschlange zu legen: die URL wird NICHT nachgebaut,
                # sondern ist genau die, die der Player schon einmal
                # abgespielt hat. Eine Nachbildung muesste die Kodierung von
                # MinimServer erraten - dieselbe Annahme, die bei *c2*b0
                # schon einmal falsch war.
                con.execute(
                    "INSERT INTO favorit (pfad, album_id, artist, album, titel,"
                    " dauer_s, quelle, res_url, didl, gesetzt_am)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (pfad, inhalt.get("album_id"), inhalt.get("artist"),
                     inhalt.get("album"), titel,
                     (inhalt.get("zeit") or {}).get("dauer_s"),
                     (inhalt.get("quelle") or {}).get("name"),
                     inhalt.get("res_url"), inhalt.get("didl_roh"), time.time()))
                return True, None
        except Exception as e:
            log.warning("Favorit nicht speicherbar: %s", e)
            return None, str(e)

    def setzen_pfad(self, pfad, album_id=None):
        """Favorit für eine Datei aus der Trackliste - ohne sie zu spielen.
        Die Angaben werden aus dem Index geholt, nicht aus der Anzeige.

        Nimmt absolute und relative Pfade an; ein Pfad ausserhalb der
        Bibliothek wird abgewiesen."""
        pfad = self.relativ(pfad)
        if not pfad:
            return None, "Pfad außerhalb der Bibliothek."
        try:
            with self._con() as con:
                vorhanden = con.execute("SELECT id FROM favorit WHERE pfad=?",
                                        (pfad,)).fetchone()
                if vorhanden:
                    con.execute("DELETE FROM favorit WHERE id=?", (vorhanden["id"],))
                    return False, None
                r = con.execute(
                    "SELECT t.titel, t.dauer_s, t.album_id, a.artist, a.album "
                    "FROM track t JOIN album a ON a.id = t.album_id "
                    "WHERE t.pfad=?", (pfad,)).fetchone()
                if r is None:
                    return None, "Titel steht nicht im Index."
                con.execute(
                    "INSERT INTO favorit (pfad, album_id, artist, album, titel,"
                    " dauer_s, quelle, gesetzt_am) VALUES (?,?,?,?,?,?,?,?)",
                    (pfad, r["album_id"], r["artist"], r["album"], r["titel"],
                     r["dauer_s"], "Bibliothek", time.time()))
                return True, None
        except Exception as e:
            log.warning("Favorit nicht speicherbar: %s", e)
            return None, str(e)

    def adresse_merken(self, pfad, res_url, didl=None):
        """Schreibt eine nachträglich aufgelöste res-URL fest.

        So muss ein über Roon gemerkter Titel nur einmal in der Bibliothek
        gesucht werden; danach ist er direkt einreihbar."""
        if not res_url:
            return
        try:
            with self._con() as con:
                if pfad:
                    con.execute("UPDATE favorit SET res_url=?, didl=? WHERE pfad=?",
                                (res_url, didl, pfad))
                else:
                    con.execute("UPDATE favorit SET res_url=?, didl=? "
                                "WHERE pfad IS NULL AND res_url IS NULL",
                                (res_url, didl))
        except Exception as e:
            log.warning("Adresse nicht speicherbar: %s", e)

    # ------------------------------------------------------------------
    @staticmethod
    def _schluessel(titel, artist):
        """Vergleichsform fuer Dubletten: Gross-/Kleinschreibung,
        Mehrfachleerzeichen und Klammerzusaetze spielen keine Rolle.
        "Hair" und "Hair (Remastered)" gelten als derselbe Titel."""
        def norm(s):
            s = (s or "").lower()
            s = re.sub(r"[\(\[].*?[\)\]]", " ", s)      # Klammerzusaetze
            s = re.sub(r"[^a-z0-9äöüß ]+", " ", s)
            return re.sub(r"\s+", " ", s).strip()
        return norm(titel), norm(artist)

    def dubletten_zusammenfuehren(self):
        """Fuehrt Eintraege zusammen, die denselben Titel meinen.

        Der haeufige Fall: ein Titel wird gemerkt, waehrend er ueber Roon
        laeuft (kein Dateibezug, `pfad` ist NULL), und spaeter noch einmal
        aus der Trackliste (mit Pfad). Beides ist derselbe Titel.

        Behalten wird der reichere Eintrag - Pfad schlaegt keinen Pfad,
        vorhandene res-URL schlaegt keine. Das aelteste Merkdatum bleibt
        erhalten, damit die Reihenfolge der Liste stimmt, und die
        Zaehlung der Quelle geht nicht verloren.

        Gibt die Zahl der entfernten Eintraege zurueck."""
        entfernt = 0
        try:
            with self._con() as con:
                zeilen = [dict(r) for r in con.execute(
                    "SELECT * FROM favorit ORDER BY gesetzt_am")]
                gruppen = {}
                for z in zeilen:
                    gruppen.setdefault(
                        self._schluessel(z["titel"], z["artist"]), []).append(z)

                for schluessel, gruppe in gruppen.items():
                    if len(gruppe) < 2 or not any(schluessel):
                        continue
                    # Den reichsten Eintrag als Bleibenden waehlen
                    def guete(z):
                        return (1 if z.get("pfad") else 0,
                                1 if z.get("res_url") else 0,
                                1 if z.get("album_id") else 0,
                                1 if z.get("didl") else 0)
                    gruppe.sort(key=guete, reverse=True)
                    bleibt, weg = gruppe[0], gruppe[1:]

                    # Fehlende Angaben aus den anderen uebernehmen
                    aenderung = {}
                    for feld in ("pfad", "album_id", "res_url", "didl",
                                 "dauer_s", "album", "quelle"):
                        if not bleibt.get(feld):
                            for z in weg:
                                if z.get(feld):
                                    aenderung[feld] = z[feld]
                                    break
                    aelteste = min(z["gesetzt_am"] for z in gruppe)
                    if aelteste < bleibt["gesetzt_am"]:
                        aenderung["gesetzt_am"] = aelteste

                    if aenderung:
                        felder = ", ".join("%s=?" % k for k in aenderung)
                        con.execute("UPDATE favorit SET %s WHERE id=?" % felder,
                                    list(aenderung.values()) + [bleibt["id"]])
                    for z in weg:
                        con.execute("DELETE FROM favorit WHERE id=?", (z["id"],))
                        entfernt += 1
                    log.info("Merkliste: %d Dublette(n) von %r zusammengefuehrt",
                             len(weg), bleibt.get("titel"))
        except Exception as e:
            log.warning("Dubletten nicht zusammenfuehrbar: %s", e)
        return entfernt

    def liste(self, grenze=2000):
        try:
            with self._con() as con:
                return [dict(r) for r in con.execute(
                    "SELECT * FROM favorit ORDER BY gesetzt_am DESC LIMIT ?",
                    (grenze,))]
        except Exception as e:
            log.warning("Favoritenliste nicht lesbar: %s", e)
            return []

    def pfade_des_albums(self, album_id):
        """Welche Titel eines Albums sind gemerkt - für die Trackliste."""
        if not album_id:
            return []
        try:
            with self._con() as con:
                return [r["pfad"] for r in con.execute(
                    "SELECT pfad FROM favorit WHERE album_id=? AND pfad IS NOT NULL",
                    (album_id,))]
        except Exception as e:
            log.warning("Album-Favoriten nicht lesbar: %s", e)
            return []

    # ------------------------------------------------------------------
    def m3u(self, musikwurzel):
        """Erzeugt eine erweiterte M3U. Wird bei Abruf gebaut und NICHT
        gespeichert - schon gar nicht in den Musikordnern.

        Titel ohne Datei (AirPlay, Radio) werden als Kommentar geführt, damit
        nichts unbemerkt verschwindet."""
        zeilen = ["#EXTM3U"]
        fehlend = []
        for f in self.liste(grenze=10000):
            if not f["pfad"]:
                fehlend.append(f)
                continue
            # In der M3U stehen absolute Pfade - Abspielprogramme brauchen sie.
            voll = os.path.join(musikwurzel or self.wurzel, f["pfad"])
            if not os.path.isfile(voll):
                fehlend.append(f)
                continue
            dauer = int(f["dauer_s"] or -1)
            wer = " – ".join(x for x in (f["artist"], f["titel"]) if x)
            zeilen.append("#EXTINF:%d,%s" % (dauer, wer))
            zeilen.append(voll)
        if fehlend:
            zeilen.append("")
            zeilen.append("# Nicht als Datei verfügbar (Quelle ohne "
                          "Bibliotheksdatei oder Datei verschoben):")
            for f in fehlend:
                zeilen.append("#   %s – %s  (%s)" % (
                    f["artist"] or "?", f["titel"] or "?", f["quelle"] or "?"))
        return "\n".join(zeilen) + "\n"
