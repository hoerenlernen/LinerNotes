"""Regressionstests ohne Zugriff auf reale Player, Musik oder API-Konten."""
import importlib
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import datenbank
import sicherheit
import notes
import bibliothek
import hoerenlernen
import umgebung

class ReleaseTests(unittest.TestCase):
    def test_schema_empty_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(pathlib.Path(d)/"nested/db.sqlite")
            datenbank.vorbereiten(db); datenbank.vorbereiten(db)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute("SELECT count(*) FROM album").fetchone()[0], 0)
                self.assertEqual(c.execute("SELECT count(*) FROM dr_album").fetchone()[0], 0)

    def test_missing_media_server(self):
        result = bibliothek.Bibliothek().suche("Album")
        self.assertEqual(result["treffer"], [])
        self.assertIn("Medienserver", result["fehler"])

    def test_minim_unicode_and_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            music = pathlib.Path(d)/"music";music.mkdir()
            (music/"Björk.flac").write_bytes(b"test")
            secret = pathlib.Path(d)/"secret.flac";secret.write_bytes(b"secret")
            (music/"link.flac").symlink_to(secret)
            with patch.object(notes, "ROOT", str(music)):
                self.assertEqual(notes.url_zu_pfad("http://server/minimserver/*/Musik/Bj*c3*b6rk"), str(music/"Björk.flac"))
                for path in ["../secret", "*2e*2e/secret", "link"]:
                    self.assertIsNone(notes.url_zu_pfad("http://server/minimserver/*/Musik/"+path))

    def test_external_cover_allowlist(self):
        hosts = {"192.0.2.10"}
        self.assertTrue(sicherheit.cover_url_erlaubt("http://192.0.2.10:9790/cover",hosts))
        for url in ["http://127.0.0.1/admin", "http://169.254.169.254/", "file:///etc/passwd", "http://user@192.0.2.10/", "http://example.com/"]:
            self.assertFalse(sicherheit.cover_url_erlaubt(url,hosts))

    def test_no_redirect(self):
        handler=sicherheit.KeineWeiterleitung()
        self.assertIsNone(handler.redirect_request(None,None,302,"",{},"http://127.0.0.1/"))

    def test_cover_rejects_svg_and_oversized(self):
        from email.message import Message
        from unittest.mock import MagicMock
        response = MagicMock(); response.__enter__.return_value=response
        for typ, data in [("image/svg+xml", b"<svg/>"), ("image/png", b"x"*(8*1024*1024+1))]:
            response.headers=Message();response.headers["Content-Type"]=typ
            response.read.return_value=data
            with patch("urllib.request.OpenerDirector.open",return_value=response):
                with self.assertRaises(ValueError): sicherheit.cover_laden("http://192.0.2.10/a")

    def test_origin_checks(self):
        self.assertTrue(sicherheit.origin_erlaubt(None,"liner.example.com"))
        self.assertTrue(sicherheit.origin_erlaubt("https://liner.example.com","liner.example.com"))
        for value in ["null", "https://evil.example", "https://liner.example.com.evil.example"]:
            self.assertFalse(sicherheit.origin_erlaubt(value,"liner.example.com"))

    def test_environment_no_shell_execution_or_override(self):
        with tempfile.TemporaryDirectory() as d:
            f=pathlib.Path(d)/"env";f.write_text('LINER_TEST="two words"\nLINER_KEEP=new\nLINER_LITERAL=$(touch /should-not-exist)\n')
            with patch.dict(os.environ,{"LINER_ENV":str(f),"LINER_KEEP":"old"}):
                umgebung.laden()
                self.assertEqual(os.environ["LINER_TEST"],"two words")
                self.assertEqual(os.environ["LINER_KEEP"],"old")
                self.assertTrue(os.environ["LINER_LITERAL"].startswith("$(touch"))

    def test_missing_music_does_not_erase_index(self):
        with tempfile.TemporaryDirectory() as d:
            db=str(pathlib.Path(d)/"db");datenbank.vorbereiten(db)
            with sqlite3.connect(db) as c:c.execute("INSERT INTO album(ordner,gesehen) VALUES ('keep',1)")
            env={**os.environ,"LINER_MUSIC_ROOT":d+"/missing","LINER_DB":db,"LINER_ENV":d+"/missing.env"}
            r=subprocess.run([sys.executable,str(ROOT/"bin/index.py")],env=env,capture_output=True)
            self.assertNotEqual(r.returncode,0)
            with sqlite3.connect(db) as c:self.assertEqual(c.execute("SELECT count(*) FROM album").fetchone()[0],1)

    def test_medley_boundaries(self):
        e=hoerenlernen.Eintrag({"artist":"Test","album":"Test","medley_grenzen":{"1":{"2":"1:00"}},"hinweise":[{"text":"Listen","nr":1,"bis":2,"zeit":"1:10"}]},"", "test.md")
        self.assertEqual(len(e.hinweise),1)
        self.assertEqual(e.hinweise[0]["nr"], 2)
        self.assertEqual(e.hinweise[0]["zeit_s"], 10)
        self.assertIsNone(e.hinweise[0]["bis"])
        with self.assertRaises(ValueError):
            hoerenlernen.Eintrag({"medley_grenzen": {}, "hinweise": [
                {"text":"Listen", "nr":1, "bis":2, "zeit":"1:10"}]}, "", "test.md")

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.env=patch.dict(os.environ, {"LINER_DB":cls.tmp.name+"/liner.db", "LINER_DATENDIR":cls.tmp.name,"LINER_SCROBBELN":"0","NAVIDROME_SPIEGELN":"0","LINER_MUSIC_ROOT":cls.tmp.name})
        cls.env.start()
        player={"host":"192.0.2.20","port":1234,"udn":"test"}
        with patch("finden.player",return_value=player),patch("finden.medienserver",return_value=None):
            cls.api=importlib.import_module("main")
        cls.dbpatch=patch.object(notes,"DB",cls.tmp.name+"/liner.db");cls.dbpatch.start()
        from fastapi.testclient import TestClient
        cls.client=TestClient(cls.api.app)  # no lifespan: no hardware requests

    @classmethod
    def tearDownClass(cls):
        cls.client.close();cls.dbpatch.stop();cls.env.stop();cls.tmp.cleanup()

    def test_fresh_install_endpoints(self):
        for path in ["/", "/api/now", "/api/dr", "/api/pruefen", "/api/favoriten", "/api/health"]:
            self.assertEqual(self.client.get(path).status_code,200,path)

    def test_cross_site_control_is_blocked(self):
        with patch.object(self.api.linn,"steuern") as control:
            r=self.client.post("/api/steuerung/play",headers={"Origin":"https://evil.example"})
            self.assertEqual(r.status_code,403);control.assert_not_called()

    def test_native_control_still_works(self):
        from unittest.mock import AsyncMock
        with patch.object(self.api.linn,"steuern",return_value=(True,"ok")),patch.object(self.api,"_nachfassen",new_callable=AsyncMock):
            self.assertEqual(self.client.post("/api/steuerung/play").status_code,200)

    def test_unindexed_local_cover_is_served_and_preserved(self):
        import local_cover
        album = pathlib.Path(self.tmp.name) / "New & Bach"
        album.mkdir(exist_ok=True)
        track = album / "01.flac"; track.touch()
        cover = album / "cover.jpg"; cover.write_bytes(b"test-cover")
        token = local_cover.reference(self.tmp.name, str(track))
        r = self.client.get("/api/cover-extern", params={"url": token})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"test-cover")
        self.assertTrue(r.headers["content-type"].startswith("image/jpeg"))
        with patch.object(self.api.linn,"transport_zustand",return_value="Playing"), \
             patch.object(self.api.linn,"quelle",return_value={}), \
             patch.object(self.api.linn,"track",return_value=("test",{"cover_url":"http://example.test/art"})), \
             patch.object(self.api.linn,"zeit",return_value={}), \
             patch.object(self.api.linn,"details",return_value={}), \
             patch.object(self.api.aufloeser,"bauen",return_value={"cover_extern":token}):
            self.assertEqual(self.api.aktualisieren()["cover_extern"], token)
        cover.unlink()
        self.assertEqual(self.client.get("/api/cover-extern",params={"url":token}).status_code,404)

    def test_untrusted_notify_and_cover_blocked(self):
        self.assertEqual(self.client.post("/notify").status_code,403)
        self.assertEqual(self.client.get("/api/cover-extern",params={"url":"http://127.0.0.1/admin"}).status_code,403)

if __name__ == "__main__": unittest.main()
