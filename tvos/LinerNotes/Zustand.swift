// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  Zustand.swift
//  Der gemeinsame Zustand der App.
//
//  Grundsatz wie im Web: Der angezeigte Zustand kommt IMMER vom Gerät,
//  nie aus einer eigenen Annahme. Es gibt keine optimistische Anzeige –
//  wer in Kazoo drückt, sieht dasselbe Bild wie wer hier drückt. Nach
//  einem eigenen Befehl gibt es lediglich eine kurze Klicksperre, damit
//  zwei schnelle Eingaben nicht zwei Befehle auslösen.
//

import Foundation
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

@MainActor
final class Zustand: ObservableObject {

    // MARK: Veröffentlichter Zustand
    @Published var jetzt = Jetzt()
    @Published var verbunden = false
    @Published var meldung: String?
    @Published var lautstaerke: Lautstaerke?
    @Published var steuerungAn = false
    @Published var kannSpringen = true
    @Published var kannWeiter = true
    @Published var kannZurueck = true
    @Published var merkliste: [Merkeintrag] = []
    @Published var verlauf: [Verlaufseintrag] = []
    @Published var suchtreffer: [Suchtreffer] = []
    @Published var suchhinweis = ""
    @Published var suchtLaeuft = false
    /// Fortlaufend interpolierte Position – der Server meldet nicht jede Sekunde.
    @Published var position: Double = 0
    /// Abdunkeln nach langer Pause (OLED-Schutz).
    @Published var abgedunkelt = false
    /// Wunsch, zu einem bestimmten Reiter zu wechseln. Nach „Album
    /// abspielen" will man sehen, was jetzt läuft – von Hand zurück zu
    /// wechseln ist ein unnötiger Schritt.
    @Published var reiterWunsch: Int?

    // MARK: Intern
    private(set) var client: ApiClient
    private var strom: EreignisStrom
    private var letzteFolge = -1
    private var startId: String?
    private var sperreBis = Date.distantPast
    private var uhr: Timer?
    private var standRef: (position: Double, zeit: Date)?
    private var pausiertSeit: Date?
    private var meldungsAufgabe: Task<Void, Never>?
    private var sucheAufgabe: Task<Void, Never>?

    var basis: URL {
        didSet {
            Einstellungen.serverAdresse = basis.absoluteString
            Task { await client.basisSetzen(basis) }
            strom.basisSetzen(basis)
            Task { await allesLaden() }
        }
    }

    init() {
        let url = URL(string: Einstellungen.serverAdresse) ?? Einstellungen.standardAdresse
        basis = url
        client = ApiClient(basis: url)
        strom = EreignisStrom(basis: url)

        strom.beiMeldung = { [weak self] stand in self?.uebernehmen(stand) }
        strom.beiVerbindung = { [weak self] an in
            guard let self else { return }
            let vorherGetrennt = !self.verbunden
            self.verbunden = an
            // Nach einer Wiederverbindung den Zustand frisch holen: der
            // Strom meldet nur Änderungen, nicht den Ist-Zustand.
            if an && vorherGetrennt { Task { await self.allesLaden() } }
        }
    }

    func starten() {
        strom.starten()
        Task { await allesLaden() }
        uhrStarten()
    }

    func beenden() {
        strom.stoppen()
        uhr?.invalidate()
        uhr = nil
        bildschirmFreigeben()
    }

    // MARK: - Zustandsübernahme

