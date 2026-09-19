// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  ApiClient.swift
//  Zugriff auf die Liner-Notes-API.
//
//  ZWEI VERBINDUNGEN
//  =================
//  1. Kurze Abrufe (GET/POST) für Listen und Befehle.
//  2. Ein dauerhafter SSE-Strom für den laufenden Zustand. Der Server
//     schickt von sich aus – kein Polling im Sekundentakt.
//
//  VERSPÄTETE ANTWORTEN
//  ====================
//  Jede Zustandsmeldung trägt eine fortlaufende `folge`. Eine kleinere
//  Nummer als die zuletzt gesehene ist eine überholte Antwort und wird
//  verworfen. Zusätzlich nennt der Server eine `start_id`: ändert sie
//  sich, hat der Dienst neu gestartet und die Nummern beginnen wieder bei
//  eins – dann MUSS der Zähler zurückgesetzt werden. Ohne das verwirft der
//  Client jede weitere Meldung als „veraltet" und die Anzeige friert ein.
//  (Genau dieser Fehler ist in der Web-Oberfläche aufgetreten.)
//

import Foundation

enum ApiFehler: LocalizedError {
    case ungueltigeAdresse
    case serverFehler(Int, String?)
    case keineAntwort

    var errorDescription: String? {
        switch self {
        case .ungueltigeAdresse:        return "Die Serveradresse ist ungültig."
        case .keineAntwort:             return "Keine Antwort vom Server."
        case let .serverFehler(_, g):   return g ?? "Der Server meldet einen Fehler."
        }
    }
}

actor ApiClient {
    private var basis: URL
    private let sitzung: URLSession

    init(basis: URL) {
        self.basis = basis
        let k = URLSessionConfiguration.default
        k.timeoutIntervalForRequest = 10
        k.waitsForConnectivity = false
        k.requestCachePolicy = .reloadIgnoringLocalCacheData
        self.sitzung = URLSession(configuration: k)
    }

    func basisSetzen(_ neu: URL) { basis = neu }
    func adresse(_ pfad: String) -> URL { basis.appendingPathComponent(pfad) }

    /// Cover-Adresse. `groesse` liefert ein Vorschaubild – für Listen
    /// wichtig: das Vollbild ist über 500 KB, ein 300er unter 30 KB.
    func coverAdresse(albumId: Int, groesse: Int? = nil) -> URL {
        var u = URLComponents(url: basis.appendingPathComponent("api/cover/\(albumId)"),
                              resolvingAgainstBaseURL: false)
        if let g = groesse { u?.queryItems = [URLQueryItem(name: "groesse", value: String(g))] }
        return u?.url ?? basis
    }

    // MARK: - Lesen

    func hole<T: Decodable>(_ pfad: String, _ typ: T.Type,
                            abfrage: [URLQueryItem] = []) async throws -> T {
        var teile = URLComponents(url: adresse(pfad), resolvingAgainstBaseURL: false)
        if !abfrage.isEmpty { teile?.queryItems = abfrage }
        guard let url = teile?.url else { throw ApiFehler.ungueltigeAdresse }
        let (daten, antwort) = try await sitzung.data(from: url)
        try pruefe(antwort, daten)
        return try JSONDecoder().decode(T.self, from: daten)
    }

    // MARK: - Befehle

    @discardableResult
    func sende(_ pfad: String, koerper: [String: Any]? = nil) async throws -> Befehlsantwort {
        var anfrage = URLRequest(url: adresse(pfad))
        anfrage.httpMethod = "POST"
        anfrage.setValue("application/json", forHTTPHeaderField: "Content-Type")
        anfrage.httpBody = try JSONSerialization.data(
            withJSONObject: koerper ?? [:], options: [])
        anfrage.timeoutInterval = 8
        let (daten, antwort) = try await sitzung.data(for: anfrage)
        // Auch ein Fehlschlag trägt eine Begründung im Rumpf – die ist für
        // den Nutzer wertvoller als ein nackter Statuscode.
        let gelesen = try? JSONDecoder().decode(Befehlsantwort.self, from: daten)
        if let http = antwort as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw ApiFehler.serverFehler(http.statusCode, gelesen?.grund)
        }
        guard let gelesen else { throw ApiFehler.keineAntwort }
        return gelesen
    }

    private func pruefe(_ antwort: URLResponse, _ daten: Data) throws {
        guard let http = antwort as? HTTPURLResponse else { throw ApiFehler.keineAntwort }
        guard (200..<300).contains(http.statusCode) else {
            let grund = (try? JSONDecoder().decode(Befehlsantwort.self, from: daten))?.grund
            throw ApiFehler.serverFehler(http.statusCode, grund)
        }
    }
}

