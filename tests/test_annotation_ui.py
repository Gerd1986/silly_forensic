import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'siliconre'))
from app import MainWindow
from PySide6.QtWidgets import QApplication, QMessageBox


class AnnotationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_project_connections_close_and_commit_or_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            window = MainWindow()
            window.project = Path(folder)
            try:
                with window.db() as db:
                    db.execute('CREATE TABLE example (value TEXT)')
                    db.execute("INSERT INTO example VALUES ('saved')")
                with self.assertRaises(sqlite3.ProgrammingError):
                    db.execute('SELECT 1')
                with self.assertRaises(RuntimeError):
                    with window.db() as failed:
                        failed.execute("INSERT INTO example VALUES ('rolled back')")
                        raise RuntimeError('abort')
                with self.assertRaises(sqlite3.ProgrammingError):
                    failed.execute('SELECT 1')
                with window.db() as verify:
                    self.assertEqual(verify.execute('SELECT value FROM example').fetchall(), [('saved',)])
            finally:
                window.close()

    def test_cancel_backup_failure_and_clear_keep_view_and_overlay(self):
        with tempfile.TemporaryDirectory() as folder:
            window = MainWindow()
            window.project = Path(folder)
            with window.db() as db:
                db.execute('CREATE TABLE markers (id INTEGER PRIMARY KEY, layer TEXT)')
                db.executemany('INSERT INTO markers VALUES (?,?)', [(1,'M1'), (2,'poly')])
            base = window.viewer.scene_.addRect(0, 0, 100, 100)
            overlay = window.viewer.scene_.addRect(10, 20, 100, 100)
            window.viewer.overlay = overlay
            window.viewer.draw_marker(5, 5, 'TCK')
            transform = window.viewer.transform()
            try:
                menus = [a.text() for a in window.menuBar().actions()]
                self.assertEqual(menus, ['Project','Layers','Annotations','Traces','Vias','Devices','Analysis','Export','View'])
                all_titles = [a.text() for m in window.menuBar().actions() for a in m.menu().actions() if not a.isSeparator()]
                self.assertEqual(len(all_titles), len(set(all_titles)))
                self.assertEqual(set(all_titles), set(window.actions_by_title))
                with patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.Cancel):
                    window.clear_all_annotations()
                self.assertFalse((Path(folder) / 'annotation_backups').exists())
                with patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.Yes), patch('app.backup_annotations', side_effect=OSError('disk full')):
                    window.clear_all_annotations()
                with window.db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM markers').fetchone()[0], 2)
                with patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.Yes):
                    window.actions_by_title['Clear all annotations...'].trigger()
                with window.db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM markers').fetchone()[0], 0)
                self.assertEqual(set(window.viewer.scene_.items()), {base, overlay})
                self.assertIs(window.viewer.overlay, overlay)
                self.assertEqual(window.viewer.transform(), transform)
                self.assertEqual(len(list((Path(folder) / 'annotation_backups').glob('*.sqlite3'))), 1)
            finally:
                window.close()
