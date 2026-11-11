try:
    from .roi_reader import read_gray_roi
except ImportError:
    from roi_reader import read_gray_roi
"""Experimental SEM trace segmentation from a user-defined image region.

Threshold and morphology are deliberately user-tunable. Results are candidate
regions, not electrically verified nets or transistor connectivity.
"""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps

def detect_traces(image_path, roi, threshold=0, min_area=30, invert=False):
    x, y, width, height = (int(v) for v in roi)
    if x < 0 or y < 0 or width < 1 or height < 1:
        raise ValueError("Invalid ROI")
    gray=read_gray_roi(image_path,(x,y,width,height))
    mode = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    if threshold == 0:
        _, mask = cv2.threshold(gray, 0, 255, mode | cv2.THRESH_OTSU)
    else:
        _, mask = cv2.threshold(gray, int(threshold), 255, mode)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    result = []
    for idx in range(1, count):
        bx, by, bw, bh, area = (int(v) for v in stats[idx])
        if area >= min_area:
            result.append({"x": x+bx, "y": y+by, "width": bw, "height": bh,
                           "area": area, "centroid": [x+float(centroids[idx,0]), y+float(centroids[idx,1])]})
    return sorted(result, key=lambda item: item["area"], reverse=True)
