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
        with patch("siliconre.grid_registration.estimate_translation", side_effect=[[dict(item,score=.99)] for item in matches]):
            with self.assertRaisesRegex(ValueError,"Inconsistent"):
                register_grid(paths,(256,256))

    def test_broad_single_peak_is_not_a_second_match(self):
        from siliconre.grid_registration import _candidate_peaks
        yy,xx=np.mgrid[:81,:121]
        scores=.973*np.exp(-((xx-60)**2+(yy-40)**2)/(2*35**2))
        # The old radius mask finds a runner-up > .96 on the same hill.
        peaks=_candidate_peaks(scores,3,.72)
        self.assertEqual([(y,x) for y,x,_ in peaks],[(40,60)])

    def test_smooth_broad_sem_structures(self):
        from PIL import ImageFilter, ImageOps
        noise=self.rng.integers(0,255,(280,440),dtype=np.uint8)
        smooth=np.asarray(ImageOps.autocontrast(
            Image.fromarray(noise).filter(ImageFilter.GaussianBlur(8))))
        a=self.save(smooth[:256,:256],"a")
        b=self.save(smooth[7:263,179:435],"b")
        match=estimate_translation(a,b,"horizontal")
        self.assertEqual((match["dx"],match["dy"]),(179,7))
        self.assertGreater(match["score"],.99)

    def test_whole_overlap_map_matches_direct_ncc(self):
        from siliconre.grid_registration import _whole_overlap_scores, _ncc
        a=self.rng.normal(size=(40,60))
        b=self.rng.normal(size=(40,60))
        dxs=np.array([20,30,40])
        dys=np.array([-3,0,4])
        scores=_whole_overlap_scores(a,b,dxs,dys)
        for iy,dy in enumerate(dys):
            for ix,dx in enumerate(dxs):
                direct=_ncc(a[max(0,dy):min(40,40+dy),dx:],
                            b[max(0,-dy):min(40,40-dy),:60-dx])
                self.assertAlmostEqual(scores[iy,ix],direct,places=6)

    def test_known_acquisition_step_resolves_periodic_image(self):
        pattern=self.rng.integers(0,255,(128,16),dtype=np.uint8)
        image=np.tile(pattern,(1,13))
        a=self.save(image[:,:128],"a")
        b=self.save(image[:,80:208],"b")
        match=estimate_translation(a,b,"horizontal",hint=(80,0))
        self.assertEqual((match["dx"],match["dy"]),(80,0))
        self.assertEqual(match["position_hint"],"acquisition_overlap")

    def test_standard_tiff_positions_resolve_periodic_image(self):
        from PIL.TiffImagePlugin import IFDRational
        pattern=self.rng.integers(0,255,(128,16),dtype=np.uint8)
        image=np.tile(pattern,(1,13))
        paths=[]
        for name,x in (("a",0),("b",80)):
            path=self.root/(name+".tif")
            Image.fromarray(image[:,x:x+128]).save(path,tiffinfo={
                282:IFDRational(100),283:IFDRational(100),296:3,
                286:IFDRational(x,100),287:IFDRational(0)})
            paths.append(path)
        match=estimate_translation(*paths,"horizontal")
        self.assertEqual((match["dx"],match["dy"]),(80,0))
        self.assertEqual(match["position_hint"],"standard_tiff_position")

    def test_shared_grid_step_resolves_individually_ambiguous_edges(self):
        from siliconre.grid_registration import _resolve_axis
        groups=[[{"dx":179,"dy":0,"score":.98},{"dx":163,"dy":0,"score":.979}],
                [{"dx":195,"dy":0,"score":.98},{"dx":179,"dy":0,"score":.979}]]
        matches=_resolve_axis(groups,"horizontal",2)
        self.assertEqual([m["dx"] for m in matches],[179,179])
        self.assertTrue(all(m["selection"]=="shared_grid_step" for m in matches))

    def test_equally_plausible_shared_steps_are_not_guessed(self):
        from siliconre.grid_registration import _resolve_axis
        options=[{"dx":179,"dy":0,"score":.98},{"dx":163,"dy":0,"score":.979}]
        with self.assertRaisesRegex(ValueError,"Ambiguous"):
            _resolve_axis([options,options],"horizontal",2)

    def test_many_maxima_do_not_fail_before_geometry_validation(self):
        from siliconre.grid_registration import _candidate_peaks
        scores=np.zeros((30,30))
        scores[::5,::5]=.97
        self.assertEqual(len(_candidate_peaks(scores,1,.72)),36)

    def test_4096_pixel_subsample_phase_recovers_original_translation(self):
        from PIL import ImageFilter
        noise=self.rng.integers(0,255,(700,7100),dtype=np.uint8)
        source=np.asarray(Image.fromarray(noise).filter(ImageFilter.GaussianBlur(2)))
        a=self.save(source[:640,:4096],"a")
        b=self.save(source[3:643,3001:7097],"b")
        match=estimate_translation(a,b,"horizontal")
        self.assertEqual((match["dx"],match["dy"]),(3001,3))
        self.assertGreater(match["score"],.99)

    def test_no_overlap_rejected(self):
        a = self.save(self.rng.integers(0,255,(128,128),dtype=np.uint8), "a")
        b = self.save(self.rng.integers(0,255,(128,128),dtype=np.uint8), "b")
        with self.assertRaisesRegex(ValueError, "Weak"):
            estimate_translation(a,b,"horizontal")

    def test_flat_rejected(self):
        path = self.save(np.full((128,128), 100, dtype=np.uint8), "flat")
        with self.assertRaisesRegex(ValueError, "Weak"):
            estimate_translation(path,path,"horizontal")

    def test_periodic_ambiguity_rejected(self):
        image = np.tile(self.rng.integers(0,255,(128,16),dtype=np.uint8), (1,8))
        path = self.save(image, "periodic")
        with self.assertRaisesRegex(ValueError, "Ambiguous|plausible"):
            estimate_translation(path,path,"horizontal")

    def test_disconnected_grid_rejected(self):
        path = self.save(self.rng.integers(0,255,(128,128),dtype=np.uint8), "a")
        with self.assertRaisesRegex(ValueError, "disconnected"):
            register_grid({(1,1):path,(1,3):path},(128,128))

if __name__ == "__main__":
    unittest.main()
