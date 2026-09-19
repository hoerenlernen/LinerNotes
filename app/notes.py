# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
notes.py – Baut aus dem, was der Linn meldet, die vollständigen Liner Notes.

DER WEG VON DER URL ZUR DATEI
=============================
MinimServer liefert res-URLs dieser Form:

  http://server:9790/minimserver/*/Musik/Pink*20Floyd*20-*20The*20Wall…/08.*20Empty*20Spaces

Zwei Eigenheiten, die man kennen muss:

  1. MinimServer kodiert Sonderzeichen als `*XX` (hex), NICHT als `%XX`.
     `*20` ist ein Leerzeichen, `*5b` eine öffnende Klammer.
  2. Die URL trägt KEINE Dateiendung. Die muss ergänzt werden.

Danach wird `Musik/` auf LINER_MUSIC_ROOT abgebildet. Verifiziert am
2026-09-17: der Weg führt zuverlässig zur richtigen Datei.

Schlägt das fehl (umbenannte Datei, andere Quelle), greift das Fallback über
Artist/Album/Tracknummer gegen den Index – deshalb existiert index.py.

Die kuratierten Tags des Nutzers und die extern angereicherten Daten werden
strikt getrennt gehalten: `eigene` gegen `extern`. In der Anzeige ist damit
erkennbar, was Gorans Urteil ist und was von MusicBrainz, Wikipedia oder
Last.fm kommt.
"""
import difflib
import local_cover
import glob
import html
import json
import logging
import os
import re
import sqlite3
import unicodedata

from mutagen.flac import FLAC
from sicherheit import innerhalb

log = logging.getLogger("liner.notes")

ROOT = os.environ.get("LINER_MUSIC_ROOT", "/pfad/zur/musik")
DB = os.environ.get("LINER_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "liner.db"))

# Textfelder aus den Dateien, in Anzeigereihenfolge.
#
# WICHTIG zur Benennung: Diese Texte sind ueberwiegend KEINE eigenen Notizen.
# `comment` und `description` enthalten bei der Haelfte der Alben
# Wikipedia-Auszuege, die Goran ueber sein musik_master-Skript eingetragen hat.
# Die Ueberschrift "Aus meinen Notizen" waere daher falsch - sie behauptete
# eine Autorschaft, die nicht besteht. Nur `listening_notes` und `mood` sind
# durchgaengig eigene Einschaetzungen.
EIGENE_FELDER = [
    ("description", "Über das Album"),
    ("description_en", "Über das Album (englisch)"),
    ("description_de", "Über das Album (deutsch)"),
    ("comment", "Album-Text"),
    ("listening_notes", "Hörnotizen"),
    ("personnel", "Besetzung"),
    ("producer", "Produktion"),
]
# Schlagwortartige Felder. Sie gehoeren NICHT zu den Prosatexten: `mood`
# ist ein einzelnes Wort ("Chill") und liess den Abschnitt "Ueber das
# Album" erscheinen, obwohl kein Albumtext vorlag (Fehler B, Stufe 2).
SCHLAGWORT_FELDER = [
    ("mood", "Stimmung"),
]
# BEWUSST LEER. Es gibt derzeit kein Tag, dessen Autorschaft belegt waere.
#
# Frueher standen hier `listening_notes` und `mood`. Beides ist falsch: `mood`
# wurde vom musik_master-Skript erzeugt (erkennbar am "Encoded By: Mutagen"),
# nicht von Goran geschrieben. Eine Marke "eigene Worte" behauptet eine
# Urheberschaft - und die darf nur dort stehen, wo sie nachweisbar ist.
# Solange es keinen verlaesslichen Beleg gibt, wird kein Tag so ausgezeichnet.
EIGENE_WORTE = set()
# Diese gehoeren zum Titel, nicht zum Album
TITELBEZOGEN = {"lyrics"}

# Fakten, die als Kopfzeile erscheinen
# Pressungsangaben. Sie beantworten "welche Ausgabe ist das", nicht
# "was laeuft" - und gehoeren darum in den Aufklappbereich (Stufe 2.3).
AUSGABE_FELDER = {"catalognumber", "barcode", "releasecountry", "source", "copyright"}

FAKTEN_FELDER = [
    ("label", "Label"),
    ("catalognumber", "Katalognummer"),
    ("barcode", "Barcode"),
    ("releasecountry", "Land"),
    ("source", "Medium"),
    ("copyright", "Copyright"),
]


_HEXPAAR = re.compile(r"[0-9a-fA-F]{2}")


def minim_dekodieren(s):
    """MinimServer kodiert einzelne UTF-8-BYTES als *XX -- keine Zeichen.

    Das ist der entscheidende Unterschied: ein "°" ist in UTF-8 zwei Bytes
    (c2 b0) und kommt darum als "*c2*b0" an. Ein zeichenweises chr() pro
    Treffer erzeugt daraus zwei Zeichen ("Â°") -- Latin-1-Mojibake. Der
    Pfad existiert dann nicht, url_zu_pfad liefert None und die Anzeige
    faellt auf den Namensabgleich zurueck.

    Richtig ist: erst alle Bytes sammeln, dann die komplette Folge als
    UTF-8 dekodieren. Betraf 163 Alben mit Sonderzeichen im Ordnernamen
    plus alle mit Sonderzeichen nur im Dateinamen."""
    rohbytes = bytearray()
    i = 0
    while i < len(s):
        if s[i] == "*" and i + 2 < len(s) and _HEXPAAR.fullmatch(s[i + 1:i + 3]):
            rohbytes.append(int(s[i + 1:i + 3], 16))
            i += 3
        else:
            rohbytes.extend(s[i].encode("utf-8"))
            i += 1
    return rohbytes.decode("utf-8", "replace")


# Satzbezeichnungen am Anfang des Titelrests: roemische Zahl ("I."),
# arabische Nummer, "No. 3", oder eine Tempoangabe ("Allegro con brio").
_SATZ_ANFANG = re.compile(
    r"^(?:[IVXLC]+[.)]|\d+[.)]|No[.,]?\s*\d+|"
    r"(?:Allegro|Andante|Adagio|Largo|Presto|Vivace|Menuetto|Menuett|Scherzo|"
    r"Rondo|Finale|Introduction|Aria|Choral|Chorus|Recitativo|Rezitativ|"
    r"Prelude|Praeludium|Präludium|Fuga|Fuge|Sarabande|Courante|Gigue|"
    r"Allemande|Gavotte|Bourr[eé]e|Menuet|Lento|Moderato|Grave|Maestoso)\b)",
    re.I)


def werk_teilen(titel):
    """Trennt "Werk: Satz" auf, z.B.
    "Symphony No. 5 in C minor, Op. 67: I. Allegro con brio"
    -> ("Symphony No. 5 in C minor, Op. 67", "I. Allegro con brio").

    Getrennt wird am LETZTEN Doppelpunkt, und nur wenn der Rest wie eine
    Satzbezeichnung beginnt. Sonst bleibt der Titel unangetastet -
    "Amazing Grace: Live" oder "I Am Troubled (2019 Remaster)" darf nicht
    zerlegt werden."""
    if not titel or ":" not in titel:
        return None, titel
    # Den ERSTEN Doppelpunkt nehmen, dem eine Satzbezeichnung folgt.
    # Der letzte waere falsch: Satznamen enthalten oft selbst einen
    # Doppelpunkt, etwa 'No. 1, Chorus "Kommt, ihr Toechter: ..."'.
    # Dann bliebe als "Werk" der halbe Satztitel stehen und die Gruppe
    # zerfaellt - bei der Matthaeus-Passion nachweisbar passiert.
    suche = 0
    while True:
        i = titel.find(":", suche)
        if i < 0:
            return None, titel
        kopf, rest = titel[:i].strip(), titel[i + 1:].strip()
        if kopf and rest and _SATZ_ANFANG.match(rest):
            return kopf, rest
        suche = i + 1


def werke_gruppieren(tracks, tags_pro_track=None):
    """Buendelt Saetze unter ihrem Werk.

    Zwei Wege, in dieser Reihenfolge:

    1. WORK / MOVEMENT / MOVEMENTNUMBER aus den Tags. Das ist der saubere
       Weg -- nur: in dieser Bibliothek traegt KEIN einzelnes Album diese
       Felder (geprueft ueber alle 654 Alben mit Genre "Classical" und per
       Stichprobe direkt in den FLACs). Der Zweig ist trotzdem da, damit
       spaeter getaggte Alben ihn sofort nutzen.

    2. Sonst aus den Titeln, ueber werk_teilen(). Das greift bei 42 Alben
       und trifft dabei ausschliesslich echte Klassik.

    Gibt None zurueck, wenn sich nichts sinnvoll buendeln laesst -- dann
    bleibt die flache Liste stehen. Sinnvoll heisst: mindestens 60 % der
    Titel lassen sich zuordnen UND mindestens ein Werk hat mehr als einen
    Satz. Ohne die zweite Bedingung wuerde ein Liederalbum zu lauter
    Einzelgruppen mit je einer Zeile.
    """
    if not tracks or len(tracks) < 3:
        return None

    # Weg 1: echte Tags
    if tags_pro_track:
        werke = [(t.get("work") or "").strip() for t in tags_pro_track]
        if all(werke) and len(set(werke)) < len(werke):
            gruppen, aktuell = [], None
            for tr, w, tg in zip(tracks, werke, tags_pro_track):
                satz = (tg.get("movement") or "").strip() or tr.get("titel") or ""
                if aktuell is None or aktuell["werk"] != w:
                    aktuell = {"werk": w, "tracks": []}
                    gruppen.append(aktuell)
                aktuell["tracks"].append(dict(tr, anzeige=satz))
            return {"quelle": "tags", "gruppen": gruppen}

    # Weg 2: aus den Titeln
    geteilt = [werk_teilen(t.get("titel") or "") for t in tracks]
    zuordenbar = sum(1 for w, _ in geteilt if w)
    if zuordenbar < max(3, len(tracks) * 0.6):
        return None

    gruppen, aktuell = [], None
    for tr, (werk, satz) in zip(tracks, geteilt):
        if not werk:
            # Zwischenstueck ohne Werkbezug: eigene Gruppe ohne Ueberschrift
            aktuell = None
            gruppen.append({"werk": None, "tracks": [dict(tr, anzeige=tr.get("titel"))]})
            continue
        if aktuell is None or aktuell["werk"] != werk:
            aktuell = {"werk": werk, "tracks": []}
            gruppen.append(aktuell)
        aktuell["tracks"].append(dict(tr, anzeige=satz))

    if max((len(g["tracks"]) for g in gruppen), default=0) < 2:
        return None
    return {"quelle": "titel", "gruppen": gruppen}


# Tags, die bei Klassik die Mitwirkenden tragen. Reihenfolge = Anzeige.
KLASSIK_ROLLEN = [
    ("conductor", "Dirigent"),
    ("orchestra", "Orchester"),
    ("ensemble", "Ensemble"),
    ("choir", "Chor"),
    ("chorus", "Chor"),
    ("soloist", "Solist"),
    ("performer", "Ausführende"),
]


def klassik_mitwirkende(tags):
    """Liest Dirigent, Orchester und weitere Rollen aus den Tags.

    Wird nur gefuellt, wenn die Felder wirklich da sind. Beim Testalbum
    (Kleiber, Beethoven 5 & 7) sind sie es NICHT -- die Wiener
    Philharmoniker stehen dort nirgends in den Tags, nur im Coverbild.
    Geraten wird nichts."""
    aus, gesehen = [], set()
    for key, rolle in KLASSIK_ROLLEN:
        wert = _erster(tags, key)
        if not wert:
            continue
        wert = _entities_aufloesen(wert).strip()
        if not wert or wert.lower() in gesehen:
            continue
        gesehen.add(wert.lower())
        aus.append({"rolle": rolle, "name": wert})
    return aus


_ARTIKEL = {"the", "a", "an", "der", "die", "das", "los", "las", "les", "el", "la"}


def _name_genannt(name, text):
    """Prueft, ob ein Name im Text vorkommt - auch in vollerer Form.

    "Paul Cauthen" gilt als genannt, wenn dort "Paul Mark Cauthen" steht:
    alle Bestandteile in richtiger Reihenfolge, bis zu zwei Woerter
    dazwischen (Mittelname, "von", "de"). Ein exakter Teilstringvergleich
    hatte genau diesen Fall verfehlt und den Kuenstlertext bei Room 41
    faelschlich als "Text" eingeordnet.

    Artikel werden ignoriert, damit "The Doors" auch auf "Doors" passt."""
    if not name or not text:
        return False
    teile = [w for w in re.findall(r"\w+", name.lower())
             if w not in _ARTIKEL and len(w) > 1]
    if not teile:
        return False
    t = text.lower()
    if " ".join(teile) in t:
        return True
    muster = r"\W+(?:\w+\W+){0,2}".join(re.escape(w) for w in teile)
    return re.search(muster, t) is not None


def _titel_kern(titel):
    """Albumtitel ohne Klammerzusaetze - "Room 41 (Deluxe Edition)" -> "Room 41"."""
    if not titel:
        return ""
    return re.sub(r"[\(\[].*?[\)\]]", "", titel).strip(" -–:").strip()


# Der Text sagt meist selbst, was er beschreibt. Das ist verlaesslicher als
# jeder Titelvergleich: eine Stichprobe von 8 umgewidmeten Texten enthielt
# 5 Fehlurteile, weil der Albumtitel zu kurz ("Ram", "4"), abgekuerzt
# ("F.L.M.") oder um Zusaetze erweitert war ("Clandestino / Bloody Border")
# -- und in einem Albumtext steht der Kuenstlername naturgemaess im ersten
# Satz ("Ramones is the debut studio album by ... Ramones").
_ALBUMTEXT_MUSTER = re.compile(
    r"\b(?:is|was|ist)\s+(?:the|a|an|das|der|die|ein|eine)\b[^.]{0,60}?"
    r"\b(?:album|ep|lp|mixtape|soundtrack|platte|single)\b"
    r"|\bstudio(?:\s|-)?album\b|\bdebut\s+(?:album|ep)\b"
    r"|\blive\s+album\b|\bcompilation\s+album\b"
    r"|\bveroeffentlichte[ns]?\s+album\b|\bwurde\s+.{0,40}veröffentlicht\b",
    re.I)

_DISKOGRAFIE_MUSTER = re.compile(
    r"^\s*(?:the\s+)?discograph(?:y|ie)\b|\bdiskografie\s+(?:von|des|der)\b"
    r"|\bis\s+a\s+(?:\w+\s+){0,3}discography\b|\bfollowing\s+is\s+a[^.]{0,40}discography\b",
    re.I)

_KUENSTLERTEXT_MUSTER = re.compile(
    r"\b(?:is|was|ist|war)\s+(?:a|an|ein|eine)\b[^.]{0,70}?"
    r"\b(?:singer|songwriter|musician|rapper|band|group|duo|trio|composer|"
    r"pianist|guitarist|drummer|saxophonist|violinist|conductor|producer|"
    r"vocalist|artist|ensemble|orchestra|quartet|s[äa]nger(?:in)?|"
    r"musiker(?:in)?|komponist(?:in)?|dirigent(?:in)?|gruppe)\b"
    r"|\b(?:was|were)\s+born\b|\bgeboren\b|\bformed\s+in\s+\d{4}\b"
    r"|\bgegr[üu]ndet\b",
    re.I)


def textart_bestimmen(text, albumtitel, artist, vorgabe):
    """Unterscheidet Albumtext von Kuenstlerbiografie.

    Bei "Paul Cauthen - Room 41" steht unter `comment` eine Biografie des
    Kuenstlers, die sogar ein Album von 2022 nennt -- ueberschrieben mit
    "Album-Text" ist das schlicht falsch.

    Regel (so vom Nutzer vorgegeben): nennt der Text den Albumtitel nicht,
    fuehrt aber den Kuenstlernamen im ersten Satz, ist es eine Biografie.
    Im Zweifel die neutrale Ueberschrift "Text" -- nie etwas erfinden."""
    if not text or len(text) < 120:
        return vorgabe, None
    kurz = text[:800]
    at = _titel_kern(albumtitel)
    ar = (artist or "").strip()

    # Erster Satz: bis zum ersten Punkt, der von Leerzeichen+Grossbuchstabe
    # gefolgt wird (damit "Mr. Smith" oder "1998." nicht trennen).
    m = re.search(r"\.\s+(?=[A-Z\u00c0-\u00de])", text[:400])
    erster = text[:m.start()] if m else text[:300]

    # 1. Nennt der Text den Albumtitel? Dann ist der Albumbezug belegt.
    if len(at) >= 4 and _name_genannt(at, kurz):
        return vorgabe, None

    # 2. Sagt der Text selbst, dass er ein Album beschreibt? Das schlaegt
    #    jeden Titelvergleich - auch bei Titeln wie "Ram" oder "4".
    if _ALBUMTEXT_MUSTER.search(erster):
        return vorgabe, None

    # 3. Eine Diskografie ist kein Albumtext, sondern Werkuebersicht.
    if _DISKOGRAFIE_MUSTER.search(text[:200]):
        return "Diskografie", "diskografie"

    # 4. Beschreibt sich der Text als Personen- oder Bandportraet - und
    #    nennt er den Kuenstler im ersten Satz?
    if len(ar) >= 3 and _KUENSTLERTEXT_MUSTER.search(erster) \
            and _name_genannt(ar, erster):
        return "Über den Künstler", "kuenstler"

    # 5. Zweifel: neutrale Ueberschrift, nichts behaupten.
    if len(at) >= 4 or len(ar) >= 3:
        return "Text", "unklar"
    return vorgabe, None


def url_zu_pfad(res_url):
    """Bildet eine MinimServer-res-URL auf den Dateipfad ab.
    Gibt None zurück, wenn die URL nicht von MinimServer stammt oder die
    Datei nicht auffindbar ist."""
    if not res_url:
        return None
    m = re.match(r"https?://[^/]+/minimserver/[^/]+/(.*)$", res_url)
    if not m:
        return None
    rest = minim_dekodieren(m.group(1))
    # MinimServer nennt die Bibliothekswurzel "Musik"
    teile = rest.split("/", 1)
    if len(teile) != 2:
        return None
    relativ = teile[1]
    kandidat = os.path.join(ROOT, relativ)
    if not innerhalb(ROOT, kandidat):
        return None
    if os.path.isfile(kandidat):
        return kandidat
    # Endung fehlt in der res-URL – ergänzen
    for treffer in sorted(glob.glob(glob.escape(kandidat) + ".*")):
        if innerhalb(ROOT, treffer) and os.path.isfile(treffer):
            return treffer
    return None


def _tags_lesen(pfad):
    try:
        f = FLAC(pfad)
    except Exception as e:
        log.warning("Tags nicht lesbar (%s): %s", pfad, e)
        return {}, None
    tags = {k.lower(): list(v) for k, v in (f.tags or {}).items()}
    info = {"bits": f.info.bits_per_sample, "samplerate": f.info.sample_rate,
            "dauer_s": round(f.info.length, 1), "bilder": len(f.pictures)}
    return tags, info


def _norm_titel(s):
    """Normalisiert einen Titel fuer den Vergleich mit dem Index."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _textkern(s, laenge=160):
    """Reduziert einen Text auf seinen Kern, um Dubletten zu erkennen:
    Kleinschreibung, nur Buchstaben und Zahlen, auf `laenge` gekuerzt."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())[:laenge]


def _ist_dublette(a, b):
    """Zwei Texte gelten als dasselbe, wenn ihr Anfang uebereinstimmt.

    Bei 52 % der Alben mit Wikipedia-Treffer ist der `comment`-Tag inhaltlich
    der Wikipedia-Text - offenbar wurde er irgendwann in die Tags uebernommen
    (geprueft 2026-09-17 an 784 Alben). Ohne diese Pruefung stuende derselbe
    Absatz zweimal auf der Seite: einmal unter "Aus meinen Notizen", einmal
    unter "Nachgeschlagen". Gorans Fassung hat Vorrang, die externe wird dann
    weggelassen."""
    return _aehnlich(a, b)


# Endungen, die einen abgeschnittenen Fremdtext verraten. Solche Texte sind
# Ausschnitte, nicht Gorans eigene Formulierung - sie werden als Auszug
# gekennzeichnet und die Endung entfernt.
_AUSZUG_MUSTER = re.compile(
    r"(\s*\.{3,}\s*$|\s*…\s*$|\s*read\s+more\s*\.*\s*$|"
    r"\s*\[?weiterlesen\]?\s*\.*\s*$|\s*mehr\s+erfahren\s*\.*\s*$)", re.I)


def _entities_aufloesen(text):
    """Wandelt HTML-Entities in Zeichen um.

    In den Tags stehen Reste wie `band&#x27;s` oder `&amp;` - sie stammen aus
    Web-Quellen, die das musik_master-Skript uebernommen hat. Ohne diese
    Umwandlung erscheinen sie roh in der Anzeige. Zweimal aufloesen, weil
    doppelt kodierte Faelle vorkommen (`&amp;#x27;`)."""
    if not text or "&" not in text:
        return text
    vorher = text
    for _ in range(2):
        nachher = html.unescape(vorher)
        if nachher == vorher:
            break
        vorher = nachher
    return vorher


def _wiederholung_kuerzen(text):
    """Entfernt eine unmittelbare Wiederholung desselben Textes.

    In den copyright-Tags steht der Inhalt teils doppelt hintereinander:

        "2019 Lightning Rod Records 2019 Lightning Rod Records"

    Das ist EIN Tag-Wert, kein doppelter Eintrag - offenbar wurden beim
    Download die (P)- und die (C)-Angabe aneinandergehaengt, die bei diesem
    Label identisch sind. Angezeigt gehoert das nur einmal.

    Gekuerzt wird nur bei einer exakten Verdopplung; bei aehnlichen, aber
    nicht gleichen Haelften bleibt der Text unberuehrt - dort koennten
    tatsaechlich zwei verschiedene Rechteinhaber genannt sein."""
    if not text:
        return text
    s = text.strip()
    # Nur die exakte Verdopplung: beide Haelften muessen ZEICHENGLEICH sein.
    #
    # Die frueher zusaetzlich geprueften Trennzeichen-Varianten sind entfernt.
    # Sie hatten Faelle zusammengefasst, die nur aehnlich aussahen - und bei
    # Copyright-Angaben ist der Unterschied zwischen "℗" und "©" oder zwischen
    # zwei Schreibweisen eines Labels bedeutungstragend. Wo nicht sicher
    # dasselbe steht, wird nichts angetastet.
    mitte, rest = divmod(len(s), 2)
    if rest == 0 and mitte > 8 and s[:mitte] == s[mitte:]:
        return s[:mitte].strip()
    # Fall mit genau einem Trennzeichen zwischen zwei identischen Haelften,
    # z.B. "X Y X Y" -> pruefen, ob die Mitte ein einzelnes Leerzeichen ist
    if rest == 1 and mitte > 8 and s[mitte] == " " and s[:mitte] == s[mitte + 1:]:
        return s[:mitte].strip()
    return s


def _auszug_pruefen(text):
    """Erkennt, ob ein Text abgeschnitten ist, und bereinigt die Endung."""
    if not text:
        return text, False
    treffer = _AUSZUG_MUSTER.search(text)
    if not treffer:
        return text, False
    return text[:treffer.start()].rstrip(" .,;–-"), True


def _gemeinsamer_anfang(a, b):
    """Laenge des gemeinsamen Anfangs zweier normalisierter Texte."""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _aehnlich(a, b, mindest_anfang=30, schwelle=0.62):
    """Prueft, ob zwei Texte auf dieselbe Quelle zurueckgehen.

    Der naheliegende Weg - ein Aehnlichkeitsmass ueber den ganzen Text -
    versagt hier. In den Dateien stehen zwei Wikipedia-REVISIONEN desselben
    Albums:

        "News of the World is the sixth studio album by English rock band
         Queen, released in 1977. Containing hit songs ..."
        "News of the World is the sixth studio album by the British rock band
         Queen, released on 28 October 1977 by EMI Records ..."

    Nach dem ersten Satz gehen sie auseinander und nennen andere Details -
    difflib kommt auf 0,21, also weit unter jeder sinnvollen Schwelle. Fuer
    einen Leser ist es trotzdem zweimal derselbe Absatz.

    Das verlaessliche Signal ist der gemeinsame ANFANG: 37 Zeichen
    ("newsoftheworldisthesixthstudioalbumby") teilen zwei unabhaengig
    geschriebene Texte praktisch nie. 30 Zeichen entsprechen etwa sechs bis
    acht Woertern und sind damit spezifisch genug; kuerzere Texte werden
    zusaetzlich ueber difflib geprueft."""
    ka, kb = _textkern(a, 400), _textkern(b, 400)
    if not ka or not kb:
        return False
    if _gemeinsamer_anfang(ka, kb) >= mindest_anfang:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= schwelle


def _dubletten_zusammenfassen(bloecke):
    """Entfernt Textbloecke, die einen anderen nur wiederholen.

    Verglichen wird der normalisierte Textkern. Ist ein Block inhaltlich in
    einem laengeren enthalten, faellt der kuerzere weg - aber nur bei
    Prosafeldern. Besetzung, Produktion und Stimmung bleiben immer stehen,
    auch wenn sie sich zufaellig aehneln, weil sie unterschiedliche Fragen
    beantworten."""
    # Vom Dublettenvergleich ausgenommen sind nur Felder, die andere Fragen
    # beantworten - Besetzung, Produktion, Stimmung. `description` gehoert
    # NICHT dazu: Ob dieser Text von Goran stammt, ist unbelegt (bei Tracy
    # Chapman wurde das zunaechst angenommen, aber nichts beweist es). Gilt
    # dieselbe Regel wie bei der entfernten Marke "eigene Worte": keine
    # Autorschaft unterstellen, also auch keinen Sonderschutz gewaehren.
    schutz = {"personnel", "producer", "mood"}
    prosa = [b for b in bloecke if b["feld"] not in schutz]
    rest = [b for b in bloecke if b["feld"] in schutz]

    # laengste zuerst, damit kuerzere Wiederholungen dagegen geprueft werden
    prosa.sort(key=lambda b: -len(b["text"]))
    behalten = []
    for b in prosa:
        ist_wiederholung = any(_aehnlich(b["text"], gross["text"]) for gross in behalten)
        if ist_wiederholung:
            b["verdeckt_von"] = True
        else:
            behalten.append(b)

    # Ursprungsreihenfolge wiederherstellen
    erlaubt = {id(b) for b in behalten}
    return [b for b in bloecke if b["feld"] in schutz or id(b) in erlaubt]


def _quelle_erkennen(text, tags):
    """Versucht, die Herkunft eines Textes zu bestimmen.

    Goran hat Wikipedia- und Last.fm-Auszuege ueber sein musik_master-Skript in
    die Tags uebernommen (bestaetigt 2026-09-17). Die Texte stehen damit in der
    Datei, sind aber nicht seine Formulierung. Wo ein URL-Tag vorliegt oder der
    Text ein erkennbares Muster hat, wird die Quelle benannt."""
    url = _erster(tags, "url", "website")
    if url:
        for name, kennung in (("Wikipedia", "wikipedia."), ("Last.fm", "last.fm"),
                              ("Discogs", "discogs."), ("AllMusic", "allmusic.")):
            if kennung in url.lower():
                return name, url
        return "eigene Quellenangabe", url
    if not text:
        return None, None
    anfang = text[:400].lower()
    # Wikipedia-Artikel beginnen fast immer mit "X is/was the ... album by"
    if re.search(r"\bis (the |a |an )?(debut |second |third |studio |live |compilation )*album\b", anfang) \
       or re.search(r"\bwas (the |a |an )?(debut |second |third |studio |live )*album\b", anfang):
        return "Wikipedia (Wortlaut)", None
    return None, None


def _erster(tags, *keys):
    for k in keys:
        v = tags.get(k)
        if v:
            return v[0]
    return ""


# Tontraeger, die eine bestimmte Auflaesung technisch nicht liefern koennen.
# Eine CD ist auf 16 bit / 44,1 kHz festgelegt, eine Schallplatte hat gar keine
# digitale Auflaesung. Meldet MusicBrainz eines davon, waehrend die Datei
# hochaufloesend ist, wurde eine andere Pressung getroffen.
NUR_CD_AUFLOESUNG = {"cd", "cd-r", "enhanced cd", "hdcd", "copy control cd"}
ANALOG_MEDIEN = {"12\" vinyl", "7\" vinyl", "10\" vinyl", "vinyl",
                 "cassette", "reel-to-reel", "8-track cartridge"}


def _mb_passt_zur_datei(mb, album_row, dateiinfo=None):
    """Entscheidet, ob die MusicBrainz-Angaben zur vorliegenden Datei gehoeren.

    Der `score` aus der Suche sagt nur, wie gut der TITEL passt - nicht die
    Pressung. Zwei belegte Fehlgriffe vom 17.09.2026:

      Yes - Going for the One: Datei 24/192 mit 5 Titeln von 1977; MusicBrainz
        liefert eine Rhino-CD von 2003 mit 12 Titeln. Score 100.
      Queen - News of the World: Datei 16/44,1 (Remaster 2011); MusicBrainz
        liefert die US-Schallplatte von 1977, Katalognummer 6E 122. Score 100.

    Wer solche Werte anzeigt, behauptet eine Herkunft, die nicht stimmt -
    schlimmer als das Feld weggelassen zu haben. Deshalb gilt:

      SICHER  - Barcode oder Release-ID steht in der Datei und stimmt ueberein.
      PLAUSIBEL - Titelzahl identisch, Jahr hoechstens ein Jahr daneben, und
                  das Medium kann die Auflaesung der Datei tragen.
      SONST   - die Pressungsangaben werden verworfen.

    Zurueckgegeben wird (passt, begruendung) - die Begruendung erscheint als
    Hinweis, damit nachvollziehbar bleibt, warum ein Feld fehlt.
    """
    if not mb:
        return False, "keine MusicBrainz-Angaben"

    tags = {}
    if album_row is not None and album_row["tags_json"]:
        try:
            tags = json.loads(album_row["tags_json"])
        except (TypeError, ValueError):
            tags = {}

    # --- sicher: eindeutige Kennung in der Datei ---
    datei_barcode = re.sub(r"\D", "", _erster(tags, "barcode") or "")
    mb_barcode = re.sub(r"\D", "", mb.get("barcode") or "")
    if datei_barcode and mb_barcode:
        if datei_barcode == mb_barcode:
            return True, "Barcode der Datei stimmt überein"
        return False, "Barcode der Datei weicht ab"
    datei_mbid = (_erster(tags, "musicbrainz_albumid") or "").strip().lower()
    if datei_mbid and mb.get("mbid"):
        if datei_mbid == mb["mbid"].lower():
            return True, "Release-ID der Datei stimmt überein"
        return False, "Release-ID der Datei weicht ab"

    # --- plausibel: drei unabhaengige Merkmale ---
    gruende = []
    mb_tracks = mb.get("trackzahl")
    datei_tracks = album_row["tracks"] if album_row is not None else None
    if mb_tracks and datei_tracks and int(mb_tracks) != int(datei_tracks):
        gruende.append("Titelzahl %s statt %s" % (mb_tracks, datei_tracks))

    mb_jahr = (mb.get("date") or "")[:4]
    datei_jahr = ((album_row["jahr"] if album_row is not None else "") or "")[:4]
    if mb_jahr.isdigit() and datei_jahr.isdigit() and abs(int(mb_jahr) - int(datei_jahr)) > 1:
        gruende.append("Jahr %s statt %s" % (mb_jahr, datei_jahr))

    medium = (mb.get("medium") or "").strip().lower()
    bits = (dateiinfo or {}).get("bits") or (album_row["bits"] if album_row is not None else 0) or 0
    rate = (dateiinfo or {}).get("samplerate") or (album_row["samplerate"] if album_row is not None else 0) or 0
    hochaufloesend = bits > 16 or rate > 48000
    if medium:
        if hochaufloesend and (medium in NUR_CD_AUFLOESUNG or medium in ANALOG_MEDIEN):
            gruende.append("Medium %s kann %d/%.1f nicht tragen"
                           % (mb.get("medium"), bits, rate / 1000.0))
        elif not hochaufloesend and medium in ANALOG_MEDIEN:
            gruende.append("Medium %s passt nicht zu einer digitalen Datei" % mb.get("medium"))

    if gruende:
        return False, "; ".join(gruende)
    return True, "Titelzahl, Jahr und Medium passen"


# Rollenbezeichnungen, wie sie in den Tags stehen, auf lesbares Deutsch.
# Erhoben am 17.09.2026 aus 600 Alben; abgedeckt sind damit die haeufigsten
# Bezeichnungen. Die Schluessel sind normalisiert (klein, ohne Leerzeichen und
# Bindestriche), weil dieselbe Rolle in drei Schreibweisen vorkommt -
# "MasteringEngineer", "Mastering Engineer" und "mastering engineer".
ROLLEN = {
    "mainartist": "Hauptinterpret", "associatedperformer": "Mitwirkend",
    "producer": "Produktion", "coproducer": "Ko-Produktion",
    "executiveproducer": "Ausführende Produktion",
    "composer": "Komposition", "lyricist": "Text",
    "composerlyricist": "Musik und Text", "writer": "Autor",
    "arranger": "Arrangement", "orchestrator": "Orchestrierung",
    "conductor": "Dirigat", "performer": "Ausführend",
    "engineer": "Toningenieur", "recordingengineer": "Aufnahme",
    "mixingengineer": "Mischung", "mixer": "Mischung",
    "masteringengineer": "Mastering", "mastering": "Mastering",
    "assistantengineer": "Toningenieur-Assistenz",
    "assistantmixer": "Mischungs-Assistenz",
    "assistantrecordingengineer": "Aufnahme-Assistenz",
    "studiopersonnel": "Studiopersonal", "musicpublisher": "Musikverlag",
    "vocals": "Gesang", "vocalist": "Gesang", "backgroundvocals": "Begleitgesang",
    "backingvocals": "Begleitgesang", "leadvocals": "Leadgesang",
    "guitar": "Gitarre", "electricguitar": "E-Gitarre",
    "acousticguitar": "Akustikgitarre", "slideguitar": "Slide-Gitarre",
    "bassguitar": "Bassgitarre", "bass": "Bass", "doublebass": "Kontrabass",
    "drums": "Schlagzeug", "percussion": "Perkussion",
    "keyboards": "Keyboards", "piano": "Klavier", "organ": "Orgel",
    "synthesizer": "Synthesizer", "electricpiano": "E-Piano",
    "harmonica": "Mundharmonika", "saxophone": "Saxofon", "trumpet": "Trompete",
    "trombone": "Posaune", "flute": "Flöte", "violin": "Violine",
    "viola": "Viola", "cello": "Cello", "harp": "Harfe",
    "strings": "Streicher", "horns": "Bläser", "orchestra": "Orchester",
    "programming": "Programmierung", "sampling": "Sampling",
    "featuredartist": "Gastbeitrag", "remixer": "Remix",
    "artwork": "Gestaltung", "photography": "Fotografie",
    "liner notes": "Begleittext",
}


def _rolle_lesbar(rohdaten):
    """Uebersetzt eine Rollenbezeichnung, wenn sie bekannt ist.

    Unbekannte Bezeichnungen bleiben stehen - eine falsche Uebersetzung waere
    schlimmer als das englische Original. CamelCase wird dabei aufgetrennt,
    damit aus "AssistantEngineer" wenigstens "Assistant Engineer" wird."""
    schluessel = re.sub(r"[\s_-]+", "", rohdaten).strip().lower()
    if schluessel in ROLLEN:
        return ROLLEN[schluessel]
    mit_leer = rohdaten.strip().lower()
    if mit_leer in ROLLEN:
        return ROLLEN[mit_leer]
    # CamelCase auftrennen: "AssistantEngineer" -> "Assistant Engineer"
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", rohdaten.strip())


def _besetzung_zerlegen(text):
    """Zerlegt einen Personnel-Tag in Personen mit Rollen.

    Das Format aus den Dateien: Personen sind durch " - " getrennt, innerhalb
    steht der Name zuerst, danach die Rollen durch Komma:

        "Rob Kinelski, Mixer - Tedd T., Producer - Joel Smallbone, ComposerLyricist"

    Als Fliesstext ist das kaum lesbar. Zurueckgegeben wird eine Liste von
    {name, rollen} - je Person eine Zeile in der Anzeige.

    Vorsicht bei Namen mit Punkt ("Tedd T.") und bei Eintraegen ohne Rolle;
    beides kommt vor und darf nicht zu leeren Zeilen fuehren."""
    if not text:
        return []
    personen = []
    for teil in re.split(r"\s+-\s+", text):
        felder = [x.strip() for x in teil.split(",") if x.strip()]
        if not felder:
            continue
        name = felder[0]
        rollen = [_rolle_lesbar(r) for r in felder[1:]]
        # Doppelte Rollen zusammenfassen, Reihenfolge behalten
        gesehen, sauber = set(), []
        for r in rollen:
            if r.lower() not in gesehen:
                gesehen.add(r.lower())
                sauber.append(r)
        personen.append({"name": name, "rollen": sauber})
    # Personen zusammenfassen, die mehrfach auftauchen
    zusammen = {}
    for pe in personen:
        s = pe["name"].lower()
        if s in zusammen:
            for r in pe["rollen"]:
                if r.lower() not in {x.lower() for x in zusammen[s]["rollen"]}:
                    zusammen[s]["rollen"].append(r)
        else:
            zusammen[s] = pe
    return list(zusammen.values())


class Aufloeser:
    def __init__(self, db=DB):
        self.db = db

    def _con(self):
        con = sqlite3.connect(self.db, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def album_aus_pfad(self, con, pfad):
        """Ordnet eine Datei ihrem Album zu.

        Der Index fasst Disc-Unterordner zu einem Album zusammen (sonst waere
        "The Wall" zwei Alben mit je 13 Titeln). Ein Dateipfad endet aber auf
        ".../Disc 1/02. The Thin Ice.flac" - der Ordner des Albums ist dann
        eine Ebene hoeher. Deshalb wird der direkte Elternordner geprueft und,
        falls der kein Album ist, dessen Elternordner.

        Zuverlaessiger als der Namensabgleich: nur dieser Weg trifft bei den
        10 Album-Duplikaten der Bibliothek garantiert das richtige."""
        if not local_cover.inside(ROOT, pfad):
            return None
        rel = os.path.relpath(pfad, ROOT)
        kandidaten = []
        d = os.path.dirname(rel)
        while d and d not in (".", os.sep):
            kandidaten.append(d)
            d = os.path.dirname(d)
        if os.path.dirname(rel) in ("", "."):
            kandidaten.append(".")        # nur wirklich lose Dateien
        for k in kandidaten[:3]:
            row = con.execute("SELECT * FROM album WHERE ordner=?", (k,)).fetchone()
            if row:
                return row
        return None

    def album_fallback(self, con, didl):
        """Wenn der Pfad nicht auflösbar war: über Artist/Album suchen."""
        import unicodedata

        def norm(s):
            if not s:
                return ""
            s = unicodedata.normalize("NFKD", s)
            s = "".join(c for c in s if not unicodedata.combining(c))
            return re.sub(r"[^a-z0-9]+", "", s.lower())

        ka = norm(didl.get("albumartist") or didl.get("artist"))
        kb = norm(didl.get("album"))
        if not (ka and kb):
            return None
        treffer = con.execute(
            "SELECT * FROM album WHERE k_artist=? AND k_album=? ORDER BY samplerate DESC, bits DESC",
            (ka, kb)).fetchall()
        if len(treffer) == 1:
            return treffer[0]
        if len(treffer) > 1:
            # Mehrdeutig (die Bibliothek enthält 10 Album-Duplikate) – die
            # höchste Auflösung ist die plausibelste Wahl, aber wir merken es an.
            log.info("Fallback mehrdeutig für %s / %s: %d Treffer",
                     ka, kb, len(treffer))
            return treffer[0]
        return None

    def anreicherung(self, con, album_id):
        raus = {}
        for r in con.execute("SELECT quelle, status, daten FROM enrich WHERE album_id=?",
                             (album_id,)):
            if r["status"] == "ok" and r["daten"]:
                try:
                    raus[r["quelle"]] = json.loads(r["daten"])
                except json.JSONDecodeError:
                    pass
        return raus

    def dynamikumfang(self, con, album_id):
        """Liefert den gemessenen DR-Wert des Albums, falls vorhanden.

        Die Skala folgt der Dynamic Range Database, damit die Farben dasselbe
        bedeuten wie dort: bis 7 rot, 8-9 orange, 10-11 gelb, ab 12 gruen."""
        r = con.execute("""SELECT dr, dr_exakt, dr_min, dr_max, peak_db, rms_db,
                                  tracks, gemessen_am
                           FROM dr_album WHERE album_id=?""", (album_id,)).fetchone()
        if not r:
            return None
        dr = r["dr"]
        stufe = "rot" if dr <= 7 else "orange" if dr <= 9 else "gelb" if dr <= 11 else "gruen"
        return {"dr": dr, "exakt": round(r["dr_exakt"], 2), "min": r["dr_min"],
                "max": r["dr_max"], "peak_db": round(r["peak_db"], 2),
                "rms_db": round(r["rms_db"], 2), "tracks": r["tracks"],
                "stufe": stufe, "gemessen_am": r["gemessen_am"]}

    def tagverdacht(self, con, album_id):
        """Liefert einen Hinweis, wenn die Tags dieses Albums auffaellig sind.

        Grundlage ist die Tabelle `verdacht` aus bin/pruefen.py. Angezeigt
        wird erst ab 70 Punkten - darunter sind die Signale zu schwach, um
        den Leser zu beunruhigen. Der Hinweis bleibt dezent und verlinkt auf
        die Liste; korrigiert wird nichts."""
        try:
            r = con.execute("SELECT punkte, gruende FROM verdacht WHERE album_id=?",
                            (album_id,)).fetchone()
        except Exception:
            return None      # Tabelle gibt es erst nach dem ersten Prueflauf
        if not r or r["punkte"] < 70:
            return None
        try:
            gruende = json.loads(r["gruende"])
        except (TypeError, ValueError):
            gruende = []
        return {"punkte": r["punkte"], "gruende": gruende}

    def trackliste(self, con, album_id):
        return [dict(r) for r in con.execute(
            "SELECT t.disc, t.nr, t.titel, t.dauer_s, t.pfad, "
            "       ROUND(d.dr,1) AS dr "
            "FROM track t LEFT JOIN dr_track d ON d.track_id = t.id "
            "WHERE t.album_id=? ORDER BY t.disc, t.nr", (album_id,))]

    # Quellenarten, bei denen es keine Bibliotheksdatei geben kann.
    OHNE_DATEI_TYPEN = {"netaux", "analog", "digital", "radio", "receiver",
                        "spotify", "scd"}

    @staticmethod
    def bitperfekt(details, dateiinfo, quelle=None, album_bekannt=False):
        """Vergleicht, was der Linn empfaengt, mit dem, was in der Datei steht.

        Der Linn meldet ueber Info/Details, was tatsaechlich bei ihm ankommt
        (CodecName, BitDepth, SampleRate, Lossless). Weicht das von der Datei
        ab, hat etwas unterwegs umgerechnet - ein Resampler in der Software,
        eine Lautstaerkeregelung im Digitalpfad, oder die Quelle liefert von
        sich aus etwas anderes (Roon mit DSP, AirPlay mit fixen 16/44,1).

        Drei Zustaende, bewusst auch ein neutraler: Wo keine Dateiangabe
        vorliegt (Radio, AirPlay, Eingaenge), wird nichts behauptet."""
        d = details or {}
        stream_bits, stream_sr = d.get("bits") or 0, d.get("samplerate") or 0
        lossless = d.get("lossless")
        if not (stream_bits and stream_sr):
            return {"stand": "unbekannt", "text": "Bitperfekt: nicht prüfbar",
                    "grund": "Der Linn liefert keine Stream-Details."}
        if not dateiinfo:
            typ = ((quelle or {}).get("typ") or "").lower()
            name = (quelle or {}).get("name") or ""
            teile = ["%d bit / %.1f kHz" % (stream_bits, stream_sr / 1000.0)]
            if d.get("codec"):
                teile.append(d["codec"])
            # AirPlay/Net Aux: Der Linn empfaengt ALAC in 16/44,1 - das ist
            # verlustfrei UEBERTRAGEN. Was vorher am Sender passiert ist,
            # weiss er nicht: YouTube Music, Spotify und Apple Music liefern
            # verlustbehaftet, AirPlay packt es dann in ALAC. Ein schlichtes
            # "verlustfrei" waere hier eine Falschaussage.
            if typ in ("netaux", "receiver"):
                return {"stand": "uebertragung",
                        "text": "Übertragung verlustfrei · Quelle unbekannt",
                        "grund": "%s liefert %s. Verlustfrei ist nur die Strecke "
                                 "zum Linn – womit die App vorher gearbeitet hat "
                                 "(z. B. YouTube Music), ist daraus nicht "
                                 "erkennbar." % (name or "Die Quelle", " · ".join(teile))}
            if typ in ("analog", "digital", "radio"):
                return {"stand": "unbekannt", "text": "Bitperfekt: nicht prüfbar",
                        "grund": "%s ist ein externer Eingang – kein Dateibezug. "
                                 "Stream: %s." % (name or "Die Quelle", " · ".join(teile))}
            if album_bekannt:
                return {"stand": "unbekannt", "text": "Bitperfekt: nicht prüfbar",
                        "grund": "Titel in der Bibliothek nicht gefunden – kein "
                                 "Dateivergleich möglich. Stream: %s."
                                 % " · ".join(teile)}
            return {"stand": "unbekannt", "text": "Bitperfekt: nicht prüfbar",
                    "grund": "Album nicht in der Bibliothek – kein "
                             "Dateivergleich möglich. Stream: %s."
                             % " · ".join(teile)}
        datei_bits, datei_sr = dateiinfo.get("bits") or 0, dateiinfo.get("samplerate") or 0
        if not (datei_bits and datei_sr):
            return {"stand": "unbekannt", "text": "Bitperfekt: nicht prüfbar",
                    "grund": "Dateiangaben unvollständig."}
        abweichungen = []
        if datei_sr != stream_sr:
            abweichungen.append("%.1f kHz → %.1f kHz" % (datei_sr / 1000.0, stream_sr / 1000.0))
        if datei_bits != stream_bits:
            abweichungen.append("%d bit → %d bit" % (datei_bits, stream_bits))
        if lossless is False:
            abweichungen.append("Stream nicht verlustfrei")
        if abweichungen:
            return {"stand": "abweichend", "text": "Bitperfekt: nein – umgerechnet",
                    "grund": "Datei %d/%.1f → Linn empfängt %d/%.1f"
                             % (datei_bits, datei_sr / 1000.0, stream_bits, stream_sr / 1000.0),
                    "abweichungen": abweichungen}
        return {"stand": "bitperfekt", "text": "Bitperfekt: ja",
                "grund": "Datei und Stream stimmen überein: %d bit / %.1f kHz%s"
                         % (datei_bits, datei_sr / 1000.0,
                            ", verlustfrei" if lossless else "")}

    def track_finden(self, con, album_id, didl, pfad):
        """Bestimmt, welcher Eintrag der Trackliste gerade laeuft.

        Ueber die res-URL ist das der Dateipfad. Kommt die Wiedergabe aus einer
        Quelle ohne Bibliotheksdatei - Roon liefert `scd://`, AirPlay gar
        nichts -, muss der Titel anders gefunden werden: erst ueber Disc und
        Titelnummer, dann ueber den normalisierten Titel. Ohne das bleibt die
        Trackliste unmarkiert, obwohl das Album erkannt wurde."""
        if pfad:
            return os.path.relpath(pfad, ROOT)
        if not album_id:
            return None
        nr = (didl.get("tracknummer") or "").strip()
        titel = _norm_titel(didl.get("titel"))
        if nr.isdigit():
            r = con.execute("SELECT pfad FROM track WHERE album_id=? AND nr=? "
                            "ORDER BY disc LIMIT 1", (album_id, int(nr))).fetchone()
            if r:
                return r["pfad"]
        if titel:
            for r in con.execute("SELECT pfad, k_titel FROM track WHERE album_id=?",
                                 (album_id,)):
                if r["k_titel"] == titel:
                    return r["pfad"]
        return None

    def bauen(self, uri, didl, details, zeit=None, quelle=None, folge=0):
        """Stellt das vollständige Now-Playing-Objekt zusammen."""
        pfad = url_zu_pfad(uri)
        tags, dateiinfo = ({}, None)
        if pfad:
            tags, dateiinfo = _tags_lesen(pfad)

        with self._con() as con:
            album_row = self.album_aus_pfad(con, pfad) if pfad else None
            quelle_zuordnung = "res-url"
            if album_row is None:
                album_row = self.album_fallback(con, didl)
                quelle_zuordnung = "fallback" if album_row else "keine"

            album_id = album_row["id"] if album_row else None
            extern = self.anreicherung(con, album_id) if album_id else {}
            dyn = self.dynamikumfang(con, album_id) if album_id else None
            verdacht = self.tagverdacht(con, album_id) if album_id else None
            tracks = self.trackliste(con, album_id) if album_id else []
            if not tags and album_row and album_row["tags_json"]:
                tags = json.loads(album_row["tags_json"])
            cov = con.execute("SELECT breite, hoehe FROM cover WHERE album_id=?",
                              (album_id,)).fetchone() if album_id else None

        mb = extern.get("musicbrainz") or {}
        eigene = []
        for key, bezeichnung in EIGENE_FELDER:
            wert = _erster(tags, key)
            if not wert:
                continue
            text, ist_auszug = _auszug_pruefen(_entities_aufloesen(wert))
            qname, qurl = (None, None)
            if key not in EIGENE_WORTE:
                qname, qurl = _quelle_erkennen(text, tags)
            liste = _besetzung_zerlegen(text) if key in ("personnel", "producer") else None
            # Albumtext oder Kuenstlerbiografie? Nur bei den Prosafeldern
            # pruefen -- Besetzung und Produktion sind keine Texte.
            if key in ("description", "description_en", "description_de", "comment"):
                bezeichnung, textart = textart_bestimmen(
                    text, didl.get("album") or _erster(tags, "album"),
                    didl.get("albumartist") or didl.get("artist")
                    or _erster(tags, "albumartist", "artist"), bezeichnung)
            else:
                textart = None
            eigene.append({"feld": key, "titel": bezeichnung, "textart": textart,
                           "text": text,
                           "liste": liste,
                           "auszug": ist_auszug, "eigene_worte": key in EIGENE_WORTE,
                           "quelle": qname, "quelle_url": qurl,
                           "zeichen": len(text)})

        mb_passt, mb_grund = _mb_passt_zur_datei(mb, album_row, dateiinfo)
        MB_SCHLUESSEL = {"catalognumber": "catalognumber", "barcode": "barcode",
                         "releasecountry": "country", "source": "medium",
                         "label": "label"}
        fakten = []
        for key, bezeichnung in FAKTEN_FELDER:
            eigen = _wiederholung_kuerzen(_entities_aufloesen(_erster(tags, key)))
            wert, nachgeschlagen = eigen, False
            if not wert and mb_passt:
                # Pressungsangaben nur uebernehmen, wenn die Zuordnung haelt
                wert = mb.get(MB_SCHLUESSEL.get(key, ""), "") or ""
                nachgeschlagen = bool(wert)
            if wert:
                fakten.append({"titel": bezeichnung, "wert": str(wert),
                               "extern": nachgeschlagen,
                               "ausgabe": key in AUSGABE_FELDER})

        # Schlagwoerter (derzeit nur `mood`) als eigene kleine Liste. Sie
        # sind keine Prosa und stehen darum nicht bei den Texten.
        schlagworte = []
        for key, bezeichnung in SCHLAGWORT_FELDER:
            wert = _erster(tags, key)
            if wert:
                schlagworte.append({"titel": bezeichnung,
                                    "wert": _entities_aufloesen(wert).strip()})

        # Mehrfachfassungen desselben Textes zusammenfuehren.
        #
        # In den Dateien stehen haeufig drei Varianten desselben
        # Wikipedia-Absatzes: unter `description_en`, unter `comment` und
        # nochmals extern nachgeladen. Der Nutzer sieht dann dreimal
        # dasselbe. Behalten wird die laengste Fassung - sie ist die
        # vollstaendigste; die uebrigen werden mit Begruendung ausgeblendet.
        eigene = _dubletten_zusammenfassen(eigene)
        eigene_texte = [e["text"] for e in eigene]
        wiki = extern.get("wikipedia")
        if wiki and any(_ist_dublette(wiki.get("text"), x) for x in eigene_texte):
            wiki = dict(wiki, verworfen="deckt sich mit eigener Notiz")
            wiki.pop("text", None)
        lfm = extern.get("lastfm")
        if lfm and lfm.get("wiki") and any(
                _ist_dublette(lfm["wiki"], x) for x in eigene_texte):
            lfm = dict(lfm); lfm["wiki"] = ""

        with self._con() as con2:
            aktueller_pfad = self.track_finden(con2, album_id, didl, pfad)
            # Wenn keine Datei geoeffnet wurde (Roon liefert scd://), aber der
            # Titel ueber den Index gefunden wurde, liegen dessen technische
            # Werte dort bereits vor. Damit ist der Bitperfekt-Vergleich auch
            # bei Roon moeglich - und genau dort interessant, weil Roons DSP
            # umrechnen kann, ohne dass man es merkt.
            if not dateiinfo and aktueller_pfad:
                r = con2.execute(
                    "SELECT bits, samplerate, dauer_s FROM track WHERE pfad=?",
                    (aktueller_pfad,)).fetchone()
                if r and r["bits"]:
                    dateiinfo = {"bits": r["bits"], "samplerate": r["samplerate"],
                                 "dauer_s": r["dauer_s"], "aus_index": True}

        # Album- und Titelbezug trennen: Lyrics und Titel-DR gehoeren zum
        # laufenden Stueck, alles andere zum Album.
        titel_lyrics = _entities_aufloesen(_erster(tags, "lyrics"))
        return {
            "laeuft": True,
            "folge": folge,
            "zuordnung": quelle_zuordnung,
            "quelle": quelle or {},
            "ohne_titelinfos": not (didl.get("titel") or didl.get("album")
                                    or _erster(tags, "title") or _erster(tags, "album")),
            "zeit": zeit or {},
            "bitperfekt": self.bitperfekt(details, dateiinfo, quelle,
                                          album_bekannt=album_id is not None),
            # Trefferlage getrennt ausweisen. Zwei Dinge, die bisher in
            # "ohne_datei" zusammenfielen:
            #   album  - ist das laufende Album in der Bibliothek?
            #   titel  - ist der laufende Titel als Datei auffindbar?
            # Nur wenn beides zutrifft, ist ein Dateivergleich moeglich.
            # Und nur wenn das Album zutrifft, darf ein Album-DR gezeigt
            # werden - sonst waere es das DR eines fremden Albums.
            "treffer": {"album": album_id is not None,
                        "titel": aktueller_pfad is not None,
                        "zuordnung": quelle_zuordnung},
            "datei_quelle": ("index" if (dateiinfo or {}).get("aus_index")
                             else "datei" if dateiinfo else None),
            "titel": didl.get("titel") or _erster(tags, "title"),
            "album": didl.get("album") or _erster(tags, "album"),
            "artist": didl.get("albumartist") or didl.get("artist") or _erster(tags, "albumartist", "artist"),
            "komponist": didl.get("komponist") or _erster(tags, "composer"),
            "jahr": (didl.get("datum") or _erster(tags, "date"))[:4],
            "genre": didl.get("genre") or _erster(tags, "genre"),
            # Jahr und Genre koennen vom Player kommen (Roon liefert beides
            # aus seiner eigenen Datenbank) oder aus den Dateitags. Das ist
            # ein Unterschied, den die Anzeige nennen muss: bei Graham
            # Central Station standen 1973/Funk oben, waehrend in der
            # Bibliothek ein anderes Album derselben Band liegt.
            "kopf_herkunft": ("quelle" if (didl.get("datum") or didl.get("genre"))
                              and album_id is None else
                              "bibliothek" if album_id is not None else None),
            "tracknummer": didl.get("tracknummer") or _erster(tags, "tracknumber"),
            "qualitaet": self._qualitaet(details, dateiinfo),
            "fakten": fakten,
            "schlagworte": schlagworte,
            "mitwirkende": klassik_mitwirkende(tags),
            "mb_zuordnung": {"passt": mb_passt, "grund": mb_grund,
                             "titel": mb.get("titel")} if mb else None,
            "eigene": eigene,
            "lyrics": titel_lyrics,
            "album_id": album_id,
            "cover_extern": local_cover.reference(ROOT, pfad) if album_id is None else None,
            "cover": {"breite": cov["breite"], "hoehe": cov["hoehe"]} if cov else None,
            "dr": dyn,
            "tagverdacht": verdacht,
            "tracks": tracks,
            "werke": werke_gruppieren(tracks),
            "aktueller_pfad": aktueller_pfad,
            # Fuer die Merkliste: genau die URL, die der Player bekommen hat,
            # und seine Originalmetadaten. Damit laesst sich ein Titel spaeter
            # wieder einreihen, ohne die Kodierung von MinimServer nachzubauen.
            "res_url": uri if (uri or "").startswith("http") else None,
            "didl_roh": didl.get("_roh") if isinstance(didl, dict) else None,
            "extern": {
                "wikipedia": wiki,
                "lastfm": lfm,
                "discogs": extern.get("discogs"),
                "musicbrainz": {k: v for k, v in mb.items() if not k.startswith("_")} or None,
            },
        }

    @staticmethod
    def _qualitaet(details, dateiinfo):
        d = details or {}
        bits = d.get("bits") or (dateiinfo or {}).get("bits")
        sr = d.get("samplerate") or (dateiinfo or {}).get("samplerate")
        if not (bits and sr):
            return ""
        text = "%d bit / %.1f kHz" % (bits, sr / 1000.0)
        if d.get("codec"):
            text += " · %s" % d["codec"]
        if d.get("lossless"):
            text += " · verlustfrei"
        return text
