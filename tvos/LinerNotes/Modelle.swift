// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  Modelle.swift
//  Liner Notes für Apple TV
//
//  Die Strukturen bilden die Antworten des Servers ab. Zwei Grundsätze:
//
//  1. Fast alles ist optional. Der Server lässt Felder weg, wenn es nichts
//     zu sagen gibt – etwa bei einer Quelle ohne Bibliotheksdatei. Eine
//     App, die Werte erzwingt, würde bei Radio oder AirPlay abstürzen.
//  2. Die deutschen Feldnamen der API werden übernommen. Eine Übersetzung
//     in englische Namen wäre eine zweite Namensebene, die bei jedem
//     Vergleich mit dem Server neu durchdacht werden müsste.
//

import Foundation

// MARK: - Now Playing

/// Was der Server aus dem Repo „Hören lernen" beisteuert.
///
/// Der Laufkommentar kommt bewusst fertig berechnet vom Server: Auf dem
/// Fernseher gibt es keine zweite Quelle für die Abspielposition, und
/// eine eigene Zeitrechnung im Client würde bei jedem Sprung driften.
struct Hoerhinweis: Decodable, Equatable {
    var id: String?
    var text: String?
    var typ: String?
    var dauer: Int?
    var restS: Int?
    var anteil: Double?
    var instrumente: [String]?

    enum CodingKeys: String, CodingKey {
        case id, text, typ, dauer, anteil, instrumente
        case restS = "rest_s"
    }
}

struct Besetzungsgruppe: Decodable, Equatable {
    var name: String?
    var farbe: String?
    var instrumente: [String]?
}

struct Besetzung: Decodable, Equatable {
    var art: String?
    var gruppen: [Besetzungsgruppe]?
}

struct Hoerenlernen: Decodable, Equatable {
    var zugeordnetUeber: String?
    var quelle: String?
    var lizenz: String?
    var repo: String?
    var woraufAchten: [String]?
    var modus: String?
    var aufnahme: String?
    var besetzung: Besetzung?
    var abschnitte: [String]?
    var tracksMitHinweisen: [Int]?
    var hinweis: Hoerhinweis?
    var imTitel: Int?

    enum CodingKeys: String, CodingKey {
        case quelle, lizenz, repo, modus, aufnahme, besetzung, abschnitte, hinweis
        case zugeordnetUeber = "zugeordnet_ueber"
        case woraufAchten = "worauf_achten"
        case tracksMitHinweisen = "tracks_mit_hinweisen"
        case imTitel = "im_titel"
    }

    /// Nur anzeigen, wenn es wirklich etwas zu zeigen gibt.
    var hatInhalt: Bool {
        !(woraufAchten?.isEmpty ?? true) || hinweis != nil
            || !(besetzung?.gruppen?.isEmpty ?? true)
    }
}

struct Jetzt: Decodable {
    var laeuft: Bool?
    var folge: Int?
    var startId: String?
    var transport: String?
    var grund: String?
    var zuordnung: String?
    var quelle: Quelle?
    var zeit: Zeit?
    var bitperfekt: Bitperfekt?
    var titel: String?
    var album: String?
    var artist: String?
    var komponist: String?
    var interpret: String?
    var jahr: String?
    var genre: String?
    var tracknummer: String?
    var qualitaet: String?
    var albumId: Int?
    var coverExtern: String?
    var aktuellerPfad: String?
    var lyrics: String?
    var favorit: Bool?
    var favoriten: [String]?
    var ohneDatei: Bool?
    var ohneTitelinfos: Bool?
    var kopfHerkunft: String?
    var treffer: Treffer?
    var dr: DR?
    var fakten: [Faktum]?
    var schlagworte: [Schlagwort]?
    var mitwirkende: [Mitwirkender]?
    var eigene: [Textblock]?
    var tracks: [Track]?
    var werke: Werke?
    var nurQuelle: NurQuelle?
    /// Bei Stillstand liefert der Server das zuletzt Gehörte mit.
    var zuletzt: ZuletztGehoert?
    /// „Hören lernen" – Hinweise aus dem gleichnamigen Repo.
    var hoerenlernen: Hoerenlernen?

