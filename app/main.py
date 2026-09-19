# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
main.py – Liner Notes: Backend und Web-Oberfläche.

ARCHITEKTUR
===========
Ein einzelner Prozess, drei Aufgaben:

  1. Er hält ein UPnP-Abo auf den Info-Dienst des Linn. Kein Polling im
     Sekundentakt – der Player meldet Änderungen selbst. Das Abo läuft nach
     300 s ab und wird bei zwei Dritteln der Laufzeit erneuert.
  2. Bei einer Meldung löst er die res-URL auf einen Dateipfad auf, liest die
     Tags (nur lesend) und ergänzt sie aus der Sidecar-Datenbank.
  3. Er liefert das Ergebnis als JSON, als SSE-Strom und als Web-Oberfläche.

Der NOTIFY des Linn landet auf einem eigenen Endpunkt dieses Servers – es
braucht keinen zweiten HTTP-Server. NOTIFY ist keine Standard-HTTP-Methode,
weshalb die Route sie ausdrücklich erlauben muss.

Fällt das Eventing aus, greift ein sparsamer Rückfall: alle 30 Sekunden ein
Blick auf den Transportzustand. Das ist bewusst kein Ersatz, sondern ein Netz.

Musikdateien werden ausschließlich lesend geöffnet.
"""
import asyncio
import json
import logging
import os
import local_cover
import re
import time

from fastapi import FastAPI, Request
import sicherheit
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, StreamingResponse)

import umgebung
umgebung.laden()

import datenbank
datenbank.vorbereiten(umgebung.datenbank())

import bibliothek as bib_mod
import favoriten as fav_mod
import finden as finden_mod
import navidrome as nd_mod
import linn as linn_mod
import notes as notes_mod
import scrobbeln as scrobble_mod
import hoerenlernen as hl_mod

logging.basicConfig(level=os.environ.get("LINER_LOGLEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("liner")

# Player: aus der Umgebung, sonst per SSDP gesucht. Bewusst keine
# Adressen im Quelltext - sie gehören zum Netz des Betreibers, nicht zum
# Programm.
_player = finden_mod.player()
if _player is None:
    log.error(finden_mod.HINWEIS_PLAYER)
    raise SystemExit(1)
LINN_HOST = _player["host"]
LINN_PORT = _player["port"]
LINN_UDN = _player["udn"]
log.info("Player: %s:%s (%s)", LINN_HOST, LINN_PORT, _player.get("quelle"))
PORT = int(os.environ.get("LINER_PORT", "5060"))
BASIS_URL = os.environ.get("LINER_CALLBACK_BASIS", "")
# Datenverzeichnis, standardmaessig neben dem Programm. Im Betrieb setzen
# LINER_DB und LINER_COVERDIR die Pfade ohnehin ueber die Umgebung; der
# Standardwert soll nur ohne Konfiguration nicht ins Leere zeigen.
DATENDIR = os.environ.get(
    "LINER_DATENDIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
COVERDIR = os.environ.get("LINER_COVERDIR",
                          os.path.join(DATENDIR, "cover"))
MUSIC_ROOT = os.environ.get("LINER_MUSIC_ROOT", "/pfad/zur/musik")

# ---------------------------------------------------------------- 2b
# Steuerung. Standardmaessig an; `LINER_STEUERUNG=0` macht die Anzeige
# wieder zum reinen Lesegeraet (kein Endpunkt, keine Bedienleiste).
STEUERUNG_AN = os.environ.get("LINER_STEUERUNG", "1") != "0"

# Sicherheitsgrenze fuer die Lautstaerke.
#
# Das Geraet selbst erlaubt 98 (VolumeLimit), Unity Gain liegt bei 80.
# Eine Weboberflaeche, die versehentlich dorthin springt, ist an einer
# Isobarik keine Kleinigkeit. Darum drei Schranken:
#
#   1. LINER_VOL_MAX  - absolute Obergrenze, die dieser Dienst zulaesst
#   2. LINER_VOL_SCHRITT_MAX - groesste Aenderung pro einzelnem Befehl
#   3. SetVolume wird nur innerhalb dieser Grenzen weitergegeben; alles
#      darueber wird abgeschnitten und protokolliert.
#
# Gehoert laut Absprache in die .env, nicht in den Code.
VOL_MAX = int(os.environ.get("LINER_VOL_MAX", "65"))
VOL_SCHRITT_MAX = int(os.environ.get("LINER_VOL_SCHRITT_MAX", "5"))

# Version der HTTP-Schnittstelle. Wird erhoeht, wenn sich bestehende
# Felder oder Endpunkte UNVERTRAEGLICH aendern - neue Felder allein sind
# kein Grund. Clients (tvOS-App) koennen daran erkennen, ob sie zum Server
# passen; ohne diese Angabe wuerde eine veraltete App still Falsches
# anzeigen.
API_VERSION = 1

app = FastAPI(title="Liner Notes", docs_url=None, redoc_url=None)

@app.middleware("http")
async def browser_schutz(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path != "/notify":
        if (request.headers.get("sec-fetch-site") == "cross-site" or
                not sicherheit.origin_erlaubt(request.headers.get("origin"),
                    request.headers.get("host", ""), os.environ.get("LINER_ORIGINS", ""))):
            return JSONResponse({"grund": "Fremde Browser-Origin ist nicht erlaubt"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


linn = linn_mod.Linn(LINN_HOST, LINN_PORT, LINN_UDN)
aufloeser = notes_mod.Aufloeser()
_server = finden_mod.medienserver()
if _server is None:
    log.warning(finden_mod.HINWEIS_SERVER)
bibliothek = bib_mod.Bibliothek(
    basis=(_server or {}).get("basis"),
    udn=(_server or {}).get("udn"))
# Spiegelung der Merkliste nach Navidrome. Damit stehen die gemerkten
# Titel auch in Feishin, play:sub und Amperfy.
NAVI_SPIEGELN = os.environ.get("NAVIDROME_SPIEGELN", "1") != "0"
navi = nd_mod.Navidrome()

merkliste = fav_mod.Favoriten(
    os.environ.get("LINER_DB", os.path.join(DATENDIR, "liner.db")),
    wurzel=MUSIC_ROOT)

# Scrobbeln an Last.fm. Navidrome scrobbelt nur, was durch seine eigenen
# Clients laeuft - der Linn kommt dort nie vorbei. Ohne diesen Baustein
# fehlt auf Last.fm gerade das, was am meisten gehoert wird.
# Abschalten: LINER_SCROBBELN=0
# Hoeren lernen. Die Texte bleiben im Repo, hier wird nur gelesen.
# Modus: aus | album | live  (Standard: album, so beauftragt)
HL_MODUS = os.environ.get("HOERENLERNEN_MODUS", "album")
hoerenlernen = hl_mod.Sammlung()

SCROBBELN = os.environ.get("LINER_SCROBBELN", "1") != "0"
scrobbler = scrobble_mod.Scrobbler(
    os.environ.get("LINER_DB", os.path.join(DATENDIR, "liner.db")))

# ---------------------------------------------------------------- Verlauf
# "Zuletzt gehoert" braucht einen Mitschnitt - es gab bisher keinen.
# Festgehalten wird auf ALBUM-Ebene, nicht pro Titel: die Liste soll sagen
# "das habe ich gehoert", nicht jeden einzelnen Satz einer Symphonie
# auffuehren. Ein Album, das erneut laeuft, wandert nach oben statt ein
# zweites Mal zu erscheinen.
import sqlite3 as _sq3

VERLAUF_SCHEMA = """
CREATE TABLE IF NOT EXISTS verlauf (
  id        INTEGER PRIMARY KEY,
  album_id  INTEGER,
  artist    TEXT,
  album     TEXT,
  quelle    TEXT,
  zuletzt   REAL NOT NULL,
  anzahl    INTEGER NOT NULL DEFAULT 1,
  UNIQUE(album_id, artist, album)
);
CREATE INDEX IF NOT EXISTS i_verlauf_zeit ON verlauf(zuletzt DESC);
"""


def _verlauf_con():
    con = _sq3.connect(os.environ.get(
        "LINER_DB", os.path.join(DATENDIR, "liner.db")), timeout=5)
    con.row_factory = _sq3.Row
    return con


def verlauf_anlegen():
    try:
        with _verlauf_con() as con:
            con.executescript(VERLAUF_SCHEMA)
    except Exception as e:
        log.error("Verlaufstabelle nicht anlegbar: %s", e)


def verlauf_merken(inhalt):
    """Haelt fest, was laeuft. Wird nur bei echtem Albumwechsel gerufen."""
    album = (inhalt or {}).get("album")
    artist = (inhalt or {}).get("artist")
    if not album:
        return
    try:
        with _verlauf_con() as con:
            con.execute(
                "INSERT INTO verlauf (album_id, artist, album, quelle, zuletzt)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(album_id, artist, album) DO UPDATE SET"
                "   zuletzt=excluded.zuletzt, anzahl=anzahl+1,"
                "   quelle=excluded.quelle",
                (inhalt.get("album_id"), artist, album,
                 (inhalt.get("quelle") or {}).get("name"), time.time()))
    except Exception as e:
        log.warning("Verlauf nicht schreibbar: %s", e)


def verlauf_lesen(grenze=15):
    try:
        with _verlauf_con() as con:
            return [dict(r) for r in con.execute(
                "SELECT album_id, artist, album, quelle, zuletzt, anzahl"
                " FROM verlauf ORDER BY zuletzt DESC LIMIT ?", (grenze,))]
    except Exception as e:
        log.warning("Verlauf nicht lesbar: %s", e)
        return []


# Kennung dieses Prozesslaufs. Aendert sie sich, weiss ein offener
# Browser, dass die Folge-Nummern von vorn beginnen.
START_ID = "%d" % int(time.time())

zustand = {"daten": {"laeuft": False}, "stand": 0.0, "version": 0,
           "letztes": None,      # letzter Titel mit Inhalt, fuer den Leerlauf
           "folge": 0}           # Sequenznummer gegen verspaetete Antworten
abonnenten = set()
_lock = asyncio.Lock()
_loop = None


def _leerlauf():
    """Ruhezustand. Zeigt das zuletzt gespielte Album weiter an - abgedunkelt,
    damit sofort erkennbar ist, dass es nicht laeuft."""
    d = {"laeuft": False, "grund": "nichts-laeuft"}
    letztes = zustand.get("letztes")
    if letztes:
        d["zuletzt"] = {k: letztes.get(k) for k in
                        ("album", "artist", "jahr", "album_id", "titel",
                         "genre", "qualitaet", "tracks", "fakten", "eigene",
                         "extern", "dr", "cover", "cover_extern")}
    return d


def aktualisieren(ausloeser="event", folge=0):
    """Fragt den Linn ab und baut die Notes neu.

    Laeuft im Thread-Pool, weil die SOAP-Aufrufe blockieren. Die `folge` ist
    eine fortlaufende Nummer: Antworten, die spaeter eintreffen als eine
    neuere Abfrage, werden verworfen. Ohne das koennte bei schnellem
    Titelwechsel die Antwort des vorigen Titels die neuere ueberschreiben.
    """
    try:
        tz = linn.transport_zustand()
        q = linn.quelle()
        if tz in ("Stopped", ""):
            # Bei Stopped behaelt der Linn die Metadaten des letzten Titels.
            # Statt sie weiter als laufend auszugeben, wird der Leerlauf
            # gezeigt - aber mit dem letzten Album als ruhigem Hintergrund.
            neu = _leerlauf()
            neu["transport"] = tz
            neu["quelle"] = q
        else:
            uri, didl = linn.track()
            zeit = linn.zeit()
            neu = aufloeser.bauen(uri, didl, linn.details(), zeit=zeit,
                                  quelle=q, folge=folge)
            neu["transport"] = tz
            # Quelle ohne Bibliotheksdatei (Roon liefert scd://, AirPlay und
            # Radio gar nichts): Was der Linn selbst meldet, ist dann alles,
            # was es gibt. Kein Fehler, nur weniger Inhalt.
            if not neu.get("album_id"):
                neu["ohne_datei"] = True
                if didl.get("cover_url") and not neu.get("cover_extern"):
                    neu["cover_extern"] = didl["cover_url"]
            # Quellen ohne jede Titelinfo (AirPlay meldet oft nichts, ein
            # analoger Eingang nie): Die Anzeige soll dann NICHT mit leerem
            # Cover und einem Gedankenstrich dastehen, sondern ruhig sagen,
            # was los ist - und das Format nennen, das der Linn empfaengt.
            # Merkzustand anhaengen: ob der laufende Titel gemerkt ist und
            # welche Titel dieses Albums in der Merkliste stehen. Kommt vom
            # Server, damit jedes Geraet dasselbe sieht.
            try:
                neu["favorit"] = merkliste.gesetzt(neu.get("aktueller_pfad"))
                neu["favoriten"] = merkliste.pfade_des_albums(neu.get("album_id"))
            except Exception as e:
                log.warning("Merkzustand nicht lesbar: %s", e)
            try:
                neu["hoerenlernen"] = hoerhinweise_zum_zustand(neu)
            except Exception as e:
                log.warning("Hörhinweise nicht ermittelbar: %s", e)
            if neu.get("ohne_titelinfos"):
                d = linn.details()
                format_text = ""
                if d.get("bits") and d.get("samplerate"):
                    format_text = "%d bit / %.1f kHz" % (d["bits"], d["samplerate"] / 1000.0)
                    if d.get("codec"):
                        format_text += " · %s" % d["codec"]
                neu["nur_quelle"] = {
                    "name": q.get("name") or "Unbekannte Quelle",
                    "typ": q.get("typ") or "",
                    "format": format_text,
                }
        neu["ausloeser"] = ausloeser
        neu["folge"] = folge
        return neu
    except Exception as e:
        log.warning("Aktualisierung fehlgeschlagen: %s", e)
        d = _leerlauf()
        d["grund"] = "linn-nicht-erreichbar"
        d["fehler"] = str(e)[:200]
        d["folge"] = folge
        return d


def hoerhinweise_zum_zustand(daten):
    """Was von „Hören lernen" zum laufenden Titel gehört.

    Bewusst knapp gehalten: Der Zustand geht per SSE an jeden Client,
    bei jedem Titelwechsel. Die langen Hintergrundtexte holt die
    Oberfläche getrennt ab, wenn sie jemand aufklappt.
    """
    if HL_MODUS == "aus" or not hoerenlernen.eintraege:
        return None

    werk = None
    tags = daten.get("werke") or {}
    if isinstance(tags, dict):
        werk = tags.get("work") or tags.get("werk")
    eintrag, wie = hoerenlernen.finden(
        mbid=(daten.get("mb_zuordnung") or {}).get("release_mbid")
        if isinstance(daten.get("mb_zuordnung"), dict) else None,
        artist=daten.get("artist"), album=daten.get("album"), werk=werk)
    if not eintrag:
        return None

    aufnahme = eintrag.aufnahme_fuer(daten.get("artist"), daten.get("album"))
    try:
        nr = int(str(daten.get("tracknummer") or "0").lstrip("0") or 0)
    except (TypeError, ValueError):
        nr = 0

    aus = {
        "zugeordnet_ueber": wie,
        "quelle": eintrag.quelle,
        "datei": os.path.basename(eintrag.datei),
        "lizenz": "CC BY-NC-SA 4.0",
        "repo": "https://github.com/hoerenlernen/hoeren-lernen",
        "worauf_achten": eintrag.worauf_achten,
        "modus": HL_MODUS,
        "aufnahme": (aufnahme or {}).get("kennung"),
        "besetzung": {"art": (eintrag.besetzung or {}).get("art") or "",
                      "gruppen": eintrag.instrumentenliste()},
        "abschnitte": [a["titel"] for a in eintrag.abschnitte()],
        "tracks_mit_hinweisen": sorted({
            eintrag.tracknummer_in(h, aufnahme) for h in eintrag.hinweise
            if eintrag.tracknummer_in(h, aufnahme)}),
    }

    if HL_MODUS == "live" and nr:
        position = (daten.get("zeit") or {}).get("position_s") or 0
        aus["hinweis"] = eintrag.aktueller_hinweis(nr, position, aufnahme)
        aus["im_titel"] = len(eintrag.hinweise_fuer_track(nr, aufnahme))
    return aus


def andere_einspielungen(eintrag):
    """Welche der genannten Einspielungen liegen in der Bibliothek?"""
    import sqlite3 as _s
    aus = []
    try:
        con = _s.connect("file:%s?mode=ro" % os.environ.get(
            "LINER_DB", os.path.join(DATENDIR, "liner.db")), uri=True, timeout=5)
    except Exception:
        return aus
    try:
        alben = con.execute("SELECT id, artist, album, jahr FROM album").fetchall()
    finally:
        con.close()
    for a in eintrag.aufnahmen:
        if not isinstance(a, dict):
            continue
        na = hl_mod.normalisieren(a.get("artist") or "")
        nb = hl_mod.normalisieren(a.get("album") or "")
        for aid, artist, album, jahr in alben:
            if not nb:
                continue
            if nb in hl_mod.normalisieren(album or "") and (
                    not na or na in hl_mod.normalisieren(artist or "")):
                aus.append({"kennung": a.get("kennung") or album,
                            "album_id": aid, "artist": artist,
                            "album": album, "jahr": jahr})
                break
    return aus


async def setzen(daten):
    async with _lock:
        # Verspaetete Antwort eines frueheren Titels verwerfen
        if daten.get("folge", 0) < zustand["daten"].get("folge", 0):
            log.debug("veraltete Antwort verworfen (Folge %s < %s)",
                      daten.get("folge"), zustand["daten"].get("folge"))
            return False
        daten["start_id"] = START_ID
        # Scrobble-Buchhaltung VOR dem Vergleich unten: dort wird bei
        # unveraendertem Zustand abgebrochen, und genau die Messpunkte
        # braucht der Scrobbler, um die Hoerdauer mitzuzaehlen.
        if SCROBBELN:
            try:
                scrobbler.beobachten(daten)
            except Exception as e:
                log.debug("Scrobble-Buchhaltung: %s", e)
        alt = json.dumps(zustand["daten"], sort_keys=True, ensure_ascii=False)
        neu = json.dumps(daten, sort_keys=True, ensure_ascii=False)
        if alt == neu:
            return False
        if daten.get("laeuft") and daten.get("album"):
            # Nur bei echtem Albumwechsel in den Verlauf - nicht bei jedem
            # Titelwechsel innerhalb desselben Albums.
            vorher = zustand["daten"] or {}
            if (vorher.get("album"), vorher.get("artist")) != \
                    (daten.get("album"), daten.get("artist")):
                verlauf_merken(daten)
            zustand["letztes"] = daten
        zustand["daten"] = daten
        zustand["stand"] = time.time()
        zustand["version"] += 1
    nachricht = "event: update\ndata: %s\n\n" % neu
    for q in list(abonnenten):
        try:
            q.put_nowait(nachricht)
        except asyncio.QueueFull:
            pass
    return True


async def anstossen(ausloeser="event"):
    zustand["folge"] += 1
    meine = zustand["folge"]
    daten = await asyncio.to_thread(aktualisieren, ausloeser, meine)
    await setzen(daten)


def aus_thread_anstossen():
    """Wird aus dem Abo-Thread aufgerufen (nicht im Event-Loop)."""
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(anstossen("abo"), _loop)


abo = None
abo_zeit = None
abo_transport = None


@app.on_event("startup")
async def start():
    global _loop, abo
    _loop = asyncio.get_running_loop()
    ip = linn_mod.eigene_ip_zu(LINN_HOST, LINN_PORT)
    callback = BASIS_URL or "http://%s:%d/notify" % (ip, PORT)
    log.info("Callback für den Linn: %s", callback)
    verlauf_anlegen()
    try:
        anzahl = hoerenlernen.einlesen()
        log.info("Hörhinweise: %d Dateien aus %s", anzahl, hoerenlernen.wurzel)
    except Exception as e:
        log.warning("Hörhinweise nicht einlesbar: %s", e)
    abo = linn_mod.EventAbo(linn, callback, aus_thread_anstossen, dienst="Info")
    abo.starten()
    # Zweites Abo auf Time: liefert den Fortschritt, ohne dass wir sekuendlich
    # nachfragen muessen. Der Linn meldet nicht jede Sekunde - die Oberflaeche
    # interpoliert zwischen den Meldungen.
    global abo_zeit
    abo_zeit = linn_mod.EventAbo(linn, callback, aus_thread_anstossen,
                                 dienst="Time", version=1)
    abo_zeit.starten()
    # Drittes Abo auf Transport. Ohne dieses Abo bemerkt die Anzeige ein
    # Pause, das jemand in Kazoo drueckt, erst beim 30-Sekunden-Rueckfall -
    # der Info-Dienst meldet nur Titelwechsel, keinen Zustandswechsel.
    # Genau das verlangt Punkt 4: die Anzeige folgt dem Geraet.
    global abo_transport
    abo_transport = linn_mod.EventAbo(linn, callback, aus_thread_anstossen,
                                      dienst="Transport", version=1)
    abo_transport.starten()
    await anstossen("start")
    asyncio.create_task(rueckfall())
    if SCROBBELN:
        if scrobbler.zugang.vollstaendig:
            asyncio.create_task(scrobble_takt())
            log.info("Scrobbeln aktiv (%d Eintraege offen)", scrobbler.offen())
        else:
            log.warning("Scrobbeln angefordert, aber Zugang unvollstaendig - "
                        "LASTFM_SCROBBLE_KEY, LASTFM_SCROBBLE_SECRET und "
                        "LASTFM_SESSION_KEY pruefen")


@app.on_event("shutdown")
async def ende():
    for a in (abo, abo_zeit, abo_transport):
        if a:
            a.stoppen()


async def scrobble_takt():
    """Warteschlange regelmaessig an Last.fm schicken.

    Getrennt vom Beobachten, damit eine langsame oder nicht erreichbare
    Gegenstelle die Anzeige nicht bremst. 60 Sekunden sind reichlich: ein
    Scrobble ist nicht zeitkritisch, und ein Stapel nimmt bis zu 50
    Titel auf einmal."""
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(scrobbler.abschicken)
        except Exception as e:
            log.debug("Scrobble-Takt: %s", e)


async def rueckfall():
    """Sicherheitsnetz, falls das Abo stillschweigend ausfällt.
    30 Sekunden sind weit entfernt von Polling im Sekundentakt, fangen aber
    einen stummen Abo-Verlust ab, bevor es auffällt."""
    while True:
        await asyncio.sleep(30)
        try:
            await anstossen("rueckfall")
        except Exception as e:
            log.debug("Rückfall: %s", e)


# ---------------------------------------------------------------- Endpunkte
@app.api_route("/notify", methods=["NOTIFY", "POST"])
async def notify(request: Request):
    if request.method != "NOTIFY" or not request.client or request.client.host != LINN_HOST:
        return PlainTextResponse("nur Player-NOTIFY", status_code=403)
    # Der Inhalt wird nicht benötigt; auch große NOTIFY-Bodies werden nicht gepuffert.
    await anstossen("notify")
    return PlainTextResponse("", status_code=200)


@app.get("/api/now")
async def api_now():
    return JSONResponse({"stand": zustand["stand"], "version": zustand["version"],
                         **zustand["daten"]})


@app.get("/api/stream")
async def api_stream():
    q = asyncio.Queue(maxsize=8)
    abonnenten.add(q)

    async def erzeugen():
        try:
            yield "event: update\ndata: %s\n\n" % json.dumps(
                zustand["daten"], ensure_ascii=False)
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"     # hält Proxy und Browser wach
        finally:
            abonnenten.discard(q)

    return StreamingResponse(erzeugen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/cover/{album_id}")
async def api_cover(album_id: int, groesse: int = 0):
    """Liefert das beste verfügbare Cover: bevorzugt das nachgeladene
    hochauflösende, sonst die cover.jpg aus dem Albumordner.

    `groesse` liefert eine verkleinerte Fassung (längste Kante in Pixeln).
    Für eine Trefferliste am Fernseher braucht es Vorschaubilder, keine
    1200er-Vollbilder: zwölf Treffer wären sonst über 6 MB über WLAN.
    Erlaubt sind feste Stufen, damit der Zwischenspeicher nicht durch
    beliebige Werte aufgebläht wird. Die Musikdateien bleiben unberührt -
    verkleinert wird nur in das Datenverzeichnis dieses Dienstes."""
    def ausliefern(pfad):
        if groesse:
            klein = _cover_verkleinern(pfad, album_id, groesse)
            if klein:
                pfad = klein
        return FileResponse(pfad, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

    gross = os.path.join(COVERDIR, "%d.jpg" % album_id)
    if os.path.isfile(gross):
        return ausliefern(gross)
    import sqlite3
    con = sqlite3.connect(notes_mod.DB, timeout=5)
    row = con.execute("SELECT cover_datei FROM album WHERE id=?", (album_id,)).fetchone()
    con.close()
    if row and row[0]:
        pfad = os.path.join(MUSIC_ROOT, row[0])
        if sicherheit.innerhalb(MUSIC_ROOT, pfad) and os.path.isfile(pfad):
            return ausliefern(pfad)
    return PlainTextResponse("kein Cover", status_code=404)


# Feste Stufen. Beliebige Werte würden den Zwischenspeicher aufblähen und
# wären ein bequemer Weg, den Server mit Rechenarbeit zu beschäftigen.
COVER_STUFEN = (200, 300, 500, 800)
MINIDIR = os.path.join(os.path.dirname(COVERDIR.rstrip("/")), "cover-klein") \
    if COVERDIR else "./data/cover-klein"


def _cover_verkleinern(quelle, album_id, wunsch):
    """Verkleinert einmalig und legt das Ergebnis ab.

    Gibt den Pfad der kleinen Fassung zurück oder None, wenn nichts zu tun
    ist (Bild schon klein) oder die Verkleinerung fehlschlägt - dann wird
    das Original ausgeliefert, statt einen Fehler zu zeigen."""
    stufe = min((s for s in COVER_STUFEN if s >= wunsch), default=COVER_STUFEN[-1])
    ziel = os.path.join(MINIDIR, "%d-%d.jpg" % (album_id, stufe))
    try:
        if os.path.isfile(ziel) and \
                os.path.getmtime(ziel) >= os.path.getmtime(quelle):
            return ziel
        from PIL import Image
        os.makedirs(MINIDIR, exist_ok=True)
        with Image.open(quelle) as im:
            if max(im.size) <= stufe:
                return None                      # schon klein genug
            im = im.convert("RGB")
            im.thumbnail((stufe, stufe), Image.LANCZOS)
            im.save(ziel, "JPEG", quality=86, optimize=True)
        return ziel
    except Exception as e:
        log.debug("Cover nicht verkleinerbar (%s): %s", quelle, e)
        return None


@app.get("/api/dr")
async def api_dr(sortierung: str = "dr", richtung: str = "asc",
                 genre: str = "", qualitaet: str = "", limit: int = 5000):
    """Alle gemessenen Alben mit DR-Wert.

    `qualitaet=cd` meint 16 bit / 44,1 kHz, `qualitaet=hires` alles darueber.
    `auffaellig` markiert Hi-Res-Alben mit DR 8 oder schlechter - dort steht
    hohe Auflaesung gegen plattgedruecktes Mastering, was den Aufpreis
    fragwuerdig macht."""
    import sqlite3
    erlaubt = {"dr": "d.dr", "artist": "a.artist", "album": "a.album",
               "jahr": "a.jahr", "genre": "a.genre", "tracks": "d.tracks"}
    spalte = erlaubt.get(sortierung, "d.dr")
    ri = "DESC" if richtung.lower() == "desc" else "ASC"

    wo, par = ["1=1"], []
    if genre:
        wo.append("a.genre LIKE ?"); par.append("%%%s%%" % genre)
    if qualitaet == "cd":
        wo.append("(a.bits <= 16 AND a.samplerate <= 44100)")
    elif qualitaet == "hires":
        wo.append("(a.bits > 16 OR a.samplerate > 48000)")

    con = sqlite3.connect(notes_mod.DB, timeout=20)
    con.row_factory = sqlite3.Row
    zeilen = con.execute(
        "SELECT a.id, a.artist, a.album, a.jahr, a.genre, a.bits, a.samplerate, "
        "       d.dr, d.dr_min, d.dr_max, d.tracks "
        "FROM dr_album d JOIN album a ON a.id = d.album_id "
        "WHERE %s ORDER BY %s %s, a.artist, a.album LIMIT ?"
        % (" AND ".join(wo), spalte, ri), par + [limit]).fetchall()
    gesamt = con.execute("SELECT COUNT(*) FROM album").fetchone()[0]
    con.close()

    raus = []
    for r in zeilen:
        hires = (r["bits"] or 0) > 16 or (r["samplerate"] or 0) > 48000
        dr = r["dr"]
        raus.append({
            "id": r["id"], "artist": r["artist"], "album": r["album"],
            "jahr": (r["jahr"] or "")[:4], "genre": r["genre"],
            "dr": dr, "min": r["dr_min"], "max": r["dr_max"], "tracks": r["tracks"],
            "qualitaet": "%d/%.1f" % (r["bits"] or 0, (r["samplerate"] or 0) / 1000.0),
            "hires": hires,
            "stufe": "rot" if dr <= 7 else "orange" if dr <= 9 else "gelb" if dr <= 11 else "gruen",
            "auffaellig": bool(hires and dr <= 8),
        })
    return {"alben": raus, "gemessen": len(raus), "gesamt": gesamt}


@app.get("/dr")
async def dr_seite():
    p = os.path.join(STATIC, "dr.html")
    if os.path.isfile(p):
        return FileResponse(p)
    return HTMLResponse("<h1>DR-Übersicht</h1><p>static/dr.html fehlt.</p>")


@app.get("/api/cover-extern")
async def api_cover_extern(url: str):
    """Cover nur von bekannten Geräten; kein allgemeiner LAN-Proxy."""
    if url.startswith(local_cover.PREFIX):
        path = local_cover.resolve(MUSIC_ROOT, url)
        if not path:
            return PlainTextResponse("kein Cover", status_code=404)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})

    import urllib.parse
    hosts = {LINN_HOST, urllib.parse.urlsplit(bibliothek.basis).hostname}
    hosts.update(x.strip() for x in os.environ.get("LINER_COVER_HOSTS", "").split(",") if x.strip())
    if not sicherheit.cover_url_erlaubt(url, hosts):
        return PlainTextResponse("Cover-Adresse nicht freigegeben", status_code=403)
    try:
        daten, typ = await asyncio.to_thread(sicherheit.cover_laden, url)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=415)
    except Exception:
        return PlainTextResponse("Cover nicht erreichbar", status_code=502)
    from fastapi.responses import Response
    return Response(daten, media_type=typ,
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/pruefen")
async def api_pruefen(limit: int = 2000):
    """Alben mit verdaechtigen Tags, nach Verdachtsstaerke sortiert.

    Erzeugt von bin/pruefen.py. Diese Schnittstelle liest nur - es wird
    ausdruecklich nichts korrigiert."""
    import sqlite3
    con = sqlite3.connect(notes_mod.DB, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        zeilen = con.execute(
            "SELECT v.punkte, v.gruende, v.label, v.copyright, v.personnel, "
            "       a.id, a.artist, a.album, a.jahr, a.genre "
            "FROM verdacht v JOIN album a ON a.id = v.album_id "
            "ORDER BY v.punkte DESC, a.artist LIMIT ?", (limit,)).fetchall()
        gesamt = con.execute("SELECT COUNT(*) FROM album").fetchone()[0]
        stand = con.execute("SELECT MAX(geprueft_am) FROM verdacht").fetchone()[0]
    except sqlite3.OperationalError:
        return {"alben": [], "gesamt": 0, "stand": None,
                "hinweis": "Noch nicht geprüft – bin/pruefen.py ausführen."}
    finally:
        con.close()
    raus = []
    for r in zeilen:
        try:
            gruende = json.loads(r["gruende"])
        except (TypeError, ValueError):
            gruende = []
        raus.append({"id": r["id"], "artist": r["artist"], "album": r["album"],
                     "jahr": (r["jahr"] or "")[:4], "genre": r["genre"],
                     "punkte": r["punkte"], "gruende": gruende,
                     "label": r["label"], "copyright": r["copyright"],
                     "personnel": r["personnel"],
                     "stufe": "hoch" if r["punkte"] >= 90 else
                              "mittel" if r["punkte"] >= 60 else "niedrig"})
    return {"alben": raus, "verdaechtig": len(raus), "gesamt": gesamt, "stand": stand}


@app.get("/pruefen")
async def pruefen_seite():
    p = os.path.join(STATIC, "pruefen.html")
    if os.path.isfile(p):
        return FileResponse(p)
    return HTMLResponse("<h1>Tag-Prüfung</h1><p>static/pruefen.html fehlt.</p>")


@app.get("/api/verdacht.csv")
async def api_verdacht_csv():
    """Liefert die CSV-Fassung zum Herunterladen."""
    pfad = "./doku/verdacht.csv"
    if not os.path.isfile(pfad):
        return PlainTextResponse("noch nicht erzeugt – bin/pruefen.py ausführen",
                                 status_code=404)
    return FileResponse(pfad, media_type="text/csv", filename="verdacht.csv")


# ---------------------------------------------------------------- 2b
# Steuerung. Alles ueber POST, damit kein Befehl versehentlich durch einen
# Vorschau-Abruf des Browsers ausgeloest wird. Dieselben Endpunkte nutzt
# spaeter die tvOS-App.

def _steuerung_aus():
    return JSONResponse({"ok": False, "grund": "Steuerung ist abgeschaltet "
                                               "(LINER_STEUERUNG=0)."},
                        status_code=403)


# WICHTIG: Diese feste Route muss VOR "/api/steuerung/{befehl}"
# stehen. FastAPI nimmt die erste passende Route - sonst fängt der
# Platzhalter "zu-titel" ab und antwortet "Unbekannter Befehl".
@app.post("/api/steuerung/zu-titel")
async def api_zu_titel(request: Request):
    """Springt in der Warteschlange zu einem Titel (Punkt 6).

    Der Abgleich laeuft ueber den Dateipfad: die Warteschlange liefert
    res-URLs, die mit demselben Dekoder auf Pfade abgebildet werden wie die
    Anzeige. Enthaelt die Warteschlange den Titel nicht, wird das gesagt -
    ungefragt etwas einzureihen waere eine Ueberraschung."""
    if not STEUERUNG_AN:
        return _steuerung_aus()
    try:
        daten = await request.json()
        pfad = (daten or {}).get("pfad")
    except Exception:
        pfad = None
    if not pfad:
        return JSONResponse({"ok": False, "grund": "Kein Titel angegeben."},
                            status_code=400)

    rel = merkliste.relativ(pfad)
    if not rel:
        return JSONResponse({"ok": False, "grund": "Pfad außerhalb der "
                             "Bibliothek."}, status_code=400)
    ziel = os.path.join(MUSIC_ROOT, rel)

    def suchen():
        for e in linn.warteschlange():
            p = notes_mod.url_zu_pfad(e["uri"])
            if p and os.path.realpath(p) == os.path.realpath(ziel):
                return e["id"]
        return None

    pid = await asyncio.to_thread(suchen)
    if pid is None:
        return JSONResponse({"ok": False, "in_warteschlange": False,
                             "grund": "Dieser Titel ist nicht in der "
                                      "Warteschlange."}, status_code=404)
    ok, grund = await asyncio.to_thread(linn.zu_id_springen, pid)
    if ok:
        await asyncio.to_thread(linn.steuern, "Play")
        await _nachfassen()
    return JSONResponse({"ok": ok, "grund": grund, "in_warteschlange": True})


@app.post("/api/steuerung/{befehl}")
async def api_steuerung(befehl: str, request: Request):
    """Transportbefehle: play, pause, stop, weiter, zurueck, umschalten.

    Nach dem Befehl wird der Zustand NICHT geraten, sondern beim Linn
    abgefragt und wie ein Ereignis verteilt. So folgt die Anzeige immer
    dem Geraet - auch wenn parallel jemand in Kazoo drueckt."""
    if not STEUERUNG_AN:
        return _steuerung_aus()

    ABBILDUNG = {"play": "Play", "pause": "Pause", "stop": "Stop",
                 "weiter": "SkipNext", "zurueck": "SkipPrevious"}

    if befehl == "umschalten":
        # Ein Knopf fuer Play/Pause. Der Zustand wird beim GERAET erfragt,
        # nicht dem eigenen Zwischenspeicher entnommen: der kann veraltet
        # sein, und dann schaltet der Knopf in die falsche Richtung.
        # Genau das ist im Test passiert.
        jetzt = await asyncio.to_thread(linn.transport_zustand)
        aktion = "Pause" if str(jetzt).lower() == "playing" else "Play"
    elif befehl in ABBILDUNG:
        aktion = ABBILDUNG[befehl]
    elif befehl == "springen":
        try:
            daten = await request.json()
            sekunde = max(0, int(float(daten.get("sekunde", 0))))
        except Exception:
            return JSONResponse({"ok": False, "grund": "Ungültige Sekunde."},
                                status_code=400)
        ok, grund = await asyncio.to_thread(linn.springen, sekunde)
        if ok:
            await _nachfassen()
        return JSONResponse({"ok": ok, "grund": grund})
    else:
        return JSONResponse({"ok": False, "grund": "Unbekannter Befehl."},
                            status_code=404)

    ok, grund = await asyncio.to_thread(linn.steuern, aktion)
    if ok:
        await _nachfassen()
    return JSONResponse({"ok": ok, "grund": grund})


@app.post("/api/steuerung/lautstaerke/{was}")
async def api_lautstaerke(was: str, request: Request):
    """lauter, leiser, stumm, setzen.

    Jede Aenderung geht durch die Grenzen aus der Konfiguration. `setzen`
    akzeptiert einen absoluten Wert, aber nur innerhalb von VOL_MAX und
    nur mit einer Schrittweite bis VOL_SCHRITT_MAX - ein Sprung von 40 auf
    90 wird abgeschnitten, nicht ausgefuehrt."""
    if not STEUERUNG_AN:
        return _steuerung_aus()

    stand = await asyncio.to_thread(linn.lautstaerke_lesen)
    jetzt = stand.get("wert")
    if jetzt is None:
        return JSONResponse({"ok": False,
                             "grund": "Lautstärke nicht lesbar."}, status_code=503)

    if was == "stumm":
        neu_stumm = not stand.get("stumm", False)
        ok, grund = await asyncio.to_thread(
            linn.steuern_volume, "SetMute", {"Value": 1 if neu_stumm else 0})
        stand = await asyncio.to_thread(linn.lautstaerke_lesen)
        return JSONResponse({"ok": ok, "grund": grund,
                             "stand": _vol_stand(stand)})

    if was in ("lauter", "leiser"):
        try:
            daten = await request.json()
            schritt = int(daten.get("schritt", 1))
        except Exception:
            schritt = 1
        schritt = max(1, min(abs(schritt), VOL_SCHRITT_MAX))
        ziel = jetzt + schritt if was == "lauter" else jetzt - schritt
    elif was == "setzen":
        try:
            daten = await request.json()
            ziel = int(daten.get("wert"))
        except Exception:
            return JSONResponse({"ok": False, "grund": "Ungültiger Wert."},
                                status_code=400)
        # Schrittweite auch beim absoluten Setzen begrenzen
        if abs(ziel - jetzt) > VOL_SCHRITT_MAX:
            ziel = jetzt + (VOL_SCHRITT_MAX if ziel > jetzt else -VOL_SCHRITT_MAX)
            log.info("Lautstaerkesprung begrenzt: Ziel auf %s gekappt", ziel)
    else:
        return JSONResponse({"ok": False, "grund": "Unbekannter Befehl."},
                            status_code=404)

    grenze = min(VOL_MAX, stand.get("geraetelimit", VOL_MAX))
    ziel = max(0, min(ziel, grenze))
    if ziel == jetzt:
        return JSONResponse({"ok": True, "grund": None,
                             "hinweis": "Grenze erreicht (%s)." % grenze,
                             "stand": _vol_stand(stand)})

    ok, grund = await asyncio.to_thread(linn.lautstaerke_setzen, ziel)
    stand = await asyncio.to_thread(linn.lautstaerke_lesen)
    return JSONResponse({"ok": ok, "grund": grund, "stand": _vol_stand(stand)})


async def _nachfassen():
    """Zustand nach einem eigenen Befehl nachziehen.

    Der Linn schaltet seinen TransportState nicht sofort um - eine Abfrage
    unmittelbar nach dem Befehl liefert noch den alten Wert. Im Test zeigte
    die Anzeige darum nach einem Pause noch "Playing".

    Zwei Anlaeufe mit kurzem Abstand. Das Transport-Abo meldet den Wechsel
    ohnehin selbst; dies ist nur das Netz darunter, damit die Rueckmeldung
    am Knopf nicht hinterherhinkt. Laeuft im Hintergrund, damit die Antwort
    auf den Befehl nicht darauf warten muss.
    """
    async def spaeter():
        for pause in (0.4, 1.2):
            await asyncio.sleep(pause)
            try:
                await anstossen("steuerung")
            except Exception as e:
                log.warning("Nachfassen fehlgeschlagen: %s", e)
    asyncio.create_task(spaeter())


def _vol_stand(stand):
    """Was die Oberflaeche ueber die Lautstaerke wissen muss."""
    return {"wert": stand.get("wert"), "stumm": stand.get("stumm"),
            "grenze": min(VOL_MAX, stand.get("geraetelimit", VOL_MAX)),
            "schritt_max": VOL_SCHRITT_MAX}


@app.get("/api/steuerung/stand")
async def api_steuerung_stand():
    """Was gerade bedienbar ist - und die Lautstaerke. Wird beim Laden
    geholt; laufende Aenderungen kommen ueber den SSE-Strom."""
    if not STEUERUNG_AN:
        return JSONResponse({"an": False})
    stand, info, modus = await asyncio.gather(
        asyncio.to_thread(linn.lautstaerke_lesen),
        asyncio.to_thread(linn.stream_info),
        asyncio.to_thread(linn.modus_info))
    return JSONResponse({"an": True, "lautstaerke": _vol_stand(stand),
                         "kann_springen": info.get("kann_springen", False),
                         "kann_pausieren": info.get("kann_pausieren", False),
                         "kann_weiter": modus.get("weiter", False),
                         "kann_zurueck": modus.get("zurueck", False)})


# ---------------------------------------------------------------- Merkliste
# "Gefaellt mir" fuer einzelne Titel. Schluessel ist der Dateipfad; die
# Musikdateien selbst werden dabei nicht angefasst.

@app.post("/api/favorit")
async def api_favorit(request: Request):
    """Schaltet den Favoriten um.

    Ohne Rumpf gilt der laufende Titel. Mit {"pfad": "..."} ein beliebiger
    Titel aus der Trackliste - so kann man merken, ohne abzuspielen."""
    try:
        daten = await request.json()
    except Exception:
        daten = {}
    pfad = (daten or {}).get("pfad")

    if pfad:
        # Die Pruefung auf die Bibliothekswurzel steckt in setzen_pfad();
        # dort ist auch die Umrechnung auf die Indexform zu Hause.
        gesetzt, grund = await asyncio.to_thread(merkliste.setzen_pfad, pfad)
    else:
        laufend = zustand["daten"] or {}
        inhalt = laufend if laufend.get("laeuft") else (laufend.get("zuletzt") or {})
        gesetzt, grund = await asyncio.to_thread(merkliste.umschalten, inhalt)

    if gesetzt is None:
        return JSONResponse({"ok": False, "grund": grund}, status_code=400)

    # Nach Navidrome spiegeln. Schlaegt das fehl, bleibt die Merkliste
    # trotzdem gueltig - die Spiegelung ist eine Zugabe, keine Bedingung.
    gespiegelt = None
    if NAVI_SPIEGELN and navi.eingerichtet:
        gespiegelt = await asyncio.to_thread(_navi_spiegeln, pfad, gesetzt)

    anzahl = len(await asyncio.to_thread(merkliste.liste))
    return JSONResponse({"ok": True, "gesetzt": gesetzt, "anzahl": anzahl,
                         "navidrome": gespiegelt})


def _navi_spiegeln(pfad, gesetzt):
    """Setzt oder entfernt die Markierung in Navidrome.

    Laeuft im Thread (HTTP + SQLite). Gibt True/False/None zurueck:
    None heisst "nicht zuzuordnen" - das ist kein Fehler, etwa bei einem
    ueber AirPlay gemerkten Titel ohne Dateibezug."""
    try:
        if pfad:
            rel = merkliste.relativ(pfad)
            eintrag = {"pfad": rel, "titel": None, "artist": None}
            if rel:
                for e in merkliste.liste():
                    if e["pfad"] == rel:
                        eintrag = e
                        break
        else:
            liste = merkliste.liste(grenze=1)
            eintrag = liste[0] if liste else {}
        sid = navi.song_id(eintrag.get("pfad"), eintrag.get("titel"),
                           eintrag.get("artist"))
        if not sid:
            return None
        ok, _ = navi.markieren(sid, an=bool(gesetzt))
        return ok
    except Exception as e:
        log.warning("Navidrome-Spiegelung fehlgeschlagen: %s", e)
        return False


@app.get("/api/favoriten")
async def api_favoriten():
    eintraege = await asyncio.to_thread(merkliste.liste)
    for e in eintraege:
        voll = merkliste.absolut(e["pfad"])
        e["datei_fehlt"] = bool(e["pfad"]) and not os.path.isfile(voll)
    return JSONResponse({"anzahl": len(eintraege), "eintraege": eintraege})


@app.post("/api/favoriten/abgleich")
async def api_favoriten_abgleich(request: Request):
    """Gleicht die Merkliste mit Navidrome ab.

    Zwei Richtungen, beide zusammenführend – es wird nichts entfernt:

      hin  – was hier gemerkt ist, wird in Navidrome markiert
      her  – was in Navidrome markiert ist, kommt in die Merkliste

    Damit stehen die Lieblingstitel an einem Ort, egal wo sie entstanden
    sind. Navidrome hatte 24 markierte Titel, bevor es diese Funktion gab;
    die bleiben erhalten und wandern herüber."""
    if not navi.eingerichtet:
        return JSONResponse({"ok": False, "grund": "Navidrome ist nicht "
                             "eingerichtet (NAVIDROME_USER/PASS in .env)."},
                            status_code=400)
    try:
        daten = await request.json()
    except Exception:
        daten = {}
    richtung = (daten or {}).get("richtung") or "beide"

    ergebnis = await asyncio.to_thread(_abgleich_lauf, richtung)
    return JSONResponse(ergebnis)


def _abgleich_lauf(richtung):
    """Der eigentliche Abgleich, im Thread: viele HTTP-Aufrufe."""
    aus = {"ok": True, "hin": 0, "her": 0, "nicht_zuzuordnen": 0,
           "fehler": 0, "zusammengefuehrt": 0}
    if not navi.erreichbar():
        return {"ok": False, "grund": "Navidrome antwortet nicht."}

    dort = navi.markierte_ids()

    # --- hin: Merkliste -> Navidrome
    if richtung in ("hin", "beide"):
        for e in merkliste.liste():
            sid = navi.song_id(e["pfad"], e["titel"], e["artist"])
            if not sid:
                aus["nicht_zuzuordnen"] += 1
                continue
            if sid in dort:
                continue
            ok, _ = navi.markieren(sid, an=True)
            if ok:
                aus["hin"] += 1
            else:
                aus["fehler"] += 1

    # --- her: Navidrome -> Merkliste
    if richtung in ("her", "beide"):
        try:
            antwort = navi._ruf("getStarred2")
            lieder = (antwort.get("starred2") or {}).get("song") or []
        except Exception as e:
            log.warning("Navidrome-Favoriten nicht lesbar: %s", e)
            lieder = []
        bekannt = {e["pfad"] for e in merkliste.liste() if e["pfad"]}
        for l in lieder:
            rel = navi.pfad_zu_id(l.get("id"))
            if not rel or rel in bekannt:
                continue
            gesetzt, _ = merkliste.setzen_pfad(rel)
            if gesetzt:
                aus["her"] += 1
                bekannt.add(rel)

    aus["zusammengefuehrt"] = merkliste.dubletten_zusammenfuehren()
    return aus


@app.get("/api/favoriten.m3u")
async def api_favoriten_m3u():
    text = await asyncio.to_thread(merkliste.m3u, MUSIC_ROOT)
    return PlainTextResponse(text, headers={
        "Content-Disposition": 'attachment; filename="liner-favoriten.m3u"',
        "Content-Type": "audio/x-mpegurl; charset=utf-8"})


@app.post("/api/favoriten/abspielen")
async def api_favoriten_abspielen(request: Request):
    """Legt die Merkliste in die Warteschlange des Linn.

    `ersetzen` (Standard) leert die Warteschlange vorher, sonst wird
    angehaengt. Gebaut wird die res-URL wie MinimServer sie liefert - die
    Kodierung uebernimmt notes.minim_kodieren, damit Sonderzeichen denselben
    Weg gehen wie beim Lesen."""
    if not STEUERUNG_AN:
        return _steuerung_aus()
    try:
        daten = await request.json()
    except Exception:
        daten = {}
    ersetzen = (daten or {}).get("ersetzen", True)

    alle = await asyncio.to_thread(merkliste.liste)
    # Einreihen geht nur mit der ECHTEN res-URL, die beim Merken
    # mitgeschrieben wurde. Titel, die nie ueber MinimServer liefen (oder
    # vor dieser Erweiterung gemerkt wurden), haben keine - die werden
    # benannt, nicht stillschweigend uebergangen.
    # Titel ohne res-URL (gemerkt über Roon oder aus der Trackliste) über
    # das ContentDirectory nachschlagen. Das schliesst die Luecke, die beim
    # Bau der Merkliste noch offen war: gemerkt werden konnte alles,
    # einreihen liess sich nur, was schon einmal über MinimServer lief.
    async def _nachliefern(e):
        gefunden = await asyncio.to_thread(
            bibliothek.titel_finden, e.get("titel"), e.get("album"),
            e.get("artist"))
        if gefunden and gefunden.get("res_url"):
            e["res_url"] = gefunden["res_url"]
            e["didl"] = gefunden.get("didl")
            await asyncio.to_thread(merkliste.adresse_merken, e["pfad"],
                                    e["res_url"], e["didl"])
            return True
        return False

    fehlten = [e for e in alle if not e.get("res_url")]
    nachgeliefert = 0
    for e in fehlten:
        try:
            if await _nachliefern(e):
                nachgeliefert += 1
        except Exception as ex:
            log.warning("Nachschlagen fehlgeschlagen (%s): %s", e.get("titel"), ex)

    eintraege = [e for e in alle if e.get("res_url")]
    # Dateiprüfung nur, wo ein Pfad bekannt ist - über Roon gemerkte Titel
    # haben keinen, sind aber jetzt trotzdem einreihbar.
    eintraege = [e for e in eintraege
                 if not e.get("pfad")
                 or os.path.isfile(merkliste.absolut(e["pfad"]) or "")]
    ohne_url = [e for e in alle if not e.get("res_url")]
    if not eintraege:
        return JSONResponse(
            {"ok": False, "anzahl_ohne_url": len(ohne_url),
             "grund": "Kein gemerkter Titel liess sich in der Bibliothek "
                      "auflösen."},
            status_code=400)

    ergebnis = await asyncio.to_thread(_warteschlange_fuellen, eintraege, ersetzen)
    if nachgeliefert:
        ergebnis["nachgeliefert"] = nachgeliefert
    if ohne_url:
        ergebnis["uebergangen"] = len(ohne_url)
    await _nachfassen()
    return JSONResponse(ergebnis)


def _warteschlange_fuellen(eintraege, ersetzen):
    """Laeuft im Thread: viele SOAP-Aufrufe, einer nach dem anderen.

    Reihenfolge ist wichtig - Insert haengt hinter eine bestehende Id, also
    muss jeder Titel hinter den vorigen. Fehler brechen nicht ab, sondern
    werden gezaehlt: eine Playlist mit 38 von 40 Titeln ist brauchbar, eine
    abgebrochene nicht."""
    if ersetzen:
        try:
            linn.aufruf("Playlist", 1, "DeleteAll", timeout=linn.STEUER_TIMEOUT)
        except Exception as e:
            return {"ok": False, "grund": "Warteschlange nicht leerbar: %s" % e}

    nach = 0        # 0 = an den Anfang
    gesetzt = fehler = 0
    fehlerliste = []
    for e in eintraege:
        uri = e["res_url"]
        didl = e.get("didl") or ""
        try:
            antwort = linn.aufruf("Playlist", 1, "Insert",
                                  {"AfterId": nach, "Uri": uri, "Metadata": didl},
                                  timeout=linn.STEUER_TIMEOUT)
            nach = int(antwort.get("NewId") or nach)
            gesetzt += 1
        except Exception as ex:
            fehler += 1
            if len(fehlerliste) < 3:
                fehlerliste.append("%s: %s" % (e["titel"], ex))
    if gesetzt and ersetzen:
        try:
            linn.aufruf("Playlist", 1, "SeekIndex", {"Value": 0},
                        timeout=linn.STEUER_TIMEOUT)
            linn.steuern("Play")
        except Exception as ex:
            log.warning("Start der Merkliste fehlgeschlagen: %s", ex)
    return {"ok": gesetzt > 0, "gesetzt": gesetzt, "fehler": fehler,
            "grund": "; ".join(fehlerliste) or None}


# ---------------------------------------------------------------- Punkt 8
# Schlanker Einstieg: Suche ueber die Bibliothek, Album abspielen, Titel
# anhaengen, zuletzt Gehoertes. Bewusst KEINE Kacheln, keine Genre- oder
# Kuenstlerseiten, keine Hierarchien - dafuer bleibt Kazoo.

@app.get("/api/bibliothek/suche")
async def api_bib_suche(q: str = "", grenze: int = 12):
    if len((q or "").strip()) < 2:
        return JSONResponse({"treffer": [], "gesamt": 0})
    erg = await asyncio.to_thread(bibliothek.suche, q, min(max(grenze, 1), 40))
    # Die res-URLs muss die Oberflaeche nicht sehen; sie schickt Album und
    # Interpret zurueck, und der Server loest erneut auf. So kann von
    # aussen keine beliebige URL in die Warteschlange gelegt werden.
    schlank = []
    for g in erg.get("treffer", []):
        album_id = None
        for t in g["titel"]:
            pfad = notes_mod.url_zu_pfad(t.get("res_url"))
            if not pfad:
                continue
            album_id = await asyncio.to_thread(_album_id_zu_pfad, pfad)
            if album_id:
                break
        schlank.append({
            "album": g["album"], "artist": g["artist"],
            "album_id": album_id,
            "anzahl": len(g["titel"]),
            "titel": [{"nr": t["nr"], "titel": t["titel"],
                       "dauer_s": t["dauer_s"]} for t in g["titel"][:40]],
        })
    return JSONResponse({"treffer": schlank, "gesamt": erg.get("gesamt", 0),
                         "fehler": erg.get("fehler")})


def _album_id_zu_pfad(pfad):
    """Album-Id zu einem Dateipfad, fuer das Cover eines Suchtreffers."""
    import sqlite3 as _s
    rel = merkliste.relativ(pfad)
    if not rel:
        return None
    try:
        con = _s.connect(os.environ.get(
            "LINER_DB", os.path.join(DATENDIR, "liner.db")), timeout=5)
        try:
            r = con.execute("SELECT album_id FROM track WHERE pfad=? LIMIT 1",
                            (rel,)).fetchone()
            return r[0] if r else None
        finally:
            con.close()
    except Exception as e:
        log.debug("Album-Id nicht ermittelbar: %s", e)
        return None


@app.post("/api/bibliothek/abspielen")
async def api_bib_abspielen(request: Request):
    """Album abspielen (Warteschlange ersetzen) oder Titel anhaengen.

    Rumpf: {"album": "...", "artist": "...", "titel": "..." (optional),
            "modus": "ersetzen" | "anhaengen"}

    Aufgeloest wird beim MinimServer, nicht aus einer mitgeschickten URL."""
    if not STEUERUNG_AN:
        return _steuerung_aus()
    try:
        daten = await request.json()
    except Exception:
        daten = {}
    album = (daten or {}).get("album")
    artist = (daten or {}).get("artist")
    titel = (daten or {}).get("titel")
    modus = (daten or {}).get("modus") or "ersetzen"
    if not album and not titel:
        return JSONResponse({"ok": False, "grund": "Album oder Titel fehlt."},
                            status_code=400)

    if titel:
        eintrag = await asyncio.to_thread(bibliothek.titel_finden, titel,
                                          album, artist)
        posten = [eintrag] if eintrag else []
    else:
        posten = await asyncio.to_thread(bibliothek.album_titel, album, artist)

    posten = [x for x in posten if x and x.get("res_url")]
    if not posten:
        return JSONResponse({"ok": False, "grund": "In der Bibliothek nicht "
                             "auflösbar."}, status_code=404)

    ergebnis = await asyncio.to_thread(
        _warteschlange_fuellen, posten, modus == "ersetzen")
    await _nachfassen()
    return JSONResponse(ergebnis)


@app.get("/api/verlauf")
async def api_verlauf(grenze: int = 15):
    eintraege = await asyncio.to_thread(verlauf_lesen, min(max(grenze, 1), 60))
    return JSONResponse({"eintraege": eintraege})


# ---------------------------------------------------------------- Hören lernen
@app.get("/api/hoerenlernen/stand")
async def api_hl_stand():
    """Was ist eingelesen? Für die Übersicht und zur Fehlersuche."""
    return JSONResponse(hoerenlernen.stand())


@app.post("/api/hoerenlernen/neu-einlesen")
async def api_hl_neu():
    """Nach einem git pull aufrufen - das Pull-Skript tut das selbst."""
    anzahl = await asyncio.to_thread(hoerenlernen.einlesen)
    await anstossen("hoerenlernen")
    return JSONResponse({"dateien": anzahl, "fehler": hoerenlernen.fehler})


@app.get("/api/hoerenlernen/album")
async def api_hl_album(artist: str = "", album: str = "", werk: str = ""):
    """Alles zu einem Album: Hintergrundtexte, Hinweise, Einspielungen."""
    eintrag, wie = hoerenlernen.finden(artist=artist, album=album, werk=werk)
    if not eintrag:
        return JSONResponse({"gefunden": False})
    aufnahme = eintrag.aufnahme_fuer(artist, album)
    return JSONResponse({
        "gefunden": True,
        "zugeordnet_ueber": wie,
        "artist": eintrag.artist, "album": eintrag.album, "werk": eintrag.werk,
        "quelle": eintrag.quelle,
        "lizenz": "CC BY-NC-SA 4.0",
        "repo": "https://github.com/hoerenlernen/hoeren-lernen",
        "worauf_achten": eintrag.worauf_achten,
        "abschnitte": eintrag.abschnitte(),
        "besetzung": {"art": (eintrag.besetzung or {}).get("art") or "",
                      "gruppen": eintrag.instrumentenliste()},
        "hinweise": [dict(h, zeit_s=eintrag.zeit_in(h, aufnahme)[0],
                          nr=eintrag.tracknummer_in(h, aufnahme))
                     for h in eintrag.hinweise],
        "aufnahme": (aufnahme or {}).get("kennung"),
        "einspielungen": await asyncio.to_thread(andere_einspielungen, eintrag),
    })


@app.post("/api/hoerenlernen/merken")
async def api_hl_merken(text: str = "", typ: str = "", instrumente: str = ""):
    """„Hinweis hier" - Stelle festhalten, Text kann später dazu.

    Geschrieben wird NICHT ins Repo, sondern neben die Liner-Datenbank.
    Ein Dienst, der in ein Git-Arbeitsverzeichnis schreibt, bringt beim
    nächsten `git pull --ff-only` Konflikte; und die Lizenzpflicht
    (Namensnennung) bleibt so dort, wo sie hingehört: beim Menschen,
    der den Block ins Repo übernimmt.

    Die Zeit kommt vom Server, nicht vom Client - sonst weicht sie um
    die Laufzeit der Anfrage ab.
    """
    d = zustand["daten"] or {}
    if not d.get("album"):
        return JSONResponse({"fehler": "nichts läuft"}, status_code=409)

    position = (d.get("zeit") or {}).get("position_s") or 0
    # Auf fünf Sekunden abrunden: ein Hinweis ist nie sekundengenau
    # gemeint, und krumme Zeiten sehen im Repo unschön aus.
    position = int(position) // 5 * 5
    try:
        nr = int(str(d.get("tracknummer") or "0").lstrip("0") or 0)
    except (TypeError, ValueError):
        nr = 0

    ziel_dir = os.path.join(DATENDIR, "hinweis-entwuerfe")
    os.makedirs(ziel_dir, exist_ok=True)
    name = re.sub(r"[^\w\-. ]", "_", "%s - %s" % (
        d.get("artist") or "?", d.get("album") or "?"))[:120]
    pfad = os.path.join(ziel_dir, name + ".md")

    neu_datei = not os.path.exists(pfad)
    zeilen = []
    if neu_datei:
        zeilen += [
            "# Entwurf, erzeugt von Liner beim Hören.",
            "# Album:  %s" % (d.get("album") or ""),
            "# Künstler: %s" % (d.get("artist") or ""),
            "# Diese Bloecke in die passende *_Hoeren.md im Repo",
            "# hoeren-lernen uebernehmen, Text ergaenzen, dann committen.",
            "",
            "hinweise:",
        ]
    kennung = "m%s" % time.strftime("%H%M%S")
    zeilen += [
        "  - id: %s" % kennung,
        "    track: \"%s\"" % (d.get("titel") or "").replace('"', "'"),
        "    nr: %d" % nr,
        "    zeit: \"%d:%02d\"" % (position // 60, position % 60),
        "    dauer: 12",
    ]
    if typ:
        zeilen.append("    typ: %s" % typ)
    if instrumente:
        zeilen.append("    instrumente: [%s]" % ", ".join(
            '"%s"' % i.strip().replace('"', "") for i in instrumente.split(",") if i.strip()))
    zeilen.append("    text: \"%s\"" % (text or "TEXT FEHLT").replace('"', "'"))

    def schreiben():
        with open(pfad, "a", encoding="utf-8") as f:
            f.write("\n".join(zeilen) + "\n")

    await asyncio.to_thread(schreiben)
    log.info("Hinweis gemerkt: %s %s bei %d:%02d",
             d.get("album"), d.get("titel"), position // 60, position % 60)
    return JSONResponse({
        "datei": os.path.basename(pfad),
        "id": kennung,
        "track": d.get("titel"),
        "nr": nr,
        "zeit": "%d:%02d" % (position // 60, position % 60),
        "hat_text": bool(text),
    })


@app.get("/api/hoerenlernen/entwuerfe")
async def api_hl_entwuerfe(datei: str = ""):
    """Die gesammelten Entwuerfe zum Abholen - als Text zum Kopieren."""
    ziel_dir = os.path.join(DATENDIR, "hinweis-entwuerfe")
    if not os.path.isdir(ziel_dir):
        return JSONResponse({"dateien": []})
    namen = sorted(n for n in os.listdir(ziel_dir) if n.endswith(".md"))
    if not datei:
        return JSONResponse({"dateien": namen})
    if datei not in namen:
        return JSONResponse({"fehler": "unbekannt"}, status_code=404)
    with open(os.path.join(ziel_dir, datei), encoding="utf-8") as f:
        return PlainTextResponse(f.read())


@app.post("/api/hoerenlernen/modus/{modus}")
async def api_hl_modus(modus: str):
    """aus | album | live. Gilt für alle Geräte, wie der Merkzustand."""
    global HL_MODUS
    if modus not in ("aus", "album", "live"):
        return JSONResponse({"fehler": "unbekannter Modus"}, status_code=400)
    HL_MODUS = modus
    await anstossen("hoerenlernen-modus")
    return JSONResponse({"modus": HL_MODUS})


@app.get("/api/version")
async def api_version():
    """Damit ein Client prueft, ob er zum Server passt."""
    return JSONResponse({"api": API_VERSION, "steuerung": STEUERUNG_AN,
                         "navidrome": NAVI_SPIEGELN and navi.eingerichtet})


@app.get("/api/health")
async def api_health():
    return {"ok": True, "abo_sid": getattr(abo, "sid", None),
            "abo_zeit_sid": getattr(abo_zeit, "sid", None),
            "version": zustand["version"], "stand": zustand["stand"],
            "linn": "%s:%d" % (LINN_HOST, LINN_PORT)}


@app.get("/")
async def index():
    p = os.path.join(STATIC, "index.html")
    if os.path.isfile(p):
        return FileResponse(p)
    return HTMLResponse("<h1>Liner Notes</h1><p>static/index.html fehlt.</p>")
