import tempfile
from io import BytesIO
import unittest
from pathlib import Path
from PIL import Image
from siliconre.tile_grid import parse_tile,plan_tile_grid,stitch_tile_grid

class TileGridTests(unittest.TestCase):
    def test_filename(self):
        self.assertEqual(parse_tile("Tile_013-017-000000_0-000.s0001_e00.tif"),(13,17))
        self.assertIsNone(parse_tile("other_013-017.tif"))
    def test_position_and_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for row,col,value in [(13,17,10),(13,19,30),(14,17,50)]:
                Image.new("L",(8,6),value).save(root/f"Tile_{row:03}-{col:03}-000000_0-000.s0001_e00.tif")
            paths=list(root.glob("Tile*"))
            plan=plan_tile_grid(paths)
            self.assertEqual(plan["size"],[24,12])
            self.assertEqual(plan["missing"],[(13,18),(14,18),(14,19)])
            target=root/"mosaic.tif"
            result=stitch_tile_grid(paths,target,align=False)
            self.assertEqual(len(result["missing_tiles"]),3)
            # Read through an in-memory buffer: Pillow's TIFF decoder can retain
            # a duplicate OS handle even after Image.close() on Windows.
            with Image.open(BytesIO(target.read_bytes())) as im:
                self.assertEqual(im.getpixel((1,1)),10)
                self.assertEqual(im.getpixel((9,1)),127)
                self.assertEqual(im.getpixel((17,1)),30)
                self.assertEqual(im.getpixel((1,7)),50)
            # Assert the mosaic is deletable before TemporaryDirectory cleanup.
            target.unlink()
            self.assertFalse(target.exists())
    def test_overlap_bigtiff_preserves_geometry_and_source_files(self):
        import numpy as np
        import tifffile
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            image=np.random.default_rng(45).integers(0,255,(256,435),dtype=np.uint8)
            a=root/"Tile_013-011_a.tif"
            b=root/"Tile_013-012_b.tif"
            Image.fromarray(image[:,:256]).save(a)
            Image.fromarray(image[:,179:]).save(b)
            originals=[a.read_bytes(),b.read_bytes()]
            output=root/"mosaic.tif"
            result=stitch_tile_grid([b,a],output)
            self.assertEqual(result["size"],[435,256])
            self.assertEqual(result["sources"][1]["position"],[179,0])
            self.assertGreater(result["registration_edges"][0]["score"],.99)
            np.testing.assert_array_equal(tifffile.imread(output),image)
            self.assertEqual([a.read_bytes(),b.read_bytes()], originals)
            output.unlink()

    def test_uncertain_alignment_writes_no_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            a=root/"Tile_001-001_a.tif"
            b=root/"Tile_001-002_b.tif"
            Image.new("L",(128,128),100).save(a)
            Image.new("L",(128,128),100).save(b)
            output=root/"mosaic.tif"
            with self.assertRaises(ValueError):
                stitch_tile_grid([a,b],output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".sources.json").exists())

    def test_duplicate_coordinate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            a=root/"Tile_001-002_a.tif"
            b=root/"Tile_001-002_b.tif"
            Image.new("L",(4,4)).save(a)
            Image.new("L",(4,4)).save(b)
            with self.assertRaisesRegex(ValueError,"Duplicate"):
                plan_tile_grid([a,b])

if __name__=="__main__":
    unittest.main()
