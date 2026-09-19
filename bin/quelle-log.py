#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
quelle-log.py – Protokolliert, was der Linn bei einer bestimmten Quelle liefert.

ZWECK
=====
Fuer Quellen ohne Bibliotheksdatei (AirPlay/Net Aux, Radio, Eingaenge) ist
unklar, welche Felder der Player ueberhaupt fuellt. Dieses Werkzeug fragt in
kurzen Abstaenden alle vier Dienste ab und schreibt die Rohdaten mit, solange
es laeuft. Ergebnis geht nach doku/rohdaten-<quelle>.txt und dient als Belegt
fuer HANDOVER.md.

Aufruf (waehrend die Quelle aktiv ist):
    python3 bin/quelle-log.py --sekunden 40

NUR LESEND.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import linn as linn_mod
import pathlib

# Konfiguration laden, wenn dieses Werkzeug von Hand laeuft: der Dienst
# bekommt seine Einstellungen von systemd, ein Aufruf aus der Shell nicht.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
try:
    import umgebung as _umgebung
    _umgebung.laden()
except Exception:
    pass


HOST = os.environ.get("LINN_HOST", None)
PORT = int(os.environ.get("LINN_PORT", "55178"))
UDN = os.environ.get("LINN_UDN", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sekunden", type=int, default=30)
    ap.add_argument("--abstand", type=float, default=5.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    l = linn_mod.Linn(HOST, PORT, UDN)
    q = l.quelle()
    name = (q.get("name") or "unbekannt").lower().replace(" ", "-")
    ziel = a.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "doku", "rohdaten-%s.txt" % name)
    os.makedirs(os.path.dirname(ziel), exist_ok=True)

    zeilen = []
    def schreib(s=""):
        print(s, flush=True)
        zeilen.append(s)

    schreib("Rohdaten des Linn Majik DS-I")
    schreib("erfasst: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    schreib("Quelle:  Index %s · Typ %s · Name %r"
            % (q.get("index"), q.get("typ"), q.get("name")))
    schreib("=" * 72)

    ende = time.time() + a.sekunden
    runde = 0
    while time.time() < ende:
        runde += 1
        schreib()
        schreib("--- Abfrage %d (%s) ---" % (runde, time.strftime("%H:%M:%S")))
        for dienst, ver, aktion in (("Transport", 1, "TransportState"),
                                    ("Product", 1, "SourceIndex"),
                                    ("Time", 1, "Time"),
                                    ("Info", 1, "Details"),
                                    ("Info", 1, "Metatext"),
                                    ("Info", 1, "Track")):
            try:
                felder = l.aufruf(dienst, ver, aktion)
            except Exception as e:
                schreib("  %s/%-14s FEHLER %s" % (dienst, aktion, str(e)[:60]))
                continue
            if not felder:
                schreib("  %s/%-14s (leer)" % (dienst, aktion))
                continue
            for k, v in felder.items():
                v = (v or "").strip()
                if aktion == "Track" and k == "Metadata" and v:
                    import html
                    schreib("  %s/%-14s %s = <%d Zeichen DIDL-Lite>"
                            % (dienst, aktion, k, len(v)))
                    for zeile in html.unescape(v).split("\n"):
                        if zeile.strip():
                            schreib("      %s" % zeile.strip()[:160])
                else:
                    schreib("  %s/%-14s %-12s %s"
                            % (dienst, aktion, k, v[:120] if v else "(leer)"))
        time.sleep(a.abstand)

    with open(ziel, "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n")
    print()
    print("  geschrieben: %s" % ziel)


if __name__ == "__main__":
    main()
