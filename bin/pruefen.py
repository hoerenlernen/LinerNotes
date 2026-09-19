#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
pruefen.py – Findet Alben mit verdaechtigen Tags.

ANLASS
======
Bei Queen "News Of The World" stehen in den Dateien Label "Skary Ink",
Copyright "2025 Skary Ink" und eine Besetzung ("Ezra Vale", "New World Kid",
"Maisie Kane"), die nichts mit Queen zu tun hat. Geschrieben wurde das vom
musik_master-Skript (Mutagen 1.47.0) - offenbar wurden Daten eines fremden
Albums uebernommen.

Dieses Werkzeug sucht vergleichbare Faelle. Es KORRIGIERT NICHTS; es
berichtet nur.

WELCHE SIGNALE ZAEHLEN
======================
Ein einzelnes Merkmal genuegt nicht. Dass ein Copyright-Jahr juenger ist als
die Veroeffentlichung, trifft auf 663 Alben zu und ist meist voellig richtig -
Craft Recordings, ABKCO und Fania sind echte Wiederveroeffentlichungs-Label.
Erst die Kombination traegt. Gewichtet wird so:

  40  Label-Ausreisser: Der Kuenstler hat mehrere Alben, die ueberwiegend auf
      einem Label liegen - dieses eine nicht. Bei Queen stehen 10 von 12 auf
      EMI; "Skary Ink", "GARLIC RECORDS" und "Z-Trading" fallen heraus.
  30  Besetzung passt nicht zum Kuenstler. Der naive Test - "kommt der
      Kuenstlername in `personnel` vor?" - liefert bei BANDS falsche Treffer:
      Bei Queen "Innuendo" steht dort korrekt "Freddie Mercury, Roger Taylor,
      John Deacon", aber eben nicht "Queen". Deshalb wird zusaetzlich geprueft,
      ob die genannten Personen bei ANDEREN Alben desselben Kuenstlers
      auftauchen. Wer dort wiederkehrt, gehoert dazu - auch ohne dass der
      Bandname fällt.
  20  Label in der ganzen Sammlung ein Einzelfall (hoechstens zwei Alben) und
      nicht als Wiederveroeffentlichungs-Label bekannt.
  15  Widerspruch zwischen `composer` und `personnel`: Der Komponist nennt den
      Kuenstler, die Besetzung kennt ihn nicht.
  10  Copyright-Jahr mehr als 15 Jahre nach der Veroeffentlichung - nur als
      zusaetzliches Indiz, nie allein.

Ab 40 Punkten gilt ein Album als verdaechtig.

NUR LESEND. Ergebnisse in die Tabelle `verdacht` und als CSV.
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import time
import unicodedata
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


