// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  TexteAnsicht.swift
//  Albumtext, Besetzung und Credits in voller Länge.
//
//  Der eigentliche Zweck des Projekts: Was steht über dieses Album zu
//  lesen? Auf der Hauptseite erscheint davon ein Anriss; hier ist alles,
//  scrollbar mit der Fernbedienung.
//
//  Es wird nichts erfunden und nichts beschönigt: leere Blöcke bleiben
//  weg, und die Überschriften kommen vom Server – der unterscheidet
//  zwischen „Über das Album", „Über den Künstler", „Diskografie" und dem
//  neutralen „Text", wenn die Zuordnung unklar ist.
//
//  FALLSTRICK: SCROLLEN BRAUCHT FOKUS
//  ==================================
//  Auf tvOS steuert die Fokus-Engine das Scrollen. Eine ScrollView, die
//  nur Text enthält, lässt sich deshalb NICHT bewegen – Text ist nicht
//  fokussierbar, die Fernbedienung findet nichts zum Anspringen, und der
//  Inhalt steht fest. (Genau so beobachtet: die Vollansicht ließ sich
//  nicht scrollen.)
//
//  Darum ist jeder Block `.focusable()`. Beim Durchgehen wandert der
//  Fokus von Abschnitt zu Abschnitt und die Ansicht folgt von selbst; der
//  fokussierte Block wird zusätzlich leicht hervorgehoben, damit sichtbar
//  bleibt, wo man ist.
//

import SwiftUI

struct TexteAnsicht: View {
    @EnvironmentObject var zustand: Zustand
    @Environment(\.dismiss) private var schliessen

    private var inhalt: Jetzt { zustand.jetzt }

    /// Prosa und Besetzung getrennt: Besetzung liest sich als Liste,
    /// nicht als Fließtext.
    private var prosa: [Textblock] {
        (inhalt.eigene ?? []).filter { !["personnel", "producer"].contains($0.feld) }
    }
    private var besetzung: [Textblock] {
        (inhalt.eigene ?? []).filter { ["personnel", "producer"].contains($0.feld) }
    }

    /// „Worauf hören" aus dem Repo Hören lernen.
    ///
    /// Steht ganz oben, weil es das ist, was man VOR dem Hören liest.
    /// Die Quellenangabe gehört sichtbar dazu: Das Repo steht unter
    /// CC BY-NC-SA 4.0 und verlangt Namensnennung.
    @ViewBuilder
    private var woraufHoeren: some View {
        if let hl = inhalt.hoerenlernen, let punkte = hl.woraufAchten,
           !punkte.isEmpty {
            abschnitt {
                VStack(alignment: .leading, spacing: 18) {
                    ueberschrift("Worauf hören", marke: nil)
                    ForEach(punkte.indices, id: \.self) { i in
                        HStack(alignment: .firstTextBaseline, spacing: 16) {
                            Text("·")
                                .font(.system(size: 34))
                                .foregroundStyle(Farben.akzent.opacity(0.8))
                            Text(punkte[i])
                                .font(.system(size: 34, design: .serif))
                                .foregroundStyle(.white.opacity(0.92))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let bes = hl.besetzung?.gruppen, !bes.isEmpty {
                        ensemble(bes, aktiv: aktiveInstrumente)
                            .padding(.top, 8)
                    }
                    Text("Hören lernen (" + (hl.lizenz ?? "CC BY-NC-SA 4.0") + ")"
                         + (hl.zugeordnetUeber.map { " · zugeordnet über " + $0 } ?? ""))
                        .font(.system(size: 22))
                        .foregroundStyle(.white.opacity(0.45))
                        .padding(.top, 6)
                }
            }
        }
    }

    /// Welche Instrumente nennt der gerade laufende Hinweis?
    private var aktiveInstrumente: Set<String> {
        Set((inhalt.hoerenlernen?.hinweis?.instrumente ?? [])
            .map { $0.lowercased() })
    }

    /// Die Besetzung, gruppenweise. Aktive Instrumente voll deckend,
    /// die übrigen deutlich abgedunkelt.
    ///
    /// Bewusst ohne Pulsieren, Zoom oder Verschiebung: Bewegung im
    /// Blickfeld zieht die Aufmerksamkeit vom Hören ab. Es ändert sich
    /// allein die Deckkraft, über eine halbe Sekunde.
    @ViewBuilder
    private func ensemble(_ gruppen: [Besetzungsgruppe],
                          aktiv: Set<String>) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(gruppen.indices, id: \.self) { i in
                let g = gruppen[i]
                VStack(alignment: .leading, spacing: 6) {
                    if let name = g.name, !name.isEmpty {
                        Text(name)
                            .font(.system(size: 20, weight: .medium))
                            .foregroundStyle(.white.opacity(0.42))
                    }
                    let instr = g.instrumente ?? []
                    HStack(spacing: 14) {
                        ForEach(instr.indices, id: \.self) { j in
                            let an = aktiv.contains(instr[j].lowercased())
                            Text(instr[j])
                                .font(.system(size: 26))
                                .foregroundStyle(.white.opacity(an ? 1.0 : 0.28))
                                .padding(.horizontal, 14)
                                .padding(.vertical, 5)
                                .overlay(
                                    Capsule().stroke(
                                        an ? Farben.akzent.opacity(0.8)
                                           : .white.opacity(0.14),
                                        lineWidth: 1))
                                .animation(.easeInOut(duration: 0.5), value: an)
                        }
                    }
                }
            }
        }
    }

