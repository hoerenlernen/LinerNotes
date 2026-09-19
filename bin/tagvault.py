#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
tagvault.py – Export und Wiedereinspielung aller Musik-Metadaten.

WARUM DIESES WERKZEUG
=====================
Die Musikbibliothek (2,3 TB) hat kein Backup und ist im Notfall über Qobuz
wiederbeschaffbar. Die *Tags* sind das nicht: comment, description, mood,
personnel und listening_notes sind kuratierte Handarbeit aus Jahren. Gezippt
passen sie in wenige Megabyte und damit in jede Cloud.

Der Kniff liegt in der Wiedererkennung. Ein neu von Qobuz geladenes Album
heisst anders als das kuratierte Original – ein Dump, der Tags nur ueber den
Dateipfad zuordnet, findet nach einem Neuaufbau nichts wieder. Deshalb
schreibt dieses Werkzeug drei Schluessel pro Track:

  1. MUSICBRAINZ_ALBUMID + Disc + Track   (robust, ueberlebt Umbenennungen)
  2. Artist / Album / Track / Titel       (Fallback, normalisiert)
  3. Dateipfad relativ zur Wurzel         (nur bei identischer Struktur)

Beim Restore wird in dieser Reihenfolge gesucht.

KOLLISIONSSCHUTZ
================
Die Bibliothek enthaelt 10 echte Album-Duplikate (Stand 2026-09-17): dasselbe
Album einmal mit und einmal ohne Auflaesungs-Suffix im Ordnernamen, teils in
zwei Auflaesungen. Fuer 300 Tracks ist der Fuzzy-Schluessel damit NICHT
eindeutig. Ein Restore, der hier raet, schreibt Tags des falschen Masters in
die Datei - schlimmer als gar nichts zu tun.

Deshalb: Schluessel, die mehrfach vorkommen, werden beim Indexaufbau
aussortiert und beim Restore uebersprungen. Solche Dateien erscheinen als
"mehrdeutig" im Bericht und muessen von Hand entschieden werden. Als
Diskriminator kommt zusaetzlich die Spieldauer (+/-2 s) zum Einsatz, die
verschiedene Remaster meist zuverlaessig trennt.

NUR LESEND im Export. Der Restore schreibt – aber ausschliesslich Felder,
die ausdruecklich erlaubt sind, und niemals ohne --apply.

Angelegt 2026-09-17.
"""
import argparse, collections, glob, gzip, hashlib, json, os, re, sys, time, unicodedata
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

# Felder, die der Restore zurueckschreiben darf. Bewusst eng gehalten:
# alles andere (Codec-Infos, Encoder-Spuren) gehoert der neuen Datei.
RESTORE_ERLAUBT = {
    "comment", "description", "description_en", "description_de",
    "listening_notes", "mood", "personnel", "producer",
    "musicbrainz_albumid", "catalognumber", "barcode", "releasecountry",
    "releasedate", "source", "url", "genre", "genre_original", "qobuz_genre",
    "label", "grouping", "work", "subtitle", "originalartist", "originaldate",
    "remixer", "lyricist", "language", "isrc", "copyright",
}


def norm(s):
    """Normalisiert fuer das Fallback-Matching: Akzente, Satzzeichen, Kleinschreibung."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "", s.lower())
    return s


def erster_wert(tags, key):
    v = tags.get(key)
    return v[0] if v else ""


def alle_flacs(root):
    """Findet FLACs in flacher Struktur UND in Disc-Unterordnern.
    Beides kommt vor: 3.239 Alben flach, 83 mit Unterordnern, eine Datei
    liegt direkt in der Wurzel."""
    for p in glob.iglob(os.path.join(glob.escape(root), "**", "*.flac"), recursive=True):
        yield p


