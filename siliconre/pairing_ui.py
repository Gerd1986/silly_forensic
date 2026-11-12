"""Point-pair registration and contact review, with native-coordinate click mapping."""
import sqlite3
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPixmap, QColor, QPen
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,
    QGraphicsView,QGraphicsScene,QWidget,QComboBox,QDoubleSpinBox,QSpinBox,QDialogButtonBox,
    QTableWidget,QTableWidgetItem,QCheckBox,QLineEdit,QPlainTextEdit,QSplitter,QAbstractItemView,QHeaderView)
import analysis_store as store
from image_regions import render_region,png_bytes,layer_path
from image_preview import image_size
from contact_review import prepare_review,save_review,residual_summary,source_crop


def summary_text(summary):
    parts=[]
    for key,label in (('training','Training'),('checks','Independent checks')):
        r=summary[key]
        parts.append(f"{label}: {r['count']} points, median {r['median_error_px']:.4f} px, max {r['max_error_px']:.4f} px" if r['count'] else label+': none')
    return '; '.join(parts)


class SourceView(QGraphicsView):
    pointPicked=Signal(float,float)

    def __init__(self):
        super().__init__();self.setScene(QGraphicsScene(self));self.roi=None;self._press=None;self.auto_fit=True
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag);self.setMinimumSize(220,220)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_source(self, raw, roi, output_size):
        pix=QPixmap()
        if not pix.loadFromData(raw,'PNG'):raise ValueError('Cannot display source crop')
        self.scene().clear();self.scene().addPixmap(pix);self.scene().setSceneRect(0,0,*output_size)
        self.roi=list(roi);self.output_size=list(output_size)
        self.auto_fit=True
        self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if getattr(self,'roi',None) is not None and self.auto_fit:
            self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)

    def source_point(self, scene_point):
        if self.roi is None or not self.sceneRect().contains(scene_point):return None
        x,y,w,h=self.roi;ow,oh=self.output_size
        if not 0<=scene_point.x()<ow or not 0<=scene_point.y()<oh:return None
        return [x+scene_point.x()*w/ow,y+scene_point.y()*h/oh]

    def mark(self, point, label, check=False):
        if self.roi is None:return
        x,y,w,h=self.roi;ow,oh=self.output_size
        px=(point[0]-x)*ow/w;py=(point[1]-y)*oh/h
        if not 0<=px<ow or not 0<=py<oh:return
        pen=QPen(QColor('#57e2ff' if check else '#ffb343'),2);pen.setCosmetic(True)
        for ax,ay,bx,by in ((px-6,py,px+6,py),(px,py-6,px,py+6)):
            item=self.scene().addLine(ax,ay,bx,by,pen);item.setData(0,'landmark')
        text=self.scene().addSimpleText(str(label));text.setPos(px+5,py+3);text.setBrush(pen.color());text.setData(0,'landmark')

    def clear_marks(self):
        for item in list(self.scene().items()):
            if item.data(0)=='landmark':self.scene().removeItem(item)

    def wheelEvent(self,event):
        self.auto_fit=False
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.scale(1.2 if event.angleDelta().y()>0 else 1/1.2,1.2 if event.angleDelta().y()>0 else 1/1.2)

    def mousePressEvent(self,event):
        self._press=event.position().toPoint() if event.button()==Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if self._press is not None and event.button()==Qt.MouseButton.LeftButton:
            if (event.position().toPoint()-self._press).manhattanLength()<4:
                p=self.source_point(self.mapToScene(event.position().toPoint()))
                if p is not None:self.pointPicked.emit(*p)
            else:self.auto_fit=False
            self._press=None
        super().mouseReleaseEvent(event)


class CropPane(QWidget):
    cropChanged=Signal()

    def __init__(self, title, path, roi, parent=None):
        super().__init__(parent);self.path=path;self.roi=list(roi)
        layout=QVBoxLayout(self);layout.addWidget(QLabel(title))
        self.view=SourceView();layout.addWidget(self.view);self.info=QLabel();self.info.setWordWrap(True);layout.addWidget(self.info)
        self.crop_button=QPushButton('Choose source crop…');self.crop_button.clicked.connect(self.choose_crop);layout.addWidget(self.crop_button)
        self.reload()

    def reload(self):
        gray,mask,_=render_region(self.path,self.roi,1024);size=[gray.shape[1],gray.shape[0]]
        self.view.set_source(png_bytes(gray,mask),self.roi,size)
        self.info.setText(f'Source ROI: {self.roi}; {self.roi[2]/size[0]:.3f} × {self.roi[3]/size[1]:.3f} original px/output px')

    def choose_crop(self):
        dialog=QDialog(self);dialog.setWindowTitle('Source crop');form=QFormLayout(dialog);boxes=[]
        iw,ih=image_size(self.path)
        for label,value,maximum,minimum in zip(('X','Y','Width','Height'),self.roi,(iw-1,ih-1,iw,ih),(0,0,1,1)):
            box=QDoubleSpinBox();box.setDecimals(3);box.setRange(minimum,maximum);box.setValue(value);boxes.append(box);form.addRow(label,box)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            old=self.roi[:];x,y,w,h=[b.value() for b in boxes];self.roi=[x,y,min(w,iw-x),min(h,ih-y)]
            try:self.reload();self.cropChanged.emit()
            except (ValueError,OSError) as exc:
                self.roi=old;self.info.setText(str(exc))


