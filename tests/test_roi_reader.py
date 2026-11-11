import tempfile
import unittest
from pathlib import Path
import numpy as np
import tifffile
from siliconre.roi_reader import read_gray_roi
from siliconre.traces import detect_traces
from siliconre.vias import detect_via_candidates

class RoiReaderTests(unittest.TestCase):
    def test_bigtiff_roi_and_trace_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"test.tif"
            img=np.zeros((256,256),dtype=np.uint8)
            img[70:90,80:180]=255
            tifffile.imwrite(path,img,bigtiff=True)
            roi=read_gray_roi(path,(75,65,110,35))
            self.assertEqual(roi.shape,(35,110))
            self.assertEqual(int(roi[10,10]),255)
            candidates=detect_traces(path,(75,65,110,35),min_area=30)
            self.assertTrue(any(c["area"]>=100 for c in candidates))
            self.assertEqual(detect_via_candidates(path,(0,0,40,40)),[])
            path.unlink()
    def test_roi_bounds_and_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"test.tif"
            tifffile.imwrite(path,np.zeros((20,20),dtype=np.uint8),bigtiff=True)
            with self.assertRaises(ValueError):
                read_gray_roi(path,(15,15,10,10))
            with self.assertRaises(ValueError):
                read_gray_roi(path,(0,0,4000,4000))

if __name__=="__main__":
    unittest.main()
