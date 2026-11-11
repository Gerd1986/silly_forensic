"""SiliconRE SEM delayering desktop viewer (prototype)."""
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
        self.setWindowTitle("SiliconRE 0.1 — SEM Delayering")
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
        if not row:
            return
        try:
            self.viewer.show_image(row[0])
            self.current_layer = name
            self.overlay_layer = None
            for x,y,label in markers:
                self.viewer.draw_marker(x,y,label)
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
