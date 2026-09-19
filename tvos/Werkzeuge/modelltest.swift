//
//  Prüft die Datenmodelle gegen ECHTE Antworten des Servers.
//
//  Ein erfolgreicher Compilerlauf sagt nichts darüber, ob die Strukturen
//  zu dem passen, was der Server tatsächlich schickt. Genau dort entstehen
//  die Fehler: ein Feld, das mal fehlt, ein Typ, der anders ist als
//  angenommen. Darum werden hier die echten Antworten dekodiert.
//

import Foundation

let basis = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1] : "http://localhost:5060"

var fehler = 0
var geprueft = 0

func hole(_ pfad: String) -> Data? {
    guard let url = URL(string: basis + pfad) else { return nil }
    var daten: Data?
    let warten = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: url) { d, _, _ in
        daten = d; warten.signal()
    }.resume()
    _ = warten.wait(timeout: .now() + 25)
    return daten
}

func pruefe<T: Decodable>(_ pfad: String, _ typ: T.Type,
                          _ bericht: (T) -> String) {
    geprueft += 1
    guard let daten = hole(pfad) else {
        print("  FEHLER  \(pfad): keine Antwort"); fehler += 1; return
    }
    do {
        let wert = try JSONDecoder().decode(T.self, from: daten)
        print("  ok      \(pfad)")
        let z = bericht(wert)
        if !z.isEmpty { print("          \(z)") }
    } catch {
        fehler += 1
        print("  FEHLER  \(pfad)")
        print("          \(error)")
        if let text = String(data: daten.prefix(300), encoding: .utf8) {
            print("          Anfang der Antwort: \(text)")
        }
    }
}

print("Prüfe die Modelle gegen \(basis)")
print("")

pruefe("/api/now", Jetzt.self) { j in
    var t: [String] = []
    t.append("laeuft=\(j.laeuft ?? false)")
    t.append("transport=\(j.transport ?? "-")")
    t.append("album=\(j.album ?? j.zuletzt?.album ?? "-")")
    t.append("tracks=\((j.tracks ?? j.zuletzt?.tracks ?? []).count)")
    t.append("werke=\((j.werke?.gruppen ?? j.zuletzt?.werke?.gruppen ?? []).count)")
    t.append("texte=\((j.eigene ?? []).count)")
    t.append("folge=\(j.folge.map(String.init) ?? "-")")
    t.append("start_id=\(j.startId ?? "-")")
    return t.joined(separator: " · ")
}

pruefe("/api/steuerung/stand", SteuerStand.self) { s in
    "an=\(s.an ?? false) · Lautstärke=\(s.lautstaerke?.wert.map(String.init) ?? "-")"
    + " · Grenze=\(s.lautstaerke?.grenze.map(String.init) ?? "-")"
    + " · springen=\(s.kannSpringen ?? false)"
}

pruefe("/api/favoriten", MerklisteAntwort.self) { m in
    "\(m.anzahl ?? 0) Titel, erster: \(m.eintraege?.first?.titel ?? "-")"
}

pruefe("/api/verlauf", VerlaufAntwort.self) { v in
    "\(v.eintraege?.count ?? 0) Einträge, erster: \(v.eintraege?.first?.album ?? "-")"
}

pruefe("/api/bibliothek/suche?q=Kleiber", SucheAntwort.self) { s in
    let t = s.treffer?.first
    return "\(s.treffer?.count ?? 0) Alben · erster: \(t?.album ?? "-")"
         + " · album_id=\(t?.albumId.map(String.init) ?? "FEHLT")"
}

pruefe("/api/version", Versionsantwort.self) { v in
    "API \(v.api ?? 0) · Steuerung \(v.steuerung ?? false) · Navidrome \(v.navidrome ?? false)"
}

// Der Ereignisstrom wird separat geprüft (stromtest.swift). Ein
// Versuch mit einfachem dataTask schlägt hier fehl, weil die Daten
// erst am Ende geliefert werden – bei einem dauerhaften Strom also
// nie. Die App nutzt darum einen Delegate.

print("")
print("\(geprueft) geprüft, \(fehler) Fehler")
exit(fehler == 0 ? 0 : 1)
