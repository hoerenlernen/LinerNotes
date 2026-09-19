#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
index.py – Leichter Index der Musikbibliothek als Fundament fuer Liner Notes.

ZWECK
=====
Zwei Aufgaben in einer Datenbank:

  1. Schnelles Auffinden von Album und Trackliste, wenn der Linn einen Titel
     meldet. Der Weg ueber die MinimServer-res-URL ist der Normalfall; faellt
     der aus (Roon, AirPlay, umbenannte Datei), greift das Fallback-Matching
     ueber Artist/Album/Track gegen diesen Index.
  2. Sidecar-Speicher fuer angereicherte Daten (MusicBrainz, Cover Art,
     Wikipedia, Last.fm). Diese landen NICHT in den Musikdateien - die
     Bibliothek bleibt unangetastet, die Anreicherung ist jederzeit
     verwerfbar und neu aufbaubar.

Inkrementell: Alben werden nur neu gelesen, wenn sich die mtime des Ordners
oder einer Datei geaendert hat. Erster Lauf ueber 3.323 Alben dauert rund
eine Minute, danach Sekunden.

NUR LESEND auf /pfad/zur/musik.
"""
import argparse, glob, os, sqlite3, sys, time, unicodedata, re
from mutagen.flac import FLAC
import sys
import pathlib

# Konfiguration laden, wenn dieses Werkzeug von Hand laeuft: der Dienst
# bekommt seine Einstellungen von systemd, ein Aufruf aus der Shell nicht.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
try:
    import umgebung as _umgebung
    _umgebung.laden()
except Exception:
    pass


ROOT = os.environ.get("LINER_MUSIC_ROOT", "/pfad/zur/musik")
DB   = os.environ.get("LINER_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "liner.db"))

SCHEMA = pathlib.Path(__file__).resolve().parent.parent.joinpath("app/schema.sql").read_text()


# Ordnernamen, die keine eigenstaendigen Alben sind, sondern Teil eines
# Mehr-Disc-Albums. 83 Alben der Bibliothek liegen so (Stand 2026-09-17).
# Ohne diese Erkennung erscheint "The Wall" als zwei Alben mit je 13 Titeln
# statt als eines mit 26 - und die Trackliste in der Anzeige ist halb leer.
DISC_MUSTER = re.compile(r"^(disc|disk|cd|vol(ume)?)[\s._-]*(\d+)$", re.I)


def disc_aus_ordner(name):
    """Liefert die Disc-Nummer, wenn der Ordnername ein Disc-Unterordner ist."""
    m = DISC_MUSTER.match(name.strip())
    return int(m.group(3)) if m else None


def norm(s):
    if not s: return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def ersterwert(tags, *keys):
    for k in keys:
        v = tags.get(k)
        if v: return v[0]
    return ""


def album_ordner():
    """Liefert (rel, absolut, flac-Pfade, max-mtime) je Ordner mit FLAC-Dateien.

    Nutzt os.scandir statt glob+getmtime: scandir liefert die stat-Daten aus
    dem Verzeichnis-Listing mit, wodurch pro Datei kein eigener Systemaufruf
    mehr faellig wird. Ueber 46.841 Dateien macht das den Unterschied zwischen
    gut vier Minuten und wenigen Sekunden.

    Deckt flache Alben UND Disc-Unterordner ab; eine lose Datei in der Wurzel
    erscheint als Ordner '.'."""
    stapel = [ROOT]
    while stapel:
        d = stapel.pop()
        flacs, mtimes = [], []
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not os.path.basename(e.path).startswith("_"):
                                stapel.append(e.path)
                        elif e.name.lower().endswith(".flac"):
                            flacs.append(e.path)
                            mtimes.append(e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
        if not flacs:
            continue
        rel = os.path.relpath(d, ROOT)
        if rel.split(os.sep)[0].startswith("_"):
            continue
        try:
            mtimes.append(os.stat(d).st_mtime)
        except OSError:
            pass
        yield rel, d, sorted(flacs), max(mtimes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="alle Alben neu einlesen")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()

    if not os.path.isdir(ROOT):
        ap.error("LINER_MUSIC_ROOT ist kein lesbares Verzeichnis; Index bleibt unverändert")
    os.makedirs(os.path.dirname(os.path.abspath(a.db)), exist_ok=True)
    con = sqlite3.connect(a.db)
    con.executescript(SCHEMA)
    con.execute("PRAGMA foreign_keys=ON")

    import json
    t0 = time.time()
    neu = akt = unveraendert = 0
    jetzt = time.time()

    # Disc-Unterordner werden zu ihrem Elternordner zusammengefasst, damit
    # ein Mehr-Disc-Album EIN Album bleibt.
    gruppen = {}
    for rel, absdir, flacs, mtime in album_ordner():
        discnr = disc_aus_ordner(os.path.basename(rel))
        schluessel = os.path.dirname(rel) if discnr and os.path.dirname(rel) else rel
        eintrag = gruppen.setdefault(schluessel, {"flacs": [], "mtime": 0.0, "absdir": absdir})
        eintrag["flacs"].extend((p, discnr) for p in flacs)
        eintrag["mtime"] = max(eintrag["mtime"], mtime)
        if discnr and os.path.dirname(rel):
            # Fuer Cover und Tags den Elternordner heranziehen
            eintrag["absdir"] = os.path.dirname(absdir)

    for rel, g in gruppen.items():
        absdir = g["absdir"]
        flacs = [p for p, _ in sorted(g["flacs"], key=lambda x: (x[1] or 0, x[0]))]
        disc_von_pfad = dict(g["flacs"])
        mtime = g["mtime"]

        row = con.execute("SELECT id, mtime FROM album WHERE ordner=?", (rel,)).fetchone()
        if row and not a.force and abs(row[1] - mtime) < 1:
            con.execute("UPDATE album SET gesehen=? WHERE id=?", (jetzt, row[0]))
            unveraendert += 1
            continue

        try:
            f0 = FLAC(flacs[0])
        except Exception as e:
            print("  WARNUNG %s: %s" % (rel, e), file=sys.stderr); continue
        at = {k.lower(): list(v) for k, v in (f0.tags or {}).items()}
        artist = ersterwert(at, "albumderartist", "albumartist", "artist")
        album  = ersterwert(at, "album")
        cover  = None
        # Cover liegt bei Mehr-Disc-Alben oft nur im Elternordner
        for basis in (absdir, os.path.dirname(flacs[0])):
            for pat in ("cover.jpg", "folder.jpg", "cover.jpeg", "front.jpg"):
                gg = glob.glob(os.path.join(glob.escape(basis), pat))
                if gg:
                    cover = os.path.relpath(gg[0], ROOT); break
            if cover: break

        gesamt = 0.0
        tracks = []
        for p in flacs:
            try: ff = FLAC(p)
            except Exception: continue
            tt = {k.lower(): list(v) for k, v in (ff.tags or {}).items()}
            def zahl(x):
                m = re.match(r"(\d+)", x or "");  return int(m.group(1)) if m else None
            gesamt += ff.info.length
            tracks.append((os.path.relpath(p, ROOT),
                           zahl(ersterwert(tt, "discnumber")) or disc_von_pfad.get(p) or 1,
                           zahl(ersterwert(tt, "tracknumber")), ersterwert(tt, "title"),
                           norm(ersterwert(tt, "title")), round(ff.info.length, 1),
                           ff.info.bits_per_sample, ff.info.sample_rate,
                           json.dumps(tt, ensure_ascii=False)))

        werte = (rel, artist, album, ersterwert(at, "date"), ersterwert(at, "genre"),
                 ersterwert(at, "label"), norm(artist), norm(album),
                 ersterwert(at, "musicbrainz_albumid"),
                 f0.info.bits_per_sample, f0.info.sample_rate,
                 len(tracks), round(gesamt, 1), cover,
                 json.dumps(at, ensure_ascii=False), mtime, jetzt)
        if row:
            con.execute("""UPDATE album SET artist=?,album=?,jahr=?,genre=?,label=?,
                           k_artist=?,k_album=?,mbid=?,bits=?,samplerate=?,tracks=?,dauer_s=?,
                           cover_datei=?,tags_json=?,mtime=?,gesehen=? WHERE id=?""",
                        werte[1:] + (row[0],))
            aid = row[0]
            con.execute("DELETE FROM track WHERE album_id=?", (aid,))
            akt += 1
        else:
            cur = con.execute("""INSERT INTO album(ordner,artist,album,jahr,genre,label,
                                 k_artist,k_album,mbid,bits,samplerate,tracks,dauer_s,
                                 cover_datei,tags_json,mtime,gesehen)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", werte)
            aid = cur.lastrowid
            neu += 1
        con.executemany("""INSERT OR REPLACE INTO track(album_id,pfad,disc,nr,titel,k_titel,
                           dauer_s,bits,samplerate,tags_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        [(aid,) + t for t in tracks])
        if (neu + akt) % 500 == 0:
            con.commit(); print("  %d Alben …" % (neu + akt), flush=True)

    # Verschwundene Alben entfernen
    weg = con.execute("DELETE FROM album WHERE gesehen < ?", (jetzt - 1,)).rowcount
    con.commit()

    n_al = con.execute("SELECT COUNT(*) FROM album").fetchone()[0]
    n_tr = con.execute("SELECT COUNT(*) FROM track").fetchone()[0]
    print()
    print("  neu %d · aktualisiert %d · unveraendert %d · entfernt %d" % (neu, akt, unveraendert, weg))
    print("  Index: %d Alben, %d Tracks" % (n_al, n_tr))
    print("  DB: %s (%.1f MB)" % (a.db, os.path.getsize(a.db) / 1048576))
    print("  Laufzeit %.1f s" % (time.time() - t0))
    con.close()


if __name__ == "__main__":
    main()
