"""Read-only local MCP interface to SiliconRE project metadata."""
import os
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP

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

if __name__ == "__main__":
    server.run(transport="stdio")
