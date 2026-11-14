import sys
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'siliconre'))
import global_alignment_edit as edits
from layer_alignment import save_alignment,load_alignment,similarity_matrix


class GlobalEditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name)
        self.path=self.project/'project.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
            db.executemany('INSERT INTO layers VALUES(?,?)',[('M1','a'),('poly','b')])
            db.commit()

    def matrix(self):
        with closing(sqlite3.connect(self.path)) as db:
            return edits.current(db,'M1','poly')

    def test_create_backup_guard_and_undo(self):
        r=edits.write(self.project,'M1','poly',10,20,30,1.2,note='visual fit')
        np.testing.assert_allclose(self.matrix(),similarity_matrix(10,20,30,1.2))
        with closing(sqlite3.connect(r['backup'])) as db:
            self.assertIsNone(edits.current(db,'M1','poly'))
        with self.assertRaises(ValueError):
            edits.write(self.project,'M1','poly',0,0)
        with self.assertRaises(ValueError):
            edits.undo(self.project,r['seq']+1)
        restored=edits.undo(self.project,r['seq'])
        self.assertIsNone(restored['source_to_base'])
        self.assertIsNone(self.matrix())

    def test_existing_inverse_pair_and_external_gui_change(self):
        with closing(sqlite3.connect(self.path)) as db:
            save_alignment(db,'poly','M1',5,6,2,1.1)
            db.commit()
        expected=self.matrix()
        r=edits.write(self.project,'M1','poly',8,9,expected_matrix=expected)
        edits.undo(self.project,r['seq'])
        np.testing.assert_allclose(self.matrix(),expected)
        r=edits.write(self.project,'M1','poly',8,9,expected_matrix=expected)
        with closing(sqlite3.connect(self.path)) as db:
            save_alignment(db,'M1','poly',30,40)
            db.commit()
        with self.assertRaises(ValueError):
            edits.undo(self.project,r['seq'])
        np.testing.assert_allclose(self.matrix(),similarity_matrix(30,40))

    def test_invalid_inputs_do_not_change_existing_alignment(self):
        r=edits.write(self.project,'M1','poly',1,2)
        for base,overlay,dx,scale in [('missing','poly',0,1),('M1','M1',0,1),
                                     ('M1','poly',float('nan'),1),('M1','poly',0,0)]:
            with self.assertRaises(ValueError):
                edits.write(self.project,base,overlay,dx,0,scale=scale,expected_matrix=r['matrix'])
        np.testing.assert_allclose(self.matrix(),similarity_matrix(1,2))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(len(edits.history(db)),1)

    def test_repeated_edits_undo_in_order(self):
        a=edits.write(self.project,'M1','poly',1,2)
        b=edits.write(self.project,'M1','poly',3,4,expected_matrix=a['matrix'])
        edits.undo(self.project,b['seq'])
        edits.undo(self.project,a['seq'])
        self.assertIsNone(self.matrix())

    def test_backup_and_journal_failures_leave_transform_unchanged(self):
        r=edits.write(self.project,'M1','poly',1,2)
        with patch.object(edits,'backup_annotations',side_effect=PermissionError('backup denied')):
            with self.assertRaises(PermissionError):
                edits.write(self.project,'M1','poly',8,9,expected_matrix=r['matrix'])
        np.testing.assert_allclose(self.matrix(),r['matrix'])
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_history BEFORE INSERT ON global_alignment_history BEGIN SELECT RAISE(ABORT,'journal failed'); END")
            db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            edits.write(self.project,'M1','poly',8,9,expected_matrix=r['matrix'])
        np.testing.assert_allclose(self.matrix(),r['matrix'])


if __name__=='__main__':
    unittest.main()

