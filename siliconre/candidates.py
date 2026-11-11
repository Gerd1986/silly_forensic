"""Editable bounding-box candidates, stored in the project SQLite database."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen, QBrush
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsItem

def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS trace_candidates (
        id INTEGER PRIMARY KEY, layer TEXT NOT NULL,
        x REAL NOT NULL, y REAL NOT NULL, width REAL NOT NULL, height REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'candidate',
        CHECK(status IN ('candidate','accepted','rejected')))""")

class CandidateRect(QGraphicsRectItem):
    def __init__(self, row_id, layer, x, y, width, height, status, save_callback):
        super().__init__(0, 0, width, height)
        self.row_id = row_id
        self.layer = layer
        self.status = status
        self.save_callback = save_callback
        self.setPos(x, y)
        self.setZValue(5)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"Candidate #{row_id}: {status} — drag to correct position")
        self.refresh_style()

    def refresh_style(self):
        colors = {"candidate": "#00e5ff", "accepted": "#00dd66", "rejected": "#ff5555"}
        self.setPen(QPen(QColor(colors[self.status]), 2))
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.save_callback(self)

    def set_status(self, status):
        self.status = status
        self.refresh_style()
        self.setToolTip(f"Candidate #{self.row_id}: {status}")
        self.save_callback(self)
