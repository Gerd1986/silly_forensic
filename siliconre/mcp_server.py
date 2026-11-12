"""Local stdio MCP interface to saved project evidence and bounded image views."""
import base64
import json
import math
from contextlib import closing, contextmanager
import os
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent, ImageContent, ToolAnnotations
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
import analysis_store as research

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
                         max_side: int = 1024, opacity: float = .5,
                         alignment_id: str = ""):
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
        registration = None
        if alignment_id:
            matrix, registration = research.region_matrix(
                db, alignment_id, base_layer, overlay_layer, (x,y,width,height))
        else:
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
    if registration:
        meta['regional_alignment'] = registration
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


def _check_source_points(db, layer, points):
    width, height = image_size(layer_path(db, PROJECT, layer))
    for point in points:
        if len(point) != 2 or not all(math.isfinite(float(v)) for v in point):
            raise ValueError("Expected finite source X/Y coordinates")
        if not 0 <= float(point[0]) < width or not 0 <= float(point[1]) < height:
            raise ValueError(f"Point outside original {layer} image")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_analysis_records(record_type: str = "", layer: str = "", status: str = "") -> list[dict]:
    """Read regional registrations and source-coordinate evidence without migrating the project.
    record_type: alignment/evidence; status: candidate/confirmed/rejected. No electrical inference.
    """
    with visual_connect() as db:
        return research.list_records(db, record_type, layer, status)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def save_regional_alignment(base_layer: str, overlay_layer: str, roi: list[float], name: str,
                            reference_points: list[dict] | None = None,
                            matrix: list[list[float]] | None = None, fit_mode: str = "similarity",
                            status: str = "candidate", note: str = "", record_id: str | None = None,
                            expected_revision: int | None = None) -> dict:
    """WRITE a reversible local overlay->base registration, never the global alignment.
    ROI=[x,y,width,height] in base pixels. Reference pairs: {source:[x,y],target:[x,y],check:false}.
    Fits similarity/affine from training pairs unless an explicit 3x3 matrix is supplied.
    check:true pairs are held out of fitting; returns their measured residuals, not confidence.
    Confirmed requires a review note and independent check pair; it never proves electrical contact.
    Updating requires record_id and its current expected_revision. Images are never rewritten.
    """
    with connect() as db:
        if len(roi) != 4:
            raise ValueError("ROI must have four values")
        w,h = image_size(layer_path(db, PROJECT, base_layer))
        layer_path(db, PROJECT, overlay_layer)
        if roi[0]+roi[2] > w or roi[1]+roi[3] > h:
            raise ValueError("ROI exceeds base image")
        for p in reference_points or []:
            _check_source_points(db, overlay_layer, [p['source']])
            _check_source_points(db, base_layer, [p['target']])
        return research.save_region(db, base_layer, overlay_layer, roi, name, reference_points,
                                    matrix, fit_mode, status, note, record_id, expected_revision)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def save_analysis_evidence(kind: str, geometry: list[dict], label: str = "",
                           status: str = "candidate", note: str = "", references: list[str] | None = None,
                           record_id: str | None = None, expected_revision: int | None = None,
                           provenance: dict | None = None) -> dict:
    """WRITE reversible point/trace/contact/region/note evidence in original layer pixels.
    Geometry=[{layer:'M1',points:[[x,y],...]}]. A contact has one point on EACH of two layers.
    Confirmed/rejected require a review note. references contains existing research record IDs.
    A confirmed contact is a documented review claim, never an automatic electrical graph edge.
    Updates require current expected_revision. Candidate is the default.
    """
    with connect() as db:
        for g in geometry:
            _check_source_points(db, g['layer'], g['points'])
        return research.save_evidence(db, kind, geometry, label, status, note,
                                      references, record_id, expected_revision, provenance=provenance)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_trace_paths(layer: str, x: int, y: int, width: int, height: int,
                        threshold: int = 0, min_area: int = 20, invert: bool = False) -> dict:
    """Read-only native-pixel conductor centerlines, not boxes or bounding-box links.
    Threshold + thinning; no gap bridging. Splits at branches and retains loops.
    Returns ordered pixel-center vertices, junctions and ROI-clipping warnings.
    At most 2 million ROI pixels / 20,000 output vertices; use smaller crops for dense wiring.
    Image paths are unverified: neither physical contacts nor electrical nets are inferred.
    """
    from trace_paths import extract_paths
    with visual_connect() as db:
        path=layer_path(db,PROJECT,layer)
    return dict(layer=layer,**extract_paths(path,[x,y,width,height],threshold,min_area,invert))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def extract_trace_paths(layer: str, x: int, y: int, width: int, height: int,
                        threshold: int = 0, min_area: int = 20, invert: bool = False) -> dict:
    """WRITE native-pixel centerline polylines as candidate research traces in one atomic batch.
    Same extraction as preview_trace_paths; no rectangle-center shortcuts, gap bridging or
    automatic electrical acceptance. Stores parameters, source ROI and endpoint/branch metadata.
    Use list_analysis_records to inspect/edit saved paths and research undo to reverse saves.
    """
    from trace_paths import extract_paths,save_paths
    with visual_connect() as db:
        path=layer_path(db,PROJECT,layer)
    result=extract_paths(path,[x,y,width,height],threshold,min_area,invert)
    with connect() as db:
        records=save_paths(db,layer,result)
    return dict(layer=layer,roi=result['roi'],threshold=result['threshold'],records=records,
                status='candidate',junctions=result['junctions'],electrically_verified=False)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False))
def delete_analysis_record(record_id: str, expected_revision: int) -> dict:
    """WRITE a reversible deletion of one research record; refuses stale or referenced records.
    Does not delete original images, legacy annotations or reconstructed electrical nets.
    """
    with connect() as db:
        return research.delete_record(db, record_id, expected_revision)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_analysis_history(limit: int = 50) -> list[dict]:
    """Read persistent undo/redo history for research edits only, including GUI and MCP edits."""
    with visual_connect() as db:
        return research.history(db, limit)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def undo_analysis_change(expected_seq: int) -> dict:
    """WRITE: undo the latest active research edit. Supply its seq from list_analysis_history.
    Refuses when history has changed. Legacy editing actions have a separate lifecycle.
    """
    with connect() as db:
        return research.undo_redo(db, expected_seq=expected_seq)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def redo_analysis_change(expected_seq: int) -> dict:
    """WRITE: redo the earliest undone active research edit with a history sequence guard."""
    with connect() as db:
        return research.undo_redo(db, redo=True, expected_seq=expected_seq)


if __name__ == "__main__":
    server.run(transport="stdio")