DB = os.environ.get("LINER_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "liner.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS verdacht (
  album_id  INTEGER PRIMARY KEY REFERENCES album(id) ON DELETE CASCADE,
  punkte    INTEGER NOT NULL,
  gruende   TEXT NOT NULL,          -- JSON-Liste
  label     TEXT, copyright TEXT, personnel TEXT,
  geprueft_am REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS i_verdacht_punkte ON verdacht(punkte DESC);
"""

# Label, die als Wiederveroeffentlichungs-Haeuser bekannt sind. Bei ihnen ist
# ein junges Copyright-Jahr normal und kein Hinweis auf falsche Tags.
REISSUE_LABEL = {
    "craft recordings", "abkco", "abkco music & records", "fania", "rhino",
    "legacy", "legacy recordings", "sony legacy", "universal music",
    "verve reissues", "analogue productions", "mobile fidelity",
    "audio fidelity", "bear family", "ace records", "cherry red",
    "esoteric recordings", "real gone music", "omnivore recordings",
    "candid", "tangerine records", "sundazed", "light in the attic",
    "elemental music", "jazz images", "concord", "blue note", "impulse!",
    "decca", "deutsche grammophon", "warner classics", "erato",
}


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def namensteile(kuenstler):
    """Zerlegt einen Kuenstlernamen in Bestandteile ab drei Zeichen."""
    roh = norm(kuenstler)
    teile = {t for t in roh.split() if len(t) >= 3}
    # Fuellwoerter, die keine Identitaet stiften
    teile -= {"the", "and", "band", "feat", "featuring", "orchestra", "trio",
              "quartet", "quintet", "ensemble", "various", "artists"}
    if roh:
        teile.add(roh)
    return teile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--csv", default="./doku/verdacht.csv")
    ap.add_argument("--schwelle", type=int, default=40)
    a = ap.parse_args()

    con = sqlite3.connect(a.db, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)

    alben = con.execute("""SELECT id, artist, album, jahr, label, k_artist, tags_json
                           FROM album""").fetchall()

    # Label je Kuenstler und in der Gesamtsammlung zaehlen
    je_kuenstler, gesamt = {}, {}
    for r in alben:
        lbl = norm(r["label"])
        if not lbl:
            continue
        je_kuenstler.setdefault(r["k_artist"], {}).setdefault(lbl, 0)
        je_kuenstler[r["k_artist"]][lbl] += 1
        gesamt[lbl] = gesamt.get(lbl, 0) + 1

    # Welche Personen kommen bei einem Kuenstler mehrfach vor? Wer in
    # mehreren Alben desselben Interpreten auftaucht, gehoert zum Umfeld -
    # selbst wenn der Bandname in der Besetzung nie fällt.
    namen_je_kuenstler = {}
    for r in alben:
        try:
            tg = json.loads(r["tags_json"] or "{}")
        except (TypeError, ValueError):
            continue
        pers = (tg.get("personnel") or [""])[0]
        if not pers:
            continue
        eimer = namen_je_kuenstler.setdefault(r["k_artist"], {})
        for stueck in re.split(r"\s+-\s+", pers):
            name = norm(stueck.split(",")[0])
            if len(name) >= 4:
                eimer[name] = eimer.get(name, 0) + 1

    treffer = []
    for r in alben:
        try:
            tags = json.loads(r["tags_json"] or "{}")
        except (TypeError, ValueError):
            continue
        def erst(k):
            v = tags.get(k)
            return v[0] if v else ""

        label = r["label"] or ""
        lbl_n = norm(label)
        personnel = erst("personnel")
        composer = erst("composer")
        copyright_ = erst("copyright")
        punkte, gruende = 0, []

        # --- 40: Label-Ausreisser beim selben Kuenstler ---
        eigene = je_kuenstler.get(r["k_artist"], {})
        if lbl_n and len(eigene) > 1 and sum(eigene.values()) >= 3:
            haupt = max(eigene.items(), key=lambda x: x[1])
            if eigene.get(lbl_n, 0) == 1 and haupt[1] >= 2 and haupt[0] != lbl_n:
                punkte += 40
                gruende.append("Label '%s' ist bei diesem Künstler ein Einzelfall – "
                               "%d von %d Alben liegen auf '%s'"
                               % (label, haupt[1], sum(eigene.values()), haupt[0]))

        # --- 30: Besetzung passt nicht zum Künstler ---
        if personnel:
            teile = namensteile(r["artist"])
            pn = norm(personnel)
            nennt_kuenstler = bool(teile) and any(t in pn for t in teile)
            # Kommen die genannten Personen bei anderen Alben desselben
            # Künstlers auch vor? Dann sind sie plausibel.
            bekannt = namen_je_kuenstler.get(r["k_artist"], {})
            diese = [norm(s.split(",")[0]) for s in re.split(r"\s+-\s+", personnel)]
            diese = [x for x in diese if len(x) >= 4]
            wiederkehrend = sum(1 for x in diese if bekannt.get(x, 0) >= 2)
            if teile and not nennt_kuenstler and diese and wiederkehrend == 0:
                punkte += 30
                gruende.append("Besetzung passt nicht zu '%s' – keiner der "
                               "Genannten kommt bei anderen Alben dieses "
                               "Künstlers vor: %s" % (r["artist"], personnel[:80]))

        # --- 20: Label in der Sammlung ein Einzelfall ---
        if lbl_n and gesamt.get(lbl_n, 0) <= 2 and lbl_n not in REISSUE_LABEL:
            punkte += 20
            gruende.append("Label '%s' kommt in der Sammlung nur %dx vor"
                           % (label, gesamt.get(lbl_n, 0)))

        # --- 15: Komponist und Besetzung widersprechen sich ---
        if personnel and composer:
            teile = namensteile(r["artist"])
            cn, pn = norm(composer), norm(personnel)
            bekannt2 = namen_je_kuenstler.get(r["k_artist"], {})
            diese2 = [norm(s.split(",")[0]) for s in re.split(r"\s+-\s+", personnel)]
            wiederkehrend2 = sum(1 for x in diese2 if len(x) >= 4 and bekannt2.get(x, 0) >= 2)
            if (teile and any(t in cn for t in teile)
                    and not any(t in pn for t in teile) and wiederkehrend2 == 0):
                punkte += 15
                gruende.append("Komponist '%s' passt zum Künstler, die Besetzung nicht"
                               % composer[:50])

        # --- 10: Copyright deutlich jünger (nur als Indiz) ---
        m = re.search(r"(19|20)\d{2}", copyright_)
        jahr = (r["jahr"] or "")[:4]
        if m and jahr.isdigit():
            diff = int(m.group(0)) - int(jahr)
            if diff > 15 and lbl_n not in REISSUE_LABEL:
                punkte += 10
                gruende.append("Copyright %s liegt %d Jahre nach der Veröffentlichung %s"
                               % (m.group(0), diff, jahr))

        if punkte >= a.schwelle:
            treffer.append({
                "album_id": r["id"], "artist": r["artist"], "album": r["album"],
                "jahr": jahr, "label": label, "copyright": copyright_[:120],
                "personnel": personnel[:160], "punkte": punkte, "gruende": gruende,
            })

    treffer.sort(key=lambda x: (-x["punkte"], x["artist"] or ""))

    con.execute("DELETE FROM verdacht")
    for x in treffer:
        con.execute("""INSERT INTO verdacht(album_id,punkte,gruende,label,copyright,
                       personnel,geprueft_am) VALUES(?,?,?,?,?,?,?)""",
                    (x["album_id"], x["punkte"], json.dumps(x["gruende"], ensure_ascii=False),
                     x["label"], x["copyright"], x["personnel"], time.time()))
    con.commit()

    os.makedirs(os.path.dirname(a.csv), exist_ok=True)
    with open(a.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Punkte", "Künstler", "Album", "Jahr", "Label", "Copyright",
                    "Besetzung", "Gründe"])
        for x in treffer:
            w.writerow([x["punkte"], x["artist"], x["album"], x["jahr"], x["label"],
                        x["copyright"], x["personnel"], " | ".join(x["gruende"])])

    print("  geprüft:     %d Alben" % len(alben))
    print("  verdächtig:  %d (ab %d Punkten)" % (len(treffer), a.schwelle))
    print("  CSV:         %s" % a.csv)
    print()
    print("  Die zehn stärksten Verdachtsfälle:")
    for x in treffer[:10]:
        print("    %3d  %-22s %-30s %s" % (x["punkte"], (x["artist"] or "")[:22],
                                           (x["album"] or "")[:30], x["label"][:20]))
    con.close()


if __name__ == "__main__":
    main()
