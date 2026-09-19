#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Nie gesendete Wiedergaben einmalig an Last.fm nachtragen.

Hintergrund: Zwischen dem 26.03.2026 (Master-Schalter
EnableExternalServices auf false) und dem 18.09.2026 hat Navidrome jede
Wiedergabe brav mitgeschrieben, aber keine gesendet. Diese Mitschriften
holen wir einmalig nach.

Grenzen, die dabei zu beachten sind - und die das Ergebnis erklaeren:

  * Navidrome speichert je Titel nur `play_date`, den LETZTEN Zeitpunkt,
    plus einen Zaehler. Wer einen Titel fuenfmal gehoert hat, kann also
    nur einmal nachgetragen werden - mehr Information existiert nicht.
  * Last.fm nimmt nur Scrobbles der letzten rund 14 Tage an. Nachgewiesen
    am 18.09.2026: von 160 Mitschriften seit dem 26.03. wurden 9
    angenommen (alle aus den letzten sieben Tagen), 151 abgelehnt. Alles
    Aeltere ist endgueltig verloren - Last.fm bietet keinen Weg, es
    nachzutragen.
    Die Zeitstempel werden NICHT geschoent, um die Annahmequote zu
    heben: ein vorgetaeuschter Hoerzeitpunkt waere schlimmer als eine
    Luecke.

Aufruf:
  scrobble-nachtragen.py --ab 2026-03-26 [--schreiben]
"""
import argparse
import calendar
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import umgebung          # laedt ~/.config/liner/liner.env  # noqa: E402
umgebung.laden()
import scrobbeln         # noqa: E402

NAVIDROME_DB = os.environ.get(
    "NAVIDROME_DB", "/var/lib/navidrome/navidrome.db")


def wiedergaben(db_pfad, ab, benutzer="gr"):
    """Titel mit Wiedergabedatum ab `ab`, aufsteigend nach Zeit."""
    con = sqlite3.connect("file:%s?mode=ro" % db_pfad, uri=True)
    con.row_factory = sqlite3.Row
    zeilen = con.execute("""
        SELECT m.artist, m.title, m.album, m.album_artist, m.duration,
               m.track_number, m.mbz_recording_id, a.play_date, a.play_count
        FROM annotation a
        JOIN media_file m ON m.id = a.item_id
        JOIN user u ON u.id = a.user_id
        WHERE a.item_type = 'media_file'
          AND a.play_date IS NOT NULL
          AND a.play_date >= ?
          AND u.user_name = ?
        ORDER BY a.play_date
    """, (ab, benutzer)).fetchall()
    con.close()
    return zeilen


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ab", default="2026-03-26",
                   help="nur Wiedergaben ab diesem Datum (Standard: 2026-03-26,"
                        " der Tag, an dem das Senden abgeschaltet wurde)")
    p.add_argument("--benutzer", default="gr")
    p.add_argument("--schreiben", action="store_true",
                   help="ohne diese Angabe nur zeigen, was passieren wuerde")
    p.add_argument("--db", default=NAVIDROME_DB)
    args = p.parse_args()

    zeilen = wiedergaben(args.db, args.ab, args.benutzer)
    print("Navidrome kennt %d Titel mit Wiedergabe ab %s" % (len(zeilen), args.ab))
    if not zeilen:
        return
    print("  Zeitraum: %s bis %s" % (zeilen[0]["play_date"][:19],
                                     zeilen[-1]["play_date"][:19]))
    mehrfach = sum(1 for z in zeilen if (z["play_count"] or 1) > 1)
    print("  davon mehrfach gehoert: %d (nachtragbar ist nur je ein Zeitpunkt)"
          % mehrfach)

    wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    liner_db = os.environ.get("LINER_DB",
                              os.path.join(wurzel, "data", "liner.db"))
    s = scrobbeln.Scrobbler(liner_db)
    if not s.zugang.vollstaendig:
        sys.exit("Zugang unvollstaendig - LASTFM_SCROBBLE_KEY/"
                 "LASTFM_SCROBBLE_SECRET/LASTFM_SESSION_KEY fehlen")

    ohne_angabe = eingereiht = 0
    for z in zeilen:
        artist = (z["artist"] or "").strip()
        titel = (z["title"] or "").strip()
        if not artist or not titel:
            ohne_angabe += 1
            continue
        # play_date steht als "2026-04-01 12:34:56[.xxx]" in UTC
        text = z["play_date"][:19]
        try:
            # play_date steht in LOKALZEIT, nicht in UTC. Nachgewiesen am
            # 18.09.2026: ein Scrobble um 17:08:53 CEST steht als
            # "2026-09-18 17:08:53" in der Spalte. mktime interpretiert
            # lokal und beruecksichtigt die Sommerzeit; timegm laege
            # zwei Stunden daneben.
            stempel = int(time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S")))
        except ValueError:
            ohne_angabe += 1
            continue
        eintrag = {
            "artist": artist, "titel": titel, "album": z["album"],
            "albumkuenstler": z["album_artist"],
            "dauer_s": int(z["duration"] or 0) or None,
            "tracknummer": z["track_number"] or None,
            "mbid": z["mbz_recording_id"] or None,
            "zeitstempel": stempel, "quelle": "nachtrag",
        }
        if args.schreiben:
            if s._einreihen(eintrag):
                eingereiht += 1
        else:
            eingereiht += 1
            if eingereiht <= 5:
                print("    Beispiel: %s | %s - %s (%s)"
                      % (time.strftime("%Y-%m-%d %H:%M", time.localtime(stempel)),
                         artist[:24], titel[:30], (z["album"] or "")[:22]))

    print("\n  einreihbar: %d, ohne Kuenstler/Titel uebersprungen: %d"
          % (eingereiht, ohne_angabe))

    if not args.schreiben:
        print("\nPROBELAUF - nichts eingereiht, nichts gesendet."
              " Mit --schreiben ausfuehren.")
        return

    print("\nsende in Stapeln von 50 ...")
    angenommen, ignoriert, offen = s.abschicken(hoechstens=10000)
    print("  angenommen: %d" % angenommen)
    print("  abgelehnt : %d" % ignoriert)
    print("  offen     : %d" % offen)


if __name__ == "__main__":
    main()
