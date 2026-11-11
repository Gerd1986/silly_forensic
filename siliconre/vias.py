try:
    from .roi_reader import read_gray_roi
except ImportError:
    from roi_reader import read_gray_roi
"""Experimental via-like circular feature proposals from a bounded SEM ROI."""
import cv2
import numpy as np
from PIL import Image, ImageOps

def detect_via_candidates(image_path, roi, min_radius=3, max_radius=30):
    x, y, w, h = map(int, roi)
    if x < 0 or y < 0 or w <= 0 or h <= 0 or w*h > 12_000_000:
        raise ValueError("ROI must be within 12 megapixels")
    if min_radius < 1 or max_radius < min_radius:
        raise ValueError("Invalid radius range")
    gray=read_gray_roi(image_path,(x,y,w,h))
    blurred = cv2.GaussianBlur(gray, (5,5), 1.2)
    circles = cv2.HoughCircles(blurred, cv2.HOUGH_GRADIENT, dp=1.2,
                              minDist=max(2*min_radius, 8), param1=110,
                              param2=22, minRadius=min_radius, maxRadius=max_radius)
    if circles is None:
        return []
    return [{"x": float(x+cx), "y": float(y+cy), "radius": float(r)}
            for cx,cy,r in circles[0]]

def ensure_via_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS via_candidates (
        id INTEGER PRIMARY KEY, layer TEXT NOT NULL,
        x REAL NOT NULL, y REAL NOT NULL, radius REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'candidate',
        CHECK(status IN ('candidate','accepted','rejected')))""")

def list_vias(db, layer, status=""):
    if status not in ("", "candidate", "accepted", "rejected"):
        raise ValueError("Invalid via status")
    ensure_via_schema(db)
    sql = "SELECT id,layer,x,y,radius,status FROM via_candidates WHERE layer=?"
    params = [layer]
    if status:
        sql += " AND status=?"
        params.append(status)
    return [dict(zip(("id","layer","x","y","radius","status"), row))
            for row in db.execute(sql+" ORDER BY id", params)]
