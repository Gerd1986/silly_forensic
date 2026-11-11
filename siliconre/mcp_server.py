"""Read-only local MCP interface to SiliconRE project metadata."""
import os
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from netgraph import list_links, connected_groups

server = FastMCP("SiliconRE")
PROJECT = Path(os.environ.get("SILICONRE_PROJECT", ".")).resolve()

def connect():
    db = PROJECT / "project.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("Set SILICONRE_PROJECT to an existing SiliconRE project")
    return sqlite3.connect(str(db))

@server.tool()
def list_layers() -> list[dict]:
    """List SEM layers and original image paths in the active project."""
    with connect() as db:
        return [{"name": name, "path": path}
                for name, path in db.execute("SELECT name,path FROM layers ORDER BY name")]

@server.tool()
def list_annotations(layer: str) -> list[dict]:
    """Get saved annotation coordinates for a layer."""
    with connect() as db:
        return [{"id": ident, "x": x, "y": y, "label": label}
                for ident, x, y, label in db.execute(
                    "SELECT id,x,y,label FROM markers WHERE layer=?", (layer,))]

@server.tool()
def list_trace_candidates(layer: str, status: str = "") -> list[dict]:
    """List reviewed SEM candidate boxes; status can be candidate, accepted, rejected, or empty for all."""
    if status not in ("", "candidate", "accepted", "rejected"):
        raise ValueError("Invalid status")
    with connect() as db:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trace_candidates'").fetchone()
        if not table:
            return []
        sql = "SELECT id,layer,x,y,width,height,status FROM trace_candidates WHERE layer=?"
        params = [layer]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id"
        return [dict(zip(("id","layer","x","y","width","height","status"), row))
                for row in db.execute(sql, params)]

@server.tool()
def list_manual_links(layer: str) -> list[dict]:
    """List explicitly user-created visual links (not electrically verified)."""
    with connect() as db:
        return list_links(db, layer)

@server.tool()
def list_manual_net_groups(layer: str) -> list[list[int]]:
    """List connected groups of accepted candidates from manual visual links."""
    with connect() as db:
        return connected_groups(db, layer)

if __name__ == "__main__":
    server.run(transport="stdio")
