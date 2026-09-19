#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
dr-scan.py – Misst den Dynamic Range aller Alben und legt ihn in liner.db ab.

WERKZEUGWAHL (geprueft 2026-09-17)
==================================
`dr14_t.meter` ist die Referenz der Pleasurize-Methode, aber nicht mehr
beziehbar: nicht in Debian, nicht auf PyPI, und die GitHub-Fassung (2.0.0)
laeuft mit numpy 2.x nicht mehr (`numpy.fromstring` wurde entfernt).

Deshalb rechnet bin/dr.py die Methode selbst - mit ffmpeg zum Dekodieren und
numpy zum Rechnen, beides ohnehin vorhanden. Die Umsetzung wurde gegen die
(gepatchte) Referenz validiert: identische Werte, DR12 und DR14 bei zwei
Tracks von "The Wall".

`musik_master.py dr` ist fuer diesen Zweck nicht verwendbar - es liest ffmpegs
`ebur128`-LRA (Lautheitsverteilung in LU) und nur den ersten Track je Album.

RUECKSICHT AUF DEN LAUFENDEN BETRIEB
====================================
Das Messen liest jede Datei vollstaendig und dekodiert sie - das ist der
teuerste Vorgang im ganzen Projekt. Damit die Wiedergabe ueber MinimServer
nie stockt:

  * nice 19 und ionice -c3 (Leerlaufklasse) fuer jeden ffmpeg-Aufruf
  * begrenzte Parallelitaet, Standard 2 von 8 Kernen
  * unterbrechbar: SIGINT/SIGTERM beendet nach dem laufenden Album
  * fortsetzbar: bereits gemessene Alben werden ueber Pfad + mtime erkannt

NICHTS WIRD GESCHRIEBEN ausser in die Datenbank. Kein Tag, keine dr14.txt.
"""
import argparse
import concurrent.futures as futures
import os
import signal
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr as drmod
import pathlib

# Konfiguration laden, wenn dieses Werkzeug von Hand laeuft: der Dienst
# bekommt seine Einstellungen von systemd, ein Aufruf aus der Shell nicht.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
try:
    import umgebung as _umgebung
    _umgebung.laden()
except Exception:
    pass


DB = os.environ.get("LINER_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "liner.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS dr_track (
  track_id INTEGER PRIMARY KEY REFERENCES track(id) ON DELETE CASCADE,
  dr REAL, peak_db REAL, rms_db REAL, gemessen_am REAL
);
CREATE TABLE IF NOT EXISTS dr_album (
  album_id  INTEGER PRIMARY KEY REFERENCES album(id) ON DELETE CASCADE,
  dr        INTEGER,            -- gerundet, wie die Dynamic Range Database
  dr_exakt  REAL,
  dr_min    INTEGER, dr_max INTEGER,
  peak_db   REAL, rms_db REAL,
  tracks    INTEGER,            -- wie viele Tracks eingingen
  mtime     REAL,               -- Stand der Dateien bei der Messung
  gemessen_am REAL
);
"""

abbruch = False


def verbinden(db):
    con = sqlite3.connect(db, timeout=60, isolation_level=None)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def with_retry(con, aktion, versuche=40, pause=3.0):
    """Fuehrt eine Datenbankaktion aus und wartet, wenn ein anderer Prozess
    gerade schreibt. 40 Versuche mal 3 s deckt auch einen laengeren
    Anreicherungslauf ab."""
    for n in range(versuche):
        try:
            return aktion(con)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            if n == 0:
                print("  Datenbank belegt (anderer Lauf schreibt) – warte …", flush=True)
            time.sleep(pause)
    raise sqlite3.OperationalError("Datenbank blieb %.0f s belegt" % (versuche * pause))


def _signal(sig, rahmen):
    global abbruch
    abbruch = True
    print("\n  Abbruch angefordert – beende nach dem laufenden Album …", flush=True)


