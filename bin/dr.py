#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""
dr.py – Dynamic Range nach der TT-DR-Methode (Pleasurize Music Foundation).

WARUM EINE EIGENE UMSETZUNG
===========================
`dr14_t.meter` ist die Referenz, aber nicht mehr beziehbar: weder in Debian
noch auf PyPI (Stand 2026-09-17: "No matching distribution found"). Das
Projekt wird seit Jahren nicht gepflegt und setzt sox voraus, das hier nicht
installiert ist.

`musik_master.py dr` taugt fuer diesen Zweck nicht: es liest ffmpegs
`ebur128`-LRA und nur den ersten Track je Album. LRA ist die Verteilung der
Lautheit ueber die Zeit (in LU), DR ist Spitze minus Mittelwert der lautesten
Passagen (in dB) - beides korreliert schwach, ist aber nicht dasselbe.

Deshalb hier die Methode direkt, mit ffmpeg zum Dekodieren und numpy zum
Rechnen. Beides ist vorhanden, es kommt keine Abhaengigkeit hinzu, und es
wird nichts in die Musikordner geschrieben.

DIE METHODE
===========
Je Kanal:
  1. Audio in Bloecke von 3 Sekunden teilen.
  2. Pro Block RMS und Spitzenwert bestimmen. Das RMS traegt den Faktor 2
     (`sqrt(2 * mean(x**2))`) - eine Eigenheit der TT-Definition, die auf
     Vollaussteuerung eines Sinus normiert.
  3. Bloecke nach RMS absteigend sortieren, die lautesten 20 % nehmen
     (mindestens einen).
  4. Aus diesen das quadratische Mittel bilden.
  5. Als Spitzenwert den ZWEITHOECHSTEN Blockspitzenwert verwenden, nicht den
     hoechsten - ein einzelner Knackser soll das Ergebnis nicht bestimmen.
  6. DR = 20*log10(Spitze / RMS).
Das Albumergebnis ist der ueber die Kanaele und Tracks gemittelte Wert,
kaufmaennisch gerundet - so weist es auch die Dynamic Range Database aus.

NUR LESEND. Ergebnisse ausschliesslich in die SQLite-Datenbank.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pathlib

# Konfiguration laden, wenn dieses Werkzeug von Hand laeuft: der Dienst
# bekommt seine Einstellungen von systemd, ein Aufruf aus der Shell nicht.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
try:
    import umgebung as _umgebung
    _umgebung.laden()
except Exception:
    pass


BLOCK_S = 3.0

# Vor jeden externen Aufruf gesetzt, sofern die Werkzeuge vorhanden sind.
# nice/ionice direkt am Aufruf statt nur vererbt: das Dekodieren ist der
# teuerste Vorgang im Projekt und darf die Wiedergabe ueber MinimServer nie
# stoeren. ionice -c3 ist die Leerlaufklasse - Plattenzugriffe nur dann, wenn
# sonst niemand liest.
VORSPANN = []
for _kandidat in (["nice", "-n", "19"], ["ionice", "-c3"]):
    if shutil.which(_kandidat[0]):
        VORSPANN = VORSPANN + _kandidat



def pcm_lesen(pfad, ziel_sr=None):
    """Dekodiert eine Audiodatei ueber ffmpeg zu float32-PCM.
    Rueckgabe: (kanaele, samplerate) mit kanaele als Liste von Arrays."""
    # ACHTUNG: ffprobe gibt bei `-of csv` die Felder in der Reihenfolge des
    # Streams aus, NICHT in der Reihenfolge der Anfrage. Eine CSV-Zeile
    # "96000,2" laesst sich nicht zuverlaessig zuordnen - genau daran ist die
    # erste Fassung gescheitert (96000 "Kanaele", Samplerate 2). Deshalb JSON
    # mit benannten Feldern.
    sondieren = subprocess.run(
        VORSPANN + ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels,sample_rate",
         "-of", "json", pfad],
        capture_output=True, text=True, timeout=60)
    try:
        strom = json.loads(sondieren.stdout)["streams"][0]
        kanaele = int(strom["channels"])
        sr = int(strom["sample_rate"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        raise RuntimeError("ffprobe lieferte keine brauchbare Streaminfo: %s" % e)
    if not (1 <= kanaele <= 16):
        raise RuntimeError("unplausible Kanalzahl: %d" % kanaele)
    if not (8000 <= sr <= 768000):
        raise RuntimeError("unplausible Samplerate: %d" % sr)

    befehl = VORSPANN + ["ffmpeg", "-v", "error", "-i", pfad, "-map", "0:a:0",
                         "-f", "f32le", "-acodec", "pcm_f32le"]
    if ziel_sr:
        befehl += ["-ar", str(ziel_sr)]
        sr = ziel_sr
    befehl += ["-"]
    p = subprocess.run(befehl, capture_output=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: %s" % p.stderr.decode("utf-8", "replace")[:200])
    daten = np.frombuffer(p.stdout, dtype="<f4")
    if kanaele > 1:
        nutzbar = (len(daten) // kanaele) * kanaele
        daten = daten[:nutzbar].reshape(-1, kanaele)
        return [daten[:, c] for c in range(kanaele)], sr
    return [daten], sr


def dr_kanal(x, sr):
    """DR eines einzelnen Kanals nach der TT-Methode."""
    block = int(round(BLOCK_S * sr))
    if block <= 0 or len(x) < block:
        return None
    anzahl = len(x) // block
    if anzahl < 1:
        return None
    m = x[:anzahl * block].reshape(anzahl, block)

    rms = np.sqrt(2.0 * np.mean(np.square(m, dtype=np.float64), axis=1))
    peak = np.max(np.abs(m), axis=1)

    n_oben = max(1, int(round(0.2 * anzahl)))
    idx = np.argsort(rms)[::-1][:n_oben]
    rms_oben = float(np.sqrt(np.mean(np.square(rms[idx]))))

    peak_sortiert = np.sort(peak)[::-1]
    # zweithoechster Spitzenwert; bei nur einem Block bleibt dieser
    peak_zweit = float(peak_sortiert[1] if len(peak_sortiert) > 1 else peak_sortiert[0])

    if rms_oben <= 0 or peak_zweit <= 0:
        return None
    return 20.0 * math.log10(peak_zweit / rms_oben), peak_zweit, rms_oben


def dr_datei(pfad):
    """DR einer Datei: Mittel ueber die Kanaele."""
    kanaele, sr = pcm_lesen(pfad)
    werte, peaks, rmse = [], [], []
    for x in kanaele:
        e = dr_kanal(x, sr)
        if e:
            werte.append(e[0]); peaks.append(e[1]); rmse.append(e[2])
    if not werte:
        return None
    return {"dr": sum(werte) / len(werte),
            "peak_db": 20.0 * math.log10(max(peaks)),
            "rms_db": 20.0 * math.log10(sum(rmse) / len(rmse))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dateien", nargs="+")
    a = ap.parse_args()
    for f in a.dateien:
        try:
            e = dr_datei(f)
            if e:
                print("DR%-3d  peak %6.2f dB  rms %7.2f dB  %s"
                      % (round(e["dr"]), e["peak_db"], e["rms_db"], os.path.basename(f)))
            else:
                print("----   (zu kurz)  %s" % os.path.basename(f))
        except Exception as ex:
            print("FEHLER %s: %s" % (os.path.basename(f), ex))
