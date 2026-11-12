"""Geometry-only cross-layer via pairing suggestions; never auto-connects nets."""
from math import hypot
try:
    from .vias import ensure_via_schema
    from .layer_alignment import load_alignment
except ImportError:  # direct-script PyInstaller entrypoint
    from vias import ensure_via_schema
    from layer_alignment import load_alignment

def suggest_via_pairs(db, base_layer, overlay_layer, tolerance=10.0):
    if base_layer == overlay_layer or tolerance <= 0:
        raise ValueError("Choose distinct layers and positive tolerance")
    ensure_via_schema(db)
    base=db.execute("SELECT id,x,y FROM via_candidates WHERE layer=? AND status='accepted'",
                    (base_layer,)).fetchall()
    overlay=db.execute("SELECT id,x,y FROM via_candidates WHERE layer=? AND status='accepted'",
                       (overlay_layer,)).fetchall()
    matrix = load_alignment(db, base_layer, overlay_layer)
    proposals=[]
    for a,ax,ay in base:
        for b,bx,by in overlay:
            mapped = matrix @ [bx, by, 1.]
            distance=hypot(ax-mapped[0],ay-mapped[1])
            if distance <= tolerance:
                proposals.append({"via_a":a,"via_b":b,"base_layer":base_layer,
                                  "overlay_layer":overlay_layer,"distance_px":round(distance,3),
                                  "verified":False})
    return sorted(proposals,key=lambda p:(p["distance_px"],p["via_a"],p["via_b"]))
