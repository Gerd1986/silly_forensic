"""Regression tests for via proposal storage and ROI validation."""
import sqlite3
import unittest
from siliconre.vias import ensure_via_schema, list_vias, detect_via_candidates

class ViaTests(unittest.TestCase):
    def test_storage_and_status_filter(self):
        db = sqlite3.connect(":memory:")
        ensure_via_schema(db)
        db.executemany("INSERT INTO via_candidates(layer,x,y,radius,status) VALUES (?,?,?,?,?)",
                       [("M1",10,20,4,"candidate"),("M1",30,40,5,"accepted"),
                        ("M2",50,60,6,"rejected")])
        self.assertEqual(len(list_vias(db,"M1")),2)
        self.assertEqual(len(list_vias(db,"M1","accepted")),1)
        with self.assertRaises(ValueError):
            list_vias(db,"M1","invalid")
        db.close()

    def test_invalid_roi_without_image_access(self):
        with self.assertRaises(ValueError):
            detect_via_candidates("missing.tif",(0,0,5000,5000))
        with self.assertRaises(ValueError):
            detect_via_candidates("missing.tif",(-1,0,100,100))

if __name__ == "__main__":
    unittest.main()
