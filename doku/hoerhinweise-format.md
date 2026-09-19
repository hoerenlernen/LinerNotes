# Hörhinweise: Dateiformat

Eine Datei je Album oder Werk, Markdown mit YAML-Kopf. Sie liegt im Repo
`hoeren-lernen` neben dem Inhalt, aus dem sie stammt, und heißt wie er:
`Soul/Whats_Going_On_Content.tex` → `Soul/Whats_Going_On_Hoeren.md`.

Liner liest diese Dateien nur. Geschrieben werden sie von Hand — oder als
Entwurf erzeugt (`bin/hoerhinweis-entwurf.py`) und dann redigiert.

---

## Kopf: Identität

```yaml
artist: "Marvin Gaye"          # Pflicht
album: "What's Going On"       # Pflicht
genre: Soul
mbid: "…"                      # optional; wenn gesetzt, hat sie Vorrang
werk: "Sinfonie Nr. 9 d-moll op. 125"   # optional, für Klassik
quelle: "Soul/Whats_Going_On_Content.tex"
```

Die `.tex`-Dateien nennen nirgends maschinenlesbar, um welches Album es
geht. Ohne diesen Kopf kann Liner nichts zuordnen.

**Zuordnung in dieser Reihenfolge:** `mbid` → `aufnahmen[].mbid` →
`artist` + `album` normalisiert → `werk` gegen die Tags `WORK` und
`ALBUM`.

---

## Aufnahmen: dasselbe Werk, andere Zeiten

Der Grund, warum dieser Block existiert, an einem gemessenen Beispiel aus
der Bibliothek:

| Einspielung | Satz I | Finale |
|---|---|---|
| Furtwängler 1951 | 17,8 min | in **8 Tracks** zerlegt |
| Szell | 15,6 min | **1 Track**, 24 min |

Zwei Minuten Unterschied auf einem Satz, und eine völlig andere
Track-Aufteilung. Ein Hinweis bei 10:00 sitzt bei Szell rund 75 Sekunden
früher. Deshalb drei Stufen, von grob nach genau:

```yaml
aufnahmen:
  - kennung: "Furtwängler 1951"
    artist: "Wilhelm Furtwängler"     # zum Wiederfinden in der Bibliothek
    album: "Beethoven: Symphony No. 9 (Furtwangler) (1951)"
    # Stufe 1 – fester Versatz in Sekunden. Für Applaus, Ansagen,
    # unterschiedliche Vorlaufzeit. Nur brauchbar, wenn das Tempo gleich
    # ist.
    versatz: 0
    # Stufe 3 – exakte Zeiten je Hinweis. Das Einzige, was bei Klassik
    # wirklich stimmt. Schlüssel ist die `id` des Hinweises.
    zeiten:
      a1: "0:06"
      a2: "1:24"
    # Zuordnung Satz -> Track, wenn sie abweicht. Furtwänglers Finale
    # liegt auf acht Tracks.
    saetze:
      "IV": [4, 5, 6, 7, 8, 9, 10, 11]

  - kennung: "Szell"
    artist: "George Szell"
    album: "Beethoven: Symphonies Nos. 8 & 9"
    # Stufe 2 – lineare Dehnung. Krücke, wenn exakte Zeiten fehlen:
    # gemessene Satzdauer der Aufnahme geteilt durch die der Referenz.
    # Reicht für die ersten Minuten, driftet danach.
    dehnung: 0.877
    saetze:
      "IV": [4]
```

