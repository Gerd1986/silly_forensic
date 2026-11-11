import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from siliconre.grid_registration import estimate_translation, register_grid, _valid_ncc

class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rng = np.random.default_rng(1234)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, array, name):
        path = self.root / (name + ".tif")
        Image.fromarray(array).save(path)
        return path

    def test_normalized_fft_matches_direct_calculation(self):
        search = self.rng.normal(size=(15, 20))
        template = search[4:10, 8:13].copy()
        scores = _valid_ncc(search, template)
        self.assertEqual(np.unravel_index(np.argmax(scores), scores.shape), (4, 8))
        self.assertAlmostEqual(scores[4, 8], 1.0, places=6)

    def test_horizontal_overlap_and_drift(self):
        image = self.rng.integers(20, 180, (280, 440), dtype=np.uint8)
        a = self.save(image[:256, :256], "a")
        b = self.save(image[7:263, 179:435] + 20, "b")
        match = estimate_translation(a, b, "horizontal")
        self.assertEqual((match["dx"], match["dy"]), (179, 7))
        self.assertGreater(match["score"], .99)

    def test_vertical_negative_drift(self):
        image = self.rng.integers(0, 255, (440, 280), dtype=np.uint8)
        a = self.save(image[:256, 8:264], "a")
        b = self.save(image[179:435, :256], "b")
        match = estimate_translation(a, b, "vertical")
        self.assertEqual((match["dx"], match["dy"]), (-8, 179))

    def test_global_two_by_two(self):
        image = self.rng.integers(0, 255, (450, 450), dtype=np.uint8)
        expected = {(13,11):(0,0), (13,12):(179,3),
                    (14,11):(2,180), (14,12):(181,183)}
        mapping = {key:self.save(image[y:y+256, x:x+256], str(key))
                   for key,(x,y) in expected.items()}
        positions, edges = register_grid(mapping, (256,256))
        self.assertEqual(positions, expected)
        self.assertEqual(len(edges), 4)

    def test_downsampled_sem_like_texture_refines_original_pixels(self):
        # Smooth structures survive coarse registration; the non-multiple
        # translation must still be recovered at full resolution.
        from PIL import ImageFilter
        noise = self.rng.integers(0,255,(700,2500),dtype=np.uint8)
        smooth = np.asarray(Image.fromarray(noise).filter(ImageFilter.GaussianBlur(2)))
        a = self.save(smooth[:640,:1536], "a")
        b = self.save(smooth[5:645,913:2449], "b")
        match = estimate_translation(a,b,"horizontal")
        self.assertEqual((match["dx"], match["dy"]),(913,5))

    def test_inconsistent_loop_rejected(self):
        from unittest.mock import patch
        paths = {(1,1):"a",(1,2):"b",(2,1):"c",(2,2):"d"}
        matches = [{"dx":180,"dy":0}, {"dx":0,"dy":180},
                   {"dx":0,"dy":180}, {"dx":200,"dy":0}]
        with patch("siliconre.grid_registration.estimate_translation", side_effect=matches):
            with self.assertRaisesRegex(ValueError,"Inconsistent"):
                register_grid(paths,(256,256))

    def test_no_overlap_rejected(self):
        a = self.save(self.rng.integers(0,255,(128,128),dtype=np.uint8), "a")
        b = self.save(self.rng.integers(0,255,(128,128),dtype=np.uint8), "b")
        with self.assertRaisesRegex(ValueError, "Uncertain"):
            estimate_translation(a,b,"horizontal")

    def test_flat_rejected(self):
        path = self.save(np.full((128,128), 100, dtype=np.uint8), "flat")
        with self.assertRaisesRegex(ValueError, "texture"):
            estimate_translation(path,path,"horizontal")

    def test_periodic_ambiguity_rejected(self):
        image = np.tile(self.rng.integers(0,255,(128,16),dtype=np.uint8), (1,8))
        path = self.save(image, "periodic")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            estimate_translation(path,path,"horizontal")

    def test_disconnected_grid_rejected(self):
        path = self.save(self.rng.integers(0,255,(128,128),dtype=np.uint8), "a")
        with self.assertRaisesRegex(ValueError, "disconnected"):
            register_grid({(1,1):path,(1,3):path},(128,128))

if __name__ == "__main__":
    unittest.main()
