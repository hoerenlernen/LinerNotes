# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Initialisiert den Sidecar-Speicher, ohne Musikdateien zu öffnen."""
import pathlib
import sqlite3

def vorbereiten(pfad):
    ziel = pathlib.Path(pfad).expanduser()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ziel, timeout=30) as con:
        con.executescript(pathlib.Path(__file__).with_name("schema.sql").read_text())
