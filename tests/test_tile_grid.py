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
            result=stitch_tile_grid(paths,target)
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
