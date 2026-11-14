"""Local stdio MCP interface to saved project evidence and bounded image views."""
import base64
import json
import math
from contextlib import closing, contextmanager
import os
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent, ImageContent
from image_regions import render_region, png_bytes, layer_path, region_geometry
from image_preview import image_size
from layer_alignment import load_alignment, alignment_parameters
import numpy as np
from netgraph import list_links, connected_groups
from vias import list_vias
from reconstruction import reconstruct, export_verilog, export_vhdl
from via_matching import suggest_via_pairs
from devices import resolve_devices, spice_netlist
from mos_detection import list_mos_candidates
from debug_ports import analyze_debug_interfaces
from logic_analysis import analyze_sequential
from project_edit import edit_project, fit_alignment
from registration import estimate_translation
import cv2

server = FastMCP("SiliconRE")
PROJECT = Path(os.environ.get("SILICONRE_PROJECT", ".")).resolve()

@contextmanager
def connect():
    db = PROJECT / "project.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("Set SILICONRE_PROJECT to an existing SiliconRE project")
    with closing(sqlite3.connect(str(db))) as connection:
        with connection:
            yield connection

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

@server.tool()
def list_via_candidates(layer: str, status: str = "") -> list[dict]:
    """List unverified via-like circular feature candidates and human review status."""
    with connect() as db:
        return list_vias(db, layer, status)

@server.tool()
def list_reconstructed_nets() -> list[dict]:
    """Read cross-layer graph nets derived only from manually accepted connections."""
    with connect() as db:
        return reconstruct(db)

@server.tool()
def get_structural_hdl(language: str = "verilog") -> str:
    """Return structural wire declarations only; no inferred gates or functional logic."""
    with connect() as db:
        if language == "verilog":
            return export_verilog(db)
        if language == "vhdl":
            return export_vhdl(db)
        raise ValueError("Language must be verilog or vhdl")

@server.tool()
def suggest_cross_layer_vias(base_layer: str, overlay_layer: str, tolerance_px: float = 10.0) -> list[dict]:
    """Read-only geometric via pairing suggestions based on saved layer alignment; unverified."""
    with connect() as db:
        return suggest_via_pairs(db,base_layer,overlay_layer,tolerance_px)

@server.tool()
def list_annotated_transistors() -> list[dict]:
    """Read user-annotated MOS transistors and their resolved net terminals."""
    with connect() as db:
        return resolve_devices(db)

@server.tool()
def get_spice_netlist() -> str:
    """Read SPICE netlist of manually annotated MOS devices; models not supplied."""
    with connect() as db:
        return spice_netlist(db)

@server.tool()
def list_mos_crossing_candidates(poly_layer: str = "", status: str = "") -> list[dict]:
    """Read POLY/ACTIVE overlap proposals; they are not confirmed transistors."""
    with connect() as db:
        return list_mos_candidates(db,poly_layer,status)

@server.tool()
def list_debug_port_hypotheses() -> list[dict]:
    """Read evidence-ranked JTAG/SWD/cJTAG/UART/SPI/I2C/SWIM/BDM/Nexus label clues; unverified."""
    with connect() as db:
        return analyze_debug_interfaces(db)

@server.tool()
def analyze_sequential_logic() -> dict:
    """Read manually annotated DFF clock groups, shift chains and unverified TAP clues."""
    with connect() as db:
        return analyze_sequential(db)


@contextmanager
def visual_connect():
    """Image tools never create or migrate project metadata."""
    path = PROJECT / "project.sqlite3"
    if not path.is_file():
        raise FileNotFoundError("Set SILICONRE_PROJECT to an existing SiliconRE project")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        yield db


def _image_result(metadata, frames):
    content = [TextContent(type="text", text=json.dumps(metadata, allow_nan=False))]
    for label, gray, mask in frames:
        content.append(TextContent(type="text", text=label))
        content.append(ImageContent(type="image", mimeType="image/png",
                       data=base64.b64encode(png_bytes(gray, mask)).decode("ascii")))
    return content


@server.tool()
def get_layer_info(layer: str) -> dict:
    """Get original layer dimensions, pixel coordinate convention and saved alignment availability."""
    with visual_connect() as db:
        path = layer_path(db, PROJECT, layer)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        pairs = ([{"base": a, "overlay": b} for a,b in db.execute(
            "SELECT base,overlay FROM alignments WHERE base=? OR overlay=?", (layer,layer))]
            if "alignments" in tables else [])
    width, height = image_size(path)
    return {"layer": layer, "width": width, "height": height,
            "coordinate_units": "original image pixels; origin at top-left; X right, Y down",
            "alignment_pairs": pairs, "max_image_output_side": 1536}


