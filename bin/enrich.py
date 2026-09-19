#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
enrich.py – Holt Zusatzdaten zu Alben und legt sie in der Sidecar-Datenbank ab.

WARUM SIDECAR UND NICHT IN DIE DATEIEN
======================================
Die Musikbibliothek (2,3 TB, 46.841 Dateien) wird hier NICHT angefasst.
Externe Daten landen in liner.db und sind damit jederzeit verwerfbar, neu
aufbaubar und ohne Rescan aktualisierbar. Nur die rein faktischen Felder
(MBID, Katalognummer, Barcode, Land) werden spaeter durch writeback.py in die
Dateien geschrieben - und nur, weil sie dann auch in Kazoo sichtbar sind und
als Wiedererkennungs-Schluessel fuer tagvault.py dienen.

Gorans kuratierte Felder (comment, description, mood, personnel,
listening_notes) werden NIE ueberschrieben und nie durch Fremdtext ergaenzt.
Externe Prosa bleibt getrennt und wird in der Anzeige als solche markiert.

QUELLEN
-------
  musicbrainz  Fakten: MBID, Katalognummer, Barcode, Land, Datum, Medium, Label
  coverart     hochauflaesendes Cover ueber die MBID (die eigenen sind 600x600)
  wikipedia    Album-Zusammenfassung als Fliesstext
  lastfm       Album-Info, Tags, Kuenstler-Bio
  discogs      Credits/Besetzung - nur aktiv, wenn DISCOGS_TOKEN gesetzt ist

RATE LIMITS
===========
MusicBrainz erlaubt 1 Anfrage/Sekunde und verlangt einen eigenen User-Agent.
Beobachtet wurden trotzdem sporadische 503 - unabhaengig davon, ob der Weg
ueber Mullvad oder direkt laeuft. Deshalb 1,3 s Abstand und Wiederholung mit
wachsender Wartezeit. Ergebnisse werden dauerhaft gespeichert; ein zweiter
Lauf holt nichts erneut.

