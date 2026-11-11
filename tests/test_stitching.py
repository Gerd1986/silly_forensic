import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from siliconre.stitching import stitch_images

class StitchingTests(unittest.TestCase):
    def test_reject_single_and_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"a.png"
            Image.fromarray(np.zeros((32,32,3),dtype=np.uint8)).save(source)
            with self.assertRaises(ValueError):
                stitch_images([source],Path(folder)/"mosaic.tif")
            with self.assertRaises(ValueError):
                stitch_images([source,source],Path(folder)/"mosaic.tif")
            self.assertFalse((Path(folder)/"mosaic.tif").exists())
    def test_no_mosaic_on_failed_alignment(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=[]
            for i in range(2):
                p=Path(folder)/f"{i}.png"
                Image.fromarray(np.full((80,80,3),100+i,dtype=np.uint8)).save(p)
                paths.append(p)
            target=Path(folder)/"mosaic.tif"
            with self.assertRaises(ValueError):
                stitch_images(paths,target)
            self.assertFalse(target.exists())

if __name__=="__main__":
    unittest.main()
