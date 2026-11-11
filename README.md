# silly_forensic / SiliconRE

Windows-first SEM delayering reverse-engineering prototype.

## v0.1
- PySide6 desktop viewer with mouse-wheel zoom, drag pan, and fit-to-view
- Import SEM PNG/JPEG/TIFF images as named layers
- SQLite project metadata and point annotations
- Local read-only MCP server exposing layers and annotations

## Run (Windows, no virtual environment required)

Install Python 3.12+, then run from the repository root:

```powershell
py -m pip install -r requirements.txt
py siliconre/app.py
```

Create a project in an empty folder, import SEM images and select a layer.

## MCP

Set `SILICONRE_PROJECT` to the folder containing `project.sqlite3`. Configure your MCP client to launch `py siliconre/mcp_server.py` over stdio. The server only reads project metadata.

## Current limitations

Prototype, not yet tested on Windows. Qt currently decodes entire images into memory; tiled/pyramid viewing is planned for large SEM datasets. Images are referenced by absolute paths. Layer alignment, segmentation, via detection, transistor recognition and netlist extraction are not yet implemented.