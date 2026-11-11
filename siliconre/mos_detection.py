"""Conservative experimental MOS candidates from aligned binary POLY and ACTIVE masks.

Images must already be aligned and represent the same pixel coordinate system.
A crossing is a candidate, never an automatically accepted transistor.
"""
import cv2
import numpy as np
from PIL import Image, ImageOps

def _binary(path, roi, polarity, threshold):
    x,y,w,h=roi
    with Image.open(path) as img:
        if x<0 or y<0 or x+w>img.width or y+h>img.height:
            raise ValueError("ROI outside mask image")
        gray=np.asarray(ImageOps.grayscale(img.crop((x,y,x+w,y+h))),dtype=np.uint8)
    if threshold==0:
        _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    else:
        _,mask=cv2.threshold(gray,threshold,255,cv2.THRESH_BINARY)
    if polarity=="dark":
        mask=cv2.bitwise_not(mask)
    elif polarity!="bright":
        raise ValueError("Polarity must be bright or dark")
    return mask

def detect_mos_candidates(poly_path, active_path, roi, poly_polarity="bright",
                          active_polarity="bright", threshold=0, min_area=5):
    x,y,w,h=map(int,roi)
    if w<1 or h<1 or w*h>12_000_000 or min_area<1 or not 0<=threshold<=255:
        raise ValueError("Invalid ROI or threshold")
    poly=_binary(poly_path,(x,y,w,h),poly_polarity,threshold)
    active=_binary(active_path,(x,y,w,h),active_polarity,threshold)
    overlap=cv2.bitwise_and(poly,active)
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(overlap,8)
    proposals=[]
    for i in range(1,count):
        lx,ly,lw,lh,area=map(int,stats[i])
        if area<min_area:
            continue
        proposals.append({"x":x+lx,"y":y+ly,"width":lw,"height":lh,
                          "area":area,"center_x":x+float(centroids[i][0]),
                          "center_y":y+float(centroids[i][1]),
                          "status":"candidate","kind":"unknown"})
    return proposals

def ensure_mos_candidate_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS mos_candidates (
        id INTEGER PRIMARY KEY, poly_layer TEXT NOT NULL, active_layer TEXT NOT NULL,
        x REAL NOT NULL, y REAL NOT NULL, width REAL NOT NULL, height REAL NOT NULL,
        area INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'candidate',
        CHECK(status IN ('candidate','accepted','rejected')))""")

def list_mos_candidates(db,poly_layer="",status=""):
    if status not in ("","candidate","accepted","rejected"):
        raise ValueError("Invalid candidate status")
    ensure_mos_candidate_schema(db)
    sql="SELECT id,poly_layer,active_layer,x,y,width,height,area,status FROM mos_candidates WHERE 1=1"
    args=[]
    if poly_layer:
        sql+=" AND poly_layer=?"
        args.append(poly_layer)
    if status:
        sql+=" AND status=?"
        args.append(status)
    keys=("id","poly_layer","active_layer","x","y","width","height","area","status")
    return [dict(zip(keys,row)) for row in db.execute(sql+" ORDER BY id",args)]
