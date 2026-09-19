// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  Bausteine.swift
//  Wiederkehrende Bauteile der Oberfläche.
//
//  Gestaltung für 2,5–3 m Abstand: große Schrift, ruhige Flächen, kein
//  Beiwerk. Die Farben folgen der Web-Oberfläche, damit beide Ansichten
//  erkennbar dasselbe Werkzeug sind.
//

import SwiftUI

enum Farben {
    /// Echtes Schwarz. Auf OLED bleiben schwarze Pixel dunkel – das spart
    /// Strom, bringt mehr Kontrast und vermeidet den blauen Schleier, den
    /// ein aufgehelltes Grau auf großen Flächen erzeugt.
    static let grund   = Color.black
    /// Flächen nur minimal abgesetzt, damit sie nicht selbst leuchten.
    static let blatt   = Color(red: 0.043, green: 0.047, blue: 0.055)
    static let linie   = Color(red: 0.149, green: 0.165, blue: 0.192)
    static let text    = Color(red: 0.925, green: 0.914, blue: 0.894)
    static let leise   = Color(red: 0.714, green: 0.737, blue: 0.776)
    static let still   = Color(red: 0.580, green: 0.612, blue: 0.659)
    static let akzent  = Color(red: 0.831, green: 0.714, blue: 0.471)
    static let gruen   = Color(red: 0.498, green: 0.788, blue: 0.541)
    static let orange  = Color(red: 0.941, green: 0.698, blue: 0.451)
    static let rot     = Color(red: 0.941, green: 0.561, blue: 0.537)
    static let herz    = Color(red: 0.886, green: 0.337, blue: 0.361)
}

/// Zeitangabe als m:ss bzw. h:mm:ss.
func zeitText(_ sekunden: Double?) -> String {
    guard let s = sekunden, s.isFinite, s >= 0 else { return "" }
    let ganz = Int(s.rounded())
    let (st, mi, se) = (ganz / 3600, (ganz % 3600) / 60, ganz % 60)
    return st > 0 ? String(format: "%d:%02d:%02d", st, mi, se)
                  : String(format: "%d:%02d", mi, se)
}

/// Eine Plakette wie im Web – Format, Bitperfekt, DR.
struct Plakette: View {
    var text: String
    var farbe: Color = Farben.leise
    var rahmen: Color = Farben.linie
    var hintergrund: Color = .clear

    var body: some View {
        Text(text)
            .font(.system(size: 26, weight: .medium))
            .foregroundStyle(farbe)
            .padding(.horizontal, 20).padding(.vertical, 10)
            .background(hintergrund)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(rahmen, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

/// Cover mit Platzhalter. Der Platz bleibt reserviert, damit das Layout
/// beim Nachladen nicht springt.
struct CoverBild: View {
    var url: URL?
    var kante: CGFloat

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8).fill(Farben.blatt)
            if let url {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let bild):
                        bild.resizable().aspectRatio(contentMode: .fill)
                    case .failure:
                        Image(systemName: "music.note")
                            .font(.system(size: kante * 0.25))
                            .foregroundStyle(Farben.still)
                    default:
                        ProgressView().tint(Farben.still)
                    }
                }
            } else {
                Image(systemName: "music.note")
                    .font(.system(size: kante * 0.25))
                    .foregroundStyle(Farben.still)
            }
        }
        .frame(width: kante, height: kante)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        // Ohne diese Kennung behält SwiftUI die bestehende AsyncImage-View
        // bei, wenn sich nur die Adresse ändert – beim Albumwechsel blieb
        // dadurch das vorige Cover stehen (beobachtet: Kleiber blieb
        // sichtbar, nachdem über die Suche Pink Floyd gestartet wurde).
        .id(url?.absoluteString ?? "leer")
    }
}

/// Fortschrittsbalken ohne Bedienfunktion – gesprungen wird über die
/// Fernbedienung, nicht durch Zielen mit einem Cursor.
struct Fortschritt: View {
    var position: Double
    var dauer: Double?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            GeometryReader { raum in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.12))
                    Capsule().fill(Farben.akzent)
                        .frame(width: max(0, raum.size.width * anteil))
                }
            }
            .frame(height: 8)
            HStack {
                Text(zeitText(position))
                Spacer()
                if let dauer, dauer > 0 { Text(zeitText(dauer)) }
            }
            .font(.system(size: 24, design: .monospaced))
            .foregroundStyle(Farben.still)
        }
    }

    private var anteil: Double {
        guard let d = dauer, d > 0 else { return 0 }
        return min(1, max(0, position / d))
    }
}

