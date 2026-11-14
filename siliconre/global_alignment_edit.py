"""Backed-up, guarded global alignment edits; regional research records stay independent."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from annotations import backup_annotations
from layer_alignment import load_alignment,save_alignment,similarity_matrix


def current(db,base,overlay):
    try:
        return load_alignment(db,base,overlay).tolist()
    except ValueError:
        return None


def same(a,b):
    if a is None or b is None:
        return a is None and b is None
    a,b=np.asarray(a,dtype=float),np.asarray(b,dtype=float)
    return a.shape==b.shape==(3,3) and bool(np.allclose(a,b,rtol=0,atol=1e-9))


def history(db,limit=50):
    if not 1<=limit<=200:
        raise ValueError('History limit must be 1..200')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='global_alignment_history'").fetchone():
        return []
    rows=db.execute('SELECT seq,payload,undone FROM global_alignment_history ORDER BY seq DESC LIMIT ?',(limit,))
    return [dict(seq=seq,**json.loads(payload),undone=bool(undone)) for seq,payload,undone in rows]


def write(project,base,overlay,dx,dy,rotation_deg=0,scale=1,expected_matrix=None,note='',references=None):
    project=Path(project).resolve()
    path=project/'project.sqlite3'
    if not path.is_file():
        raise FileNotFoundError('Existing project required')
    if base==overlay:
        raise ValueError('Distinct layers required')
    matrix=similarity_matrix(dx,dy,rotation_deg,scale).tolist()
    if not isinstance(note,str) or len(note)>8000:
        raise ValueError('Invalid note')
    references=references or []
    if len(references)>1000:
        raise ValueError('At most 1000 reference pairs')
    # Validate metadata before locking and serializing it into history.
    json.dumps(references,allow_nan=False)
    with closing(sqlite3.connect(str(path),timeout=30)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            for layer in (base,overlay):
                if not db.execute('SELECT 1 FROM layers WHERE name=?',(layer,)).fetchone():
                    raise ValueError('Unknown layer: '+layer)
            old=current(db,base,overlay)
            if not same(old,expected_matrix):
                raise ValueError('Global alignment changed; reload get_layer_alignment and pass source_to_base as expected_matrix')
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
                backup=backup_annotations(source,project)
            # Capture both directions after schema migration, preserving legacy reverse rows.
            from layer_alignment import ensure_alignment_schema
            ensure_alignment_schema(db)
            before=db.execute('SELECT base,overlay,dx,dy,rotation_deg,scale FROM alignments WHERE (base=? AND overlay=?) OR (base=? AND overlay=?)',
                              (base,overlay,overlay,base)).fetchall()
            save_alignment(db,base,overlay,dx,dy,rotation_deg,scale)
            db.execute('CREATE TABLE IF NOT EXISTS global_alignment_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0)')
            payload=dict(base_layer=base,overlay_layer=overlay,before=before,matrix=matrix,
                note=note,reference_points=references,backup=str(backup),timestamp=datetime.now(timezone.utc).isoformat())
            cur=db.execute('INSERT INTO global_alignment_history(payload) VALUES(?)',(json.dumps(payload,allow_nan=False),))
            db.commit()
            return dict(seq=cur.lastrowid,**payload,verified=False)
        except Exception:
            db.rollback()
            raise


def undo(project,expected_seq):
    project=Path(project).resolve()
    path=project/'project.sqlite3'
    if not path.is_file():
        raise FileNotFoundError('Existing project required')
    with closing(sqlite3.connect(str(path),timeout=30)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute('SELECT seq,payload FROM global_alignment_history WHERE undone=0 ORDER BY seq DESC LIMIT 1').fetchone()
            if not row or row[0]!=expected_seq:
                raise ValueError('History changed; reload global alignment history')
            p=json.loads(row[1])
            base,overlay=p['base_layer'],p['overlay_layer']
            if not same(current(db,base,overlay),p['matrix']):
                raise ValueError('Alignment changed outside this history; refusing undo')
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
                backup=backup_annotations(source,project)
            db.execute('DELETE FROM alignments WHERE (base=? AND overlay=?) OR (base=? AND overlay=?)',(base,overlay,overlay,base))
            db.executemany('INSERT INTO alignments(base,overlay,dx,dy,rotation_deg,scale) VALUES(?,?,?,?,?,?)',p['before'])
            db.execute('UPDATE global_alignment_history SET undone=1 WHERE seq=?',(expected_seq,))
            db.commit()
            return dict(seq=expected_seq,action='undo',backup=str(backup),source_to_base=current(db,base,overlay))
        except Exception:
            db.rollback()
            raise
