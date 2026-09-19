// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  TracklisteAnsicht.swift
//  Die Titel des laufenden Albums, mit Werkgruppierung wie im Web.
//
//  Auswahl springt in der Warteschlange zu diesem Titel. Enthält die
//  Warteschlange ihn nicht, sagt der Server das – ungefragt etwas
//  einzureihen wäre eine Überraschung.
//

import SwiftUI

struct TracklisteAnsicht: View {
    @EnvironmentObject var zustand: Zustand

    private var inhalt: Jetzt { zustand.jetzt }
    private var tracks: [Track] { inhalt.tracks ?? inhalt.zuletzt?.tracks ?? [] }
    private var werke: Werke? { inhalt.werke ?? inhalt.zuletzt?.werke }

    var body: some View {
        ScrollViewReader { blick in
            ScrollView {
                VStack(alignment: .leading, spacing: 30) {
                    Text(inhalt.album ?? inhalt.zuletzt?.album ?? "Titel")
                        .font(.system(size: 46, weight: .bold, design: .serif))
                        .foregroundStyle(Farben.text)

                    if tracks.isEmpty {
                        Text("Für diese Quelle gibt es keine Titelliste.")
                            .font(.system(size: 30))
                            .foregroundStyle(Farben.still)
                    } else if let gruppen = werke?.gruppen, !gruppen.isEmpty {
                        ForEach(gruppen) { gruppe in
                            VStack(alignment: .leading, spacing: 14) {
                                if let werk = gruppe.werk {
                                    Text(werk)
                                        .font(.system(size: 28, weight: .medium))
                                        .foregroundStyle(Farben.akzent)
                                        .padding(.top, 10)
                                }
                                ForEach(gruppe.tracks) { t in zeile(t) }
                            }
                        }
                    } else {
                        ForEach(tracks) { t in zeile(t) }
                    }
                }
                .padding(.horizontal, 80).padding(.vertical, 50)
            }
            .background(Farben.grund)
            .onChange(of: inhalt.aktuellerPfad) { _, neu in
                // Den laufenden Titel in den Blick holen.
                if let neu { withAnimation { blick.scrollTo(neu, anchor: .center) } }
            }
        }
    }

    /// Nutzt dieselbe Zeile wie die übrige App: Play-Zeichen beim Fokus,
    /// Lautsprecher beim laufenden Titel, Herz wenn gemerkt. Auf tvOS ist
    /// einer Zeile sonst nicht anzusehen, dass sie etwas tut.
    private func zeile(_ t: Track) -> some View {
        TitelZeile(track: t,
                   laeuft: t.pfad != nil && t.pfad == inhalt.aktuellerPfad,
                   gemerkt: (inhalt.favoriten ?? []).contains(t.pfad ?? "")) {
            zustand.zuTitel(t)
        }
        .id(t.pfad ?? t.id)
    }
}