    /// Umschließt einen Abschnitt fokussierbar, damit die Fernbedienung
    /// ihn anspringen kann und die Ansicht mitscrollt.
    @ViewBuilder
    private func abschnitt<Inhalt: View>(@ViewBuilder _ bau: () -> Inhalt) -> some View {
        AbschnittsRahmen { bau() }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 44) {
                kopf

                woraufHoeren

                ForEach(prosa) { block in
                    abschnitt {
                    VStack(alignment: .leading, spacing: 14) {
                        ueberschrift(block.titel, marke: block.auszug == true ? "Auszug" : nil)
                        Text(block.text)
                            .font(.system(size: 36, design: .serif))
                            .foregroundStyle(Farben.leise)
                            .lineSpacing(14)
                            // Lesezeilen nicht über rund 80 Zeichen.
                            .frame(maxWidth: 1500, alignment: .leading)
                        if let quelle = block.quelle {
                            Text("Quelle: \(quelle)")
                                .font(.system(size: 22))
                                .foregroundStyle(Farben.still)
                        }
                    }
                    }
                }

                ForEach(besetzung) { block in
                    abschnitt {
                    VStack(alignment: .leading, spacing: 14) {
                        ueberschrift(block.titel)
                        if let liste = block.liste, !liste.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(liste.indices, id: \.self) { i in
                                    HStack(alignment: .top, spacing: 20) {
                                        Text(liste[i].rolle ?? "")
                                            .font(.system(size: 28))
                                            .foregroundStyle(Farben.still)
                                            .frame(width: 320, alignment: .leading)
                                        Text(liste[i].name ?? "")
                                            .font(.system(size: 32))
                                            .foregroundStyle(Farben.leise)
                                    }
                                }
                            }
                        } else {
                            Text(block.text)
                                .font(.system(size: 32, design: .serif))
                                .foregroundStyle(Farben.leise)
                                .lineSpacing(12)
                                .frame(maxWidth: 1400, alignment: .leading)
                        }
                    }
                    }
                }

                if let lyrics = inhalt.lyrics, !lyrics.isEmpty {
                    abschnitt {
                        VStack(alignment: .leading, spacing: 14) {
                            ueberschrift("Text zu diesem Titel")
                            Text(lyrics)
                                .font(.system(size: 32, design: .serif))
                                .foregroundStyle(Farben.leise)
                                .lineSpacing(14)
                        }
                    }
                }

                fakten

                if prosa.isEmpty && besetzung.isEmpty && (inhalt.lyrics ?? "").isEmpty {
                    Text("Zu diesem Album stehen keine Texte in den Dateien.")
                        .font(.system(size: 34))
                        .foregroundStyle(Farben.still)
                        .padding(.top, 40)
                }
            }
            .padding(.horizontal, 100)
            .padding(.vertical, 70)
        }
        .background(Farben.grund.ignoresSafeArea())
    }

    private var kopf: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(inhalt.album ?? inhalt.zuletzt?.album ?? "—")
                .font(.system(size: 56, weight: .bold, design: .serif))
                .foregroundStyle(Farben.text)
            if let artist = inhalt.artist ?? inhalt.zuletzt?.artist {
                Text(artist)
                    .font(.system(size: 36, design: .serif))
                    .foregroundStyle(Farben.akzent)
            }
        }
        .padding(.bottom, 10)
    }

    private func ueberschrift(_ text: String, marke: String? = nil) -> some View {
        HStack(spacing: 14) {
            Text(text.uppercased())
                .font(.system(size: 26, weight: .medium))
                .foregroundStyle(Farben.still)
                .tracking(1.5)
            if let marke {
                Text(marke)
                    .font(.system(size: 20))
                    .foregroundStyle(Farben.still)
                    .padding(.horizontal, 10).padding(.vertical, 3)
                    .overlay(RoundedRectangle(cornerRadius: 3)
                        .stroke(Farben.linie, lineWidth: 1))
            }
        }
    }

    /// Pressungsangaben – im Web der Aufklappbereich „Ausgabe".
    private var fakten: some View {
        let alle = inhalt.fakten ?? []
        return Group {
            if !alle.isEmpty {
                AbschnittsRahmen {
                VStack(alignment: .leading, spacing: 14) {
                    ueberschrift("Ausgabe")
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(alle.indices, id: \.self) { i in
                            HStack(alignment: .top, spacing: 20) {
                                Text(alle[i].titel)
                                    .font(.system(size: 26))
                                    .foregroundStyle(Farben.still)
                                    .frame(width: 320, alignment: .leading)
                                Text(alle[i].wert)
                                    .font(.system(size: 30))
                                    .foregroundStyle(Farben.leise)
                                if alle[i].extern == true {
                                    Text("nachgeschlagen")
                                        .font(.system(size: 18))
                                        .foregroundStyle(Farben.still)
                                }
                            }
                        }
                    }
                }
                }
            }
        }
    }
}

/// Ein fokussierbarer Abschnitt.
///
/// Notwendig, weil auf tvOS nur fokussierbare Inhalte gescrollt werden
/// können. Die Hervorhebung ist bewusst zurückhaltend – sie soll zeigen,
/// wo man steht, ohne beim Lesen zu stören.
private struct AbschnittsRahmen<Inhalt: View>: View {
    @ViewBuilder var inhalt: Inhalt
    @FocusState private var fokussiert: Bool

    var body: some View {
        inhalt
            .padding(24)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(fokussiert ? Color.white.opacity(0.06) : .clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(fokussiert ? Farben.linie : .clear, lineWidth: 2)
            )
            .focusable()
            .focused($fokussiert)
            .animation(.easeOut(duration: 0.15), value: fokussiert)
    }
}
