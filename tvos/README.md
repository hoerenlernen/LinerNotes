# LinerNotes für Apple TV

## Deutsch

Die native SwiftUI-App zeigt die Daten des LinerNotes-Servers. Sie benötigt einen laufenden Server und eine vom Apple TV erreichbare Adresse. Die Oberfläche ist derzeit deutschsprachig.

1. `LinerNotes.xcodeproj` in Xcode öffnen (Build mit Xcode 27 / tvOS-27-Simulator geprüft; Deployment Target tvOS 17).
2. Für ein echtes Gerät unter **Signing & Capabilities** das eigene Team und eine eigene Bundle-ID einstellen. Alternativ `Lokal.xcconfig.beispiel` nach `Lokal.xcconfig` kopieren und die eigene Team-ID eintragen. Persönliche Signierung nicht committen.
3. Apple TV und Mac im selben Netz koppeln: am TV **Fernbedienungen und Geräte → Remote-App und Geräte**, in Xcode **Window → Devices and Simulators**.
4. Apple TV als Ziel wählen und mit **Run** installieren. Für den Simulator ist keine Gerätesignierung erforderlich. Installation auf Hardware benötigt gültige Apple-Provisionierung; deren Bedingungen hängen vom verwendeten Konto ab.
5. Unter **Einstellungen** die Serveradresse setzen, etwa `http://192.168.1.10:5060`. `localhost` bezeichnet auf dem Apple TV das Apple TV selbst. Lokalen Netzwerkzugriff erlauben, falls gefragt. Bei HTTPS ein gültiges, vom Gerät akzeptiertes Zertifikat verwenden.

Die App zeigt Album, Titel, technische Daten, Texte und Hörhinweise; Suche und Merkliste verwenden die Serverfunktionen. Die Play/Pause-Taste schaltet die Wiedergabe um. Listen werden mit dem normalen Fokus der Fernbedienung bedient. Steuerungsmöglichkeiten hängen von Player und Quelle ab; siehe die bekannte Roon-Einschränkung in der Haupt-README.

Keine Offline-Funktion und keine eigene Authentifizierung. Der Server darf nur aus einem vertrauenswürdigen Netz oder über abgesicherten Zugang erreichbar sein. Bei Pause wird die Ansicht nach einer Wartezeit abgedunkelt; dies garantiert keinen Schutz vor Einbrennen.

Simulator-Build aus diesem Ordner:

```sh
xcodebuild -project LinerNotes.xcodeproj -scheme LinerNotes \
  -sdk appletvsimulator -configuration Debug \
  -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
```

Den Simulator in Xcode auswählen und über Run starten. Eine Aufnahme lässt sich über das Screenshot-Menü des Simulators erstellen. Nicht benötigte Simulator-Runtimes über die Xcode-Einstellungen entfernen.

## English

The native SwiftUI app displays data from a running LinerNotes server. Its interface is currently in German. The build was checked with Xcode 27 and the tvOS 27 simulator; the deployment target is tvOS 17.

Open `LinerNotes.xcodeproj` in Xcode. For hardware installation choose your own signing team and bundle identifier under **Signing & Capabilities**, or copy `Lokal.xcconfig.beispiel` to the ignored `Lokal.xcconfig` and enter your team ID. Pair the TV using its **Remotes and Devices → Remote App and Devices** screen and Xcode's **Devices and Simulators**, select the device and Run. Hardware requires valid Apple provisioning; account-specific conditions apply. The simulator can be built without signing using the command above.

In the app's **Einstellungen** tab, enter the reachable server address, for example `http://192.168.1.10:5060`. Localhost would refer to the TV itself. Allow local network access if requested; HTTPS requires a certificate trusted by the device.

Album data, tracks, texts, listening notes, search and favourites come from the server. Play/Pause toggles playback; lists use normal remote focus navigation. Available controls depend on the player and source. See the main README for the unresolved resume-after-Roon-pause limitation.

There is no offline mode or separate authentication. Restrict server access to a trusted network or secured connection. Dimming while paused is not a guarantee against burn-in. Use the simulator's screenshot menu for captures and Xcode settings to remove unused runtimes.
