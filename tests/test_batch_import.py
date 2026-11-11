import tempfile
import unittest
from pathlib import Path
from siliconre.batch_import import images_in_folder,plan_import

class BatchImportTests(unittest.TestCase):
    def test_names_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/"M1.PNG"
            b=Path(tmp)/"m1.tif"
            a.touch()
            b.touch()
            result=plan_import([a,b,a],["M1"])
            self.assertEqual(len(result),2)
            self.assertEqual(len({name.casefold() for name,_ in result}),2)
            self.assertTrue(all(name.casefold()!="m1" for name,_ in result))
    def test_folder_recursion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"a.tiff").touch()
            (root/"ignore.txt").touch()
            (root/"sub").mkdir()
            (root/"sub"/"b.jpg").touch()
            self.assertEqual(len(images_in_folder(root)),1)
            self.assertEqual(len(images_in_folder(root,True)),2)
    def test_missing_folder(self):
        with self.assertRaises(ValueError):
            images_in_folder("/this/folder/does/not/exist")

if __name__=="__main__":
    unittest.main()
