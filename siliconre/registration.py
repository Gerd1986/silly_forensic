"""Estimate translation between two SEM images using OpenCV phase correlation.

The returned dx,dy is the translation to apply to the overlay in base-image
pixel coordinates. This assumes the images share orientation and pixel scale.
"""
import cv2
import numpy as np
try:
    from .image_preview import gray_preview, image_size
except ImportError:
    from image_preview import gray_preview, image_size

def _preview(path: str, max_side: int = 1536, step=None):
    return gray_preview(path,max_side=max_side,step=step)

def estimate_translation(base_path: str, overlay_path: str):
    base_size=image_size(base_path)
    overlay_size=image_size(overlay_path)
    step=max(1,(max(*base_size,*overlay_size)+1535)//1536)
    base,_=_preview(base_path,step=step)
    overlay,_=_preview(overlay_path,step=step)
    if min(*base.shape,*overlay.shape) < 32:
        raise ValueError("Images are too small for registration")
    # Windowing reduces edge artefacts; normalize contrast and remove DC.
    if min(float(base.std()),float(overlay.std()))<1e-6:
        raise ValueError("Images contain insufficient texture for alignment")
    shape=(max(base.shape[0],overlay.shape[0]),max(base.shape[1],overlay.shape[1]))
    a=np.zeros(shape,dtype=np.float32)
    b=np.zeros(shape,dtype=np.float32)
    a[:base.shape[0],:base.shape[1]]=(base-float(base.mean()))/float(base.std())
    b[:overlay.shape[0],:overlay.shape[1]]=(overlay-float(overlay.mean()))/float(overlay.std())
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    shift, response = cv2.phaseCorrelate(a, b, window)
    if not np.isfinite([*shift,response]).all():
        raise ValueError("Alignment did not produce a finite displacement")
    # phaseCorrelate(base, overlay) reports overlay displacement relative to base.
    return -shift[0] * step, -shift[1] * step, float(response)