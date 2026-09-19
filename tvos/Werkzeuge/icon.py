#!/usr/bin/env python3
"""App-Symbol fuer tvOS zeichnen: drei Ebenen, zwei Groessen.

tvOS legt die Ebenen beim Fokus gegeneinander versetzt uebereinander -
daher gehoert der Untergrund nach hinten, die Schallplatte in die Mitte
und der Schriftzug nach vorn. So entsteht Tiefe statt eines Aufklebers.

Die Rueckebene MUSS deckend sein (kein Alpha), die vorderen duerfen
transparent sein. Genau daran ist die erste Fassung gescheitert: Sie war
schwarz auf schwarz und damit unsichtbar.
"""
from PIL import Image, ImageDraw, ImageFont
import pathlib

BASIS = (pathlib.Path(__file__).resolve().parent.parent / "LinerNotes" /
         "Assets.xcassets" / "App Icon & Top Shelf Image.brandassets")

SCHRIFT = "/System/Library/Fonts/Supplemental/Futura.ttc"

GRUND_OBEN = (26, 23, 19)      # warmes Dunkel, nicht Schwarz - sonst
GRUND_UNTEN = (13, 12, 11)     # verschwindet das Symbol im Hintergrund
GOLD = (198, 160, 78)
GOLD_HELL = (232, 214, 176)
RILLE = (120, 98, 52)


def hinten(b, h):
    """Deckender Untergrund mit sanftem Verlauf und einem Schimmer
    dort, wo vorn die Platte sitzt."""
    bild = Image.new("RGB", (b, h), GRUND_UNTEN)
    d = ImageDraw.Draw(bild)
    for y in range(h):
        t = y / max(1, h - 1)
        farbe = tuple(int(GRUND_OBEN[i] + (GRUND_UNTEN[i] - GRUND_OBEN[i]) * t)
                      for i in range(3))
        d.line([(0, y), (b, y)], fill=farbe)
    # Schimmer als weiche Scheibe
    mx, my = int(b * 0.33), h // 2
    r = int(h * 0.46)
    schimmer = Image.new("L", (b, h), 0)
    ds = ImageDraw.Draw(schimmer)
    for i in range(24):
        rr = r + i * max(1, h // 90)
        ds.ellipse([mx - rr, my - rr, mx + rr, my + rr], outline=max(0, 34 - i))
    bild.paste(Image.new("RGB", (b, h), (60, 50, 34)), (0, 0), schimmer)
    return bild


def mitte(b, h):
    """Die Schallplatte: Rillen und Label, sonst durchsichtig."""
    bild = Image.new("RGBA", (b, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(bild)
    mx, my = int(b * 0.33), h // 2
    aussen = int(h * 0.40)
    strich = max(1, h // 120)
    # Grundscheibe
    d.ellipse([mx - aussen, my - aussen, mx + aussen, my + aussen],
              fill=(22, 20, 17, 255), outline=GOLD + (210,), width=strich * 2)
    # Rillen
    n = 7
    for i in range(1, n + 1):
        r = int(aussen * (0.92 - i * 0.075))
        if r <= 0:
            break
        d.ellipse([mx - r, my - r, mx + r, my + r],
                  outline=RILLE + (190,), width=strich)
    # Label und Loch
    lr = int(aussen * 0.26)
    d.ellipse([mx - lr, my - lr, mx + lr, my + lr], fill=GOLD + (255,))
    hr = max(2, int(aussen * 0.05))
    d.ellipse([mx - hr, my - hr, mx + hr, my + hr], fill=(18, 16, 14, 255))
    return bild


def vorn(b, h):
    """Der Schriftzug. Vorn, damit er beim Fokus voranschwebt."""
    bild = Image.new("RGBA", (b, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(bild)
    gross = ImageFont.truetype(SCHRIFT, int(h * 0.175), index=0)
    klein = ImageFont.truetype(SCHRIFT, int(h * 0.082), index=0)
    x = int(b * 0.575)   # nicht weiter nach rechts:
    # tvOS rundet die Ecken ab, randnahe Schrift wird angeschnitten
    d.text((x, int(h * 0.36)), "LINER", font=gross, fill=GOLD_HELL + (255,),
           anchor="lm")
    d.text((x + int(h * 0.01), int(h * 0.585)), "N O T E S", font=klein,
           fill=GOLD + (235,), anchor="lm")
    return bild


def schreiben(ordner, b, h):
    ziel = BASIS / ordner
    paare = (("Hinten", "hinten.png", hinten(b, h)),
             ("Mitte", "mitte.png", mitte(b, h)),
             ("Vorn", "vorn.png", vorn(b, h)))
    for ebene, name, bild in paare:
        pfad = ziel / ("%s.imagestacklayer" % ebene) / "Content.imageset" / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        bild.save(pfad)
        print("  %5dx%-4d %s" % (b, h, pfad.relative_to(BASIS)))


for ordner, b, h in (("App Icon.imagestacklayer", 400, 240),
                     ("App Icon - App Store.imagestacklayer", 1280, 768)):
    if not (BASIS / ordner).is_dir():
        # nach dem Umbenennen heissen sie .imagestack
        ordner = ordner.replace(".imagestacklayer", ".imagestack")
    schreiben(ordner, b, h)
