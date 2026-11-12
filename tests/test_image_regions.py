import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
import tifffile
from siliconre.image_regions import render_region, png_bytes
from siliconre.layer_alignment import similarity_matrix


class ImageRegionTests(unittest.TestCase):
    def test_native_crop_and_transparent_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'source.tif'
            data = np.arange(32*48,dtype=np.uint16).reshape(32,48)%256
            tifffile.imwrite(path, data.astype(np.uint8))
            with patch('PIL.Image.open', side_effect=AssertionError('TIFF must be mapped')):
                gray, mask, meta = render_region(path,(7,4,20,18),64)
            np.testing.assert_array_equal(gray, data[4:22,7:27])
            self.assertTrue(mask.all())
            gray, mask, meta = render_region(path,(-10,-5,20,20),64)
            self.assertEqual(int(mask.sum()), 150)
            with Image.open(io.BytesIO(png_bytes(gray,mask))) as decoded:
                np.testing.assert_array_equal(np.array(decoded)[:,:,3], mask.astype(np.uint8)*255)
            _, mask, _ = render_region(path,(1000,1000,10,10),64)
            self.assertFalse(mask.any())

    def test_clockwise_rotation_and_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'source.tif'
            data = np.arange(20*30,dtype=np.uint16).reshape(20,30)%256
            tifffile.imwrite(path,data.astype(np.uint8))
            gray, mask, _ = render_region(path,(0,0,20,30),64,similarity_matrix(20,0,90,1))
            np.testing.assert_array_equal(gray,np.rot90(data,-1))
            self.assertTrue(mask.all())
            gray, mask, _ = render_region(path,(5,-3,30,20),64,similarity_matrix(5,-3))
            np.testing.assert_array_equal(gray,data)

    def test_scaling_and_uint16_box_average(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'source.tif'
            data = np.indices((128,128)).sum(axis=0)%2*65535
            tifffile.imwrite(path,data.astype(np.uint16))
            gray, mask, meta = render_region(path,(0,0,128,128),64)
            self.assertTrue(mask.all())
            self.assertLessEqual(np.max(np.abs(gray.astype(float)-128)),1)
            self.assertEqual(meta['output_size'], [64,64])
            self.assertEqual(meta['source_sampling_step'],2)
            gray, mask, _ = render_region(path,(0,0,256,256),64,similarity_matrix(scale=2))
            self.assertLessEqual(np.max(np.abs(gray.astype(float)-128)),1)

    def test_invalid_requests_before_io(self):
        for rect, side in [((0,0,0,10),64), ((0,0,10,10),9999),
                           ((float('nan'),0,10,10),64), ((0,0,1e10,10),64)]:
            with self.assertRaises(ValueError):
                render_region('missing.tif',rect,side)
