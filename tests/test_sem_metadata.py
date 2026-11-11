import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageFilter, ImageOps
from siliconre.sem_metadata import read_fei_position,fei_grid_positions,fit_fei_positions
from siliconre.grid_registration import register_grid,estimate_translation

def metadata(x,y,pixel=2e-8,rotation=0,unit="m",stage_rotation=-.9):
    return f"""<Metadata>
<StageSettings><StagePosition><X>{x}</X><Y>{y}</Y><Rotation>{stage_rotation}</Rotation></StagePosition></StageSettings>
<BinaryResult><PixelSize><X unit="{unit}">{pixel}</X><Y unit="{unit}">{pixel}</Y></PixelSize></BinaryResult>
<ScanSettings><ScanRotation>{rotation}</ScanRotation></ScanSettings>
<Optics><BeamShift><X>0</X><Y>0</Y></BeamShift></Optics>
</Metadata>"""

class FeiMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def save(self,name,array,x=0,y=0,**kwargs):
        path=self.root/name
        Image.fromarray(array).save(path,tiffinfo={34683:metadata(x,y,**kwargs)})
        return path
    def test_calibrated_stage_positions_invert_image_y(self):
        a=self.save("a.tif",np.zeros((128,128),np.uint8),.005,-.004)
        b=self.save("b.tif",np.zeros((128,128),np.uint8),.005+80*2e-8,-.004)
        c=self.save("c.tif",np.zeros((128,128),np.uint8),.005,-.004-90*2e-8)
        positions,edges=fei_grid_positions({(1,1):a,(1,2):b,(2,1):c},(128,128))
        np.testing.assert_allclose(positions[(1,2)],(80,0),atol=1e-6)
        np.testing.assert_allclose(positions[(2,1)],(0,90),atol=1e-6)
        self.assertEqual(len(edges),2)
        self.assertAlmostEqual(read_fei_position(a)["pixel_size_m"][0],2e-8)
    def test_unknown_units_and_scan_rotation_are_not_guessed(self):
        for kwargs in ({"unit":"nm"},{"rotation":.1},{"stage_rotation":float("nan")}):
            path=self.save("a.tif",np.zeros((128,128),np.uint8),**kwargs)
            self.assertIsNone(read_fei_position(path))
    def test_changed_stage_rotation_rejected(self):
        a=self.save("a.tif",np.zeros((128,128),np.uint8))
        b=self.save("b.tif",np.zeros((128,128),np.uint8),80*2e-8,0,stage_rotation=.5)
        self.assertIsNone(fei_grid_positions({(1,1):a,(1,2):b},(128,128)))
    def test_flat_images_use_measured_stage_positions_with_review_flag(self):
        a=self.save("a.tif",np.full((128,128),100,np.uint8))
        b=self.save("b.tif",np.full((128,128),100,np.uint8),80*2e-8,0)
        positions,edges=register_grid({(1,1):a,(1,2):b},(128,128))
        self.assertEqual(positions[(1,2)],(80,0))
        self.assertEqual(edges[0]["selection"],"metadata_only_weak_image")
        self.assertTrue(edges[0]["review_required"])
        self.assertEqual(edges[0]["position_hint"],"fei_xml_34683")
    def test_sem_noise_and_stage_error_are_refined(self):
        rng=np.random.default_rng(117)
        texture=ImageOps.autocontrast(Image.fromarray(
            rng.integers(0,255,(280,450),dtype=np.uint8)).filter(ImageFilter.GaussianBlur(3)))
        world=np.asarray(texture,dtype=float)
        def shot(crop):
            return np.clip(crop+rng.normal(0,15,crop.shape),0,255).astype(np.uint8)
        a=self.save("a.tif",shot(world[:256,:256]))
        b=self.save("b.tif",shot(world[5:261,176:432]),180*2e-8,0)
        positions,edges=register_grid({(1,1):a,(1,2):b},(256,256))
        self.assertEqual(edges[0]["selection"],"image_refined")
        self.assertLessEqual(abs(edges[0]["dx"]-176),1)
        self.assertLessEqual(abs(edges[0]["dy"]-5),1)
        self.assertGreater(edges[0]["score"],.72)
    def test_excessive_global_drift_reverts_to_calibrated_positions(self):
        initial={(1,1):(0.,0.),(1,2):(80.,0.)}
        observations=[{"first":[1,1],"second":[1,2],"dx":200,"dy":0,
                       "selection":"image_refined"}]
        positions,edges=fit_fei_positions(initial,observations,(128,128))
        self.assertEqual(positions[(1,2)],(80,0))
        self.assertEqual(edges[0]["selection"],"metadata_only_global_drift_limit")
        self.assertTrue(edges[0]["review_required"])
    def test_fei_bigtiff_export_records_physical_layout(self):
        import tifffile
        from siliconre.tile_grid import stitch_tile_grid
        a=self.save("Tile_001-001.tif",np.full((128,128),25,np.uint8))
        b=self.save("Tile_001-002.tif",np.full((128,128),50,np.uint8),80*2e-8,0)
        target=self.root/"mosaic.tif"
        result=stitch_tile_grid([a,b],target)
        self.assertEqual(result["size"],[208,128])
        self.assertEqual(result["method"],"fei_stage_metadata_bigtiff")
        self.assertEqual(result["metadata_only_edges"],1)
        self.assertEqual(result["review_required_edges"],1)
        data=tifffile.imread(target)
        self.assertEqual(int(data[64,40]),25)
        self.assertEqual(int(data[64,120]),50)

if __name__=="__main__":
    unittest.main()