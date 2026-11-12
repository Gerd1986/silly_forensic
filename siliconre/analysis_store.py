"""Reversible research records. Coordinates always belong to original source layers.

These records deliberately do not modify the reconstructed electrical graph.
All writes use one SQLite savepoint; reads never create/migrate tables.
"""
import json
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np

STATUSES = ("candidate", "confirmed", "rejected")
KINDS = ("point", "trace", "contact", "region", "note")


def _exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_records'").fetchone() is not None


@contextmanager
def _write(db):
    db.execute("SAVEPOINT analysis_edit")
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_records (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_history (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL,
            before_json TEXT, after_json TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            last_action_at TEXT, last_action_actor TEXT)""")
        # Persistent monotonic versions prevent stale edits after undo/delete/recreate (ABA).
        db.execute("CREATE TABLE IF NOT EXISTS analysis_versions (id TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
        yield
        db.execute("RELEASE SAVEPOINT analysis_edit")
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT analysis_edit")
        db.execute("RELEASE SAVEPOINT analysis_edit")
        raise


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _text(value, name, limit=8000, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"Invalid {name}")
    return value


def _point(point):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError("Each point must contain X and Y")
    p = [float(v) for v in point]
    if not all(math.isfinite(v) and v >= 0 for v in p):
        raise ValueError("Source coordinates must be finite and nonnegative")
    return p


def _layer(db, name):
    if not db.execute("SELECT 1 FROM layers WHERE name=?", (name,)).fetchone():
        raise ValueError(f"Unknown layer: {name}")


def get_record(db, record_id):
    row = db.execute("SELECT payload FROM analysis_records WHERE id=?", (record_id,)).fetchone() if _exists(db) else None
    if row is None:
        raise ValueError("Unknown analysis record")
    return json.loads(row[0])


def list_records(db, record_type="", layer="", status=""):
    if record_type not in ("", "alignment", "evidence") or status not in ("", *STATUSES):
        raise ValueError("Invalid type/status filter")
    if not _exists(db):
        return []
    rows = db.execute("SELECT payload FROM analysis_records ORDER BY id")
    result = []
    for row in rows:
        d = json.loads(row[0])
        layers = ([d['base_layer'], d['overlay_layer']] if d['type'] == 'alignment'
                  else [g['layer'] for g in d['geometry']])
        if (not record_type or d['type'] == record_type) and (not layer or layer in layers) and (not status or d['status'] == status):
            result.append(d)
    return result


def _put(db, record, record_id, expected_revision, actor):
    actor = _text(actor, "actor", 200, True)
    old = get_record(db, record_id) if record_id else None
    if old and (expected_revision is None or old['revision'] != expected_revision):
        raise ValueError("Stale revision; reload the record before editing")
    if old and old['type'] != record['type']:
        raise ValueError("Cannot change record type")
    ident = record_id or uuid4().hex
    before = _json(old) if old else None
    row = db.execute("SELECT revision FROM analysis_versions WHERE id=?", (ident,)).fetchone()
    version = row[0] + 1 if row else 1
    record.update(id=ident, revision=version, updated_at=datetime.now(timezone.utc).isoformat(), actor=actor)
    after = _json(record)
    db.execute("INSERT OR REPLACE INTO analysis_versions VALUES (?,?)", (ident, version))
    db.execute("INSERT OR REPLACE INTO analysis_records VALUES (?,?,?)", (ident, record['type'], after))
    db.execute("UPDATE analysis_history SET active=0 WHERE undone=1 AND active=1")
    db.execute("INSERT INTO analysis_history(record_id,before_json,after_json,actor,created_at) VALUES (?,?,?,?,?)",
               (ident, before, after, actor, record['updated_at']))
    return record


def _review(status, note):
    if status not in STATUSES:
        raise ValueError("Status must be candidate, confirmed or rejected")
    _text(note, "review note", required=status != 'candidate')


def _matrix(matrix):
    m = np.asarray(matrix, dtype=float)
    if m.shape != (3, 3) or not np.isfinite(m).all() or not np.allclose(m[2], [0, 0, 1]):
        raise ValueError("Expected a finite 3x3 affine matrix")
    singular = np.linalg.svd(m[:2, :2], compute_uv=False)
    if min(singular) < .01 or max(singular) > 100 or np.linalg.det(m[:2, :2]) <= 0:
        raise ValueError("Transform must preserve orientation and have scales within 0.01..100")
    return m


def fit_references(references, mode="similarity"):
    """Fit only training pairs; check=True pairs remain independent holdout points."""
    if mode not in ('similarity', 'affine'):
        raise ValueError("Fit mode must be similarity or affine")
    train = [p for p in references if not p.get('check', False)]
    minimum = 2 if mode == 'similarity' else 3
    if len(train) < minimum:
        raise ValueError(f"At least {minimum} training reference pairs required")
    source = np.array([_point(p['source']) for p in train])
    target = np.array([_point(p['target']) for p in train])
    if mode == 'affine':
        design = np.column_stack((source, np.ones(len(source))))
        if np.linalg.matrix_rank(design) < 3:
            raise ValueError("Affine reference points must not be collinear")
        m = np.vstack((np.linalg.lstsq(design, target, rcond=None)[0].T, [0, 0, 1]))
    else:
        x, y = source.T
        design = np.zeros((2*len(source), 4)); rhs = target.reshape(-1)
        design[::2] = np.column_stack((x, -y, np.ones(len(x)), np.zeros(len(x))))
        design[1::2] = np.column_stack((y, x, np.zeros(len(x)), np.ones(len(x))))
        if np.linalg.matrix_rank(design) < 4:
            raise ValueError("Distinct reference points required")
        a, b, dx, dy = np.linalg.lstsq(design, rhs, rcond=None)[0]
        m = np.array([[a, -b, dx], [b, a, dy], [0, 0, 1]])
    return _matrix(m)


def save_region(db, base_layer, overlay_layer, roi, name, reference_points=None,
                matrix=None, fit_mode="similarity", status="candidate", note="",
                record_id=None, expected_revision=None, actor="MCP"):
    _layer(db, base_layer); _layer(db, overlay_layer)
    if base_layer == overlay_layer:
        raise ValueError("Distinct layers required")
    if len(roi) != 4:
        raise ValueError("ROI is [x,y,width,height] in base pixels")
    x, y = _point(roi[:2]); w, h = map(float, roi[2:])
    if not all(math.isfinite(v) and v > 0 for v in (w, h)):
        raise ValueError("Positive finite ROI dimensions required")
    _text(name, "name", 300, True); _review(status, note)
    refs = reference_points or []
    if len(refs) > 1000:
        raise ValueError("At most 1000 reference pairs")
    if any(not isinstance(p.get('check',False),bool) for p in refs):
        raise ValueError('Reference check flags must be JSON booleans')
    refs = [dict(source=_point(p['source']), target=_point(p['target']), check=bool(p.get('check', False))) for p in refs]
    m = fit_references(refs, fit_mode) if matrix is None else _matrix(matrix)
    residuals = []
    for p in refs:
        predicted = (m @ [*p['source'], 1])[:2]
        residuals.append(dict(check=p['check'], error_px=float(np.linalg.norm(predicted-p['target']))))
    if status == 'confirmed' and not any(p['check'] for p in refs):
        raise ValueError("Confirmed regional registration requires an independent check pair and review note")
    record = dict(type='alignment', base_layer=base_layer, overlay_layer=overlay_layer,
                  roi=[x, y, w, h], name=name, matrix=m.tolist(), reference_points=refs,
                  residuals=residuals, fit_mode=fit_mode if matrix is None else 'manual', status=status, note=note)
    with _write(db):
        return _put(db, record, record_id, expected_revision, actor)


def region_matrix(db, record_id, base, overlay, rectangle):
    """Explicit region only, direct pair only, strict containment; never extrapolate."""
    r = get_record(db, record_id)
    if r['type'] != 'alignment' or (r['base_layer'], r['overlay_layer']) != (base, overlay):
        raise ValueError("Regional alignment belongs to a different layer pair")
    if r['status'] == 'rejected':
        raise ValueError("Rejected regional alignment cannot be applied")
    x, y, w, h = map(float, rectangle); rx, ry, rw, rh = r['roi']
    if not all(math.isfinite(v) for v in (x,y,w,h)) or w <= 0 or h <= 0 or x < rx or y < ry or x+w > rx+rw+1e-6 or y+h > ry+rh+1e-6:
        raise ValueError("Requested view exceeds the regional alignment ROI")
    return _matrix(r['matrix']), r


def save_evidence(db, kind, geometry, label="", status="candidate", note="",
                  references=None, record_id=None, expected_revision=None, actor="MCP", provenance=None):
    if kind not in KINDS or not 1 <= len(geometry) <= 32:
        raise ValueError("Invalid evidence kind or geometry count")
    _text(label, "label", 500); _review(status, note)
    provenance=provenance or {}
    if not isinstance(provenance,dict) or len(_json(provenance))>32000:
        raise ValueError('Provenance must be a JSON object of at most 32000 characters')
    clean = []
    for g in geometry:
        _layer(db, g['layer']); pts = [_point(p) for p in g['points']]
        if not 1 <= len(pts) <= 10000 or (kind == 'trace' and len(pts) < 2) or (kind == 'region' and len(pts) < 3):
            raise ValueError("Invalid point count for evidence geometry")
        clean.append(dict(layer=g['layer'], points=pts))
    if kind == 'contact' and (len(clean) != 2 or clean[0]['layer'] == clean[1]['layer'] or any(len(g['points']) != 1 for g in clean)):
        raise ValueError("A contact needs one source point on each of two distinct layers")
    if kind == 'point' and (len(clean) != 1 or len(clean[0]['points']) != 1):
        raise ValueError("A point needs exactly one coordinate")
    refs = references or []
    if len(refs) > 100:
        raise ValueError("At most 100 evidence references")
    record = dict(type='evidence', kind=kind, geometry=clean, label=label, status=status, note=note,
                  references=refs,provenance=provenance)
    with _write(db):
        if record_id:
            old=get_record(db,record_id)
            if old.get('geometry')!=clean or old.get('kind')!=kind:
                record['provenance']=dict(provenance)
                record['provenance'].pop('contact_review',None)
        snapshots = []
        for ident in refs:
            if ident == record_id:
                raise ValueError("Cannot reference itself")
            target = get_record(db, ident)
            snapshots.append({k:v for k,v in target.items() if k != 'reference_snapshots'})
        record['reference_snapshots'] = snapshots
        return _put(db, record, record_id, expected_revision, actor)


def delete_record(db, record_id, expected_revision, actor="MCP"):
    with _write(db):
        old = get_record(db, record_id)
        if old['revision'] != expected_revision:
            raise ValueError("Stale revision; reload before deleting")
        dependents = [r['id'] for r in list_records(db) if record_id in r.get('references', [])]
        if dependents:
            raise ValueError("Record is referenced by: " + ', '.join(dependents))
        db.execute("DELETE FROM analysis_records WHERE id=?", (record_id,))
        db.execute("UPDATE analysis_history SET active=0 WHERE undone=1 AND active=1")
        db.execute("INSERT INTO analysis_history(record_id,before_json,after_json,actor,created_at) VALUES (?,?,NULL,?,?)",
                   (record_id, _json(old), _text(actor, 'actor', 200, True), datetime.now(timezone.utc).isoformat()))
    return dict(deleted=record_id)


def history(db, limit=50):
    if not 1 <= limit <= 200:
        raise ValueError("History limit must be 1..200")
    if not _exists(db):
        return []
    return [dict(zip(('seq','record_id','actor','created_at','undone','active','last_action_at','last_action_actor'), row)) for row in db.execute(
        'SELECT seq,record_id,actor,created_at,undone,active,last_action_at,last_action_actor FROM analysis_history ORDER BY seq DESC LIMIT ?', (limit,))]


def undo_redo(db, redo=False, expected_seq=None, actor='MCP'):
    with _write(db):
        row = db.execute('SELECT seq,record_id,before_json,after_json FROM analysis_history '
                         'WHERE active=1 AND undone=? ORDER BY seq '+('ASC' if redo else 'DESC')+' LIMIT 1', (int(redo),)).fetchone()
        if not row:
            raise ValueError('Nothing to redo' if redo else 'Nothing to undo')
        seq, ident, before, after = row
        if expected_seq is not None and expected_seq != seq:
            raise ValueError("History changed; refresh before undo/redo")
        expected, restore = (before, after) if redo else (after, before)
        current = db.execute('SELECT payload FROM analysis_records WHERE id=?', (ident,)).fetchone()
        # Payload revision/time changes during undo, so compare stable content only.
        def content(raw):
            d = json.loads(raw) if raw else None
            return {k:v for k,v in d.items() if k not in ('revision','updated_at','actor')} if d else None
        if content(current[0] if current else None) != content(expected):
            raise ValueError("Record changed outside history; undo/redo refused")
        if restore:
            record = json.loads(restore)
            version = db.execute('SELECT revision FROM analysis_versions WHERE id=?', (ident,)).fetchone()[0]+1
            record.update(revision=version, updated_at=datetime.now(timezone.utc).isoformat())
            db.execute('UPDATE analysis_versions SET revision=? WHERE id=?', (version,ident))
            db.execute('INSERT OR REPLACE INTO analysis_records VALUES (?,?,?)', (ident,record['type'],_json(record)))
        else:
            db.execute('DELETE FROM analysis_records WHERE id=?', (ident,))
        db.execute('UPDATE analysis_history SET undone=?,last_action_at=?,last_action_actor=? WHERE seq=?',
                   (0 if redo else 1,datetime.now(timezone.utc).isoformat(),_text(actor,'actor',200,True),seq))
        return dict(seq=seq, record_id=ident, action='redo' if redo else 'undo')
