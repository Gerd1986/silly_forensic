import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import cv2
import tifffile
from PIL import Image
from siliconre.image_preview import gray_preview,image_size,_cached_preview
from siliconre.registration import estimate_translation

class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        _cached_preview.cache_clear()
    def tearDown(self):
        _cached_preview.cache_clear()
        self.temp.cleanup()
    def test_large_tiff_bypasses_pillow_without_disabling_safety_limit(self):
        path=self.root/'giant.tif'
        data=tifffile.memmap(path,shape=(15000,15000),dtype=np.uint8,bigtiff=True)
        data[0:40,0:40]=200
        data.flush()
        data._mmap.close()
        safety=Image.MAX_IMAGE_PIXELS
        with patch('PIL.Image.open',side_effect=AssertionError('full Pillow decode forbidden')):
            preview,size=gray_preview(path,max_side=128)
        self.assertEqual(size,(15000,15000))
        self.assertLessEqual(max(preview.shape),128)
        self.assertEqual(preview.dtype,np.float32)
        self.assertFalse(preview.flags.writeable)
        self.assertEqual(Image.MAX_IMAGE_PIXELS,safety)
        path.unlink()  # memory map must already be closed on Windows
    def test_box_averaging_includes_partial_edge_blocks(self):
        path=self.root/'odd.tif'
        data=np.arange(15,dtype=np.uint8).reshape(3,5)*10
        tifffile.imwrite(path,data)
        preview,size=gray_preview(path,max_side=32,step=2)
        expected=np.array([[30,50,65],[105,125,140]],dtype=np.float32)
        np.testing.assert_array_equal(preview,expected)
        self.assertEqual(size,(5,3))
    def test_uint16_preview_preserves_fractional_mean(self):
        path=self.root/'16.tif'
        data=np.array([[0,65535],[0,65535]],dtype=np.uint16)
        tifffile.imwrite(path,data)
        preview,_=gray_preview(path,max_side=32,step=2)
        self.assertAlmostEqual(float(preview[0,0]),32767.5/256,places=5)
    def test_equal_image_translation_direction(self):
        rng=np.random.default_rng(144)
        base=rng.integers(0,255,(256,256),dtype=np.uint8)
        overlay=np.roll(base,(-11,17),axis=(0,1))
        a,b=self.root/'a.tif',self.root/'b.tif'
        tifffile.imwrite(a,base);tifffile.imwrite(b,overlay)
        dx,dy,response=estimate_translation(a,b)
        self.assertAlmostEqual(dx,-17,delta=.5)
        self.assertAlmostEqual(dy,11,delta=.5)
        self.assertGreater(response,.1)
    def test_different_mosaic_sizes_keep_one_pixel_scale(self):
        rng=np.random.default_rng(288)
        base=rng.integers(0,255,(1300,1600),dtype=np.uint8)
        overlay=cv2.warpAffine(base,np.float32([[1,0,24],[0,1,-18]]),(1640,1340))
        a,b=self.root/'a.tif',self.root/'b.tif'
        tifffile.imwrite(a,base);tifffile.imwrite(b,overlay)
        dx,dy,response=estimate_translation(a,b)
        self.assertAlmostEqual(dx,-24,delta=2)
        self.assertAlmostEqual(dy,18,delta=2)
        self.assertGreater(response,.1)
    def test_flat_images_rejected(self):
        path=self.root/'flat.png'
        Image.new('L',(128,128),100).save(path)
        with self.assertRaisesRegex(ValueError,'texture'):
            estimate_translation(path,path)

if __name__=='__main__':
    unittest.main()