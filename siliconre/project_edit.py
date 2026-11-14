"""Transactional project edits used by MCP; no arbitrary SQL or filesystem writes."""
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from annotations import backup_annotations
from layer_alignment import save_alignment, similarity_matrix, alignment_parameters
from netgraph import add_link
from reconstruction import associate_via_trace, connect_vias, ensure_schema
from devices import add_device, ensure_device_schema
from logic_analysis import add_logic_cell, ensure_logic_schema


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS markers(id INTEGER PRIMARY KEY,layer TEXT,x REAL,y REAL,label TEXT)")
    db.execute("""CREATE TABLE IF NOT EXISTS trace_candidates(id INTEGER PRIMARY KEY,
        layer TEXT NOT NULL,x REAL,y REAL,width REAL,height REAL,status TEXT NOT NULL)""")
    ensure_schema(db)
    ensure_device_schema(db)
    ensure_logic_schema(db)
    db.execute("""CREATE TABLE IF NOT EXISTS mcp_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,layer TEXT NOT NULL,label TEXT NOT NULL,data TEXT NOT NULL,
        note TEXT NOT NULL,status TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS mcp_edit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,operations TEXT NOT NULL,backup TEXT NOT NULL)""")


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Coordinates must be finite")
    return value


def status(value):
    if value not in ('candidate', 'accepted', 'rejected'):
        raise ValueError("Invalid review status")
    return value


def layer_exists(db, name):
    if not db.execute("SELECT 1 FROM layers WHERE name=?", (name,)).fetchone():
        raise ValueError("Unknown layer: " + str(name))


def points(value, minimum=2):
    if not isinstance(value, list) or not minimum <= len(value) <= 10000:
        raise ValueError("Invalid point count")
    if any(not isinstance(p, (list, tuple)) or len(p) != 2 for p in value):
        raise ValueError("Each point must contain x,y")
    return [[finite(x), finite(y)] for x,y in value]


def fit_alignment(base_points, overlay_points):
    """Least-squares orientation-preserving similarity; original pixel coordinates."""
    a = np.array(points(base_points))
    b = np.array(points(overlay_points))
    if a.shape != b.shape:
        raise ValueError("Point lists must have equal length")
    center_a, center_b = a.mean(0), b.mean(0)
    aa, bb = a-center_a, b-center_b
    denom = float((bb*bb).sum())
    if denom < 1e-12:
        raise ValueError("Overlay control points must be distinct")
    u, singular, vt = np.linalg.svd(bb.T @ aa)
    sign = np.eye(2)
    sign[-1,-1] = np.linalg.det(u @ vt)
    rotation = u @ sign @ vt
    scale = float((singular * np.diag(sign)).sum()/denom)
    translation = center_a - scale * center_b @ rotation
    matrix = np.eye(3)
    matrix[:2,:2] = scale * rotation.T
    matrix[:2,2] = translation
    dx,dy,angle,scale = alignment_parameters(matrix)
    similarity_matrix(dx,dy,angle,scale)
    errors = np.linalg.norm(scale*b @ rotation + translation-a, axis=1)
    return dict(dx=dx,dy=dy,rotation_deg=angle,scale=scale,
                residuals_px=errors.tolist(),rms_error_px=float(np.sqrt((errors**2).mean())),
                max_error_px=float(errors.max()),source_to_base=matrix.tolist(),
                verified=False,point_count=len(a))


def evidence_data(db,kind,data):
    if kind not in ('polyline','region','hypothesis','alignment_points') or not isinstance(data,dict):
        raise ValueError('Invalid evidence kind or data')
    if kind in ('polyline','region'):
        data = {**data,'points':points(data['points'],3 if kind=='region' else 2)}
    if kind == 'alignment_points':
        layer_exists(db,data['overlay_layer'])
        data = {**data,'base_points':points(data['base_points']),
                'overlay_points':points(data['overlay_points'])}
        fit_alignment(data['base_points'],data['overlay_points'])
    return json.dumps(data,allow_nan=False)


