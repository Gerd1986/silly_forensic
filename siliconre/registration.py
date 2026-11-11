"""Estimate translation between two SEM images using OpenCV phase correlation.

The returned dx,dy is the translation to apply to the overlay in base-image
pixel coordinates. This assumes the images share orientation and pixel scale.
"""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps

def _preview(path: str, max_side: int = 1536):
    with Image.open(path) as im:
        im = ImageOps.grayscale(im)
        original = im.size
        im.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
        return np.asarray(im, dtype=np.float32), original

def estimate_translation(base_path: str, overlay_path: str):
    base, base_size = _preview(base_path)
    overlay, overlay_size = _preview(overlay_path)
    if base_size != overlay_size:
        raise ValueError("Automatic translation requires equal image dimensions and pixel scale")
    if base.shape != overlay.shape:
        raise ValueError("Preview dimensions differ")
    if min(base.shape) < 32:
        raise ValueError("Images are too small for registration")
    # Windowing reduces edge artefacts; normalize contrast and remove DC.
    a = (base - float(base.mean())) / max(float(base.std()), 1e-6)
    b = (overlay - float(overlay.mean())) / max(float(overlay.std()), 1e-6)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    shift, response = cv2.phaseCorrelate(a, b, window)
    scale_x = base_size[0] / a.shape[1]
    scale_y = base_size[1] / a.shape[0]
    # phaseCorrelate(base, overlay) reports overlay displacement relative to base.
    return -shift[0] * scale_x, -shift[1] * scale_y, float(response)
