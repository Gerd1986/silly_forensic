from contextlib import closing
from unittest.mock import patch
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
        with tempfile.TemporaryDirectory() as folder, closing(self.make_db()) as db:
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
            with closing(sqlite3.connect(backup)) as restored:
                for name in ANNOTATION_TABLES:
                    self.assertEqual(restored.execute(f'SELECT count(*) FROM {name}').fetchone()[0], 2)

    def test_delete_failure_rolls_back_every_table(self):
        with closing(self.make_db()) as db:
            db.execute("CREATE TRIGGER block_clear BEFORE DELETE ON markers BEGIN SELECT RAISE(ABORT,'blocked'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                clear_annotations(db)
            for name in ANNOTATION_TABLES:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {name}').fetchone()[0], 2)

    def test_old_project_and_repeated_clear(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE markers (label TEXT)')
            db.execute("INSERT INTO markers VALUES ('TCK')")
            self.assertEqual(clear_annotations(db), {'markers': 1})
            self.assertEqual(clear_annotations(db), {'markers': 0})

    def test_backup_connection_is_closed_on_success_and_failure(self):
        real_connect = sqlite3.connect
        targets = []
        def tracked_connect(*args, **kwargs):
            db = real_connect(*args, **kwargs)
            targets.append(db)
            return db
        with tempfile.TemporaryDirectory() as folder, closing(self.make_db()) as source:
            with patch('siliconre.annotations.sqlite3.connect', side_effect=tracked_connect):
                backup_annotations(source, folder)
                class FailedSource:
                    def backup(self, target):
                        raise sqlite3.OperationalError('backup failed')
                with self.assertRaises(sqlite3.OperationalError):
                    backup_annotations(FailedSource(), folder)
            for target in targets:
                with self.assertRaises(sqlite3.ProgrammingError):
                    target.execute('SELECT 1')
            self.assertEqual(len(list((Path(folder) / 'annotation_backups').glob('*.sqlite3'))), 1)