def _guard(dialog, callback):
    try:callback()
    except (ValueError,KeyError,TypeError,sqlite3.Error,OSError,np.linalg.LinAlgError) as exc:
        dialog.message.setText(str(exc))


class PairDialog(QDialog):
    """Left=base target, right=overlay source. Explicitly click each pair."""
    def __init__(self, window, existing=None, contact=False):
        super().__init__(window);self.window=window;self.existing=existing;self.contact=contact
        self.saved=None;self.pairs=list(existing.get('reference_points',[])) if existing else []
        self.pending=None;self.pending_check=False
        self.setWindowTitle('Pick contact points' if contact else 'Local registration: click reference pairs');self.resize(1150,800)
        root=QVBoxLayout(self);self.message=QLabel('Click the base image first, then the matching point in the overlay source. Drag to pan; wheel to zoom.')
        self.message.setWordWrap(True);root.addWidget(self.message)
        self.base=existing['base_layer'] if existing else window.current_layer
        with window.db() as db:
            names=[n for n, in db.execute('SELECT name FROM layers ORDER BY name') if n!=self.base]
            if not names:raise ValueError('At least two layers required')
            self.paths={n:layer_path(db,window.project,n) for n in [self.base,*names]}
        controls=QHBoxLayout();self.overlay=QComboBox();self.overlay.addItems(names)
        preferred=existing['overlay_layer'] if existing else window.overlay_layer
        if preferred in names:self.overlay.setCurrentText(preferred)
        controls.addWidget(QLabel('Base: '+self.base+' | Overlay/source:'));controls.addWidget(self.overlay)
        self.check=QCheckBox('New pair is an independent check (not fitted)');controls.addWidget(self.check)
        self.mode=QComboBox();self.mode.addItems(['similarity','affine']);controls.addWidget(self.mode)
        if existing and existing['fit_mode']=='affine':self.mode.setCurrentText('affine')
        self.check.setVisible(not contact);self.mode.setVisible(not contact);root.addLayout(controls)
        self.split=QSplitter();root.addWidget(self.split,1)
        roi=list(existing['roi']) if existing else window.research.visible_roi();self.valid_roi=list(roi)
        self.left=CropPane('Base / target: '+self.base,self.paths[self.base],roi);self.split.addWidget(self.left)
        self.right=None;self.change_overlay()
        self.left.view.pointPicked.connect(self.pick_target);self.left.cropChanged.connect(self.redraw)
        self.overlay.currentTextChanged.connect(lambda: _guard(self,self.change_overlay))
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['Check only','Base target X,Y','Overlay source X,Y','Error in base px'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMaximumHeight(155);self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self.table);self.table.itemChanged.connect(self.change_role)
        row=QHBoxLayout();remove=QPushButton('Remove selected pair');remove.clicked.connect(self.remove_pair);row.addWidget(remove)
        fit=QPushButton('Calculate / check');fit.clicked.connect(lambda:_guard(self,self.calculate));fit.setVisible(not contact);row.addWidget(fit)
        crop=QPushButton('Use base crop as validity ROI');crop.clicked.connect(self.use_roi);crop.setVisible(not contact);row.addWidget(crop)
        root.addLayout(row);self.roi_label=QLabel();root.addWidget(self.roi_label)
        form=QFormLayout();self.name=QLineEdit(existing['name'] if existing else ('Contact' if contact else 'Local registration'))
        form.addRow('Name / label',self.name);self.status=QComboBox();self.status.addItems(store.STATUSES)
        if existing:self.status.setCurrentText(existing['status'])
        form.addRow('Review status',self.status);self.note=QPlainTextEdit(existing['note'] if existing else '');self.note.setMaximumHeight(65)
        form.addRow('Review note',self.note);root.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(lambda:_guard(self,self.save));buttons.rejected.connect(self.reject);root.addWidget(buttons)
        self.fill_table();self.use_roi_label()
        self.mode.currentTextChanged.connect(self.invalidate_fit)

    def invalidate_fit(self,*args):
        self.status.setCurrentText('candidate')
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item=QTableWidgetItem('—');item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable);self.table.setItem(row,3,item)
        self.table.blockSignals(False)
        self.message.setText('Reference roles, model or validity region changed. Calculate and inspect the residuals again.')

    def change_overlay(self):
        layer=self.overlay.currentText();iw,ih=image_size(self.paths[layer]);x,y,w,h=self.left.roi
        # An unregistered default crop is navigation only, never a corresponding point.
        roi=[min(x,max(0,iw-min(w,iw))),min(y,max(0,ih-min(h,ih))),min(w,iw),min(h,ih)]
        if self.right:
            old=self.right;old.setParent(None);old.deleteLater();self.pairs=[];self.pending=None
        self.right=CropPane('Overlay / source: '+layer+' (independent coordinates)',self.paths[layer],roi)
        self.split.addWidget(self.right);self.right.view.pointPicked.connect(self.pick_source)
        self.right.cropChanged.connect(self.redraw)
        if hasattr(self,'table'):self.fill_table()

    def pick_target(self,x,y):
        self.pending=[x,y];self.pending_check=self.check.isChecked()
        self.message.setText('Now click the corresponding point in the overlay/source image. No match is assumed.')
        self.redraw()

    def pick_source(self,x,y):
        if self.pending is None:
            self.message.setText('Click the base/target image first.');return
        pair=dict(source=[x,y],target=self.pending,check=self.pending_check if not self.contact else False)
        self.pairs=[pair] if self.contact else [*self.pairs,pair];self.pending=None
        self.status.setCurrentText('candidate');self.fill_table()
        self.message.setText('Pair recorded. Add another pair, mark an independent check, or remove and repick a wrong pair.')

    def fill_table(self):
        self.table.blockSignals(True);self.table.setRowCount(len(self.pairs))
        for row,p in enumerate(self.pairs):
            item=QTableWidgetItem();item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if p['check'] else Qt.CheckState.Unchecked);self.table.setItem(row,0,item)
            for col,key in ((1,'target'),(2,'source')):
                item=QTableWidgetItem(', '.join(f'{v:.3f}' for v in p[key]));item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable);self.table.setItem(row,col,item)
            item=QTableWidgetItem('—');item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable);self.table.setItem(row,3,item)
        self.table.blockSignals(False);self.overlay.setEnabled(not self.pairs and self.pending is None)
        self.redraw()

    def change_role(self,item):
        if item.column()!=0:return
        self.pairs[item.row()]['check']=item.checkState()==Qt.CheckState.Checked
        self.status.setCurrentText('candidate');self.fill_table();self.invalidate_fit()

    def remove_pair(self):
        row=self.table.currentRow()
        if row>=0:del self.pairs[row]
        self.pending=None;self.status.setCurrentText('candidate');self.fill_table();self.invalidate_fit()

    def redraw(self):
        self.left.view.clear_marks();self.right.view.clear_marks()
        for i,p in enumerate(self.pairs):
            self.left.view.mark(p['target'],i+1,p['check']);self.right.view.mark(p['source'],i+1,p['check'])
        if self.pending:self.left.view.mark(self.pending,'next',self.pending_check)

    def use_roi(self):self.valid_roi=list(self.left.roi);self.use_roi_label();self.invalidate_fit()
    def use_roi_label(self):self.roi_label.setText('Validity ROI in base pixels: '+str(self.valid_roi) if not self.contact else 'Each contact point keeps its own source-layer coordinates.')

    def calculate(self):
        m=store.fit_references(self.pairs,self.mode.currentText());res=[]
        self.table.blockSignals(True)
        for i,p in enumerate(self.pairs):
            error=float(np.linalg.norm((m@[*p['source'],1])[:2]-p['target']));res.append(dict(check=p['check'],error_px=error))
            item=QTableWidgetItem(f'{error:.4f}');item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable);self.table.setItem(i,3,item)
        self.table.blockSignals(False)
        summary=residual_summary(dict(residuals=res));self.message.setText('Measured residuals (not confidence): '+summary_text(summary))
        return m

    def save(self):
        if self.pending is not None:raise ValueError('Finish or remove the pending point pair first')
        with self.window.db() as db:
            if self.contact:
                if len(self.pairs)!=1:raise ValueError('Pick one point on each of the two source images')
                p=self.pairs[0]
                self.saved=store.save_evidence(db,'contact',[dict(layer=self.base,points=[p['target']]),
                    dict(layer=self.overlay.currentText(),points=[p['source']])],self.name.text(),self.status.currentText(),self.note.toPlainText(),actor='GUI')
            else:
                self.calculate()
                self.saved=store.save_region(db,self.base,self.overlay.currentText(),self.valid_roi,self.name.text(),self.pairs,
                    fit_mode=self.mode.currentText(),status=self.status.currentText(),note=self.note.toPlainText(),
                    record_id=self.existing['id'] if self.existing else None,
                    expected_revision=self.existing['revision'] if self.existing else None,actor='GUI')
        self.accept()


