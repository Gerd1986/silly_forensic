import sqlite3
import unittest
from contextlib import closing
import numpy as np
from siliconre.layer_alignment import (
    similarity_matrix, ensure_alignment_schema, save_alignment, load_alignment, alignment_parameters)
from siliconre.via_matching import suggest_via_pairs
from siliconre.vias import ensure_via_schema


class LayerAlignmentTests(unittest.TestCase):
    def test_legacy_migration_and_reverse_inverse(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE alignments (base TEXT, overlay TEXT, dx REAL, dy REAL, PRIMARY KEY(base,overlay))")
            db.execute("INSERT INTO alignments VALUES ('M1','poly',12,-7)")
            np.testing.assert_allclose(load_alignment(db,'M1','poly'), similarity_matrix(12,-7))
            ensure_alignment_schema(db)
            self.assertEqual(db.execute("SELECT dx,dy,rotation_deg,scale FROM alignments").fetchone(), (12,-7,0,1))
            save_alignment(db,'M1','poly',123,-45,17,1.03)
            matrix = load_alignment(db,'M1','poly')
            np.testing.assert_allclose(load_alignment(db,'poly','M1') @ matrix, np.eye(3), atol=1e-10)
            np.testing.assert_allclose(alignment_parameters(matrix), [123,-45,17,1.03])
            save_alignment(db,'poly','M1',8,9,-3,.99)
            self.assertEqual(db.execute("SELECT count(*) FROM alignments").fetchone()[0], 1)
            np.testing.assert_allclose(load_alignment(db,'M1','poly'), np.linalg.inv(similarity_matrix(8,9,-3,.99)))

    def test_missing_alignment_and_invalid_scale(self):
        with closing(sqlite3.connect(":memory:")) as db:
            with self.assertRaisesRegex(ValueError, "No saved alignment"):
                load_alignment(db, 'A', 'B')
            for scale in (0, -1, float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    save_alignment(db,'A','B',0,0,0,scale)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0)

    def test_rotated_scaled_via_suggestions_both_directions(self):
        with closing(sqlite3.connect(":memory:")) as db:
            ensure_via_schema(db)
            save_alignment(db,'M1','poly',100,50,90,2)
            db.execute("INSERT INTO via_candidates(id,layer,x,y,radius,status) VALUES (1,'M1',60,70,3,'accepted')")
            db.execute("INSERT INTO via_candidates(id,layer,x,y,radius,status) VALUES (2,'poly',10,20,3,'accepted')")
            self.assertEqual(suggest_via_pairs(db,'M1','poly',.01)[0]['distance_px'], 0)
            self.assertEqual(suggest_via_pairs(db,'poly','M1',.01)[0]['distance_px'], 0)
