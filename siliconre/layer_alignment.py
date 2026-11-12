"""Overlay source -> base similarity transforms, using source pixel-edge coordinates.

Positive rotation is clockwise on screen (Y down), around source (0,0).
Scaling and rotation precede translation. Original images are never rewritten.
"""
import math
import numpy as np


def similarity_matrix(dx=0., dy=0., rotation_deg=0., scale=1.):
    dx, dy, angle, scale = [float(v) for v in (dx, dy, rotation_deg, scale)]
    if not all(math.isfinite(v) for v in (dx, dy, angle, scale)) or not .01 <= scale <= 100:
        raise ValueError("Finite offsets/angle and scale between 0.01 and 100 required")
    c, s = math.cos(math.radians(angle))*scale, math.sin(math.radians(angle))*scale
    return np.array([[c, -s, dx], [s, c, dy], [0., 0., 1.]])


def ensure_alignment_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS alignments (
        base TEXT, overlay TEXT, dx REAL, dy REAL,
        rotation_deg REAL NOT NULL DEFAULT 0, scale REAL NOT NULL DEFAULT 1,
        PRIMARY KEY(base,overlay))""")
    columns = {r[1] for r in db.execute("PRAGMA table_info(alignments)")}
    for name, default in (("rotation_deg", 0), ("scale", 1)):
        if name not in columns:
            db.execute(f"ALTER TABLE alignments ADD COLUMN {name} REAL NOT NULL DEFAULT {default}")


def load_alignment(db, base, overlay):
    """Read legacy/current schemas without writes; inverse-map reverse pairs."""
    if base == overlay:
        return np.eye(3)
    columns = {r[1] for r in db.execute("PRAGMA table_info(alignments)")}
    if not columns:
        raise ValueError("No saved alignment for this layer pair")
    angle = "rotation_deg" if "rotation_deg" in columns else "0"
    scale = "scale" if "scale" in columns else "1"
    sql = f"SELECT dx,dy,{angle},{scale} FROM alignments WHERE base=? AND overlay=?"
    row = db.execute(sql, (base, overlay)).fetchone()
    if row is not None:
        return similarity_matrix(*row)
    row = db.execute(sql, (overlay, base)).fetchone()
    if row is None:
        raise ValueError("No saved alignment for this layer pair")
    return np.linalg.inv(similarity_matrix(*row))


def alignment_parameters(matrix):
    return (float(matrix[0, 2]), float(matrix[1, 2]),
            math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])),
            math.hypot(matrix[0, 0], matrix[1, 0]))


def save_alignment(db, base, overlay, dx, dy, rotation_deg=0., scale=1.):
    if base == overlay:
        raise ValueError("Choose distinct layers")
    similarity_matrix(dx, dy, rotation_deg, scale)
    ensure_alignment_schema(db)
    db.execute("""INSERT OR REPLACE INTO alignments
        (base,overlay,dx,dy,rotation_deg,scale) VALUES (?,?,?,?,?,?)""",
        (base, overlay, dx, dy, rotation_deg, scale))
    # A stale reverse entry must not override a new manual correction.
    db.execute("DELETE FROM alignments WHERE base=? AND overlay=?", (overlay, base))
