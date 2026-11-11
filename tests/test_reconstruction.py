import sqlite3
import unittest
from siliconre.reconstruction import (ensure_schema, associate_via_trace, connect_vias,
                                      reconstruct, export_verilog, export_vhdl)

class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE trace_candidates(id INTEGER PRIMARY KEY,layer TEXT,status TEXT)")
        self.db.executemany("INSERT INTO trace_candidates VALUES (?,?,?)",
                            [(1,"M1","accepted"),(2,"M1","accepted"),(3,"M2","accepted"),
                             (4,"M2","rejected")])
        ensure_schema(self.db)
        self.db.executemany("INSERT INTO via_candidates(id,layer,x,y,radius,status) VALUES (?,?,?,?,?,?)",
                            [(10,"M1",10,10,3,"accepted"),(11,"M2",10,10,3,"accepted"),
                             (12,"M2",20,20,3,"rejected")])
    def tearDown(self):
        self.db.close()
    def test_cross_layer_reconstruction(self):
        associate_via_trace(self.db,10,1)
        associate_via_trace(self.db,11,3)
        connect_vias(self.db,10,11)
        nets=reconstruct(self.db)
        connected=[n for n in nets if len(n["members"])==4]
        self.assertEqual(len(connected),1)
        self.assertEqual({n["layer"] for n in connected[0]["members"]},{"M1","M2"})
        self.assertIn("module siliconre_reconstruction",export_verilog(self.db))
        self.assertIn("architecture structural",export_vhdl(self.db))
        self.assertNotIn("assign ",export_verilog(self.db))
    def test_rejected_or_same_layer_links(self):
        for fn,args in [(associate_via_trace,(12,3)),(associate_via_trace,(10,3)),
                        (connect_vias,(10,10)),(connect_vias,(11,12))]:
            with self.subTest(fn=fn.__name__,args=args),self.assertRaises(ValueError):
                fn(self.db,*args)
    def test_unlinked_nets_remain_distinct(self):
        self.assertEqual(len(reconstruct(self.db)),5)

if __name__=="__main__":
    unittest.main()