    enum CodingKeys: String, CodingKey {
        case laeuft, folge, transport, grund, zuordnung, quelle, zeit
        case bitperfekt, titel, album, artist, komponist, interpret, jahr
        case genre, tracknummer, qualitaet, lyrics, favorit, favoriten
        case treffer, dr, fakten, schlagworte, mitwirkende, eigene, tracks, werke
        case startId = "start_id"
        case albumId = "album_id"
        case coverExtern = "cover_extern"
        case aktuellerPfad = "aktueller_pfad"
        case ohneDatei = "ohne_datei"
        case ohneTitelinfos = "ohne_titelinfos"
        case kopfHerkunft = "kopf_herkunft"
        case nurQuelle = "nur_quelle"
        case zuletzt, hoerenlernen
    }

    /// Läuft gerade wirklich etwas – oder zeigen wir nur Erinnerung?
    var spieltAb: Bool { (laeuft ?? false) && (transport?.lowercased() == "playing") }
    var pausiert: Bool { (laeuft ?? false) && (transport?.lowercased() == "paused") }
    var gestoppt: Bool { !(laeuft ?? false) }
}

/// Was der Server bei Stillstand als „zuletzt gespielt" mitgibt. Bewusst
/// eine eigene, flachere Struktur: sie wird nur zur Anzeige gebraucht.
struct ZuletztGehoert: Decodable {
    var titel: String?
    var album: String?
    var artist: String?
    var albumId: Int?
    var tracks: [Track]?
    var werke: Werke?

    enum CodingKeys: String, CodingKey {
        case titel, album, artist, tracks, werke
        case albumId = "album_id"
    }
}

struct Quelle: Decodable {
    var index: Int?
    var typ: String?
    var name: String?
    var format: String?
}

struct NurQuelle: Decodable {
    var name: String?
    var format: String?
}

struct Zeit: Decodable {
    var positionS: Double?
    var dauerS: Double?
    enum CodingKeys: String, CodingKey {
        case positionS = "position_s"
        case dauerS = "dauer_s"
    }
}

struct Treffer: Decodable {
    var album: Bool?
    var titel: Bool?
    var zuordnung: String?
}

struct Bitperfekt: Decodable {
    var stand: String?          // bitperfekt | abweichend | unbekannt | uebertragung
    var text: String?
    var grund: String?
}

struct DR: Decodable {
    var dr: Int?
    var exakt: Double?
    var min: Int?
    var max: Int?
    var stufe: String?          // gruen | gelb | orange | rot
    var tracks: Int?
}

struct Faktum: Decodable {
    var titel: String
    var wert: String
    var extern: Bool?
    var ausgabe: Bool?
}

struct Schlagwort: Decodable {
    var titel: String
    var wert: String
}

struct Mitwirkender: Decodable {
    var rolle: String
    var name: String
}

struct Textblock: Decodable, Identifiable {
    var feld: String
    var titel: String
    var text: String
    var textart: String?
    var auszug: Bool?
    var quelle: String?
    var quelleUrl: String?
    var zeichen: Int?
    var liste: [Besetzungseintrag]?

    var id: String { feld }

    enum CodingKeys: String, CodingKey {
        case feld, titel, text, textart, auszug, quelle, zeichen, liste
        case quelleUrl = "quelle_url"
    }
}

struct Besetzungseintrag: Decodable {
    var name: String?
    var rolle: String?
}

struct Track: Decodable, Identifiable, Hashable {
    var disc: Int?
    var nr: Int?
    var titel: String?
    var anzeige: String?
    var dauerS: Double?
    var pfad: String?
    var dr: Double?

    /// Der Pfad ist eindeutig; fehlt er, dient die Kombination als Notnagel.
    var id: String { pfad ?? "\(disc ?? 1)-\(nr ?? 0)-\(titel ?? "")" }

    var beschriftung: String { anzeige ?? titel ?? "—" }

    enum CodingKeys: String, CodingKey {
        case disc, nr, titel, anzeige, pfad, dr
        case dauerS = "dauer_s"
    }

