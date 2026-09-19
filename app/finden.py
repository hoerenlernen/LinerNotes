# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
finden.py – Geräte im Netz suchen, statt Adressen im Code zu führen.

WARUM
=====
Vorher standen die Adressen des Players und des Medienservers als
Standardwerte im Quelltext (`PLAYER`, `server:9791`) samt ihren
UPnP-Kennungen. Für ein Repo, das später öffentlich werden könnte, ist das
unnötig: Beide Geräte melden sich per SSDP selbst.

REIHENFOLGE
===========
1. Umgebungsvariablen – wer sie setzt, bestimmt und spart die Suche.
2. SSDP-Suche im Netz.
3. Nichts gefunden: Fehlermeldung, die sagt, was zu setzen wäre.

Gesucht wird nach `urn:av-openhome-org:service:Product:*` (Linn und andere
OpenHome-Player) und `urn:schemas-upnp-org:device:MediaServer:1`
(MinimServer und andere). Die Antwort nennt die `LOCATION` der
Gerätebeschreibung; daraus werden Host, Port und Kennung gelesen.
"""
import logging
import os
import re
import socket
import urllib.parse
import urllib.request

log = logging.getLogger("liner.finden")

SUCHE = ("M-SEARCH * HTTP/1.1\r\n"
         "HOST: 239.255.255.250:1900\r\n"
         'MAN: "ssdp:discover"\r\n'
         "MX: {mx}\r\n"
         "ST: {st}\r\n\r\n")


def _ssdp(suchtyp, sekunden=3):
    """Sammelt die LOCATION-Adressen aller Geräte, die antworten."""
    nachricht = SUCHE.format(mx=sekunden, st=suchtyp).encode()
    orte = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(sekunden + 1)
        s.sendto(nachricht, ("239.255.255.250", 1900))
        while True:
            try:
                daten, _ = s.recvfrom(65535)
            except socket.timeout:
                break
            m = re.search(rb"LOCATION:\s*(\S+)", daten, re.I)
            if m:
                ort = m.group(1).decode("utf-8", "replace")
                if ort not in orte:
                    orte.append(ort)
    except Exception as e:
        log.warning("SSDP-Suche fehlgeschlagen: %s", e)
    finally:
        s.close()
    return orte


def _beschreibung(ort):
    try:
        with urllib.request.urlopen(ort, timeout=6) as f:
            return f.read().decode("utf-8", "replace")
    except Exception as e:
        log.debug("Gerätebeschreibung nicht lesbar (%s): %s", ort, e)
        return ""


def _aus_ort(ort):
    """Host und Port aus der LOCATION-Adresse."""
    t = urllib.parse.urlparse(ort)
    return t.hostname, t.port or 80


def _kennung(xml):
    m = re.search(r"<UDN>\s*uuid:([^<\s]+)", xml, re.I)
    return m.group(1) if m else None


def _name(xml):
    m = re.search(r"<friendlyName>([^<]+)</friendlyName>", xml, re.I)
    return m.group(1).strip() if m else None


def player(host=None, port=None, udn=None, name_enthaelt=None):
    """Findet einen OpenHome-Player.

    Sind Host, Port und Kennung gesetzt, wird nicht gesucht. `name_enthaelt`
    grenzt ein, wenn mehrere Player antworten (z.B. „Wohnzimmer")."""
    host = host or os.environ.get("LINN_HOST") or None
    port = port or os.environ.get("LINN_PORT") or None
    udn = udn or os.environ.get("LINN_UDN") or None
    if host and port and udn:
        return {"host": host, "port": int(port), "udn": udn, "quelle": "Umgebung"}

    filter_name = (name_enthaelt or os.environ.get("LINN_NAME") or "").lower()
    for ort in _ssdp("urn:av-openhome-org:service:Product:1"):
        xml = _beschreibung(ort)
        if "av-openhome-org" not in xml:
            continue
        gname = _name(xml) or ""
        if filter_name and filter_name not in gname.lower():
            continue
        h, p = _aus_ort(ort)
        k = _kennung(xml)
        if h and k:
            log.info("Player gefunden: %s (%s:%s)", gname or "?", h, p)
            return {"host": host or h, "port": int(port or p),
                    "udn": udn or k, "name": gname, "quelle": "SSDP"}
    return None


def medienserver(basis=None, udn=None, name_enthaelt=None):
    """Findet einen UPnP-Medienserver (MinimServer und andere)."""
    basis = basis or os.environ.get("MINIM_BASIS") or None
    udn = udn or os.environ.get("MINIM_UDN") or None
    if basis and udn:
        return {"basis": basis.rstrip("/"), "udn": udn, "quelle": "Umgebung"}

    filter_name = (name_enthaelt or os.environ.get("MINIM_NAME") or "").lower()
    for ort in _ssdp("urn:schemas-upnp-org:device:MediaServer:1"):
        xml = _beschreibung(ort)
        if "ContentDirectory" not in xml:
            continue
        gname = _name(xml) or ""
        if filter_name and filter_name not in gname.lower():
            continue
        h, p = _aus_ort(ort)
        k = _kennung(xml)
        if h and k:
            log.info("Medienserver gefunden: %s (%s:%s)", gname or "?", h, p)
            return {"basis": basis or "http://%s:%d" % (h, p),
                    "udn": udn or k, "name": gname, "quelle": "SSDP"}
    return None


HINWEIS_PLAYER = (
    "Kein OpenHome-Player gefunden. Entweder ist das Gerät aus, oder SSDP "
    "kommt nicht durch. Dann in der Umgebung setzen: LINN_HOST, LINN_PORT, "
    "LINN_UDN (siehe .env.beispiel)."
)
HINWEIS_SERVER = (
    "Kein UPnP-Medienserver gefunden. Alternativ MINIM_BASIS und MINIM_UDN "
    "in der Umgebung setzen (siehe .env.beispiel)."
)
