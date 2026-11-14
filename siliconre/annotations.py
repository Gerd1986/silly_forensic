"""Project-wide annotation reset, retaining layer images and registration."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4


# Children first; do not reset IDs, to avoid reusing externally referenced IDs.
ANNOTATION_TABLES = (
    "logic_pins", "logic_cells", "mos_devices", "via_trace_links",
    "via_layer_links", "candidate_links", "mos_candidates",
    "via_candidates", "trace_candidates", "markers",
)


def backup_annotations(db, project):
    folder = Path(project) / "annotation_backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"before_clear_{stamp}_{uuid4().hex[:8]}.sqlite3"
    try:
        with closing(sqlite3.connect(str(path))) as target:
            db.backup(target)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def clear_annotations(db):
    """Atomically clear existing annotation tables, including older projects."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    deleted = {}
    db.execute("SAVEPOINT clear_all_annotations")
    try:
        for table in ANNOTATION_TABLES:
            if table in tables:
                deleted[table] = db.execute(f'DELETE FROM "{table}"').rowcount
        if 'analysis_records' in tables:
            deleted['research_evidence'] = db.execute(
                "DELETE FROM analysis_records WHERE type='evidence'").rowcount
            # Clear-all is restored through its full database backup, like legacy
            # annotations. It is a history barrier: do not resurrect cleared data
            # or references to it through an older research undo/redo branch.
            if 'analysis_history' in tables:
                db.execute('UPDATE analysis_history SET active=0')
        db.execute("RELEASE SAVEPOINT clear_all_annotations")
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT clear_all_annotations")
        db.execute("RELEASE SAVEPOINT clear_all_annotations")
        raise
    return deleted
