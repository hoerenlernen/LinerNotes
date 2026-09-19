# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
bibliothek.py – Auflösung von Bibliothekseinträgen über MinimServer.

WOFÜR
=====
Um einen Titel in die Warteschlange des Linn zu legen, braucht man die
**res-URL**, die MinimServer für ihn ausgibt – nicht den Dateipfad. Diese
URL selbst zu bauen wäre ein Fehler: sie kodiert einzelne UTF-8-Bytes als
`*XX` und lässt die Dateiendung weg. Genau diese Annahme war beim Lesen
schon einmal falsch (Kapitel 14.1). Darum wird die URL **erfragt**, beim
Server, der sie vergibt.

Gefunden per SSDP: `server:9791`, samt UPnP-Kennung. Die Medien-URLs
verweisen dagegen auf Port 9790 – zwei verschiedene Ports, nicht
verwechseln.

WAS DER SERVER KANN
===================
`GetSearchCapabilities` meldet: `upnp:class`, `dc:title`, `dc:creator`,
`upnp:artist`, `upnp:album`, `upnp:genre`, `dc:date`, `res`. Damit lässt
sich nach Titel, Album und Interpret suchen.

Eine Suche nach `object.container.album` liefert **nichts** – Container
sind nicht durchsuchbar. Albumtitel werden darum über die *Items* geholt
(`upnp:album = "…"`) und hier gruppiert.

