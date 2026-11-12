"""Project research dock shared by manual inspection and MCP-written records."""
import json
import sqlite3

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPen, QPainterPath, QTransform
from PySide6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QGridLayout, QPushButton,
    QListWidget, QListWidgetItem, QLabel, QInputDialog, QMessageBox, QFileDialog,
    QGraphicsRectItem, QGraphicsItem)

import analysis_store as store


def item_matrix(item):
    t = item.sceneTransform()
    return [[t.m11(), t.m21(), t.dx()], [t.m12(), t.m22(), t.dy()], [0, 0, 1]]


class AnalysisDock(QDockWidget):
    def __init__(self, window):
        super().__init__("Research evidence / regional overlays", window)
        self.setObjectName('research_evidence')
        self.window = window
        self.active_region = None
        self.trace_points = []
        self.items = []
        widget = QWidget(); layout = QVBoxLayout(widget)
        note = QLabel("Source-pixel evidence; candidate / confirmed / rejected.\n"
                      "Confirmation does not add electrical connections.\n"
                      "Refresh after MCP edits. Trace: click vertices, Enter to save, Esc to cancel.")
        note.setWordWrap(True); layout.addWidget(note)
        self.records = QListWidget(); layout.addWidget(self.records)
        self.records.currentItemChanged.connect(self.focus_record)
        buttons = QGridLayout(); layout.addLayout(buttons)
        callbacks = [('Refresh', self.refresh), ('Point', self.new_point),
                     ('Draw trace', self.start_trace), ('Contact / region…', self.new_geometry),
                     ('Extract centerlines', self.extract_centerlines),
                     ('Save regional overlay…', self.new_alignment), ('Apply region', self.apply_selected),
                     ('Global overlay', self.global_overlay), ('Edit / review…', self.edit_selected),
                     ('Delete record', self.delete_selected), ('Export JSON', self.export),
                     ('Undo research', lambda: self.undo(False)), ('Redo research', lambda: self.undo(True))]
        for i, (title, callback) in enumerate(callbacks):
            button = QPushButton(title); button.clicked.connect(lambda checked=False, cb=callback: self.run(cb))
            buttons.addWidget(button, i//2, i%2)
        self.setWidget(widget)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self)

    def run(self, callback):
        if not self.window.project or not self.window.current_layer:
            QMessageBox.information(self, 'Project required', 'Open a project and select a layer first')
            return
        try:
            callback()
        except (ValueError, KeyError, TypeError, sqlite3.Error, OSError) as exc:
            QMessageBox.warning(self, 'Research operation failed', str(exc))

    def refresh(self):
        self.records.clear()
        if not self.window.project:
            return
        with self.window.db() as db:
            rows = store.list_records(db, layer=self.window.current_layer or '')
        for r in rows:
            name = r.get('name', r.get('label', ''))
            item = QListWidgetItem(f"{r['status']} | {r.get('kind',r['type'])} | {name} | {r['id'][:8]}")
            item.setData(Qt.ItemDataRole.UserRole, r['id']); item.setToolTip(r.get('note',''))
            self.records.addItem(item)
        self.draw(rows)
        if self.active_region:
            try:
                with self.window.db() as db:
                    current = store.get_record(db, self.active_region['id'])
                if current['revision'] != self.active_region['revision'] or current['status'] == 'rejected':
                    self.global_overlay()
            except ValueError:
                self.global_overlay()

    def selected(self):
        item = self.records.currentItem()
        if not item:
            raise ValueError('Select a research record first')
        with self.window.db() as db:
            return store.get_record(db, item.data(Qt.ItemDataRole.UserRole))

    def focus_record(self, item, previous=None):
        if item is None or not self.window.project:return
        try:
            r=self.selected()
            if r['type']=='alignment':
                if r['base_layer']!=self.window.current_layer:return
                rect=QRectF(*r['roi'])
            else:
                pts=[p for g in r['geometry'] if g['layer']==self.window.current_layer for p in g['points']]
                if not pts:return
                xs,ys=zip(*pts);rect=QRectF(min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)).adjusted(-30,-30,30,30)
            self.window.viewer.fitInView(rect,Qt.AspectRatioMode.KeepAspectRatio)
        except (ValueError,sqlite3.Error):return

    def visible_roi(self):
        v = self.window.viewer
        rect = v.mapToScene(v.viewport().rect()).boundingRect().intersected(v.pix.boundingRect())
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    def center(self):
        x,y,w,h = self.visible_roi()
        return [x+w/2, y+h/2]

    def json_dialog(self, title, data):
        text, ok = QInputDialog.getMultiLineText(self, title,
            'Original source pixels. Status: candidate/confirmed/rejected; review note required for review.\n'
            'Registration: matrix=null fits reference_points; check:true pairs are not fitted.\n'
            'Reference source belongs to overlay, target to base. ROI belongs to base.',
            json.dumps(data, indent=2))
        return json.loads(text) if ok else None

    def save_dialog(self, record, existing=None):
        data = {k:v for k,v in record.items() if k not in ('id','revision','actor','updated_at','residuals','type','reference_snapshots')}
        if record['type'] == 'alignment' and data.get('fit_mode') == 'manual':
            data['fit_mode'] = 'similarity'
        data = self.json_dialog('Regional alignment' if record['type']=='alignment' else 'Research evidence', data)
        if data is None:
            return
        with self.window.db() as db:
            # Reject out-of-image points, including records edited through the JSON dialog.
            from image_preview import image_size
            from image_regions import layer_path
            def check(layer, points):
                w,h = image_size(layer_path(db, self.window.project, layer))
                for x,y in points:
                    if not 0 <= x < w or not 0 <= y < h:
                        raise ValueError('Coordinates outside original image')
            if record['type'] == 'alignment':
                w,h = image_size(layer_path(db,self.window.project,data['base_layer']))
                x,y,rw,rh = data['roi']
                if x+rw>w or y+rh>h:
                    raise ValueError('ROI exceeds base image')
                for p in data.get('reference_points',[]):
                    check(data['base_layer'],[p['target']]); check(data['overlay_layer'],[p['source']])
                operation = store.save_region
            else:
                for g in data['geometry']:
                    check(g['layer'],g['points'])
                operation = store.save_evidence
            saved = operation(db, **data, record_id=existing['id'] if existing else None,
                              expected_revision=existing['revision'] if existing else None, actor='GUI')
        self.refresh()
        if self.active_region and saved['id']==self.active_region['id']:
            self.apply_region(saved)

    def new_point(self):
        self.save_dialog(dict(type='evidence',kind='point',label='',status='candidate',note='',references=[],
                              geometry=[dict(layer=self.window.current_layer,points=[self.center()])]))

    def extract_centerlines(self):
        from trace_paths import extract_paths,save_paths
        from image_regions import layer_path
        x,y,w,h=self.visible_roi();roi=[int(x),int(y),int(w),int(h)]
        threshold,ok=QInputDialog.getInt(self,'Trace paths','Threshold (0 = Otsu):',0,0,255)
        if not ok:return
        polarity,ok=QInputDialog.getItem(self,'Trace paths','Conductor contrast:',
                                        ['Bright conductors','Dark conductors'],0,False)
        if not ok:return
        minimum,ok=QInputDialog.getInt(self,'Trace paths','Minimum segmented area (pixels):',20,1,2000000)
        if not ok:return
        with self.window.db() as db:path=layer_path(db,self.window.project,self.window.current_layer)
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            extraction=extract_paths(path,roi,threshold,minimum,polarity=='Dark conductors')
            with self.window.db() as db:records=save_paths(db,self.window.current_layer,extraction,actor='GUI')
        finally:QApplication.restoreOverrideCursor()
        self.refresh()
        self.window.statusBar().showMessage(f"Saved {len(records)} candidate centerline paths; inspect ends and junctions")

    def new_geometry(self):
        kind, ok = QInputDialog.getItem(self,'Evidence kind','Kind:', ['contact','region','note'],0,False)
        if not ok:
            return
        point = self.center(); geometry = [dict(layer=self.window.current_layer,points=[point])]
        if kind=='contact':
            with self.window.db() as db:
                layers=[n for n, in db.execute('SELECT name FROM layers') if n!=self.window.current_layer]
            if not layers:
                raise ValueError('A contact requires two layers')
            peer,ok=QInputDialog.getItem(self,'Contact peer','Second layer:',layers,0,False)
            if not ok:
                return
            # No invented cross-layer coordinate: explicit user entry is required.
            text,ok=QInputDialog.getText(self,'Contact peer','Original X,Y on second layer (inspect it first):')
            if not ok:
                return
            geometry.append(dict(layer=peer,points=[[float(v) for v in text.split(',')]]))
        elif kind=='region':
            x,y,w,h=self.visible_roi();geometry[0]['points']=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]
            # Pixel-edge polygons may touch the image boundary; keep vertices on sampled pixels.
            geometry[0]['points']=[[min(px,self.window.viewer.pix.width-1),min(py,self.window.viewer.pix.height-1)] for px,py in geometry[0]['points']]
        self.save_dialog(dict(type='evidence',kind=kind,label='',status='candidate',note='',references=[],geometry=geometry))

    def start_trace(self):
        self.window.viewer.set_mouse_mode('trace')
        self.trace_points=[]
        self.window.statusBar().showMessage('Trace evidence: click vertices; Enter saves; Esc cancels')

    def add_trace_point(self, x, y):
        self.trace_points.append([x,y]); self.draw_trace_preview()

    def draw_trace_preview(self):
        for item in list(self.window.viewer.scene_.items()):
            if item.data(0)=='research_preview':
                self.window.viewer.scene_.removeItem(item)
        if self.trace_points:
            path=QPainterPath(QPointF(*self.trace_points[0]))
            for p in self.trace_points[1:]:path.lineTo(*p)
            pen=QPen(QColor('#ffbd57'),2);pen.setCosmetic(True)
            item=self.window.viewer.scene_.addPath(path,pen);item.setZValue(30);item.setData(0,'research_preview')

    def cancel_trace(self):
        self.trace_points=[];self.draw_trace_preview()

    def finish_trace(self):
        points=self.trace_points[:]
        if len(points)<2:
            raise ValueError('Trace requires at least two vertices')
        self.window.viewer.set_mouse_mode('pointer')
        self.save_dialog(dict(type='evidence',kind='trace',geometry=[dict(layer=self.window.current_layer,points=points)],
                              label='',status='candidate',note='',references=[]))

    def new_alignment(self):
        v=self.window.viewer
        if not v.overlay or not self.window.overlay_layer:
            raise ValueError('Choose and adjust an overlay first')
        self.save_dialog(dict(type='alignment',base_layer=self.window.current_layer,
            overlay_layer=self.window.overlay_layer,roi=self.visible_roi(),name='Local alignment',
            matrix=item_matrix(v.overlay),fit_mode='similarity',reference_points=[],status='candidate',note=''))

    def apply_selected(self):
        self.apply_region(self.selected())

    def apply_region(self, record):
        if record['type']!='alignment' or record['base_layer']!=self.window.current_layer:
            raise ValueError('Select an alignment with the current layer as base')
        with self.window.db() as db:
            m,record=store.region_matrix(db,record['id'],record['base_layer'],record['overlay_layer'],record['roi'])
            from image_regions import layer_path
            path=layer_path(db,self.window.project,record['overlay_layer'])
        v=self.window.viewer
        v.show_overlay(path,opacity=self.window.opacity.value()/100)
        clip=QGraphicsRectItem(QRectF(*record['roi']));clip.setPen(QPen(Qt.PenStyle.NoPen))
        clip.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape,True)
        clip.setZValue(1);v.scene_.addItem(clip);v.overlay.setParentItem(clip);v._overlay_clip=clip
        v.overlay.setTransform(QTransform(m[0,0],m[1,0],m[0,1],m[1,1],m[0,2],m[1,2]))
        self.window.overlay_layer=record['overlay_layer'];self.active_region=record
        v.fitInView(QRectF(*record['roi']),Qt.AspectRatioMode.KeepAspectRatio)
        self.window.statusBar().showMessage('Regional overlay: '+record['name']+' (clipped to ROI; '+record['status']+')')

    def global_overlay(self):
        self.active_region=None
        if not self.window.viewer.overlay or not self.window.overlay_layer:
            return
        with self.window.db() as db:
            from layer_alignment import load_alignment,alignment_parameters
            from image_regions import layer_path
            path=layer_path(db,self.window.project,self.window.overlay_layer)
            try:values=alignment_parameters(load_alignment(db,self.window.current_layer,self.window.overlay_layer))
            except ValueError:values=(0,0,0,1)
        dx,dy,angle,scale=values
        self.window.viewer.show_overlay(path,dx,dy,self.window.opacity.value()/100,angle,scale)

    def save_drag(self, item):
        r=self.active_region
        with self.window.db() as db:
            saved=store.save_region(db,r['base_layer'],r['overlay_layer'],r['roi'],r['name'],r['reference_points'],
                item_matrix(item),status='candidate',note=r['note'],record_id=r['id'],expected_revision=r['revision'],actor='GUI')
        self.active_region=saved;self.refresh()

    def edit_selected(self):
        r=self.selected();self.save_dialog(r,r)

    def delete_selected(self):
        r=self.selected()
        with self.window.db() as db:store.delete_record(db,r['id'],r['revision'],actor='GUI')
        self.refresh()

    def undo(self, redo):
        with self.window.db() as db:store.undo_redo(db,redo=redo,actor='GUI')
        self.refresh()

    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'Export research evidence',str(self.window.project/'research_evidence.json'),'JSON (*.json)')
        if path:
            from pathlib import Path
            with self.window.db() as db:records=store.list_records(db);journal=store.history(db,200)
            Path(path).write_text(json.dumps(dict(coordinates='original source-layer pixels',records=records,history=journal),indent=2),encoding='utf-8')

    def draw(self, records):
        scene=self.window.viewer.scene_
        for item in list(scene.items()):
            if item.data(0)=='research':scene.removeItem(item)
        colors=dict(candidate='#ffbd57',confirmed='#60e0b0',rejected='#f17b85')
        for r in records:
            pen=QPen(QColor(colors[r['status']]),2);pen.setCosmetic(True)
            if r['status']!='confirmed':pen.setStyle(Qt.PenStyle.DashLine)
            geometries=r.get('geometry',[])
            if r['type']=='alignment' and r['base_layer']==self.window.current_layer:
                x,y,w,h=r['roi'];geometries=[dict(layer=r['base_layer'],points=[[x,y],[x+w,y],[x+w,y+h],[x,y+h],[x,y]])]
            for g in geometries:
                if g['layer']!=self.window.current_layer:continue
                pts=g['points']
                if len(pts)==1:
                    x,y=pts[0];item=scene.addEllipse(x-5,y-5,10,10,pen)
                else:
                    path=QPainterPath(QPointF(*pts[0]))
                    for p in pts[1:]:path.lineTo(*p)
                    if r.get('kind')=='region':path.closeSubpath()
                    item=scene.addPath(path,pen)
                item.setZValue(20);item.setData(0,'research');item.setToolTip(
                    f"{r['id']} | {r['status']} | {r.get('label',r.get('name',''))}\n{r['note']}")
                if r.get('kind')=='trace':
                    for endpoint,key in ((pts[0],'start_kind'),(pts[-1],'end_kind')):
                        kind=r.get('provenance',{}).get(key,'manual endpoint')
                        ep=QPen(QColor('#e78dff') if kind=='junction' else QColor(colors[r['status']]),2)
                        ep.setCosmetic(True);x,y=endpoint
                        dot=scene.addEllipse(x-3,y-3,6,6,ep);dot.setZValue(21);dot.setData(0,'research')
                        dot.setToolTip(kind+'; physical/electrical endpoint unverified')
