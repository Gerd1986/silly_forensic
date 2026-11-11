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
from vias import detect_via_candidates, ensure_via_schema, list_vias
from reconstruction import associate_via_trace, connect_vias, reconstruct, export_json, export_verilog, export_vhdl
from via_matching import suggest_via_pairs
from devices import add_device, list_devices, resolve_devices, spice_netlist
from mos_detection import detect_mos_candidates, ensure_mos_candidate_schema, list_mos_candidates
from debug_ports import analyze_debug_interfaces
from logic_analysis import KINDS, PINS, add_logic_cell, resolved_cells, analyze_sequential
from batch_import import images_in_folder
from stitching import stitch_images
from tile_grid import stitch_tile_grid, plan_tile_grid, parse_tile

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
        self.setWindowTitle("SiliconRE 1.9.1 — SEM Delayering")
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
            ("Stitch selected SEM images", self.import_images),
            ("Stitch SEM image folder", self.import_folder),
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
            ("Detect via candidates", self.detect_vias),
            ("Review nearest via", self.review_via),
            ("Export vias JSON", self.export_vias),
            ("Associate via to trace", self.associate_via),
            ("Connect vias across layers", self.connect_layer_vias),
            ("Suggest aligned via pairs", self.suggest_aligned_vias),
            ("Export reconstructed nets", self.export_reconstruction),
            ("Add MOS transistor", self.add_mos),
            ("Delete MOS transistor", self.delete_mos),
            ("Export transistor netlist", self.export_transistors),
            ("Detect MOS candidates", self.detect_mos),
            ("Review MOS candidate", self.review_mos),
            ("Export MOS candidates JSON", self.export_mos_candidates),
            ("Analyze debug port clues", self.analyze_ports),
            ("Annotate logic cell", self.annotate_logic),
            ("Analyze sequential logic", self.analyze_logic),
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

    def _import_many(self, paths):
        if not self.project:
            QMessageBox.information(self,"Project required","Create or open a project first")
            return
        sources=[str(Path(p).resolve()) for p in paths]
        if len(sources)<2:
            QMessageBox.warning(self,"Stitch images","Select at least two overlapping images")
            return
        detected=all(parse_tile(path) is not None for path in sources)
        methods=["Tile grid (automatic overlap alignment)",
                 "Tile grid (edge-to-edge, no overlap)", "Automatic image matching",
                 "Tile grid (known acquisition overlap)"]
        method,ok=QInputDialog.getItem(self,"Stitching mode","Assembly method:",
                                      methods,0 if detected else 2,False)
        if not ok:
            return
        grid_mode=method in (methods[0],methods[1],methods[3])
        align_grid=method in (methods[0],methods[3])
        known_overlap=None
        if method==methods[3]:
            horizontal,ok=QInputDialog.getDouble(self,"Acquisition overlap",
                "Known horizontal overlap from scan setup (%):",10.,.1,95.,3)
            if not ok:
                return
            vertical,ok=QInputDialog.getDouble(self,"Acquisition overlap",
                "Known vertical overlap from scan setup (%):",horizontal,.1,95.,3)
            if not ok:
                return
            known_overlap=(horizontal,vertical)
        if grid_mode:
            try:
                preview=plan_tile_grid(sources)
            except (ValueError,OSError) as exc:
                QMessageBox.warning(self,"Tile grid",str(exc))
                return
            gaps=len(preview["missing"])
            if gaps and QMessageBox.question(self,"Missing tiles",
                    f"Grid has {gaps} missing positions. Fill them with gray placeholders?")!=QMessageBox.StandardButton.Yes:
                return
        name,ok=QInputDialog.getText(self,"Stitch images","New layer name (e.g. M1):")
        if not ok or not name.strip():
            return
        name=name.strip()
        with self.db() as db:
            if db.execute("SELECT 1 FROM layers WHERE name=? COLLATE NOCASE",(name,)).fetchone():
                QMessageBox.warning(self,"Stitch images","Layer name already exists")
                return
        output_dir=self.project/"stitched_layers"
        output_dir.mkdir(parents=True,exist_ok=True)
        import re
        safe=re.sub(r"[^A-Za-z0-9_-]+","_",name).strip("_") or "layer"
        output=output_dir/(safe+".tif")
        if output.exists():
            QMessageBox.warning(self,"Stitch images","Output filename already exists")
            return
        if QMessageBox.question(self,"Stitch images",
            f"Automatically align and stitch {len(sources)} overlapping images into one layer '{name}'?\n"
            "This may take time. If alignment fails, no layer will be created.")!=QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result=(stitch_tile_grid(sources,output,align=align_grid,overlap=known_overlap) if grid_mode
                    else stitch_images(sources,output))
            with self.db() as db:
                db.execute("INSERT INTO layers(name,path) VALUES (?,?)",(name,str(output)))
        except Exception as exc:
            QMessageBox.warning(self,"Stitch images",str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh_layers()
        matches=self.layers.findItems(name,Qt.MatchFlag.MatchExactly)
        if matches:
            self.layers.setCurrentItem(matches[0])
        self.statusBar().showMessage(
            f"Stitched {len(sources)} images into {name}: {result['size'][0]} x {result['size'][1]}")

    def import_images(self):
        if not self.project:
            QMessageBox.information(self,"Project required","Create or open a project first")
            return
        paths,_=QFileDialog.getOpenFileNames(self,"Select overlapping SEM images","",
                            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if paths:
            self._import_many(paths)

    def import_folder(self):
        if not self.project:
            QMessageBox.information(self,"Project required","Create or open a project first")
            return
        folder=QFileDialog.getExistingDirectory(self,"Select SEM image folder")
        if not folder:
            return
        recursive=QMessageBox.question(self,"Folder stitching",
            "Include images in subfolders too?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
        try:
            self._import_many(images_in_folder(folder,recursive))
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,"Folder stitching",str(exc))

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
            self.show_vias()
            self.show_mos_candidates()
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

    def show_vias(self):
        if not self.current_layer:
            return
        for item in list(self.viewer.scene_.items()):
            if item.data(0) == "via_candidate":
                self.viewer.scene_.removeItem(item)
        with self.db() as db:
            vias = list_vias(db, self.current_layer)
        for via in vias:
            x, y, r = via["x"], via["y"], via["radius"]
            color = {"candidate":"#ffff00","accepted":"#00ff66","rejected":"#ff5555"}[via["status"]]
            circle = self.viewer.scene_.addEllipse(x-r,y-r,2*r,2*r,QPen(QColor(color),2))
            circle.setZValue(7)
            circle.setData(0, "via_candidate")
            circle.setToolTip(f"Via-like candidate #{via['id']} ({via['status']}); unverified")

    def detect_vias(self):
        if not self.current_layer or not self.viewer.pix:
            return
        with self.db() as db:
            image_path = db.execute("SELECT path FROM layers WHERE name=?", (self.current_layer,)).fetchone()[0]
        roi = self.viewer.mapToScene(self.viewer.viewport().rect()).boundingRect().intersected(
            self.viewer.pix.sceneBoundingRect())
        x, y = max(0, int(roi.left())), max(0, int(roi.top()))
        w, h = int(roi.right())-x, int(roi.bottom())-y
        if w < 1 or h < 1 or w*h > 12_000_000:
            QMessageBox.warning(self, "Via detection", "Zoom into an image region smaller than 12 MP")
            return
        radius, ok = QInputDialog.getInt(self, "Via-like features", "Maximum radius (pixels):", 30, 3, 500)
        if not ok:
            return
        try:
            proposals = detect_via_candidates(image_path, (x,y,w,h), max_radius=radius)
            with self.db() as db:
                ensure_via_schema(db)
                db.executemany("INSERT INTO via_candidates(layer,x,y,radius) VALUES (?,?,?,?)",
                               [(self.current_layer, v["x"], v["y"], v["radius"]) for v in proposals])
            self.show_vias()
            self.statusBar().showMessage(f"{len(proposals)} unverified via-like circular features")
        except Exception as exc:
            QMessageBox.warning(self, "Via detection", str(exc))

    def review_via(self):
        if not self.current_layer:
            return
        with self.db() as db:
            vias = list_vias(db, self.current_layer)
        if not vias:
            return
        labels = [f"#{v['id']} ({v['x']:.0f},{v['y']:.0f}) {v['status']}" for v in vias]
        choice, ok = QInputDialog.getItem(self, "Review via", "Candidate:", labels, 0, False)
        if not ok:
            return
        status, ok = QInputDialog.getItem(self, "Review via", "Status:", ["candidate","accepted","rejected"], 0, False)
        if not ok:
            return
        selected = vias[labels.index(choice)]
        with self.db() as db:
            db.execute("UPDATE via_candidates SET status=? WHERE id=? AND layer=?",
                       (status, selected["id"], self.current_layer))
        self.show_vias()

    def export_vias(self):
        if not self.current_layer:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export via candidates",
                                              str(self.project / (self.current_layer + "_vias.json")), "JSON (*.json)")
        if not path:
            return
        with self.db() as db:
            data = list_vias(db, self.current_layer)
        with open(path, "w", encoding="utf-8") as out:
            json.dump({"layer": self.current_layer, "electrically_verified": False,
                       "via_like_candidates": data}, out, indent=2)
        self.statusBar().showMessage(f"Exported {len(data)} via-like candidates")

    def associate_via(self):
        if not self.current_layer:
            return
        with self.db() as db:
            vias = [v for v in list_vias(db,self.current_layer,"accepted")]
            traces = db.execute("SELECT id FROM trace_candidates WHERE layer=? AND status='accepted' ORDER BY id",
                                (self.current_layer,)).fetchall()
        if not vias or not traces:
            QMessageBox.information(self,"Association","Accept a via and a trace in this layer first")
            return
        via, ok = QInputDialog.getItem(self,"Associate via","Via ID:",[str(v["id"]) for v in vias],0,False)
        if not ok:
            return
        trace, ok = QInputDialog.getItem(self,"Associate via","Trace ID:",[str(t[0]) for t in traces],0,False)
        if not ok:
            return
        try:
            with self.db() as db:
                associate_via_trace(db,int(via),int(trace))
            self.statusBar().showMessage("Manual via-to-trace association saved")
        except ValueError as exc:
            QMessageBox.warning(self,"Association",str(exc))

    def connect_layer_vias(self):
        if not self.current_layer:
            return
        with self.db() as db:
            ensure_via_schema(db)
            rows = db.execute("SELECT id,layer FROM via_candidates WHERE status='accepted' ORDER BY layer,id").fetchall()
        if len(set(layer for _,layer in rows)) < 2:
            QMessageBox.information(self,"Cross-layer via","Accept vias on at least two different layers")
            return
        options = [f"{layer}: #{ident}" for ident,layer in rows]
        first, ok = QInputDialog.getItem(self,"Cross-layer via","First via:",options,0,False)
        if not ok:
            return
        second, ok = QInputDialog.getItem(self,"Cross-layer via","Second via:",options,0,False)
        if not ok:
            return
        a=rows[options.index(first)][0]
        b=rows[options.index(second)][0]
        try:
            with self.db() as db:
                connect_vias(db,a,b)
            self.statusBar().showMessage("Manual cross-layer via link saved; electrical continuity unverified")
        except ValueError as exc:
            QMessageBox.warning(self,"Cross-layer via",str(exc))

    def suggest_aligned_vias(self):
        if not self.current_layer:
            return
        with self.db() as db:
            options=[row[0] for row in db.execute(
                "SELECT name FROM layers WHERE name != ? ORDER BY name",(self.current_layer,))]
        if not options:
            return
        overlay,ok=QInputDialog.getItem(self,"Via pairing","Compare with layer:",options,0,False)
        if not ok:
            return
        tolerance,ok=QInputDialog.getDouble(self,"Via pairing","Maximum center distance (pixels):",10,0.1,10000,1)
        if not ok:
            return
        try:
            with self.db() as db:
                proposals=suggest_via_pairs(db,self.current_layer,overlay,tolerance)
            if not proposals:
                QMessageBox.information(self,"Via pairing","No matching accepted via candidates")
                return
            choices=[f"Via #{p['via_a']} ↔ #{p['via_b']} ({p['distance_px']:.2f} px)" for p in proposals]
            choice,ok=QInputDialog.getItem(self,"Review suggested pair",
                                            "Select one proposed pair to connect:",choices,0,False)
            if not ok:
                return
            selected=proposals[choices.index(choice)]
            answer=QMessageBox.question(self,"Confirm manual link",
                "Save this visually suggested cross-layer connection? Electrical continuity is NOT verified.")
            if answer != QMessageBox.StandardButton.Yes:
                return
            with self.db() as db:
                connect_vias(db,selected["via_a"],selected["via_b"])
            self.statusBar().showMessage("Reviewed cross-layer via link saved")
        except ValueError as exc:
            QMessageBox.warning(self,"Via pairing",str(exc))

    def export_reconstruction(self):
        if not self.project:
            return
        path, chosen = QFileDialog.getSaveFileName(
            self,"Export reconstructed nets",str(self.project / "siliconre_nets.json"),
            "JSON (*.json);;Verilog (*.v);;VHDL (*.vhd)")
        if not path:
            return
        ext=Path(path).suffix.lower()
        if ext not in (".json",".v",".vhd",".vhdl"):
            ext={"JSON (*.json)":".json","Verilog (*.v)":".v","VHDL (*.vhd)":".vhd"}.get(chosen,".json")
            path += ext
        with self.db() as db:
            result = export_verilog(db) if ext==".v" else export_vhdl(db) if ext in (".vhd",".vhdl") else export_json(db)
        with open(path,"w",encoding="utf-8") as stream:
            stream.write(result)
        self.statusBar().showMessage(f"Exported structural connectivity: {path}")

    def add_mos(self):
        if not self.current_layer or not self.project:
            return
        with self.db() as db:
            rows=db.execute("SELECT id FROM trace_candidates WHERE status='accepted' ORDER BY id").fetchall()
        if len(rows)<3:
            QMessageBox.information(self,"MOS","Accept at least three trace candidates before mapping G/D/S")
            return
        name,ok=QInputDialog.getText(self,"MOS annotation","Unique device name (letters, digits, underscore):")
        if not ok:
            return
        kind,ok=QInputDialog.getItem(self,"MOS annotation","Type:",["nmos","pmos"],0,False)
        if not ok:
            return
        choices=[str(row[0]) for row in rows]
        pins=[]
        for terminal in ("Gate","Drain","Source"):
            value,ok=QInputDialog.getItem(self,"MOS terminal",f"{terminal} accepted trace ID:",choices,0,False)
            if not ok:
                return
            pins.append(int(value))
        bulk_choices=["(unmapped)"]+choices
        bulk,ok=QInputDialog.getItem(self,"MOS terminal","Bulk accepted trace ID (optional):",bulk_choices,0,False)
        if not ok:
            return
        rect=self.viewer.mapToScene(self.viewer.viewport().rect()).boundingRect()
        center=rect.center()
        try:
            with self.db() as db:
                add_device(db,name,kind,self.current_layer,center.x(),center.y(),
                           *pins,None if bulk=="(unmapped)" else int(bulk))
            self.statusBar().showMessage(f"Annotated {kind} {name} with manually selected terminals")
        except (ValueError,sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self,"MOS annotation",str(exc))

    def delete_mos(self):
        if not self.project:
            return
        with self.db() as db:
            devices=list_devices(db)
        if not devices:
            return
        names=[d["name"] for d in devices]
        choice,ok=QInputDialog.getItem(self,"Delete MOS transistor","Device:",names,0,False)
        if not ok:
            return
        if QMessageBox.question(self,"Confirm deletion",f"Delete annotated device {choice}?") != QMessageBox.StandardButton.Yes:
            return
        with self.db() as db:
            db.execute("DELETE FROM mos_devices WHERE name=?",(choice,))
        self.statusBar().showMessage(f"Deleted annotated MOS device {choice}")

    def export_transistors(self):
        if not self.project:
            return
        path,_=QFileDialog.getSaveFileName(self,"Export MOS SPICE netlist",
                                            str(self.project/"siliconre_mos.cir"),"SPICE (*.cir)")
        if not path:
            return
        with self.db() as db:
            output=spice_netlist(db)
        with open(path,"w",encoding="utf-8") as stream:
            stream.write(output)
        self.statusBar().showMessage(f"Exported annotated MOS devices: {path}")

    def show_mos_candidates(self):
        if not self.current_layer:
            return
        for item in list(self.viewer.scene_.items()):
            if item.data(0)=="mos_candidate":
                self.viewer.scene_.removeItem(item)
        with self.db() as db:
            rows=list_mos_candidates(db,self.current_layer)
        for c in rows:
            color={"candidate":"#ffa500","accepted":"#00ff66","rejected":"#ff5555"}[c["status"]]
            rect=self.viewer.scene_.addRect(c["x"],c["y"],c["width"],c["height"],QPen(QColor(color),2))
            rect.setZValue(8)
            rect.setData(0,"mos_candidate")
            rect.setToolTip(f"MOS crossing candidate #{c['id']} ({c['status']}); not a verified transistor")

    def detect_mos(self):
        if not self.current_layer or not self.viewer.pix:
            return
        with self.db() as db:
            layers=dict(db.execute("SELECT name,path FROM layers"))
        options=[name for name in layers if name!=self.current_layer]
        if not options:
            QMessageBox.information(self,"MOS detection","Import an aligned ACTIVE mask layer first")
            return
        active,ok=QInputDialog.getItem(self,"MOS crossing detector","ACTIVE mask layer:",options,0,False)
        if not ok:
            return
        polarity,ok=QInputDialog.getItem(self,"POLY mask","POLY foreground:",["bright","dark"],0,False)
        if not ok:
            return
        active_polarity,ok=QInputDialog.getItem(self,"ACTIVE mask","ACTIVE foreground:",["bright","dark"],0,False)
        if not ok:
            return
        roi=self.viewer.mapToScene(self.viewer.viewport().rect()).boundingRect().intersected(
            self.viewer.pix.sceneBoundingRect())
        x,y=max(0,int(roi.left())),max(0,int(roi.top()))
        w,h=int(roi.right())-x,int(roi.bottom())-y
        if w<1 or h<1 or w*h>12_000_000:
            QMessageBox.warning(self,"MOS detection","Zoom into a region smaller than 12 MP")
            return
        try:
            proposals=detect_mos_candidates(layers[self.current_layer],layers[active],
                                            (x,y,w,h),polarity,active_polarity)
            with self.db() as db:
                ensure_mos_candidate_schema(db)
                db.executemany("""INSERT INTO mos_candidates
                  (poly_layer,active_layer,x,y,width,height,area)
                  VALUES (?,?,?,?,?,?,?)""",
                  [(self.current_layer,active,p["x"],p["y"],p["width"],p["height"],p["area"])
                   for p in proposals])
            self.show_mos_candidates()
            self.statusBar().showMessage(f"{len(proposals)} unverified POLY/ACTIVE crossing candidates")
        except Exception as exc:
            QMessageBox.warning(self,"MOS detection",str(exc))

    def review_mos(self):
        if not self.current_layer:
            return
        with self.db() as db:
            rows=list_mos_candidates(db,self.current_layer)
        if not rows:
            return
        options=[f"#{c['id']} ({c['x']:.0f},{c['y']:.0f}) {c['status']}" for c in rows]
        selected,ok=QInputDialog.getItem(self,"MOS review","Candidate:",options,0,False)
        if not ok:
            return
        status,ok=QInputDialog.getItem(self,"MOS review","Status:",["candidate","accepted","rejected"],0,False)
        if not ok:
            return
        with self.db() as db:
            db.execute("UPDATE mos_candidates SET status=? WHERE id=?",
                       (status,rows[options.index(selected)]["id"]))
        self.show_mos_candidates()

    def export_mos_candidates(self):
        if not self.current_layer:
            return
        path,_=QFileDialog.getSaveFileName(self,"Export MOS candidates",
                  str(self.project/(self.current_layer+"_mos_candidates.json")),"JSON (*.json)")
        if not path:
            return
        with self.db() as db:
            rows=list_mos_candidates(db,self.current_layer)
        with open(path,"w",encoding="utf-8") as stream:
            json.dump({"verified_transistors":False,"candidates":rows},stream,indent=2)
        self.statusBar().showMessage(f"Exported {len(rows)} MOS candidates")

    def analyze_ports(self):
        if not self.project:
            return
        with self.db() as db:
            findings=analyze_debug_interfaces(db)
        if not findings:
            QMessageBox.information(self,"Debug ports","No recognized signal labels found. Annotate points with TCK/TMS/TDI/TDO, SWCLK/SWDIO, etc.")
            return
        lines=[]
        for finding in findings:
            lines.append(f"{finding['protocol']}: {len(finding['matched_signals'])}/{len(finding['required_signals'])} required labels")
            lines.append("  Matched: "+", ".join(finding["matched_signals"]))
            lines.append("  Missing: "+(", ".join(finding["missing_signals"]) or "none"))
        QMessageBox.information(self,"Debug port hypotheses","\n".join(lines[:45])+
            "\n\nLabel-based hypotheses only; NOT confirmed port identification.")
        path,_=QFileDialog.getSaveFileName(self,"Export debug port evidence",
                        str(self.project/"debug_port_hypotheses.json"),"JSON (*.json)")
        if path:
            with open(path,"w",encoding="utf-8") as stream:
                json.dump({"verified":False,"method":"marker label matching","findings":findings},
                          stream,indent=2)

    def annotate_logic(self):
        if not self.project or not self.current_layer:
            return
        with self.db() as db:
            choices=[str(r[0]) for r in db.execute(
                "SELECT id FROM trace_candidates WHERE status='accepted' ORDER BY id")]
        if not choices:
            QMessageBox.information(self,"Logic annotation","Accept trace candidates first")
            return
        name,ok=QInputDialog.getText(self,"Logic annotation","Cell name:")
        if not ok:
            return
        kind,ok=QInputDialog.getItem(self,"Logic annotation","Cell type:",list(KINDS),0,False)
        if not ok:
            return
        pins={}
        for pin in PINS[kind]:
            chosen,ok=QInputDialog.getItem(self,"Logic annotation",f"{pin} trace ID:",choices,0,False)
            if not ok:
                return
            pins[pin]=int(chosen)
        try:
            with self.db() as db:
                add_logic_cell(db,name,kind,self.current_layer,pins)
            self.statusBar().showMessage(f"Annotated {kind} {name} (manual evidence)")
        except (ValueError,sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self,"Logic annotation",str(exc))

    def analyze_logic(self):
        if not self.project:
            return
        with self.db() as db:
            result=analyze_sequential(db)
        groups=result["clock_groups"]
        chains=result["shift_register_candidates"]
        states=result["tap_state_register_candidates"]
        summary=(f"Annotated cells: {len(result['cells'])}\n"
                 f"DFF clock groups: {len(groups)}\n"
                 f"Possible shift-register chains: {len(chains)}\n"
                 f"Possible four-bit state registers: {len(states)}\n\n"
                 "A JTAG TAP is NOT verified; transition logic is not yet evaluated.")
        QMessageBox.information(self,"Sequential logic analysis",summary)
        path,_=QFileDialog.getSaveFileName(self,"Export sequential evidence",
                      str(self.project/"sequential_analysis.json"),"JSON (*.json)")
        if path:
            with open(path,"w",encoding="utf-8") as stream:
                json.dump(result,stream,indent=2)

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
