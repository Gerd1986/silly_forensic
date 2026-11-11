import sqlite3
import unittest
from siliconre.logic_analysis import add_logic_cell,analyze_sequential

class SequentialTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE trace_candidates(id INTEGER PRIMARY KEY,layer TEXT,status TEXT)")
        self.db.executemany("INSERT INTO trace_candidates VALUES (?,?,?)",
                            [(i,"M1","accepted") for i in range(1,8)])
    def tearDown(self):
        self.db.close()
    def test_clock_and_shift_chain(self):
        add_logic_cell(self.db,"FF0","DFF","M1",{"D":1,"Q":2,"CLK":7})
        add_logic_cell(self.db,"FF1","DFF","M1",{"D":2,"Q":3,"CLK":7})
        result=analyze_sequential(self.db)
        self.assertEqual(result["clock_groups"][0]["count"],2)
        self.assertTrue(any(c["cells"]==["FF0","FF1"] for c in result["shift_register_candidates"]))
        self.assertFalse(result["automatic_tap_identification"])
    def test_four_bits_not_a_verified_tap(self):
        for i in range(4):
            add_logic_cell(self.db,f"FF{i}","DFF","M1",{"D":i+1,"Q":i+2,"CLK":7})
        result=analyze_sequential(self.db)
        self.assertEqual(len(result["tap_state_register_candidates"]),1)
        self.assertFalse(result["tap_state_register_candidates"][0]["tap_verified"])
    def test_reject_unaccepted(self):
        with self.assertRaises(ValueError):
            add_logic_cell(self.db,"FF","DFF","M1",{"D":1,"Q":2,"CLK":999})

if __name__=="__main__":
    unittest.main()
