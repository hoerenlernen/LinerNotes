# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Hörhinweise aus dem Repo „Hören lernen" lesen und zuordnen.

Grundsatz aus dem Auftrag: Die Texte bleiben im Repo, Liner liest sie
nur. Nichts davon geht in die Musik-Tags - sie werden weiterentwickelt
und stehen unter CC BY-NC-SA 4.0, die Namensnennung verlangt. Deshalb
erscheint bei jeder Anzeige die Quelle.

Aufbau einer Datei: YAML-Kopf mit Identität, Besetzung, Aufnahmen und
Hinweisen; darunter Markdown als Hintergrundtext. Beschrieben in
doku/hoerhinweise-format.md.

Das schwierigste Stück ist die Zeitumrechnung zwischen Einspielungen.
Gemessen an der eigenen Bibliothek: Furtwänglers erster Satz der
Neunten dauert 17,8 Minuten, Szells 15,6 - und Furtwängler zerlegt das
Finale in acht Tracks, Szell in einen. Ein fester Versatz reicht dafür
nicht, eine lineare Dehnung ist eine Krücke, und wirklich stimmen tun
nur von Hand gesetzte Zeiten je Aufnahme.
"""

import logging
import os
import re
import unicodedata

log = logging.getLogger("liner.hoerenlernen")

try:
    import yaml
except ImportError:                                   # pragma: no cover
    yaml = None

try:
    import markdown as md_modul
except ImportError:                                   # pragma: no cover
    md_modul = None


def normalisieren(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " und ")
    return re.sub(r"[^a-z0-9]", "", s)


def sekunden(wert):
    """„2:30" oder „2:30-2:45" oder eine Zahl -> Sekunden (Anfang)."""
    if wert is None:
        return None
    if isinstance(wert, (int, float)):
        return int(wert)
    m = re.match(r"\s*(\d{1,3}):(\d{2})", str(wert))
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return int(str(wert).strip())
    except ValueError:
        return None


class Eintrag:
    """Eine Datei: ein Album oder ein Werk."""

    def __init__(self, daten, koerper, datei):
        self.datei = datei
        self.artist = daten.get("artist") or ""
        self.album = daten.get("album") or ""
        self.werk = daten.get("werk") or ""
        self.genre = daten.get("genre") or ""
        self.mbid = daten.get("mbid") or ""
        self.quelle = daten.get("quelle") or ""
        self.besetzung = daten.get("besetzung") or {}
        self.aufnahmen = daten.get("aufnahmen") or []
        self.worauf_achten = [str(x) for x in (daten.get("worauf_achten") or [])]
        self.hinweise = []
        for i, h in enumerate(daten.get("hinweise") or []):
            if not isinstance(h, dict) or not h.get("text"):
                continue
            self.hinweise.append({
                "id": h.get("id") or "h%d" % (i + 1),
                "track": h.get("track") or "",
                "nr": h.get("nr"),
                "bis": h.get("bis"),
                "zeit_s": sekunden(h.get("zeit")),
                "dauer": int(h.get("dauer") or 12),
                "typ": h.get("typ") or "",
                "instrumente": [str(x) for x in (h.get("instrumente") or [])],
                "text": str(h["text"]),
            })
        # Medley-Zeiten gelten ab dem ersten Track. Explizite Grenzen
        # vermeiden Annahmen ueber die Aufteilung einer Ausgabe. Die
        # kanonischen Hinweise gehen identisch an Web und tvOS.
        grenzen = daten.get("medley_grenzen") or {}
        for h in self.hinweise:
            nr, bis, zeit = h["nr"], h["bis"], h["zeit_s"]
            if not isinstance(nr, int) or not isinstance(bis, int) or bis <= nr:
                continue
            teil = grenzen.get(nr, grenzen.get(str(nr))) or {}
            starts = [(nr, 0)]
            for ziel in range(nr + 1, bis + 1):
                start = sekunden(teil.get(ziel, teil.get(str(ziel))))
                if start is None or start <= starts[-1][1]:
                    raise ValueError("Medley %s-%s: Trackgrenze fuer %s fehlt oder ist ungueltig"
                                     % (nr, bis, ziel))
                starts.append((ziel, start))
            if zeit is not None:
                for ziel, start in reversed(starts):
                    if zeit >= start:
                        h["nr"], h["zeit_s"] = ziel, zeit - start
                        break
            h["bis"] = None
        self.hinweise.sort(key=lambda h: (h.get("nr") or 0, h.get("zeit_s") or 0))
        self.koerper = koerper or ""

    # -- Zuordnung ---------------------------------------------------------

    def schluessel(self):
        """Alle Schreibweisen, unter denen dieser Eintrag zu finden ist."""
        aus = set()
        if self.mbid:
            aus.add(("mbid", self.mbid.lower()))
        if self.artist and self.album:
            aus.add(("kuenstler_album",
                     normalisieren(self.artist) + "|" + normalisieren(self.album)))
        for a in self.aufnahmen:
            if not isinstance(a, dict):
                continue
            if a.get("mbid"):
                aus.add(("mbid", str(a["mbid"]).lower()))
            if a.get("artist") and a.get("album"):
                aus.add(("kuenstler_album",
                         normalisieren(a["artist"]) + "|" + normalisieren(a["album"])))
        if self.werk:
            aus.add(("werk", normalisieren(self.werk)))
        return aus

    def aufnahme_fuer(self, artist, album, mbid=None):
        """Welche der Einspielungen liegt gerade vor?"""
        for a in self.aufnahmen:
            if not isinstance(a, dict):
                continue
            if mbid and a.get("mbid") and str(a["mbid"]).lower() == str(mbid).lower():
                return a
            if a.get("album") and normalisieren(a["album"]) == normalisieren(album):
                return a
            if (a.get("artist") and a.get("album")
                    and normalisieren(a["artist"]) in normalisieren(artist or "")
                    and normalisieren(album or "").startswith(
                        normalisieren(a["album"])[:12])):
                return a
        return None

    # -- Zeiten ------------------------------------------------------------

    def zeit_in(self, hinweis, aufnahme):
        """Zeitpunkt dieses Hinweises in DIESER Einspielung, in Sekunden.

        Reihenfolge: von Hand gesetzte Zeit (stimmt), sonst Dehnung
        (Krücke), sonst Versatz (nur bei gleichem Tempo brauchbar),
        sonst die Zeit aus dem Kopf.
        """
        grund = self.hinweise and hinweis.get("zeit_s")
        if grund is None:
            return None, "ohne"
        if not aufnahme:
            return hinweis["zeit_s"], "unverändert"

        zeiten = aufnahme.get("zeiten") or {}
        eigen = zeiten.get(hinweis["id"])
        if eigen is not None:
            s = sekunden(eigen)
            if s is not None:
                return s, "gemessen"

        s = hinweis["zeit_s"]
        dehnung = aufnahme.get("dehnung")
        if dehnung:
            try:
                s = s * float(dehnung)
            except (TypeError, ValueError):
                pass
        versatz = aufnahme.get("versatz")
        if versatz:
            try:
                s = s + float(versatz)
            except (TypeError, ValueError):
                pass
        art = "gerechnet" if (dehnung or versatz) else "unverändert"
        return max(0, int(round(s))), art

    def tracknummer_in(self, hinweis, aufnahme):
        """Track dieses Hinweises in dieser Einspielung.

        Furtwängler legt das Finale auf acht Tracks, Szell auf einen -
        deshalb darf eine Aufnahme die Zuordnung überschreiben.
        """
        nr = hinweis.get("nr")
        if not aufnahme:
            return nr
        saetze = aufnahme.get("saetze") or {}
        for satz, tracks in saetze.items():
            if not isinstance(tracks, (list, tuple)) or not tracks:
                continue
            # Satzbezeichnung im Tracktitel des Hinweises suchen
            if re.search(r"\b%s\b" % re.escape(str(satz)),
                         hinweis.get("track") or "", re.I):
                return tracks[0]
        return nr

    def hinweise_fuer_track(self, nr, aufnahme=None):
        aus = []
        for h in self.hinweise:
            if self.tracknummer_in(h, aufnahme) != nr:
                continue
            zeit, art = self.zeit_in(h, aufnahme)
            e = dict(h)
            e["zeit_s"] = zeit
            e["zeitart"] = art
            aus.append(e)
        aus.sort(key=lambda h: h.get("zeit_s") or 0)
        return aus

    def aktueller_hinweis(self, nr, position_s, aufnahme=None, vorlauf=2):
        """Welcher Hinweis gehört zu dieser Sekunde?

        `vorlauf` blendet ihn kurz VOR der Stelle ein - wer erst liest,
        wenn die Stelle schon läuft, hat sie verpasst.
        """
        passend = None
        for h in self.hinweise_fuer_track(nr, aufnahme):
            if h["zeit_s"] is None:
                continue
            beginn = h["zeit_s"] - vorlauf
            ende = h["zeit_s"] + h["dauer"]
            if beginn <= position_s < ende:
                passend = dict(h)
                passend["rest_s"] = max(0, round(ende - position_s))
                passend["anteil"] = min(1.0, max(0.0,
                    (position_s - beginn) / float(h["dauer"] + vorlauf)))
        return passend

    # -- Darstellung -------------------------------------------------------

    def abschnitte(self):
        """Der Markdown-Teil, in aufklappbare Abschnitte zerlegt."""
        if not self.koerper.strip():
            return []
        teile = re.split(r"^##\s+(.+?)\s*$", self.koerper, flags=re.M)
        aus = []
        # teile[0] ist alles vor der ersten Überschrift
        vorspann = teile[0].strip()
        if vorspann:
            aus.append({"titel": "Zum Album", "html": self._html(vorspann)})
        for i in range(1, len(teile) - 1, 2):
            aus.append({"titel": teile[i].strip(),
                        "html": self._html(teile[i + 1])})
        return aus

    @staticmethod
    def _html(text):
        if md_modul:
            return md_modul.markdown(text.strip(), extensions=["tables"])
        return "<p>%s</p>" % text.strip().replace("\n\n", "</p><p>")

    def instrumentenliste(self):
        """Alle Instrumente der Besetzung, gruppenweise."""
        gruppen = (self.besetzung or {}).get("gruppen") or []
        aus = []
        for g in gruppen:
            if not isinstance(g, dict):
                continue
            aus.append({
                "name": g.get("name") or "",
                "farbe": g.get("farbe") or "",
                "instrumente": [str(x) for x in (g.get("instrumente") or [])],
            })
        return aus

    def kurz(self):
        return {
            "artist": self.artist, "album": self.album, "werk": self.werk,
            "genre": self.genre, "datei": os.path.basename(self.datei),
            "quelle": self.quelle,
            "hinweise": len(self.hinweise),
            "worauf_achten": len(self.worauf_achten),
            "aufnahmen": [a.get("kennung") for a in self.aufnahmen
                          if isinstance(a, dict)],
        }


class Sammlung:
    """Alle Dateien des Repos, mit Index."""

    def __init__(self, wurzel=None, eigene=None):
        # Kein Vorgabepfad: Wo das Repo liegt, weiss nur die Umgebung.
        # Fehlt HOERENLERNEN_PFAD, bleibt die Funktion einfach aus - das
        # ist richtiger als ein geratener Pfad, der bei jedem anders ist.
        self.wurzel = wurzel or os.environ.get("HOERENLERNEN_PFAD", "")
        # Zweiter Ort fuer noch nicht committete Dateien. Dadurch laesst
        # sich eine Datei ausprobieren, bevor sie ins Repo geht - und das
        # Repo bleibt unberuehrt, so wie beauftragt. Bei gleichem Album
        # gewinnt die eigene Datei.
        self.eigene = eigene or os.environ.get("HOERENLERNEN_EIGENE", "")
        self.eintraege = []
        self.nach_mbid = {}
        self.nach_kuenstler_album = {}
        self.nach_werk = {}
        self.gelesen_am = 0
        self.fehler = []

    def einlesen(self):
        import time
        self.eintraege, self.fehler = [], []
        self.nach_mbid, self.nach_kuenstler_album, self.nach_werk = {}, {}, {}
        if not os.path.isdir(self.wurzel) and not (
                self.eigene and os.path.isdir(self.eigene)):
            self.fehler.append("Kein Verzeichnis gefunden: %s" % self.wurzel)
            return 0
        if yaml is None:
            self.fehler.append("pyyaml fehlt - Hörhinweise werden nicht gelesen")
            return 0

        orte = [o for o in (self.eigene, self.wurzel) if o and os.path.isdir(o)]
        for ort in orte:
          for verzeichnis, _, dateien in os.walk(ort):
            if ".git" in verzeichnis:
                continue
            for name in sorted(dateien):
                if not name.endswith("_Hoeren.md"):
                    continue
                pfad = os.path.join(verzeichnis, name)
                try:
                    eintrag = self._lesen(pfad)
                except Exception as e:
                    self.fehler.append("%s: %s" % (name, e))
                    log.warning("Hörhinweise %s nicht lesbar: %s", name, e)
                    continue
                if eintrag:
                    self.eintraege.append(eintrag)
                    for art, wert in eintrag.schluessel():
                        ziel = {"mbid": self.nach_mbid,
                                "kuenstler_album": self.nach_kuenstler_album,
                                "werk": self.nach_werk}[art]
                        ziel.setdefault(wert, eintrag)
        self.gelesen_am = time.time()
        log.info("Hörhinweise: %d Dateien, %d Hinweise gesamt",
                 len(self.eintraege), sum(len(e.hinweise) for e in self.eintraege))
        return len(self.eintraege)

    def _lesen(self, pfad):
        roh = open(pfad, encoding="utf-8", errors="replace").read()
        if not roh.lstrip().startswith("---"):
            return None
        teile = roh.split("---", 2)
        if len(teile) < 3:
            return None
        daten = yaml.safe_load(teile[1]) or {}
        if not isinstance(daten, dict):
            return None
        return Eintrag(daten, teile[2], pfad)

    # -- Suche -------------------------------------------------------------

    def finden(self, mbid=None, artist=None, album=None, werk=None):
        """Rückgabe: (Eintrag, wie zugeordnet) oder (None, None)."""
        if mbid:
            e = self.nach_mbid.get(str(mbid).lower())
            if e:
                return e, "MusicBrainz-ID"
        if artist and album:
            s = normalisieren(artist) + "|" + normalisieren(album)
            e = self.nach_kuenstler_album.get(s)
            if e:
                return e, "Künstler und Album"
            # Nachsicht: Ausgabentitel sind oft länger („… (Deluxe)")
            na, nb = normalisieren(artist), normalisieren(album)
            for schluessel, eintrag in self.nach_kuenstler_album.items():
                ka, kb = schluessel.split("|", 1)
                if not ka or not kb:
                    continue
                if (ka in na or na in ka) and (kb in nb or nb in kb):
                    return eintrag, "Künstler und Album (ungefähr)"
        if werk:
            nw = normalisieren(werk)
            e = self.nach_werk.get(nw)
            if e:
                return e, "Werk"
            for schluessel, eintrag in self.nach_werk.items():
                if schluessel and (schluessel in nw or nw in schluessel):
                    return eintrag, "Werk (ungefähr)"
        return None, None

    def stand(self):
        return {
            "wurzel": self.wurzel,
            "eigene": self.eigene,
            "dateien": len(self.eintraege),
            "hinweise": sum(len(e.hinweise) for e in self.eintraege),
            "gelesen_am": self.gelesen_am,
            "fehler": self.fehler,
            "alben": [e.kurz() for e in self.eintraege],
        }
