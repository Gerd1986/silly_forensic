import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
import numpy as np
import tifffile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'siliconre'))
from PySide6.QtCore import QPointF,Qt
from PySide6.QtTest import QTest,QSignalSpy
from PySide6.QtWidgets import QApplication
from app import MainWindow
from pairing_ui import PairDialog,ContactReviewDialog,SourceView
from image_regions import png_bytes
import analysis_store as store


class PairingUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.path=self.root/'img.tif';tifffile.imwrite(self.path,np.full((300,300),120,np.uint8))
        self.w=MainWindow();self.w.project=self.root
        with self.w.db() as db:
            db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
            db.executemany('INSERT INTO layers VALUES (?,?)',[(n,str(self.path)) for n in ('M1','M2','M3')])
        self.w.viewer.show_image(self.path);self.w.current_layer='M1';self.dialogs=[]

    def tearDown(self):
        for d in self.dialogs:d.close();d.deleteLater()
        self.w.viewer.pix.close();self.w.close();self.w.deleteLater();self.app.processEvents();self.tmp.cleanup()

    def pair_dialog(self,**kw):
        with patch.object(self.w.research,'visible_roi',return_value=[0,0,200,200]):d=PairDialog(self.w,**kw)
        self.dialogs.append(d);return d

    def test_alternating_clicks_fit_and_heldout_pair_without_json(self):
        d=self.pair_dialog()
        d.pick_source(1,1);self.assertEqual(d.pairs,[])
        for target,source in (([20,20],[25,27]),([120,20],[125,27])):
            d.pick_target(*target);d.pick_source(*source)
        d.check.setChecked(True);d.pick_target(70,101);d.pick_source(75,107)
        m=d.calculate();np.testing.assert_allclose(m,[[1,0,-5],[0,1,-7],[0,0,1]],atol=1e-10)
        self.assertAlmostEqual(float(d.table.item(2,3).text()),1)
        d.note.setPlainText('Independent third landmark checked');d.status.setCurrentText('confirmed');d.save()
        self.assertIsNotNone(d.saved);self.assertEqual(d.saved['reference_points'][2]['check'],True)
        self.assertEqual(d.saved['status'],'confirmed')

    def test_source_coordinate_mapping_and_real_click(self):
        v=SourceView();self.dialogs.append(v)
        raw=png_bytes(np.zeros((100,200),np.uint8),np.ones((100,200),bool))
        v.set_source(raw,[1000,2000,400,200],[200,100]);v.resize(600,400);v.show();self.app.processEvents()
        self.assertEqual(v.source_point(QPointF(20,30)),[1040,2060])
        spy=QSignalSpy(v.pointPicked);point=v.mapFromScene(QPointF(20,30))
        QTest.mouseClick(v.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        self.assertEqual(spy.count(),1)
        x,y=spy.at(0);self.assertAlmostEqual(x,1040,delta=2);self.assertAlmostEqual(y,2060,delta=2)

    def test_contact_points_saved_without_assuming_same_coordinates(self):
        d=self.pair_dialog(contact=True);d.pick_target(10,20);d.pick_source(30,40);d.save()
        self.assertEqual(d.saved['geometry'],[dict(layer='M1',points=[[10,20]]),dict(layer='M2',points=[[30,40]])])
        self.assertEqual(d.saved['status'],'candidate')

    def test_cancel_does_not_write_and_check_role_can_change(self):
        d=self.pair_dialog();d.pick_target(10,10);d.pick_source(15,17)
        d.table.item(0,0).setCheckState(Qt.CheckState.Checked);self.assertTrue(d.pairs[0]['check'])
        d.reject()
        with self.w.db() as db:self.assertEqual(store.list_records(db),[])

    def test_model_change_clears_old_residuals_and_review_status(self):
        d=self.pair_dialog()
        for target,source in (([20,20],[25,27]),([120,20],[125,27]),([20,120],[25,127])):
            d.pick_target(*target);d.pick_source(*source)
        d.calculate();d.status.setCurrentText('confirmed');d.mode.setCurrentText('affine')
        self.assertEqual(d.status.currentText(),'candidate')
        self.assertEqual(d.table.item(0,3).text(),'—')

    def test_contact_review_requires_reload_after_settings_and_keeps_snapshot(self):
        with self.w.db() as db:c=store.save_evidence(db,'contact',[dict(layer='M1',points=[[20,20]]),dict(layer='M2',points=[[25,27]])])
        d=ContactReviewDialog(self.w,c);self.dialogs.append(d)
        self.assertEqual(d.split.count(),2)
        d.size.setValue(128)
        with self.assertRaisesRegex(ValueError,'Reload'):d.save()
        d.reload();d.note.setPlainText('Inspected source images');d.status.setCurrentText('confirmed');d.save()
        self.assertEqual(d.saved['provenance']['contact_review']['crop_size'],128)
        self.assertEqual(d.saved['geometry'],c['geometry'])


if __name__=='__main__':unittest.main()