Fortsetzbar: Abbruch jederzeit moeglich, der naechste Lauf macht weiter.
"""
import argparse, json, os, random, sqlite3, sys, time, urllib.parse, urllib.request, urllib.error
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


DB   = os.environ.get("LINER_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "liner.db"))
ROOT = os.environ.get("LINER_MUSIC_ROOT", "/pfad/zur/musik")
COVERDIR = os.environ.get("LINER_COVERDIR", "./data/cover")
UA   = os.environ.get("MB_USER_AGENT", "LinerNotes/0.1 ( +https://github.com/hoerenlernen/liner )")
LASTFM = os.environ.get("LASTFM_API_KEY", "")
DISCOGS = os.environ.get("DISCOGS_TOKEN", "")

letzte = {}

def hole(url, pause=1.3, versuche=4, kopf=None, raw=False, schluessel="mb"):
    """GET mit Rate-Limit je Quelle und Backoff bei 503/429."""
    global letzte
    kopf = dict(kopf or {})
    kopf.setdefault("User-Agent", UA)
    for n in range(versuche):
        seit = time.time() - letzte.get(schluessel, 0)
        if seit < pause:
            time.sleep(pause - seit)
        letzte[schluessel] = time.time()
        try:
            r = urllib.request.Request(url, headers=kopf)
            with urllib.request.urlopen(r, timeout=20) as f:
                d = f.read()
            return d if raw else json.loads(d.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (503, 429, 502, 504) and n < versuche - 1:
                time.sleep((2 ** n) + random.random())
                continue
            if e.code == 404:
                return None
            raise
        except Exception:
            if n < versuche - 1:
                time.sleep((2 ** n) + random.random())
                continue
            raise
    return None


# Zusaetze, die MusicBrainz-Treffer verhindern. Beobachtet 2026-09-17:
# "Baduizm - Special Edition" findet nichts, "Baduizm" dagegen Score 100.
# Solche Klammer- und Strich-Zusaetze stehen in dieser Bibliothek haeufig im
# Albumtitel, weil sie die Pressung unterscheiden - fuer die Suche sind sie
# Ballast.
import re as _re
ZUSATZ = _re.compile(
    r"\s*[\(\[][^\)\]]*("
    r"remaster(ed)?|remix|edition|version|deluxe|expanded|anniversary|"
    r"bonus|reissue|mono|stereo|explicit|clean|single|ep|live at"
    r")[^\)\]]*[\)\]]"
    r"|\s+-\s+(special|deluxe|expanded|remastered|anniversary)\s+edition\s*$",
    _re.I)


def titel_varianten(album):
    """Liefert den Titel und - falls Zusaetze erkannt wurden - die bereinigte
    Kurzform als zweiten Versuch."""
    yield album
    kurz = ZUSATZ.sub("", album).strip(" -–—")
    kurz = _re.sub(r"\s{2,}", " ", kurz)
    if kurz and kurz.lower() != album.lower():
        yield kurz


def mb_release(artist, album):
    """Sucht das Release und liefert die faktischen Felder.
    Versucht zuerst den vollen Titel, dann die um Pressungs-Zusaetze
    bereinigte Kurzform."""
    beste = None
    for kandidat in titel_varianten(album):
        q = 'artist:"%s" AND release:"%s"' % (artist.replace('"', ""), kandidat.replace('"', ""))
        d = hole("https://musicbrainz.org/ws/2/release?" + urllib.parse.urlencode(
            {"query": q, "fmt": "json", "limit": 5}), schluessel="mb")
        if d and d.get("releases"):
            kand = d["releases"][0]
            if not beste or kand.get("score", 0) > beste.get("score", 0):
                beste = kand
                beste["_suchtitel"] = kandidat
            if beste.get("score", 0) >= 95:
                break
    if not beste:
        return None
    if beste.get("score", 0) < 90:
        return {"_score": beste.get("score", 0), "_unsicher": True,
                "titel": beste.get("title"), "mbid": beste.get("id")}
    li = (beste.get("label-info") or [{}])[0]
    medien = beste.get("media") or [{}]
    return {
        "_score": beste.get("score"),
        "_suchtitel": beste.get("_suchtitel"),
        "mbid": beste.get("id"),
        "titel": beste.get("title"),
        "release_group": (beste.get("release-group") or {}).get("id"),
        "catalognumber": li.get("catalog-number"),
        "label": (li.get("label") or {}).get("name"),
        "barcode": beste.get("barcode"),
        "country": beste.get("country"),
        "date": beste.get("date"),
        "status": beste.get("status"),
        "medium": medien[0].get("format"),
        "discs": len(medien),
        "trackzahl": beste.get("track-count"),
    }


def coverart(mbid, album_id, con, mindest_breite=700):
    """Holt das groesste verfuegbare Cover vom Cover Art Archive.

    Uebernimmt nur, was besser ist als das vorhandene: Die eigenen cover.jpg
    liegen im Median bei 600x600, gelegentlich liefert das Archiv aber nur
    500x494 - das waere ein Rueckschritt. Alles unter mindest_breite wird
    daher verworfen."""
    d = hole("https://coverartarchive.org/release/%s" % mbid, pause=1.0, schluessel="caa")
    if not d or not d.get("images"):
        return None
    front = next((i for i in d["images"] if i.get("front")), d["images"][0])
    tn = front.get("thumbnails", {})
    url = tn.get("1200") or tn.get("large") or front.get("image")
    if not url:
        return None
    roh = hole(url, pause=1.0, raw=True, schluessel="caa")
    if not roh:
        return None
    # Groesse aus dem JPEG-Header lesen, ohne Bildbibliothek
    import struct
    b, h = 0, 0
    i = 2
    while i < len(roh) - 9:
        if roh[i] != 0xFF:
            i += 1; continue
        m = roh[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3):
            h, b = struct.unpack(">HH", roh[i + 5:i + 9]); break
        if m in (0xD8, 0xD9):
            i += 2; continue
        i += 2 + struct.unpack(">H", roh[i + 2:i + 4])[0]
    if b and b < mindest_breite:
        return {"verworfen": True, "breite": b, "hoehe": h,
                "grund": "kleiner als %d px" % mindest_breite}
    os.makedirs(COVERDIR, exist_ok=True)
    ziel = os.path.join(COVERDIR, "%d.jpg" % album_id)
    with open(ziel, "wb") as f:
        f.write(roh)
    con.execute("""INSERT OR REPLACE INTO cover(album_id,quelle,breite,hoehe,bytes,datei,geholt_am)
                   VALUES(?,?,?,?,?,?,?)""",
                (album_id, "coverartarchive", b, h, len(roh), ziel, time.time()))
    return {"breite": b, "hoehe": h, "bytes": len(roh)}


def wikipedia(titel, sprachen=("en", "de")):
    for sp in sprachen:
        d = hole("https://%s.wikipedia.org/api/rest_v1/page/summary/%s"
                 % (sp, urllib.parse.quote(titel.replace(" ", "_"))),
                 pause=0.3, schluessel="wiki")
        if d and d.get("extract") and d.get("type") == "standard":
            return {"sprache": sp, "titel": d.get("title"),
                    "text": d["extract"], "url": (d.get("content_urls") or {}).get("desktop", {}).get("page")}
    return None


def lastfm(artist, album):
    if not LASTFM:
        return None
    d = hole("https://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode(
        {"method": "album.getinfo", "api_key": LASTFM, "artist": artist,
         "album": album, "format": "json", "autocorrect": "1"}),
        pause=0.3, schluessel="lfm")
    if not d or "album" not in d:
        return None
    a = d["album"]
    return {"name": a.get("name"), "artist": a.get("artist"),
            "listeners": a.get("listeners"), "playcount": a.get("playcount"),
            "tags": [t["name"] for t in (a.get("tags") or {}).get("tag", [])][:8],
            "wiki": (a.get("wiki") or {}).get("content", "")[:4000],
            "url": a.get("url")}


def discogs(artist, album):
    if not DISCOGS:
        return None
    d = hole("https://api.discogs.com/database/search?" + urllib.parse.urlencode(
        {"artist": artist, "release_title": album, "type": "release", "per_page": 3}),
        pause=1.1, kopf={"Authorization": "Discogs token=%s" % DISCOGS}, schluessel="dc")
    if not d or not d.get("results"):
        return None
    rid = d["results"][0]["id"]
    r = hole("https://api.discogs.com/releases/%d" % rid, pause=1.1,
             kopf={"Authorization": "Discogs token=%s" % DISCOGS}, schluessel="dc")
    if not r:
        return None
    credits = ["%s – %s" % (x.get("name", ""), x.get("role", "")) for x in (r.get("extraartists") or [])]
    return {"id": rid, "titel": r.get("title"), "jahr": r.get("year"),
            "labels": [l.get("name") for l in (r.get("labels") or [])],
            "catno": (r.get("labels") or [{}])[0].get("catno"),
            "formate": [f.get("name") for f in (r.get("formats") or [])],
            "land": r.get("country"), "notes": (r.get("notes") or "")[:2000],
            "credits": credits[:60], "url": r.get("uri")}


QUELLEN = {"musicbrainz", "coverart", "wikipedia", "lastfm", "discogs"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--limit", type=int, default=0, help="nur N Alben (0 = alle)")
    ap.add_argument("--quellen", default="musicbrainz,coverart,wikipedia,lastfm,discogs")
    ap.add_argument("--neu", action="store_true", help="auch schon geholte erneut abfragen")
    a = ap.parse_args()
    aktiv = [q for q in a.quellen.split(",") if q in QUELLEN]

    con = sqlite3.connect(a.db)
    con.execute("PRAGMA foreign_keys=ON")
    alben = con.execute("SELECT id, artist, album, ordner FROM album "
                        "WHERE artist<>'' AND album<>'' ORDER BY id").fetchall()
    if a.limit:
        alben = alben[:a.limit]

    stat = {q: {"ok": 0, "leer": 0, "fehler": 0, "cache": 0} for q in aktiv}
    t0 = time.time()
    for i, (aid, artist, album, ordner) in enumerate(alben, 1):
        vorhanden = {r[0] for r in con.execute(
            "SELECT quelle FROM enrich WHERE album_id=? AND status='ok'", (aid,))}
        mbid = None
        for q in aktiv:
            if q in vorhanden and not a.neu:
                stat[q]["cache"] += 1
                if q == "musicbrainz":
                    row = con.execute("SELECT daten FROM enrich WHERE album_id=? AND quelle='musicbrainz'", (aid,)).fetchone()
                    if row and row[0]:
                        mbid = (json.loads(row[0]) or {}).get("mbid")
                continue
            try:
                if q == "musicbrainz":
                    d = mb_release(artist, album)
                    if d: mbid = d.get("mbid")
                elif q == "coverart":
                    d = coverart(mbid, aid, con) if mbid else None
                elif q == "wikipedia":
                    d = wikipedia("%s (album)" % album) or wikipedia(album)
                elif q == "lastfm":
                    d = lastfm(artist, album)
                elif q == "discogs":
                    d = discogs(artist, album)
                st = "ok" if d else "leer"
                stat[q]["ok" if d else "leer"] += 1
            except Exception as e:
                d, st = {"fehler": str(e)[:200]}, "fehler"
                stat[q]["fehler"] += 1
            con.execute("""INSERT OR REPLACE INTO enrich(album_id,quelle,status,geholt_am,daten)
                           VALUES(?,?,?,?,?)""",
                        (aid, q, st, time.time(), json.dumps(d, ensure_ascii=False) if d else None))
        if i % 10 == 0:
            con.commit()
            verstrichen = time.time() - t0
            rest = (len(alben) - i) * (verstrichen / i)
            print("  %d/%d  (%.0f%%)  noch ~%.0f min" % (i, len(alben), i*100/len(alben), rest/60), flush=True)
    con.commit()

    print()
    print("  %-12s %6s %6s %7s %7s" % ("Quelle", "ok", "leer", "fehler", "Cache"))
    for q in aktiv:
        s = stat[q]
        print("  %-12s %6d %6d %7d %7d" % (q, s["ok"], s["leer"], s["fehler"], s["cache"]))
    print()
    print("  Laufzeit %.1f min" % ((time.time() - t0) / 60))
    con.close()


if __name__ == "__main__":
    main()
