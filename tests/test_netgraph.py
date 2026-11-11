"""Standard-library regression tests for manual SEM candidate links."""
import sqlite3
import unittest
from siliconre.netgraph import add_link, list_links, connected_groups

class NetGraphTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE trace_candidates(id INTEGER PRIMARY KEY, layer TEXT, status TEXT)")
        self.db.executemany("INSERT INTO trace_candidates VALUES (?,?,?)",
                            [(1,"M1","accepted"),(2,"M1","accepted"),(3,"M1","accepted"),
                             (4,"M1","rejected"),(5,"M2","accepted")])

    def tearDown(self):
        self.db.close()

    def test_groups_and_duplicate_link(self):
        add_link(self.db,"M1",2,1)
        add_link(self.db,"M1",1,2)
        self.assertEqual(len(list_links(self.db,"M1")),1)
        self.assertEqual(connected_groups(self.db,"M1"),[[1,2],[3]])

    def test_invalid_links(self):
        for pair in [(1,1),(1,4),(1,5),(1,99)]:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                add_link(self.db,"M1",*pair)

    def test_transitive_group(self):
        add_link(self.db,"M1",1,2)
        add_link(self.db,"M1",2,3)
        self.assertEqual(connected_groups(self.db,"M1"),[[1,2,3]])

if __name__ == "__main__":
    unittest.main()
