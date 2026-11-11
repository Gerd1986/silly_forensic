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
            self.assertEqual(len(raw),w*h*channels)
            actual=np.frombuffer(raw,dtype=np.uint8).reshape(h,w,channels)
            np.testing.assert_array_equal(actual[:,:,0],(image[8:28:2,6:26:2]%256).astype(np.uint8))
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


    def test_both_axes_and_edge_tiles_at_every_level(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"asymmetric.tif"
            y,x=np.indices((773,1027))
            image=((17*y+3*x)%256).astype(np.uint8)
            tifffile.imwrite(path,image,bigtiff=True)
            reader=BigTiffReader(path)
            try:
                for level,(factor,width,height) in enumerate(reader.levels):
                    reference=image[::factor,::factor]
                    for tx,ty,tw,th in [(0,0,13,11),(7,5,19,17),(width-1,height-1,512,512)]:
                        raw,w,h,channels=reader.crop(level,tx,ty,tw,th)
                        self.assertEqual(len(raw),w*h*channels)
                        actual=np.frombuffer(raw,dtype=np.uint8).reshape(h,w,channels)
                        expected=reference[ty:ty+th,tx:tx+tw]
                        self.assertEqual(actual.shape[:2],expected.shape)
                        for channel in range(channels):
                            np.testing.assert_array_equal(actual[:,:,channel],expected)
            finally:
                reader.close()

    def test_rgb_rgba_and_uint16_pyramid_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            for channels in (3,4):
                for dtype in (np.uint8,np.uint16):
                    with self.subTest(channels=channels,dtype=dtype):
                        path=Path(folder)/f"color-{channels}-{np.dtype(dtype).name}.tif"
                        y,x=np.indices((519,1031))
                        image=np.stack([((x*(c+3)+y*(c+7))%256) for c in range(channels)],axis=2)
                        if dtype==np.uint16:
                            image=image*257
                        image=image.astype(dtype)
                        tifffile.imwrite(path,image,bigtiff=True,photometric="rgb")
                        reader=BigTiffReader(path)
                        try:
                            for level,(factor,_,_) in enumerate(reader.levels):
                                raw,w,h,c=reader.crop(level,2,3,17,13)
                                self.assertEqual(len(raw),w*h*c)
                                actual=np.frombuffer(raw,dtype=np.uint8).reshape(h,w,c)
                                expected=image[3*factor:(3+h)*factor:factor,2*factor:(2+w)*factor:factor]
                                if dtype==np.uint16:
                                    expected=(expected>>8).astype(np.uint8)
                                np.testing.assert_array_equal(actual,expected)
                        finally:
                            reader.close()

if __name__=="__main__":
    unittest.main()
