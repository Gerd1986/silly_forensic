"""Geometry-only cross-layer via pairing suggestions; never auto-connects nets."""
from math import hypot
try:
    from .vias import ensure_via_schema
except ImportError:  # direct-script PyInstaller entrypoint
    from vias import ensure_via_schema

def suggest_via_pairs(db, base_layer, overlay_layer, tolerance=10.0):
    if base_layer == overlay_layer or tolerance <= 0:
        raise ValueError("Choose distinct layers and positive tolerance")
    ensure_via_schema(db)
    base=db.execute("SELECT id,x,y FROM via_candidates WHERE layer=? AND status='accepted'",
                    (base_layer,)).fetchall()
    overlay=db.execute("SELECT id,x,y FROM via_candidates WHERE layer=? AND status='accepted'",
                       (overlay_layer,)).fetchall()
    # Stored alignment maps overlay coordinates into base coordinates.
    row=db.execute("SELECT dx,dy FROM alignments WHERE base=? AND overlay=?",
                   (base_layer,overlay_layer)).fetchone()
    if row is None:
        reverse=db.execute("SELECT dx,dy FROM alignments WHERE base=? AND overlay=?",
                           (overlay_layer,base_layer)).fetchone()
        if reverse is None:
            raise ValueError("No saved alignment for this layer pair")
        dx,dy=-reverse[0],-reverse[1]
    else:
        dx,dy=row
    proposals=[]
    for a,ax,ay in base:
        for b,bx,by in overlay:
            distance=hypot(ax-(bx+dx),ay-(by+dy))
            if distance <= tolerance:
                proposals.append({"via_a":a,"via_b":b,"base_layer":base_layer,
                                  "overlay_layer":overlay_layer,"distance_px":round(distance,3),
                                  "verified":False})
    return sorted(proposals,key=lambda p:(p["distance_px"],p["via_a"],p["via_b"]))
