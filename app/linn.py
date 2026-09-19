# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
linn.py – Kommunikation mit dem Linn Majik DS-I über OpenHome.

WIE DER LINN SEINE METADATEN HERGIBT
====================================
Der Player bietet zwei Wege. Wir nutzen den Dienst `Info`, weil er beides
liefert: die res-URL der spielenden Datei UND die vollständigen DIDL-Lite-
Metadaten, die MinimServer mitgeschickt hat.

  Info/Track    -> Uri + Metadata (DIDL-Lite)
  Info/Details  -> Duration, BitRate, BitDepth, SampleRate, Lossless, Codec
  Info/Metatext -> nur bei Radio belegt, bei Dateien leer

Statt zu pollen abonnieren wir den Dienst (UPnP SUBSCRIBE). Der Linn schickt
dann bei jeder Änderung ein NOTIFY an unsere Callback-Adresse; der Zähler
`TrackCount` erhöht sich bei jedem Titelwechsel und dient als Auslöser. Das
Abo läuft nach 300 s ab und wird vorher erneuert.

Verifiziert am 2026-09-17 gegen "„Wohnzimmer"" (Majik DS-I):
SUBSCRIBE liefert eine SID, das initiale NOTIFY kommt binnen Sekunden.

Quelle spielt keine Rolle, solange über MinimServer abgespielt wird — also
über die OpenHome-Playlist (bei Goran umbenannt zu "Radio"). Roon läuft über
eine eigene Quelle und ist hier bewusst nicht abgedeckt.
"""
import html
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("liner.linn")

SOAP_HUELLE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body><u:{aktion} xmlns:u=\"{ns}\"/></s:Body></s:Envelope>"
)

# Mit Argumenten: OpenHome erwartet die Parameter als Kindelemente in der
# Reihenfolge des SCPD.
SOAP_HUELLE_ARG = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:{aktion} xmlns:u="{ns}">{argumente}</u:{aktion}>'
    "</s:Body></s:Envelope>"
)


def _xml_maskieren(wert):
    """Argumentwerte maskieren. Wichtig bei Metadaten (DIDL-Lite), die
    selbst XML sind und darum vollstaendig escaped uebergeben werden."""
    return (str(wert).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


class Linn:
    def __init__(self, host, port, udn, timeout=15):
        self.host, self.port, self.udn = host, int(port), udn
        self.timeout = timeout
        self.basis = "http://%s:%d" % (self.host, self.port)
        self._quellen = None        # SourceXml wird einmal geholt und behalten
        self._letzte_quelle = None  # ueberbrueckt Aussetzer des Geraets
        self._sperre = threading.Lock()  # keine parallelen Anfragen an den Linn

    # ----- SOAP -----------------------------------------------------------
    def _control_url(self, dienst, version):
        return "%s/%s/av.openhome.org-%s-%s/control" % (self.basis, self.udn, dienst, version)

    def event_url(self, dienst, version):
        return "%s/%s/av.openhome.org-%s-%s/event" % (self.basis, self.udn, dienst, version)

    def aufruf(self, dienst, version, aktion, argumente=None, timeout=None):
        """Ein SOAP-Aufruf, serialisiert.

        Die Sperre stellt sicher, dass nie zwei Anfragen gleichzeitig beim
        Linn ankommen. Er ist ein eingebettetes Geraet und beantwortet
        parallele Aufrufe waehrend der Wiedergabe unzuverlaessig - beobachtet
        als Zeitueberschreitung bei laufendem AirPlay.

        `argumente` ist ein dict in SCPD-Reihenfolge (Python haelt die
        Einfuegereihenfolge). `timeout` uebersteuert den Standardwert -
        Steuerbefehle warten kuerzer als Abfragen, damit die Bedienung nie
        haengt."""
        ns = "urn:av-openhome-org:service:%s:%s" % (dienst, version)
        if argumente:
            arg_xml = "".join("<%s>%s</%s>" % (k, _xml_maskieren(v), k)
                              for k, v in argumente.items())
            koerper = SOAP_HUELLE_ARG.format(
                aktion=aktion, ns=ns, argumente=arg_xml).encode("utf-8")
        else:
            koerper = SOAP_HUELLE.format(aktion=aktion, ns=ns).encode("utf-8")
        req = urllib.request.Request(
            self._control_url(dienst, version), data=koerper,
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPACTION": '"%s#%s"' % (ns, aktion)})
        with self._sperre:
            with urllib.request.urlopen(
                    req, timeout=timeout or self.timeout) as f:
                rohdaten = f.read().decode("utf-8", "replace")
        return self._soap_felder(rohdaten)

    # ---------------------------------------------------------------- 2b
    # Steuerung. Grundsatz: der Transport-Dienst zuerst, weil er
    # quellenuebergreifend arbeitet (Playlist, Radio, UpnpAv, RAOP). Nur
    # wenn er den Befehl nicht kennt, wird auf Playlist zurueckgefallen.
    #
    # Der Timeout ist bewusst knapp: ein Bedienknopf, der zehn Sekunden
    # haengt, ist schlimmer als eine Fehlermeldung.
    STEUER_TIMEOUT = 5

    def steuern(self, aktion, argumente=None):
        """Transport-Befehl mit Rueckfall auf Playlist.

        Gibt (True, None) zurueck oder (False, Grund). Wirft nicht -
        Bedienfehler duerfen die Anzeige nie zum Stehen bringen."""
        versuche = [("Transport", 1)]
        if aktion in ("Play", "Pause", "Stop"):
            versuche.append(("Playlist", 1))
        elif aktion == "SkipNext":
            versuche.append(("Playlist", 1, "Next"))
        elif aktion == "SkipPrevious":
            versuche.append(("Playlist", 1, "Previous"))
        letzter = None
        for eintrag in versuche:
            dienst, version = eintrag[0], eintrag[1]
            name = eintrag[2] if len(eintrag) > 2 else aktion
            try:
                self.aufruf(dienst, version, name, argumente,
                            timeout=self.STEUER_TIMEOUT)
                log.info("Steuerbefehl %s/%s ausgefuehrt", dienst, name)
                return True, None
            except Exception as e:
                letzter = "%s/%s: %s" % (dienst, name, e)
                log.warning("Steuerbefehl fehlgeschlagen - %s", letzter)
        return False, letzter

    def warteschlange(self):
        """Liest die Warteschlange als Liste von (Id, res-URL).

        `IdArray` liefert die Ids base64-kodiert als Folge von 32-Bit-Zahlen
        in Netzwerk-Byteordnung. `ReadList` liefert dann zu einer Id-Liste
        die zugehoerigen Uris - beides in einem Aufruf, nicht Titel fuer
        Titel: bei 68 Eintraegen waeren das 68 SOAP-Runden.

        Gibt [] zurueck, wenn die aktuelle Quelle keine Warteschlange hat
        (Radio, AirPlay, Analogeingang)."""
        import base64
        import struct
        try:
            r = self.aufruf("Playlist", 1, "IdArray",
                            timeout=self.STEUER_TIMEOUT)
        except Exception as e:
            log.debug("IdArray nicht lesbar: %s", e)
            return []
        roh = base64.b64decode(r.get("Array") or "")
        if not roh or len(roh) % 4:
            return []
        ids = list(struct.unpack(">%dI" % (len(roh) // 4), roh))
        if not ids:
            return []
        aus = []
        # In Blöcken lesen: ein einzelnes ReadList mit 1000 Ids waere eine
        # sehr grosse SOAP-Antwort.
        for start in range(0, len(ids), 50):
            teil = ids[start:start + 50]
            try:
                antwort = self.aufruf(
                    "Playlist", 1, "ReadList",
                    {"IdList": " ".join(str(i) for i in teil)},
                    timeout=self.STEUER_TIMEOUT)
            except Exception as e:
                log.warning("ReadList fehlgeschlagen: %s", e)
                break
            liste = antwort.get("TrackList") or ""
            # Die Antwort ist XML: <Entry><Id>..</Id><Uri>..</Uri>...
            import html as _html
            text = _html.unescape(liste)
            for m in re.finditer(r"<Entry>(.*?)</Entry>", text, re.S):
                block = m.group(1)
                mid = re.search(r"<Id>(\d+)</Id>", block)
                muri = re.search(r"<Uri>(.*?)</Uri>", block, re.S)
                if mid and muri:
                    aus.append({"id": int(mid.group(1)),
                                "uri": _html.unescape(muri.group(1)).strip()})
        return aus

    def zu_id_springen(self, playlist_id):
        """Springt in der Warteschlange zu einer Id.

        SeekId statt SeekIndex: Ids bleiben stabil, wenn sich die
        Warteschlange aendert - ein Index zeigt dann auf den falschen
        Titel."""
        try:
            self.aufruf("Playlist", 1, "SeekId", {"Value": int(playlist_id)},
                        timeout=self.STEUER_TIMEOUT)
            return True, None
        except Exception as e:
            log.warning("SeekId fehlgeschlagen: %s", e)
            return False, str(e)

    def stream_info(self):
        """StreamId, CanSeek, CanPause - entscheidet, was bedienbar ist."""
        try:
            r = self.aufruf("Transport", 1, "StreamInfo",
                            timeout=self.STEUER_TIMEOUT)
            return {"stream_id": r.get("StreamId"),
                    "kann_springen": r.get("CanSeek") == "1",
                    "kann_pausieren": r.get("CanPause") == "1"}
        except Exception as e:
            log.warning("StreamInfo nicht lesbar: %s", e)
            return {}

    def modus_info(self):
        """Ob Titelsprung ueberhaupt moeglich ist (bei Analogquellen nicht)."""
        try:
            r = self.aufruf("Transport", 1, "ModeInfo",
                            timeout=self.STEUER_TIMEOUT)
            return {"weiter": r.get("CanSkipNext") == "1",
                    "zurueck": r.get("CanSkipPrevious") == "1"}
        except Exception as e:
            log.warning("ModeInfo nicht lesbar: %s", e)
            return {}

    def springen(self, sekunde):
        """Absolut in der Zeit springen. Transport braucht die StreamId -
        ohne sie trifft der Sprung im schlimmsten Fall den falschen Titel,
        wenn zwischenzeitlich gewechselt wurde."""
        info = self.stream_info()
        if not info.get("kann_springen"):
            return False, "Diese Quelle erlaubt kein Springen."
        sid = info.get("stream_id")
        if sid:
            ok, grund = self.steuern("SeekSecondAbsolute",
                                     {"StreamId": sid,
                                      "SecondAbsolute": int(sekunde)})
            if ok:
                return True, None
        try:
            self.aufruf("Playlist", 1, "SeekSecondAbsolute",
                        {"Value": int(sekunde)}, timeout=self.STEUER_TIMEOUT)
            return True, None
        except Exception as e:
            return False, str(e)

    def lautstaerke_lesen(self):
        """Aktueller Wert, Stummschaltung und die Grenzen des Geraets."""
        aus = {}
        try:
            aus["wert"] = int(self.aufruf(
                "Volume", 4, "Volume", timeout=self.STEUER_TIMEOUT).get("Value", 0))
            aus["stumm"] = self.aufruf(
                "Volume", 4, "Mute", timeout=self.STEUER_TIMEOUT).get("Value") == "1"
            aus["geraetelimit"] = int(self.aufruf(
                "Volume", 4, "VolumeLimit", timeout=self.STEUER_TIMEOUT).get("Value", 100))
        except Exception as e:
            log.warning("Lautstaerke nicht lesbar: %s", e)
            return aus or {}
        return aus

    def lautstaerke_setzen(self, wert):
        """Absoluten Wert setzen. Die Begrenzung passiert eine Ebene
        hoeher (main.py) - hier wird nur uebergeben, was erlaubt wurde."""
        return self.steuern_volume("SetVolume", {"Value": int(wert)})

    def steuern_volume(self, aktion, argumente=None):
        """Wie steuern(), aber fuer den Volume-Dienst (kein Rueckfall)."""
        try:
            self.aufruf("Volume", 4, aktion, argumente,
                        timeout=self.STEUER_TIMEOUT)
            return True, None
        except Exception as e:
            log.warning("Volume/%s fehlgeschlagen: %s", aktion, e)
            return False, str(e)

    @staticmethod
    def _soap_felder(xml_text):
        """Zieht die Antwortfelder aus der SOAP-Hülle. Bewusst per Regex:
        die Antworten sind flach, und ElementTree stolpert über die
        wechselnden Namensräume der OpenHome-Dienste."""
        ergebnis = {}
        for name, wert in re.findall(r"<(\w+)>(.*?)</\1>", xml_text, re.S):
            if name in ("Envelope", "Body") or name.endswith("Response"):
                continue
            ergebnis[name] = wert
        return ergebnis

    # ----- Zustand abfragen ----------------------------------------------
    def transport_zustand(self):
        try:
            return self.aufruf("Transport", 1, "TransportState").get("State", "")
        except Exception as e:
            log.debug("TransportState nicht lesbar: %s", e)
            return ""

    def quelle_index(self):
        try:
            return int(self.aufruf("Product", 1, "SourceIndex").get("Value", "-1"))
        except Exception:
            return -1

    def track(self):
        """Liefert (uri, didl_dict) des laufenden Titels.

        Das rohe DIDL-XML wird unter `_roh` mitgegeben: die Merkliste
        braucht es unveraendert, um einen Titel spaeter wieder einzureihen.
        Selbst gebaute Metadaten waeren aermer als die des Servers."""
        felder = self.aufruf("Info", 1, "Track")
        uri = felder.get("Uri", "") or ""
        didl = felder.get("Metadata", "") or ""
        gelesen = self.didl_lesen(didl)
        if isinstance(gelesen, dict):
            gelesen["_roh"] = didl or None
        return uri, gelesen

    def zeit(self):
        """Fortschritt aus dem Time-Dienst.

        Der Linn liefert `Seconds`, `Duration` und `TrackCount`. Der Dienst ist
        abonnierbar, meldet aber nicht jede Sekunde - dazwischen wird in der
        Oberflaeche interpoliert. So braucht es kein Sekunden-Polling.
        Verifiziert 2026-09-17: Seconds 116, Duration 204, TrackCount 130."""
        try:
            f = self.aufruf("Time", 1, "Time")
        except Exception as e:
            log.debug("Time nicht lesbar: %s", e)
            return {}
        def zahl(x):
            try: return int(f.get(x, 0))
            except (TypeError, ValueError): return 0
        return {"position_s": zahl("Seconds"), "dauer_s": zahl("Duration"),
                "trackcount": zahl("TrackCount")}

    def quellenliste(self):
        """Die Quellenliste des Geraets, einmal geholt und behalten.

        Sie aendert sich praktisch nie - Quellen bekommt ein Linn nur bei
        einer Umkonfiguration dazu. Ein erneutes Abfragen bei jedem
        Titelwechsel ist verschwendete Last auf einem Geraet, das waehrend
        der Wiedergabe ohnehin knapp bei Kraeften ist: Am 17.09.2026 lief
        `Product/SourceIndex` bei laufendem AirPlay in eine
        Zeitueberschreitung, waehrend die uebrigen Dienste antworteten.

        Ein Fehlschlag wird NICHT gespeichert - beim naechsten Mal wird es
        erneut versucht. Nur ein Erfolg landet im Zwischenspeicher."""
        if self._quellen:
            return self._quellen
        try:
            rohdaten = self.aufruf("Product", 1, "SourceXml").get("Value", "")
        except Exception as e:
            log.debug("SourceXml nicht lesbar: %s", e)
            return []
        liste = []
        for eintrag in re.findall(r"<Source>(.*?)</Source>", html.unescape(rohdaten), re.S):
            def g(tag, e=eintrag):
                m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), e)
                return m.group(1) if m else ""
            liste.append({"typ": g("Type"), "name": g("Name")})
        if liste:
            self._quellen = liste
            log.info("Quellenliste geholt: %d Quellen", len(liste))
        return liste

    def quelle(self):
        """Aktive Quelle als {index, typ, name}.

        Die Namen sind frei vergeben - bei Goran heisst Quelle 17 vom Typ
        `Scd` "Roon". Fuer die Anzeige zaehlt der Name, fuer die Logik der Typ:
        `Playlist` und `UpnpAv` liefern Dateien aus der Bibliothek, `Scd`
        (Roon), `Radio`, `Analog` und `Digital` nicht.

        Antwortet das Geraet nicht, wird der letzte bekannte Wert
        zurueckgegeben statt eines leeren - ein Aussetzer soll die Anzeige
        nicht zuruecksetzen."""
        liste = self.quellenliste()
        try:
            idx = int(self.aufruf("Product", 1, "SourceIndex").get("Value", "-1"))
        except Exception as e:
            log.debug("SourceIndex nicht lesbar (%s) - letzter Wert bleibt", e)
            return dict(self._letzte_quelle) if self._letzte_quelle else \
                   {"index": -1, "typ": "", "name": "", "veraltet": True}
        if 0 <= idx < len(liste):
            q = liste[idx]
            ergebnis = {"index": idx, "typ": q["typ"], "name": q["name"]}
        else:
            ergebnis = {"index": idx, "typ": "", "name": ""}
        self._letzte_quelle = ergebnis
        return ergebnis

    def details(self):
        f = self.aufruf("Info", 1, "Details")
        def zahl(x, standard=0):
            try: return int(f.get(x, standard))
            except (TypeError, ValueError): return standard
        return {"dauer_s": zahl("Duration"), "bitrate": zahl("BitRate"),
                "bits": zahl("BitDepth"), "samplerate": zahl("SampleRate"),
                "lossless": f.get("Lossless") == "1", "codec": f.get("CodecName", "")}

    @staticmethod
    def didl_lesen(didl_text):
        """Zerlegt die DIDL-Lite-Metadaten, die MinimServer mitschickt."""
        if not didl_text.strip():
            return {}
        text = html.unescape(didl_text)
        try:
            baum = ET.fromstring(text)
        except ET.ParseError:
            return {}
        ns = {"dc": "http://purl.org/dc/elements/1.1/",
              "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
              "d": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"}
        item = baum.find("d:item", ns)
        if item is None:
            return {}
        def hol(pfad):
            e = item.find(pfad, ns)
            return (e.text or "").strip() if e is not None and e.text else ""
        rollen = {}
        for e in item.findall("upnp:artist", ns):
            rollen.setdefault(e.get("role") or "Performer", (e.text or "").strip())
        res = item.find("d:res", ns)
        return {
            "titel": hol("dc:title"),
            "album": hol("upnp:album"),
            "artist": rollen.get("Performer") or hol("upnp:artist"),
            "albumartist": rollen.get("AlbumArtist", ""),
            "komponist": rollen.get("Composer", ""),
            "datum": hol("dc:date"),
            "genre": hol("upnp:genre"),
            "cover_url": hol("upnp:albumArtURI"),
            "tracknummer": hol("upnp:originalTrackNumber"),
            "res_url": (res.text or "").strip() if res is not None and res.text else "",
            "samplerate": (res.get("sampleFrequency") if res is not None else "") or "",
            "bits": (res.get("bitsPerSample") if res is not None else "") or "",
        }


class EventAbo:
    """Hält ein UPnP-Abo auf Info offen und erneuert es rechtzeitig.

    Der Linn kündigt das Abo nach `TIMEOUT` Sekunden. Wir erneuern bei zwei
    Dritteln der Laufzeit; schlägt das fehl, wird neu abonniert. Ohne diese
    Erneuerung verstummt die Anzeige nach fünf Minuten – ein Fehler, der beim
    Testen leicht übersehen wird, weil am Anfang alles funktioniert."""

    def __init__(self, linn, callback_url, bei_aenderung, dienst="Info", version=1,
                 laufzeit=300):
        self.linn = linn
        self.callback_url = callback_url
        self.bei_aenderung = bei_aenderung
        self.dienst, self.version, self.laufzeit = dienst, version, laufzeit
        self.sid = None
        self._stop = threading.Event()
        self._thread = None

    def _anfrage(self, methode, kopf):
        req = urllib.request.Request(self.linn.event_url(self.dienst, self.version),
                                     method=methode)
        for k, v in kopf.items():
            req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=8)

    def abonnieren(self):
        antwort = self._anfrage("SUBSCRIBE", {
            "CALLBACK": "<%s>" % self.callback_url,
            "NT": "upnp:event",
            "TIMEOUT": "Second-%d" % self.laufzeit})
        self.sid = antwort.headers.get("SID")
        log.info("Abo aktiv, SID=%s", self.sid)
        return self.sid

    def erneuern(self):
        if not self.sid:
            return self.abonnieren()
        try:
            self._anfrage("SUBSCRIBE", {"SID": self.sid,
                                        "TIMEOUT": "Second-%d" % self.laufzeit})
            log.debug("Abo erneuert")
            return self.sid
        except Exception as e:
            log.warning("Erneuern fehlgeschlagen (%s) – neu abonnieren", e)
            self.sid = None
            return self.abonnieren()

    def abbestellen(self):
        if not self.sid:
            return
        try:
            self._anfrage("UNSUBSCRIBE", {"SID": self.sid})
            log.info("Abo beendet")
        except Exception as e:
            log.debug("Abbestellen fehlgeschlagen: %s", e)
        self.sid = None

    def _schleife(self):
        while not self._stop.is_set():
            try:
                if not self.sid:
                    self.abonnieren()
                    self.bei_aenderung()
                # bei zwei Dritteln der Laufzeit erneuern
                if self._stop.wait(self.laufzeit * 2 / 3):
                    break
                self.erneuern()
            except Exception as e:
                log.warning("Abo-Schleife: %s – neuer Versuch in 15 s", e)
                self.sid = None
                if self._stop.wait(15):
                    break

    def starten(self):
        self._thread = threading.Thread(target=self._schleife, daemon=True)
        self._thread.start()

    def stoppen(self):
        self._stop.set()
        self.abbestellen()


def eigene_ip_zu(host, port=80):
    """Ermittelt die IP, unter der uns der Linn erreicht."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, int(port)))
        return s.getsockname()[0]
    finally:
        s.close()