class ContactReviewDialog(QDialog):
    def __init__(self,window,contact):
        super().__init__(window);self.window=window;self.contact=contact;self.plan=None;self.saved=None
        self.setWindowTitle('Contact review — original source details');self.resize(1200,750)
        root=QVBoxLayout(self);self.message=QLabel();self.message.setWordWrap(True);root.addWidget(self.message)
        controls=QHBoxLayout();self.alignment=QComboBox();self.context=QComboBox();self.size=QSpinBox();self.size.setRange(32,1024);self.size.setValue(256)
        for combo in (self.alignment,self.context):
            combo.setMinimumContentsLength(18);combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMaximumWidth(300)
        self.alignment.addItem('No registration selected','');self.context.addItem('No third layer','')
        layers={g['layer'] for g in contact['geometry']}
        with window.db() as db:regions=store.list_records(db,'alignment')
        for r in regions:
            if r['status']=='rejected':continue
            label=f"{r['name']} | {r['base_layer']} ← {r['overlay_layer']} | rev {r['revision']}"
            if set((r['base_layer'],r['overlay_layer']))==layers:self.alignment.addItem(label,r['id'])
            if r['base_layer'] in layers and r['overlay_layer'] not in layers:self.context.addItem(label,r['id'])
        for label,widget in (('Registration',self.alignment),('Third layer',self.context),('Crop px',self.size)):
            controls.addWidget(QLabel(label));controls.addWidget(widget)
        reload=QPushButton('Load / reload details');reload.clicked.connect(lambda:_guard(self,self.reload));controls.addWidget(reload);root.addLayout(controls)
        self.split=QSplitter();root.addWidget(self.split,1);self.summary=QLabel();self.summary.setWordWrap(True);root.addWidget(self.summary)
        form=QFormLayout();self.status=QComboBox();self.status.addItems(store.STATUSES);self.status.setCurrentText(contact['status']);form.addRow('Decision',self.status)
        self.note=QPlainTextEdit(contact['note']);self.note.setMaximumHeight(85);form.addRow('Reason',self.note);root.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(lambda:_guard(self,self.save));buttons.rejected.connect(self.reject);root.addWidget(buttons)
        self.alignment.currentIndexChanged.connect(self.invalidate);self.context.currentIndexChanged.connect(self.invalidate);self.size.valueChanged.connect(self.invalidate)
        _guard(self,self.reload)

    def invalidate(self,*args):
        self.plan=None;self.message.setText('Settings changed. Reload details before saving a decision.')

    def reload(self):
        with self.window.db() as db:
            plan,frames=prepare_review(db,self.window.project,self.contact['id'],self.alignment.currentData(),self.context.currentData(),self.size.value())
        while self.split.count():
            widget=self.split.widget(0);widget.setParent(None);widget.deleteLater()
        for meta,raw in zip(plan['views'],frames):
            pane=QWidget();layout=QVBoxLayout(pane);layout.addWidget(QLabel(meta['layer']+' | '+meta['role']))
            view=SourceView();view.set_source(raw,meta['roi'],meta['output_size']);view.mark(meta['point'],'contact',meta['role'].startswith('predicted'));layout.addWidget(view)
            label=QLabel(f"Original ROI {meta['roi']} | source point {meta['point']}");label.setWordWrap(True);layout.addWidget(label);self.split.addWidget(pane)
        self.plan=plan
        self.message.setText(f"Contact {plan['contact_id'][:8]}, revision {plan['contact_revision']}. Orange: observed source point; cyan: predicted context only. No automatic electrical inference.")
        offset=plan['geometric_offset']
        self.summary.setText('\n'.join([*plan['warnings'],*[f"{r['record']['name']} ({r['record']['status']}): {summary_text(r['residual_summary'])}" for r in plan['registrations']],
                           f"Contact-point geometric displacement: {offset['distance_px']:.4f} base pixels (not a contact probability)" if offset else 'Contact-point displacement: no registration selected']))

    def save(self):
        if self.plan is None:raise ValueError('Reload and inspect the source details first')
        with self.window.db() as db:
            self.saved=save_review(db,self.window.project,self.contact['id'],self.plan['contact_revision'],self.status.currentText(),self.note.toPlainText(),
                self.alignment.currentData(),self.context.currentData(),self.plan['crop_size'],
                {v['layer']:v['image_sha256'] for v in self.plan['views']},
                {r['record']['id']:r['record']['revision'] for r in self.plan['registrations']},actor='GUI')
        self.accept()
