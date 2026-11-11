import tempfile
import unittest
from pathlib import Path
import numpy as np
import tifffile
from siliconre.bigtiff_reader import BigTiffReader

class BigTiffReaderTests(unittest.TestCase):
    def test_virtual_pyramid_and_tile(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"large.tif"
            image=np.arange(1024*1024,dtype=np.uint32).reshape(1024,1024)
            tifffile.imwrite(path,(image%256).astype(np.uint8),bigtiff=True)
            reader=BigTiffReader(path)
            self.assertEqual((reader.width,reader.height),(1024,1024))
            self.assertEqual(reader.levels[-1],(2,512,512))
            raw,w,h,channels=reader.crop(1,3,4,10,10)
            self.assertEqual((w,h,channels),(10,10,3))
            expected=(image[8,6]%256)
            self.assertEqual(raw[0],expected)
            reader.close()
            path.unlink()
    def test_grayscale_16bit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"16.tif"
            tifffile.imwrite(path,np.full((20,20),65535,dtype=np.uint16),bigtiff=True)
            reader=BigTiffReader(path)
            raw,w,h,ch=reader.crop(0,0,0,2,2)
            self.assertEqual((w,h,ch),(2,2,3))
            self.assertEqual(raw[0],255)
            reader.close()

if __name__=="__main__":
    unittest.main()
