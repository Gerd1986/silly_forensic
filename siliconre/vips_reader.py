"""libvips-backed random-access image reader for large SEM images.

Optional pyvips and libvips runtime. Uses native TIFF pyramid page access via
page=n; some TIFF SubIFD layouts may require further format-specific support.
"""
import math
from pathlib import Path

class VipsReader:
    def __init__(self, filename):
        import pyvips
        self.filename = str(filename)
        self.pyvips = pyvips
        self.base = pyvips.Image.new_from_file(self.filename, access="random")
        self.width, self.height = self.base.width, self.base.height
        self.levels = [(self.base, self.width, self.height)]
        # libvips page-based loading is supported for many pyramidal TIFFs.
        # Only retain geometrically valid reduced-resolution pages.
        n_pages = min(int(self.base.get("n-pages")) if self.base.get_typeof("n-pages") else 1, 32)
        for page in range(1, n_pages):
            try:
                im = pyvips.Image.new_from_file(self.filename, page=page, access="random")
                if 0 < im.width < self.width and 0 < im.height < self.height:
                    self.levels.append((im, im.width, im.height))
            except Exception:
                break
        self.levels.sort(key=lambda entry: entry[1], reverse=True)

    def choose(self, display_scale):
        target = min(1.0, max(float(display_scale), 1e-9))
        matches = [(i, w, h) for i, (_, w, h) in enumerate(self.levels) if w/self.width >= target]
        return matches[-1][0] if matches else len(self.levels)-1

    def crop(self, level, x, y, width, height):
        im, w, h = self.levels[level]
        tile = im.crop(x, y, min(width, w-x), min(height, h-y))
        if tile.bands > 4:
            tile = tile.extract_band(0, n=3)
        if tile.bands == 2:
            tile = tile.extract_band(0)
        if tile.format != "uchar":
            tile = tile.cast("uchar")
        if tile.bands == 1:
            tile = tile.colourspace("srgb")
        return tile.write_to_memory(), tile.width, tile.height, tile.bands

    def close(self):
        self.levels.clear()
        self.base = None
