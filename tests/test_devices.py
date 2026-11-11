import sqlite3
import unittest
from siliconre.devices import add_device, resolve_devices, spice_netlist
from siliconre.reconstruction import export_json, export_verilog, export_vhdl

class MosDeviceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE trace_candidates(id INTEGER PRIMARY KEY,layer TEXT,status TEXT)")
        self.db.executemany("INSERT INTO trace_candidates VALUES (?,?,?)",
                            [(1,"M1","accepted"),(2,"M1","accepted"),(3,"M1","accepted"),
                             (4,"M1","accepted"),(5,"M1","rejected")])
    def tearDown(self):
        self.db.close()
    def test_manual_device_and_exports(self):
        add_device(self.db,"Q1","nmos","M1",10,20,1,2,3,4)
        d=resolve_devices(self.db)[0]
        self.assertTrue(d["complete"])
        self.assertEqual(d["pins"]["g"],"net_0001")
        self.assertIn("nmos Q1 (",export_verilog(self.db))
        self.assertIn("Q1: nmos_device port map",export_vhdl(self.db))
        self.assertIn('"name": "Q1"',export_json(self.db))
        self.assertIn("MQ1 ",spice_netlist(self.db))
    def test_reject_bad_name_and_trace(self):
        for name,gate in (("bad name",1),("Q2",5),("Q3",99)):
            with self.subTest(name=name),self.assertRaises(ValueError):
                add_device(self.db,name,"pmos","M1",0,0,gate,2,3)
    def test_bulk_missing_not_in_spice(self):
        add_device(self.db,"Q4","pmos","M1",0,0,1,2,3)
        self.assertIn("SKIPPED Q4",spice_netlist(self.db))

if __name__=="__main__":
    unittest.main()
