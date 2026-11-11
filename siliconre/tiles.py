"""Tile-based SEM image rendering with bounded visible tile cache.

Pillow provides a convenient baseline; some compressed formats may still decode
the whole source internally. Use tiled pyramidal TIFF for production workloads.
"""
from collections import OrderedDict
from PIL import Image, ImageOps
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QGraphicsItem

TILE = 512
MAX_TILES = 96

class TiledImage(QGraphicsItem):
    def __init__(self, filename):
        super().__init__()
        self.filename = str(filename)
        self.source = Image.open(self.filename)
        self.width, self.height = self.source.size
        self.cache = OrderedDict()
        self.setZValue(0)

    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)

    def _tile(self, col, row):
        key = (col, row)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        x, y = col * TILE, row * TILE
        im = self.source.crop((x, y, min(x+TILE, self.width), min(y+TILE, self.height)))
        if im.mode not in ("RGB", "RGBA"):
            im = ImageOps.autocontrast(im.convert("L")).convert("RGB")
        else:
            im = im.convert("RGBA" if im.mode == "RGBA" else "RGB")
        raw = im.tobytes()
        fmt = QImage.Format.Format_RGBA8888 if im.mode == "RGBA" else QImage.Format.Format_RGB888
        pix = QPixmap.fromImage(QImage(raw, im.width, im.height, im.width * (4 if im.mode == "RGBA" else 3), fmt).copy())
        self.cache[key] = pix
        if len(self.cache) > MAX_TILES:
            self.cache.popitem(last=False)
        return pix

    def paint(self, painter, option, widget=None):
        visible = option.exposedRect.intersected(self.boundingRect())
        if visible.isEmpty():
            return
        left = max(0, int(visible.left()) // TILE)
        top = max(0, int(visible.top()) // TILE)
        right = min((self.width-1)//TILE, int(visible.right())//TILE)
        bottom = min((self.height-1)//TILE, int(visible.bottom())//TILE)
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform, False)
        for row in range(top, bottom+1):
            for col in range(left, right+1):
                pix = self._tile(col, row)
                painter.drawPixmap(col*TILE, row*TILE, pix)

    def close(self):
        self.source.close()
        self.cache.clear()
