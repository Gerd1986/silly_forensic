"""SiliconRE SEM delayering desktop viewer (prototype)."""
import csv
import json
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QAction, QPixmap, QPen, QColor
from PySide6.QtWidgets import (
    QApplication, QGraphicsEllipseItem, QGraphicsPixmapItem, QGraphicsScene,
    QGraphicsView, QFileDialog, QInputDialog, QMainWindow, QMessageBox,
    QDockWidget, QListWidget, QToolBar, QSlider, QLabel
)

from tiles import TiledImage
from registration import estimate_translation
from pyramid import build_pyramid
from traces import detect_traces
from candidates import CandidateRect, ensure_schema
from netgraph import add_link, list_links, connected_groups, ensure_net_schema

class Viewer(QGraphicsView):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.pix = None
        self.overlay = None
        self.mark_mode = False

    def show_image(self, path):
        if self.pix:
            self.pix.close()
        if self.overlay:
            self.overlay.close()
        self.scene_.clear()
        self.pix = TiledImage(path)
        self.scene_.addItem(self.pix)
        self.overlay = None
        self.scene_.setSceneRect(self.pix.boundingRect())
        self.fitInView(self.scene_.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def show_overlay(self, path, dx=0, dy=0, opacity=0.5):
        if self.overlay:
            self.scene_.removeItem(self.overlay)
            self.overlay.close()
        self.overlay = TiledImage(path)
        self.overlay.setZValue(1)
        self.overlay.setPos(dx, dy)
        self.overlay.setOpacity(opacity)
        self.scene_.addItem(self.overlay)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if self.mark_mode and self.pix and event.button() == Qt.MouseButton.LeftButton:
            p = self.mapToScene(event.position().toPoint())
            if self.pix.boundingRect().contains(self.pix.mapFromScene(p)):
                self.window.add_marker(p.x(), p.y())
                return
        super().mousePressEvent(event)

    def draw_marker(self, x, y, label):
        r = 5
        dot = self.scene_.addEllipse(x-r, y-r, 2*r, 2*r, QPen(QColor("#ffcc00"), 2))
        dot.setToolTip(label)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SiliconRE 0.4 — SEM Delayering")
        self.resize(1300, 850)
        self.project = None
        self.current_layer = None
        self.viewer = Viewer(self)
        self.setCentralWidget(self.viewer)
        self.layers = QListWidget()
        self.overlay_layer = None
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(50)
        self.opacity.valueChanged.connect(self.update_opacity)
        self.layers.currentTextChanged.connect(self.select_layer)
        dock = QDockWidget("Layers", self)
        dock.setWidget(self.layers)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        opacity_dock = QDockWidget("Overlay opacity", self)
        opacity_dock.setWidget(self.opacity)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, opacity_dock)
        bar = QToolBar("Project", self)
        self.addToolBar(bar)
        for title, callback in [
            ("New project", self.new_project),
            ("Open project", self.open_project),
            ("Add SEM image", self.import_image),
            ("Mark point", self.toggle_mark),
            ("Overlay layer", self.choose_overlay),
            ("Align overlay (dx,dy)", self.align_overlay),
            ("Auto-align overlay", self.auto_align_overlay),
            ("Build TIFF pyramid", self.create_pyramid),
            ("Detect trace candidates", self.detect_candidates),
            ("Accept selected", lambda: self.review_selected("accepted")),
            ("Reject selected", lambda: self.review_selected("rejected")),
            ("Delete selected", self.delete_selected),
            ("Resize selected", self.resize_selected),
            ("Export candidates CSV", self.export_candidates),
            ("Connect selected", self.connect_selected),
            ("Remove selected links", self.remove_selected_links),
            ("Export manual nets JSON", self.export_nets),
            ("Fit", lambda: self.viewer.fitInView(self.viewer.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)),
        ]:
            act = QAction(title, self)
            act.triggered.connect(callback)
            bar.addAction(act)
        self.statusBar().showMessage("Create or open a project to begin")

    def db(self):
        return sqlite3.connect(str(self.project / "project.sqlite3"))

    def new_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose project directory")
        if not folder:
            return
        self.project = Path(folder)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS layers (name TEXT PRIMARY KEY, path TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS markers (id INTEGER PRIMARY KEY, layer TEXT, x REAL, y REAL, label TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS alignments (base TEXT, overlay TEXT, dx REAL, dy REAL, PRIMARY KEY(base,overlay))")
            ensure_schema(db)
        self.refresh_layers()

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Open project directory")
        if not folder:
            return
        self.project = Path(folder)
        if not (self.project / "project.sqlite3").exists():
            QMessageBox.warning(self, "Invalid project", "No project.sqlite3 found")
            self.project = None
            return
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS alignments (base TEXT, overlay TEXT, dx REAL, dy REAL, PRIMARY KEY(base,overlay))")
        self.refresh_layers()

    def refresh_layers(self):
        self.layers.clear()
        with self.db() as db:
            for (name,) in db.execute("SELECT name FROM layers ORDER BY name"):
                self.layers.addItem(name)

    def import_image(self):
        if not self.project:
            QMessageBox.information(self, "Project required", "Create or open a project first")
            return
        path, _ = QFileDialog.getOpenFileName(self, "SEM image", "", "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "Layer name", "Layer (e.g. M1, VIA1, POLY):")
        if not ok or not name.strip():
            return
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO layers(name,path) VALUES (?,?)", (name.strip(), path))
        self.refresh_layers()
        self.layers.setCurrentRow(self.layers.count()-1)

    def select_layer(self, name):
        if not name or not self.project:
            return
        with self.db() as db:
            row = db.execute("SELECT path FROM layers WHERE name=?", (name,)).fetchone()
            markers = db.execute("SELECT x,y,label FROM markers WHERE layer=?", (name,)).fetchall()
            ensure_schema(db)
            candidates = db.execute("SELECT id,layer,x,y,width,height,status FROM trace_candidates WHERE layer=?", (name,)).fetchall()
        if not row:
            return
        try:
            self.viewer.show_image(row[0])
            self.current_layer = name
            self.overlay_layer = None
            for x,y,label in markers:
                self.viewer.draw_marker(x,y,label)
            for candidate in candidates:
                self.show_candidate(candidate)
            self.refresh_link_lines()
            self.statusBar().showMessage(name + " — " + row[0])
        except Exception as exc:
            QMessageBox.warning(self, "Image error", str(exc))

    def choose_overlay(self):
        if not self.current_layer:
            return
        with self.db() as db:
            options = [row[0] for row in db.execute("SELECT name FROM layers WHERE name != ? ORDER BY name", (self.current_layer,))]
        if not options:
            return
        name, ok = QInputDialog.getItem(self, "Overlay layer", "Layer:", options, 0, False)
        if not ok:
            return
        with self.db() as db:
            path = db.execute("SELECT path FROM layers WHERE name=?", (name,)).fetchone()[0]
            row = db.execute("SELECT dx,dy FROM alignments WHERE base=? AND overlay=?", (self.current_layer, name)).fetchone()
        self.viewer.show_overlay(path, *(row or (0, 0)), self.opacity.value()/100)
        self.overlay_layer = name

    def align_overlay(self):
        if not self.overlay_layer or not self.viewer.overlay:
            return
        dx, ok = QInputDialog.getDouble(self, "Translation registration", "X offset in pixels:", self.viewer.overlay.pos().x(), -1000000, 1000000, 2)
        if not ok:
            return
        dy, ok = QInputDialog.getDouble(self, "Translation registration", "Y offset in pixels:", self.viewer.overlay.pos().y(), -1000000, 1000000, 2)
        if not ok:
            return
        self.viewer.overlay.setPos(dx, dy)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO alignments(base,overlay,dx,dy) VALUES (?,?,?,?)",
                       (self.current_layer, self.overlay_layer, dx, dy))

    def auto_align_overlay(self):
        if not self.current_layer or not self.overlay_layer:
            QMessageBox.information(self, "Select overlay", "Select an overlay layer first")
            return
        with self.db() as db:
            base = db.execute("SELECT path FROM layers WHERE name=?", (self.current_layer,)).fetchone()[0]
            overlay = db.execute("SELECT path FROM layers WHERE name=?", (self.overlay_layer,)).fetchone()[0]
        try:
            dx, dy, confidence = estimate_translation(base, overlay)
            if confidence < 0.1:
                QMessageBox.warning(self, "Weak registration", f"Low phase-correlation response ({confidence:.3f}). No change applied.")
                return
            self.viewer.overlay.setPos(dx, dy)
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO alignments(base,overlay,dx,dy) VALUES (?,?,?,?)",
                           (self.current_layer, self.overlay_layer, dx, dy))
            self.statusBar().showMessage(f"Auto alignment: dx={dx:.1f}, dy={dy:.1f}, response={confidence:.3f}")
        except Exception as exc:
            QMessageBox.warning(self, "Registration failed", str(exc))

    def create_pyramid(self):
        if not self.current_layer:
            return
        with self.db() as db:
            source = db.execute("SELECT path FROM layers WHERE name=?", (self.current_layer,)).fetchone()[0]
        destination, _ = QFileDialog.getSaveFileName(self, "Save tiled pyramid", str(self.project / (self.current_layer + "_pyramid.tif")), "TIFF (*.tif)")
        if not destination:
            return
        try:
            build_pyramid(source, destination)
            self.statusBar().showMessage("Pyramid saved: " + destination)
        except Exception as exc:
            QMessageBox.warning(self, "Pyramid failed", str(exc))

    def detect_candidates(self):
        if not self.current_layer:
            return
        with self.db() as db:
            image_path = db.execute("SELECT path FROM layers WHERE name=?", (self.current_layer,)).fetchone()[0]
        view_rect = self.viewer.mapToScene(self.viewer.viewport().rect()).boundingRect()
        image_rect = self.viewer.pix.boundingRect()
        roi = view_rect.intersected(image_rect)
        x, y = max(0, int(roi.left())), max(0, int(roi.top()))
        width, height = int(roi.width()), int(roi.height())
        if width < 1 or height < 1:
            return
        if width * height > 12000000:
            QMessageBox.warning(self, "ROI too large", "Zoom in to a region smaller than 12 megapixels")
            return
        threshold, ok = QInputDialog.getInt(self, "Trace candidates", "Threshold (0 = Otsu):", 0, 0, 255)
        if not ok:
            return
        try:
            regions = detect_traces(image_path, (x, y, width, height), threshold)
            with self.db() as db:
                ensure_schema(db)
                for region in regions:
                    cursor = db.execute(
                        "INSERT INTO trace_candidates(layer,x,y,width,height,status) VALUES (?,?,?,?,?,?)",
                        (self.current_layer, region["x"], region["y"], region["width"], region["height"], "candidate"))
                    self.show_candidate((cursor.lastrowid, self.current_layer, region["x"], region["y"],
                                         region["width"], region["height"], "candidate"))
            self.statusBar().showMessage(f"{len(regions)} unverified trace candidates in visible region")
        except Exception as exc:
            QMessageBox.warning(self, "Trace detection failed", str(exc))

    def show_candidate(self, row):
        item = CandidateRect(*row, self.save_candidate)
        self.viewer.scene_.addItem(item)

    def save_candidate(self, item):
        with self.db() as db:
            db.execute("UPDATE trace_candidates SET x=?,y=?,width=?,height=?,status=? WHERE id=? AND layer=?",
                       (item.pos().x(), item.pos().y(), item.rect().width(), item.rect().height(),
                        item.status, item.row_id, item.layer))
        self.refresh_link_lines()

    def review_selected(self, status):
        for item in self.viewer.scene_.selectedItems():
            if isinstance(item, CandidateRect):
                item.set_status(status)

    def delete_selected(self):
        items = [item for item in self.viewer.scene_.selectedItems() if isinstance(item, CandidateRect)]
        with self.db() as db:
            for item in items:
                ensure_net_schema(db)
                db.execute("DELETE FROM candidate_links WHERE layer=? AND (source_id=? OR target_id=?)", (item.layer, item.row_id, item.row_id))
                db.execute("DELETE FROM trace_candidates WHERE id=? AND layer=?", (item.row_id, item.layer))
                self.viewer.scene_.removeItem(item)
        self.refresh_link_lines()

    def resize_selected(self):
        items = [item for item in self.viewer.scene_.selectedItems() if isinstance(item, CandidateRect)]
        if len(items) != 1:
            QMessageBox.information(self, "Resize", "Select exactly one candidate rectangle")
            return
        item = items[0]
        width, ok = QInputDialog.getDouble(self, "Resize candidate", "Width (pixels):", item.rect().width(), 1, 1000000, 1)
        if not ok:
            return
        height, ok = QInputDialog.getDouble(self, "Resize candidate", "Height (pixels):", item.rect().height(), 1, 1000000, 1)
        if not ok:
            return
        item.setRect(0, 0, width, height)
        self.save_candidate(item)
        self.refresh_link_lines()

    def export_candidates(self):
        if not self.current_layer:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export candidates CSV",
                                              str(self.project / (self.current_layer + "_candidates.csv")),
                                              "CSV (*.csv)")
        if not path:
            return
        with self.db() as db:
            ensure_schema(db)
            rows = db.execute("SELECT id,layer,x,y,width,height,status FROM trace_candidates WHERE layer=? ORDER BY id",
                              (self.current_layer,)).fetchall()
        with open(path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(("id", "layer", "x", "y", "width", "height", "status"))
            writer.writerows(rows)
        self.statusBar().showMessage(f"Exported {len(rows)} candidates to {path}")

    def refresh_link_lines(self):
        if not self.current_layer:
            return
        for item in list(self.viewer.scene_.items()):
            if item.data(0) == "manual_link":
                self.viewer.scene_.removeItem(item)
        candidates = {item.row_id: item for item in self.viewer.scene_.items()
                      if isinstance(item, CandidateRect)}
        with self.db() as db:
            links = list_links(db, self.current_layer)
        for edge in links:
            a = candidates.get(edge["source_id"])
            b = candidates.get(edge["target_id"])
            if a is None or b is None:
                continue
            pa = a.mapToScene(a.rect().center())
            pb = b.mapToScene(b.rect().center())
            line = self.viewer.scene_.addLine(pa.x(), pa.y(), pb.x(), pb.y(), QPen(QColor("#ff66ff"), 2))
            line.setZValue(4)
            line.setData(0, "manual_link")
            line.setToolTip("Manual visual connection; electrical continuity unverified")

    def connect_selected(self):
        items = [item for item in self.viewer.scene_.selectedItems() if isinstance(item, CandidateRect)]
        if len(items) != 2:
            QMessageBox.information(self, "Connect", "Select exactly two accepted candidates (Ctrl+click)")
            return
        try:
            with self.db() as db:
                add_link(db, self.current_layer, items[0].row_id, items[1].row_id)
            self.refresh_link_lines()
            self.statusBar().showMessage("Manual connection saved (not electrically verified)")
        except ValueError as exc:
            QMessageBox.warning(self, "Connect", str(exc))

    def remove_selected_links(self):
        items = [item for item in self.viewer.scene_.selectedItems() if isinstance(item, CandidateRect)]
        if len(items) != 2:
            QMessageBox.information(self, "Remove connection", "Select exactly two connected candidates")
            return
        a, b = sorted((items[0].row_id, items[1].row_id))
        with self.db() as db:
            ensure_net_schema(db)
            db.execute("DELETE FROM candidate_links WHERE layer=? AND source_id=? AND target_id=?",
                       (self.current_layer, a, b))
        self.refresh_link_lines()
        self.statusBar().showMessage("Manual connection removed")

    def export_nets(self):
        if not self.current_layer:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export manual nets",
                                              str(self.project / (self.current_layer + "_manual_nets.json")),
                                              "JSON (*.json)")
        if not path:
            return
        with self.db() as db:
            payload = {"layer": self.current_layer, "verified": False,
                       "note": "Manual visual links only; electrical continuity unverified",
                       "links": list_links(db, self.current_layer),
                       "groups": connected_groups(db, self.current_layer)}
        with open(path, "w", encoding="utf-8") as out:
            json.dump(payload, out, indent=2)
        self.statusBar().showMessage("Manual connectivity exported")

    def update_opacity(self, value):
        if self.viewer.overlay:
            self.viewer.overlay.setOpacity(value/100)

    def toggle_mark(self):
        self.viewer.mark_mode = not self.viewer.mark_mode
        self.statusBar().showMessage("Click to mark a point" if self.viewer.mark_mode else "Pan mode")

    def add_marker(self, x, y):
        if not self.current_layer:
            return
        label, ok = QInputDialog.getText(self, "Annotation", "Label:")
        if not ok:
            return
        with self.db() as db:
            db.execute("INSERT INTO markers(layer,x,y,label) VALUES (?,?,?,?)",
                       (self.current_layer, x, y, label))
        self.viewer.draw_marker(x, y, label)

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
