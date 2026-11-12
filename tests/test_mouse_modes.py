import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import tifffile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'siliconre'))
from app import MainWindow
from layer_alignment import load_alignment, save_alignment, alignment_parameters
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox


class MouseModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.path = self.root/'image.tif'
        tifffile.imwrite(self.path, np.full((800,1200),100,dtype=np.uint8))
        self.window = MainWindow()
        w = self.window
        w.project = self.root
        with w.db() as db:
            db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
            db.executemany('INSERT INTO layers VALUES (?,?)', [('M1',str(self.path)),('poly',str(self.path))])
            db.execute('CREATE TABLE markers(id INTEGER PRIMARY KEY,layer TEXT,x REAL,y REAL,label TEXT)')
            db.execute("INSERT INTO markers VALUES (1,'M1',100,100,'keep me')")
            save_alignment(db,'M1','poly',10,15,3,1.02)
        w.viewer.show_image(self.path)
        w.current_layer='M1'
        w.viewer.show_overlay(self.path,10,15,.5,3,1.02)
        w.overlay_layer='poly'
        w.show()
        w.activateWindow()
        w.viewer.setFocus()
        self.app.processEvents()
        w.viewer.resetTransform()
        w.viewer.scale(2,2)
        w.viewer.centerOn(500,350)
        self.app.processEvents()
        self.viewport = w.viewer.viewport()
        self.start = self.viewport.rect().center()
        self.end = self.start+QPoint(40,20)

    def tearDown(self):
        self.window.viewer.cancel_overlay_drag()
        QTest.mouseRelease(self.viewport,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,self.end)
        if self.window.viewer.pix:
            self.window.viewer.pix.close()
        if self.window.viewer.overlay:
            self.window.viewer.overlay.close()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.folder.cleanup()

    def saved(self):
        with self.window.db() as db:
            return alignment_parameters(load_alignment(db,'M1','poly'))

    def press_drag(self):
        self.window.actions_by_title['Move overlay'].trigger()
        QTest.mousePress(self.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.start)
        self.assertIsNotNone(self.window.viewer._overlay_drag)
        self.assertEqual(self.viewport.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        QTest.mouseMove(self.viewport,self.end)

    def test_mark_toggle_pointer_icon_and_escape_preserve_annotations(self):
        w = self.window
        self.assertTrue(w.mouse_actions['pointer'].isChecked())
        for action in w.mouse_actions.values():
            self.assertFalse(action.icon().isNull())
        w.actions_by_title['Mark point'].trigger()
        self.assertTrue(w.viewer.mark_mode)
        self.assertTrue(w.mouse_actions['mark'].isChecked())
        self.assertEqual(self.viewport.cursor().shape(),Qt.CursorShape.CrossCursor)
        with patch.object(w,'add_marker') as marker:
            QTest.mouseClick(self.viewport,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,self.start)
            marker.assert_called_once()
            w.actions_by_title['Pointer / neutral'].trigger()
            QTest.mouseClick(self.viewport,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,self.start)
            self.assertEqual(marker.call_count,1)
        w.actions_by_title['Mark point'].trigger()
        w.actions_by_title['Mark point'].trigger()
        self.assertFalse(w.viewer.mark_mode)
        w.actions_by_title['Mark point'].trigger()
        QTest.keyClick(self.viewport,Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(w.viewer.mark_mode)
        self.assertTrue(w.mouse_actions['pointer'].isChecked())
        self.assertEqual(self.viewport.cursor().shape(),Qt.CursorShape.ArrowCursor)
        with w.db() as db:
            self.assertEqual(db.execute('SELECT label FROM markers').fetchall(),[('keep me',)])

    def test_drag_uses_scene_coordinates_and_saves_once_on_release(self):
        w=self.window
        original=QPointF(w.viewer.overlay.pos())
        background=QPointF(w.viewer.pix.pos())
        view_transform=w.viewer.transform()
        delta=w.viewer.mapToScene(self.end)-w.viewer.mapToScene(self.start)
        self.press_drag()
        self.assertAlmostEqual(w.viewer.overlay.pos().x(),original.x()+delta.x(),places=6)
        np.testing.assert_allclose(self.saved(),[10,15,3,1.02])
        with patch('app.save_alignment',wraps=save_alignment) as save:
            QTest.mouseRelease(self.viewport,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,self.end)
            self.assertEqual(save.call_count,1)
        np.testing.assert_allclose(self.saved(),[10+delta.x(),15+delta.y(),3,1.02],atol=1e-7)
        self.assertEqual(w.viewer.pix.pos(),background)
        self.assertEqual(w.viewer.transform(),view_transform)
        self.assertEqual(self.viewport.cursor().shape(),Qt.CursorShape.OpenHandCursor)

    def test_escape_cancels_unsaved_drag(self):
        self.press_drag()
        QTest.keyClick(self.viewport,Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertIsNone(self.window.viewer._overlay_drag)
        self.assertEqual(self.window.viewer.overlay.pos(),QPointF(10,15))
        self.assertEqual(self.window.viewer.mouse_mode,'pointer')
        QTest.mouseRelease(self.viewport,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,self.end)
        np.testing.assert_allclose(self.saved(),[10,15,3,1.02])

    def test_save_failure_restores_overlay_and_database(self):
        self.press_drag()
        with patch('app.save_alignment',side_effect=sqlite3.OperationalError('locked')), patch.object(QMessageBox,'warning') as warning:
            QTest.mouseRelease(self.viewport,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,self.end)
            warning.assert_called_once()
        self.assertEqual(self.window.viewer.overlay.pos(),QPointF(10,15))
        np.testing.assert_allclose(self.saved(),[10,15,3,1.02])

    def test_pointer_or_layer_switch_cancels_drag_and_missing_overlay_is_neutral(self):
        self.press_drag()
        self.window.actions_by_title['Pointer / neutral'].trigger()
        self.assertEqual(self.window.viewer.overlay.pos(),QPointF(10,15))
        QTest.mouseRelease(self.viewport,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,self.end)
        self.press_drag()
        self.window.viewer.show_image(self.path)
        self.assertEqual(self.window.viewer.mouse_mode,'pointer')
        self.assertIsNone(self.window.viewer._overlay_drag)
        with patch.object(QMessageBox,'information'):
            self.window.actions_by_title['Move overlay'].trigger()
        self.assertEqual(self.window.viewer.mouse_mode,'pointer')
        np.testing.assert_allclose(self.saved(),[10,15,3,1.02])
