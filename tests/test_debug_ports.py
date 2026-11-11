import sqlite3
import unittest
from siliconre.debug_ports import analyze_debug_interfaces

class DebugPortTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE markers(layer TEXT,x REAL,y REAL,label TEXT)")
        self.db.execute("""CREATE TABLE trace_candidates(
            id INTEGER PRIMARY KEY,layer TEXT,x REAL,y REAL,width REAL,height REAL,status TEXT)""")
        self.db.execute("INSERT INTO trace_candidates VALUES (1,'M1',0,0,100,100,'accepted')")
    def tearDown(self):
        self.db.close()
    def test_jtag_evidence_not_verification(self):
        self.db.executemany("INSERT INTO markers VALUES ('M1',10,10,?)",
                            [(s,) for s in ("TCK","TMS","TDI","TDO")])
        findings=analyze_debug_interfaces(self.db)
        jtag=next(f for f in findings if f["protocol"]=="JTAG_1149_1")
        self.assertEqual(jtag["coverage"],1)
        self.assertEqual(jtag["classification"],"label_based_hypothesis")
        self.assertFalse(jtag["evidence"]["TCK"][0]["electrically_verified"])
        self.assertEqual(len(jtag["evidence"]["TCK"][0]["candidate_nets"]),1)
    def test_swd_and_no_false_substrings(self):
        self.db.executemany("INSERT INTO markers VALUES ('M1',10,10,?)",
                            [(s,) for s in ("SWCLK","SWDIO","CONTEXT","NOTCK")])
        findings=analyze_debug_interfaces(self.db)
        self.assertEqual(next(f for f in findings if f["protocol"]=="SWD")["coverage"],1)
        self.assertFalse(any(f["protocol"]=="JTAG_1149_1" for f in findings))
    def test_empty(self):
        self.assertEqual(analyze_debug_interfaces(self.db),[])

if __name__=="__main__":
    unittest.main()