    /// Übernimmt eine Meldung des Servers – mit Schutz gegen überholte
    /// Antworten und gegen eingefrorene Anzeige nach einem Serverneustart.
    private func uebernehmen(_ stand: Jetzt) {
        if let neueId = stand.startId {
            if let alte = startId, alte != neueId {
                // Der Dienst hat neu gestartet: die Folgenummern beginnen
                // wieder bei eins. Ohne Rücksetzen würde ab hier jede
                // Meldung als „veraltet" verworfen.
                letzteFolge = -1
            }
            startId = neueId
        }
        if let folge = stand.folge {
            if folge < letzteFolge { return }
            letzteFolge = folge
        }

        jetzt = stand
        // Sicherung gegen einen hängenden Zwischenzustand: Meldet der
        // Server „gestoppt" ganz ohne Titel und ohne „zuletzt", ist das
        // meist der Moment zwischen Leeren und Füllen der Warteschlange.
        // Nach kurzer Zeit einmal nachsehen, statt auf das nächste
        // Ereignis zu warten.
        if stand.gestoppt && stand.titel == nil && stand.zuletzt == nil {
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                await self?.jetztLaden()
            }
        }
        let z = stand.zeit
        standRef = (z?.positionS ?? 0, Date())
        position = z?.positionS ?? 0
        bildschirmPflegen(stand)
    }

    func allesLaden() async {
        async let a: Void = jetztLaden()
        async let b: Void = steuerstandLaden()
        async let c: Void = merklisteLaden()
        async let d: Void = verlaufLaden()
        _ = await (a, b, c, d)
    }

    func jetztLaden() async {
        do {
            let stand = try await client.hole("api/now", Jetzt.self)
            uebernehmen(stand)
        } catch {
            // Kein Grund für eine Meldung: der Strom versucht es weiter.
        }
    }

    func steuerstandLaden() async {
        do {
            let s = try await client.hole("api/steuerung/stand", SteuerStand.self)
            steuerungAn = s.an ?? false
            lautstaerke = s.lautstaerke
            kannSpringen = s.kannSpringen ?? true
            kannWeiter = s.kannWeiter ?? true
            kannZurueck = s.kannZurueck ?? true
        } catch {
            steuerungAn = false
        }
    }

    func merklisteLaden() async {
        let antwort = try? await client.hole("api/favoriten", MerklisteAntwort.self)
        merkliste = antwort?.eintraege ?? []
    }

    func verlaufLaden() async {
        let antwort = try? await client.hole("api/verlauf", VerlaufAntwort.self)
        verlauf = antwort?.eintraege ?? []
    }

    // MARK: - Befehle

    /// Eine kurze Sperre gegen Doppeleingaben. Keine Zustandssperre – die
    /// Anzeige folgt weiterhin sofort dem Gerät.
    private func gesperrt() -> Bool {
        if Date() < sperreBis { return true }
        sperreBis = Date().addingTimeInterval(0.45)
        return false
    }

    func befehl(_ pfad: String, koerper: [String: Any]? = nil) {
        guard steuerungAn, !gesperrt() else { return }
        Task {
            do {
                let antwort = try await client.sende(pfad, koerper: koerper)
                if antwort.ok == false { zeige(antwort.grund ?? "Nicht ausgeführt.") }
                if let hinweis = antwort.hinweis { zeige(hinweis) }
                if let stand = antwort.stand { lautstaerke = stand }
            } catch let f as ApiFehler {
                zeige(f.errorDescription ?? "Befehl nicht angekommen.")
            } catch {
                zeige("Befehl nicht angekommen.")
            }
        }
    }

    func umschalten()  { befehl("api/steuerung/umschalten") }
    func stoppen()     { befehl("api/steuerung/stop") }
    func weiter()      { guard kannWeiter  else { return }; befehl("api/steuerung/weiter") }
    func zurueck()     { guard kannZurueck else { return }; befehl("api/steuerung/zurueck") }

    func springen(zu sekunde: Double) {
        guard kannSpringen else { zeige("Diese Quelle erlaubt kein Springen."); return }
        befehl("api/steuerung/springen", koerper: ["sekunde": Int(max(0, sekunde))])
    }

    /// Lautstärke. Die Grenzen setzt der Server; hier wird nur ein Schritt
    /// angefordert, nie ein absoluter Sprung.
    func lauter()  { befehl("api/steuerung/lautstaerke/lauter",  koerper: ["schritt": 1]) }
    func leiser()  { befehl("api/steuerung/lautstaerke/leiser",  koerper: ["schritt": 1]) }
    func stumm()   { befehl("api/steuerung/lautstaerke/stumm") }

    func zuTitel(_ track: Track) {
        guard let pfad = track.pfad else { return }
        guard steuerungAn, !gesperrt() else { return }
        Task {
            do {
                _ = try await client.sende("api/steuerung/zu-titel", koerper: ["pfad": pfad])
            } catch let f as ApiFehler {
                zeige(f.errorDescription ?? "Sprung nicht möglich.")
            } catch {
                zeige("Sprung nicht möglich.")
            }
        }
    }

    // MARK: - Merkliste

    var laufenderGemerkt: Bool { jetzt.favorit ?? false }

    func merkenUmschalten(pfad: String? = nil) {
        guard !gesperrt() else { return }
        Task {
            do {
                var koerper: [String: Any] = [:]
                if let pfad { koerper["pfad"] = pfad }
                let antwort = try await client.sende("api/favorit", koerper: koerper)
                if antwort.ok == false {
                    zeige(antwort.grund ?? "Nicht gespeichert.")
                } else {
                    await merklisteLaden()
                    await jetztLaden()
                }
            } catch {
                zeige("Nicht gespeichert.")
            }
        }
    }

    func merklisteAbspielen() {
        guard steuerungAn else { return }
        Task {
            do {
                let antwort = try await client.sende("api/favoriten/abspielen",
                                                     koerper: ["ersetzen": true])
                if antwort.ok == false { zeige(antwort.grund ?? "Nicht eingereiht.") }
                else { zeige("\(antwort.anzahl ?? 0) Titel eingereiht") }
            } catch let f as ApiFehler {
                zeige(f.errorDescription ?? "Nicht eingereiht.")
            } catch {
                zeige("Nicht eingereiht.")
            }
        }
    }

    // MARK: - Suche

    func suchen(_ text: String) {
        sucheAufgabe?.cancel()
        let sauber = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard sauber.count >= 2 else {
            suchtreffer = []; suchhinweis = ""; suchtLaeuft = false
            return
        }
        suchtLaeuft = true
        sucheAufgabe = Task {
            // Kurz warten: der Server durchsucht 48.000 Titel, und auf der
            // Bildschirmtastatur entsteht Text zeichenweise.
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            do {
                let antwort = try await client.hole(
                    "api/bibliothek/suche", SucheAntwort.self,
                    abfrage: [URLQueryItem(name: "q", value: sauber),
                              URLQueryItem(name: "grenze", value: "12")])
                guard !Task.isCancelled else { return }
                suchtreffer = antwort.treffer ?? []
                if let fehler = antwort.fehler {
                    suchhinweis = "Suche nicht möglich: \(fehler)"
                } else if suchtreffer.isEmpty {
                    suchhinweis = "Nichts gefunden."
                } else {
                    suchhinweis = "\(suchtreffer.count) Alben · \(antwort.gesamt ?? 0) Titel"
                }
            } catch {
                guard !Task.isCancelled else { return }
                suchtreffer = []
                suchhinweis = "Suche nicht möglich."
            }
            suchtLaeuft = false
        }
    }

    func abspielen(album: String?, artist: String?, titel: String? = nil,
                   ersetzen: Bool = true) {
        guard steuerungAn else { return }
        Task {
            var koerper: [String: Any] = ["modus": ersetzen ? "ersetzen" : "anhaengen"]
            if let album { koerper["album"] = album }
            if let artist { koerper["artist"] = artist }
            if let titel { koerper["titel"] = titel }
            do {
                let antwort = try await client.sende("api/bibliothek/abspielen",
                                                     koerper: koerper)
                if antwort.ok == false { zeige(antwort.grund ?? "Nicht eingereiht.") }
                else {
                    zeige(ersetzen ? "Album wird gespielt" : "Angehängt")
                    await verlaufLaden()
                    if ersetzen {
                        // Zur Wiedergabe wechseln und den Zustand
                        // nachziehen: Der Linn braucht einen Moment, bis er
                        // den neuen Titel meldet. Ohne dieses Nachfassen
                        // stand in der Anzeige noch das vorige Album.
                        reiterWunsch = 0
                        await nachfassen()
                    }
                }
            } catch let f as ApiFehler {
                zeige(f.errorDescription ?? "Nicht eingereiht.")
            } catch {
                zeige("Nicht eingereiht.")
            }
        }
    }

    /// Holt den Zustand nach, bis wirklich etwas läuft.
    ///
    /// Beim Einreihen leert der Server zuerst die Warteschlange. Dabei
    /// entsteht ein kurzer Zwischenzustand „gestoppt ohne Titel" – und
    /// genau darauf blieb die Anzeige hängen, weil ein früherer Versuch
    /// schon abbrach, sobald sich der Titel geändert hatte. Ein Wechsel
    /// auf „kein Titel" ist aber auch eine Änderung.
    ///
    /// Darum ist die Bedingung jetzt: warten, bis der Player spielt.
    /// Kommt es nicht dazu, hört das Nachfassen nach rund sieben Sekunden
    /// auf – der Ereignisstrom meldet es ohnehin, sobald es soweit ist.
    private func nachfassen() async {
        for warten in [0.4, 0.8, 1.2, 1.8, 2.5] {
            try? await Task.sleep(nanoseconds: UInt64(warten * 1_000_000_000))
            await jetztLaden()
            if jetzt.spieltAb { return }
        }
    }

    // MARK: - Meldungen

    func zeige(_ text: String, dauer: TimeInterval = 5) {
        meldung = text
        meldungsAufgabe?.cancel()
        meldungsAufgabe = Task {
            try? await Task.sleep(nanoseconds: UInt64(dauer * 1_000_000_000))
            guard !Task.isCancelled else { return }
            meldung = nil
        }
    }

    // MARK: - Uhr und Bildschirm

    /// Zwischen den Meldungen wird interpoliert, statt den Server zu fragen.
    private func uhrStarten() {
        uhr?.invalidate()
        uhr = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, let ref = self.standRef else { return }
                guard self.jetzt.spieltAb else { return }
                var p = ref.position + Date().timeIntervalSince(ref.zeit)
                if let dauer = self.jetzt.zeit?.dauerS, dauer > 0 { p = Swift.min(p, dauer) }
                self.position = p
            }
        }
    }

    /// Bildschirmschoner nur unterdrücken, solange wirklich etwas läuft.
    /// Bei Pause nach fünf Minuten abdunkeln, bei Stopp alles freigeben –
    /// ein Fernseher zeigt dieses Bild sonst stundenlang unverändert.
    private func bildschirmPflegen(_ stand: Jetzt) {
        if stand.spieltAb {
            pausiertSeit = nil
            abgedunkelt = false
            bildschirmHalten(true)
        } else if stand.pausiert {
            if pausiertSeit == nil { pausiertSeit = Date() }
            bildschirmHalten(false)
            if let seit = pausiertSeit, Date().timeIntervalSince(seit) > 300 {
                abgedunkelt = true
            }
        } else {
            pausiertSeit = nil
            abgedunkelt = false
            bildschirmFreigeben()
        }
    }

    private func bildschirmHalten(_ an: Bool) {
        #if canImport(UIKit)
        UIApplication.shared.isIdleTimerDisabled = an
        #endif
    }

    private func bildschirmFreigeben() {
        #if canImport(UIKit)
        UIApplication.shared.isIdleTimerDisabled = false
        #endif
    }

    /// Wird vom Zeitgeber der Ansicht gerufen: die Pausendauer läuft
    /// weiter, ohne dass der Server etwas meldet.
    func pausenpruefung() {
        guard let seit = pausiertSeit else { return }
        if Date().timeIntervalSince(seit) > 300 { abgedunkelt = true }
    }
}
