# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
umgebung.py – lädt die Konfiguration, wenn ein Skript von Hand läuft.

WARUM
=====
Der Dienst bekommt seine Einstellungen von systemd (EnvironmentFile und
Environment=). Die Werkzeuge in `bin/` laufen aber von Hand oder aus dem
Pflegelauf – dort ist die Umgebung leer, und die neutralen Standardwerte
im Quelltext (`/pfad/zur/musik`) zeigen ins Nichts.

Das ist beim Herausnehmen der privaten Pfade aufgefallen: Eine
Tag-Sicherung lief durch und schrieb eine leere Datei, weil die
Bibliothekswurzel nicht existierte.

Dieses Modul liest darum dieselbe Datei wie systemd, sofern die Werte
nicht schon in der Umgebung stehen. Vorhandene Variablen werden NIE
überschrieben – wer sie setzt, behält das letzte Wort.

Fundort der Datei, in dieser Reihenfolge:
  1. $LINER_ENV
  2. ~/.config/liner/liner.env
  3. .env neben dem Projekt (für eine Arbeitskopie)
"""
import os
import pathlib
import shlex

ORTE = [
    lambda: os.environ.get("LINER_ENV"),
    lambda: os.path.expanduser("~/.config/liner/liner.env"),
    lambda: str(pathlib.Path(__file__).resolve().parent.parent / ".env"),
]


def _lesen(pfad):
    werte = {}
    try:
        for zeile in pathlib.Path(pfad).read_text().splitlines():
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#") or "=" not in zeile:
                continue
            name, _, wert = zeile.partition("=")
            name = name.strip()
            if not name or not name.replace("_", "").isalnum():
                continue
            # Anführungszeichen entfernen wie systemd es tut
            try:
                teile = shlex.split(wert, comments=True)
                werte[name] = teile[0] if teile else ""
            except ValueError:
                werte[name] = wert.strip().strip("\"'")
    except OSError:
        return {}
    return werte


def laden(still=True):
    """Ergänzt os.environ um fehlende Werte. Gibt den Fundort zurück."""
    for ort in ORTE:
        pfad = ort()
        if not pfad or not os.path.isfile(pfad):
            continue
        werte = _lesen(pfad)
        if not werte:
            continue
        neu = 0
        for name, wert in werte.items():
            if name not in os.environ:
                os.environ[name] = wert
                neu += 1
        if not still:
            print("Konfiguration aus %s (%d Werte ergänzt)" % (pfad, neu))
        return pfad
    return None


def musikwurzel():
    """Die Bibliothekswurzel – mit klarer Meldung, wenn sie fehlt."""
    laden()
    wurzel = os.environ.get("LINER_MUSIC_ROOT", "/pfad/zur/musik")
    if not os.path.isdir(wurzel):
        raise SystemExit(
            "Die Musikbibliothek wurde nicht gefunden: %s\n"
            "LINER_MUSIC_ROOT setzen oder in ~/.config/liner/liner.env "
            "eintragen (Vorlage: .env.beispiel)." % wurzel)
    return wurzel


def datenbank():
    """Pfad der Sidecar-Datenbank."""
    laden()
    standard = str(pathlib.Path(__file__).resolve().parent.parent
                   / "data" / "liner.db")
    return os.environ.get("LINER_DB", standard)
