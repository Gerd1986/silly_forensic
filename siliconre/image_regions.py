"""Bounded grayscale MCP image rendering; original image files are read only.

Coordinates describe source/base pixel edges. Output centers sample a uniform
grid inside the requested rectangle. Memory-mapped TIFFs are box-averaged in
source row bands, avoiding full-mosaic allocation and contact aliasing.
"""
import io
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps
try:
    from .image_preview import _mapped_reader, _mapped_preview, image_size, MAX_DECODE_PIXELS
except ImportError:
    from image_preview import _mapped_reader, _mapped_preview, image_size, MAX_DECODE_PIXELS

MAX_SIDE = 1536
MAX_SOURCE_PREVIEW_PIXELS = 8_000_000


def region_geometry(x, y, width, height, max_side):
    if not all(math.isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0:
        raise ValueError("Finite coordinates and positive region dimensions required")
    if not isinstance(max_side, int) or not 64 <= max_side <= MAX_SIDE:
        raise ValueError(f"max_side must be an integer between 64 and {MAX_SIDE}")
    if max(abs(v) for v in (x, y, width, height)) > 1e9:
        raise ValueError("Region coordinates exceed supported range")
    factor = max(1., width/max_side, height/max_side)
    return max(1, math.ceil(width/factor)), max(1, math.ceil(height/factor))


def _source_preview(path, bounds, step):
    x0, y0, x1, y1 = bounds
    reader = _mapped_reader(path)
    if reader is not None:
        try:
            result = _mapped_preview(reader.data[y0:y1, x0:x1], step)
        finally:
            reader.close()
    else:
        with Image.open(path) as source:
            if source.width*source.height > MAX_DECODE_PIXELS:
                raise ValueError("Large-image screening requires an uncompressed memory-mappable TIFF")
            cropped = ImageOps.grayscale(source.crop(bounds))
            result = _mapped_preview(np.asarray(cropped), step)
    return result


def render_region(path, rectangle, max_side=1024, matrix=None):
    """Return uint8 image, coverage mask and metadata; matrix maps source -> frame."""
    x, y, width, height = rectangle
    ow, oh = region_geometry(x, y, width, height, max_side)
    matrix = np.eye(3) if matrix is None else np.asarray(matrix, dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("Invalid alignment matrix")
    inverse = np.linalg.inv(matrix)
    sw, sh = image_size(path)
    xx = x + (np.arange(ow, dtype=np.float64)+.5)*width/ow
    yy = y + (np.arange(oh, dtype=np.float64)+.5)*height/oh
    sx = inverse[0, 0]*xx[None, :] + inverse[0, 1]*yy[:, None] + inverse[0, 2]
    sy = inverse[1, 0]*xx[None, :] + inverse[1, 1]*yy[:, None] + inverse[1, 2]
    valid = (sx >= 0) & (sx < sw) & (sy >= 0) & (sy < sh)
    result = np.zeros((oh, ow), dtype=np.uint8)
    meta = {"rectangle": {"x": x, "y": y, "width": width, "height": height},
            "output_size": [ow, oh], "source_size": [sw, sh],
            "source_to_frame": matrix.tolist(),
            "output_pixel_size_in_frame": [width/ow, height/oh],
            "covered_fraction": float(valid.mean()),
            "coordinates": "pixel edges; output centers at x+(column+0.5)*width/output_width, similarly y"}
    if not valid.any():
        return result, valid, meta
    source_per_output = min(width/ow, height/oh) / math.hypot(matrix[0, 0], matrix[1, 0])
    step = max(1, math.floor(source_per_output))
    x0, x1 = max(0, math.floor(sx.min())-step), min(sw, math.ceil(sx.max())+step+1)
    y0, y1 = max(0, math.floor(sy.min())-step), min(sh, math.ceil(sy.max())+step+1)
    # Long thin rotated requests still obey the intermediate allocation cap.
    while math.ceil((x1-x0)/step)*math.ceil((y1-y0)/step) > MAX_SOURCE_PREVIEW_PIXELS:
        step += max(1, step//4)
    preview = _source_preview(path, (x0, y0, x1, y1), step)
    def centers(start, end):
        starts = np.arange(start, end, step, dtype=float)
        return starts + np.minimum(step, end-starts)/2
    cols = np.interp(sx, centers(x0, x1), np.arange(preview.shape[1])).astype(np.float32)
    rows = np.interp(sy, centers(y0, y1), np.arange(preview.shape[0])).astype(np.float32)
    sampled = cv2.remap(preview, cols, rows, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    result[valid] = np.clip(np.rint(sampled[valid]), 0, 255).astype(np.uint8)
    meta["source_sampling_step"] = step
    return result, valid, meta


def png_bytes(gray, mask=None):
    image = Image.fromarray(gray)
    if mask is not None:
        image = image.convert("RGBA")
        image.putalpha(Image.fromarray(mask.astype(np.uint8)*255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def layer_path(db, project, layer):
    row = db.execute("SELECT path FROM layers WHERE name=?", (layer,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown layer: {layer}")
    path = Path(row[0])
    return path if path.is_absolute() else Path(project)/path
