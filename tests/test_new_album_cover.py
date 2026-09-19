import base64
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import local_cover
import notes

class NewAlbumCoverTests(unittest.TestCase):
    def test_new_album_not_assigned_to_root(self):
        with tempfile.TemporaryDirectory() as root:
            con = sqlite3.connect(':memory:')
            con.row_factory = sqlite3.Row
            con.execute('CREATE TABLE album (id INTEGER, ordner TEXT)')
            con.executemany('INSERT INTO album VALUES (?,?)', [(1,'.'),(2,'Known')])
            resolver = notes.Aufloeser()
            with patch.object(notes, 'ROOT', root):
                self.assertIsNone(resolver.album_aus_pfad(con,root+'/New/track.flac'))
                self.assertEqual(resolver.album_aus_pfad(con,root+'/track.flac')['id'],1)
                self.assertEqual(resolver.album_aus_pfad(con,root+'/Known/CD 1/track.flac')['id'],2)
                self.assertIsNone(resolver.album_aus_pfad(con,root+'/../track.flac'))
            con.close()

    def test_cover_without_index_and_special_characters(self):
        with tempfile.TemporaryDirectory() as root:
            album=pathlib.Path(root)/'Bach; Suiten & Konzerte [16B] é'
            album.mkdir(); track=album/'01.flac'; track.touch()
            cover=album/'cover.jpg'; cover.write_bytes(b'example')
            token=local_cover.reference(root,str(track))
            self.assertTrue(token.startswith(local_cover.PREFIX))
            self.assertNotIn('&',token)
            self.assertEqual(local_cover.resolve(root,token),str(cover))
            cover.unlink()
            self.assertIsNone(local_cover.resolve(root,token))
            self.assertIsNone(local_cover.reference(root,str(track)))

    def test_rejects_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            target=pathlib.Path(outside)/'cover.jpg'; target.touch()
            (pathlib.Path(root)/'cover.jpg').symlink_to(target)
            (pathlib.Path(root)/'track.flac').touch()
            self.assertIsNone(local_cover.reference(root,root+'/track.flac'))
            for value in ['../cover.jpg',str(target),'../../etc/passwd','cover.jpg','secret.env']:
                token=local_cover.PREFIX+base64.urlsafe_b64encode(value.encode()).decode()
                self.assertIsNone(local_cover.resolve(root,token))
            self.assertIsNone(local_cover.resolve(root,local_cover.PREFIX+'%%%'))

if __name__=='__main__': unittest.main()