@server.tool()
def get_layer_alignment(base_layer: str, overlay_layer: str) -> dict:
    """Read saved overlay -> base translation, clockwise rotation and scale; handles reverse pairs.
    Manual registration is provisional, not proof of feature correspondence.
    """
    with visual_connect() as db:
        layer_path(db, PROJECT, base_layer)
        layer_path(db, PROJECT, overlay_layer)
        matrix = load_alignment(db, base_layer, overlay_layer)
    dx, dy, angle, scale = alignment_parameters(matrix)
    return {"base_layer": base_layer, "overlay_layer": overlay_layer,
            "dx": dx, "dy": dy, "rotation_deg_clockwise": angle, "scale": scale,
            "source_to_base": matrix.tolist(), "pivot": [0,0], "verified": False}


@server.tool()
def get_layer_overview(layer: str, max_side: int = 1024):
    """View a box-averaged grayscale overview, at most 1536 pixels/side, with original coordinates.
    Use get_layer_region for detail. This does not extract or accept circuit features.
    """
    region_geometry(0, 0, 1, 1, max_side)
    with visual_connect() as db:
        path = layer_path(db, PROJECT, layer)
    width, height = image_size(path)
    gray, mask, meta = render_region(path, (0,0,width,height), max_side)
    meta.update({"layer": layer, "coordinate_frame": layer, "view": "overview"})
    return _image_result(meta, [(layer+" overview", gray, mask)])


@server.tool()
def get_layer_region(layer: str, x: float, y: float, width: float, height: float,
                     max_side: int = 1024):
    """View a bounded source-coordinate crop (top-left x,y and width,height in original pixels).
    Output is downsampled as needed. Transparent areas indicate missing image coverage.
    """
    region_geometry(x,y,width,height,max_side)
    with visual_connect() as db:
        path = layer_path(db, PROJECT, layer)
    gray, mask, meta = render_region(path, (x,y,width,height), max_side)
    meta.update({"layer": layer, "coordinate_frame": layer, "view": "region"})
    return _image_result(meta, [(layer+" source region", gray, mask)])


@server.tool()
def compare_layer_region(base_layer: str, overlay_layer: str,
                         x: float, y: float, width: float, height: float,
                         max_side: int = 1024, opacity: float = .5):
    """Return base, aligned overlay and blended PNGs on ONE base-coordinate grid.
    Applies the saved dx/dy, clockwise rotation and uniform scale, including reverse pairs.
    Requires saved alignment; never guesses identity for unaligned layers.
    Transparency marks absent coverage. Rough registration is insufficient to prove connectivity.
    """
    region_geometry(x,y,width,height,max_side)
    if not math.isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValueError("opacity must be between 0 and 1")
    with visual_connect() as db:
        base_path = layer_path(db, PROJECT, base_layer)
        overlay_path = layer_path(db, PROJECT, overlay_layer)
        matrix = load_alignment(db, base_layer, overlay_layer)
    rectangle = (x,y,width,height)
    base, bm, base_meta = render_region(base_path, rectangle, max_side)
    overlay, om, overlay_meta = render_region(overlay_path, rectangle, max_side, matrix)
    blend = base.copy()
    both = bm & om
    blend[both] = np.rint(base[both]*(1-opacity)+overlay[both]*opacity).astype(np.uint8)
    blend[om & ~bm] = overlay[om & ~bm]
    meta = {"coordinate_frame": base_layer, "base_layer": base_layer,
            "overlay_layer": overlay_layer, "base": base_meta, "overlay": overlay_meta,
            "opacity": opacity, "common_covered_fraction": float(both.mean()),
            "verified": False, "note": "Visual comparison using saved registration; no electrical inference"}
    return _image_result(meta, [(base_layer+" in base coordinates", base, bm),
                               (overlay_layer+" aligned into base coordinates", overlay, om),
                               ("Blended comparison", blend, bm | om)])