    static func == (a: Track, b: Track) -> Bool { a.id == b.id }
    func hash(into h: inout Hasher) { h.combine(id) }
}

struct Werke: Decodable {
    var quelle: String?         // tags | titel
    var gruppen: [Werkgruppe]?
}

struct Werkgruppe: Decodable, Identifiable {
    var werk: String?
    var tracks: [Track]

    var id: String { werk ?? (tracks.first?.id ?? UUID().uuidString) }
}

// MARK: - Steuerung

struct SteuerStand: Decodable {
    var an: Bool?
    var lautstaerke: Lautstaerke?
    var kannSpringen: Bool?
    var kannPausieren: Bool?
    var kannWeiter: Bool?
    var kannZurueck: Bool?

    enum CodingKeys: String, CodingKey {
        case an, lautstaerke
        case kannSpringen = "kann_springen"
        case kannPausieren = "kann_pausieren"
        case kannWeiter = "kann_weiter"
        case kannZurueck = "kann_zurueck"
    }
}

struct Lautstaerke: Decodable {
    var wert: Int?
    var stumm: Bool?
    var grenze: Int?
    var schrittMax: Int?

    enum CodingKeys: String, CodingKey {
        case wert, stumm, grenze
        case schrittMax = "schritt_max"
    }
}

/// Einheitliche Antwort der Steuerbefehle: `{ok, grund, …}`.
struct Befehlsantwort: Decodable {
    var ok: Bool?
    var grund: String?
    var hinweis: String?
    var stand: Lautstaerke?
    var gesetzt: Bool?
    var anzahl: Int?
    var navidrome: Bool?
    var inWarteschlange: Bool?

    enum CodingKeys: String, CodingKey {
        case ok, grund, hinweis, stand, gesetzt, anzahl, navidrome
        case inWarteschlange = "in_warteschlange"
    }
}

// MARK: - Merkliste, Suche, Verlauf

struct MerklisteAntwort: Decodable {
    var anzahl: Int?
    var eintraege: [Merkeintrag]?
}

struct Merkeintrag: Decodable, Identifiable {
    var id: Int
    var pfad: String?
    var albumId: Int?
    var artist: String?
    var album: String?
    var titel: String?
    var dauerS: Double?
    var quelle: String?
    var resUrl: String?
    var dateiFehlt: Bool?

    enum CodingKeys: String, CodingKey {
        case id, pfad, artist, album, titel, quelle
        case albumId = "album_id"
        case dauerS = "dauer_s"
        case resUrl = "res_url"
        case dateiFehlt = "datei_fehlt"
    }
}

struct SucheAntwort: Decodable {
    var treffer: [Suchtreffer]?
    var gesamt: Int?
    var fehler: String?
}

struct Suchtreffer: Decodable, Identifiable {
    var album: String?
    var artist: String?
    var albumId: Int?
    var anzahl: Int?
    var titel: [Suchtitel]?

    var id: String { "\(album ?? "")|\(artist ?? "")" }

    enum CodingKeys: String, CodingKey {
        case album, artist, anzahl, titel
        case albumId = "album_id"
    }
}

struct Suchtitel: Decodable, Identifiable {
    var nr: Int?
    var titel: String?
    var dauerS: Double?
    var id: String { "\(nr ?? 0)-\(titel ?? "")" }
    enum CodingKeys: String, CodingKey {
        case nr, titel
        case dauerS = "dauer_s"
    }
}

struct VerlaufAntwort: Decodable {
    var eintraege: [Verlaufseintrag]?
}

struct Verlaufseintrag: Decodable, Identifiable {
    var albumId: Int?
    var artist: String?
    var album: String?
    var quelle: String?
    var zuletzt: Double?
    var anzahl: Int?

    var id: String { "\(albumId ?? 0)|\(album ?? "")|\(artist ?? "")" }

    enum CodingKeys: String, CodingKey {
        case artist, album, quelle, zuletzt, anzahl
        case albumId = "album_id"
    }
}

struct Versionsantwort: Decodable {
    var api: Int?
    var steuerung: Bool?
    var navidrome: Bool?
}