def messen(pfad):
    try:
        return pfad, drmod.dr_datei(pfad), None
    except Exception as e:
        return pfad, None, str(e)[:160]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--jobs", type=int, default=2, help="parallele Dekodierungen (Standard 2)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--neu", action="store_true", help="auch bereits gemessene erneut messen")
    ap.add_argument("--album", default="", help="nur Alben, deren Ordner diesen Text enthaelt")
    a = ap.parse_args()

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    root = os.environ.get("LINER_MUSIC_ROOT", "/pfad/zur/musik")
    con = verbinden(a.db)
    # Schema anlegen kann am Schreib-Lock eines parallel laufenden
    # Anreicherungslaufs scheitern (WAL erlaubt genau einen Schreiber, und
    # CREATE TABLE braucht den Schema-Lock). Deshalb mit Wiederholung statt
    # mit Abbruch - der Lauf soll sich um andere Prozesse nicht kuemmern
    # muessen.
    with_retry(con, lambda c: c.executescript(SCHEMA))

    bedingung = "WHERE a.ordner LIKE ?" if a.album else ""
    parameter = ("%%%s%%" % a.album,) if a.album else ()
    alben = con.execute(
        "SELECT a.id, a.ordner, a.mtime, a.artist, a.album FROM album a %s ORDER BY a.id"
        % bedingung, parameter).fetchall()

    offen = []
    for aid, ordner, mtime, artist, album in alben:
        if not a.neu:
            row = con.execute("SELECT mtime FROM dr_album WHERE album_id=?", (aid,)).fetchone()
            if row and row[0] and abs(row[0] - (mtime or 0)) < 1:
                continue
        offen.append((aid, ordner, mtime, artist, album))
    if a.limit:
        offen = offen[:a.limit]

    print("  Alben gesamt %d · zu messen %d · parallel %d" % (len(alben), len(offen), a.jobs))
    if not offen:
        print("  nichts zu tun."); return

    t0 = time.time()
    fertig = 0
    for aid, ordner, mtime, artist, album in offen:
        if abbruch:
            break
        tracks = con.execute("SELECT id, pfad FROM track WHERE album_id=? ORDER BY disc, nr",
                             (aid,)).fetchall()
        pfade = [(tid, os.path.join(root, p)) for tid, p in tracks]
        pfade = [(tid, p) for tid, p in pfade if os.path.isfile(p)]
        if not pfade:
            continue

        ergebnisse = {}
        with futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for pfad, wert, fehler in pool.map(messen, [p for _, p in pfade]):
                ergebnisse[pfad] = (wert, fehler)

        werte = []
        for tid, p in pfade:
            wert, fehler = ergebnisse.get(p, (None, "nicht gemessen"))
            if not wert:
                continue
            werte.append(wert)
            with_retry(con, lambda c, _t=tid, _w=wert: c.execute(
                """INSERT OR REPLACE INTO dr_track(track_id,dr,peak_db,rms_db,gemessen_am)
                   VALUES(?,?,?,?,?)""",
                (_t, _w["dr"], _w["peak_db"], _w["rms_db"], time.time())))
        if werte:
            drs = [w["dr"] for w in werte]
            mittel = sum(drs) / len(drs)
            daten = (aid, int(round(mittel)), mittel,
                     int(round(min(drs))), int(round(max(drs))),
                     max(w["peak_db"] for w in werte),
                     sum(w["rms_db"] for w in werte) / len(werte),
                     len(werte), mtime, time.time())
            with_retry(con, lambda c, _d=daten: c.execute(
                """INSERT OR REPLACE INTO dr_album
                   (album_id,dr,dr_exakt,dr_min,dr_max,peak_db,rms_db,tracks,mtime,gemessen_am)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""", _d))
        fertig += 1
        if fertig % 5 == 0 or fertig == len(offen):
            verstrichen = time.time() - t0
            rest = (len(offen) - fertig) * verstrichen / fertig
            print("  %d/%d  DR%-3s %-40s  noch ~%.0f min"
                  % (fertig, len(offen), int(round(mittel)) if werte else "-",
                     ("%s – %s" % (artist, album))[:40], rest / 60), flush=True)

    print()
    print("  gemessen: %d Alben in %.1f min" % (fertig, (time.time() - t0) / 60))
    if abbruch:
        print("  abgebrochen – ein erneuter Lauf setzt fort.")
    con.close()


if __name__ == "__main__":
    main()