def cmd_export(args):
    ziel = args.out
    anzahl = 0
    alben = collections.Counter()
    eintraege = []
    t0 = time.time()

    for pfad in alle_flacs(ROOT):
        rel = os.path.relpath(pfad, ROOT)
        if rel.split(os.sep)[0].startswith("_"):
            continue
        try:
            f = FLAC(pfad)
        except Exception as e:
            print("  WARNUNG unlesbar: %s (%s)" % (rel, e), file=sys.stderr)
            continue

        tags = {k.lower(): list(v) for k, v in f.tags.items()} if f.tags else {}
        art = erster_wert(tags, "albumartist") or erster_wert(tags, "artist")
        alb = erster_wert(tags, "album")
        eintraege.append({
            "pfad": rel,
            "mbid": erster_wert(tags, "musicbrainz_albumid"),
            "disc": erster_wert(tags, "discnumber") or "1",
            "track": erster_wert(tags, "tracknumber"),
            "k_artist": norm(art),
            "k_album": norm(alb),
            "k_title": norm(erster_wert(tags, "title")),
            "tags": tags,
            "bilder": len(f.pictures),
            "sr": f.info.sample_rate,
            "bits": f.info.bits_per_sample,
            "sek": round(f.info.length, 1),
        })
        alben[os.path.dirname(rel)] += 1
        anzahl += 1
        if anzahl % 5000 == 0:
            print("  %d Dateien …" % anzahl, flush=True)

    dump = {
        "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wurzel": ROOT,
        "werkzeug": "tagvault.py",
        "version": 1,
        "dateien": anzahl,
        "alben": len(alben),
        "tracks": eintraege,
    }
    roh = json.dumps(dump, ensure_ascii=False, indent=1).encode("utf-8")
    with gzip.open(ziel, "wb", compresslevel=9) as fh:
        fh.write(roh)

    sha = hashlib.sha256(open(ziel, "rb").read()).hexdigest()
    with open(ziel + ".sha256", "w") as fh:
        fh.write("%s  %s\n" % (sha, os.path.basename(ziel)))

    mit = collections.Counter()
    for e in eintraege:
        for k in e["tags"]:
            mit[k] += 1

    print()
    print("  Dateien:      %d" % anzahl)
    print("  Alben:        %d" % len(alben))
    print("  unkomprimiert %.1f MB" % (len(roh) / 1048576))
    print("  gezippt       %.1f MB   %s" % (os.path.getsize(ziel) / 1048576, ziel))
    print("  SHA256        %s…" % sha[:32])
    print("  Laufzeit      %.1f s" % (time.time() - t0))
    print()
    print("  Abdeckung der kuratierten Felder:")
    for k in ("comment", "mood", "personnel", "description", "listening_notes",
              "musicbrainz_albumid", "catalognumber", "barcode", "releasecountry"):
        n = mit.get(k, 0)
        print("    %-22s %6d Tracks  (%d%%)" % (k, n, n * 100 // max(anzahl, 1)))


def lade(dumpfile):
    with gzip.open(dumpfile, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def cmd_restore(args):
    dump = lade(args.dump)
    print("  Dump vom %s, %d Dateien, Wurzel %s" % (dump["erzeugt"], dump["dateien"], dump["wurzel"]))

    # Indizes aufbauen. Mehrfach belegte Schluessel werden verworfen, statt
    # einen beliebigen Kandidaten zu behalten (siehe KOLLISIONSSCHUTZ oben).
    def eindeutig(paare):
        zaehler = collections.Counter(k for k, _ in paare)
        return ({k: v for k, v in paare if zaehler[k] == 1},
                sorted(k for k, n in zaehler.items() if n > 1))

    per_pfad = dict((e["pfad"], e) for e in dump["tracks"])
    mbid_paare, fuzzy_paare, dauer_paare = [], [], []
    for e in dump["tracks"]:
        if e["mbid"] and e["track"]:
            mbid_paare.append(((e["mbid"], e["disc"], e["track"]), e))
        if e["k_artist"] and e["k_album"] and e["track"]:
            fuzzy_paare.append(((e["k_artist"], e["k_album"], e["disc"], e["track"]), e))
            # Dauer auf 2 s gerundet als Diskriminator fuer Remaster/Auflaesungen
            dauer_paare.append(((e["k_artist"], e["k_album"], e["disc"], e["track"],
                                 int(round(e.get("sek", 0) / 2.0))), e))
    per_mbid,  mbid_kollision  = eindeutig(mbid_paare)
    per_fuzzy, fuzzy_kollision = eindeutig(fuzzy_paare)
    per_dauer, _               = eindeutig(dauer_paare)
    if fuzzy_kollision:
        print("  mehrdeutige Fuzzy-Schluessel verworfen: %d" % len(fuzzy_kollision))
    if mbid_kollision:
        print("  mehrdeutige MBID-Schluessel verworfen:  %d" % len(mbid_kollision))

    treffer = collections.Counter()
    plan = []
    mehrdeutige = []
    fuzzy_kollision = set(fuzzy_kollision)
    for pfad in alle_flacs(ROOT):
        rel = os.path.relpath(pfad, ROOT)
        if rel.split(os.sep)[0].startswith("_"):
            continue
        try:
            f = FLAC(pfad)
        except Exception:
            treffer["unlesbar"] += 1
            continue
        tags = {k.lower(): list(v) for k, v in f.tags.items()} if f.tags else {}
        mbid = erster_wert(tags, "musicbrainz_albumid")
        disc = erster_wert(tags, "discnumber") or "1"
        trk = erster_wert(tags, "tracknumber")
        art = norm(erster_wert(tags, "albumartist") or erster_wert(tags, "artist"))
        alb = norm(erster_wert(tags, "album"))

        dauer_key = (art, alb, disc, trk, int(round(f.info.length / 2.0)))
        e = None; weg = None
        if mbid and (mbid, disc, trk) in per_mbid:
            e, weg = per_mbid[(mbid, disc, trk)], "mbid"
        elif dauer_key in per_dauer:
            e, weg = per_dauer[dauer_key], "fuzzy+dauer"
        elif (art, alb, disc, trk) in per_fuzzy:
            e, weg = per_fuzzy[(art, alb, disc, trk)], "fuzzy"
        elif rel in per_pfad:
            e, weg = per_pfad[rel], "pfad"
        elif (art, alb, disc, trk) in fuzzy_kollision:
            treffer["mehrdeutig"] += 1
            mehrdeutige.append(rel)
            continue
        if not e:
            treffer["kein_treffer"] += 1
            continue
        treffer[weg] += 1

        fehlt = {k: v for k, v in e["tags"].items()
                 if k in RESTORE_ERLAUBT and k not in tags}
        if fehlt:
            plan.append((pfad, fehlt))

    print()
    print("  Zuordnung:")
    for k in ("mbid", "fuzzy+dauer", "fuzzy", "pfad", "mehrdeutig", "kein_treffer", "unlesbar"):
        if treffer[k]:
            print("    %-14s %d" % (k, treffer[k]))
    print("  Dateien mit fehlenden Feldern: %d" % len(plan))
    if mehrdeutige:
        print()
        print("  MEHRDEUTIG – von Hand entscheiden (%d Dateien, erste 5):" % len(mehrdeutige))
        for r in mehrdeutige[:5]:
            print("    %s" % r[:92])
    if not args.apply:
        print()
        print("  TROCKENLAUF – nichts geschrieben. Mit --apply ausfuehren.")
        for pfad, fehlt in plan[:5]:
            print("    %s" % os.path.relpath(pfad, ROOT)[:70])
            print("      + %s" % ", ".join(sorted(fehlt)))
        return
    n = 0
    for pfad, fehlt in plan:
        f = FLAC(pfad)
        for k, v in fehlt.items():
            f.tags[k] = v
        f.save()
        n += 1
    print("  geschrieben: %d Dateien" % n)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="Alle Tags in eine .json.gz schreiben (nur lesend)")
    e.add_argument("--out", default="./data/tagvault-%s.json.gz" % time.strftime("%Y%m%d"))
    e.set_defaults(func=cmd_export)
    r = sub.add_parser("restore", help="Fehlende Tags aus einem Dump zurueckschreiben")
    r.add_argument("dump")
    r.add_argument("--apply", action="store_true", help="tatsaechlich schreiben (sonst Trockenlauf)")
    r.set_defaults(func=cmd_restore)
    a = ap.parse_args()
    a.func(a)
