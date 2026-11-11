import sqlite3
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from siliconre.mos_detection import (detect_mos_candidates,ensure_mos_candidate_schema,
                                      list_mos_candidates)

class MosDetectionTests(unittest.TestCase):
    def test_crossing_and_no_crossing(self):
        with tempfile.TemporaryDirectory() as tmp:
            poly=np.zeros((80,80),dtype=np.uint8)
            active=np.zeros((80,80),dtype=np.uint8)
            poly[5:75,35:42]=255
            active[35:44,5:75]=255
            a,b=Path(tmp)/"poly.png",Path(tmp)/"active.png"
            Image.fromarray(poly).save(a)
            Image.fromarray(active).save(b)
            found=detect_mos_candidates(str(a),str(b),(0,0,80,80))
            self.assertEqual(len(found),1)
            self.assertTrue(35<=found[0]["center_x"]<=42)
            active[:]=0
            Image.fromarray(active).save(b)
            self.assertEqual(detect_mos_candidates(str(a),str(b),(0,0,80,80)),[])
    def test_schema_and_invalid_roi(self):
        db=sqlite3.connect(":memory:")
        ensure_mos_candidate_schema(db)
        db.execute("""INSERT INTO mos_candidates(poly_layer,active_layer,x,y,width,height,area)
                      VALUES ('POLY','ACTIVE',1,2,3,4,12)""")
        self.assertEqual(len(list_mos_candidates(db,"POLY")),1)
        self.assertEqual(list_mos_candidates(db,"OTHER"),[])
        with self.assertRaises(ValueError):
            list_mos_candidates(db,status="invalid")
        with self.assertRaises(ValueError):
            detect_mos_candidates("missing","missing",(0,0,5000,5000))
        db.close()

if __name__=="__main__":
    unittest.main()
