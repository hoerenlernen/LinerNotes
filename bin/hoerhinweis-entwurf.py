#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Aus einem Repo-Inhalt einen Hörhinweis-Entwurf im neuen Format bauen.

Der Entwurf wird NIE automatisch übernommen. Er geht in eine Datei, die
von Hand redigiert und dann ins Repo committet wird - so steht es im
Auftrag, und so bleibt die Lizenzpflicht beim Menschen.

Was der Generator kann:

  * alle vier Auszeichnungs-Stile des Bestands lesen (s. --stile)
  * Trackliste und echte Laufzeiten aus der Liner-Datenbank ziehen
  * Hinweise über die Spieldauer verteilen, wo keine Zeiten im Text stehen
  * Instrumente aus dem Hinweistext vorschlagen
  * den Typ (struktur/klang/text/kontext) raten
  * Standzeit aus der Textlänge schätzen

Was er NICHT kann: die Zeiten stimmen. Wo sie aus dem Text kommen,
stammen sie aus einem Buch, nicht aus deiner Aufnahme. Wo sie verteilt
wurden, sind sie geraten. Beides ist als ZU-PRUEFEN markiert.

Aufruf:
    hoerhinweis-entwurf.py Soul/Whats_Going_On_Content.tex \\
        --artist "Marvin Gaye" --album "What's Going On" \\
        --ausgabe /tmp/Whats_Going_On_Hoeren.md