// MARK: - SSE

/// Liest den Ereignisstrom des Servers und meldet jede Zustandsänderung.
///
/// Kein Polling: der Server schickt, wenn sich etwas ändert. Bricht die
/// Verbindung ab, wird mit wachsendem Abstand neu verbunden (1 s → 30 s),
/// damit ein schlafender Server nicht im Sekundentakt angefragt wird.
final class EreignisStrom: NSObject, URLSessionDataDelegate {
    private var sitzung: URLSession!
    private var aufgabe: URLSessionDataTask?
    private var puffer = Data()
    private var basis: URL
    private var warteZeit: TimeInterval = 1
    private var laeuftNoch = true

    /// Wird bei jeder Meldung aufgerufen (auf dem Hauptthread).
    var beiMeldung: ((Jetzt) -> Void)?
    /// true = verbunden, false = getrennt.
    var beiVerbindung: ((Bool) -> Void)?

    init(basis: URL) {
        self.basis = basis
        super.init()
        let k = URLSessionConfiguration.default
        // Der Strom ist dauerhaft: kein Zeitlimit für die Antwort, aber
        // eines für den Verbindungsaufbau.
        k.timeoutIntervalForRequest = .infinity
        k.timeoutIntervalForResource = .infinity
        k.requestCachePolicy = .reloadIgnoringLocalCacheData
        self.sitzung = URLSession(configuration: k, delegate: self, delegateQueue: nil)
    }

    func starten() {
        laeuftNoch = true
        verbinden()
    }

    func stoppen() {
        laeuftNoch = false
        aufgabe?.cancel()
        aufgabe = nil
    }

    func basisSetzen(_ neu: URL) {
        basis = neu
        aufgabe?.cancel()
        if laeuftNoch { verbinden() }
    }

    private func verbinden() {
        guard laeuftNoch else { return }
        var anfrage = URLRequest(url: basis.appendingPathComponent("api/stream"))
        anfrage.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        anfrage.timeoutInterval = .infinity
        puffer.removeAll()
        aufgabe = sitzung.dataTask(with: anfrage)
        aufgabe?.resume()
    }

    private func erneutVersuchen() {
        guard laeuftNoch else { return }
        DispatchQueue.main.async { self.beiVerbindung?(false) }
        let wartet = warteZeit
        warteZeit = Swift.min(warteZeit * 2, 30)
        DispatchQueue.global().asyncAfter(deadline: .now() + wartet) { [weak self] in
            self?.verbinden()
        }
    }

    // MARK: URLSessionDataDelegate

    func urlSession(_ s: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        if let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
            warteZeit = 1
            DispatchQueue.main.async { self.beiVerbindung?(true) }
            completionHandler(.allow)
        } else {
            completionHandler(.cancel)
            erneutVersuchen()
        }
    }

    func urlSession(_ s: URLSession, dataTask: URLSessionDataTask, didReceive daten: Data) {
        puffer.append(daten)
        // Ereignisse sind durch eine Leerzeile getrennt.
        while let grenze = puffer.range(of: Data("\n\n".utf8)) {
            let block = puffer.subdata(in: puffer.startIndex..<grenze.lowerBound)
            puffer.removeSubrange(puffer.startIndex..<grenze.upperBound)
            verarbeite(block)
        }
        // Schutz gegen unbegrenztes Wachsen, falls nie eine Leerzeile kommt.
        if puffer.count > 4_000_000 { puffer.removeAll() }
    }

    func urlSession(_ s: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        erneutVersuchen()
    }

    private func verarbeite(_ block: Data) {
        guard let text = String(data: block, encoding: .utf8) else { return }
        var nutzlast = ""
        for zeile in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if zeile.hasPrefix("data:") {
                nutzlast += zeile.dropFirst(5).trimmingCharacters(in: .whitespaces)
            }
        }
        guard !nutzlast.isEmpty,
              let roh = nutzlast.data(using: .utf8),
              let stand = try? JSONDecoder().decode(Jetzt.self, from: roh)
        else { return }
        DispatchQueue.main.async { self.beiMeldung?(stand) }
    }
}
