// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  EinstellungenAnsicht.swift
//  Serveradresse und Zustand der Verbindung.
//

import SwiftUI

struct EinstellungenAnsicht: View {
    @EnvironmentObject var zustand: Zustand
    @State private var adresse = Einstellungen.serverAdresse
    @State private var version: Versionsantwort?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 30) {
                Text("Einstellungen")
                    .font(.system(size: 46, weight: .bold, design: .serif))
                    .foregroundStyle(Farben.text)

                VStack(alignment: .leading, spacing: 12) {
                    Text("SERVERADRESSE")
                        .font(.system(size: 22, weight: .medium))
                        .foregroundStyle(Farben.still)
                    TextField("http://server:5060", text: $adresse)
                        .textFieldStyle(.plain)
                        .font(.system(size: 30))
                        .padding(18)
                        .background(Farben.blatt)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    Button("Übernehmen") {
                        if let url = URL(string: adresse.trimmingCharacters(in: .whitespaces)),
                           url.scheme != nil {
                            zustand.basis = url
                            Task { await versionLaden() }
                        } else {
                            zustand.zeige("Die Adresse ist ungültig.")
                        }
                    }
                    .font(.system(size: 26))
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("VERBINDUNG")
                        .font(.system(size: 22, weight: .medium))
                        .foregroundStyle(Farben.still)
                    Text(zustand.verbunden ? "verbunden" : "getrennt – neuer Versuch läuft")
                        .font(.system(size: 28))
                        .foregroundStyle(zustand.verbunden ? Farben.gruen : Farben.orange)
                    if let v = version {
                        Text("API-Version \(v.api ?? 0) · Steuerung \(v.steuerung == true ? "an" : "aus") · Navidrome \(v.navidrome == true ? "an" : "aus")")
                            .font(.system(size: 24))
                            .foregroundStyle(Farben.leise)
                        if (v.api ?? 0) != 1 {
                            Hinweisleiste(text: "Diese App erwartet API-Version 1. Der Server meldet \(v.api ?? 0) – Anzeige möglicherweise unvollständig.")
                        }
                    }
                }
            }
            .padding(.horizontal, 80).padding(.vertical, 50)
        }
        .background(Farben.grund)
        .task { await versionLaden() }
    }

    private func versionLaden() async {
        version = try? await zustand.client.hole("api/version", Versionsantwort.self)
    }
}
