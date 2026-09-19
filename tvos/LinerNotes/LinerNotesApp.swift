// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  LinerNotesApp.swift
//  Einstieg der App.
//

import SwiftUI

@main
struct LinerNotesApp: App {
    @StateObject private var zustand = Zustand()

    init() {
        // Eine beim Start übergebene Serveradresse festschreiben, bevor
        // der Zustand sie liest.
        Einstellungen.startargumentUebernehmen()
    }
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            HauptAnsicht()
                .environmentObject(zustand)
                .preferredColorScheme(.dark)
                .onAppear { zustand.starten() }
                .onChange(of: phase) { _, neu in
                    // Im Hintergrund keine offene Verbindung halten – das
                    // Apple TV schläft sonst mit laufendem Strom ein.
                    if neu == .active { zustand.starten() }
                    else if neu == .background { zustand.beenden() }
                }
        }
    }
}
