// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  Einstellungen.swift
//  Serveradresse, dauerhaft gespeichert.
//

import Foundation

enum Einstellungen {
    /// Neutraler Standardwert. Die eigene Adresse wird beim ersten Start
    /// unter „Einstellungen" eingetragen und liegt danach in den
    /// UserDefaults des Geräts – nicht im Quelltext.
    ///
    /// Hier steht bewusst KEINE echte Adresse. Auf einem Fernseher ist
    /// „localhost" zwar nie richtig, aber eine Heimnetz-Adresse im Repo
    /// wäre schlechter: Sie gehört zur Anlage, nicht zum Programm. Zwei
    /// Wege führen zur richtigen Adresse:
    ///
    ///   * einmalig im Gerät unter „Einstellungen" eintragen, oder
    ///   * beim Start übergeben:
    ///     `xcrun devicectl device process launch --device <UDID> \
    ///        --terminate-existing org.linernotes.app -- --server http://host:5060`
    ///
    /// Läuft der Server unter HTTPS mit gültigem Zertifikat, braucht es
    /// keine ATS-Ausnahme. Für eine reine HTTP-Adresse im Heimnetz muss
    /// in der Info-Konfiguration eine Ausnahme gesetzt werden.
    static let standardAdresse = URL(string: "http://localhost:5060")!

    private static let schluessel = "liner.serverAdresse"

    static var serverAdresse: String {
        get {
            UserDefaults.standard.string(forKey: schluessel)
                ?? standardAdresse.absoluteString
        }
        set { UserDefaults.standard.set(newValue, forKey: schluessel) }
    }

    /// Übernimmt eine beim Start übergebene Adresse dauerhaft.
    ///
    /// Gelesen wird direkt aus `CommandLine.arguments` – der Umweg über
    /// die Argument-Domäne der UserDefaults hat auf dem Gerät nicht
    /// gegriffen (der Schlüssel enthält einen Punkt, und die Domäne wird
    /// nur für den laufenden Prozess gefüllt).
    ///
    /// Gebraucht nach einer Neuinstallation: Eine geänderte Bundle-ID
    /// bedeutet neue UserDefaults, und eine URL mit der Fernbedienung
    /// einzutippen ist mühsam.
    ///
    ///   xcrun devicectl device process launch --device <id> \\
    ///     --terminate-existing org.linernotes.app -- --server https://host
    static func startargumentUebernehmen() {
        let argumente = CommandLine.arguments
        guard let i = argumente.firstIndex(where: { $0 == "--server" || $0 == "-server" }),
              i + 1 < argumente.count
        else { return }
        let wert = argumente[i + 1].trimmingCharacters(in: .whitespaces)
        guard !wert.isEmpty, let url = URL(string: wert), url.scheme != nil
        else { return }
        UserDefaults.standard.set(wert, forKey: schluessel)
        // Sofort schreiben: stürzt die App später ab, ist die Adresse
        // trotzdem gesichert.
        UserDefaults.standard.synchronize()
    }
}
