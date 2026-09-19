//
//  Prüft den ECHTEN Ereignisstrom der App gegen den laufenden Server.
//  Verwendet die Klasse EreignisStrom unverändert aus ApiClient.swift.
//
import Foundation

let basis = URL(string: CommandLine.arguments.count > 1
                ? CommandLine.arguments[1] : "http://localhost:5060")!

let strom = EreignisStrom(basis: basis)
var meldungen = 0
var verbindungen: [Bool] = []
let fertig = DispatchSemaphore(value: 0)

strom.beiVerbindung = { an in
    verbindungen.append(an)
    print("  Verbindung: \(an ? "offen" : "getrennt")")
}
strom.beiMeldung = { stand in
    meldungen += 1
    print("  Meldung \(meldungen): transport=\(stand.transport ?? "-")"
        + " · folge=\(stand.folge.map(String.init) ?? "-")"
        + " · start_id=\(stand.startId ?? "-")"
        + " · titel=\(stand.titel ?? stand.zuletzt?.titel ?? "-")")
    if meldungen >= 2 { fertig.signal() }
}

print("Lausche am Ereignisstrom von \(basis) …")
strom.starten()

// Die Rückmeldungen laufen über die Main-Queue. Ein Semaphore auf dem
// Hauptthread würde sie blockieren – in der App läuft dieser Thread frei,
// darum hier ein RunLoop statt einer Sperre.
let ende = Date().addingTimeInterval(45)
while Date() < ende && meldungen < 2 {
    RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.5))
}
strom.stoppen()

print("")
if meldungen > 0 && verbindungen.first == true {
    print("OK: \(meldungen) Meldung(en) empfangen und dekodiert")
    exit(0)
} else {
    print("FEHLER: \(meldungen) Meldungen, Verbindungen: \(verbindungen)")
    exit(1)
}
