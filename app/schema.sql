
CREATE TABLE IF NOT EXISTS album (
  id            INTEGER PRIMARY KEY,
  ordner        TEXT UNIQUE NOT NULL,   -- relativ zur Wurzel
  artist        TEXT, album TEXT, jahr TEXT, genre TEXT, label TEXT,
  k_artist      TEXT, k_album TEXT,     -- normalisiert fuer Fallback-Suche
  mbid          TEXT,
  bits          INTEGER, samplerate INTEGER,
  tracks        INTEGER, dauer_s REAL,
  cover_datei   TEXT,
  tags_json     TEXT,                   -- alle Album-Level-Tags als JSON
  mtime         REAL,
  gesehen       REAL
);
CREATE INDEX IF NOT EXISTS i_album_fuzzy ON album(k_artist, k_album);
CREATE INDEX IF NOT EXISTS i_album_mbid  ON album(mbid);

CREATE TABLE IF NOT EXISTS track (
  id        INTEGER PRIMARY KEY,
  album_id  INTEGER NOT NULL REFERENCES album(id) ON DELETE CASCADE,
  pfad      TEXT UNIQUE NOT NULL,       -- relativ zur Wurzel
  disc      INTEGER, nr INTEGER,
  titel     TEXT, k_titel TEXT,
  dauer_s   REAL, bits INTEGER, samplerate INTEGER,
  tags_json TEXT
);
CREATE INDEX IF NOT EXISTS i_track_album ON track(album_id, disc, nr);
CREATE INDEX IF NOT EXISTS i_track_pfad  ON track(pfad);

-- Angereicherte Daten je Album und Quelle. Ein Eintrag pro (album, quelle).
CREATE TABLE IF NOT EXISTS enrich (
  album_id  INTEGER NOT NULL REFERENCES album(id) ON DELETE CASCADE,
  quelle    TEXT NOT NULL,              -- musicbrainz | coverart | wikipedia | lastfm | discogs
  status    TEXT NOT NULL,              -- ok | leer | fehler
  geholt_am REAL NOT NULL,
  daten     TEXT,                       -- JSON
  PRIMARY KEY (album_id, quelle)
);

CREATE TABLE IF NOT EXISTS cover (
  album_id INTEGER PRIMARY KEY REFERENCES album(id) ON DELETE CASCADE,
  quelle   TEXT, breite INTEGER, hoehe INTEGER, bytes INTEGER, datei TEXT, geholt_am REAL
);

CREATE TABLE IF NOT EXISTS dr_track (
  track_id INTEGER PRIMARY KEY REFERENCES track(id) ON DELETE CASCADE,
  dr REAL, peak_db REAL, rms_db REAL, gemessen_am REAL
);
CREATE TABLE IF NOT EXISTS dr_album (
  album_id  INTEGER PRIMARY KEY REFERENCES album(id) ON DELETE CASCADE,
  dr        INTEGER,            -- gerundet, wie die Dynamic Range Database
  dr_exakt  REAL,
  dr_min    INTEGER, dr_max INTEGER,
  peak_db   REAL, rms_db REAL,
  tracks    INTEGER,            -- wie viele Tracks eingingen
  mtime     REAL,               -- Stand der Dateien bei der Messung
  gemessen_am REAL
);
