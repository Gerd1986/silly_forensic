import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import tifffile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'siliconre'))
from PySide6.QtCore import Qt, QPointF
from PySide6.QtWidgets import QApplication
from app import MainWindow
import analysis_store as store
from analysis_ui import item_matrix
from layer_alignment import save_alignment,load_alignment


class AnalysisUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.image=self.root/'test.tif';tifffile.imwrite(self.image,np.full((200,300),100,np.uint8))
        self.window=MainWindow();w=self.window;w.project=self.root
        with w.db() as db:
            db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
            db.executemany('INSERT INTO layers VALUES (?,?)',[(n,str(self.image)) for n in ('M1','M2')])
            save_alignment(db,'M1','M2',3,4)
        w.viewer.show_image(self.image);w.current_layer='M1'

    def tearDown(self):
        v=self.window.viewer
        if v.pix:v.pix.close()
        if v.overlay:v.overlay.close()
        self.window.close();self.window.deleteLater();self.app.processEvents();self.tmp.cleanup()

    def region(self):
        with self.window.db() as db:
            return store.save_region(db,'M1','M2',[20,30,100,100],'local',
                                     matrix=[[1,.01,2],[0,1,5],[0,0,1]])

    def test_apply_clip_drag_undo_and_global_alignment_unchanged(self):
        w=self.window;r=self.region();w.research.apply_region(r)
        self.assertIsNotNone(w.viewer._overlay_clip)
        self.assertEqual(w.viewer._overlay_clip.rect().getRect(),(20,30,100,100))
        np.testing.assert_allclose(item_matrix(w.viewer.overlay),r['matrix'])
        start=w.viewer.overlay.pos();w.viewer.overlay.setPos(start+QPointF(7,8))
        w.save_overlay_drag(w.viewer.overlay,start)
        with w.db() as db:
            updated=store.get_record(db,r['id'])
            np.testing.assert_allclose(load_alignment(db,'M1','M2'),[[1,0,3],[0,1,4],[0,0,1]])
            self.assertAlmostEqual(updated['matrix'][0][2],9)
        w.research.undo(False)
        self.assertIsNone(w.research.active_region)
        self.assertIsNone(w.viewer._overlay_clip)
        w.research.apply_region(r)
        w.viewer.show_image(self.image)
        self.assertIsNone(w.research.active_region)

    def test_mcp_style_record_visible_and_trace_cancel(self):
        w=self.window
        with w.db() as db:
            store.save_evidence(db,'trace',[dict(layer='M1',points=[[10,10],[20,20]])],label='MCP route')
        w.research.refresh()
        self.assertEqual(w.research.records.count(),1)
        self.assertTrue(any(item.data(0)=='research' for item in w.viewer.scene_.items()))
        w.research.start_trace();w.research.add_trace_point(1,1);w.research.add_trace_point(2,2)
        w.viewer.set_mouse_mode('pointer')
        self.assertEqual(w.research.trace_points,[])
        self.assertFalse(any(item.data(0)=='research_preview' for item in w.viewer.scene_.items()))

    def test_trace_finish_saves_points_and_returns_to_pointer(self):
        w=self.window;w.research.start_trace();w.research.add_trace_point(1,1);w.research.add_trace_point(5,6)
        def accept(title,data):return data
        with patch.object(w.research,'json_dialog',side_effect=accept):w.research.finish_trace()
        self.assertEqual(w.viewer.mouse_mode,'pointer')
        with w.db() as db:
            self.assertEqual(store.list_records(db)[0]['geometry'][0]['points'],[[1,1],[5,6]])


if __name__=='__main__':unittest.main()
