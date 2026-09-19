# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Begrenzungen für LAN-Zugriffe; ersetzt keine Firewall oder Anmeldung."""
import ipaddress
import os
import urllib.parse
import urllib.request

def innerhalb(wurzel, pfad):
    root, target = os.path.realpath(wurzel), os.path.realpath(pfad)
    return target == root or target.startswith(root + os.sep)

def origin_erlaubt(origin, host, extras=""):
    if not origin:
        return True  # native Clients senden keinen Origin-Header
    try:
        u = urllib.parse.urlsplit(origin)
        return (u.scheme in ("http", "https") and not u.username and
                not u.password and u.netloc.lower() == host.lower()) or origin in {
                    x.strip().rstrip("/") for x in extras.split(",") if x.strip()}
    except ValueError:
        return False

def cover_url_erlaubt(url, hosts):
    try:
        u = urllib.parse.urlsplit(url)
        ipaddress.ip_address(u.hostname or "")
        return (u.scheme in ("http", "https") and not u.username and
                not u.password and u.hostname in hosts and u.port != 0)
    except ValueError:
        return False

class KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def cover_laden(url):
    # Keine Weiterleitungen oder Umgebungs-Proxies: erlaubte Geräte exakt erreichen.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), KeineWeiterleitung())
    with opener.open(url, timeout=8) as response:
        typ = response.headers.get_content_type()
        if typ not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValueError("kein unterstütztes Rasterbild")
        daten = response.read(8 * 1024 * 1024 + 1)
        if len(daten) > 8 * 1024 * 1024:
            raise ValueError("Cover zu groß")
        return daten, typ
