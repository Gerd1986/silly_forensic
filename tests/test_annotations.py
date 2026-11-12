import sqlite3
import tempfile
import unittest
from pathlib import Path
from siliconre.annotations import ANNOTATION_TABLES, backup_annotations, clear_annotations


class AnnotationClearTests(unittest.TestCase):
    def make_db(self):
        db = sqlite3.connect(':memory:')
        for name in ANNOTATION_TABLES:
            db.execute(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY, layer TEXT)')
            db.executemany(f'INSERT INTO {name} VALUES (?,?)', [(1, 'M1'), (2, 'poly')])
        db.execute('CREATE TABLE layers (name TEXT, path TEXT)')
        db.execute("INSERT INTO layers VALUES ('M1','image.tif')")
        db.execute('CREATE TABLE alignments (dx REAL)')
        db.execute('INSERT INTO alignments VALUES (12.5)')
        db.commit()
        return db

    def test_clear_all_layers_preserves_images_and_alignment_and_backup(self):
        with self.make_db() as db, tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'image.tif'
            image.write_bytes(b'original image')
            backup = backup_annotations(db, folder)
            counts = clear_annotations(db)
            self.assertEqual(sum(counts.values()), 2 * len(ANNOTATION_TABLES))
            for name in ANNOTATION_TABLES:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {name}').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT * FROM layers').fetchall(), [('M1','image.tif')])
            self.assertEqual(db.execute('SELECT * FROM alignments').fetchall(), [(12.5,)])
            self.assertEqual(image.read_bytes(), b'original image')
            with sqlite3.connect(backup) as restored:
                for name in ANNOTATION_TABLES:
                    self.assertEqual(restored.execute(f'SELECT count(*) FROM {name}').fetchone()[0], 2)

    def test_delete_failure_rolls_back_every_table(self):
        with self.make_db() as db:
            db.execute("CREATE TRIGGER block_clear BEFORE DELETE ON markers BEGIN SELECT RAISE(ABORT,'blocked'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                clear_annotations(db)
            for name in ANNOTATION_TABLES:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {name}').fetchone()[0], 2)

    def test_old_project_and_repeated_clear(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE markers (label TEXT)')
            db.execute("INSERT INTO markers VALUES ('TCK')")
            self.assertEqual(clear_annotations(db), {'markers': 1})
            self.assertEqual(clear_annotations(db), {'markers': 0})
