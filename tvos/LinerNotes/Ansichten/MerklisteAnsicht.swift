// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  MerklisteAnsicht.swift
//  Die gemerkten Titel und der Weg in die Warteschlange.
//

import SwiftUI

struct MerklisteAnsicht: View {
    @EnvironmentObject var zustand: Zustand

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                HStack(spacing: 30) {
                    Text("Gemerkte Titel")
                        .font(.system(size: 46, weight: .bold, design: .serif))
                        .foregroundStyle(Farben.text)
                    Spacer()
                    if zustand.steuerungAn && !zustand.merkliste.isEmpty {
                        Button("In die Warteschlange") { zustand.merklisteAbspielen() }
                            .font(.system(size: 26))
                    }
                }

                if zustand.merkliste.isEmpty {
                    Text("Noch nichts gemerkt. Das Herz in der Wiedergabe legt Titel hierher.")
                        .font(.system(size: 28))
                        .foregroundStyle(Farben.still)
                } else {
                    Text("\(zustand.merkliste.count) Titel · auch in Navidrome markiert")
                        .font(.system(size: 22))
                        .foregroundStyle(Farben.still)

                    ForEach(zustand.merkliste) { e in
                        HStack(spacing: 24) {
                            CoverBild(url: e.albumId.map {
                                URL(string: "\(zustand.basis.absoluteString)/api/cover/\($0)?groesse=200")!
                            }, kante: 100)

                            VStack(alignment: .leading, spacing: 4) {
                                Text(e.titel ?? "—")
                                    .font(.system(size: 28, design: .serif))
                                    .foregroundStyle(Farben.text).lineLimit(1)
                                Text([e.artist, e.album].compactMap { $0 }.joined(separator: " · "))
                                    .font(.system(size: 22))
                                    .foregroundStyle(Farben.leise).lineLimit(1)
                            }

                            Spacer()

                            if e.dateiFehlt == true {
                                Text("Datei fehlt")
                                    .font(.system(size: 20))
                                    .foregroundStyle(Farben.orange)
                            }
                            Text(zeitText(e.dauerS))
                                .font(.system(size: 24, design: .monospaced))
                                .foregroundStyle(Farben.still)

                            Button {
                                zustand.merkenUmschalten(pfad: e.pfad)
                            } label: {
                                Image(systemName: "heart.fill")
                                    .foregroundStyle(Farben.herz)
                            }
                            .buttonStyle(ZeichenStil())
                        }
                        .padding(.vertical, 10)
                    }
                }
            }
            .padding(.horizontal, 80).padding(.vertical, 50)
        }
        .background(Farben.grund)
    }
}