"""

import argparse
import os
import re
import sqlite3
import sys
import unicodedata

# Pfade aus der Umgebung; der Datenbankort wird sonst aus dem Ort dieses
# Skripts abgeleitet. Feste Pfade gehoeren nicht ins Repo.
_WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("HOERENLERNEN_PFAD", "")
LINER_DB = os.environ.get("LINER_DB", os.path.join(_WURZEL, "data", "liner.db"))

# --------------------------------------------------------------------------
# LaTeX zu Text
# --------------------------------------------------------------------------

def entschaerfen(t):
    """LaTeX in lesbaren Text. Knapp gehalten - nur was im Bestand vorkommt."""
    t = t.replace("~", " ")
    t = re.sub(r"\\(?:textbf|textit|emph|texttt|textsc)\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\{\\color\{[^}]*\}([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", t)
    t = t.replace("``", "\u201e").replace("''", "\u201c")
    t = t.replace("---", "\u2013").replace("--", "\u2013")
    t = t.replace("{", "").replace("}", "").replace("\\", "")
    return re.sub(r"\s+", " ", t).strip()


def sekunden(text):
    m = re.match(r"\s*(\d{1,3}):(\d{2})", text or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def mmss(s):
    return "%d:%02d" % (int(s) // 60, int(s) % 60)


# --------------------------------------------------------------------------
# Die vier Stile des Bestands
# --------------------------------------------------------------------------

# Ein Trackanfang sieht im Bestand auf drei Arten aus. Nach
# Gliederungsebene zu schneiden funktioniert NICHT: Robert Johnson setzt
# seine Kopfzeile in den \\section-Block und die Hinweise in
# \\subsection-Bloecke darunter - wer an beidem schneidet, trennt die
# Kopfzeile von ihren Hinweisen ab.
_TRACKSTART = [
    # \subsection*{Track 1: ``Smells Like Teen Spirit'' (5:01)}  (Rock)
    re.compile(r"^\\(?:sub)?subsection\*?\{Tracks?\s*(\d+)\s*:\s*(.+?)\s*"
               r"\((\d{1,3}:\d{2})\)\}", re.M),
    # \section{Titel} - Nummer und Dauer stehen in der Kopfzeile darunter
    re.compile(r"^\\section\{([^}]+)\}", re.M),
]


_SATZ = re.compile(
    r"^\\section\{((?:Erster|Zweiter|Dritter|Vierter|Fuenfter|Fünfter)\s+Satz[^}]*|"
    r"(?:I|II|III|IV|V|VI|VII)\.\s+[^}]+|Satz\s+(?:I|II|III|IV|V)[^}]*)\}", re.M)

_VARIATION = re.compile(r"\\variation\{(\d+)\}\{([^}]*)\}")


def bloecke_schneiden(text):
    """Den Text in Abschnitte je Track zerlegen.

    Rueckgabe je Block: (titel, nr, dauer, ist_track, inhalt)

    `ist_track` ist True, wenn der Block aus einem eindeutigen
    Trackmuster stammt (Track-Ueberschrift, Satz, Variation). Nur beim
    letzten Ausweg - alle \\section-Ueberschriften - ist es False, denn
    dort stehen auch Nachworte und Glossare.
    """
    # Wo vorhanden, nur den Musikteil betrachten - sonst alles, denn die
    # Rock-Dateien kennen die Teil-Ueberschrift gar nicht.
    teil = text.split("DIE MUSIK", 1)
    musik = teil[1] if len(teil) > 1 else text

    marken = []
    for m in _TRACKSTART[0].finditer(musik):
        marken.append((m.start(), entschaerfen(m.group(2)),
                       int(m.group(1)), sekunden(m.group(3)), True))

    # Klassik I: Saetze. "Erster Satz: Allegro ma non troppo" ist ein
    # Track, "Das Orchester verstehen" nicht - deshalb ein eigenes
    # Muster statt aller \section-Ueberschriften.
    if not marken:
        for m in _SATZ.finditer(musik):
            marken.append((m.start(), entschaerfen(m.group(1)), None, None, True))

    # Klassik II: Bachs Variationen. Jede ist ein Track. Die Nummer ist
    # die Variationsnummer PLUS EINS, weil auf Aufnahmen die Aria auf
    # Track 1 liegt - das ist eine Annahme, sie steht im Kopf der
    # erzeugten Datei.
    if not marken:
        for m in _VARIATION.finditer(musik):
            marken.append((m.start(), "Variation %s (%s)" % (
                m.group(1), entschaerfen(m.group(2))),
                int(m.group(1)) + 1, None, True))

    # Letzter Ausweg: alle Abschnitte des Musikteils
    if not marken:
        for m in _TRACKSTART[1].finditer(musik):
            marken.append((m.start(), entschaerfen(m.group(1)), None, None, False))

    marken.sort()
    aus = []
    for i, (pos, titel, nr, dauer, ist_track) in enumerate(marken):
        ende = marken[i + 1][0] if i + 1 < len(marken) else len(musik)
        aus.append((titel, nr, dauer, ist_track, musik[pos:ende]))
    return aus


def kopf_lesen(block):
    """Tracknummer und Laufzeit aus der Kopfzeile eines Track-Abschnitts.

    Drei Schreibweisen, alle im Bestand belegt:
      "Track 1~*~3:53"                    (Soul)
      "Dauer: 2:48 ... Track 1"           (Blues)
      "Dauer: 7:47~*~Erkenntnis"          (Jazz, ohne Nummer)
    Ohne Nummer wird sie spaeter aus der Reihenfolge abgeleitet.
    """
    m = re.search(r"Tracks?\s*(\d+)(?:\s*(?:--|\u2013|-)\s*(\d+))?\s*[\u00b7~\s]+(\d{1,3}:\d{2})",
                  block)
    if m:
        return (int(m.group(1)), int(m.group(2)) if m.group(2) else None,
                sekunden(m.group(3)))

    dauer = None
    m = re.search(r"Dauer:\s*(\d{1,3}:\d{2})", block)
    if m:
        dauer = sekunden(m.group(1))
    m2 = re.search(r"Dauer:[^\n]{0,90}?Tracks?\s*(\d+)", block)
    if m2:
        return (int(m2.group(1)), None, dauer)
    if dauer is not None:
        return (None, None, dauer)
    return (None, None, None)


def hinweise_lesen(block):
    """Alle Stile. Rückgabe: Liste (sekunde|None, text, ist_achteauf)."""
    aus = []
    belegt = set()

    # Stil A: \timecode{0:00} + \note{...}
    # Beethoven schreibt zusaetzlich ein Etikett hinter einen Strich:
    # \timecode{0:00 -- 0:15 | Die leere Quinte}. Das ist eine
    # Ueberschrift fuer den Hinweis und kommt dem Text voran.
    for m in re.finditer(r"\\timecode\{([^}]+)\}\s*\n?\s*\\note\{(.*?)\}\s*"
                         r"(?=\n\n|\\timecode|\\achteauf|\\newpage|\\section|$)",
                         block, re.S):
        marke = m.group(1)
        text = entschaerfen(m.group(2))
        if "|" in marke:
            etikett = entschaerfen(marke.split("|", 1)[1])
            if etikett and etikett.lower() not in text.lower():
                text = "%s: %s" % (etikett, text)
        aus.append((sekunden(marke), text, False))
        belegt.add(m.start())

    # Stil B: \timecode{0:00--0:05} Text steht direkt dahinter
    for m in re.finditer(r"\\timecode\{([^}]+)\}[ \t]*(?!\s*\\note)(.+?)\s*"
                         r"(?=\n\n|\\timecode|\\achteauf|\\newpage|\\section|$)",
                         block, re.S):
        if m.start() in belegt:
            continue
        txt = entschaerfen(m.group(2))
        if txt:
            aus.append((sekunden(m.group(1)), txt, False))

    # Stil C: \variation{1}{G-Dur, 3/4-Takt} + \note{...}
    # Der Variationskopf ist schon der Tracktitel, im Hinweis steht nur
    # die Note - sonst stuende die Tonart in jedem Satz noch einmal.
    for m in re.finditer(r"\\variation\{(\d+)\}\{([^}]*)\}\s*\n?\s*\\note\{(.*?)\}\s*"
                         r"(?=\n\n|\\variation|\\achteauf|\\vergleich|$)", block, re.S):
        aus.append((None, entschaerfen(m.group(3)), False))

    # Stil D: Zeitbereich in der Zwischenüberschrift
    for m in re.finditer(r"^\\subsection\*?\{([^}]*?)\((\d{1,3}:\d{2})\s*(?:--|\u2013|-)\s*"
                         r"(\d{1,3}:\d{2})\)\}\s*\n+(.{20,400}?)(?=\n\n)", block, re.M | re.S):
        aus.append((sekunden(m.group(2)),
                    "%s: %s" % (entschaerfen(m.group(1)).strip(),
                                entschaerfen(m.group(4))), False))

    # Stil E: \textbf{0:00} Text steht direkt dahinter (Blues)
    for m in re.finditer(r"\\textbf\{(\d{1,3}:\d{2})\}\s*(.+?)\s*"
                         r"(?=\n\n|\\textbf\{\d|\\achteauf|\\newpage|"
                         r"\\section|\\subsection|$)", block, re.S):
        txt = entschaerfen(m.group(2))
        if txt and len(txt) > 8:
            aus.append((sekunden(m.group(1)), txt, False))

    # "Achte auf" - überall gleich
    for m in re.finditer(r"\\achteauf\{(.*?)\}\s*"
                         r"(?=\n\n|\\timecode|\\newpage|\\section|\\variation|$)",
                         block, re.S):
        aus.append((None, entschaerfen(m.group(1)), True))

    aus.sort(key=lambda x: (x[0] is None, x[0] or 0))
    return _entdoppeln(aus)


def _entdoppeln(hinweise):
    """Denselben Text nicht zweimal.

    Beethoven fasst seine Hinweise zusaetzlich in Zwischentiteln mit
    Zeitbereich zusammen; Stil A und Stil D greifen dann beide auf
    denselben Absatz zu. Ein Hinweis faellt weg, wenn sein Text in
    einem laengeren schon vollstaendig enthalten ist.
    """
    def kern(t):
        return re.sub(r"[^a-z0-9äöüß]", "", (t or "").lower())

    behalten = []
    for sek, text, achte in sorted(hinweise, key=lambda h: -len(h[1] or "")):
        k = kern(text)
        if not k:
            continue
        if any(k in kern(t) for _, t, _ in behalten):
            continue
        behalten.append((sek, text, achte))
    behalten.sort(key=lambda x: (x[0] is None, x[0] or 0))
    return behalten


# --------------------------------------------------------------------------
# Anreichern: Instrumente, Typ, Standzeit
# --------------------------------------------------------------------------

INSTRUMENTE = [
    "Violine I", "Violine II", "Violine", "Viola", "Bratsche", "Violoncello",
    "Cello", "Kontrabass", "Streicher", "Flöte", "Oboe", "Klarinette",
    "Fagott", "Horn", "Trompete", "Posaune", "Tuba", "Pauke", "Schlagwerk",
    "Cembalo", "Klavier", "Orgel", "Harfe", "Chor", "Sopran", "Alt", "Tenor",
    "Bass", "Bassgitarre", "Gitarre", "E-Gitarre", "Schlagzeug", "Drums",
    "Congas", "Bongos", "Tamburin", "Saxophon", "Sax", "Mundharmonika",
    "Orchester", "Synthesizer", "Moog", "Mellotron", "Vibraphon",
    "Rhodes", "Hammond", "Streicherteppich", "Bläser", "Background",
]

_TYP_MUSTER = [
    ("text", r"\bsingt\b|\bzeile\b|\bstrophe\b|\brefrain\b|\btext\b|\bworte\b|"
             r"\bstimme setzt\b|\u201e[^\u201c]{8,}\u201c"),
    ("struktur", r"\bthema\b|\bwiederholung\b|\bübergang\b|\bsteigerung\b|"
                 r"\bkehrt zurück\b|\beinsatz\b|\bbeginnt\b|\bendet\b|\bbogen\b|"
                 r"\bexposition\b|\bdurchführung\b|\breprise\b|\bcoda\b|\bbridge\b"),
    ("kontext", r"\b1[6-9]\d\d\b|\b20[0-2]\d\b|\bjahr\b|\bkrieg\b|\bstudio\b|"
                r"\baufgenommen\b|\bproduzent\b|\blabel\b"),
]


def instrumente_finden(text):
    t = text.lower()
    treffer = []
    for i in INSTRUMENTE:
        if re.search(r"\b" + re.escape(i.lower()) + r"\b", t):
            # längere Namen gewinnen: "Violine I" statt "Violine"
            if not any(i.lower() in a.lower() and i != a for a in treffer):
                treffer.append(i)
    # Teilmengen entfernen
    return [i for i in treffer
            if not any(i != a and i.lower() in a.lower() for a in treffer)][:4]


def typ_raten(text, ist_achteauf):
    t = text.lower()
    if ist_achteauf:
        return "klang"
    for typ, muster in _TYP_MUSTER:
        if re.search(muster, t):
            return typ
    if instrumente_finden(text):
        return "klang"
    return "struktur"


def standzeit(text):
    """Etwa 14 Zeichen je Sekunde Lesezeit, mindestens 8, höchstens 20."""
    return max(8, min(20, round(len(text) / 14.0)))


# --------------------------------------------------------------------------
# Trackliste aus der Bibliothek
# --------------------------------------------------------------------------

def bibliothek(artist, album, album_id=None):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % LINER_DB, uri=True)
    except Exception:
        return None, []

    def norm(s):
        s = unicodedata.normalize("NFKD", (s or "").lower())
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]", "", s)

    if album_id:
        zeile = con.execute("SELECT id, artist, album FROM album WHERE id=?",
                            (album_id,)).fetchone()
    else:
        zeile = None
        for r in con.execute("SELECT id, artist, album FROM album"):
            if norm(artist) in norm(r[1]) or norm(r[1]) in norm(artist):
                if norm(album) and (norm(album) in norm(r[2]) or norm(r[2]) in norm(album)):
                    zeile = r
                    break
    if not zeile:
        con.close()
        return None, []
    tracks = con.execute(
        "SELECT nr, titel, dauer_s FROM track WHERE album_id=? ORDER BY disc, nr",
        (zeile[0],)).fetchall()
    con.close()
    return zeile, tracks


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------

def y(s):
    """Einzeiliger YAML-Wert."""
    s = (s or "").replace('"', "'").replace("\\", "")
    return '"%s"' % s


def bauen(quelle, artist, album, genre, album_id=None, dichte=25):
    roh = open(os.path.join(REPO, quelle) if not os.path.isabs(quelle) else quelle,
               encoding="utf-8", errors="replace").read()
    bloecke = bloecke_schneiden(roh)
    treffer, tracks = bibliothek(artist, album, album_id)

    # Tracks der Bibliothek nach Nummer, für Laufzeiten
    dauer_je_nr = {}
    titel_je_nr = {}
    for nr, titel, d in tracks:
        if nr and nr not in dauer_je_nr:
            dauer_je_nr[nr] = d
            titel_je_nr[nr] = titel

    zeilen = []
    album_hinweise = []
    hinweise = []
    lfd = 0
    geraten = 0
    aus_buch = 0

    laufende_nr = 0
    for titel, vor_nr, vor_dauer, ist_track, block in bloecke:
        nr, bis, buchdauer = kopf_lesen(block)
        # Nummer und Dauer aus der Ueberschrift haben Vorrang - sie
        # stehen direkt am Track, die Kopfzeile kann fehlen.
        nr = vor_nr if vor_nr is not None else nr
        buchdauer = vor_dauer if vor_dauer is not None else buchdauer
        roh_hinweise = hinweise_lesen(block)
        if not roh_hinweise:
            continue
        if not ist_track and nr is None and buchdauer is None:
            # Weder Trackmuster noch Nummer noch Dauer: kein Track,
            # sondern ein Nachwort- oder Einfuehrungsabschnitt.
            album_hinweise += [t for _, t, a in roh_hinweise if a]
            continue
        if nr is None:
            # Dauer, aber keine Nummer (Jazz). Die Abschnitte stehen in
            # Reihenfolge, also aus der Position ableiten - und im Kopf
            # der Datei vermerken, dass das eine Annahme ist.
            laufende_nr += 1
            nr = laufende_nr
        else:
            laufende_nr = max(laufende_nr, nr)

        laenge = dauer_je_nr.get(nr) or buchdauer or 0
        ohne_zeit = [h for h in roh_hinweise if h[0] is None and not h[2]]
        mit_zeit = [h for h in roh_hinweise if h[0] is not None]

        # Hinweise ohne Zeit gleichmäßig verteilen
        verteilt = {}
        if ohne_zeit and laenge:
            schritt = laenge / (len(ohne_zeit) + 1.0)
            for i, h in enumerate(ohne_zeit, 1):
                verteilt[id(h)] = round(schritt * i)

        for h in roh_hinweise:
            sek, text, ist_achte = h
            if ist_achte and sek is None:
                album_hinweise.append(text)
                continue
            lfd += 1
            kennung = "a%d" % lfd
            if sek is None:
                sek = verteilt.get(id(h))
                sicher = False
                geraten += 1
            else:
                sicher = True
                aus_buch += 1
            hinweise.append({
                "id": kennung, "track": titel, "nr": nr, "bis": bis,
                "zeit": mmss(sek) if sek is not None else "0:00",
                "sicher": sicher, "text": text,
                "typ": typ_raten(text, ist_achte),
                "instrumente": instrumente_finden(text),
                "dauer": standzeit(text),
            })

    # ---- Datei zusammensetzen ----
    zeilen.append("---")
    zeilen.append("# Hoerhinweise fuer Liner. Inhalt: %s" % quelle)
    zeilen.append("# Lizenz CC BY-NC-SA 4.0, Repo hoeren-lernen.")
    zeilen.append("#")
    zeilen.append("# Hinweise gehoeren dorthin, wo etwas passiert. Ruhige Stellen")
    zeilen.append("# duerfen leer bleiben - eine gleichmaessige Dichte ist kein Ziel.")
    zeilen.append("#")
    zeilen.append("# ENTWURF - jede Zeit ist zu pruefen. Zeiten mit der Marke")
    zeilen.append("# ZU-PRUEFEN wurden gleichmaessig ueber die Spieldauer verteilt,")
    zeilen.append("# nicht gemessen. Die uebrigen stammen aus dem Buchtext und")
    zeilen.append("# beziehen sich womoeglich auf eine andere Ausgabe.")
    zeilen.append("artist: %s" % y(artist))
    zeilen.append("album: %s" % y(album))
    zeilen.append("genre: %s" % genre)
    zeilen.append("# mbid:")
    zeilen.append("quelle: %s" % y(quelle))
    zeilen.append("")

    if treffer:
        zeilen.append("# In der Bibliothek gefunden: #%d %s - %s (%d Titel)"
                      % (treffer[0], treffer[1], treffer[2], len(tracks)))
    else:
        zeilen.append("# NICHT in der Bibliothek gefunden - Laufzeiten fehlen,")
        zeilen.append("# die Verteilung stuetzt sich auf die Angaben im Buch.")
    zeilen.append("")

    zeilen.append("# Weitere Einspielungen desselben Werks. Siehe")
    zeilen.append("# doku/hoerhinweise-format.md, Abschnitt \u201eAufnahmen\u201c.")
    zeilen.append("# aufnahmen:")
    zeilen.append("#   - kennung: \"…\"")
    zeilen.append("#     artist: \"…\"")
    zeilen.append("#     album: \"…\"")
    zeilen.append("#     versatz: 0")
    zeilen.append("")

    zeilen.append("# Nur ausfuellen, wenn Hinweise Instrumente nennen.")
    benutzte = sorted({i for h in hinweise for i in h["instrumente"]})
    if benutzte:
        zeilen.append("besetzung:")
        zeilen.append("  art: band            # orchester | band")
        zeilen.append("  gruppen:")
        zeilen.append("    - name: \"Alle\"")
        zeilen.append("      instrumente: [%s]" % ", ".join(y(i) for i in benutzte))
        zeilen.append("  # Gruppen bitte sinnvoll aufteilen - das hier ist nur")
        zeilen.append("  # die Liste dessen, was in den Hinweisen vorkommt.")
    else:
        zeilen.append("# besetzung:")
    zeilen.append("")

    zeilen.append("worauf_achten:")
    for a in album_hinweise[:8]:
        zeilen.append("  - %s" % y(a))
    if not album_hinweise:
        zeilen.append("  # - \"…\"")
    zeilen.append("")

    zeilen.append("hinweise:")
    for h in hinweise:
        marke = "" if h["sicher"] else "   # ZU-PRUEFEN (verteilt, nicht gemessen)"
        zeilen.append("  - id: %s" % h["id"])
        zeilen.append("    track: %s" % y(h["track"]))
        zeilen.append("    nr: %d" % h["nr"])
        if h["bis"]:
            zeilen.append("    bis: %d" % h["bis"])
        zeilen.append("    zeit: \"%s\"%s" % (h["zeit"], marke))
        zeilen.append("    dauer: %d" % h["dauer"])
        zeilen.append("    typ: %s" % h["typ"])
        if h["instrumente"]:
            zeilen.append("    instrumente: [%s]"
                          % ", ".join(y(i) for i in h["instrumente"]))
        zeilen.append("    text: %s" % y(h["text"]))
    zeilen.append("---")
    zeilen.append("")
    zeilen.append("## Hintergrund")
    zeilen.append("")
    zeilen.append("Hier den Fliesstext einsetzen, den man VOR dem Hoeren liest.")
    zeilen.append("Jede `##`-Ueberschrift wird in Liner ein eigener Abschnitt.")
    zeilen.append("")

    # Abstaende je Track. BEWUSST OHNE WERTUNG: Hinweise gehoeren
    # dorthin, wo etwas passiert - eine ruhige Passage darf leer
    # bleiben, und bei einem langsamen Satz ist eine lange Luecke oft
    # genau richtig. Der Generator zeigt nur, wo die Luecken sind; ob
    # das ein Mangel ist, entscheidet der Mensch.
    je_track = {}
    for h in hinweise:
        je_track.setdefault(h["nr"], 0)
        je_track[h["nr"]] += 1
    duenn = []
    spielzeit = 0
    for nr, anzahl in sorted(je_track.items()):
        laenge = dauer_je_nr.get(nr)
        if not laenge:
            continue
        spielzeit += laenge
        abstand = laenge / max(1, anzahl)
        if abstand > 45:
            duenn.append((nr, titel_je_nr.get(nr, "?"), anzahl,
                          round(laenge), round(abstand)))

    return "\n".join(zeilen), {
        "hinweise": len(hinweise), "album": len(album_hinweise),
        "aus_buch": aus_buch, "geraten": geraten,
        "tracks_bibliothek": len(tracks),
        "instrumente": len(benutzte),
        "spielzeit": spielzeit,
        "abstand": round(spielzeit / len(hinweise)) if hinweise and spielzeit else None,
        "duenn": duenn,
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("quelle", help="Pfad zur *_Content.tex (relativ zum Repo)")
    p.add_argument("--artist", required=True)
    p.add_argument("--album", required=True)
    p.add_argument("--genre", default="")
    p.add_argument("--album-id", type=int, default=None,
                   help="Album-ID aus liner.db, falls die Suche danebengreift")
    p.add_argument("--ausgabe", help="Zieldatei (Standard: neben der Quelle, "
                                     "aber NIE ins Repo geschrieben)")
    args = p.parse_args()

    genre = args.genre or (args.quelle.split("/")[0] if "/" in args.quelle else "")
    text, zahlen = bauen(args.quelle, args.artist, args.album, genre, args.album_id)

    ziel = args.ausgabe or "/tmp/%s_Hoeren.md" % os.path.basename(
        args.quelle).replace("_Content.tex", "")
    with open(ziel, "w", encoding="utf-8") as f:
        f.write(text)

    print("Entwurf: %s" % ziel)
    print("  Hinweise gesamt      %d" % zahlen["hinweise"])
    print("    davon mit Zeit aus dem Buch  %d" % zahlen["aus_buch"])
    print("    davon verteilt (ZU-PRUEFEN)  %d" % zahlen["geraten"])
    print("  Album-Hinweise       %d" % zahlen["album"])
    print("  Instrumente erkannt  %d" % zahlen["instrumente"])
    print("  Tracks aus der Bibliothek     %d" % zahlen["tracks_bibliothek"])
    if zahlen.get("abstand"):
        print("  Abstand: im Schnitt alle %d s ein Hinweis" % zahlen["abstand"])
    if zahlen.get("duenn"):
        print("\n  Tracks mit groesseren Abstaenden (nur zur Kenntnis -")
        print("  in ruhigen Passagen ist das oft richtig so):")
        for nr, titel, anzahl, laenge, abstand in zahlen["duenn"][:12]:
            print("    %2s. %-44s %2d Hinweise auf %4.1f min = alle %3d s"
                  % (nr, titel[:42], anzahl, laenge / 60.0, abstand))
    print("\nDie Datei wird NICHT ins Repo geschrieben. Redigieren, dann")
    print("selbst committen.")


if __name__ == "__main__":
    main()
