import sqlite3
import unittest
from siliconre.reconstruction import ensure_schema
from siliconre.via_matching import suggest_via_pairs

class ViaMatchingTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE alignments(base TEXT,overlay TEXT,dx REAL,dy REAL)")
        ensure_schema(self.db)
        self.db.executemany("INSERT INTO via_candidates(id,layer,x,y,radius,status) VALUES (?,?,?,?,?,?)",
                            [(1,"M1",20,30,3,"accepted"),(2,"M2",15,32,3,"accepted"),
                             (3,"M2",15,32,3,"rejected")])
    def tearDown(self):
        self.db.close()
    def test_aligned_pair_no_auto_connect(self):
        self.db.execute("INSERT INTO alignments VALUES ('M1','M2',5,-2)")
        pairs=suggest_via_pairs(self.db,"M1","M2",1)
        self.assertEqual([(p["via_a"],p["via_b"]) for p in pairs],[(1,2)])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM via_layer_links").fetchone()[0],0)
    def test_missing_alignment_rejected(self):
        with self.assertRaises(ValueError):
            suggest_via_pairs(self.db,"M1","M2")
    def test_reverse_alignment(self):
        self.db.execute("INSERT INTO alignments VALUES ('M2','M1',-5,2)")
        self.assertEqual(len(suggest_via_pairs(self.db,"M1","M2",1)),1)

if __name__=="__main__":
    unittest.main()
