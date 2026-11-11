"""Multiresolution SEM renderer for tiled pyramidal TIFF and ordinary images."""
import math
from collections import OrderedDict
from pathlib import Path
from PIL import Image, ImageOps
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

TILE = 512
MAX_TILES = 96

class TiledImage(QGraphicsItem):
    def __init__(self, filename):
        super().__init__()
        self.filename = str(filename)
        self.source = Image.open(self.filename)
        self.levels = []
        # Pillow exposes pyramidal TIFF IFDs as frames for common TIFF layouts.
        # Only strictly smaller frames are treated as reduced-resolution levels.
        base = self.source.size
        self.width, self.height = base
        for index in range(getattr(self.source, "n_frames", 1)):
            self.source.seek(index)
            w, h = self.source.size
            if w <= base[0] and h <= base[1] and (w, h) not in [size for _, size in self.levels]:
                self.levels.append((index, (w, h)))
        self.levels.sort(key=lambda item: item[1][0], reverse=True)
        if not self.levels or self.levels[0][1] != base:
            self.levels.insert(0, (0, base))
        self.source.seek(0)
        self.cache = OrderedDict()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)

    def _level(self, painter):
        scale = painter.worldTransform().m11()
        if scale <= 0:
            return self.levels[0]
        # Select closest level not coarser than one source pixel per screen pixel.
        target = min(1.0, scale)
        candidates = [entry for entry in self.levels if entry[1][0] / self.width >= target]
        return candidates[-1] if candidates else self.levels[-1]

    def _tile(self, frame, col, row):
        key = (frame, col, row)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        self.source.seek(frame)
        w, h = self.source.size
        x, y = col*TILE, row*TILE
        im = self.source.crop((x, y, min(x+TILE, w), min(y+TILE, h)))
        if im.mode not in ("RGB", "RGBA"):
            im = ImageOps.autocontrast(im.convert("L")).convert("RGB")
        else:
            im = im.convert("RGBA" if im.mode == "RGBA" else "RGB")
        channels = 4 if im.mode == "RGBA" else 3
        fmt = QImage.Format.Format_RGBA8888 if channels == 4 else QImage.Format.Format_RGB888
        raw = im.tobytes()
        pix = QPixmap.fromImage(QImage(raw, im.width, im.height, im.width*channels, fmt).copy())
        self.cache[key] = pix
        if len(self.cache) > MAX_TILES:
            self.cache.popitem(last=False)
        return pix

    def paint(self, painter, option, widget=None):
        rect = option.exposedRect.intersected(self.boundingRect())
        if rect.isEmpty():
            return
        frame, (w, h) = self._level(painter)
        sx, sy = w/self.width, h/self.height
        left = max(0, int(rect.left()*sx)//TILE)
        top = max(0, int(rect.top()*sy)//TILE)
        right = min((w-1)//TILE, int(rect.right()*sx)//TILE)
        bottom = min((h-1)//TILE, int(rect.bottom()*sy)//TILE)
        painter.save()
        painter.scale(1/sx, 1/sy)
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform, False)
        for row in range(top, bottom+1):
            for col in range(left, right+1):
                painter.drawPixmap(col*TILE, row*TILE, self._tile(frame, col, row))
        painter.restore()

    def close(self):
        self.source.close()
        self.cache.clear()