NUR LESEND
==========
Dieses Modul stellt ausschließlich Suchanfragen. Es schreibt weder in die
Bibliothek noch in Dateien.
"""
import html
import logging
import re
import threading
import urllib.request

log = logging.getLogger("liner.bibliothek")

NS = "urn:schemas-upnp-org:service:ContentDirectory:1"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _feld(xml, name):
    m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (name, name), xml, re.S)
    return html.unescape(m.group(1)).strip() if m else None


class Bibliothek:
    def __init__(self, basis=None,
                 udn=None, timeout=20):
        self.basis = (basis or "").rstrip("/")
        self.udn = udn
        self.timeout = timeout
        # MinimServer ist ein Java-Prozess auf demselben Rechner; parallele
        # Suchanfragen bringen nichts und koennen sich gegenseitig bremsen.
        self._sperre = threading.Lock()

    def _control_url(self):
        return "%s/%s/upnp.org-ContentDirectory-1/control" % (self.basis, self.udn)

    def _soap(self, aktion, args):
        if not self.basis or not self.udn:
            raise RuntimeError("Kein Medienserver konfiguriert oder gefunden")
        arg_xml = "".join("<%s>%s</%s>" % (k, _esc(v), k) for k, v in args.items())
        koerper = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            '<s:Body><u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>'
            % (aktion, NS, arg_xml, aktion)).encode("utf-8")
        req = urllib.request.Request(self._control_url(), data=koerper, headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": '"%s#%s"' % (NS, aktion)})
        with self._sperre:
            with urllib.request.urlopen(req, timeout=self.timeout) as f:
                return f.read().decode("utf-8", "replace")

    @staticmethod
    def _didl_und_zahl(antwort):
        """Das DIDL steckt maskiert im <Result>-Feld."""
        m = re.search(r"<Result>(.*?)</Result>", antwort, re.S)
        didl = html.unescape(m.group(1)) if m else ""
        tm = re.search(r"<TotalMatches>(\d+)</TotalMatches>", antwort)
        return didl, int(tm.group(1)) if tm else 0

    @staticmethod
    def _items_lesen(didl):
        """Zerlegt die Items. Das vollständige Item-XML wird mitgegeben:
        der Linn bekommt es als Metadata unverändert zurück."""
        aus = []
        for roh in re.findall(r"<item\b.*?</item>", didl, re.S):
            res = re.search(r"<res\b[^>]*>(.*?)</res>", roh, re.S)
            url = html.unescape(res.group(1)).strip() if res else None
            if not url or not url.startswith("http"):
                continue
            dauer = None
            d = re.search(r'duration="([^"]+)"', roh)
            if d:
                try:
                    teile = [float(x) for x in d.group(1).split(":")]
                    dauer = teile[0] * 3600 + teile[1] * 60 + teile[2]
                except Exception:
                    pass
            nr = _feld(roh, "upnp:originalTrackNumber")
            aus.append({
                "titel": _feld(roh, "dc:title"),
                "album": _feld(roh, "upnp:album"),
                "artist": _feld(roh, "upnp:artist") or _feld(roh, "dc:creator"),
                "nr": int(nr) if (nr or "").isdigit() else None,
                "dauer_s": dauer,
                "res_url": url,
                # Ein vollständiges DIDL-Dokument um dieses eine Item herum -
                # so erwartet es Playlist/Insert.
                "didl": ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                         'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                         'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                         + roh + "</DIDL-Lite>"),
            })
        return aus

    # ------------------------------------------------------------------
    def suche(self, text, grenze=25):
        """Freie Suche über Titel, Album und Interpret.

        Gibt Titel zurück, nach Album gruppiert – ohne Kacheln, ohne
        Hierarchien. Genau das, was für einen schlanken Einstieg reicht."""
        text = (text or "").strip()
        if len(text) < 2:
            return {"treffer": [], "gesamt": 0}
        t = text.replace('"', "")
        kriterium = ('upnp:class derivedfrom "object.item.audioItem" and '
                     '(dc:title contains "%s" or upnp:album contains "%s" '
                     'or upnp:artist contains "%s")' % (t, t, t))
        # Reichlich holen und selbst sortieren. MinimServer liefert
        # alphabetisch, nicht nach Relevanz: eine Suche nach "Graham Central
        # Station" brachte so vier beliebige Alben und gerade NICHT das
        # gleichnamige. Darum ein grosszuegiges Fenster und eine eigene
        # Wertung.
        try:
            antwort = self._soap("Search", {
                "ContainerID": "0", "SearchCriteria": kriterium, "Filter": "*",
                "StartingIndex": "0", "RequestedCount": "400",
                "SortCriteria": ""})
        except Exception as e:
            log.warning("Suche fehlgeschlagen: %s", e)
            return {"treffer": [], "gesamt": 0, "fehler": str(e)}
        didl, gesamt = self._didl_und_zahl(antwort)
        items = self._items_lesen(didl)

        # Nach Album gruppieren
        gruppen = {}
        for it in items:
            schluessel = (it["album"] or "", it["artist"] or "")
            g = gruppen.setdefault(schluessel, {
                "album": it["album"], "artist": it["artist"], "titel": []})
            g["titel"].append(it)

        klein = t.lower()

        # Wortgrenzen sind entscheidend: die Suche nach "Hair" lieferte
        # sonst "Rockin' Chair" und "Big Chair" ganz oben, weil "chair" den
        # Suchtext als Teilstring enthaelt - der gesuchte Titel "Hair" kam
        # gar nicht vor. Ein Treffer an einer Wortgrenze wiegt darum weit
        # mehr als einer mitten im Wort.
        wortmuster = re.compile(r"\b" + re.escape(klein), re.I)

        def _stufe(feld, hoch, mittel, niedrig):
            """hoch = exakt, mittel = an der Wortgrenze, niedrig = irgendwo."""
            if not feld:
                return 0
            f = feld.lower()
            if f == klein:
                return hoch
            if wortmuster.search(f):
                return mittel
            if klein in f:
                return niedrig
            return 0

        def wertung(g):
            """Hoeher ist besser. Was der Mensch sucht, steht meist im
            Albumtitel oder im Kuenstlernamen; ein Treffer nur in einem
            Songtitel ist das schwaechste Signal - es sei denn, der Titel
            stimmt genau."""
            p = _stufe(g["album"], 100, 55, 8)
            p += _stufe(g["artist"], 70, 40, 6)
            # Der beste Titeltreffer der Gruppe zaehlt mit
            p += max((_stufe(x["titel"], 45, 22, 2) for x in g["titel"]),
                     default=0)
            # Ein Album, von dem viele Titel passen, ist wahrscheinlich
            # gemeint; mehr als ein paar Treffer bringen aber nichts mehr.
            p += min(len(g["titel"]), 5)
            return p

        treffer = sorted(gruppen.values(), key=wertung, reverse=True)
        for g in treffer:
            g["titel"].sort(key=lambda x: (x["nr"] is None, x["nr"] or 0,
                                           x["titel"] or ""))
            g["wertung"] = wertung(g)
        return {"treffer": treffer[:grenze], "gesamt": gesamt}

    def album_titel(self, album, artist=None):
        """Alle Titel eines Albums, in Trackreihenfolge.

        Der Albumname wird exakt verglichen (`=`), nicht `contains` - sonst
        liefert "Graham Central Station" auch die Titel von "Graham Central
        Station Live". Der Interpret grenzt Namensdubletten weiter ein."""
        if not album:
            return []
        kriterium = ('upnp:class derivedfrom "object.item.audioItem" '
                     'and upnp:album = "%s"' % album.replace('"', ""))
        if artist:
            kriterium += ' and upnp:artist contains "%s"' % artist.replace('"', "")
        try:
            antwort = self._soap("Search", {
                "ContainerID": "0", "SearchCriteria": kriterium, "Filter": "*",
                "StartingIndex": "0", "RequestedCount": "200",
                "SortCriteria": ""})
        except Exception as e:
            log.warning("Albumauflösung fehlgeschlagen: %s", e)
            return []
        didl, _ = self._didl_und_zahl(antwort)
        items = self._items_lesen(didl)
        # MinimServer beachtet SortCriteria hier nicht - selbst sortieren.
        items.sort(key=lambda x: (x["nr"] is None, x["nr"] or 0, x["titel"] or ""))
        return items

    def titel_finden(self, titel, album=None, artist=None):
        """Einen bestimmten Titel auflösen - für Merklisteneinträge, die
        keine res-URL haben (gemerkt über Roon oder die Trackliste)."""
        if not titel:
            return None
        if album:
            for it in self.album_titel(album, artist):
                if (it["titel"] or "").lower() == titel.lower():
                    return it
        kriterium = ('upnp:class derivedfrom "object.item.audioItem" '
                     'and dc:title = "%s"' % titel.replace('"', ""))
        if artist:
            kriterium += ' and upnp:artist contains "%s"' % artist.replace('"', "")
        try:
            antwort = self._soap("Search", {
                "ContainerID": "0", "SearchCriteria": kriterium, "Filter": "*",
                "StartingIndex": "0", "RequestedCount": "10", "SortCriteria": ""})
        except Exception as e:
            log.warning("Titelauflösung fehlgeschlagen: %s", e)
            return None
        didl, _ = self._didl_und_zahl(antwort)
        items = self._items_lesen(didl)
        if album:
            for it in items:
                if (it["album"] or "").lower() == album.lower():
                    return it
        return items[0] if items else None

    def erreichbar(self):
        try:
            self._soap("GetSearchCapabilities", {})
            return True
        except Exception:
            return False