/// Eine ruhige Zeile für Hinweise und Fehler.
struct Hinweisleiste: View {
    var text: String
    var farbe: Color = Farben.orange

    var body: some View {
        Text(text)
            .font(.system(size: 26))
            .foregroundStyle(farbe)
            .padding(.horizontal, 24).padding(.vertical, 14)
            .background(Farben.blatt)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(farbe.opacity(0.5), lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}

/// Stil für Listenzeilen auf dem Fernseher.
///
/// Auf tvOS steuert man mit dem Fokus, nicht mit einem Zeiger – wo er
/// steht, MUSS sichtbar sein. `.buttonStyle(.plain)` zeigt gar nichts,
/// `.card` erzeugt eine große Karte, die für eine Titelzeile zu wuchtig
/// ist. Darum ein eigener Stil: heller Grund, Akzentrand und eine leichte
/// Vergrößerung, die aus drei Metern noch wahrnehmbar ist.
struct ZeilenStil: ButtonStyle {
    @Environment(\.isFocused) private var fokussiert: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.vertical, 10)
            .padding(.horizontal, 20)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(fokussiert ? Color.white.opacity(0.14) : Color.clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(fokussiert ? Farben.akzent : Color.clear, lineWidth: 2)
            )
            .scaleEffect(fokussiert ? 1.02 : 1.0)
            .animation(.easeOut(duration: 0.15), value: fokussiert)
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// Runder Knopf für einzelne Zeichen (Herz, Lautstärke).
struct ZeichenStil: ButtonStyle {
    @Environment(\.isFocused) private var fokussiert: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(12)
            .background(Circle().fill(fokussiert ? Color.white.opacity(0.18) : .clear))
            .overlay(Circle().stroke(fokussiert ? Farben.akzent : .clear, lineWidth: 2))
            .scaleEffect(fokussiert ? 1.08 : 1.0)
            .animation(.easeOut(duration: 0.15), value: fokussiert)
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// Eine Titelzeile mit sichtbarem Fokus und Play-Hinweis.
///
/// Auf tvOS ist einer Zeile nicht anzusehen, ob sie etwas tut. Beim Fokus
/// erscheint darum links ein Play-Zeichen – es sagt: hier drücken spielt
/// diesen Titel.
struct TitelZeile: View {
    var track: Track
    var laeuft: Bool
    var gemerkt: Bool
    var tun: () -> Void

    @FocusState private var fokussiert: Bool

    var body: some View {
        Button(action: tun) {
            HStack(spacing: 18) {
                ZStack {
                    if fokussiert {
                        Image(systemName: "play.fill")
                            .font(.system(size: 22))
                            .foregroundStyle(Farben.akzent)
                    } else if laeuft {
                        Image(systemName: "speaker.wave.2.fill")
                            .font(.system(size: 20))
                            .foregroundStyle(Farben.akzent)
                    } else {
                        Text(track.nr.map(String.init) ?? "")
                            .font(.system(size: 24, design: .monospaced))
                            .foregroundStyle(Farben.still)
                    }
                }
                .frame(width: 46)

                Text(track.beschriftung)
                    .font(.system(size: 28, design: .serif))
                    .foregroundStyle(laeuft ? Farben.akzent : Farben.text)
                    .lineLimit(1)

                Spacer(minLength: 12)

                if gemerkt {
                    Image(systemName: "heart.fill")
                        .font(.system(size: 20))
                        .foregroundStyle(Farben.herz)
                }
                if let dr = track.dr {
                    Text("DR\(Int(dr.rounded()))")
                        .font(.system(size: 20, design: .monospaced))
                        .foregroundStyle(Farben.still)
                }
                Text(zeitText(track.dauerS))
                    .font(.system(size: 22, design: .monospaced))
                    .foregroundStyle(Farben.still)
                    .frame(width: 92, alignment: .trailing)
            }
            .padding(.vertical, 9)
            .padding(.horizontal, 16)
        }
        .buttonStyle(.plain)
        .focused($fokussiert)
        .background(
            RoundedRectangle(cornerRadius: 8)
                // Auf OLED keine hellen Flächen: der Fokus wird über einen
                // Rahmen und die Akzentfarbe gezeigt, nicht über einen
                // aufgehellten Block.
                .fill(fokussiert ? Color.white.opacity(0.07)
                      : (laeuft ? Color.white.opacity(0.03) : .clear))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(fokussiert ? Farben.akzent : .clear, lineWidth: 2)
        )
        .animation(.easeOut(duration: 0.15), value: fokussiert)
    }
}