@server.tool()
def plan_layer_scan(layer: str, tile_width: int = 4096, tile_height: int = 4096,
                    overlap_px: int = 256, start_index: int = 0, limit: int = 25) -> dict:
    """Page through an overlapping row-major screening grid in source pixels.
    Use each returned rectangle with get_layer_region or compare_layer_region (layer as base).
    This plans views only; it does not claim that any region has been inspected.
    """
    if not 1 <= tile_width <= 16384 or not 1 <= tile_height <= 16384:
        raise ValueError("Tile dimensions must be between 1 and 16384")
    if not 0 <= overlap_px < min(tile_width,tile_height) or start_index < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid overlap, start index or page limit")
    with visual_connect() as db:
        path = layer_path(db, PROJECT, layer)
    width, height = image_size(path)
    dx, dy = tile_width-overlap_px, tile_height-overlap_px
    columns = max(1, math.ceil(max(0,width-tile_width)/dx)+1)
    rows = max(1, math.ceil(max(0,height-tile_height)/dy)+1)
    total = columns*rows
    tiles = []
    for index in range(start_index,min(start_index+limit,total)):
        x, y = (index%columns)*dx, (index//columns)*dy
        tiles.append({"index": index, "x": x, "y": y,
                      "width": min(tile_width,width-x), "height": min(tile_height,height-y)})
    next_index = start_index+len(tiles)
    return {"coordinate_frame": layer, "source_size": [width,height], "total_tiles": total,
            "tiles": tiles, "next_index": next_index if next_index < total else None}


@server.tool()
def edit_project_objects(operations: list[dict]) -> dict:
    """Atomically edit the active project, with pre-edit SQLite backup and audit log.
    1..500 operations; all roll back on any error. Source images are never changed.
    action=marker: layer,x,y,label.
    action=trace_candidate: layer,x,y,width,height,status.
    action=via: layer,x,y,radius,status.
    action=evidence: kind(polyline/region/hypothesis/alignment_points),layer,label,note,
      status,data. Polyline/region data={points:[[x,y],...]}; hypothesis data is JSON.
      alignment_points data={overlay_layer,base_points:[[x,y],...],overlay_points:...}.
    action=alignment: base_layer,overlay_layer,dx,dy,rotation_deg,scale.
    action=trace_link: layer,source_id,target_id (accepted trace candidates).
    action=via_trace_link: via_id,trace_id. action=via_layer_link: via_a,via_b.
    action=mos_device: name,kind(nmos/pmos),layer,x,y,gate,drain,source,optional bulk,note.
    action=logic_cell: name,kind(DFF/MUX2/AND2/OR2/XOR2/NOT/NAND2/NOR2),layer,pins,note.
    action=update: kind(marker/trace_candidate/via/evidence/mos_device/logic_cell),id,fields.
      Geometry and label/note/status can be updated where applicable; no layer moves.
    action=delete: kind (same plus trace_link/via_trace_link/via_layer_link),id.
    Coordinates use ORIGINAL layer pixels, not preview pixels. Status defaults candidate;
    accepted is reviewed visual evidence, never proof of electrical continuity.
    IDs from creations are returned in operation order; use a later call to link new IDs.
    """
    return edit_project(PROJECT, operations)


@server.tool()
def list_project_evidence(layer: str = "", kind: str = "", limit: int = 100,
                          after_id: int = 0) -> list[dict]:
    """Read MCP polylines, block regions, hypotheses and alignment point sets by stable ID.
    Page by passing the final ID as after_id. No metadata is created by this read.
    """
    if not 1 <= limit <= 500 or after_id < 0:
        raise ValueError("Invalid page")
    with visual_connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mcp_evidence'").fetchone():
            return []
        rows = db.execute("""SELECT id,kind,layer,label,data,note,status FROM mcp_evidence
            WHERE id>? AND (?='' OR layer=?) AND (?='' OR kind=?) ORDER BY id LIMIT ?""",
            (after_id,layer,layer,kind,kind,limit))
        return [dict(id=i,kind=k,layer=l,label=label,data=json.loads(data),note=n,status=s)
                for i,k,l,label,data,n,s in rows]


@server.tool()
def list_project_edits(after_id: int = 0, limit: int = 50) -> list[dict]:
    """Read edit audit records and backup paths. Source files are never included."""
    if not 1 <= limit <= 100 or after_id < 0:
        raise ValueError("Invalid page")
    with visual_connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mcp_edit_log'").fetchone():
            return []
        return [dict(id=i,timestamp=t,operations=json.loads(o),backup=b) for i,t,o,b in
                db.execute('SELECT id,timestamp,operations,backup FROM mcp_edit_log WHERE id>? ORDER BY id LIMIT ?',
                           (after_id,limit))]


@server.tool()
def set_layer_alignment(base_layer: str, overlay_layer: str, dx: float, dy: float,
                        rotation_deg: float = 0, scale: float = 1) -> dict:
    """Save overlay -> base global similarity, backed up and audited.
    Rotation clockwise in degrees; scale/rotation about (0,0), then translation.
    Use compare_layer_region afterwards at multiple locations to validate the result.
    """
    return edit_project(PROJECT,[dict(action='alignment',base_layer=base_layer,
        overlay_layer=overlay_layer,dx=dx,dy=dy,rotation_deg=rotation_deg,scale=scale)])


@server.tool()
def align_from_control_points(base_layer: str, overlay_layer: str,
                              base_points: list[list[float]], overlay_points: list[list[float]],
                              save: bool = False) -> dict:
    """Fit global translation/rotation/uniform scale from corresponding ORIGINAL pixel points.
    At least two distinct pairs; use >=3 well-spread pairs for meaningful residual checks.
    Returns RMS and per-point residuals. save=True stores transform AND point evidence
    atomically. Low fitting error alone does not establish correct feature correspondence.
    """
    with visual_connect() as db:
        layer_path(db,PROJECT,base_layer)
        layer_path(db,PROJECT,overlay_layer)
    if base_layer == overlay_layer:
        raise ValueError('Choose distinct layers')
    fit = fit_alignment(base_points,overlay_points)
    if save:
        fit['edit'] = edit_project(PROJECT,[dict(action='alignment',base_layer=base_layer,
            overlay_layer=overlay_layer,**{k:fit[k] for k in ('dx','dy','rotation_deg','scale')}),
            dict(action='evidence',kind='alignment_points',layer=base_layer,label='Alignment control points',
                 data=dict(overlay_layer=overlay_layer,base_points=base_points,overlay_points=overlay_points,
                           rms_error_px=fit['rms_error_px']))])
    return fit


@server.tool()
def propose_image_alignment(base_layer: str, overlay_layer: str) -> dict:
    """Estimate global translation from bounded previews. Does NOT save.
    Assumes similar contrast, orientation and scale. Cross-material SEM layers can
    produce incorrect peaks; inspect several compare_layer_region crops before saving.
    """
    with visual_connect() as db:
        a,b = [layer_path(db,PROJECT,l) for l in (base_layer,overlay_layer)]
    dx,dy,response = estimate_translation(str(a),str(b))
    return dict(base_layer=base_layer,overlay_layer=overlay_layer,dx=dx,dy=dy,
                rotation_deg=0,scale=1,response=response,verified=False,saved=False)


@server.tool()
def propose_local_alignment(base_layer: str, overlay_layer: str, x: float,y: float,
                            width: float,height: float,max_side: int = 1024) -> dict:
    """Estimate residual translation inside an ALREADY aligned base-coordinate crop.
    Does not save or silently replace a global alignment with a local correction.
    Returns a proposed global transform; validate distant crops before set_layer_alignment.
    Requires full coverage and textured images; response is a heuristic, not probability.
    """
    region_geometry(x,y,width,height,max_side)
    with visual_connect() as db:
        a,b = [layer_path(db,PROJECT,l) for l in (base_layer,overlay_layer)]
        matrix = load_alignment(db,base_layer,overlay_layer)
    base,bm,_ = render_region(a,(x,y,width,height),max_side)
    overlay,om,_ = render_region(b,(x,y,width,height),max_side,matrix)
    if not (bm & om).all() or min(*base.shape)<32:
        raise ValueError('Choose a fully covered crop of at least 32 output pixels per side')
    if min(float(base.std()),float(overlay.std()))<1e-6:
        raise ValueError('Insufficient texture')
    window = cv2.createHanningWindow((base.shape[1],base.shape[0]),cv2.CV_32F)
    shift,response = cv2.phaseCorrelate(base.astype(np.float32),overlay.astype(np.float32),window)
    delta = [-shift[0]*width/base.shape[1],-shift[1]*height/base.shape[0]]
    if not np.isfinite([*delta,response]).all():
        raise ValueError('No finite alignment estimate')
    dx,dy,angle,scale = alignment_parameters(matrix)
    return dict(base_layer=base_layer,overlay_layer=overlay_layer,
                dx=dx+delta[0],dy=dy+delta[1],rotation_deg=angle,scale=scale,
                residual_translation_px=delta,response=float(response),verified=False,saved=False)


if __name__ == "__main__":
    server.run(transport="stdio")