def apply_operation(db, op):
    action = op['action']
    layer = op.get('layer', '')
    if action in ('marker','trace_candidate','via','evidence'):
        layer_exists(db, layer)
    if action == 'marker':
        cur = db.execute("INSERT INTO markers(layer,x,y,label) VALUES(?,?,?,?)",
                         (layer,finite(op['x']),finite(op['y']),str(op.get('label',''))))
    elif action == 'trace_candidate':
        w,h = finite(op['width']),finite(op['height'])
        if min(w,h) <= 0:
            raise ValueError("Positive candidate dimensions required")
        cur = db.execute("INSERT INTO trace_candidates(layer,x,y,width,height,status) VALUES(?,?,?,?,?,?)",
            (layer,finite(op['x']),finite(op['y']),w,h,status(op.get('status','candidate'))))
    elif action == 'via':
        radius = finite(op['radius'])
        if radius <= 0:
            raise ValueError("Positive radius required")
        cur = db.execute("INSERT INTO via_candidates(layer,x,y,radius,status) VALUES(?,?,?,?,?)",
            (layer,finite(op['x']),finite(op['y']),radius,status(op.get('status','candidate'))))
    elif action == 'evidence':
        kind = op['kind']
        data = evidence_data(db,kind,op.get('data',{}))
        cur = db.execute("INSERT INTO mcp_evidence(kind,layer,label,data,note,status) VALUES(?,?,?,?,?,?)",
            (kind,layer,str(op.get('label','')),data,
             str(op.get('note','')),status(op.get('status','candidate'))))
    elif action == 'alignment':
        base,overlay = op['base_layer'],op['overlay_layer']
        layer_exists(db,base)
        layer_exists(db,overlay)
        save_alignment(db,base,overlay,finite(op['dx']),finite(op['dy']),
                       finite(op.get('rotation_deg',0)),finite(op.get('scale',1)))
        return {'action':action,'base_layer':base,'overlay_layer':overlay}
    elif action == 'trace_link':
        layer_exists(db,layer)
        add_link(db,layer,op['source_id'],op['target_id'])
        return {'action':action}
    elif action == 'via_trace_link':
        associate_via_trace(db,op['via_id'],op['trace_id'])
        return {'action':action}
    elif action == 'via_layer_link':
        connect_vias(db,op['via_a'],op['via_b'])
        return {'action':action}
    elif action == 'mos_device':
        layer_exists(db,layer)
        add_device(db,op['name'],op['kind'],layer,finite(op['x']),finite(op['y']),
                   op['gate'],op['drain'],op['source'],op.get('bulk'),str(op.get('note','')))
        return {'action':action}
    elif action == 'logic_cell':
        layer_exists(db,layer)
        add_logic_cell(db,op['name'],op['kind'],layer,op['pins'],str(op.get('note','')))
        return {'action':action}
    elif action in ('update','delete'):
        # Fixed allowlist, never accept SQL identifiers supplied by a caller.
        tables = {'marker':('markers',{'x','y','label'}),
                  'trace_candidate':('trace_candidates',{'x','y','width','height','status'}),
                  'via':('via_candidates',{'x','y','radius','status'}),
                  'evidence':('mcp_evidence',{'label','note','status','data'}),
                  'trace_link':('candidate_links',set()),
                  'via_trace_link':('via_trace_links',set()),
                  'via_layer_link':('via_layer_links',set()),
                  'mos_device':('mos_devices',{'note'}),
                  'logic_cell':('logic_cells',{'note'})}
        table,allowed = tables[op['kind']]
        ident = int(op['id'])
        if not db.execute(f'SELECT 1 FROM {table} WHERE id=?',(ident,)).fetchone():
            raise ValueError("Object does not exist")
        # Prevent dangling electrical references when removing/rejecting a candidate.
        fields = op.get('fields',{})
        removing = action=='delete' or fields.get('status') in ('candidate','rejected')
        if removing and table=='trace_candidates':
            if db.execute("SELECT 1 FROM logic_pins WHERE trace_id=?",(ident,)).fetchone():
                raise ValueError("Remove dependent logic cells first")
            if db.execute("SELECT 1 FROM mos_devices WHERE gate_trace=? OR drain_trace=? OR source_trace=? OR bulk_trace=?",(ident,)*4).fetchone():
                raise ValueError("Remove dependent MOS devices first")
            db.execute("DELETE FROM candidate_links WHERE source_id=? OR target_id=?",(ident,ident))
            db.execute("DELETE FROM via_trace_links WHERE trace_id=?",(ident,))
        if removing and table=='via_candidates':
            db.execute("DELETE FROM via_trace_links WHERE via_id=?",(ident,))
            db.execute("DELETE FROM via_layer_links WHERE via_a=? OR via_b=?",(ident,ident))
        if action=='delete':
            if table=='logic_cells':
                db.execute("DELETE FROM logic_pins WHERE cell_id=?",(ident,))
            db.execute(f'DELETE FROM {table} WHERE id=?',(ident,))
        else:
            if not fields or not set(fields)<=allowed:
                raise ValueError("Unsupported update fields")
            clean = {}
            for key,value in fields.items():
                if key=='data':
                    kind=db.execute('SELECT kind FROM mcp_evidence WHERE id=?',(ident,)).fetchone()[0]
                    clean[key]=evidence_data(db,kind,value)
                else:
                    clean[key] = status(value) if key=='status' else str(value) if key in ('label','note') else finite(value)
                if key in ('width','height','radius') and clean[key]<=0:
                    raise ValueError("Positive size required")
            db.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),ident))
        return {'action':action,'kind':op['kind'],'id':ident}
    else:
        raise ValueError("Unknown edit action: " + action)
    return {'action':action,'id':cur.lastrowid}


def edit_project(project, operations):
    if not isinstance(operations,list) or not 1 <= len(operations) <= 500:
        raise ValueError("Supply 1..500 operations")
    serialized = json.dumps(operations,allow_nan=False)
    if len(serialized)>2_000_000:
        raise ValueError("Edit batch too large")
    path = Path(project)/'project.sqlite3'
    if not path.is_file():
        raise FileNotFoundError("Existing project required")
    with closing(sqlite3.connect(str(path),timeout=30)) as db:
        # Reserve writer before snapshot: backup and edits describe the same state.
        db.execute('BEGIN IMMEDIATE')
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
                backup = backup_annotations(source,project)
            schema(db)
            result = [apply_operation(db,op) for op in operations]
            db.execute("INSERT INTO mcp_edit_log(timestamp,operations,backup) VALUES(?,?,?)",
                (datetime.now(timezone.utc).isoformat(),serialized,str(backup)))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {'results':result,'backup':str(backup),'electrically_verified':False}