**Ehrlich dazu:** Stufe 2 ist eine Krücke. Musiker beschleunigen und
verzögern nicht gleichmäßig; nach zehn Minuten liegt eine lineare
Dehnung leicht zehn Sekunden daneben. Für Struktur-Hinweise („jetzt
kommt das Thema wieder") ist das zu ungenau. Der Weg zu Stufe 3 ist der
Knopf **„Hinweis hier"** in der Web-Ansicht: einmal mitlaufen lassen,
an jeder Stelle drücken, fertig sind die Zeiten für diese Einspielung.

Ohne `aufnahmen:`-Block gelten die Zeiten aus `hinweise:` für alles, was
auf Künstler und Album passt. Für Pop und Rock ist das der Normalfall —
dort gibt es meist nur eine Aufnahme.

---

## Besetzung: für die Ensemble-Ansicht

Nur nötig, wenn Hinweise Instrumente nennen. Fehlt der Block, zeigt
Liner keine Ensemble-Ansicht — nicht eine leere.

```yaml
besetzung:
  art: orchester            # orchester | band
  gruppen:
    - name: "Streicher"
      farbe: rostrot
      instrumente: ["Violine I", "Violine II", Viola, Violoncello, Kontrabass]
    - name: "Holz"
      farbe: blau
      instrumente: [Flöte, Oboe, Klarinette, Fagott]
    - name: "Blech"
      farbe: gold
      instrumente: [Horn, Trompete, Posaune]
    - name: "Schlagwerk"
      farbe: violett
      instrumente: [Pauke]
```

Für eine Band:

```yaml
besetzung:
  art: band
  gruppen:
    - name: "Rhythmus"
      instrumente: [Bass, Schlagzeug, Congas]
    - name: "Harmonie"
      instrumente: [Klavier, Gitarre]
    - name: "Bläser"
      instrumente: [Saxophon, Posaune]
    - name: "Gesang"
      instrumente: ["Marvin Gaye", "Background"]
```

Die Namen in `instrumente:` sind zugleich die Schlüssel, auf die sich
`hinweise[].instrumente` bezieht. Schreibweise muss übereinstimmen.

---

## Hinweise

```yaml
hinweise:
  - id: a1                       # stabil, wird von aufnahmen.zeiten benutzt
    track: "I. Allegro ma non troppo"
    nr: 1                        # Tracknummer; mit `bis:` für Medleys
    zeit: "0:00"                 # "mm:ss" oder "mm:ss-mm:ss"
    dauer: 14                    # Sekunden Standzeit (Standard: 12)
    typ: struktur                # struktur | klang | text | kontext
    instrumente: [Kontrabass, Violoncello]
    text: "Ein Summen aus leeren Quinten. Noch keine Tonart, nur ein Raum."
```

**Wohin ein Hinweis gehört:** dorthin, wo etwas passiert. Eine
gleichmäßige Dichte ist ausdrücklich **kein** Ziel — ruhige Passagen
dürfen leer bleiben, und bei einem langsamen Satz ist eine lange Lücke
oft genau richtig. Der Generator listet größere Abstände auf, wertet sie
aber nicht.

Was dagegen gilt: **ein Satz, nicht drei.** Wer liest, hört nicht zu. Die
Vorlage der DG-App macht es vor — eine Zeile, die man im Vorbeigehen
erfasst.

**`typ`** dient dem späteren Filtern:

| Typ | Was gemeint ist |
|---|---|
| `struktur` | Form: Thema, Wiederholung, Übergang, Steigerung |
| `klang` | Klangfarbe, Instrumentierung, Raum, Aufnahme |
| `text` | was gesungen wird und was es bedeutet |
| `kontext` | Entstehung, Zeitgeschichte, Biographisches |

**`dauer`** steuert, wie lange der Hinweis unten stehen bleibt. Kurze
Sätze 8–10 Sekunden, längere 15–20. Liner zeigt die Restzeit als kleinen
Ring, wie in der Vorlage.

---

## Songtexte

Nur sehr kurz zitieren, besser beschreiben. Nicht:

> „Mother, mother, there's too many of you crying. Brother, brother,
> brother, there's far too many of you dying."

Sondern:

> Marvin ruft nacheinander Mutter, Bruder, Vater an — er redet die
> Familie an und meint das Land.

Das ist nicht nur rechtlich sauberer, es ist auch die bessere Lehre: Wer
den Text mitliest, hört ihn nicht.

---

## Fließtext

Unter dem YAML-Kopf, als gewöhnliches Markdown. Jede `##`-Überschrift
wird in Liner ein aufklappbarer Abschnitt unter „Hintergrund" — zum
Lesen **vor** dem Hören, nicht währenddessen.

```markdown
## Der Kontext

1971. Motown …

## Wie es klingen sollte

…
```

---

## Vollständigkeit

Nichts außer `artist`, `album` und einem der Blöcke `worauf_achten`,
`hinweise` oder Fließtext ist Pflicht. Eine Datei ohne Zeitstempel
funktioniert: Dann gibt es eben nur die Album-Hinweise und den
Hintergrund.

## Medleys mit getrennten Tracks

Bei `nr` und `bis` sind die Zeiten relativ zum ersten Track des Medleys.
Der YAML-Kopf muss die Startzeiten aller folgenden Tracks angeben, etwa:

```yaml
medley_grenzen:
  5: {6: 102}
  7: {8: 451}
```

Liner rechnet beim Einlesen auf einzelne Tracknummern und lokale Sekunden um.
Ein Hinweis genau an einer Grenze gehoert zum folgenden Track. Fehlende oder
nicht aufsteigende Grenzen werden als Dateifehler gemeldet. Die Grenzen
gelten fuer die beschriebene Trackaufteilung; andere Ausgaben brauchen eigene
Dateien bzw. entsprechend angepasste Hinweise.
