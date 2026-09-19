// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  SucheAnsicht.swift
//  Suche über die Bibliothek und „Zuletzt gehört".
//
//  Bewusst schmal: ein Suchfeld, flache Ergebnisse, zwei Aktionen je
//  Treffer. Keine Kacheln, keine Genre- oder Künstlerseiten – dafür
//  bleibt Kazoo.
//

import SwiftUI

struct SucheAnsicht: View {
    @EnvironmentObject var zustand: Zustand
    @State private var text = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 34) {
                Text("In der Bibliothek suchen")
                    .font(.system(size: 46, weight: .bold, design: .serif))
                    .foregroundStyle(Farben.text)

                // Auf tvOS öffnet ein TextField die Bildschirmtastatur.
                HStack(spacing: 16) {
                    TextField("Künstler, Album oder Titel", text: $text)
                        .textFieldStyle(.plain)
                        .font(.system(size: 32))
                        .padding(20)
                        .background(Farben.blatt)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .onChange(of: text) { _, neu in zustand.suchen(neu) }

                    // Zurücksetzen. Auf tvOS hat ein Textfeld keine
                    // eingebaute Löschtaste, und ein Suchwort Zeichen für
                    // Zeichen mit der Fernbedienung zu entfernen ist mühsam.
                    if !text.isEmpty {
                        Button {
                            text = ""
                            zustand.suchen("")
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 34))
                                .foregroundStyle(Farben.still)
                                .frame(width: 50, height: 50)
                        }
                        .buttonStyle(ZeichenStil())
                        .accessibilityLabel("Suche löschen")
                    }
                }

                if !zustand.suchhinweis.isEmpty {
                    Text(zustand.suchhinweis)
                        .font(.system(size: 24))
                        .foregroundStyle(Farben.still)
                }

                ForEach(zustand.suchtreffer) { treffer in
                    trefferZeile(treffer)
                }

                if !zustand.verlauf.isEmpty {
                    Text("Zuletzt gehört")
                        .font(.system(size: 40, weight: .bold, design: .serif))
                        .foregroundStyle(Farben.text)
                        .padding(.top, 20)
                    ForEach(zustand.verlauf) { v in verlaufZeile(v) }
                }
            }
            .padding(.horizontal, 80).padding(.vertical, 50)
        }
        .background(Farben.grund)
    }

    private func trefferZeile(_ t: Suchtreffer) -> some View {
        HStack(spacing: 24) {
            CoverBild(url: t.albumId.map {
                URL(string: "\(zustand.basis.absoluteString)/api/cover/\($0)?groesse=200")!
            }, kante: 120)

            VStack(alignment: .leading, spacing: 6) {
                Text(t.album ?? "—")
                    .font(.system(size: 30, design: .serif))
                    .foregroundStyle(Farben.text).lineLimit(1)
                Text(t.artist ?? "")
                    .font(.system(size: 24))
                    .foregroundStyle(Farben.leise).lineLimit(1)
                Text("\(t.anzahl ?? 0) Titel")
                    .font(.system(size: 20))
                    .foregroundStyle(Farben.still)
            }

            Spacer()

            if zustand.steuerungAn {
                Button("Abspielen") {
                    zustand.abspielen(album: t.album, artist: t.artist, ersetzen: true)
                }
                Button("Anhängen") {
                    zustand.abspielen(album: t.album, artist: t.artist, ersetzen: false)
                }
            }
        }
        .padding(.vertical, 12)
        .font(.system(size: 24))
    }

    private func verlaufZeile(_ v: Verlaufseintrag) -> some View {
        HStack(spacing: 24) {
            CoverBild(url: v.albumId.map {
                URL(string: "\(zustand.basis.absoluteString)/api/cover/\($0)?groesse=200")!
            }, kante: 100)
            VStack(alignment: .leading, spacing: 4) {
                Text(v.album ?? "—")
                    .font(.system(size: 28, design: .serif))
                    .foregroundStyle(Farben.text).lineLimit(1)
                Text(v.artist ?? "")
                    .font(.system(size: 22))
                    .foregroundStyle(Farben.leise).lineLimit(1)
            }
            Spacer()
            if zustand.steuerungAn {
                Button("Abspielen") {
                    zustand.abspielen(album: v.album, artist: v.artist, ersetzen: true)
                }
                .font(.system(size: 24))
            }
        }
        .padding(.vertical, 10)
    }
}
