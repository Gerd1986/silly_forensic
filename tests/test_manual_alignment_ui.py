import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import tifffile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'siliconre'))
from app import MainWindow
from layer_alignment import load_alignment, similarity_matrix
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QDialog, QDoubleSpinBox, QInputDialog, QMessageBox


class ManualAlignmentUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_loads_mcp_route_and_saved_overlay(self):
        from project_edit import edit_project
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            path=folder/'image.tif'
            tifffile.imwrite(path,np.zeros((64,80),dtype=np.uint8))
            window=MainWindow()
            window.project=folder
            try:
                with window.db() as db:
                    db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
                    db.executemany('INSERT INTO layers VALUES(?,?)',[('M1',str(path)),('poly',str(path))])
                edit_project(folder,[dict(action='marker',layer='M1',x=5,y=6,label='pad'),
                    dict(action='evidence',kind='polyline',layer='M1',label='TPI route',
                         data={'points':[[5,6],[10,6],[10,20]]}),
                    dict(action='alignment',base_layer='M1',overlay_layer='poly',dx=3,dy=4)])
                window.current_layer='M1'
                window.overlay_layer='poly'
                window.refresh_project_evidence()
                self.assertEqual(window.overlay_layer,'poly')
                self.assertTrue(any(i.toolTip().startswith('TPI route') for i in window.viewer.scene_.items()))
                self.assertEqual(window.viewer.overlay.pos(),QPointF(3,4))
                self.assertEqual(window.actions_by_title['Refresh project evidence'].shortcut().toString(),'F5')
            finally:
                window.close()

    def test_live_preview_cancel_save_reload_and_autoalign_guard(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            path=folder/'image.tif'
            tifffile.imwrite(path,np.zeros((64,80),dtype=np.uint8))
            window=MainWindow()
            window.project=folder
            try:
                with window.db() as db:
                    db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
                    db.executemany('INSERT INTO layers VALUES (?,?)',[('M1',str(path)),('poly',str(path))])
                    db.execute('CREATE TABLE alignments(base TEXT,overlay TEXT,dx REAL,dy REAL,PRIMARY KEY(base,overlay))')
                window.current_layer='M1'
                window.viewer.show_image(path)
                window.viewer.show_overlay(path)
                window.overlay_layer='poly'
                def change(dialog):
                    boxes=dialog.findChildren(QDoubleSpinBox)
                    for box,value in zip(boxes,(35,-12,7.5,102)):
                        box.setValue(value)
                    point=window.viewer.overlay.mapToScene(QPointF(10,20))
                    expected=similarity_matrix(35,-12,7.5,1.02) @ [10,20,1]
                    np.testing.assert_allclose([point.x(),point.y()],expected[:2],atol=1e-7)
                def cancel(dialog):
                    change(dialog)
                    return QDialog.DialogCode.Rejected
                with patch.object(QDialog,'exec',cancel):
                    window.align_overlay()
                self.assertEqual(window.viewer.overlay.rotation(),0)
                self.assertEqual(window.viewer.overlay.scale(),1)
                with window.db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM alignments').fetchone()[0],0)
                def save(dialog):
                    change(dialog)
                    return QDialog.DialogCode.Accepted
                with patch.object(QDialog,'exec',save):
                    window.align_overlay()
                with window.db() as db:
                    np.testing.assert_allclose(load_alignment(db,'M1','poly'), similarity_matrix(35,-12,7.5,1.02))
                with patch.object(QInputDialog,'getItem',return_value=('poly',True)):
                    window.choose_overlay()
                self.assertAlmostEqual(window.viewer.overlay.rotation(),7.5)
                self.assertAlmostEqual(window.viewer.overlay.scale(),1.02)
                with patch('app.estimate_translation',side_effect=AssertionError('manual transform must survive')), patch.object(QMessageBox,'information'):
                    window.auto_align_overlay()
                self.assertAlmostEqual(window.viewer.overlay.rotation(),7.5)
            finally:
                if window.viewer.pix:
                    window.viewer.pix.close()
                if window.viewer.overlay:
                    window.viewer.overlay.close()
                window.close()

