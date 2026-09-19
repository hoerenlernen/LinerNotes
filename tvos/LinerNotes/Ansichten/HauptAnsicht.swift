// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  HauptAnsicht.swift
//  Rahmen der App: Now Playing als Heimat, alles andere über Reiter.
//
//  SIRI REMOTE
//  ===========
//  Play/Pause-Taste  -> Wiedergabe umschalten (systemweit, funktioniert
//                       auch, wenn der Fokus woanders liegt)
//
//  Wischgesten sind BEWUSST nicht belegt: `onMoveCommand` fängt jede
//  Richtungsbewegung ab, auch die zum Verschieben des Fokus. Ein Wisch
//  zum Leiser-Knopf sprang dadurch gleichzeitig einen Titel zurück. Auf
//  einer Seite mit Bedienelementen gehören die Richtungen der
//  Fokus-Engine; Titel wechselt man über die Knöpfe.
//

import SwiftUI

struct HauptAnsicht: View {
    @EnvironmentObject var zustand: Zustand
    @State private var reiter = HauptAnsicht.startReiter

    /// Startreiter, für Prüfläufe über ein Startargument setzbar:
    /// `xcrun simctl launch <udid> org.linernotes.app -reiter 2`
    /// Ohne Argument beginnt die App immer bei „Es läuft".
    static var startReiter: Int {
        let werte = UserDefaults.standard
        let n = werte.integer(forKey: "reiter")
        return (0...4).contains(n) ? n : 0
    }

    var body: some View {
        ZStack {
            Farben.grund.ignoresSafeArea()

            TabView(selection: $reiter) {
                JetztAnsicht()
                    .tabItem { Text("Es läuft") }
                    .tag(0)

                TracklisteAnsicht()
                    .tabItem { Text("Titel") }
                    .tag(1)

                SucheAnsicht()
                    .tabItem { Text("Suchen") }
                    .tag(2)

                MerklisteAnsicht()
                    .tabItem { Text("Gemerkt") }
                    .tag(3)

                EinstellungenAnsicht()
                    .tabItem { Text("Einstellungen") }
                    .tag(4)
            }
            .opacity(zustand.abgedunkelt ? 0.18 : 1)
            .animation(.easeInOut(duration: 2), value: zustand.abgedunkelt)

            // Meldungen liegen über allem, damit sie auch in Listen sichtbar sind.
            if let meldung = zustand.meldung {
                VStack {
                    Spacer()
                    Hinweisleiste(text: meldung)
                        .padding(.bottom, 60)
                }
                .transition(.opacity)
            }

            // Unten links, nicht oben: oben sitzt die Reiterleiste, und der
            // Hinweis hat sie im Simulator überdeckt.
            if !zustand.verbunden {
                VStack {
                    Spacer()
                    HStack {
                        Hinweisleiste(text: "Keine Verbindung zum Server – neuer Versuch läuft",
                                      farbe: Farben.still)
                            .padding(.leading, 80).padding(.bottom, 50)
                        Spacer()
                    }
                }
            }
        }
        // Die Play/Pause-Taste gilt überall in der App.
        .onPlayPauseCommand { zustand.umschalten() }
        // Nach „Album abspielen" zur Wiedergabe wechseln.
        .onChange(of: zustand.reiterWunsch) { _, neu in
            if let neu {
                reiter = neu
                zustand.reiterWunsch = nil
            }
        }
        .animation(.easeInOut(duration: 0.25), value: zustand.meldung)
    }
}
