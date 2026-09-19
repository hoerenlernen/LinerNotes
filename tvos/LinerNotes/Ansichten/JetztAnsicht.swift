// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Goran Ristic and contributors
//
//  JetztAnsicht.swift
//  Now Playing – die Hauptansicht, gedacht für 2,5–3 m Abstand.
//
//  Aufbau: großes Cover links, rechts Titel, Werk/Satz bei Klassik,
//  Fortschritt, Format, Bitperfekt, Album-DR. Darunter die Texte,
//  scrollbar mit der Fernbedienung.
//

import SwiftUI

struct JetztAnsicht: View {
    @EnvironmentObject var zustand: Zustand
    @State private var texteOffen = false

    private var inhalt: Jetzt { zustand.jetzt }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if inhalt.gestoppt && inhalt.zuletzt == nil {
                    ruheBild
                } else {
                    kopf
                }
            }
            .padding(.vertical, 30)
        }
        .background(Farben.grund)
        // Laufkommentar unten. Dezent: nur Deckkraft, keine Bewegung -
        // auf drei Meter Abstand lenkt jede Animation vom Hoeren ab.
        .overlay(alignment: .bottom) { hoerLeiste }
        // KEINE Wischgesten für Titelwechsel.
        //
        // `onMoveCommand` fängt JEDE Richtungsbewegung ab – auch die, mit
        // der man nur den Fokus verschiebt. Das führte dazu, dass ein Wisch
        // nach links zum Leiser-Knopf gleichzeitig zum vorigen Titel
        // sprang, ohne dass etwas geklickt wurde.
        //
        // Auf einer Seite mit Bedienelementen gehören die Richtungen der
        // Fokus-Engine. Titel wechselt man über die Knöpfe ⏮ ⏭; die
        // Play/Pause-TASTE der Fernbedienung bleibt belegt (in
        // HauptAnsicht), denn sie ist eine echte Taste und kollidiert
        // nicht mit der Navigation.
        .fullScreenCover(isPresented: $texteOffen) {
            TexteAnsicht().environmentObject(zustand)
        }
    }

    // MARK: Hören lernen

    /// Der mitlaufende Kommentar. Erscheint nur im Modus „live" und nur,
    /// wenn fuer die laufende Sekunde ein Hinweis vorliegt.
    @ViewBuilder
    private var hoerLeiste: some View {
        if let hl = inhalt.hoerenlernen, let h = hl.hinweis, let text = h.text,
           !text.isEmpty {
            HStack(alignment: .center, spacing: 22) {
                // Restzeit als Ring, wie in der Vorlage. Kein Zaehlwerk,
                // keine Zahl - nur ein Anhalt, wie lange der Satz bleibt.
                Circle()
                    .trim(from: 0, to: h.anteil ?? 1)
                    .stroke(Farben.akzent.opacity(0.85),
                            style: StrokeStyle(lineWidth: 5, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .frame(width: 34, height: 34)

                VStack(alignment: .leading, spacing: 6) {
                    Text(text)
                        .font(.system(size: 34, weight: .regular))
                        .foregroundStyle(.white.opacity(0.94))
                        .lineLimit(2)
                        .minimumScaleFactor(0.8)
                    if let instr = h.instrumente, !instr.isEmpty {
                        Text(instr.joined(separator: " · "))
                            .font(.system(size: 22))
                            .foregroundStyle(.white.opacity(0.55))
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 60)
            .padding(.vertical, 26)
            .background(.black.opacity(0.82))
            .overlay(alignment: .top) {
                Rectangle().fill(.white.opacity(0.10)).frame(height: 1)
            }
            .transition(.opacity)
            .animation(.easeInOut(duration: 0.7), value: h.id)
            .accessibilityLabel(Text(text))
        }
    }

    // MARK: Ruhe

    private var ruheBild: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Nichts läuft.")
                .font(.system(size: 54, weight: .semibold, design: .serif))
                .foregroundStyle(Farben.text)
            if let quelle = inhalt.quelle?.name {
                Text("Quelle: \(quelle)")
                    .font(.system(size: 30))
                    .foregroundStyle(Farben.still)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 120)
    }

    // MARK: Kopf

    private var kopf: some View {
        HStack(alignment: .top, spacing: 50) {
            // Linke Spalte: Cover und direkt darunter die Bedienung.
            // Vorher stand sie ganz unten am Bildrand und wirkte
            // abgehängt; beim Cover ist ohnehin das Auge.
            VStack(alignment: .leading, spacing: 30) {
                CoverBild(url: coverAdresse, kante: 700)
                if zustand.steuerungAn {
                    bedienung
                        .frame(width: 700)
                }
            }
            // Eigener Fokusbereich. Ohne diese Kennzeichnung sucht die
            // Fokus-Engine rein geometrisch das nächste Ziel – bei der
            // großen Lücke zwischen Bedienleiste und Text fand sie keines
            // und sprang stattdessen nach oben in die Reiterleiste.
            // Mit focusSection wechselt der Fokus sauber zwischen links
            // und rechts.
            .focusSection()

            VStack(alignment: .leading, spacing: 14) {
                Text(anzeigeAlbum)
                    .font(.system(size: 50, weight: .semibold, design: .serif))
                    .foregroundStyle(Farben.leise)
                    .lineLimit(2).minimumScaleFactor(0.6)

                if let artist = anzeigeArtist, !artist.isEmpty {
                    Text(artist)
                        .font(.system(size: 38, design: .serif))
                        .foregroundStyle(Farben.akzent)
                        .lineLimit(1)
                }

                mitwirkende

                laufendesStueck

                plaketten

                // Der Textanriss steht neben dem Cover, nicht darunter:
                // unterhalb der Bedienleiste wäre er außerhalb des Bildes
                // gelandet, und gerade er ist der Grund für dieses Projekt.
                texte

                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .focusSection()
        }
    }

    private var mitwirkende: some View {
        Group {
            if let leute = inhalt.mitwirkende, !leute.isEmpty {
                HStack(spacing: 28) {
                    ForEach(leute.indices, id: \.self) { i in
                        HStack(spacing: 8) {
                            Text(leute[i].rolle.uppercased())
                                .font(.system(size: 20, weight: .medium))
                                .foregroundStyle(Farben.still)
                            Text(leute[i].name)
                                .font(.system(size: 26))
                                .foregroundStyle(Farben.leise)
                        }
                    }
                }
            }
        }
    }

    /// Der laufende Titel. Bei Klassik wird das Werk klein darüber
    /// gesetzt und die Satzbezeichnung groß – dieselbe Trennung wie in
    /// der Trackliste.
    private var laufendesStueck: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 16) {
                Text(zustandsZeichen)
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(Farben.still)
                if let quelle = inhalt.quelle?.name {
                    Text("QUELLE: \(quelle.uppercased())")
                        .font(.system(size: 20, weight: .medium))
                        .foregroundStyle(Farben.still)
                        .padding(.horizontal, 12).padding(.vertical, 5)
                        .overlay(RoundedRectangle(cornerRadius: 4)
                            .stroke(Farben.linie, lineWidth: 1))
                }
            }

            if let werk = werkDesLaufenden {
                Text(werk)
                    .font(.system(size: 30, weight: .medium))
                    .foregroundStyle(Farben.akzent)
                    .lineLimit(2)
            }

            Text(satzDesLaufenden)
                .font(.system(size: 68, weight: .semibold, design: .serif))
                .foregroundStyle(Farben.text)
                .lineLimit(3).minimumScaleFactor(0.55)

            if let unterzeile, !unterzeile.isEmpty {
                // Aus drei Metern war 26 pt kaum zu lesen.
                Text(unterzeile)
                    .font(.system(size: 32))
                    .foregroundStyle(Farben.leise)
                    .lineLimit(1)
            }

            Fortschritt(position: zustand.position, dauer: inhalt.zeit?.dauerS)
                .padding(.top, 8)
        }
        // Kein Kasten: Polsterung und Hintergrund kosteten rund 60 pt
        // Höhe – genau die, die der Trackliste fehlten. Auf OLED ist eine
        // große aufgehellte Fläche ohnehin unerwünscht; die Akzentlinie
        // links rahmt den Bereich ebenso deutlich.
        .padding(.leading, 26)
        .padding(.vertical, 6)
        .overlay(alignment: .leading) {
            Rectangle().fill(Farben.akzent).frame(width: 4)
        }
    }

    private var plaketten: some View {
        HStack(spacing: 16) {
            if let q = inhalt.qualitaet { Plakette(text: q) }
            if let bp = inhalt.bitperfekt, let text = bp.text {
                switch bp.stand {
                case "bitperfekt":
                    Plakette(text: "✓ \(text)", farbe: Farben.gruen,
                             rahmen: Farben.gruen.opacity(0.5),
                             hintergrund: Farben.gruen.opacity(0.10))
                case "abweichend":
                    Plakette(text: "⚠ \(text)", farbe: Farben.orange,
                             rahmen: Farben.orange.opacity(0.5),
                             hintergrund: Farben.orange.opacity(0.10))
                default:
                    Plakette(text: text)
                }
            }
            if let dr = inhalt.dr, let wert = dr.dr {
                Plakette(text: "Album-DR \(wert)", farbe: drFarbe(dr.stufe),
                         rahmen: drFarbe(dr.stufe).opacity(0.5),
                         hintergrund: drFarbe(dr.stufe).opacity(0.10))
            }
        }
    }

    private func drFarbe(_ stufe: String?) -> Color {
        switch stufe {
        case "gruen":  return Farben.gruen
        case "gelb":   return Farben.akzent
        case "orange": return Farben.orange
        case "rot":    return Farben.rot
        default:       return Farben.leise
        }
    }

    // MARK: Bedienung

    /// Bedienleiste: reine Symbole mit Fokusrahmen, wie auf tvOS üblich.
    /// Beschriftungen kosten Platz, und die Zeichen sind eindeutig. Eine
    /// einzige Lautstärkegruppe – vorher standen zwei Lautsprechersymbole
    /// neben einem dritten für Stumm, was doppelt gemoppelt war.
    /// Bedienleiste in zwei Reihen unter dem Cover.
    ///
    /// In einer Reihe wurde sie breiter als das Cover und ragte über den
    /// linken Rand hinaus – neun Bedienelemente brauchen mehr als 760 pt.
    /// Oben der Transport, darunter Lautstärke und Merken: das trennt
    /// auch inhaltlich, was zusammengehört.
    private var bedienung: some View {
        VStack(spacing: 18) {
            HStack(spacing: 20) {
                symbol("backward.end.fill", "Vorheriger Titel") { zustand.zurueck() }
                    .disabled(!zustand.kannZurueck)
                symbol(inhalt.spieltAb ? "pause.fill" : "play.fill",
                       inhalt.spieltAb ? "Pause" : "Wiedergabe", gross: true) { zustand.umschalten() }
                symbol("stop.fill", "Stopp") { zustand.stoppen() }
                symbol("forward.end.fill", "Nächster Titel") { zustand.weiter() }
                    .disabled(!zustand.kannWeiter)
            }
            HStack(spacing: 16) {
                symbol("minus", "Leiser") { zustand.leiser() }
                Text(lautstaerkeText)
                    .font(.system(size: 30, design: .monospaced))
                    .foregroundStyle(stumm ? Farben.orange : Farben.leise)
                    .frame(minWidth: 100)
                symbol("plus", "Lauter") { zustand.lauter() }
                symbol(stumm ? "speaker.slash.fill" : "speaker.fill",
                       stumm ? "Ton an" : "Stumm") { zustand.stumm() }

                Rectangle().fill(Farben.linie).frame(width: 1, height: 38)
                    .padding(.horizontal, 8)

                symbol(zustand.laufenderGemerkt ? "heart.fill" : "heart",
                       zustand.laufenderGemerkt ? "Nicht mehr merken" : "Merken",
                       farbe: zustand.laufenderGemerkt ? Farben.herz : Farben.leise) {
                    zustand.merkenUmschalten()
                }
            }
        }
    }

    private var stumm: Bool { zustand.lautstaerke?.stumm ?? false }

    private func symbol(_ name: String, _ beschreibung: String,
                        gross: Bool = false, farbe: Color = Farben.leise,
                        tun: @escaping () -> Void) -> some View {
        Button(action: tun) {
            Image(systemName: name)
                .font(.system(size: gross ? 46 : 36))
                .foregroundStyle(farbe)
                .frame(width: gross ? 64 : 52, height: gross ? 64 : 52)
        }
        .buttonStyle(ZeichenStil())
        .accessibilityLabel(beschreibung)
    }

    private var lautstaerkeText: String {
        guard let l = zustand.lautstaerke else { return "—" }
        if l.stumm ?? false { return "stumm" }
        return "\(l.wert ?? 0)"
    }

    // MARK: Texte

    /// Anriss der Texte – der eigentliche Grund für dieses Projekt.
    ///
    /// Ohne ihn wäre die Anzeige nur eine schönere Fernbedienung. Gezeigt
    /// werden die ersten Zeilen des wichtigsten Textes; „Mehr lesen"
    /// öffnet alles. Gibt es nichts, erscheint auch nichts – kein leerer
    /// Kasten, keine erfundene Zeile.
    private var texte: some View {
        Group {
            if let block = wichtigsterText {
                Button { texteOffen = true } label: {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack(spacing: 14) {
                            Text(block.titel.uppercased())
                                .font(.system(size: 26, weight: .medium))
                                .foregroundStyle(Farben.still)
                                .tracking(1.5)
                            if block.auszug == true {
                                Text("Auszug")
                                    .font(.system(size: 19))
                                    .foregroundStyle(Farben.still)
                                    .padding(.horizontal, 9).padding(.vertical, 2)
                                    .overlay(RoundedRectangle(cornerRadius: 3)
                                        .stroke(Farben.linie, lineWidth: 1))
                            }
                            Spacer()
                            Text(weitereZahl)
                                .font(.system(size: 22))
                                .foregroundStyle(Farben.still)
                        }
                        Text(block.text)
                            .font(.system(size: 34, design: .serif))
                            .foregroundStyle(Farben.leise)
                            .lineSpacing(13)
                            // Drei Zeilen: darunter ist Platz für die
                            // Trackliste, und ein Anriss soll ein Anriss
                            // bleiben.
                            // Sieben Zeilen: Der Text ist der Grund für
                            // dieses Projekt, und die Trackliste hat einen
                            // eigenen Reiter. Ein größerer Block ist mit der
                            // Fernbedienung außerdem leichter zu treffen –
                            // vorher war er ein schmaler Streifen.
                            // Fünf Zeilen. Sieben füllten zwar die Spalte,
                            // schoben aber „Mehr lesen" unter den Bildrand –
                            // und ohne diesen Hinweis findet niemand die
                            // Vollansicht.
                            .lineLimit(5)
                            .multilineTextAlignment(.leading)
                            // Lesezeilen nicht über rund 80 Zeichen: lange
                            // Zeilen ermüden, der gewonnene Platz gehört
                            // der Trackliste.
                            .frame(maxWidth: 1000, alignment: .leading)
                        Text("Mehr lesen →")
                            .font(.system(size: 26, weight: .medium))
                            .foregroundStyle(Farben.akzent)
                    }
                }
                .buttonStyle(ZeilenStil())
                .padding(.top, 6)
                // Der Anriss füllt die Spalte, damit er als Fokusziel
                // groß genug ist.
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    /// Der Text, der zuerst gezeigt wird: Albumtext vor Künstlertext,
    /// Künstlertext vor allem anderen. Besetzung und Produktion sind
    /// keine Prosa und kommen erst in der Vollansicht.
    private var wichtigsterText: Textblock? {
        let bloecke = (inhalt.eigene ?? []).filter {
            !["personnel", "producer"].contains($0.feld)
        }
        let reihenfolge = ["description", "description_de", "description_en", "comment"]
        for feld in reihenfolge {
            if let treffer = bloecke.first(where: { $0.feld == feld }) { return treffer }
        }
        return bloecke.first
    }

    private var weitereZahl: String {
        let n = (inhalt.eigene ?? []).count + ((inhalt.lyrics ?? "").isEmpty ? 0 : 1)
        return n > 1 ? "und \(n - 1) weitere" : ""
    }

    // MARK: Ableitungen

    private var coverAdresse: URL? {
        if let id = inhalt.albumId ?? inhalt.zuletzt?.albumId {
            // Auf dem Fernseher lohnt das Vollbild – es wird groß gezeigt.
            return URL(string: "\(zustand.basis.absoluteString)/api/cover/\(id)")
        }
        if let extern = inhalt.coverExtern,
           let kodiert = extern.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            return URL(string: "\(zustand.basis.absoluteString)/api/cover-extern?url=\(kodiert)")
        }
        return nil
    }

    private var anzeigeAlbum: String {
        inhalt.album ?? inhalt.zuletzt?.album ?? "—"
    }

    private var anzeigeArtist: String? {
        inhalt.artist ?? inhalt.zuletzt?.artist
    }

    private var zustandsZeichen: String {
        if inhalt.gestoppt { return "■ GESTOPPT" }
        if inhalt.pausiert { return "❙❙ PAUSE" }
        return "▶ ES LÄUFT"
    }

    /// Bei Klassik trägt der Titel oft „Werk: Satz". Die Trennung kommt
    /// aus der Werkgruppierung des Servers – hier wird nur zugeordnet,
    /// nichts neu geraten.
    private var werkDesLaufenden: String? {
        guard let pfad = inhalt.aktuellerPfad,
              let gruppen = inhalt.werke?.gruppen else { return nil }
        for gruppe in gruppen where gruppe.tracks.contains(where: { $0.pfad == pfad }) {
            return gruppe.werk
        }
        return nil
    }

    private var satzDesLaufenden: String {
        if let pfad = inhalt.aktuellerPfad, let gruppen = inhalt.werke?.gruppen {
            for gruppe in gruppen {
                if let t = gruppe.tracks.first(where: { $0.pfad == pfad }) {
                    return t.beschriftung
                }
            }
        }
        return inhalt.titel ?? "—"
    }

    /// Unterzeile des laufenden Stücks.
    ///
    /// Leere Werte dürfen keine Beschriftung erzeugen: Der Server liefert
    /// Felder auch als leeren String, und dann stand dort „Komposition:"
    /// ohne Namen. Die Tracknummer wird von führenden Nullen befreit –
    /// „Titel 01" liest sich wie eine Dateibezeichnung.
    private var unterzeile: String? {
        var teile: [String] = []
        if let nr = inhalt.tracknummer?.trimmingCharacters(in: .whitespaces),
           !nr.isEmpty {
            let sauber = Int(nr).map(String.init) ?? nr
            teile.append("Titel \(sauber)")
        }
        if let i = inhalt.interpret?.trimmingCharacters(in: .whitespaces),
           !i.isEmpty, i != inhalt.artist {
            teile.append(i)
        }
        if let k = inhalt.komponist?.trimmingCharacters(in: .whitespaces),
           !k.isEmpty {
            teile.append("Komposition: \(k)")
        }
        return teile.isEmpty ? nil : teile.joined(separator: "  ·  ")
    }
}
